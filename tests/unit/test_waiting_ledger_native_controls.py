"""Shared waiting reader admits only producer-verified non-dispatch controls."""

from datetime import UTC, datetime
import hashlib

import pytest
from sqlalchemy import event, inspect
from test_execution_control_purpose import KINDS, snapshot
from test_execution_control_purpose import case as case
from test_execution_control_purpose import offline as offline

from bestseller.infra.db.models import ExecutionAttemptModel
from bestseller.services.waiting_chapter_review_resume import _ledger

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


async def checked(r):
    # Compare the persisted SQLite representation on both sides; its datetime
    # decoder removes offsets when populate_existing reloads native rows.
    for row in list(r.session.identity_map.values()):
        if inspect(row).persistent:
            await r.session.refresh(row)
    before, writes, locks = await snapshot(r.session), [], []

    def sql(_conn, _cursor, statement, *_args):
        if not statement.lstrip().upper().startswith("SELECT"):
            writes.append(statement)

    def orm(state):
        if getattr(state.statement, "_for_update_arg", None) is not None:
            locks.append(state.statement)

    event.listen(r.engine.sync_engine, "before_cursor_execute", sql)
    event.listen(r.session.sync_session, "do_orm_execute", orm)
    try:
        return await _ledger(r.session, r.scope, lock=False)
    finally:
        event.remove(r.engine.sync_engine, "before_cursor_execute", sql)
        event.remove(r.session.sync_session, "do_orm_execute", orm)
        assert await snapshot(r.session) == before
        assert not writes and not locks
        assert not r.session.new and not r.session.dirty and not r.session.deleted


@pytest.mark.parametrize("case", KINDS, indirect=True)
async def test_actual_native_controls_are_settled_without_changing_parents_or_money(case):
    result = await checked(case)
    assert result == await checked(case)
    assert case.op.key in result["operations"]
    assert case.attempt.id in result["attempts"]
    if case.op.status == "unusable_provider_return":
        assert case.partial_attempt.status == "reconciled_charged"
        assert case.partial_attempt.actual_tokens is None


@pytest.mark.parametrize("case", KINDS, indirect=True)
@pytest.mark.parametrize(
    "fault", ["forged", "missing_parent", "own_send", "pending", "unknown_status"]
)
async def test_controls_cannot_hide_missing_parents_paid_sends_or_unsettled_roots(case, fault):
    r = case
    if fault == "forged":
        r.op.key = hashlib.sha256(b"forged control identity").hexdigest()
    elif fault == "missing_parent":
        await r.session.delete(getattr(r, "partial_parent", r.parent))
    elif fault == "own_send":
        r.session.add(
            ExecutionAttemptModel(
                id="illegal-control-send",
                operation_key=r.op.key,
                scope_id=r.scope,
                input_hash=r.op.input_hash,
                model_name="fixture",
                status="completed",
                reserved_tokens=100,
                actual_tokens=1,
                created_at=datetime.now(UTC),
            )
        )
    elif fault == "pending":
        r.parent.status = "pending"
    else:
        r.op.status = "invented_terminal_control"
    await r.session.commit()
    with pytest.raises(ValueError):
        await checked(r)


@pytest.mark.parametrize("case", ["phase_handoff"], indirect=True)
@pytest.mark.parametrize("fault", ["reserved", "unknown", "orphan"])
async def test_real_handoff_does_not_exempt_any_other_physical_attempt(case, fault):
    if fault == "orphan":
        case.attempt.operation_key = hashlib.sha256(b"missing actual root").hexdigest()
    else:
        case.attempt.status = fault
    await case.session.commit()
    with pytest.raises(ValueError):
        await checked(case)
