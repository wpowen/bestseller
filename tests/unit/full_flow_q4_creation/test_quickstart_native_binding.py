"""Real Web caller up to the Q4 request-identity boundary, without a send.

Only architect/model IO and the Redis lease transport are inert. Project,
workflow, sessions, task dispatch, terminal closing and DB resume reads remain
native. These are SQLite boundary cases, not reviewed-conception/E21 proof.
"""

import asyncio
import copy
from types import SimpleNamespace
from uuid import UUID

import pytest
from sqlalchemy import func, select

from bestseller.infra.db import models as m
from bestseller.services import conception, llm, projects, story_architect
from bestseller.services.budget import current_execution_scope
from bestseller.services.progress_context import TaskCancelledError
from bestseller.web import server

from .test_creation_project_handoff import STAGING_KEY, prepare, quickstart

pytestmark = pytest.mark.unit


def install_task(manager, payload, task_id):
    manager._tasks[task_id] = server.WebTaskState(
        task_id=task_id, task_type="autowrite", status="queued",
        project_slug=str(payload["slug"]), title=str(payload["title"]), payload=copy.deepcopy(payload),
        created_at="2026-10-28T00:00:00+00:00", updated_at="2026-10-28T00:00:00+00:00",
    )


@pytest.mark.parametrize("stop", [False, True])
@pytest.mark.parametrize("exit_kind", ["failed", "cancelled"])
def test_native_quickstart_passes_real_ids_before_conception_and_reenters_same_project(
    creation_db, monkeypatch, stop, exit_kind,
):
    case = creation_db
    task_id = "q4-native-creation"
    manager, payload = quickstart(case, monkeypatch, stop=stop, task_id=task_id)
    install_task(manager, payload, task_id)
    boundaries = []
    identities = []
    architect_identities = []

    async def architect_io_unavailable(session, settings, **kwargs):
        # No invented StoryFacets or authorizing result. Normal caller handles
        # unavailable advisory architect IO before entering conception.
        workflow_id = llm._conception_workflow_id_var.get()
        async with case.sessions() as observer:
            project = await projects.get_project_by_slug(observer, str(payload["slug"]))
            workflow = await observer.get(m.WorkflowRunModel, workflow_id) if workflow_id else None
            architect_identities.append((project.id if project else None,
                                         workflow.project_id if workflow else None, workflow_id))
        raise RuntimeError("offline architect intentionally unavailable")

    async def conception_request_boundary(session, settings, **kwargs):
        boundaries.append(copy.deepcopy(kwargs))
        assert (kwargs.get("user_hints") or {}).get("reader_first_planning") is not False
        # This is the real Q4 identity guard, fed only the actual caller args.
        identity = conception._reader_first_request_identity({"reader_first_request_context": {
            "project_id": kwargs.get("project_id"), "workflow_run_id": kwargs.get("workflow_run_id"),
        }})
        project_id, workflow_id = UUID(identity["project_id"]), UUID(identity["workflow_run_id"])
        async with case.sessions() as observer:
            project = await observer.get(m.ProjectModel, project_id)
            workflow = await observer.get(m.WorkflowRunModel, workflow_id)
            assert project is not None and project.slug == payload["slug"]
            assert workflow is not None and workflow.project_id == project.id
            assert (workflow.scope_type, workflow.scope_id) == ("creation", None)
            assert project.metadata_json[STAGING_KEY]["state"] == "staging"
            assert workflow.metadata_json["execution_scope_id"] == current_execution_scope()
            assert project.metadata_json["execution_scope_id"] == current_execution_scope()
            assert not project.metadata_json.get("creation_canonical_snapshot_id")
            assert not project.metadata_json.get("conception_approved")
            identities.append((project.id, workflow.id, current_execution_scope(), project.created_at))
        if exit_kind == "cancelled":
            raise TaskCancelledError("offline cancellation before first send")
        raise RuntimeError("offline failure before first send")

    monkeypatch.setattr(story_architect, "architect_story_facets", architect_io_unavailable)
    monkeypatch.setattr(conception, "run_conception_pipeline", conception_request_boundary)
    manager._run_with_slot(task_id, manager._run_autowrite_worker, payload)
    assert len(boundaries) == 1
    assert len(identities) == 1, "normal quickstart never supplied real Q4 project/workflow identity"
    assert architect_identities == [(identities[0][0], identities[0][0], identities[0][1])]
    assert manager._tasks[task_id].status == exit_kind

    async def read_failure_and_scope():
        async with case.sessions() as session:
            project = await session.get(m.ProjectModel, identities[0][0])
            workflow = await session.get(m.WorkflowRunModel, identities[0][1])
            assert workflow.status == exit_kind
            assert project.metadata_json[STAGING_KEY]["state"] == "staging"
            assert (await session.scalar(select(func.count()).select_from(m.ProjectModel))) == 1
            assert (await session.scalar(select(func.count()).select_from(m.PlanningArtifactVersionModel))) == 0
            assert (await session.scalar(select(func.count()).select_from(m.LlmRunModel))) == 0
        assert await manager._task_execution_scope(task_id) == identities[0][2]
        return await server._rebuild_payload_from_db(case.settings, str(payload["slug"]))

    rebuilt = asyncio.run(read_failure_and_scope())
    assert rebuilt["_run_conception"] is True
    retry_payload = server._merge_resume_payload_with_project_truth(payload, rebuilt)
    assert retry_payload["_conception_workflow_run_id"] == str(identities[0][1])
    # Re-enter the same normal worker with persisted source identities. No new
    # project, fresh task, synthetic approved result or changed budget scope.
    install_task(manager, retry_payload, task_id)
    manager._run_with_slot(task_id, manager._run_autowrite_worker, retry_payload)
    assert len(identities) == 2
    assert identities[1][0] == identities[0][0]
    assert identities[1][2:] == identities[0][2:]
    async def read_reentry_terminal():
        async with case.sessions() as session:
            workflow = await session.get(m.WorkflowRunModel, identities[1][1])
            assert workflow.status == exit_kind
    asyncio.run(read_reentry_terminal())
    asyncio.run(read_failure_and_scope())


def test_restart_routes_failed_staging_back_to_conception_even_with_a_project_row(creation_db, monkeypatch):
    case = creation_db
    task_id = "q4-native-creation"
    manager, payload = quickstart(case, monkeypatch, task_id=task_id)
    binding = asyncio.run(prepare(case, payload, task_id=task_id))

    async def close_original_attempt():
        await server._close_creation_workflow_after_task_exit_async(
            case.settings, attempt_id=str(payload["_conception_attempt_id"]),
            status="failed", error_message="offline stopped before model send",
        )
        return await server._rebuild_payload_from_db(case.settings, binding.project.slug)

    rebuilt = asyncio.run(close_original_attempt())
    assert rebuilt["_run_conception"] is True
    install_task(manager, rebuilt, task_id)
    manager._pending_auto_resume_ids = [task_id]
    dispatched = []

    class RecordedThread:
        def __init__(self, *, target, args, daemon):
            dispatched.append((target, args, daemon))

        def start(self):
            pass  # Observe dispatch; this case never starts a background worker.

    monkeypatch.setattr(server, "threading", SimpleNamespace(Thread=RecordedThread))
    resumed = manager.auto_resume_zombies(settings=case.settings)
    assert resumed == [task_id]
    assert len(dispatched) == 1
    target, args, daemon = dispatched[0]
    assert target == manager._run_with_slot
    assert args[0] == task_id and args[1] == manager._run_autowrite_worker
    assert args[2]["_run_conception"] is True
    assert args[2]["_conception_workflow_run_id"] == str(binding.workflow.id)
    assert daemon is True
