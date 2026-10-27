"""Main resume state regression; actual SQLite, inert admission boundary."""

import asyncio

import pytest
import test_materialized_outline_source_recovery as fixtures

from bestseller.infra.db.models import WorkflowRunModel
from bestseller.services import materialized_outline_source_recovery as service
from bestseller.services import pipelines, planning_draft_admission
from bestseller.services.budget import bind_execution_scope
from bestseller.services.retained_planning_generation import _Reader

case = fixtures.case
store = fixtures.store
pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


async def invoke(c):
    async with c.factory() as session:
        with bind_execution_scope(c.scope):
            return await service.assess_materialized_outline_source(
                session,
                c.settings,
                project_slug=c.project.slug,
                artifact_id=c.outline.id,
                materialization_workflow_id=c.owner.id,
                requested_by="main-resume-state-test",
            )


async def failed_owner(c):
    await invoke(c)
    identity = service.admission_workflow_id(c.project.id, c.outline.id)
    await fixtures.mutate(
        c,
        WorkflowRunModel,
        identity,
        status="failed",
        current_step="execution_result_unknown",
        error_message="execution_result_unknown",
    )
    c.calls.clear()
    return await fixtures.journal(c)


async def test_boundary_observes_durable_resume_state_without_resetting_evidence(case, monkeypatch):
    c = case
    old = await failed_owner(c)
    protected = await fixtures.protected(c)
    original = planning_draft_admission.assess_planning_draft_admission
    observations = []

    async def boundary(session, settings, **kwargs):
        # The existing fixture replaces only admission/provider work. This read
        # uses another SQLite connection, so an uncommitted ORM change cannot pass.
        async with c.factory() as other:
            saved = await other.get(WorkflowRunModel, old.id)
            observations.append((saved.status, saved.current_step, saved.error_message))
            assert saved.metadata_json["protocol"] == old.metadata_json["protocol"]
            assert saved.scope_id == old.scope_id and saved.project_id == old.project_id
            assert saved.metadata_json["previous_source_assessment_failures"] == [
                {
                    "status": "failed",
                    "current_step": "execution_result_unknown",
                    "error_message": "execution_result_unknown",
                }
            ]
        return await original(session, settings, **kwargs)

    monkeypatch.setattr(planning_draft_admission, "assess_planning_draft_admission", boundary)
    await invoke(c)
    expected = ("running", "assess_current_source", None)
    assert observations == [expected]
    saved = await fixtures.journal(c)
    assert saved.id == old.id and saved.status == "completed"
    assert saved.metadata_json["protocol"]["attempts"] == old.metadata_json["protocol"]["attempts"]
    assert len(saved.metadata_json["previous_source_assessment_failures"]) == 1
    assert await fixtures.protected(c) == protected


@pytest.mark.parametrize("status", ["cancelled", "stopping", "paused"])
async def test_resume_never_revives_halted_owner(case, status):
    c = case
    old = await failed_owner(c)
    await fixtures.mutate(c, WorkflowRunModel, old.id, status=status)
    protected = await fixtures.protected(c)
    with pytest.raises(ValueError, match="workflow_halted"):
        await invoke(c)
    saved = await fixtures.journal(c)
    assert saved.status == status and saved.metadata_json == old.metadata_json
    assert not c.calls and await fixtures.protected(c) == protected


async def test_cancellation_after_fresh_read_wins_resume_cas(case, monkeypatch):
    c = case
    old = await failed_owner(c)
    original = _Reader.get
    armed = True

    async def read_then_cancel(reader, model, identity):
        nonlocal armed
        row = await original(reader, model, identity)
        if armed and model is WorkflowRunModel and identity == old.id:
            armed = False
            await fixtures.mutate(
                c,
                WorkflowRunModel,
                old.id,
                status="cancelled",
                current_step="operator_cancelled",
                error_message="keep cancellation",
            )
        return row

    monkeypatch.setattr(_Reader, "get", read_then_cancel)
    protected = await fixtures.protected(c)
    with pytest.raises(ValueError, match="workflow_changed_during_checkpoint") as caught:
        await invoke(c)
    saved = await fixtures.journal(c)
    assert saved.status == "cancelled" and saved.current_step == "operator_cancelled"
    assert saved.error_message == "keep cancellation" and saved.metadata_json == old.metadata_json
    assert caught.value.workflow_failures[0]["workflow_id"] == str(old.id)
    assert not c.calls and await fixtures.protected(c) == protected


@pytest.mark.parametrize("existing", [False, True], ids=["new-owner", "failed-owner"])
async def test_initial_checkpoint_failure_attaches_exact_owner_even_after_rollback(
    case, monkeypatch, existing
):
    c = case
    if existing:
        old = await failed_owner(c)
    protected = await fixtures.protected(c)
    failure = RuntimeError("initial checkpoint fixture failure")

    async def checkpoint(session):
        await session.flush()
        await session.rollback()  # Expire ORM identity, as a failed commit can do.
        raise failure

    monkeypatch.setattr(pipelines, "_checkpoint_commit", checkpoint)
    with pytest.raises(RuntimeError) as caught:
        await invoke(c)
    assert caught.value is failure
    attached = failure.workflow_failures[0]
    assert attached["workflow_id"] == str(service.admission_workflow_id(c.project.id, c.outline.id))
    assert attached["project_id"] == str(c.project.id)
    assert attached["workflow_type"] == service.WORKFLOW_TYPE
    assert attached["error_type"] == "RuntimeError"
    assert not c.calls and await fixtures.protected(c) == protected
    saved = await fixtures.journal(c)
    if existing:
        assert saved.status == "failed" and saved.metadata_json == old.metadata_json
    else:
        assert saved is None


async def test_initial_checkpoint_cancellation_is_attached_and_does_not_dispatch(case, monkeypatch):
    c = case
    old = await failed_owner(c)
    failure = asyncio.CancelledError()

    async def checkpoint(session):
        await session.rollback()
        raise failure

    monkeypatch.setattr(pipelines, "_checkpoint_commit", checkpoint)
    with pytest.raises(asyncio.CancelledError) as caught:
        await invoke(c)
    assert caught.value is failure
    assert failure.workflow_failures[0]["workflow_id"] == str(old.id)
    assert failure.workflow_failures[0]["code"] == "workflow_interrupted"
    saved = await fixtures.journal(c)
    assert saved.status == "failed" and saved.metadata_json == old.metadata_json
    assert not c.calls


@pytest.mark.parametrize("position", ["before_persist", "after_return", "after_final_read"])
async def test_independent_failure_after_resume_is_never_revived(case, monkeypatch, position):
    c = case
    old = await failed_owner(c)
    protected = await fixtures.protected(c)
    original = planning_draft_admission.assess_planning_draft_admission
    original_get = _Reader.get
    external = []
    armed = False

    async def fail_independently():
        async with c.factory() as other:
            row = await other.get(WorkflowRunModel, old.id)
            assert row.status == "running"
            row.status = "failed"
            row.current_step = "independent_failure"
            row.error_message = "preserve external failure"
            row.metadata_json = {**row.metadata_json, "external_failure": {"retain": None}}
            await other.commit()
            external.append(row.metadata_json)

    async def boundary(session, settings, **kwargs):
        nonlocal armed
        if position == "before_persist":
            await fail_independently()
        result = await original(session, settings, **kwargs)
        if position == "after_return":
            await fail_independently()
        if position == "after_final_read":
            armed = True
        return result

    async def read_then_fail(reader, model, identity):
        nonlocal armed
        row = await original_get(reader, model, identity)
        if armed and model is WorkflowRunModel and identity == old.id:
            armed = False
            await fail_independently()
        return row

    monkeypatch.setattr(planning_draft_admission, "assess_planning_draft_admission", boundary)
    monkeypatch.setattr(_Reader, "get", read_then_fail)
    code = (
        "workflow_changed_during_commit"
        if position == "after_final_read"
        else "workflow_changed_during_assessment"
    )
    with pytest.raises(ValueError, match=code) as caught:
        await invoke(c)
    saved = await fixtures.journal(c)
    assert saved.status == "failed" and saved.current_step == "independent_failure"
    assert saved.error_message == "preserve external failure"
    assert len(external) == 1 and saved.metadata_json == external[0]
    assert saved.metadata_json["protocol"]["attempts"] == old.metadata_json["protocol"]["attempts"]
    assert caught.value.workflow_failures[0]["workflow_id"] == str(old.id)
    assert len(c.calls) == 1 and await fixtures.protected(c) == protected


@pytest.mark.parametrize("original_failed", [False, True], ids=["completed", "failed"])
async def test_first_checkpoint_rejects_a_new_failure_since_original_owner_read(
    case, monkeypatch, original_failed
):
    c = case
    old = await failed_owner(c)
    if not original_failed:
        await fixtures.mutate(
            c,
            WorkflowRunModel,
            old.id,
            status="completed",
            current_step="current_source_admitted",
            error_message=None,
        )
    original_get = _Reader.get
    armed = True

    async def fail_before_first_checkpoint_read(reader, model, identity):
        nonlocal armed
        if armed and model is WorkflowRunModel and identity == old.id:
            armed = False
            await fixtures.mutate(
                c,
                WorkflowRunModel,
                old.id,
                status="failed",
                current_step="new_failure",
                error_message="new independent failure",
            )
        return await original_get(reader, model, identity)

    monkeypatch.setattr(_Reader, "get", fail_before_first_checkpoint_read)
    with pytest.raises(ValueError, match="workflow_changed_during_assessment"):
        await invoke(c)
    saved = await fixtures.journal(c)
    assert saved.status == "failed" and saved.current_step == "new_failure"
    assert saved.error_message == "new independent failure"
    assert saved.metadata_json == old.metadata_json and not c.calls


async def test_completed_cached_read_remains_completed_at_boundary(case, monkeypatch):
    c = case
    first = await invoke(c)
    old = await fixtures.journal(c)
    original = planning_draft_admission.assess_planning_draft_admission
    seen = []

    async def boundary(session, settings, **kwargs):
        current = await fixtures.journal(c)
        seen.append(current.status)
        assert current.metadata_json["protocol"] == old.metadata_json["protocol"]
        return await original(session, settings, **kwargs)

    monkeypatch.setattr(planning_draft_admission, "assess_planning_draft_admission", boundary)
    assert await invoke(c) == first
    assert seen == ["completed"]
    saved = await fixtures.journal(c)
    assert saved.status == "completed" and saved.metadata_json == old.metadata_json


async def test_protocol_exhaustion_is_durable_machine_blocked_without_valid_false_grade(
    case, monkeypatch
):
    from bestseller.infra.db.session import _save_workflow_failures_after_rollback

    c = case
    old = await failed_owner(c)
    protected = await fixtures.protected(c)
    journal = []
    failures = []

    async def exhausted_boundary(session, settings, **kwargs):
        state = kwargs["protocol_state"]
        state["attempts"].append(
            {
                "completion": {"content": "original invalid citations", "llm_run_id": None},
                "status": "protocol_invalid",
                "error": {"detail": "original citation error"},
            }
        )
        assert len(state["attempts"]) == planning_draft_admission.MAX_PROTOCOL_REPAIRS + 1 == 2
        state["status"] = "protocol_exhausted"
        await kwargs["persist_protocol"]()
        journal.extend(state["attempts"])
        error = planning_draft_admission.PlanningDraftAdmissionProtocolError(state)
        failures.append(error)
        raise error

    monkeypatch.setattr(
        planning_draft_admission, "assess_planning_draft_admission", exhausted_boundary
    )
    with pytest.raises(planning_draft_admission.PlanningDraftAdmissionProtocolError) as caught:
        await invoke(c)
    assert caught.value is failures[0]
    saved = await fixtures.journal(c)
    assert saved.status == "machine_blocked"
    assert (
        saved.current_step
        == saved.error_message
        == caught.value.code
        == "admission_protocol_exhausted"
    )
    assert saved.metadata_json["source_admission_status"] == "protocol_unknown"
    assert saved.metadata_json["protocol"] == caught.value.receipt
    assert saved.metadata_json["protocol"]["status"] == "protocol_exhausted"
    assert saved.metadata_json["protocol"]["attempts"] == journal
    # Existing receipt stays historical; no new valid False grade is manufactured.
    assert saved.metadata_json.get("source_admission") == old.metadata_json.get("source_admission")
    assert caught.value.workflow_failures[0]["workflow_id"] == str(old.id)
    await _save_workflow_failures_after_rollback(c.factory, caught.value)
    saved = await fixtures.journal(c)
    assert saved.status == "machine_blocked" and saved.current_step == caught.value.code
    assert saved.metadata_json["protocol"]["attempts"] == journal
    assert await fixtures.protected(c) == protected
