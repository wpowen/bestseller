from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.domain.enums import ArtifactType
from bestseller.infra.db.models import PlanningArtifactVersionModel, ProjectModel
from bestseller.infra.db.session import create_engine
from bestseller.services import pipelines
from bestseller.settings import AppSettings

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_pipeline_consumers_ignore_newer_needs_review_artifacts(
    postgresql_test_settings: AppSettings,
) -> None:
    """Production pipeline readers must consume approved canonical inputs only."""

    engine = create_engine(postgresql_test_settings)
    async with engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)
        try:
            project = ProjectModel(
                id=uuid4(),
                slug=f"pipeline-approved-read-{uuid4().hex}",
                title="Pipeline approved read integration fixture",
                language="en",
                genre="fantasy",
                target_word_count=100_000,
                target_chapters=40,
                metadata_json={},
            )
            session.add(project)
            await session.flush()

            world_v1 = PlanningArtifactVersionModel(
                project_id=project.id,
                artifact_type=ArtifactType.WORLD_SPEC.value,
                scope_ref_id=None,
                version_no=1,
                status="approved",
                schema_version="test",
                content={"fixture_version": "world-approved-v1"},
                created_by="pipeline-approved-read-integration-test",
            )
            world_v2 = PlanningArtifactVersionModel(
                project_id=project.id,
                artifact_type=ArtifactType.WORLD_SPEC.value,
                scope_ref_id=None,
                version_no=2,
                status="needs_review",
                schema_version="test",
                content={"fixture_version": "world-needs-review-v2"},
                created_by="pipeline-approved-read-integration-test",
            )
            outline_v1 = PlanningArtifactVersionModel(
                project_id=project.id,
                artifact_type=ArtifactType.CHAPTER_OUTLINE_BATCH.value,
                scope_ref_id=None,
                version_no=1,
                status="approved",
                schema_version="test",
                content={
                    "chapters": [
                        {
                            "chapter_number": 1,
                            "volume_number": 1,
                            "title": "Approved chapter",
                        }
                    ]
                },
                created_by="pipeline-approved-read-integration-test",
            )
            outline_v2 = PlanningArtifactVersionModel(
                project_id=project.id,
                artifact_type=ArtifactType.CHAPTER_OUTLINE_BATCH.value,
                scope_ref_id=None,
                version_no=2,
                status="needs_review",
                schema_version="test",
                content={
                    "chapters": [
                        {
                            "chapter_number": 1,
                            "volume_number": 1,
                            "title": "Needs review chapter",
                        }
                    ]
                },
                created_by="pipeline-approved-read-integration-test",
            )
            volume_outline_v1 = PlanningArtifactVersionModel(
                project_id=project.id,
                artifact_type=ArtifactType.VOLUME_CHAPTER_OUTLINE.value,
                scope_ref_id=None,
                version_no=1,
                status="approved",
                schema_version="test",
                content={"fixture_version": "volume-approved-v1", "chapters": []},
                created_by="pipeline-approved-read-integration-test",
            )
            volume_outline_v2 = PlanningArtifactVersionModel(
                project_id=project.id,
                artifact_type=ArtifactType.VOLUME_CHAPTER_OUTLINE.value,
                scope_ref_id=None,
                version_no=2,
                status="needs_review",
                schema_version="test",
                content={"fixture_version": "volume-needs-review-v2", "chapters": []},
                created_by="pipeline-approved-read-integration-test",
            )
            session.add_all(
                [
                    world_v1,
                    world_v2,
                    outline_v1,
                    outline_v2,
                    volume_outline_v1,
                    volume_outline_v2,
                ]
            )
            await session.flush()

            world = await pipelines._get_current_world_spec_artifact(
                session,
                project=project,
            )
            chapters = await pipelines._resume_outline_chapters_for_volume(
                session,
                project_id=project.id,
                volume_number=1,
                expected_count=1,
            )

            assert world is world_v1
            assert world.content["fixture_version"] == "world-approved-v1"
            assert chapters == [outline_v1.content["chapters"][0]]
            volume_outlines = await pipelines._load_approved_volume_chapter_outline_artifacts(
                session,
                project_id=project.id,
            )
            assert volume_outlines == [volume_outline_v1]
        finally:
            await session.close()
            await transaction.rollback()
    await engine.dispose()
