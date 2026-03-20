from __future__ import annotations

from uuid import uuid4

import pytest

from bestseller.infra.db.models import ChapterModel, ProjectModel, SceneCardModel
from bestseller.services import rewrite_cascade as cascade_services
from bestseller.settings import load_settings


pytestmark = pytest.mark.unit


class FakeSession:
    def __init__(self, *, get_map: dict[tuple[object, object], object] | None = None) -> None:
        self.get_map = dict(get_map or {})

    async def get(self, model: object, key: object) -> object | None:
        return self.get_map.get((model, key))


def build_settings():
    return load_settings(env={})


def build_project() -> ProjectModel:
    project = ProjectModel(
        slug="my-story",
        title="My Story",
        genre="fantasy",
        target_word_count=120000,
        target_chapters=24,
        metadata_json={},
    )
    project.id = uuid4()
    return project


@pytest.mark.asyncio
async def test_run_rewrite_cascade_reruns_impacted_chapters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = build_project()
    chapter = ChapterModel(
        project_id=project.id,
        chapter_number=2,
        title="静默航道",
        chapter_goal="推进调查",
        information_revealed=[],
        information_withheld=[],
        foreshadowing_actions={},
        metadata_json={},
        target_word_count=3000,
    )
    chapter.id = uuid4()
    scene = SceneCardModel(
        project_id=project.id,
        chapter_id=chapter.id,
        scene_number=1,
        scene_type="reveal",
        participants=["沈砚"],
        purpose={},
        entry_state={},
        exit_state={},
        key_dialogue_beats=[],
        sensory_anchors={},
        forbidden_actions=[],
        metadata_json={},
        target_word_count=1000,
    )
    scene.id = uuid4()

    async def fake_get_project_by_slug(session, slug: str):
        return project

    async def fake_refresh_rewrite_impacts(session, project_slug: str, **kwargs):
        return type(
            "RewriteImpactResultStub",
            (),
            {
                "rewrite_task_id": uuid4(),
                "impacts": [
                    type("ImpactStub", (), {"impacted_type": "scene", "impacted_id": scene.id})(),
                    type("ImpactStub", (), {"impacted_type": "chapter", "impacted_id": chapter.id})(),
                ],
            },
        )()

    async def fake_run_chapter_pipeline(session, settings, project_slug: str, chapter_number: int, **kwargs):
        return type(
            "ChapterPipelineStub",
            (),
            {"workflow_run_id": uuid4(), "requires_human_review": False},
        )()

    monkeypatch.setattr(cascade_services, "get_project_by_slug", fake_get_project_by_slug)
    monkeypatch.setattr(cascade_services, "refresh_rewrite_impacts", fake_refresh_rewrite_impacts)
    monkeypatch.setattr(cascade_services, "run_chapter_pipeline", fake_run_chapter_pipeline)

    session = FakeSession(
        get_map={
            (ChapterModel, chapter.id): chapter,
            (SceneCardModel, scene.id): scene,
        }
    )
    result = await cascade_services.run_rewrite_cascade(
        session,
        build_settings(),
        "my-story",
        chapter_number=1,
        scene_number=1,
    )

    assert result.impact_count == 2
    assert len(result.processed_chapters) == 1
    assert result.processed_chapters[0].chapter_number == 2
