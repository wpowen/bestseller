"""SQLite proof of finite, additive reconciliation of an old parser rejection."""

from copy import deepcopy
import inspect as python_inspect
import json
import re

import pytest
from sqlalchemy import ForeignKeyConstraint, JSON, MetaData, inspect, select
from sqlalchemy.dialects.postgresql import JSONB
from test_planning_admission_native_entry import (
    SCOPE, _native_grade, _protected_inputs,
)
from test_planning_admission_native_entry import native as native  # noqa: F401
from test_planning_draft_admission import FAIL, _assess_protocol
from test_planning_draft_admission import harness as harness  # noqa: F401

from bestseller.infra.db.models import (
    ExecutionAttemptModel, ExecutionBudgetModel, ExecutionOperationModel,
    LlmRunModel, WorkflowRunModel,
)
from bestseller.services import llm, planning_admission_submission as submission
from bestseller.services import planning_draft_admission as admission

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]
STATE_KEY = "retained_admission_protocol"
OLD_ERROR = "planning_admission_submission:ambiguous_structured_commentary"
RANGE_PROSE = "The first action precedes action_sequence[3-4]; actions[3-4] occur later."


def _deny_provider(*args, **kwargs):
    raise AssertionError("Retained validation must never dispatch or claim another call")


def _legacy_commentary(current):
    # Emulate only the former restriction, while running the real native parser,
    # real service, gateway and paid-proof checks throughout the test.
    def old(content):
        status = current(content)
        stripped = re.sub(r"(?<=[A-Za-z0-9_\]])\[\d+\]", "", content)
        if any(marker in stripped for marker in ("```", "~~~", "{", "}", "[", "]")):
            raise ValueError("ambiguous_structured_commentary")
        return status
    return old


async def _initialize_workflow(h):
    # The shared native harness persists all gateway/financial rows. Add an
    # actual WorkflowRun table as well, so recovery state crosses real sessions.
    table = WorkflowRunModel.__table__.to_metadata(MetaData())
    for column in table.columns:
        if isinstance(column.type, JSONB):
            column.type = JSON()
    for constraint in list(table.constraints):
        if isinstance(constraint, ForeignKeyConstraint):
            table.constraints.remove(constraint)
    async with h.session_factory() as session:
        assert session.bind.dialect.name == "sqlite"
        connection = await session.connection()
        await connection.run_sync(table.create)
        session.add(WorkflowRunModel(
            id=h.workflow.id, project_id=h.project.id,
            workflow_type="retained_native_admission_test", status="running",
            metadata_json={"unrelated_history": {"literal": "preserve"}},
        ))
        await session.commit()
    original_wrapper = h.wrap

    class WorkflowSession(original_wrapper):
        async def get(self, model, key, **kwargs):
            if model is WorkflowRunModel:
                return await self.actual.get(model, key, **kwargs)
            return await super().get(model, key, **kwargs)

    h.wrap = WorkflowSession


async def _persist(h, state):
    async with h.session_factory() as session:
        row = await session.get(WorkflowRunModel, h.workflow.id)
        row.metadata_json = {**row.metadata_json, STATE_KEY: deepcopy(state)}
        await session.commit()


async def _load(h):
    async with h.session_factory() as session:
        row = await session.get(WorkflowRunModel, h.workflow.id)
        assert row.metadata_json["unrelated_history"] == {"literal": "preserve"}
        # A new session plus JSON roundtrip, never the earlier mutable object.
        return json.loads(json.dumps(row.metadata_json[STATE_KEY]))


async def _financial(h):
    result = {}
    async with h.session_factory() as session:
        for model in (ExecutionBudgetModel, ExecutionOperationModel, ExecutionAttemptModel, LlmRunModel):
            fields = [prop.key for prop in inspect(model).column_attrs]
            rows = [deepcopy({key: getattr(row, key) for key in fields})
                    for row in await session.scalars(select(model))]
            result[model.__tablename__] = sorted(rows, key=lambda row: str(row.get("id", row.get("key", ""))))
        budget = await session.get(ExecutionBudgetModel, SCOPE)
        assert budget.calls_used == 2
        assert len(result[LlmRunModel.__tablename__]) == 2
        assert len(result[ExecutionAttemptModel.__tablename__]) == 2
    return result


async def _exhaust_under_old_parser(h, monkeypatch, *, hard_rejected=False):
    await _initialize_workflow(h)
    current = submission._commentary
    monkeypatch.setattr(submission, "_commentary", _legacy_commentary(current))
    payload = _native_grade(h.kwargs()["candidate_payload"], FAIL if hard_rejected else None)
    if hard_rejected:
        for check in payload["projection_checks"]:
            check.update(status="conflict", related_violation_paths=[FAIL["hard_violations"][0]["path"]])
    h.raw = "\n" + json.dumps(payload, ensure_ascii=False, indent=2) + " \n"
    first = deepcopy(payload)
    first["editorial_notes"] = "This must be a list, not a string."
    first_raw = json.dumps(first, ensure_ascii=False)

    def response(result):
        text = "Initial submission audit." if len(h.sent) == 1 else RANGE_PROSE
        result["content"] = result["raw_message"]["content"] = text
        if len(h.sent) == 1:
            result["tool_calls"][0]["function"]["arguments"] = first_raw
            result["raw_message"]["tool_calls"][0]["function"]["arguments"] = first_raw

    h.native_mutation = response
    state = {}

    async def persist():
        await _persist(h, state)

    with pytest.raises(admission.PlanningDraftAdmissionProtocolError, match="admission_protocol_exhausted"):
        await _assess_protocol(h, state, persist)
    assert state == await _load(h)
    assert state["status"] == "protocol_exhausted"
    assert len(state["attempts"]) == len(h.sent) == 2
    assert all(item["status"] == "protocol_invalid" for item in state["attempts"])
    assert state["attempts"][0]["error"]["detail"] != OLD_ERROR
    assert state["attempts"][1]["error"]["detail"] == OLD_ERROR
    monkeypatch.setattr(submission, "_commentary", current)
    monkeypatch.setattr(llm, "_call_litellm", _deny_provider)
    monkeypatch.setattr(admission, "complete_text", _deny_provider)
    return state, payload


@pytest.mark.parametrize("hard_rejected", [False, True])
async def test_same_policy_old_parser_rejection_reconciles_from_paid_sqlite_journal_without_new_credit(
    native, monkeypatch, hard_rejected,
):
    h = native
    protected = _protected_inputs(h)
    exhausted, payload = await _exhaust_under_old_parser(h, monkeypatch, hard_rejected=hard_rejected)
    old = deepcopy(exhausted)
    financial = await _financial(h)
    state = await _load(h)

    async def persist():
        await _persist(h, state)

    receipt = await _assess_protocol(h, state, persist)
    assert state == await _load(h)
    assert state["status"] == "validated" and len(state["attempts"]) == 2
    assert receipt["draft_eligible"] is (not hard_rejected)
    assert receipt["assessment"]["hard_violations"] == payload["hard_violations"]
    assert receipt["raw_grade"] == h.raw
    assert state["attempts"][0] == old["attempts"][0]
    item, previous = state["attempts"][1], old["attempts"][1]
    assert item["completion"] == previous["completion"]
    assert item["error"] == previous["error"]
    assert item["status"] == "validated"
    assert item["error_status"] == "historical_before_parser_reconciliation"
    assert len(item["validation_reconciliations"]) == 1
    reconciliation = item["validation_reconciliations"][0]
    assert reconciliation == {
        "schema": "planning-admission-parser-reconciliation.v1",
        "adapter": "attached-numeric-range-commentary.v1",
        "adapter_source_sha256": admission.content_hash(python_inspect.getsource(submission._commentary)),
        "prior_status": "protocol_invalid", "prior_error": previous["error"],
        "llm_run_id": previous["completion"]["llm_run_id"],
        "physical_return_sha256": admission.content_hash(admission._physical_return(previous["completion"])),
        "graded_input_hash": old["graded_input_hash"], "candidate_hash": old["candidate_hash"],
        "hard_admission_policy": old["hard_admission_policy"],
        "current_submission_receipt_sha256": admission.content_hash(receipt["submission_receipt"]),
        "current_assessment_sha256": admission.content_hash(receipt["assessment"]),
        "story_changed": False, "provider_dispatches": 0, "new_allowance_granted": False,
    }
    first_result, first_state = deepcopy(receipt), deepcopy(state)
    state = await _load(h)
    assert await _assess_protocol(h, state, persist) == first_result
    assert state == first_state == await _load(h)
    assert len(h.sent) == 2 and await _financial(h) == financial
    assert _protected_inputs(h) == protected
    async with h.session_factory() as session:
        if hard_rejected:
            with pytest.raises(ValueError, match="admission_hard_rejected"):
                await admission.verify_planning_draft_admission(h.wrap(session), **h.kwargs(), receipt=receipt)
        else:
            assert await admission.verify_planning_draft_admission(h.wrap(session), **h.kwargs(), receipt=receipt) == receipt


@pytest.mark.parametrize("other_error", [
    {"detail": "planning_admission_submission:raw_message_tool_conflict"},
    {"detail": "admission_source_authority_invalid"}, {}, None,
])
async def test_current_valid_physical_return_cannot_reconcile_a_different_old_error(native, monkeypatch, other_error):
    h = native
    state, _ = await _exhaust_under_old_parser(h, monkeypatch)
    state["attempts"][1]["error"] = deepcopy(other_error)
    await _persist(h, state)
    state = await _load(h)
    previous = deepcopy(state["attempts"][1])
    financial = await _financial(h)

    async def persist():
        await _persist(h, state)

    with pytest.raises(ValueError, match="admission_parser_transition_unapproved"):
        await _assess_protocol(h, state, persist)
    assert state["status"] != "validated"
    assert state["attempts"][1] == previous
    assert "validation_reconciliations" not in state["attempts"][1]
    assert "assessment" not in state and "draft_eligible" not in state
    assert state == await _load(h)
    assert len(h.sent) == 2 and await _financial(h) == financial
