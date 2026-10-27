"""Real SQLite cohort selection excludes later approved rows from failed consumers."""
from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

import pytest
from test_retained_planning_generation import case as case  # noqa: F401
from test_retained_planning_generation import snapshot

from bestseller.domain.book_design_snapshot import build_book_design_snapshot
from bestseller.domain.enums import ArtifactType
from bestseller.infra.db.models import PlanningArtifactVersionModel, WorkflowRunModel
from bestseller.services import foundation_selection as selection
from bestseller.services import planner, workflows

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]
KINDS = tuple(ArtifactType(kind) for kind in selection.KINDS)


@pytest.fixture
async def selected(case):  # noqa: F811
    rig = case
    design = build_book_design_snapshot(
        creation_intent={"genre_intent": {"genre_key": "mystery", "genre_label": "Mystery",
            "source": "legacy_inferred"}, "chapter_count": rig.project.target_chapters, "language": "en"},
        protagonist="Mara", tone="measured",
        target_words=rig.project.target_word_count, chapter_count=rig.project.target_chapters,
    )
    rig.project.metadata_json = {
        **rig.project.metadata_json, "premise": "Scientists inspect measurement errors",
        "book_design_snapshot": design.model_dump(mode="json"),
        "book_design_snapshot_hash": design.source_hash,
        "book_design_snapshot_id": design.snapshot_id,
    }
    lineage = planner.planning_artifact_meta(rig.project)
    payloads = {
        "book_spec": {"title": "Observatory", "theme_statement": "Publish the error",
                      "protagonist": {"name": "Mara"}, "_meta": deepcopy(lineage)},
        "world_spec": {"world_name": "Observatory", "world_premise": "Compare dated measurements",
                       "hard_rules": [{"rule": "Sealed records retain their original dates"}],
                       "_meta": deepcopy(lineage)},
        "cast_spec": {"protagonist": {"name": "Mara", "goal": "Publish the error"},
                      "_meta": deepcopy(lineage)},
    }
    projection = planner._compile_source_bound_volume_plan(
        rig.project, rig.project.metadata_json["premise"],
        payloads["book_spec"], payloads["world_spec"], payloads["cast_spec"],
    )
    volume = deepcopy(projection)
    for item in volume:
        item.update(volume_title="Measurement season", volume_goal="Recheck the sealed records")
        item["_meta"] = {**item.get("_meta", {}), **lineage,
                         "source_projection_sha256": planner._approved_payload_hash(projection)}
    payloads["volume_plan"] = volume
    rows = {}
    for kind, value in payloads.items():
        row = PlanningArtifactVersionModel(
            project_id=rig.project.id, artifact_type=kind, status="approved",
            version_no=2, schema_version="fixture.v1", content=value,
            source_run_id=rig.original.id,
        )
        rig.session.add(row)
        rows[kind] = row
    await rig.session.flush()
    rig.consumer.status = "completed"
    rig.consumer.scope_type, rig.consumer.scope_id = "project", rig.project.id
    rig.consumer.metadata_json = {**rig.consumer.metadata_json,
        "artifact_ids": {kind: str(row.id) for kind, row in rows.items()}}
    newer_failed = WorkflowRunModel(
        project_id=rig.project.id, workflow_type="generate_foundation_plan",
        scope_type="project", scope_id=rig.project.id, status="failed", metadata_json={},
    )
    rig.session.add(newer_failed)
    await rig.session.flush()
    newer = {}
    for kind in ("book_spec", "world_spec"):
        value = deepcopy(payloads[kind])
        value["later_failed_workflow_marker"] = "must remain archived, never mixed into this selection"
        row = PlanningArtifactVersionModel(
            project_id=rig.project.id, artifact_type=kind, version_no=3, status="approved",
            schema_version="fixture.v1", content=value, source_run_id=newer_failed.id,
        )
        rig.session.add(row)
        newer[kind] = row
    await rig.session.commit()
    await selection.pin_completed_foundation(rig.session, project=rig.project, workflow_id=rig.consumer.id)
    await rig.session.commit()
    rig.selected, rig.newer, rig.newer_failed = rows, newer, newer_failed
    return rig


async def get(rig, kind, **kwargs):
    return await workflows.get_latest_approved_planning_artifact(
        rig.session, project_id=rig.project.id, artifact_type=ArtifactType(kind), **kwargs,
    )


async def test_all_four_consumers_use_exact_completed_cohort_and_preserve_newer_failed_history(selected):
    rig = selected
    before = await snapshot(rig)
    for kind in KINDS:
        assert (await get(rig, kind)).id == rig.selected[kind.value].id
    for kind, row in rig.newer.items():
        latest = await workflows.get_latest_planning_artifact(
            rig.session, project_id=rig.project.id, artifact_type=ArtifactType(kind),
        )
        assert latest.id == row.id != rig.selected[kind].id
    assert rig.original.status == rig.newer_failed.status == "failed"
    assert await snapshot(rig) == before
    assert rig.denied == []


@pytest.mark.parametrize("fault", ["null_pointer", "empty_pointer", "failed_consumer", "different_ids",
                                    "content_changed", "canonical_changed"])
async def test_invalid_selected_pointer_or_cohort_never_falls_back_to_newest(selected, monkeypatch, fault):
    rig = selected
    if fault in {"null_pointer", "empty_pointer"}:
        rig.project.metadata_json = {**rig.project.metadata_json, selection.POINTER: None if fault == "null_pointer" else {}}
    elif fault == "failed_consumer": rig.consumer.status = "failed"
    elif fault == "different_ids":
        rig.consumer.metadata_json = {**rig.consumer.metadata_json, "artifact_ids": {
            **rig.consumer.metadata_json["artifact_ids"], "world_spec": str(rig.newer["world_spec"].id)}}
    elif fault == "content_changed":
        rig.selected["world_spec"].content = {**rig.selected["world_spec"].content, "new_fact": "different"}
    else:
        rig.project.metadata_json = {**rig.project.metadata_json, "creation_canonical_snapshot_hash": "0" * 64}
    await rig.session.commit()
    before = await snapshot(rig)
    async def forbidden(*args, **kwargs):
        pytest.fail("invalid selection must fail before materialization")
    monkeypatch.setattr(workflows, "materialize_story_bible", forbidden)
    with pytest.raises(ValueError, match="foundation_selection"):
        await get(rig, "book_spec")
    with pytest.raises(ValueError, match="foundation_selection"):
        await workflows.materialize_latest_story_bible(rig.session, rig.project.slug)
    assert await snapshot(rig) == before


async def test_scope_filter_cannot_select_another_asset_or_fall_back(selected):
    rig = selected
    with pytest.raises(ValueError, match="foundation_selection_scope_mismatch"):
        await get(rig, "world_spec", scope_ref_id=uuid4())
    assert (await get(rig, "world_spec", scope_ref_id=None)).id == rig.selected["world_spec"].id


async def test_absent_pointer_keeps_legacy_latest_and_nonfoundation_kind_ignores_pointer(selected):
    rig = selected
    metadata = deepcopy(rig.project.metadata_json)
    metadata.pop(selection.POINTER)
    rig.project.metadata_json = metadata
    await rig.session.commit()
    assert (await get(rig, "book_spec")).id == rig.newer["book_spec"].id
    row = PlanningArtifactVersionModel(
        project_id=rig.project.id, artifact_type="chapter_outline_batch", version_no=1,
        status="approved", schema_version="fixture.v1", content={"chapters": []},
    )
    rig.session.add(row)
    rig.project.metadata_json = {**rig.project.metadata_json, selection.POINTER: {"malformed": True}}
    await rig.session.commit()
    assert (await get(rig, "chapter_outline_batch")).id == row.id


async def test_latest_materializer_passes_one_exact_tuple_and_never_independent_latest_rows(selected, monkeypatch):
    rig = selected
    before = await snapshot(rig)
    result = SimpleNamespace(bound=True)
    async def materialize(session, slug, **kwargs):
        assert session is rig.session and slug == rig.project.slug
        assert kwargs["source_artifact_ids"] == {kind: row.id for kind, row in rig.selected.items()}
        assert {kind: kwargs[kind + "_content"] for kind in selection.KINDS} == {
            kind: row.content for kind, row in rig.selected.items()}
        return result
    monkeypatch.setattr(workflows, "materialize_story_bible", materialize)
    assert await workflows.materialize_latest_story_bible(rig.session, rig.project.slug) is result
    assert await snapshot(rig) == before


@pytest.mark.parametrize("fault", ["wrong_id", "wrong_payload", "missing_id", "extra_id"])
async def test_direct_materializer_refuses_bypass_before_audit_or_any_stage(selected, monkeypatch, fault):
    rig = selected
    payload = deepcopy(rig.selected["world_spec"].content)
    ids = {"world_spec": rig.selected["world_spec"].id}
    if fault == "wrong_id": ids["world_spec"] = rig.newer["world_spec"].id
    elif fault == "wrong_payload": payload = deepcopy(rig.newer["world_spec"].content)
    elif fault == "missing_id": ids = {}
    else: ids["book_spec"] = rig.selected["book_spec"].id
    def forbidden(*args, **kwargs):
        pytest.fail("wrong explicit selection reached a materialization stage")
    monkeypatch.setattr(workflows, "_audit_bible_completeness", forbidden)
    monkeypatch.setattr(workflows, "create_workflow_run", forbidden)
    before = await snapshot(rig)
    with pytest.raises(ValueError, match="foundation_selection_materialization_binding_mismatch"):
        await workflows.materialize_story_bible(
            rig.session, rig.project.slug, world_spec_content=payload, source_artifact_ids=ids,
        )
    assert await snapshot(rig) == before


async def test_direct_valid_selected_subset_reaches_unchanged_original_audit(selected, monkeypatch):
    rig = selected
    class AuditReached(BaseException):
        pass
    def audit(**kwargs):
        assert kwargs["world_spec_content"] == rig.selected["world_spec"].content
        assert kwargs["book_spec_content"] is kwargs["cast_spec_content"] is None
        raise AuditReached
    monkeypatch.setattr(workflows, "_audit_bible_completeness", audit)
    before = await snapshot(rig)
    with pytest.raises(AuditReached):
        await workflows.materialize_story_bible(
            rig.session, rig.project.slug,
            world_spec_content=deepcopy(rig.selected["world_spec"].content),
            source_artifact_ids={"world_spec": rig.selected["world_spec"].id},
        )
    assert await snapshot(rig) == before
