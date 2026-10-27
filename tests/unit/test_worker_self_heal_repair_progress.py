"""Worker wiring: native committed proofs, durable observer state, queue failure."""
# ruff: noqa: F811 -- imported pytest fixtures are injected by name

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.schema import CreateTable
from test_self_heal_recovery_accounting import recovery  # noqa: F401
from test_self_heal_repair_progress import (  # noqa: F401
    collect,
    committed_hype,
    factory,
    hype_case,
    restore,
    rig,
)

from bestseller.infra.db.models import (
    BookProductionControlModel,
    ExecutionBudgetModel,
    ProjectModel,
    WorkflowRunModel,
)
from bestseller.services import self_heal_repair_progress as observer
from bestseller.services.production_control import load_control_state
from bestseller.services.retry_ledger import assert_execution_scope_open, project_execution_scope
from bestseller.worker import self_heal as worker

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


async def configure_worker(r, monkeypatch):
    async with r.session.bind.begin() as connection:
        for model in (BookProductionControlModel, ExecutionBudgetModel):
            ddl = str(CreateTable(model.__table__).compile(dialect=connection.dialect))
            await connection.exec_driver_sql(ddl.replace("::jsonb", "").replace("CREATE TABLE ", "CREATE TABLE IF NOT EXISTS "))
    r.session.add(BookProductionControlModel(project_id=r.project.id, desired_state="run", command_serial=1))
    r.session.add(ExecutionBudgetModel(scope_id=project_execution_scope(r.project), max_calls=300, max_tokens=1000000,
        calls_used=0, tokens_charged=0, status="active", deadline_at=datetime.now(UTC)+timedelta(days=1), metadata_json={}))
    await r.session.commit()
    monkeypatch.setattr(worker, "load_control_state", load_control_state)
    monkeypatch.setattr(worker, "assert_execution_scope_open", assert_execution_scope_open)
    baseline = observer.compare_repair_progress(None, await collect(r))["next_state"]
    preserved = {
        "execution_budget_audit": {"attempts": 290, "unknown": 0},
        "review_attempts": 7,
        "reader_attraction": {"score": 52, "status": "open"},
        "human_acceptance": "not_performed",
    }
    r.project.metadata_json = {
        **r.project.metadata_json, **deepcopy(preserved),
        observer.STATE_KEY: baseline,
        "self_heal_last_chapters_total": 1,
        "self_heal_last_chapters_with_draft": 1,
        "self_heal_no_progress_attempts": 4,
    }
    await r.session.commit()
    monkeypatch.setattr(worker, "get_server_session", factory(r))
    stuck = worker.StuckProject(
        project_id=r.project.id, slug=r.project.slug, reason="outline_replan_required",
        stuck_at_chapter=None, chapters_total=1, chapters_with_draft=1,
        heal_kind="outline_replan", progress_rank=(9999,),
    )
    monkeypatch.setattr(worker, "find_stuck_projects", AsyncMock(return_value=[stuck]))
    return preserved


async def metadata(r):
    async with factory(r)() as session:
        row = await session.get(ProjectModel, r.project.id)
        return deepcopy(row.metadata_json)


async def test_worker_counts_committed_native_effect_once_and_preserves_paid_debt(
    recovery, hype_case, monkeypatch,
):
    r = hype_case
    preserved = await configure_worker(r, monkeypatch)
    await committed_hype(r)
    result = await worker.heal_stuck_projects(r.settings)
    assert len(result) == 1
    after = await metadata(r)
    assert after["self_heal_no_progress_attempts"] == 0
    assert len(after[observer.STATE_KEY]["seen_effect_keys"]) == 1
    assert {key: after[key] for key in preserved} == preserved
    assert len(await worker.heal_stuck_projects(r.settings)) == 1
    repeated = await metadata(r)
    assert repeated["self_heal_no_progress_attempts"] == 1
    assert repeated[observer.STATE_KEY] == after[observer.STATE_KEY]
    assert {key: repeated[key] for key in preserved} == preserved


@pytest.mark.parametrize("outcome", ["dedup", "error"])
async def test_queue_failure_does_not_consume_native_effect_or_watchdog(
    recovery, hype_case, monkeypatch, outcome,
):
    r = hype_case
    await configure_worker(r, monkeypatch)
    await committed_hype(r)
    before = await metadata(r)
    if outcome == "dedup":
        recovery.dispatch.return_value = None
    else:
        recovery.dispatch.side_effect = RuntimeError("offline queue failure")
    assert await worker.heal_stuck_projects(r.settings) == []
    assert await metadata(r) == before
    recovery.dispatch.side_effect = None
    recovery.dispatch.return_value = "accepted-native-recovery"
    assert len(await worker.heal_stuck_projects(r.settings)) == 1
    assert (await metadata(r))["self_heal_no_progress_attempts"] == 0


async def test_uncommitted_native_repair_cannot_renew_existing_watchdog(hype_case):
    r = hype_case
    before = await collect(r)
    state = observer.compare_repair_progress(None, before)["next_state"]
    await restore(r)
    snapshot = await worker._observe_committed_repair_progress(r.session, r.settings, r.project.id)
    after, abandoned = worker._compute_heal_progress_state(
        {observer.STATE_KEY: state, "self_heal_last_chapters_total": 1,
         "self_heal_no_progress_attempts": 4},
        1, repair_progress_snapshot=snapshot,
    )
    assert abandoned and after["self_heal_no_progress_attempts"] == 5
    await r.session.rollback()


@pytest.mark.parametrize("prior", [None, {}, {"state_sha256": "corrupt"}])
async def test_missing_or_corrupt_observer_is_baseline_only(hype_case, prior):
    r = hype_case
    await committed_hype(r)
    meta = {observer.STATE_KEY: prior, "self_heal_no_progress_attempts": 4}
    after, abandoned = worker._compute_heal_progress_state(
        meta, 1, repair_progress_snapshot=await collect(r), progress_rank=(9999,),
    )
    assert abandoned and after["self_heal_no_progress_attempts"] == 5
    assert after[observer.STATE_KEY]["seen_effect_keys"]
    assert meta[observer.STATE_KEY] == prior


async def test_observation_unavailable_or_wrong_project_grants_nothing(hype_case, monkeypatch):
    r = hype_case
    for result in (RuntimeError("unavailable"), {"project_id": "foreign"}):
        reader = AsyncMock()
        if isinstance(result, Exception):
            reader.side_effect = result
        else:
            reader.return_value = result
        monkeypatch.setattr(worker, "collect_repair_progress", reader)
        assert await worker._observe_committed_repair_progress(r.session, r.settings, r.project.id) is None
    assert await worker._observe_committed_repair_progress(SimpleNamespace(), r.settings, r.project.id) is None


async def test_execution_terminal_never_observes_or_resumes(recovery, monkeypatch):
    recovery.project.metadata_json = {"execution_terminal": {"terminal": True, "code": "execution_cancelled"}}
    reader = AsyncMock(side_effect=AssertionError("terminal cannot observe"))
    monkeypatch.setattr(worker, "_observe_committed_repair_progress", reader)
    before = deepcopy(recovery.project.metadata_json)
    assert await worker.heal_stuck_projects(recovery.settings) == []
    reader.assert_not_awaited()
    assert recovery.project.metadata_json == before


async def test_valid_old_observer_state_replay_cannot_reconsume_native_effect(recovery, hype_case, monkeypatch):
    r = hype_case
    await configure_worker(r, monkeypatch)
    before = await metadata(r)
    await committed_hype(r)
    assert len(await worker.heal_stuck_projects(r.settings)) == 1
    async with factory(r)() as fresh:
        audits = list(await fresh.scalars(select(WorkflowRunModel).where(WorkflowRunModel.workflow_type == worker._REPAIR_OBSERVER_WORKFLOW)))
        assert len(audits) == 1 and audits[0].status == "completed"
        row = await fresh.get(ProjectModel, r.project.id)
        row.metadata_json = before  # Exact older valid state/hash, same native source.
        await fresh.commit()
    assert await worker.heal_stuck_projects(r.settings) == []
    after = await metadata(r)
    assert after["self_heal_no_progress_attempts"] == 5
    async with factory(r)() as fresh:
        assert len(list(await fresh.scalars(select(WorkflowRunModel).where(WorkflowRunModel.workflow_type == worker._REPAIR_OBSERVER_WORKFLOW)))) == 1


@pytest.mark.parametrize("stop", ["stop", "unknown", "exhausted"])
async def test_final_admission_sees_stop_or_ledger_transition_after_prior_scan(recovery, hype_case, monkeypatch, stop):
    r = hype_case
    await configure_worker(r, monkeypatch)
    await committed_hype(r)
    before = await metadata(r)
    original = worker._lock_heal_dispatch

    async def transitioned(session, stuck, pool):
        async with factory(r)() as other:
            if stop == "stop":
                row = await other.get(BookProductionControlModel, r.project.id)
                row.desired_state = "stop"
            else:
                row = await other.get(ExecutionBudgetModel, project_execution_scope(r.project))
                row.status = stop
            await other.commit()
        return await original(session, stuck, pool)

    monkeypatch.setattr(worker, "_lock_heal_dispatch", transitioned)
    assert await worker.heal_stuck_projects(r.settings) == []
    recovery.dispatch.assert_not_awaited()
    assert await metadata(r) == before


async def test_native_periodic_observer_job_does_not_block_its_own_owner_scan():
    import pickle
    from arq.jobs import serialize_job
    job = serialize_job("run_self_heal_task", (), {}, None, 1)
    redis = SimpleNamespace(keys=AsyncMock(side_effect=lambda match: ["arq:job:self-heal"] if match == "arq:job:*" else []),
                            get=AsyncMock(return_value=job))
    assert await worker._active_arq_project_slugs(redis) == set()
    redis.get.return_value = pickle.dumps({"f": "unknown_writer", "a": (), "k": {}})
    assert await worker._active_arq_project_slugs(redis) is None
    for malformed in ({"f": "run_self_heal_task"},
                      {"f": "run_self_heal_task", "a": None, "k": None},
                      {"f": "run_self_heal_task", "a": [], "k": {}, "t": None, "et": 1}):
        redis.get.return_value = pickle.dumps(malformed)
        assert await worker._active_arq_project_slugs(redis) is None


@pytest.mark.parametrize("bad", [None, "bad"])
async def test_lost_chapter_baseline_never_clears_an_existing_counter(bad):
    after, abandoned = worker._compute_heal_progress_state(
        {"self_heal_last_chapters_total": bad, "self_heal_no_progress_attempts": 4}, 1,
    )
    assert abandoned and after["self_heal_no_progress_attempts"] == 5
