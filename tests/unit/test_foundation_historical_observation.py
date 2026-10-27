"""Offline real-observation fixtures for a mixed current foundation selection."""
from copy import deepcopy
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest

# Share only deterministic evidence builders; execute the actual operator def.
_spec = importlib.util.spec_from_file_location("foundation_history_fixtures",
    Path(__file__).with_name("test_foundation_supersession_operator.py"))
fixtures = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fixtures)
operator = fixtures.operator
uid = fixtures.uid


@pytest.mark.parametrize("fault", [None, "missing_row", "missing_kind", "wrong_kind",
    "foreign_row", "wrong_row_id", "unapproved", "foreign_workflow", "failed_workflow",
    "wrong_workflow_type", "reused_failed_producer"])
async def test_historical_complete_foundation_is_distinct_from_current_exact_selection(monkeypatch, fault):
    from bestseller.infra.db.models import (ProjectModel, PlanningArtifactVersionModel,
        WorkflowRunModel, ExecutionBudgetModel, ExecutionOperationModel, ChapterModel)
    from bestseller.services import workflows, conception_approval_bridge, budget
    project_id = UUID(fixtures.PROJECT)
    historical_id, failed_id = map(uid, ("historical-completed", "partial-failed"))
    intent_id, snapshot_id, conception_id = map(uid, ("intent", "snapshot", "conception"))
    scope = "conception:" + uid("task")
    project = ProjectModel(id=project_id, slug="observatory", language="en", genre="mystery",
        target_chapters=30, target_word_count=78000, lock_version=1, metadata_json={
            "execution_scope_id": scope, "creation_intent_snapshot_id": intent_id,
            "conception_snapshot_id": snapshot_id, "creation_intent_contract_hash": "contract",
            "conception_workflow_run_id": conception_id})
    intent = PlanningArtifactVersionModel(id=UUID(intent_id), project_id=project_id,
        status="validated", content={"explicit": True})
    snapshot = PlanningArtifactVersionModel(id=UUID(snapshot_id), project_id=project_id,
        artifact_type="conception_snapshot", status="canonical", source_run_id=UUID(conception_id),
        version_no=1, content={"creation_intent_hash": "contract"})
    artifacts, latest, source_ids = [], {}, {}
    for kind in operator.KINDS:
        artifact = PlanningArtifactVersionModel(id=UUID(uid("historical-" + kind)),
            project_id=project_id, artifact_type=kind, status="approved", version_no=1,
            source_run_id=UUID(historical_id), content={"legacy": kind})
        artifacts.append(artifact)
        latest[kind] = artifact
        source_ids[kind] = str(artifact.id)
    # A later failed foundation has a successfully retained Book v2, but no
    # completed WF yet associates that Book with the old other three assets.
    book = PlanningArtifactVersionModel(id=UUID(uid("current-book")), project_id=project_id,
        artifact_type="book_spec", status="approved", version_no=2,
        source_run_id=UUID(failed_id), content={"theme_statement": "Publish the error"})
    artifacts.append(book)
    latest["book_spec"] = book
    completed = WorkflowRunModel(id=UUID(historical_id), project_id=project_id,
        workflow_type="generate_foundation_plan", status="completed",
        metadata_json={"artifact_ids": source_ids})
    failed = WorkflowRunModel(id=UUID(failed_id), project_id=project_id,
        workflow_type="generate_foundation_plan", status="failed", metadata_json={})
    conception = WorkflowRunModel(id=UUID(conception_id), project_id=project_id,
        workflow_type="conception_initial", status="completed", metadata_json={"execution_scope_id": scope})
    if fault == "missing_row":
        artifacts.pop(0)  # Missing historical Book; the selected current Book still exists.
    elif fault == "missing_kind":
        del source_ids["world_spec"]
    elif fault == "wrong_kind":
        latest["world_spec"].artifact_type = "cast_spec"
    elif fault == "foreign_row":
        latest["world_spec"].project_id = UUID(uid("foreign"))
    elif fault == "wrong_row_id":
        source_ids["world_spec"] = uid("nonexistent-row")
    elif fault == "unapproved":
        latest["world_spec"].status = "rejected"
    elif fault == "foreign_workflow":
        completed.project_id = UUID(uid("foreign"))
    elif fault == "failed_workflow":
        completed.status = "failed"
    elif fault == "wrong_workflow_type":
        completed.workflow_type = "conception_initial"
    elif fault == "reused_failed_producer":
        artifacts[0].source_run_id = UUID(failed_id)
    objects = {(type(row), row.id): row for row in (intent, snapshot, completed, failed, conception)}
    # The fake has no mutation methods: observation can only read model rows.
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
            if description["entity"] is PlanningArtifactVersionModel:
                return artifacts
            if description["entity"] is WorkflowRunModel:
                # Return invalid WF fixtures too: the observation must verify
                # the positive proof, not assume a fixture/query conferred it.
                return [completed] if description["name"] == "WorkflowRunModel" else []
            assert description["entity"] in {ExecutionOperationModel, ChapterModel}
            return []

    async def get_latest(session, *, project_id, artifact_type):
        return latest[artifact_type.value]

    monkeypatch.setattr(workflows, "get_latest_approved_planning_artifact", get_latest)
    monkeypatch.setattr(conception_approval_bridge, "build_conception_options", lambda **kwargs: {})
    monkeypatch.setattr(budget, "execution_snapshot", lambda value: {})
    task = {"status": "completed", "result": {"mode": "conception_only", "planning_started": False},
        "payload": {"language": "en", "genre": "mystery", "creation_input_receipt": {"contract_hash": "contract"},
            "creation_intent_contract": {"explicit": True}, "_conception_workflow_run_id": conception_id}}
    state = {"scope": scope, "authorization_key": "test", "project_slug": "observatory"}
    args = SimpleNamespace(project_id=fixtures.PROJECT, target_chapters=30, words_per_chapter=2600)
    originals = deepcopy([row.content for row in artifacts]), deepcopy(completed.metadata_json)
    result = await operator.observation(ReadOnlySession(), state, task, args)
    assert result["completed_foundation_workflows"] == []
    assert result["foundation_associations"] == {}
    assert result["foundation"]["book_spec"]["id"] == str(book.id)
    valid = fault in {None, "reused_failed_producer"}
    assert result["historical_foundation_workflows"] == ([historical_id] if valid else [])
    if valid:
        proof = result["historical_foundation_associations"][historical_id]
        assert proof["artifact_ids"] == source_ids
        assert proof["status"] == "completed" and proof["project_id"] == fixtures.PROJECT
        assert proof["artifact_row_sha256"] == {kind: operator.digest(result["artifact_history"][identity])
                                                for kind, identity in source_ids.items()}
        assert source_ids["book_spec"] != str(book.id)
        if fault == "reused_failed_producer":
            assert result["artifact_history"][source_ids["book_spec"]]["source_run_id"] == failed_id
        # The new historical proof is part of the inspected/CAS authority.
        first_authority = result["authority_sha256"]
        completed.metadata_json = {"artifact_ids": {**source_ids, "world_spec": uid("absent")}}
        changed = await operator.observation(ReadOnlySession(), state, task, args)
        assert changed["authority_sha256"] != first_authority
        assert changed["historical_foundation_workflows"] == []
        completed.metadata_json = originals[1]
    else:
        assert result["historical_foundation_associations"] == {}
    assert originals == ([row.content for row in artifacts], completed.metadata_json)
