"""Offline recovery regressions: durable progress and actual dispatch attempts."""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from bestseller.worker import self_heal as sh

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_repeated_scans_from_same_worker_use_distinct_leases(monkeypatch):
    owners = []

    async def held(_redis, owner):
        owners.append(owner)
        return False

    monkeypatch.setattr(sh, "_try_acquire_heal_lock", held)
    monkeypatch.setattr(sh, "_wait_for_scan_done", AsyncMock(return_value=False))
    redis = SimpleNamespace(get=AsyncMock(return_value=None))
    for _ in range(2):
        assert await sh.heal_stuck_projects(object(), redis=redis, worker_id="same-worker") == []
    assert owners[0] != owners[1]
    assert all(owner != "same-worker" for owner in owners)


def test_chapter_count_oscillation_does_not_renew_retry_budget() -> None:
    metadata, _ = sh._compute_heal_progress_state({}, chapters_total=10)
    for count in (9, 10, 9, 10, 9):
        metadata, abandoned = sh._compute_heal_progress_state(
            metadata, chapters_total=count, progress_fingerprint=f"renamed:{count}"
        )
    assert abandoned is True
    assert metadata["self_heal_last_chapters_total"] == 10
    assert metadata["self_heal_no_progress_attempts"] == 5


def test_draft_delivery_counts_when_all_chapter_stubs_already_exist() -> None:
    metadata = {
        "self_heal_last_chapters_total": 100,
        "self_heal_last_chapters_with_draft": 4,
        "self_heal_no_progress_attempts": 4,
    }
    metadata, abandoned = sh._compute_heal_progress_state(
        metadata, chapters_total=100, chapters_with_draft=5
    )
    assert abandoned is False
    assert metadata["self_heal_no_progress_attempts"] == 0
    assert metadata["self_heal_last_chapters_with_draft"] == 5


def test_first_draft_observation_migrates_without_renewing_budget() -> None:
    metadata, abandoned = sh._compute_heal_progress_state(
        {"self_heal_last_chapters_total": 100, "self_heal_no_progress_attempts": 4},
        chapters_total=100,
        chapters_with_draft=5,
    )
    assert abandoned is True
    assert metadata["self_heal_last_chapters_with_draft"] == 5


def test_draft_count_oscillation_is_not_new_delivery() -> None:
    metadata = {
        "self_heal_last_chapters_total": 100,
        "self_heal_last_chapters_with_draft": 5,
        "self_heal_no_progress_attempts": 0,
    }
    for count in (4, 5, 4, 5, 4):
        metadata, abandoned = sh._compute_heal_progress_state(
            metadata, chapters_total=100, chapters_with_draft=count
        )
    assert abandoned is True
    assert metadata["self_heal_last_chapters_with_draft"] == 5


def test_renamed_reason_never_renews_stalled_rank_budget() -> None:
    metadata, _ = sh._compute_heal_progress_state(
        {}, chapters_total=0, progress_rank=(6200, -4, -1, 0)
    )
    for index in range(sh.MAX_SELF_HEAL_NO_PROGRESS_ATTEMPTS):
        metadata["production_pause_reason"] = f"renamed_{index}"
        metadata, abandoned = sh._compute_heal_progress_state(
            metadata,
            chapters_total=0,
            progress_fingerprint=f"reason_{index}",
            progress_rank=(6200, -4, -1, 0),
        )
    assert abandoned is True
    assert metadata["self_heal_no_progress_attempts"] == 5


@pytest.mark.asyncio
async def test_retry_only_ghost_does_not_permanently_own_project() -> None:
    job_id = "repair:heal:recovery"
    retry_key = f"arq:retry:{job_id}"
    keys = {retry_key}
    pool = SimpleNamespace(
        exists=AsyncMock(side_effect=lambda *items: sum(item in keys for item in items)),
        zscore=AsyncMock(return_value=None),
        delete=AsyncMock(),
        zrem=AsyncMock(),
        eval=AsyncMock(side_effect=[[None, None, b"1", -2, -2, -1], 1]),
    )
    assert await sh._clear_stale_heal_job_if_needed(pool, job_id) is True
    assert retry_key in pool.eval.call_args.args
    pool.delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_scheduled_retry_is_not_a_ghost() -> None:
    job_id = "repair:heal:recovery"
    keys = {f"arq:retry:{job_id}"}
    pool = SimpleNamespace(
        exists=AsyncMock(side_effect=lambda *items: sum(item in keys for item in items)),
        zscore=AsyncMock(return_value=float("inf")),
        delete=AsyncMock(),
    )
    assert await sh._clear_stale_heal_job_if_needed(pool, job_id) is False
    pool.delete.assert_not_awaited()


@pytest.fixture
def recovery(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """No real DB, Redis, provider, filesystem export, or retry ledger writes."""
    import arq.connections

    project = SimpleNamespace(id="p", slug="recovery", metadata_json={})
    session = SimpleNamespace(get=AsyncMock(return_value=project), commit=AsyncMock(),
                              scalars=AsyncMock(return_value=[]))

    @asynccontextmanager
    async def sessions():
        yield session

    stuck = sh.StuckProject(
        project_id=project.id,
        slug=project.slug,
        reason="missing_drafts",
        stuck_at_chapter=5,
        chapters_total=100,
        chapters_with_draft=5,
    )
    pool = SimpleNamespace(
        exists=AsyncMock(return_value=0),
        zscore=AsyncMock(return_value=None),
        aclose=AsyncMock(),
    )
    dispatch = AsyncMock(return_value="autowrite:heal:recovery")
    monkeypatch.setattr(sh, "get_server_session", sessions)
    monkeypatch.setattr(sh, "find_stuck_projects", AsyncMock(return_value=[stuck]))
    monkeypatch.setattr(sh, "reap_orphan_workflow_runs", AsyncMock(return_value=0))
    # This fixture models a successful, empty ownership read. Unavailable Redis
    # is UNKNOWN in the native owner contract and cannot authorize dispatch.
    monkeypatch.setattr(sh, "_active_arq_project_slugs", AsyncMock(return_value=set()))
    monkeypatch.setattr(sh, "assert_execution_scope_open", AsyncMock())
    monkeypatch.setattr(sh, "load_control_state", AsyncMock(
        return_value=SimpleNamespace(halted=False, command_serial=1)))
    monkeypatch.setattr(sh, "halted_project_ids", AsyncMock(return_value=set()))
    monkeypatch.setattr(sh, "_has_active_continuation_pipeline_run", AsyncMock(return_value=False))
    monkeypatch.setattr(sh, "_has_active_pipeline_run", AsyncMock(return_value=False))
    monkeypatch.setattr(
        sh, "_clear_auto_resumable_generation_gate_pause", AsyncMock(return_value=False)
    )
    monkeypatch.setattr(sh, "is_project_delete_tombstoned", lambda *_: False)
    monkeypatch.setattr(sh, "_requeue_stuck_project", dispatch)
    monkeypatch.setattr(arq.connections, "create_pool", AsyncMock(return_value=pool))
    settings = SimpleNamespace(redis=SimpleNamespace(url="redis://unused:6379/0"))
    return SimpleNamespace(
        project=project, session=session, pool=pool, dispatch=dispatch, settings=settings
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["dedup", "error"])
async def test_no_attempt_charged_when_dispatch_did_not_happen(recovery, outcome) -> None:
    if outcome == "dedup":
        recovery.dispatch.return_value = None
    else:
        recovery.dispatch.side_effect = RuntimeError("offline queue failure")
    for _ in range(8):
        assert await sh.heal_stuck_projects(recovery.settings) == []
    assert recovery.project.metadata_json == {}
    recovery.session.commit.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("heal_kind", ["autowrite", "repair", "project_pipeline", "outline_replan"])
async def test_pending_owner_near_limit_does_not_acquire_terminal_debt(
    recovery, monkeypatch, heal_kind
) -> None:
    recovery.project.metadata_json = {
        "self_heal_last_chapters_total": 100,
        "self_heal_no_progress_attempts": 4,
    }
    monkeypatch.setattr(
        sh,
        "find_stuck_projects",
        AsyncMock(
            return_value=[
                sh.StuckProject(
                    project_id="p",
                    slug="recovery",
                    reason="missing_drafts",
                    stuck_at_chapter=5,
                    chapters_total=100,
                    chapters_with_draft=5,
                    heal_kind=heal_kind,
                )
            ]
        ),
    )
    recovery.pool.exists.return_value = 1
    # Future score: scheduled backlog, never a stale in-progress ghost.
    recovery.pool.zscore.return_value = float("inf")
    for _ in range(8):
        assert await sh.heal_stuck_projects(recovery.settings) == []
    assert recovery.project.metadata_json["self_heal_no_progress_attempts"] == 4
    assert "self_heal_abandoned" not in recovery.project.metadata_json
    recovery.dispatch.assert_not_awaited()


@pytest.mark.asyncio
async def test_dispatch_records_draft_delivery_before_stall_limit(recovery) -> None:
    recovery.project.metadata_json = {
        "self_heal_last_chapters_total": 100,
        "self_heal_last_chapters_with_draft": 4,
        "self_heal_no_progress_attempts": 4,
    }
    dispatched = await sh.heal_stuck_projects(recovery.settings)
    assert len(dispatched) == 1
    assert recovery.project.metadata_json["self_heal_no_progress_attempts"] == 0
    assert recovery.project.metadata_json["self_heal_last_chapters_with_draft"] == 5


@pytest.mark.asyncio
async def test_execution_stop_stays_terminal_despite_new_delivery(recovery) -> None:
    recovery.project.metadata_json = {
        "execution_terminal": {"terminal": True, "code": "execution_cancelled"},
        "self_heal_last_chapters_total": 1,
    }
    original = dict(recovery.project.metadata_json)
    assert await sh.heal_stuck_projects(recovery.settings) == []
    assert recovery.project.metadata_json == original
    recovery.dispatch.assert_not_awaited()
