"""Actual ledger reserve/settle transactions, isolated SQLite, never network."""

from datetime import datetime, timezone, timedelta
from uuid import uuid4
import pytest
from sqlalchemy import MetaData, JSON, ForeignKeyConstraint, select, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from bestseller.infra.db import models as m
from bestseller.services import finite_book_budget as finite, llm
from bestseller.services.minimax_book_cost_contract import CONTRACT_SHA256
from bestseller.services.budget import ExecutionLedger, ExecutionBudgetError
from bestseller.settings import load_settings, LLMRoleSettings

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.fixture
async def case(tmp_path):
    engine = create_async_engine(
        "sqlite+aiosqlite:///" + str(tmp_path / "finite_book_test.sqlite3")
    )
    meta = MetaData()
    for model in (m.ExecutionBudgetModel, m.ExecutionOperationModel, m.ExecutionAttemptModel):
        t = model.__table__.to_metadata(meta)
        for c in t.columns:
            if isinstance(c.type, JSONB):
                c.type = JSON()
                c.server_default = None
        for constraint in list(t.constraints):
            if isinstance(constraint, ForeignKeyConstraint):
                t.constraints.remove(constraint)
    async with engine.begin() as conn:
        for t in meta.tables.values():
            await conn.run_sync(t.create)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    rid = str(uuid4())
    pid = str(uuid4())
    scope = "original-scope"
    wid = str(uuid4())
    async with factory() as s:
        meta = {
            "provider_authorization": {
                "mode": "completion_bound",
                "status": "active",
                "models": ["openai/MiniMax-M3"],
                "endpoint_hosts": ["api.minimaxi.com"],
                "authorization_ref": "old-human",
                "objective": "old-book",
            }
        }
        meta = finite.install(
            meta,
            run_id=rid,
            project_id=pid,
            model="openai/MiniMax-M3.1-Flash",
            endpoint="https://api.minimaxi.com/v1",
            caps={
                "calls": 3,
                "money_micro_cny": 1100000,
                "primary": 2,
                "transport_retry": 1,
                "hard_repair": 0,
            },
            counter_sha256=CONTRACT_SHA256,
            authorization_ref="fixture-explicit-book-only",
            explicit_approval=True,
            capacity_limit=196608,
        )
        s.add(
            m.ExecutionBudgetModel(
                scope_id=scope,
                max_calls=64,
                max_tokens=1000000,
                calls_used=316,
                tokens_charged=6112141,
                status="active",
                deadline_at=datetime.now(timezone.utc) - timedelta(days=2),
                metadata_json=meta,
            )
        )
        await s.commit()
    ledger = ExecutionLedger(engine, scope, load_settings().budget)

    async def quote():
        req = llm.LLMCompletionRequest(
            logical_role="critic",
            project_id=pid,
            workflow_run_id=wid,
            system_prompt="合成",
            user_prompt="不含生产正文",
            fallback_response="{}",
            prompt_template="draft_continuity_hard_safety",
        )
        role = LLMRoleSettings(
            model="openai/MiniMax-M3.1-Flash",
            api_base="https://api.minimaxi.com/v1",
            temperature=0.2,
            max_tokens=6500,
            timeout_seconds=10,
        )
        with (
            llm.bind_execution_scope(scope),
            finite.bind_run(rid, project_id=pid, stage="4:review"),
        ):
            return await finite.quote(
                req,
                role,
                llm._effective_input_payload(req, role),
                llm._effective_input_hash(req, role),
            )

    yield ledger, factory, rid, await quote(), wid
    await engine.dispose()


async def attempt(ledger, q, wid, op):
    if op in {"second", "next"}:
        q = {**q, "stage": "5:review"}
    claim = await ledger.claim(op, q["input_hash"])
    aid = await ledger.reserve(
        claim,
        q["model"],
        q["input_hash"],
        1000,
        endpoint=q["endpoint"],
        workflow_id=wid,
        template="book-fixture",
        version="1",
        finite_book_binding=q,
    )
    return claim, aid


async def test_atomic_money_limit_applies_to_completion_bound_and_rolls_back_grant(case):
    ledger, factory, rid, q, wid = case
    claim, aid = await attempt(ledger, q, wid, "first")
    with pytest.raises(ExecutionBudgetError, match="finite_money_exhausted"):
        await attempt(ledger, q, wid, "second")
    async with factory() as s:
        row = await s.get(m.ExecutionBudgetModel, ledger.scope_id)
        run = row.metadata_json["finite_book_runs"][rid]
        assert (row.calls_used, row.max_calls, row.max_tokens) == (317, 64, 1000000)
        assert run["calls"] == 1 and run["held_micro_cny"] == 1026000
        assert len(row.metadata_json["single_dispatch_grants"]) == 1
        assert row.metadata_json["provider_authorization"]["models"] == ["openai/MiniMax-M3"]
        assert await s.scalar(select(func.count()).select_from(m.ExecutionAttemptModel)) == 1


async def test_actual_usage_releases_unused_money_exactly_once(case):
    ledger, factory, rid, q, wid = case
    claim, aid = await attempt(ledger, q, wid, "first")
    result = {"model_name": q["model"], "input_tokens": 100, "output_tokens": 10}
    await ledger.settle(aid, "completed", actual_tokens=110, claim=claim, result=result)
    await ledger.settle(aid, "completed", actual_tokens=110, claim=claim, result=result)
    async with factory() as s:
        row = await s.get(m.ExecutionBudgetModel, ledger.scope_id)
        run = row.metadata_json["finite_book_runs"][rid]
        assert run["spent_micro_cny"] == 140 and run["held_micro_cny"] == 0
        assert (row.calls_used, row.tokens_charged) == (317, 6112141 + 110)
    await attempt(ledger, q, wid, "next")


@pytest.mark.parametrize("status,tokens", [("unknown", None), ("rejected", 0), ("completed", None)])
async def test_unknown_or_legacy_inferred_zero_keeps_money_and_stops(case, status, tokens):
    ledger, factory, rid, q, wid = case
    claim, aid = await attempt(ledger, q, wid, "uncertain")
    await ledger.settle(aid, status, actual_tokens=tokens)
    async with factory() as s:
        row = await s.get(m.ExecutionBudgetModel, ledger.scope_id)
        run = row.metadata_json["finite_book_runs"][rid]
        assert (
            row.status == "unknown"
            and run["held_micro_cny"] == 1026000
            and run["status"] == "unknown"
        )
    with pytest.raises(ExecutionBudgetError):
        await attempt(ledger, q, wid, "no-resend")


async def test_retry_cannot_chain_grandchildren_and_calls_have_atomic_limit(case):
    ledger, factory, rid, q, wid = case
    claim, aid = await attempt(ledger, q, wid, "same")
    await ledger.settle(aid, "not_sent", actual_tokens=0)
    retry = {**q, "category": "transport_retry", "retry_of": aid}
    second = await ledger.reserve(
        claim,
        retry["model"],
        retry["input_hash"],
        1000,
        endpoint=retry["endpoint"],
        workflow_id=wid,
        template="book-fixture",
        version="1",
        finite_book_binding=retry,
    )
    await ledger.settle(second, "not_sent", actual_tokens=0)
    retry2 = {**retry, "retry_of": second}
    with pytest.raises(ExecutionBudgetError):
        await ledger.reserve(
            claim,
            retry2["model"],
            retry2["input_hash"],
            1000,
            endpoint=retry2["endpoint"],
            workflow_id=wid,
            template="book-fixture",
            version="1",
            finite_book_binding=retry2,
        )
    async with factory() as s:
        row = await s.get(m.ExecutionBudgetModel, ledger.scope_id)
        assert row.metadata_json["finite_book_runs"][rid]["calls"] == 2


async def test_unbound_context_cannot_bypass_active_finite_cap(case):
    ledger, factory, rid, q, wid = case
    claim = await ledger.claim("bypass", "x")
    with pytest.raises(ExecutionBudgetError, match="finite_run_context_required"):
        await ledger.reserve(
            claim, "openai/MiniMax-M3", "x", 100, endpoint=q["endpoint"], workflow_id=wid
        )


async def test_formal_m31_cost_contract_accepts_native_assistant_replay_only():
    from bestseller.services.minimax_book_cost_contract import input_bound

    payload = {
        "model": "openai/MiniMax-M3.1-Flash",
        "n_candidates": 1,
        "max_tokens": 16384,
        "tools": [],
        "messages": [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {"type": "function", "function": {"name": "submit", "arguments": "{}"}}
                ],
            },
            {"role": "tool", "content": "ok"},
        ],
    }
    assert input_bound(payload)["input_tokens_upper"] == 1000000
    payload["messages"][0]["content"] = [
        {"type": "image_url", "image_url": {"url": "https://no.invalid/a"}}
    ]
    with pytest.raises(ValueError):
        input_bound(payload)


@pytest.mark.parametrize("capacity,allowed", [(65536, True), (196608, True), (196609, False)])
async def test_approved_capacity_and_stricter_frontier_guard_before_claim(case, capacity, allowed):
    ledger, factory, rid, q, wid = case
    request = llm.LLMCompletionRequest(
        logical_role="critic",
        project_id=q["project_id"],
        workflow_run_id=wid,
        system_prompt="合成",
        user_prompt="离线边界",
        fallback_response="{}",
        prompt_template="draft_continuity_hard_safety",
    )
    role = LLMRoleSettings(
        model=q["model"],
        api_base=q["endpoint"],
        max_tokens=6500,
        temperature=0.2,
        timeout_seconds=10,
    )
    payload = llm._effective_input_payload(request, role)
    digest = llm._effective_input_hash(request, role)
    with (
        llm.bind_execution_scope(ledger.scope_id),
        finite.bind_run(rid, project_id=q["project_id"], stage="4:review", capacity_limit=capacity),
    ):
        async with factory() as s:
            if allowed:
                await finite.guard_prepared_request(request, role, payload, digest, session=s)
            else:
                with pytest.raises(
                    finite.FiniteBudgetError, match="finite_capacity_binding_changed"
                ):
                    await finite.guard_prepared_request(request, role, payload, digest, session=s)
            assert await s.scalar(select(func.count()).select_from(m.ExecutionOperationModel)) == 0
            assert await s.scalar(select(func.count()).select_from(m.ExecutionAttemptModel)) == 0
            row = await s.get(m.ExecutionBudgetModel, ledger.scope_id)
            assert row.calls_used == 316
            assert row.metadata_json["finite_book_runs"][rid]["calls"] == 0


async def test_finite_run_output_ceiling_is_preclaim_and_locked_reserve_fence(case):
    ledger, factory, rid, q, wid = case
    request = llm.LLMCompletionRequest(
        logical_role="critic",
        project_id=q["project_id"],
        workflow_run_id=wid,
        system_prompt="合成",
        user_prompt="单本输出边界",
        fallback_response="{}",
        prompt_template="draft_continuity_hard_safety",
        max_tokens_override=32768,
    )
    role = LLMRoleSettings(
        model=q["model"],
        api_base=q["endpoint"],
        temperature=0.2,
        max_tokens=32769,
        timeout_seconds=10,
    )
    payload = llm._effective_input_payload(request, role)
    assert payload["max_tokens"] == 32768  # Explicit legal request, independent of model capacity.
    digest = llm._effective_input_hash(request, role)
    with (
        llm.bind_execution_scope(ledger.scope_id),
        finite.bind_run(rid, project_id=q["project_id"], stage="4:review"),
    ):
        async with factory() as session:
            # A stricter normal effective request is legal under the sealed book cap.
            await finite.guard_prepared_request(request, role, payload, digest, session=session)
            # The real M3.1 model supports larger outputs; the immutable finite
            # authorization must still reject an oversized actual request.
            oversized = request.model_copy(update={"max_tokens_override": 32769})
            actual = llm._effective_input_payload(oversized, role)
            assert actual["max_tokens"] == 32769
            with pytest.raises(finite.FiniteBudgetError, match="finite_output_limit"):
                await finite.guard_prepared_request(oversized, role, actual,
                    llm._effective_input_hash(oversized, role), session=session)
            # Independently challenge the prepared payload fence, without raising
            # any global/normal writer/provider output ceiling.
            import hashlib, json

            payload = {**payload, "max_tokens": 32769}
            digest = hashlib.sha256(
                json.dumps(
                    payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                ).encode()
            ).hexdigest()
            with pytest.raises(finite.FiniteBudgetError, match="finite_output_limit"):
                await finite.guard_prepared_request(request, role, payload, digest, session=session)
            assert (
                await session.scalar(select(func.count()).select_from(m.ExecutionOperationModel))
                == 0
            )
        quote = await finite.quote(request, role, payload, digest)
        claim = await ledger.claim("output-limit-direct", digest)
        with pytest.raises(ExecutionBudgetError, match="finite_output_limit"):
            await ledger.reserve(
                claim,
                role.model,
                digest,
                1000,
                endpoint=role.api_base,
                workflow_id=wid,
                finite_book_binding=quote,
            )
    async with factory() as session:
        row = await session.get(m.ExecutionBudgetModel, ledger.scope_id)
        assert row.calls_used == 316
        assert row.metadata_json["finite_book_runs"][rid]["calls"] == 0
        assert await session.scalar(select(func.count()).select_from(m.ExecutionAttemptModel)) == 0
