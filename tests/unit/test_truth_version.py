"""Unit tests for ``truth_version`` — first-seed bump safety.

The critical invariant under test: on the very first seeding of a core truth
artifact (no prior fingerprint recorded), ``maybe_bump_project_truth_version``
must NOT set ``truth_updated_at``. Otherwise every historical materialization
run would appear stale and
``assert_truth_materializations_fresh`` would falsely block the first draft.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from bestseller.domain.enums import ArtifactType
from bestseller.infra.db.models import ProjectModel
from bestseller.services.truth_version import (
    initialize_truth_metadata,
    maybe_bump_project_truth_version,
    truth_state_from_project,
)

pytestmark = pytest.mark.unit


def _build_project(metadata: dict | None = None) -> ProjectModel:
    project = ProjectModel(
        slug="blood-twins",
        title="Blood Twins",
        genre="fantasy",
        target_word_count=120000,
        target_chapters=60,
        language="zh-CN",
        metadata_json=metadata if metadata is not None else {},
    )
    project.id = uuid4()
    return project


# ---------------------------------------------------------------------------
# initialize_truth_metadata
# ---------------------------------------------------------------------------


def test_initialize_truth_metadata_fills_defaults() -> None:
    payload = initialize_truth_metadata(None)
    assert payload["truth_version"] == 1
    assert payload["truth_updated_at"] is None
    assert payload["truth_last_changed_artifact_type"] is None
    assert payload["_truth_artifact_fingerprints"] == {}
    assert payload["_truth_change_log"] == []


def test_initialize_truth_metadata_preserves_existing_values() -> None:
    payload = initialize_truth_metadata(
        {
            "truth_version": 3,
            "truth_updated_at": "2026-01-01T00:00:00+00:00",
            "_truth_artifact_fingerprints": {"premise": "abcd"},
            "_truth_change_log": [{"truth_version": 2}],
        }
    )
    assert payload["truth_version"] == 3
    assert payload["truth_updated_at"] == "2026-01-01T00:00:00+00:00"
    assert payload["_truth_artifact_fingerprints"] == {"premise": "abcd"}
    assert payload["_truth_change_log"] == [{"truth_version": 2}]


# ---------------------------------------------------------------------------
# First-seed path — the core fix
# ---------------------------------------------------------------------------


def test_first_seed_records_fingerprint_but_leaves_updated_at_none() -> None:
    project = _build_project()

    bumped = maybe_bump_project_truth_version(
        project,
        artifact_type=ArtifactType.PREMISE,
        content={"logline": "A tale of two twins"},
    )

    # Must NOT report a bump, and must NOT touch truth_updated_at.
    assert bumped is False
    assert project.metadata_json["truth_version"] == 1
    assert project.metadata_json["truth_updated_at"] is None
    assert project.metadata_json["truth_last_changed_artifact_type"] is None

    # Fingerprint is recorded so future changes can detect drift.
    fingerprints = project.metadata_json["_truth_artifact_fingerprints"]
    assert ArtifactType.PREMISE.value in fingerprints
    assert fingerprints[ArtifactType.PREMISE.value]  # non-empty sha256

    # Change log must stay empty; first seed is not a change.
    assert project.metadata_json["_truth_change_log"] == []


def test_first_seed_of_each_core_artifact_each_leaves_updated_at_none() -> None:
    project = _build_project()

    for artifact_type, content in (
        (ArtifactType.PREMISE, {"logline": "x"}),
        (ArtifactType.BOOK_SPEC, {"title": "x"}),
        (ArtifactType.WORLD_SPEC, {"geography": "x"}),
        (ArtifactType.CAST_SPEC, {"protagonist": "x"}),
        (ArtifactType.VOLUME_PLAN, {"volumes": []}),
        (ArtifactType.ACT_PLAN, {"acts": []}),
    ):
        bumped = maybe_bump_project_truth_version(
            project,
            artifact_type=artifact_type,
            content=content,
        )
        assert bumped is False, f"{artifact_type.value} should be first-seed"

    # Even after seeding all six core artifacts, timestamp stays None.
    assert project.metadata_json["truth_updated_at"] is None
    assert project.metadata_json["truth_last_changed_artifact_type"] is None
    assert project.metadata_json["truth_version"] == 1


def test_non_core_artifact_does_not_record_fingerprint() -> None:
    project = _build_project()

    bumped = maybe_bump_project_truth_version(
        project,
        artifact_type=ArtifactType.CHAPTER_OUTLINE_BATCH,
        content={"chapters": []},
    )

    assert bumped is False
    assert project.metadata_json["_truth_artifact_fingerprints"] == {}
    assert project.metadata_json["truth_updated_at"] is None


# ---------------------------------------------------------------------------
# Second change — the real bump path
# ---------------------------------------------------------------------------


def test_second_change_bumps_version_and_sets_timestamp() -> None:
    project = _build_project()

    # First seed — no bump, no timestamp.
    maybe_bump_project_truth_version(
        project,
        artifact_type=ArtifactType.PREMISE,
        content={"logline": "v1"},
    )
    assert project.metadata_json["truth_updated_at"] is None

    # Second write with different content — this IS a change.
    bumped = maybe_bump_project_truth_version(
        project,
        artifact_type=ArtifactType.PREMISE,
        content={"logline": "v2 rewritten"},
    )
    assert bumped is True
    assert project.metadata_json["truth_version"] == 2
    assert project.metadata_json["truth_updated_at"] is not None
    assert (
        project.metadata_json["truth_last_changed_artifact_type"]
        == ArtifactType.PREMISE.value
    )

    # Change log now has one entry.
    log = project.metadata_json["_truth_change_log"]
    assert len(log) == 1
    assert log[0]["truth_version"] == 2
    assert log[0]["artifact_type"] == ArtifactType.PREMISE.value


def test_rewrite_with_identical_content_does_not_bump() -> None:
    project = _build_project()

    maybe_bump_project_truth_version(
        project,
        artifact_type=ArtifactType.PREMISE,
        content={"logline": "same"},
    )
    # Rewrite with exact same content — fingerprint matches previous.
    bumped = maybe_bump_project_truth_version(
        project,
        artifact_type=ArtifactType.PREMISE,
        content={"logline": "same"},
    )
    assert bumped is False
    assert project.metadata_json["truth_version"] == 1
    assert project.metadata_json["truth_updated_at"] is None


def test_truth_state_from_project_after_first_seed() -> None:
    project = _build_project()
    maybe_bump_project_truth_version(
        project,
        artifact_type=ArtifactType.PREMISE,
        content={"logline": "v1"},
    )

    state = truth_state_from_project(project)
    assert state.version == 1
    assert state.updated_at is None
    assert state.last_changed_artifact_type is None
    assert ArtifactType.PREMISE.value in state.fingerprints


def test_truth_state_after_real_bump_reports_timestamp() -> None:
    project = _build_project()
    maybe_bump_project_truth_version(
        project,
        artifact_type=ArtifactType.PREMISE,
        content={"logline": "v1"},
    )
    maybe_bump_project_truth_version(
        project,
        artifact_type=ArtifactType.BOOK_SPEC,
        content={"title": "Blood Twins"},
    )
    maybe_bump_project_truth_version(
        project,
        artifact_type=ArtifactType.BOOK_SPEC,
        content={"title": "Blood Twins Revised"},
    )

    state = truth_state_from_project(project)
    assert state.version == 2
    assert state.updated_at is not None
    assert state.last_changed_artifact_type == ArtifactType.BOOK_SPEC.value


def test_scope_ref_id_keys_are_distinct() -> None:
    project = _build_project()
    scope_a = uuid4()
    scope_b = uuid4()

    # First seed for scope A — no bump.
    assert (
        maybe_bump_project_truth_version(
            project,
            artifact_type=ArtifactType.VOLUME_PLAN,
            content={"volumes": ["A"]},
            scope_ref_id=scope_a,
        )
        is False
    )
    # First seed for scope B — also no bump (distinct key).
    assert (
        maybe_bump_project_truth_version(
            project,
            artifact_type=ArtifactType.VOLUME_PLAN,
            content={"volumes": ["B"]},
            scope_ref_id=scope_b,
        )
        is False
    )

    # Timestamp is still None: both were first-seed.
    assert project.metadata_json["truth_updated_at"] is None

    fingerprints = project.metadata_json["_truth_artifact_fingerprints"]
    assert f"{ArtifactType.VOLUME_PLAN.value}:{scope_a}" in fingerprints
    assert f"{ArtifactType.VOLUME_PLAN.value}:{scope_b}" in fingerprints
