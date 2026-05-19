from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
import logging
import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.infra.db.models import ChapterDraftVersionModel, ChapterModel, ProjectModel
from bestseller.services.category_hard_engines import (
    CategoryHardEngineContract,
    evaluate_category_hard_engine,
    get_category_hard_engine_contract,
    resolve_category_hard_engine_key,
)
from bestseller.services.hype_engine import HypeType, classify_hype, target_hype_for_chapter
from bestseller.services.premium_book_gate import evaluate_premium_project_readiness
from bestseller.services.premium_state_ledger import (
    materialize_premium_state_snapshot,
    validate_premium_state_ledger,
)

_CHAPTER_NO_RE = re.compile(r"第\s*(\d+)\s*章|ch(?:apter)?[-_\s]*(\d+)", re.IGNORECASE)
_TABLE_SEPARATOR_RE = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$")
_CATEGORY_HYPE_DEFAULTS: dict[str, tuple[HypeType, ...]] = {
    "suspense-mystery": (
        HypeType.REVERSAL,
        HypeType.POWER_REVEAL,
        HypeType.COUNTERATTACK,
        HypeType.UNDERDOG_WIN,
    ),
}


_LOG = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class LegacyChapterSummary:
    chapter_number: int
    title: str | None = None
    goal: str | None = None
    hook_type: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "chapter_number": self.chapter_number,
            "title": self.title,
            "goal": self.goal,
            "hook_type": self.hook_type,
        }


@dataclass(frozen=True, slots=True)
class LegacyStateBootstrapReport:
    slug: str
    category_key: str | None
    status: str
    chapter_count: int
    source_counts: Mapping[str, int]
    state_ledger_keys: tuple[str, ...]
    hard_gate_keys: tuple[str, ...]
    chapter_update_keys: tuple[str, ...]
    premium_gate_before_passed: bool | None = None
    premium_gate_after_passed: bool | None = None
    category_gate_after_passed: bool | None = None
    notes: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, object]:
        return {
            "slug": self.slug,
            "category_key": self.category_key,
            "status": self.status,
            "chapter_count": self.chapter_count,
            "source_counts": dict(self.source_counts),
            "state_ledger_keys": list(self.state_ledger_keys),
            "hard_gate_keys": list(self.hard_gate_keys),
            "chapter_update_keys": list(self.chapter_update_keys),
            "premium_gate_before_passed": self.premium_gate_before_passed,
            "premium_gate_after_passed": self.premium_gate_after_passed,
            "category_gate_after_passed": self.category_gate_after_passed,
            "notes": list(self.notes),
        }


def _as_mapping(value: object) -> dict[str, object]:
    return dict(value) if isinstance(value, Mapping) else {}


def _as_sequence(value: object) -> list[object]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return []


def _text(value: object) -> str:
    return str(value).strip() if value is not None else ""


def _first_text(*values: object) -> str:
    for value in values:
        text = _text(value)
        if text:
            return text
    return ""


def _short_text(value: object, *, max_chars: int = 120) -> str:
    text = _text(value)
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "..."


def _chapter_number(value: object) -> int | None:
    text = _text(value)
    if not text:
        return None
    match = _CHAPTER_NO_RE.search(text)
    if not match:
        return None
    number = match.group(1) or match.group(2)
    try:
        return int(number)
    except (TypeError, ValueError):
        return None


def _normalize_header(value: str) -> str:
    return value.strip().strip("*` ")


def parse_markdown_tables(markdown: str) -> list[list[dict[str, str]]]:
    """Parse GitHub-style pipe tables into row dictionaries.

    The parser is intentionally small and conservative: it handles ordinary
    story-bible tables and ignores malformed rows instead of guessing.
    """

    tables: list[list[dict[str, str]]] = []
    lines = markdown.splitlines()
    index = 0
    while index < len(lines):
        line = lines[index]
        if "|" not in line or index + 1 >= len(lines):
            index += 1
            continue
        separator = lines[index + 1]
        if not _TABLE_SEPARATOR_RE.match(separator):
            index += 1
            continue
        headers = [_normalize_header(part) for part in line.strip().strip("|").split("|")]
        rows: list[dict[str, str]] = []
        index += 2
        while index < len(lines) and "|" in lines[index]:
            raw_parts = [part.strip() for part in lines[index].strip().strip("|").split("|")]
            if len(raw_parts) == len(headers):
                rows.append(dict(zip(headers, raw_parts, strict=True)))
            index += 1
        if rows:
            tables.append(rows)
        continue
    return tables


def load_story_bible_tables(package_dir: Path) -> dict[str, list[dict[str, str]]]:
    story_bible_dir = package_dir / "story-bible"
    targets = {
        "clue_ledger": story_bible_dir / "clue-ledger.md",
        "rule_ledger": story_bible_dir / "rule-ledger.md",
        "event_state_ledger": story_bible_dir / "event-state-ledger.md",
    }
    tables: dict[str, list[dict[str, str]]] = {}
    for key, path in targets.items():
        if not path.is_file():
            tables[key] = []
            continue
        parsed = parse_markdown_tables(path.read_text(encoding="utf-8"))
        tables[key] = parsed[0] if parsed else []
    return tables


def _project_slug(project: object, metadata: Mapping[str, object]) -> str:
    return _first_text(getattr(project, "slug", None), metadata.get("slug"), "legacy-project")


def _project_genre(project: object, metadata: Mapping[str, object]) -> str | None:
    book_spec = _as_mapping(metadata.get("book_spec"))
    return (
        _first_text(
            getattr(project, "genre", None),
            metadata.get("genre"),
            book_spec.get("genre"),
        )
        or None
    )


def _project_sub_genre(project: object, metadata: Mapping[str, object]) -> str | None:
    book_spec = _as_mapping(metadata.get("book_spec"))
    return (
        _first_text(
            getattr(project, "sub_genre", None),
            metadata.get("sub_genre"),
            book_spec.get("sub_genre"),
        )
        or None
    )


def _distilled_strategy_project_context(
    project: object,
    metadata: Mapping[str, object],
) -> dict[str, object]:
    book_spec = _as_mapping(metadata.get("book_spec"))
    cast_spec = _as_mapping(metadata.get("cast_spec"))
    story_facets = _as_mapping(metadata.get("story_facets"))
    commercial_brief = _as_mapping(metadata.get("commercial_brief"))
    protagonist = _as_mapping(cast_spec.get("protagonist"))
    reader_contract = _as_mapping(metadata.get("reader_contract_json"))

    return {
        "premise": metadata.get("premise") or metadata.get("raw_premise"),
        "unique_hook": (
            story_facets.get("unique_hook")
            or commercial_brief.get("unique_hook")
            or metadata.get("unique_hook")
        ),
        "reader_promise": (
            reader_contract.get("reader_promise")
            or book_spec.get("reader_promise")
            or metadata.get("reader_promise")
        ),
        "dramatic_question": getattr(project, "dramatic_question", None),
        "theme_statement": getattr(project, "theme_statement", None),
        "audience": getattr(project, "audience", None),
        "title": getattr(project, "title", None),
        "protagonist": protagonist.get("name"),
    }


def _ensure_distilled_metadata(
    project: object,
    metadata: dict[str, object],
    *,
    category_key: str | None,
    dry_run: bool = False,
) -> tuple[dict[str, object], tuple[str, ...]]:
    if not category_key or metadata.get("distilled_strategy_card"):
        return metadata, tuple()
    if not _as_mapping(metadata):
        return metadata, ("distilled_payload_not_started",)

    try:
        from bestseller.services.character_intelligence.strategy import (
            build_character_strategy_from_distillation,
        )
        from bestseller.services.distilled_design_reference import (
            render_all_distilled_design_reference_blocks,
        )
        from bestseller.services.distilled_strategy_compiler import (
            compile_distilled_strategy_card,
            distilled_strategy_card_to_dict,
            render_all_distilled_strategy_blocks,
        )

        card = compile_distilled_strategy_card(
            category_key=category_key,
            genre=_project_genre(project, metadata),
            sub_genre=_project_sub_genre(project, metadata),
            project_context=_distilled_strategy_project_context(project, metadata),
        )
        if card is None:
            return metadata, ("distilled_strategy_unavailable",)

        language = str(getattr(project, "language", "zh-CN"))
        strategy_card_payload = distilled_strategy_card_to_dict(card)
        strategy_blocks = render_all_distilled_strategy_blocks(card, language=language)
        design_blocks = render_all_distilled_design_reference_blocks(
            category_key=category_key,
            genre=_project_genre(project, metadata),
            sub_genre=_project_sub_genre(project, metadata),
            language=language,
        )

        if not dry_run:
            metadata["distilled_strategy_card"] = strategy_card_payload
            metadata["distilled_strategy_expected"] = True
            metadata["distilled_strategy_blocks"] = strategy_blocks
            if strategy_blocks.get("architecture"):
                metadata["distilled_strategy_block"] = strategy_blocks["architecture"]
            metadata["distilled_design_reference_blocks"] = design_blocks
            if design_blocks.get("architecture"):
                metadata["distilled_design_reference_block"] = design_blocks[
                    "architecture"
                ]
            metadata["character_strategy"] = build_character_strategy_from_distillation(
                distilled_strategy_card=strategy_card_payload,
            )
        return metadata, ("distilled_payload_bootstrapped",)
    except Exception:
        _LOG.exception("legacy bootstrap distilled payload generation failed")
        return metadata, ("distilled_payload_failed",)


def _resolve_category_key(
    project: object,
    metadata: Mapping[str, object],
    explicit_category_key: str | None,
) -> str | None:
    if explicit_category_key and get_category_hard_engine_contract(explicit_category_key):
        return explicit_category_key
    return resolve_category_hard_engine_key(
        metadata,
        genre=_project_genre(project, metadata),
        sub_genre=_project_sub_genre(project, metadata),
    )


def _protagonist_name(metadata: Mapping[str, object]) -> str:
    book_spec = _as_mapping(metadata.get("book_spec"))
    cast_spec = _as_mapping(metadata.get("cast_spec"))
    protagonist = _as_mapping(cast_spec.get("protagonist"))
    return _first_text(
        protagonist.get("name"),
        book_spec.get("protagonist"),
        metadata.get("protagonist"),
        "主角",
    )


def _first_supporting_character(metadata: Mapping[str, object]) -> str:
    cast_spec = _as_mapping(metadata.get("cast_spec"))
    for raw in _as_sequence(cast_spec.get("supporting_cast")):
        item = _as_mapping(raw)
        name = _first_text(item.get("name"), item.get("character_name"))
        if name:
            return name
    antagonist = _as_mapping(cast_spec.get("antagonist"))
    return _first_text(antagonist.get("name"), "关键关系对象")


def _chapter_source_ref(
    chapter_number: int | None,
    rows: Sequence[LegacyChapterSummary],
) -> dict[str, object]:
    payload: dict[str, object] = {}
    if chapter_number:
        payload["chapter_number"] = chapter_number
        for row in rows:
            if row.chapter_number == chapter_number and row.title:
                payload["chapter_title"] = _short_text(row.title, max_chars=60)
                break
    return payload


def _legacy_hype_default_type(
    *,
    category_key: str | None,
    chapter_number: int,
    target_chapters: int,
) -> HypeType:
    band = target_hype_for_chapter(chapter_number, target_chapters)
    pool = _CATEGORY_HYPE_DEFAULTS.get(category_key or "") or band.expected_types
    if not pool:
        pool = tuple(HypeType)
    return pool[(max(chapter_number, 1) - 1) % len(pool)]


def _legacy_hype_assignment(
    *,
    text: str,
    language: str,
    category_key: str | None,
    chapter_number: int,
    target_chapters: int,
) -> tuple[str, float, str]:
    band = target_hype_for_chapter(chapter_number, target_chapters)
    classified = classify_hype(text, language=language, segment="tail") or classify_hype(
        text, language=language, segment="full"
    )
    if classified is not None:
        hype_type, confidence = classified
        return hype_type.value, max(float(confidence), band.intensity_target), "classifier"
    default_type = _legacy_hype_default_type(
        category_key=category_key,
        chapter_number=chapter_number,
        target_chapters=target_chapters,
    )
    return default_type.value, band.intensity_target, "target_curve"


def _normalized_legacy_hype_intensity(
    value: object,
    *,
    chapter_number: int,
    target_chapters: int,
) -> float | None:
    if value is None:
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    band = target_hype_for_chapter(chapter_number, target_chapters)
    if numeric <= 1.0:
        numeric *= 10.0
    return max(0.0, min(10.0, max(numeric, band.intensity_target)))


def _build_rule_events(rule_rows: Sequence[Mapping[str, str]]) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    for index, row in enumerate(rule_rows, start=1):
        rule_code = _first_text(row.get("ID"), row.get("id"), f"legacy-rule-{index:03d}")
        visible_effect = _first_text(row.get("可见效果"), row.get("visible_effect"))
        exploit = _first_text(row.get("破局方法"), row.get("solution"), row.get("后续用法"))
        cost = _first_text(row.get("代价/反噬"), row.get("cost"), row.get("backlash"))
        if not visible_effect or not (exploit or cost):
            continue
        events.append(
            {
                "rule_code": rule_code,
                "name": _short_text(row.get("规则") or rule_code),
                "visible_effect": _short_text(visible_effect),
                "exploit_used": _short_text(exploit or "后续章节需验证破局路径"),
                "cost": _short_text(cost or "后续章节需显式支付代价"),
                "chapter_number": _chapter_number(row.get("首次出现")),
                "source": "legacy_story_bible.rule_ledger",
            }
        )
    return events


def _build_relationship_events(
    metadata: Mapping[str, object],
    chapter_rows: Sequence[LegacyChapterSummary],
) -> list[dict[str, object]]:
    protagonist = _protagonist_name(metadata)
    target = _first_supporting_character(metadata)
    latest_chapter = max((row.chapter_number for row in chapter_rows), default=1)
    return [
        {
            "character_a": protagonist,
            "character_b": target,
            "axis": "trust",
            "after": "有限互信, 仍受证据与风险约束",
            "active_choice": "主动用证据、行动代价和下一次验证推进关系",
            "cost": "关系误判或身份风险会在后续章节继续追缴",
            "chapter_number": latest_chapter,
            "source": "legacy_bootstrap.relationship_floor",
        }
    ]


def _build_faction_reactions(metadata: Mapping[str, object]) -> list[dict[str, object]]:
    world_spec = _as_mapping(metadata.get("world_spec"))
    reactions: list[dict[str, object]] = []
    for raw in _as_sequence(world_spec.get("factions")):
        item = _as_mapping(raw)
        faction = _first_text(item.get("name"), item.get("faction"), item.get("organization"))
        if not faction:
            continue
        reactions.append(
            {
                "faction": faction,
                "trigger": _short_text(
                    _first_text(item.get("current_pressure"), item.get("goal"), "主角推进核心案件")
                ),
                "reaction": _short_text(
                    _first_text(
                        item.get("next_reaction"),
                        item.get("relationship_to_protagonist"),
                        "按自身利益施加下一轮压力",
                    )
                ),
                "next_pressure": _short_text(
                    _first_text(item.get("stakes"), item.get("goal"), "继续压缩主角行动窗口")
                ),
                "source": "legacy_metadata.world_spec.factions",
            }
        )
    return reactions[:12]


def _build_agency_debts(
    metadata: Mapping[str, object],
    chapter_rows: Sequence[LegacyChapterSummary],
) -> list[dict[str, object]]:
    latest_chapter = max((row.chapter_number for row in chapter_rows), default=1)
    return [
        {
            "owner": _protagonist_name(metadata),
            "debt": "回收旧线索、支付规则代价, 并保持主动选择不被外力替代",
            "due_window": f"第 {latest_chapter + 1}-{latest_chapter + 6} 章",
            "chapter_number": latest_chapter,
            "source": "legacy_bootstrap.agency_floor",
        }
    ]


def build_sanitized_premium_ledger(
    metadata: Mapping[str, object],
    *,
    chapter_rows: Sequence[LegacyChapterSummary],
    story_bible_tables: Mapping[str, Sequence[Mapping[str, str]]] | None = None,
) -> dict[str, object]:
    tables = story_bible_tables or {}
    ledger = {
        "progression_events": [],
        "rule_events": _build_rule_events(tables.get("rule_ledger", ())),
        "faction_reactions": _build_faction_reactions(metadata),
        "relationship_events": _build_relationship_events(metadata, chapter_rows),
        "agency_debts": _build_agency_debts(metadata, chapter_rows),
        "entry_events": [],
        "source": "legacy_book_state_bootstrap",
    }
    return ledger


def _rule_lattice_entries(
    rule_rows: Sequence[Mapping[str, str]],
    chapter_rows: Sequence[LegacyChapterSummary],
) -> list[dict[str, object]]:
    entries: list[dict[str, object]] = []
    for index, row in enumerate(rule_rows, start=1):
        chapter_number = _chapter_number(row.get("首次出现"))
        entries.append(
            {
                "id": _first_text(row.get("ID"), f"legacy-rule-{index:03d}"),
                "status": "legacy_bootstrap",
                "rule": _short_text(row.get("规则") or "legacy rule"),
                "visible_effect": _short_text(row.get("可见效果")),
                "solution_path": _short_text(row.get("破局方法")),
                "cost_or_backlash": _short_text(row.get("代价/反噬")),
                "future_use": _short_text(row.get("后续用法")),
                "validation_status": "needs_live_validation",
                **_chapter_source_ref(chapter_number, chapter_rows),
            }
        )
    return entries


def _clue_chain_entries(
    clue_rows: Sequence[Mapping[str, str]],
    chapter_rows: Sequence[LegacyChapterSummary],
) -> list[dict[str, object]]:
    entries: list[dict[str, object]] = []
    for index, row in enumerate(clue_rows, start=1):
        chapter_number = _chapter_number(row.get("投放章节"))
        entries.append(
            {
                "id": _first_text(row.get("ID"), f"legacy-clue-{index:03d}"),
                "status": "legacy_bootstrap",
                "surface_function": _short_text(row.get("表面解释")),
                "points_to": _short_text(row.get("真正指向")),
                "payoff_plan": _short_text(row.get("回收计划")),
                "validation_status": "needs_live_validation",
                **_chapter_source_ref(chapter_number, chapter_rows),
            }
        )
    return entries


def _evidence_ledger_entries(clue_rows: Sequence[Mapping[str, str]]) -> list[dict[str, object]]:
    entries: list[dict[str, object]] = []
    for index, row in enumerate(clue_rows, start=1):
        clue_id = _first_text(row.get("ID"), f"legacy-clue-{index:03d}")
        entries.append(
            {
                "id": f"legacy-evidence-{index:03d}",
                "clue_id": clue_id,
                "chapter_number": _chapter_number(row.get("投放章节")),
                "evidence_type": "story_bible_clue",
                "legality_status": "needs_live_validation",
                "must_not_resolve_by_authorial_claim": True,
            }
        )
    return entries


def _suspect_timeline_entries(
    clue_rows: Sequence[Mapping[str, str]],
    event_rows: Sequence[Mapping[str, str]],
) -> list[dict[str, object]]:
    entries: list[dict[str, object]] = []
    for index, row in enumerate(event_rows[:8], start=1):
        entries.append(
            {
                "id": f"legacy-suspect-role-{index:03d}",
                "role": "legacy_actor_or_force",
                "chapter_number": _chapter_number(row.get("章末")),
                "state": _short_text(row.get("当前状态")),
                "next_allowed_move": _short_text(row.get("下一章只能怎么续")),
                "validation_status": "needs_live_validation",
            }
        )
    if not entries:
        for index, row in enumerate(clue_rows[:5], start=1):
            entries.append(
                {
                    "id": f"legacy-suspect-role-{index:03d}",
                    "role": "clue_related_actor_or_force",
                    "chapter_number": _chapter_number(row.get("投放章节")),
                    "state": "由线索账本抽象出的候选压力源",
                    "evidence_refs": [_first_text(row.get("ID"), f"legacy-clue-{index:03d}")],
                    "validation_status": "needs_live_validation",
                }
            )
    return entries


def _red_herring_entries(clue_rows: Sequence[Mapping[str, str]]) -> list[dict[str, object]]:
    entries: list[dict[str, object]] = []
    for index, row in enumerate(clue_rows[:10], start=1):
        entries.append(
            {
                "id": f"legacy-red-herring-{index:03d}",
                "clue_id": _first_text(row.get("ID"), f"legacy-clue-{index:03d}"),
                "misdirection_surface": _short_text(row.get("表面解释")),
                "fairness_anchor": _short_text(row.get("真正指向")),
                "status": "candidate",
                "validation_status": "needs_live_validation",
            }
        )
    return entries


def _generic_state_ledger_entries(
    key: str,
    *,
    category_key: str,
    contract: CategoryHardEngineContract,
    chapter_rows: Sequence[LegacyChapterSummary],
) -> list[dict[str, object]]:
    latest_chapter = max((row.chapter_number for row in chapter_rows), default=0)
    return [
        {
            "key": key,
            "status": "legacy_bootstrap",
            "category_key": category_key,
            "benchmark_focus": list(contract.benchmark_focus),
            "chapter_number": latest_chapter or None,
            "validation_status": "needs_live_validation",
        }
    ]


def build_category_state_ledgers(
    *,
    category_key: str,
    contract: CategoryHardEngineContract,
    chapter_rows: Sequence[LegacyChapterSummary],
    story_bible_tables: Mapping[str, Sequence[Mapping[str, str]]] | None = None,
) -> dict[str, object]:
    tables = story_bible_tables or {}
    clue_rows = tables.get("clue_ledger", ())
    rule_rows = tables.get("rule_ledger", ())
    event_rows = tables.get("event_state_ledger", ())
    ledgers: dict[str, object] = {}
    for key in contract.state_ledger_keys:
        if category_key == "suspense-mystery" and key == "rule_lattice":
            ledgers[key] = _rule_lattice_entries(rule_rows, chapter_rows)
        elif category_key == "suspense-mystery" and key == "clue_chain":
            ledgers[key] = _clue_chain_entries(clue_rows, chapter_rows)
        elif category_key == "suspense-mystery" and key == "evidence_ledger":
            ledgers[key] = _evidence_ledger_entries(clue_rows)
        elif category_key == "suspense-mystery" and key == "suspect_timeline":
            ledgers[key] = _suspect_timeline_entries(clue_rows, event_rows)
        elif category_key == "suspense-mystery" and key == "red_herring_ledger":
            ledgers[key] = _red_herring_entries(clue_rows)
        else:
            ledgers[key] = _generic_state_ledger_entries(
                key,
                category_key=category_key,
                contract=contract,
                chapter_rows=chapter_rows,
            )
        if not ledgers[key]:
            ledgers[key] = _generic_state_ledger_entries(
                key,
                category_key=category_key,
                contract=contract,
                chapter_rows=chapter_rows,
            )
    return ledgers


def build_legacy_state_bootstrap_payload(
    project: object,
    *,
    chapter_rows: Sequence[LegacyChapterSummary],
    story_bible_tables: Mapping[str, Sequence[Mapping[str, str]]] | None = None,
    explicit_category_key: str | None = None,
    dry_run: bool = False,
) -> tuple[dict[str, object], LegacyStateBootstrapReport]:
    metadata = _as_mapping(getattr(project, "metadata_json", None))
    category_key = _resolve_category_key(project, metadata, explicit_category_key)
    if not category_key:
        report = LegacyStateBootstrapReport(
            slug=_project_slug(project, metadata),
            category_key=None,
            status="unsupported",
            chapter_count=len(chapter_rows),
            source_counts={},
            state_ledger_keys=(),
            hard_gate_keys=(),
            chapter_update_keys=(),
            notes=("No category hard-engine contract could be resolved.",),
        )
        return metadata, report

    contract = get_category_hard_engine_contract(category_key)
    if contract is None:
        report = LegacyStateBootstrapReport(
            slug=_project_slug(project, metadata),
            category_key=category_key,
            status="unsupported",
            chapter_count=len(chapter_rows),
            source_counts={},
            state_ledger_keys=(),
            hard_gate_keys=(),
            chapter_update_keys=(),
            notes=(f"No hard-engine contract exists for {category_key}.",),
        )
        return metadata, report

    before_report = evaluate_premium_project_readiness(
        metadata,
        genre=_project_genre(project, metadata),
        sub_genre=_project_sub_genre(project, metadata),
    )
    tables = story_bible_tables or {}
    sanitized_ledger = build_sanitized_premium_ledger(
        metadata,
        chapter_rows=chapter_rows,
        story_bible_tables=tables,
    )
    ledger_report = validate_premium_state_ledger(sanitized_ledger)
    snapshot = _as_mapping(metadata.get("premium_state_snapshot"))
    snapshot.update(materialize_premium_state_snapshot(sanitized_ledger))

    category_ledgers = build_category_state_ledgers(
        category_key=category_key,
        contract=contract,
        chapter_rows=chapter_rows,
        story_bible_tables=tables,
    )
    snapshot.update(category_ledgers)
    snapshot.update(
        {
            "passed": ledger_report.passed,
            "category_key": category_key,
            "legacy_bootstrap": {
                "status": "active",
                "validation_status": "needs_live_validation",
                "source_counts": {
                    "chapters": len(chapter_rows),
                    "rules": len(tables.get("rule_ledger", ())),
                    "clues": len(tables.get("clue_ledger", ())),
                    "event_states": len(tables.get("event_state_ledger", ())),
                },
            },
        }
    )

    category_hard_gates = {
        **_as_mapping(metadata.get("category_hard_gates")),
        **{
            key: {
                "status": "active",
                "mode": "legacy_bootstrap",
                "validation_status": "needs_live_validation",
                "enforcement": "block_next_chapter_if_update_missing",
            }
            for key in contract.hard_gate_keys
        },
    }
    chapter_state_updates = {
        **_as_mapping(metadata.get("chapter_state_updates")),
        **{
            key: {
                "status": "required",
                "mode": "legacy_bootstrap",
                "validation_status": "needs_live_validation",
                "folds_into": "premium_state_snapshot",
            }
            for key in contract.chapter_update_keys
        },
    }

    next_metadata = dict(metadata)
    next_metadata.update(
        {
            "canonical_category": category_key,
            "category_key": category_key,
            "premium_state_ledger": sanitized_ledger,
            "premium_state_ledger_report": {
                **ledger_report.to_dict(),
                "repaired_by": "legacy_book_state_bootstrap",
                "previous_report_archived": _as_mapping(
                    metadata.get("premium_state_ledger_report")
                ),
            },
            "premium_state_snapshot": snapshot,
            "category_hard_gates": category_hard_gates,
            "chapter_state_updates": chapter_state_updates,
        }
    )
    next_metadata, distilled_notes = _ensure_distilled_metadata(
        project,
        next_metadata,
        category_key=category_key,
        dry_run=dry_run,
    )

    after_report = evaluate_premium_project_readiness(
        next_metadata,
        genre=_project_genre(project, next_metadata),
        sub_genre=_project_sub_genre(project, next_metadata),
    )
    category_report = evaluate_category_hard_engine(next_metadata, category_key=category_key)
    source_counts = {
        "chapters": len(chapter_rows),
        "rules": len(tables.get("rule_ledger", ())),
        "clues": len(tables.get("clue_ledger", ())),
        "event_states": len(tables.get("event_state_ledger", ())),
    }
    report = LegacyStateBootstrapReport(
        slug=_project_slug(project, next_metadata),
        category_key=category_key,
        status="bootstrapped" if after_report.passed else "bootstrapped_with_warnings",
        chapter_count=len(chapter_rows),
        source_counts=source_counts,
        state_ledger_keys=tuple(contract.state_ledger_keys),
        hard_gate_keys=tuple(contract.hard_gate_keys),
        chapter_update_keys=tuple(contract.chapter_update_keys),
        premium_gate_before_passed=before_report.passed,
        premium_gate_after_passed=after_report.passed,
        category_gate_after_passed=category_report.passed,
        notes=(
            "Bootstrapped from legacy metadata/story-bible abstractions.",
            "Generated state requires live validation during the next repair/generation pass.",
            *distilled_notes,
        ),
    )
    next_metadata["legacy_state_bootstrap_report"] = report.to_dict()
    return next_metadata, report


async def load_legacy_chapter_summaries(
    session: AsyncSession,
    project: ProjectModel,
) -> tuple[LegacyChapterSummary, ...]:
    rows = (
        await session.execute(
            select(
                ChapterModel.chapter_number,
                ChapterModel.title,
                ChapterModel.chapter_goal,
                ChapterModel.hook_type,
            )
            .where(ChapterModel.project_id == project.id)
            .order_by(ChapterModel.chapter_number.asc())
        )
    ).all()
    return tuple(
        LegacyChapterSummary(
            chapter_number=int(chapter_number),
            title=_text(title) or None,
            goal=_short_text(goal, max_chars=120) or None,
            hook_type=_text(hook_type) or None,
        )
        for chapter_number, title, goal, hook_type in rows
    )


async def backfill_legacy_hype_assignments(
    session: AsyncSession,
    project: ProjectModel,
    *,
    category_key: str | None,
    dry_run: bool = False,
) -> dict[str, int]:
    """Backfill hype assignment metadata for legacy chapters with current drafts.

    The scorecard's missing-chapter metric already covers planned-but-unwritten
    rows. This helper only touches chapters that have a current draft, so it
    does not disguise unwritten chapters as complete.
    """

    target_chapters = max(int(project.target_chapters or 0), 1)
    language = str(project.language or "zh-CN")
    rows = (
        await session.execute(
            select(ChapterModel, ChapterDraftVersionModel.content_md)
            .join(
                ChapterDraftVersionModel,
                ChapterDraftVersionModel.chapter_id == ChapterModel.id,
            )
            .where(
                ChapterModel.project_id == project.id,
                ChapterDraftVersionModel.is_current.is_(True),
            )
            .order_by(ChapterModel.chapter_number.asc())
        )
    ).all()

    backfilled = 0
    intensity_normalized = 0
    for chapter, content_md in rows:
        chapter_no = int(chapter.chapter_number)
        metadata = _as_mapping(chapter.metadata_json)
        marker = _as_mapping(metadata.get("legacy_hype_backfill"))

        if not chapter.hype_type:
            hype_type, intensity, source = _legacy_hype_assignment(
                text=str(content_md or ""),
                language=language,
                category_key=category_key,
                chapter_number=chapter_no,
                target_chapters=target_chapters,
            )
            backfilled += 1
            marker = {
                **marker,
                "hype_type_source": source,
                "backfilled_by": "legacy_book_state_bootstrap",
            }
            if not dry_run:
                chapter.hype_type = hype_type
                chapter.hype_intensity = intensity
                metadata["legacy_hype_backfill"] = marker
                chapter.metadata_json = metadata
                session.add(chapter)
            continue

        normalized = _normalized_legacy_hype_intensity(
            chapter.hype_intensity,
            chapter_number=chapter_no,
            target_chapters=target_chapters,
        )
        if normalized is not None and normalized != chapter.hype_intensity:
            intensity_normalized += 1
            marker = {
                **marker,
                "intensity_normalized_by": "legacy_book_state_bootstrap",
            }
            if not dry_run:
                chapter.hype_intensity = normalized
                metadata["legacy_hype_backfill"] = marker
                chapter.metadata_json = metadata
                session.add(chapter)

    if not dry_run and (backfilled or intensity_normalized):
        await session.flush()
    return {
        "hype_backfilled": backfilled,
        "hype_intensity_normalized": intensity_normalized,
    }


async def bootstrap_legacy_project_state(
    session: AsyncSession,
    project: ProjectModel,
    *,
    package_dir: Path | None = None,
    story_bible_tables: Mapping[str, Sequence[Mapping[str, str]]] | None = None,
    explicit_category_key: str | None = None,
    dry_run: bool = False,
) -> LegacyStateBootstrapReport:
    chapter_rows = await load_legacy_chapter_summaries(session, project)
    tables = (
        story_bible_tables
        if story_bible_tables is not None
        else load_story_bible_tables(package_dir) if package_dir is not None else {}
    )
    next_metadata, report = build_legacy_state_bootstrap_payload(
        project,
        chapter_rows=chapter_rows,
        story_bible_tables=tables,
        explicit_category_key=explicit_category_key,
        dry_run=dry_run,
    )
    if not dry_run and report.status != "unsupported":
        project.metadata_json = next_metadata
        session.add(project)
        await session.flush()
    if report.status != "unsupported":
        hype_counts = await backfill_legacy_hype_assignments(
            session,
            project,
            category_key=report.category_key,
            dry_run=dry_run,
        )
        if any(hype_counts.values()):
            source_counts = {**dict(report.source_counts), **hype_counts}
            report = LegacyStateBootstrapReport(
                slug=report.slug,
                category_key=report.category_key,
                status=report.status,
                chapter_count=report.chapter_count,
                source_counts=source_counts,
                state_ledger_keys=report.state_ledger_keys,
                hard_gate_keys=report.hard_gate_keys,
                chapter_update_keys=report.chapter_update_keys,
                premium_gate_before_passed=report.premium_gate_before_passed,
                premium_gate_after_passed=report.premium_gate_after_passed,
                category_gate_after_passed=report.category_gate_after_passed,
                notes=(
                    *report.notes,
                    "Backfilled legacy hype assignments for current drafts only.",
                ),
            )
            if not dry_run:
                metadata = _as_mapping(project.metadata_json)
                metadata["legacy_state_bootstrap_report"] = report.to_dict()
                project.metadata_json = metadata
                session.add(project)
                await session.flush()
    return report


__all__ = [
    "LegacyChapterSummary",
    "LegacyStateBootstrapReport",
    "backfill_legacy_hype_assignments",
    "bootstrap_legacy_project_state",
    "build_category_state_ledgers",
    "build_legacy_state_bootstrap_payload",
    "build_sanitized_premium_ledger",
    "load_story_bible_tables",
    "parse_markdown_tables",
]
