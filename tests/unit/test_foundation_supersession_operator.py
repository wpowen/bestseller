"""Immutable history, exact selector association and original generation proof.

These are offline operator fixtures, not production/PG acceptance.
"""
import copy
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid5, NAMESPACE_URL

import pytest
from bestseller.services.source_bound_planning_content import CONTENT_POLICY, PLANNING_PROMPT_VERSION
from bestseller.services.source_bound_planning_submission import parse_planning_submission

PATH = Path(__file__).resolve().parents[2] / "reports/continuous-recovery-2026-10-16/advance_fresh_acceptance.py"
SPEC = importlib.util.spec_from_file_location("foundation_supersession_operator", PATH)
operator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(operator)


def uid(label):
    return str(uuid5(NAMESPACE_URL, label))


PROJECT, SELECTOR, ORIGINAL = map(uid, ("project", "selector", "original-failed"))


def workflow(identity, status="completed"):
    return {"id": identity, "project_id": PROJECT,
            "workflow_type": "generate_foundation_plan", "status": status}


def submission(kind):
    payload = {
        "book_spec": {"theme_statement": "An observatory chooses to publish its error."},
        "world_spec": {"world_name": "Observatory"},
        "cast_spec": {"protagonist": {"name": "Mara"}},
        "volume_plan": [{"volume_number": 1, "volume_goal": "Recheck the measurement"}],
    }[kind]
    arguments = (json.dumps(payload, ensure_ascii=False) if kind in {"world_spec", "cast_spec"}
                 else json.dumps({"asset_json": json.dumps(payload, ensure_ascii=False)}))
    _, _, receipt = parse_planning_submission({"content": "", "tool_calls": [{
        "id": "call-" + kind, "type": "function", "function": {
            "name": "submit_" + kind, "arguments": arguments}}], "raw_message": None}, kind=kind)
    return payload, receipt


def snapshots():
    before = {"project_id": PROJECT, "artifact_history": {}, "foundation": {}}
    after = {"project_id": PROJECT, "artifact_history": {}, "foundation": {},
             "completed_foundation_workflows": [SELECTOR], "foundation_associations": {},
             "foundation_provenance": {}}
    for kind in operator.KINDS:
        old_id, new_id, llm_id = map(uid, ("old-" + kind, "new-" + kind, "llm-" + kind))
        row = {"id": old_id, "project_id": PROJECT, "artifact_type": kind,
               "content": {"old_template": True}, "source_run_id": None, "version_no": 1}
        before["artifact_history"][old_id] = copy.deepcopy(row)
        after["artifact_history"][old_id] = copy.deepcopy(row)
        before["foundation"][kind] = {"id": old_id, "version_no": 1,
                                      "hash": operator.digest(row["content"]), "source_run_id": None}
        payload, original = submission(kind)
        compact = {key: value for key, value in original.items()
                   if key not in {"raw_arguments", "original_content", "asset_json"}}
        meta = {"source_content_policy": CONTENT_POLICY, "generation_llm_run_id": llm_id,
                "planning_submission_receipt": {**compact, "workflow_run_id": SELECTOR,
                    "llm_run_id": llm_id, "original_receipt_sha256": operator.digest(original)}}
        items = payload if isinstance(payload, list) else [payload]
        for item in items:
            item["_meta"] = copy.deepcopy(meta)
        row = {"id": new_id, "project_id": PROJECT, "artifact_type": kind,
               "version_no": 2, "source_run_id": SELECTOR, "content": payload}
        after["artifact_history"][new_id] = row
        after["foundation"][kind] = {"id": new_id, "version_no": 2,
            "source_run_id": SELECTOR, "hash": operator.digest(payload)}
        after["foundation_provenance"][kind] = {
            "artifact_id": new_id, "artifact_sha256": operator.digest(payload),
            "artifact_row_sha256": operator.digest(row), "source_workflow": workflow(SELECTOR),
            "generation_workflow": workflow(SELECTOR),
            "llm_run": {"id": llm_id, "project_id": PROJECT, "workflow_run_id": SELECTOR,
                "logical_role": "planner", "provider": "openai", "model_name": "test-model",
                "prompt_template": "planner_" + kind, "prompt_version": PLANNING_PROMPT_VERSION,
                "prompt_hash": "a" * 64, "finish_reason": "tool_calls"},
            "submission_revalidated": True,
            "submission_receipt": compact, "submission_receipt_sha256": operator.digest(original),
        }
    after["foundation_associations"][SELECTOR] = {
        **workflow(SELECTOR), "artifact_ids": {kind: item["id"]
                                             for kind, item in after["foundation"].items()}}
    return before, after


def rebind_original(after, *, wrapper=False):
    """Keep the failed producer even when the row is a new current-WF wrapper."""
    selected = after["foundation"]["book_spec"]
    row = after["artifact_history"][selected["id"]]
    proof = after["foundation_provenance"]["book_spec"]
    if not wrapper:
        row["source_run_id"] = selected["source_run_id"] = ORIGINAL
        proof["source_workflow"] = workflow(ORIGINAL, "failed")
    proof["generation_workflow"] = workflow(ORIGINAL, "failed")
    proof["llm_run"]["workflow_run_id"] = ORIGINAL
    row["content"]["_meta"]["planning_submission_receipt"]["workflow_run_id"] = ORIGINAL
    selected["hash"] = proof["artifact_sha256"] = operator.digest(row["content"])
    proof["artifact_row_sha256"] = operator.digest(row)


def reuse_book(before, after):
    rebind_original(after)
    selected = after["foundation"]["book_spec"]
    before["foundation"]["book_spec"] = copy.deepcopy(selected)
    before["artifact_history"][selected["id"]] = copy.deepcopy(after["artifact_history"][selected["id"]])


def test_new_versions_preserve_every_old_row_without_regrading():
    before, after = snapshots()
    original = copy.deepcopy((before, after))
    receipt = operator.verify_foundation_supersession(before, after)
    assert receipt["old_rows_unchanged"] is True
    assert receipt["historical_quality_regraded"] is False
    assert receipt["reused_current_policy_kinds"] == []
    assert (before, after) == original


def test_current_book_v2_already_in_before_reuses_original_failed_producer():
    before, after = snapshots()
    reuse_book(before, after)
    original = copy.deepcopy((before, after))
    receipt = operator.verify_foundation_supersession(before, after)
    assert receipt["reused_current_policy_kinds"] == ["book_spec"]
    assert receipt["old_artifact_ids"]["book_spec"] == receipt["new_artifact_ids"]["book_spec"]
    binding = receipt["generation_bindings"]["book_spec"]
    assert binding["source_run_id"] == binding["generation_workflow_run_id"] == ORIGINAL
    assert receipt["selection_workflow_ids"] == [SELECTOR]
    assert (before, after) == original


def test_new_wrapper_can_keep_original_generation_workflow_distinct():
    before, after = snapshots()
    rebind_original(after, wrapper=True)
    original = copy.deepcopy((before, after))
    receipt = operator.verify_foundation_supersession(before, after)
    binding = receipt["generation_bindings"]["book_spec"]
    assert binding["source_run_id"] == SELECTOR
    assert binding["generation_workflow_run_id"] == ORIGINAL
    assert binding["provider_artifact_bytes_equivalence_asserted"] is False
    assert (before, after) == original


@pytest.mark.parametrize("fault", ["old_content", "old_source", "old_missing", "same_version",
                                   "legacy_same_id", "policy", "missing_run", "wrong_workflow"])
def test_rebuild_requires_real_new_receipts_and_immutable_history(fault):
    before, after = snapshots()
    old_id, new_id = uid("old-book_spec"), uid("new-book_spec")
    if fault == "old_content":
        after["artifact_history"][old_id]["content"] = {}
    elif fault == "old_source":
        after["artifact_history"][old_id]["source_run_id"] = "invented"
    elif fault == "old_missing":
        del after["artifact_history"][old_id]
    elif fault == "same_version":
        after["foundation"]["book_spec"]["version_no"] = 1
    elif fault == "legacy_same_id":
        after["foundation"]["book_spec"] = copy.deepcopy(before["foundation"]["book_spec"])
        after["foundation_associations"][SELECTOR]["artifact_ids"]["book_spec"] = old_id
    elif fault == "wrong_workflow":
        after["foundation"]["book_spec"]["source_run_id"] = "foreign"
    else:
        meta = after["artifact_history"][new_id]["content"]["_meta"]
        meta["source_content_policy" if fault == "policy" else "generation_llm_run_id"] = None
    with pytest.raises(operator.Refused):
        operator.verify_foundation_supersession(before, after)


@pytest.mark.parametrize("fault", ["missing_proof", "body_hash", "proof_body", "row_hash",
    "foreign_original", "foreign_wrapper", "wrong_type", "running_original", "missing_llm",
    "llm_foreign", "llm_wrong_wf", "llm_wrong_template", "llm_wrong_version", "llm_truncated",
    "llm_mock", "missing_submission", "unverified_submission", "submission_wrong_kind",
    "submission_hash", "compact_original_wf", "compact_llm", "missing_association",
    "association_wrong_asset", "association_foreign", "association_running"])
def test_current_book_reuse_fails_closed_on_missing_or_foreign_evidence(fault):
    before, after = snapshots()
    reuse_book(before, after)
    proof = after["foundation_provenance"]["book_spec"]
    selected = after["foundation"]["book_spec"]
    if fault == "missing_proof":
        del after["foundation_provenance"]["book_spec"]
    elif fault == "body_hash":
        selected["hash"] = "different"
    elif fault == "proof_body":
        proof["artifact_sha256"] = "different"
    elif fault == "row_hash":
        proof["artifact_row_sha256"] = "different"
    elif fault in {"foreign_original", "foreign_wrapper"}:
        proof["generation_workflow" if fault == "foreign_original" else "source_workflow"]["project_id"] = uid("foreign")
    elif fault == "wrong_type":
        proof["generation_workflow"]["workflow_type"] = "conception_initial"
    elif fault == "running_original":
        proof["generation_workflow"]["status"] = "running"
    elif fault == "missing_llm":
        proof["llm_run"] = None
    elif fault.startswith("llm_"):
        field, value = {"llm_foreign": ("project_id", uid("foreign")),
            "llm_wrong_wf": ("workflow_run_id", SELECTOR),
            "llm_wrong_template": ("prompt_template", "planner_world_spec"),
            "llm_wrong_version": ("prompt_version", "1.0"),
            "llm_truncated": ("finish_reason", "length"), "llm_mock": ("provider", "mock")}[fault]
        proof["llm_run"][field] = value
    elif fault == "missing_submission":
        proof["submission_receipt"] = None
    elif fault == "unverified_submission":
        proof["submission_revalidated"] = False
    elif fault == "submission_wrong_kind":
        proof["submission_receipt"]["kind"] = "world_spec"
    elif fault == "submission_hash":
        proof["submission_receipt_sha256"] = "different"
    elif fault.startswith("compact_"):
        compact = after["artifact_history"][selected["id"]]["content"]["_meta"]["planning_submission_receipt"]
        compact["workflow_run_id" if fault == "compact_original_wf" else "llm_run_id"] = "different"
        # Historical immutability remains independently tested above.
    elif fault == "missing_association":
        after["foundation_associations"] = {}
    else:
        association = after["foundation_associations"][SELECTOR]
        if fault == "association_wrong_asset":
            association["artifact_ids"]["book_spec"] = uid("other-book")
        elif fault == "association_foreign":
            association["project_id"] = uid("foreign")
        else:
            association["status"] = "running"
    with pytest.raises(operator.Refused):
        operator.verify_foundation_supersession(before, after)


async def test_observation_reads_original_failed_generation_and_separate_wrapper(monkeypatch):
    from bestseller.infra.db.models import (ProjectModel, PlanningArtifactVersionModel,
        WorkflowRunModel, ExecutionBudgetModel, ExecutionOperationModel, ChapterModel, LlmRunModel)
    from bestseller.services import workflows, conception_approval_bridge, budget
    before, after = snapshots()
    rebind_original(after, wrapper=True)
    project_id = UUID(PROJECT)
    scope = "conception:" + uid("task")
    intent_id, snapshot_id, conception_id = map(uid, ("intent", "snapshot", "conception"))
    metadata = {"execution_scope_id": scope, "creation_intent_snapshot_id": intent_id,
        "conception_snapshot_id": snapshot_id, "creation_intent_contract_hash": "contract",
        "conception_workflow_run_id": conception_id}
    project = ProjectModel(id=project_id, slug="observatory", language="en", genre="mystery",
        target_chapters=30, target_word_count=78000, lock_version=1, metadata_json=metadata)
    intent = PlanningArtifactVersionModel(id=UUID(intent_id), project_id=project_id,
        status="validated", content={"explicit": True})
    snapshot = PlanningArtifactVersionModel(id=UUID(snapshot_id), project_id=project_id,
        artifact_type="conception_snapshot", status="canonical", source_run_id=UUID(conception_id),
        version_no=1, content={"creation_intent_hash": "contract"})
    objects = {(PlanningArtifactVersionModel, intent.id): intent,
               (PlanningArtifactVersionModel, snapshot.id): snapshot}
    current = {}
    all_artifacts = []
    for kind in operator.KINDS:
        row = after["artifact_history"][after["foundation"][kind]["id"]]
        artifact = PlanningArtifactVersionModel(**{**row, "id": UUID(row["id"]),
            "project_id": project_id, "source_run_id": UUID(row["source_run_id"])})
        current[kind] = artifact
        all_artifacts.append(artifact)
        llm = after["foundation_provenance"][kind]["llm_run"]
        objects[LlmRunModel, UUID(llm["id"])] = LlmRunModel(**{**llm,
            "id": UUID(llm["id"]), "project_id": project_id,
            "workflow_run_id": UUID(llm["workflow_run_id"])})
    selected_run = WorkflowRunModel(id=UUID(SELECTOR), project_id=project_id,
        workflow_type="generate_foundation_plan", status="completed", metadata_json={
            "artifact_ids": {kind: str(artifact.id) for kind, artifact in current.items()},
            "planning_submissions": {uid("llm-" + kind): submission(kind)[1]
                                     for kind in operator.KINDS if kind != "book_spec"}})
    original = WorkflowRunModel(id=UUID(ORIGINAL), project_id=project_id,
        workflow_type="generate_foundation_plan", status="failed", metadata_json={
            "planning_submissions": {uid("llm-book_spec"): submission("book_spec")[1]}})
    conception = WorkflowRunModel(id=UUID(conception_id), project_id=project_id,
        workflow_type="conception_initial", status="completed", metadata_json={"execution_scope_id": scope})
    for row in (selected_run, original, conception):
        objects[WorkflowRunModel, row.id] = row

    class ReadOnlySession:
        async def get(self, model, key):
            if model is ExecutionBudgetModel:
                assert key == scope
                return SimpleNamespace(metadata_json={})
            return objects.get((model, key))

        async def scalar(self, query):
            assert query.column_descriptions[0]["entity"] is ProjectModel
            return project

        async def scalars(self, query):
            description = query.column_descriptions[0]
            entity = description["entity"]
            if entity is PlanningArtifactVersionModel:
                return all_artifacts
            if entity is WorkflowRunModel:
                return [selected_run] if description["name"] == "WorkflowRunModel" else []
            assert entity in {ExecutionOperationModel, ChapterModel}
            return []

    async def latest(session, *, project_id, artifact_type):
        assert str(project_id) == PROJECT
        return current[artifact_type.value]

    monkeypatch.setattr(workflows, "get_latest_approved_planning_artifact", latest)
    monkeypatch.setattr(conception_approval_bridge, "build_conception_options", lambda **kwargs: {})
    monkeypatch.setattr(budget, "execution_snapshot", lambda value: {})
    task = {"status": "completed", "result": {"mode": "conception_only", "planning_started": False},
        "payload": {"language": "en", "genre": "mystery", "creation_input_receipt": {"contract_hash": "contract"},
            "creation_intent_contract": {"explicit": True}, "_conception_workflow_run_id": conception_id}}
    state = {"scope": scope, "authorization_key": "test", "project_slug": "observatory"}
    args = SimpleNamespace(project_id=PROJECT, target_chapters=30, words_per_chapter=2600)
    result = await operator.observation(ReadOnlySession(), state, task, args)
    proof = result["foundation_provenance"]["book_spec"]
    assert proof["generation_workflow"]["id"] == ORIGINAL
    assert proof["generation_workflow"]["status"] == "failed"
    assert proof["source_workflow"]["id"] == SELECTOR
    assert proof["submission_revalidated"] is True
    assert proof["submission_receipt_sha256"] == current["book_spec"].content["_meta"]["planning_submission_receipt"]["original_receipt_sha256"]
    assert "asset_json" not in proof["submission_receipt"]
    assert result["foundation_associations"][SELECTOR]["artifact_ids"]["book_spec"] == str(current["book_spec"].id)
    # Real observation serialization feeds the pure verifier without attribution repair.
    before["artifact_history"] = {}
    operator.verify_foundation_supersession(before, result)
    original.metadata_json["planning_submissions"][uid("llm-book_spec")]["asset_json"] = "{}"
    tampered = await operator.observation(ReadOnlySession(), state, task, args)
    assert tampered["foundation_provenance"]["book_spec"]["submission_revalidated"] is False
    assert tampered["authority_sha256"] != result["authority_sha256"]
    with pytest.raises(operator.Refused):
        operator.verify_foundation_supersession(before, tampered)


def test_rebuild_flag_is_explicit_and_not_available_for_window():
    args = operator.parser().parse_args([])
    assert args.rebuild_foundation is False
    assert operator.parser().parse_args(["--rebuild-foundation"]).rebuild_foundation is True
