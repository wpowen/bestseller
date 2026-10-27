from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.domain.enums import ArtifactType
from bestseller.domain.planning import PlanningArtifactCreate
from bestseller.infra.db.models import PlanningArtifactVersionModel, ProjectModel
from bestseller.infra.db.session import create_engine
from bestseller.services import workflows as workflow_services
from bestseller.services.projects import import_planning_artifact
from bestseller.settings import AppSettings

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_real_db_latest_approved_artifact_selection_is_status_and_scope_safe(
    postgresql_test_settings: AppSettings,
) -> None:
    """Use bestseller_test transactionally; never leave fixture artifacts behind."""

    engine = create_engine(postgresql_test_settings)
    async with engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)
        try:
            project = ProjectModel(
                id=uuid4(),
                slug=f"approved-read-{uuid4().hex}",
                title="Approved artifact read integration fixture",
                language="en",
                genre="fantasy",
                target_word_count=100_000,
                target_chapters=40,
                metadata_json={},
            )
            session.add(project)
            await session.flush()
            project_id = project.id

            artifact_type = ArtifactType.BOOK_SPEC.value
            current_version = int(
                await session.scalar(
                    select(
                        func.coalesce(func.max(PlanningArtifactVersionModel.version_no), 0)
                    ).where(
                        PlanningArtifactVersionModel.project_id == project_id,
                        PlanningArtifactVersionModel.artifact_type == artifact_type,
                        PlanningArtifactVersionModel.scope_ref_id.is_(None),
                    )
                )
                or 0
            )
            approved_v1 = PlanningArtifactVersionModel(
                project_id=project_id,
                artifact_type=artifact_type,
                scope_ref_id=None,
                version_no=current_version + 100,
                status="approved",
                schema_version="test",
                content={"fixture_version": "approved-v1"},
                created_by="approved-read-integration-test",
            )
            pending_v2 = PlanningArtifactVersionModel(
                project_id=project_id,
                artifact_type=artifact_type,
                scope_ref_id=None,
                version_no=current_version + 101,
                status="needs_review",
                schema_version="test",
                content={"fixture_version": "needs-review-v2"},
                created_by="approved-read-integration-test",
            )
            volume_id = uuid4()
            scoped_approved = PlanningArtifactVersionModel(
                project_id=project_id,
                artifact_type=artifact_type,
                scope_ref_id=volume_id,
                version_no=1,
                status="approved",
                schema_version="test",
                content={"fixture_version": "scoped-approved"},
                created_by="approved-read-integration-test",
            )
            scoped_pending = PlanningArtifactVersionModel(
                project_id=project_id,
                artifact_type=artifact_type,
                scope_ref_id=volume_id,
                version_no=2,
                status="needs_review",
                schema_version="test",
                content={"fixture_version": "scoped-needs-review"},
                created_by="approved-read-integration-test",
            )
            pending_only_volume_id = uuid4()
            pending_only = PlanningArtifactVersionModel(
                project_id=project_id,
                artifact_type=artifact_type,
                scope_ref_id=pending_only_volume_id,
                version_no=1,
                status="needs_review",
                schema_version="test",
                content={"fixture_version": "pending-only"},
                created_by="approved-read-integration-test",
            )
            session.add_all(
                [approved_v1, pending_v2, scoped_approved, scoped_pending, pending_only]
            )
            await session.flush()

            latest = await workflow_services.get_latest_planning_artifact(
                session,
                project_id=project_id,
                artifact_type=ArtifactType.BOOK_SPEC,
            )
            approved = await workflow_services.get_latest_approved_planning_artifact(
                session,
                project_id=project_id,
                artifact_type=ArtifactType.BOOK_SPEC,
            )
            assert latest is not None and latest.content["fixture_version"] == "needs-review-v2"
            assert approved is not None and approved.content["fixture_version"] == "approved-v1"

            await session.delete(approved_v1)
            await session.flush()
            approved_after_removal = await workflow_services.get_latest_approved_planning_artifact(
                session,
                project_id=project_id,
                artifact_type=ArtifactType.BOOK_SPEC,
                scope_ref_id=pending_only_volume_id,
            )
            assert approved_after_removal is None

            scoped_approved_result = await workflow_services.get_latest_approved_planning_artifact(
                session,
                project_id=project_id,
                artifact_type=ArtifactType.BOOK_SPEC,
                scope_ref_id=volume_id,
            )
            assert scoped_approved_result is not None
            assert scoped_approved_result.content["fixture_version"] == "scoped-approved"

            scoped_only_type = ArtifactType.CREATIVE_EXPLORATION
            scoped_only = PlanningArtifactVersionModel(
                project_id=project_id,
                artifact_type=scoped_only_type.value,
                scope_ref_id=uuid4(),
                version_no=1,
                status="approved",
                schema_version="test",
                content={"fixture_version": "scoped-only-approved"},
                created_by="approved-read-integration-test",
            )
            session.add(scoped_only)
            await session.flush()
            default_scope_result = await workflow_services.get_latest_approved_planning_artifact(
                session,
                project_id=project_id,
                artifact_type=scoped_only_type,
            )
            assert default_scope_result is not None
            assert default_scope_result.content["fixture_version"] == "scoped-only-approved"
        finally:
            await session.close()
            await transaction.rollback()
    await engine.dispose()


@pytest.mark.asyncio
async def test_real_db_source_bound_review_transition_forces_new_artifact_version(
    postgresql_test_settings: AppSettings,
) -> None:
    """A pending-to-reviewed receipt transition cannot reuse the old row."""

    engine = create_engine(postgresql_test_settings)
    async with engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)
        try:
            project = ProjectModel(
                id=uuid4(),
                slug=f"source-review-reuse-{uuid4().hex}",
                title="Source review reuse integration fixture",
                language="en",
                genre="fantasy",
                target_word_count=100_000,
                target_chapters=40,
                metadata_json={},
            )
            session.add(project)
            await session.flush()

            old = PlanningArtifactVersionModel(
                project_id=project.id,
                artifact_type=ArtifactType.WORLD_SPEC.value,
                scope_ref_id=None,
                version_no=1,
                status="approved",
                schema_version="test",
                content={
                    "logline": "Same derived world.",
                    "_meta": {
                        "input_hash": "same-input",
                        "source_bound_expansion": "derived_pending_review",
                        "source_bound_expansion_review": "independent_semantic_review_required",
                    },
                },
                created_by="source-review-reuse-integration-test",
            )
            session.add(old)
            await session.flush()

            reviewed = await import_planning_artifact(
                session,
                project.slug,
                PlanningArtifactCreate(
                    artifact_type=ArtifactType.WORLD_SPEC,
                    content={
                        "logline": "Same derived world.",
                        "_meta": {
                            "input_hash": "same-input",
                            "source_bound_expansion": "reviewed",
                            "source_bound_expansion_review": "passed",
                            "source_bound_expansion_review_receipt": {
                                "candidate_hash": "candidate-v2",
                                "llm_run_id": str(uuid4()),
                            },
                        },
                    },
                ),
            )
            assert reviewed.id != old.id
            assert reviewed.version_no == 2
            assert old.content["_meta"]["source_bound_expansion"] == "derived_pending_review"
            assert reviewed.content["_meta"]["source_bound_expansion"] == "reviewed"

            safe_reuse = await import_planning_artifact(
                session,
                project.slug,
                PlanningArtifactCreate(
                    artifact_type=ArtifactType.WORLD_SPEC,
                    content={
                        "logline": "Same derived world.",
                        "_meta": {
                            "input_hash": "same-input",
                            "source_bound_expansion": "reviewed",
                            "source_bound_expansion_review": "passed",
                            "source_bound_expansion_review_receipt": reviewed.content["_meta"][
                                "source_bound_expansion_review_receipt"
                            ],
                            "unrelated_runtime_stamp": "different-but-safe",
                        },
                    },
                ),
            )
            assert safe_reuse.id == reviewed.id
        finally:
            await session.close()
            await transaction.rollback()
    await engine.dispose()
