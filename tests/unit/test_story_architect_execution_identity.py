"""The architect's explicit identity must address its original implicit slot zero."""

from copy import deepcopy
import hashlib
import json
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bestseller.services import llm, model_catalog, story_architect as architect
from bestseller.services.budget import ExecutionBudgetError, bind_execution_scope
from bestseller.settings import load_settings

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]
BODY = json.dumps({"sub_genres": ["orbital exploration"], "setting": "A moving observatory",
                   "tone": "hopeful", "trope_tags": ["survey expedition"]})
ARGS = dict(primary_genre="science-fantasy", language="en", user_hints=None,
            existing_facets=[], genre_intent=None)


@compiles(JSONB, "sqlite")
def _sqlite_jsonb(type_, compiler, **kw):
    return "JSON"


def _old_request():
    return llm.LLMCompletionRequest(
        logical_role="planner", system_prompt=architect._build_system_prompt("en"),
        user_prompt=architect._build_user_prompt(**ARGS),
        fallback_response="{}",
        prompt_template="story_architect_v1", prompt_version="1.0",
        metadata={"agent": "story_architect", "genre": "science-fantasy",
                  "genre_intent_contract_hash": None, "semantic_repair_findings": []},
    )


def _old_operation_id(request, role):
    effective_hash = llm._effective_input_hash(request, role)
    semantic = hashlib.sha256(json.dumps({
        "input_hash": effective_hash, "role": request.logical_role,
        "template": request.prompt_template, "version": request.prompt_version,
    }, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()
    return semantic + ":slot:0", effective_hash


@pytest.mark.parametrize("route", ["role", "catalog", "unavailable", "runtime-profile"])
async def test_explicit_id_matches_old_gateway_derivation_and_preserves_request(monkeypatch, route):
    from bestseller import settings as settings_module

    settings = load_settings(env={})
    settings.llm.planner.model = "openai/synthetic-role"
    settings.llm.planner.api_base = "https://role.invalid/v1"
    effective = settings.model_copy(deep=True)
    if route == "runtime-profile":
        effective.llm.planner.model = "openai/synthetic-profile"
        effective.llm.planner.api_base = "https://profile.invalid/v1"
    monkeypatch.setattr(settings_module, "apply_runtime_llm_profile", lambda value: effective)
    entry = SimpleNamespace(model="openai/synthetic-catalog", api_base="https://catalog.invalid/v1",
                            api_key_env=None, api_key_header=None, available=route != "unavailable")
    monkeypatch.setattr(model_catalog, "get_model_catalog_entry", lambda key: entry)
    role = effective.llm.planner
    if route == "catalog":
        role = llm._apply_model_override(role, entry)
    captured = []

    async def complete(session, config, request):
        captured.append(request)
        return SimpleNamespace(content=BODY)

    monkeypatch.setattr(architect, "complete_text", complete)
    with llm.bind_conception_model("synthetic-catalog" if route in {"catalog", "unavailable"} else None):
        await architect._call_architect_llm(None, settings, **ARGS)
    request = captured[0]
    old = _old_request()
    operation_id, effective_hash = _old_operation_id(old, role)
    assert request.operation_id == operation_id
    assert request.sample_slot is None  # ':sample:0' would address a different root
    assert llm._effective_input_payload(request, role) == llm._effective_input_payload(old, role)
    assert request.metadata["prepared_effective_input"] == {
        "schema": "effective-input.v1", "hash": effective_hash,
    }
    for field in ("system_prompt", "user_prompt", "logical_role", "prompt_template",
                  "prompt_version", "model_tier", "model_catalog_key"):
        assert getattr(request, field) == getattr(old, field)
    assert {k: request.metadata[k] for k in old.metadata} == old.metadata


async def test_execution_identity_failure_is_not_retried_as_creative_repair(monkeypatch):
    calls = []
    error = ExecutionBudgetError("execution_prepared_input_changed")

    async def existing(*args, **kwargs):
        return []

    async def fail(*args, **kwargs):
        calls.append(1)
        raise error

    monkeypatch.setattr(architect, "list_existing_facets", existing)
    monkeypatch.setattr(architect, "_call_architect_llm", fail)
    with pytest.raises(ExecutionBudgetError) as caught:
        await architect.architect_story_facets(None, load_settings(env={}),
                                               primary_genre="science-fantasy", language="en")
    assert caught.value is error and calls == [1]


async def test_repair_input_has_its_own_exact_slot_zero_identity(monkeypatch):
    from bestseller.services.llm_closed_loop import LLMGateFinding

    settings = load_settings(env={})
    requests = []

    async def complete(session, config, request):
        requests.append(request)
        return SimpleNamespace(content=BODY)

    monkeypatch.setattr(architect, "complete_text", complete)
    await architect._call_architect_llm(None, settings, **ARGS)
    await architect._call_architect_llm(None, settings, **ARGS, repair_findings=[LLMGateFinding(
        code="OBSERVATORY_SETTING_INVALID", severity="major", path="story_facets.setting",
        message="Repair the setting", expected="orbital", actual="surface",
        repair_action="Keep the original exploration request and correct the location.",
    )])
    assert requests[0].operation_id != requests[1].operation_id
    assert requests[1].prompt_template == "story_architect_v1_repair"
    assert all(r.operation_id.endswith(":slot:0") and r.sample_slot is None for r in requests)
    assert requests[0].metadata["prepared_effective_input"] != requests[1].metadata["prepared_effective_input"]


@pytest.fixture
async def architect_db(tmp_path):
    from bestseller.infra.db.models import (
        ExecutionAttemptModel, ExecutionBudgetModel, ExecutionOperationModel, LlmRunModel,
    )
    path = tmp_path / "architect_identity_test.sqlite3"
    assert "test" in path.name
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    async with engine.begin() as conn:
        for model in (ExecutionBudgetModel, ExecutionOperationModel, ExecutionAttemptModel, LlmRunModel):
            await conn.run_sync(model.__table__.create)
    yield engine
    await engine.dispose()


@pytest.mark.parametrize("drift", [False, True])
async def test_original_implicit_paid_root_reused_or_drift_blocked_without_send(architect_db, monkeypatch, drift):
    from bestseller.infra.db.models import (
        ExecutionAttemptModel, ExecutionBudgetModel, ExecutionOperationModel, LlmRunModel,
    )
    from bestseller import settings as settings_module

    settings = load_settings(env={})
    settings.llm.mock = False
    settings.llm.planner.model = "openai/synthetic-role"
    settings.llm.planner.rate_limit_fallback_model = None
    settings.artifact_store.retain_prompt_payloads = False
    sends = []

    async def send(request, role):
        sends.append(role.model)
        return BODY, 10, 5, "stop", None, None

    monkeypatch.setattr(llm, "_call_litellm", send)
    scope = "conception:synthetic-architect-original"
    sessions = async_sessionmaker(architect_db, expire_on_commit=False)
    with bind_execution_scope(scope):
        async with sessions() as session:
            original = await llm.complete_text(session, settings, _old_request())
            await session.commit()
    operation_key = original.metadata["execution_receipt"]["operation_key"]
    async with sessions() as session:
        root = await session.get(ExecutionOperationModel, operation_key)
        before = {c.name: deepcopy(getattr(root, c.key)) for c in root.__table__.columns}
        attempts_before = [a.id for a in (await session.scalars(select(ExecutionAttemptModel))).all()]
    effective = settings.model_copy(deep=True)
    profile_calls = 0

    def profile(config):
        nonlocal profile_calls
        profile_calls += 1
        if drift and profile_calls >= 2:
            changed = effective.model_copy(deep=True)
            changed.llm.planner.api_base = "https://changed-endpoint.invalid/v1"
            return changed
        return effective

    monkeypatch.setattr(settings_module, "apply_runtime_llm_profile", profile)
    monkeypatch.setattr(llm, "apply_runtime_llm_profile", profile)
    with bind_execution_scope(scope), llm.bind_execution_operation("new-ambient-phase-must-not-rebind-old-root"):
        async with sessions() as session:
            if drift:
                with pytest.raises(ExecutionBudgetError, match="execution_prepared_input_changed"):
                    await architect._call_architect_llm(session, settings, **ARGS)
            else:
                facets = await architect._call_architect_llm(session, settings, **ARGS)
                assert facets.setting == "A moving observatory"
            await session.commit()
    assert sends == ["openai/synthetic-role"]
    async with sessions() as session:
        root = await session.get(ExecutionOperationModel, operation_key)
        assert {c.name: getattr(root, c.key) for c in root.__table__.columns} == before
        assert [a.id for a in (await session.scalars(select(ExecutionAttemptModel))).all()] == attempts_before
        budget = await session.get(ExecutionBudgetModel, scope)
        assert budget.calls_used == 1 and budget.tokens_charged == 15
        rows = (await session.scalars(select(LlmRunModel))).all()
        assert len(rows) == 1 and rows[0].id == original.llm_run_id
        assert rows[0].prompt_hash == root.input_hash
