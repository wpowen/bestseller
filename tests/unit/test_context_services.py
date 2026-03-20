from __future__ import annotations

from uuid import uuid4

import pytest

from bestseller.domain.narrative_tree import NarrativeTreeNodeRead
from bestseller.domain.retrieval import RetrievedChunk, RetrievalSearchResult
from bestseller.infra.db.models import CanonFactModel, ChapterModel, ProjectModel, SceneCardModel, TimelineEventModel
from bestseller.services import context as context_services
from bestseller.settings import load_settings


pytestmark = pytest.mark.unit


class FakeSession:
    def __init__(
        self,
        *,
        scalar_results: list[object | None] | None = None,
        scalars_results: list[list[object]] | None = None,
    ) -> None:
        self.scalar_results = list(scalar_results or [])
        self.scalars_results = list(scalars_results or [])

    async def scalar(self, stmt: object) -> object | None:
        if not self.scalar_results:
            return None
        return self.scalar_results.pop(0)

    async def scalars(self, stmt: object) -> list[object]:
        if not self.scalars_results:
            return []
        return self.scalars_results.pop(0)


def build_settings():
    return load_settings(env={})


def build_project() -> ProjectModel:
    project = ProjectModel(
        slug="my-story",
        title="长夜巡航",
        genre="science-fantasy",
        target_word_count=80000,
        target_chapters=12,
        metadata_json={},
    )
    project.id = uuid4()
    return project


def build_chapter(project_id, chapter_number: int, title: str) -> ChapterModel:
    chapter = ChapterModel(
        project_id=project_id,
        chapter_number=chapter_number,
        title=title,
        chapter_goal="推进主线",
        information_revealed=[],
        information_withheld=[],
        foreshadowing_actions={},
        metadata_json={},
        target_word_count=3000,
    )
    chapter.id = uuid4()
    return chapter


def build_scene(project_id, chapter_id, scene_number: int, title: str) -> SceneCardModel:
    scene = SceneCardModel(
        project_id=project_id,
        chapter_id=chapter_id,
        scene_number=scene_number,
        scene_type="setup",
        title=title,
        participants=["沈砚"],
        purpose={"story": "推进调查", "emotion": "警觉"},
        entry_state={},
        exit_state={},
        metadata_json={},
        target_word_count=1000,
    )
    scene.id = uuid4()
    return scene


@pytest.mark.asyncio
async def test_build_scene_writer_context_includes_visible_history_and_filters_future(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = build_settings()
    settings.generation.active_context_scenes = 2

    project = build_project()
    chapter1 = build_chapter(project.id, 1, "失准星图")
    chapter2 = build_chapter(project.id, 2, "静默航道")
    previous_scene = build_scene(project.id, chapter1.id, 2, "偏移的航标")
    current_scene = build_scene(project.id, chapter2.id, 1, "旧搭档回舰")

    summary_fact = CanonFactModel(
        project_id=project.id,
        subject_type="scene_card",
        subject_id=previous_scene.id,
        subject_label=previous_scene.title,
        predicate="scene_summary",
        fact_type="scene_summary",
        value_json={
            "chapter_number": 1,
            "scene_number": 2,
            "summary": "沈砚发现第一处异常。",
            "story_purpose": "找到线索",
            "emotion_purpose": "警觉",
        },
        valid_from_chapter_no=1,
        is_current=True,
        tags=[],
    )
    summary_fact.id = uuid4()

    participant_fact = CanonFactModel(
        project_id=project.id,
        subject_type="character",
        subject_id=context_services.stable_character_id(project.id, "沈砚"),
        subject_label="沈砚",
        predicate="last_known_state",
        fact_type="state",
        value_json={
            "chapter_number": 1,
            "scene_number": 2,
            "state": {"emotion": "警觉"},
        },
        valid_from_chapter_no=1,
        is_current=True,
        tags=[],
    )
    participant_fact.id = uuid4()

    event = TimelineEventModel(
        project_id=project.id,
        chapter_id=chapter1.id,
        scene_card_id=previous_scene.id,
        event_name="发现异常",
        event_type="reveal",
        story_time_label="昨夜",
        story_order=1.02,
        participant_ids=["沈砚"],
        consequences=["调查升级"],
        metadata_json={"summary": "发现异常", "chapter_number": 1, "scene_number": 2},
    )
    event.id = uuid4()

    async def fake_get_project_by_slug(session, slug: str):
        return project

    async def fake_load_scene_story_bible_context(session, *, project, chapter, scene):
        return {"logline": "调查被篡改的航线。", "themes": ["真相"]}

    async def fake_search_retrieval_for_project(session, settings, project, query_text, **kwargs):
        return RetrievalSearchResult(
            project_id=project.id,
            query_text=query_text,
            chunks=[
                RetrievedChunk(
                    source_type="scene_draft",
                    source_id=uuid4(),
                    chunk_index=0,
                    score=0.8,
                    chunk_text="过去场景命中",
                    metadata={"chapter_number": 1, "scene_number": 2},
                ),
                RetrievedChunk(
                    source_type="scene_draft",
                    source_id=uuid4(),
                    chunk_index=0,
                    score=0.9,
                    chunk_text="未来场景命中",
                    metadata={"chapter_number": 3, "scene_number": 1},
                ),
            ],
        )

    async def fake_resolve_narrative_tree_paths_for_project(
        session,
        project,
        paths,
        **kwargs,
    ):
        return [
            NarrativeTreeNodeRead(
                id=uuid4(),
                node_path="/chapters/002/contract",
                parent_path="/chapters/002",
                depth=3,
                node_type="chapter_contract",
                title="第2章 contract",
                summary="本章必须推进主线调查。",
                body_md="# 第2章 contract",
                source_type="chapter_contract",
                source_ref_id=uuid4(),
                scope_level="chapter",
                scope_chapter_number=2,
                metadata={},
            )
        ]

    async def fake_search_narrative_tree_for_project(session, project, query_text, **kwargs):
        return type(
            "NarrativeTreeSearchStub",
            (),
            {
                "hits": [
                    type("TreeHitStub", (), {"node_path": "/arcs/main-plot"})(),
                    type("TreeHitStub", (), {"node_path": "/chapters/003"})(),
                ]
            },
        )()

    monkeypatch.setattr(context_services, "get_project_by_slug", fake_get_project_by_slug)
    monkeypatch.setattr(
        context_services,
        "load_scene_story_bible_context",
        fake_load_scene_story_bible_context,
    )
    monkeypatch.setattr(
        context_services,
        "search_retrieval_for_project",
        fake_search_retrieval_for_project,
    )
    monkeypatch.setattr(
        context_services,
        "resolve_narrative_tree_paths_for_project",
        fake_resolve_narrative_tree_paths_for_project,
    )
    monkeypatch.setattr(
        context_services,
        "search_narrative_tree_for_project",
        fake_search_narrative_tree_for_project,
    )

    session = FakeSession(
        scalar_results=[chapter2, current_scene],
        scalars_results=[
            [chapter1, chapter2],
            [previous_scene, current_scene],
            [summary_fact],
            [event],
            [participant_fact],
        ],
    )

    packet = await context_services.build_scene_writer_context(
        session,
        settings,
        "my-story",
        2,
        1,
    )

    assert packet.project_slug == "my-story"
    assert packet.recent_scene_summaries[0].summary == "沈砚发现第一处异常。"
    assert packet.recent_timeline_events[0].event_name == "发现异常"
    assert packet.participant_canon_facts[0].subject_label == "沈砚"
    assert packet.tree_context_nodes[0].node_path == "/chapters/002/contract"
    assert len(packet.retrieval_chunks) == 1
    assert packet.retrieval_chunks[0].chunk_text == "过去场景命中"


@pytest.mark.asyncio
async def test_build_chapter_writer_context_includes_scene_plan_and_filters_future(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = build_settings()
    settings.generation.active_context_scenes = 2

    project = build_project()
    chapter1 = build_chapter(project.id, 1, "失准星图")
    chapter2 = build_chapter(project.id, 2, "静默航道")
    previous_scene = build_scene(project.id, chapter1.id, 2, "偏移的航标")
    current_scene_a = build_scene(project.id, chapter2.id, 1, "旧搭档回舰")
    current_scene_b = build_scene(project.id, chapter2.id, 2, "黑匣子缺页")
    previous_scene.status = "approved"
    current_scene_a.status = "approved"
    current_scene_b.status = "planned"

    current_scene_summary = CanonFactModel(
        project_id=project.id,
        subject_type="scene_card",
        subject_id=current_scene_a.id,
        subject_label=current_scene_a.title,
        predicate="scene_summary",
        fact_type="scene_summary",
        value_json={
            "chapter_number": 2,
            "scene_number": 1,
            "summary": "沈砚重新登舰并接触旧搭档。",
            "story_purpose": "推进调查",
            "emotion_purpose": "戒备",
        },
        valid_from_chapter_no=2,
        is_current=True,
        tags=[],
    )
    current_scene_summary.id = uuid4()

    previous_summary = CanonFactModel(
        project_id=project.id,
        subject_type="scene_card",
        subject_id=previous_scene.id,
        subject_label=previous_scene.title,
        predicate="scene_summary",
        fact_type="scene_summary",
        value_json={
            "chapter_number": 1,
            "scene_number": 2,
            "summary": "沈砚发现第一处异常。",
            "story_purpose": "找到线索",
            "emotion_purpose": "警觉",
        },
        valid_from_chapter_no=1,
        is_current=True,
        tags=[],
    )
    previous_summary.id = uuid4()

    event = TimelineEventModel(
        project_id=project.id,
        chapter_id=chapter1.id,
        scene_card_id=previous_scene.id,
        event_name="发现异常",
        event_type="reveal",
        story_time_label="昨夜",
        story_order=1.02,
        participant_ids=["沈砚"],
        consequences=["调查升级"],
        metadata_json={"summary": "发现异常", "chapter_number": 1, "scene_number": 2},
    )
    event.id = uuid4()

    async def fake_get_project_by_slug(session, slug: str):
        return project

    async def fake_load_scene_story_bible_context(session, *, project, chapter, scene):
        return {"logline": "调查被篡改的航线。", "themes": ["真相"]}

    async def fake_search_retrieval_for_project(session, settings, project, query_text, **kwargs):
        return RetrievalSearchResult(
            project_id=project.id,
            query_text=query_text,
            chunks=[
                RetrievedChunk(
                    source_type="chapter_draft",
                    source_id=uuid4(),
                    chunk_index=0,
                    score=0.85,
                    chunk_text="本章之前的关键线索",
                    metadata={"chapter_number": 1},
                ),
                RetrievedChunk(
                    source_type="chapter_draft",
                    source_id=uuid4(),
                    chunk_index=0,
                    score=0.92,
                    chunk_text="未来章节片段",
                    metadata={"chapter_number": 3},
                ),
            ],
        )

    async def fake_resolve_narrative_tree_paths_for_project(
        session,
        project,
        paths,
        **kwargs,
    ):
        return [
            NarrativeTreeNodeRead(
                id=uuid4(),
                node_path="/chapters/002",
                parent_path="/chapters",
                depth=2,
                node_type="chapter",
                title="第2章",
                summary="本章推进调查。",
                body_md="# 第2章",
                source_type="chapter",
                source_ref_id=uuid4(),
                scope_level="chapter",
                scope_chapter_number=2,
                metadata={},
            )
        ]

    async def fake_search_narrative_tree_for_project(session, project, query_text, **kwargs):
        return type(
            "NarrativeTreeSearchStub",
            (),
            {"hits": [type("TreeHitStub", (), {"node_path": "/chapters/002/contract"})()]},
        )()

    monkeypatch.setattr(context_services, "get_project_by_slug", fake_get_project_by_slug)
    monkeypatch.setattr(
        context_services,
        "load_scene_story_bible_context",
        fake_load_scene_story_bible_context,
    )
    monkeypatch.setattr(
        context_services,
        "search_retrieval_for_project",
        fake_search_retrieval_for_project,
    )
    monkeypatch.setattr(
        context_services,
        "resolve_narrative_tree_paths_for_project",
        fake_resolve_narrative_tree_paths_for_project,
    )
    monkeypatch.setattr(
        context_services,
        "search_narrative_tree_for_project",
        fake_search_narrative_tree_for_project,
    )

    session = FakeSession(
        scalar_results=[chapter2],
        scalars_results=[
            [current_scene_a, current_scene_b],
            [current_scene_summary],
            [chapter1, chapter2],
            [previous_scene, current_scene_a, current_scene_b],
            [previous_summary],
            [event],
        ],
    )

    packet = await context_services.build_chapter_writer_context(
        session,
        settings,
        "my-story",
        2,
    )

    assert packet.project_slug == "my-story"
    assert packet.chapter_scenes[0].summary == "沈砚重新登舰并接触旧搭档。"
    assert packet.previous_scene_summaries[0].summary == "沈砚发现第一处异常。"
    assert packet.recent_timeline_events[0].event_name == "发现异常"
    assert packet.tree_context_nodes[0].node_path == "/chapters/002"
    assert len(packet.retrieval_chunks) == 1
    assert packet.retrieval_chunks[0].chunk_text == "本章之前的关键线索"
