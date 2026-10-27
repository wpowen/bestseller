from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import MetaData, JSON, ForeignKeyConstraint, select, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from bestseller.services import production_strategy as strategy
from bestseller.services.budget import ExecutionLedger, ExecutionBudgetError
from bestseller.infra.db import models as m
from bestseller.settings import load_settings

pytestmark = pytest.mark.unit


def test_breadth_changes_leave_all_quality_controls_and_research_unchanged():
    original = dict(n_candidates=6, premise_card_count=4, raw_idea_pool_multiplier=3,
                    judge_hard_floors={"genre": 8}, story_layer_votes=2,
                    story_architecture_contract_enabled=True, commercial_gate_mode="advisory")
    before = deepcopy(original)
    complete = strategy.tournament_config(original, "complete")
    assert complete["n_candidates"] == 2 and complete["premise_card_count"] == 1
    for key in ("judge_hard_floors", "story_layer_votes", "story_architecture_contract_enabled", "commercial_gate_mode"):
        assert complete[key] == before[key]
    assert strategy.tournament_config(original, "research") == before == original
    assert strategy.candidate_count(12, "complete") == 2
    assert strategy.candidate_count(12, "research") == 12


def test_legacy_resume_never_silently_changes_its_paid_inputs():
    assert strategy.profile_from_hints({"cost_style": "minimal"}) == "research"
    assert strategy.normalize_profile(None) == "complete"
    assert strategy.profile_from_hints({"production_profile": "complete"}) == "complete"
    with pytest.raises(ValueError):
        strategy.normalize_profile("skip-gates")


def test_downstream_guard_resets_on_error_and_preserves_nested_context():
    args = dict(calls_used=26, max_calls=64, tokens_charged=100, max_tokens=1000000, requested_tokens=100)
    with strategy.protect_downstream("complete", 4):
        with pytest.raises(ExecutionBudgetError, match="execution_downstream_budget_reserved"):
            strategy.assert_conception_headroom(**args)
        with strategy.protect_downstream("research", 4):
            strategy.assert_conception_headroom(**args)
        with pytest.raises(ExecutionBudgetError):
            strategy.assert_conception_headroom(**args)
    strategy.assert_conception_headroom(**args)


@pytest.mark.asyncio
async def test_actual_ledger_keeps_downstream_credit_and_reuses_paid_return(tmp_path):
    engine = create_async_engine("sqlite+aiosqlite:///" + str(tmp_path / "production_strategy_test.sqlite3"))
    meta = MetaData()
    for model in (m.ExecutionBudgetModel, m.ExecutionOperationModel, m.ExecutionAttemptModel):
        table = model.__table__.to_metadata(meta)
        for column in table.columns:
            if isinstance(column.type, JSONB):
                column.type = JSON()
                column.server_default = None
        for constraint in list(table.constraints):
            if isinstance(constraint, ForeignKeyConstraint):
                table.constraints.remove(constraint)
    async with engine.begin() as connection:
        await connection.run_sync(meta.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        session.add(m.ExecutionBudgetModel(scope_id="original", max_calls=64, max_tokens=1000000,
            calls_used=25, tokens_charged=1000, status="active",
            deadline_at=datetime.now(timezone.utc) + timedelta(hours=1), metadata_json={}))
        await session.commit()
    ledger = ExecutionLedger(engine, "original", load_settings().budget)
    try:
        claim = await ledger.claim("idea", "hash")
        with strategy.protect_downstream("complete", 4):
            attempt = await ledger.reserve(claim, "test-model", "hash", 100)
            await ledger.settle(attempt, "completed", actual_tokens=50, claim=claim,
                                result={"content": "retained synthetic result", "model_name": "test-model"})
            # A cached successful operation is reusable without spending the
            # protected 38 calls, even though a fresh call cannot be admitted.
            cached = await ledger.claim("idea", "hash")
            assert cached.cached_result == {"content": "retained synthetic result", "model_name": "test-model"}
            next_claim = await ledger.claim("new-idea", "other-hash")
            with pytest.raises(ExecutionBudgetError, match="execution_downstream_budget_reserved"):
                await ledger.reserve(next_claim, "test-model", "other-hash", 100)
        async with factory() as session:
            row = await session.get(m.ExecutionBudgetModel, "original")
            assert (row.calls_used, row.tokens_charged, row.status) == (26, 1050, "active")
            assert await session.scalar(select(func.count()).select_from(m.ExecutionAttemptModel)) == 1
        # Once conception exits, the same original credit is available to body
        # work. No new scope, ceiling increase or fabricated result is needed.
        await ledger.reserve(next_claim, "test-model", "other-hash", 100)
    finally:
        await engine.dispose()


def test_token_headroom_is_enforced_without_releasing_unknown_reservations():
    with strategy.protect_downstream("complete", 4):
        with pytest.raises(ExecutionBudgetError, match="execution_downstream_budget_reserved"):
            strategy.assert_conception_headroom(calls_used=1, max_calls=64,
                tokens_charged=930000, max_tokens=1000000, requested_tokens=3000)


def test_explicit_research_also_protects_body_without_changing_transport():
    with strategy.protect_downstream("research", 4, reserve_research=True):
        assert strategy.stream_conception_enabled() is False
        with pytest.raises(ExecutionBudgetError):
            strategy.assert_conception_headroom(calls_used=26, max_calls=64,
                tokens_charged=1000, max_tokens=1000000, requested_tokens=100)


def test_resume_keeps_frozen_strategy_and_rejects_implicit_new_default():
    original = {"user_hints": {"production_profile": "research", "concept_seed": "supplied"}}
    incoming = {"user_hints": {"concept_seed": "supplied"}}
    frozen = strategy.freeze_resume_profile(original, incoming)
    assert frozen["user_hints"]["production_profile"] == "research"
    assert "production_profile" not in incoming["user_hints"]
    for original in ({}, original):
        with pytest.raises(ValueError, match="production_profile_changed"):
            strategy.freeze_resume_profile(original, {"production_profile": "complete"})


@pytest.mark.parametrize("profile", [None, "complete", "research"])
def test_real_creation_boundary_freezes_strategy_in_receipt_and_input(tmp_path, monkeypatch, profile):
    from bestseller.web import server
    manager = server.WebTaskManager(persist_path=tmp_path / "tasks.json")
    captured = []
    monkeypatch.setattr(manager, "create_autowrite_task", lambda payload: captured.append(payload) or {"task_id": "fixture"})
    payload = {"genre_key": "apocalypse-supply", "chapter_count": 4}
    if profile:
        payload["production_profile"] = profile
    result = manager.create_quickstart_task(payload)
    expected = profile or "complete"
    assert captured[0]["user_hints"]["production_profile"] == expected
    assert captured[0]["production_profile"] == expected
    assert result["creation_input_receipt"]["production_profile"] == expected


def test_conception_wrapper_protects_new_modes_without_changing_legacy_identity():
    from bestseller.services.conception_checkpoints import stable_conception_input_hash
    original = {"attempt_id": "old", "input_payload": {"user_hints": {"production_profile": "complete"}}}
    resumed = deepcopy(original)
    resumed["attempt_id"] = "new-workflow"
    assert stable_conception_input_hash(original) == stable_conception_input_hash(resumed)
    resumed["input_payload"]["user_hints"]["production_profile"] = "research"
    assert stable_conception_input_hash(original) != stable_conception_input_hash(resumed)
