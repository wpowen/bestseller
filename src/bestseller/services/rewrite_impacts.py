from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.domain.rewrite import RewriteImpactAnalysisResult, RewriteImpactRecord
from bestseller.infra.db.models import (
    CanonFactModel,
    ChapterModel,
    RewriteImpactModel,
    RewriteTaskModel,
    SceneCardModel,
    TimelineEventModel,
)
from bestseller.services.projects import get_project_by_slug


def _clamp_score(value: float) -> float:
    return round(max(0.0, min(1.0, value)), 2)


def _impact_level_from_score(score: float) -> str:
    if score >= 0.85:
        return "must"
    if score >= 0.6:
        return "should"
    return "may"


def _max_impact_level(impacts: list[RewriteImpactRecord]) -> str:
    if any(impact.impact_level == "must" for impact in impacts):
        return "must"
    if any(impact.impact_level == "should" for impact in impacts):
        return "should"
    if any(impact.impact_level == "may" for impact in impacts):
        return "may"
    return "none"


@dataclass(slots=True)
class _ImpactDraft:
    impacted_type: str
    impacted_id: UUID
    impact_score: float
    reason: str


def _merge_impact(
    impact_map: dict[tuple[str, UUID], _ImpactDraft],
    *,
    impacted_type: str,
    impacted_id: UUID,
    impact_score: float,
    reason: str,
) -> None:
    key = (impacted_type, impacted_id)
    normalized_score = _clamp_score(impact_score)
    existing = impact_map.get(key)
    if existing is None:
        impact_map[key] = _ImpactDraft(
            impacted_type=impacted_type,
            impacted_id=impacted_id,
            impact_score=normalized_score,
            reason=reason.strip(),
        )
        return
    if normalized_score > existing.impact_score:
        existing.impact_score = normalized_score
    if reason.strip() and reason.strip() not in existing.reason:
        existing.reason = f"{existing.reason}；{reason.strip()}"


def _is_later_scene(
    *,
    source_chapter_number: int,
    source_scene_number: int,
    chapter_number: int,
    scene_number: int,
) -> bool:
    if chapter_number > source_chapter_number:
        return True
    return chapter_number == source_chapter_number and scene_number > source_scene_number


async def analyze_rewrite_impacts_for_scene_task(
    session: AsyncSession,
    *,
    project_id: UUID,
    chapter: ChapterModel,
    scene: SceneCardModel,
    rewrite_task: RewriteTaskModel,
) -> RewriteImpactAnalysisResult:
    chapter_rows = list(
        await session.scalars(
            select(ChapterModel).where(ChapterModel.project_id == project_id).order_by(ChapterModel.chapter_number.asc())
        )
    )
    chapter_by_id = {chapter_row.id: chapter_row for chapter_row in chapter_rows}

    scene_rows = list(
        await session.scalars(
            select(SceneCardModel)
            .where(SceneCardModel.project_id == project_id)
            .order_by(SceneCardModel.chapter_id.asc(), SceneCardModel.scene_number.asc())
        )
    )
    canon_rows = list(
        await session.scalars(
            select(CanonFactModel).where(
                CanonFactModel.project_id == project_id,
                CanonFactModel.is_current.is_(True),
            )
        )
    )
    timeline_rows = list(
        await session.scalars(select(TimelineEventModel).where(TimelineEventModel.project_id == project_id))
    )
    timeline_by_scene_id = {
        timeline.scene_card_id: timeline for timeline in timeline_rows if timeline.scene_card_id is not None
    }

    participants = {
        str(participant).strip()
        for participant in scene.participants
        if isinstance(participant, str) and participant.strip()
    }
    impact_map: dict[tuple[str, UUID], _ImpactDraft] = {}

    _merge_impact(
        impact_map,
        impacted_type="chapter",
        impacted_id=chapter.id,
        impact_score=0.95,
        reason=f"当前重写直接发生在第{chapter.chapter_number}章，本章装配结果必须重新生成。",
    )

    for canon_fact in canon_rows:
        if canon_fact.source_scene_id == scene.id:
            _merge_impact(
                impact_map,
                impacted_type="fact",
                impacted_id=canon_fact.id,
                impact_score=0.96,
                reason=(
                    f"事实“{canon_fact.subject_label}/{canon_fact.predicate}”直接由当前待重写场景抽取，"
                    "重写后必须刷新。"
                ),
            )
            continue

        if (
            canon_fact.subject_type == "character"
            and canon_fact.subject_label in participants
            and int(canon_fact.valid_from_chapter_no) >= int(chapter.chapter_number)
        ):
            base_score = 0.9 if canon_fact.predicate == "last_known_state" else 0.8
            _merge_impact(
                impact_map,
                impacted_type="fact",
                impacted_id=canon_fact.id,
                impact_score=base_score,
                reason=(
                    f"角色“{canon_fact.subject_label}”的后续 Canon 事实依赖当前场景推进，"
                    "重写可能改变该角色状态或出场记录。"
                ),
            )

    impacted_chapter_ids: set[UUID] = {chapter.id}
    participant_count = max(len(participants), 1)
    for candidate_scene in scene_rows:
        candidate_chapter = chapter_by_id.get(candidate_scene.chapter_id)
        if candidate_chapter is None:
            continue
        if not _is_later_scene(
            source_chapter_number=int(chapter.chapter_number),
            source_scene_number=int(scene.scene_number),
            chapter_number=int(candidate_chapter.chapter_number),
            scene_number=int(candidate_scene.scene_number),
        ):
            continue
        overlap = participants.intersection(
            {
                str(participant).strip()
                for participant in candidate_scene.participants
                if isinstance(participant, str) and participant.strip()
            }
        )
        if not overlap:
            continue
        timeline_event = timeline_by_scene_id.get(candidate_scene.id)
        overlap_ratio = len(overlap) / participant_count
        score = 0.65 + (0.15 * overlap_ratio)
        if int(candidate_chapter.chapter_number) == int(chapter.chapter_number):
            score += 0.08
        if timeline_event is not None:
            score += 0.1
        _merge_impact(
            impact_map,
            impacted_type="scene",
            impacted_id=candidate_scene.id,
            impact_score=score,
            reason=(
                f"后续第{candidate_chapter.chapter_number}章第{candidate_scene.scene_number}场与当前场景共享角色"
                f" {', '.join(sorted(overlap))}，人物状态与因果链需要回看。"
                + (" 该场景已沉淀到时间线中。" if timeline_event is not None else "")
            ),
        )
        impacted_chapter_ids.add(candidate_chapter.id)

    for chapter_row in chapter_rows:
        if chapter_row.id not in impacted_chapter_ids or chapter_row.id == chapter.id:
            continue
        scene_ids_in_chapter = {
            candidate_scene.id for candidate_scene in scene_rows if candidate_scene.chapter_id == chapter_row.id
        }
        impacted_scene_scores = [
            impact.impact_score
            for impact in impact_map.values()
            if impact.impacted_type == "scene" and impact.impacted_id in scene_ids_in_chapter
        ]
        if not impacted_scene_scores:
            continue
        _merge_impact(
            impact_map,
            impacted_type="chapter",
            impacted_id=chapter_row.id,
            impact_score=max(0.6, max(impacted_scene_scores) - 0.05),
            reason=(
                f"第{chapter_row.chapter_number}章包含受当前重写影响的后续场景，"
                "章节摘要、装配正文和导出结果建议重新检查。"
            ),
        )

    await session.execute(delete(RewriteImpactModel).where(RewriteImpactModel.rewrite_task_id == rewrite_task.id))

    persisted_impacts: list[RewriteImpactRecord] = []
    persisted_pairs: list[tuple[RewriteImpactRecord, RewriteImpactModel]] = []
    for impact in sorted(
        impact_map.values(),
        key=lambda item: (_impact_level_from_score(item.impact_score), item.impact_score),
        reverse=True,
    ):
        impact_model = RewriteImpactModel(
            rewrite_task_id=rewrite_task.id,
            impacted_type=impact.impacted_type,
            impacted_id=impact.impacted_id,
            impact_level=_impact_level_from_score(impact.impact_score),
            impact_score=impact.impact_score,
            reason=impact.reason,
        )
        session.add(impact_model)
        impact_record = RewriteImpactRecord(
            impacted_type=impact.impacted_type,
            impacted_id=impact.impacted_id,
            impact_level=_impact_level_from_score(impact.impact_score),
            impact_score=impact.impact_score,
            reason=impact.reason,
        )
        persisted_impacts.append(impact_record)
        persisted_pairs.append((impact_record, impact_model))

    await session.flush()
    for impact_record, persisted_model in persisted_pairs:
        impact_record.id = persisted_model.id

    return RewriteImpactAnalysisResult(
        rewrite_task_id=rewrite_task.id,
        project_id=project_id,
        source_chapter_number=int(chapter.chapter_number),
        source_scene_number=int(scene.scene_number),
        impact_count=len(persisted_impacts),
        max_impact_level=_max_impact_level(persisted_impacts),
        impacts=persisted_impacts,
    )


async def refresh_rewrite_impacts(
    session: AsyncSession,
    project_slug: str,
    *,
    rewrite_task_id: UUID | None = None,
    chapter_number: int | None = None,
    scene_number: int | None = None,
) -> RewriteImpactAnalysisResult:
    project = await get_project_by_slug(session, project_slug)
    if project is None:
        raise ValueError(f"Project '{project_slug}' was not found.")

    chapter: ChapterModel | None = None
    scene: SceneCardModel | None = None
    rewrite_task: RewriteTaskModel | None = None

    if rewrite_task_id is not None:
        rewrite_task = await session.scalar(
            select(RewriteTaskModel).where(
                RewriteTaskModel.project_id == project.id,
                RewriteTaskModel.id == rewrite_task_id,
            )
        )
        if rewrite_task is None:
            raise ValueError(f"Rewrite task '{rewrite_task_id}' was not found for '{project_slug}'.")
        source_scene_id = rewrite_task.trigger_source_id
        if source_scene_id is None and "scene_id" in rewrite_task.metadata_json:
            source_scene_id = UUID(str(rewrite_task.metadata_json["scene_id"]))
        if source_scene_id is None:
            raise ValueError("Rewrite task does not point to a source scene.")
        scene = await session.get(SceneCardModel, source_scene_id)
        if scene is None:
            raise ValueError("Source scene for rewrite task was not found.")
        chapter = await session.get(ChapterModel, scene.chapter_id)
        if chapter is None:
            raise ValueError("Source chapter for rewrite task was not found.")
    else:
        if chapter_number is None or scene_number is None:
            raise ValueError("Provide rewrite_task_id or both chapter_number and scene_number.")
        chapter = await session.scalar(
            select(ChapterModel).where(
                ChapterModel.project_id == project.id,
                ChapterModel.chapter_number == chapter_number,
            )
        )
        if chapter is None:
            raise ValueError(f"Chapter {chapter_number} was not found for '{project_slug}'.")
        scene = await session.scalar(
            select(SceneCardModel).where(
                SceneCardModel.chapter_id == chapter.id,
                SceneCardModel.scene_number == scene_number,
            )
        )
        if scene is None:
            raise ValueError(
                f"Scene {scene_number} was not found in chapter {chapter_number} for '{project_slug}'."
            )
        rewrite_task = await session.scalar(
            select(RewriteTaskModel)
            .where(
                RewriteTaskModel.project_id == project.id,
                RewriteTaskModel.trigger_source_id == scene.id,
            )
            .order_by(RewriteTaskModel.created_at.desc())
        )
        if rewrite_task is None:
            raise ValueError(
                f"Scene {scene_number} in chapter {chapter_number} does not have a rewrite task."
            )

    return await analyze_rewrite_impacts_for_scene_task(
        session,
        project_id=project.id,
        chapter=chapter,
        scene=scene,
        rewrite_task=rewrite_task,
    )


async def list_rewrite_impacts(
    session: AsyncSession,
    project_slug: str,
    *,
    rewrite_task_id: UUID,
) -> list[RewriteImpactModel]:
    project = await get_project_by_slug(session, project_slug)
    if project is None:
        raise ValueError(f"Project '{project_slug}' was not found.")

    rewrite_task = await session.scalar(
        select(RewriteTaskModel).where(
            RewriteTaskModel.project_id == project.id,
            RewriteTaskModel.id == rewrite_task_id,
        )
    )
    if rewrite_task is None:
        raise ValueError(f"Rewrite task '{rewrite_task_id}' was not found for '{project_slug}'.")

    stmt = (
        select(RewriteImpactModel)
        .where(RewriteImpactModel.rewrite_task_id == rewrite_task.id)
        .order_by(RewriteImpactModel.impact_score.desc(), RewriteImpactModel.created_at.asc())
    )
    return list(await session.scalars(stmt))
