"""Normal quickstart preserves the validated grant before dispatch.

Service spies delimit web orchestration; native grant/ledger tests are separate.
No worker, production configuration, model or network is started here.
"""
from contextlib import asynccontextmanager
from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

import pytest

from bestseller.services import execution_authorization, model_catalog
from bestseller.settings import load_settings
from bestseller.web import server

pytestmark = pytest.mark.unit


@pytest.fixture
def web_case(tmp_path, monkeypatch):
    settings = load_settings(env={})
    entry = model_catalog.ModelCatalogEntry(
        id="offline-book", display_name="Offline", vendor="fixture",
        model="fixture/book", api_base="https://offline.invalid/v1", available=True,
    )
    monkeypatch.setattr(server, "load_settings", lambda: settings)
    monkeypatch.setattr(model_catalog, "get_model_catalog_entry", lambda _id: entry)
    monkeypatch.setattr(model_catalog, "default_new_project_model_id", lambda _: entry.id)
    engine = object()

    @asynccontextmanager
    async def scope(_settings):
        yield SimpleNamespace(bind=engine)

    monkeypatch.setattr(server, "session_scope", scope)
    payload = {"genre_key": "realistic-life", "creation_mode": "fanqie_short",
               "length_key": "fanqie-short-8k", "concept_seed": "都市现实、家庭情感",
               "llm_model_id": entry.id, "future_input": {"ordered": [2, 1]}}
    grant = {"grant_id": str(uuid4()), "task_id": str(uuid4()),
             "payload_sha256": "a" * 64, "model": entry.model, "endpoint": entry.api_base,
             "max_calls": 400, "max_tokens": 20_000_000,
             "created_at": "2026-10-29T00:00:00+00:00", "deadline_at": "2026-10-30T00:00:00+00:00"}
    grant["scope_id"] = "conception:" + grant["task_id"]
    payload["execution_budget_grant_id"] = grant["grant_id"]
    calls, dispatches = [], []

    async def consume(actual_engine, **kwargs):
        assert actual_engine is engine
        calls.append(deepcopy(kwargs))
        return deepcopy(grant)

    monkeypatch.setattr(execution_authorization, "consume_new_book_budget", consume, raising=False)
    manager = server.WebTaskManager(persist_path=tmp_path / "tasks.json")

    class Thread:
        def __init__(self, *, target, args, daemon):
            self.args = args

        def start(self):
            assert calls, "worker dispatched before native grant validation"
            import json
            saved = json.loads(manager._persist_path.read_text())
            assert grant["task_id"] in str(saved), "worker dispatched before durable task identity"
            dispatches.append(self.args)

    monkeypatch.setattr(server, "threading", SimpleNamespace(
        Thread=Thread, Lock=server.threading.Lock,
        BoundedSemaphore=server.threading.BoundedSemaphore,
    ))
    return SimpleNamespace(manager=manager, payload=payload, grant=grant, calls=calls,
                           dispatches=dispatches, entry=entry, engine=engine)


def test_complete_original_payload_is_consumed_before_native_dispatch(web_case):
    case = web_case
    before = deepcopy(case.payload)
    result = case.manager.create_quickstart_task(case.payload)
    assert case.calls == [{
        "payload": before, "model": case.entry.model, "endpoint": case.entry.api_base,
    }]
    assert result["task_id"] == case.grant["task_id"]
    assert case.payload == before
    assert len(case.dispatches) == 1
    stored = case.manager._tasks[result["task_id"]]
    assert stored.payload["new_book_budget_grant"] == case.grant
    assert stored.project_slug.endswith(case.grant["task_id"])


def test_duplicate_post_reuses_native_identity_and_does_not_dispatch_twice(web_case):
    case = web_case
    first = case.manager.create_quickstart_task(case.payload)
    second = case.manager.create_quickstart_task(deepcopy(case.payload))
    assert first["task_id"] == second["task_id"] == case.grant["task_id"]
    assert len(case.calls) == 2 and len(case.dispatches) == 1
    assert len(case.manager._tasks) == 1


@pytest.mark.parametrize("status", ["completed", "failed", "cancelled"])
def test_repeat_post_does_not_rearm_terminal_task(web_case, status):
    case = web_case
    first = case.manager.create_quickstart_task(case.payload)
    original = case.manager._tasks[first["task_id"]]
    original.status = status
    original.error = "original terminal evidence"
    before = deepcopy(original.to_dict())
    result = case.manager.create_quickstart_task(case.payload)
    assert result["status"] == status
    assert original.to_dict() == before
    assert len(case.dispatches) == 1


def test_grant_rejection_never_dispatches_or_falls_back_to_random_identity(web_case, monkeypatch):
    case = web_case

    async def reject(*args, **kwargs):
        raise ValueError("new_book_budget_payload_mismatch")

    monkeypatch.setattr(execution_authorization, "consume_new_book_budget", reject)
    with pytest.raises(ValueError, match="new_book_budget_payload_mismatch"):
        case.manager.create_quickstart_task(case.payload)
    assert case.dispatches == [] and case.manager._tasks == {}


def test_save_failure_keeps_retry_identity_without_in_memory_zombie(web_case, monkeypatch):
    case = web_case
    save = case.manager._save_to_disk

    def broken():
        raise OSError("offline_task_save_failed")

    monkeypatch.setattr(case.manager, "_save_to_disk", broken)
    with pytest.raises(OSError, match="offline_task_save_failed"):
        case.manager.create_quickstart_task(case.payload)
    assert not case.manager._tasks and not case.dispatches
    monkeypatch.setattr(case.manager, "_save_to_disk", save)
    result = case.manager.create_quickstart_task(case.payload)
    assert result["task_id"] == case.grant["task_id"] and len(case.dispatches) == 1


def test_original_request_may_not_import_internal_grant_binding(web_case):
    case = web_case
    case.payload["new_book_budget_grant"] = deepcopy(case.grant)
    with pytest.raises(ValueError, match="new_book_budget_internal_binding_forbidden"):
        case.manager.create_quickstart_task(case.payload)
    assert case.calls == [] and case.dispatches == []


def test_ungranted_quickstart_keeps_existing_entry_and_does_not_call_grant_service(
    web_case, monkeypatch,
):
    case = web_case
    case.payload.pop("execution_budget_grant_id")
    forwarded = []
    monkeypatch.setattr(
        case.manager, "create_autowrite_task", lambda payload: forwarded.append(payload) or {},
    )
    case.manager.create_quickstart_task(case.payload)
    assert case.calls == [] and len(forwarded) == 1
    assert "new_book_budget_grant" not in forwarded[0]


def test_concurrent_exact_posts_claim_only_one_local_dispatch(web_case):
    from concurrent.futures import ThreadPoolExecutor

    case = web_case
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(case.manager.create_quickstart_task,
                                [deepcopy(case.payload), deepcopy(case.payload)]))
    assert {row["task_id"] for row in results} == {case.grant["task_id"]}
    assert len(case.dispatches) == 1 and len(case.manager._tasks) == 1


def test_thread_start_failure_retries_persisted_envelope_once(web_case, monkeypatch):
    case = web_case
    thread = server.threading.Thread

    class BrokenThread(thread):
        def start(self):
            raise RuntimeError("offline_thread_start_failed")

    monkeypatch.setattr(server.threading, "Thread", BrokenThread)
    with pytest.raises(RuntimeError, match="offline_thread_start_failed"):
        case.manager.create_quickstart_task(case.payload)
    original = deepcopy(case.manager._tasks[case.grant["task_id"]].to_dict())
    assert case.dispatches == []
    monkeypatch.setattr(server.threading, "Thread", thread)
    result = case.manager.create_quickstart_task(case.payload)
    assert result["task_id"] == original["task_id"]
    assert case.manager._tasks[case.grant["task_id"]].to_dict() == original
    assert len(case.dispatches) == 1


def test_restart_post_does_not_race_native_startup_recovery(web_case):
    case = web_case
    case.manager.create_quickstart_task(case.payload)
    restarted = server.WebTaskManager(persist_path=case.manager._persist_path)
    assert case.grant["task_id"] in restarted._pending_auto_resume_ids
    result = restarted.create_quickstart_task(case.payload)
    assert result["task_id"] == case.grant["task_id"]
    assert len(case.dispatches) == 1
