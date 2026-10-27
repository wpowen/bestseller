"""One physical return keeps one receipt and its original attribution."""

import asyncio
from copy import deepcopy
from typing import get_args
from uuid import uuid4

import pytest
from sqlalchemy import JSON, ForeignKeyConstraint, MetaData, func, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from test_execution_budget_core import gateway_request, gateway_settings

from bestseller.infra.db.models import (
    ExecutionAttemptModel,
    ExecutionBudgetModel,
    ExecutionOperationModel,
    LlmRunModel,
    ProjectModel,
)
from bestseller.services import budget, llm

pytestmark = pytest.mark.unit


@pytest.fixture
async def receipt_db(tmp_path):
    path = tmp_path / "llm_receipt_binding_test.sqlite3"
    assert "test" in path.name
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    metadata = MetaData()
    for model in (ProjectModel, LlmRunModel):
        table = model.__table__.to_metadata(metadata)
        for column in table.columns:
            if isinstance(column.type, JSONB):
                column.type = JSON()
                column.server_default = None
        for constraint in list(table.constraints):
            if isinstance(constraint, ForeignKeyConstraint):
                table.constraints.remove(constraint)
    async with engine.begin() as connection:
        for model in (ExecutionBudgetModel, ExecutionOperationModel, ExecutionAttemptModel):
            await connection.run_sync(model.__table__.create)
        for table in metadata.tables.values():
            await connection.run_sync(table.create)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture
def paid_gateway(monkeypatch):
    sends = []

    async def send(request, role):
        sends.append(request)
        return "retained paid result", 10, 5, "stop", None, None

    async def no_project_override(*args):
        return None

    monkeypatch.setattr(llm, "_call_litellm", send)
    monkeypatch.setattr(llm, "_resolve_project_model_override", no_project_override)
    settings = gateway_settings(calls=5)
    settings.llm.planner.model = "openai/MiniMax-M3"
    settings.llm.planner.api_base = "https://api.minimaxi.com/v1"
    return settings, sends


def attributed_request(text="The orbital garden opens."):
    return gateway_request(operation_id="stable-paid-operation").model_copy(
        update={
            "project_id": uuid4(),
            "workflow_run_id": uuid4(),
            "step_run_id": uuid4(),
            "user_prompt": text,
            "prompt_version": "v2",
            "metadata": {"credentials": "must-not-be-cached"},
        }
    )


def replay_request(source):
    return source.model_copy(
        update={
            "project_id": uuid4(),
            "workflow_run_id": uuid4(),
            "step_run_id": uuid4(),
        }
    )


async def complete(session, settings, request):
    with budget.bind_execution_scope("audit:receipt-binding"):
        return await llm.complete_text(session, settings, request)


def test_paid_origin_accepts_every_request_role():
    for role in get_args(llm.LLMRole):
        request = attributed_request().model_copy(update={"logical_role": role})
        origin = llm._paid_call_origin(request, "paid-input-hash")
        restored = llm._cached_paid_call_origin({"paid_call_origin": origin})
        assert restored["status"] == "recorded"
        assert restored["logical_role"] == role


@pytest.mark.parametrize("text", ["The orbital garden opens.", "山城茶馆的旧账重现。"])
async def test_rollback_recovery_keeps_original_attribution_and_records_reader(
    receipt_db,
    paid_gateway,
    text,
):
    settings, sends = paid_gateway
    source = attributed_request(text)
    reader = replay_request(source)
    sessions = async_sessionmaker(receipt_db, expire_on_commit=False)
    async with sessions() as session:
        original = await complete(session, settings, source)
        await session.rollback()
    async with sessions() as session:
        restored = await complete(session, settings, reader)
        row = await session.get(LlmRunModel, restored.llm_run_id)
        assert (row.project_id, row.workflow_run_id, row.step_run_id) == (
            source.project_id,
            source.workflow_run_id,
            source.step_run_id,
        )
        assert row.metadata_json["paid_call_origin"]["status"] == "recorded"
        assert row.metadata_json["execution_recovery"]["workflow_run_id"] == str(
            reader.workflow_run_id
        )
        assert row.metadata_json["execution_recovery"]["project_id"] == str(reader.project_id)
        assert row.metadata_json["execution_recovery"]["step_run_id"] == str(reader.step_run_id)
        assert restored.metadata["reused_source_workflow_run_id"] == str(source.workflow_run_id)
        assert row.prompt_hash == restored.metadata["effective_input_receipt"]["hash"]
        assert "must-not-be-cached" not in str(row.metadata_json)
        await session.commit()
    async with sessions() as session:
        again = await complete(session, settings, replay_request(source))
        assert again.llm_run_id == restored.llm_run_id
        assert await session.scalar(select(func.count()).select_from(LlmRunModel)) == 1
        await session.commit()
    assert len(sends) == 1
    assert original.content == restored.content == again.content


async def test_replay_before_original_insert_binds_both_to_one_receipt(
    receipt_db,
    paid_gateway,
    monkeypatch,
):
    settings, sends = paid_gateway
    source = attributed_request()
    reader = replay_request(source)
    before_insert, release_original = asyncio.Event(), asyncio.Event()
    persist = llm._persist_llm_run_safely

    async def hold_original(session, row):
        if row.workflow_run_id == source.workflow_run_id:
            before_insert.set()
            await release_original.wait()
        return await persist(session, row)

    monkeypatch.setattr(llm, "_persist_llm_run_safely", hold_original)
    sessions = async_sessionmaker(receipt_db, expire_on_commit=False)

    async def original_call():
        async with sessions() as session:
            result = await complete(session, settings, source)
            await session.commit()
            return result

    task = asyncio.create_task(original_call())
    try:
        await asyncio.wait_for(before_insert.wait(), 3)
        async with sessions() as session:
            restored = await complete(session, settings, reader)
            await session.commit()
    finally:
        release_original.set()
    original = await asyncio.wait_for(task, 3)
    assert original.llm_run_id == restored.llm_run_id
    async with sessions() as session:
        rows = (await session.scalars(select(LlmRunModel))).all()
        assert len(rows) == 1
        assert rows[0].input_tokens + rows[0].output_tokens == 15
        assert rows[0].workflow_run_id == source.workflow_run_id
        assert rows[0].metadata_json["execution_recovery"]["workflow_run_id"] == str(
            reader.workflow_run_id
        )
    assert len(sends) == 1


async def test_multiple_recovery_callers_share_one_receipt(receipt_db, paid_gateway):
    settings, sends = paid_gateway
    source = attributed_request()
    sessions = async_sessionmaker(receipt_db, expire_on_commit=False)
    async with sessions() as session:
        await complete(session, settings, source)
        await session.rollback()

    async def recover():
        async with sessions() as session:
            result = await complete(session, settings, replay_request(source))
            await session.commit()
            return result.llm_run_id

    ids = await asyncio.wait_for(asyncio.gather(*(recover() for _ in range(3))), 5)
    assert len(set(ids)) == 1
    async with sessions() as session:
        assert await session.scalar(select(func.count()).select_from(LlmRunModel)) == 1
    assert len(sends) == 1


@pytest.mark.parametrize(
    "invalid_origin", [None, {"schema": "paid-call-origin.v1", "status": "recorded"}]
)
async def test_legacy_cache_does_not_invent_source_attribution(
    receipt_db,
    paid_gateway,
    invalid_origin,
):
    settings, sends = paid_gateway
    source = attributed_request()
    sessions = async_sessionmaker(receipt_db, expire_on_commit=False)
    async with sessions() as session:
        await complete(session, settings, source)
        await session.rollback()
    async with sessions() as session:
        operation = await session.scalar(
            select(ExecutionOperationModel).where(ExecutionOperationModel.status == "completed")
        )
        saved = {**operation.result_json, "metadata": dict(operation.result_json["metadata"])}
        saved["metadata"].pop("paid_call_origin", None)
        if invalid_origin is not None:
            saved["metadata"]["paid_call_origin"] = invalid_origin
        operation.result_json = saved
        await session.commit()
    reader = replay_request(source)
    async with sessions() as session:
        restored = await complete(session, settings, reader)
        row = await session.get(LlmRunModel, restored.llm_run_id)
        assert (row.project_id, row.workflow_run_id, row.step_run_id) == (None, None, None)
        assert row.metadata_json["paid_call_origin"]["status"] == "unknown"
        assert row.metadata_json["paid_call_origin"]["legacy"] is True
        assert row.metadata_json["execution_recovery"]["workflow_run_id"] == str(
            reader.workflow_run_id
        )
        assert restored.metadata["reused_source_workflow_run_id"] is None
        await session.commit()
    assert len(sends) == 1


async def test_legacy_cache_keeps_existing_legal_receipt(receipt_db, paid_gateway):
    settings, sends = paid_gateway
    source = attributed_request()
    reader = replay_request(source)
    sessions = async_sessionmaker(receipt_db, expire_on_commit=False)
    async with sessions() as session:
        original = await complete(session, settings, source)
        await session.commit()
    async with sessions() as session:
        operation = await session.scalar(
            select(ExecutionOperationModel).where(ExecutionOperationModel.status == "completed")
        )
        saved = {**operation.result_json, "metadata": dict(operation.result_json["metadata"])}
        saved["metadata"].pop("paid_call_origin", None)
        operation.result_json = saved
        existing = await session.get(LlmRunModel, original.llm_run_id)
        existing.metadata_json = {
            key: value for key, value in existing.metadata_json.items() if key != "paid_call_origin"
        }
        original_hash = existing.prompt_hash
        original_metadata = deepcopy(existing.metadata_json)
        await session.commit()
    async with sessions() as session:
        restored = await complete(session, settings, reader)
        assert restored.llm_run_id == original.llm_run_id
        row = await session.get(LlmRunModel, restored.llm_run_id)
        assert (row.project_id, row.workflow_run_id, row.step_run_id) == (
            source.project_id,
            source.workflow_run_id,
            source.step_run_id,
        )
        assert row.prompt_hash == original_hash
        assert (row.input_tokens, row.output_tokens) == (10, 5)
        assert row.metadata_json == original_metadata
        assert restored.metadata["execution_recovery"]["workflow_run_id"] == str(
            reader.workflow_run_id
        )
        assert restored.metadata["reused_source_workflow_run_id"] == str(source.workflow_run_id)
        assert restored.metadata["paid_call_origin"]["status"] == "unknown"
        assert restored.metadata["paid_call_origin"]["legacy"] is True
        assert await session.scalar(select(func.count()).select_from(LlmRunModel)) == 1
        await session.commit()
    assert len(sends) == 1


async def test_origin_survives_exit_immediately_after_paid_settlement(
    receipt_db,
    paid_gateway,
    monkeypatch,
):
    settings, sends = paid_gateway
    source = attributed_request()
    settle = budget.ExecutionLedger.settle

    class HardExit(BaseException):
        pass

    async def exit_after_settlement(self, *args, **kwargs):
        await settle(self, *args, **kwargs)
        raise HardExit()

    sessions = async_sessionmaker(receipt_db, expire_on_commit=False)
    with monkeypatch.context() as patch:
        patch.setattr(budget.ExecutionLedger, "settle", exit_after_settlement)
        async with sessions() as session:
            with pytest.raises(HardExit):
                await complete(session, settings, source)
    async with sessions() as session:
        restored = await complete(session, settings, replay_request(source))
        row = await session.get(LlmRunModel, restored.llm_run_id)
        assert row.workflow_run_id == source.workflow_run_id
        assert row.step_run_id == source.step_run_id
        assert row.project_id == source.project_id
        await session.commit()
    assert len(sends) == 1


async def test_distinct_operations_keep_distinct_paid_receipts(receipt_db, paid_gateway):
    settings, sends = paid_gateway
    source = attributed_request()
    sessions = async_sessionmaker(receipt_db, expire_on_commit=False)
    ids = []
    for operation in ("operation-one", "operation-two"):
        async with sessions() as session:
            result = await complete(
                session, settings, source.model_copy(update={"operation_id": operation})
            )
            ids.append(result.llm_run_id)
            await session.commit()
    assert len(set(ids)) == 2
    assert len(sends) == 2
