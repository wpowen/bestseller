"""Native primary purpose only: no commercial grade, replay, grant or fee credit."""

from copy import deepcopy
from datetime import UTC, datetime
import json
from uuid import UUID

import pytest
from sqlalchemy import select
from test_commercial_component_cohort import SCOPE
from test_commercial_component_cohort import cohort as cohort
from test_commercial_request_purpose import (
    add_run,
    add_transport,
    paid,
    sha,
)
from test_commercial_request_purpose import (
    case as case,
)
from test_commercial_request_purpose import (
    charged_failure_case as charged_failure_case,
)
from test_commercial_request_purpose import (
    rejected_case as rejected_case,
)

from bestseller.infra.db import models as m
from bestseller.services.commercial_judge_recovery import _assert_no_unbound_commercial_spend
from bestseller.services.commercial_protocol_transition_runtime import _row
from bestseller.services.commercial_request_purpose import prove_known_noncommercial_request

pytestmark = pytest.mark.unit
VERSION = "7.0-citation-tuple-submission"


@pytest.fixture(params=["completed", "stopped"])
def primary(request):
    value = deepcopy(
        request.getfixturevalue("case" if request.param == "completed" else "charged_failure_case")
    )
    if request.param == "completed":
        route = paid(value)["metadata"]["paid_call_origin"]
        paid(value)["tool_calls"] = [{"function": {"name": "submit_chapter_critique"}}]
    else:
        route = value["failure_receipts"][0]["result_json"]
    route.update(logical_role="critic", prompt_template="chapter_review", prompt_version=VERSION)
    return value


def route(value):
    if value["operation"]["status"] == "stopped":
        return value["failure_receipts"][0]["result_json"]
    return paid(value)["metadata"]["paid_call_origin"]


def test_native_v7_primary_proves_purpose_without_mutation(primary):
    if primary["operation"]["status"] == "completed":
        run = add_run(primary, origin=True)
        run.update(logical_role="critic", prompt_template="chapter_review", prompt_version=VERSION)
        add_transport(primary)
    before = deepcopy(primary)
    assert prove_known_noncommercial_request(**primary) is True
    assert primary == before


@pytest.mark.parametrize(
    "field,value",
    [
        ("logical_role", "planner"),
        ("logical_role", "editor"),
        ("prompt_version", "6.0"),
        ("prompt_version", VERSION + "-unknown"),
        ("prompt_version", None),
        ("prompt_template", "chapter_commercial_quality_judge"),
    ],
)
def test_wrong_or_missing_route_never_proves_primary(primary, field, value):
    route(primary)[field] = value
    assert prove_known_noncommercial_request(**primary) is False


def test_commercial_tool_conflicts_with_native_primary_route(primary):
    target = (
        primary["failure_receipts"][0]["result_json"]
        if primary["operation"]["status"] == "stopped"
        else paid(primary)
    )
    target["raw_message"] = {"tool_calls": [{"function": {"name": "submit_chapter_review"}}]}
    assert prove_known_noncommercial_request(**primary) is False


def test_unversioned_retained_timeout_is_not_silently_assigned_native_v7(charged_failure_case):
    receipt = charged_failure_case["failure_receipts"][0]["result_json"]
    receipt.update(
        logical_role="critic", prompt_template="chapter_review", exception_type="Timeout"
    )
    receipt.pop("prompt_version", None)
    before = deepcopy(charged_failure_case)
    assert prove_known_noncommercial_request(**charged_failure_case) is False
    assert charged_failure_case == before


@pytest.mark.parametrize(
    "fault", ["scope", "hash", "attempt", "physical_sends", "interrupted", "extra_attempt"]
)
def test_versioned_route_cannot_waive_physical_failure_guards(charged_failure_case, fault):
    value = charged_failure_case
    receipt = value["failure_receipts"][0]["result_json"]
    receipt.update(logical_role="critic", prompt_template="chapter_review", prompt_version=VERSION)
    if fault == "scope":
        value["failure_receipts"][0]["scope_id"] = "other-scope"
    elif fault == "hash":
        value["failure_receipts"][0]["input_hash"] = sha("other-input")
    elif fault == "attempt":
        receipt["attempt_id"] = "other-attempt"
    elif fault == "physical_sends":
        receipt["physical_sends"] = 2
    elif fault == "interrupted":
        receipt["operator_interruption"] = True
    else:
        value["actual_attempts"].append({**value["actual_attempts"][0], "id": "unreceipted-send"})
    assert prove_known_noncommercial_request(**value) is False


@pytest.fixture
def equivalent_primary(charged_failure_case):
    old = deepcopy(charged_failure_case)
    receipt = old["failure_receipts"][0]["result_json"]
    receipt.update(
        logical_role="critic", prompt_template="chapter_review", exception_type="Timeout"
    )
    receipt.pop("prompt_version", None)
    # A distinct completed root with complete physical evidence, sharing only
    # the scope, model and effective wire hash with the stopped primary.
    witness = deepcopy(old)
    op, attempt = witness["operation"], witness["actual_attempts"][0]
    op.update(key=sha("completed primary successor"), status="completed")
    attempt.update(
        id="successor-send", operation_key=op["key"], status="completed", actual_tokens=55
    )
    origin = {
        "schema": "paid-call-origin.v1",
        "status": "recorded",
        "project_id": None,
        "workflow_run_id": None,
        "step_run_id": None,
        "logical_role": "critic",
        "prompt_template": "chapter_review",
        "prompt_version": VERSION,
        "prompt_hash": op["input_hash"],
    }
    op["result_json"] = {
        "model_name": attempt["model_name"],
        "content": "primary commentary",
        "tool_calls": [{"function": {"name": "submit_chapter_critique"}}],
        "metadata": {
            "paid_call_origin": origin,
            "effective_input_receipt": {
                "schema": "effective-input.v1",
                "hash": op["input_hash"],
                "model": attempt["model_name"],
            },
            "execution_receipt": {
                "scope_id": op["scope_id"],
                "operation_key": op["key"],
                "attempts": [
                    {
                        "attempt_id": attempt["id"],
                        "model": attempt["model_name"],
                        "effective_input_hash": op["input_hash"],
                    }
                ],
            },
        },
    }
    witness["failure_receipts"] = []
    run = add_run(witness, origin=True)
    run.update(logical_role="critic", prompt_template="chapter_review", prompt_version=VERSION)
    add_transport(witness)
    return {**old, "equivalent_requests": [witness]}


def test_unversioned_timeout_uses_complete_equivalent_primary_for_purpose_only(equivalent_primary):
    before = deepcopy(equivalent_primary)
    assert prove_known_noncommercial_request(**equivalent_primary) is True
    assert equivalent_primary == before
    assert "prompt_version" not in equivalent_primary["failure_receipts"][0]["result_json"]
    assert equivalent_primary["actual_attempts"][0]["status"] == "reconciled_charged"
    assert equivalent_primary["operation"]["result_json"] is None


@pytest.mark.parametrize(
    "fault",
    [
        "hash",
        "scope",
        "model",
        "commercial",
        "legacy",
        "unknown_version",
        "wrong_role",
        "no_origin",
        "no_attempts",
        "extra_attempt",
        "missing_declared_attempt",
        "bad_transport",
        "root_not_completed",
        "nested_alias",
        "old_legacy",
        "old_wrong_role",
        "old_not_stopped",
        "old_not_charged",
        "old_extra_attempt",
        "old_failure_missing",
        "old_failure_hash",
        "old_commercial",
        "old_interrupted",
        "old_multiple_sends",
        "no_alias",
    ],
)
def test_equivalence_never_waives_original_or_witness_guards(equivalent_primary, fault):
    value = equivalent_primary
    witness = value["equivalent_requests"][0]
    origin = paid(witness)["metadata"]["paid_call_origin"]
    failure = value["failure_receipts"][0]["result_json"]
    if fault in {"hash", "scope"}:
        field = "input_hash" if fault == "hash" else "scope_id"
        witness["operation"][field] = sha("different")
    elif fault == "model":
        paid(witness)["model_name"] = "different-model"
    elif fault == "commercial":
        paid(witness)["raw_message"] = {
            "tool_calls": [{"function": {"name": "submit_chapter_review"}}]
        }
    elif fault in {"legacy", "unknown_version"}:
        origin["prompt_version"] = "6.0" if fault == "legacy" else None
    elif fault == "wrong_role":
        origin["logical_role"] = "planner"
    elif fault == "no_origin":
        del paid(witness)["metadata"]["paid_call_origin"]
    elif fault == "no_attempts":
        witness["actual_attempts"] = []
    elif fault == "extra_attempt":
        witness["actual_attempts"].append({**witness["actual_attempts"][0], "id": "unreceipted"})
    elif fault == "missing_declared_attempt":
        paid(witness)["metadata"]["execution_receipt"]["attempts"] = []
    elif fault == "bad_transport":
        witness["transport_results"][0]["key"] = sha("forged-control")
    elif fault == "root_not_completed":
        witness["operation"]["status"] = "stopped"
    elif fault == "nested_alias":
        witness["equivalent_requests"] = [deepcopy(witness)]
    elif fault == "old_legacy":
        failure["prompt_version"] = "6.0"
    elif fault == "old_wrong_role":
        failure["logical_role"] = "editor"
    elif fault == "old_not_stopped":
        value["operation"]["status"] = "unknown"
    elif fault == "old_not_charged":
        value["actual_attempts"][0]["status"] = "unknown"
    elif fault == "old_extra_attempt":
        value["actual_attempts"].append({**value["actual_attempts"][0], "id": "unreceipted-old"})
    elif fault == "old_failure_missing":
        value["failure_receipts"] = []
    elif fault == "old_failure_hash":
        value["failure_receipts"][0]["input_hash"] = sha("other-wire")
    elif fault == "old_commercial":
        failure["tool"] = "submit_chapter_review"
    elif fault == "old_interrupted":
        failure["operator_interruption"] = True
    elif fault == "old_multiple_sends":
        failure["physical_sends"] = 2
    else:
        value["equivalent_requests"] = []
    before = deepcopy(value)
    assert prove_known_noncommercial_request(**value) is False
    assert value == before


@pytest.mark.parametrize("field", ["input_hash", "scope_id", "model_name"])
def test_internally_valid_different_wire_cannot_prove_old_purpose(equivalent_primary, field):
    witness = equivalent_primary["equivalent_requests"][0]
    old = witness["operation"].get(field, paid(witness)["model_name"])
    new = sha("other-wire") if field == "input_hash" else "different-scope-or-model"

    def replace(value):
        if isinstance(value, dict):
            return {k: replace(v) for k, v in value.items()}
        if isinstance(value, list):
            return [replace(v) for v in value]
        return new if value == old else value

    other = replace(witness)
    for row in other["transport_results"]:
        row["key"] = sha(
            f"transport-result.v1\n{row['scope_id']}\n{row['result_json']['attempt_id']}"
        )
    assert prove_known_noncommercial_request(**other) is True
    equivalent_primary["equivalent_requests"] = [other]
    assert prove_known_noncommercial_request(**equivalent_primary) is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault",
    [
        None,
        "no_alias",
        "witness_transport",
        "legacy",
        "credit",
        "replay",
        "dispatcher",
        "new_opaque",
    ],
)
async def test_sqlite_caller_proves_purpose_preserving_all_rows_and_other_guards(
    cohort,
    equivalent_primary,
    fault,
):
    value = deepcopy(equivalent_primary)
    old, witness = value, value.pop("equivalent_requests")[0]
    # Rebind the synthetic evidence to the fixture's explicitly test-only scope.
    for request in (old, witness):
        request["operation"]["scope_id"] = SCOPE
        for attempt in request["actual_attempts"]:
            attempt.update(scope_id=SCOPE, reserved_tokens=1000, created_at=datetime.now(UTC))
        for row in request["failure_receipts"] + request["transport_results"]:
            row["scope_id"] = SCOPE
            domain = row["result_json"]["schema"]
            row["key"] = sha(f"{domain}\n{SCOPE}\n{row['result_json']['attempt_id']}")
        if request is witness:
            for result in [paid(witness), witness["transport_results"][0]["result_json"]["result"]]:
                result["metadata"]["execution_receipt"]["scope_id"] = SCOPE
            for run in witness["original_runs"]:
                run["metadata_json"]["execution_receipt"]["scope_id"] = SCOPE
    _op, attempt = old["operation"], old["actual_attempts"][0]
    rid, request_hash = "original-primary-quarantine", sha("reconciliation arguments")
    reconciliation = {
        "key": sha(f"reconciliation.v1\n{SCOPE}\n{rid}"),
        "scope_id": SCOPE,
        "input_hash": request_hash,
        "status": "reconciliation_receipt",
        "result_json": {
            "reconciliation_id": rid,
            "request_hash": request_hash,
            "local_dispatcher_stopped": True,
            "remote_operation_closed": False,
            "receipt": {
                "reconciliation_id": rid,
                "scope_id": SCOPE,
                "attempt_id": attempt["id"],
                "decision": "quarantine_charged",
                "credit_released": False,
                "automatic_resume": False,
                "operation_replay_allowed": False,
                "remote_outcome": "unknown_quarantined",
            },
        },
    }
    if fault == "witness_transport":
        witness["transport_results"][0]["key"] = sha("forged transport")
    elif fault == "legacy":
        old["failure_receipts"][0]["result_json"]["prompt_version"] = "6.0"
    elif fault == "credit":
        reconciliation["result_json"]["receipt"]["credit_released"] = True
    elif fault == "replay":
        reconciliation["result_json"]["receipt"]["operation_replay_allowed"] = True
    elif fault == "dispatcher":
        reconciliation["result_json"]["local_dispatcher_stopped"] = False

    def operation_model(row):
        return m.ExecutionOperationModel(owner="fixture", lease_until=datetime.now(UTC), **row)

    for request in [old] + ([] if fault == "no_alias" else [witness]):
        cohort.session.add_all(
            operation_model(row)
            for row in [
                request["operation"],
                *request["failure_receipts"],
                *request["transport_results"],
            ]
        )
        cohort.session.add_all(m.ExecutionAttemptModel(**a) for a in request["actual_attempts"])
        cohort.session.add_all(
            m.LlmRunModel(**{**run, "id": UUID(run["id"]), "provider": "fixture"})
            for run in request["original_runs"]
        )
    cohort.session.add(operation_model(reconciliation))
    if fault == "new_opaque":
        cohort.session.add(
            operation_model(
                {
                    "key": sha("unrelated unknown history"),
                    "scope_id": SCOPE,
                    "input_hash": sha("unrelated unknown input"),
                    "status": "stopped",
                    "result_json": None,
                }
            )
        )
    cohort.budget.calls_used = 235
    cohort.budget.max_calls = 1000
    cohort.budget.tokens_charged = 1055
    await cohort.session.commit()

    async def snapshots():
        return {
            model.__tablename__: sorted(
                [
                    json.dumps(_row(row), sort_keys=True)
                    for row in await cohort.session.scalars(select(model))
                ]
            )
            for model in (
                m.ExecutionOperationModel,
                m.ExecutionAttemptModel,
                m.LlmRunModel,
                m.ExecutionBudgetModel,
                m.WorkflowRunModel,
                m.ChapterModel,
                m.ChapterDraftVersionModel,
                m.ProjectModel,
            )
        }

    before = await snapshots()
    kwargs = {
        "project_id": cohort.project.id,
        "chapter_id": cohort.chapter.id,
        "body_hash": sha(cohort.draft.content_md),
        "scope": SCOPE,
    }
    if fault is None:
        assert await _assert_no_unbound_commercial_spend(cohort.session, **kwargs) is None
    else:
        error = (
            "control_receipt_unverified"
            if fault in {"credit", "replay", "dispatcher"}
            else "requires_recovery"
        )
        with pytest.raises(ValueError, match=error):
            await _assert_no_unbound_commercial_spend(cohort.session, **kwargs)
    assert await snapshots() == before
    assert cohort.budget.calls_used == 235 and cohort.budget.tokens_charged == 1055
    assert not cohort.session.new and not cohort.session.dirty
    assert cohort.calls == []
