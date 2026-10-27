"""Actual judge -> gateway resolution stops at claim; no transport or paid reservation."""

from copy import deepcopy
import json
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select
from test_commercial_component_cohort import SCOPE
from test_commercial_component_cohort import cohort as cohort
from test_commercial_judge_recovery import BODY

from bestseller import settings as settings_module
from bestseller.infra.db.models import (
    ExecutionAttemptModel,
    ExecutionOperationModel,
)
from bestseller.services import chapter_llm_quality_judge as judge
from bestseller.services import commercial_judge_recovery as recovery
from bestseller.services import llm, model_catalog
from bestseller.services.budget import ExecutionBudgetError, bind_execution_scope
from bestseller.services.model_catalog import ModelCatalogEntry

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


class ClaimIntercept(BaseException):
    """Stop the real gateway before claim can reserve money or send anything."""


@pytest.fixture
async def gateway_probe(cohort, monkeypatch, tmp_path):
    rig = cohort
    rig.settings.llm.mock = False
    rig.settings.llm.retry.rate_limit_fallback_enabled = False
    rig.settings.llm.critic.model = "openai/fixture-base"
    rig.settings.llm.critic.model_override = None
    rig.settings.llm.critic.api_base = "https://base.fixture.invalid/v1"
    rig.settings.llm.critic.temperature = 0.27
    rig.settings.llm.critic.max_tokens = 4096
    rig.settings.llm.critic.stream = True
    monkeypatch.delenv("BESTSELLER__LLM__COMMERCIAL_JUDGE_MODEL_KEY", raising=False)
    profile = tmp_path / "runtime-profile.json"
    profile.write_text(json.dumps({"active_profile": ""}))
    monkeypatch.setenv(settings_module.LLM_RUNTIME_PROFILE_ENV, str(profile))
    entries = {
        "project-fixture": ModelCatalogEntry(
            id="project-fixture",
            display_name="Project fixture",
            vendor="fixture",
            model="openai/fixture-project",
            api_base="https://project.fixture.invalid/v1",
        ),
        "commercial-fixture": ModelCatalogEntry(
            id="commercial-fixture",
            display_name="Commercial fixture",
            vendor="fixture",
            model="openai/fixture-commercial",
            api_base="https://commercial.fixture.invalid/v1",
        ),
    }
    catalog_reads = []
    prepared, hashed, claims, requests = [], [], [], []

    def catalog_entry(key):
        catalog_reads.append(key)
        return entries.get(key)

    monkeypatch.setattr(model_catalog, "get_model_catalog_entry", catalog_entry)
    original_prepare = recovery.prepare_commercial_component_slot

    async def observe_preparation(*args, **kwargs):
        result = await original_prepare(*args, **kwargs)
        prepared.append(
            {
                "input_hash": kwargs["current_input_hash"],
                "effective_critic": deepcopy(kwargs["effective_critic"]),
                "sources": kwargs["sources"],
                "binding": deepcopy(result[1]),
            }
        )
        return result

    monkeypatch.setattr(recovery, "prepare_commercial_component_slot", observe_preparation)
    original_hash = llm._effective_input_hash

    def observe_actual_hash(request, role):
        digest = original_hash(request, role)
        if request.prompt_template == "chapter_commercial_quality_judge":
            hashed.append(
                {
                    "request": request,
                    "role": role.model_copy(deep=True),
                    "hash": digest,
                    "payload": llm._effective_input_payload(request, role),
                }
            )
        return digest

    monkeypatch.setattr(llm, "_effective_input_hash", observe_actual_hash)

    class ClaimProbe:
        def __init__(self, engine, scope, budget):
            self.scope = scope

        async def claim(self, operation_id, prompt_hash):
            claims.append({"scope": self.scope, "operation_id": operation_id, "hash": prompt_hash})
            raise ClaimIntercept()

    monkeypatch.setattr(llm, "ExecutionLedger", ClaimProbe)

    async def forbidden_transport(*args, **kwargs):
        pytest.fail("provider transport must never be entered")

    monkeypatch.setattr(llm, "_call_litellm_with_retry", forbidden_transport)
    monkeypatch.setattr(llm, "_call_litellm", forbidden_transport)

    # Keep the actual gateway, including its own runtime/catalog/strong resolver.
    async def actual_gateway(session, settings, request):
        requests.append(request)
        return await llm.complete_text(session, settings, request)

    monkeypatch.setattr(judge, "complete_text", actual_gateway)
    return SimpleNamespace(
        rig=rig,
        entries=entries,
        profile=profile,
        catalog_reads=catalog_reads,
        prepared=prepared,
        hashed=hashed,
        claims=claims,
        requests=requests,
    )


async def invoke(probe):
    rig = probe.rig
    with bind_execution_scope(SCOPE):
        return await judge.judge_chapter_commercial_quality(
            rig.session,
            rig.settings,
            chapter_number=2,
            content_md=BODY,
            project_id=rig.project.id,
            chapter_id=rig.chapter.id,
            draft_id=rig.draft.id,
            workflow_run_id=rig.owner.id,
            sample_slot="chapter-quality.independent-v2:0",
            commercial_cohort_samples=3,
        )


async def assert_no_financial_activity(probe):
    rig = probe.rig
    await rig.session.refresh(rig.budget)
    assert (rig.budget.calls_used, rig.budget.tokens_charged) == (0, 0)
    assert await rig.session.scalar(select(func.count()).select_from(ExecutionAttemptModel)) == 0
    assert await rig.session.scalar(select(func.count()).select_from(ExecutionOperationModel)) == 0
    assert rig.calls == []  # No synthetic completion fixture was used either.


@pytest.mark.parametrize(
    "mode",
    [
        "base",
        "strong",
        "project",
        "commercial",
        "strong_commercial",
        "runtime",
        "runtime_commercial",
    ],
)
async def test_fresh_prepared_hash_equals_actual_gateway_claim_after_independent_resolution(
    gateway_probe,
    monkeypatch,
    mode,
):
    probe, rig = gateway_probe, gateway_probe.rig
    if mode in {"strong", "strong_commercial"}:
        rig.settings.llm.critic.model_override = "openai/fixture-strong"
    if mode in {"project", "commercial", "strong_commercial", "runtime_commercial"}:
        rig.project.metadata_json = {"llm_model_id": "project-fixture"}
        await rig.session.commit()
    if mode in {"commercial", "strong_commercial", "runtime_commercial"}:
        monkeypatch.setenv("BESTSELLER__LLM__COMMERCIAL_JUDGE_MODEL_KEY", "commercial-fixture")
    if mode.startswith("runtime"):
        probe.profile.write_text(json.dumps({"active_profile": "minimax"}))
    settings_before = deepcopy(rig.settings.model_dump(mode="json"))
    with pytest.raises(ClaimIntercept):
        await invoke(probe)
    assert len(probe.prepared) == len(probe.hashed) == len(probe.claims) == len(probe.requests) == 1
    prepared, actual, claim = probe.prepared[0], probe.hashed[0], probe.claims[0]
    assert prepared["input_hash"] == actual["hash"] == claim["hash"]
    assert claim["scope"] == SCOPE
    # Compare real independently resolved role objects, not a second literal request.
    assert prepared["effective_critic"] == actual["role"].model_dump(mode="json")
    assert actual["request"].metadata["prepared_effective_input"] == {
        "schema": "effective-input.v1",
        "hash": claim["hash"],
    }
    assert actual["request"].model_tier == "strong"
    assert actual["request"].tool_choice == "required"
    assert actual["payload"]["stream"] is False  # Function tools control transport mode.
    assert actual["payload"]["max_tokens"] == llm._effective_request_max_tokens(
        actual["role"],
        actual["request"],
    )
    assert actual["request"].max_tokens_override > 0
    tool = actual["request"].tools[0]["function"]
    assert tool["name"] == "submit_chapter_review"
    variants = tool["parameters"]["properties"]["issues"]["items"]["anyOf"]
    current_ids = {
        key for key, entry in prepared["sources"].catalog.items() if entry["kind"] == "current"
    }
    assert all(set(variant["properties"]["body_id"]["enum"]) == current_ids for variant in variants)
    if mode == "strong":
        assert actual["role"].model == "openai/fixture-strong"
    elif mode == "project":
        assert actual["role"].model == "openai/fixture-project"
    elif mode in {"commercial", "strong_commercial", "runtime_commercial"}:
        assert actual["role"].model == "openai/fixture-commercial"
        assert actual["role"].api_base == "https://commercial.fixture.invalid/v1"
        assert probe.catalog_reads.count("commercial-fixture") >= 2
    elif mode == "runtime":
        assert actual["role"].model == "openai/MiniMax-M3.1-Flash"
        assert actual["role"].api_base == "https://api.minimaxi.com/v1"
    else:
        assert actual["role"].model == "openai/fixture-base"
    await rig.session.refresh(rig.owner)
    journal = rig.owner.metadata_json["commercial_component_cohorts"][prepared["binding"]["key"]]
    slot = journal["slots"]["chapter-quality.independent-v2:0"]
    assert slot["state"] == "prepared" and slot["used_attempts"] == 1
    assert slot["request"] == probe.requests[0].model_dump(mode="json")
    assert rig.settings.model_dump(mode="json") == settings_before
    await assert_no_financial_activity(probe)


@pytest.mark.parametrize("drift", ["temperature", "endpoint", "max_tokens", "tool_schema"])
async def test_actual_gateway_rejects_post_preparation_drift_before_claim_or_reservation(
    gateway_probe,
    monkeypatch,
    drift,
):
    probe = gateway_probe
    monkeypatch.setenv("BESTSELLER__LLM__COMMERCIAL_JUDGE_MODEL_KEY", "commercial-fixture")

    async def changed_gateway(session, settings, request):
        probe.requests.append(request)
        changed = request
        if drift == "temperature":
            settings = settings.model_copy(deep=True)
            settings.llm.critic.temperature = 0.71
        elif drift == "endpoint":
            probe.entries["commercial-fixture"] = probe.entries["commercial-fixture"].model_copy(
                update={"api_base": "https://changed.fixture.invalid/v1"},
            )
        elif drift == "max_tokens":
            changed = request.model_copy(
                update={"max_tokens_override": request.max_tokens_override - 1}
            )
        else:
            tools = deepcopy(request.tools)
            tools[0]["function"]["parameters"]["description"] = "changed after preparation"
            changed = request.model_copy(update={"tools": tools})
        return await llm.complete_text(session, settings, changed)

    monkeypatch.setattr(judge, "complete_text", changed_gateway)
    with pytest.raises(ExecutionBudgetError, match="execution_prepared_input_changed"):
        await invoke(probe)
    assert len(probe.prepared) == len(probe.hashed) == 1
    assert probe.prepared[0]["input_hash"] != probe.hashed[0]["hash"]
    assert probe.claims == []
    await assert_no_financial_activity(probe)
    rig = probe.rig
    await rig.session.refresh(rig.owner)
    journal = rig.owner.metadata_json["commercial_component_cohorts"][
        probe.prepared[0]["binding"]["key"]
    ]
    assert len(journal["slots"]) == 1
    slot = journal["slots"]["chapter-quality.independent-v2:0"]
    assert slot["state"] == "prepared" and slot["used_attempts"] == 1
    assert slot["request"] == probe.requests[0].model_dump(mode="json")
