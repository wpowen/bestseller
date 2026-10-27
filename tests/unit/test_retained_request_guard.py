"""Offline SQLite proof: exact paid admission never claims, commits or sends."""

import asyncio
from copy import deepcopy
from datetime import UTC, datetime, timedelta
import hashlib
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import delete, event, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bestseller.infra.db.models import (
    ExecutionAttemptModel,
    ExecutionBudgetModel,
    ExecutionOperationModel,
)
from bestseller.services.budget import bind_execution_scope
from bestseller.services.prompt_source_policy import content_hash
from bestseller.services.retained_request_guard import (
    RetainedRequestPrepared,
    RetainedRequestRejected,
    bind_retained_request,
    current_retained_binding,
    guard_prepared_request,
    mark_retained_reused,
    retained_attempts_sha256,
)

pytestmark = pytest.mark.unit
SCOPE = "audit:retained-request-test"
DIGEST = "a" * 64
MODEL = "openai/example-model"
ATTEMPT_FIELDS = tuple(attr.key for attr in ExecutionAttemptModel.__mapper__.column_attrs)


@pytest.fixture
async def paid(tmp_path):
    path = tmp_path / "retained_request_guard_test.sqlite3"
    assert "test" in path.name
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    async with engine.begin() as connection:
        for model in (ExecutionBudgetModel, ExecutionOperationModel, ExecutionAttemptModel):
            await connection.run_sync(model.__table__.create)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    project_id, workflow_id = uuid4(), uuid4()
    request = SimpleNamespace(
        project_id=project_id,
        workflow_run_id=workflow_id,
        step_run_id=None,
        logical_role="editor",
        prompt_template="rewrite",
        prompt_version="v1",
        operation_id="stable-paid-request",
        sample_slot=0,
        metadata={},
    )
    key = hashlib.sha256(f"{SCOPE}\nstable-paid-request:sample:0".encode()).hexdigest()
    result = {
        "content": "完整的既有付费正文。",
        "provider": "openai",
        "model_name": MODEL,
        "input_tokens": 10,
        "output_tokens": 5,
        "finish_reason": "stop",
        "fallback_used": False,
        "metadata": {
            "paid_call_origin": {
                "schema": "paid-call-origin.v1",
                "status": "recorded",
                "project_id": str(project_id),
                "workflow_run_id": str(workflow_id),
                "step_run_id": None,
                "logical_role": "editor",
                "prompt_template": "rewrite",
                "prompt_version": "v1",
                "prompt_hash": DIGEST,
            },
            "effective_input_receipt": {
                "schema": "effective-input.v1",
                "hash": DIGEST,
                "model": MODEL,
            },
        },
    }
    now = datetime.now(UTC)
    async with sessions() as session:
        session.add(
            ExecutionBudgetModel(
                scope_id=SCOPE,
                max_calls=1,
                max_tokens=100,
                calls_used=1,
                tokens_charged=15,
                status="active",
                deadline_at=now + timedelta(minutes=5),
            )
        )
        session.add(
            ExecutionOperationModel(
                key=key,
                scope_id=SCOPE,
                input_hash=DIGEST,
                status="completed",
                owner="original-owner",
                lease_until=now,
                result_json=result,
            )
        )
        session.add(
            ExecutionAttemptModel(
                id="attempt-original",
                operation_key=key,
                scope_id=SCOPE,
                model_name=MODEL,
                input_hash=DIGEST,
                reserved_tokens=100,
                actual_tokens=15,
                status="completed",
                created_at=now,
            )
        )
        await session.commit()
    async with sessions() as session:
        attempts = await attempt_rows(session)
    binding = {
        "scope_id": SCOPE,
        "project_id": str(project_id),
        "workflow_run_id": str(workflow_id),
        "logical_role": "editor",
        "prompt_template": "rewrite",
        "prompt_version": "v1",
        "model": MODEL,
        "input_hash": DIGEST,
        "operation_key": key,
        "result_sha256": content_hash(result),
        "attempts_sha256": retained_attempts_sha256(attempts),
        "expected_claim_id": "preserved-domain-claim",
        "source_witness": {"ids": ["source-v1"]},
    }
    try:
        yield SimpleNamespace(
            engine=engine,
            sessions=sessions,
            binding=binding,
            request=request,
            role=SimpleNamespace(model=MODEL),
            result=result,
            attempts=attempts,
        )
    finally:
        await engine.dispose()


async def attempt_rows(session):
    return [
        dict(row)
        for row in (
            await session.execute(
                select(*(getattr(ExecutionAttemptModel, field) for field in ATTEMPT_FIELDS))
            )
        ).mappings()
    ]


async def admit(paid, *, request=None, digest=DIGEST, role=None):
    async with paid.sessions() as session:
        await guard_prepared_request(session, request or paid.request, role or paid.role, digest)


async def change(paid, model, **fields):
    async with paid.sessions() as session:
        await session.execute(update(model).values(**fields))
        await session.commit()


async def snapshot(paid):
    async with paid.sessions() as session:
        values = []
        for model in (ExecutionBudgetModel, ExecutionOperationModel, ExecutionAttemptModel):
            columns = [getattr(model, attr.key) for attr in model.__mapper__.column_attrs]
            rows = (await session.execute(select(*columns))).mappings().all()
            values.append([dict(row) for row in rows])
        return content_hash(values)


async def test_unbound_calls_remain_ordinary():
    assert current_retained_binding() is None
    await guard_prepared_request(None, None, None, "ignored")
    mark_retained_reused("ignored")


async def test_inspection_stops_before_all_writes_and_broad_catches(paid, monkeypatch):
    before = await snapshot(paid)
    statements = []
    event.listen(
        paid.engine.sync_engine,
        "before_cursor_execute",
        lambda conn, cursor, statement, parameters, context, many: statements.append(statement),
    )
    swallowed = False
    with (
        bind_execution_scope(SCOPE),
        bind_retained_request(paid.binding, inspect_only=True) as state,
    ):
        async with paid.sessions() as session:

            async def forbidden(*args, **kwargs):
                pytest.fail("guard attempted a flush/commit")

            monkeypatch.setattr(session, "flush", forbidden)
            monkeypatch.setattr(session, "commit", forbidden)
            session.add(
                ExecutionOperationModel(
                    key="b" * 64,
                    scope_id=SCOPE,
                    input_hash=DIGEST,
                    status="pending",
                    owner="unrelated-pending",
                    lease_until=datetime.now(UTC),
                )
            )
            try:
                try:
                    await guard_prepared_request(session, paid.request, paid.role, DIGEST)
                except Exception:
                    swallowed = True
            except RetainedRequestPrepared as prepared:
                assert prepared.receipt["matched"] is True
                assert prepared.receipt["operation_key"] == paid.binding["operation_key"]
                assert prepared.receipt["provider_dispatches"] == 0
            else:
                pytest.fail("inspection returned to the executor")
            assert session.new
            assert state.reused is False
            with pytest.raises(RetainedRequestRejected, match="reuse_not_prepared"):
                mark_retained_reused(paid.binding["operation_key"])
    assert not swallowed
    assert all(statement.lstrip().upper().startswith("SELECT") for statement in statements)
    assert await snapshot(paid) == before


async def test_exact_reuse_is_marked_only_after_successful_gateway_hook(paid):
    before = await snapshot(paid)
    with bind_execution_scope(SCOPE), bind_retained_request(paid.binding) as state:
        with pytest.raises(RetainedRequestRejected, match="reuse_not_prepared"):
            mark_retained_reused(paid.binding["operation_key"])
        await admit(paid)
        assert state.reused is False
        with pytest.raises(RetainedRequestRejected, match="reuse_not_prepared"):
            mark_retained_reused("b" * 64)
        mark_retained_reused(paid.binding["operation_key"])
        assert state.reused is True
        with pytest.raises(AttributeError):
            state.reused = False
        await admit(paid)  # Same paid result may be read again, never another sample.
        mark_retained_reused(paid.binding["operation_key"])
    assert current_retained_binding() is None
    assert await snapshot(paid) == before


@pytest.mark.parametrize("followup", [False, True])
async def test_every_new_request_is_rejected_before_actual_reuse(paid, followup):
    with (
        bind_execution_scope(SCOPE),
        bind_retained_request(
            paid.binding,
            allow_followup_after_reuse=followup,
        ),
    ):
        await admit(paid)
        with pytest.raises(RetainedRequestRejected, match="unpaid_request_forbidden"):
            await admit(paid, digest="b" * 64)


@pytest.mark.parametrize("inspect_only,followup", [(False, False), (True, True)])
async def test_strict_mode_never_allows_new_input(paid, inspect_only, followup):
    with (
        bind_execution_scope(SCOPE),
        bind_retained_request(
            paid.binding,
            inspect_only=inspect_only,
            allow_followup_after_reuse=followup,
        ),
    ):
        with pytest.raises(RetainedRequestRejected, match="unpaid_request_forbidden"):
            await admit(paid, digest="b" * 64)


async def test_explicit_followup_requires_mark_and_still_pins_old_input(paid):
    with (
        bind_execution_scope(SCOPE),
        bind_retained_request(
            paid.binding,
            allow_followup_after_reuse=True,
        ) as state,
    ):
        await admit(paid)
        mark_retained_reused(paid.binding["operation_key"])
        followup = deepcopy(paid.request)
        followup.prompt_template = "rewrite_repair"
        followup.operation_id = "new-legitimate-repair"
        await admit(paid, request=followup, digest="b" * 64)
        followup_key = hashlib.sha256(
            f"{SCOPE}\nnew-legitimate-repair:sample:0".encode()
        ).hexdigest()
        with pytest.raises(RetainedRequestRejected, match="reuse_not_prepared"):
            mark_retained_reused("c" * 64)  # Not the admitted followup's cache hit.
        mark_retained_reused(followup_key)
        assert state.reused
        with pytest.raises(RetainedRequestRejected, match="template_changed"):
            await admit(paid, request=followup)  # Same input cannot use the followup escape.
        followup = deepcopy(paid.request)
        followup.sample_slot = 1
        with pytest.raises(RetainedRequestRejected, match="operation_changed"):
            await admit(paid, request=followup)
        with pytest.raises(RetainedRequestRejected, match="reuse_not_prepared"):
            mark_retained_reused(followup_key)  # Failed same-input admission revoked followup.


async def test_different_input_cannot_repurpose_old_root_after_reuse(paid):
    with (
        bind_execution_scope(SCOPE),
        bind_retained_request(
            paid.binding,
            allow_followup_after_reuse=True,
        ),
    ):
        await admit(paid)
        mark_retained_reused(paid.binding["operation_key"])
        with pytest.raises(RetainedRequestRejected, match="paid_input_changed"):
            await admit(paid, digest="b" * 64)


async def test_default_stays_retained_only_even_after_completed_reuse(paid):
    with bind_execution_scope(SCOPE), bind_retained_request(paid.binding) as state:
        await admit(paid)
        mark_retained_reused(paid.binding["operation_key"])
        request = deepcopy(paid.request)
        request.operation_id = "new-followup"
        with pytest.raises(RetainedRequestRejected, match="unpaid_request_forbidden"):
            await admit(paid, request=request, digest="b" * 64)
        assert state.reused


async def test_fresh_read_failure_cannot_be_swallowed_as_fallback(paid, monkeypatch):
    async with paid.sessions() as session:

        async def unavailable(*args, **kwargs):
            raise RuntimeError("database unavailable")

        monkeypatch.setattr(session, "execute", unavailable)
        with bind_execution_scope(SCOPE), bind_retained_request(paid.binding, inspect_only=True):
            with pytest.raises(RetainedRequestRejected, match="fresh_read_failed") as caught:
                await guard_prepared_request(session, paid.request, paid.role, DIGEST)
            assert not isinstance(caught.value, Exception)
            assert caught.value.receipt["matched"] is False
            with pytest.raises(RetainedRequestRejected, match="reuse_not_prepared"):
                mark_retained_reused(paid.binding["operation_key"])


@pytest.mark.parametrize(
    "field,value,code",
    [
        ("project_id", uuid4(), "project_changed"),
        ("workflow_run_id", uuid4(), "workflow_changed"),
        ("logical_role", "writer", "role_changed"),
        ("prompt_template", "different", "template_changed"),
        ("prompt_version", "v2", "version_changed"),
        ("operation_id", "new-namespace", "operation_changed"),
        ("sample_slot", 1, "operation_changed"),
        ("sample_slot", True, "sample_invalid"),
        ("operation_id", None, "operation_changed"),
    ],
)
async def test_request_identity_changes_rejected_even_with_identical_input(
    paid, field, value, code
):
    request = deepcopy(paid.request)
    setattr(request, field, value)
    with bind_execution_scope(SCOPE), bind_retained_request(paid.binding, inspect_only=True):
        with pytest.raises(RetainedRequestRejected, match=code):
            await admit(paid, request=request)


async def test_scope_model_and_missing_stable_identity_rejected(paid):
    with bind_execution_scope("other-scope"), bind_retained_request(paid.binding):
        with pytest.raises(RetainedRequestRejected, match="scope_changed"):
            await admit(paid)
    with bind_execution_scope(SCOPE), bind_retained_request(paid.binding):
        with pytest.raises(RetainedRequestRejected, match="model_changed"):
            await admit(paid, role=SimpleNamespace(model="other-model"))
        request = deepcopy(paid.request)
        request.operation_id = request.sample_slot = None
        with pytest.raises(RetainedRequestRejected, match="operation_identity_missing"):
            await admit(paid, request=request)


@pytest.mark.parametrize("status", ["unknown", "pending", "stopped", "reconciled_charged"])
async def test_uncompleted_paid_root_is_never_reused(paid, status):
    await change(paid, ExecutionOperationModel, status=status)
    with bind_execution_scope(SCOPE), bind_retained_request(paid.binding):
        with pytest.raises(RetainedRequestRejected, match="paid_root_not_completed"):
            await admit(paid)


@pytest.mark.parametrize(
    "model,code",
    [
        (ExecutionOperationModel, "paid_root_missing"),
        (ExecutionAttemptModel, "paid_attempts_changed"),
    ],
)
async def test_missing_root_or_attempts_rejected(paid, model, code):
    async with paid.sessions() as session:
        await session.execute(delete(model))
        await session.commit()
    with bind_execution_scope(SCOPE), bind_retained_request(paid.binding):
        with pytest.raises(RetainedRequestRejected, match=code):
            await admit(paid)


@pytest.mark.parametrize(
    "column,value",
    [
        ("status", "unknown"),
        ("status", "reserved"),
        ("model_name", "wrong-model"),
        ("input_hash", "b" * 64),
        ("scope_id", "other"),
        ("reserved_tokens", 101),
        ("actual_tokens", 16),
        ("created_at", datetime(2020, 1, 1, tzinfo=UTC)),
    ],
)
async def test_every_attempt_column_is_freshly_bound(paid, column, value):
    await change(paid, ExecutionAttemptModel, **{column: value})
    with bind_execution_scope(SCOPE), bind_retained_request(paid.binding, inspect_only=True):
        with pytest.raises(RetainedRequestRejected, match="paid_attempts_changed"):
            await admit(paid)


async def test_added_attempt_and_changed_result_cannot_hide_behind_identity_map(paid):
    async with paid.sessions() as session:
        stale = await session.get(ExecutionOperationModel, paid.binding["operation_key"])
        result = deepcopy(paid.result)
        result["content"] += "unauthorized changed body"
        await change(paid, ExecutionOperationModel, result_json=result)
        assert stale.result_json == paid.result
        with bind_execution_scope(SCOPE), bind_retained_request(paid.binding):
            with pytest.raises(RetainedRequestRejected, match="paid_result_changed"):
                await guard_prepared_request(session, paid.request, paid.role, DIGEST)
    await change(paid, ExecutionOperationModel, result_json=paid.result)
    async with paid.sessions() as session:
        row = deepcopy(paid.attempts[0])
        row["id"] = "extra-attempt"
        session.add(ExecutionAttemptModel(**row))
        await session.commit()
    with bind_execution_scope(SCOPE), bind_retained_request(paid.binding):
        with pytest.raises(RetainedRequestRejected, match="paid_attempts_changed"):
            await admit(paid)


@pytest.mark.parametrize(
    "section,field,value,code",
    [
        ("paid_call_origin", "project_id", str(uuid4()), "paid_origin_changed"),
        ("paid_call_origin", "workflow_run_id", str(uuid4()), "paid_origin_changed"),
        ("paid_call_origin", "step_run_id", str(uuid4()), "paid_origin_changed"),
        ("paid_call_origin", "logical_role", "critic", "paid_origin_changed"),
        ("paid_call_origin", "prompt_template", "other", "paid_origin_changed"),
        ("paid_call_origin", "prompt_version", "v2", "paid_origin_changed"),
        ("paid_call_origin", "prompt_hash", "b" * 64, "paid_origin_changed"),
        ("paid_call_origin", "status", "unknown", "paid_origin_changed"),
        ("effective_input_receipt", "hash", "b" * 64, "effective_receipt_changed"),
        ("effective_input_receipt", "model", "other", "effective_receipt_changed"),
        ("effective_input_receipt", "schema", "old.v0", "effective_receipt_changed"),
    ],
)
async def test_origin_and_effective_receipt_are_checked_beyond_supplied_hash(
    paid, section, field, value, code
):
    result = deepcopy(paid.result)
    result["metadata"][section][field] = value
    await change(paid, ExecutionOperationModel, result_json=result)
    binding = {**paid.binding, "result_sha256": content_hash(result)}
    with bind_execution_scope(SCOPE), bind_retained_request(binding):
        with pytest.raises(RetainedRequestRejected, match=code):
            await admit(paid)


@pytest.mark.parametrize(
    "status,actual",
    [("unknown", None), ("reserved", None), ("reconciled_charged", 15), ("completed", None)],
)
async def test_unsettled_actuals_rejected_even_if_witness_hash_is_supplied(paid, status, actual):
    await change(paid, ExecutionAttemptModel, status=status, actual_tokens=actual)
    async with paid.sessions() as session:
        binding = {
            **paid.binding,
            "attempts_sha256": retained_attempts_sha256(await attempt_rows(session)),
        }
    with bind_execution_scope(SCOPE), bind_retained_request(binding):
        with pytest.raises(RetainedRequestRejected, match="paid_attempts_unsettled"):
            await admit(paid)


async def test_binding_is_deeply_readonly_and_cannot_be_nested_to_bypass(paid):
    with bind_execution_scope(SCOPE), bind_retained_request(paid.binding):
        bound = current_retained_binding()
        assert bound["expected_claim_id"] == "preserved-domain-claim"
        paid.binding["model"] = "changed outside binding"
        paid.binding["source_witness"]["ids"].append("changed")
        assert bound["source_witness"]["ids"] == ("source-v1",)
        with pytest.raises(TypeError):
            bound["model"] = "changed"
        with pytest.raises(TypeError):
            bound["source_witness"]["ids"] = ()
        with pytest.raises(RetainedRequestRejected, match="nested_binding"):
            with bind_retained_request(paid.binding, allow_followup_after_reuse=True):
                pytest.fail("nested guard escaped")
        await admit(paid)


async def test_child_tasks_cannot_inspect_admit_mark_or_rebind(paid):
    release = asyncio.Event()

    async def child():
        await release.wait()
        actions = [
            lambda: current_retained_binding(),
            lambda: mark_retained_reused(paid.binding["operation_key"]),
        ]
        for action in actions:
            with pytest.raises(RetainedRequestRejected, match="owner_task_changed"):
                action()
        with pytest.raises(RetainedRequestRejected, match="owner_task_changed"):
            await admit(paid)
        with pytest.raises(RetainedRequestRejected, match="owner_task_changed"):
            with bind_retained_request(paid.binding):
                pytest.fail("child rebound inherited guard")

    with bind_execution_scope(SCOPE), bind_retained_request(paid.binding):
        await admit(paid)
        mark_retained_reused(paid.binding["operation_key"])
        first = asyncio.create_task(child())
        release.set()
        await first  # Even successful parent reuse grants no child permission.
        release.clear()
        late = asyncio.create_task(child())
    release.set()
    await late  # A task inherited before exit cannot use a stale binding either.


async def test_concurrent_independent_owners_do_not_share_reuse_permission(paid):
    both = asyncio.Barrier(2)

    async def owner(reuse):
        with (
            bind_execution_scope(SCOPE),
            bind_retained_request(
                paid.binding,
                allow_followup_after_reuse=True,
            ) as state,
        ):
            await admit(paid)
            if reuse:
                mark_retained_reused(paid.binding["operation_key"])
            await both.wait()
            if reuse:
                request = deepcopy(paid.request)
                request.operation_id = "distinct-followup"
                await admit(paid, request=request, digest="b" * 64)
            else:
                with pytest.raises(RetainedRequestRejected, match="unpaid_request_forbidden"):
                    await admit(paid, digest="b" * 64)
            return state.reused

    assert await asyncio.gather(owner(True), owner(False)) == [True, False]


def test_attempt_hash_covers_full_rows_and_uses_iso_sorting():
    now = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
    row = {
        "id": "z",
        "operation_key": "a" * 64,
        "scope_id": SCOPE,
        "model_name": MODEL,
        "input_hash": DIGEST,
        "reserved_tokens": 100,
        "actual_tokens": 15,
        "status": "completed",
        "created_at": now,
    }
    other = {**row, "id": "a"}
    expected = [{**other, "created_at": now.isoformat()}, {**row, "created_at": now.isoformat()}]
    assert retained_attempts_sha256([row, other]) == content_hash(expected)
    assert retained_attempts_sha256([other, row]) == content_hash(expected)
    with pytest.raises(RetainedRequestRejected, match="attempt_columns"):
        retained_attempts_sha256(
            [{key: value for key, value in row.items() if key != "created_at"}]
        )
