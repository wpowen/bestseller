from __future__ import annotations

import json
import logging
import re
from typing import Any
from uuid import UUID

from sqlalchemy import and_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.domain.feedback import (
    ArcBeatUpdateExtraction,
    CanonFactExtraction,
    ChapterFeedbackPayload,
    ChapterFeedbackResult,
    CharacterStateExtraction,
    ClueObservationExtraction,
    PromiseMadeExtraction,
    RelationshipEventExtraction,
    WorldDetailExtraction,
)
from bestseller.services.llm import LLMCompletionRequest, complete_text
from bestseller.services.writing_profile import is_english_language
from bestseller.settings import AppSettings


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------


async def extract_chapter_feedback(
    session: AsyncSession,
    settings: AppSettings,
    *,
    project_id: UUID,
    chapter: Any,
    chapter_md: str,
    workflow_run_id: UUID | None = None,
) -> ChapterFeedbackResult:
    """Run post-chapter feedback extraction and apply state changes to DB.

    This is the core "反哺" (fanbu) service.  It fires a SINGLE LLM call to
    extract structured state changes from the written prose, then writes them
    back into the relevant data models so subsequent chapters see an
    up-to-date world state.
    """
    from bestseller.infra.db.models import (
        AntagonistPlanModel,
        ArcBeatModel,
        CharacterModel,
        ClueModel,
        PlotArcModel,
        ProjectModel,
    )

    chapter_number: int = chapter.chapter_number
    chapter_id: UUID = chapter.id

    # Fetch the project to determine language
    project = await session.scalar(
        select(ProjectModel).where(ProjectModel.id == project_id)
    )
    language = project.language if project else None

    # Load context needed for the prompt
    characters = list(
        await session.scalars(
            select(CharacterModel).where(CharacterModel.project_id == project_id)
        )
    )
    active_arcs = list(
        await session.scalars(
            select(PlotArcModel).where(
                PlotArcModel.project_id == project_id,
                PlotArcModel.status.in_(["planned", "active", "in_progress"]),
            )
        )
    )
    unresolved_clues = list(
        await session.scalars(
            select(ClueModel).where(
                ClueModel.project_id == project_id,
                ClueModel.status.in_(["planted", "active"]),
                ClueModel.actual_paid_off_chapter_number.is_(None),
            )
        )
    )
    active_antagonist_plans = list(
        await session.scalars(
            select(AntagonistPlanModel).where(
                AntagonistPlanModel.project_id == project_id,
                AntagonistPlanModel.status == "active",
            )
        )
    )

    # Load arc beats for active arcs (needed for the prompt)
    arc_beats_by_arc: dict[UUID, list[Any]] = {}
    if active_arcs:
        arc_ids = [a.id for a in active_arcs]
        all_beats = list(
            await session.scalars(
                select(ArcBeatModel).where(ArcBeatModel.plot_arc_id.in_(arc_ids))
            )
        )
        for beat in all_beats:
            arc_beats_by_arc.setdefault(beat.plot_arc_id, []).append(beat)

    # 1. Build prompts
    system_prompt, user_prompt = _build_feedback_extraction_prompts(
        chapter=chapter,
        chapter_md=chapter_md,
        characters=characters,
        active_arcs=active_arcs,
        arc_beats_by_arc=arc_beats_by_arc,
        unresolved_clues=unresolved_clues,
        active_antagonist_plans=active_antagonist_plans,
        language=language,
    )

    # 2. Call LLM
    fallback = json.dumps(ChapterFeedbackPayload().model_dump(), ensure_ascii=False)
    request = LLMCompletionRequest(
        logical_role="editor",
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        fallback_response=fallback,
        prompt_template="chapter_feedback_extraction",
        prompt_version="1.0",
        project_id=project_id,
        workflow_run_id=workflow_run_id,
    )

    result = ChapterFeedbackResult(
        project_id=project_id,
        chapter_id=chapter_id,
        chapter_number=chapter_number,
    )

    try:
        llm_result = await complete_text(session, settings, request)
        result.llm_run_id = llm_result.llm_run_id
    except Exception:
        logger.exception(
            "LLM call failed for chapter %d feedback extraction", chapter_number
        )
        result.extraction_status = "llm_error"
        return result

    # 3. Parse JSON response tolerantly
    payload = _parse_feedback_payload(llm_result.content)
    if payload is None:
        logger.warning(
            "Failed to parse feedback payload for chapter %d", chapter_number
        )
        result.extraction_status = "parse_error"
        return result

    total_items = (
        len(payload.character_states)
        + len(payload.relationship_events)
        + len(payload.arc_beat_updates)
        + len(payload.clue_observations)
        + len(payload.world_details)
        + len(payload.canon_facts)
        + len(payload.promises_made)
    )
    if total_items == 0:
        result.extraction_status = "empty"
        return result

    # 4. Apply each category of extractions to DB
    result.character_states_updated = await _apply_character_state_updates(
        session, project_id, chapter, payload.character_states
    )
    result.relationship_events_created = await _apply_relationship_events(
        session, project_id, chapter, payload.relationship_events
    )
    result.arc_beats_updated = await _apply_arc_beat_updates(
        session, project_id, payload.arc_beat_updates
    )
    result.clue_observations_applied = await _apply_clue_payoff_observations(
        session, project_id, chapter, payload.clue_observations
    )
    result.world_details_enriched = await _apply_world_enrichments(
        session, project_id, payload.world_details
    )
    result.canon_facts_created = await _apply_canon_fact_extractions(
        session, project_id, chapter, payload.canon_facts
    )
    result.promises_created = await _apply_promise_extractions(
        session, project_id, chapter, payload.promises_made
    )

    logger.info(
        "Chapter %d feedback: %d char-states, %d rel-events, %d arc-beats, "
        "%d clues, %d world-details, %d canon-facts, %d promises",
        chapter_number,
        result.character_states_updated,
        result.relationship_events_created,
        result.arc_beats_updated,
        result.clue_observations_applied,
        result.world_details_enriched,
        result.canon_facts_created,
        result.promises_created,
    )
    return result


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------


_SYSTEM_PROMPT_ZH = """\
你是长篇小说反哺分析器。阅读章节正文后，提取以下结构化信息：
1. 角色状态变化（情绪、弧线、力量等级、信念变化、知识获取、信任变化、存活状态、对主角立场、生命周期离场状态）
2. 关系事件（两人之间的关系发生了什么变化）
3. 叙事弧节拍推进（哪些计划节拍被实际完成了）
4. 伏笔状态（哪些伏笔被埋下或兑现了）
5. 世界细节补充（正文中揭露了哪些新的地点/规则/势力细节）
6. 新事实（正文中确立了哪些重要事实）
7. 新立的承诺/誓言/债务（promises_made）

【立场、存活状态、生命周期离场状态的特殊规则】
- 只有正文明确写出触发事件时才填写变化后的值；否则全部留 null 维持原状。
- 填写 stance 时，必须在 stance_change_reason 里写出触发事件的一句话描述。
- alive_status 一旦判定为 deceased，即为正式死亡，后续章节不可再复活（除非原始计划允许假死）。
- 力量等级 (power_tier) 下降时，必须在 power_tier_downgrade_reason 里说明原因，否则本次变化被过滤。
- lifecycle_status 仅用于非死亡的离场状态：missing（失踪）/ sealed（封印）/ sleeping（沉睡）/
  comatose（昏迷）/ exiled（流放）。当正文明确触发上述状态时才填写，并在
  lifecycle_status_reason 里提供一句话触发事件描述（必须提供否则被忽略）。
  若正文中有明确的恢复/解封章节预告，填入 lifecycle_exit_chapter（整数），否则填 null。
- lifecycle_status 与 alive_status=deceased 互斥；如角色已死，只填 alive_status=deceased。

【承诺/誓言/债务提取规则（promises_made）】
- 只提取本章正文中**新立的**承诺、誓言、债务或义务；已有的承诺不重复提取。
- 必须同时有立誓者（promisor）和受诺者（promisee），且两人都是已知角色。
- kind 可选值：revenge（复仇）/ protection（守护）/ message（传话）/ fealty（效忠）/
  debt（债务）/ quest（任务）/ deathbed（临终遗愿）/ null（其他）。
- 若正文明确提及兑现章节，填入 due_chapter（整数），否则填 null。

只提取正文中明确呈现的内容，不要推测或虚构。
输出必须是合法 JSON，不要解释。"""

_SYSTEM_PROMPT_EN = """\
You are a novel feedback analyzer. After reading a chapter, extract structured \
information about character state changes, relationship events, arc beat \
completions, clue observations, world details, and new canon facts.

Lifecycle fields — alive_status / stance / power_tier / lifecycle_status — only
change when the prose shows an explicit triggering event (betrayal, alliance,
death, sealing, going missing, exile, etc.).

Rules:
- When stance changes, stance_change_reason MUST describe the triggering event
  in one sentence, or the change is rejected.
- When power_tier drops, power_tier_downgrade_reason MUST give the cause.
- Once alive_status is "deceased" the character cannot later be "alive" again
  in future chapters unless the project allows fake deaths.
- lifecycle_status covers non-death offstage states only:
    missing | sealed | sleeping | comatose | exiled
  Fill it only when the prose explicitly triggers one of these states, AND
  provide lifecycle_status_reason (one sentence). Without a reason the field
  is silently dropped. If the prose hints at a planned return chapter, fill
  lifecycle_exit_chapter (integer); otherwise null.
- lifecycle_status and alive_status=deceased are mutually exclusive.

For promises_made: only extract NEW promises / oaths / vows / debts that are
explicitly established in this chapter's prose. A promise requires both a
promisor (the one making it) and a promisee (the recipient). Valid kinds are:
revenge, protection, message, fealty, debt, quest, deathbed. If the prose
explicitly names a chapter where the promise will be fulfilled, fill
due_chapter (integer); otherwise null.

Only extract what is explicitly shown in the prose. Do not infer or fabricate.
Output valid JSON only."""

_OUTPUT_SCHEMA = """\
{
  "character_states": [
    {
      "character_name": "...",
      "emotional_state": "...|null",
      "arc_state": "...|null",
      "power_tier": "...|null",
      "physical_state": "...|null",
      "alive_status": "alive|injured|dying|deceased|null",
      "stance": "ally|enemy|neutral|conflicted|protagonist|rival|null",
      "stance_change_reason": "本章触发立场变化的关键事件简述，若无变化填 null",
      "power_tier_downgrade_reason": "若本章力量等级下降，说明原因（封印/重伤/道具失效等），若未下降填 null",
      "lifecycle_status": "missing|sealed|sleeping|comatose|exiled|null",
      "lifecycle_status_reason": "触发离场状态的关键事件一句话描述（若无变化填 null，必须提供否则被忽略）",
      "lifecycle_exit_chapter": "预计恢复/解封/回归的章节编号，若未知则填 null",
      "beliefs_gained": ["..."],
      "beliefs_invalidated": ["..."],
      "knowledge_gained": ["..."],
      "trust_changes": {"other_character_name": "increased|decreased|broken|restored"}
    }
  ],
  "relationship_events": [
    {
      "character_a": "...",
      "character_b": "...",
      "event_description": "...",
      "relationship_change": "...",
      "is_milestone": false
    }
  ],
  "arc_beat_updates": [
    {
      "arc_code": "...",
      "beat_order": 1,
      "status": "completed|in_progress|failed",
      "evidence": "..."
    }
  ],
  "clue_observations": [
    {
      "clue_code": "...",
      "action": "planted|paid_off",
      "evidence": "..."
    }
  ],
  "world_details": [
    {
      "entity_type": "location|rule|faction",
      "name": "...",
      "detail": "..."
    }
  ],
  "canon_facts": [
    {
      "subject": "...",
      "predicate": "...",
      "value": "...",
      "fact_type": "extracted"
    }
  ],
  "promises_made": [
    {
      "promisor": "立誓/承诺者角色名",
      "promisee": "受诺者角色名",
      "content": "承诺内容（一句话，用原文语气）",
      "kind": "revenge|protection|message|fealty|debt|quest|deathbed|null",
      "due_chapter": "预计兑现章节（若正文明确提及），否则填 null"
    }
  ]
}"""


def _build_feedback_extraction_prompts(
    chapter: Any,
    chapter_md: str,
    characters: list[Any],
    active_arcs: list[Any],
    arc_beats_by_arc: dict[UUID, list[Any]],
    unresolved_clues: list[Any],
    active_antagonist_plans: list[Any],
    *,
    language: str | None = None,
) -> tuple[str, str]:
    """Build system + user prompts for feedback extraction.

    Returns (system_prompt, user_prompt).
    """
    is_en = is_english_language(language)
    system_prompt = _SYSTEM_PROMPT_EN if is_en else _SYSTEM_PROMPT_ZH

    # --- User prompt sections ---
    sections: list[str] = []

    # Header
    chapter_title = getattr(chapter, "title", None) or ""
    chapter_number = chapter.chapter_number
    if is_en:
        sections.append(f"# Chapter {chapter_number}: {chapter_title}")
    else:
        sections.append(f"# 第{chapter_number}章：{chapter_title}")

    # Full chapter prose
    if is_en:
        sections.append("## Chapter Prose")
    else:
        sections.append("## 章节正文")
    sections.append(chapter_md)

    # Character roster (compact)
    if characters:
        if is_en:
            sections.append("## Current Character Roster")
        else:
            sections.append("## 当前角色列表")
        for char in characters:
            knowledge_summary = _compact_knowledge_summary(char.knowledge_state_json)
            arc_state = getattr(char, "arc_state", None) or "unknown"
            sections.append(
                f"- {char.name} | arc_state={arc_state} | knowledge={knowledge_summary}"
            )

    # Active arcs with beats
    if active_arcs:
        if is_en:
            sections.append("## Active Arcs (with beats)")
        else:
            sections.append("## 活跃叙事弧（含节拍）")
        for arc in active_arcs:
            sections.append(f"- {arc.arc_code}: {arc.name} (status={arc.status})")
            beats = arc_beats_by_arc.get(arc.id, [])
            sorted_beats = sorted(beats, key=lambda b: b.beat_order)
            for beat in sorted_beats:
                sections.append(
                    f"  - beat {beat.beat_order}: {beat.summary} (status={beat.status})"
                )

    # Unresolved clues
    if unresolved_clues:
        if is_en:
            sections.append("## Unresolved Clues")
        else:
            sections.append("## 未解决伏笔")
        for clue in unresolved_clues:
            sections.append(f"- {clue.clue_code}: {clue.label}")

    # Active antagonist plans
    if active_antagonist_plans:
        if is_en:
            sections.append("## Active Antagonist Plans")
        else:
            sections.append("## 活跃反派计划")
        for plan in active_antagonist_plans:
            sections.append(f"- {plan.plan_code}: {plan.title} (move={plan.current_move})")

    # Output schema specification
    if is_en:
        sections.append("## Output JSON Schema")
        sections.append(
            "Please output strictly in the following JSON structure, "
            "with no additional explanation:"
        )
    else:
        sections.append("## 输出 JSON 格式")
        sections.append("请严格按照以下 JSON 结构输出，不要添加任何说明文字：")
    sections.append(f"```json\n{_OUTPUT_SCHEMA}\n```")

    user_prompt = "\n\n".join(sections)
    return system_prompt, user_prompt


def _compact_knowledge_summary(knowledge_state: dict[str, Any] | None) -> str:
    """Return a compact one-line summary of character knowledge state."""
    if not knowledge_state:
        return "{}"
    knows = knowledge_state.get("knows", [])
    falsely_believes = knowledge_state.get("falsely_believes", [])
    parts: list[str] = []
    if knows:
        parts.append(f"knows({len(knows)})")
    if falsely_believes:
        parts.append(f"falsely_believes({len(falsely_believes)})")
    return ", ".join(parts) if parts else "{}"


# ---------------------------------------------------------------------------
# JSON parser — tolerant
# ---------------------------------------------------------------------------


def _parse_feedback_payload(text: str) -> ChapterFeedbackPayload | None:
    """Tolerant parser that extracts a ChapterFeedbackPayload from LLM output.

    Tries multiple strategies:
    1. Direct json.loads on the stripped text
    2. Extract from markdown code fences
    3. Find outermost { ... } braces
    4. Return empty payload on total failure
    """
    stripped = text.strip()

    # Strategy 1: direct parse
    parsed = _try_json_loads(stripped)
    if parsed is not None:
        return _validate_payload(parsed)

    # Strategy 2: markdown code fence
    fence_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", stripped, re.DOTALL)
    if fence_match:
        parsed = _try_json_loads(fence_match.group(1).strip())
        if parsed is not None:
            return _validate_payload(parsed)

    # Strategy 3: find outermost braces
    first_brace = stripped.find("{")
    last_brace = stripped.rfind("}")
    if first_brace != -1 and last_brace > first_brace:
        parsed = _try_json_loads(stripped[first_brace : last_brace + 1])
        if parsed is not None:
            return _validate_payload(parsed)

    # Strategy 4: empty fallback
    logger.warning("All JSON parse strategies failed, returning empty payload")
    return ChapterFeedbackPayload()


def _try_json_loads(text: str) -> dict[str, Any] | None:
    """Attempt json.loads, returning None on failure."""
    try:
        result = json.loads(text)
        if isinstance(result, dict):
            return result
    except (json.JSONDecodeError, ValueError):
        pass
    return None


def _validate_payload(data: dict[str, Any]) -> ChapterFeedbackPayload:
    """Validate and construct a ChapterFeedbackPayload from a dict.

    Invalid items within a list are silently dropped to maximize extraction.
    """
    try:
        return ChapterFeedbackPayload.model_validate(data)
    except Exception:
        logger.warning("Pydantic validation failed, attempting partial extraction")
        return _partial_extract_payload(data)


def _partial_extract_payload(data: dict[str, Any]) -> ChapterFeedbackPayload:
    """Best-effort partial extraction when full validation fails."""
    payload = ChapterFeedbackPayload()

    for field_name, model_cls in [
        ("character_states", CharacterStateExtraction),
        ("relationship_events", RelationshipEventExtraction),
        ("arc_beat_updates", ArcBeatUpdateExtraction),
        ("clue_observations", ClueObservationExtraction),
        ("world_details", WorldDetailExtraction),
        ("canon_facts", CanonFactExtraction),
        ("promises_made", PromiseMadeExtraction),
    ]:
        raw_list = data.get(field_name, [])
        if not isinstance(raw_list, list):
            continue
        valid_items: list[Any] = []
        for item in raw_list:
            if not isinstance(item, dict):
                continue
            try:
                valid_items.append(model_cls.model_validate(item))
            except Exception:
                logger.debug("Skipping invalid %s item: %s", field_name, item)
        setattr(payload, field_name, valid_items)

    return payload


# ---------------------------------------------------------------------------
# Apply functions
# ---------------------------------------------------------------------------


async def _apply_character_state_updates(
    session: AsyncSession,
    project_id: UUID,
    chapter: Any,
    extractions: list[CharacterStateExtraction],
) -> int:
    """Apply character state changes extracted from prose."""
    from bestseller.infra.db.models import CharacterModel, CharacterStateSnapshotModel

    if not extractions:
        return 0

    applied = 0
    # Track characters whose lifecycle transitioned to "deceased" in this
    # chapter so the death-ripple service can propagate grief / closure
    # / relationship-end events after every state has been written.
    newly_deceased_ids: list[UUID] = []
    for extraction in extractions:
        # Find character by name
        character = await session.scalar(
            select(CharacterModel).where(
                CharacterModel.project_id == project_id,
                CharacterModel.name == extraction.character_name,
            )
        )
        if character is None:
            logger.warning(
                "Feedback: character '%s' not found, skipping state update",
                extraction.character_name,
            )
            continue

        # Event-gated lifecycle fields: stance writeback requires a reason,
        # alive_status "deceased" stamps death_chapter_number, power_tier
        # downgrade requires a reason. Values that fail gating are kept in
        # the snapshot as evidence but never propagated to CharacterModel.
        _current_alive = getattr(character, "alive_status", None) or "alive"
        _accepted_alive: str | None = None
        if extraction.alive_status:
            if _current_alive == "deceased" and extraction.alive_status != "deceased":
                # Never resurrect — keep existing deceased state. Log so the
                # contradiction check has evidence if the prose slipped through.
                logger.warning(
                    "Feedback ch%d: rejecting alive_status %s→%s for '%s' "
                    "(character is already deceased)",
                    chapter.chapter_number,
                    _current_alive,
                    extraction.alive_status,
                    character.name,
                )
            else:
                _accepted_alive = extraction.alive_status

        _current_stance = getattr(character, "stance", None)
        _accepted_stance: str | None = None
        if (
            extraction.stance
            and extraction.stance != _current_stance
            and _current_stance not in (None, "")
        ):
            if not (extraction.stance_change_reason or "").strip():
                logger.warning(
                    "Feedback ch%d: rejecting stance %s→%s for '%s' "
                    "(no stance_change_reason provided)",
                    chapter.chapter_number,
                    _current_stance,
                    extraction.stance,
                    character.name,
                )
            else:
                _accepted_stance = extraction.stance
        elif extraction.stance and _current_stance in (None, ""):
            # First-time stance assignment doesn't need justification.
            _accepted_stance = extraction.stance

        # Create snapshot — always record extraction values so reviewers can
        # inspect what the LLM claimed even when gating rejected writeback.
        trust_map: dict[str, str] = dict(extraction.trust_changes) if extraction.trust_changes else {}
        beliefs = list(extraction.beliefs_gained) if extraction.beliefs_gained else []
        snapshot = CharacterStateSnapshotModel(
            project_id=project_id,
            character_id=character.id,
            chapter_id=chapter.id,
            chapter_number=chapter.chapter_number,
            scene_number=None,
            arc_state=extraction.arc_state,
            emotional_state=extraction.emotional_state,
            physical_state=extraction.physical_state,
            power_tier=extraction.power_tier,
            alive_status=extraction.alive_status,
            stance=extraction.stance,
            trust_map=trust_map,
            beliefs=beliefs,
            notes=f"feedback_extraction:ch{chapter.chapter_number}",
        )
        session.add(snapshot)

        # Update character knowledge state
        updated_knowledge = _merge_knowledge_state(
            existing=dict(character.knowledge_state_json),
            knowledge_gained=extraction.knowledge_gained,
            beliefs_gained=extraction.beliefs_gained,
            beliefs_invalidated=extraction.beliefs_invalidated,
        )
        _update_values: dict[str, Any] = {"knowledge_state_json": updated_knowledge}

        # Writeback power_tier to CharacterModel so subsequent chapter prompts
        # see the latest value rather than the bible-time default. The fallback
        # in story_bible.get_effective_character_state depends on either the
        # snapshot or the character row holding a non-null value; keeping the
        # character row current makes the "most recent non-null" lookup cheap.
        if extraction.power_tier:
            _update_values["power_tier"] = extraction.power_tier

        if _accepted_alive:
            _update_values["alive_status"] = _accepted_alive
            if (
                _accepted_alive == "deceased"
                and getattr(character, "death_chapter_number", None) is None
            ):
                _update_values["death_chapter_number"] = chapter.chapter_number
                # Queue this character for the post-loop death-ripple
                # propagation. We capture the id (immutable) rather than
                # the row so a concurrent update of ``character`` does
                # not affect the queue. Only first-time deaths trigger
                # ripples; an already-dead-but-re-extracted row stays
                # silent (idempotency below also catches duplicates).
                newly_deceased_ids.append(character.id)

        if _accepted_stance:
            _update_values["stance"] = _accepted_stance

        # Lifecycle status writeback — non-deceased offstage states are stored
        # in metadata_json.lifecycle_status so the effective_lifecycle_state()
        # resolver can find them.  Guard rules mirror the stance writeback:
        # a reason is required and the kind must be in the allowed set.
        _accepted_lifecycle: str | None = None
        _lifecycle_reason = (extraction.lifecycle_status_reason or "").strip()
        if extraction.lifecycle_status:
            from bestseller.services.character_lifecycle import LIFECYCLE_KINDS  # noqa: PLC0415

            _lk = extraction.lifecycle_status.strip().lower()
            # "deceased" is already handled via alive_status; skip it here to
            # avoid double-writing and to keep the two pathways clean.
            if _lk in LIFECYCLE_KINDS and _lk != "deceased":
                if _lifecycle_reason:
                    _accepted_lifecycle = _lk
                else:
                    logger.warning(
                        "Feedback ch%d: rejecting lifecycle_status=%s for '%s' "
                        "(no lifecycle_status_reason provided)",
                        chapter.chapter_number,
                        _lk,
                        character.name,
                    )

        # Phase-4: advance lie_truth_arc phase when core_lie is invalidated
        # Also handles lifecycle writeback here since both mutate metadata_json.
        _char_meta = dict(character.metadata_json or {})
        _meta_dirty = False

        if extraction.beliefs_invalidated:
            _lt_arc = _char_meta.get("lie_truth_arc")
            if isinstance(_lt_arc, dict) and _lt_arc.get("core_lie"):
                _core_lie = _lt_arc["core_lie"]
                if _core_lie in extraction.beliefs_invalidated:
                    _phase_order = [
                        "believing_lie",
                        "questioning_lie",
                        "confronting_lie",
                        "embracing_truth",
                    ]
                    _cur = _lt_arc.get("current_phase", "believing_lie")
                    try:
                        _idx = _phase_order.index(_cur)
                    except ValueError:
                        _idx = 0
                    if _idx < len(_phase_order) - 1:
                        _lt_arc = {**_lt_arc, "current_phase": _phase_order[_idx + 1]}
                        _char_meta = {**_char_meta, "lie_truth_arc": _lt_arc}
                        _meta_dirty = True

        if _accepted_lifecycle:
            _lifecycle_payload: dict[str, Any] = {
                "kind": _accepted_lifecycle,
                "since_chapter": chapter.chapter_number,
                "reason": _lifecycle_reason,
            }
            if extraction.lifecycle_exit_chapter is not None:
                _lifecycle_payload["scheduled_exit_chapter"] = extraction.lifecycle_exit_chapter
            _char_meta = {**_char_meta, "lifecycle_status": _lifecycle_payload}
            _meta_dirty = True
            logger.info(
                "Feedback ch%d: setting lifecycle_status=%s for '%s' (exit_ch=%s)",
                chapter.chapter_number,
                _accepted_lifecycle,
                character.name,
                extraction.lifecycle_exit_chapter,
            )

        if _meta_dirty:
            _update_values["metadata_json"] = _char_meta

        await session.execute(
            update(CharacterModel)
            .where(CharacterModel.id == character.id)
            .values(**_update_values)
        )

        # Update arc_state if provided
        if extraction.arc_state:
            await session.execute(
                update(CharacterModel)
                .where(CharacterModel.id == character.id)
                .values(arc_state=extraction.arc_state)
            )

        applied += 1

    await session.flush()

    # Death-ripple propagation runs AFTER every state row in this
    # chapter has been written so the survivors' relationships /
    # current states are consistent. Failures are logged but never
    # break feedback — the ripples are a quality enhancement, not a
    # correctness gate.
    if newly_deceased_ids:
        try:
            from bestseller.services.death_ripple import (  # noqa: PLC0415
                apply_death_ripples_for_chapter,
            )
            await apply_death_ripples_for_chapter(
                session,
                project_id=project_id,
                deceased_character_ids=newly_deceased_ids,
                chapter_number=chapter.chapter_number,
            )
        except Exception:
            logger.exception(
                "death_ripple post-feedback failed for project=%s ch=%s",
                project_id,
                getattr(chapter, "chapter_number", "?"),
            )

    return applied


def _merge_knowledge_state(
    *,
    existing: dict[str, Any],
    knowledge_gained: list[str],
    beliefs_gained: list[str],
    beliefs_invalidated: list[str],
) -> dict[str, Any]:
    """Merge new knowledge into existing character knowledge_state_json.

    Returns a NEW dict (immutable pattern).

    Structure: {
        "knows": [...],
        "falsely_believes": [...],
    }
    """
    knows: list[str] = list(existing.get("knows", []))
    falsely_believes: list[str] = list(existing.get("falsely_believes", []))

    # Add new knowledge items
    knows_set = set(knows)
    for item in knowledge_gained:
        if item not in knows_set:
            knows.append(item)
            knows_set.add(item)

    # Add new beliefs
    for belief in beliefs_gained:
        if belief not in knows_set:
            knows.append(belief)
            knows_set.add(belief)

    # Invalidate beliefs: remove from falsely_believes if present,
    # add to knows as "learned the truth"
    invalidated_set = set(beliefs_invalidated)
    falsely_believes = [fb for fb in falsely_believes if fb not in invalidated_set]

    return {
        **existing,
        "knows": knows,
        "falsely_believes": falsely_believes,
    }


async def _apply_relationship_events(
    session: AsyncSession,
    project_id: UUID,
    chapter: Any,
    extractions: list[RelationshipEventExtraction],
) -> int:
    """Create relationship event records and update emotion tracks."""
    from bestseller.infra.db.models import (
        CharacterModel,
        EmotionTrackModel,
        RelationshipEventModel,
    )

    if not extractions:
        return 0

    applied = 0
    for extraction in extractions:
        # Create relationship event record
        event = RelationshipEventModel(
            project_id=project_id,
            character_a_label=extraction.character_a,
            character_b_label=extraction.character_b,
            chapter_number=chapter.chapter_number,
            scene_number=None,
            event_description=extraction.event_description,
            relationship_change=extraction.relationship_change,
            is_milestone=extraction.is_milestone,
            metadata_json={"source": "feedback_extraction"},
        )
        session.add(event)

        # Find matching emotion track by character pair labels
        emotion_track = await session.scalar(
            select(EmotionTrackModel).where(
                EmotionTrackModel.project_id == project_id,
                (
                    and_(
                        EmotionTrackModel.character_a_label == extraction.character_a,
                        EmotionTrackModel.character_b_label == extraction.character_b,
                    )
                    | and_(
                        EmotionTrackModel.character_a_label == extraction.character_b,
                        EmotionTrackModel.character_b_label == extraction.character_a,
                    )
                ),
            )
        )
        if emotion_track is not None:
            trust_delta, conflict_delta = _infer_emotion_deltas(
                extraction.relationship_change
            )
            new_trust = max(0.0, min(1.0, float(emotion_track.trust_level) + trust_delta))
            new_conflict = max(0.0, min(1.0, float(emotion_track.conflict_level) + conflict_delta))
            await session.execute(
                update(EmotionTrackModel)
                .where(EmotionTrackModel.id == emotion_track.id)
                .values(
                    trust_level=new_trust,
                    conflict_level=new_conflict,
                    last_shift_chapter_number=chapter.chapter_number,
                )
            )

        applied += 1

    await session.flush()
    return applied


def _infer_emotion_deltas(relationship_change: str) -> tuple[float, float]:
    """Infer trust and conflict level deltas from a relationship change description.

    Returns (trust_delta, conflict_delta) in range [-0.15, +0.15].
    """
    text_lower = relationship_change.lower()

    trust_delta = 0.0
    conflict_delta = 0.0

    # Positive trust indicators
    positive_trust = [
        "trust", "closer", "bond", "alliance", "reconcil", "forgive",
        "信任", "亲近", "和解", "原谅", "结盟", "信赖",
    ]
    # Negative trust indicators
    negative_trust = [
        "betray", "distrust", "suspect", "broken", "deceiv", "lie",
        "背叛", "怀疑", "欺骗", "猜忌", "破裂", "不信任",
    ]
    # Conflict increase indicators
    conflict_increase = [
        "conflict", "fight", "argue", "tension", "hostil", "oppos",
        "confront", "clash",
        "冲突", "争吵", "对抗", "敌意", "对立", "矛盾",
    ]
    # Conflict decrease indicators
    conflict_decrease = [
        "peace", "resolv", "calm", "agree", "harmon", "cooperat",
        "和平", "解决", "平息", "合作", "和谐",
    ]

    for keyword in positive_trust:
        if keyword in text_lower:
            trust_delta += 0.1
            break
    for keyword in negative_trust:
        if keyword in text_lower:
            trust_delta -= 0.1
            break
    for keyword in conflict_increase:
        if keyword in text_lower:
            conflict_delta += 0.1
            break
    for keyword in conflict_decrease:
        if keyword in text_lower:
            conflict_delta -= 0.1
            break

    # Clamp
    trust_delta = max(-0.15, min(0.15, trust_delta))
    conflict_delta = max(-0.15, min(0.15, conflict_delta))

    return trust_delta, conflict_delta


async def _apply_arc_beat_updates(
    session: AsyncSession,
    project_id: UUID,
    extractions: list[ArcBeatUpdateExtraction],
) -> int:
    """Update arc beat statuses and check for arc completion."""
    from bestseller.infra.db.models import ArcBeatModel, PlotArcModel

    if not extractions:
        return 0

    applied = 0
    arcs_to_check: set[UUID] = set()

    for extraction in extractions:
        # Find the plot arc
        plot_arc = await session.scalar(
            select(PlotArcModel).where(
                PlotArcModel.project_id == project_id,
                PlotArcModel.arc_code == extraction.arc_code,
            )
        )
        if plot_arc is None:
            logger.warning(
                "Feedback: arc '%s' not found, skipping beat update",
                extraction.arc_code,
            )
            continue

        # Find the specific beat
        arc_beat = await session.scalar(
            select(ArcBeatModel).where(
                ArcBeatModel.plot_arc_id == plot_arc.id,
                ArcBeatModel.beat_order == extraction.beat_order,
            )
        )
        if arc_beat is None:
            logger.warning(
                "Feedback: beat %d for arc '%s' not found, skipping",
                extraction.beat_order,
                extraction.arc_code,
            )
            continue

        # Update beat status
        new_metadata = dict(arc_beat.metadata_json)
        new_metadata["feedback_evidence"] = extraction.evidence
        await session.execute(
            update(ArcBeatModel)
            .where(ArcBeatModel.id == arc_beat.id)
            .values(
                status=extraction.status,
                metadata_json=new_metadata,
            )
        )

        arcs_to_check.add(plot_arc.id)
        applied += 1

    # Check if any arcs are now fully completed
    for arc_id in arcs_to_check:
        all_beats = list(
            await session.scalars(
                select(ArcBeatModel).where(ArcBeatModel.plot_arc_id == arc_id)
            )
        )
        if all_beats and all(b.status == "completed" for b in all_beats):
            await session.execute(
                update(PlotArcModel)
                .where(PlotArcModel.id == arc_id)
                .values(status="completed")
            )
            logger.info("Feedback: arc %s fully completed", arc_id)

    await session.flush()
    return applied


async def _apply_clue_payoff_observations(
    session: AsyncSession,
    project_id: UUID,
    chapter: Any,
    extractions: list[ClueObservationExtraction],
) -> int:
    """Update clue planting / payoff status based on observations."""
    from bestseller.infra.db.models import ClueModel

    if not extractions:
        return 0

    applied = 0
    for extraction in extractions:
        clue = await session.scalar(
            select(ClueModel).where(
                ClueModel.project_id == project_id,
                ClueModel.clue_code == extraction.clue_code,
            )
        )
        if clue is None:
            logger.warning(
                "Feedback: clue '%s' not found, skipping", extraction.clue_code
            )
            continue

        if extraction.action == "planted":
            # Only set planted chapter if not already set
            if clue.planted_in_chapter_number is None:
                new_metadata = dict(clue.metadata_json)
                new_metadata["planted_evidence"] = extraction.evidence
                await session.execute(
                    update(ClueModel)
                    .where(ClueModel.id == clue.id)
                    .values(
                        planted_in_chapter_number=chapter.chapter_number,
                        status="planted",
                        metadata_json=new_metadata,
                    )
                )
                applied += 1

        elif extraction.action == "paid_off":
            new_metadata = dict(clue.metadata_json)
            new_metadata["payoff_evidence"] = extraction.evidence
            await session.execute(
                update(ClueModel)
                .where(ClueModel.id == clue.id)
                .values(
                    actual_paid_off_chapter_number=chapter.chapter_number,
                    status="paid_off",
                    metadata_json=new_metadata,
                )
            )
            applied += 1

    await session.flush()
    return applied


async def _apply_world_enrichments(
    session: AsyncSession,
    project_id: UUID,
    extractions: list[WorldDetailExtraction],
) -> int:
    """Append world details to location / rule / faction metadata."""
    from bestseller.infra.db.models import FactionModel, LocationModel, WorldRuleModel

    if not extractions:
        return 0

    applied = 0
    for extraction in extractions:
        entity_type = extraction.entity_type.lower()

        if entity_type == "location":
            entity = await session.scalar(
                select(LocationModel).where(
                    LocationModel.project_id == project_id,
                    LocationModel.name == extraction.name,
                )
            )
            if entity is None:
                logger.warning(
                    "Feedback: location '%s' not found, skipping", extraction.name
                )
                continue
            new_metadata = _append_prose_detail(entity.metadata_json, extraction.detail)
            await session.execute(
                update(LocationModel)
                .where(LocationModel.id == entity.id)
                .values(metadata_json=new_metadata)
            )

        elif entity_type == "rule":
            entity = await session.scalar(
                select(WorldRuleModel).where(
                    WorldRuleModel.project_id == project_id,
                    WorldRuleModel.name == extraction.name,
                )
            )
            if entity is None:
                logger.warning(
                    "Feedback: world rule '%s' not found, skipping", extraction.name
                )
                continue
            new_metadata = _append_prose_detail(entity.metadata_json, extraction.detail)
            await session.execute(
                update(WorldRuleModel)
                .where(WorldRuleModel.id == entity.id)
                .values(metadata_json=new_metadata)
            )

        elif entity_type == "faction":
            entity = await session.scalar(
                select(FactionModel).where(
                    FactionModel.project_id == project_id,
                    FactionModel.name == extraction.name,
                )
            )
            if entity is None:
                logger.warning(
                    "Feedback: faction '%s' not found, skipping", extraction.name
                )
                continue
            new_metadata = _append_prose_detail(entity.metadata_json, extraction.detail)
            await session.execute(
                update(FactionModel)
                .where(FactionModel.id == entity.id)
                .values(metadata_json=new_metadata)
            )

        else:
            logger.warning(
                "Feedback: unknown entity_type '%s', skipping", entity_type
            )
            continue

        applied += 1

    await session.flush()
    return applied


def _append_prose_detail(
    metadata_json: dict[str, Any], detail: str
) -> dict[str, Any]:
    """Append a detail to the 'prose_details' list in metadata.

    Returns a NEW dict (immutable pattern).
    """
    new_metadata = dict(metadata_json)
    prose_details: list[str] = list(new_metadata.get("prose_details", []))
    prose_details.append(detail)
    new_metadata["prose_details"] = prose_details
    return new_metadata


async def _apply_canon_fact_extractions(
    session: AsyncSession,
    project_id: UUID,
    chapter: Any,
    extractions: list[CanonFactExtraction],
) -> int:
    """Create or update canon facts based on extraction."""
    from bestseller.infra.db.models import CanonFactModel

    if not extractions:
        return 0

    applied = 0
    for extraction in extractions:
        # Check for existing current fact with same subject + predicate
        existing = await session.scalar(
            select(CanonFactModel).where(
                CanonFactModel.project_id == project_id,
                CanonFactModel.subject_label == extraction.subject,
                CanonFactModel.predicate == extraction.predicate,
                CanonFactModel.is_current.is_(True),
            )
        )

        if existing is not None:
            # Supersede the old fact
            await session.execute(
                update(CanonFactModel)
                .where(CanonFactModel.id == existing.id)
                .values(
                    is_current=False,
                    valid_to_chapter_no=chapter.chapter_number,
                )
            )
            # Create new fact referencing the old one
            new_fact = CanonFactModel(
                project_id=project_id,
                subject_type=existing.subject_type,
                subject_id=existing.subject_id,
                subject_label=extraction.subject,
                predicate=extraction.predicate,
                fact_type=extraction.fact_type,
                value_json={"value": extraction.value},
                confidence=1.0,
                source_type="feedback",
                source_chapter_id=chapter.id,
                valid_from_chapter_no=chapter.chapter_number,
                supersedes_fact_id=existing.id,
                is_current=True,
                tags=["feedback_extracted"],
            )
            session.add(new_fact)
        else:
            # Infer subject_type from the subject string
            subject_type = _infer_subject_type(extraction.subject)
            new_fact = CanonFactModel(
                project_id=project_id,
                subject_type=subject_type,
                subject_id=None,
                subject_label=extraction.subject,
                predicate=extraction.predicate,
                fact_type=extraction.fact_type,
                value_json={"value": extraction.value},
                confidence=1.0,
                source_type="feedback",
                source_chapter_id=chapter.id,
                valid_from_chapter_no=chapter.chapter_number,
                is_current=True,
                tags=["feedback_extracted"],
            )
            session.add(new_fact)

        applied += 1

    await session.flush()
    return applied


async def _apply_promise_extractions(
    session: AsyncSession,
    project_id: UUID,
    chapter: Any,
    extractions: list[PromiseMadeExtraction],
) -> int:
    """Persist newly-made promises extracted from prose into the ledger.

    Resolution side (fulfilled / broken) is NOT handled here — that
    requires matching against existing rows which is a separate flow.
    Here we only create *new* promise records.

    Each extraction goes through a basic sanity filter:
    - ``promisor`` and ``promisee`` must be non-empty and different.
    - ``content`` must be non-empty.
    - Character name lookup is attempted; if neither can be found the
      promise still records (plans/early chapters may reference chars
      not yet in the DB) but ``promisor_id`` / ``promisee_id`` stay null.
    """
    from bestseller.infra.db.models import CharacterModel  # noqa: PLC0415
    from bestseller.services.interpersonal_promises import (  # noqa: PLC0415
        record_promise,
    )

    if not extractions:
        return 0

    created = 0
    for extraction in extractions:
        promisor_name = (extraction.promisor or "").strip()
        promisee_name = (extraction.promisee or "").strip()
        content = (extraction.content or "").strip()

        # Sanity guard: both parties and content are required.
        if not promisor_name or not promisee_name or not content:
            logger.debug(
                "Feedback ch%d: skipping incomplete promise extraction "
                "(promisor=%r promisee=%r content_len=%d)",
                chapter.chapter_number,
                promisor_name,
                promisee_name,
                len(content),
            )
            continue
        if promisor_name == promisee_name:
            logger.debug(
                "Feedback ch%d: skipping self-promise for '%s'",
                chapter.chapter_number,
                promisor_name,
            )
            continue

        # Best-effort character row lookup (missing rows → null FKs, not error).
        promisor_row = await session.scalar(
            select(CharacterModel).where(
                CharacterModel.project_id == project_id,
                CharacterModel.name == promisor_name,
            )
        )
        promisee_row = await session.scalar(
            select(CharacterModel).where(
                CharacterModel.project_id == project_id,
                CharacterModel.name == promisee_name,
            )
        )

        await record_promise(
            session,
            project_id=project_id,
            promisor=promisor_row,
            promisee=promisee_row,
            promisor_label=promisor_name,
            promisee_label=promisee_name,
            content=content,
            kind=extraction.kind or None,
            made_chapter_number=chapter.chapter_number,
            due_chapter_number=extraction.due_chapter,
            metadata={"source": "feedback_extraction"},
        )
        created += 1
        logger.info(
            "Feedback ch%d: recorded promise %s→%s [%s]",
            chapter.chapter_number,
            promisor_name,
            promisee_name,
            extraction.kind or "?",
        )

    return created


def _infer_subject_type(subject: str) -> str:
    """Infer the subject_type for a canon fact based on the subject label.

    This is a best-effort heuristic; the LLM extraction prompt does not
    require the caller to provide subject_type explicitly.
    """
    lower = subject.lower()
    location_hints = [
        "city", "town", "village", "forest", "mountain", "river", "castle",
        "palace", "temple", "cave", "island", "realm", "kingdom",
        "城", "镇", "村", "山", "河", "宫", "殿", "庙", "岛", "域",
    ]
    character_hints = [
        "lord", "lady", "prince", "princess", "king", "queen", "master",
        "大人", "公主", "王子", "国王", "女王", "师父",
    ]
    for hint in location_hints:
        if hint in lower:
            return "location"
    for hint in character_hints:
        if hint in lower:
            return "character"
    return "general"
