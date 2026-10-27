"""Isolated SQLite + actual planner/judge/checkpoint, fake final gateway only."""

# ruff: noqa: ANN001, ANN201, ANN202 -- Match repository test fixture annotation policy.

import asyncio
from copy import deepcopy
from datetime import UTC, datetime, timedelta
import json
from types import SimpleNamespace
from uuid import UUID, uuid4

from bestseller.services import pending_outline_judge_recovery as service
import pytest
from sqlalchemy import JSON, MetaData, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.schema import CreateTable
from test_foundation_selection import seed_objects

from bestseller.infra.db.models import (
    ExecutionAttemptModel,
    ExecutionBudgetModel,
    ExecutionOperationModel,
    LlmRunModel,
    PlanningArtifactVersionModel,
    ProjectModel,
    WorkflowRunModel,
    WorkflowStepRunModel,
)
from bestseller.services import foundation_selection, llm, outline_llm_judge, planner
from bestseller.services.budget import current_execution_scope
from bestseller.services.llm import LLMCompletionResult
from bestseller.services.outline_recovery_checkpoint import recovery_binding
from bestseller.services.prompt_source_policy import content_hash
from bestseller.services.rolling_outline import rolling_window_schedule_hash
from bestseller.settings import load_settings

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]
MODELS = (
    ProjectModel,
    WorkflowRunModel,
    WorkflowStepRunModel,
    PlanningArtifactVersionModel,
    LlmRunModel,
    ExecutionBudgetModel,
    ExecutionOperationModel,
    ExecutionAttemptModel,
)


@pytest.fixture
async def case(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'pending_judge_test.sqlite3'}")
    factory = async_sessionmaker(engine, expire_on_commit=False)

    def create(connection):
        for model in MODELS:
            table = model.__table__.to_metadata(MetaData())
            for column in table.c:
                if isinstance(column.type, JSONB):
                    column.type = JSON()
                if column.server_default and "::jsonb" in str(column.server_default.arg):
                    column.server_default = None
            connection.execute(CreateTable(table, include_foreign_key_constraints=[]))

    async with engine.begin() as connection:
        await connection.run_sync(create)
    project, canonical, foundation, old_foundation, assets = seed_objects()
    scope, source_id = f"conception:{uuid4()}", uuid4()
    schedule = [
        {"volume_number": 1, "window_index": i + 1, "window_start": a, "window_end": b}
        for i, (a, b) in enumerate(((1, 8), (9, 16), (17, 23), (24, 30)))
    ]
    project.metadata_json = {
        **project.metadata_json,
        "rolling_outline_plan": {
            "window_start": 1,
            "window_end": 8,
            "source_snapshot_hash": project.metadata_json["book_design_snapshot_hash"],
        },
        "rolling_outline_windows": schedule,
        "rolling_outline_windows_hash": rolling_window_schedule_hash(schedule),
    }
    candidate = {
        "batch_name": "full-window",
        "chapters": [
            {
                "chapter_number": n,
                "volume_number": 1,
                "title": f"Register {n}",
                "goal": f"Compare the observatory record {n} with its dated receipt.",
                "scenes": [{"scene_number": 1, "purpose": {"story": f"Read entry {n}."}}],
            }
            for n in range(1, 9)
        ],
        "_meta": {"paid_generation": ["immutable-original"]},
    }
    digest = content_hash(candidate)
    binding = recovery_binding(project.metadata_json, 1, [1, 8])
    key = content_hash(binding)
    pending = {
        "candidate": candidate,
        "candidate_hash": digest,
        "binding": binding,
        "workflow_run_id": str(source_id),
        "report": None,
        "judgement_status": "pending",
    }
    project.metadata_json = {
        **project.metadata_json,
        "outline_commercial_judge_checkpoints": {key: {"latest_candidate": pending}},
    }
    previous = WorkflowRunModel(
        id=source_id,
        project_id=project.id,
        workflow_type="generate_volume_plan",
        scope_type="volume",
        scope_id=project.id,
        status="failed",
        current_step="volume_1_outline_commercial_judge",
        error_message="llm_project_and_workflow_required",
        metadata_json={
            "execution_scope_id": scope,
            "prior_attempts": 9,
            "outline_repair_dependency_hash": binding["dependency_hash"],
            "outline_repair_source_snapshot_hash": binding["source_snapshot_hash"],
            "outline_repair_window": [1, 8],
            "old_raw": {"keep": ["all"]},
        },
    )
    budget = ExecutionBudgetModel(
        scope_id=scope,
        max_calls=100,
        max_tokens=1000000,
        calls_used=17,
        tokens_charged=12000,
        deadline_at=datetime.now(UTC) + timedelta(days=1),
        status="active",
        metadata_json={
            "provider_authorization": {
                "mode": "completion_bound",
                "status": "active",
                "authorization_ref": "human-root",
                "models": ["retained-model"],
                "endpoint_hosts": ["api.invalid"],
                "objective": "Complete this independent project",
            }
        },
    )
    old_root = ExecutionOperationModel(
        key="a" * 64,
        scope_id=scope,
        input_hash="b" * 64,
        owner="original-owner",
        status="completed",
        lease_until=datetime.now(UTC),
        result_json={"content": "ORIGINAL-PAID-RAW", "usage": {"tokens": 12000}},
    )
    old_attempt = ExecutionAttemptModel(
        id="original-attempt",
        operation_key=old_root.key,
        scope_id=scope,
        model_name="retained-model",
        input_hash=old_root.input_hash,
        reserved_tokens=12000,
        actual_tokens=12000,
        status="completed",
        created_at=datetime.now(UTC),
    )
    async with factory() as session:
        session.add_all(
            [
                project,
                canonical,
                foundation,
                old_foundation,
                *assets.values(),
                previous,
                budget,
                old_root,
                old_attempt,
            ]
        )
        await session.commit()
        selected = await foundation_selection.pin_completed_foundation(
            session, project=project, workflow_id=foundation.id
        )
        await session.commit()
        await session.refresh(old_root)
        await session.refresh(old_attempt)
        original = {
            "workflow": service._row(previous),
            "root": service._row(old_root),
            "attempt": service._row(old_attempt),
        }
    monkeypatch.setattr(planner, "is_strict_prewrite_project", lambda _: True)
    monkeypatch.setattr(planner, "strict_blocks", lambda *a: True)
    calls = []

    async def forbidden(*args: object, **kwargs: object):
        raise AssertionError("Only the injected offline final judge gateway is allowed")

    monkeypatch.setattr(llm, "complete_text", forbidden)
    h = SimpleNamespace(
        factory=factory,
        project=project,
        source_id=source_id,
        scope=scope,
        candidate=candidate,
        digest=digest,
        key=key,
        original=original,
        selected=selected,
        assets=assets,
        calls=calls,
        settings=load_settings(),
        mode="pass",
    )

    async def gateway(session, settings, request):
        h.calls.append(request)
        owner_id = service._identity(project.id, source_id, digest)
        assert request.project_id == project.id and request.workflow_run_id == owner_id
        assert current_execution_scope() == scope
        assert llm._semantic_operation_namespace.get() == (
            f"pending-outline-judge:{source_id}:{digest}",
            0,
        )
        async with factory() as reader:
            owner = await reader.get(WorkflowRunModel, owner_id)
            assert owner.status == "running"
            assert owner.metadata_json["plan"]["original_pending_record"] == pending
            assert owner.metadata_json["plan"]["original_workflow_snapshot"] == original["workflow"]
        if h.mode == "failure":
            raise RuntimeError("offline transport error; old paid state retained")
        if h.mode == "db_failure":
            raise h.error
        if h.mode == "cancel":
            raise asyncio.CancelledError()
        if h.mode == "drift":
            project_row = await session.get(ProjectModel, project.id)
            project_row.metadata_json = {**project_row.metadata_json, "new_author_fact": "changed"}
        raw = __import__("json").dumps(
            {
                "pass": h.mode != "reject",
                "overall_score": 0.96 if h.mode != "reject" else 0.62,
                "dimension_scores": dict.fromkeys(outline_llm_judge.OUTLINE_JUDGE_DIMENSIONS, 0.96),
                "blocking_issues": [],
                "rewrite_plan": {
                    "scope": "outline",
                    "change": [],
                    "preserve": [],
                    "instructions": "Retain current sources.",
                },
            }
        )
        run = LlmRunModel(
            id=uuid4(),
            project_id=request.project_id,
            workflow_run_id=request.workflow_run_id,
            logical_role="critic",
            provider="openai",
            model_name="retained-model",
            prompt_template=request.prompt_template,
            prompt_version=request.prompt_version,
            prompt_hash="f" * 64,
            finish_reason="stop",
            input_tokens=100,
            output_tokens=50,
            metadata_json={"offline_gateway_fixture": True},
        )
        session.add(run)
        await session.flush()
        return LLMCompletionResult(
            content=raw,
            llm_run_id=run.id,
            provider="openai",
            model_name="retained-model",
            finish_reason="stop",
            input_tokens=100,
            output_tokens=50,
        )

    monkeypatch.setattr(outline_llm_judge, "complete_text", gateway)
    try:
        yield h
    finally:
        await engine.dispose()


async def inspect_plan(h):
    async with h.factory() as session:
        result = await service.recover_pending_outline_judgement(
            session,
            h.settings,
            project_id=h.project.id,
            source_workflow_id=h.source_id,
            candidate_hash=h.digest,
            requested_by="human-review",
        )
        assert not session.new and not session.dirty
        await session.rollback()
        return result


async def execute(h, prepared):
    async with h.factory() as session:
        return await service.recover_pending_outline_judgement(
            session,
            h.settings,
            project_id=h.project.id,
            source_workflow_id=h.source_id,
            candidate_hash=h.digest,
            requested_by="human-review",
            execute=True,
            expected_plan_sha256=prepared["plan_sha256"],
        )


async def unchanged_originals(h):
    async with h.factory() as reader:
        assert (
            service._row(await reader.get(WorkflowRunModel, h.source_id)) == h.original["workflow"]
        )
        assert (
            service._row(await reader.get(ExecutionOperationModel, "a" * 64)) == h.original["root"]
        )
        assert (
            service._row(await reader.get(ExecutionAttemptModel, "original-attempt"))
            == h.original["attempt"]
        )
        budget = await reader.get(ExecutionBudgetModel, h.scope)
        assert (budget.calls_used, budget.tokens_charged) == (17, 12000)
        for expected in h.assets.values():
            row = await reader.get(PlanningArtifactVersionModel, expected.id)
            assert row.content == expected.content


@pytest.mark.parametrize("mode", ["pass", "reject"])
async def test_actual_judge_only_checkpoint_and_idempotent_repeat(case, mode):
    h = case
    h.mode = mode
    prepared = await inspect_plan(h)
    assert prepared["plan"]["selection"] == h.selected and h.calls == []
    result = await execute(h, prepared)
    assert result["status"] == "completed" and result["judge_only"]
    assert result["report"]["pass"] == (mode == "pass")
    assert not result["adoption_allowed"] and not result["full_window_production_completed"]
    assert len(h.calls) == 1
    again = await execute(h, prepared)
    assert again["report"] == result["report"] and again["reused"]
    assert len(h.calls) == 1
    await unchanged_originals(h)


@pytest.mark.parametrize(
    "fault",
    [
        "missing_checkpoint",
        "candidate_changed",
        "binding",
        "schedule",
        "selection_missing",
        "wrong_origin",
        "scope",
        "authorization",
        "unknown_root",
        "unknown_attempt",
    ],
)
async def test_invalid_positive_proof_stops_before_claim_and_gateway(case, fault):
    h = case
    async with h.factory() as session:
        p = await session.get(ProjectModel, h.project.id)
        old = await session.get(WorkflowRunModel, h.source_id)
        meta = deepcopy(p.metadata_json)
        if fault == "missing_checkpoint":
            meta.pop("outline_commercial_judge_checkpoints")
        elif fault == "candidate_changed":
            meta["outline_commercial_judge_checkpoints"][h.key]["latest_candidate"]["candidate"][
                "chapters"
            ][0]["goal"] = "Unbound replacement"
        elif fault == "binding":
            meta["book_design_snapshot"]["source_hash"] = "0" * 64
        elif fault == "schedule":
            meta["rolling_outline_windows"][0]["window_end"] = 7
        elif fault == "selection_missing":
            meta.pop(foundation_selection.POINTER)
        elif fault == "wrong_origin":
            old.error_message = "Some unrelated failure"
        elif fault == "scope":
            old.metadata_json = {**old.metadata_json, "execution_scope_id": None}
        elif fault == "authorization":
            budget = await session.get(ExecutionBudgetModel, h.scope)
            budget.metadata_json = {}
        elif fault == "unknown_root":
            (await session.get(ExecutionOperationModel, "a" * 64)).status = "unknown"
        elif fault == "unknown_attempt":
            (await session.get(ExecutionAttemptModel, "original-attempt")).status = "unknown"
        p.metadata_json = meta
        await session.commit()
    with pytest.raises(ValueError):
        await inspect_plan(h)
    assert h.calls == []
    async with h.factory() as reader:
        assert (
            list(
                await reader.scalars(
                    select(WorkflowRunModel).where(
                        WorkflowRunModel.workflow_type == service.WORKFLOW_TYPE
                    )
                )
            )
            == []
        )


@pytest.mark.parametrize(
    "mode,error", [("failure", RuntimeError), ("cancel", asyncio.CancelledError)]
)
async def test_failure_terminal_identity_and_cancellation_preserve_history(case, mode, error):
    h = case
    h.mode = mode
    prepared = await inspect_plan(h)
    with pytest.raises(error):
        await execute(h, prepared)
    repeated = await execute(h, prepared)
    assert repeated["status"] == "failed" and repeated["terminal"]
    assert repeated["report"] is None and len(h.calls) == 1
    await unchanged_originals(h)


async def test_source_cas_after_inspection_no_claim_or_spend(case):
    h = case
    prepared = await inspect_plan(h)
    async with h.factory() as session:
        p = await session.get(ProjectModel, h.project.id)
        p.metadata_json = {**p.metadata_json, "explicit_new_constraint": "Keep the archive open."}
        await session.commit()
    with pytest.raises(ValueError, match="plan_changed"):
        await execute(h, prepared)
    assert h.calls == []


async def test_source_changes_during_judge_never_complete_recovery(case):
    h = case
    h.mode = "drift"
    prepared = await inspect_plan(h)
    with pytest.raises(ValueError, match="terminal_source_changed"):
        await execute(h, prepared)
    async with h.factory() as reader:
        owner = await reader.get(WorkflowRunModel, UUID(prepared["workflow_run_id"]))
        assert owner.status == "failed" and "report" not in owner.metadata_json
    await unchanged_originals(h)


async def test_running_after_durable_report_finishes_without_another_gateway_call(case):
    h = case
    prepared = await inspect_plan(h)
    first = await execute(h, prepared)
    async with h.factory() as session:
        owner = await session.get(WorkflowRunModel, UUID(prepared["workflow_run_id"]))
        owner.status = "running"  # crash fixture: checkpoint committed, terminal step absent
        owner.metadata_json = {k: v for k, v in owner.metadata_json.items() if k != "report"}
        await session.commit()
    resumed = await execute(h, prepared)
    assert resumed["status"] == "completed" and resumed["report"] == first["report"]
    assert len(h.calls) == 1
    await unchanged_originals(h)


async def test_selected_row_change_after_inspection_is_not_latest_asset_fallback(case):
    h = case
    prepared = await inspect_plan(h)
    async with h.factory() as session:
        world = await session.get(PlanningArtifactVersionModel, h.assets["world_spec"].id)
        world.created_by = "changed-source-owner"
        await session.commit()
    with pytest.raises(ValueError, match="plan_changed"):
        await execute(h, prepared)
    assert h.calls == []


async def test_preexisting_deterministic_identity_collision_cannot_dispatch(case):
    h = case
    prepared = await inspect_plan(h)
    async with h.factory() as session:
        session.add(
            WorkflowRunModel(
                id=UUID(prepared["workflow_run_id"]),
                project_id=h.project.id,
                workflow_type="unrelated_task",
                status="running",
                metadata_json={"keep": "unrelated record"},
            )
        )
        await session.commit()
    with pytest.raises(ValueError, match="existing_identity_or_plan_changed"):
        await execute(h, prepared)
    assert h.calls == []
    async with h.factory() as reader:
        row = await reader.get(WorkflowRunModel, UUID(prepared["workflow_run_id"]))
        assert row.workflow_type == "unrelated_task"
        assert row.metadata_json == {"keep": "unrelated record"}


async def test_dbapi_failure_stores_driver_only_and_frames_without_sql_or_parameters(case):
    h = case

    class DriverLockError(Exception):
        sqlstate = "55P03"

    class NeverStringifyDBAPIError(DBAPIError):
        def __str__(self) -> str:
            raise AssertionError("SQLAlchemy wrapper stringification exposes SQL/params")

    h.mode = "db_failure"
    h.error = NeverStringifyDBAPIError(
        "SELECT forbidden_statement FROM private_table WHERE token=:token",
        {"token": "DO-NOT-LOG-THIS-PARAMETER"},
        DriverLockError("driver lock acquisition timed out"),
    )
    prepared = await inspect_plan(h)
    with pytest.raises(NeverStringifyDBAPIError):
        await execute(h, prepared)
    async with h.factory() as reader:
        owner = await reader.get(WorkflowRunModel, UUID(prepared["workflow_run_id"]))
        diagnostic = owner.metadata_json["failure"]
        assert owner.status == "failed"
        assert diagnostic["driver_type"] == "DriverLockError"
        assert diagnostic["sqlstate"] == "55P03"
        assert diagnostic["driver_message"] == "driver lock acquisition timed out"
        assert diagnostic["frames"]
        assert all(
            set(frame) == {"filename", "lineno", "function"} for frame in diagnostic["frames"]
        )
        serialized = json.dumps([diagnostic, owner.error_message], allow_nan=False)
        for forbidden in ("forbidden_statement", "private_table", "DO-NOT-LOG-THIS-PARAMETER"):
            assert forbidden not in serialized
    repeated = await execute(h, prepared)
    assert repeated["terminal"] and len(h.calls) == 1
    await unchanged_originals(h)
