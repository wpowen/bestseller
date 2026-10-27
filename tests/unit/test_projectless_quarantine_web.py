"""Real manager and native SQLite quarantine proof; no provider or production IO.

The imported fixture's partial stream/checkpoint are explicitly synthetic.
Boundary tampering wraps the real SELECT-only helper, never replaces its proof.
"""
from contextlib import asynccontextmanager
from copy import deepcopy

import pytest

from test_conception_logline_recovery import recovered, case  # noqa: F401
from test_execution_budget_resume import no_provider_or_network, state  # noqa: F401

from bestseller.services import conception_logline_recovery as recovery
from bestseller.services.prompt_source_policy import content_hash
from bestseller.web import server

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.fixture
async def web(recovered, tmp_path, monkeypatch):
    c = recovered

    @asynccontextmanager
    async def session_scope(_settings):
        async with c.factory() as session:
            yield session
            await session.commit()

    monkeypatch.setattr(server, "session_scope", session_scope)
    manager = server.WebTaskManager(persist_path=tmp_path / "projectless-tasks.json")
    task = server.WebTaskState(**deepcopy(c.task))
    task.title = "synthetic retained title"
    task.result["retained_paid_context"] = {"fixture": "keep"}
    task.record_event("synthetic_prior_failure", {"keep": True}, milestone=True)
    manager._tasks[c.task_id] = task
    manager._save_to_disk()
    c.manager, c.web_task = manager, task
    return c


async def reconcile(c):
    return await c.manager.execution_reconciliation(
        c.task_id, c.evidence.model_dump(mode="json"), settings=c.settings
    )


async def test_existing_native_receipt_replay_clears_exact_unknown_without_dispatch(web):
    c = web
    before, rows_before = deepcopy(c.web_task.to_dict()), await state(c)
    disk_before = c.manager._persist_path.read_bytes()
    response = await reconcile(c)
    assert response["ready_for_manual_resume"] is True
    assert response["automatic_resume"] is False
    proof = response["projectless_quarantine_proof"]
    assert proof["task_snapshot_sha256"] == content_hash(before)
    assert proof["quarantine"] == c.quarantine
    assert proof["provider_calls"] == 0
    assert c.web_task.status == "failed"
    assert c.web_task.current_stage == "execution_reconciled_manual_resume_required"
    assert c.web_task.error is None and not c.manager._executing_task_ids
    assert "execution_terminal" not in c.web_task.result
    assert c.web_task.result["retained_paid_context"] == before["result"]["retained_paid_context"]
    assert c.web_task.result["last_execution_reconciliation"] == c.quarantine
    event = c.web_task.milestone_events[-1]
    assert event["stage"] == "execution_reconciled"
    assert event["payload"]["previous_projection"]["result"] == before["result"]
    assert event["payload"]["previous_terminal"] == before["result"]["execution_terminal"]
    assert c.web_task.milestone_events[:-1] == before["milestone_events"]
    assert await state(c) == rows_before
    assert c.manager._persist_path.read_bytes() != disk_before
    loaded = server.WebTaskManager(persist_path=c.manager._persist_path)
    assert loaded._tasks[c.task_id].to_dict() == c.web_task.to_dict()


@pytest.mark.parametrize("field", ["task_id", "scope_id", "task_snapshot_sha256"])
async def test_foreign_returned_proof_cannot_clear_task(web, monkeypatch, field):
    c = web
    original = recovery.verified_projectless_quarantine

    async def foreign(*args, **kwargs):
        proof = await original(*args, **kwargs)
        assert proof is not None
        return {**proof, field: "foreign"}

    monkeypatch.setattr(recovery, "verified_projectless_quarantine", foreign)
    before, rows_before = deepcopy(c.web_task.to_dict()), await state(c)
    response = await reconcile(c)
    assert response["ready_for_manual_resume"] is False
    assert c.web_task.to_dict() == before and await state(c) == rows_before


@pytest.mark.parametrize("change", ["payload", "title", "milestone", "executing"])
async def test_full_task_cas_and_live_owner_block_after_real_native_proof(web, monkeypatch, change):
    c = web
    original = recovery.verified_projectless_quarantine
    expected = None

    async def racing(*args, **kwargs):
        nonlocal expected
        proof = await original(*args, **kwargs)
        assert proof is not None
        if change == "payload":
            c.web_task.payload["premise"] = "new author input"
        elif change == "title":
            c.web_task.title = "changed title"
        elif change == "milestone":
            c.web_task.record_event("concurrent_event", {"keep": True}, milestone=True)
        else:
            c.manager._executing_task_ids.add(c.task_id)
        expected = deepcopy(c.web_task.to_dict())
        return proof

    monkeypatch.setattr(recovery, "verified_projectless_quarantine", racing)
    rows_before = await state(c)
    disk_before = c.manager._persist_path.read_bytes()
    response = await reconcile(c)
    assert response["ready_for_manual_resume"] is False
    assert c.web_task.to_dict() == expected
    assert c.web_task.result["execution_terminal"]["code"] == "execution_result_unknown"
    assert c.manager._persist_path.read_bytes() == disk_before
    assert await state(c) == rows_before


@pytest.mark.parametrize("fault", ["missing_checkpoint", "foreign_source", "other_terminal", "cancelled"])
async def test_real_native_missing_or_foreign_source_and_other_stops_stay_fenced(web, fault):
    from test_execution_budget_resume import edit
    from bestseller.infra.db.models import WorkflowRunModel

    c = web
    if fault == "missing_checkpoint":
        await edit(c, WorkflowRunModel, c.wid,
                   lambda row: row.metadata_json.pop("conception_checkpoint"))
    elif fault == "foreign_source":
        c.web_task.payload["_conception_workflow_run_id"] = "00000000-0000-0000-0000-000000000001"
    elif fault == "other_terminal":
        c.web_task.error = c.web_task.current_stage = "execution_budget_exhausted"
        c.web_task.result["execution_terminal"]["code"] = "execution_budget_exhausted"
    else:
        c.web_task.cancel_requested = True
    before, rows_before = deepcopy(c.web_task.to_dict()), await state(c)
    response = await reconcile(c)
    assert response["ready_for_manual_resume"] is False
    assert "projectless_quarantine_proof" not in response
    assert c.web_task.to_dict() == before and await state(c) == rows_before
