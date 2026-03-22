from __future__ import annotations

from typing import Any
from uuid import UUID, uuid5

from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.domain.knowledge import SceneKnowledgeRefreshResult
from bestseller.infra.db.models import (
    CanonFactModel,
    CharacterModel,
    CharacterStateSnapshotModel,
    ChapterModel,
    ProjectModel,
    SceneCardModel,
    SceneDraftVersionModel,
    StyleGuideModel,
    TimelineEventModel,
)
from bestseller.services.llm import LLMCompletionRequest, complete_text
from bestseller.services.projects import get_project_by_slug
from bestseller.services.retrieval import index_scene_retrieval_context
from bestseller.services.story_bible import get_or_create_character_by_name, stable_character_id
from bestseller.settings import AppSettings


def render_scene_summary_fallback(
    project: ProjectModel,
    chapter: ChapterModel,
    scene: SceneCardModel,
) -> str:
    participants = "、".join(scene.participants) if scene.participants else "相关角色"
    story_purpose = str(scene.purpose.get("story", "推进主线"))
    emotion_purpose = str(scene.purpose.get("emotion", "抬高当前张力"))
    return (
        f"《{project.title}》第{chapter.chapter_number}章第{scene.scene_number}场"
        f"“{scene.title or f'场景{scene.scene_number}'}”中，{participants}围绕“{story_purpose}”展开推进，"
        f"核心情绪为“{emotion_purpose}”，并在结尾留下新的不确定性。"
    )


def build_scene_summary_prompts(
    project: ProjectModel,
    chapter: ChapterModel,
    scene: SceneCardModel,
    draft: SceneDraftVersionModel,
    style_guide: StyleGuideModel | None,
) -> tuple[str, str]:
    system_prompt = (
        "你是长篇小说知识层的剧情摘要器。"
        "请用中文输出一段 2 到 3 句的剧情摘要，强调事件推进、人物状态变化和下一步悬念。"
    )
    user_prompt = (
        f"项目：《{project.title}》\n"
        f"章节：第{chapter.chapter_number}章 {chapter.title or ''}\n"
        f"章节目标：{chapter.chapter_goal}\n"
        f"场景：第{scene.scene_number}场 {scene.title or ''}\n"
        f"场景类型：{scene.scene_type}\n"
        f"参与者：{scene.participants}\n"
        f"剧情目的：{scene.purpose.get('story', '推进主线')}\n"
        f"情绪目的：{scene.purpose.get('emotion', '拉高当前张力')}\n"
        f"时间标签：{scene.time_label or '未指定'}\n"
        f"视角：{style_guide.pov_type if style_guide else 'third-limited'}\n"
        f"当前草稿：\n{draft.content_md}\n"
        "请生成精炼摘要，方便后续做长篇一致性追踪。"
    )
    return system_prompt, user_prompt


def _scene_story_order(chapter_number: int, scene_number: int) -> float:
    return float(f"{chapter_number}.{scene_number:02d}")


def _extract_participant_state(scene: SceneCardModel, participant: str) -> dict[str, Any]:
    raw_exit_state = scene.exit_state or {}
    participant_state = raw_exit_state.get(participant)
    if isinstance(participant_state, dict):
        return participant_state
    if isinstance(participant_state, str):
        return {"state": participant_state}
    if raw_exit_state:
        return {"scene_exit_state": raw_exit_state}
    return {
        "story_focus": str(scene.purpose.get("story", "推进主线")),
        "emotion_focus": str(scene.purpose.get("emotion", "拉高张力")),
        "scene_type": scene.scene_type,
    }


def _extract_nested_text(state: dict[str, Any], key: str) -> str | None:
    value = state.get(key)
    if isinstance(value, str):
        return value
    return None


async def _upsert_character_state_snapshot(
    session: AsyncSession,
    *,
    project_id: UUID,
    character: CharacterModel,
    chapter: ChapterModel,
    scene: SceneCardModel,
    state_payload: dict[str, Any],
    summary_text: str,
) -> CharacterStateSnapshotModel:
    existing = await session.scalar(
        select(CharacterStateSnapshotModel).where(
            CharacterStateSnapshotModel.project_id == project_id,
            CharacterStateSnapshotModel.character_id == character.id,
            CharacterStateSnapshotModel.chapter_number == chapter.chapter_number,
            CharacterStateSnapshotModel.scene_number == scene.scene_number,
        )
    )
    if existing is not None:
        existing.arc_state = _extract_nested_text(state_payload, "arc_state") or existing.arc_state
        existing.emotional_state = (
            _extract_nested_text(state_payload, "emotion")
            or _extract_nested_text(state_payload, "emotional_state")
            or existing.emotional_state
        )
        existing.physical_state = (
            _extract_nested_text(state_payload, "physical_state") or existing.physical_state
        )
        existing.power_tier = _extract_nested_text(state_payload, "power_tier") or existing.power_tier
        existing.trust_map = dict(state_payload.get("trust_map", existing.trust_map or {}))
        existing.beliefs = list(state_payload.get("beliefs", existing.beliefs or []))
        existing.notes = summary_text
        return existing

    snapshot = CharacterStateSnapshotModel(
        project_id=project_id,
        character_id=character.id,
        chapter_id=chapter.id,
        scene_card_id=scene.id,
        chapter_number=chapter.chapter_number,
        scene_number=scene.scene_number,
        arc_state=_extract_nested_text(state_payload, "arc_state") or character.arc_state,
        emotional_state=_extract_nested_text(state_payload, "emotion")
        or _extract_nested_text(state_payload, "emotional_state"),
        physical_state=_extract_nested_text(state_payload, "physical_state"),
        power_tier=_extract_nested_text(state_payload, "power_tier") or character.power_tier,
        trust_map=state_payload.get("trust_map", {}),
        beliefs=list(state_payload.get("beliefs", [])),
        notes=summary_text,
    )
    session.add(snapshot)
    await session.flush()
    return snapshot


async def _load_scene_knowledge_context(
    session: AsyncSession,
    project_slug: str,
    chapter_number: int,
    scene_number: int,
) -> tuple[ProjectModel, ChapterModel, SceneCardModel, SceneDraftVersionModel, StyleGuideModel | None]:
    project = await get_project_by_slug(session, project_slug)
    if project is None:
        raise ValueError(f"Project '{project_slug}' was not found.")

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

    draft = await session.scalar(
        select(SceneDraftVersionModel).where(
            SceneDraftVersionModel.scene_card_id == scene.id,
            SceneDraftVersionModel.is_current.is_(True),
        )
    )
    if draft is None:
        raise ValueError(
            f"Scene {scene_number} in chapter {chapter_number} does not have a current draft."
        )

    style_guide = await session.get(StyleGuideModel, project.id)
    return project, chapter, scene, draft, style_guide


async def _upsert_canon_fact(
    session: AsyncSession,
    *,
    project_id: UUID,
    subject_type: str,
    subject_id: UUID,
    subject_label: str,
    predicate: str,
    fact_type: str,
    value_json: dict[str, Any],
    source_scene_id: UUID,
    source_chapter_id: UUID,
    valid_from_chapter_no: int,
    tags: list[str],
    notes: str | None = None,
) -> tuple[CanonFactModel, bool]:
    existing = await session.scalar(
        select(CanonFactModel).where(
            CanonFactModel.project_id == project_id,
            CanonFactModel.subject_type == subject_type,
            CanonFactModel.subject_id == subject_id,
            CanonFactModel.predicate == predicate,
            CanonFactModel.is_current.is_(True),
        )
    )
    if (
        existing is not None
        and existing.value_json == value_json
        and existing.source_scene_id == source_scene_id
        and existing.source_chapter_id == source_chapter_id
        and existing.tags == tags
    ):
        return existing, True

    if existing is not None:
        existing.is_current = False
        existing.valid_to_chapter_no = valid_from_chapter_no

    fact = CanonFactModel(
        project_id=project_id,
        subject_type=subject_type,
        subject_id=subject_id,
        subject_label=subject_label,
        predicate=predicate,
        fact_type=fact_type,
        value_json=value_json,
        confidence=1.0,
        source_type="extracted",
        source_scene_id=source_scene_id,
        source_chapter_id=source_chapter_id,
        valid_from_chapter_no=valid_from_chapter_no,
        is_current=True,
        tags=tags,
        notes=notes,
    )
    session.add(fact)
    await session.flush()
    return fact, False


async def _upsert_timeline_event(
    session: AsyncSession,
    *,
    project_id: UUID,
    chapter_id: UUID,
    scene_id: UUID,
    event_name: str,
    event_type: str,
    story_time_label: str,
    story_order: float,
    participant_labels: list[str],
    consequences: list[str],
    metadata_json: dict[str, Any],
) -> tuple[TimelineEventModel, bool]:
    existing = await session.scalar(
        select(TimelineEventModel).where(TimelineEventModel.scene_card_id == scene_id)
    )
    if (
        existing is not None
        and existing.event_name == event_name
        and existing.event_type == event_type
        and str(existing.story_time_label) == story_time_label
        and list(existing.participant_ids) == participant_labels
        and list(existing.consequences) == consequences
        and existing.metadata_json == metadata_json
    ):
        return existing, True

    if existing is not None:
        await session.execute(
            delete(TimelineEventModel).where(TimelineEventModel.scene_card_id == scene_id)
        )

    event = TimelineEventModel(
        project_id=project_id,
        chapter_id=chapter_id,
        scene_card_id=scene_id,
        event_name=event_name,
        event_type=event_type,
        story_time_label=story_time_label,
        story_order=story_order,
        participant_ids=participant_labels,
        consequences=consequences,
        metadata_json=metadata_json,
    )
    session.add(event)
    await session.flush()
    return event, False


async def refresh_scene_knowledge(
    session: AsyncSession,
    settings: AppSettings,
    project_slug: str,
    chapter_number: int,
    scene_number: int,
    *,
    workflow_run_id: UUID | None = None,
    step_run_id: UUID | None = None,
) -> SceneKnowledgeRefreshResult:
    project, chapter, scene, draft, style_guide = await _load_scene_knowledge_context(
        session,
        project_slug,
        chapter_number,
        scene_number,
    )

    fallback_summary = render_scene_summary_fallback(project, chapter, scene)
    system_prompt, user_prompt = build_scene_summary_prompts(
        project,
        chapter,
        scene,
        draft,
        style_guide,
    )
    completion = await complete_text(
        session,
        settings,
        LLMCompletionRequest(
            logical_role="summarizer",
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            fallback_response=fallback_summary,
            prompt_template="scene_knowledge_summary",
            prompt_version="1.0",
            project_id=project.id,
            workflow_run_id=workflow_run_id,
            step_run_id=step_run_id,
            metadata={
                "project_slug": project.slug,
                "chapter_number": chapter.chapter_number,
                "scene_number": scene.scene_number,
            },
        ),
    )
    summary_text = completion.content.strip() or fallback_summary

    canon_fact_ids: list[UUID] = []
    canon_facts_created = 0
    canon_facts_reused = 0
    tag_prefix = [f"chapter:{chapter.chapter_number}", f"scene:{scene.scene_number}", scene.scene_type]

    project_fact, project_reused = await _upsert_canon_fact(
        session,
        project_id=project.id,
        subject_type="project",
        subject_id=project.id,
        subject_label=project.title,
        predicate="latest_story_turn",
        fact_type="plot_progression",
        value_json={
            "chapter_number": chapter.chapter_number,
            "scene_number": scene.scene_number,
            "scene_title": scene.title,
            "summary": summary_text,
            "story_purpose": scene.purpose.get("story"),
            "emotion_purpose": scene.purpose.get("emotion"),
        },
        source_scene_id=scene.id,
        source_chapter_id=chapter.id,
        valid_from_chapter_no=chapter.chapter_number,
        tags=tag_prefix,
    )
    canon_fact_ids.append(project_fact.id)
    canon_facts_reused += int(project_reused)
    canon_facts_created += int(not project_reused)

    scene_fact, scene_reused = await _upsert_canon_fact(
        session,
        project_id=project.id,
        subject_type="scene_card",
        subject_id=scene.id,
        subject_label=scene.title or f"第{chapter.chapter_number}章第{scene.scene_number}场",
        predicate="scene_summary",
        fact_type="scene_summary",
        value_json={
            "chapter_number": chapter.chapter_number,
            "scene_number": scene.scene_number,
            "summary": summary_text,
            "word_count": draft.word_count,
            "draft_version_no": draft.version_no,
            "story_purpose": scene.purpose.get("story"),
            "emotion_purpose": scene.purpose.get("emotion"),
        },
        source_scene_id=scene.id,
        source_chapter_id=chapter.id,
        valid_from_chapter_no=chapter.chapter_number,
        tags=tag_prefix,
    )
    canon_fact_ids.append(scene_fact.id)
    canon_facts_reused += int(scene_reused)
    canon_facts_created += int(not scene_reused)

    for participant in scene.participants:
        character = await get_or_create_character_by_name(
            session,
            project_id=project.id,
            character_name=participant,
        )
        participant_id = character.id
        participant_state = _extract_participant_state(scene, participant)
        presence_fact, presence_reused = await _upsert_canon_fact(
            session,
            project_id=project.id,
            subject_type="character",
            subject_id=participant_id,
            subject_label=participant,
            predicate="last_seen_scene",
            fact_type="presence",
            value_json={
                "chapter_number": chapter.chapter_number,
                "scene_number": scene.scene_number,
                "scene_title": scene.title,
                "time_label": scene.time_label,
                "scene_type": scene.scene_type,
            },
            source_scene_id=scene.id,
            source_chapter_id=chapter.id,
            valid_from_chapter_no=chapter.chapter_number,
            tags=[*tag_prefix, "presence"],
        )
        canon_fact_ids.append(presence_fact.id)
        canon_facts_reused += int(presence_reused)
        canon_facts_created += int(not presence_reused)

        state_fact, state_reused = await _upsert_canon_fact(
            session,
            project_id=project.id,
            subject_type="character",
            subject_id=participant_id,
            subject_label=participant,
            predicate="last_known_state",
            fact_type="state",
            value_json={
                "chapter_number": chapter.chapter_number,
                "scene_number": scene.scene_number,
                "state": participant_state,
                "summary": summary_text,
            },
            source_scene_id=scene.id,
            source_chapter_id=chapter.id,
            valid_from_chapter_no=chapter.chapter_number,
            tags=[*tag_prefix, "state"],
        )
        canon_fact_ids.append(state_fact.id)
        canon_facts_reused += int(state_reused)
        canon_facts_created += int(not state_reused)

        await _upsert_character_state_snapshot(
            session,
            project_id=project.id,
            character=character,
            chapter=chapter,
            scene=scene,
            state_payload=participant_state,
            summary_text=summary_text,
        )
        character.arc_state = _extract_nested_text(participant_state, "arc_state") or character.arc_state
        character.power_tier = _extract_nested_text(participant_state, "power_tier") or character.power_tier
        character.metadata_json = {
            **(character.metadata_json or {}),
            "last_seen_chapter_number": chapter.chapter_number,
            "last_seen_scene_number": scene.scene_number,
            "last_summary": summary_text,
        }

    timeline_event, timeline_reused = await _upsert_timeline_event(
        session,
        project_id=project.id,
        chapter_id=chapter.id,
        scene_id=scene.id,
        event_name=scene.title or f"第{chapter.chapter_number}章第{scene.scene_number}场",
        event_type=scene.scene_type,
        story_time_label=scene.time_label or f"第{chapter.chapter_number}章第{scene.scene_number}场",
        story_order=_scene_story_order(chapter.chapter_number, scene.scene_number),
        participant_labels=scene.participants,
        consequences=[
            str(scene.purpose.get("story", "推进主线")),
            str(scene.purpose.get("emotion", "抬高当前张力")),
        ],
        metadata_json={
            "summary": summary_text,
            "scene_title": scene.title,
            "draft_version_no": draft.version_no,
            "participant_labels": scene.participants,
            "chapter_number": chapter.chapter_number,
            "scene_number": scene.scene_number,
        },
    )
    await index_scene_retrieval_context(
        session,
        settings,
        project=project,
        chapter_id=chapter.id,
        chapter_number=chapter.chapter_number,
        scene=scene,
        draft_content=draft.content_md,
        summary_text=summary_text,
    )

    return SceneKnowledgeRefreshResult(
        project_id=project.id,
        chapter_id=chapter.id,
        scene_id=scene.id,
        chapter_number=chapter.chapter_number,
        scene_number=scene.scene_number,
        canon_fact_ids=canon_fact_ids,
        timeline_event_ids=[timeline_event.id],
        canon_facts_created=canon_facts_created,
        canon_facts_reused=canon_facts_reused,
        timeline_events_created=int(not timeline_reused),
        timeline_events_reused=int(timeline_reused),
        summary_text=summary_text,
        llm_run_id=completion.llm_run_id,
    )


async def list_canon_facts(
    session: AsyncSession,
    project_slug: str,
    *,
    current_only: bool = True,
    subject_label: str | None = None,
    chapter_number: int | None = None,
) -> list[CanonFactModel]:
    project = await get_project_by_slug(session, project_slug)
    if project is None:
        raise ValueError(f"Project '{project_slug}' was not found.")

    stmt = select(CanonFactModel).where(CanonFactModel.project_id == project.id)
    if current_only:
        stmt = stmt.where(CanonFactModel.is_current.is_(True))
    if subject_label:
        stmt = stmt.where(CanonFactModel.subject_label == subject_label)
    if chapter_number is not None:
        stmt = stmt.where(CanonFactModel.valid_from_chapter_no <= chapter_number).where(
            or_(
                CanonFactModel.valid_to_chapter_no.is_(None),
                CanonFactModel.valid_to_chapter_no >= chapter_number,
            )
        )
    stmt = stmt.order_by(
        CanonFactModel.subject_type.asc(),
        CanonFactModel.subject_label.asc(),
        CanonFactModel.predicate.asc(),
        CanonFactModel.created_at.desc(),
    )
    return list(await session.scalars(stmt))


async def list_timeline_events(
    session: AsyncSession,
    project_slug: str,
    *,
    chapter_number: int | None = None,
) -> list[TimelineEventModel]:
    project = await get_project_by_slug(session, project_slug)
    if project is None:
        raise ValueError(f"Project '{project_slug}' was not found.")

    stmt = select(TimelineEventModel).where(TimelineEventModel.project_id == project.id)
    if chapter_number is not None:
        chapter = await session.scalar(
            select(ChapterModel).where(
                ChapterModel.project_id == project.id,
                ChapterModel.chapter_number == chapter_number,
            )
        )
        if chapter is None:
            raise ValueError(f"Chapter {chapter_number} was not found for '{project_slug}'.")
        stmt = stmt.where(TimelineEventModel.chapter_id == chapter.id)
    stmt = stmt.order_by(TimelineEventModel.story_order.asc(), TimelineEventModel.created_at.asc())
    return list(await session.scalars(stmt))
