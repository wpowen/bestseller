"""Phase-3 distillation: aggregate chapter_cards into book-level assets via LLM.

Produces repo-safe artifacts under ``data/distillation/source-XXXX/``:
``volume_cards.jsonl``, ``book_design_card.json``, ``mechanism_candidates.jsonl``,
``material_entries.review.jsonl``, ``anti_copy_ledger.json``, ``grammar_patch.yaml``.

Layering: chapter cards are batched (default 25) into volume cards; book-level
JSON is produced from volume cards only (plus a few sampled compressed chapter
rows), never full-book chapter text in one prompt.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence, cast

import yaml
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.services.distillation_assets import (
    MATERIAL_REVIEW_FILENAMES,
    _first_existing,
    read_json,
    read_jsonl,
    write_json,
    write_jsonl,
)
from bestseller.services.distillation_chapter_llm import load_chapter_card_schema, validate_chapter_card
from bestseller.services.distillation_genre_classifier import (
    ALLOWED_DISTILLATION_GENRE_BUCKETS,
)
from bestseller.services.distillation_privacy_gate import privacy_violation_count_for_material_row
from bestseller.services.llm import LLMCompletionRequest, LLMRole, complete_text
from bestseller.services.planner import _extract_json_payload
from bestseller.settings import AppSettings

logger = logging.getLogger(__name__)

DEFAULT_CHAPTER_BATCH = 25
ACTIVE_CONFIDENCE_MIN = 0.72
PLOT_RETELLING_MARKERS: tuple[str, ...] = (
    "接着",
    "随后",
    "于是",
    "后来",
    "与此同时",
    "第二天",
    "本章",
    "章末",
)


def compress_chapter_card_for_prompt(row: dict[str, Any], *, max_list: int = 8) -> dict[str, Any]:
    """Shrink a chapter_card for upstream aggregation prompts."""

    def clip_list(items: Any, n: int) -> list[str]:
        if not isinstance(items, list):
            return []
        out: list[str] = []
        for it in items[:n]:
            if isinstance(it, dict):
                out.append(json.dumps(it, ensure_ascii=False)[:240])
            else:
                out.append(str(it)[:240])
        return out

    return {
        "abs_chapter_no": row.get("abs_chapter_no"),
        "volume_no": row.get("volume_no"),
        "chapter_function": str(row.get("chapter_function") or "")[:700],
        "reusable_mechanisms": clip_list(row.get("reusable_mechanisms"), max_list),
        "craft_observations": row.get("craft_observations")
        if isinstance(row.get("craft_observations"), dict)
        else {},
        "reader_rewards": clip_list(row.get("reader_rewards"), 5),
        "open_hooks": clip_list(row.get("open_hooks"), 4),
        "non_reusable_specifics": clip_list(row.get("non_reusable_specifics"), 4),
        "risk_flags": clip_list(row.get("risk_flags"), 8),
        "confidence": row.get("confidence"),
    }


def infer_aggregate_key(manifest: dict[str, Any]) -> str:
    """Prefer LLM ``distillation_genre_bucket``; else map ``genre_hint`` to a stable slug."""

    bucket = manifest.get("distillation_genre_bucket")
    if (
        isinstance(bucket, str)
        and bucket in ALLOWED_DISTILLATION_GENRE_BUCKETS
        and bucket != "distillation-genre-unclassified"
    ):
        return bucket

    hint = str(manifest.get("genre_hint") or "")
    if re.search(r"异界|穿越|異界|otherworld|transmigration|isekai", hint, re.I):
        return "otherworld-cross-system"
    if re.search(r"都市|现实|现代|职场", hint):
        return "urban-contemporary"
    if re.search(r"种田|基建|经营|领地|基地|base-building|settlement|management", hint, re.I):
        return "base-building"
    if re.search(r"东方美学|国风|水墨|志怪|eastern aesthetic", hint, re.I):
        return "eastern-aesthetic"
    if re.search(r"玄幻|仙侠|修真|修仙|东方", hint):
        return "eastern-progression-fantasy"
    if re.search(r"科幻|星际|赛博", hint):
        return "science-fiction-progression"
    if re.search(r"悬疑|推理|刑侦", hint):
        return "suspense-mystery"
    slug = re.sub(r"[^a-z0-9]+", "-", hint.lower()).strip("-")
    if len(slug) < 3:
        slug = "distillation-generic"
    return slug[:80]


def expected_abs_chapters_from_index(chapters_index: dict[str, Any]) -> set[int]:
    chs = chapters_index.get("chapters") or []
    out: set[int] = set()
    if isinstance(chs, list) and chs:
        for item in chs:
            if isinstance(item, dict) and isinstance(item.get("abs_chapter_no"), int):
                out.add(int(item["abs_chapter_no"]))
        return out
    cc = chapters_index.get("chapter_count")
    if isinstance(cc, int) and cc > 0:
        return set(range(1, cc + 1))
    return out


def chapter_cards_by_abs(chapter_cards: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(chapter_cards, key=lambda r: int(r.get("abs_chapter_no") or 0))


def batch_chapter_cards(
    ordered_cards: Sequence[dict[str, Any]],
    *,
    batch_min: int = 20,
    batch_max: int = 30,
) -> list[list[dict[str, Any]]]:
    """Split ordered chapter cards into batches of size in [batch_min, batch_max]."""

    size = min(batch_max, max(batch_min, DEFAULT_CHAPTER_BATCH))
    batches: list[list[dict[str, Any]]] = []
    buf: list[dict[str, Any]] = []
    for row in ordered_cards:
        buf.append(row)
        if len(buf) >= size:
            batches.append(buf)
            buf = []
    if buf:
        batches.append(buf)
    return batches


def validate_volume_card(obj: dict[str, Any]) -> None:
    for key in (
        "source_id",
        "volume_no",
        "chapter_range",
        "arc_function",
        "dominant_engine",
        "state_progression",
        "turning_points",
        "reusable_mechanisms",
        "failure_modes",
    ):
        if key not in obj:
            raise ValueError(f"volume_card missing field: {key}")
    if not isinstance(obj.get("volume_no"), int) or int(obj["volume_no"]) < 1:
        raise ValueError("volume_card.volume_no must be int >= 1")
    tps = obj.get("turning_points")
    if not isinstance(tps, list):
        raise ValueError("volume_card.turning_points must be a list")
    for tp in tps:
        if not isinstance(tp, dict) or "abs_chapter_no" not in tp or "function" not in tp:
            raise ValueError("volume_card.turning_points entries need abs_chapter_no + function")


def validate_book_design_card(obj: dict[str, Any]) -> None:
    for key in (
        "book_id",
        "source_ref",
        "source_type",
        "status",
        "parsed_profile",
        "genre_tags",
        "reader_promise",
        "core_engine",
        "state_variables",
        "reader_rewards",
        "reusable_mechanisms",
        "non_reusable_specifics",
        "risk_patterns",
    ):
        if key not in obj:
            raise ValueError(f"book_design_card missing field: {key}")
    if not isinstance(obj.get("parsed_profile"), dict):
        raise ValueError("book_design_card.parsed_profile must be an object")


def validate_author_craft_card(obj: dict[str, Any]) -> None:
    for key in (
        "source_id",
        "source_type",
        "status",
        "style_safety_policy",
        "pov_and_distance",
        "sentence_rhythm",
        "paragraphing",
        "dialogue_system",
        "description_strategy",
        "exposition_strategy",
        "emotional_temperature",
        "hooking_and_transitions",
        "adaptation_guidelines",
        "taboo_copy_signals",
        "confidence",
    ):
        if key not in obj:
            raise ValueError(f"author_craft_card missing field: {key}")
    for key in (
        "sentence_rhythm",
        "paragraphing",
        "dialogue_system",
        "description_strategy",
        "exposition_strategy",
        "emotional_temperature",
        "hooking_and_transitions",
        "adaptation_guidelines",
        "taboo_copy_signals",
    ):
        if not isinstance(obj.get(key), list):
            raise ValueError(f"author_craft_card.{key} must be a list")
    conf = obj.get("confidence")
    if not isinstance(conf, (int, float)) or not (0.0 <= float(conf) <= 1.0):
        raise ValueError("author_craft_card.confidence must be a number in [0, 1]")


def validate_anti_copy_ledger(obj: dict[str, Any]) -> None:
    for key in ("source_id", "blocked_categories", "blocked_combinations", "replacement_policy"):
        if key not in obj:
            raise ValueError(f"anti_copy_ledger missing field: {key}")


def looks_like_shallow_plot_retelling(summary: str) -> bool:
    if len(summary) <= 420:
        return False
    hits = sum(1 for m in PLOT_RETELLING_MARKERS if m in summary)
    return hits >= 3


def _coerce_dict_payload(
    obj: Any,
    *,
    source_id: str,
    stage: str,
    defaults: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if isinstance(obj, list):
        for item in obj:
            if isinstance(item, dict):
                obj = item
                break
        else:
            obj = None
    if isinstance(obj, dict):
        row = dict(obj)
        row.setdefault("source_id", source_id)
        row.setdefault("distillation_source_id", source_id)
        return row
    if defaults is None:
        defaults = {}
    fallback = dict(defaults)
    fallback.setdefault("source_id", source_id)
    fallback.setdefault("distillation_source_id", source_id)
    fallback.setdefault("_distillation_stage", stage)
    fallback.setdefault("distillation_fallback", True)
    return fallback


def _coerce_volume_card(
    obj: Any,
    *,
    source_id: str,
    volume_no: int,
    chapter_range: str,
) -> dict[str, Any]:
    default = {
        "source_id": source_id,
        "volume_no": volume_no,
        "chapter_range": chapter_range,
        "arc_function": "Fallback aggregation due LLM structure issue.",
        "dominant_engine": "unknown",
        "state_progression": ["LLM output fallback for this volume."],
        "turning_points": [],
        "reusable_mechanisms": [],
        "failure_modes": [],
    }
    row = _coerce_dict_payload(obj, source_id=source_id, stage="volume_card", defaults=default)
    try:
        row["volume_no"] = int(row.get("volume_no") or volume_no)
    except (TypeError, ValueError):
        row["volume_no"] = int(volume_no)
    row["chapter_range"] = str(row.get("chapter_range") or chapter_range)
    row.setdefault("arc_function", default["arc_function"])
    row.setdefault("dominant_engine", default["dominant_engine"])
    row.setdefault("state_progression", default["state_progression"])
    raw_turning_points = row.get("turning_points")
    normalised_turning_points: list[dict[str, Any]] = []
    if isinstance(raw_turning_points, list):
        for item in raw_turning_points:
            if not isinstance(item, dict):
                continue
            fn = str(item.get("function") or item.get("role") or item.get("description") or "").strip()
            if not fn:
                continue
            raw_no = item.get("abs_chapter_no") or item.get("chapter_no") or item.get("chapter")
            try:
                abs_no = int(raw_no)
            except (TypeError, ValueError):
                match = re.search(r"\d+", str(raw_no or ""))
                if not match:
                    continue
                abs_no = int(match.group(0))
            if abs_no < 1:
                continue
            normalised_turning_points.append({"abs_chapter_no": abs_no, "function": fn})
    row["turning_points"] = normalised_turning_points
    if not isinstance(row.get("reusable_mechanisms"), list):
        row["reusable_mechanisms"] = []
    if not isinstance(row.get("failure_modes"), list):
        row["failure_modes"] = []
    return row


def _coerce_book_design_card(obj: Any, *, source_id: str, source_ref: str) -> dict[str, Any]:
    default = {
        "book_id": source_id,
        "source_ref": source_ref,
        "source_type": "distillation_package",
        "status": "draft_review",
        "parsed_profile": {
            "chapter_count": 0,
            "volume_count": 0,
            "encoding": "",
        },
        "genre_tags": ["distillation-generic"],
        "reader_promise": "LLM structured distillation fallback.",
        "core_engine": "unknown",
        "state_variables": ["fallback_progress"],
        "reader_rewards": ["distillable_mechanism_reliability"],
        "reusable_mechanisms": ["fallback mechanism abstraction"],
        "non_reusable_specifics": [],
        "risk_patterns": ["low_confidence_fallback"],
    }
    row = _coerce_dict_payload(obj, source_id=source_id, stage="book_design_card", defaults=default)
    for key, value in default.items():
        if row.get(key) in (None, "", [], {}):
            row[key] = value
    row["book_id"] = str(row.get("book_id") or source_id)
    row["source_ref"] = str(row.get("source_ref") or source_ref)
    row["source_type"] = str(row.get("source_type") or default["source_type"])
    row["status"] = str(row.get("status") or default["status"])
    if not isinstance(row.get("parsed_profile"), dict):
        row["parsed_profile"] = dict(default["parsed_profile"])
    if isinstance(row.get("core_engine"), list):
        row["core_engine"] = " / ".join(str(x) for x in row["core_engine"] if str(x).strip())
    if not str(row.get("core_engine") or "").strip():
        row["core_engine"] = default["core_engine"]
    for key in (
        "genre_tags",
        "state_variables",
        "reader_rewards",
        "reusable_mechanisms",
        "non_reusable_specifics",
        "risk_patterns",
    ):
        value = row.get(key)
        if isinstance(value, list):
            row[key] = [str(x) for x in value if str(x).strip()]
        elif value in (None, ""):
            row[key] = list(default[key])
        else:
            row[key] = [str(value)]
        if not row[key] and default.get(key):
            row[key] = list(default[key])
    return row


def _coerce_string_list(value: Any, fallback: Sequence[str]) -> list[str]:
    if isinstance(value, list):
        out = [str(x).strip() for x in value if str(x).strip()]
    elif value in (None, "", {}, []):
        out = []
    else:
        out = [str(value).strip()]
    return out or list(fallback)


def _coerce_author_craft_card(obj: Any, *, source_id: str) -> dict[str, Any]:
    default = {
        "source_id": source_id,
        "source_type": "distillation_package",
        "status": "draft_review",
        "style_safety_policy": (
            "Anonymous craft profile only; do not imitate a named author, quote source prose, "
            "or preserve a copyable expression pattern."
        ),
        "pov_and_distance": "close-third or project-selected POV with controlled narrative distance",
        "sentence_rhythm": ["vary short action beats with medium explanatory sentences"],
        "paragraphing": ["keep paragraphs functional: action, reaction, or information turn"],
        "dialogue_system": ["use role-specific intent, subtext, and conflict pressure"],
        "description_strategy": ["describe only details that change stakes, mood, or reader inference"],
        "exposition_strategy": ["place exposition after visible need or conflict pressure"],
        "emotional_temperature": ["surface emotion through choices, body action, and withheld admission"],
        "hooking_and_transitions": ["end units on changed state, unresolved question, or sharper constraint"],
        "adaptation_guidelines": [
            "translate craft controls into the new project's genre, cast, and premise",
            "change imagery fields, sentence signatures, names, and scenario chains",
        ],
        "taboo_copy_signals": [
            "source titles, author names, exact phrases, named entities, and distinctive sentence templates"
        ],
        "confidence": 0.0,
    }
    row = _coerce_dict_payload(obj, source_id=source_id, stage="author_craft_card", defaults=default)
    for key, value in default.items():
        if key in {"sentence_rhythm", "paragraphing", "dialogue_system", "description_strategy",
                   "exposition_strategy", "emotional_temperature", "hooking_and_transitions",
                   "adaptation_guidelines", "taboo_copy_signals"}:
            row[key] = _coerce_string_list(row.get(key), cast(Sequence[str], value))
        elif row.get(key) in (None, "", {}, []):
            row[key] = value
    row["source_id"] = source_id
    row["source_type"] = str(row.get("source_type") or default["source_type"])
    row["status"] = str(row.get("status") or default["status"])
    row["style_safety_policy"] = str(row.get("style_safety_policy") or default["style_safety_policy"])
    row["pov_and_distance"] = str(row.get("pov_and_distance") or default["pov_and_distance"])
    try:
        conf = float(row.get("confidence") if row.get("confidence") is not None else 0.0)
    except (TypeError, ValueError):
        conf = 0.0
    row["confidence"] = max(0.0, min(1.0, conf))
    return row


def _coerce_tail_bundle(obj: Any, *, source_id: str) -> tuple[list[Any], dict[str, Any], list[Any]]:
    default = {
        "source_id": source_id,
        "mechanism_candidates": [],
        "anti_copy_ledger": {
            "source_id": source_id,
            "blocked_categories": [],
            "blocked_combinations": [],
            "replacement_policy": [],
        },
        "material_entries_review": [],
    }
    payload = _coerce_dict_payload(obj, source_id=source_id, stage="book_tail_bundle", defaults=default)

    mechs = payload.get("mechanism_candidates")
    if not isinstance(mechs, list):
        mechs = []
    ledger = payload.get("anti_copy_ledger")
    if not isinstance(ledger, dict):
        ledger = {
            "source_id": source_id,
            "blocked_categories": [],
            "blocked_combinations": [],
            "replacement_policy": [],
        }
    else:
        ledger = dict(ledger)
        ledger.setdefault("source_id", source_id)
        if not isinstance(ledger.get("blocked_categories"), list):
            ledger["blocked_categories"] = []
        if not isinstance(ledger.get("blocked_combinations"), list):
            ledger["blocked_combinations"] = []
        if not isinstance(ledger.get("replacement_policy"), list):
            ledger["replacement_policy"] = []
    mats = payload.get("material_entries_review")
    if not isinstance(mats, list):
        mats = []

    return mechs, ledger, mats


def material_row_has_executable_content(content: dict[str, Any]) -> bool:
    if not content:
        return False
    if not isinstance(content, dict):
        return False
    if "distillation_source_ids" not in content:
        return False
    ids = content.get("distillation_source_ids")
    if not isinstance(ids, list) or not ids:
        return False
    # Require at least one structural key beyond ids
    structural = (
        "state_variables",
        "required_cost",
        "guardrail",
        "beat_order",
        "scene_inputs",
        "scene_outputs",
        "stages",
        "arc_ladder",
        "valid_venues",
        "required_change",
        "blocked_elements",
        "replacement_rule",
        "replace_with",
        "gate",
        "decision_policy",
    )
    return any(k in content and content.get(k) not in (None, "", [], {}) for k in structural)


def material_passes_active_gate(
    row: dict[str, Any],
    *,
    anti_copy_ledger: dict[str, Any],
) -> tuple[bool, str | None]:
    """Return (ok, reject_reason) for promotion to ``status=active``."""

    conf = row.get("confidence")
    try:
        cval = float(conf) if conf is not None else 0.0
    except (TypeError, ValueError):
        cval = 0.0
    if cval < ACTIVE_CONFIDENCE_MIN:
        return False, "confidence_below_threshold"

    summary = str(row.get("narrative_summary") or "")
    if not summary.strip():
        return False, "empty_narrative_summary"

    vcount, vmsgs = privacy_violation_count_for_material_row(row, anti_copy_ledger=anti_copy_ledger)
    if vcount:
        detail = vmsgs[0] if vmsgs else "privacy_violation"
        return False, f"privacy_or_anti_copy:{detail[:400]}"

    rf = row.get("risk_flags")
    if isinstance(rf, list) and rf:
        joined = ",".join(str(x) for x in rf).lower()
        if "source_specific" in joined or "source-specific" in joined:
            return False, "risk_flags_source_specific"

    if looks_like_shallow_plot_retelling(summary):
        return False, "suspected_plot_retelling"

    content = row.get("content_json")
    if not isinstance(content, dict):
        return False, "content_json_not_object"
    if not material_row_has_executable_content(content):
        return False, "content_json_not_executable"

    return True, None


def promote_review_rows_to_active(
    rows: Iterable[dict[str, Any]],
    *,
    anti_copy_ledger: dict[str, Any],
    source_id: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split review rows into active-ready vs rejected audit rows."""

    active: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for row in rows:
        r = dict(row)
        content = r.get("content_json")
        if not isinstance(content, dict):
            content = {
                "distillation_source_ids": [source_id],
                "state_variables": ["unspecified_distillation_mechanism"],
                "guardrail": "Automated minimal payload — expand before promotion.",
            }
        ids = content.get("distillation_source_ids")
        if not isinstance(ids, list):
            ids = []
        if source_id not in ids:
            ids = list(ids) + [source_id]
        content = dict(content)
        content["distillation_source_ids"] = ids
        r["content_json"] = content
        ok, reason = material_passes_active_gate(r, anti_copy_ledger=anti_copy_ledger)
        if ok:
            r["status"] = "active"
            active.append(r)
        else:
            r["status"] = "review"
            rejected.append({"row": r, "reject_reason": reason})
    return active, rejected


def grammar_patch_fallback(
    *,
    aggregate_key: str,
    source_id: str,
    book_design: dict[str, Any],
) -> dict[str, Any]:
    """Deterministic minimal grammar patch when LLM output is unusable."""

    tags = book_design.get("genre_tags") or []
    cat_tags = [str(t) for t in tags if isinstance(t, str)][:10]
    return {
        "key": aggregate_key,
        "name": aggregate_key.replace("-", " "),
        "source_ids": [source_id],
        "status": "review",
        "applies_to_categories": cat_tags or ["distillation-generic"],
        "required_contracts": [
            "exposure_cost_model",
            "state_tracking",
            "no_source_specific_names",
        ],
        "state_variables": list(book_design.get("state_variables") or [])[:24],
        "chapter_change_vectors": [
            "state_shift",
            "reader_reward_payoff",
            "hook_resolution_or_escalation",
        ],
        "reader_rewards": list(book_design.get("reader_rewards") or [])[:12],
        "hook_or_aftereffect_types": ["misread_reassessment", "new_constraint"],
        "forbidden_defaults": [
            "copy_source_title_or_names",
            "effortless_asset_collection",
            "plot_summary_as_mechanism",
        ],
    }


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def write_error_report(private_errors_dir: Path, source_id: str, payload: dict[str, Any]) -> Path:
    private_errors_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = private_errors_dir / f"{source_id}_book_agg_{ts}.json"
    _atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    return path


async def complete_distillation_json(
    session: AsyncSession,
    settings: AppSettings,
    *,
    logical_role: LLMRole,
    system_prompt: str,
    user_prompt: str,
    prompt_template: str,
    max_tokens: int = 8192,
    metadata: dict[str, Any] | None = None,
    retries: int = 3,
) -> Any:
    """Call ``complete_text`` and parse JSON with bounded retries on parse/validation."""

    last_exc: BaseException | None = None
    effective_user_prompt = user_prompt
    semantic_repair_history: list[dict[str, Any]] = []
    for attempt in range(max(1, retries)):
        try:
            result = await complete_text(
                session,
                settings,
                LLMCompletionRequest(
                    logical_role=logical_role,
                    system_prompt=system_prompt,
                    user_prompt=effective_user_prompt,
                    fallback_response="{}",
                    prompt_template=prompt_template,
                    prompt_version="v1",
                    project_id=None,
                    workflow_run_id=None,
                    metadata={
                        **(metadata or {}),
                        "attempt": attempt + 1,
                        "semantic_repair_history": semantic_repair_history[-3:],
                    },
                    max_tokens_override=max_tokens,
                ),
            )
            return _extract_json_payload(result.content)
        except Exception as exc:  # noqa: BLE001
            from bestseller.services.llm_closed_loop import (
                build_repair_user_prompt,
                findings_from_exception,
            )

            last_exc = exc
            findings = findings_from_exception(exc)
            semantic_repair_history.append(
                {
                    "attempt": attempt + 1,
                    "error_type": type(exc).__name__,
                    "findings": [finding.to_dict() for finding in findings[:8]],
                }
            )
            effective_user_prompt = build_repair_user_prompt(
                original_user_prompt=user_prompt,
                findings=findings,
                language=None,
            )
            logger.warning(
                "distillation LLM parse/call failed (attempt %s/%s): %s; diagnostics=%s",
                attempt + 1,
                retries,
                exc,
                [finding.code for finding in findings[:6]],
            )
            await asyncio.sleep(min(8.0, 1.5 * (2**attempt)))
    assert last_exc is not None
    raise RuntimeError("distillation LLM retries exhausted") from last_exc


@dataclass(frozen=True)
class BookAggregationResult:
    source_id: str
    volume_cards_written: int
    book_design_written: bool
    author_craft_written: bool
    mechanism_rows: int
    material_review_rows: int
    material_active_rows: int
    errors: tuple[str, ...]


async def aggregate_source_package_async(
    session: AsyncSession,
    settings: AppSettings,
    *,
    package_dir: Path,
    repo_root: Path,
    private_errors_dir: Path,
    chapter_batch_size: int | None = None,
    batch_min: int = 20,
    batch_max: int = 30,
    write_active_artifacts: bool = False,
) -> BookAggregationResult:
    """Run LLM aggregation for a single anonymized source package."""

    manifest = read_json(package_dir / "source_manifest.json")
    source_id = str(manifest.get("source_id") or "")
    if not source_id.startswith("source-"):
        raise ValueError("invalid source_id in manifest")

    chapters_index = read_json(package_dir / "chapters.index.json")
    chapter_cards_path = package_dir / "chapter_cards.jsonl"
    if not chapter_cards_path.is_file():
        raise FileNotFoundError(f"missing {chapter_cards_path}")

    cards = read_jsonl(chapter_cards_path)
    schema_cc = load_chapter_card_schema(repo_root)
    for row in cards:
        validate_chapter_card(row, schema=schema_cc)

    expected = expected_abs_chapters_from_index(chapters_index)
    present = {int(r["abs_chapter_no"]) for r in cards if isinstance(r.get("abs_chapter_no"), int)}
    missing = sorted(expected - present) if expected else []
    if missing:
        msg = f"missing chapter cards for abs_chapters={missing[:40]}{'...' if len(missing) > 40 else ''}"
        write_error_report(
            private_errors_dir,
            source_id,
            {"stage": "precheck", "error": msg, "missing_count": len(missing)},
        )
        raise ValueError(msg)

    ordered = chapter_cards_by_abs(cards)
    if chapter_batch_size is not None:
        bs = max(1, int(chapter_batch_size))
        manual: list[list[dict[str, Any]]] = []
        buf: list[dict[str, Any]] = []
        for row in ordered:
            buf.append(row)
            if len(buf) >= bs:
                manual.append(buf)
                buf = []
        if buf:
            manual.append(buf)
        batches = manual
    else:
        batches = batch_chapter_cards(ordered, batch_min=batch_min, batch_max=batch_max)

    vol_system = (
        "# ROLE\n"
        "You are a volume-design synthesizer. Your job: read N anonymized chapter_card JSON\n"
        "objects from ONE volume of a source novel and distill ONE volume_card capturing\n"
        "the volume's reusable design DNA (NOT its plot).\n"
        "\n"
        "# CONTEXT\n"
        "Your output enters a cross-project library. Future books in the same genre will\n"
        "retrieve your volume_card to learn 'how volume-level arcs typically build and pay off'.\n"
        "Identity must be SCRUBBED; only transferable mechanics survive.\n"
        "\n"
        "# TASK\n"
        "Produce exactly ONE volume_card JSON object.\n"
        "\n"
        "# CONSTRAINTS · Anonymization (HARD)\n"
        "- No personal names, place names, book titles, file paths, technique names.\n"
        "- Replace with role labels: protagonist / antagonist / mentor / local_power_system / artifact_x.\n"
        "- Describe MECHANICS (e.g. 'setup-payoff 3-chapter cycle'), NOT plot events.\n"
        "- Never quote distinctive phrases (>3 consecutive content words).\n"
        "\n"
        "# THINKING (in your head before JSON)\n"
        "1. Scan all chapter_cards — identify the volume's arc shape (setup / escalation / climax / resolution).\n"
        "2. Identify the dominant engine (single-line / multi-line / spiral / etc).\n"
        "3. Find 2-4 turning_points (which chapters pivot the arc?).\n"
        "4. Extract reusable_mechanisms — transferable across books.\n"
        "5. List failure_modes the original avoided or fell into.\n"
        "\n"
        "# OUTPUT FORMAT\n"
        "Strict JSON object. No markdown, no commentary."
    )

    volume_rows: list[dict[str, Any]] = []
    for vol_idx, batch in enumerate(batches, start=1):
        mini = [compress_chapter_card_for_prompt(r) for r in batch]
        lo = int(batch[0].get("abs_chapter_no") or 0)
        hi = int(batch[-1].get("abs_chapter_no") or 0)
        user = "\n".join(
            [
                "=== DISTILLATION_TASK: volume_card ===",
                f"SOURCE_ID: {source_id}",
                f"VOLUME_NO: {vol_idx}",
                f"CHAPTER_ABS_RANGE: {lo}-{hi}",
                "CHAPTER_CARD_BATCH_JSON:",
                json.dumps(mini, ensure_ascii=False),
                "",
                "Return one JSON object with fields:",
                "source_id, volume_no, chapter_range, arc_function, dominant_engine,",
                "state_progression (string array), turning_points (array of "
                "{abs_chapter_no, function}), setup_payoff_rhythm (optional string),",
                "reusable_mechanisms (string array), failure_modes (string array),",
                "craft_profile (optional object with anonymous prose-craft observations).",
            ]
        )
        raw = await complete_distillation_json(
            session,
            settings,
            logical_role=cast(LLMRole, "summarizer"),
            system_prompt=vol_system,
            user_prompt=user,
            prompt_template="distillation_volume_card",
            max_tokens=4096,
            metadata={"distillation_source_id": source_id, "volume_no": vol_idx},
        )
        raw = _coerce_volume_card(
            raw,
            source_id=source_id,
            volume_no=vol_idx,
            chapter_range=f"{lo}-{hi}",
        )
        raw["source_id"] = source_id
        try:
            raw["volume_no"] = int(raw.get("volume_no") or vol_idx)
        except (TypeError, ValueError):
            raw["volume_no"] = int(vol_idx)
        raw["chapter_range"] = str(raw.get("chapter_range") or f"{lo}-{hi}")
        validate_volume_card(raw)
        volume_rows.append(raw)

    vol_path = package_dir / "volume_cards.jsonl"
    write_jsonl(vol_path, volume_rows)

    # Sample a few compressed chapters for book-level call (first + mid + last)
    sample_chapters: list[dict[str, Any]] = []
    if ordered:
        picks = {0, len(ordered) // 2, len(ordered) - 1}
        for i in sorted(picks):
            sample_chapters.append(compress_chapter_card_for_prompt(ordered[i]))

    book_system = (
        "# ROLE\n"
        "You are a book-level design synthesizer. Your output is a reusable design fingerprint\n"
        "for ONE source novel — NOT a plot recap, NOT a literary review.\n"
        "\n"
        "# CONTEXT\n"
        "Downstream consumer: cross-project methodology library. Future authors retrieve your\n"
        "card to learn 'how books in this genre typically architect across the whole arc'.\n"
        "\n"
        "# TASK\n"
        "Produce exactly ONE book_design_card JSON object capturing the book's transferable architecture.\n"
        "\n"
        "# CONSTRAINTS · Anonymization (HARD)\n"
        "- FORBIDDEN: real names, geography, artifacts, techniques, book titles, file paths.\n"
        "- Use only abstract mechanisms and genre grammar.\n"
        "- Describe the SHAPE of choices, not the specific content.\n"
        "\n"
        "# THINKING (in your head before JSON)\n"
        "1. From volume_cards + sampled chapter_cards, identify the book's macro-engine.\n"
        "2. Identify the central tension axis (what reader question drives the whole arc?).\n"
        "3. Extract the genre's grammar: which scene-types appear, with what frequency.\n"
        "4. Note do-not-copy fingerprints (author voice, idiosyncratic style markers).\n"
        "\n"
        "# OUTPUT FORMAT\n"
        "Strict JSON object. No markdown, no commentary."
    )
    book_user = "\n".join(
        [
            "=== DISTILLATION_TASK: book_design_card ===",
            f"SOURCE_ID: {source_id}",
            "VOLUME_CARDS_JSON:",
            json.dumps(volume_rows, ensure_ascii=False),
            "SAMPLE_COMPRESSED_CHAPTER_CARDS_JSON:",
            json.dumps(sample_chapters, ensure_ascii=False),
            "",
            "Return JSON with required fields per book_design_card schema:",
            "book_id, source_ref, source_type, status, parsed_profile, genre_tags,",
            "reader_promise, core_engine, state_variables, reader_rewards,",
            "reusable_mechanisms, non_reusable_specifics, risk_patterns.",
            "book_id must equal SOURCE_ID.",
            f"source_ref should be distillation:{str(manifest.get('source_hash_sha256') or '')[:24]}",
            "source_type: distillation_package",
            "status: draft_review",
        ]
    )
    book_raw = await complete_distillation_json(
        session,
        settings,
        logical_role=cast(LLMRole, "planner"),
        system_prompt=book_system,
        user_prompt=book_user,
        prompt_template="distillation_book_design_card",
        max_tokens=8192,
        metadata={"distillation_source_id": source_id},
    )
    source_ref = f"distillation:{str(manifest.get('source_hash_sha256') or '')[:24]}"
    book_raw = _coerce_book_design_card(
        book_raw,
        source_id=source_id,
        source_ref=source_ref,
    )
    book_raw["book_id"] = source_id
    book_raw["source_ref"] = source_ref
    pp = book_raw.get("parsed_profile")
    if not isinstance(pp, dict):
        parse_profile = manifest.get("parse_profile") or {}
        ch_count = chapters_index.get("chapter_count")
        if not isinstance(ch_count, int) and isinstance(parse_profile, dict):
            ch_count = parse_profile.get("chapter_count")
        book_raw["parsed_profile"] = {
            "chapter_count": int(ch_count or len(ordered)),
            "volume_count": len(volume_rows),
            "encoding": str(manifest.get("encoding") or ""),
        }
    validate_book_design_card(book_raw)
    write_json(package_dir / "book_design_card.json", book_raw)

    craft_system = (
        "# ROLE\n"
        "You are an anonymous craft-controls distiller. You produce ONE author_craft_card JSON\n"
        "capturing the high-level prose-craft dials a writer turned, NOT an instruction set to\n"
        "imitate this specific author.\n"
        "\n"
        "# CONTEXT\n"
        "Downstream consumer: future authors writing in the same genre. They retrieve your card\n"
        "to ask 'what craft dials are common for this kind of story', NOT 'how do I sound exactly\n"
        "like author X'.\n"
        "\n"
        "# TASK\n"
        "Produce ONE author_craft_card JSON with abstract craft controls.\n"
        "\n"
        "# CONSTRAINTS · Anti-imitation (HARD)\n"
        "- FORBIDDEN: author names, source titles, exact phrases, distinctive sentence templates,\n"
        "  or any instruction phrased as 'write in the style of X'.\n"
        "- ONLY allowed: high-level abstract controls — POV distance, sentence rhythm bands,\n"
        "  paragraphing rules, dialogue method, description strategy, exposition placement,\n"
        "  emotional temperature, hook-and-transition patterns.\n"
        "- If you find yourself describing a specific phrase or punctuation tic → drop it,\n"
        "  put it in taboo_copy_signals instead.\n"
        "\n"
        "# THINKING (in your head before JSON)\n"
        "1. From samples, abstract WHAT the author does (POV / rhythm / paragraphing).\n"
        "2. For each, generalize 1 level (e.g. 'tight third with internal monologue' NOT 'always uses semicolons').\n"
        "3. Build taboo_copy_signals — concrete things future authors must NOT copy verbatim.\n"
        "4. Self-check: could this card describe 10 different authors in the same genre? If too specific, generalize.\n"
        "\n"
        "# OUTPUT FORMAT\n"
        "Strict JSON object. No markdown, no commentary."
    )
    craft_user = "\n".join(
        [
            "=== DISTILLATION_TASK: author_craft_card ===",
            f"SOURCE_ID: {source_id}",
            "BOOK_DESIGN_CARD_JSON:",
            json.dumps(book_raw, ensure_ascii=False),
            "VOLUME_CARDS_JSON:",
            json.dumps(volume_rows, ensure_ascii=False),
            "SAMPLE_COMPRESSED_CHAPTER_CARDS_JSON:",
            json.dumps(sample_chapters, ensure_ascii=False),
            "",
            "Return JSON with fields:",
            "source_id, source_type, status, style_safety_policy, pov_and_distance,",
            "sentence_rhythm, paragraphing, dialogue_system, description_strategy,",
            "exposition_strategy, emotional_temperature, hooking_and_transitions,",
            "adaptation_guidelines, taboo_copy_signals, confidence.",
            "source_id must equal SOURCE_ID; source_type must be distillation_package;",
            "status must be draft_review.",
        ]
    )
    craft_raw = await complete_distillation_json(
        session,
        settings,
        logical_role=cast(LLMRole, "summarizer"),
        system_prompt=craft_system,
        user_prompt=craft_user,
        prompt_template="distillation_author_craft_card",
        max_tokens=4096,
        metadata={"distillation_source_id": source_id},
    )
    craft_raw = _coerce_author_craft_card(craft_raw, source_id=source_id)
    validate_author_craft_card(craft_raw)
    write_json(package_dir / "author_craft_card.json", craft_raw)

    tail_system = (
        "# ROLE\n"
        "You are the distillation bundle finalizer. After volume_card / book_design_card /\n"
        "author_craft_card are in hand, you produce the three TAIL artifacts that close out the\n"
        "distillation package:\n"
        "  1. mechanism_candidates — promotion candidates for the cross-project library\n"
        "  2. anti_copy_ledger — hard taboos future books MUST not copy from this source\n"
        "  3. material_entries_review — reusable material entries pending review\n"
        "\n"
        "# CONTEXT\n"
        "Downstream consumer: the library curator (human + LLM). They will decide which mechanisms\n"
        "get promoted to the cross-project library, and which copy-signals get globally blocked.\n"
        "Anything you mark `status: review` will sit in a review queue, NOT auto-promoted.\n"
        "\n"
        "# TASK\n"
        "Output ONE JSON object with three top-level keys:\n"
        "- mechanism_candidates: array of {source_id, mechanism_id, candidate_type, summary,\n"
        "  evidence_scope, promotion_target, status, confidence}\n"
        "- anti_copy_ledger: {source_id, blocked_categories, blocked_combinations, replacement_policy}\n"
        "- material_entries_review: array of {dimension, slug, name, narrative_summary, content_json,\n"
        "  genre, sub_genre, tags, source_type, confidence, status}\n"
        "\n"
        "# CONSTRAINTS\n"
        "- All material_entries_review[*].status = 'review' (never 'active').\n"
        "- Abstract reusable mechanisms + anonymous writing-craft controls ONLY.\n"
        "- FORBIDDEN: names, places, titles, file paths, author imitation directives.\n"
        "- anti_copy_ledger.blocked_categories must list specific source-fingerprint categories\n"
        "  (e.g. 'specific magic system terminology', 'specific dynasty names').\n"
        "\n"
        "# THINKING (in your head before JSON)\n"
        "1. From the three input cards, identify 3-10 transferable mechanisms worth promoting.\n"
        "2. For each: name it abstractly, cite which volumes evidence it, set confidence honestly.\n"
        "3. Build anti_copy_ledger: what's so specific to this source that future books must NOT replicate it?\n"
        "4. Build material_entries: anonymous, slug-cased, review-status.\n"
        "\n"
        "# OUTPUT FORMAT\n"
        "Strict JSON object. No markdown, no commentary."
    )
    tail_user = "\n".join(
        [
            "=== DISTILLATION_TASK: book_tail_bundle ===",
            f"SOURCE_ID: {source_id}",
            "BOOK_DESIGN_CARD_JSON:",
            json.dumps(book_raw, ensure_ascii=False),
            "AUTHOR_CRAFT_CARD_JSON:",
            json.dumps(craft_raw, ensure_ascii=False),
            "VOLUME_CARDS_JSON:",
            json.dumps(volume_rows, ensure_ascii=False),
        ]
    )
    tail = await complete_distillation_json(
        session,
        settings,
        logical_role=cast(LLMRole, "summarizer"),
        system_prompt=tail_system,
        user_prompt=tail_user,
        prompt_template="distillation_book_tail_bundle",
        max_tokens=8192,
        metadata={"distillation_source_id": source_id},
    )
    mechs, ledger, mats = _coerce_tail_bundle(
        tail,
        source_id=source_id,
    )
    ledger["source_id"] = source_id
    validate_anti_copy_ledger(ledger)

    write_jsonl(package_dir / "mechanism_candidates.jsonl", mechs)
    write_json(package_dir / "anti_copy_ledger.json", ledger)
    write_jsonl(package_dir / "material_entries.review.jsonl", mats)

    agg_key = infer_aggregate_key(manifest)
    gram_system = (
        "# ROLE\n"
        "You are a story-design grammar patch composer. After all source-level distillation cards\n"
        "are in hand, you produce ONE patch that updates the cross-project story-design grammar\n"
        "for this aggregate genre bucket.\n"
        "\n"
        "# CONTEXT\n"
        "story_design_grammars/<aggregate_key>.yaml is the canonical 'how chapters in this genre\n"
        "should be structured' spec consumed by every future project in the bucket.\n"
        "Your patch will be merged into it — be precise, abstract, and bucket-applicable.\n"
        "\n"
        "# TASK\n"
        "Return ONE JSON object with keys:\n"
        "- key (must match AGGREGATE_KEY)\n"
        "- name, source_ids, status\n"
        "- applies_to_categories (which sub-genres benefit from this patch)\n"
        "- required_contracts (must-haves a chapter MUST satisfy in this grammar)\n"
        "- state_variables (what tracked state evolves chapter-by-chapter)\n"
        "- chapter_change_vectors (what must change between adjacent chapters)\n"
        "- reader_rewards (what payoff types this grammar expects)\n"
        "- hook_or_aftereffect_types (how chapters end)\n"
        "- forbidden_defaults (what generic defaults this bucket REJECTS)\n"
        "\n"
        "# CONSTRAINTS\n"
        "- key must exactly equal the AGGREGATE_KEY supplied in user prompt.\n"
        "- All entries must be abstract / bucket-applicable, NOT source-specific.\n"
        "- forbidden_defaults must be concrete enough to act on (e.g. 'do not open chapter with\n"
        "  generic dream sequence' — not 'avoid clichés').\n"
        "- Strict JSON. No markdown.\n"
        "\n"
        "# THINKING (in your head before JSON)\n"
        "1. Read book_design_card → infer the chapter-level shape this genre needs.\n"
        "2. Identify state variables that drive chapter-to-chapter momentum.\n"
        "3. Specify required_contracts at the smallest level chapters must always honor.\n"
        "4. List forbidden_defaults — what bad defaults future LLMs MUST avoid in this bucket."
    )
    gram_user = "\n".join(
        [
            "=== DISTILLATION_TASK: grammar_patch ===",
            f"AGGREGATE_KEY: {agg_key}",
            f"SOURCE_ID: {source_id}",
            "BOOK_DESIGN_CARD_JSON:",
            json.dumps(book_raw, ensure_ascii=False),
        ]
    )
    try:
        gram_raw = await complete_distillation_json(
            session,
            settings,
            logical_role=cast(LLMRole, "planner"),
            system_prompt=gram_system,
            user_prompt=gram_user,
            prompt_template="distillation_grammar_patch",
            max_tokens=4096,
            metadata={"distillation_source_id": source_id, "aggregate_key": agg_key},
        )
        if not isinstance(gram_raw, dict):
            raise ValueError("grammar patch must be object")
        gram_raw.setdefault("key", agg_key)
        gram_raw.setdefault("source_ids", [source_id])
        gram_raw.setdefault("status", "review")
    except Exception as exc:  # noqa: BLE001
        logger.warning("grammar LLM failed, using fallback: %s", exc)
        gram_raw = grammar_patch_fallback(
            aggregate_key=agg_key,
            source_id=source_id,
            book_design=book_raw,
        )

    gpath = package_dir / "grammar_patch.yaml"
    _atomic_write_text(gpath, yaml.safe_dump(gram_raw, allow_unicode=True, sort_keys=False))

    active_rows, rejected = promote_review_rows_to_active(
        mats,
        anti_copy_ledger=ledger,
        source_id=source_id,
    )
    if write_active_artifacts:
        write_jsonl(package_dir / "material_entries.active.jsonl", active_rows)
    rej_path = private_errors_dir / f"{source_id}_material_rejected.json"
    _atomic_write_text(rej_path, json.dumps(rejected, ensure_ascii=False, indent=2) + "\n")

    return BookAggregationResult(
        source_id=source_id,
        volume_cards_written=len(volume_rows),
        book_design_written=True,
        author_craft_written=True,
        mechanism_rows=len(mechs),
        material_review_rows=len(mats),
        material_active_rows=len(active_rows) if write_active_artifacts else 0,
        errors=tuple(),
    )


def package_book_phase_complete(package_dir: Path) -> bool:
    """Return True if all six book-phase artifacts exist."""

    names = (
        "volume_cards.jsonl",
        "book_design_card.json",
        "author_craft_card.json",
        "mechanism_candidates.jsonl",
        "anti_copy_ledger.json",
        "grammar_patch.yaml",
    )
    for n in names:
        if not (package_dir / n).is_file():
            return False
    if _first_existing(package_dir, MATERIAL_REVIEW_FILENAMES) is None:
        return False
    return True


def write_aggregate_active_materials(
    aggregate_dir: Path,
    *,
    anti_copy_rules_path: Path,
    private_reports_dir: Path,
) -> tuple[int, int]:
    """Filter aggregate ``material_entries.review.jsonl`` into ``material_entries.active.jsonl``."""

    review_path = aggregate_dir / "material_entries.review.jsonl"
    if not review_path.is_file():
        return 0, 0
    rows = read_jsonl(review_path)
    ledger: dict[str, Any] = {}
    if anti_copy_rules_path.is_file():
        loaded = read_json(anti_copy_rules_path)
        if isinstance(loaded, dict):
            ledger = loaded
    active_out: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for row in rows:
        sids: list[str] = []
        cj = row.get("content_json")
        if isinstance(cj, dict):
            raw_ids = cj.get("distillation_source_ids")
            if isinstance(raw_ids, list):
                sids = [str(x) for x in raw_ids if str(x).startswith("source-")]
        sid = sids[0] if sids else "source-unknown"
        a_rows, rej = promote_review_rows_to_active([row], anti_copy_ledger=ledger, source_id=sid)
        active_out.extend(a_rows)
        rejected.extend(rej)
    write_jsonl(aggregate_dir / "material_entries.active.jsonl", active_out)
    private_reports_dir.mkdir(parents=True, exist_ok=True)
    _atomic_write_text(
        private_reports_dir / f"aggregate_{aggregate_dir.name}_material_rejected.json",
        json.dumps(rejected, ensure_ascii=False, indent=2) + "\n",
    )
    return len(rows), len(active_out)
