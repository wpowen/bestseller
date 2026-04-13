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

import json
import logging
import re
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.domain.context import ChapterStateSnapshotContext, HardFactContext
from bestseller.infra.db.models import ChapterModel, ChapterStateSnapshotModel
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


_EXTRACTION_SYSTEM_PROMPT = (
    "你是小说连续性事实抽取器。你的任务是读完本章正文和上一章末的事实状态，"
    "输出【严格 JSON】，列出本章结束时所有可枚举的硬事实。\n\n"
    "硬事实包括：倒计时时间、人物等级/修为/境界、具名资源/物品计数、关键位置、"
    "故事内时间点、可度量距离。\n\n"
    "JSON 格式（严格遵守）：\n"
    "{\n"
    "  \"facts\": [\n"
    "    {\n"
    "      \"name\": \"末日倒计时\",\n"
    "      \"value\": \"20\",\n"
    "      \"unit\": \"小时\",\n"
    "      \"kind\": \"countdown\",\n"
    "      \"subject\": null,\n"
    "      \"notes\": \"本章开头 24 小时，经历第一次交易损耗 4 小时\",\n"
    "      \"source_quote\": \"倒计时落到了二十小时\"\n"
    "    }\n"
    "  ]\n"
    "}\n\n"
    "规则：\n"
    "1. kind 只能取：countdown | level | resource | location | time | distance | inventory_count | elapsed_story_time | other\n"
    "2. value 统一用字符串（包含数字）——JSON 数字和字符串都能，但必须避免歧义。\n"
    "3. 只抽取**本章正文里明确提到**的硬事实，不得编造、推测、补全。\n"
    "4. 不要输出 Markdown、解释、注释或任何非 JSON 内容。直接以 `{` 开头，以 `}` 结尾。\n"
    "5. 如果本章没有任何可枚举硬事实，返回 `{\"facts\": []}`。\n"
    "6. 如果某条事实在上一章已经出现且本章未变化，**仍要列出**（标注 notes=\"无变化\"），便于下一章继续约束。\n"
    "7. 对 kind=level 的事实（修为/等级/境界），值只能单调递增。如果你发现本章值比上一章低，"
    "在 notes 中标注 `regression=true` 并说明原因。\n"
    "8. 对 kind=countdown 的事实，值只能单调递减。如果倒计时重置了，在 notes 中标注 `reset=true`。\n"
    "9. 必须抽取一条 kind=elapsed_story_time 的事实，记录本章故事内经过的时间（如 \"3小时\"、\"1天\"、\"数分钟\"）。"
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


def _parse_extraction_payload(raw: str) -> tuple[list[HardFactContext], str | None]:
    """Return (facts, error).

    Tolerant parser: accepts fenced JSON, bare JSON, or leading/trailing text.
    """
    if not raw or not raw.strip():
        return [], "empty_response"

    candidate: str | None = None

    fenced = _FENCED_JSON_RE.search(raw)
    if fenced is not None:
        candidate = fenced.group(1)
    else:
        bare = _BARE_JSON_OBJECT_RE.search(raw)
        if bare is not None:
            candidate = bare.group(0)

    if candidate is None:
        return [], "no_json_object_found"

    try:
        payload = json.loads(candidate)
    except json.JSONDecodeError as exc:
        return [], f"json_decode_error:{exc.msg}"

    if not isinstance(payload, dict):
        return [], "payload_not_object"

    raw_facts = payload.get("facts")
    if not isinstance(raw_facts, list):
        return [], "missing_facts_list"

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

    return facts, None


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


async def load_previous_chapter_snapshot(
    session: AsyncSession,
    *,
    project_id: UUID,
    current_chapter_number: int,
) -> ChapterStateSnapshotContext | None:
    """Return the most recent ``ChapterStateSnapshotContext`` strictly before ``current_chapter_number``."""
    if current_chapter_number <= 1:
        return None
    row = await session.scalar(
        select(ChapterStateSnapshotModel)
        .where(
            ChapterStateSnapshotModel.project_id == project_id,
            ChapterStateSnapshotModel.chapter_number < current_chapter_number,
        )
        .order_by(ChapterStateSnapshotModel.chapter_number.desc())
        .limit(1)
    )
    if row is None:
        return None
    facts = _facts_from_storage(row.facts)
    return ChapterStateSnapshotContext(
        chapter_number=row.chapter_number,
        facts=facts,
    )


async def extract_chapter_state_snapshot(
    session: AsyncSession,
    settings: AppSettings,
    *,
    project_id: UUID,
    chapter: ChapterModel,
    chapter_md: str,
    workflow_run_id: UUID | None = None,
    step_run_id: UUID | None = None,
) -> ChapterStateSnapshotModel:
    """Extract and persist the end-of-chapter hard-fact snapshot.

    On any failure (empty LLM response, unparseable JSON, etc.) a row is still
    persisted with ``extraction_status != "ok"`` and ``facts={"facts": []}``,
    so the pipeline remains additive and never crashes on continuity issues.
    """

    previous = await load_previous_chapter_snapshot(
        session,
        project_id=project_id,
        current_chapter_number=chapter.chapter_number,
    )

    system_prompt = _EXTRACTION_SYSTEM_PROMPT
    user_prompt = _build_extraction_user_prompt(
        chapter=chapter,
        chapter_md=chapter_md or "（本章正文缺失）",
        previous_snapshot=previous,
    )

    fallback_payload = json.dumps({"facts": []}, ensure_ascii=False)

    completion = await complete_text(
        session,
        settings,
        LLMCompletionRequest(
            logical_role="editor",
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            fallback_response=fallback_payload,
            prompt_template="chapter_state_snapshot",
            prompt_version="1.0",
            project_id=project_id,
            workflow_run_id=workflow_run_id,
            step_run_id=step_run_id,
            metadata={
                "chapter_number": chapter.chapter_number,
                "task": "continuity_hard_fact_extraction",
            },
        ),
    )

    raw = completion.content or ""
    facts, error = _parse_extraction_payload(raw)
    extraction_status = "ok" if error is None else f"failed:{error}"

    if error is not None:
        logger.warning(
            "Hard-fact extraction failed for project=%s chapter=%d: %s",
            project_id,
            chapter.chapter_number,
            error,
        )

    existing = await session.scalar(
        select(ChapterStateSnapshotModel).where(
            ChapterStateSnapshotModel.project_id == project_id,
            ChapterStateSnapshotModel.chapter_id == chapter.id,
        )
    )

    stored_facts = _facts_to_storage(facts)

    if existing is None:
        snapshot = ChapterStateSnapshotModel(
            project_id=project_id,
            chapter_id=chapter.id,
            chapter_number=chapter.chapter_number,
            facts=stored_facts,
            raw_extraction=raw[:8000] if raw else None,
            extraction_model=completion.model_name,
            extraction_status=extraction_status,
        )
        session.add(snapshot)
    else:
        existing.facts = stored_facts
        existing.raw_extraction = raw[:8000] if raw else None
        existing.extraction_model = completion.model_name
        existing.extraction_status = extraction_status
        snapshot = existing

    await session.flush()
    return snapshot


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


def _extract_numeric(value: str) -> float | None:
    """Try to extract a numeric value from a fact value string."""
    import re as _re
    match = _re.search(r"[-+]?\d*\.?\d+", value)
    if match:
        return float(match.group())
    return None


def render_hard_fact_snapshot_block(snapshot: ChapterStateSnapshotContext | None) -> str:
    """Render the ``CURRENT_STATE`` prompt block for injection into writer prompts.

    Returns an empty string when there is no snapshot (first chapter, extraction
    failed, or snapshot has zero facts) so callers can safely concatenate.
    """
    if snapshot is None or not snapshot.facts:
        return ""

    lines: list[str] = [
        f"=== 当前事实状态（来自第 {snapshot.chapter_number} 章末 — 必须严格遵守，不得前后矛盾）===",
    ]
    for fact in snapshot.facts:
        prefix = f"[{fact.subject}] " if fact.subject else ""
        unit = f" {fact.unit}" if fact.unit else ""
        notes = f"  // {fact.notes}" if fact.notes else ""
        lines.append(f"- {prefix}{fact.name}: {fact.value}{unit}{notes}")
    lines.append(
        "=== 任何数值/位置/物品变化都必须在本章正文里给出读者可见的触发事件（交易、战斗、时间流逝等）===",
    )
    return "\n".join(lines)
