from __future__ import annotations

from uuid import uuid4

import pytest

from bestseller.domain.workflow import ChapterOutlineBatchInput
from bestseller.infra.db.models import (
    PlanningArtifactVersionModel,
    ProjectModel,
    WorkflowRunModel,
    WorkflowStepRunModel,
)
from bestseller.domain.enums import ArtifactType
from bestseller.services import workflows as workflow_services


pytestmark = pytest.mark.unit


class FakeSession:
    def __init__(self, scalar_results: list[object | None] | None = None) -> None:
        self.scalar_results = list(scalar_results or [])
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


def build_project() -> ProjectModel:
    project = ProjectModel(
        slug="my-story",
        title="My Story",
        genre="fantasy",
        target_word_count=120000,
        target_chapters=60,
        metadata_json={},
    )
    project.id = uuid4()
    return project


def build_batch() -> ChapterOutlineBatchInput:
    return ChapterOutlineBatchInput.model_validate(
        {
            "batch_name": "opening",
            "chapters": [
                {
                    "chapter_number": 1,
                    "title": "The Signal",
                    "goal": "Introduce the investigation.",
                    "scenes": [
                        {
                            "scene_number": 1,
                            "scene_type": "setup",
                            "title": "Silent Dock",
                        }
                    ],
                }
            ],
        }
    )


@pytest.mark.asyncio
async def test_materialize_chapter_outline_batch_creates_workflow_records(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = build_project()

    async def fake_get_project_by_slug(session: object, slug: str) -> ProjectModel:
        return project

    async def fake_create_chapter(session: object, project_slug: str, payload: object) -> object:
        return type("ChapterStub", (), {"id": uuid4(), "chapter_number": payload.chapter_number})()

    async def fake_create_scene_card(
        session: object,
        project_slug: str,
        chapter_number: int,
        payload: object,
    ) -> object:
        return type("SceneStub", (), {"id": uuid4(), "scene_number": payload.scene_number})()

    monkeypatch.setattr(workflow_services, "get_project_by_slug", fake_get_project_by_slug)
    monkeypatch.setattr(workflow_services, "create_chapter", fake_create_chapter)
    monkeypatch.setattr(workflow_services, "create_scene_card", fake_create_scene_card)

    session = FakeSession()
    result = await workflow_services.materialize_chapter_outline_batch(
        session,
        "my-story",
        build_batch(),
        requested_by="tester",
    )

    workflow_runs = [obj for obj in session.added if isinstance(obj, WorkflowRunModel)]
    workflow_steps = [obj for obj in session.added if isinstance(obj, WorkflowStepRunModel)]

    assert result.chapters_created == 1
    assert result.scenes_created == 1
    assert len(workflow_runs) == 1
    assert workflow_runs[0].status == "completed"
    assert workflow_runs[0].metadata_json["chapters_created"] == 1
    assert workflow_runs[0].metadata_json["scenes_created"] == 1
    assert len(workflow_steps) == 3


@pytest.mark.asyncio
async def test_materialize_latest_chapter_outline_batch_uses_stored_artifact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = build_project()
    artifact = PlanningArtifactVersionModel(
        project_id=project.id,
        artifact_type="chapter_outline_batch",
        scope_ref_id=None,
        version_no=2,
        status="approved",
        schema_version="1.0",
        content=build_batch().model_dump(mode="json", by_alias=True),
        created_by="tester",
    )
    artifact.id = uuid4()

    async def fake_get_project_by_slug(session: object, slug: str) -> ProjectModel:
        return project

    async def fake_create_chapter(session: object, project_slug: str, payload: object) -> object:
        return type("ChapterStub", (), {"id": uuid4(), "chapter_number": payload.chapter_number})()

    async def fake_create_scene_card(
        session: object,
        project_slug: str,
        chapter_number: int,
        payload: object,
    ) -> object:
        return type("SceneStub", (), {"id": uuid4(), "scene_number": payload.scene_number})()

    monkeypatch.setattr(workflow_services, "get_project_by_slug", fake_get_project_by_slug)
    monkeypatch.setattr(workflow_services, "create_chapter", fake_create_chapter)
    monkeypatch.setattr(workflow_services, "create_scene_card", fake_create_scene_card)

    session = FakeSession(scalar_results=[artifact])
    result = await workflow_services.materialize_latest_chapter_outline_batch(
        session,
        "my-story",
        requested_by="tester",
    )

    assert result.source_artifact_id == artifact.id
    assert result.batch_name == "opening"


@pytest.mark.asyncio
async def test_materialize_chapter_outline_batch_marks_workflow_failed_on_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = build_project()

    async def fake_get_project_by_slug(session: object, slug: str) -> ProjectModel:
        return project

    async def fake_create_chapter(session: object, project_slug: str, payload: object) -> object:
        raise ValueError("chapter creation failed")

    monkeypatch.setattr(workflow_services, "get_project_by_slug", fake_get_project_by_slug)
    monkeypatch.setattr(workflow_services, "create_chapter", fake_create_chapter)

    session = FakeSession()

    with pytest.raises(ValueError, match="chapter creation failed"):
        await workflow_services.materialize_chapter_outline_batch(
            session,
            "my-story",
            build_batch(),
        )

    workflow_runs = [obj for obj in session.added if isinstance(obj, WorkflowRunModel)]
    workflow_steps = [obj for obj in session.added if isinstance(obj, WorkflowStepRunModel)]

    assert len(workflow_runs) == 1
    assert workflow_runs[0].status == "failed"
    assert workflow_steps[-1].status == "failed"


@pytest.mark.asyncio
async def test_materialize_story_bible_creates_workflow_records(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = build_project()

    async def fake_get_project_by_slug(session: object, slug: str) -> ProjectModel:
        return project

    async def fake_apply_book_spec(session: object, project_obj: object, content: object) -> bool:
        return True

    async def fake_upsert_world_spec(session: object, project_obj: object, content: object) -> dict[str, int]:
        return {
            "world_rules_upserted": 2,
            "locations_upserted": 1,
            "factions_upserted": 1,
        }

    async def fake_upsert_cast_spec(session: object, project_obj: object, content: object) -> dict[str, int]:
        return {
            "characters_upserted": 3,
            "relationships_upserted": 2,
            "state_snapshots_created": 3,
        }

    async def fake_upsert_volume_plan(session: object, project_obj: object, content: object) -> dict[str, int]:
        return {"volumes_upserted": 2}

    async def fake_refresh_story_bible_retrieval_index(session: object, settings: object, project_id) -> int:
        return 9

    async def fake_refresh_world_expansion_boundaries(session: object, *, project: object) -> dict[str, int]:
        return {
            "world_backbones_upserted": 1,
            "volume_frontiers_upserted": 2,
            "deferred_reveals_upserted": 3,
            "expansion_gates_upserted": 2,
        }

    monkeypatch.setattr(workflow_services, "get_project_by_slug", fake_get_project_by_slug)
    monkeypatch.setattr(workflow_services, "apply_book_spec", fake_apply_book_spec)
    monkeypatch.setattr(workflow_services, "upsert_world_spec", fake_upsert_world_spec)
    monkeypatch.setattr(workflow_services, "upsert_cast_spec", fake_upsert_cast_spec)
    monkeypatch.setattr(workflow_services, "upsert_volume_plan", fake_upsert_volume_plan)
    monkeypatch.setattr(
        workflow_services,
        "refresh_world_expansion_boundaries",
        fake_refresh_world_expansion_boundaries,
    )
    monkeypatch.setattr(
        workflow_services,
        "refresh_story_bible_retrieval_index",
        fake_refresh_story_bible_retrieval_index,
    )

    session = FakeSession()
    result = await workflow_services.materialize_story_bible(
        session,
        "my-story",
        requested_by="tester",
        book_spec_content={"title": "长夜巡航"},
        world_spec_content={"rules": []},
        cast_spec_content={"supporting_cast": []},
        volume_plan_content=[],
    )

    workflow_runs = [obj for obj in session.added if isinstance(obj, WorkflowRunModel)]
    workflow_steps = [obj for obj in session.added if isinstance(obj, WorkflowStepRunModel)]

    assert result.applied_artifacts == ["book_spec", "world_spec", "cast_spec", "volume_plan"]
    assert result.world_rules_upserted == 2
    assert result.characters_upserted == 3
    assert result.volumes_upserted == 2
    assert result.world_backbones_upserted == 1
    assert len(workflow_runs) == 1
    assert workflow_runs[0].status == "completed"
    assert len(workflow_steps) == 7


@pytest.mark.asyncio
async def test_materialize_latest_story_bible_uses_stored_artifacts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = build_project()
    book_artifact = PlanningArtifactVersionModel(
        project_id=project.id,
        artifact_type=ArtifactType.BOOK_SPEC.value,
        scope_ref_id=None,
        version_no=1,
        status="approved",
        schema_version="1.0",
        content={"title": "长夜巡航"},
        created_by="tester",
    )
    book_artifact.id = uuid4()
    cast_artifact = PlanningArtifactVersionModel(
        project_id=project.id,
        artifact_type=ArtifactType.CAST_SPEC.value,
        scope_ref_id=None,
        version_no=1,
        status="approved",
        schema_version="1.0",
        content={"supporting_cast": []},
        created_by="tester",
    )
    cast_artifact.id = uuid4()

    async def fake_get_project_by_slug(session: object, slug: str) -> ProjectModel:
        return project

    async def fake_get_latest_planning_artifact(session: object, *, project_id, artifact_type):
        if artifact_type is ArtifactType.BOOK_SPEC:
            return book_artifact
        if artifact_type is ArtifactType.CAST_SPEC:
            return cast_artifact
        return None

    async def fake_materialize_story_bible(session: object, project_slug: str, **kwargs):
        return workflow_services.StoryBibleMaterializationResult(
            workflow_run_id=uuid4(),
            project_id=project.id,
            applied_artifacts=["book_spec", "cast_spec"],
            characters_upserted=1,
            source_artifact_ids={
                "book_spec": book_artifact.id,
                "cast_spec": cast_artifact.id,
            },
        )

    monkeypatch.setattr(workflow_services, "get_project_by_slug", fake_get_project_by_slug)
    monkeypatch.setattr(
        workflow_services,
        "get_latest_planning_artifact",
        fake_get_latest_planning_artifact,
    )
    monkeypatch.setattr(workflow_services, "materialize_story_bible", fake_materialize_story_bible)

    result = await workflow_services.materialize_latest_story_bible(
        FakeSession(),
        "my-story",
        requested_by="tester",
    )

    assert result.applied_artifacts == ["book_spec", "cast_spec"]
    assert result.source_artifact_ids["book_spec"] == book_artifact.id


@pytest.mark.asyncio
async def test_materialize_narrative_graph_creates_workflow_records(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = build_project()

    async def fake_get_project_by_slug(session: object, slug: str) -> ProjectModel:
        return project

    async def fake_rebuild_narrative_graph(session: object, *, project, volume_plan_content=None):
        assert project.id == project.id
        assert volume_plan_content == [{"volume_number": 1}]
        return {
            "plot_arc_count": 3,
            "arc_beat_count": 8,
            "clue_count": 2,
            "payoff_count": 1,
            "chapter_contract_count": 4,
            "scene_contract_count": 12,
        }

    monkeypatch.setattr(workflow_services, "get_project_by_slug", fake_get_project_by_slug)
    monkeypatch.setattr(workflow_services, "rebuild_narrative_graph", fake_rebuild_narrative_graph)

    session = FakeSession()
    result = await workflow_services.materialize_narrative_graph(
        session,
        "my-story",
        requested_by="tester",
        volume_plan_content=[{"volume_number": 1}],
    )

    workflow_runs = [obj for obj in session.added if isinstance(obj, WorkflowRunModel)]
    workflow_steps = [obj for obj in session.added if isinstance(obj, WorkflowStepRunModel)]

    assert result.plot_arc_count == 3
    assert result.scene_contract_count == 12
    assert len(workflow_runs) == 1
    assert workflow_runs[0].status == "completed"
    assert workflow_runs[0].metadata_json["plot_arc_count"] == 3
    assert len(workflow_steps) == 2


@pytest.mark.asyncio
async def test_materialize_latest_narrative_graph_uses_volume_plan_artifact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = build_project()
    volume_plan_artifact = PlanningArtifactVersionModel(
        project_id=project.id,
        artifact_type=ArtifactType.VOLUME_PLAN.value,
        scope_ref_id=None,
        version_no=1,
        status="approved",
        schema_version="1.0",
        content=[{"volume_number": 1}],
        created_by="tester",
    )
    volume_plan_artifact.id = uuid4()

    async def fake_get_project_by_slug(session: object, slug: str) -> ProjectModel:
        return project

    async def fake_get_latest_planning_artifact(session: object, *, project_id, artifact_type):
        if artifact_type is ArtifactType.VOLUME_PLAN:
            return volume_plan_artifact
        return None

    async def fake_materialize_narrative_graph(session: object, project_slug: str, **kwargs):
        assert kwargs["volume_plan_content"] == [{"volume_number": 1}]
        assert kwargs["source_artifact_ids"][ArtifactType.VOLUME_PLAN.value] == volume_plan_artifact.id
        return workflow_services.NarrativeGraphMaterializationResult(
            workflow_run_id=uuid4(),
            project_id=project.id,
            plot_arc_count=3,
            arc_beat_count=8,
            clue_count=2,
            payoff_count=1,
            chapter_contract_count=4,
            scene_contract_count=12,
            source_artifact_ids={ArtifactType.VOLUME_PLAN.value: volume_plan_artifact.id},
        )

    monkeypatch.setattr(workflow_services, "get_project_by_slug", fake_get_project_by_slug)
    monkeypatch.setattr(
        workflow_services,
        "get_latest_planning_artifact",
        fake_get_latest_planning_artifact,
    )
    monkeypatch.setattr(
        workflow_services,
        "materialize_narrative_graph",
        fake_materialize_narrative_graph,
    )

    result = await workflow_services.materialize_latest_narrative_graph(
        FakeSession(),
        "my-story",
        requested_by="tester",
    )

    assert result.plot_arc_count == 3
    assert result.source_artifact_ids[ArtifactType.VOLUME_PLAN.value] == volume_plan_artifact.id


@pytest.mark.asyncio
async def test_materialize_narrative_tree_creates_workflow_records(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = build_project()

    async def fake_get_project_by_slug(session: object, slug: str) -> ProjectModel:
        return project

    async def fake_rebuild_narrative_tree(session: object, *, project) -> dict[str, object]:
        return {
            "node_count": 12,
            "node_type_counts": {"chapter": 2, "scene": 4},
        }

    monkeypatch.setattr(workflow_services, "get_project_by_slug", fake_get_project_by_slug)
    monkeypatch.setattr(workflow_services, "rebuild_narrative_tree", fake_rebuild_narrative_tree)

    session = FakeSession()
    result = await workflow_services.materialize_narrative_tree(
        session,
        "my-story",
        requested_by="tester",
    )

    workflow_runs = [obj for obj in session.added if isinstance(obj, WorkflowRunModel)]
    workflow_steps = [obj for obj in session.added if isinstance(obj, WorkflowStepRunModel)]

    assert result.node_count == 12
    assert result.node_type_counts["chapter"] == 2
    assert len(workflow_runs) == 1
    assert workflow_runs[0].status == "completed"
    assert len(workflow_steps) == 1


@pytest.mark.asyncio
async def test_materialize_latest_narrative_tree_delegates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = build_project()

    async def fake_get_project_by_slug(session: object, slug: str) -> ProjectModel:
        return project

    async def fake_materialize_narrative_tree(session: object, project_slug: str, **kwargs):
        assert project_slug == "my-story"
        return type(
            "NarrativeTreeResultStub",
            (),
            {
                "workflow_run_id": uuid4(),
                "project_id": project.id,
                "node_count": 7,
                "node_type_counts": {"premise": 1},
            },
        )()

    monkeypatch.setattr(workflow_services, "get_project_by_slug", fake_get_project_by_slug)
    monkeypatch.setattr(
        workflow_services,
        "materialize_narrative_tree",
        fake_materialize_narrative_tree,
    )

    result = await workflow_services.materialize_latest_narrative_tree(
        FakeSession(),
        "my-story",
        requested_by="tester",
    )

    assert result.node_count == 7
