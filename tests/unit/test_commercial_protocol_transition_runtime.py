"""SQLite transition audits: real authorization/persistence, no gateway or credit mutation.

Controlled serialized snapshots reuse the pure service's proof fixtures. They
are not actual paid requests or a claim of production financial authorization.
"""

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.schema import CreateTable
from test_adopted_knowledge_lineage import _jsonb  # noqa: F401
from test_commercial_protocol_transition import add_quarantined_orphan, sha
from test_commercial_protocol_transition import case as case

from bestseller.infra.db.models import (
    ChapterModel,
    ExecutionAttemptModel,
    ExecutionBudgetModel,
    ExecutionOperationModel,
    LlmRunModel,
    ProjectModel,
    WorkflowRunModel,
)
from bestseller.services import commercial_protocol_transition_runtime as runtime
from bestseller.services import llm
from bestseller.services.execution_authorization import authorize_provider_completion

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.fixture
async def transition_db(tmp_path, monkeypatch, case):
    path = tmp_path / "commercial_protocol_transition_runtime_test.sqlite3"
    assert "test" in path.name
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    async with engine.begin() as conn:
        for model in (
            ProjectModel,
            ChapterModel,
            WorkflowRunModel,
            ExecutionBudgetModel,
            ExecutionOperationModel,
            ExecutionAttemptModel,
            LlmRunModel,
        ):
            ddl = str(CreateTable(model.__table__).compile(dialect=conn.dialect))
            await conn.exec_driver_sql(ddl.replace("::jsonb", ""))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    scope = case["execution_scope_id"]
    async with factory() as setup:
        project = ProjectModel(
            id=UUID(case["project_id"]),
            slug="transition-runtime-test",
            title="test",
            genre="test",
            target_word_count=10000,
            target_chapters=3,
            metadata_json={"protected": "keep"},
        )
        chapter = ChapterModel(
            id=UUID(case["chapter_id"]),
            project_id=project.id,
            chapter_number=2,
            chapter_goal="test",
            metadata_json={"protected": "keep"},
        )
        budget = ExecutionBudgetModel(
            scope_id=scope,
            max_calls=10,
            max_tokens=10000,
            calls_used=2,
            tokens_charged=625,
            deadline_at=datetime.now(UTC) + timedelta(hours=1),
            status="active",
            metadata_json={},
        )
        setup.add_all([project, chapter, budget])
        await setup.commit()
    # Exercise the real explicit operator API in an isolated fixture. Do not
    # monkeypatch completion_authorization or fabricate its return value.
    await authorize_provider_completion(
        engine,
        scope_id=scope,
        idempotency_key="isolated-unit-authorization",
        actor="isolated-test-operator",
        authorization_ref="unit-fixture-only",
        objective="exercise local transition receipt persistence without provider calls",
        expected_max_calls=10,
        expected_max_tokens=10000,
    )

    async def forbidden(*args, **kwargs):
        pytest.fail("transition runtime must not call a provider")

    monkeypatch.setattr(llm, "complete_text", forbidden)
    async with factory() as session:
        snapshots = deepcopy(case["snapshots"])

        async def controlled_snapshots(_session, *, project_id, scope):
            assert project_id == UUID(case["project_id"])
            assert scope == case["execution_scope_id"]
            return deepcopy(snapshots)

        original_snapshot_loader = runtime.transition_snapshots
        monkeypatch.setattr(runtime, "transition_snapshots", controlled_snapshots)
        yield SimpleNamespace(
            engine=engine,
            factory=factory,
            session=session,
            case=case,
            snapshots=snapshots,
            project_id=UUID(case["project_id"]),
            chapter_id=UUID(case["chapter_id"]),
            scope=scope,
            current_hash=case["current_effective_input_hash"],
            contract=deepcopy(case["target_protocol"]["evaluation_contract"]),
            critic={"model": "openai/MiniMax-M3", "api_base": "https://api.minimaxi.com/v1"},
            real_snapshots=original_snapshot_loader,
        )
        await session.rollback()
    await engine.dispose()


async def inspect_transition(rig, session=None, **changes):
    arguments = {
        "project_id": rig.project_id,
        "chapter_id": rig.chapter_id,
        "scope": rig.scope,
        "evaluation_contract": rig.contract,
        "current_input_hash": rig.current_hash,
        "effective_critic": rig.critic,
    }
    arguments.update(changes)
    return await runtime.inspect_authorized_protocol_transition(
        session if session is not None else rig.session,
        **arguments,
    )


async def protected_rows(rig):
    async with rig.factory() as session:
        return {
            model.__tablename__: sorted(
                [runtime._row(row) for row in await session.scalars(select(model))],
                key=str,
            )
            for model in (
                ExecutionBudgetModel,
                ExecutionOperationModel,
                ExecutionAttemptModel,
                LlmRunModel,
                ProjectModel,
                ChapterModel,
            )
        }


async def workflow_count(rig):
    async with rig.factory() as observer:
        return await observer.scalar(select(func.count()).select_from(WorkflowRunModel))


async def seed_actual_history(rig):
    """Store the pure fixture's original rows with explicit ORM-required defaults."""
    for run in rig.snapshots["runs"]:
        rig.session.add(
            LlmRunModel(
                id=UUID(run["id"]),
                project_id=UUID(run["project_id"]),
                workflow_run_id=None,
                logical_role="critic",
                provider="fixture",
                model_name=run["model_name"],
                prompt_template=run["prompt_template"],
                prompt_version=run["prompt_version"],
                prompt_hash=run["prompt_hash"],
                metadata_json=deepcopy(run["metadata_json"]),
                request_payload_ref=run.get("request_payload_ref"),
                response_payload_ref=run.get("response_payload_ref"),
            )
        )
    for op in rig.snapshots["operations"]:
        rig.session.add(
            ExecutionOperationModel(
                key=op["key"],
                scope_id=op["scope_id"],
                input_hash=op["input_hash"],
                status=op["status"],
                result_json=deepcopy(op.get("result_json")),
                owner="fixture",
                lease_until=datetime.now(UTC) + timedelta(hours=1),
            )
        )
    for attempt in rig.snapshots["attempts"]:
        rig.session.add(
            ExecutionAttemptModel(
                **attempt,
                created_at=datetime.now(UTC),
            )
        )
    await rig.session.commit()


async def test_inspection_is_readonly_and_persistence_is_caller_committed(transition_db):
    rig = transition_db
    before = await protected_rows(rig)
    exclusions = await inspect_transition(rig)
    assert exclusions["run_ids"] == [rig.snapshots["runs"][0]["id"]]
    assert await workflow_count(rig) == 0
    identity = await runtime.persist_inspected_protocol_transition(
        rig.session,
        expected_input_hash=rig.current_hash,
    )
    assert await workflow_count(rig) == 0  # Independent connection: no hidden commit.
    await rig.session.commit()
    async with rig.factory() as reopened:
        row = await reopened.get(WorkflowRunModel, UUID(identity))
        assert row.project_id == rig.project_id and row.scope_id == rig.chapter_id
        assert row.workflow_type == "commercial_protocol_transition" and row.status == "completed"
        assert row.current_step == "legacy_protocol_retired"
        assert row.metadata_json["authorization_ref"] == "unit-fixture-only"
        receipt = row.metadata_json["receipt"]
        for flag in (
            "credit_released",
            "operation_replay_allowed",
            "ownership_inferred",
            "body_exclusion_asserted",
            "old_grades_reused",
            "cohort_identity_changed",
        ):
            assert receipt[flag] is False
        assert receipt["binding"]["current_effective_input_hash"] == rig.current_hash
    assert await protected_rows(rig) == before


async def test_reopen_inspect_and_persist_is_idempotent_without_new_credit(transition_db):
    rig = transition_db
    before = await protected_rows(rig)
    first = await inspect_transition(rig)
    identity = await runtime.persist_inspected_protocol_transition(
        rig.session,
        expected_input_hash=rig.current_hash,
    )
    await rig.session.commit()
    for _ in range(2):
        async with rig.factory() as reopened:
            assert await inspect_transition(rig, session=reopened) == first
            assert (
                await runtime.persist_inspected_protocol_transition(
                    reopened,
                    expected_input_hash=rig.current_hash,
                )
                == identity
            )
            await reopened.commit()
    assert await workflow_count(rig) == 1
    assert await protected_rows(rig) == before


async def test_caller_rollback_discards_audit_not_original_evidence(transition_db):
    rig = transition_db
    before = await protected_rows(rig)
    await inspect_transition(rig)
    await runtime.persist_inspected_protocol_transition(
        rig.session, expected_input_hash=rig.current_hash
    )
    await rig.session.rollback()
    assert await workflow_count(rig) == 0
    assert await protected_rows(rig) == before


@pytest.mark.parametrize("already_persisted", [False, True])
async def test_prepared_input_mismatch_blocks_audit_without_resetting_credit(
    transition_db,
    already_persisted,
):
    rig = transition_db
    before = await protected_rows(rig)
    await inspect_transition(rig)
    if already_persisted:
        await runtime.persist_inspected_protocol_transition(
            rig.session, expected_input_hash=rig.current_hash
        )
        await rig.session.commit()
    with pytest.raises(ValueError, match="commercial_transition_prepared_input_changed"):
        await runtime.persist_inspected_protocol_transition(
            rig.session,
            expected_input_hash=sha("different actual prepared request"),
        )
    assert await workflow_count(rig) == int(already_persisted)
    assert await protected_rows(rig) == before


async def test_persist_requires_explicit_prepared_hash_and_no_inspection_is_noop(transition_db):
    rig = transition_db
    with pytest.raises(TypeError):
        await runtime.persist_inspected_protocol_transition(rig.session)
    assert (
        await runtime.persist_inspected_protocol_transition(
            rig.session,
            expected_input_hash=rig.current_hash,
        )
        is None
    )
    assert await workflow_count(rig) == 0


@pytest.mark.parametrize("state", ["no_authorization", "paused", "closed", "unknown", "cancelled"])
async def test_real_sqlite_authorization_blocks_inspection_and_persistence(transition_db, state):
    rig = transition_db
    budget = await rig.session.get(ExecutionBudgetModel, rig.scope)
    metadata = deepcopy(budget.metadata_json)
    if state == "no_authorization":
        metadata.pop("provider_authorization")
    elif state in {"paused", "closed"}:
        metadata["provider_authorization"]["status"] = (
            "paused" if state == "paused" else "completed"
        )
    else:
        budget.status = state
    budget.metadata_json = metadata
    await rig.session.commit()
    before = await protected_rows(rig)
    assert await inspect_transition(rig) is None
    assert (
        await runtime.persist_inspected_protocol_transition(
            rig.session,
            expected_input_hash=rig.current_hash,
        )
        is None
    )
    assert await workflow_count(rig) == 0
    assert await protected_rows(rig) == before


@pytest.mark.parametrize("provider_change", ["model", "endpoint"])
async def test_actual_authorization_model_and_endpoint_are_enforced(transition_db, provider_change):
    rig = transition_db
    critic = dict(rig.critic)
    critic["model" if provider_change == "model" else "api_base"] = (
        "unapproved/model" if provider_change == "model" else "https://unapproved.invalid/v1"
    )
    before = await protected_rows(rig)
    with pytest.raises(ValueError, match="commercial_transition_provider_not_authorized"):
        await inspect_transition(rig, effective_critic=critic)
    assert await workflow_count(rig) == 0
    assert await protected_rows(rig) == before


@pytest.mark.parametrize("drift", ["original_row", "new_attempt", "receipt"])
async def test_persisted_proof_reinspection_rejects_snapshot_or_receipt_drift(transition_db, drift):
    rig = transition_db
    await inspect_transition(rig)
    identity = await runtime.persist_inspected_protocol_transition(
        rig.session,
        expected_input_hash=rig.current_hash,
    )
    await rig.session.commit()
    if drift == "original_row":
        rig.snapshots["operations"][0]["result_json"]["content"] += " altered"
    elif drift == "new_attempt":
        extra = deepcopy(rig.snapshots["attempts"][0])
        extra["id"] = "new-unlisted-attempt"
        rig.snapshots["attempts"].append(extra)
    else:
        row = await rig.session.get(WorkflowRunModel, UUID(identity))
        metadata = deepcopy(row.metadata_json)
        metadata["receipt"]["credit_released"] = True
        row.metadata_json = metadata
        await rig.session.commit()
    before = await protected_rows(rig)
    with pytest.raises(ValueError, match="commercial_transition_"):
        await inspect_transition(rig)
    assert await workflow_count(rig) == 1
    assert await protected_rows(rig) == before


@pytest.mark.parametrize("field", ["scope_id", "status", "workflow_type"])
async def test_existing_audit_row_binding_is_validated_from_sqlite(transition_db, field):
    rig = transition_db
    await inspect_transition(rig)
    identity = await runtime.persist_inspected_protocol_transition(
        rig.session,
        expected_input_hash=rig.current_hash,
    )
    row = await rig.session.get(WorkflowRunModel, UUID(identity))
    setattr(row, field, rig.project_id if field == "scope_id" else "wrong")
    await rig.session.commit()
    with pytest.raises(ValueError, match="commercial_transition_persisted_binding_mismatch"):
        await inspect_transition(rig)
    assert await workflow_count(rig) == 1


async def test_real_snapshot_loader_reads_legacy_rows_and_retains_quarantine_charges(
    transition_db,
    monkeypatch,
):
    rig = transition_db
    data = {**rig.case, "snapshots": rig.snapshots}
    original_quarantine = add_quarantined_orphan(data)
    await seed_actual_history(rig)
    monkeypatch.setattr(runtime, "transition_snapshots", rig.real_snapshots)
    before = await protected_rows(rig)
    snapshots = await runtime.transition_snapshots(
        rig.session,
        project_id=rig.project_id,
        scope=rig.scope,
    )
    assert len(snapshots["runs"]) == 1
    assert len(snapshots["attempts"]) == 2
    assert len(snapshots["quarantine_receipts"]) == 1
    assert snapshots["quarantine_receipts"][0]["result_json"] == original_quarantine["result_json"]
    exclusions = await inspect_transition(rig)
    assert len(exclusions["attempt_ids"]) == 2
    await runtime.persist_inspected_protocol_transition(
        rig.session, expected_input_hash=rig.current_hash
    )
    await rig.session.commit()
    assert await protected_rows(rig) == before
    assert await workflow_count(rig) == 1
