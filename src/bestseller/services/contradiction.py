"""Pre-scene contradiction detection service.

Runs BEFORE each scene is written to catch potential contradictions and
continuity issues.  All checks are pure database queries — zero LLM cost.

Checks performed
----------------

1. **Character knowledge leaks** — flags if `scene_information_release`
   references something a participant `falsely_believes` or is `unaware_of`.
2. **Stale clues** — planted/active clues whose expected payoff chapter has
   already passed without resolution.
3. **Dormant antagonist plans** — active/escalating plans whose target chapter
   was exceeded by a configurable threshold.
4. **Dead-end arcs** — active plot arcs with no completed beats in the last N
   chapters.
5. **Timeline ordering** — non-monotonic ``story_order`` in recent timeline
   events.
"""

from __future__ import annotations

import logging
import re
from typing import Any
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.domain.contradiction import (
    ContradictionCheckResult,
    ContradictionViolation,
    ContradictionWarning,
)
from bestseller.infra.db.models import (
    AntagonistPlanModel,
    ArcBeatModel,
    ChapterModel,
    CharacterModel,
    CharacterStateSnapshotModel,
    ClueModel,
    PlotArcModel,
    ProjectModel,
    RelationshipEventModel,
    SceneCardModel,
    SceneContractModel,
    TimelineEventModel,
)
from bestseller.services.writing_profile import is_english_language
from bestseller.settings import AppSettings


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Default thresholds — used when ``settings`` is None or the pipeline section
# does not carry the relevant field.
# ---------------------------------------------------------------------------

_DEFAULT_STALE_CLUE_THRESHOLD: int = 15
_DEFAULT_DORMANT_PLAN_THRESHOLD: int = 10
_DEFAULT_ARC_INACTIVITY_THRESHOLD: int = 8


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CJK_WORD_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]{2,}")
_LATIN_WORD_RE = re.compile(r"[A-Za-z\u00C0-\u024F]{3,}")


def _extract_keywords(text: str | None) -> set[str]:
    """Extract rough keywords from a string for heuristic matching.

    For CJK text we pull 2+ char substrings; for Latin text 3+ char words.
    All results are lowercased.
    """
    if not text:
        return set()
    keywords: set[str] = set()
    for match in _CJK_WORD_RE.finditer(text):
        keywords.add(match.group(0))
    for match in _LATIN_WORD_RE.finditer(text):
        keywords.add(match.group(0).lower())
    return keywords


def _get_threshold(settings: AppSettings | None, attr: str, default: int) -> int:
    """Safely read a pipeline threshold from settings."""
    if settings is None:
        return default
    pipeline = getattr(settings, "pipeline", None)
    if pipeline is None:
        return default
    return int(getattr(pipeline, attr, default))


# ---------------------------------------------------------------------------
# Sub-checks
# ---------------------------------------------------------------------------


async def _check_character_knowledge_leaks(
    session: AsyncSession,
    project_id: UUID,
    chapter_number: int,
    scene_participants: list[str] | None,
    scene_information_release: str | None,
    language: str | None = None,
) -> tuple[list[ContradictionViolation], list[ContradictionWarning]]:
    """Flag if a scene's information release conflicts with a participant's
    knowledge state (falsely_believes or unaware_of).
    """
    violations: list[ContradictionViolation] = []
    warnings: list[ContradictionWarning] = []

    if not scene_participants or not scene_information_release:
        return violations, warnings

    _is_en = is_english_language(language)

    release_keywords = _extract_keywords(scene_information_release)
    if not release_keywords:
        return violations, warnings

    stmt = (
        select(CharacterModel)
        .where(
            CharacterModel.project_id == project_id,
            CharacterModel.name.in_(scene_participants),
        )
    )
    result = await session.execute(stmt)
    characters = result.scalars().all()

    for character in characters:
        knowledge: dict[str, Any] = character.knowledge_state_json or {}
        falsely_believes: list[str] = knowledge.get("falsely_believes", []) or []
        unaware_of: list[str] = knowledge.get("unaware_of", []) or []

        for item in falsely_believes:
            item_keywords = _extract_keywords(item)
            overlap = release_keywords & item_keywords
            if overlap:
                kw_str = ", ".join(sorted(overlap))
                message = (
                    f"Character '{character.name}' currently falsely believes '{item}', "
                    f"but this scene's information release involves related content (keywords: {kw_str}). "
                    f"The cognitive conflict must be addressed in the scene."
                    if _is_en else
                    f"角色「{character.name}」当前错误地相信「{item}」，"
                    f"但本场景的信息释放涉及相关内容（关键词：{kw_str}）。"
                    f"需要在场景中处理这一认知冲突。"
                )
                violations.append(
                    ContradictionViolation(
                        check_type="character_knowledge_leak",
                        severity="error",
                        message=message,
                        evidence=f"falsely_believes: {item}",
                    )
                )

        for item in unaware_of:
            item_keywords = _extract_keywords(item)
            overlap = release_keywords & item_keywords
            if overlap:
                kw_str = ", ".join(sorted(overlap))
                message = (
                    f"Character '{character.name}' is currently unaware of '{item}', "
                    f"but this scene plans to release related information (keywords: {kw_str}). "
                    f"Confirm whether an information-delivery event should be arranged first."
                    if _is_en else
                    f"角色「{character.name}」目前不知道「{item}」，"
                    f"但本场景计划释放相关信息（关键词：{kw_str}）。"
                    f"确认是否需要先安排信息传递事件。"
                )
                recommendation = (
                    f"Add a bridging event before or during this scene to let '{character.name}' learn this information."
                    if _is_en else
                    f"在本场景之前或之中加入让「{character.name}」获知此信息的桥接事件。"
                )
                warnings.append(
                    ContradictionWarning(
                        check_type="character_knowledge_leak",
                        message=message,
                        recommendation=recommendation,
                    )
                )

    return violations, warnings


async def _check_stale_clues(
    session: AsyncSession,
    project_id: UUID,
    chapter_number: int,
    settings: AppSettings | None,
    language: str | None = None,
) -> list[ContradictionWarning]:
    """Find planted/active clues whose expected payoff chapter has passed."""
    warnings: list[ContradictionWarning] = []

    threshold = _get_threshold(settings, "feedback_stale_clue_threshold", _DEFAULT_STALE_CLUE_THRESHOLD)

    stmt = (
        select(ClueModel)
        .where(
            ClueModel.project_id == project_id,
            ClueModel.status.in_(("planted", "active")),
            ClueModel.expected_payoff_by_chapter_number.isnot(None),
            ClueModel.expected_payoff_by_chapter_number < chapter_number,
            ClueModel.actual_paid_off_chapter_number.is_(None),
        )
        .order_by(ClueModel.expected_payoff_by_chapter_number.asc())
        .limit(50)
    )
    result = await session.execute(stmt)
    stale_clues = result.scalars().all()

    _is_en = is_english_language(language)

    for clue in stale_clues:
        overdue_chapters = chapter_number - (clue.expected_payoff_by_chapter_number or chapter_number)
        planted_ch = clue.planted_in_chapter_number or "?"
        expected_ch = clue.expected_payoff_by_chapter_number
        label = getattr(clue, "label", "") or ""

        if _is_en:
            severity_note = "critically overdue" if overdue_chapters > threshold else "overdue"
            message = (
                f"Clue '{clue.clue_code}' ({label}) planted in chapter {planted_ch}, "
                f"expected payoff by chapter {expected_ch}, "
                f"now at chapter {chapter_number} — {overdue_chapters} chapters overdue. "
                f"({severity_note})"
            )
            recommendation = (
                f"Resolve clue '{clue.clue_code}' in the upcoming scene or later chapters, "
                f"or adjust its expected payoff timeline."
            )
        else:
            severity_note = "严重超期" if overdue_chapters > threshold else "已超期"
            message = (
                f"线索「{clue.clue_code}」（{label}）"
                f"在第 {planted_ch} 章埋下，"
                f"预期在第 {expected_ch} 章前回收，"
                f"当前已到第 {chapter_number} 章，超期 {overdue_chapters} 章。"
                f"（{severity_note}）"
            )
            recommendation = (
                f"在即将写作的场景或后续章节中安排线索「{clue.clue_code}」的回收，"
                f"或调整其预期回收时间。"
            )

        warnings.append(
            ContradictionWarning(
                check_type="stale_clue",
                message=message,
                recommendation=recommendation,
            )
        )

    return warnings


async def _check_dormant_antagonist_plans(
    session: AsyncSession,
    project_id: UUID,
    chapter_number: int,
    settings: AppSettings | None,
    language: str | None = None,
) -> list[ContradictionWarning]:
    """Find active/escalating antagonist plans that have gone dormant."""
    warnings: list[ContradictionWarning] = []

    threshold = _get_threshold(settings, "feedback_dormant_plan_threshold", _DEFAULT_DORMANT_PLAN_THRESHOLD)

    stmt = (
        select(AntagonistPlanModel)
        .where(
            AntagonistPlanModel.project_id == project_id,
            AntagonistPlanModel.status.in_(("active", "escalating")),
            AntagonistPlanModel.target_chapter_number.isnot(None),
            (AntagonistPlanModel.target_chapter_number + threshold) < chapter_number,
        )
        .order_by(AntagonistPlanModel.target_chapter_number.asc())
        .limit(50)
    )
    result = await session.execute(stmt)
    dormant_plans = result.scalars().all()

    _is_en = is_english_language(language)

    for plan in dormant_plans:
        overdue = chapter_number - (plan.target_chapter_number or chapter_number)
        if _is_en:
            message = (
                f"Antagonist plan '{plan.plan_code}' ({plan.title}) "
                f"targeted chapter {plan.target_chapter_number}, "
                f"now at chapter {chapter_number} — {overdue} chapters past target, "
                f"status is still '{plan.status}' with no recent progress."
            )
            recommendation = (
                f"Advance or escalate antagonist plan '{plan.plan_code}', "
                f"or mark its status as failed/shelved."
            )
        else:
            message = (
                f"反派计划「{plan.plan_code}」（{plan.title}）"
                f"目标章节为第 {plan.target_chapter_number} 章，"
                f"当前第 {chapter_number} 章已超出目标 {overdue} 章，"
                f"状态仍为「{plan.status}」但长期无推进。"
            )
            recommendation = (
                f"推进或升级反派计划「{plan.plan_code}」，"
                f"或将其状态标记为已失败/已搁置。"
            )
        warnings.append(
            ContradictionWarning(
                check_type="dormant_antagonist",
                message=message,
                recommendation=recommendation,
            )
        )

    return warnings


async def _check_dead_end_arcs(
    session: AsyncSession,
    project_id: UUID,
    chapter_number: int,
    settings: AppSettings | None,
    language: str | None = None,
) -> list[ContradictionWarning]:
    """Find active plot arcs with no recently completed beats."""
    warnings: list[ContradictionWarning] = []

    threshold = _get_threshold(settings, "feedback_arc_inactivity_threshold", _DEFAULT_ARC_INACTIVITY_THRESHOLD)
    lookback_start = max(1, chapter_number - threshold)

    stmt = (
        select(PlotArcModel)
        .where(
            PlotArcModel.project_id == project_id,
            PlotArcModel.status == "active",
        )
    )
    result = await session.execute(stmt)
    active_arcs = result.scalars().all()

    _is_en = is_english_language(language)

    for arc in active_arcs:
        recent_completed_stmt = (
            select(func.count())
            .select_from(ArcBeatModel)
            .where(
                ArcBeatModel.plot_arc_id == arc.id,
                ArcBeatModel.status == "completed",
                ArcBeatModel.scope_chapter_number.isnot(None),
                ArcBeatModel.scope_chapter_number >= lookback_start,
            )
        )
        count = await session.scalar(recent_completed_stmt) or 0

        if count == 0:
            if _is_en:
                message = (
                    f"Plot arc '{arc.arc_code}' ({arc.name}) is active, "
                    f"but has no completed beats in the last {threshold} chapters "
                    f"(chapters {lookback_start}--{chapter_number})."
                )
                recommendation = (
                    f"Schedule new beat progression for plot arc '{arc.arc_code}', "
                    f"or mark its status as paused/completed."
                )
            else:
                message = (
                    f"情节弧「{arc.arc_code}」（{arc.name}）状态为活跃，"
                    f"但在最近 {threshold} 章（第 {lookback_start}–{chapter_number} 章）"
                    f"内没有任何已完成的弧线节拍。"
                )
                recommendation = (
                    f"为情节弧「{arc.arc_code}」安排新的节拍推进，"
                    f"或将其状态标记为暂停/完成。"
                )
            warnings.append(
                ContradictionWarning(
                    check_type="dead_end_arc",
                    message=message,
                    recommendation=recommendation,
                )
            )

    return warnings


async def _check_timeline_ordering(
    session: AsyncSession,
    project_id: UUID,
    chapter_number: int,
    language: str | None = None,
) -> list[ContradictionViolation]:
    """Check that recent timeline events have monotonically increasing story_order."""
    violations: list[ContradictionViolation] = []

    lookback_start = max(1, chapter_number - 5)

    stmt = (
        select(TimelineEventModel)
        .where(
            TimelineEventModel.project_id == project_id,
        )
        .order_by(TimelineEventModel.story_order.asc())
        .limit(200)
    )
    result = await session.execute(stmt)
    events = result.scalars().all()

    if len(events) < 2:
        return violations

    _is_en = is_english_language(language)

    prev_order: float | None = None
    prev_name: str | None = None

    for event in events:
        current_order = float(event.story_order)
        current_name = event.event_name or "(unnamed)"

        if prev_order is not None and current_order < prev_order:
            message = (
                f"Timeline ordering anomaly: '{prev_name}' (order={prev_order}) "
                f"is followed by '{current_name}' (order={current_order}) — "
                f"story_order is not monotonically increasing."
                if _is_en else
                f"时间线事件顺序异常：「{prev_name}」(order={prev_order}) "
                f"之后出现了「{current_name}」(order={current_order})，"
                f"story_order 非递增。"
            )
            violations.append(
                ContradictionViolation(
                    check_type="timeline_order",
                    severity="error",
                    message=message,
                    evidence=(
                        f"prev_order={prev_order}, current_order={current_order}"
                    ),
                )
            )

        prev_order = current_order
        prev_name = current_name

    return violations


async def _check_scene_state_continuity(
    session: AsyncSession,
    project_id: UUID,
    chapter_number: int,
    scene_number: int,
    language: str | None = None,
) -> tuple[list[ContradictionViolation], list[ContradictionWarning]]:
    """Compare the previous scene's exit_state with the current scene's entry_state.

    If scene_number > 1, loads scene (scene_number - 1) of the same chapter.
    If scene_number == 1, loads the last scene of the previous chapter.
    Flags obvious contradictions between exit and entry states.
    """
    violations: list[ContradictionViolation] = []
    warnings: list[ContradictionWarning] = []

    # Resolve the current chapter
    current_chapter = await session.scalar(
        select(ChapterModel).where(
            ChapterModel.project_id == project_id,
            ChapterModel.chapter_number == chapter_number,
        )
    )
    if current_chapter is None:
        return violations, warnings

    # Load current scene
    current_scene = await session.scalar(
        select(SceneCardModel).where(
            SceneCardModel.chapter_id == current_chapter.id,
            SceneCardModel.scene_number == scene_number,
        )
    )
    if current_scene is None:
        return violations, warnings

    # Determine previous scene
    prev_scene: SceneCardModel | None = None
    if scene_number > 1:
        prev_scene = await session.scalar(
            select(SceneCardModel).where(
                SceneCardModel.chapter_id == current_chapter.id,
                SceneCardModel.scene_number == scene_number - 1,
            )
        )
    elif chapter_number > 1:
        prev_chapter = await session.scalar(
            select(ChapterModel).where(
                ChapterModel.project_id == project_id,
                ChapterModel.chapter_number == chapter_number - 1,
            )
        )
        if prev_chapter is not None:
            # Get the last scene of the previous chapter (highest scene_number)
            prev_scene = await session.scalar(
                select(SceneCardModel)
                .where(SceneCardModel.chapter_id == prev_chapter.id)
                .order_by(SceneCardModel.scene_number.desc())
                .limit(1)
            )

    if prev_scene is None:
        return violations, warnings

    prev_exit: dict[str, Any] = prev_scene.exit_state or {}
    curr_entry: dict[str, Any] = current_scene.entry_state or {}

    if not prev_exit or not curr_entry:
        return violations, warnings

    # Extract keywords from all string values in both state dicts
    prev_exit_text = " ".join(str(v) for v in prev_exit.values() if isinstance(v, str))
    curr_entry_text = " ".join(str(v) for v in curr_entry.values() if isinstance(v, str))

    prev_keywords = _extract_keywords(prev_exit_text)
    curr_keywords = _extract_keywords(curr_entry_text)

    if not prev_keywords or not curr_keywords:
        return violations, warnings

    _is_en = is_english_language(language)

    prev_ch = chapter_number if scene_number > 1 else chapter_number - 1

    # Heuristic conflict pairs: if previous exit mentions one term and current
    # entry mentions its opposite, flag a potential contradiction.
    _conflict_pairs = [
        ("离开", "仍在"),
        ("死亡", "活着"),
        ("离去", "留下"),
        ("昏迷", "清醒"),
        ("lost", "found"),
        ("left", "stayed"),
        ("dead", "alive"),
        ("unconscious", "awake"),
        ("destroyed", "intact"),
    ]

    for term_a, term_b in _conflict_pairs:
        a_in_prev = any(term_a in kw for kw in prev_keywords)
        b_in_curr = any(term_b in kw for kw in curr_keywords)
        a_in_curr = any(term_a in kw for kw in curr_keywords)
        b_in_prev = any(term_b in kw for kw in prev_keywords)

        if (a_in_prev and b_in_curr) or (b_in_prev and a_in_curr):
            found_exit = term_a if a_in_prev else term_b
            found_entry = term_b if a_in_prev else term_a
            if _is_en:
                message = (
                    f"Scene state discontinuity: previous scene exit state contains '{found_exit}', "
                    f"but current scene entry state contains '{found_entry}'. "
                    f"(Previous: chapter {prev_ch} scene {prev_scene.scene_number} "
                    f"-> Current: chapter {chapter_number} scene {scene_number})"
                )
            else:
                message = (
                    f"场景状态不连续：上一场景离场状态包含「{found_exit}」，"
                    f"但当前场景入场状态包含「{found_entry}」。"
                    f"（上一场景：第{prev_ch}章"
                    f"第{prev_scene.scene_number}场 → 当前：第{chapter_number}章第{scene_number}场）"
                )
            violations.append(
                ContradictionViolation(
                    check_type="scene_state_continuity",
                    severity="error",
                    message=message,
                    evidence=(
                        f"prev_exit={prev_exit_text[:200]}; "
                        f"curr_entry={curr_entry_text[:200]}"
                    ),
                )
            )

    # Also warn if there is no keyword overlap at all between exit and entry,
    # which may indicate a missing transition.
    overlap = prev_keywords & curr_keywords
    if not overlap and prev_keywords and curr_keywords:
        if _is_en:
            message = (
                f"Previous scene (chapter {prev_ch} scene {prev_scene.scene_number}) "
                f"exit state and current scene (chapter {chapter_number} scene {scene_number}) "
                f"entry state share no common keywords — a transition may be missing."
            )
            recommendation = "Check whether a bridging or transition event is needed between the two scenes."
        else:
            message = (
                f"上一场景（第{prev_ch}章"
                f"第{prev_scene.scene_number}场）的离场状态与"
                f"当前场景（第{chapter_number}章第{scene_number}场）的入场状态"
                f"没有任何共同关键词，可能存在过渡缺失。"
            )
            recommendation = "检查两个场景之间是否缺少衔接或过渡事件。"
        warnings.append(
            ContradictionWarning(
                check_type="scene_state_continuity",
                message=message,
                recommendation=recommendation,
            )
        )

    return violations, warnings


# ---------------------------------------------------------------------------
# Character lifecycle checks (Phase A4 — resurrection / stance / power_tier)
# ---------------------------------------------------------------------------


async def _check_resurrection(
    session: AsyncSession,
    project_id: UUID,
    chapter_number: int,
    scene_participants: list[str] | None,
    language: str | None = None,
    *,
    scene: Any = None,
) -> tuple[list[ContradictionViolation], list[ContradictionWarning]]:
    """Flag scenes that stage a deceased character as an active participant.

    A character is treated as dead in chapter N only when
    ``characters.death_chapter_number`` is set and strictly less than N.
    ``alive_status`` is *not* consulted: it reflects the character row's
    current snapshot and contains no timeline information, so using it
    here would spread the present-tense state to every historical chapter.
    Reincarnation is allowed only if the project invariants opt in.

    Flashback / memorial / vision / dream / quoted-reference scenes are
    EXEMPT — a deceased character may legitimately appear there as memory
    or quoted material, and the planner uses ``scene.scene_type`` or
    ``scene.metadata_json.scene_mode`` to mark such scenes. Pass the
    ``scene`` keyword so the check can read those annotations.

    Fake-death characters whose reveal chapter has passed
    (``metadata_json.fake_death.revealed_chapter <= N``) are also
    exempt — the "death" was a ruse.
    """
    from bestseller.services.character_lifecycle import (
        is_character_dead_at_chapter,
        scene_is_flashback_like,
    )

    violations: list[ContradictionViolation] = []
    warnings: list[ContradictionWarning] = []

    if not scene_participants:
        return violations, warnings

    # Flashback / memorial / vision / dream scenes are allowed to stage
    # deceased characters — that is the whole point of those modes.
    # Returning early here also keeps the prompt's "deceased may be
    # remembered / quoted / used as a corpse or letter" guidance
    # consistent with what the contradiction layer enforces.
    if scene is not None and scene_is_flashback_like(scene):
        return violations, warnings

    project = await session.get(ProjectModel, project_id)
    reincarnation_allowed = False
    if project is not None and project.invariants_json:
        reincarnation_allowed = bool(
            project.invariants_json.get("reincarnation_allowed", False)
        )

    _is_en = is_english_language(language)

    for name in scene_participants:
        character = await session.scalar(
            select(CharacterModel).where(
                CharacterModel.project_id == project_id,
                CharacterModel.name == name,
            )
        )
        if character is None:
            continue

        # death_chapter_number is the only timeline-anchored signal;
        # the helper folds in the fake-death-revealed exemption so a
        # character whose "death" was a ruse and has been revealed
        # alive can resume normal scene participation.
        death_ch = getattr(character, "death_chapter_number", None)
        # The original check used ``death_ch < chapter_number`` (strict);
        # we keep that semantic — a character "died in chapter N" can
        # still appear in chapter N's tail scenes as the death itself.
        is_dead = (
            death_ch is not None
            and int(death_ch) < int(chapter_number)
            and is_character_dead_at_chapter(
                death_chapter_number=death_ch,
                chapter_number=chapter_number,
                character_metadata=getattr(character, "metadata_json", None),
            )
        )
        if not is_dead:
            continue

        if reincarnation_allowed:
            if _is_en:
                recommendation = (
                    f"'{name}' died in chapter {death_ch}. Reincarnation is "
                    "allowed — make the return explicit (memory, vision, "
                    "reborn form)."
                )
            else:
                recommendation = (
                    f"「{name}」死于第{death_ch}章。本项目允许转世，"
                    "请在场景内显式说明回归形态（回忆/幻象/转世之身）。"
                )
            warnings.append(
                ContradictionWarning(
                    check_type="character_resurrection",
                    message=recommendation,
                    recommendation=recommendation,
                )
            )
            continue

        if _is_en:
            message = (
                f"Deceased character '{name}' is listed as a participant in "
                f"chapter {chapter_number} (died chapter {death_ch}). "
                "The scene must be rewritten without their active role."
            )
        else:
            message = (
                f"第{chapter_number}章的场景参与者包含已故角色「{name}」"
                f"（死于第{death_ch}章）。请修改场景，勿让其出场或说话。"
            )
        violations.append(
            ContradictionViolation(
                check_type="character_resurrection",
                severity="error",
                message=message,
                evidence=f"death_chapter={death_ch}",
            )
        )

    return violations, warnings


async def _check_offstage_state_appearances(
    session: AsyncSession,
    project_id: UUID,
    chapter_number: int,
    scene_participants: list[str] | None,
    language: str | None = None,
    *,
    scene: Any = None,
) -> tuple[list[ContradictionViolation], list[ContradictionWarning]]:
    """Flag participants whose lifecycle kind forbids present-tense
    participation in this chapter.

    Complementary to ``_check_resurrection``:

    * ``_check_resurrection`` covers the canonical death case (the
      character has *died* and should not act).
    * This check covers the other offstage states — ``missing``,
      ``sealed``, ``sleeping``, ``comatose`` — which are not deaths
      but still forbid present-tense scene participation.

    The lifecycle kind is read from ``metadata_json.lifecycle_status``
    via :func:`character_lifecycle.effective_lifecycle_state`. A
    character whose effective kind is ``deceased`` is left for the
    older check so we keep evidence shapes / messages stable.

    Like the resurrection check, this is exempted by flashback /
    memorial / vision / dream / quoted-reference scenes — those modes
    legitimately stage offstage characters as memory or symbol.
    """
    from bestseller.services.character_lifecycle import (
        OFFSTAGE_KINDS,
        appearance_rule_for,
        effective_lifecycle_state,
        scene_is_flashback_like,
    )

    violations: list[ContradictionViolation] = []
    warnings: list[ContradictionWarning] = []

    if not scene_participants:
        return violations, warnings

    if scene is not None and scene_is_flashback_like(scene):
        return violations, warnings

    _is_en = is_english_language(language)

    for name in scene_participants:
        character = await session.scalar(
            select(CharacterModel).where(
                CharacterModel.project_id == project_id,
                CharacterModel.name == name,
            )
        )
        if character is None:
            continue

        kind, payload = effective_lifecycle_state(
            alive_status=getattr(character, "alive_status", None),
            death_chapter_number=getattr(character, "death_chapter_number", None),
            chapter_number=chapter_number,
            character_metadata=getattr(character, "metadata_json", None),
        )

        # Deceased flows through ``_check_resurrection`` for stable
        # evidence / message shape; here we only police the other
        # offstage kinds.
        if kind == "deceased" or kind not in OFFSTAGE_KINDS:
            continue

        rule = appearance_rule_for(kind)
        if rule.can_act_in_present:
            continue

        since = payload.get("since_chapter")
        scheduled_exit = payload.get("scheduled_exit_chapter")
        kind_zh = {
            "missing": "失踪",
            "sealed": "被封印",
            "sleeping": "沉睡",
            "comatose": "昏迷",
        }.get(kind, kind)

        if _is_en:
            msg = (
                f"Character '{name}' is currently {kind} (since chapter "
                f"{since or '?'}"
                f"{f', release planned at chapter {scheduled_exit}' if scheduled_exit else ''})"
                f" and cannot act in chapter {chapter_number}. "
                "They may be referenced as a body / sealed form / vague "
                "rumour, or surface in a flashback / vision scene, but "
                "no present-tense action or new dialogue."
            )
        else:
            msg = (
                f"角色「{name}」当前处于「{kind_zh}」状态"
                f"（自第{since or '?'}章起"
                f"{f'，计划于第{scheduled_exit}章解除' if scheduled_exit else ''}），"
                f"第{chapter_number}章不可发出当下动作或新对话。"
                "可作为肉身/封印体/远闻被提及，"
                "或在显式标注的回忆/幻象/梦境场景中出现。"
            )
        violations.append(
            ContradictionViolation(
                check_type=f"character_{kind}_appearance",
                severity="error",
                message=msg,
                evidence=(
                    f"kind={kind}; since={since}; scheduled_exit={scheduled_exit}"
                ),
            )
        )

    return violations, warnings


async def _check_stance_flip(
    session: AsyncSession,
    project_id: UUID,
    chapter_number: int,
    scene_participants: list[str] | None,
    language: str | None = None,
) -> tuple[list[ContradictionViolation], list[ContradictionWarning]]:
    """Warn when a character's stance flips without a milestone event.

    Rules:
    * Character's current ``stance`` vs. most recent snapshot ``stance``.
    * An ally↔enemy flip requires (a) a milestone relationship event in the
      last 3 chapters OR (b) ``stance_locked_until_chapter`` not yet expired.
    * Lock-active violations are hard errors; missing-event is a warning.
    """
    violations: list[ContradictionViolation] = []
    warnings: list[ContradictionWarning] = []

    if not scene_participants:
        return violations, warnings

    _is_en = is_english_language(language)
    _hostile = {"enemy", "rival", "antagonist"}
    _friendly = {"ally", "friend", "mentor", "protagonist"}

    def _is_flip(prev: str | None, curr: str | None) -> bool:
        if not prev or not curr:
            return False
        return (
            (prev in _hostile and curr in _friendly)
            or (prev in _friendly and curr in _hostile)
        )

    for name in scene_participants:
        character = await session.scalar(
            select(CharacterModel).where(
                CharacterModel.project_id == project_id,
                CharacterModel.name == name,
            )
        )
        if character is None:
            continue

        curr_stance = getattr(character, "stance", None)
        if not curr_stance:
            continue

        prior_snap = await session.scalar(
            select(CharacterStateSnapshotModel)
            .where(
                CharacterStateSnapshotModel.project_id == project_id,
                CharacterStateSnapshotModel.character_id == character.id,
                CharacterStateSnapshotModel.chapter_number < chapter_number,
                CharacterStateSnapshotModel.stance.is_not(None),
            )
            .order_by(
                CharacterStateSnapshotModel.chapter_number.desc(),
                CharacterStateSnapshotModel.scene_number.desc().nullslast(),
                CharacterStateSnapshotModel.created_at.desc(),
            )
            .limit(1)
        )
        prior_stance = prior_snap.stance if prior_snap is not None else None

        if not _is_flip(prior_stance, curr_stance):
            continue

        locked_until = getattr(character, "stance_locked_until_chapter", None)
        if locked_until is not None and locked_until >= chapter_number:
            if _is_en:
                message = (
                    f"Stance flip blocked: '{name}' stance went {prior_stance} "
                    f"→ {curr_stance}, but stance is locked until chapter "
                    f"{locked_until}. Previous state in chapter "
                    f"{prior_snap.chapter_number if prior_snap else '?'}."
                )
            else:
                message = (
                    f"立场翻转被锁定：「{name}」自第"
                    f"{prior_snap.chapter_number if prior_snap else '?'}章后"
                    f"由 {prior_stance} 翻为 {curr_stance}，"
                    f"但立场锁定到第{locked_until}章。"
                )
            violations.append(
                ContradictionViolation(
                    check_type="character_stance_flip_locked",
                    severity="error",
                    message=message,
                    evidence=(
                        f"prior={prior_stance}; curr={curr_stance}; "
                        f"locked_until={locked_until}"
                    ),
                )
            )
            continue

        # Check for milestone event within the last 3 chapters
        recent_milestone = await session.scalar(
            select(func.count(RelationshipEventModel.id)).where(
                RelationshipEventModel.project_id == project_id,
                RelationshipEventModel.is_milestone.is_(True),
                or_(
                    RelationshipEventModel.character_a_label == name,
                    RelationshipEventModel.character_b_label == name,
                ),
                RelationshipEventModel.chapter_number >= chapter_number - 3,
                RelationshipEventModel.chapter_number <= chapter_number,
            )
        )
        if not recent_milestone:
            if _is_en:
                message = (
                    f"'{name}' stance flipped {prior_stance} → {curr_stance} "
                    "without a milestone event in the last 3 chapters."
                )
                recommendation = (
                    "Add a milestone relationship event (betrayal, alliance, "
                    "redemption) or revert the stance."
                )
            else:
                message = (
                    f"「{name}」立场由 {prior_stance} 翻为 {curr_stance}，"
                    "但最近 3 章没有里程碑级别的关系事件触发。"
                )
                recommendation = (
                    "请在本章补写背叛/结盟/救赎等关键事件，或恢复原立场。"
                )
            warnings.append(
                ContradictionWarning(
                    check_type="character_stance_flip_unjustified",
                    message=message,
                    recommendation=recommendation,
                )
            )

    return violations, warnings


async def _check_power_tier_regression(
    session: AsyncSession,
    project_id: UUID,
    chapter_number: int,
    scene_participants: list[str] | None,
    language: str | None = None,
) -> tuple[list[ContradictionViolation], list[ContradictionWarning]]:
    """Warn when power_tier drops below the historical peak without a reason.

    Peak is computed from snapshot history; regression is judged by position
    in ``invariants.power_system.tiers`` when provided, else by string inequality.
    """
    warnings: list[ContradictionWarning] = []
    if not scene_participants:
        return [], warnings

    project = await session.get(ProjectModel, project_id)
    tier_order: list[str] = []
    tier_aliases: dict[str, str] = {}
    if project is not None and project.invariants_json:
        power_sys = project.invariants_json.get("power_system")
        if isinstance(power_sys, dict):
            tiers = power_sys.get("tiers")
            if isinstance(tiers, list):
                tier_order = [str(t) for t in tiers if isinstance(t, (str, int))]
            aliases = power_sys.get("tier_aliases")
            if isinstance(aliases, dict):
                tier_aliases = {str(k): str(v) for k, v in aliases.items()}

    _is_en = is_english_language(language)

    def _canonical(tier: str) -> str:
        # Direct alias hit first; otherwise look for any canonical token
        # contained in the (often modifier-rich) tier label.
        text = str(tier).strip()
        if not text:
            return text
        if text in tier_aliases:
            return tier_aliases[text]
        for alias_key, canonical in tier_aliases.items():
            if alias_key and alias_key in text:
                return canonical
        for canonical in tier_order:
            if canonical and canonical in text:
                return canonical
        return text

    def _rank(tier: str | None) -> int:
        # No tier_order configured → refuse to compare; never hash.
        # Unknown tier → -1, also a no-compare signal (won't trigger
        # spurious regression vs. a known-tier peak).
        if not tier or not tier_order:
            return -1
        canonical = _canonical(tier)
        try:
            return tier_order.index(canonical)
        except ValueError:
            return -1

    for name in scene_participants:
        character = await session.scalar(
            select(CharacterModel).where(
                CharacterModel.project_id == project_id,
                CharacterModel.name == name,
            )
        )
        if character is None:
            continue

        # Prefer the snapshot AT this chapter (if one was recorded) over
        # the live ``character.power_tier`` field. The live value reflects
        # the present state and is misleading when scanning a historical
        # chapter where the character had a different tier — e.g. a
        # post-death "残存执念" current value compared against pre-death
        # peaks would always look like a regression.
        #
        # Within at-chapter snapshots prefer scene_number IS NULL: those
        # are post-write LLM extractions (services/feedback.py), which
        # reflect what was actually narrated. Scene-numbered snapshots
        # come from planning-time projections and lag the live taxonomy.
        curr_snap = await session.scalar(
            select(CharacterStateSnapshotModel)
            .where(
                CharacterStateSnapshotModel.project_id == project_id,
                CharacterStateSnapshotModel.character_id == character.id,
                CharacterStateSnapshotModel.chapter_number == chapter_number,
                CharacterStateSnapshotModel.power_tier.is_not(None),
            )
            .order_by(CharacterStateSnapshotModel.scene_number.asc().nulls_first())
            .limit(1)
        )
        if curr_snap is not None:
            curr = curr_snap.power_tier
        else:
            # No snapshot at this chapter. Falling back to the live
            # ``character.power_tier`` is only safe when the character
            # hasn't died before this chapter — otherwise the live value
            # is a *post-death* state ("残存执念", "已故", …) that has
            # no meaningful tier rank to compare against earlier peaks.
            death_ch = getattr(character, "death_chapter_number", None)
            if death_ch is not None and chapter_number <= death_ch:
                # Pre-death historical chapter without a per-chapter
                # snapshot — refuse to compare; live value is wrong proxy.
                continue
            curr = getattr(character, "power_tier", None)
        if not curr:
            continue

        prior_snaps = list(
            await session.scalars(
                select(CharacterStateSnapshotModel)
                .where(
                    CharacterStateSnapshotModel.project_id == project_id,
                    CharacterStateSnapshotModel.character_id == character.id,
                    CharacterStateSnapshotModel.chapter_number < chapter_number,
                    CharacterStateSnapshotModel.power_tier.is_not(None),
                )
                .order_by(CharacterStateSnapshotModel.chapter_number.desc())
                .limit(32)
            )
        )
        if not prior_snaps:
            continue

        peak_tier = max(
            (s.power_tier for s in prior_snaps if s.power_tier),
            key=_rank,
            default=None,
        )
        if peak_tier is None:
            continue

        curr_rank = _rank(curr)
        peak_rank = _rank(peak_tier)
        # Only warn when both endpoints are known canonical tiers AND the
        # gap is at least two tiers. A 1-tier drop is below the noise
        # floor: cross-taxonomy snapshots ("中阶" old-system vs "金丹期"
        # new-system) routinely differ by one position even when story
        # canon hasn't changed. Real plot regressions (sealing, severe
        # injury) drop by 2+ tiers and are the ones worth flagging.
        REGRESSION_MIN_GAP = 2
        if (
            curr_rank >= 0
            and peak_rank >= 0
            and (peak_rank - curr_rank) >= REGRESSION_MIN_GAP
        ):
            if _is_en:
                message = (
                    f"'{name}' power_tier regressed {peak_tier} → {curr} "
                    "without a downgrade reason in feedback."
                )
                recommendation = (
                    "Record an injury / sealing / artifact-loss event, or "
                    "restore the prior tier."
                )
            else:
                message = (
                    f"「{name}」力量等级由 {peak_tier} 下降为 {curr}，"
                    "feedback 未记录 power_tier_downgrade_reason。"
                )
                recommendation = "请在本章显式补写封印/重伤/道具失效等触发事件。"
            warnings.append(
                ContradictionWarning(
                    check_type="character_power_tier_regression",
                    message=message,
                    recommendation=recommendation,
                )
            )

    return [], warnings


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


async def run_pre_scene_contradiction_checks(
    session: AsyncSession,
    project_id: UUID,
    chapter_number: int,
    scene_number: int,
    scene_participants: list[str] | None = None,
    scene_information_release: str | None = None,
    settings: AppSettings | None = None,
    language: str | None = None,
    *,
    scene: Any = None,
) -> ContradictionCheckResult:
    """Run all pre-scene contradiction sub-checks and return an aggregate result.

    Each sub-check runs independently — a failure in one does not block others.
    The ``passed`` flag is ``True`` only when there are zero violations.
    """
    all_violations: list[ContradictionViolation] = []
    all_warnings: list[ContradictionWarning] = []
    checks_run = 0

    # 1. Character knowledge leaks
    try:
        v, w = await _check_character_knowledge_leaks(
            session,
            project_id,
            chapter_number,
            scene_participants,
            scene_information_release,
            language=language,
        )
        all_violations.extend(v)
        all_warnings.extend(w)
        checks_run += 1
    except Exception:
        logger.exception(
            "character_knowledge_leak 检查失败 (project=%s, ch=%d, sc=%d)",
            project_id,
            chapter_number,
            scene_number,
        )

    # 2. Stale clues
    try:
        stale = await _check_stale_clues(session, project_id, chapter_number, settings, language=language)
        all_warnings.extend(stale)
        checks_run += 1
    except Exception:
        logger.exception(
            "stale_clue 检查失败 (project=%s, ch=%d, sc=%d)",
            project_id,
            chapter_number,
            scene_number,
        )

    # 3. Dormant antagonist plans
    try:
        dormant = await _check_dormant_antagonist_plans(session, project_id, chapter_number, settings, language=language)
        all_warnings.extend(dormant)
        checks_run += 1
    except Exception:
        logger.exception(
            "dormant_antagonist 检查失败 (project=%s, ch=%d, sc=%d)",
            project_id,
            chapter_number,
            scene_number,
        )

    # 4. Dead-end arcs
    try:
        dead_ends = await _check_dead_end_arcs(session, project_id, chapter_number, settings, language=language)
        all_warnings.extend(dead_ends)
        checks_run += 1
    except Exception:
        logger.exception(
            "dead_end_arc 检查失败 (project=%s, ch=%d, sc=%d)",
            project_id,
            chapter_number,
            scene_number,
        )

    # 5. Timeline ordering
    try:
        timeline_violations = await _check_timeline_ordering(session, project_id, chapter_number, language=language)
        all_violations.extend(timeline_violations)
        checks_run += 1
    except Exception:
        logger.exception(
            "timeline_order 检查失败 (project=%s, ch=%d, sc=%d)",
            project_id,
            chapter_number,
            scene_number,
        )

    # 6. Scene state continuity
    try:
        continuity_v, continuity_w = await _check_scene_state_continuity(
            session, project_id, chapter_number, scene_number, language=language,
        )
        all_violations.extend(continuity_v)
        all_warnings.extend(continuity_w)
        checks_run += 1
    except Exception:
        logger.exception(
            "scene_state_continuity 检查失败 (project=%s, ch=%d, sc=%d)",
            project_id,
            chapter_number,
            scene_number,
        )

    # 7. Character resurrection (deceased)
    try:
        resv, resw = await _check_resurrection(
            session, project_id, chapter_number, scene_participants,
            language=language, scene=scene,
        )
        all_violations.extend(resv)
        all_warnings.extend(resw)
        checks_run += 1
    except Exception:
        logger.exception(
            "character_resurrection 检查失败 (project=%s, ch=%d, sc=%d)",
            project_id, chapter_number, scene_number,
        )

    # 7b. Other offstage states (missing / sealed / sleeping / comatose)
    try:
        offv, offw = await _check_offstage_state_appearances(
            session, project_id, chapter_number, scene_participants,
            language=language, scene=scene,
        )
        all_violations.extend(offv)
        all_warnings.extend(offw)
        checks_run += 1
    except Exception:
        logger.exception(
            "character_offstage_state 检查失败 (project=%s, ch=%d, sc=%d)",
            project_id, chapter_number, scene_number,
        )

    # 8. Stance flip justification
    try:
        stv, stw = await _check_stance_flip(
            session, project_id, chapter_number, scene_participants, language=language,
        )
        all_violations.extend(stv)
        all_warnings.extend(stw)
        checks_run += 1
    except Exception:
        logger.exception(
            "character_stance_flip 检查失败 (project=%s, ch=%d, sc=%d)",
            project_id, chapter_number, scene_number,
        )

    # 9. Power tier regression
    try:
        _pv, pw = await _check_power_tier_regression(
            session, project_id, chapter_number, scene_participants, language=language,
        )
        all_warnings.extend(pw)
        checks_run += 1
    except Exception:
        logger.exception(
            "character_power_tier_regression 检查失败 (project=%s, ch=%d, sc=%d)",
            project_id, chapter_number, scene_number,
        )

    passed = len(all_violations) == 0

    if all_violations:
        logger.warning(
            "pre-scene contradiction checks: %d violation(s) for project=%s ch=%d sc=%d",
            len(all_violations),
            project_id,
            chapter_number,
            scene_number,
        )
    if all_warnings:
        logger.info(
            "pre-scene contradiction checks: %d warning(s) for project=%s ch=%d sc=%d",
            len(all_warnings),
            project_id,
            chapter_number,
            scene_number,
        )

    return ContradictionCheckResult(
        passed=passed,
        violations=all_violations,
        warnings=all_warnings,
        checks_run=checks_run,
    )


# ---------------------------------------------------------------------------
# Post-write premature-death scan
# ---------------------------------------------------------------------------
#
# Why this exists
# ---------------
# ``_check_resurrection`` only fires when a character whose
# ``death_chapter_number`` is *less than* the current chapter shows up as a
# scene participant. It cannot catch the inverse failure mode: the writer
# LLM stages a death scene for a character whose ``death_chapter_number`` is
# scheduled *later* (e.g. the ch6 苏瑶 / 陆沉 incident — Su Yao's planned
# death is ch435 and Lu Chen's is ch458, but ch6 prose still wrote them
# dying). The pre-scene check passes because the participants are correctly
# alive; the violation is in the prose itself.
#
# This scanner runs over the assembled chapter markdown, looks at the
# ``protected roster`` (every character with ``death_chapter_number >
# current_chapter``) and reports any prose passage that puts one of those
# names within a small window of a death verb. The regex catalogue is
# deliberately conservative — it only flags strong signals like
# "X 倒下了/断气/殒命/咽下最后一口气/X 死前/X 永远闭上了眼" so it does not
# misfire on metaphors ("他像死人一样睡着了") or on someone else dying nearby.

# Death keywords. Two tiers:
#   _STRONG: unambiguous death verbs / phrasings — direct hit produces a
#       blocking violation.
#   _IMPLIED: phrasings that frame the protected character as already dead
#       even without a death verb in the same sentence ("X 死前", "X 临终").
_PREMATURE_DEATH_STRONG_ZH: tuple[str, ...] = (
    "死了", "死亡", "已死", "毙命", "咽气", "咽下最后一口气",
    "断气", "气绝", "殒命", "身亡", "罹难", "魂飞", "魂断",
    "永远闭上了眼", "缓缓倒下", "倒下不起", "倒在血泊",
    "再也站不起来", "再也没醒来", "停止了呼吸", "心跳停止",
    "化作齑粉", "形神俱灭", "灰飞烟灭", "丧命", "命陨", "命丧",
)
_PREMATURE_DEATH_IMPLIED_ZH: tuple[str, ...] = (
    "死前", "临终前", "弥留之际", "遗体", "尸首", "之死",
    "葬礼", "送葬", "祭奠",
)
_PREMATURE_DEATH_STRONG_EN: tuple[str, ...] = (
    " died", " is dead", " was dead", " killed",
    " passed away", " breathed his last", " breathed her last",
    " breathed their last", " drew his last breath", " drew her last breath",
    " stopped breathing", " heart stopped", " collapsed and never rose",
    " never woke again",
)
_PREMATURE_DEATH_IMPLIED_EN: tuple[str, ...] = (
    "before he died", "before she died", "before they died",
    "his death", "her death", "their death",
    "funeral", "corpse", "remains of",
)

# Window (in characters) inside which a name+death-keyword pair is treated
# as "the character is being killed" rather than coincidence. CJK prose is
# dense, so we use a tight window. English prose is looser.
_PREMATURE_WINDOW_ZH: int = 30
_PREMATURE_WINDOW_EN: int = 80


def _scan_premature_death_in_text(
    *,
    chapter_md: str,
    protected_names: list[str],
    language: str | None,
    other_names: list[str] | None = None,
) -> list[tuple[str, str, str]]:
    """Return tuples of ``(character_name, kind, evidence)`` for protected
    characters whose name is the *closest* one to a death keyword in the
    surrounding window.

    The "closest name to the death verb" heuristic is what keeps the
    scanner from misfiring on co-located names — a sentence like
    "苏瑶冷冷一笑，叶长青已死" mentions 苏瑶 nearby but the death
    actually attaches to 叶长青. Old position-of-name scans would flag
    苏瑶 here; the proximity attribution does not.

    Parameters
    ----------
    chapter_md : str
        The assembled chapter markdown.
    protected_names : list[str]
        Characters whose ``death_chapter_number`` is later than this
        chapter. A death keyword closest to one of these names produces a
        finding.
    other_names : list[str] | None
        All other character names known to the project. They are used
        only as "distractor" candidates to win the proximity contest —
        they do not produce findings of their own. ``None`` falls back
        to a tiny built-in pronoun list, which still catches the
        most common false positives.
    language : str | None
        Language hint; when English, both name needles and prose are
        casefolded for matching.

    ``kind`` is either ``"strong"`` (direct death verb) or ``"implied"``
    (post-mortem framing). ``evidence`` is a ~80-char window. Pure
    function — no DB, deterministic, easy to unit-test.
    """

    if not chapter_md or not protected_names:
        return []

    is_en = is_english_language(language)
    strong = _PREMATURE_DEATH_STRONG_EN if is_en else _PREMATURE_DEATH_STRONG_ZH
    implied = _PREMATURE_DEATH_IMPLIED_EN if is_en else _PREMATURE_DEATH_IMPLIED_ZH
    window = _PREMATURE_WINDOW_EN if is_en else _PREMATURE_WINDOW_ZH

    haystack = chapter_md if not is_en else chapter_md.casefold()

    # Build the candidate name set (protected + other) so the proximity
    # contest can correctly attribute the death to whichever name is
    # closest to the keyword.
    protected_clean: list[str] = [
        n.strip() for n in protected_names if isinstance(n, str) and n.strip()
    ]
    other_clean: list[str] = [
        n.strip() for n in (other_names or []) if isinstance(n, str) and n.strip()
    ]
    # Distractors: at minimum, the bare pronouns. They catch the most
    # common false-positive shape — protected name in clause 1, an
    # unnamed third party dies in clause 2.
    if not other_clean:
        other_clean = (
            ["he", "she", "they", "him", "her", "them"]
            if is_en
            else ["他", "她", "它"]
        )

    protected_set = {n.casefold() if is_en else n for n in protected_clean}
    all_names = list(set(protected_clean) | set(other_clean))
    name_needles: list[tuple[str, str]] = []  # (canonical, lookup)
    for raw in all_names:
        if not raw:
            continue
        name_needles.append((raw, raw.casefold() if is_en else raw))

    def _protected_death_window_is_exempt(evidence: str) -> bool:
        """Exempt only explicit non-present-tense frames for protected deaths.

        Generic memory words such as "记得" are not enough here: "他记得陆沉死前"
        is still a premature post-mortem leak. Stronger frames like dreams,
        visions, flash-forwards, and funeral recollection are allowed.
        """

        if not evidence:
            return False
        if is_en:
            lowered = evidence.casefold()
            return any(
                marker in lowered
                for marker in (
                    "in a dream",
                    "in a vision",
                    "years later",
                    "in the future",
                    "at the funeral",
                    "the funeral that day",
                )
            )
        return any(
            marker in evidence
            for marker in (
                "脑海中浮现",
                "脑海里浮现",
                "梦中",
                "梦里",
                "幻象",
                "幻视",
                "预见",
                "预知",
                "多年以后",
                "未来",
                "葬礼那天",
            )
        )

    def _all_name_positions() -> list[tuple[int, str]]:
        """All ``(position, canonical_name)`` pairs in the haystack."""
        positions: list[tuple[int, str]] = []
        for canonical, needle in name_needles:
            cursor = 0
            while True:
                hit = haystack.find(needle, cursor)
                if hit < 0:
                    break
                positions.append((hit, canonical))
                cursor = hit + max(1, len(needle))
        positions.sort(key=lambda x: x[0])
        return positions

    name_positions = _all_name_positions()

    def _closest_name(idx: int) -> str | None:
        """Find the character name whose nearest occurrence is closest to
        the death-keyword position ``idx``, within the proximity window.
        Returns the canonical name or ``None`` when no name is in range.
        """
        best: tuple[int, str] | None = None
        for pos, canonical in name_positions:
            if abs(pos - idx) > window:
                continue
            distance = abs(pos - idx)
            if best is None or distance < best[0]:
                best = (distance, canonical)
        return best[1] if best else None

    findings: list[tuple[str, str, str]] = []
    seen_keys: set[tuple[str, str]] = set()

    def _record(name_canonical: str, kind: str, kw_idx: int) -> None:
        # Check protected membership using the canonical (case-preserved)
        # form when CJK, casefolded when EN.
        compare_form = name_canonical.casefold() if is_en else name_canonical
        if compare_form not in protected_set:
            return
        key = (name_canonical, kind)
        if key in seen_keys:
            return
        seen_keys.add(key)
        start = max(0, kw_idx - 30)
        end = min(len(chapter_md), kw_idx + 60)
        evidence = chapter_md[start:end].replace("\n", " ")
        if _protected_death_window_is_exempt(evidence):
            return
        findings.append((name_canonical, kind, evidence))

    def _scan_keywords(keywords: tuple[str, ...], kind: str) -> None:
        for kw in keywords:
            cursor = 0
            while True:
                hit = haystack.find(kw, cursor)
                if hit < 0:
                    break
                cursor = hit + max(1, len(kw))
                attributed = _closest_name(hit)
                if attributed is None:
                    continue
                _record(attributed, kind, hit)

    # NOTE: no flashback exemption here.
    # The premature-death scanner targets *protected* characters whose
    # death is scheduled for a LATER chapter — there is no legitimate
    # retrospective frame for a death that hasn't happened yet, so any
    # death-flavoured language around a protected name is a real leak.
    # The flashback / memorial / vision exemption applies only to
    # already-deceased characters (handled in ``_check_resurrection``
    # and ``_filter_dead_scene_participants``).
    #
    # Order matters: strong wins over implied for the same name, because
    # ``_record`` short-circuits on (name, kind). We record strong first.
    _scan_keywords(strong, "strong")
    _scan_keywords(implied, "implied")

    return findings


async def check_premature_death_in_prose(
    session: AsyncSession,
    project_id: UUID,
    chapter_number: int,
    chapter_md: str,
    *,
    language: str | None = None,
) -> tuple[list[ContradictionViolation], list[ContradictionWarning]]:
    """Scan an assembled chapter for death descriptions of protected
    characters (``death_chapter_number`` strictly greater than the current
    chapter).

    Returns ``(violations, warnings)``. ``"strong"`` matches are violations
    (block the chapter), ``"implied"`` matches are warnings (the prose
    treats the character as already dead in passing — possibly a flashback,
    possibly a leak; reviewer decides).
    """

    violations: list[ContradictionViolation] = []
    warnings: list[ContradictionWarning] = []

    if not chapter_md:
        return violations, warnings

    rows = await session.execute(
        select(CharacterModel.name, CharacterModel.death_chapter_number).where(
            CharacterModel.project_id == project_id,
            CharacterModel.death_chapter_number.is_not(None),
            CharacterModel.death_chapter_number > chapter_number,
        )
    )
    protected: list[tuple[str, int]] = [
        (str(name).strip(), int(death_ch))
        for name, death_ch in rows
        if name and death_ch is not None
    ]
    if not protected:
        return violations, warnings

    # Load every other character name on the project so the proximity
    # attribution can correctly distinguish "protected died" from
    # "another character died nearby".
    protected_name_set = {name for name, _ in protected}
    other_rows = await session.scalars(
        select(CharacterModel.name).where(
            CharacterModel.project_id == project_id,
            CharacterModel.name.notin_(protected_name_set),
        )
    )
    other_names: list[str] = [
        str(name).strip() for name in other_rows if name and str(name).strip()
    ]

    is_en = is_english_language(language)
    findings = _scan_premature_death_in_text(
        chapter_md=chapter_md,
        protected_names=[name for name, _ in protected],
        other_names=other_names,
        language=language,
    )
    if not findings:
        return violations, warnings

    death_ch_by_name = {name: death_ch for name, death_ch in protected}
    for name, kind, evidence in findings:
        scheduled = death_ch_by_name.get(name)
        if kind == "strong":
            if is_en:
                msg = (
                    f"Premature death: '{name}' is described as dying / dead in "
                    f"chapter {chapter_number}, but the planner schedules the "
                    f"death for chapter {scheduled}. Rewrite the passage so the "
                    f"character survives this chapter."
                )
            else:
                msg = (
                    f"提前死亡：「{name}」在第{chapter_number}章的正文里出现"
                    f"死亡/濒死描写，但其计划死亡章节为第{scheduled}章。"
                    "请改写本章相关段落，让该角色在本章存活。"
                )
            violations.append(
                ContradictionViolation(
                    check_type="character_premature_death",
                    severity="error",
                    message=msg,
                    evidence=f"…{evidence}…",
                )
            )
        else:  # implied
            if is_en:
                msg = (
                    f"Possible premature death: prose around '{name}' uses "
                    f"post-mortem framing in chapter {chapter_number}, but the "
                    f"planned death is chapter {scheduled}. Verify this is a "
                    f"flashback or remove the framing."
                )
                rec = (
                    "If this is a flashback, label the scene explicitly; "
                    "otherwise rephrase to avoid 'before X died' / 'X's death' "
                    "until chapter " + str(scheduled) + "."
                )
            else:
                msg = (
                    f"疑似提前死亡：「{name}」在第{chapter_number}章周围出现"
                    f"事后追述（如「X死前」「临终」「遗体」），"
                    f"但其计划死亡章节为第{scheduled}章。"
                    "请确认这是回忆/前瞻还是误写。"
                )
                rec = (
                    f"若为回忆/前瞻，请显式标记；否则改写表述，"
                    f"避免在第{scheduled}章之前出现「{name}死前/临终/遗体」"
                    "等已死去的修辞。"
                )
            warnings.append(
                ContradictionWarning(
                    check_type="character_premature_death_implied",
                    message=msg,
                    recommendation=rec,
                )
            )

    if violations:
        logger.warning(
            "premature-death scan: %d violation(s) for project=%s ch=%d",
            len(violations), project_id, chapter_number,
        )
    return violations, warnings
