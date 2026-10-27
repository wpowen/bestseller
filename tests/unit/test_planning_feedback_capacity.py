"""Staged feedback precheck only; real calibrated admission, no claim/provider."""

# ruff: noqa: ANN001, ANN002, ANN003, ANN201, ANN202 -- Offline heterogeneous fixture adapters.

from contextlib import nullcontext
from copy import deepcopy
import json
import math
import socket
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from bestseller.services import finite_book_budget as finite
from bestseller.services import llm
from bestseller.services import model_context_capacity as context
from bestseller.services import planning_surgical_budget as budget
from bestseller.services import planning_surgical_repair as producer
from bestseller.services import planning_surgical_transport as frozen
from bestseller.services import source_complete_retention as retention
from bestseller.services.llm_capacity import INPUT_SAFETY_FACTOR, ModelRequestCapacityError
from bestseller.services.source_bound_planning_content import SourceBoundPlanningCapacityError
from bestseller.settings import LLMRoleSettings, load_settings

pytestmark = pytest.mark.unit

staged = frozen


def canonical(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


@pytest.fixture(autouse=True)
def offline(monkeypatch, tmp_path):
    def forbidden(*args, **kwargs):
        raise AssertionError("Staged tests forbid network, real provider and PG")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(llm, "_call_litellm", forbidden)
    monkeypatch.setenv("BESTSELLER_LLM_RUNTIME_PROFILE_PATH", str(tmp_path / "no-profile.json"))


def arguments(*, feedback=False):
    return {
        "system_prompt": "Surgical repair preserves all original authority boundaries.",
        "project_brief": {"unknown": None, "explicit_author_controls": ["No new powers."]},
        "directives": {
            "repair_directives": ["Correct only the proved field."],
            "protocol_feedback": (
                [
                    {
                        "attempt": 1,
                        "llm_run_id": str(uuid4()),
                        "error": "Missing required scene_number.",
                        "rejected_response": "FULL_NATIVE_RESPONSE " + "z" * 24_000,
                        "authority": "untrusted_protocol_failure_not_current_candidate",
                    }
                ]
                if feedback
                else []
            ),
        },
        "candidate_payload": {"chapters": [{"chapter_number": 1, "scenes": [{"scene_number": 1}]}]},
        "patch_protocol": {
            "required": ["chapter_number", "scope", "field", "value"],
            "scene_number": "required for scene scope",
        },
        "source_context": {
            "complete_raw": "x" * 222_000,
            "legacy": {
                "pass": False,
                "source_run_id": None,
                "literal": {"$ref": "/not-resolved"},
                "types": [1, 1.0, True],
            },
        },
    }


def role(**overrides):
    return LLMRoleSettings(
        **{
            "model": "openai/MiniMax-M3.1-Flash",
            "api_base": "https://api.minimaxi.com/v1",
            "max_tokens": 32768,
            "reasoning_effort": "medium",
            "stream": True,
            "temperature": 0.82,
            "timeout_seconds": 900,
            **overrides,
        }
    )


def documented_feedback_bound(actual_role):
    # Proposed caller derivation, deliberately not part of the pure codec.
    ceiling = context.documented_capacity(actual_role.model, endpoint=actual_role.api_base)
    return math.floor((ceiling - actual_role.max_tokens) / INPUT_SAFETY_FACTOR)


def prospective_build(values, actual_role):
    options = {"max_chars": 1_000_000}
    if values["directives"]["protocol_feedback"]:
        options["max_estimated_tokens"] = documented_feedback_bound(actual_role)
    return staged.build_source_complete_surgical_prompts(**values, **options)


def request_from(result, actual_role):
    system, user, receipt = result
    request = llm.LLMCompletionRequest(
        logical_role="planner",
        project_id=uuid4(),
        workflow_run_id=uuid4(),
        system_prompt=system,
        user_prompt=user,
        fallback_response='{"patches":[]}',
        prompt_template="planner_volume_1_chapter_outline_commercial_surgical_repair",
        prompt_version=budget.PROMPT_VERSION,
        max_tokens_override=actual_role.max_tokens,
        metadata={
            "planning_surgical_source_transport": deepcopy(receipt),
            "planning_surgical_output_budget": budget.output_budget(
                actual_role, answer_tokens=12000
            ),
        },
    )
    original = staged.decode_source_complete_surgical_prompts(
        system_prompt=system,
        user_prompt=user,
        transport_receipt=receipt,
        max_chars=1_000_000,
        max_estimated_tokens=receipt["prompt_capacity"]["max_estimated_tokens"],
    )
    # This test fixture constructs custody after explicitly choosing a trusted
    # caller bound; production readers must derive theirs independently.
    wire = json.loads(user[len(staged.MARKER) :])
    packet = canonical(wire["payload"])
    start = user.index(packet)
    request.metadata[retention.METADATA_KEY] = retention.source_complete_retention_metadata(
        request,
        role=actual_role,
        logical_source=original,
        packet_start=start,
        packet_end=start + len(packet),
    )
    return request


def settings(tmp_path, actual_role):
    configured = load_settings(env={})
    configured.llm.mock = False
    configured.llm.planner = actual_role
    configured.artifact_store.mode = "local"
    configured.artifact_store.retain_prompt_payloads = True
    configured.artifact_store.local_dir = str(tmp_path)
    return configured


@pytest.mark.parametrize("max_chars", [120_000, 1_000_000])
def test_default_small_codec_tuple_receipt_shape_and_wire_are_frozen_exact(max_chars):
    values = arguments()
    values["source_context"]["complete_raw"] = "short exact body"
    old = frozen.build_source_complete_surgical_prompts(**values, max_chars=max_chars)
    new = staged.build_source_complete_surgical_prompts(**values, max_chars=max_chars)
    assert canonical(new) == canonical(old)
    assert canonical(new) == canonical(
        staged.build_source_complete_surgical_prompts(
            **values,
            max_chars=max_chars,
            max_estimated_tokens=60_000,
        )
    )
    assert set(new[2]) == set(old[2])  # No new default metadata key.
    assert staged.PROTOCOL == frozen.PROTOCOL and staged.SCHEMA == frozen.SCHEMA


def test_actual_initial_builder_metadata_request_and_effective_input_remain_exact(
    tmp_path, monkeypatch
):
    actual_role = role()
    configured = settings(tmp_path, actual_role)
    project = SimpleNamespace(id=uuid4(), slug="unrelated-observatory", metadata_json={})
    values = arguments()
    source = deepcopy(values["source_context"])
    source["project_source_brief"] = values["project_brief"]
    kwargs = {
        "project": project,
        "workflow_run_id": uuid4(),
        "logical_name": "volume_1_chapter_outline",
        "outline_payload": values["candidate_payload"],
        "directives": values["directives"]["repair_directives"],
        "source_context": source,
    }
    before = producer.build_request(configured, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(
            producer,
            "build_source_complete_surgical_prompts",
            staged.build_source_complete_surgical_prompts,
        )
        patch.setattr(
            producer,
            "decode_source_complete_surgical_prompts",
            staged.decode_source_complete_surgical_prompts,
        )
        after = producer.build_request(configured, **kwargs)
    assert canonical(before.model_dump(mode="json")) == canonical(after.model_dump(mode="json"))
    before_role = budget.resolve_request_role(configured, project, before)
    after_role = budget.resolve_request_role(configured, project, after)
    assert llm._effective_input_hash(before, before_role) == llm._effective_input_hash(
        after, after_role
    )
    assert (
        before.metadata["planning_surgical_source_transport"]["prompt_capacity"][
            "max_estimated_tokens"
        ]
        == 60_000
    )


def test_initial_keeps_default_and_only_full_feedback_needs_explicit_bound(tmp_path):
    actual_role = role()
    initial = arguments()
    first = prospective_build(initial, actual_role)
    assert canonical(first) == canonical(
        frozen.build_source_complete_surgical_prompts(
            **initial,
            max_chars=1_000_000,
        )
    )
    feedback = deepcopy(initial)
    feedback["directives"] = arguments(feedback=True)["directives"]
    before = canonical(feedback)
    with pytest.raises(SourceBoundPlanningCapacityError):
        staged.build_source_complete_surgical_prompts(**feedback, max_chars=1_000_000)
    result = prospective_build(feedback, actual_role)
    limit = documented_feedback_bound(actual_role)
    assert limit == 439_650
    assert result[2]["prompt_capacity"]["estimated_tokens"] > 60_000
    assert result[2]["prompt_capacity"]["max_estimated_tokens"] == limit
    restored = staged.decode_source_complete_surgical_prompts(
        system_prompt=result[0],
        user_prompt=result[1],
        transport_receipt=result[2],
        max_chars=1_000_000,
        max_estimated_tokens=limit,
    )
    assert canonical(restored) == canonical(
        {k: v for k, v in feedback.items() if k != "system_prompt"}
    )
    assert canonical(feedback) == before
    request = request_from(result, actual_role)
    configured = settings(tmp_path, actual_role)
    with llm.bind_project_prompt_audit(request.project_id, reason="offline whole feedback"):
        assert (
            retention.retention_limit(
                configured, request, audit=llm._project_prompt_audit.get(), role=actual_role
            )
            == 1_000_000
        )
    payload = llm._effective_input_payload(request, actual_role)
    assert payload["max_tokens"] == 32768 and payload["reasoning_effort"] == "medium"


@pytest.mark.parametrize("invalid", [None, True, False, 0, -1, 1_000_001, 60_000.0, "1000000"])
def test_both_apis_reject_nonpositive_noninteger_or_overmaximum_estimate_limits(invalid):
    values = arguments()
    values["source_context"]["complete_raw"] = "short"
    original = staged.build_source_complete_surgical_prompts(**values)
    with pytest.raises(ValueError, match="max_estimated_tokens_invalid"):
        staged.build_source_complete_surgical_prompts(**values, max_estimated_tokens=invalid)
    with pytest.raises(ValueError, match="max_estimated_tokens_invalid"):
        staged.decode_source_complete_surgical_prompts(
            system_prompt=original[0],
            user_prompt=original[1],
            max_estimated_tokens=invalid,
        )


def test_exact_estimate_boundary_and_one_lower_fail_without_wire_or_source_change():
    values = arguments()
    values["source_context"]["complete_raw"] = "short"
    default = staged.build_source_complete_surgical_prompts(**values)
    exact = default[2]["prompt_capacity"]["estimated_tokens"]
    result = staged.build_source_complete_surgical_prompts(**values, max_estimated_tokens=exact)
    assert result[:2] == default[:2]
    with pytest.raises(SourceBoundPlanningCapacityError):
        staged.build_source_complete_surgical_prompts(**values, max_estimated_tokens=exact - 1)
    with pytest.raises(SourceBoundPlanningCapacityError):
        staged.decode_source_complete_surgical_prompts(
            system_prompt=result[0],
            user_prompt=result[1],
            max_estimated_tokens=exact - 1,
        )


@pytest.mark.parametrize("receipt_supplied", [False, True])
def test_reader_never_self_authorizes_larger_limit_from_feedback_or_receipt(receipt_supplied):
    result = prospective_build(arguments(feedback=True), role())
    with pytest.raises(SourceBoundPlanningCapacityError):
        staged.decode_source_complete_surgical_prompts(
            system_prompt=result[0],
            user_prompt=result[1],
            transport_receipt=result[2] if receipt_supplied else None,
            max_chars=1_000_000,
        )


@pytest.mark.parametrize("tampered", [True, 60_000, 1_000_001, "439650"])
def test_effective_limit_is_receipt_bound_and_tampering_fails(tampered):
    actual_role = role()
    result = prospective_build(arguments(feedback=True), actual_role)
    bad = deepcopy(result[2])
    bad["prompt_capacity"]["max_estimated_tokens"] = tampered
    with pytest.raises(ValueError, match="receipt_mismatch"):
        staged.decode_source_complete_surgical_prompts(
            system_prompt=result[0],
            user_prompt=result[1],
            transport_receipt=bad,
            max_chars=1_000_000,
            max_estimated_tokens=documented_feedback_bound(actual_role),
        )


@pytest.mark.parametrize(
    "overrides",
    [
        {"model": "openai/unknown-model"},
        {"api_base": "https://unverified.invalid/v1"},
    ],
)
def test_unknown_model_or_route_keeps_65536_context_and_rejects_full_feedback(overrides):
    actual_role = role(**overrides)
    assert context.documented_capacity(actual_role.model, endpoint=actual_role.api_base) == 65536
    limit = documented_feedback_bound(actual_role)
    assert limit == 14_894
    with pytest.raises(SourceBoundPlanningCapacityError):
        prospective_build(arguments(feedback=True), actual_role)


def test_one_million_character_cap_cannot_be_widened_by_estimated_token_override():
    values = arguments(feedback=True)
    values["source_context"]["complete_raw"] = "x" * 1_000_001
    with pytest.raises(SourceBoundPlanningCapacityError) as error:
        staged.build_source_complete_surgical_prompts(
            **values,
            max_chars=1_000_000,
            max_estimated_tokens=1_000_000,
        )
    assert error.value.receipt["combined_chars"] > 1_000_000


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["unknown_route", "finite"])
async def test_actual_public_capacity_guard_rejects_before_any_claim_or_provider(
    failure,
    tmp_path,
    monkeypatch,
):
    actual_role = role(
        **({"api_base": "https://unverified.invalid/v1"} if failure == "unknown_route" else {})
    )
    # Codec permission alone deliberately does not constitute model admission.
    request = request_from(
        staged.build_source_complete_surgical_prompts(
            **arguments(feedback=True),
            max_chars=1_000_000,
            max_estimated_tokens=439_650,
        ),
        actual_role,
    )
    configured = settings(tmp_path, actual_role)
    monkeypatch.setattr(llm, "_resolve_project_model_override", AsyncMock(return_value=None))
    monkeypatch.setattr(
        llm, "_preserve_legacy_project_default", AsyncMock(return_value=actual_role)
    )
    claim = AsyncMock(side_effect=AssertionError("financial claim must not happen"))
    gateway = AsyncMock(side_effect=AssertionError("provider must not happen"))
    monkeypatch.setattr(llm.ExecutionLedger, "claim", claim)
    monkeypatch.setattr(llm, "_call_litellm", gateway)
    bound = (
        finite.bind_run(
            uuid4(), project_id=request.project_id, stage="offline-check", capacity_limit=65536
        )
        if failure == "finite"
        else nullcontext()
    )
    with (
        bound,
        llm.bind_project_prompt_audit(request.project_id, reason="offline preclaim capacity"),
    ):
        with pytest.raises(ModelRequestCapacityError):
            await llm.complete_text(object(), configured, request)
    claim.assert_not_awaited()
    gateway.assert_not_awaited()


@pytest.mark.asyncio
async def test_actual_physical_attempt_rechecks_capacity_before_reserve_or_send(
    tmp_path, monkeypatch
):
    actual_role = role()
    request = request_from(prospective_build(arguments(feedback=True), actual_role), actual_role)
    configured = settings(tmp_path, actual_role)
    ledger = SimpleNamespace(
        scope_id="offline-original-scope",
        reserve=AsyncMock(),
        assert_result_adoptable=AsyncMock(),
    )
    call = llm._ExecutionCall(ledger, object(), settings=configured)
    gateway = AsyncMock(side_effect=AssertionError("provider must not happen"))
    monkeypatch.setattr(llm, "_call_litellm", gateway)
    token = llm._execution_call_var.set(call)
    try:
        with llm.bind_project_prompt_audit(request.project_id, reason="offline each-send capacity"):
            assert (
                retention.retention_limit(
                    configured,
                    request,
                    audit=llm._project_prompt_audit.get(),
                    role=actual_role,
                )
                == 1_000_000
            )
            # A narrower existing finite context is rechecked on the physical
            # attempt; prior codec/retention success cannot bypass it.
            with (
                finite.bind_run(
                    uuid4(),
                    project_id=request.project_id,
                    stage="offline-check",
                    capacity_limit=65536,
                ),
                pytest.raises(ModelRequestCapacityError),
            ):
                await llm._paid_transport_attempt_impl(request, actual_role)
        ledger.reserve.assert_not_awaited()
        gateway.assert_not_awaited()
        assert call.attempts == []
    finally:
        llm._execution_call_var.reset(token)
