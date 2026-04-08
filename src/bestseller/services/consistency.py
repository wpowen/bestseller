from __future__ import annotations

import re
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.domain.project_review import (
    ProjectConsistencyFinding,
    ProjectConsistencyResult,
    ProjectConsistencyScores,
)
from bestseller.infra.db.models import (
    AntagonistPlanModel,
    CanonFactModel,
    ChapterContractModel,
    ChapterDraftVersionModel,
    ChapterModel,
    CharacterModel,
    CharacterStateSnapshotModel,
    ClueModel,
    EmotionTrackModel,
    ExportArtifactModel,
    PayoffModel,
    PlotArcModel,
    ProjectModel,
    QualityScoreModel,
    ReviewReportModel,
    RewriteTaskModel,
    SceneCardModel,
    SubplotScheduleModel,
    TimelineEventModel,
    VolumeModel,
    WorldRuleModel,
)
from bestseller.services.llm import LLMCompletionRequest, complete_text
from bestseller.services.projects import get_project_by_slug
from bestseller.settings import AppSettings


def _clamp_score(value: float) -> float:
    return round(max(0.0, min(1.0, value)), 2)


def _severity_from_score(score: float) -> str:
    if score < 0.45:
        return "high"
    if score < 0.7:
        return "medium"
    return "low"


def _max_severity(findings: list[ProjectConsistencyFinding]) -> str:
    if any(finding.severity == "high" for finding in findings):
        return "high"
    if any(finding.severity == "medium" for finding in findings):
        return "medium"
    return "low"


def _safe_ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 1.0
    return _clamp_score(numerator / denominator)


def _term_candidates(*values: str | None) -> list[str]:
    terms: list[str] = []
    for value in values:
        if not value:
            continue
        for token in re.findall(r"[0-9A-Za-z\u4e00-\u9fff]{2,}", value):
            if token not in terms:
                terms.append(token)
    return terms


def _collect_narrative_review_signals(
    *,
    chapter_count: int,
    max_chapter_number: int,
    chapter_contracts: list[ChapterContractModel],
    plot_arcs: list[PlotArcModel],
    clues: list[ClueModel],
    payoffs: list[PayoffModel],
    emotion_tracks: list[EmotionTrackModel],
    antagonist_plans: list[AntagonistPlanModel],
    world_rules: list[WorldRuleModel],
    protagonist_snapshots: list[CharacterStateSnapshotModel],
    chapter_drafts: list[ChapterDraftVersionModel],
    protagonist_count: int,
    antagonist_count: int,
) -> dict[str, object]:
    main_arc_codes = {
        arc.arc_code
        for arc in plot_arcs
        if arc.arc_type in {"main_plot", "growth", "faction", "romance", "mystery"}
    } or {"main_plot"}
    main_plot_chapter_count = 0
    main_plot_progression = 1.0
    if plot_arcs and chapter_contracts:
        main_plot_chapter_count = len(
            {
                contract.chapter_number
                for contract in chapter_contracts
                if set(contract.primary_arc_codes + contract.supporting_arc_codes) & main_arc_codes
            }
        )
        main_plot_progression = _safe_ratio(main_plot_chapter_count, chapter_count)

    overdue_clue_count = len(
        [
            clue
            for clue in clues
            if clue.expected_payoff_by_chapter_number is not None
            and clue.expected_payoff_by_chapter_number <= max_chapter_number
            and clue.actual_paid_off_chapter_number is None
        ]
    )
    mystery_balance = 1.0
    if clues or payoffs:
        payoff_coverage = _safe_ratio(len(payoffs), max(len(clues), 1))
        overdue_penalty = 1.0 - min(overdue_clue_count / max(len(clues), 1), 1.0)
        mystery_balance = _clamp_score((payoff_coverage + overdue_penalty) / 2)

    stale_emotion_track_count = len(
        [
            track
            for track in emotion_tracks
            if track.last_shift_chapter_number is not None
            and max_chapter_number - track.last_shift_chapter_number >= 3
        ]
    )
    emotional_continuity = 1.0
    if emotion_tracks:
        freshness_scores: list[float] = []
        horizon = max(max_chapter_number, 1)
        for track in emotion_tracks:
            if track.last_shift_chapter_number is None:
                freshness_scores.append(0.6)
                continue
            staleness = max(0, max_chapter_number - track.last_shift_chapter_number)
            freshness_scores.append(max(0.0, 1 - (staleness / max(horizon, 3))))
        emotional_continuity = _clamp_score(sum(freshness_scores) / len(freshness_scores))

    distinct_arc_states = {
        snapshot.arc_state.strip()
        for snapshot in protagonist_snapshots
        if snapshot.arc_state and snapshot.arc_state.strip()
    }
    protagonist_arc_step_count = len(distinct_arc_states)
    protagonist_snapshot_chapter_count = len({snapshot.chapter_number for snapshot in protagonist_snapshots})
    character_arc_progression = 1.0
    if protagonist_count > 0 and chapter_count > 1:
        coverage_score = _safe_ratio(protagonist_snapshot_chapter_count, chapter_count)
        step_target = min(max(chapter_count, 2), 4)
        step_score = _safe_ratio(protagonist_arc_step_count, step_target)
        character_arc_progression = _clamp_score((coverage_score + step_score) / 2)

    grounded_world_rule_count = 0
    combined_draft_text = "\n".join(draft.content_md for draft in chapter_drafts)
    for rule in world_rules:
        candidates = _term_candidates(rule.rule_code, rule.name, rule.story_consequence, rule.description)[:6]
        if any(term and term in combined_draft_text for term in candidates):
            grounded_world_rule_count += 1
    world_rule_consistency = 1.0
    if world_rules:
        world_rule_consistency = _clamp_score((1 + _safe_ratio(grounded_world_rule_count, len(world_rules))) / 2)

    active_antagonist_plan_count = len(
        [
            plan
            for plan in antagonist_plans
            if plan.status in {"active", "planned"}
            and (plan.target_chapter_number is None or plan.target_chapter_number >= max_chapter_number)
        ]
    )
    antagonist_pressure = 1.0
    if antagonist_count > 0:
        if not antagonist_plans:
            antagonist_pressure = 0.2
        else:
            plan_coverage = min(1.0, len(antagonist_plans) / max(1, min(chapter_count, 4)))
            live_pressure = 1.0 if active_antagonist_plan_count > 0 else 0.45
            antagonist_pressure = _clamp_score((plan_coverage + live_pressure) / 2)

    return {
        "main_plot_progression": main_plot_progression,
        "main_plot_chapter_count": main_plot_chapter_count,
        "mystery_balance": mystery_balance,
        "overdue_clue_count": overdue_clue_count,
        "clue_count": len(clues),
        "payoff_count": len(payoffs),
        "emotional_continuity": emotional_continuity,
        "emotion_track_count": len(emotion_tracks),
        "stale_emotion_track_count": stale_emotion_track_count,
        "character_arc_progression": character_arc_progression,
        "protagonist_arc_step_count": protagonist_arc_step_count,
        "protagonist_snapshot_chapter_count": protagonist_snapshot_chapter_count,
        "world_rule_consistency": world_rule_consistency,
        "world_rule_count": len(world_rules),
        "grounded_world_rule_count": grounded_world_rule_count,
        "antagonist_pressure": antagonist_pressure,
        "antagonist_count": antagonist_count,
        "antagonist_plan_count": len(antagonist_plans),
        "active_antagonist_plan_count": active_antagonist_plan_count,
    }


def render_project_review_summary(review_result: ProjectConsistencyResult) -> str:
    lines = [
        f"结论：{review_result.verdict}",
        f"总分：{review_result.scores.overall}",
        f"最高严重级别：{review_result.severity_max}",
    ]
    if review_result.findings:
        lines.append("发现的问题：")
        lines.extend(
            f"- [{finding.category}/{finding.severity}] {finding.message}"
            for finding in review_result.findings
        )
    if review_result.recommended_actions:
        lines.append("建议动作：")
        lines.extend(f"- {item}" for item in review_result.recommended_actions)
    return "\n".join(lines)


def build_project_review_prompts(
    project: ProjectModel,
    review_result: ProjectConsistencyResult,
) -> tuple[str, str]:
    system_prompt = (
        "你是长篇小说项目级一致性审校器。"
        "请用中文输出简洁的项目审校结论，强调当前风险和优先动作。"
    )
    user_prompt = (
        f"项目：《{project.title}》\n"
        f"项目状态：{project.status}\n"
        f"一致性评分：{review_result.scores.model_dump(mode='json')}\n"
        f"当前发现：{[finding.model_dump(mode='json') for finding in review_result.findings]}\n"
        f"证据：{review_result.evidence_summary}\n"
        "请给出一段项目级审校结论和下一步建议。"
    )
    return system_prompt, user_prompt


def evaluate_project_consistency(
    *,
    settings: AppSettings,
    chapter_count: int,
    chapter_draft_count: int,
    complete_chapter_count: int,
    scene_count: int,
    approved_scene_count: int,
    scene_summary_count: int,
    timeline_event_count: int,
    pending_rewrite_count: int,
    project_export_count: int,
    chapter_export_count: int,
    expect_project_export: bool = True,
    main_plot_progression: float | None = None,
    main_plot_chapter_count: int = 0,
    mystery_balance: float | None = None,
    overdue_clue_count: int = 0,
    clue_count: int = 0,
    payoff_count: int = 0,
    emotional_continuity: float | None = None,
    emotion_track_count: int = 0,
    stale_emotion_track_count: int = 0,
    character_arc_progression: float | None = None,
    protagonist_arc_step_count: int = 0,
    protagonist_snapshot_chapter_count: int = 0,
    world_rule_consistency: float | None = None,
    world_rule_count: int = 0,
    grounded_world_rule_count: int = 0,
    antagonist_pressure: float | None = None,
    antagonist_count: int = 0,
    antagonist_plan_count: int = 0,
    active_antagonist_plan_count: int = 0,
    supporting_character_count: int = 0,
    supporting_with_arc_count: int = 0,
    supporting_with_voice_count: int = 0,
    dormant_subplot_count: int = 0,
    total_subplot_count: int = 0,
    open_arc_count: int = 0,
    open_clue_count: int = 0,
    is_final_volume: bool = False,
) -> ProjectConsistencyResult:
    safe_chapter_count = max(chapter_count, 1)
    safe_scene_count = max(scene_count, 1)

    chapter_coverage = _clamp_score(chapter_draft_count / safe_chapter_count)
    scene_knowledge = _clamp_score(approved_scene_count / safe_scene_count)
    canon_coverage = _clamp_score(scene_summary_count / safe_scene_count)
    timeline_coverage = _clamp_score(timeline_event_count / safe_scene_count)
    revision_pressure = _clamp_score(1 - (pending_rewrite_count / safe_scene_count))
    export_readiness = 1.0
    if expect_project_export:
        export_readiness = _clamp_score(
            1.0
            if project_export_count > 0
            else (0.7 if chapter_export_count >= chapter_count else 0.0)
        )
    main_plot_progression = 1.0 if main_plot_progression is None else _clamp_score(main_plot_progression)
    mystery_balance = 1.0 if mystery_balance is None else _clamp_score(mystery_balance)
    emotional_continuity = 1.0 if emotional_continuity is None else _clamp_score(emotional_continuity)
    character_arc_progression = (
        1.0 if character_arc_progression is None else _clamp_score(character_arc_progression)
    )
    world_rule_consistency = (
        1.0 if world_rule_consistency is None else _clamp_score(world_rule_consistency)
    )
    antagonist_pressure = 1.0 if antagonist_pressure is None else _clamp_score(antagonist_pressure)

    # Supporting cast depth: do supporting characters have arc trajectories + voice profiles?
    supporting_cast_depth = 1.0
    if supporting_character_count > 0:
        arc_ratio = _safe_ratio(supporting_with_arc_count, supporting_character_count)
        voice_ratio = _safe_ratio(supporting_with_voice_count, supporting_character_count)
        supporting_cast_depth = _clamp_score((arc_ratio + voice_ratio) / 2)

    # Subplot health: are subplots alive or have any gone dormant too long?
    subplot_health = 1.0
    if total_subplot_count > 0:
        subplot_health = _clamp_score(1.0 - (dormant_subplot_count / total_subplot_count))

    # Resolution completeness: for final volumes, check that arcs and clues are closing
    resolution_completeness = 1.0
    if is_final_volume and (open_arc_count > 0 or open_clue_count > 0):
        total_open = open_arc_count + open_clue_count
        resolution_completeness = _clamp_score(max(0.0, 1.0 - (total_open * 0.15)))

    _score_parts = [
        chapter_coverage,
        scene_knowledge,
        canon_coverage,
        timeline_coverage,
        revision_pressure,
        export_readiness,
        main_plot_progression,
        mystery_balance,
        emotional_continuity,
        character_arc_progression,
        world_rule_consistency,
        antagonist_pressure,
        supporting_cast_depth,
        subplot_health,
        resolution_completeness,
    ]
    overall = _clamp_score(sum(_score_parts) / len(_score_parts))

    findings: list[ProjectConsistencyFinding] = []
    if chapter_draft_count < chapter_count:
        score = chapter_coverage
        findings.append(
            ProjectConsistencyFinding(
                category="chapter_coverage",
                severity=_severity_from_score(score),
                message=f"当前只有 {chapter_draft_count}/{chapter_count} 个章节完成组装草稿。",
            )
        )
    if complete_chapter_count < chapter_count:
        score = _clamp_score(complete_chapter_count / safe_chapter_count)
        findings.append(
            ProjectConsistencyFinding(
                category="chapter_status",
                severity=_severity_from_score(score),
                message=f"当前只有 {complete_chapter_count}/{chapter_count} 个章节进入 complete 状态。",
            )
        )
    if scene_summary_count < scene_count:
        findings.append(
            ProjectConsistencyFinding(
                category="canon_coverage",
                severity=_severity_from_score(canon_coverage),
                message=f"知识层当前只覆盖 {scene_summary_count}/{scene_count} 个场景摘要。",
            )
        )
    if timeline_event_count < scene_count:
        findings.append(
            ProjectConsistencyFinding(
                category="timeline_coverage",
                severity=_severity_from_score(timeline_coverage),
                message=f"时间线当前只覆盖 {timeline_event_count}/{scene_count} 个场景事件。",
            )
        )
    if pending_rewrite_count > 0:
        findings.append(
            ProjectConsistencyFinding(
                category="revision_pressure",
                severity=_severity_from_score(revision_pressure),
                message=f"仍有 {pending_rewrite_count} 个待处理 rewrite 任务。",
            )
        )
    if expect_project_export and project_export_count == 0:
        findings.append(
            ProjectConsistencyFinding(
                category="export_readiness",
                severity=_severity_from_score(export_readiness),
                message="项目级导出尚未生成，当前整书交付物不完整。",
            )
        )
    if main_plot_progression < 0.75:
        findings.append(
            ProjectConsistencyFinding(
                category="main_plot_progression",
                severity=_severity_from_score(main_plot_progression),
                message=f"主线只在 {main_plot_chapter_count}/{chapter_count} 个章节中被显式承担，推进密度偏低。",
            )
        )
    if clue_count > 0 and mystery_balance < 0.75:
        findings.append(
            ProjectConsistencyFinding(
                category="mystery_balance",
                severity=_severity_from_score(mystery_balance),
                message=(
                    f"暗线当前有 {clue_count} 个伏笔、{payoff_count} 个兑现，"
                    f"其中 {overdue_clue_count} 个伏笔已经超期未回收。"
                ),
            )
        )
    if emotion_track_count > 0 and emotional_continuity < 0.75:
        findings.append(
            ProjectConsistencyFinding(
                category="emotion_continuity",
                severity=_severity_from_score(emotional_continuity),
                message=(
                    f"当前有 {emotion_track_count} 条关系/情绪线，其中 {stale_emotion_track_count} 条已经连续多章没有变化。"
                ),
            )
        )
    if chapter_count >= 3 and character_arc_progression < 0.75:
        findings.append(
            ProjectConsistencyFinding(
                category="character_arc_progression",
                severity=_severity_from_score(character_arc_progression),
                message=(
                    f"主角弧光当前只有 {protagonist_arc_step_count} 个明显台阶，"
                    f"角色状态快照仅覆盖 {protagonist_snapshot_chapter_count}/{chapter_count} 个章节。"
                ),
            )
        )
    if world_rule_count > 0 and world_rule_consistency < 0.75:
        findings.append(
            ProjectConsistencyFinding(
                category="world_rule_consistency",
                severity=_severity_from_score(world_rule_consistency),
                message=(
                    f"已定义 {world_rule_count} 条世界规则，但只有 {grounded_world_rule_count} 条在当前章节草稿中获得明确落地。"
                ),
            )
        )
    if antagonist_count > 0 and antagonist_pressure < 0.75:
        findings.append(
            ProjectConsistencyFinding(
                category="antagonist_pressure",
                severity=_severity_from_score(antagonist_pressure),
                message=(
                    f"项目存在 {antagonist_count} 名反派角色，但当前只有 {antagonist_plan_count} 条反派计划，"
                    f"其中仍在当前叙事地平线内生效的只有 {active_antagonist_plan_count} 条。"
                ),
            )
        )
    if supporting_character_count > 0 and supporting_cast_depth < 0.75:
        findings.append(
            ProjectConsistencyFinding(
                category="supporting_cast_depth",
                severity=_severity_from_score(supporting_cast_depth),
                message=(
                    f"项目有 {supporting_character_count} 个配角，"
                    f"但只有 {supporting_with_arc_count} 个有弧线轨迹、"
                    f"{supporting_with_voice_count} 个有语言指纹。配角丰满度不足。"
                ),
            )
        )
    if total_subplot_count > 0 and subplot_health < 0.75:
        findings.append(
            ProjectConsistencyFinding(
                category="subplot_health",
                severity=_severity_from_score(subplot_health),
                message=(
                    f"项目有 {total_subplot_count} 条副线，但 {dormant_subplot_count} 条已沉默超过 5 章。"
                ),
            )
        )
    if is_final_volume and resolution_completeness < 0.75:
        findings.append(
            ProjectConsistencyFinding(
                category="resolution_completeness",
                severity=_severity_from_score(resolution_completeness),
                message=(
                    f"最终卷仍有 {open_arc_count} 条未关闭弧线和 {open_clue_count} 条未兑现伏笔，"
                    f"烂尾风险高。"
                ),
            )
        )

    threshold = settings.quality.thresholds.chapter_coherence_min_score
    verdict = "pass" if overall >= threshold and not findings else "attention"
    recommended_actions: list[str] = []
    if chapter_draft_count < chapter_count:
        recommended_actions.append("先补齐缺失章节的 draft/assemble 流程。")
    if scene_summary_count < scene_count or timeline_event_count < scene_count:
        recommended_actions.append("重新运行章节或项目 pipeline，补齐知识层抽取。")
    if pending_rewrite_count > 0:
        recommended_actions.append("优先处理仍处于 pending/queued 的 rewrite 任务。")
    if expect_project_export and project_export_count == 0:
        recommended_actions.append("生成最新的项目级导出文件，保证可交付版本存在。")
    if main_plot_progression < 0.75:
        recommended_actions.append("提高 chapter contract 中主线 arc 的覆盖率，避免连续章节失去主线推进。")
    if clue_count > 0 and mystery_balance < 0.75:
        recommended_actions.append("补齐伏笔兑现计划，优先处理已经超期未回收的暗线节点。")
    if emotion_track_count > 0 and emotional_continuity < 0.75:
        recommended_actions.append("为核心关系线补一轮明确的推进、拉扯或兑现，避免情绪线断档。")
    if chapter_count >= 3 and character_arc_progression < 0.75:
        recommended_actions.append("补写主角弧光台阶，确保关键章节出现新的 arc_state 变化。")
    if world_rule_count > 0 and world_rule_consistency < 0.75:
        recommended_actions.append("把关键世界规则显式落到正文事件与代价里，避免世界观停留在设定层。")
    if antagonist_count > 0 and antagonist_pressure < 0.75:
        recommended_actions.append("补强反派计划与升级节点，确保主角不是单向推进。")
    if supporting_character_count > 0 and supporting_cast_depth < 0.75:
        recommended_actions.append("为配角补充弧线轨迹和语言指纹(voice_profile)，确保每个命名配角有辨识度。")
    if total_subplot_count > 0 and subplot_health < 0.75:
        recommended_actions.append("激活沉默副线，在接下来的章节中给予推进或明确收束。")
    if is_final_volume and resolution_completeness < 0.75:
        recommended_actions.append("优先关闭开放弧线和兑现伏笔，避免烂尾。规划收尾章节的 EndingContract。")
    if not recommended_actions:
        recommended_actions.append("当前项目一致性通过，可继续扩大章节规模或切换到真实模型。")

    return ProjectConsistencyResult(
        verdict=verdict,
        severity_max=_max_severity(findings),
        scores=ProjectConsistencyScores(
            overall=overall,
            chapter_coverage=chapter_coverage,
            scene_knowledge=scene_knowledge,
            canon_coverage=canon_coverage,
            timeline_coverage=timeline_coverage,
            revision_pressure=revision_pressure,
            export_readiness=export_readiness,
            main_plot_progression=main_plot_progression,
            mystery_balance=mystery_balance,
            emotional_continuity=emotional_continuity,
            character_arc_progression=character_arc_progression,
            world_rule_consistency=world_rule_consistency,
            antagonist_pressure=antagonist_pressure,
            supporting_cast_depth=supporting_cast_depth,
            subplot_health=subplot_health,
            resolution_completeness=resolution_completeness,
        ),
        findings=findings,
        evidence_summary={
            "chapter_count": chapter_count,
            "chapter_draft_count": chapter_draft_count,
            "complete_chapter_count": complete_chapter_count,
            "scene_count": scene_count,
            "approved_scene_count": approved_scene_count,
            "scene_summary_count": scene_summary_count,
            "timeline_event_count": timeline_event_count,
            "pending_rewrite_count": pending_rewrite_count,
            "project_export_count": project_export_count,
            "chapter_export_count": chapter_export_count,
            "main_plot_chapter_count": main_plot_chapter_count,
            "clue_count": clue_count,
            "payoff_count": payoff_count,
            "overdue_clue_count": overdue_clue_count,
            "emotion_track_count": emotion_track_count,
            "stale_emotion_track_count": stale_emotion_track_count,
            "protagonist_arc_step_count": protagonist_arc_step_count,
            "protagonist_snapshot_chapter_count": protagonist_snapshot_chapter_count,
            "world_rule_count": world_rule_count,
            "grounded_world_rule_count": grounded_world_rule_count,
            "antagonist_count": antagonist_count,
            "antagonist_plan_count": antagonist_plan_count,
            "active_antagonist_plan_count": active_antagonist_plan_count,
        },
        recommended_actions=recommended_actions,
    )


async def review_project_consistency(
    session: AsyncSession,
    settings: AppSettings,
    project_slug: str,
    *,
    workflow_run_id: UUID | None = None,
    step_run_id: UUID | None = None,
    expect_project_export: bool = True,
) -> tuple[ProjectConsistencyResult, ReviewReportModel, QualityScoreModel]:
    project = await get_project_by_slug(session, project_slug)
    if project is None:
        raise ValueError(f"Project '{project_slug}' was not found.")

    chapter_count = int(
        await session.scalar(
            select(func.count()).select_from(ChapterModel).where(ChapterModel.project_id == project.id)
        )
        or 0
    )
    chapter_draft_count = int(
        await session.scalar(
            select(func.count())
            .select_from(ChapterDraftVersionModel)
            .where(
                ChapterDraftVersionModel.project_id == project.id,
                ChapterDraftVersionModel.is_current.is_(True),
            )
        )
        or 0
    )
    complete_chapter_count = int(
        await session.scalar(
            select(func.count())
            .select_from(ChapterModel)
            .where(
                ChapterModel.project_id == project.id,
                ChapterModel.status == "complete",
            )
        )
        or 0
    )
    scene_count = int(
        await session.scalar(
            select(func.count()).select_from(SceneCardModel).where(SceneCardModel.project_id == project.id)
        )
        or 0
    )
    approved_scene_count = int(
        await session.scalar(
            select(func.count())
            .select_from(SceneCardModel)
            .where(
                SceneCardModel.project_id == project.id,
                SceneCardModel.status == "approved",
            )
        )
        or 0
    )
    scene_summary_count = int(
        await session.scalar(
            select(func.count())
            .select_from(CanonFactModel)
            .where(
                CanonFactModel.project_id == project.id,
                CanonFactModel.fact_type == "scene_summary",
                CanonFactModel.is_current.is_(True),
            )
        )
        or 0
    )
    timeline_event_count = int(
        await session.scalar(
            select(func.count())
            .select_from(TimelineEventModel)
            .where(TimelineEventModel.project_id == project.id)
        )
        or 0
    )
    pending_rewrite_count = int(
        await session.scalar(
            select(func.count())
            .select_from(RewriteTaskModel)
            .where(
                RewriteTaskModel.project_id == project.id,
                RewriteTaskModel.status.in_(["pending", "queued"]),
            )
        )
        or 0
    )
    project_export_count = int(
        await session.scalar(
            select(func.count())
            .select_from(ExportArtifactModel)
            .where(
                ExportArtifactModel.project_id == project.id,
                ExportArtifactModel.source_scope == "project",
            )
        )
        or 0
    )
    chapter_export_count = int(
        await session.scalar(
            select(func.count())
            .select_from(ExportArtifactModel)
            .where(
                ExportArtifactModel.project_id == project.id,
                ExportArtifactModel.source_scope == "chapter",
            )
        )
        or 0
    )
    chapter_contracts = list(
        await session.scalars(
            select(ChapterContractModel)
            .where(ChapterContractModel.project_id == project.id)
            .order_by(ChapterContractModel.chapter_number.asc())
        )
    )
    plot_arcs = list(
        await session.scalars(
            select(PlotArcModel).where(PlotArcModel.project_id == project.id)
        )
    )
    clues = list(
        await session.scalars(
            select(ClueModel).where(ClueModel.project_id == project.id)
        )
    )
    payoffs = list(
        await session.scalars(
            select(PayoffModel).where(PayoffModel.project_id == project.id)
        )
    )
    emotion_tracks = list(
        await session.scalars(
            select(EmotionTrackModel).where(EmotionTrackModel.project_id == project.id)
        )
    )
    antagonist_plans = list(
        await session.scalars(
            select(AntagonistPlanModel).where(AntagonistPlanModel.project_id == project.id)
        )
    )
    world_rules = list(
        await session.scalars(
            select(WorldRuleModel).where(WorldRuleModel.project_id == project.id)
        )
    )
    protagonists = list(
        await session.scalars(
            select(CharacterModel).where(
                CharacterModel.project_id == project.id,
                CharacterModel.role == "protagonist",
            )
        )
    )
    antagonists = list(
        await session.scalars(
            select(CharacterModel).where(
                CharacterModel.project_id == project.id,
                CharacterModel.role == "antagonist",
            )
        )
    )
    protagonist_ids = [item.id for item in protagonists if item.id is not None]
    protagonist_snapshots = list(
        await session.scalars(
            select(CharacterStateSnapshotModel)
            .where(
                CharacterStateSnapshotModel.project_id == project.id,
                CharacterStateSnapshotModel.character_id.in_(protagonist_ids or [UUID(int=0)]),
            )
            .order_by(
                CharacterStateSnapshotModel.chapter_number.asc(),
                CharacterStateSnapshotModel.scene_number.asc().nullsfirst(),
            )
        )
    ) if protagonist_ids else []
    chapter_drafts = list(
        await session.scalars(
            select(ChapterDraftVersionModel).where(
                ChapterDraftVersionModel.project_id == project.id,
                ChapterDraftVersionModel.is_current.is_(True),
            )
        )
    )
    max_chapter_number = max((chapter.chapter_number for chapter in chapter_contracts), default=chapter_count)
    narrative_signals = _collect_narrative_review_signals(
        chapter_count=chapter_count,
        max_chapter_number=max_chapter_number,
        chapter_contracts=chapter_contracts,
        plot_arcs=plot_arcs,
        clues=clues,
        payoffs=payoffs,
        emotion_tracks=emotion_tracks,
        antagonist_plans=antagonist_plans,
        world_rules=world_rules,
        protagonist_snapshots=protagonist_snapshots,
        chapter_drafts=chapter_drafts,
        protagonist_count=len(protagonists),
        antagonist_count=len(antagonists),
    )

    # ── Supporting cast depth signals ──
    supporting_characters = list(
        await session.scalars(
            select(CharacterModel).where(
                CharacterModel.project_id == project.id,
                CharacterModel.role.notin_(["protagonist", "antagonist"]),
            )
        )
    )
    supporting_character_count = len(supporting_characters)
    supporting_with_arc_count = len(
        [c for c in supporting_characters if c.arc_trajectory and c.arc_trajectory.strip()]
    )
    supporting_with_voice_count = len(
        [c for c in supporting_characters if c.voice_profile_json and c.voice_profile_json != {}]
    )

    # ── Subplot health signals ──
    subplot_arcs = [arc for arc in plot_arcs if arc.arc_type not in {"main_plot"}]
    total_subplot_count = len(subplot_arcs)
    dormant_subplot_count = 0
    if subplot_arcs and max_chapter_number >= 6:
        subplot_schedule_rows = list(
            await session.scalars(
                select(SubplotScheduleModel).where(
                    SubplotScheduleModel.project_id == project.id
                )
            )
        )
        recent_active_arc_ids = {
            row.plot_arc_id
            for row in subplot_schedule_rows
            if row.chapter_number >= max_chapter_number - 4
            and row.prominence in ("primary", "secondary", "mention")
        }
        dormant_subplot_count = len(
            [arc for arc in subplot_arcs if arc.id not in recent_active_arc_ids]
        )

    # ── Resolution completeness signals ──
    open_arc_count = len([arc for arc in plot_arcs if arc.status in ("active", "planned")])
    open_clue_count = len([clue for clue in clues if clue.actual_paid_off_chapter_number is None])
    total_volume_count = int(
        await session.scalar(
            select(func.count()).select_from(VolumeModel).where(VolumeModel.project_id == project.id)
        )
        or 0
    )
    is_final_volume = (
        total_volume_count > 0 and project.current_volume_number >= total_volume_count
    )

    review_result = evaluate_project_consistency(
        settings=settings,
        chapter_count=chapter_count,
        chapter_draft_count=chapter_draft_count,
        complete_chapter_count=complete_chapter_count,
        scene_count=scene_count,
        approved_scene_count=approved_scene_count,
        scene_summary_count=scene_summary_count,
        timeline_event_count=timeline_event_count,
        pending_rewrite_count=pending_rewrite_count,
        project_export_count=project_export_count,
        chapter_export_count=chapter_export_count,
        expect_project_export=expect_project_export,
        supporting_character_count=supporting_character_count,
        supporting_with_arc_count=supporting_with_arc_count,
        supporting_with_voice_count=supporting_with_voice_count,
        dormant_subplot_count=dormant_subplot_count,
        total_subplot_count=total_subplot_count,
        open_arc_count=open_arc_count,
        open_clue_count=open_clue_count,
        is_final_volume=is_final_volume,
        **narrative_signals,
    )

    fallback_summary = render_project_review_summary(review_result)
    system_prompt, user_prompt = build_project_review_prompts(project, review_result)
    completion = await complete_text(
        session,
        settings,
        LLMCompletionRequest(
            logical_role="critic",
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            fallback_response=fallback_summary,
            prompt_template="project_consistency_review",
            prompt_version="1.0",
            project_id=project.id,
            workflow_run_id=workflow_run_id,
            step_run_id=step_run_id,
            metadata={
                "project_slug": project.slug,
                "verdict": review_result.verdict,
            },
        ),
    )
    review_summary = completion.content.strip() or fallback_summary

    report = ReviewReportModel(
        project_id=project.id,
        target_type="project",
        target_id=project.id,
        reviewer_type=completion.model_name,
        verdict=review_result.verdict,
        severity_max=review_result.severity_max,
        llm_run_id=completion.llm_run_id,
        structured_output={
            "findings": [finding.model_dump(mode="json") for finding in review_result.findings],
            "evidence_summary": review_result.evidence_summary,
            "recommended_actions": review_result.recommended_actions,
            "review_summary": review_summary,
        },
    )
    session.add(report)
    await session.flush()

    await session.execute(
        update(QualityScoreModel)
        .where(
            QualityScoreModel.target_type == "project",
            QualityScoreModel.target_id == project.id,
            QualityScoreModel.is_current.is_(True),
        )
        .values(is_current=False)
    )

    quality = QualityScoreModel(
        project_id=project.id,
        target_type="project",
        target_id=project.id,
        review_report_id=report.id,
        is_current=True,
        score_overall=review_result.scores.overall,
        score_goal=review_result.scores.chapter_coverage,
        score_conflict=review_result.scores.main_plot_progression,
        score_emotion=review_result.scores.emotional_continuity,
        score_dialogue=review_result.scores.mystery_balance,
        score_style=review_result.scores.world_rule_consistency,
        score_hook=review_result.scores.export_readiness,
        evidence_summary=review_result.evidence_summary,
    )
    session.add(quality)
    await session.flush()
    return review_result, report, quality
