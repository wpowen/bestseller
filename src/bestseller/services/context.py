from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.domain.context import (
    ChapterSceneContext,
    ChapterWriterContextPacket,
    ParticipantCanonFactContext,
    RecentSceneSummary,
    SceneWriterContextPacket,
    TimelineEventContext,
)
from bestseller.domain.narrative_tree import NarrativeTreeNodeRead
from bestseller.domain.narrative import (
    AntagonistPlanRead,
    ArcBeatRead,
    ChapterContractRead,
    ClueRead,
    EmotionTrackRead,
    PayoffRead,
    PlotArcRead,
    SceneContractRead,
)
from bestseller.infra.db.models import (
    AntagonistPlanModel,
    ArcBeatModel,
    CanonFactModel,
    ChapterContractModel,
    ChapterModel,
    ClueModel,
    EmotionTrackModel,
    PayoffModel,
    PlotArcModel,
    ProjectModel,
    SceneCardModel,
    SceneContractModel,
    TimelineEventModel,
)
from bestseller.services.projects import get_project_by_slug
from bestseller.services.narrative_tree import (
    antagonist_plan_path,
    arc_path,
    chapter_contract_path,
    chapter_path,
    character_path,
    clue_path,
    expansion_gate_path,
    emotion_track_path,
    payoff_path,
    resolve_narrative_tree_paths_for_project,
    scene_contract_path,
    scene_path,
    search_narrative_tree_for_project,
    volume_path,
    volume_frontier_path,
    world_backbone_path,
)
from bestseller.services.retrieval import search_retrieval_for_project
from bestseller.services.story_bible import load_scene_story_bible_context, stable_character_id
from bestseller.settings import AppSettings


def _scene_position(chapter_number: int, scene_number: int | None) -> tuple[int, int]:
    return chapter_number, scene_number or 0


def _is_before_current_position(
    chapter_number: int | None,
    scene_number: int | None,
    *,
    current_chapter_number: int,
    current_scene_number: int,
) -> bool:
    if chapter_number is None:
        return True
    return _scene_position(chapter_number, scene_number) < _scene_position(
        current_chapter_number,
        current_scene_number,
    )


def _canon_fact_position(fact: CanonFactModel) -> tuple[int | None, int | None]:
    if fact.value_json:
        chapter_number = fact.value_json.get("chapter_number")
        scene_number = fact.value_json.get("scene_number")
        if isinstance(chapter_number, int):
            return chapter_number, scene_number if isinstance(scene_number, int) else None
    return fact.valid_from_chapter_no, None


def _retrieval_chunk_position(chunk: dict[str, Any]) -> tuple[int | None, int | None]:
    metadata = chunk.get("metadata") or {}
    chapter_number = metadata.get("chapter_number")
    scene_number = metadata.get("scene_number")
    return (
        chapter_number if isinstance(chapter_number, int) else None,
        scene_number if isinstance(scene_number, int) else None,
    )


def _dedupe_tree_nodes(nodes: list[NarrativeTreeNodeRead]) -> list[NarrativeTreeNodeRead]:
    seen: set[str] = set()
    result: list[NarrativeTreeNodeRead] = []
    for node in nodes:
        if node.node_path in seen:
            continue
        seen.add(node.node_path)
        result.append(node)
    return result


def _plot_arc_read(item: PlotArcModel) -> PlotArcRead:
    return PlotArcRead(
        id=item.id,
        arc_code=item.arc_code,
        name=item.name,
        arc_type=item.arc_type,
        promise=item.promise,
        core_question=item.core_question,
        target_payoff=item.target_payoff,
        status=item.status,
        scope_level=item.scope_level,
        scope_volume_number=item.scope_volume_number,
        scope_chapter_number=item.scope_chapter_number,
        description=item.description,
    )


def _arc_beat_read(item: ArcBeatModel, arc_code: str | None = None) -> ArcBeatRead:
    return ArcBeatRead(
        id=item.id,
        plot_arc_id=item.plot_arc_id,
        arc_code=arc_code or str(item.metadata_json.get("arc_code", "unknown")),
        beat_order=item.beat_order,
        scope_level=item.scope_level,
        scope_volume_number=item.scope_volume_number,
        scope_chapter_number=item.scope_chapter_number,
        scope_scene_number=item.scope_scene_number,
        beat_kind=item.beat_kind,
        title=item.title,
        summary=item.summary,
        emotional_shift=item.emotional_shift,
        information_release=item.information_release,
        expected_payoff=item.expected_payoff,
        status=item.status,
    )


def _clue_read(item: ClueModel) -> ClueRead:
    return ClueRead(
        id=item.id,
        clue_code=item.clue_code,
        label=item.label,
        clue_type=item.clue_type,
        description=item.description,
        plot_arc_id=item.plot_arc_id,
        planted_in_volume_number=item.planted_in_volume_number,
        planted_in_chapter_number=item.planted_in_chapter_number,
        planted_in_scene_number=item.planted_in_scene_number,
        expected_payoff_by_volume_number=item.expected_payoff_by_volume_number,
        expected_payoff_by_chapter_number=item.expected_payoff_by_chapter_number,
        expected_payoff_by_scene_number=item.expected_payoff_by_scene_number,
        actual_paid_off_chapter_number=item.actual_paid_off_chapter_number,
        actual_paid_off_scene_number=item.actual_paid_off_scene_number,
        reveal_guard=item.reveal_guard,
        status=item.status,
    )


def _payoff_read(item: PayoffModel) -> PayoffRead:
    return PayoffRead(
        id=item.id,
        payoff_code=item.payoff_code,
        label=item.label,
        description=item.description,
        plot_arc_id=item.plot_arc_id,
        source_clue_id=item.source_clue_id,
        target_volume_number=item.target_volume_number,
        target_chapter_number=item.target_chapter_number,
        target_scene_number=item.target_scene_number,
        actual_chapter_number=item.actual_chapter_number,
        actual_scene_number=item.actual_scene_number,
        status=item.status,
    )


def _emotion_track_read(item: EmotionTrackModel) -> EmotionTrackRead:
    return EmotionTrackRead(
        id=item.id,
        track_code=item.track_code,
        track_type=item.track_type,
        title=item.title,
        character_a_label=item.character_a_label,
        character_b_label=item.character_b_label,
        relationship_type=item.relationship_type,
        summary=item.summary,
        desired_payoff=item.desired_payoff,
        trust_level=float(item.trust_level),
        attraction_level=float(item.attraction_level),
        distance_level=float(item.distance_level),
        conflict_level=float(item.conflict_level),
        intimacy_stage=item.intimacy_stage,
        last_shift_chapter_number=item.last_shift_chapter_number,
        status=item.status,
    )


def _antagonist_plan_read(item: AntagonistPlanModel) -> AntagonistPlanRead:
    return AntagonistPlanRead(
        id=item.id,
        plan_code=item.plan_code,
        antagonist_character_id=item.antagonist_character_id,
        antagonist_label=item.antagonist_label,
        title=item.title,
        threat_type=item.threat_type,
        goal=item.goal,
        current_move=item.current_move,
        next_countermove=item.next_countermove,
        escalation_condition=item.escalation_condition,
        reveal_timing=item.reveal_timing,
        scope_volume_number=item.scope_volume_number,
        target_chapter_number=item.target_chapter_number,
        pressure_level=float(item.pressure_level),
        status=item.status,
    )


def _chapter_contract_read(item: ChapterContractModel) -> ChapterContractRead:
    return ChapterContractRead(
        id=item.id,
        chapter_id=item.chapter_id,
        chapter_number=item.chapter_number,
        contract_summary=item.contract_summary,
        opening_state=dict(item.opening_state),
        core_conflict=item.core_conflict,
        emotional_shift=item.emotional_shift,
        information_release=item.information_release,
        closing_hook=item.closing_hook,
        primary_arc_codes=list(item.primary_arc_codes),
        supporting_arc_codes=list(item.supporting_arc_codes),
        active_arc_beat_ids=[str(beat_id) for beat_id in item.active_arc_beat_ids],
        planted_clue_codes=list(item.planted_clue_codes),
        due_payoff_codes=list(item.due_payoff_codes),
    )


def _scene_contract_read(item: SceneContractModel) -> SceneContractRead:
    return SceneContractRead(
        id=item.id,
        chapter_id=item.chapter_id,
        scene_card_id=item.scene_card_id,
        chapter_number=item.chapter_number,
        scene_number=item.scene_number,
        contract_summary=item.contract_summary,
        entry_state=dict(item.entry_state),
        exit_state=dict(item.exit_state),
        core_conflict=item.core_conflict,
        emotional_shift=item.emotional_shift,
        information_release=item.information_release,
        tail_hook=item.tail_hook,
        arc_codes=list(item.arc_codes),
        arc_beat_ids=[str(beat_id) for beat_id in item.arc_beat_ids],
        planted_clue_codes=list(item.planted_clue_codes),
        payoff_codes=list(item.payoff_codes),
    )


def _query_text(project: ProjectModel, chapter: ChapterModel, scene: SceneCardModel) -> str:
    query_parts = [
        project.title,
        chapter.title or "",
        chapter.chapter_goal,
        scene.title or "",
        scene.scene_type,
        str(scene.purpose.get("story", "")),
        str(scene.purpose.get("emotion", "")),
        " ".join(scene.participants),
    ]
    return " ".join(part.strip() for part in query_parts if part and str(part).strip())


def _chapter_query_text(project: ProjectModel, chapter: ChapterModel, scenes: list[SceneCardModel]) -> str:
    query_parts = [
        project.title,
        chapter.title or "",
        chapter.chapter_goal,
        *(
            f"{scene.title or ''} {scene.scene_type} {scene.purpose.get('story', '')} {' '.join(scene.participants)}"
            for scene in scenes
        ),
    ]
    return " ".join(part.strip() for part in query_parts if part and str(part).strip())


def _tree_paths_for_scene_context(
    *,
    chapter: ChapterModel,
    scene: SceneCardModel,
    volume_number: int | None,
    active_arc_codes: list[str],
    clue_codes: list[str],
    payoff_codes: list[str],
    emotion_track_codes: list[str],
    antagonist_plan_codes: list[str],
) -> list[str]:
    paths = [
        "/book/premise",
        "/book/book-spec",
        world_backbone_path(),
        "/world/expansion-gates",
        chapter_path(chapter.chapter_number),
        chapter_contract_path(chapter.chapter_number),
        scene_path(chapter.chapter_number, scene.scene_number),
        scene_contract_path(chapter.chapter_number, scene.scene_number),
    ]
    if volume_number is not None:
        paths.append(volume_path(volume_number))
        paths.append(volume_frontier_path(volume_number))
        paths.append(expansion_gate_path(f"unlock-volume-{volume_number:02d}"))
    paths.extend(character_path(name) for name in scene.participants)
    paths.extend(arc_path(code) for code in active_arc_codes[:4])
    paths.extend(clue_path(code) for code in clue_codes[:4])
    paths.extend(payoff_path(code) for code in payoff_codes[:3])
    paths.extend(emotion_track_path(code) for code in emotion_track_codes[:3])
    paths.extend(antagonist_plan_path(code) for code in antagonist_plan_codes[:3])
    return list(dict.fromkeys(path for path in paths if path))


def _tree_paths_for_chapter_context(
    *,
    chapter: ChapterModel,
    scenes: list[SceneCardModel],
    volume_number: int | None,
    active_arc_codes: list[str],
    clue_codes: list[str],
    payoff_codes: list[str],
    emotion_track_codes: list[str],
    antagonist_plan_codes: list[str],
) -> list[str]:
    paths = [
        "/book/premise",
        "/book/book-spec",
        world_backbone_path(),
        "/world/expansion-gates",
        chapter_path(chapter.chapter_number),
        chapter_contract_path(chapter.chapter_number),
    ]
    if volume_number is not None:
        paths.append(volume_path(volume_number))
        paths.append(volume_frontier_path(volume_number))
        paths.append(expansion_gate_path(f"unlock-volume-{volume_number:02d}"))
    for scene in scenes[:4]:
        paths.append(scene_path(chapter.chapter_number, scene.scene_number))
        paths.append(scene_contract_path(chapter.chapter_number, scene.scene_number))
        paths.extend(character_path(name) for name in scene.participants)
    paths.extend(arc_path(code) for code in active_arc_codes[:4])
    paths.extend(clue_path(code) for code in clue_codes[:6])
    paths.extend(payoff_path(code) for code in payoff_codes[:4])
    paths.extend(emotion_track_path(code) for code in emotion_track_codes[:4])
    paths.extend(antagonist_plan_path(code) for code in antagonist_plan_codes[:4])
    return list(dict.fromkeys(path for path in paths if path))


def _track_matches_scene(track: EmotionTrackModel, participants: set[str]) -> bool:
    return (
        track.character_a_label in participants
        or track.character_b_label in participants
    )


def _track_matches_chapter(track: EmotionTrackModel, participants: set[str]) -> bool:
    return _track_matches_scene(track, participants)


def _plan_matches_chapter(plan: AntagonistPlanModel, chapter_number: int) -> bool:
    if plan.target_chapter_number is None:
        return True
    return plan.target_chapter_number >= chapter_number


async def build_scene_writer_context(
    session: AsyncSession,
    settings: AppSettings,
    project_slug: str,
    chapter_number: int,
    scene_number: int,
) -> SceneWriterContextPacket:
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

    return await build_scene_writer_context_from_models(
        session,
        settings,
        project,
        chapter,
        scene,
    )


async def build_scene_writer_context_from_models(
    session: AsyncSession,
    settings: AppSettings,
    project: ProjectModel,
    chapter: ChapterModel,
    scene: SceneCardModel,
) -> SceneWriterContextPacket:

    story_bible_context = await load_scene_story_bible_context(
        session,
        project=project,
        chapter=chapter,
        scene=scene,
    )

    query_text = _query_text(project, chapter, scene)
    retrieval_result = await search_retrieval_for_project(
        session,
        settings,
        project,
        query_text=query_text,
        top_k=min(settings.retrieval.top_k, max(4, settings.generation.active_context_scenes * 2)),
    )

    chapters = {
        item.id: item
        for item in await session.scalars(
            select(ChapterModel).where(ChapterModel.project_id == project.id)
        )
    }
    previous_scenes = [
        item
        for item in await session.scalars(
            select(SceneCardModel).where(SceneCardModel.project_id == project.id)
        )
        if _is_before_current_position(
            chapters.get(item.chapter_id).chapter_number if chapters.get(item.chapter_id) is not None else None,
            item.scene_number,
            current_chapter_number=chapter.chapter_number,
            current_scene_number=scene.scene_number,
        )
    ]
    previous_scenes.sort(
        key=lambda item: (
            chapters.get(item.chapter_id).chapter_number if chapters.get(item.chapter_id) is not None else 0,
            item.scene_number,
        ),
        reverse=True,
    )
    previous_scene_ids = {item.id for item in previous_scenes[: settings.generation.active_context_scenes]}

    summary_facts = [
        fact
        for fact in await session.scalars(
            select(CanonFactModel).where(
                CanonFactModel.project_id == project.id,
                CanonFactModel.fact_type == "scene_summary",
                CanonFactModel.is_current.is_(True),
            )
        )
        if fact.subject_id in previous_scene_ids
    ]
    summary_facts.sort(
        key=lambda fact: _scene_position(
            int(fact.value_json.get("chapter_number", 0)),
            int(fact.value_json.get("scene_number", 0)),
        ),
        reverse=True,
    )

    recent_scene_summaries = [
        RecentSceneSummary(
            chapter_number=int(fact.value_json.get("chapter_number", 0)),
            scene_number=int(fact.value_json.get("scene_number", 0)),
            scene_title=str(fact.subject_label),
            summary=str(fact.value_json.get("summary", fact.notes or "")),
            story_purpose=str(fact.value_json.get("story_purpose")) if fact.value_json.get("story_purpose") else None,
            emotion_purpose=str(fact.value_json.get("emotion_purpose")) if fact.value_json.get("emotion_purpose") else None,
        )
        for fact in summary_facts[: settings.generation.active_context_scenes]
        if fact.value_json.get("summary") or fact.notes
    ]

    current_story_order = float(f"{chapter.chapter_number}.{scene.scene_number:02d}")
    recent_timeline_events = [
        TimelineEventContext(
            chapter_number=chapters.get(item.chapter_id).chapter_number if item.chapter_id in chapters else None,
            scene_number=(
                item.metadata_json.get("scene_number")
                if isinstance(item.metadata_json.get("scene_number"), int)
                else None
            ),
            event_name=item.event_name,
            event_type=item.event_type,
            story_time_label=item.story_time_label,
            consequences=list(item.consequences),
            summary=(
                str(item.metadata_json.get("summary"))
                if item.metadata_json.get("summary")
                else None
            ),
        )
        for item in sorted(
            [
                event
                for event in await session.scalars(
                    select(TimelineEventModel).where(TimelineEventModel.project_id == project.id)
                )
                if float(event.story_order) < current_story_order
            ],
            key=lambda event: float(event.story_order),
            reverse=True,
        )[: settings.generation.active_context_scenes]
    ]

    participant_ids = [stable_character_id(project.id, name) for name in scene.participants]
    participant_canon_facts = []
    if participant_ids:
        visible_facts: dict[tuple[str, str], CanonFactModel] = {}
        facts = [
            fact
            for fact in await session.scalars(
                select(CanonFactModel).where(
                    CanonFactModel.project_id == project.id,
                    CanonFactModel.subject_type == "character",
                    CanonFactModel.subject_id.in_(participant_ids),
                )
            )
            if fact.predicate in {"last_known_state", "last_seen_scene"}
        ]
        for fact in facts:
            fact_chapter_number, fact_scene_number = _canon_fact_position(fact)
            if not _is_before_current_position(
                fact_chapter_number,
                fact_scene_number,
                current_chapter_number=chapter.chapter_number,
                current_scene_number=scene.scene_number,
            ):
                continue
            key = (fact.subject_label, fact.predicate)
            existing = visible_facts.get(key)
            if existing is not None and _canon_fact_position(existing) >= _canon_fact_position(fact):
                continue
            visible_facts[key] = fact

        for fact in sorted(
            visible_facts.values(),
            key=lambda item: (_canon_fact_position(item), item.subject_label, item.predicate),
            reverse=True,
        ):
            fact_chapter_number, fact_scene_number = _canon_fact_position(fact)
            participant_canon_facts.append(
                ParticipantCanonFactContext(
                    subject_label=fact.subject_label,
                    predicate=fact.predicate,
                    chapter_number=fact_chapter_number,
                    scene_number=fact_scene_number,
                    value=dict(fact.value_json),
                )
            )

    retrieval_chunks = [
        chunk
        for chunk in retrieval_result.chunks
        if (
            (
                chunk.source_type == "world_rule"
                and (
                    not isinstance(story_bible_context.get("volume_frontier"), dict)
                    or not story_bible_context.get("volume_frontier", {}).get("visible_rule_codes")
                    or (
                        (chunk.metadata or {}).get("rule_code")
                        in set(story_bible_context.get("volume_frontier", {}).get("visible_rule_codes", []))
                    )
                )
            )
            or chunk.source_type in {"character", "relationship", "volume"}
            or _is_before_current_position(
                *_retrieval_chunk_position(chunk.model_dump(mode="json")),
                current_chapter_number=chapter.chapter_number,
                current_scene_number=scene.scene_number,
            )
        )
    ]

    plot_arcs = list(
        await session.scalars(
            select(PlotArcModel)
            .where(PlotArcModel.project_id == project.id)
            .order_by(PlotArcModel.arc_type.asc(), PlotArcModel.arc_code.asc())
        )
    )
    arc_code_by_id = {item.id: item.arc_code for item in plot_arcs}
    scene_beats = list(
        await session.scalars(
            select(ArcBeatModel).where(
                ArcBeatModel.project_id == project.id,
                ArcBeatModel.scope_chapter_number == chapter.chapter_number,
                ArcBeatModel.scope_scene_number == scene.scene_number,
            )
        )
    )
    chapter_beats = list(
        await session.scalars(
            select(ArcBeatModel).where(
                ArcBeatModel.project_id == project.id,
                ArcBeatModel.scope_chapter_number == chapter.chapter_number,
                ArcBeatModel.scope_scene_number.is_(None),
            )
        )
    )
    chapter_contract_row = await session.scalar(
        select(ChapterContractModel).where(
            ChapterContractModel.project_id == project.id,
            ChapterContractModel.chapter_id == chapter.id,
        )
    )
    scene_contract_row = await session.scalar(
        select(SceneContractModel).where(
            SceneContractModel.project_id == project.id,
            SceneContractModel.scene_card_id == scene.id,
        )
    )
    unresolved_clues = [
        _clue_read(item)
        for item in await session.scalars(
            select(ClueModel).where(
                ClueModel.project_id == project.id,
                ClueModel.status.in_(("planted", "active")),
            )
        )
        if (
            item.planted_in_chapter_number is None
            or _is_before_current_position(
                item.planted_in_chapter_number,
                item.planted_in_scene_number,
                current_chapter_number=chapter.chapter_number,
                current_scene_number=scene.scene_number,
            )
        )
    ]
    planned_payoffs = [
        _payoff_read(item)
        for item in await session.scalars(
            select(PayoffModel).where(PayoffModel.project_id == project.id)
        )
        if (
            item.target_chapter_number == chapter.chapter_number
            and (
                item.target_scene_number is None
                or item.target_scene_number >= scene.scene_number
            )
        )
    ]
    active_plot_arc_reads = [_plot_arc_read(item) for item in plot_arcs[:4]]
    active_arc_beat_reads = [
        _arc_beat_read(item, arc_code_by_id.get(item.plot_arc_id))
        for item in (scene_beats + chapter_beats)[:6]
    ]
    scene_participants = {participant for participant in scene.participants if participant}
    emotion_track_rows = list(
        await session.scalars(
            select(EmotionTrackModel)
            .where(
                EmotionTrackModel.project_id == project.id,
                EmotionTrackModel.status.in_(("active", "planned")),
            )
            .order_by(
                EmotionTrackModel.conflict_level.desc(),
                EmotionTrackModel.attraction_level.desc(),
                EmotionTrackModel.track_code.asc(),
            )
        )
    )
    active_emotion_tracks = [
        _emotion_track_read(item)
        for item in emotion_track_rows
        if _track_matches_scene(item, scene_participants)
    ][:4]
    antagonist_plan_rows = list(
        await session.scalars(
            select(AntagonistPlanModel)
            .where(
                AntagonistPlanModel.project_id == project.id,
                AntagonistPlanModel.status.in_(("active", "planned")),
            )
            .order_by(
                AntagonistPlanModel.pressure_level.desc(),
                AntagonistPlanModel.target_chapter_number.asc().nullsfirst(),
                AntagonistPlanModel.plan_code.asc(),
            )
        )
    )
    active_antagonist_plans = [
        _antagonist_plan_read(item)
        for item in antagonist_plan_rows
        if _plan_matches_chapter(item, chapter.chapter_number)
    ][:3]
    preferred_tree_paths = _tree_paths_for_scene_context(
        chapter=chapter,
        scene=scene,
        volume_number=(
            story_bible_context.get("volume", {}).get("volume_number")
            if isinstance(story_bible_context.get("volume"), dict)
            else None
        ),
        active_arc_codes=[
            item.arc_code
            for item in active_plot_arc_reads
            if item.arc_code
        ],
        clue_codes=[item.clue_code for item in unresolved_clues if item.clue_code],
        payoff_codes=[item.payoff_code for item in planned_payoffs if item.payoff_code],
        emotion_track_codes=[item.track_code for item in active_emotion_tracks if item.track_code],
        antagonist_plan_codes=[item.plan_code for item in active_antagonist_plans if item.plan_code],
    )
    deterministic_tree_nodes = await resolve_narrative_tree_paths_for_project(
        session,
        project,
        preferred_tree_paths,
        current_chapter_number=chapter.chapter_number,
        current_scene_number=scene.scene_number,
    )
    tree_search_result = await search_narrative_tree_for_project(
        session,
        project,
        query_text,
        preferred_paths=preferred_tree_paths,
        current_chapter_number=chapter.chapter_number,
        current_scene_number=scene.scene_number,
        top_k=max(4, settings.generation.active_context_scenes * 2),
    )
    searched_tree_nodes = await resolve_narrative_tree_paths_for_project(
        session,
        project,
        [item.node_path for item in tree_search_result.hits],
        current_chapter_number=chapter.chapter_number,
        current_scene_number=scene.scene_number,
    )
    tree_context_nodes = _dedupe_tree_nodes(deterministic_tree_nodes + searched_tree_nodes)[:12]

    return SceneWriterContextPacket(
        project_id=project.id,
        project_slug=project.slug,
        chapter_id=chapter.id,
        scene_id=scene.id,
        chapter_number=chapter.chapter_number,
        scene_number=scene.scene_number,
        query_text=query_text,
        story_bible=story_bible_context,
        recent_scene_summaries=recent_scene_summaries,
        recent_timeline_events=recent_timeline_events,
        participant_canon_facts=participant_canon_facts[: max(4, settings.generation.active_context_scenes * 2)],
        active_plot_arcs=active_plot_arc_reads,
        active_arc_beats=active_arc_beat_reads,
        unresolved_clues=unresolved_clues[:6],
        planned_payoffs=planned_payoffs[:4],
        active_emotion_tracks=active_emotion_tracks,
        active_antagonist_plans=active_antagonist_plans,
        chapter_contract=_chapter_contract_read(chapter_contract_row)
        if isinstance(chapter_contract_row, ChapterContractModel)
        else None,
        scene_contract=_scene_contract_read(scene_contract_row)
        if isinstance(scene_contract_row, SceneContractModel)
        else None,
        tree_context_nodes=tree_context_nodes,
        retrieval_chunks=retrieval_chunks,
    )


async def build_chapter_writer_context(
    session: AsyncSession,
    settings: AppSettings,
    project_slug: str,
    chapter_number: int,
) -> ChapterWriterContextPacket:
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

    scenes = list(
        await session.scalars(
            select(SceneCardModel)
            .where(SceneCardModel.chapter_id == chapter.id)
            .order_by(SceneCardModel.scene_number.asc())
        )
    )
    if not scenes:
        raise ValueError(f"Chapter {chapter_number} does not have any scene cards.")

    story_bible_context = await load_scene_story_bible_context(
        session,
        project=project,
        chapter=chapter,
        scene=scenes[0],
    )
    query_text = _chapter_query_text(project, chapter, scenes)
    retrieval_result = await search_retrieval_for_project(
        session,
        settings,
        project,
        query_text=query_text,
        top_k=min(settings.retrieval.top_k, max(6, settings.generation.active_context_scenes * 3)),
    )

    scene_ids = {scene.id for scene in scenes}
    scene_summary_map = {
        fact.subject_id: fact
        for fact in await session.scalars(
            select(CanonFactModel).where(
                CanonFactModel.project_id == project.id,
                CanonFactModel.fact_type == "scene_summary",
                CanonFactModel.subject_id.in_(scene_ids),
                CanonFactModel.is_current.is_(True),
            )
        )
    }

    chapters = {
        item.id: item
        for item in await session.scalars(
            select(ChapterModel).where(ChapterModel.project_id == project.id)
        )
    }
    previous_scenes = [
        item
        for item in await session.scalars(
            select(SceneCardModel).where(SceneCardModel.project_id == project.id)
        )
        if _is_before_current_position(
            chapters.get(item.chapter_id).chapter_number if chapters.get(item.chapter_id) is not None else None,
            item.scene_number,
            current_chapter_number=chapter.chapter_number,
            current_scene_number=1,
        )
    ]
    previous_scenes.sort(
        key=lambda item: (
            chapters.get(item.chapter_id).chapter_number if chapters.get(item.chapter_id) is not None else 0,
            item.scene_number,
        ),
        reverse=True,
    )
    previous_scene_ids = {item.id for item in previous_scenes[: settings.generation.active_context_scenes]}
    previous_summary_facts = [
        fact
        for fact in await session.scalars(
            select(CanonFactModel).where(
                CanonFactModel.project_id == project.id,
                CanonFactModel.fact_type == "scene_summary",
                CanonFactModel.subject_id.in_(previous_scene_ids),
                CanonFactModel.is_current.is_(True),
            )
        )
    ]
    previous_summary_facts.sort(
        key=lambda fact: _scene_position(
            int(fact.value_json.get("chapter_number", 0)),
            int(fact.value_json.get("scene_number", 0)),
        ),
        reverse=True,
    )

    recent_timeline_events = [
        TimelineEventContext(
            chapter_number=chapters.get(item.chapter_id).chapter_number if item.chapter_id in chapters else None,
            scene_number=(
                item.metadata_json.get("scene_number")
                if isinstance(item.metadata_json.get("scene_number"), int)
                else None
            ),
            event_name=item.event_name,
            event_type=item.event_type,
            story_time_label=item.story_time_label,
            consequences=list(item.consequences),
            summary=(
                str(item.metadata_json.get("summary"))
                if item.metadata_json.get("summary")
                else None
            ),
        )
        for item in sorted(
            [
                event
                for event in await session.scalars(
                    select(TimelineEventModel).where(TimelineEventModel.project_id == project.id)
                )
                if float(event.story_order) < float(f"{chapter.chapter_number}.99")
            ],
            key=lambda event: float(event.story_order),
            reverse=True,
        )[: max(4, settings.generation.active_context_scenes * 2)]
    ]

    retrieval_chunks = [
        chunk
        for chunk in retrieval_result.chunks
        if (
            (
                chunk.source_type == "world_rule"
                and (
                    not isinstance(story_bible_context.get("volume_frontier"), dict)
                    or not story_bible_context.get("volume_frontier", {}).get("visible_rule_codes")
                    or (
                        (chunk.metadata or {}).get("rule_code")
                        in set(story_bible_context.get("volume_frontier", {}).get("visible_rule_codes", []))
                    )
                )
            )
            or chunk.source_type in {"character", "relationship", "volume"}
            or _is_before_current_position(
                *_retrieval_chunk_position(chunk.model_dump(mode="json")),
                current_chapter_number=chapter.chapter_number,
                current_scene_number=99,
            )
        )
    ]

    plot_arcs = list(
        await session.scalars(
            select(PlotArcModel)
            .where(PlotArcModel.project_id == project.id)
            .order_by(PlotArcModel.arc_type.asc(), PlotArcModel.arc_code.asc())
        )
    )
    arc_code_by_id = {item.id: item.arc_code for item in plot_arcs}
    active_arc_beats = list(
        await session.scalars(
            select(ArcBeatModel).where(
                ArcBeatModel.project_id == project.id,
                ArcBeatModel.scope_chapter_number == chapter.chapter_number,
            )
        )
    )
    unresolved_clues = [
        _clue_read(item)
        for item in await session.scalars(
            select(ClueModel).where(
                ClueModel.project_id == project.id,
                ClueModel.status.in_(("planted", "active")),
            )
        )
        if item.planted_in_chapter_number is None or item.planted_in_chapter_number <= chapter.chapter_number
    ]
    planned_payoffs = [
        _payoff_read(item)
        for item in await session.scalars(
            select(PayoffModel).where(PayoffModel.project_id == project.id)
        )
        if item.target_chapter_number is not None and item.target_chapter_number >= chapter.chapter_number
    ]
    chapter_contract_row = await session.scalar(
        select(ChapterContractModel).where(
            ChapterContractModel.project_id == project.id,
            ChapterContractModel.chapter_id == chapter.id,
        )
    )
    active_plot_arc_reads = [_plot_arc_read(item) for item in plot_arcs[:4]]
    active_arc_beat_reads = [
        _arc_beat_read(item, arc_code_by_id.get(item.plot_arc_id))
        for item in active_arc_beats[:8]
    ]
    chapter_participants = {
        participant
        for scene in scenes
        for participant in scene.participants
        if participant
    }
    emotion_track_rows = list(
        await session.scalars(
            select(EmotionTrackModel)
            .where(
                EmotionTrackModel.project_id == project.id,
                EmotionTrackModel.status.in_(("active", "planned")),
            )
            .order_by(
                EmotionTrackModel.conflict_level.desc(),
                EmotionTrackModel.attraction_level.desc(),
                EmotionTrackModel.track_code.asc(),
            )
        )
    )
    active_emotion_tracks = [
        _emotion_track_read(item)
        for item in emotion_track_rows
        if _track_matches_chapter(item, chapter_participants)
    ][:6]
    antagonist_plan_rows = list(
        await session.scalars(
            select(AntagonistPlanModel)
            .where(
                AntagonistPlanModel.project_id == project.id,
                AntagonistPlanModel.status.in_(("active", "planned")),
            )
            .order_by(
                AntagonistPlanModel.pressure_level.desc(),
                AntagonistPlanModel.target_chapter_number.asc().nullsfirst(),
                AntagonistPlanModel.plan_code.asc(),
            )
        )
    )
    active_antagonist_plans = [
        _antagonist_plan_read(item)
        for item in antagonist_plan_rows
        if _plan_matches_chapter(item, chapter.chapter_number)
    ][:4]
    preferred_tree_paths = _tree_paths_for_chapter_context(
        chapter=chapter,
        scenes=scenes,
        volume_number=(
            story_bible_context.get("volume", {}).get("volume_number")
            if isinstance(story_bible_context.get("volume"), dict)
            else None
        ),
        active_arc_codes=[item.arc_code for item in active_plot_arc_reads if item.arc_code],
        clue_codes=[item.clue_code for item in unresolved_clues if item.clue_code],
        payoff_codes=[item.payoff_code for item in planned_payoffs if item.payoff_code],
        emotion_track_codes=[item.track_code for item in active_emotion_tracks if item.track_code],
        antagonist_plan_codes=[item.plan_code for item in active_antagonist_plans if item.plan_code],
    )
    deterministic_tree_nodes = await resolve_narrative_tree_paths_for_project(
        session,
        project,
        preferred_tree_paths,
        current_chapter_number=chapter.chapter_number,
        current_scene_number=99,
    )
    tree_search_result = await search_narrative_tree_for_project(
        session,
        project,
        query_text,
        preferred_paths=preferred_tree_paths,
        current_chapter_number=chapter.chapter_number,
        current_scene_number=99,
        top_k=max(6, settings.generation.active_context_scenes * 3),
    )
    searched_tree_nodes = await resolve_narrative_tree_paths_for_project(
        session,
        project,
        [item.node_path for item in tree_search_result.hits],
        current_chapter_number=chapter.chapter_number,
        current_scene_number=99,
    )
    tree_context_nodes = _dedupe_tree_nodes(deterministic_tree_nodes + searched_tree_nodes)[:14]

    return ChapterWriterContextPacket(
        project_id=project.id,
        project_slug=project.slug,
        chapter_id=chapter.id,
        chapter_number=chapter.chapter_number,
        query_text=query_text,
        chapter_goal=chapter.chapter_goal,
        story_bible=story_bible_context,
        chapter_scenes=[
            ChapterSceneContext(
                scene_number=scene.scene_number,
                title=scene.title,
                scene_type=scene.scene_type,
                status=scene.status,
                participants=list(scene.participants),
                story_purpose=str(scene.purpose.get("story")) if scene.purpose.get("story") else None,
                emotion_purpose=str(scene.purpose.get("emotion")) if scene.purpose.get("emotion") else None,
                summary=(
                    str(scene_summary_map[scene.id].value_json.get("summary"))
                    if scene.id in scene_summary_map and scene_summary_map[scene.id].value_json.get("summary")
                    else None
                ),
            )
            for scene in scenes
        ],
        previous_scene_summaries=[
            RecentSceneSummary(
                chapter_number=int(fact.value_json.get("chapter_number", 0)),
                scene_number=int(fact.value_json.get("scene_number", 0)),
                scene_title=str(fact.subject_label),
                summary=str(fact.value_json.get("summary", fact.notes or "")),
                story_purpose=str(fact.value_json.get("story_purpose")) if fact.value_json.get("story_purpose") else None,
                emotion_purpose=str(fact.value_json.get("emotion_purpose")) if fact.value_json.get("emotion_purpose") else None,
            )
            for fact in previous_summary_facts
            if fact.value_json.get("summary") or fact.notes
        ],
        recent_timeline_events=recent_timeline_events,
        active_plot_arcs=active_plot_arc_reads,
        active_arc_beats=active_arc_beat_reads,
        unresolved_clues=unresolved_clues[:8],
        planned_payoffs=planned_payoffs[:6],
        active_emotion_tracks=active_emotion_tracks,
        active_antagonist_plans=active_antagonist_plans,
        chapter_contract=_chapter_contract_read(chapter_contract_row)
        if isinstance(chapter_contract_row, ChapterContractModel)
        else None,
        tree_context_nodes=tree_context_nodes,
        retrieval_chunks=retrieval_chunks,
    )
