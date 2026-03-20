from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from bestseller.domain.enums import ArtifactType
from bestseller.infra.db.models import (
    ChapterDraftVersionModel,
    ChapterModel,
    CharacterModel,
    CharacterStateSnapshotModel,
    FactionModel,
    LocationModel,
    PlanningArtifactVersionModel,
    ProjectModel,
    RelationshipModel,
    SceneCardModel,
    SceneDraftVersionModel,
    VolumeModel,
    WorkflowRunModel,
    WorkflowStepRunModel,
    WorldRuleModel,
)
from bestseller.services import inspection as inspection_services


pytestmark = pytest.mark.unit


class FakeSession:
    def __init__(self, *, scalars_results: list[list[object]] | None = None) -> None:
        self.scalars_results = list(scalars_results or [])

    async def scalars(self, stmt: object) -> list[object]:
        if not self.scalars_results:
            return []
        return self.scalars_results.pop(0)

    async def scalar(self, stmt: object) -> object | None:
        if not self.scalars_results:
            return None
        items = self.scalars_results.pop(0)
        return items[0] if items else None


def build_project() -> ProjectModel:
    project = ProjectModel(
        slug="my-story",
        title="长夜巡航",
        genre="science-fantasy",
        target_word_count=80000,
        target_chapters=12,
        current_volume_number=1,
        current_chapter_number=2,
        status="writing",
        metadata_json={},
    )
    project.id = uuid4()
    return project


@pytest.mark.asyncio
async def test_list_planning_artifacts_returns_versions(monkeypatch: pytest.MonkeyPatch) -> None:
    project = build_project()
    artifact = PlanningArtifactVersionModel(
        project_id=project.id,
        artifact_type="book_spec",
        version_no=2,
        status="approved",
        schema_version="1.0",
        content={"title": "长夜巡航"},
    )
    artifact.id = uuid4()
    artifact.created_at = datetime.now(timezone.utc)

    async def fake_get_project_by_slug(session, slug: str):
        return project

    monkeypatch.setattr(inspection_services, "get_project_by_slug", fake_get_project_by_slug)

    session = FakeSession(scalars_results=[[artifact]])
    results = await inspection_services.list_planning_artifacts(session, "my-story")

    assert len(results) == 1
    assert results[0].artifact_type == ArtifactType.BOOK_SPEC
    assert results[0].version_no == 2


@pytest.mark.asyncio
async def test_build_project_structure_returns_nested_structure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = build_project()
    volume = VolumeModel(
        project_id=project.id,
        volume_number=1,
        title="第一卷",
        status="writing",
        metadata_json={},
    )
    volume.id = uuid4()
    chapter = ChapterModel(
        project_id=project.id,
        volume_id=volume.id,
        chapter_number=1,
        title="失准星图",
        chapter_goal="展示危机开端",
        status="drafting",
        target_word_count=3000,
        current_word_count=1800,
        information_revealed=[],
        information_withheld=[],
        foreshadowing_actions={},
        metadata_json={},
    )
    chapter.id = uuid4()
    scene = SceneCardModel(
        project_id=project.id,
        chapter_id=chapter.id,
        scene_number=1,
        scene_type="setup",
        status="planned",
        title="封港命令",
        participants=["沈砚"],
        purpose={"story": "抛出任务"},
        entry_state={},
        exit_state={},
        target_word_count=1000,
        metadata_json={},
    )
    scene.id = uuid4()
    scene_draft = SceneDraftVersionModel(
        project_id=project.id,
        scene_card_id=scene.id,
        version_no=2,
        content_md="scene draft",
        word_count=900,
    )
    chapter_draft = ChapterDraftVersionModel(
        project_id=project.id,
        chapter_id=chapter.id,
        version_no=1,
        content_md="chapter draft",
        word_count=1800,
        assembled_from_scene_draft_ids=[],
    )

    async def fake_get_project_by_slug(session, slug: str):
        return project

    monkeypatch.setattr(inspection_services, "get_project_by_slug", fake_get_project_by_slug)

    session = FakeSession(
        scalars_results=[[volume], [chapter], [scene], [scene_draft], [chapter_draft]]
    )
    result = await inspection_services.build_project_structure(session, "my-story")

    assert result.project_slug == "my-story"
    assert result.total_chapters == 1
    assert result.total_scenes == 1
    assert result.volumes[0].chapters[0].scenes[0].current_draft_version_no == 2


@pytest.mark.asyncio
async def test_build_story_bible_overview_uses_latest_character_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = build_project()
    world_rule = WorldRuleModel(
        project_id=project.id,
        rule_code="R001",
        name="记录优先",
        description="官方记录优先于个人证词",
        metadata_json={},
    )
    location = LocationModel(
        project_id=project.id,
        name="边境星港",
        location_type="station",
        key_rule_codes=["R001"],
        metadata_json={},
    )
    faction = FactionModel(project_id=project.id, name="帝国档案局", metadata_json={})
    character_a = CharacterModel(
        project_id=project.id,
        name="沈砚",
        role="protagonist",
        goal="找证据",
        is_pov_character=True,
        knowledge_state_json={},
        metadata_json={},
    )
    character_a.id = uuid4()
    character_b = CharacterModel(
        project_id=project.id,
        name="顾临",
        role="ally",
        is_pov_character=False,
        knowledge_state_json={},
        metadata_json={},
    )
    character_b.id = uuid4()
    relationship = RelationshipModel(
        project_id=project.id,
        character_a_id=character_a.id,
        character_b_id=character_b.id,
        relationship_type="旧搭档",
        strength=0.6,
        metadata_json={},
    )
    snapshot = CharacterStateSnapshotModel(
        project_id=project.id,
        character_id=character_a.id,
        chapter_number=2,
        scene_number=1,
        arc_state="开始正视真相",
        trust_map={},
        beliefs=["官方记录不可信"],
    )

    async def fake_get_project_by_slug(session, slug: str):
        return project

    async def fake_get_latest_character_state(session, **kwargs):
        if kwargs["character_id"] == character_a.id:
            return snapshot
        return None

    monkeypatch.setattr(inspection_services, "get_project_by_slug", fake_get_project_by_slug)
    monkeypatch.setattr(inspection_services, "get_latest_character_state", fake_get_latest_character_state)

    session = FakeSession(
        scalars_results=[[world_rule], [location], [faction], [character_a, character_b], [relationship]]
    )
    result = await inspection_services.build_story_bible_overview(session, "my-story")

    assert result.project_slug == "my-story"
    assert result.world_rules[0].rule_code == "R001"
    assert result.characters[0].latest_state is None or result.characters[0].latest_state.chapter_number >= 0
    shen_yan = next(item for item in result.characters if item.name == "沈砚")
    assert shen_yan.latest_state is not None
    assert shen_yan.latest_state.arc_state == "开始正视真相"
    assert result.relationships[0].relationship_type == "旧搭档"


@pytest.mark.asyncio
async def test_build_project_workflow_overview_returns_runs_and_steps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = build_project()
    workflow_run = WorkflowRunModel(
        project_id=project.id,
        workflow_type="generate_novel_plan",
        status="completed",
        scope_type="project",
        scope_id=project.id,
        requested_by="system",
        current_step="completed",
        metadata_json={"chapter_count": 4},
    )
    workflow_run.id = uuid4()
    workflow_run.created_at = datetime.now(timezone.utc)
    workflow_run.updated_at = datetime.now(timezone.utc)
    workflow_step_a = WorkflowStepRunModel(
        workflow_run_id=workflow_run.id,
        step_name="generate_book_spec",
        step_order=1,
        status="completed",
        input_ref={},
        output_ref={"artifact": "book_spec"},
    )
    workflow_step_a.id = uuid4()
    workflow_step_a.created_at = datetime.now(timezone.utc)
    workflow_step_b = WorkflowStepRunModel(
        workflow_run_id=workflow_run.id,
        step_name="generate_world_spec",
        step_order=2,
        status="completed",
        input_ref={},
        output_ref={"artifact": "world_spec"},
    )
    workflow_step_b.id = uuid4()
    workflow_step_b.created_at = datetime.now(timezone.utc)

    async def fake_get_project_by_slug(session, slug: str):
        return project

    monkeypatch.setattr(inspection_services, "get_project_by_slug", fake_get_project_by_slug)

    session = FakeSession(scalars_results=[[workflow_run], [workflow_step_a, workflow_step_b]])
    overview = await inspection_services.build_project_workflow_overview(session, "my-story")

    assert overview.project_slug == "my-story"
    assert overview.run_count == 1
    assert overview.completed_run_count == 1
    assert overview.runs[0].workflow_type == "generate_novel_plan"
    assert overview.runs[0].step_count == 2
    assert overview.runs[0].steps[0].step_name == "generate_book_spec"
