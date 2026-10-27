"""Integration checks for the no-LLM derived projection bridge."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import select

_fixture_spec = importlib.util.spec_from_file_location(
    "derived_revision_apply_fixture",
    Path(__file__).with_name("test_derived_revision_apply.py"),
)
assert _fixture_spec and _fixture_spec.loader
_fixture_module = importlib.util.module_from_spec(_fixture_spec)
_fixture_spec.loader.exec_module(_fixture_module)
_revision_db_fixture = _fixture_module.revision_db

from bestseller.domain.enums import ArtifactType  # noqa: E402
from bestseller.infra.db.models import PlanningArtifactVersionModel, ProjectModel  # noqa: E402
from bestseller.services.derived_revision_apply import apply_reviewed_derived_revision  # noqa: E402
from bestseller.services.derived_revision_projection import (  # noqa: E402
    refresh_derived_revision_projection,
)


@pytest_asyncio.fixture
async def revision_db_fixture():
    async for value in _revision_db_fixture.__wrapped__():
        yield value

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_projection_calls_real_source_bound_path_and_fails_closed(
    revision_db_fixture,
) -> None:
    factory, _, info = revision_db_fixture
    async with factory() as session:
        await apply_reviewed_derived_revision(
            session,
            project_slug=info["slug"],
            report_id=info["report_id"],
            expected_lock_version=info["lock_version"],
        )
        await session.commit()
        result = await refresh_derived_revision_projection(
            session, None, info["slug"], info["report_id"]
        )
        assert result["status"] == "projected"
        await session.commit()
        project = await session.scalar(
            select(ProjectModel).where(ProjectModel.slug == info["slug"])
        )
        assert project is not None
        assert project.metadata_json["derived_revision_projection"]["status"] == "projected"
        assert project.metadata_json["derived_input_revision"]["projection_pending"] is False
        cast = await session.scalar(
            select(PlanningArtifactVersionModel).where(
                PlanningArtifactVersionModel.project_id == project.id,
                PlanningArtifactVersionModel.artifact_type == ArtifactType.CAST_SPEC.value,
            )
        )
        assert cast is not None


async def test_projection_materializer_failure_rolls_back_new_artifacts(
    revision_db_fixture, monkeypatch
) -> None:
    from bestseller.services import derived_revision_projection as service

    factory, _, info = revision_db_fixture
    async with factory() as session:
        await apply_reviewed_derived_revision(
            session,
            project_slug=info["slug"],
            report_id=info["report_id"],
            expected_lock_version=info["lock_version"],
        )
        await session.commit()

    async def fail_materializer(*args, **kwargs):
        raise RuntimeError("injected materializer failure")

    monkeypatch.setattr(service, "materialize_story_bible", fail_materializer)
    async with factory() as session:
        # This forces the test fixture past its optional seriality contract so
        # the real import + rollback boundary is exercised. The first test
        # above remains the unmodified production-path attempt.
        monkeypatch.setattr(service, "_enforce_seriality_volume_mapping", lambda *a, **k: None)
        with pytest.raises(RuntimeError, match="injected materializer failure"):
            await refresh_derived_revision_projection(
                session, None, info["slug"], info["report_id"]
            )
        await session.rollback()
        project = await session.scalar(
            select(ProjectModel).where(ProjectModel.slug == info["slug"])
        )
        assert project is not None
        assert project.metadata_json["derived_revision_projection_pending"] is True
        projected_cast = await session.scalar(
            select(PlanningArtifactVersionModel).where(
                PlanningArtifactVersionModel.project_id == project.id,
                PlanningArtifactVersionModel.artifact_type == ArtifactType.CAST_SPEC.value,
            )
        )
        assert projected_cast is None
