"""Real SQLite source/financial inventory; no provider or production database."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta
import hashlib
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import JSON, ForeignKeyConstraint, MetaData, event, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import JSONB
from test_foundation_selection import store  # noqa: F401 - fixture dependency
from test_materialized_outline_source_recovery import case as materialized_case  # noqa: F401

from bestseller.infra.db import models as db
from bestseller.services import foundation_selection, llm, planner
from bestseller.services import outline_revision_lifecycle as lifecycle
from bestseller.services import planning_admission_transport_recovery as service
from bestseller.services import planning_draft_admission as admission
from bestseller.services.budget import bind_execution_scope
from bestseller.services.planner import _without_planning_meta
from bestseller.services.prompt_source_policy import content_hash

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.fixture
async def case(materialized_case, monkeypatch):  # noqa: F811 - imported fixture
    c = materialized_case
    engine = c.factory.kw["bind"]
    assert engine.dialect.name == "sqlite" and "test" in engine.url.database
    for model in (db.ChapterDraftVersionModel, db.CanonFactModel, db.TimelineEventModel):
        table = model.__table__.to_metadata(MetaData())
        for column in table.c:
            if isinstance(column.type, JSONB):
                column.type = JSON()
            if column.server_default is not None and "::" in str(column.server_default.arg):
                column.server_default = None
        for constraint in list(table.constraints):
            if isinstance(constraint, ForeignKeyConstraint):
                table.constraints.remove(constraint)
        async with engine.begin() as connection:
            await connection.run_sync(table.create)
    c.settings.llm.critic = c.settings.llm.critic.model_copy(
        update={
            "model": "openai/MiniMax-M3.1-Flash",
            "api_base": "https://api.minimaxi.com/v1",
            "max_tokens": 32768,
            "reasoning_effort": "low",
            "thinking_type": None,
        }
    )
    c.settings.artifact_store.retain_prompt_payloads = True
    c.settings.artifact_store.mode = "local"
    c.before = datetime.now(UTC) - timedelta(hours=1)
    # The upstream source fixture explicitly declares nine historical charges.
    # Supply their real rows, retaining its counters and three reserved holds.
    async with c.factory() as session:
        project = await session.get(db.ProjectModel, c.project.id)
        project.target_chapters = len(c.outline.content["chapters"])
        volume = await session.get(db.PlanningArtifactVersionModel, c.assets["volume_plan"].id)
        detached = SimpleNamespace(
            **{
                key: deepcopy(getattr(project, key))
                for key in (
                    "id",
                    "target_chapters",
                    "target_word_count",
                    "metadata_json",
                    "language",
                )
            }
        )
        detached._source_bound_planning_canonical = {
            "id": str(c.canonical.id),
            "content_hash": content_hash(c.canonical.content),
            "content": deepcopy(c.canonical.content),
        }
        projection = planner._compile_source_bound_volume_plan(
            detached,
            project.metadata_json["premise"],
            *(c.assets[kind].content for kind in foundation_selection.KINDS[:3]),
        )
        volume.content = [
            {
                "volume_number": 1,
                "_meta": {"source_projection_sha256": planner._approved_payload_hash(projection)},
            }
        ]
        await session.commit()
        await foundation_selection.pin_completed_foundation(
            session,
            project=project,
            workflow_id=c.assets["volume_plan"].source_run_id,
            settings=c.settings,
        )
        # The staging reader binds its original-input reconstruction to a real
        # workflow row. This inert owner supplies identity, never a grade.
        session.add(
            db.WorkflowRunModel(
                id=service.admission_workflow_id(project.id, c.outline.id),
                project_id=project.id,
                workflow_type="source_input_fixture",
                status="completed",
                metadata_json={},
            )
        )
        for index in range(9):
            amount = 138 if index == 8 else 137
            key = hashlib.sha256(f"retained-financial-{index}".encode()).hexdigest()
            session.add(
                db.ExecutionOperationModel(
                    key=key,
                    scope_id=c.scope,
                    input_hash="a" * 64,
                    status="stopped" if index < 3 else "completed",
                    owner="historical",
                    lease_until=c.budget.deadline_at,
                    result_json={"unchanged": {"unknown": None}},
                )
            )
            session.add(
                db.ExecutionAttemptModel(
                    id=uuid4().hex,
                    scope_id=c.scope,
                    operation_key=key,
                    input_hash="a" * 64,
                    model_name="historical-model",
                    reserved_tokens=amount,
                    actual_tokens=None if index < 3 else amount,
                    status="reconciled_charged" if index < 3 else "completed",
                    created_at=c.before,
                )
            )
        await session.commit()
        candidate = deepcopy(_without_planning_meta(c.outline.content))
        candidate["chapters"][0]["chapter_goal"] = "Check the independent station register."
        with bind_execution_scope(c.scope):
            result = await lifecycle.stage_full_outline_revision(
                session,
                c.settings,
                project_slug=c.project.slug,
                parent_artifact_id=c.outline.id,
                parent_materialization_workflow_id=c.owner.id,
                candidate_payload=candidate,
                proposal_receipt={"ungraded": True, "old_false": False},
                actor="offline operator",
                authorization_ref="existing explicit objective",
                explicit_user_approval=True,
            )
        c.wid = UUID(result["workflow_run_id"])
        c.aid = UUID(result["artifact_id"])
        project, owner, artifact = [
            await session.get(model, key)
            for model, key in (
                (db.ProjectModel, c.project.id),
                (db.WorkflowRunModel, c.wid),
                (db.PlanningArtifactVersionModel, c.aid),
            )
        ]
        state = deepcopy(owner.metadata_json)
        with bind_execution_scope(c.scope):
            supplied = await admission._input(
                session,
                project=project,
                workflow_run_id=c.wid,
                candidate_payload=_without_planning_meta(artifact.content),
                book_spec=c.assets["book_spec"].content,
                world_spec=c.assets["world_spec"].content,
                cast_spec=c.assets["cast_spec"].content,
                commercial_report={
                    "schema": "historical-plan-review-observations.v1",
                    **deepcopy(
                        state["full_outline_revision"]["base_binding"][
                            "historical_editorial_observations"
                        ]
                    ),
                },
                check_initial_context=True,
            )
        c.supplied = admission._input_for_contract(supplied, service.GRADE_SCHEMA)
        state["protocol"] = {
            "status": "input_budget_blocked",
            "attempts": [],
            "candidate_hash": state["candidate_hash"],
            "graded_input_hash": content_hash(c.supplied),
            "hard_admission_policy": service.POLICY,
            "input_capacity": {
                "budget_passed": False,
                "truncated": False,
                "boundary": "complete source and native tools capacity",
                "error_type": "SourceBoundPlanningCapacityError",
            },
            "unknown": {"preserve": None},
        }
        state["full_outline_revision"]["phase"] = "protocol_unknown"
        owner.status = "machine_blocked"
        owner.current_step = "admission_input_budget_exceeded"
        owner.metadata_json = state
        await session.commit()
    c.state = deepcopy(state)
    c.operation_id = admission._protocol_operation(
        c.project, c.wid, content_hash(c.supplied), [], 0
    )
    c.root = hashlib.sha256(f"{c.scope}\n{c.operation_id}".encode()).hexdigest()

    async def forbidden(*args, **kwargs):
        raise AssertionError("No gateway/network/provider in read-only migration")

    monkeypatch.setattr(llm, "complete_text", forbidden)
    monkeypatch.setattr(llm, "_call_litellm", forbidden)
    monkeypatch.setattr(admission, "complete_text", forbidden)
    return c


async def prepare(c, **overrides):
    kwargs = {
        "workflow_run_id": c.wid,
        "actor": "offline operator",
        "authorization_ref": "existing explicit objective",
        "explicit_user_approval": True,
    }
    kwargs.update(overrides)
    async with c.factory() as session:
        with bind_execution_scope(c.scope):
            return await service.prepare_literal_admission_migration(session, c.settings, **kwargs)


async def snapshot(c):
    async with c.factory() as session:
        reader = service._Reader(session)
        return {
            model.__tablename__: content_hash([vars(row) for row in await reader.rows(model)])
            for model in (
                db.WorkflowRunModel,
                db.ProjectModel,
                db.PlanningArtifactVersionModel,
                db.ChapterModel,
                db.SceneCardModel,
                db.BookProductionControlModel,
                db.ChapterDraftVersionModel,
                db.CanonFactModel,
                db.TimelineEventModel,
                db.ExecutionBudgetModel,
                db.ExecutionOperationModel,
                db.ExecutionAttemptModel,
                db.LlmRunModel,
            )
        }


async def test_real_inventory_and_proposal_preserve_status_journal_rows_and_holds(case):
    c = case
    before = await snapshot(c)
    async with c.factory() as session:

        def no_write(conn, cursor, statement, parameters, context, executemany):
            assert not statement.lstrip().lower().startswith(("insert", "update", "delete"))
            if context.compiled is not None:
                pg_sql = str(context.compiled.statement.compile(dialect=postgresql.dialect()))
                assert "FOR UPDATE" not in pg_sql and "FOR SHARE" not in pg_sql

        event.listen(session.bind.sync_engine, "before_cursor_execute", no_write)
        try:
            with bind_execution_scope(c.scope):
                proof = await service.verify_zero_paid_admission_inventory(
                    session, c.settings, workflow_run_id=c.wid
                )
                result = await service.prepare_literal_admission_migration(
                    session,
                    c.settings,
                    workflow_run_id=c.wid,
                    actor="offline operator",
                    authorization_ref="existing explicit objective",
                    explicit_user_approval=True,
                )
            assert not session.new and not session.dirty and not session.deleted
        finally:
            event.remove(session.bind.sync_engine, "before_cursor_execute", no_write)
    assert await snapshot(c) == before
    assert result == await prepare(c)
    inventory = proof["inventory"]
    assert inventory["accounting"]["calls_used"] == 9
    assert inventory["accounting"]["tokens_charged"] == 1234
    assert len(inventory["reconciled_reserved_holds"]) == 3
    proposed = result["proposed_state"]
    assert proposed["protocol"] == {**c.state["protocol"], "wire_version": service.WIRE_VERSION}
    receipt = proposed["transport_migrations"][0]
    assert receipt["original_protocol"] == c.state["protocol"]
    assert receipt["original_state"] == c.state
    restored = deepcopy(proposed)
    del restored["protocol"]["wire_version"]
    del restored["transport_migrations"]
    assert restored == c.state
    assert receipt["native_attempt_cap"] == 2 and not receipt["new_allowance_granted"]


@pytest.mark.parametrize(
    "kind",
    [
        "pending_root",
        "completed_root",
        "attempt_no_telemetry",
        "orphan_transport",
        "orphan_failure",
        "orphan_provider_return",
        "unknown_origin",
        "other_scope_origin",
        "run",
        "run_origin_only",
        "grant_only",
    ],
)
async def test_hidden_physical_or_telemetry_inventory_rejects_without_repairing_rows(case, kind):
    c = case
    async with c.factory() as session:
        if kind in {"run", "run_origin_only"}:
            session.add(
                db.LlmRunModel(
                    project_id=c.project.id,
                    workflow_run_id=c.wid if kind == "run" else None,
                    logical_role="critic",
                    provider="openai",
                    model_name="historical-model",
                    metadata_json={"paid_call_origin": {"workflow_run_id": str(c.wid)}},
                )
            )
        elif kind == "grant_only":
            budget = await session.get(db.ExecutionBudgetModel, c.scope)
            budget.metadata_json = {
                **budget.metadata_json,
                "single_dispatch_grants": {
                    "retained": {"binding": {"workflow_id": str(c.wid)}, "status": "available"}
                },
            }
        else:
            scope = c.scope
            if kind == "other_scope_origin":
                scope = "project:independent-scope"
                session.add(
                    db.ExecutionBudgetModel(
                        scope_id=scope,
                        max_calls=2,
                        max_tokens=1000,
                        calls_used=0,
                        tokens_charged=0,
                        deadline_at=c.budget.deadline_at,
                        status="active",
                        metadata_json={},
                    )
                )
                await session.flush()
            key = (
                c.root
                if kind in {"pending_root", "completed_root", "attempt_no_telemetry"}
                else uuid4().hex * 2
            )
            value = None
            if kind.startswith("orphan_"):
                value = {
                    "schema": kind,
                    "operation_key": c.root,
                    "attempt_id": "lost-attempt",
                    "result": {"content": "retained physical response"},
                }
            if kind in {"unknown_origin", "other_scope_origin"}:
                value = {
                    "result": {"metadata": {"paid_call_origin": {"workflow_run_id": str(c.wid)}}}
                }
            session.add(
                db.ExecutionOperationModel(
                    key=key,
                    scope_id=scope,
                    input_hash="b" * 64,
                    status="pending" if kind == "pending_root" else "completed",
                    owner="retained",
                    lease_until=c.budget.deadline_at,
                    result_json=value,
                )
            )
            if kind == "attempt_no_telemetry":
                session.add(
                    db.ExecutionAttemptModel(
                        id=uuid4().hex,
                        operation_key=c.root,
                        scope_id=scope,
                        input_hash="b" * 64,
                        model_name="historical-model",
                        reserved_tokens=40,
                        actual_tokens=40,
                        status="completed",
                        created_at=c.before,
                    )
                )
                budget = await session.get(db.ExecutionBudgetModel, scope)
                budget.calls_used += 1
                budget.tokens_charged += 40
        await session.commit()
    before = await snapshot(c)
    with pytest.raises(ValueError):
        await prepare(c)
    assert await snapshot(c) == before


@pytest.mark.parametrize(
    "change",
    [
        "calls",
        "tokens",
        "active_operation",
        "unknown_attempt",
        "other_workflow",
        "late_attempt",
        "finite_totals",
    ],
)
async def test_global_scope_must_be_quiescent_and_financially_closed(case, change):
    c = case
    async with c.factory() as session:
        budget = await session.get(db.ExecutionBudgetModel, c.scope)
        if change == "calls":
            budget.calls_used -= 1
        elif change == "tokens":
            budget.tokens_charged -= 1
        elif change == "finite_totals":
            budget.metadata_json = {**budget.metadata_json, "active_finite_book_run": "missing"}
        elif change == "active_operation":
            operation = await session.get(
                db.ExecutionOperationModel,
                (await session.scalars(select(db.ExecutionOperationModel.key))).first(),
            )
            operation.status = "pending"
        elif change == "other_workflow":
            session.add(
                db.WorkflowRunModel(
                    project_id=c.project.id,
                    workflow_type="unrelated",
                    status="running",
                    metadata_json={"execution_scope_id": c.scope},
                )
            )
        else:
            attempt = (await session.scalars(select(db.ExecutionAttemptModel))).first()
            if change == "unknown_attempt":
                attempt.status = "unknown"
            else:
                attempt.created_at = datetime.now(UTC) + timedelta(seconds=1)
        await session.commit()
    before = await snapshot(c)
    with pytest.raises(ValueError):
        await prepare(c)
    assert await snapshot(c) == before


@pytest.mark.parametrize(
    "change",
    [
        "control",
        "scope",
        "candidate",
        "policy",
        "journal",
        "already_migrated",
        "status",
        "projection",
        "historical_report",
        "capacity_provenance",
        "other_project",
    ],
)
async def test_current_source_control_and_original_block_are_independent_fences(case, change):
    c = case
    async with c.factory() as session:
        owner = await session.get(db.WorkflowRunModel, c.wid)
        state = deepcopy(owner.metadata_json)
        if change == "control":
            (await session.get(db.BookProductionControlModel, c.project.id)).desired_state = "pause"
        elif change == "scope":
            state["execution_scope_id"] = "project:other"
        elif change == "candidate":
            row = await session.get(db.PlanningArtifactVersionModel, c.aid)
            content = deepcopy(row.content)
            content["chapters"][0]["chapter_goal"] = "A different current candidate."
            row.content = content
        elif change == "policy":
            state["protocol"]["hard_admission_policy"] = "f" * 64
        elif change == "journal":
            state["protocol"]["attempts"] = [{"completion": {}, "status": "returned"}]
        elif change == "already_migrated":
            state["protocol"]["wire_version"] = service.WIRE_VERSION
        elif change == "status":
            owner.status = "running"
        elif change == "projection":
            (await session.get(db.ChapterModel, c.chapters[0].id)).title = "Changed retained plan"
        elif change == "capacity_provenance":
            state["protocol"]["input_capacity"]["error_type"] = "UnknownError"
        elif change == "other_project":
            (await session.get(db.PlanningArtifactVersionModel, c.aid)).project_id = uuid4()
        else:
            state["full_outline_revision"]["base_binding"]["historical_editorial_observations"][
                "outline_reader_experience_judge"
            ]["pass"] = True
        owner.metadata_json = state
        await session.commit()
    with pytest.raises(ValueError):
        await prepare(c)


async def test_caller_dirty_state_is_not_flushed(case):
    c = case
    async with c.factory() as session:
        budget = await session.get(db.ExecutionBudgetModel, c.scope)
        budget.tokens_charged = 0
        with (
            bind_execution_scope(c.scope),
            pytest.raises(ValueError, match="pending_protected_changes"),
        ):
            await service.prepare_literal_admission_migration(
                session,
                c.settings,
                workflow_run_id=c.wid,
                actor="offline operator",
                authorization_ref="existing explicit objective",
                explicit_user_approval=True,
            )
        assert session.is_modified(budget)
    async with c.factory() as session:
        assert (await session.get(db.ExecutionBudgetModel, c.scope)).tokens_charged == 1234


async def test_explicit_authorization_and_scope_are_required(case):
    c = case
    with pytest.raises(ValueError, match="explicit_authorization_required"):
        await prepare(c, explicit_user_approval=False)
    async with c.factory() as session:
        with bind_execution_scope("other-scope"), pytest.raises(ValueError):
            await service.verify_zero_paid_admission_inventory(
                session, c.settings, workflow_run_id=c.wid
            )


@pytest.mark.parametrize("change", ["workflow_row", "control", "ledger"])
async def test_preparation_rechecks_current_context_after_first_inventory(
    case, monkeypatch, change
):
    c = case
    original = service._inventory
    reads = 0

    async def concurrent_change(*args, **kwargs):
        nonlocal reads
        result = await original(*args, **kwargs)
        reads += 1
        if reads == 1:
            async with c.factory() as other:
                if change == "workflow_row":
                    (
                        await other.get(db.WorkflowRunModel, c.wid)
                    ).current_step = "changed externally"
                elif change == "control":
                    (
                        await other.get(db.BookProductionControlModel, c.project.id)
                    ).desired_state = "pause"
                else:
                    (await other.get(db.ExecutionBudgetModel, c.scope)).tokens_charged += 1
                await other.commit()
        return result

    monkeypatch.setattr(service, "_inventory", concurrent_change)
    with pytest.raises(ValueError):
        await prepare(c)
    # Only the simulated external writer changed its one field; no proposal was
    # returned, no journal entry or wire marker was written by the helper.
    async with c.factory() as session:
        owner = await session.get(db.WorkflowRunModel, c.wid)
        assert owner.metadata_json == c.state and owner.status == "machine_blocked"
