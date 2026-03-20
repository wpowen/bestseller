from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from bestseller.domain.pipeline import ChapterPipelineResult
from bestseller.infra.db.models import (
    ChapterModel,
    ExportArtifactModel,
    ProjectModel,
    RewriteTaskModel,
    SceneCardModel,
    WorkflowRunModel,
    WorkflowStepRunModel,
)
from bestseller.services import repair as repair_services
from bestseller.settings import load_settings


pytestmark = pytest.mark.unit


class FakeSession:
    def __init__(
        self,
        *,
        scalar_results: list[object | None] | None = None,
        scalars_results: list[list[object]] | None = None,
        get_map: dict[tuple[object, object], object] | None = None,
    ) -> None:
        self.scalar_results = list(scalar_results or [])
        self.scalars_results = list(scalars_results or [])
        self.get_map = dict(get_map or {})
        self.added: list[object] = []

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


def build_settings():
    return load_settings(env={})


def build_project() -> ProjectModel:
    project = ProjectModel(
        slug="my-story",
        title="My Story",
        genre="sci-fi",
        target_word_count=120000,
        target_chapters=24,
        metadata_json={},
    )
    project.id = uuid4()
    return project


def build_chapter(project_id, chapter_number: int) -> ChapterModel:
    chapter = ChapterModel(
        project_id=project_id,
        chapter_number=chapter_number,
        title=f"第{chapter_number}章",
        chapter_goal="推进调查",
        information_revealed=[],
        information_withheld=[],
        foreshadowing_actions={},
        metadata_json={},
        target_word_count=3000,
    )
    chapter.id = uuid4()
    return chapter


def build_scene(project_id, chapter_id, scene_number: int) -> SceneCardModel:
    scene = SceneCardModel(
        project_id=project_id,
        chapter_id=chapter_id,
        scene_number=scene_number,
        scene_type="reveal",
        title=f"场景{scene_number}",
        participants=["沈砚"],
        purpose={"story": "推进调查", "emotion": "警觉"},
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
async def test_run_project_repair_supersedes_tasks_and_reruns_affected_chapters(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    project = build_project()
    chapter1 = build_chapter(project.id, 1)
    chapter2 = build_chapter(project.id, 2)
    chapter3 = build_chapter(project.id, 3)
    scene1 = build_scene(project.id, chapter1.id, 1)
    task_scene = RewriteTaskModel(
        project_id=project.id,
        trigger_type="scene_review",
        trigger_source_id=scene1.id,
        rewrite_strategy="scene_dialogue_conflict_expansion",
        priority=3,
        status="pending",
        instructions="补强场景",
        context_required=[],
        metadata_json={"scene_id": str(scene1.id), "chapter_id": str(chapter1.id)},
    )
    task_scene.id = uuid4()
    task_chapter = RewriteTaskModel(
        project_id=project.id,
        trigger_type="chapter_review",
        trigger_source_id=chapter3.id,
        rewrite_strategy="chapter_coherence_bridge_rewrite",
        priority=4,
        status="queued",
        instructions="补强章节",
        context_required=[],
        metadata_json={"chapter_id": str(chapter3.id)},
    )
    task_chapter.id = uuid4()
    export_artifact = ExportArtifactModel(
        project_id=project.id,
        export_type="markdown",
        source_scope="project",
        source_id=project.id,
        storage_uri=str(tmp_path / "output" / "project.md"),
        checksum="a" * 64,
        version_label="project-current",
    )
    export_artifact.id = uuid4()

    async def fake_get_project_by_slug(session, slug: str):
        return project

    async def fake_refresh_rewrite_impacts(session, project_slug: str, **kwargs):
        return type(
            "RewriteImpactResultStub",
            (),
            {
                "rewrite_task_id": task_scene.id,
                "impacts": [
                    type("ImpactStub", (), {"impacted_type": "chapter", "impacted_id": chapter2.id})(),
                ],
            },
        )()

    async def fake_run_chapter_pipeline(session, settings, project_slug: str, chapter_number: int, **kwargs):
        chapter_id = {1: chapter1.id, 2: chapter2.id, 3: chapter3.id}[chapter_number]
        return ChapterPipelineResult(
            workflow_run_id=uuid4(),
            project_id=project.id,
            chapter_id=chapter_id,
            chapter_number=chapter_number,
            scene_results=[],
            chapter_draft_id=uuid4(),
            chapter_draft_version_no=2,
            final_verdict="pass",
            requires_human_review=False,
        )

    async def fake_export_project_markdown(session, settings, project_slug: str, **kwargs):
        output_path = tmp_path / "output" / "project.md"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("# My Story", encoding="utf-8")
        return export_artifact, output_path

    async def fake_review_project_consistency(session, settings, project_slug: str, **kwargs):
        return (
            type("ReviewResultStub", (), {"verdict": "pass"})(),
            type("ReportStub", (), {"id": uuid4()})(),
            type("QualityStub", (), {"id": uuid4()})(),
        )

    monkeypatch.setattr(repair_services, "get_project_by_slug", fake_get_project_by_slug)
    monkeypatch.setattr(repair_services, "refresh_rewrite_impacts", fake_refresh_rewrite_impacts)
    monkeypatch.setattr(repair_services, "run_chapter_pipeline", fake_run_chapter_pipeline)
    monkeypatch.setattr(repair_services, "export_project_markdown", fake_export_project_markdown)
    monkeypatch.setattr(
        repair_services,
        "review_project_consistency",
        fake_review_project_consistency,
    )

    session = FakeSession(
        scalar_results=[0],
        scalars_results=[[task_scene, task_chapter]],
        get_map={
            (ChapterModel, chapter1.id): chapter1,
            (ChapterModel, chapter2.id): chapter2,
            (ChapterModel, chapter3.id): chapter3,
            (SceneCardModel, scene1.id): scene1,
        },
    )
    result = await repair_services.run_project_repair(
        session,
        build_settings(),
        "my-story",
    )

    workflow_runs = [obj for obj in session.added if isinstance(obj, WorkflowRunModel)]
    workflow_steps = [obj for obj in session.added if isinstance(obj, WorkflowStepRunModel)]

    assert result.pending_rewrite_task_count == 2
    assert result.superseded_task_count == 2
    assert [item.chapter_number for item in result.processed_chapters] == [1, 2, 3]
    assert result.export_artifact_id == export_artifact.id
    assert result.final_verdict == "pass"
    assert result.remaining_pending_rewrite_count == 0
    assert result.requires_human_review is False
    assert task_scene.status == "cancelled"
    assert task_chapter.status == "cancelled"
    assert len(workflow_runs) == 1
    assert workflow_runs[0].status == "completed"
    assert len(workflow_steps) == 7


@pytest.mark.asyncio
async def test_run_project_repair_handles_no_pending_rewrites(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = build_project()

    async def fake_get_project_by_slug(session, slug: str):
        return project

    async def fake_review_project_consistency(session, settings, project_slug: str, **kwargs):
        return (
            type("ReviewResultStub", (), {"verdict": "pass"})(),
            type("ReportStub", (), {"id": uuid4()})(),
            type("QualityStub", (), {"id": uuid4()})(),
        )

    monkeypatch.setattr(repair_services, "get_project_by_slug", fake_get_project_by_slug)
    monkeypatch.setattr(
        repair_services,
        "review_project_consistency",
        fake_review_project_consistency,
    )

    session = FakeSession(
        scalar_results=[0],
        scalars_results=[[]],
    )
    result = await repair_services.run_project_repair(
        session,
        build_settings(),
        "my-story",
        export_markdown=False,
    )

    assert result.pending_rewrite_task_count == 0
    assert result.superseded_task_count == 0
    assert result.processed_chapters == []
    assert result.export_artifact_id is None
    assert result.final_verdict == "pass"
    assert result.requires_human_review is False
