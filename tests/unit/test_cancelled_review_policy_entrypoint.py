"""Actual recovery API wiring with SQLite; stop at its dispatch boundary."""
from copy import deepcopy
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import event, text
from sqlalchemy.schema import CreateTable
from test_chapter_cancelled_review_recovery import db as db, retained as retained

from bestseller.infra.db.models import RewriteTaskModel, WorkflowRunModel
from bestseller.services import pipelines
from bestseller.services.budget import bind_execution_scope

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.fixture
async def entry(retained, monkeypatch):
    session, *_ = retained
    ddl = str(CreateTable(RewriteTaskModel.__table__).compile(dialect=session.bind.dialect))
    await session.execute(text(ddl.replace("::jsonb", "")))
    await session.commit()
    dispatch = AsyncMock(return_value="stopped_before_dispatch")
    monkeypatch.setattr(pipelines, "_run_prepared_chapter_review_cycle", dispatch)

    def forbidden(**kwargs):
        raise AssertionError("cancelled workflow must not be relabelled as adoption outcome")

    monkeypatch.setattr(pipelines, "_record_chapter_adoption_outcome", forbidden)
    return retained, dispatch


async def invoke(rows, *, previous_id=None, revision="real-policy-change"):
    session, project, chapter, draft, previous, _control, _budget, scope = rows
    with bind_execution_scope(scope):
        return await pipelines.resume_chapter_review_after_policy_change(
            session, object(), project.slug, chapter.chapter_number,
            previous_workflow_run_id=previous_id or previous.id, expected_draft_id=draft.id,
            expected_body_hash=pipelines.chapter_review_body_fingerprint(draft.content_md)["digest"],
            policy_revision=revision, policy_source_hash=pipelines.chapter_review_recovery_source_hash(),
            reason="Explicit cancelled source recovery; retain prior evidence", requested_by="test-operator")


async def test_actual_api_preserves_cancelled_terminal_fields_and_original_attempts(entry):
    rows, dispatch = entry
    session, _project, chapter, draft, previous, _control, budget, _scope = rows
    original_cycles = deepcopy(chapter.metadata_json["chapter_critic_cycles"])
    terminal = (previous.status, previous.current_step, previous.error_message)
    paid = deepcopy(previous.metadata_json)
    usage = (budget.calls_used, budget.tokens_charged, deepcopy(budget.metadata_json))
    body = draft.content_md
    assert await invoke(rows) == "stopped_before_dispatch"
    await session.refresh(previous)
    await session.refresh(chapter)
    await session.refresh(budget)
    assert (previous.status, previous.current_step, previous.error_message) == terminal
    assert previous.metadata_json["paid_history"] == paid["paid_history"]
    assert (budget.calls_used, budget.tokens_charged, budget.metadata_json) == usage
    assert draft.content_md == body and draft.is_current
    assert chapter.metadata_json["chapter_critic_cycles"] == original_cycles
    assert chapter.metadata_json["chapter_review_attempts_active"] == 9
    cycle = chapter.metadata_json["chapter_review_recovery_cycles"][-1]
    assert cycle["attempts_base"] == 9
    assert cycle["cancelled_origin_proof"]["previous_workflow_run_id"] == str(previous.id)
    assert cycle["cancelled_origin_proof"]["source_cycle"] == next(iter(original_cycles.values()))
    assert dispatch.await_count == 1
    assert dispatch.call_args.kwargs["cycle"] == cycle


@pytest.mark.parametrize("boundary", ["earlier_workflow", "paused"])
async def test_actual_api_strict_guard_rejects_before_any_write_or_dispatch(entry, boundary):
    rows, dispatch = entry
    session, project, chapter, _draft, previous, control, _budget, _scope = rows
    previous_id = None
    if boundary == "earlier_workflow":
        other = WorkflowRunModel(project_id=project.id, scope_type="chapter", scope_id=chapter.id,
            workflow_type="chapter_pipeline", status="cancelled", metadata_json={})
        session.add(other)
        await session.flush()
        previous_id = other.id
    else:
        control.desired_state = "pause"
    await session.commit()
    await session.refresh(chapter)
    before = deepcopy(chapter.metadata_json), deepcopy(previous.metadata_json)
    writes = []

    def track(_conn, _cursor, statement, _params, _context, _many):
        if statement.lstrip().split()[0].upper() in {"INSERT", "UPDATE", "DELETE"}:
            writes.append(statement)

    event.listen(session.bind.sync_connection, "before_cursor_execute", track)
    try:
        with pytest.raises(ValueError, match="chapter_cancelled_review_recovery_"):
            await invoke(rows, previous_id=previous_id)
    finally:
        event.remove(session.bind.sync_connection, "before_cursor_execute", track)
    assert not writes and not session.dirty and not session.new
    assert before == (chapter.metadata_json, previous.metadata_json)
    dispatch.assert_not_awaited()


async def test_actual_api_same_policy_cannot_reopen_completed_recovery_with_new_label(entry):
    rows, dispatch = entry
    session, _project, chapter, *_ = rows
    await invoke(rows)
    metadata = deepcopy(chapter.metadata_json)
    metadata["chapter_review_recovery_cycles"][-1]["state"] = "completed"
    chapter.metadata_json = metadata
    await session.commit()
    with pytest.raises(ValueError, match="policy_already_used"):
        await invoke(rows, revision="new-label-does-not-reset-credit")
    assert dispatch.await_count == 1
    assert len(chapter.metadata_json["chapter_review_recovery_cycles"]) == 1
