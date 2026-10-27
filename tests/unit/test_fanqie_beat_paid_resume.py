"""Paid Beat projection recovery with native SQL/proof and lease IO fixtures."""

from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import UTC, datetime
import json
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select
from test_fanqie_beat_recovery import case, payload, protected  # noqa: F401
from test_foundation_field_recovery import record_completion
from test_foundation_selection import store  # noqa: F401
from test_worker_owner_boundaries_r07 import Redis

from bestseller.domain.enums import WorkflowStatus
from bestseller.infra.db.models import (
    BookProductionControlModel,
    ExecutionAttemptModel,
    ExecutionOperationModel,
    PlanningArtifactVersionModel,
    WorkflowRunModel,
)
from bestseller.services import fanqie_beat_recovery as beat
from bestseller.services.budget import ExecutionBudgetError, bind_execution_scope
from bestseller.services.truth_version import initialize_truth_metadata
from bestseller.services.workflows import create_workflow_run
from bestseller.worker import tasks

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


class LeaseIO(Redis):
    """Only Redis IO is substituted; native acquisition/probe/guards execute."""

    async def aclose(self, **kwargs):
        pass

    async def set(self, key, value, **kwargs):
        if kwargs.get("nx") and key in self.values:
            return False
        return await super().set(key, value, **kwargs)

    async def get(self, key):
        callback = getattr(self, "on_probe", None)
        if callback is not None:
            self.on_probe = None
            await callback()
        return await super().get(key)

    async def eval(self, *args):
        self.renew_count = getattr(self, "renew_count", 0) + 1
        callback = getattr(self, "on_renew", None)
        if callback is not None:
            await callback(self.renew_count)
        return await super().eval(*args)


@pytest.fixture
async def paid_case(case, tmp_path, monkeypatch):  # noqa: F811
    c = case
    c.task_id = str(uuid4())
    c.project.status = "planning"
    c.budget.scope_id = "conception:" + c.task_id
    c.project.metadata_json = initialize_truth_metadata(
        {
            **deepcopy(c.project.metadata_json),
            "execution_scope_id": c.budget.scope_id,
        }
    )
    c.settings.output.base_dir = str(tmp_path / "test-output")
    c.redis = LeaseIO()
    monkeypatch.setattr("redis.asyncio.Redis.from_url", lambda *a, **kw: c.redis)
    await c.session.commit()
    c.original_owner = uuid4().hex
    with bind_execution_scope(c.budget.scope_id):
        assert (
            await tasks._acquire_pipeline_live(c.redis, c.project.slug, c.original_owner)
            == "acquired"
        )
        async with tasks._pipeline_live_guard(
            c.redis,
            c.project.slug,
            c.original_owner,
            "acquired",
        ):
            c.manifest = await beat._current_sources(
                c.session,
                c.settings,
                c.project,
                c.sources["premise"],
                c.assets["book_spec"].content,
                c.assets["cast_spec"].content,
                None,
            )
            c.workflow = await create_workflow_run(
                c.session,
                project_id=c.project.id,
                workflow_type=beat.WORKFLOW_TYPE,
                status=WorkflowStatus.RUNNING,
                scope_type="project",
                scope_id=c.project.id,
                requested_by="synthetic-native-test",
                current_step="generate_source_exact_beats",
                metadata={"beat_source_sha256": beat._sha(c.manifest)},
                idempotency_key=f"{beat.WORKFLOW_TYPE}:{c.project.id}:{beat._sha(c.manifest)}",
            )
            request = beat.build_current_request(
                c.project, c.manifest, workflow_run_id=c.workflow.id
            )
            c.completion, c.run = await record_completion(
                c,
                request,
                content=beat._json(payload(c)),
            )
            c.budget.calls_used = 1
            c.budget.tokens_charged = c.run.input_tokens + c.run.output_tokens
            await c.session.commit()
    now = datetime.now(UTC).isoformat()
    c.task = {
        "task_id": c.task_id,
        "task_type": "autowrite",
        "status": "failed",
        "created_at": now,
        "updated_at": now,
        "project_slug": c.project.slug,
        "cancel_requested": False,
        "payload": {"slug": c.project.slug, "project_type": "fanqie_short"},
        "error": "Traceback (most recent call last):\n"
        '  File "/app/src/bestseller/services/fanqie_short_pipeline.py", line 888, '
        "in run_fanqie_short_pipeline\n"
        "    outline_payload = build_fanqie_segment_outline_batch(\n"
        '  File "/app/src/bestseller/services/fanqie_short_planner.py", line 486, '
        "in build_fanqie_segment_outline_batch\n"
        "    return project_segment_outline(\n"
        '  File "/app/src/bestseller/services/fanqie_segment_projection.py", line 173, '
        "in project_segment_outline\n"
        "ValueError: fanqie_projection_scene_field_type:entry_state\n",
        "milestone_events": [
            {
                "timestamp": now,
                "stage": "fanqie_foundation_plan_completed",
                "payload": {
                    "project_slug": c.project.slug,
                    "workflow_run_id": c.project.metadata_json["active_foundation_selection"][
                        "workflow_id"
                    ],
                },
            },
            {
                "timestamp": now,
                "stage": "fanqie_beat_sheet_started",
                "payload": {"project_slug": c.project.slug},
            },
        ],
        "progress_events": [],
    }
    c.task_path = Path(c.settings.output.base_dir) / ".web_tasks.json"
    c.task_path.parent.mkdir()
    c.task_path.write_text(json.dumps([c.task]))
    return c


async def prepare(c):
    return await beat.prepare_failed_beat_projection_recovery(
        c.session,
        c.settings,
        project_id=c.project.id,
        requested_by="synthetic-native-test",
    )


async def recover(c):
    return await beat.ensure_beat_sheet(
        c.session,
        c.settings,
        project_slug=c.project.slug,
        premise=c.sources["premise"],
        book_spec=c.assets["book_spec"].content,
        cast_spec=c.assets["cast_spec"].content,
    )


async def refresh(c):
    for row in (c.project, c.workflow, c.run, c.budget, *c.assets.values()):
        await c.session.refresh(row)


@asynccontextmanager
async def acquired(c):
    with bind_execution_scope(c.budget.scope_id):
        token = uuid4().hex
        state = await tasks._acquire_pipeline_live(c.redis, c.project.slug, token)
        async with tasks._pipeline_live_guard(c.redis, c.project.slug, token, state):
            yield


async def test_prepare_failed_recover_and_rollback_keep_original_paid_identity(paid_case):
    c = paid_case
    before = await protected(c)
    receipt = await prepare(c)
    assert receipt["provider_calls"] == receipt["new_attempts"] == 0
    assert receipt["credit_released"] is False
    assert c.workflow.status == "failed"
    await c.session.rollback()
    assert await protected(c) == before
    await refresh(c)
    receipt = await prepare(c)
    await c.session.commit()
    failed = await protected(c)
    ids = {w.id for w in await c.session.scalars(select(WorkflowRunModel))}
    with bind_execution_scope(c.budget.scope_id):
        token = uuid4().hex
        assert await tasks._acquire_pipeline_live(c.redis, c.project.slug, token) == "acquired"
        async with tasks._pipeline_live_guard(c.redis, c.project.slug, token, "acquired"):
            selection = await recover(c)
            assert selection.receipt["producer_workflow_id"] == str(c.workflow.id)
            assert selection.receipt["llm_run_id"] == str(c.run.id)
            assert c.workflow.status == "completed"
            await c.session.rollback()
    assert await protected(c) == failed
    await refresh(c)
    with bind_execution_scope(c.budget.scope_id):
        token = uuid4().hex
        assert await tasks._acquire_pipeline_live(c.redis, c.project.slug, token) == "acquired"
        async with tasks._pipeline_live_guard(c.redis, c.project.slug, token, "acquired"):
            selection = await recover(c)
            await c.session.commit()
    assert {w.id for w in await c.session.scalars(select(WorkflowRunModel))} == ids
    artifact = await c.session.get(PlanningArtifactVersionModel, selection.artifact_id)
    assert artifact.source_run_id == c.workflow.id
    assert artifact.content["unknown_authored"] == payload(c)["unknown_authored"]
    assert selection.receipt["quality_approval_granted"] is False
    assert c.workflow.metadata_json["execution_owner"] == c.original_owner
    assert c.workflow.metadata_json["beat_projection_failure_receipt"] == receipt
    after = await protected(c)
    # Financial roots/runs/attempts and foundation/project sources are immutable.
    for index in (0, 2, 3, 4, 5):
        assert after[index] == before[index], {
            key: (before[index][0].get(key), after[index][0].get(key))
            for key in before[index][0]
            if before[index][0].get(key) != after[index][0].get(key)
        }


@pytest.mark.parametrize(
    "fault",
    [
        "live",
        "unknown",
        "legacy",
        "task",
        "trace",
        "paid",
        "pause",
        "control_unknown",
        "pending_attempt",
        "sibling",
        "progress",
        "other_scope",
    ],
)
async def test_prepare_refuses_without_verified_failure_paid_and_dead_owner(paid_case, fault):
    c = paid_case
    if fault == "live":
        await c.redis.set(tasks._pipeline_live_key(c.project.slug), "live", ex=300)
    elif fault == "unknown":
        c.redis.fail = "scan"
    elif fault == "legacy":
        c.workflow.metadata_json = {
            k: v for k, v in c.workflow.metadata_json.items() if k != "native_pipeline_owner"
        }
    elif fault in {"task", "trace"}:
        c.task["status" if fault == "task" else "error"] = "running"
        c.task_path.write_text(json.dumps([c.task]))
    elif fault == "paid":
        root = await c.session.get(
            ExecutionOperationModel,
            c.run.metadata_json["execution_receipt"]["operation_key"],
        )
        root.status = "unknown"
    elif fault == "pending_attempt":
        row = await c.session.scalar(
            select(ExecutionAttemptModel).where(
                ExecutionAttemptModel.operation_key
                == c.run.metadata_json["execution_receipt"]["operation_key"],
            )
        )
        row.status = "reserved"
    elif fault == "sibling":
        c.session.add(
            WorkflowRunModel(
                project_id=c.project.id,
                workflow_type="chapter_pipeline",
                status="running",
                scope_type="project",
                scope_id=c.project.id,
                metadata_json={},
            )
        )
    elif fault == "progress":
        c.task["milestone_events"] = []
        c.task_path.write_text(json.dumps([c.task]))
    elif fault == "other_scope":
        pass
    else:
        c.session.add(
            BookProductionControlModel(
                project_id=c.project.id,
                desired_state="unknown" if fault == "control_unknown" else "pause",
            )
        )
    await c.session.commit()
    before = await protected(c)
    with bind_execution_scope("project:other" if fault == "other_scope" else c.budget.scope_id):
        with pytest.raises((ValueError, ExecutionBudgetError)):
            await prepare(c)
    assert await protected(c) == before


@pytest.mark.parametrize(
    "fault",
    [
        "source",
        "route",
        "receipt",
        "root",
        "transport",
        "attempt",
        "owner",
        "sibling",
        "control",
        "budget",
        "ambiguous_producer",
        "physical_file",
    ],
)
async def test_failed_paid_resume_refuses_drift_without_gateway_or_mutation(paid_case, fault):
    c = paid_case
    await prepare(c)
    await c.session.commit()
    if fault == "source":
        c.project.title = "Changed source"
    elif fault == "route":
        c.settings.llm.planner.temperature += 0.1
    elif fault == "receipt":
        meta = deepcopy(c.workflow.metadata_json)
        meta[beat.FAILURE_RECEIPT]["binding"]["paid_proof"]["llm_run_id"] = str(uuid4())
        c.workflow.metadata_json = meta
    elif fault == "root":
        row = await c.session.get(
            ExecutionOperationModel,
            c.run.metadata_json["execution_receipt"]["operation_key"],
        )
        row.status = "unknown"
    elif fault == "transport":
        row = await c.session.scalar(
            select(ExecutionOperationModel).where(
                ExecutionOperationModel.status == "transport_result",
            )
        )
        await c.session.delete(row)
    elif fault == "attempt":
        row = await c.session.scalar(select(ExecutionAttemptModel))
        row.actual_tokens += 1
    elif fault == "owner":
        c.workflow.metadata_json = {
            k: v for k, v in c.workflow.metadata_json.items() if k != "native_pipeline_owner"
        }
    elif fault in {"sibling", "ambiguous_producer"}:
        c.session.add(
            WorkflowRunModel(
                project_id=c.project.id,
                workflow_type=beat.WORKFLOW_TYPE
                if fault == "ambiguous_producer"
                else "chapter_pipeline",
                status="running",
                scope_type="project",
                scope_id=c.project.id,
                metadata_json={},
            )
        )
    elif fault == "control":
        c.session.add(BookProductionControlModel(project_id=c.project.id, desired_state="pause"))
    elif fault == "budget":
        c.budget.status = "unknown"
    else:
        c.paths[f"{c.run.id}:response"].write_text("{}")
    await c.session.commit()
    before = await protected(c)
    async with acquired(c):
        with pytest.raises((ValueError, ExecutionBudgetError)):
            await recover(c)
    assert await protected(c) == before


async def test_prepare_idempotent_and_no_unknown_or_newer_live_web_task_is_ignored(paid_case):
    c = paid_case
    first = await prepare(c)
    await c.session.commit()
    before = await protected(c)
    assert await prepare(c) == first
    assert await protected(c) == before
    other = {**c.task, "task_id": str(uuid4()), "status": "queued"}
    c.task_path.write_text(json.dumps([c.task, other]))
    with pytest.raises(ValueError, match="live_web_task"):
        await prepare(c)
    assert await protected(c) == before


@pytest.mark.parametrize("change", ["control", "paid"])
async def test_prepare_rechecks_other_session_changes_after_initial_paid_proof(paid_case, change):
    c = paid_case
    project_id = c.project.id
    key = c.run.metadata_json["execution_receipt"]["operation_key"]

    async def concurrent_write():
        async with c.factory() as other:
            if change == "control":
                other.add(BookProductionControlModel(project_id=project_id, desired_state="pause"))
            else:
                root = await other.get(ExecutionOperationModel, key)
                root.status = "unknown"
            await other.commit()

    c.redis.on_probe = concurrent_write
    with pytest.raises(ValueError):
        await prepare(c)
    await c.session.refresh(c.workflow)
    assert c.workflow.status == "running"
    assert beat.FAILURE_RECEIPT not in c.workflow.metadata_json


async def test_resume_rechecks_paid_sql_rows_after_locked_owner_assertion(paid_case):
    c = paid_case
    await prepare(c)
    await c.session.commit()
    key = c.run.metadata_json["execution_receipt"]["operation_key"]
    c.redis.renew_count = 0

    async def concurrent_write(count):
        # Guard entry, initial resume-owner check, then locked resume-owner check.
        if count == 3:
            async with c.factory() as other:
                root = await other.get(ExecutionOperationModel, key)
                root.status = "unknown"
                await other.commit()

    c.redis.on_renew = concurrent_write
    async with acquired(c):
        with pytest.raises(ValueError):
            await recover(c)
    await c.session.rollback()
    await c.session.refresh(c.workflow)
    assert c.workflow.status == "failed"
    assert not list(
        await c.session.scalars(
            select(PlanningArtifactVersionModel).where(
                PlanningArtifactVersionModel.artifact_type == "fanqie_beat_sheet",
            )
        )
    )


async def test_paid_resume_never_uses_gateway_without_acquired_native_owner(paid_case):
    c = paid_case
    await prepare(c)
    await c.session.commit()
    before = await protected(c)
    with bind_execution_scope(c.budget.scope_id), pytest.raises(ValueError, match="owner"):
        await recover(c)
    assert await protected(c) == before
