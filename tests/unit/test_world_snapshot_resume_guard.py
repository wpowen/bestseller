from types import SimpleNamespace
from uuid import uuid4

import pytest

from bestseller.domain.enums import ArtifactType
from bestseller.services import pipelines
from bestseller.services.book_design import assert_current_world_snapshot

pytestmark = pytest.mark.unit


def project(*, snapshot_hash: str | None = "current"):
    metadata = {}
    if snapshot_hash is not None:
        metadata["book_design_snapshot_hash"] = snapshot_hash
    return SimpleNamespace(id=uuid4(), metadata_json=metadata)


def project_with_metadata(metadata):
    return SimpleNamespace(id=uuid4(), metadata_json=metadata)


def artifact(*, source_hash: str = "current", status: str = "approved"):
    return SimpleNamespace(
        status=status,
        content={"_meta": {"source_snapshot_hash": source_hash}},
    )


@pytest.mark.parametrize(
    "case, expected",
    [
        ("missing", "WORLD_SPEC_ARTIFACT_MISSING"),
        ("stale", "WORLD_SPEC_SNAPSHOT_MISMATCH"),
        ("missing_lineage", "WORLD_SPEC_LINEAGE_MISSING"),
        ("unapproved", "WORLD_SPEC_ARTIFACT_NOT_APPROVED"),
    ],
)
def test_snapshot_bound_project_rejects_unsafe_world_artifact(case, expected):
    current = project()
    if case == "missing":
        candidate = None
    elif case == "stale":
        candidate = artifact(source_hash="old")
    elif case == "missing_lineage":
        candidate = SimpleNamespace(status="approved", content={})
    else:
        candidate = artifact(status="draft")
    with pytest.raises(ValueError, match=expected):
        assert_current_world_snapshot(current, candidate)
    assert current.metadata_json == {"book_design_snapshot_hash": "current"}


def test_snapshot_bound_project_accepts_matching_approved_artifact_without_mutation():
    current = project()
    candidate = artifact()
    returned = assert_current_world_snapshot(current, candidate)
    assert returned is candidate
    assert current.metadata_json == {"book_design_snapshot_hash": "current"}


@pytest.mark.parametrize(
    "metadata, expected",
    [
        ({"book_design_snapshot": {}}, "WORLD_SPEC_SNAPSHOT_HASH_MISSING"),
        ({"book_design_snapshot": "corrupt"}, "WORLD_SPEC_SNAPSHOT_HASH_MISSING"),
        ({"book_design_snapshot_hash": 123}, "WORLD_SPEC_SNAPSHOT_HASH_MISSING"),
        ({"book_design_snapshot_hash": ""}, "WORLD_SPEC_SNAPSHOT_HASH_MISSING"),
        ({"creation_intent_contract": {}}, "WORLD_SPEC_SNAPSHOT_HASH_MISSING"),
        ({"book_design_snapshot_id": "snapshot-id"}, "WORLD_SPEC_SNAPSHOT_HASH_MISSING"),
        ({"book_design_snapshot_status": "locked"}, "WORLD_SPEC_SNAPSHOT_HASH_MISSING"),
    ],
)
def test_malformed_snapshot_metadata_is_not_treated_as_legacy(metadata, expected):
    with pytest.raises(ValueError, match=expected):
        assert_current_world_snapshot(project_with_metadata(metadata), artifact())


@pytest.mark.parametrize(
    "content, expected",
    [
        (None, "WORLD_SPEC_ARTIFACT_INVALID"),
        ({"_meta": {"source_snapshot_hash": 123}}, "WORLD_SPEC_LINEAGE_MISSING"),
        ({"_meta": {"source_snapshot_hash": ""}}, "WORLD_SPEC_LINEAGE_MISSING"),
    ],
)
def test_invalid_content_or_lineage_fails_closed(content, expected):
    candidate = SimpleNamespace(status="approved", content=content)
    with pytest.raises(ValueError, match=expected):
        assert_current_world_snapshot(project(), candidate)


def test_legacy_project_keeps_previous_behavior():
    candidate = artifact(source_hash="anything", status="draft")
    assert assert_current_world_snapshot(project(snapshot_hash=None), candidate) is candidate
    assert assert_current_world_snapshot(project(snapshot_hash=None), None) is None


@pytest.mark.asyncio
async def test_loader_guards_before_caller_can_consume_stale_payload(monkeypatch):
    current = project()
    stale = artifact(source_hash="old")

    calls = []

    async def load(*args, **kwargs):
        calls.append(kwargs)
        return stale

    monkeypatch.setattr(pipelines, "get_latest_approved_planning_artifact", load)
    with pytest.raises(ValueError, match="WORLD_SPEC_SNAPSHOT_MISMATCH"):
        await pipelines._get_current_world_spec_artifact(None, project=current)
    assert calls == [{"project_id": current.id, "artifact_type": ArtifactType.WORLD_SPEC}]


@pytest.mark.asyncio
async def test_loader_passes_matching_binding_to_caller(monkeypatch):
    current = project()
    matching = artifact()

    calls = []

    async def load(*args, **kwargs):
        calls.append(kwargs)
        return matching

    monkeypatch.setattr(pipelines, "get_latest_approved_planning_artifact", load)
    assert await pipelines._get_current_world_spec_artifact(None, project=current) is matching
    assert calls == [{"project_id": current.id, "artifact_type": ArtifactType.WORLD_SPEC}]
