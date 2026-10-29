"""RED: authority lost during the final ORM lock await cannot adopt a body.

SQLite executes the actual ORM statement but cannot provide PostgreSQL row-lock
waiting. The barrier delays return from that precise FOR UPDATE scalar call;
it does not replace the session, workflow, owner guard or native receipt checks.
These are boundary tests, not evidence of PostgreSQL locking or paid quality.
"""

import asyncio

from full_flow_emotional_preservation.support import equal, require
import pytest
from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.infra.db import models as m
from bestseller.services import production_control
from bestseller.services.chapter_revision import ReaderRevisionIdentityError
from bestseller.services.progress_context import TaskCancelledError
from full_flow_reader_revision_identity.test_reader_revision_workflow_identity import (
    ACCEPTANCE,
    ANCHOR,
    EDITOR,
    VERIFY,
    native_receipts,
)

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.mark.parametrize("interruption", ["owner_replaced", "pause", "unchanged"])
async def test_final_chapter_lock_wait_rechecks_authority_before_adoption(
    revision_case, monkeypatch, interruption
):
    case = revision_case
    before = await case.protected()
    reached, release = asyncio.Event(), asyncio.Event()
    native_scalar = AsyncSession.scalar
    lock_returns = []

    async def chapter_lock_barrier(session, statement, *args, **kwargs):
        value = await native_scalar(session, statement, *args, **kwargs)
        if isinstance(statement, Select) and statement._for_update_arg is not None:
            columns = tuple(statement.selected_columns)
            if len(columns) == 1 and columns[0].shares_lineage(m.ChapterModel.__table__.c.id):
                equal("lock.actual_chapter_row", case.chapter_id, value)
                lock_returns.append(value)
                equal("lock.only_one_adoption_barrier", 1, len(lock_returns))
                reached.set()
                # Native SQL has executed. Only its await return is held here;
                # SQLite does not implement the requested row-lock contention.
                await release.wait()
        return value

    monkeypatch.setattr(AsyncSession, "scalar", chapter_lock_barrier)
    worker = asyncio.create_task(case.apply())
    arrival = asyncio.create_task(reached.wait())
    result = None
    try:
        await asyncio.wait((worker, arrival), timeout=15, return_when=asyncio.FIRST_COMPLETED)
        if not reached.is_set() and worker.done():
            await worker  # Preserve the exact native failure before the barrier.
        require("lock.native_boundary_reached", reached.is_set())
        equal("lock.worker_still_waiting", False, worker.done())
        equal("lock.source_not_yet_adopted", before, await case.protected())
        workflow_id = await native_receipts(case, {ANCHOR, EDITOR, ACCEPTANCE, VERIFY})
        equal("lock.last_native_send", VERIFY, case.boundaries[-1]["template"])
        equal("lock.preservation_completed", True, case.volta[-1]["receipt"]["preserved"])

        ledger, files, wires = await case.ledger(), case.files(), len(case.wires)
        lease_key = "bestseller:pipeline-live:" + case.slug
        original_owner = case.redis.store[lease_key]
        workflow = (await case.workflow_rows())[0]
        equal(
            "lock.actual_owner_binding", original_owner, workflow.metadata_json["execution_owner"]
        )
        paused_serial = None
        if interruption == "owner_replaced":
            # Change only the offline Redis adapter's acquired lease. The real
            # owner assertion and normal workflow stamp remain untouched.
            case.redis.store[lease_key] = "barrier-successor-owner"
        elif interruption == "pause":
            # A different actual ORM session persists the normal PAUSE command.
            async with case.sessions.begin() as controller:
                state = await production_control.request_pause(
                    controller,
                    case.project_id,
                    actor="reader-identity-barrier-test",
                    reason="pause while reader revision awaits the chapter lock",
                )
                paused_serial = state.command_serial
            async with case.sessions() as observer:
                control = await observer.get(m.BookProductionControlModel, case.project_id)
                equal("lock.pause_committed_before_release", "pause", control.desired_state)
                equal("lock.pause_command_serial", paused_serial, control.command_serial)

        release.set()
        try:
            result = await asyncio.wait_for(worker, timeout=15)
        except (TaskCancelledError, ReaderRevisionIdentityError):
            if interruption == "unchanged":
                raise
            # The outer owner exit may reject AFTER the session has committed.
            # An exception alone is therefore not proof of denied adoption.

        equal("lock.no_more_native_send", wires, len(case.wires))
        equal("lock.native_ledger_retained", ledger, await case.ledger())
        equal("lock.native_payloads_retained", files, case.files())
        rows = await case.workflow_rows()
        equal("lock.no_new_workflow", [workflow_id], [row.id for row in rows])
        equal(
            "lock.original_owner_not_rebound",
            original_owner,
            rows[0].metadata_json["execution_owner"],
        )
        if interruption == "unchanged":
            equal("lock.unchanged_authority_can_adopt", True, result["ok"])
            require("lock.control_really_adopted", before != await case.protected())
            return

        # Read after apply's session AND outer owner guard have both exited.
        # This must fail even if owner loss raised only after a durable commit.
        equal("lock_wait.no_adoption_after_" + interruption, before, await case.protected())
        equal(
            "lock.no_success_result", False, isinstance(result, dict) and result.get("ok") is True
        )
        async with case.sessions() as observer:
            task = await observer.get(m.RewriteTaskModel, case.task_id)
            equal("lock.task_not_completed", False, task.status == "completed")
            equal(
                "lock.no_result_draft_pointer",
                False,
                "result_chapter_draft_id" in task.metadata_json,
            )
            equal("lock.workflow_not_completed", False, rows[0].status == "completed")
            if interruption == "pause":
                control = await observer.get(m.BookProductionControlModel, case.project_id)
                equal("lock.pause_not_reset", "pause", control.desired_state)
                equal("lock.pause_generation_retained", paused_serial, control.command_serial)
        if interruption == "owner_replaced":
            equal(
                "lock.successor_not_released",
                "barrier-successor-owner",
                case.redis.store.get(lease_key),
            )
    finally:
        release.set()
        for pending in (worker, arrival):
            if not pending.done():
                pending.cancel()
        await asyncio.gather(worker, arrival, return_exceptions=True)
