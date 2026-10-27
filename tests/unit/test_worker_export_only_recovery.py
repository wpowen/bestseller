"""Queue boundary only; native grades/publisher are separately integrated in PG."""
# ruff: noqa: F811
from contextlib import asynccontextmanager
from copy import deepcopy
import hashlib
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.schema import CreateTable
from test_self_heal_repair_progress import factory, hype_case, rig  # noqa: F401

from bestseller.infra.db.models import BookProductionControlModel
from bestseller.services.export_only_recovery import ExportOnlyRecoveryResult, ExportOnlyStatus
from bestseller.worker import export_recovery as worker
from bestseller.worker import self_heal, tasks

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.fixture
async def candidate(hype_case, monkeypatch):
    r = hype_case
    async with r.session.bind.begin() as conn:
        ddl = str(CreateTable(BookProductionControlModel.__table__).compile(dialect=conn.dialect))
        await conn.exec_driver_sql(ddl.replace("::jsonb", ""))
    r.project.target_chapters = 1
    r.project.status = "completed"
    # These labels only exercise candidate census. Admission is an explicit
    # external seam, never represented as actual grade/knowledge success here.
    r.draft.promotion_state = "promoted"
    await r.session.commit()
    r.session.add(BookProductionControlModel(project_id=r.project.id, desired_state="run", command_serial=1))
    await r.session.commit()
    monkeypatch.setattr(worker, "get_server_session", factory(r))
    monkeypatch.setattr(worker, "get_settings", lambda: r.settings)
    monkeypatch.setattr(self_heal, "_active_arq_project_slugs", AsyncMock(return_value=set()))
    checked = AsyncMock(return_value=ExportOnlyRecoveryResult(ExportOnlyStatus.ELIGIBLE, r.project.id, "boundary-seam"))
    monkeypatch.setattr(worker, "preflight_export_only_recovery", checked)
    r.checked = checked
    r.redis = NS(enqueue_job=AsyncMock(return_value=object()))
    return r


async def test_export_candidate_uses_only_special_job_without_touching_watchdog(candidate):
    r = candidate
    before = deepcopy(r.project.metadata_json)
    queued = await worker.enqueue_export_only_recoveries(r.settings, r.redis)
    assert len(queued) == 1 and queued[0]["heal_kind"] == "export_only"
    r.checked.assert_awaited_once()
    call = r.redis.enqueue_job.call_args
    assert call.args == ("run_export_only_recovery_task",)
    assert call.kwargs["payload"] == {"project_id": str(r.project.id), "project_slug": r.project.slug}
    assert call.kwargs["_job_id"] == "export-only:heal:" + r.project.slug
    await r.session.refresh(r.project)
    assert r.project.metadata_json == before


@pytest.mark.parametrize("blocked", ["unknown_owner", "live_owner", "stop", "blocked", "already_complete", "dedup", "partial", "archived"])
async def test_no_export_enqueue_for_unproven_or_owned_book(candidate, monkeypatch, blocked):
    r = candidate
    if blocked in {"unknown_owner", "live_owner"}:
        monkeypatch.setattr(self_heal, "_active_arq_project_slugs", AsyncMock(return_value=None if blocked == "unknown_owner" else {r.project.slug}))
    elif blocked == "stop":
        control = await r.session.get(BookProductionControlModel, r.project.id)
        control.desired_state = "stop"
        await r.session.commit()
    elif blocked in {"blocked", "already_complete"}:
        r.checked.return_value = ExportOnlyRecoveryResult(ExportOnlyStatus(blocked), r.project.id, "boundary-seam")
    elif blocked == "dedup":
        r.redis.enqueue_job.return_value = None
    elif blocked == "partial":
        r.draft.promotion_state = "candidate"
        await r.session.commit()
    else:
        r.project.status = "archived"
        await r.session.commit()
    assert await worker.enqueue_export_only_recoveries(r.settings, r.redis) == []
    if blocked != "dedup":
        r.redis.enqueue_job.assert_not_awaited()


async def test_handler_rechecks_native_helper_with_fresh_factory_under_acquired_owner(candidate, monkeypatch):
    r = candidate
    calls = []

    @asynccontextmanager
    async def guard(redis, slug, token, state):
        assert redis is r.redis and slug == r.project.slug and state == "acquired"
        calls.append("owned")
        yield
        calls.append("released")

    async def native(factory_arg, settings, **kwargs):
        assert calls == ["owned"]
        assert kwargs == {"project_id": r.project.id, "requested_by": worker.REQUESTED_BY}
        async with factory_arg() as fresh:
            assert fresh is not r.session and fresh.bind is r.session.bind
        calls.append("native")
        return ExportOnlyRecoveryResult(ExportOnlyStatus.COMPLETED, r.project.id, "boundary-seam")

    monkeypatch.setattr(tasks, "_acquire_pipeline_live", AsyncMock(return_value="acquired"))
    monkeypatch.setattr(tasks, "_pipeline_live_guard", guard)
    monkeypatch.setattr(worker, "run_export_only_recovery", native)
    result = await worker.run_export_only_recovery_task({"redis": r.redis}, "original-export-job", {"project_id": str(r.project.id), "project_slug": r.project.slug})
    assert result["status"] == "completed" and result["provider_calls"] == 0
    assert calls == ["owned", "native", "released"]


@pytest.mark.parametrize("state", ["held_by_other", "degraded"])
async def test_handler_never_falls_back_to_prose_when_lease_unavailable(candidate, monkeypatch, state):
    r = candidate
    monkeypatch.setattr(tasks, "_acquire_pipeline_live", AsyncMock(return_value=state))
    native = AsyncMock(side_effect=AssertionError("unowned cannot export"))
    monkeypatch.setattr(worker, "run_export_only_recovery", native)
    result = await worker.run_export_only_recovery_task({"redis": r.redis}, "export-job", {"project_id": str(r.project.id), "project_slug": r.project.slug})
    assert result["status"] == "blocked"
    native.assert_not_awaited()


async def test_file_hint_detects_missing_corrupt_outputs_without_becoming_authority(tmp_path):
    output = tmp_path / "book" / "exports"
    output.mkdir(parents=True)
    records = {}
    for key, name in {"markdown_path": "fanqie-short.md", "readiness_path": "signing-readiness.json", "source_receipt_path": "adopted-source.json"}.items():
        path = output / name
        path.write_text(name)
        records[key] = {"sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    project = NS(slug="book", project_type="fanqie_short", metadata_json={"fanqie_whole_export_receipt": {"delivery_verification": {"files": records}}})
    settings = NS(output=NS(base_dir=tmp_path))
    assert not worker._export_needs_probe(project, settings)
    (output / "fanqie-short.md").write_text("corrupt")
    assert worker._export_needs_probe(project, settings)
    (output / "fanqie-short.md").unlink()
    assert worker._export_needs_probe(project, settings)
    project.slug = "../outside"
    assert not worker._export_needs_probe(project, settings)
