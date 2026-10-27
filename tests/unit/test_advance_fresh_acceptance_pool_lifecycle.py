"""Offline lifecycle checks for the operator; all engines and service calls are fakes."""

from contextlib import nullcontext
from copy import deepcopy
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy.ext import asyncio as sqlalchemy_async

from bestseller import settings as settings_module
from bestseller.infra.db import session as database
from bestseller.services import chapter_outline_readiness

PATH = (
    Path(__file__).resolve().parents[2]
    / "reports/continuous-recovery-2026-10-16/advance_fresh_acceptance.py"
)
spec = importlib.util.spec_from_file_location("advance_pool_lifecycle_subject", PATH)
operator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(operator)
pytestmark = pytest.mark.unit


@pytest.mark.parametrize("stage", ["foundation", "window", "outline"])
@pytest.mark.parametrize("borrowed", [False, True], ids=["owned-pool", "existing-pool"])
@pytest.mark.parametrize("failure", [False, True], ids=["success", "service-failure"])
async def test_paid_stage_pool_initialized_before_sessions_and_only_owned_pool_closed(
    stage, borrowed, failure, monkeypatch, tmp_path
):
    events, receipts = [], []
    pool = object()
    monkeypatch.setattr(database, "_global_engine", pool if borrowed else None)
    settings = settings_module.load_settings().model_copy(deep=True)
    settings.generation.target_chapters = 30
    settings.generation.words_per_chapter.target = 2600
    settings.pipeline.rolling_outline_window_size = 8
    settings.pipeline.enable_rolling_outline = True
    monkeypatch.setattr(settings_module, "load_settings", lambda: settings)
    monkeypatch.setattr(settings_module, "apply_runtime_llm_profile", lambda value: value)

    async def init_db(value):
        assert value is settings and database._global_engine is None
        events.append("init_pool")
        database._global_engine = pool

    async def shutdown_db():
        assert database._global_engine is pool
        events.append("shutdown_pool")
        database._global_engine = None

    async def dispose():
        events.append("dispose_local_engine")

    init = AsyncMock(side_effect=init_db)
    shutdown = AsyncMock(side_effect=shutdown_db)
    engine = SimpleNamespace(dispose=AsyncMock(side_effect=dispose))
    monkeypatch.setattr(database, "init_db", init)
    monkeypatch.setattr(database, "shutdown_db", shutdown)
    monkeypatch.setattr(database, "create_engine", lambda _: engine)

    project = SimpleNamespace(id=uuid4())

    class Session:
        async def __aenter__(self):
            assert database._global_engine is pool
            events.append("session_enter")
            return self

        async def __aexit__(self, *args):
            events.append("session_exit")

        async def commit(self):
            events.append("commit")

        async def get(self, *args):
            return project

    monkeypatch.setattr(sqlalchemy_async, "async_sessionmaker", lambda *a, **k: Session)

    async def rollback(*args):
        assert database._global_engine is pool
        events.append("rollback")

    monkeypatch.setattr(database, "_rollback_and_save_failures", AsyncMock(side_effect=rollback))
    before = {
        "project_id": str(project.id),
        "chapters": [
            {
                "number": 1,
                "metadata": {
                    "outline_prefix_readiness": {"artifact_id": str(uuid4())},
                },
            }
        ],
    }

    async def observation(*args):
        assert database._global_engine is pool
        events.append("observation")
        return deepcopy(before)

    async def perform(*args):
        assert database._global_engine is pool
        events.append("perform")
        if failure:
            raise RuntimeError("synthetic_service_failure")
        return {"synthetic": True}

    monkeypatch.setattr(operator, "observation", observation)
    monkeypatch.setattr(operator, "perform", perform)
    monkeypatch.setattr(operator, "reviewed_inspection", lambda *a: deepcopy(before))
    monkeypatch.setattr(operator, "admit_mutation", lambda *a: None)
    monkeypatch.setattr(operator, "verify_readback", lambda *a: events.append("verify_readback"))
    monkeypatch.setattr(operator, "gateway_guard", lambda *a: nullcontext())
    monkeypatch.setattr(
        chapter_outline_readiness, "verify_outline_prefix_readiness", AsyncMock(return_value=None)
    )
    state = {"scope": "conception:" + str(uuid4())}
    monkeypatch.setattr(operator, "identity", lambda *a: (state, {}, {}))
    plan = {
        "identity": {},
        "plan_sha256": "reviewed-plan",
        "sources_sha256": "reviewed-sources",
        "operator_sha256": "reviewed-operator",
    }
    monkeypatch.setattr(operator, "proposal", lambda *a: deepcopy(plan))

    def append_receipt(directory, value):
        assert database._global_engine is pool
        receipts.append(deepcopy(value))
        events.append("receipt")
        return tmp_path / "synthetic-receipt.json"

    monkeypatch.setattr(operator, "append_receipt", append_receipt)
    monkeypatch.setattr(operator, "file_hash", lambda *a: "synthetic-receipt-hash")
    args = operator.parser().parse_args(
        [
            "--stage",
            stage,
            "--reviewed-plan-sha256",
            "reviewed-plan",
            "--idempotency-key",
            "synthetic-step",
            "--parent-review-ref",
            "offline-review",
            *(["--chapter", "1"] if stage == "outline" else []),
        ]
    )
    if failure:
        with pytest.raises(RuntimeError, match="synthetic_service_failure"):
            await operator.execute(args, plan)
        assert "rollback" in events and "commit" not in events
        assert receipts[-1]["status"] == "failed"
    else:
        await operator.execute(args, plan)
        assert "commit" in events and "verify_readback" in events
        assert receipts[-1]["status"] == "completed"
    assert events.count("observation") == 2  # Independent readback before pool disposal.
    engine.dispose.assert_awaited_once_with()
    if borrowed:
        init.assert_not_awaited()
        shutdown.assert_not_awaited()
        assert database._global_engine is pool
        assert events[-1] == "dispose_local_engine"
    else:
        init.assert_awaited_once_with(settings)
        shutdown.assert_awaited_once_with()
        assert events[0] == "init_pool"
        assert events[-2:] == ["shutdown_pool", "dispose_local_engine"]
        assert database._global_engine is None
