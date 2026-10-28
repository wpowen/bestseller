"""RED for a nullable native outline producer ID, not native positive acceptance.

Component packets below contain synthetic workflow identity exports only. They
have no admission grade, adopted prose, paid result or complete-reader success.
Passing a component check is NOT a complete native positive or authorization.
The SQLite primitive calls the real normal producer; it does not manufacture
materialization, admission, promoted drafts or grades. Main runs these tests.
"""

from copy import deepcopy
from types import SimpleNamespace
from uuid import UUID, NAMESPACE_URL, uuid5

import pytest
from sqlalchemy import select
from test_native_title_sources import native_source_store  # noqa: F401 -- shared real SQLite fixture

from bestseller.infra.db import models as db
from bestseller.services.fanqie_short_planner import persist_fanqie_chapter_outline
from bestseller.services.materialized_outline_source_recovery import SCHEMA, admission_workflow_id
from bestseller.services.native_title_operator import title_operator as op

pytestmark = pytest.mark.unit


def identity(label):
    return str(uuid5(NAMESPACE_URL, "bestseller:null-outline-component:" + label))


def component_packet(count=8):
    """Identity consistency inputs only; no native provenance/grade is asserted."""
    pid, aid, mid = identity("project"), identity("artifact"), identity("materializer")
    admission_id = str(admission_workflow_id(UUID(pid), UUID(aid)))
    project = SimpleNamespace(id=pid, target_chapters=count,
        metadata_json={"execution_scope_id": "project:" + pid})
    content = {"batch_name": "component-only", "chapters": [
        {"chapter_number": n, "chapter_goal": f"Read observation {n}",
         "scenes": [{"scene_number": 1, "purpose": {"story": f"Inspect record {n}"}}]}
        for n in range(1, count + 1)
    ]}
    binding = {
        "source_artifact_id": aid, "source_content_sha256": op.fingerprint(content),
        "candidate_hash": op.fingerprint(content), "materialization_workflow_run_id": mid,
        "chapter_projection_hashes": {identity(f"chapter-{n}"): op.fingerprint(chapter)
            for n, chapter in enumerate(content["chapters"], 1)},
        "foundation": {kind: {"id": identity(kind), "content_hash": op.fingerprint({"kind": kind})}
            for kind in ("book_spec", "world_spec", "cast_spec", "volume_plan")},
        "canonical_id": identity("canonical"), "canonical_hash": op.fingerprint({"count": count}),
        "historical_editorial_observations": {
            "outline_llm_commercial_judge": None, "outline_reader_experience_judge": None},
    }
    # Existing complete-plan return fields, projected without inventing a
    # producer run. Actual acquisition must obtain this from the real reader.
    receipt = {
        "schema": "fanqie-materialized-plan-reuse.v1",
        "planning_workflow_run_id": identity("foundation-owner"),
        "outline_workflow_run_id": mid, "hard_admission_workflow_run_id": admission_id,
        "source_artifact_id": aid, "source_binding": deepcopy(binding),
        "foundation_artifact_ids": {kind: value["id"] for kind, value in binding["foundation"].items()},
        "story_bible_workflow_run_id": None, "narrative_graph_workflow_run_id": None,
    }
    materializer = {
        "id": mid, "project_id": pid, "workflow_type": "materialize_chapter_outline_batch",
        "status": "completed", "scope_type": "planning_artifact", "scope_id": aid,
        "metadata_json": {"source_artifact_id": aid},
    }
    admission = {
        "id": admission_id, "project_id": pid, "workflow_type": "materialized_outline_source_admission",
        "status": "completed", "scope_type": "planning_artifact", "scope_id": aid,
        "metadata_json": {"schema": SCHEMA, **deepcopy(binding)},
    }
    packet = {
        "id": aid, "project_id": pid, "artifact_type": "chapter_outline_batch", "status": "approved",
        "source_run_id": None, "content": content, "source_binding": deepcopy(binding),
        "complete_plan_receipt": receipt, "materialization_workflow": materializer,
        "admission_workflow": admission,
    }
    return project, packet


@pytest.mark.parametrize("count", range(4, 9))
def test_component_null_producer_binds_independent_native_workflow_identities(count):
    """RED: necessary component consistency, never a complete native positive."""
    project, packet = component_packet(count)
    before = deepcopy((vars(project), packet))
    result = op._current_project_plan(project, packet)
    assert result[0] == project.id and result[1] == count
    assert packet["source_run_id"] is None
    assert (vars(project), packet) == before


@pytest.mark.parametrize("fault", [
    "missing_receipt", "missing_binding", "missing_materializer", "missing_admission",
    "receipt_artifact", "receipt_outline", "receipt_admission", "binding_materializer",
    "binding_source_hash", "materializer_project", "materializer_type", "materializer_state",
    "materializer_scope", "materializer_source", "admission_id", "admission_project",
    "admission_type", "admission_state", "admission_scope", "admission_binding",
])
def test_component_null_producer_does_not_turn_unbound_workflow_data_into_authority(fault):
    project, packet = component_packet()
    if fault == "missing_receipt":
        packet.pop("complete_plan_receipt")
    elif fault == "missing_binding":
        packet.pop("source_binding")
    elif fault == "missing_materializer":
        packet.pop("materialization_workflow")
    elif fault == "missing_admission":
        packet.pop("admission_workflow")
    elif fault == "receipt_artifact":
        packet["complete_plan_receipt"]["source_artifact_id"] = identity("other-artifact")
    elif fault == "receipt_outline":
        packet["complete_plan_receipt"]["outline_workflow_run_id"] = identity("other-owner")
    elif fault == "receipt_admission":
        packet["complete_plan_receipt"]["hard_admission_workflow_run_id"] = identity("other-admission")
    elif fault == "binding_materializer":
        packet["source_binding"]["materialization_workflow_run_id"] = identity("other-owner")
    elif fault == "binding_source_hash":
        packet["source_binding"]["source_content_sha256"] = "0" * 64
    elif fault.startswith("materializer_"):
        row = packet["materialization_workflow"]
        if fault == "materializer_project":
            row["project_id"] = identity("other-project")
        elif fault == "materializer_type":
            row["workflow_type"] = "unrelated_workflow"
        elif fault == "materializer_state":
            row["status"] = "running"
        elif fault == "materializer_scope":
            row["scope_id"] = identity("other-artifact")
        else:
            row["metadata_json"]["source_artifact_id"] = identity("other-artifact")
    else:
        row = packet["admission_workflow"]
        if fault == "admission_id":
            row["id"] = identity("other-admission")
        elif fault == "admission_project":
            row["project_id"] = identity("other-project")
        elif fault == "admission_type":
            row["workflow_type"] = "unrelated_workflow"
        elif fault == "admission_state":
            row["status"] = "failed"
        elif fault == "admission_scope":
            row["scope_type"] = "project"
        else:
            row["metadata_json"]["source_content_sha256"] = "0" * 64
    with pytest.raises(ValueError):
        op._current_project_plan(project, packet)
    assert packet["source_run_id"] is None


@pytest.mark.parametrize("bad", ["", "not-a-uuid", "00000000-0000-0000-0000-000000000000"])
def test_component_malformed_nonnull_producer_does_not_fall_back_to_null_route(bad):
    project, packet = component_packet()
    packet["source_run_id"] = bad
    with pytest.raises(ValueError):
        op._current_project_plan(project, packet)


def test_component_existing_nonnull_producer_identity_remains_literal():
    project, packet = component_packet()
    packet["source_run_id"] = identity("retained-real-producer-identity")
    for key in ("complete_plan_receipt", "materialization_workflow", "admission_workflow"):
        packet.pop(key)
    before = deepcopy(packet)
    assert op._current_project_plan(project, packet)[0] == project.id
    assert packet == before


@pytest.mark.asyncio
async def test_normal_sqlite_producer_really_leaves_run_id_null_without_native_acceptance(native_source_store):
    """Actual producer primitive only: no materialization/admission/body grade."""
    case = native_source_store
    async with case.factory() as session:
        project = await session.get(db.ProjectModel, case.project_id)
        before_workflows = set((await session.scalars(select(db.WorkflowRunModel.id))).all())
        outline = {"batch_name": "normal-native-producer", "chapters": [
            {"chapter_number": n, "chapter_goal": f"Inspect station {n}", "target_word_count": 2000,
             "scenes": [{"scene_number": 1, "purpose": {"story": f"Read station record {n}"}}]}
            for n in range(1, project.target_chapters + 1)
        ]}
        artifact_id = await persist_fanqie_chapter_outline(session, project.slug, outline)
        artifact = await session.get(db.PlanningArtifactVersionModel, artifact_id)
        assert artifact.project_id == project.id and artifact.artifact_type == "chapter_outline_batch"
        assert artifact.source_run_id is None
        assert [c["chapter_number"] for c in artifact.content["chapters"]] == list(range(1, 9))
        assert set((await session.scalars(select(db.WorkflowRunModel.id))).all()) == before_workflows
        assert not (await session.scalars(select(db.ChapterModel))).all()
        assert not (await session.scalars(select(db.ChapterDraftVersionModel))).all()
