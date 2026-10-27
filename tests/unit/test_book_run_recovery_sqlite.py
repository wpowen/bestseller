"""New book controller recovery, real SQLite; stage boundaries are inert.

No model, real manuscript, lease server, or PostgreSQL acceptance is claimed.
"""

import asyncio
from copy import deepcopy
from types import SimpleNamespace

import pytest
from test_pending_manuscript_continuation import db as db, seed
from bestseller.infra.db import models as m
from bestseller.services import book_run as goal
from bestseller.services.pending_manuscript_continuation import AwaitingAuthority
from bestseller.settings import load_settings

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.fixture
async def controller(db, monkeypatch):
    async with db() as s:
        p = await seed(s, True)
        p.target_chapters = 4
        row = await goal.create(s, load_settings(), project=p, requested_model="fixture")
        await s.commit()
        rid = row.id

    async def verify(*a, **k):
        pass

    # Ownership/control tests do not assert source qualification.
    monkeypatch.setattr(goal, "verify_selected", verify)
    calls = []

    async def handler(session, project, row, context):
        key = row.current_step
        calls.append(key)
        return {"verified": True, "chapters": 4}

    async def permit(*args):
        return True, None

    yield SimpleNamespace(
        db=db,
        rid=rid,
        calls=calls,
        permit=permit,
        handlers={k: handler for k in (*goal.STAGES, *goal.TERMINAL)},
    )


async def run(c, session, **kw):
    return await goal.execute(
        session,
        c.rid,
        settings=load_settings(),
        handlers=c.handlers,
        permit=c.permit,
        owner_id=kw.pop("owner_id", "new"),
        **kw,
    )


async def test_committed_stage_restart_and_completed_reopen_do_not_repeat(controller):
    c = controller
    normal = c.handlers["knowledge"]

    async def pause(s, p, row, context):
        if context["chapter_number"] == 2:
            raise AwaitingAuthority("offline_pause")
        return await normal(s, p, row, context)

    c.handlers["knowledge"] = pause
    async with c.db() as s:
        assert (await run(c, s))["state"] == "awaiting_authority"
    c.handlers["knowledge"] = normal
    async with c.db() as s:
        assert (await run(c, s, owner_id="restart"))["whole_book_complete"]
    assert c.calls == [
        "1:knowledge",
        "2:knowledge",
        "3:knowledge",
        "4:knowledge",
        "4:ending",
        "4:export",
        "4:reopen",
    ]
    before = list(c.calls)
    async with c.db() as s:
        assert (await run(c, s))["whole_book_complete"]
    assert c.calls == [*before, "complete_manuscript_with_debt"]


async def test_started_unknown_stops_without_repeating_handler(controller):
    c = controller

    async def interrupted(*args):
        c.calls.append("unknown")
        raise RuntimeError("transport_result_unknown")

    c.handlers["knowledge"] = interrupted
    async with c.db() as s:
        with pytest.raises(RuntimeError, match="transport_result_unknown"):
            await run(c, s, owner_id="old")
    async with c.db() as s:
        row = await s.get(m.WorkflowRunModel, c.rid)
        assert row.metadata_json["stage_started"] == "1:knowledge"
        assert not row.metadata_json["outputs"]
        result = await run(c, s, resume_after_restart=True, recovery_owner="old")
        assert result["state"] == "machine_blocked"
    assert c.calls == ["unknown"]


async def test_two_recovery_owners_only_one_claims(controller):
    c = controller
    async with c.db() as s:
        row = await s.get(m.WorkflowRunModel, c.rid)
        row.status = "running"
        row.metadata_json = {**row.metadata_json, "owner": "dead"}
        await s.commit()
    entered, release = asyncio.Event(), asyncio.Event()
    normal = c.handlers["knowledge"]

    async def held(*args):
        entered.set()
        await release.wait()
        return await normal(*args)

    c.handlers["knowledge"] = held

    both_read = asyncio.Event()
    readers = 0

    async def contender(owner):
        nonlocal readers
        async with c.db() as s:
            original_get = s.get
            first_get = True

            async def synchronized_get(model, identity, **kw):
                nonlocal first_get, readers
                value = await original_get(model, identity, **kw)
                if first_get and model is m.WorkflowRunModel and identity == c.rid:
                    first_get = False
                    readers += 1
                    if readers == 2:
                        both_read.set()
                    await asyncio.wait_for(both_read.wait(), 3)
                return value

            s.get = synchronized_get
            return await run(c, s, owner_id=owner, resume_after_restart=True, recovery_owner="dead")

    tasks = [asyncio.create_task(contender(owner)) for owner in ("first", "second")]
    try:
        await asyncio.wait_for(entered.wait(), 3)
        done, pending = await asyncio.wait(tasks, timeout=3, return_when=asyncio.FIRST_COMPLETED)
        assert len(done) == 1 and len(pending) == 1
        assert next(iter(done)).result()["state"] == "already_running"
    finally:
        release.set()
    results = await asyncio.gather(*tasks)
    assert sum(r.get("whole_book_complete") is True for r in results) == 1
    assert readers == 2
    assert c.calls.count("1:knowledge") == 1


async def test_stale_recovery_owner_cannot_mutate_checkpoint(controller):
    c = controller
    async with c.db() as s:
        row = await s.get(m.WorkflowRunModel, c.rid)
        row.status = "running"
        row.metadata_json = {**row.metadata_json, "owner": "actual"}
        await s.commit()
        before = deepcopy(row.metadata_json)
        assert (await run(c, s, resume_after_restart=True, recovery_owner="stale"))[
            "state"
        ] == "already_running"
        await s.refresh(row)
        assert row.metadata_json == before
    assert c.calls == []


@pytest.mark.parametrize("loss", ["lease", "owner"])
async def test_loss_during_stage_cannot_publish_or_advance(controller, loss):
    c = controller
    live = True

    async def permit(*args):
        return live, "lease_lost"

    async def lose(s, p, row, context):
        nonlocal live
        c.calls.append(row.current_step)
        if loss == "lease":
            live = False
        else:
            row.metadata_json = {**row.metadata_json, "owner": "successor"}
            await s.commit()
        return {"verified": True}

    c.permit = permit
    c.handlers["knowledge"] = lose
    async with c.db() as s:
        result = await run(c, s)
        assert result["state"] == "owner_lost"
    async with c.db() as s:
        row = await s.get(m.WorkflowRunModel, c.rid)
        assert row.metadata_json["stage_started"] == "1:knowledge"
        assert row.metadata_json["outputs"] == {}
        expected = "new" if loss == "lease" else "successor"
        assert row.metadata_json["owner"] == expected
    assert c.calls == ["1:knowledge"]


@pytest.mark.parametrize(
    "error", [ValueError("hard"), AwaitingAuthority("wait"), RuntimeError("unknown")]
)
async def test_superseded_owner_exception_cannot_change_successor_state(controller, error):
    c = controller

    async def replaced(s, p, row, context):
        row.metadata_json = {**row.metadata_json, "owner": "successor"}
        await s.commit()
        raise error

    c.handlers["knowledge"] = replaced
    async with c.db() as s:
        assert (await run(c, s))["state"] == "owner_lost"
    async with c.db() as s:
        row = await s.get(m.WorkflowRunModel, c.rid)
        assert row.status == "running"
        assert row.metadata_json["owner"] == "successor"
        assert row.metadata_json["stage_started"] == "1:knowledge"
        assert row.metadata_json["outputs"] == {}
