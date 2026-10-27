"""Pending review excludes legacy planning only with exact native witnesses."""

from contextlib import asynccontextmanager
from copy import deepcopy
import hashlib
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import event
from test_legacy_request_purpose import foundation_case as foundation_case
from test_legacy_request_purpose import offline as offline
from test_legacy_request_purpose import snapshot
from test_legacy_request_purpose import sqlite_case as sqlite_case
from test_legacy_request_purpose import store as store

from bestseller.infra.db.models import WorkflowRunModel
from bestseller.services import legacy_request_purpose as legacy
from bestseller.services.chapter_pending_review_policy_recovery import _primary_absence

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.fixture
async def pending_legacy(sqlite_case):
    r = sqlite_case
    r.owner = WorkflowRunModel(
        project_id=r.project.id,
        scope_type="chapter",
        scope_id=uuid4(),
        workflow_type="chapter_pipeline",
        status="failed",
        current_step="review_chapter_v1",
    )
    r.session.add(r.owner)
    await r.session.commit()
    r.cycle_id = hashlib.sha256(uuid4().bytes).hexdigest()
    return r


async def absence(r, *, settings=True):
    return await _primary_absence(
        r.session,
        r.settings if settings else None,
        r.project,
        SimpleNamespace(id=r.owner.scope_id),
        r.owner,
        r.root.scope_id,
        r.cycle_id,
        "A distinct current chapter body without any primary review.",
    )


@asynccontextmanager
async def readonly(r, monkeypatch):
    before = await snapshot(r.session)
    writes, locks = [], []

    def sql(_conn, _cursor, statement, *_args):
        if not statement.lstrip().upper().startswith("SELECT"):
            writes.append(statement)

    def orm(state):
        if getattr(state.statement, "_for_update_arg", None) is not None:
            locks.append(state.statement)

    async def forbidden(*args, **kwargs):
        raise AssertionError("pending purpose proof must not flush or commit")

    monkeypatch.setattr(r.session, "flush", forbidden)
    monkeypatch.setattr(r.session, "commit", forbidden)
    event.listen(r.engine.sync_engine, "before_cursor_execute", sql)
    event.listen(r.session.sync_session, "do_orm_execute", orm)
    try:
        yield
    finally:
        assert await snapshot(r.session) == before
        assert not r.session.new and not r.session.dirty and not r.session.deleted
        assert not writes and not locks
        event.remove(r.engine.sync_engine, "before_cursor_execute", sql)
        event.remove(r.session.sync_session, "do_orm_execute", orm)


async def test_exact_legacy_request_is_excluded_readonly_without_attribution_rewrite(
    pending_legacy, monkeypatch
):
    r = pending_legacy
    async with readonly(r, monkeypatch):
        digest = await absence(r)
        assert digest == await absence(r) and len(digest) == 64
        proof = r.session.info[legacy.INFO][r.root.key]
        assert proof["purpose"] == "known_legacy_beat_sheet_planning"
        assert proof["input_sha256"] == r.root.input_hash
        assert proof["original_null_attribution_preserved"] is True
        assert proof["request_reconstructed"] is True
        assert all(proof[k] is False for k in (
            "source_authority_granted", "grade_granted", "credit_granted",
        ))
        assert r.run.prompt_template is None and r.run.workflow_run_id is None


@pytest.mark.parametrize(
    "change",
    ["source", "malformed_witness", "current_protocol_marker", "no_settings", "no_task"],
)
async def test_incomplete_route_requires_full_original_proof(pending_legacy, monkeypatch, change):
    r = pending_legacy
    if change == "source":
        r.project.title += " changed source"
    elif change in {"malformed_witness", "current_protocol_marker"}:
        result = deepcopy(r.root.result_json)
        if change == "malformed_witness":
            result["metadata"]["effective_input_receipt"]["model"] = "openai/foreign"
        else:
            result["metadata"]["commercial_source_receipt"] = {}
        r.root.result_json = result
    elif change == "no_task":
        r.run.metadata_json = {
            k: v for k, v in r.run.metadata_json.items() if k != "task"
        }
    await r.session.commit()
    async with readonly(r, monkeypatch):
        with pytest.raises(ValueError):
            await absence(r, settings=change != "no_settings")
    assert legacy.INFO not in r.session.info


async def test_complete_conflicting_routes_never_use_legacy_proof(pending_legacy, monkeypatch):
    r = pending_legacy
    result = deepcopy(r.root.result_json)
    result["metadata"]["paid_call_origin"]["prompt_template"] = "planner_book_spec"
    r.root.result_json = result
    transport = deepcopy(r.transport.result_json)
    transport["result"]["metadata"]["paid_call_origin"].update(
        logical_role="critic", prompt_template="chapter_review"
    )
    r.transport.result_json = transport
    await r.session.commit()

    async def forbidden(*args, **kwargs):
        raise AssertionError("complete positive conflicts cannot use legacy fallback")

    monkeypatch.setattr(legacy, "prove_unlabelled_beatsheet_request", forbidden)
    async with readonly(r, monkeypatch):
        with pytest.raises(ValueError, match="root_purpose_conflict"):
            await absence(r)
