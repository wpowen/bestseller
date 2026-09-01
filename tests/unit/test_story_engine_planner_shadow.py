from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from bestseller.domain.enums import ArtifactType
from bestseller.services import planner


@pytest.mark.asyncio
async def test_planner_shadow_records_non_authoritative_artifact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact = SimpleNamespace(
        id=uuid4(),
        version_no=3,
        status="needs_replan",
        content={
            "projection_status": "needs_replan",
            "blocking_codes": ["LEGACY_PREMIUM_STATE_SNAPSHOT_MISSING"],
            "can_drive_generation": False,
        },
    )

    async def fake_persist(*args, **kwargs):
        return artifact

    monkeypatch.setattr(planner, "persist_legacy_story_engine_shadow", fake_persist)
    project = SimpleNamespace(id=uuid4(), metadata_json={})
    records = []

    result = await planner._persist_story_engine_shadow_advisory(
        object(),
        settings=SimpleNamespace(
            pipeline=SimpleNamespace(enable_story_engine_shadow=True)
        ),
        project=project,
        story_design_kernel={"reader_promise": "test"},
        workflow_run_id=uuid4(),
        artifact_records=records,
    )

    assert result is artifact
    assert records[0].artifact_type is ArtifactType.STORY_ENGINE_V2
    assert project.metadata_json["story_engine_shadow"]["can_drive_generation"] is False
    assert project.metadata_json["story_engine_shadow"]["status"] == "needs_replan"


@pytest.mark.asyncio
async def test_planner_shadow_failure_does_not_block_legacy_planning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def broken_persist(*args, **kwargs):
        raise RuntimeError("shadow storage unavailable")

    monkeypatch.setattr(planner, "persist_legacy_story_engine_shadow", broken_persist)
    project = SimpleNamespace(id=uuid4(), metadata_json={})
    records = []

    result = await planner._persist_story_engine_shadow_advisory(
        object(),
        settings=SimpleNamespace(
            pipeline=SimpleNamespace(enable_story_engine_shadow=True)
        ),
        project=project,
        story_design_kernel={"reader_promise": "test"},
        workflow_run_id=uuid4(),
        artifact_records=records,
    )

    assert result is None
    assert records == []
    assert project.metadata_json["story_engine_shadow"]["status"] == "shadow_error"
    assert project.metadata_json["story_engine_shadow"]["can_drive_generation"] is False


@pytest.mark.asyncio
async def test_planner_shadow_can_be_disabled_without_side_effects() -> None:
    project = SimpleNamespace(id=uuid4(), metadata_json={})
    records = []

    result = await planner._persist_story_engine_shadow_advisory(
        object(),
        settings=SimpleNamespace(
            pipeline=SimpleNamespace(enable_story_engine_shadow=False)
        ),
        project=project,
        story_design_kernel={"reader_promise": "test"},
        workflow_run_id=uuid4(),
        artifact_records=records,
    )

    assert result is None
    assert records == []
    assert project.metadata_json == {}
