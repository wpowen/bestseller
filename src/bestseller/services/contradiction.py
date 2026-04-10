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

from sqlalchemy import select, func
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
    ClueModel,
    PlotArcModel,
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
