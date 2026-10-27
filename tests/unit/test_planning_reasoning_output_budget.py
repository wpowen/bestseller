"""Output accounting only; synthetic sources and transport, no paid model."""
from copy import deepcopy
from uuid import uuid4

import pytest
from sqlalchemy import select

from bestseller.infra.db.models import ExecutionBudgetModel, LlmRunModel
from bestseller.services import llm, planning_draft_admission as admission
from bestseller.services.planning_output_budget import output_budget, apply_output_budget
from bestseller.settings import LLMRoleSettings
from test_planning_draft_admission import harness as harness  # noqa: F401
from test_planning_draft_admission import _assess_protocol

pytestmark = pytest.mark.unit


def role(**updates):
    return LLMRoleSettings(model="openai/MiniMax-M3.1-Flash", max_tokens=32768,
        temperature=0.25, timeout_seconds=180).model_copy(update=updates)


def request(receipt):
    return llm.LLMCompletionRequest(logical_role="critic", project_id=uuid4(), workflow_run_id=uuid4(),
        prompt_template=admission.TEMPLATE, prompt_version=admission.SCHEMA,
        system_prompt="Only inspect complete sources", user_prompt="Synthetic fixture, never sent",
        fallback_response="{}", max_tokens_override=receipt["total_output_tokens"],
        metadata={"planning_output_budget": deepcopy(receipt)})


@pytest.mark.parametrize("effort", [None, "low", "medium", "high", "xhigh", "max"])
def test_explicit_effort_and_old_narrowed_default_survive_larger_allowance(effort):
    original = role(reasoning_effort=effort)
    before = original.model_dump()
    receipt = output_budget(original)
    req = request(receipt)
    actual = llm._apply_conception_phase_settings(req, original)
    wire = llm._effective_input_payload(req, actual)
    assert wire["max_tokens"] == 32768
    assert wire["reasoning_effort"] == (effort or "low")
    assert wire["model"] == original.model and wire["tools"] is None
    assert original.model_dump() == before
    assert actual == apply_output_budget(req, original)


@pytest.mark.parametrize("cap", [4096, 6000, 16384, 32768, 1000000])
def test_role_allowance_with_model_ceiling_keeps_answer_reserve(cap):
    from bestseller.services.word_targets import model_output_token_ceiling
    original = role(max_tokens=cap)
    receipt = output_budget(original)
    assert receipt["total_output_tokens"] == min(max(6000, cap), model_output_token_ceiling(original.model))
    assert receipt["reasoning_effort"] == ("low" if 6000 < cap else "max")


@pytest.mark.parametrize("field", ["model", "schema", "total_output_tokens", "reasoning_effort", "role_output_tokens"])
def test_mutated_receipt_rejected_before_any_dispatch(field):
    original = role()
    req = request(output_budget(original))
    req.metadata["planning_output_budget"][field] = "changed"
    with pytest.raises(ValueError, match="request_binding_changed"):
        llm._apply_conception_phase_settings(req, original)


@pytest.mark.parametrize("field,value", [("prompt_template", "chapter_review"),
    ("prompt_version", "planning-volume-admission.v1"), ("logical_role", "planner"),
    ("max_tokens_override", 6000)])
def test_budget_cannot_leak_to_other_routes_or_override_request(field, value):
    original = role()
    req = request(output_budget(original)).model_copy(update={field: value})
    with pytest.raises(ValueError, match="request_binding_changed"):
        llm._apply_conception_phase_settings(req, original)


def test_legacy_paid_requests_and_other_models_keep_exact_hashes():
    original = role()
    req = request(output_budget(original))
    req.metadata = {}
    req.max_tokens_override = 6000
    before = llm._effective_input_hash(req, original)
    assert llm._apply_conception_phase_settings(req, original) is original
    assert llm._effective_input_hash(req, original) == before
    assert output_budget(role(model="openai/MiniMax-M3")) is None


@pytest.mark.asyncio
async def test_actual_admission_gateway_capacity_hash_and_replay_use_same_budget(harness, monkeypatch):
    h = harness
    h.settings.llm.critic = h.settings.llm.critic.model_copy(update={
        "model": "openai/MiniMax-M3.1-Flash", "max_tokens": 32768, "reasoning_effort": None})
    observed = []
    async def transport(req, actual_role):
        observed.append((req, actual_role))
        assert actual_role.reasoning_effort == "low"
        assert llm._effective_input_payload(req, actual_role)["max_tokens"] == 32768
        calls = [{"id": "fixture", "type": "function", "function": {
            "name": "submit_planning_admission", "arguments": h.raw}}]
        return "", 10, 5, "tool_calls", calls, {
            "role": "assistant", "content": None, "tool_calls": calls, "reasoning_content": "fixture"}
    monkeypatch.setattr(llm, "_call_litellm", transport)
    state = {}
    result = await _assess_protocol(h, state)
    assert result["draft_eligible"] is True
    req, actual_role = observed[0]
    assert req.metadata["planning_admission_input_receipt"]["effective_max_output_tokens"] == 32768
    assert req.metadata["source_transport"]["request_capacity"]["effective_max_output_tokens"] == 32768
    assert llm._effective_input_hash(req, actual_role) == result["effective_input_hash"]
    assert await _assess_protocol(h, state) == result and len(observed) == 1
    async with h.session_factory() as session:
        budget = await session.get(ExecutionBudgetModel, "audit:admission-fixture")
        assert budget.calls_used == 1 and budget.tokens_charged == 15
        run = await session.scalar(select(LlmRunModel))
        assert run.metadata_json["planning_output_budget"] == req.metadata["planning_output_budget"]


@pytest.mark.asyncio
async def test_original_protocol_identity_is_checkpointed_before_any_provider_send(harness):
    h = harness
    state, snapshots = {}, []
    async def stop_at_checkpoint():
        snapshots.append(deepcopy(state))
        raise RuntimeError("offline checkpoint interruption")
    with pytest.raises(RuntimeError, match="checkpoint interruption"):
        await _assess_protocol(h, state, stop_at_checkpoint)
    assert h.sent == []
    assert snapshots[0]["attempts"] == [] and snapshots[0]["status"] == "pending"
    assert snapshots[0]["graded_input_hash"] and snapshots[0]["hard_admission_policy"] == admission.HARD_ADMISSION_POLICY
    result = await _assess_protocol(h, state)
    assert result["draft_eligible"] and len(h.sent) == 1
