"""Hard-fact continuity extraction for autowrite pipelines.

End-of-chapter hard facts (countdowns, character levels, resources, locations,
inventories, story-time) are extracted by the ``editor`` role after every
chapter is finalized, then injected into the next chapter's writing prompt as a
strict continuity constraint.

This prevents cross-chapter drift — for example, a countdown timer going
``24h → 74h → 10d`` between consecutive chapters because the writer never saw
the previous chapter's end-state as a structured value.

The extractor:

* Runs on the ``editor`` role at low temperature (role-configured).
* Asks for a **strict JSON** payload only.
* Parses the first JSON object it can find, gracefully handling fenced code
  blocks (` ```json ... ``` `) and free-text wrappers.
* **Never raises** back into the pipeline: on any failure, the snapshot is
  still persisted with ``extraction_status == "failed"`` and ``facts = {}``,
  letting the next chapter fall back to the legacy context path.

Design notes
------------

* No dependency on LLM ``response_format`` / JSON-mode parameters — the
  project's ``complete_text`` wrapper does not expose one, and provider support
  varies.  Instead, we use a disciplined system prompt plus a tolerant parser.
* Facts are stored as a ``dict`` with a single ``facts`` list under it, so the
  column can later grow additional top-level keys (e.g. ``schema_version``,
  ``warnings``) without a migration.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import logging
from pathlib import Path
import re
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.domain.context import ChapterStateSnapshotContext, HardFactContext
from bestseller.infra.db.models import ChapterModel, ChapterStateSnapshotModel
from bestseller.services.budget import ExecutionBudgetError
from bestseller.services.checker_schema import CheckerIssue, CheckerReport
from bestseller.services.feedback import (
    KNOWLEDGE_LINEAGE_SCHEMA,
    KNOWLEDGE_SOURCE_KEYS,
    _knowledge_llm_receipt,
    _knowledge_operation_id,
    validate_adopted_knowledge_source,
)
from bestseller.services.llm import LLMCompletionRequest, complete_text
from bestseller.settings import AppSettings

logger = logging.getLogger(__name__)


_ALLOWED_KINDS: frozenset[str] = frozenset(
    {
        "countdown",
        "level",
        "resource",
        "location",
        "time",
        "distance",
        "inventory_count",
        "elapsed_story_time",
        "other",
    }
)

# Monotonic direction rules: which fact kinds must only go up or down
_MONOTONIC_UP_KINDS: frozenset[str] = frozenset({"level"})
_MONOTONIC_DOWN_KINDS: frozenset[str] = frozenset({"countdown"})


_FENCED_JSON_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL | re.IGNORECASE)
_BARE_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)

# Unescaped control characters (0x00-0x1F excluding TAB/LF/CR, plus 0x7F).
# These sneak into LLM JSON output and make ``json.loads`` reject the whole
# payload even with ``strict=False`` when they appear outside of strings.
_CTRL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


_EXTRACTION_SYSTEM_PROMPT = (
    "# ROLE\n"
    "你是小说连续性事实抽取器——专门服务长篇连载的「跨章硬事实清单」。\n"
    "你做过 50+ 本签约连载的连续性审计，能从一章正文中精确抽取「下一章不能违反的硬事实」。\n"
    "\n"
    "# CONTEXT\n"
    "你的产出会进入 chapter_state_snapshot 表，成为下一章生成时的「硬事实白名单」。\n"
    "下游写手会读取这份硬事实，并且不允许违反——比如本章末倒计时 20 小时，下章不能突然变 25。\n"
    "因此你的抽取必须严格保守：宁可漏一条次要事实，也不能编造一条不存在的事实。\n"
    "\n"
    "# TASK\n"
    "读完本章正文和上一章末的事实状态，输出【严格 JSON】，"
    "列出本章结束时所有可枚举的硬事实 + 本章在故事时间线上的锚点。\n"
    "\n"
    "# THINKING（产出 JSON 前在脑内 4 步）\n"
    "1. 通读本章，标记所有出现具体数字 / 等级 / 时间锚 / 位置 / 物品的段落。\n"
    "2. 对照上一章硬事实清单：哪些发生变化？哪些保持不变？哪些被本章覆盖？\n"
    "3. 对每一条事实在心中检查「能否在正文中找到一句 source_quote」——找不到 → 弃用。\n"
    "4. 用最小化原则输出：本章如实发生 + 上章未变；经过时间仅在正文明确给出时填写。\n"
    "\n"
    "# CONTEXT · 硬事实定义\n"
    "硬事实包括：倒计时时间、人物等级 / 修为 / 境界、具名资源 / 物品计数、关键位置、"
    "故事内时间点、可度量距离。\n"
    "\n"
    "JSON 格式（严格遵守）：\n"
    "{\n"
    '  "time_anchor": "末世第 4 天 清晨",\n'
    '  "chapter_time_span": "约 3 小时",\n'
    '  "facts": [\n'
    "    {\n"
    '      "name": "末日倒计时",\n'
    '      "value": "20",\n'
    '      "unit": "小时",\n'
    '      "kind": "countdown",\n'
    '      "subject": null,\n'
    '      "notes": "本章开头 24 小时，经历第一次交易损耗 4 小时",\n'
    '      "source_quote": "倒计时落到了二十小时"\n'
    "    }\n"
    "  ]\n"
    "}\n\n"
    "规则：\n"
    "1. kind 只能取：countdown | level | resource | location | time | distance | inventory_count | elapsed_story_time | other\n"
    "2. value 统一用字符串（包含数字）——JSON 数字和字符串都能，但必须避免歧义。\n"
    "3. 只抽取**本章正文里明确提到**的硬事实，不得编造、推测、补全。\n"
    "4. 不要输出 Markdown、解释、注释或任何非 JSON 内容。直接以 `{` 开头，以 `}` 结尾。\n"
    '5. 如果本章没有任何可枚举硬事实，返回 `{"facts": []}`（time_anchor / chapter_time_span 仍需尽力填写）。\n'
    '6. 如果某条事实在上一章已经出现且本章未变化，**仍要列出**（标注 notes="无变化"），便于下一章继续约束。\n'
    "7. 对 kind=level 的事实（修为/等级/境界），值只能单调递增。如果你发现本章值比上一章低，"
    "在 notes 中标注 `regression=true` 并说明原因。\n"
    "8. 对 kind=countdown 的事实，值只能单调递减。如果倒计时重置了，在 notes 中标注 `reset=true`。\n"
    "如果上一章存在倒计时，但本章正文没有明确出现新的倒计时数字，不得根据 chapter_time_span 推断多单位下降；"
    "只能沿用上一章最后有证据的数值及单位，不得自行递减；将 source_quote 置为 null，notes 标注当前剩余时间未明确。\n"
    '9. 仅当正文明确给出经过时间时，抽取 kind=elapsed_story_time 的事实（如 "3小时"、"1天"、"数分钟"）。\n'
    "10. time_anchor 字段：本章开始时的故事内时间点，格式推荐 `末世第 N 天 清晨/上午/下午/傍晚/晚上` 或 `Day N dawn/morning/noon/afternoon/evening/night`。"
    "如果本章为回忆/倒叙/闪回，**必须**在 time_anchor 中包含关键字 `flashback` 或 `回忆`。\n"
    "11. chapter_time_span 字段：仅记录正文明确给出的经过时间；无法确定时返回 null。"
    "不得根据步行、交谈、坐下等动作估算数分钟，也不得据此新增 elapsed_story_time 硬事实。\n"
    "12. 若本章实在无法判断 time_anchor（例如纯内心独白、梦境），返回 null；不得编造。"
)


def _render_previous_snapshot(previous: ChapterStateSnapshotContext | None) -> str:
    if previous is None or not previous.facts:
        return "（无 — 这是第一章）"
    lines: list[str] = [f"上一章（第 {previous.chapter_number} 章）末硬事实："]
    for fact in previous.facts:
        unit = f" {fact.unit}" if fact.unit else ""
        subj = f"[{fact.subject}] " if fact.subject else ""
        notes = f"  // {fact.notes}" if fact.notes else ""
        lines.append(f"- {subj}{fact.name}: {fact.value}{unit}  (kind={fact.kind}){notes}")
    return "\n".join(lines)


def _build_extraction_user_prompt(
    *,
    chapter: ChapterModel,
    chapter_md: str,
    previous_snapshot: ChapterStateSnapshotContext | None,
) -> str:
    return (
        f"第 {chapter.chapter_number} 章 《{chapter.title or ''}》\n\n"
        f"{_render_previous_snapshot(previous_snapshot)}\n\n"
        "=== 本章正文开始 ===\n"
        f"{chapter_md}\n"
        "=== 本章正文结束 ===\n\n"
        "请严格按照 system prompt 规定的 JSON 格式输出本章结束时的硬事实状态。"
    )


def _parse_extraction_payload(
    raw: str,
) -> tuple[list[HardFactContext], str | None, str | None, str | None]:
    """Return (facts, time_anchor, chapter_time_span, error).

    Tolerant parser: accepts fenced JSON, bare JSON, or leading/trailing text.
    """
    if not raw or not raw.strip():
        return [], None, None, "empty_response"

    candidate: str | None = None

    fenced = _FENCED_JSON_RE.search(raw)
    if fenced is not None:
        candidate = fenced.group(1)
    else:
        bare = _BARE_JSON_OBJECT_RE.search(raw)
        if bare is not None:
            candidate = bare.group(0)

    if candidate is None:
        return [], None, None, "no_json_object_found"

    # LLMs occasionally emit raw control characters (``\n``, ``\t``) inside
    # JSON string values — strict mode rejects them with
    # "Invalid control character at". ``strict=False`` accepts them so we
    # recover gracefully instead of discarding the whole snapshot.
    try:
        payload = json.loads(candidate, strict=False)
    except json.JSONDecodeError as exc:
        # Second-chance: strip the control characters and retry before
        # giving up. Some providers emit literal ``\r`` bytes mid-string
        # that even ``strict=False`` cannot handle on older payloads.
        sanitized = _CTRL_CHARS_RE.sub(" ", candidate)
        if sanitized != candidate:
            try:
                payload = json.loads(sanitized, strict=False)
            except json.JSONDecodeError as retry_exc:
                return [], None, None, f"json_decode_error:{retry_exc.msg}"
        else:
            return [], None, None, f"json_decode_error:{exc.msg}"

    if not isinstance(payload, dict):
        return [], None, None, "payload_not_object"

    raw_facts = payload.get("facts")
    if not isinstance(raw_facts, list):
        return [], None, None, "missing_facts_list"

    facts: list[HardFactContext] = []
    for entry in raw_facts:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        value = entry.get("value")
        kind = entry.get("kind")
        if not isinstance(name, str) or not name.strip():
            continue
        if value is None:
            continue
        if not isinstance(kind, str) or kind.strip() not in _ALLOWED_KINDS:
            kind = "other"
        facts.append(
            HardFactContext(
                name=name.strip(),
                value=str(value).strip(),
                unit=_optional_str(entry.get("unit")),
                kind=kind.strip(),
                subject=_optional_str(entry.get("subject")),
                notes=_optional_str(entry.get("notes")),
                source_quote=_optional_str(entry.get("source_quote")),
            )
        )

    time_anchor = _optional_str(payload.get("time_anchor"))
    chapter_time_span = _optional_str(payload.get("chapter_time_span"))

    return facts, time_anchor, chapter_time_span, None


def _optional_str(raw: Any) -> str | None:
    if raw is None:
        return None
    text = str(raw).strip()
    return text or None


def _facts_to_storage(facts: list[HardFactContext]) -> dict[str, Any]:
    return {"facts": [fact.model_dump(mode="json", exclude_none=True) for fact in facts]}


def _facts_from_storage(raw: dict[str, Any] | None) -> list[HardFactContext]:
    if not raw:
        return []
    entries = raw.get("facts") if isinstance(raw, dict) else None
    if not isinstance(entries, list):
        return []
    facts: list[HardFactContext] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        try:
            facts.append(HardFactContext.model_validate(entry))
        except Exception:  # noqa: BLE001
            logger.debug("Skipping malformed stored hard fact: %r", entry)
            continue
    return facts


def _normalize_inferred_countdown_jumps(
    facts: list[HardFactContext],
    previous: ChapterStateSnapshotContext | None,
    *,
    chapter_md: str | None = None,
) -> list[HardFactContext]:
    """Carry forward the last evidenced countdown instead of inventing progress.

    A reset or flashback note is not evidence on its own. During extraction,
    quotes must also occur in the source prose; semantic entailment is still
    the extractor's responsibility.
    """
    if previous is None or not previous.facts or not facts:
        return facts
    previous_countdowns = {
        (fact.subject, fact.name): fact for fact in previous.facts if fact.kind == _COUNTDOWN_KIND
    }
    normalized: list[HardFactContext] = []
    for fact in facts:
        prev = previous_countdowns.get((fact.subject, fact.name))
        if fact.kind != _COUNTDOWN_KIND or prev is None:
            normalized.append(fact)
            continue
        if fact.source_quote and (chapter_md is None or fact.source_quote in chapter_md):
            normalized.append(fact)
            continue
        if fact.value == prev.value and fact.unit == prev.unit and not fact.source_quote:
            normalized.append(fact)
            continue
        normalized.append(
            fact.model_copy(
                update={
                    "value": prev.value,
                    "unit": prev.unit,
                    "notes": "保留上一章最后有证据的数值；本章未提供可核对的新数值，实际剩余时间未更新。",
                    "source_quote": None,
                }
            )
        )
    return normalized


_SNAPSHOT_DEPENDENCIES_SCHEMA = "chapter-snapshot-dependencies.v1"


def _snapshot_digest(value: object) -> str:
    return hashlib.sha256(json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")).hexdigest()


def _snapshot_source_matches(contract: Mapping, lineage: Mapping) -> bool:
    return all(
        lineage.get(key) is not None
        and type(contract.get(key)) is type(lineage[key])
        and contract.get(key) == lineage[key]
        for key in KNOWLEDGE_SOURCE_KEYS
    )


@dataclass(frozen=True)
class _SnapshotInput:
    chapter_id: UUID | None = None
    source: dict[str, Any] | None = None
    context: ChapterStateSnapshotContext | None = None
    literal_context: ChapterStateSnapshotContext | None = None
    retained_contexts: tuple[ChapterStateSnapshotContext, ...] = ()


def _context_payload(context: ChapterStateSnapshotContext) -> dict:
    value = context.model_dump(mode="json")
    if context.epistemic_view_sha256 is None:
        for key in (
            "reported_observations",
            "epistemic_view_sha256",
            "epistemic_application_sha256",
            "epistemic_policy",
        ):
            value.pop(key, None)
        for fact in value["facts"]:
            fact.pop("epistemic_disposition", None)
            fact.pop("source_observation", None)
    return value


def _snapshot_dependencies(chapter_number: int, previous: _SnapshotInput) -> dict[str, Any]:
    """Bind both the selected predecessor and the actual fact context (or absence)."""
    return {
        "schema": _SNAPSHOT_DEPENDENCIES_SCHEMA,
        "previous_chapter_number": chapter_number - 1 if chapter_number > 1 else None,
        "previous_chapter_id": str(previous.chapter_id) if previous.chapter_id else None,
        "previous_source": {key: previous.source[key] for key in KNOWLEDGE_SOURCE_KEYS}
        if previous.source
        else None,
        "previous_context_sha256": _snapshot_digest(_context_payload(previous.context))
        if previous.context is not None
        else None,
        **(
            {
                "epistemic_policy": previous.context.epistemic_policy,
                "previous_epistemic_view_sha256": previous.context.epistemic_view_sha256,
                "previous_epistemic_application_sha256": (
                    previous.context.epistemic_application_sha256
                ),
            }
            if previous.context is not None and previous.context.epistemic_view_sha256
            else {}
        ),
    }


def _original_dependencies(contract: Mapping, number: int, previous: _SnapshotInput) -> dict:
    """Validate literal paid inputs, separately from the new admitted-view chain."""
    stored = contract.get("snapshot_dependencies")
    if isinstance(stored, Mapping) and "epistemic_policy" in stored:
        # A policy-only reprojection must not claim that the paid call consumed
        # the new view. Reconstruct its exact original retained application.
        for context in (previous.context, *previous.retained_contexts):
            if context is None:
                continue
            original = _snapshot_dependencies(
                number,
                _SnapshotInput(
                    previous.chapter_id,
                    previous.source,
                    context,
                ),
            )
            if _snapshot_digest(original) == _snapshot_digest(stored):
                return original
        return _snapshot_dependencies(number, previous)
    return _snapshot_dependencies(
        number,
        _SnapshotInput(
            previous.chapter_id,
            previous.source,
            previous.literal_context,
        ),
    )


def _snapshot_dependencies_match(
    contract: Mapping, dependencies: Mapping, chapter_number: int,
) -> bool:
    # The root chapter had no predecessor input to lose. Later legacy rows
    # cannot acquire new provenance merely because their own body still matches.
    if "snapshot_dependencies" not in contract:
        return chapter_number == 1
    stored = contract["snapshot_dependencies"]
    if not isinstance(stored, Mapping):
        return False
    try:
        return _snapshot_digest(stored) == _snapshot_digest(dependencies)
    except (TypeError, ValueError):
        return False


def _snapshot_literal_matches(
    status: str,
    stored: Mapping,
    lineage: Mapping,
    dependencies: Mapping,
    chapter_number: int,
) -> bool:
    contract = stored.get("snapshot_contract")
    return bool(
        status == "ok_" + str(lineage.get("adoption_state"))
        and isinstance(contract, Mapping)
        and contract.get("is_usable") is True
        and _snapshot_source_matches(contract, lineage)
        and _snapshot_dependencies_match(contract, dependencies, chapter_number)
    )


def _snapshot_cache_matches(
    status: str,
    stored: Mapping,
    lineage: Mapping,
    dependencies: Mapping,
    chapter_number: int,
    *,
    source_body: str | None = None,
    snapshot: ChapterStateSnapshotModel | None = None,
    previous: _SnapshotInput | None = None,
) -> bool:
    literal = _snapshot_literal_matches(status, stored, lineage, dependencies, chapter_number)
    return bool(
        literal
        and snapshot is not None
        and isinstance(source_body, str)
        and _verified_epistemic_view(snapshot, source_body, previous or _SnapshotInput())
    )


_EPISTEMIC_VIEW = "epistemic_admitted_view"
_EPISTEMIC_HISTORY = "epistemic_admission_history"
_EPISTEMIC_SCHEMA = "chapter-snapshot-epistemic-view.v1"


def snapshot_epistemic_witness(snapshot: ChapterStateSnapshotModel) -> str:
    """Exact beforeimage for the explicit DB-only migration operator."""
    return _snapshot_digest(
        {
            "id": str(snapshot.id),
            "project_id": str(snapshot.project_id),
            "chapter_id": str(snapshot.chapter_id),
            "chapter_number": snapshot.chapter_number,
            **{
                key: getattr(snapshot, key)
                for key in (
                    "facts",
                    "raw_extraction",
                    "extraction_model",
                    "extraction_status",
                    "time_anchor",
                    "chapter_time_span",
                )
            },
        }
    )


def _epistemic_application(
    snapshot: ChapterStateSnapshotModel, previous: _SnapshotInput, source_body: str
) -> dict:
    raw = snapshot.raw_extraction
    if not isinstance(raw, str):
        raise ValueError("snapshot_epistemic_raw_missing")
    matched = _FENCED_JSON_RE.search(raw) or _BARE_JSON_OBJECT_RE.search(raw)
    if matched is None:
        raise ValueError("snapshot_epistemic_raw_invalid")
    text = matched.group(1) if matched.re is _FENCED_JSON_RE else matched.group(0)
    try:
        candidate = json.loads(text, strict=False)
    except json.JSONDecodeError:
        candidate = json.loads(_CTRL_CHARS_RE.sub(" ", text), strict=False)
    if not isinstance(candidate, dict) or not isinstance(candidate.get("facts"), list):
        raise ValueError("snapshot_epistemic_candidate_invalid")
    stored = snapshot.facts or {}
    contract = stored.get("snapshot_contract") or {}
    parsed, _anchor, _span, error = _parse_extraction_payload(raw)
    dependencies = contract.get("snapshot_dependencies") or {}
    literal_previous = previous.literal_context
    if "epistemic_policy" in dependencies:
        literal_previous = next(
            (
                context
                for context in (previous.context, *previous.retained_contexts)
                if context is not None
                and _snapshot_dependencies(
                    snapshot.chapter_number,
                    _SnapshotInput(previous.chapter_id, previous.source, context),
                )
                == dependencies
            ),
            None,
        )
        if literal_previous is None:
            raise ValueError("snapshot_epistemic_original_input_unavailable")
    original = _normalize_inferred_countdown_jumps(parsed, literal_previous, chapter_md=source_body)
    if error is not None or original != _facts_from_storage(stored):
        raise ValueError("snapshot_epistemic_original_candidate_changed")
    # Audit times/history never enter the semantic classifier's application.
    source_keys = (
        *KNOWLEDGE_SOURCE_KEYS,
        "llm_run_id",
        "operation_id",
        "workflow_run_id",
        "step_run_id",
        "provider",
        "model",
        "prompt_template",
        "prompt_version",
        "prompt_sha256",
        "fallback_used",
        "llm_operations",
        "snapshot_dependencies",
    )
    return {
        "snapshot_id": str(snapshot.id),
        "raw_sha256": hashlib.sha256(raw.encode()).hexdigest(),
        "model_candidate": candidate,
        "stored_facts": deepcopy(stored.get("facts", [])),
        "original_source_contract": {
            k: deepcopy(contract[k]) for k in source_keys if k in contract
        },
        "time_anchor": snapshot.time_anchor,
        "chapter_time_span": snapshot.chapter_time_span,
        "extraction_status": snapshot.extraction_status,
        "extraction_model": snapshot.extraction_model,
    }


def build_snapshot_epistemic_view(
    snapshot: ChapterStateSnapshotModel,
    *,
    source_body: str,
    previous: _SnapshotInput | None = None,
) -> dict:
    """Pure derived view; a classification is never adoption/grade authority."""
    from bestseller.services.knowledge_epistemic_admission import (
        POLICY,
        derive_snapshot_item_disposition,
    )

    previous = previous or _SnapshotInput()
    application = _epistemic_application(snapshot, previous, source_body)
    source = application["original_source_contract"]
    source = {**source, "raw_sha256": application["raw_sha256"]}
    if source.get("source_body_sha256") != hashlib.sha256(source_body.encode()).hexdigest():
        raise ValueError("snapshot_epistemic_body_mismatch")
    if previous.source is not None and previous.context is None:
        raise ValueError("snapshot_epistemic_predecessor_not_current")
    items = application["stored_facts"]
    if not isinstance(items, list):
        raise ValueError("snapshot_epistemic_items_invalid")
    decisions, admitted, observations, carries = [], [], [], []
    for index, item in enumerate(items):
        path = f"/stored_facts/{index}"
        decision = derive_snapshot_item_disposition(
            item=item,
            item_path=path,
            source_lineage=source,
            source_body=source_body,
            application_payload=application,
        )
        decisions.append(decision)
        if decision["disposition"] == "retain_existing_fact":
            # Parser validity is separate from the quotation classification.
            HardFactContext.model_validate(item)
            admitted.append(deepcopy(item))
        elif decision["disposition"] == "reported_observation":
            observations.append(
                {
                    "item_path": path,
                    "disposition_id": decision["disposition_id"],
                    **deepcopy(decision["resolved_observation"]),
                }
            )
        elif (
            isinstance(item, Mapping)
            and item.get("kind") == "countdown"
            and not item.get("source_quote")
        ):
            # Preserve only an unchanged, already admitted countdown, never an
            # inferred decrement or a previous quarantined numeric assertion.
            matches = [
                fact
                for fact in (previous.context.facts if previous.context else [])
                if all(
                    getattr(fact, key) == item.get(key)
                    for key in ("name", "value", "unit", "kind", "subject")
                )
            ]
            if len(matches) == 1 and previous.context.epistemic_view_sha256:
                admitted.append(deepcopy(item))
                carries.append(
                    {
                        "item_path": path,
                        "policy": "admitted-countdown-carry.v1",
                        "previous_view_sha256": previous.context.epistemic_view_sha256,
                        "previous_fact_sha256": _snapshot_digest(
                            matches[0].model_dump(mode="json")
                        ),
                    }
                )
    scalars = {}
    for field in ("time_anchor", "chapter_time_span"):
        value = application[field]
        item = {"field": field, "value": value, "source_quote": value}
        decision = derive_snapshot_item_disposition(
            item=item,
            item_path=f"/{field}",
            source_lineage=source,
            source_body=source_body,
            application_payload=application,
            classification="snapshot_scalar",
        )
        decisions.append(decision)
        scalars[field] = value if decision["disposition"] == "retain_existing_fact" else None
        if decision["disposition"] == "reported_observation":
            observations.append(
                {
                    "item_path": f"/{field}",
                    "disposition_id": decision["disposition_id"],
                    **deepcopy(decision["resolved_observation"]),
                }
            )
    view = {
        "schema": _EPISTEMIC_SCHEMA,
        "policy": POLICY,
        "epistemic_source_sha256": hashlib.sha256(
            Path(__file__).with_name("knowledge_epistemic_admission.py").read_bytes()
        ).hexdigest(),
        "snapshot_view_source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "snapshot_application_sha256": _snapshot_digest(application),
        "source_contract_sha256": _snapshot_digest((snapshot.facts or {}).get("snapshot_contract")),
        "view_dependencies": {
            "policy": POLICY,
            "previous_view_sha256": previous.context.epistemic_view_sha256
            if previous.context
            else None,
            "previous_application_sha256": previous.context.epistemic_application_sha256
            if previous.context
            else None,
            # Readers independently revalidate adoption. Hash the same source
            # identity as the paid dependency fence, rather than route-specific
            # diagnostics such as promotion state or the debt receipt envelope.
            "previous_source": {
                key: deepcopy(previous.source[key]) for key in KNOWLEDGE_SOURCE_KEYS
            } if previous.source else None,
        },
        "item_dispositions": decisions,
        "facts": admitted,
        "reported_observations": observations,
        "prior_admitted_carries": carries,
        **scalars,
        "processing_complete": True,
        "truth_approved": False,
        "grade_granted": False,
        "credit_granted": False,
    }
    return {**view, "view_sha256": _snapshot_digest(view)}


def _epistemic_history(snapshot: ChapterStateSnapshotModel) -> list:
    history = (snapshot.facts or {}).get(_EPISTEMIC_HISTORY, [])
    if not isinstance(history, list):
        raise ValueError("snapshot_epistemic_history_invalid")
    previous = None
    for entry in history:
        if not isinstance(entry, Mapping):
            raise ValueError("snapshot_epistemic_history_invalid")
        unsigned = {k: v for k, v in entry.items() if k != "receipt_sha256"}
        if (
            entry.get("schema") != "snapshot-epistemic-application.v1"
            or entry.get("previous_receipt_sha256") != previous
            or entry.get("receipt_sha256") != _snapshot_digest(unsigned)
        ):
            raise ValueError("snapshot_epistemic_history_changed")
        previous = entry["receipt_sha256"]
    return history


def _verified_epistemic_view(
    snapshot: ChapterStateSnapshotModel, body: str, previous: _SnapshotInput
) -> dict | None:
    try:
        stored = (snapshot.facts or {}).get(_EPISTEMIC_VIEW)
        history = _epistemic_history(snapshot)
        expected = build_snapshot_epistemic_view(snapshot, source_body=body, previous=previous)
        if (
            not isinstance(stored, Mapping)
            or not history
            or stored.get("application_receipt_sha256") != history[-1]["receipt_sha256"]
            or history[-1].get("view_sha256") != expected["view_sha256"]
            or {k: v for k, v in stored.items() if k != "application_receipt_sha256"} != expected
        ):
            return None
        return {**expected, "application_receipt_sha256": history[-1]["receipt_sha256"]}
    except (ValueError, KeyError, TypeError, AttributeError):
        return None


def _publish_epistemic_view(
    snapshot: ChapterStateSnapshotModel,
    view: Mapping,
    *,
    actor: str,
    reason: str,
    before_sha256: str,
) -> dict:
    history = deepcopy(_epistemic_history(snapshot))
    receipt = {
        "schema": "snapshot-epistemic-application.v1",
        "actor": actor,
        "reason": reason,
        "before_sha256": before_sha256,
        "view_sha256": view["view_sha256"],
        "previous_receipt_sha256": history[-1]["receipt_sha256"] if history else None,
        "previous_view": deepcopy((snapshot.facts or {}).get(_EPISTEMIC_VIEW)),
        "provider_calls": 0,
        "grade_granted": False,
        "credit_granted": False,
    }
    receipt["receipt_sha256"] = _snapshot_digest(receipt)
    snapshot.facts = {
        **deepcopy(snapshot.facts or {}),
        _EPISTEMIC_VIEW: {
            **deepcopy(view),
            "application_receipt_sha256": receipt["receipt_sha256"],
        },
        _EPISTEMIC_HISTORY: [*history, receipt],
    }
    return receipt


async def validate_snapshot_epistemic_view(
    session: AsyncSession,
    *,
    project_id: UUID,
    chapter_id: UUID,
    source_chapter_draft_version_id: UUID,
    chapter_md: str,
) -> dict:
    """Read-only operator preflight; no lock, flush, model call or publication."""
    with session.no_autoflush:
        lineage, rejection = await validate_adopted_knowledge_source(
            session,
            project_id=project_id,
            chapter_id=chapter_id,
            source_chapter_draft_version_id=source_chapter_draft_version_id,
            chapter_md=chapter_md,
            lock=False,
        )
        if rejection:
            raise ValueError("snapshot_epistemic_source_rejected:" + rejection)
        row = await session.scalar(
            select(ChapterStateSnapshotModel)
            .where(
                ChapterStateSnapshotModel.project_id == project_id,
                ChapterStateSnapshotModel.chapter_id == chapter_id,
            )
            .execution_options(populate_existing=True, autoflush=False)
        )
        if row is None:
            raise ValueError("snapshot_epistemic_snapshot_missing")
        previous = await _current_snapshot_input(
            session,
            project_id=project_id,
            chapter_number=row.chapter_number - 1,
            include_source=True,
        )
        contract = (row.facts or {}).get("snapshot_contract") or {}
        if not _snapshot_literal_matches(
            row.extraction_status,
            row.facts or {},
            lineage,
            _original_dependencies(contract, row.chapter_number, previous),
            row.chapter_number,
        ):
            raise ValueError("snapshot_epistemic_literal_dependencies_changed")
        view = build_snapshot_epistemic_view(row, source_body=chapter_md, previous=previous)
        return {"snapshot_sha256": snapshot_epistemic_witness(row), "admission_receipt": view}


async def apply_snapshot_epistemic_view(
    session: AsyncSession,
    *,
    project_id: UUID,
    chapter_id: UUID,
    source_chapter_draft_version_id: UUID,
    chapter_md: str,
    expected_snapshot_sha256: str,
    admission_receipt: Mapping,
    actor: str,
    reason: str,
) -> dict:
    """Explicit same-body derived publication; flush only, caller owns commit."""
    from bestseller.infra.db.models import ChapterDraftVersionModel

    if not actor.strip() or not reason.strip() or session.new or session.dirty or session.deleted:
        raise ValueError("snapshot_epistemic_clean_witness_and_actor_required")
    async with session.begin_nested():
        for model, identity in (
            (ChapterModel, chapter_id),
            (ChapterDraftVersionModel, source_chapter_draft_version_id),
        ):
            await session.scalar(
                select(model)
                .where(model.id == identity, model.project_id == project_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        row = await session.scalar(
            select(ChapterStateSnapshotModel)
            .where(
                ChapterStateSnapshotModel.project_id == project_id,
                ChapterStateSnapshotModel.chapter_id == chapter_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if row is None:
            raise ValueError("snapshot_epistemic_snapshot_missing")
        checked = await validate_snapshot_epistemic_view(
            session,
            project_id=project_id,
            chapter_id=chapter_id,
            source_chapter_draft_version_id=source_chapter_draft_version_id,
            chapter_md=chapter_md,
        )
        if checked["admission_receipt"] != dict(admission_receipt):
            raise ValueError("snapshot_epistemic_receipt_changed")
        history = _epistemic_history(row)
        previous = await _current_snapshot_input(
            session,
            project_id=project_id,
            chapter_number=row.chapter_number - 1,
            include_source=True,
        )
        if (
            history
            and history[-1].get("before_sha256") == expected_snapshot_sha256
            and _verified_epistemic_view(row, chapter_md, previous)
        ):
            if history[-1]["actor"] != actor.strip() or history[-1]["reason"] != reason.strip():
                raise ValueError("snapshot_epistemic_application_changed")
            return {"status": "already_current", "receipt": history[-1]}
        if checked["snapshot_sha256"] != expected_snapshot_sha256:
            raise ValueError("snapshot_epistemic_stale_witness")
        receipt = _publish_epistemic_view(
            row,
            admission_receipt,
            actor=actor.strip(),
            reason=reason.strip(),
            before_sha256=expected_snapshot_sha256,
        )
        await session.flush()
        if not _verified_epistemic_view(row, chapter_md, previous):
            raise ValueError("snapshot_epistemic_publication_changed")
        return {"status": "applied", "receipt": receipt}


def _snapshot_context(
    snapshot: ChapterStateSnapshotModel, *, view: Mapping | None = None
) -> ChapterStateSnapshotContext:
    # A raw context remains available for forensic literal-input reconstruction.
    # Production readers below supply a verified view; raw contexts grant no
    # strict authority in the renderer.
    return ChapterStateSnapshotContext(
        chapter_number=snapshot.chapter_number,
        facts=_facts_from_storage({"facts": view["facts"]})
        if view
        else _facts_from_storage(snapshot.facts),
        time_anchor=view.get("time_anchor") if view else snapshot.time_anchor,
        chapter_time_span=view.get("chapter_time_span") if view else snapshot.chapter_time_span,
        reported_observations=list(view.get("reported_observations", [])) if view else [],
        epistemic_view_sha256=view.get("view_sha256") if view else None,
        epistemic_application_sha256=view.get("application_receipt_sha256") if view else None,
        epistemic_policy=view.get("policy") if view else None,
    )


def _retained_literal_contexts(
    snapshot: ChapterStateSnapshotModel, body: str, previous: _SnapshotInput
) -> tuple:
    """Retained inputs for hash reconstruction only; never current authority."""
    try:
        application_sha = _snapshot_digest(_epistemic_application(snapshot, previous, body))
        contract_sha = _snapshot_digest((snapshot.facts or {}).get("snapshot_contract"))
        history = _epistemic_history(snapshot)
        by_receipt = {entry["receipt_sha256"]: entry for entry in history}
        views = [
            (snapshot.facts or {}).get(_EPISTEMIC_VIEW),
            *(entry.get("previous_view") for entry in history),
        ]
        return tuple(
            _snapshot_context(snapshot, view=view)
            for view in views
            if isinstance(view, Mapping)
            and view.get("snapshot_application_sha256") == application_sha
            and view.get("source_contract_sha256") == contract_sha
            and (by_receipt.get(view.get("application_receipt_sha256")) or {}).get("view_sha256")
            == view.get("view_sha256")
        )
    except (ValueError, TypeError, KeyError):
        return ()


def snapshot_evidence_for_sources(
    sources: Mapping[int, tuple[UUID, Mapping[str, Any] | None, ChapterStateSnapshotModel | None]],
    *,
    source_bodies: Mapping[int, str] | None = None,
) -> dict[int, bool]:
    """Pure guard over one project's preloaded, selector-validated sources.

    The caller supplies every relevant predecessor from its SQL readback and
    shared adoption selector, including verified debt. This does no SQL or
    source selection, and retains no state beyond this call. A gap resets the
    predecessor; mixed projects fail closed rather than sharing fact context.
    """
    projects = {
        source["project_id"]
        for _, source, _ in sources.values()
        if isinstance(source, Mapping) and isinstance(source.get("project_id"), str)
    }
    if len(projects) > 1:
        return dict.fromkeys(sources, False)
    result: dict[int, bool] = {}
    previous = _SnapshotInput()
    last_number = 0
    for number, (chapter_id, lineage, snapshot) in sorted(sources.items()):
        if number != last_number + 1:
            previous = _SnapshotInput()
        source = (
            dict(lineage)
            if (
                isinstance(lineage, Mapping)
                and all(lineage.get(key) is not None for key in KNOWLEDGE_SOURCE_KEYS)
                and lineage.get("schema") == KNOWLEDGE_LINEAGE_SCHEMA
                and lineage.get("chapter_id") == str(chapter_id)
                and type(lineage.get("source_version_no")) is int
                and lineage["source_version_no"] >= 1
                and lineage.get("adoption_state") in {"promoted", "accepted_with_debt"}
            )
            else None
        )
        context, literal_context = None, None
        if (
            source is not None
            and snapshot is not None
            and str(snapshot.project_id) == source["project_id"]
            and snapshot.chapter_id == chapter_id
            and snapshot.chapter_number == number
            and isinstance(snapshot.facts, Mapping)
            and _snapshot_literal_matches(
                snapshot.extraction_status,
                snapshot.facts,
                source,
                _original_dependencies(
                    snapshot.facts.get("snapshot_contract") or {}, number, previous
                ),
                number,
            )
        ):
            literal_context = _snapshot_context(snapshot)
            body = source_bodies.get(number) if source_bodies is not None else None
            view = (
                _verified_epistemic_view(snapshot, body, previous)
                if isinstance(body, str)
                else None
            )
            if view:
                context = _snapshot_context(snapshot, view=view)
        result[number] = context is not None
        retained = (
            _retained_literal_contexts(snapshot, body, previous)
            if literal_context and isinstance(body, str)
            else ()
        )
        previous = _SnapshotInput(chapter_id, source, context, literal_context, retained)
        last_number = number
    return result


async def _current_snapshot_input(
    session: AsyncSession,
    *,
    project_id: UUID,
    chapter_number: int,
    memo: dict[int, _SnapshotInput] | None = None,
    include_source: bool = False,
) -> _SnapshotInput:
    """Validate the dependency chain iteratively, including stale ancestors.

    Descendants can consume only an authority whose own inputs are current.
    Iteration avoids Python's recursion limit for long serials. Column/refresh
    reads and lock=False keep this a read-only validator of database truth.
    """
    from bestseller.services.adopted_chapter_source import select_adopted_chapter_source

    states = memo if memo is not None else {}
    pending: list[tuple[ChapterStateSnapshotModel, dict[str, Any], str]] = []
    previous = _SnapshotInput()
    while chapter_number >= 1:
        if chapter_number in states:
            previous = states[chapter_number]
            break
        row = await session.scalar(
            select(ChapterStateSnapshotModel)
            .where(
                ChapterStateSnapshotModel.project_id == project_id,
                ChapterStateSnapshotModel.chapter_number == chapter_number,
                ChapterStateSnapshotModel.extraction_status.in_(
                    ("ok_promoted", "ok_accepted_with_debt")
                ),
            )
            .execution_options(populate_existing=True, autoflush=False)
        )
        stored = row.facts if row is not None and isinstance(row.facts, Mapping) else {}
        contract = stored.get("snapshot_contract")
        if not (include_source or pending or memo is not None) and (
            not isinstance(contract, Mapping) or contract.get("is_usable") is not True
        ):
            # With no candidate authority there is nothing for a direct reader
            # to consume. Dependency construction still observes the source.
            states[chapter_number] = previous
            break
        chapter = await session.scalar(
            select(ChapterModel)
            .where(
                ChapterModel.project_id == project_id,
                ChapterModel.chapter_number == chapter_number,
            )
            .execution_options(populate_existing=True, autoflush=False)
        )
        if chapter is None:
            states[chapter_number] = previous
            break
        source = None
        adopted, _ = await select_adopted_chapter_source(session, chapter=chapter, lock=False)
        if adopted is not None:
            lineage, rejection = await validate_adopted_knowledge_source(
                session,
                project_id=project_id,
                chapter_id=chapter.id,
                source_chapter_draft_version_id=adopted.id,
                chapter_md=adopted.content_md,
                lock=False,
            )
            if not rejection:
                source = lineage
        previous = _SnapshotInput(chapter.id, source)
        if (
            source is None
            or not isinstance(contract, Mapping)
            or contract.get("is_usable") is not True
            or row.chapter_id != chapter.id
            or row.extraction_status != "ok_" + str(source.get("adoption_state"))
            or not _snapshot_source_matches(contract, source)
        ):
            states[chapter_number] = previous
            break
        pending.append((row, source, adopted.content_md))
        chapter_number -= 1
        previous = _SnapshotInput()
    for row, source, body in reversed(pending):
        contract = row.facts["snapshot_contract"]
        dependencies = _original_dependencies(contract, row.chapter_number, previous)
        context, literal_context = None, None
        if _snapshot_dependencies_match(contract, dependencies, row.chapter_number):
            literal_context = _snapshot_context(row)
            view = _verified_epistemic_view(row, body, previous)
            if view:
                context = _snapshot_context(row, view=view)
        retained = _retained_literal_contexts(row, body, previous) if literal_context else ()
        previous = _SnapshotInput(row.chapter_id, source, context, literal_context, retained)
        states[row.chapter_number] = previous
    return previous


async def snapshot_evidence_current(
    session: AsyncSession,
    *,
    project_id: UUID,
    chapter_number: int,
    source_lineage: Mapping[str, Any] | None = None,
) -> bool:
    """Read-only closure guard: exact adopted source AND current fact dependencies.

    Window/resume consumers may provide their validated source lineage for an
    additional exact binding. This never extracts, upgrades legacy proof, or
    modifies historical snapshots.
    """
    current = await _current_snapshot_input(
        session, project_id=project_id, chapter_number=chapter_number,
    )
    return bool(
        current.context is not None and current.source is not None
        and (source_lineage is None or _snapshot_source_matches(current.source, source_lineage))
    )


async def snapshot_evidence_statuses(
    session: AsyncSession,
    *,
    project_id: UUID,
    chapter_numbers: Iterable[int],
    source_lineages: Mapping[int, Mapping[str, Any]] | None = None,
) -> dict[int, bool]:
    """Validate a project's requested chapters with one call-local prefix memo.

    Each ancestor is checked at most once, including its selected source and
    fact dependencies. No result survives this call or crosses projects. When
    expected lineages are supplied, a missing entry fails closed too.
    """
    states: dict[int, _SnapshotInput] = {}
    result: dict[int, bool] = {}
    for number in sorted(set(chapter_numbers)):
        current = await _current_snapshot_input(
            session, project_id=project_id, chapter_number=number, memo=states,
        )
        expected = source_lineages.get(number) if source_lineages is not None else None
        result[number] = bool(
            current.context is not None and current.source is not None
            and (source_lineages is None or (
                expected is not None and _snapshot_source_matches(current.source, expected)
            ))
        )
    return result


async def load_previous_chapter_snapshot(
    session: AsyncSession,
    *,
    project_id: UUID,
    current_chapter_number: int,
) -> ChapterStateSnapshotContext | None:
    """Return the exact, promoted snapshot for the immediately prior chapter.

    A snapshot from chapter N-2 is historical context, not the hard-fact state
    for chapter N.  Likewise an extraction made from a draft/legacy version is
    never allowed to become a silent continuity authority.
    """
    current = await _current_snapshot_input(
        session,
        project_id=project_id,
        chapter_number=current_chapter_number - 1,
    )
    if current.context is None and current.source is None and current_chapter_number > 1:
        retained = await session.scalar(
            select(ChapterStateSnapshotModel.id)
            .where(
                ChapterStateSnapshotModel.project_id == project_id,
                ChapterStateSnapshotModel.chapter_number == current_chapter_number - 1,
            )
            .execution_options(autoflush=False)
        )
        if retained is not None:
            current = await _current_snapshot_input(
                session,
                project_id=project_id,
                chapter_number=current_chapter_number - 1,
                include_source=True,
            )
    if current.context is None and current.source is not None:
        raise RuntimeError(
            "source_context_unavailable: previous_chapter_snapshot: epistemic_view_required"
        )
    return current.context


async def extract_chapter_state_snapshot(
    session: AsyncSession,
    settings: AppSettings,
    *,
    project_id: UUID,
    chapter: ChapterModel,
    chapter_md: str,
    source_chapter_draft_version_id: UUID | None = None,
    source_is_promoted: bool = False,
    workflow_run_id: UUID | None = None,
    step_run_id: UUID | None = None,
) -> ChapterStateSnapshotModel:
    """Extract and persist the end-of-chapter hard-fact snapshot.

    On any failure (empty LLM response, unparseable JSON, etc.) a row is still
    persisted with ``extraction_status != "ok"`` and ``facts={"facts": []}``,
    so the pipeline remains additive and never crashes on continuity issues.
    """

    source_lineage, rejection = await validate_adopted_knowledge_source(
        session,
        project_id=project_id,
        chapter_id=chapter.id,
        source_chapter_draft_version_id=source_chapter_draft_version_id,
        chapter_md=chapter_md,
    )
    existing = await session.scalar(
        select(ChapterStateSnapshotModel)
        .where(
            ChapterStateSnapshotModel.project_id == project_id,
            ChapterStateSnapshotModel.chapter_id == chapter.id,
        )
        .execution_options(populate_existing=True)
    )
    if rejection:
        return await _persist_snapshot_attempt(
            session,
            project_id=project_id,
            chapter=chapter,
            existing=existing,
            facts=[],
            raw=None,
            model=None,
            status="rejected_source",
            contract={**source_lineage, "is_usable": False, "rejection_reason": rejection},
        )
    previous_input = await _current_snapshot_input(
        session,
        project_id=project_id,
        chapter_number=chapter.chapter_number - 1,
        include_source=True,
    )
    dependencies = _snapshot_dependencies(chapter.chapter_number, previous_input)
    previous = previous_input.context
    if previous_input.source is not None and previous is None:
        raise ValueError("snapshot_epistemic_predecessor_not_current")
    # The extraction may already have been paid for before adoption. Reuse
    # those exact bytes only after the ordinary adopted-source check above;
    # candidate previews themselves never enter the chapter snapshot table.
    from bestseller.infra.db.models import ChapterDraftVersionModel
    from bestseller.services.chapter_continuity_admission import reusable_preview

    source_draft = await session.get(ChapterDraftVersionModel, source_chapter_draft_version_id)
    preview_prompt = _build_extraction_user_prompt(
        chapter=chapter,
        chapter_md=chapter_md,
        previous_snapshot=previous,
    )
    preview = (
        reusable_preview(
            source_draft,
            source_lineage,
            dependencies,
            prompt_sha256=hashlib.sha256(
                (_EXTRACTION_SYSTEM_PROMPT + "\n" + preview_prompt).encode()
            ).hexdigest(),
        )
        if source_draft is not None
        else None
    )
    if existing is not None and _snapshot_literal_matches(
        existing.extraction_status,
        existing.facts or {},
        source_lineage,
        _original_dependencies(
            (existing.facts or {}).get("snapshot_contract") or {},
            chapter.chapter_number,
            previous_input,
        ),
        chapter.chapter_number,
    ):
        if _verified_epistemic_view(existing, chapter_md, previous_input):
            return existing
        raise ValueError("snapshot_epistemic_reconciliation_required")
    if preview is not None:
        facts, anchor, span, error = _parse_extraction_payload(preview["raw"])
        if error is None:
            facts = _normalize_inferred_countdown_jumps(facts, previous, chapter_md=chapter_md)
            return await _persist_snapshot_attempt(
                session,
                project_id=project_id,
                chapter=chapter,
                existing=existing,
                facts=facts,
                raw=preview["raw"],
                model=preview.get("model"),
                status="ok_" + source_lineage["adoption_state"],
                time_anchor=anchor,
                chapter_time_span=span,
                contract={
                    **source_lineage,
                    "snapshot_dependencies": dependencies,
                    "is_usable": True,
                    "pre_adoption_preview_reused": True,
                    **{
                        key: preview.get(key)
                        for key in (
                            "operation_id",
                            "llm_run_id",
                            "workflow_run_id",
                            "model",
                            "provider",
                            "prompt_template",
                            "prompt_version",
                            "prompt_sha256",
                            "fallback_used",
                        )
                    },
                },
            )
    if existing is not None:
        for prior in reversed((existing.facts or {}).get("snapshot_history", [])):
            if not isinstance(prior, Mapping) or not isinstance(prior.get("facts"), Mapping):
                continue
            prior_source = prior.get("facts", {}).get("snapshot_contract", {})
            historical_snapshot = ChapterStateSnapshotModel(
                id=existing.id,
                project_id=project_id,
                chapter_id=chapter.id,
                chapter_number=chapter.chapter_number,
                facts=deepcopy(prior["facts"]),
                raw_extraction=prior.get("raw_extraction"),
                extraction_model=prior.get("extraction_model"),
                extraction_status=prior.get("extraction_status"),
                time_anchor=prior.get("time_anchor"),
                chapter_time_span=prior.get("chapter_time_span"),
            )
            if _snapshot_cache_matches(
                prior.get("extraction_status"),
                prior["facts"],
                source_lineage,
                _original_dependencies(prior_source, chapter.chapter_number, previous_input),
                chapter.chapter_number,
                source_body=chapter_md,
                snapshot=historical_snapshot,
                previous=previous_input,
            ):
                return await _persist_snapshot_attempt(
                    session,
                    project_id=project_id,
                    chapter=chapter,
                    existing=existing,
                    facts=_facts_from_storage(prior["facts"]),
                    raw=prior.get("raw_extraction"),
                    model=prior.get("extraction_model"),
                    status="ok_accepted_with_debt"
                    if source_lineage["adoption_state"] == "accepted_with_debt"
                    else "ok_promoted",
                    contract={**prior_source, **source_lineage},
                    time_anchor=prior.get("time_anchor"),
                    chapter_time_span=prior.get("chapter_time_span"),
                )
    # A changed predecessor changes the extraction input even when this body's
    # identity is unchanged. Keep that distinction in the local operation key.
    source_lineage = {**source_lineage, "snapshot_dependencies": dependencies}
    operation_id = (
        _knowledge_operation_id("snapshot", source_lineage)
        + ":deps:"
        + _snapshot_digest(dependencies)
    )
    llm_receipts: list[dict[str, Any]] = []

    system_prompt = _EXTRACTION_SYSTEM_PROMPT
    user_prompt = _build_extraction_user_prompt(
        chapter=chapter,
        chapter_md=chapter_md or "（本章正文缺失）",
        previous_snapshot=previous,
    )

    fallback_payload = json.dumps({"facts": []}, ensure_ascii=False)

    request = LLMCompletionRequest(
        logical_role="editor",
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        fallback_response=fallback_payload,
        prompt_template="chapter_state_snapshot",
        prompt_version="2.0",
        project_id=project_id,
        workflow_run_id=workflow_run_id,
        step_run_id=step_run_id,
        operation_id=operation_id,
        metadata={
            "source_lineage": source_lineage,
            "chapter_number": chapter.chapter_number,
            "task": "continuity_hard_fact_extraction",
        },
    )
    try:
        completion = await complete_text(session, settings, request)
        llm_receipts.append(_knowledge_llm_receipt(completion, request))
    except ExecutionBudgetError:
        raise
    except Exception as exc:
        return await _persist_snapshot_attempt(
            session,
            project_id=project_id,
            chapter=chapter,
            existing=existing,
            facts=[],
            raw=None,
            model=None,
            status="failed:llm_error",
            contract={
                **source_lineage,
                "is_usable": False,
                "error_type": type(exc).__name__,
                "operation_id": operation_id,
            },
        )

    raw = completion.content or ""
    facts, time_anchor, chapter_time_span, error = _parse_extraction_payload(raw)
    if error is not None:
        try:
            from bestseller.services.llm_closed_loop import (
                LLMGateFinding,
                build_repair_user_prompt,
            )

            _, rejection = await validate_adopted_knowledge_source(
                session,
                project_id=project_id,
                chapter_id=chapter.id,
                source_chapter_draft_version_id=source_chapter_draft_version_id,
                chapter_md=chapter_md,
            )
            if rejection:
                return await _persist_snapshot_attempt(
                    session,
                    project_id=project_id,
                    chapter=chapter,
                    existing=existing,
                    facts=facts,
                    raw=raw,
                    model=completion.model_name,
                    status="rejected_source",
                    contract={
                        **source_lineage,
                        "is_usable": False,
                        "rejection_reason": rejection,
                        "llm_operations": llm_receipts,
                    },
                )
            current_previous = await _current_snapshot_input(
                session,
                project_id=project_id,
                chapter_number=chapter.chapter_number - 1,
                include_source=True,
            )
            if not _snapshot_dependencies_match(
                source_lineage,
                _snapshot_dependencies(chapter.chapter_number, current_previous),
                chapter.chapter_number,
            ):
                return await _persist_snapshot_attempt(
                    session,
                    project_id=project_id,
                    chapter=chapter,
                    existing=existing,
                    facts=facts,
                    raw=raw,
                    model=completion.model_name,
                    status="rejected_dependencies",
                    contract={
                        **source_lineage,
                        "is_usable": False,
                        "rejection_reason": "snapshot_dependencies_changed",
                        "llm_operations": llm_receipts,
                    },
                )
            repair_request = LLMCompletionRequest(
                logical_role="editor",
                system_prompt=system_prompt,
                user_prompt=build_repair_user_prompt(
                    original_user_prompt=user_prompt,
                    findings=[
                        LLMGateFinding(
                            code="CONTINUITY_EXTRACTION_PARSE_ERROR",
                            severity="major",
                            path="$",
                            message=error,
                            expected="A JSON object with a facts array and optional time fields.",
                            actual=raw[:240],
                            repair_action=(
                                "Return only valid JSON. Keep extracted facts concrete, "
                                "chapter-local, and canon-safe."
                            ),
                        )
                    ],
                    language=getattr(chapter, "language", None),
                ),
                fallback_response=fallback_payload,
                prompt_template="chapter_state_snapshot_repair",
                prompt_version="2.0",
                project_id=project_id,
                workflow_run_id=workflow_run_id,
                step_run_id=step_run_id,
                operation_id=operation_id + ":repair",
                metadata={
                    "source_lineage": source_lineage,
                    "chapter_number": chapter.chapter_number,
                    "task": "continuity_hard_fact_extraction_repair",
                    "semantic_repair_of": str(completion.llm_run_id)
                    if completion.llm_run_id
                    else None,
                    "previous_error": error,
                },
            )
            repair_completion = await complete_text(session, settings, repair_request)
            llm_receipts.append(_knowledge_llm_receipt(repair_completion, repair_request))
            repair_raw = repair_completion.content or ""
            (
                repair_facts,
                repair_time_anchor,
                repair_chapter_time_span,
                repair_error,
            ) = _parse_extraction_payload(repair_raw)
            if repair_error is None:
                completion = repair_completion
                raw = repair_raw
                facts = repair_facts
                time_anchor = repair_time_anchor
                chapter_time_span = repair_chapter_time_span
                error = None
        except ExecutionBudgetError:
            raise
        except Exception:
            logger.warning(
                "Hard-fact extraction repair failed for project=%s chapter=%d",
                project_id,
                chapter.chapter_number,
                exc_info=True,
            )
    if error is None:
        facts = _normalize_inferred_countdown_jumps(facts, previous, chapter_md=chapter_md)
    # Defensive truncation: older deployments still have the VARCHAR(32)
    # column (migration 0015 widens it to TEXT). Cap at 120 chars so a long
    # composite error like ``failed:json_decode_error:Invalid control character at``
    # never trips ``StringDataRightTruncationError`` even if the migration
    # hasn't been applied yet. 120 is comfortably under any reasonable
    # downgrade path while still preserving enough context to triage.
    _, rejection = await validate_adopted_knowledge_source(
        session,
        project_id=project_id,
        chapter_id=chapter.id,
        source_chapter_draft_version_id=source_chapter_draft_version_id,
        chapter_md=chapter_md,
    )
    if rejection:
        extraction_status = "rejected_source"
    elif getattr(completion, "fallback_used", False):
        extraction_status = "failed:fallback"
    elif error is None:
        extraction_status = (
            "ok_accepted_with_debt"
            if source_lineage["adoption_state"] == "accepted_with_debt"
            else "ok_promoted"
        )
    else:
        extraction_status = f"failed:{error}"[:120]

    if error is not None:
        logger.warning(
            "Hard-fact extraction failed for project=%s chapter=%d: %s",
            project_id,
            chapter.chapter_number,
            error,
        )

    return await _persist_snapshot_attempt(
        session,
        project_id=project_id,
        chapter=chapter,
        existing=existing,
        facts=facts,
        raw=raw,
        model=completion.model_name,
        status=extraction_status,
        time_anchor=time_anchor,
        chapter_time_span=chapter_time_span,
        contract={
            **source_lineage,
            **llm_receipts[-1],
            "llm_operations": llm_receipts,
            "rejection_reason": rejection,
            "is_usable": extraction_status in {"ok_promoted", "ok_accepted_with_debt"},
        },
    )


async def _persist_snapshot_attempt(
    session: AsyncSession,
    *,
    project_id: UUID,
    chapter: ChapterModel,
    existing: ChapterStateSnapshotModel | None,
    facts: list[HardFactContext],
    raw: str | None,
    model: str | None,
    status: str,
    contract: dict[str, Any],
    time_anchor: str | None = None,
    chapter_time_span: str | None = None,
) -> ChapterStateSnapshotModel:
    async with session.begin_nested():
        return await _persist_snapshot_attempt_in_transaction(
            session,
            project_id=project_id,
            chapter=chapter,
            existing=existing,
            facts=facts,
            raw=raw,
            model=model,
            status=status,
            contract=contract,
            time_anchor=time_anchor,
            chapter_time_span=chapter_time_span,
        )


async def _persist_snapshot_attempt_in_transaction(
    session: AsyncSession,
    *,
    project_id: UUID,
    chapter: ChapterModel,
    existing: ChapterStateSnapshotModel | None,
    facts: list[HardFactContext],
    raw: str | None,
    model: str | None,
    status: str,
    contract: dict[str, Any],
    time_anchor: str | None = None,
    chapter_time_span: str | None = None,
) -> ChapterStateSnapshotModel:
    from bestseller.infra.db.models import ChapterDraftVersionModel

    # Consistent lock order: chapter -> source draft -> snapshot. The entire
    # check and publication live in a DB-only savepoint after the paid call.
    await session.scalar(
        select(ChapterModel.id)
        .where(
            ChapterModel.id == chapter.id,
            ChapterModel.project_id == project_id,
        )
        .with_for_update()
    )
    if status in {"ok_promoted", "ok_accepted_with_debt"}:
        source_id = UUID(contract["source_chapter_draft_version_id"])
        body = await session.scalar(
            select(ChapterDraftVersionModel.content_md).where(
                ChapterDraftVersionModel.id == source_id,
                ChapterDraftVersionModel.project_id == project_id,
                ChapterDraftVersionModel.chapter_id == chapter.id,
            )
        )
        current_source, rejection = await validate_adopted_knowledge_source(
            session,
            project_id=project_id,
            chapter_id=chapter.id,
            source_chapter_draft_version_id=source_id,
            chapter_md=body or "",
        )
        if rejection or not _snapshot_source_matches(contract, current_source):
            status = "rejected_source"
            contract = {
                **contract,
                "is_usable": False,
                "rejection_reason": rejection or "source_lineage_mismatch",
            }
        else:
            status = (
                "ok_accepted_with_debt"
                if current_source["adoption_state"] == "accepted_with_debt"
                else "ok_promoted"
            )
            contract = {**contract, **current_source}
            current_previous = await _current_snapshot_input(
                session,
                project_id=project_id,
                chapter_number=chapter.chapter_number - 1,
                include_source=True,
            )
            if not _snapshot_dependencies_match(
                contract,
                _snapshot_dependencies(chapter.chapter_number, current_previous),
                chapter.chapter_number,
            ):
                status = "rejected_dependencies"
                contract = {
                    **contract,
                    "is_usable": False,
                    "rejection_reason": "snapshot_dependencies_changed",
                }
    # The paid gateway may have committed and another worker may have
    # published a newer snapshot while this attempt was in flight.
    existing = await session.scalar(
        select(ChapterStateSnapshotModel)
        .where(
            ChapterStateSnapshotModel.project_id == project_id,
            ChapterStateSnapshotModel.chapter_id == chapter.id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    old_facts = dict(existing.facts or {}) if existing is not None else {}
    attempt = {
        "facts": {**_facts_to_storage(facts), "snapshot_contract": contract},
        "raw_extraction": raw,
        "extraction_model": model,
        "extraction_status": status,
        "time_anchor": time_anchor,
        "chapter_time_span": chapter_time_span,
    }
    if (
        existing is not None
        and status not in {"ok_promoted", "ok_accepted_with_debt"}
        and (
            await _current_snapshot_input(
                session,
                project_id=project_id,
                chapter_number=chapter.chapter_number,
                include_source=True,
            )
        ).literal_context
        is not None
    ):
        old_facts["snapshot_attempts"] = [*old_facts.get("snapshot_attempts", []), attempt]
        existing.facts = old_facts
        await session.flush()
        # Audit projection of the rejected attempt; the persisted authority is
        # left intact, and the full rejection is stored in snapshot_attempts.
        return ChapterStateSnapshotModel(
            id=existing.id,
            project_id=project_id,
            chapter_id=chapter.id,
            chapter_number=chapter.chapter_number,
            facts=attempt["facts"],
            raw_extraction=raw,
            extraction_model=model,
            extraction_status=status,
            time_anchor=time_anchor,
            chapter_time_span=chapter_time_span,
        )
    history = list(old_facts.get("snapshot_history", []))
    if existing is not None:
        history.append(
            {
                "facts": {
                    key: value for key, value in old_facts.items() if key != "snapshot_history"
                },
                "raw_extraction": existing.raw_extraction,
                "extraction_model": existing.extraction_model,
                "extraction_status": existing.extraction_status,
                "time_anchor": existing.time_anchor,
                "chapter_time_span": existing.chapter_time_span,
            }
        )
    stored_facts = _merge_snapshot_storage(
        existing_facts=old_facts,
        extracted_facts=_facts_to_storage(facts),
        snapshot_contract=contract,
    )
    stored_facts["snapshot_history"] = history

    if existing is None:
        snapshot = ChapterStateSnapshotModel(
            project_id=project_id,
            chapter_id=chapter.id,
            chapter_number=chapter.chapter_number,
            facts=stored_facts,
            raw_extraction=raw,
            extraction_model=model,
            extraction_status=status,
            time_anchor=time_anchor,
            chapter_time_span=chapter_time_span,
        )
        session.add(snapshot)
    else:
        existing.facts = stored_facts
        existing.raw_extraction = raw
        existing.extraction_model = model
        existing.extraction_status = status
        existing.time_anchor = time_anchor
        existing.chapter_time_span = chapter_time_span
        snapshot = existing

    await session.flush()
    if status in {"ok_promoted", "ok_accepted_with_debt"}:
        view = build_snapshot_epistemic_view(snapshot, source_body=body, previous=current_previous)
        _publish_epistemic_view(
            snapshot,
            view,
            actor="snapshot_producer",
            reason="current-source item admission",
            before_sha256=snapshot_epistemic_witness(snapshot),
        )
        await session.flush()
    return snapshot


def _merge_snapshot_storage(
    *,
    existing_facts: Mapping[str, Any] | object,
    extracted_facts: Mapping[str, Any],
    snapshot_contract: Mapping[str, Any],
) -> dict[str, Any]:
    """Refresh extracted facts without erasing a canonical Engine fold."""

    existing = dict(existing_facts) if isinstance(existing_facts, Mapping) else {}
    merged = {
        **dict(extracted_facts),
        "snapshot_contract": dict(snapshot_contract),
    }
    story_engine_state = existing.get("story_engine")
    if isinstance(story_engine_state, Mapping):
        merged["story_engine"] = deepcopy(dict(story_engine_state))
    if _EPISTEMIC_HISTORY in existing:
        merged[_EPISTEMIC_HISTORY] = deepcopy(existing[_EPISTEMIC_HISTORY])
    if _EPISTEMIC_VIEW in existing:
        # Retained only as the next application's beforeimage; never a fallback.
        merged[_EPISTEMIC_VIEW] = deepcopy(existing[_EPISTEMIC_VIEW])
    return merged


def validate_fact_monotonicity(
    current_facts: list[HardFactContext],
    previous_facts: list[HardFactContext],
) -> list[str]:
    """Check that monotonic facts (level, countdown) have not regressed.

    Returns a list of warning messages for any violations found.
    """
    if not previous_facts or not current_facts:
        return []

    prev_by_name: dict[str, HardFactContext] = {f.name: f for f in previous_facts}
    warnings: list[str] = []

    for fact in current_facts:
        prev = prev_by_name.get(fact.name)
        if prev is None:
            continue

        try:
            cur_num = _extract_numeric(fact.value)
            prev_num = _extract_numeric(prev.value)
        except ValueError:
            continue

        if cur_num is None or prev_num is None:
            continue

        if fact.kind in _MONOTONIC_UP_KINDS and cur_num < prev_num:
            warnings.append(
                f"[数值回退] {fact.name}: 从 {prev.value} 降到 {fact.value} "
                f"(kind={fact.kind} 应单调递增)。"
                f"如果确实发生了降级，必须在正文中给出明确原因。"
            )
        elif fact.kind in _MONOTONIC_DOWN_KINDS and cur_num > prev_num:
            warnings.append(
                f"[倒计时重置] {fact.name}: 从 {prev.value} 涨到 {fact.value} "
                f"(kind={fact.kind} 应单调递减)。"
                f"如果倒计时被重置，必须在正文中给出明确原因。"
            )

    return warnings


_CN_DIGITS = {
    "零": 0,
    "〇": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}
_CN_UNITS = {"十": 10, "百": 100, "千": 1000}


def _chinese_numeral_to_int(text: str) -> int | None:
    """Convert a run of Chinese numeral chars (十/二十/三百/七 ...) to int.

    Handles the small magnitudes used by story countdowns (七日之期, 还有三天,
    二十天). Returns None if the run contains an unexpected character.
    """

    if not text:
        return None
    total = 0
    last_digit = 0
    seen = False
    for ch in text:
        if ch in _CN_DIGITS:
            last_digit = _CN_DIGITS[ch]
            seen = True
        elif ch in _CN_UNITS:
            total += (last_digit or 1) * _CN_UNITS[ch]
            last_digit = 0
            seen = True
        else:
            return None
    return (total + last_digit) if seen else None


def _extract_numeric(value: str) -> float | None:
    """Try to extract a numeric value from a fact value string.

    Supports both ASCII digits ("3天") and Chinese numerals ("三天",
    "二十天", "七日之期") so zh-language countdowns are not silently skipped.
    """
    import re as _re

    match = _re.search(r"[-+]?\d*\.?\d+", value)
    if match:
        return float(match.group())
    cn_match = _re.search(r"[零〇一二两三四五六七八九十百千]+", value)
    if cn_match:
        parsed = _chinese_numeral_to_int(cn_match.group())
        if parsed is not None:
            return float(parsed)
    return None


# ---------------------------------------------------------------------------
# Phase D3 — time / countdown arithmetic validators.
#
# These emit Phase-A1 ``CheckerReport`` envelopes so the scorecard and
# write_gate layers consume them through the same contract as every other
# audit surface. Inputs are pure ``ChapterStateSnapshotContext`` values so
# the validators stay testable without touching the DB.
# ---------------------------------------------------------------------------


_TIME_CONTINUITY_AGENT = "time-continuity"

_COUNTDOWN_KIND = "countdown"

_FLASHBACK_KEYWORDS: frozenset[str] = frozenset(
    {"flashback", "闪回", "回忆", "倒叙", "插叙", "追忆"}
)


def _snapshot_is_flashback(snapshot: ChapterStateSnapshotContext) -> bool:
    """Infer flashback tag from fact notes.

    We look for ``flashback=true`` / ``reset=true`` style hints in the
    free-prose ``notes`` of any fact, plus common Chinese markers. The
    continuity extractor is instructed (system prompt rule 8) to surface
    ``reset=true`` for intentional countdown resets, which gives us the
    same signal for free.
    """

    for fact in snapshot.facts:
        notes = (fact.notes or "").lower()
        if not notes:
            continue
        if "flashback=true" in notes or "reset=true" in notes:
            return True
        if any(keyword in notes for keyword in _FLASHBACK_KEYWORDS):
            return True
    return False


def _index_facts_by_name(
    facts: list[HardFactContext],
    *,
    kind: str | None = None,
) -> dict[str, HardFactContext]:
    """Return ``{fact.name: fact}`` optionally filtered by ``kind``."""

    out: dict[str, HardFactContext] = {}
    for fact in facts:
        if kind is not None and fact.kind != kind:
            continue
        out[fact.name] = fact
    return out


def check_countdown_arithmetic(
    current_snapshot: ChapterStateSnapshotContext,
    previous_snapshot: ChapterStateSnapshotContext | None,
    *,
    is_flashback: bool | None = None,
) -> CheckerReport:
    """CountdownArithmeticCheck (Phase D3).

    For each named countdown fact, verify that ``prev_value - current_value``
    falls within ``{0, 1}`` — i.e. the countdown advanced by at most one
    unit. Jumps > 1 (e.g. D-5 → D-2) are flagged ``critical`` /
    ``can_override = False`` because the reader can't silently re-sync a
    hard deadline; regen must fix the arithmetic or mark the chapter as a
    flashback.

    ``is_flashback`` overrides heuristic detection when the caller already
    knows (e.g. from a chapter flag). When ``None`` we fall back to
    sniffing notes on ``current_snapshot``.
    """

    chapter = int(current_snapshot.chapter_number)
    issues: list[CheckerIssue] = []
    metrics: dict[str, Any] = {
        "countdowns_checked": 0,
        "jumps_detected": 0,
        "resets_detected": 0,
    }

    if previous_snapshot is None or not previous_snapshot.facts:
        return CheckerReport(
            agent=_TIME_CONTINUITY_AGENT,
            chapter=chapter,
            overall_score=100,
            passed=True,
            issues=(),
            metrics=metrics,
            summary="No previous snapshot — countdown arithmetic skipped.",
        )

    flashback = (
        bool(_snapshot_is_flashback(current_snapshot))
        if is_flashback is None
        else bool(is_flashback)
    )

    prev_countdowns = _index_facts_by_name(previous_snapshot.facts, kind=_COUNTDOWN_KIND)
    cur_countdowns = _index_facts_by_name(current_snapshot.facts, kind=_COUNTDOWN_KIND)

    for name, cur_fact in cur_countdowns.items():
        prev_fact = prev_countdowns.get(name)
        if prev_fact is None:
            continue

        cur_num = _extract_numeric(cur_fact.value)
        prev_num = _extract_numeric(prev_fact.value)
        if cur_num is None or prev_num is None:
            continue

        metrics["countdowns_checked"] += 1
        delta = prev_num - cur_num

        if delta < 0:
            # Countdown went UP — only acceptable if flashback or explicit reset.
            if flashback:
                continue
            metrics["resets_detected"] += 1
            issues.append(
                CheckerIssue(
                    id="COUNTDOWN_RESET",
                    type="countdown_reset",
                    severity="critical",
                    location=f"第 {chapter} 章末 — {name}",
                    description=(
                        f"倒计时 {name} 从 {prev_fact.value} 涨回 {cur_fact.value}，"
                        f"但本章未标记 flashback/reset。硬事实倒计时只能单调递减。"
                    ),
                    suggestion=(
                        "请在本章正文中明确倒计时被重置的触发事件（如外部干预、规则变更），"
                        "并在事实 notes 中添加 reset=true；否则回退倒计时变化。"
                    ),
                    can_override=False,
                    allowed_rationales=(),
                )
            )
            continue

        # delta >= 0; flag only if the gap exceeds one unit.
        if delta > 1 and not flashback:
            metrics["jumps_detected"] += 1
            issues.append(
                CheckerIssue(
                    id="COUNTDOWN_ARITHMETIC_JUMP",
                    type="countdown_arithmetic",
                    severity="critical",
                    location=f"第 {chapter} 章末 — {name}",
                    description=(
                        f"倒计时 {name} 从 {prev_fact.value} 直接跳到 {cur_fact.value}，"
                        f"跨度 {delta:g} 单位。硬事实倒计时每章只能推进 0 或 1 个单位。"
                    ),
                    suggestion=(
                        "请在本章正文里补足中间推进（每章减 1），或把跨度并入后续章节；"
                        "如果这是必要的时间跳跃，请把本章标记为 flashback/过渡章再重新提交。"
                    ),
                    can_override=False,
                    allowed_rationales=(),
                )
            )

    passed = not issues
    overall_score = 100 if passed else max(0, 100 - 40 * len(issues))

    return CheckerReport(
        agent=_TIME_CONTINUITY_AGENT,
        chapter=chapter,
        overall_score=overall_score,
        passed=passed,
        issues=tuple(issues),
        metrics=metrics,
        summary=(
            "Countdown arithmetic clean."
            if passed
            else f"{len(issues)} countdown-arithmetic violation(s) detected."
        ),
    )


# --- Time anchor parsing ---------------------------------------------------

# Match "第 N 天" / "Day N" / "D-N" / "末世第 N 天" / "第 N 天 · 清晨". We pull
# the first integer we find and a coarse part-of-day bucket for tie-breaking
# within the same day.
_DAY_NUMBER_RE = re.compile(r"(?:day|d[-\s]*|第)\s*([-+]?\d+)\s*(?:天|day|d)?", re.IGNORECASE)
_BARE_INT_RE = re.compile(r"[-+]?\d+")

_PART_OF_DAY_ORDER: dict[str, int] = {
    "凌晨": 0,
    "拂晓": 0,
    "清晨": 1,
    "早晨": 1,
    "上午": 2,
    "morning": 1,
    "dawn": 0,
    "中午": 3,
    "正午": 3,
    "noon": 3,
    "下午": 4,
    "afternoon": 4,
    "傍晚": 5,
    "黄昏": 5,
    "evening": 5,
    "晚上": 6,
    "夜里": 6,
    "深夜": 7,
    "night": 6,
    "midnight": 7,
}


def _parse_time_anchor(anchor: str | None) -> tuple[int, int] | None:
    """Parse a free-prose time anchor into ``(day, part_of_day_order)``.

    Returns ``None`` when no day integer can be extracted. The part-of-day
    score is ``0`` when not recognised so two "Day 3" anchors compare
    equal; a "Day 3 清晨" anchor precedes a "Day 3 傍晚" anchor on the
    ordering axis.
    """

    if not anchor:
        return None
    text = anchor.strip()
    if not text:
        return None

    day_match = _DAY_NUMBER_RE.search(text)
    if day_match is None:
        # Fall back to bare integer — handles "3 · 清晨" style anchors.
        bare = _BARE_INT_RE.search(text)
        if bare is None:
            return None
        try:
            day = int(bare.group())
        except ValueError:
            return None
    else:
        try:
            day = int(day_match.group(1))
        except ValueError:
            return None

    lowered = text.lower()
    part_score = 0
    for keyword, order in _PART_OF_DAY_ORDER.items():
        if keyword in lowered or keyword in text:
            part_score = order
            break

    return day, part_score


def check_time_regression(
    current_snapshot: ChapterStateSnapshotContext,
    previous_snapshot: ChapterStateSnapshotContext | None,
    *,
    is_flashback: bool | None = None,
) -> CheckerReport:
    """TimeRegressionCheck (Phase D3).

    Flags a chapter whose ``time_anchor`` resolves to a point strictly
    before the previous chapter's anchor, unless the chapter is tagged as
    a flashback. Severity ``high`` / ``can_override = True`` because
    legitimate narrative jumps (parallel POVs, dream sequences) exist —
    the author can sign an Override Contract citing
    ``WORLD_RULE_CONSTRAINT`` or ``LOGIC_INTEGRITY``.
    """

    chapter = int(current_snapshot.chapter_number)
    metrics: dict[str, Any] = {
        "current_anchor": current_snapshot.time_anchor,
        "previous_anchor": (previous_snapshot.time_anchor if previous_snapshot else None),
        "flashback_detected": False,
    }

    if previous_snapshot is None:
        return CheckerReport(
            agent=_TIME_CONTINUITY_AGENT,
            chapter=chapter,
            overall_score=100,
            passed=True,
            issues=(),
            metrics=metrics,
            summary="No previous snapshot — time anchor regression skipped.",
        )

    cur_parsed = _parse_time_anchor(current_snapshot.time_anchor)
    prev_parsed = _parse_time_anchor(previous_snapshot.time_anchor)
    metrics["current_parsed"] = cur_parsed
    metrics["previous_parsed"] = prev_parsed

    if cur_parsed is None or prev_parsed is None:
        return CheckerReport(
            agent=_TIME_CONTINUITY_AGENT,
            chapter=chapter,
            overall_score=100,
            passed=True,
            issues=(),
            metrics=metrics,
            summary="Unparseable time anchor — regression check skipped.",
        )

    flashback = (
        bool(_snapshot_is_flashback(current_snapshot))
        if is_flashback is None
        else bool(is_flashback)
    )
    metrics["flashback_detected"] = flashback

    if cur_parsed >= prev_parsed:
        return CheckerReport(
            agent=_TIME_CONTINUITY_AGENT,
            chapter=chapter,
            overall_score=100,
            passed=True,
            issues=(),
            metrics=metrics,
            summary="Time anchor non-regressing.",
        )

    if flashback:
        return CheckerReport(
            agent=_TIME_CONTINUITY_AGENT,
            chapter=chapter,
            overall_score=100,
            passed=True,
            issues=(),
            metrics={**metrics, "flashback_accepted": True},
            summary="Time anchor regression accepted (flashback tagged).",
        )

    issue = CheckerIssue(
        id="TIME_ANCHOR_REGRESSION",
        type="time_regression",
        severity="high",
        location=f"第 {chapter} 章",
        description=(
            f"本章时间锚点 '{current_snapshot.time_anchor}' 早于上一章 "
            f"'{previous_snapshot.time_anchor}'，但章节未标记 flashback。"
        ),
        suggestion=(
            "请在事实 notes 中加入 flashback=true 或改章节正文标记为回忆/插叙，"
            "或签署 Override Contract 并注明 rationale=WORLD_RULE_CONSTRAINT/LOGIC_INTEGRITY。"
        ),
        can_override=True,
        allowed_rationales=("WORLD_RULE_CONSTRAINT", "LOGIC_INTEGRITY"),
    )

    return CheckerReport(
        agent=_TIME_CONTINUITY_AGENT,
        chapter=chapter,
        overall_score=60,
        passed=False,
        issues=(issue,),
        metrics=metrics,
        summary="Time anchor regressed without flashback tag.",
    )


def render_hard_fact_snapshot_block(snapshot: ChapterStateSnapshotContext | None) -> str:
    """Render the ``CURRENT_STATE`` prompt block for injection into writer prompts.

    Current observations remain meaningful context even without admitted facts.
    Missing or unverified views contribute no strict authority.
    """
    from bestseller.services.knowledge_epistemic_admission import POLICY

    if (
        snapshot is None
        or snapshot.epistemic_policy != POLICY
        or not snapshot.epistemic_view_sha256
    ):
        return ""

    lines: list[str] = []
    if snapshot.facts:
        lines.append(
            f"=== 当前事实状态（来自第 {snapshot.chapter_number} 章末 "
            "— 必须严格遵守，不得前后矛盾）==="
        )
    for fact in snapshot.facts:
        prefix = f"[{fact.subject}] " if fact.subject else ""
        unit = f" {fact.unit}" if fact.unit else ""
        notes = f"  // {fact.notes}" if fact.notes else ""
        lines.append(f"- {prefix}{fact.name}: {fact.value}{unit}{notes}")
    if snapshot.facts:
        lines.append(
            "=== 任何数值/位置/物品变化都必须在本章正文里给出"
            "读者可见的触发事件（交易、战斗、时间流逝等）===",
        )
    if snapshot.reported_observations:
        lines.append("=== 来源转述与未确认观察（保留话语，不确认其中主张为世界事实）===")  # noqa: RUF001
        for observation in snapshot.reported_observations:
            text = observation.get("text")
            if isinstance(text, str) and text:
                lines.append(f"- 原文话语：{text}")  # noqa: RUF001
        lines.append("=== 不据此判定真伪、纠正他人认知或推导客观数值变化 ===")
    return "\n".join(lines)
