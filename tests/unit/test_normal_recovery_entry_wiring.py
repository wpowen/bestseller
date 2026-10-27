"""Execute normal Web/ARQ entry wrappers; synthetic SQL/prepare/pipe seams only.

No AST extraction, unwrapping, application boot, DB, provider or real queue.
Native owner acquisition/guard and decorated worker/manager methods run intact.
Commit observations concern the explicit prepare handoff before the pipeline;
they make no claim about real session-context commits after pipeline work.
"""
import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from types import SimpleNamespace as NS
from uuid import uuid4

import httpx
import pytest
from sqlalchemy.engine import Engine

from bestseller.services import chapter_review_reconciliation as reconciliation
from bestseller.services import pipelines, projects, story_source, web_pipeline_owner, workflows
from bestseller.services import autonomous_continuation, llm
from bestseller.services.truth_version import TruthVersionStaleError
from bestseller.web import server
from bestseller.worker import tasks

pytestmark = pytest.mark.unit
SLUG = "normal-recovery-wiring-test"


class MemoryRedis:
    """Redis wire collaborator, not a Redis server/concurrency certificate."""
    def __init__(self): self.store = {}; self.closed = False
    async def set(self, key, value, nx=False, ex=None):
        if nx and key in self.store: return False
        self.store[key] = value
        return True
    async def eval(self, script, _count, key, owner, *_args):
        if self.store.get(key) != owner: return 0
        if "'DEL'" in script: self.store.pop(key, None)
        return 1
    async def delete(self, key): self.store.pop(key, None)
    async def aclose(self, **_kwargs): self.closed = True


@pytest.fixture
def rig(monkeypatch, tmp_path):
    r = NS(events=[], provider_calls=[], pipeline_calls=[], workflow_calls=[], prepare_status="prepared",
           prepare_error=False, truth_stale=False, truth_refreshed=False, deny_after_refresh=False,
           sessions=[], redis=MemoryRedis())
    r.project = NS(id=uuid4(), slug=SLUG, title="Synthetic existing book", genre="fiction", sub_genre=None,
        audience=None, language="zh-CN", project_type="linear", target_word_count=10000, target_chapters=4,
        metadata_json={"execution_scope_id": "normal-entry-wiring-test", "old_failure": "retain", "calls": 290})
    r.original_project = deepcopy(vars(r.project))
    r.settings = NS(quality=NS(draft_mode=False), redis=NS(url="redis://synthetic-test:1/0",
        socket_timeout_seconds=1, socket_connect_timeout_seconds=1, pool_max_connections=2))

    def forbidden(*_args, **_kwargs):
        r.provider_calls.append(True)
        raise AssertionError("normal wiring test attempted real model/HTTP/DB")
    monkeypatch.setattr(llm, "complete_text", forbidden)
    monkeypatch.setattr(llm, "_call_litellm", forbidden)
    monkeypatch.setattr(httpx.Client, "send", forbidden)
    monkeypatch.setattr(httpx.AsyncClient, "send", forbidden)
    monkeypatch.setattr(Engine, "connect", forbidden)
    monkeypatch.setattr(web_pipeline_owner.Redis, "from_url", lambda *_a, **_k: r.redis)
    monkeypatch.setattr(server, "load_settings", lambda: r.settings)
    monkeypatch.setattr(tasks, "get_settings", lambda: r.settings)

    class Session:
        async def scalar(self, _query): return r.project
        async def commit(self): r.events.append("commit")
    @asynccontextmanager
    async def session_scope(*_args):
        session = Session(); r.sessions.append(session)
        yield session
    monkeypatch.setattr(server, "session_scope", session_scope)
    monkeypatch.setattr(tasks, "get_server_session", session_scope)

    async def get_project(session, slug):
        assert slug == SLUG
        r.events.append("existing_project")
        return r.project
    monkeypatch.setattr(server, "get_project_by_slug", get_project)
    monkeypatch.setattr(projects, "get_project_by_slug", get_project)

    async def prepare(session, settings, *, project_id, requested_by):
        assert session in r.sessions and settings is r.settings and project_id == r.project.id
        assert r.redis.store.get(tasks._pipeline_live_key(SLUG)) not in (None, "foreign-owner")
        r.events.append("prepare:" + requested_by)
        if r.prepare_error or (r.deny_after_refresh and r.truth_refreshed):
            raise ValueError("SYNTHETIC retained review preparation rejected")
        return {"status": r.prepare_status, "project_id": str(project_id), "provider_calls": 0}
    monkeypatch.setattr(reconciliation, "prepare_native_review_resume", prepare)

    async def new_workflow(*_args, **_kwargs):
        r.workflow_calls.append(r.project.id); r.events.append("new_workflow")
        return NS(id=uuid4())
    monkeypatch.setattr(workflows, "create_workflow_run", new_workflow)
    result = NS(project_id=r.project.id, workflow_run_id=uuid4(), requires_human_review=False, final_verdict="pass",
        model_dump=lambda mode="json": {"project_id": str(r.project.id), "project_slug": SLUG,
                                       "requires_human_review": False, "final_verdict": "pass"})
    async def pipe(*args, **kwargs):
        session = kwargs.get("session", args[0] if args else None)
        assert session in r.sessions
        slug = kwargs.get("project_slug") or (kwargs.get("project_payload").slug
            if kwargs.get("project_payload") else args[2] if len(args) > 2 else None)
        assert slug == SLUG
        await tasks._assert_pipeline_live_owner()  # real acquired-owner guard
        r.events.append("pipeline"); r.pipeline_calls.append(r.project.id)
        await workflows.create_workflow_run(session)
        if r.truth_stale and len(r.pipeline_calls) == 1:
            raise TruthVersionStaleError(project_slug=SLUG, truth_version=2, stale_components=())
        return result
    for module, name in ((server, "run_autowrite_pipeline"), (server, "run_project_repair"),
                         (pipelines, "run_autowrite_pipeline"), (pipelines, "run_project_pipeline")):
        monkeypatch.setattr(module, name, pipe)
    for name in ("repair_prewrite_material_from_task", "run_chapter_pipeline", "apply_chapter_revision_task"):
        monkeypatch.setattr(server, name, pipe)

    async def unchanged(*_a, **_kw): return None
    async def open_scope(*_a, **_kw): return None
    async def no_closure(*_a, **_kw): return False
    async def accepted_premise(session, project):
        assert project is r.project
        return "SYNTHETIC existing accepted premise"
    async def refresh(session, settings, project, **_kwargs):
        assert session in r.sessions and project is r.project
        r.events.append("truth_refresh"); r.truth_refreshed = True
    for name in ("_skip_archived_project_if_needed", "_skip_halted_project_if_needed", "_skip_outline_replan_project_if_needed"):
        monkeypatch.setattr(tasks, name, unchanged)
    monkeypatch.setattr(tasks, "assert_execution_scope_open", open_scope)
    monkeypatch.setattr(tasks, "_enqueue_quality_closure_if_needed", no_closure)
    monkeypatch.setattr(story_source, "load_accepted_project_premise", accepted_premise)
    monkeypatch.setattr(pipelines, "_refresh_stale_truth_materializations_for_resume", refresh)
    monkeypatch.setattr(autonomous_continuation, "record_window_continuation", unchanged)
    monkeypatch.setattr(server, "_close_creation_workflow_after_task_exit", lambda *_a, **_kw: None)
    class Reporter:
        async def emit(self, *_args, **_kwargs): pass
    monkeypatch.setattr(tasks, "RedisProgressReporter", lambda *_a, **_kw: Reporter())
    monkeypatch.setattr(tasks, "make_sync_callback", lambda _: lambda *_a, **_kw: None)

    r.manager = server.WebTaskManager(persist_path=tmp_path / "wiring-test-tasks.json")
    r.task_id = "normal-entry-existing-task"
    r.manager._tasks[r.task_id] = server.WebTaskState(task_id=r.task_id, task_type="autowrite", status="queued",
        created_at="2026-10-20T00:00:00Z", updated_at="2026-10-20T00:00:00Z", project_slug=SLUG)
    r.payload = {"slug": SLUG, "project_slug": SLUG, "title": r.project.title, "genre": r.project.genre,
        "premise": "SYNTHETIC existing premise", "target_words": 10000, "target_chapters": 4,
        "_run_conception": False, "export_markdown": True, "chapter_number": 1,
        "rewrite_task_id": str(uuid4()), "operation": "regenerate"}
    yield r
    assert not r.provider_calls, "A swallowed provider/DB error cannot count as guard success"
    assert vars(r.project) == r.original_project


def web(r, kind):
    getattr(r.manager, {"autowrite": "_run_autowrite_worker", "repair": "_run_repair_worker",
        "prewrite": "_run_prewrite_repair_worker", "revision": "_run_chapter_revision_worker"}[kind])(r.task_id, r.payload)


def worker(r, kind):
    operation = tasks.run_autowrite_task if kind == "autowrite" else tasks.run_project_pipeline_task
    return asyncio.run(operation({"redis": r.redis}, kind + ":existing-native-task", {"project_slug": SLUG}))


def reset(r):
    r.events.clear(); r.pipeline_calls.clear(); r.workflow_calls.clear()
    r.redis.store.clear(); r.redis.closed = False


def trace(r):
    return [event for event in r.events if event == "commit" or event == "pipeline"
            or event == "new_workflow" or event == "truth_refresh" or event.startswith("prepare:")]


def test_actual_web_owner_denial_never_enters_any_normal_runner(rig):
    for kind in ("autowrite", "repair", "prewrite", "revision"):
        reset(rig); rig.redis.store[tasks._pipeline_live_key(SLUG)] = "foreign-owner"
        web(rig, kind)
        assert rig.manager.get_task(rig.task_id)["status"] == "cancelled", kind
        assert not rig.pipeline_calls and not rig.workflow_calls and not trace(rig), kind
        assert rig.redis.closed and rig.redis.store[tasks._pipeline_live_key(SLUG)] == "foreign-owner"


def test_actual_decorated_worker_owner_denial_never_prepares_or_enters_pipeline(rig):
    for kind in ("autowrite", "project"):
        reset(rig); rig.redis.store[tasks._pipeline_live_key(SLUG)] = "foreign-owner"
        result = worker(rig, kind)
        assert result["status"].startswith("skipped_"), kind
        assert not trace(rig) and not rig.workflow_calls, kind
        assert rig.redis.store[tasks._pipeline_live_key(SLUG)] == "foreign-owner"


def test_web_prepare_commit_precedes_pipeline_for_same_existing_project(rig):
    for kind in ("autowrite", "repair"):
        for status in ("prepared", "unchanged"):
            reset(rig); rig.prepare_status = status
            web(rig, kind)
            actor = "web-ui" if kind == "autowrite" else "web-ui-repair"
            expected = ["prepare:" + actor] + (["commit"] if status == "prepared" else []) + ["pipeline", "new_workflow"]
            assert trace(rig) == expected, (kind, status, rig.events)
            assert rig.pipeline_calls == rig.workflow_calls == [rig.project.id]
            assert rig.manager.get_task(rig.task_id)["status"] == "completed"


def test_worker_prepare_commit_precedes_pipeline_for_same_existing_project(rig):
    for kind in ("autowrite", "project"):
        for status in ("prepared", "unchanged"):
            reset(rig); rig.prepare_status = status
            result = worker(rig, kind)
            actor = "worker-autowrite" if kind == "autowrite" else "worker-project"
            expected = ["prepare:" + actor] + (["commit"] if status == "prepared" else []) + ["pipeline", "new_workflow"]
            assert trace(rig) == expected, (kind, status, rig.events)
            assert result["project_id"] == str(rig.project.id)
            assert rig.pipeline_calls == rig.workflow_calls == [rig.project.id]


def test_web_prepare_exception_never_creates_new_workflow_or_enters_pipeline(rig):
    rig.prepare_error = True
    for kind in ("autowrite", "repair"):
        reset(rig); web(rig, kind)
        assert rig.manager.get_task(rig.task_id)["status"] == "failed", kind
        assert not rig.pipeline_calls and not rig.workflow_calls and "commit" not in rig.events
        assert len([e for e in rig.events if e.startswith("prepare:")]) == 1
        assert not rig.redis.store


def test_worker_prepare_exception_never_creates_new_workflow_or_enters_pipeline(rig):
    rig.prepare_error = True
    for kind in ("autowrite", "project"):
        reset(rig)
        with pytest.raises(ValueError, match="SYNTHETIC retained review preparation rejected"):
            worker(rig, kind)
        assert not rig.pipeline_calls and not rig.workflow_calls and "commit" not in rig.events
        assert not rig.redis.store


def test_truth_refresh_reprepares_before_second_pipeline_and_only_prepared_commits(rig):
    rig.truth_stale = True
    for status in ("prepared", "unchanged"):
        reset(rig); rig.prepare_status = status; rig.truth_refreshed = False
        result = worker(rig, "project")
        initial = ["prepare:worker-project"] + (["commit"] if status == "prepared" else [])
        repeated = ["prepare:worker-truth-refresh"] + (["commit"] if status == "prepared" else [])
        assert trace(rig) == initial + ["pipeline", "new_workflow", "truth_refresh", "commit"] + repeated + ["pipeline", "new_workflow"]
        assert result["project_id"] == str(rig.project.id)
        assert rig.pipeline_calls == rig.workflow_calls == [rig.project.id, rig.project.id]


def test_truth_refresh_prepare_exception_blocks_second_pipeline_and_new_workflow(rig):
    rig.truth_stale = True; rig.deny_after_refresh = True; rig.prepare_status = "unchanged"
    with pytest.raises(ValueError, match="SYNTHETIC retained review preparation rejected"):
        worker(rig, "project")
    assert trace(rig) == ["prepare:worker-project", "pipeline", "new_workflow", "truth_refresh", "commit",
                          "prepare:worker-truth-refresh"]
    assert rig.pipeline_calls == rig.workflow_calls == [rig.project.id]
    assert not rig.redis.store
