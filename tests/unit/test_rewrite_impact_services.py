from __future__ import annotations

from contextlib import asynccontextmanager
from uuid import uuid4

import pytest

from bestseller.infra.db.models import (
    CanonFactModel,
    ChapterModel,
    ProjectModel,
    RewriteImpactModel,
    RewriteTaskModel,
    SceneCardModel,
    TimelineEventModel,
)
from bestseller.services import rewrite_impacts as rewrite_impact_services


pytestmark = pytest.mark.unit


class FakeSession:
    def __init__(
        self,
        *,
        scalar_results: list[object | None] | None = None,
        scalars_results: list[list[object]] | None = None,
        get_map: dict[object, object] | None = None,
    ) -> None:
        self.scalar_results = list(scalar_results or [])
        self.scalars_results = list(scalars_results or [])
        self.get_map = dict(get_map or {})
        self.added: list[object] = []
        self.executed: list[object] = []

    def add(self, obj: object) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        for obj in self.added:
            table = getattr(obj, "__table__", None)
            if table is None or "id" not in table.c:
                continue
            if getattr(obj, "id", None) is None:
                setattr(obj, "id", uuid4())

    async def scalar(self, stmt: object) -> object | None:
        if not self.scalar_results:
            return None
        return self.scalar_results.pop(0)

    async def scalars(self, stmt: object) -> list[object]:
        if not self.scalars_results:
            return []
        return self.scalars_results.pop(0)

    async def get(self, model: object, key: object) -> object | None:
        return self.get_map.get((model, key))

    async def execute(self, stmt: object) -> None:
        self.executed.append(stmt)


def build_project() -> ProjectModel:
    project = ProjectModel(
        slug="impact-story",
        title="Impact Story",
        genre="sci-fi",
        target_word_count=80000,
        target_chapters=20,
        metadata_json={},
    )
    project.id = uuid4()
    return project


def build_chapter(project_id, chapter_number: int) -> ChapterModel:
    chapter = ChapterModel(
        project_id=project_id,
        chapter_number=chapter_number,
        title=f"第{chapter_number}章",
        chapter_goal="推进主线",
        information_revealed=[],
        information_withheld=[],
        foreshadowing_actions={},
        metadata_json={},
        target_word_count=3000,
    )
    chapter.id = uuid4()
    return chapter


def build_scene(project_id, chapter_id, scene_number: int, participants: list[str]) -> SceneCardModel:
    scene = SceneCardModel(
        project_id=project_id,
        chapter_id=chapter_id,
        scene_number=scene_number,
        scene_type="setup",
        title=f"场景{scene_number}",
        participants=participants,
        purpose={"story": "推进主线", "emotion": "紧张"},
        entry_state={},
        exit_state={},
        key_dialogue_beats=[],
        sensory_anchors={},
        forbidden_actions=[],
        metadata_json={},
        target_word_count=1000,
    )
    scene.id = uuid4()
    return scene


@pytest.mark.asyncio
async def test_analyze_rewrite_impacts_persists_fact_scene_and_chapter_impacts() -> None:
    project = build_project()
    source_chapter = build_chapter(project.id, 1)
    later_chapter = build_chapter(project.id, 2)
    source_scene = build_scene(project.id, source_chapter.id, 1, ["沈砚", "港务官"])
    later_scene = build_scene(project.id, later_chapter.id, 1, ["沈砚", "新角色"])

    rewrite_task = RewriteTaskModel(
        project_id=project.id,
        trigger_type="scene_review",
        trigger_source_id=source_scene.id,
        rewrite_strategy="scene_dialogue_conflict_expansion",
        instructions="补强冲突",
        context_required=[],
        metadata_json={},
    )
    rewrite_task.id = uuid4()

    source_fact = CanonFactModel(
        project_id=project.id,
        subject_type="scene_card",
        subject_id=source_scene.id,
        subject_label="场景1",
        predicate="scene_summary",
        fact_type="scene_summary",
        value_json={"summary": "旧摘要"},
        source_scene_id=source_scene.id,
        source_chapter_id=source_chapter.id,
        valid_from_chapter_no=1,
        is_current=True,
        tags=[],
    )
    source_fact.id = uuid4()

    later_fact = CanonFactModel(
        project_id=project.id,
        subject_type="character",
        subject_id=uuid4(),
        subject_label="沈砚",
        predicate="last_known_state",
        fact_type="state",
        value_json={"state": "警觉"},
        source_scene_id=later_scene.id,
        source_chapter_id=later_chapter.id,
        valid_from_chapter_no=2,
        is_current=True,
        tags=[],
    )
    later_fact.id = uuid4()

    later_event = TimelineEventModel(
        project_id=project.id,
        chapter_id=later_chapter.id,
        scene_card_id=later_scene.id,
        event_name="后续波及",
        event_type="followup",
        story_time_label="次日",
        story_order=2.01,
        participant_ids=["沈砚"],
        consequences=["推进后续局面"],
        metadata_json={},
    )
    later_event.id = uuid4()

    session = FakeSession(
        scalars_results=[
            [source_chapter, later_chapter],
            [source_scene, later_scene],
            [source_fact, later_fact],
            [later_event],
        ]
    )

    result = await rewrite_impact_services.analyze_rewrite_impacts_for_scene_task(
        session,
        project_id=project.id,
        chapter=source_chapter,
        scene=source_scene,
        rewrite_task=rewrite_task,
    )

    assert result.impact_count >= 4
    assert result.max_impact_level == "must"
    persisted = [obj for obj in session.added if isinstance(obj, RewriteImpactModel)]
    assert any(obj.impacted_type == "fact" and obj.impacted_id == source_fact.id for obj in persisted)
    assert any(obj.impacted_type == "scene" and obj.impacted_id == later_scene.id for obj in persisted)
    assert any(obj.impacted_type == "chapter" and obj.impacted_id == later_chapter.id for obj in persisted)


@pytest.mark.asyncio
async def test_refresh_rewrite_impacts_uses_latest_scene_task(monkeypatch: pytest.MonkeyPatch) -> None:
    project = build_project()
    chapter = build_chapter(project.id, 1)
    scene = build_scene(project.id, chapter.id, 1, ["沈砚"])
    rewrite_task = RewriteTaskModel(
        project_id=project.id,
        trigger_type="scene_review",
        trigger_source_id=scene.id,
        rewrite_strategy="scene_dialogue_conflict_expansion",
        instructions="补强冲突",
        context_required=[],
        metadata_json={},
    )
    rewrite_task.id = uuid4()

    async def fake_get_project_by_slug(session, slug: str) -> ProjectModel:
        return project

    async def fake_analyze(session, **kwargs):
        return rewrite_impact_services.RewriteImpactAnalysisResult(
            rewrite_task_id=rewrite_task.id,
            project_id=project.id,
            source_chapter_number=1,
            source_scene_number=1,
            impact_count=1,
            max_impact_level="must",
            impacts=[],
        )

    monkeypatch.setattr(rewrite_impact_services, "get_project_by_slug", fake_get_project_by_slug)
    monkeypatch.setattr(
        rewrite_impact_services,
        "analyze_rewrite_impacts_for_scene_task",
        fake_analyze,
    )
    session = FakeSession(
        scalar_results=[chapter, scene, rewrite_task],
    )

    result = await rewrite_impact_services.refresh_rewrite_impacts(
        session,
        "impact-story",
        chapter_number=1,
        scene_number=1,
    )

    assert result.rewrite_task_id == rewrite_task.id
