from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.domain.project_review import (
    ProjectConsistencyFinding,
    ProjectConsistencyResult,
    ProjectConsistencyScores,
)
from bestseller.infra.db.models import (
    CanonFactModel,
    ChapterDraftVersionModel,
    ChapterModel,
    ExportArtifactModel,
    ProjectModel,
    QualityScoreModel,
    ReviewReportModel,
    RewriteTaskModel,
    SceneCardModel,
    TimelineEventModel,
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
    overall = _clamp_score(
        (
            chapter_coverage
            + scene_knowledge
            + canon_coverage
            + timeline_coverage
            + revision_pressure
            + export_readiness
        )
        / 6
    )

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
        score_conflict=review_result.scores.scene_knowledge,
        score_emotion=review_result.scores.canon_coverage,
        score_dialogue=review_result.scores.timeline_coverage,
        score_style=review_result.scores.revision_pressure,
        score_hook=review_result.scores.export_readiness,
        evidence_summary=review_result.evidence_summary,
    )
    session.add(quality)
    await session.flush()
    return review_result, report, quality
