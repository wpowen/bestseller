"""Arc summary and world snapshot services for LINEAR novels.

Adapted from the IF system's arc summary and world snapshot patterns
(if_prompts.py lines 623-747, if_context.py ContextAssembler).

Arc summaries are generated at arc boundaries (every ~12 chapters) and
stored as CanonFactModel records with fact_type="arc_summary".
World snapshots track the evolving state of all characters, factions,
and world conditions, stored as fact_type="world_snapshot".
"""

from __future__ import annotations

import json
import logging
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.infra.db.models import CanonFactModel, ProjectModel
from bestseller.services.llm import LLMCompletionRequest, complete_text
from bestseller.services.planner import _json_dumps
from bestseller.services.writing_profile import is_english_language

logger = logging.getLogger(__name__)


# ── Arc Summary ──────────────────────────────────────────────────────


def _linear_arc_summary_prompt(
    project: ProjectModel,
    chapter_summaries: list[dict[str, Any]],
    arc_start: int,
    arc_end: int,
    language: str,
) -> tuple[str, str]:
    """Build system + user prompts for arc summary generation."""
    is_en = is_english_language(language)

    chapter_info = json.dumps(
        [
            {
                "chapter": s.get("chapter_number", "?"),
                "summary": s.get("summary", s.get("text", "")),
            }
            for s in chapter_summaries
        ],
        ensure_ascii=False,
    )

    system_prompt = (
        "You are summarizing a completed narrative arc for a long-form serial novel. "
        "Output ONLY valid JSON, no markdown."
        if is_en
        else "你正在为长篇连载小说总结一段已完成的叙事弧线。输出必须是合法 JSON，不要解释。"
    )

    user_prompt = (
        (
            f"Project: {project.title}\n"
            f"Arc chapters: {arc_start} to {arc_end}\n"
            f"Chapter summaries:\n{chapter_info}\n\n"
            "Write a structured arc summary. Output ONLY valid JSON:\n"
            "{\n"
            '  "protagonist_growth": "<how the protagonist grew during this arc (2-3 sentences)>",\n'
            '  "relationship_changes": [\n'
            '    {"characters": ["char_a", "char_b"], "change": "<how their relationship changed>"}\n'
            "  ],\n"
            '  "unresolved_threads": ["<plot thread still open after this arc>"],\n'
            '  "power_level_summary": "<protagonist power/status at arc end (1-2 sentences)>",\n'
            '  "next_arc_setup": "<what tension sets up the next arc (1-2 sentences)>",\n'
            '  "open_clues": [\n'
            '    {"code": "<clue_code>", "description": "<what was planted>", "planted_chapter": <N>}\n'
            "  ],\n"
            '  "resolved_clues": ["<clue_code resolved in this arc>"]\n'
            "}\n\n"
            "Rules:\n"
            "- Be specific and concrete\n"
            "- protagonist_growth must mention actual changes\n"
            "- next_arc_setup must end with anticipation or danger"
        )
        if is_en
        else (
            f"项目：{project.title}\n"
            f"弧线章节：第{arc_start}章 至 第{arc_end}章\n"
            f"各章摘要：\n{chapter_info}\n\n"
            "请输出结构化弧线总结（纯 JSON）：\n"
            "{\n"
            '  "protagonist_growth": "<主角在本弧线中的成长（2-3句）>",\n'
            '  "relationship_changes": [\n'
            '    {"characters": ["角色A", "角色B"], "change": "<关系变化>"}\n'
            "  ],\n"
            '  "unresolved_threads": ["<本弧线结束后仍未解决的伏线>"],\n'
            '  "power_level_summary": "<弧线结束时主角的实力/状态（1-2句）>",\n'
            '  "next_arc_setup": "<为下一弧线埋下的悬念/张力（1-2句）>",\n'
            '  "open_clues": [\n'
            '    {"code": "<线索代号>", "description": "<已埋下的线索>", "planted_chapter": <章号>}\n'
            "  ],\n"
            '  "resolved_clues": ["<本弧线中已回收的线索代号>"]\n'
            "}\n\n"
            "要求：具体、不空泛；主角成长必须提到实际变化；下弧设置必须有紧迫感。"
        )
    )

    return system_prompt, user_prompt


def _fallback_arc_summary(arc_start: int, arc_end: int) -> dict[str, Any]:
    """Generate a minimal fallback arc summary when LLM fails."""
    return {
        "protagonist_growth": f"主角在第{arc_start}-{arc_end}章中持续成长。",
        "relationship_changes": [],
        "unresolved_threads": [f"第{arc_end}章末的悬念待解。"],
        "power_level_summary": "主角的实力和认知都有所提升。",
        "next_arc_setup": "新的挑战即将到来。",
        "open_clues": [],
        "resolved_clues": [],
    }


async def generate_linear_arc_summary(
    session: AsyncSession,
    settings: Any,
    project: ProjectModel,
    arc_chapter_start: int,
    arc_chapter_end: int,
    chapter_summaries: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Generate an arc summary via LLM with fallback."""
    if chapter_summaries is None:
        chapter_summaries = []

    system_prompt, user_prompt = _linear_arc_summary_prompt(
        project, chapter_summaries, arc_chapter_start, arc_chapter_end, project.language,
    )
    fallback = _fallback_arc_summary(arc_chapter_start, arc_chapter_end)

    try:
        completion = await complete_text(
            session,
            settings,
            LLMCompletionRequest(
                logical_role="planner",
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                fallback_response=json.dumps(fallback, ensure_ascii=False),
                prompt_template="linear_arc_summary",
                prompt_version="1.0",
                project_id=project.id,
                metadata={
                    "arc_start": arc_chapter_start,
                    "arc_end": arc_chapter_end,
                },
            ),
        )
        payload = json.loads(completion.content)
        if not isinstance(payload, dict):
            payload = fallback
    except Exception:
        logger.warning(
            "Arc summary LLM failed for chapters %d-%d; using fallback",
            arc_chapter_start, arc_chapter_end, exc_info=True,
        )
        payload = fallback

    return payload


async def store_linear_arc_summary(
    session: AsyncSession,
    project: ProjectModel,
    arc_index: int,
    summary_data: dict[str, Any],
    ch_start: int,
    ch_end: int,
) -> CanonFactModel:
    """Store an arc summary as a CanonFactModel record."""
    fact = CanonFactModel(
        id=uuid4(),
        project_id=project.id,
        subject_type="arc",
        subject_label=f"arc_{arc_index:03d}",
        predicate="arc_summary",
        fact_type="arc_summary",
        value_json={
            **summary_data,
            "arc_index": arc_index,
            "chapter_start": ch_start,
            "chapter_end": ch_end,
        },
        confidence=1.0,
        source_type="generated",
        valid_from_chapter_no=ch_start,
        valid_to_chapter_no=ch_end,
        is_current=True,
        tags=["arc_summary", f"arc_{arc_index}"],
    )
    session.add(fact)
    await session.flush()
    return fact


async def load_recent_arc_summaries(
    session: AsyncSession,
    project_id: UUID,
    before_chapter: int,
    limit: int = 3,
) -> list[dict[str, Any]]:
    """Load the most recent arc summaries before a given chapter."""
    stmt = (
        select(CanonFactModel)
        .where(
            CanonFactModel.project_id == project_id,
            CanonFactModel.fact_type == "arc_summary",
            CanonFactModel.is_current.is_(True),
            CanonFactModel.valid_to_chapter_no < before_chapter,
        )
        .order_by(CanonFactModel.valid_to_chapter_no.desc())
        .limit(limit)
    )
    results = await session.scalars(stmt)
    return [r.value_json for r in results]


# ── World Snapshot ───────────────────────────────────────────────────


def _linear_world_snapshot_prompt(
    project: ProjectModel,
    arc_summary: dict[str, Any],
    prev_snapshot: dict[str, Any] | None,
    language: str,
) -> tuple[str, str]:
    """Build system + user prompts for world snapshot generation."""
    is_en = is_english_language(language)

    prev_section = ""
    if prev_snapshot:
        prev_section = (
            f"\nPrevious world snapshot:\n"
            f"  Character states: {json.dumps(prev_snapshot.get('character_states', {}), ensure_ascii=False)}\n"
            f"  Faction states: {json.dumps(prev_snapshot.get('faction_states', {}), ensure_ascii=False)}\n"
            f"  Revealed truths: {json.dumps(prev_snapshot.get('revealed_truths', []), ensure_ascii=False)}\n"
            f"  Active threats: {json.dumps(prev_snapshot.get('active_threats', []), ensure_ascii=False)}\n"
            if is_en
            else (
                f"\n前一次世界快照：\n"
                f"  角色状态：{json.dumps(prev_snapshot.get('character_states', {}), ensure_ascii=False)}\n"
                f"  势力状态：{json.dumps(prev_snapshot.get('faction_states', {}), ensure_ascii=False)}\n"
                f"  已揭示真相：{json.dumps(prev_snapshot.get('revealed_truths', []), ensure_ascii=False)}\n"
                f"  活跃威胁：{json.dumps(prev_snapshot.get('active_threats', []), ensure_ascii=False)}\n"
            )
        )

    system_prompt = (
        "You are tracking the world state for a long-form serial novel. "
        "Output ONLY valid JSON, no markdown."
        if is_en
        else "你正在追踪长篇连载小说的世界状态。输出必须是合法 JSON，不要解释。"
    )

    user_prompt = (
        (
            f"Project: {project.title}\n"
            f"Arc summary:\n{json.dumps(arc_summary, ensure_ascii=False)}\n"
            f"{prev_section}\n"
            "Generate an updated world state snapshot. Output ONLY valid JSON:\n"
            "{\n"
            '  "character_states": {\n'
            '    "<name>": {"status": "<current situation>", "location": "<where>", "attitude": "<toward protagonist>"}\n'
            "  },\n"
            '  "faction_states": {\n'
            '    "<name>": {"strength": "<dominant|strong|rising|weakening|collapsed>", "attitude": "<toward protagonist>"}\n'
            "  },\n"
            '  "revealed_truths": ["<fact revealed to protagonist or reader>"],\n'
            '  "active_threats": ["<ongoing danger or pressure>"],\n'
            '  "world_summary": "<200-word natural language summary for future prompts>"\n'
            "}"
        )
        if is_en
        else (
            f"项目：{project.title}\n"
            f"弧线总结：\n{json.dumps(arc_summary, ensure_ascii=False)}\n"
            f"{prev_section}\n"
            "请生成更新的世界状态快照（纯 JSON）：\n"
            "{\n"
            '  "character_states": {\n'
            '    "<角色名>": {"status": "<当前处境>", "location": "<所在位置>", "attitude": "<对主角态度>"}\n'
            "  },\n"
            '  "faction_states": {\n'
            '    "<势力名>": {"strength": "<dominant|strong|rising|weakening|collapsed>", "attitude": "<对主角态度>"}\n'
            "  },\n"
            '  "revealed_truths": ["<已揭示的真相>"],\n'
            '  "active_threats": ["<当前活跃的威胁>"],\n'
            '  "world_summary": "<200字以内的自然语言世界状态总结，直接注入后续章节 Prompt>"\n'
            "}\n\n"
            "要求：world_summary 用中文、现在时、不超过200字，必须涵盖主角实力、关键盟友/敌人状态、当前威胁。"
        )
    )

    return system_prompt, user_prompt


def _fallback_world_snapshot(arc_summary: dict[str, Any]) -> dict[str, Any]:
    """Generate a minimal fallback world snapshot."""
    return {
        "character_states": {},
        "faction_states": {},
        "revealed_truths": arc_summary.get("unresolved_threads", []),
        "active_threats": [arc_summary.get("next_arc_setup", "新的威胁正在酝酿。")],
        "world_summary": arc_summary.get("power_level_summary", "故事持续推进中。"),
    }


async def generate_linear_world_snapshot(
    session: AsyncSession,
    settings: Any,
    project: ProjectModel,
    chapter_number: int,
    arc_summary: dict[str, Any],
) -> dict[str, Any]:
    """Generate a world snapshot via LLM with fallback."""
    prev_snapshot = await load_latest_world_snapshot(session, project.id, chapter_number)

    system_prompt, user_prompt = _linear_world_snapshot_prompt(
        project, arc_summary, prev_snapshot, project.language,
    )
    fallback = _fallback_world_snapshot(arc_summary)

    try:
        completion = await complete_text(
            session,
            settings,
            LLMCompletionRequest(
                logical_role="planner",
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                fallback_response=json.dumps(fallback, ensure_ascii=False),
                prompt_template="linear_world_snapshot",
                prompt_version="1.0",
                project_id=project.id,
                metadata={"chapter_number": chapter_number},
            ),
        )
        payload = json.loads(completion.content)
        if not isinstance(payload, dict):
            payload = fallback
    except Exception:
        logger.warning(
            "World snapshot LLM failed for chapter %d; using fallback",
            chapter_number, exc_info=True,
        )
        payload = fallback

    return payload


async def store_linear_world_snapshot(
    session: AsyncSession,
    project: ProjectModel,
    chapter_number: int,
    snapshot_data: dict[str, Any],
) -> CanonFactModel:
    """Store a world snapshot as a CanonFactModel record."""
    # Mark previous snapshots as non-current
    prev_stmt = (
        select(CanonFactModel)
        .where(
            CanonFactModel.project_id == project.id,
            CanonFactModel.fact_type == "world_snapshot",
            CanonFactModel.is_current.is_(True),
        )
    )
    prev_facts = await session.scalars(prev_stmt)
    for f in prev_facts:
        f.is_current = False

    fact = CanonFactModel(
        id=uuid4(),
        project_id=project.id,
        subject_type="world",
        subject_label="world_state",
        predicate="world_snapshot",
        fact_type="world_snapshot",
        value_json={
            **snapshot_data,
            "as_of_chapter": chapter_number,
        },
        confidence=1.0,
        source_type="generated",
        valid_from_chapter_no=chapter_number,
        is_current=True,
        tags=["world_snapshot", f"ch_{chapter_number}"],
    )
    session.add(fact)
    await session.flush()
    return fact


async def load_latest_world_snapshot(
    session: AsyncSession,
    project_id: UUID,
    before_chapter: int,
) -> dict[str, Any] | None:
    """Load the most recent world snapshot before a given chapter."""
    stmt = (
        select(CanonFactModel)
        .where(
            CanonFactModel.project_id == project_id,
            CanonFactModel.fact_type == "world_snapshot",
            CanonFactModel.is_current.is_(True),
            CanonFactModel.valid_from_chapter_no < before_chapter,
        )
        .order_by(CanonFactModel.valid_from_chapter_no.desc())
        .limit(1)
    )
    result = await session.scalar(stmt)
    return result.value_json if result else None
