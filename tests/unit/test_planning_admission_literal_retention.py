"""Exact critic literal retention; actual codecs, retainer and capacity, no provider."""

from contextlib import nullcontext
from copy import deepcopy
import json
import socket
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from test_planning_admission_ref_protocol import supplied
from test_source_complete_retention import persist_actual

from bestseller.services import llm
from bestseller.services import planning_admission_ref_protocol as refs
from bestseller.services import planning_draft_admission as admission
from bestseller.services import planning_output_budget as budget
from bestseller.services import source_complete_retention as retention
from bestseller.services.finite_book_budget import bind_run
from bestseller.services.llm_capacity import ModelRequestCapacityError
from bestseller.services.lossless_source_encoding import (
    _bytes,
    _container,
    decode_lossless_source,
    encode_lossless_source,
)
from bestseller.settings import LLMRoleSettings, load_settings

pytestmark = pytest.mark.unit
WIRE = "v4-literal-full-source.v1"


@pytest.fixture(autouse=True)
def no_provider_or_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Literal retention tests forbid PG/provider/network")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(llm, "_call_litellm", forbidden)


def case(tmp_path, *, effort=None, archive_chars=130_001):
    settings = load_settings(env={})
    settings.artifact_store.mode = "local"
    settings.artifact_store.retain_prompt_payloads = True
    settings.artifact_store.local_dir = str(tmp_path)
    role = LLMRoleSettings(
        model="openai/MiniMax-M3.1-Flash",
        temperature=0.25,
        max_tokens=32768,
        timeout_seconds=900,
        api_base="https://api.minimaxi.com/v1",
        reasoning_effort=effort,
    )
    base = supplied()
    base["untrusted_commercial_report"]["opaque_original_proof"] = "x" * archive_chars
    logical = admission._input_for_contract(base, refs.SCHEMA)
    tool, _ = refs.reference_tool(admission._Grade.model_json_schema(), logical)
    data = {"graded_input_hash": admission.content_hash(logical), "input": logical}
    packet = _container(deepcopy(data), [], _bytes(data), version=1)
    prefix = "Complete literal review data; no reference expansion.\n"
    raw = retention._json(packet)
    request = llm.LLMCompletionRequest(
        logical_role="critic",
        project_id=UUID(base["project_id"]),
        workflow_run_id=UUID(base["workflow_run_id"]),
        prompt_template=admission.TEMPLATE,
        prompt_version=WIRE,
        system_prompt=refs.SYSTEM,
        user_prompt=prefix + raw,
        fallback_response="{}",
        tools=[tool],
        tool_choice={"type": "function", "function": {"name": "submit_planning_admission"}},
        max_tokens_override=32768,
        metadata={"planning_output_budget": budget.output_budget(role)},
    )
    state = SimpleNamespace(
        settings=settings,
        request=request,
        base_role=role,
        role=llm._apply_conception_phase_settings(request, role),
        source=data,
        packet=packet,
        start=len(prefix),
        end=len(prefix) + len(raw),
    )
    rebind(state)
    return state


def rebind(state):
    state.request.metadata[retention.METADATA_KEY] = retention.source_complete_retention_metadata(
        state.request,
        role=state.base_role,
        logical_source=state.source,
        packet_start=state.start,
        packet_end=state.end,
    )


def check(state, *, role=None):
    return retention.retention_limit(
        state.settings,
        state.request,
        audit=llm._project_prompt_audit.get(),
        role=role or state.role,
    )


@pytest.mark.parametrize("effort", [None, "low", "medium", "high", "max"])
def test_new_wire_uses_same_actual_output_budget_without_changing_grade_or_role(tmp_path, effort):
    state = case(tmp_path, effort=effort)
    req = state.request
    receipt = req.metadata[retention.METADATA_KEY]
    assert budget.LITERAL_FULL_SOURCE_VERSION == WIRE and len(WIRE) <= 32
    assert budget.is_literal_full_source_request(req)
    assert state.role.reasoning_effort == (effort or "low")
    assert state.base_role.reasoning_effort == effort
    assert state.source["input"]["schema"] == refs.SCHEMA
    assert state.source["graded_input_hash"] == admission.content_hash(state.source["input"])
    for version in (admission.SCHEMA, refs.SCHEMA):
        legacy = req.model_copy(update={"prompt_version": version})
        assert budget.apply_output_budget(legacy, state.base_role) == state.role
        assert llm._effective_input_payload(legacy, state.role) == llm._effective_input_payload(
            req, state.role
        )
    assert receipt["effective_input_sha256"] == llm._effective_input_hash(req, state.role)
    assert receipt["grade_asserted"] is False and receipt["author_authority_granted"] is False


def test_complete_literal_packet_and_native_schema_retain_every_unknown_and_old_proof(tmp_path):
    state = case(tmp_path)
    before = state.request.model_dump(mode="json")
    assert len(state.request.user_prompt) > 120_000
    assert decode_lossless_source(state.packet) == state.source
    with llm.bind_project_prompt_audit(state.request.project_id, reason="literal full source"):
        assert check(state, role=state.base_role) == check(state) == 1_000_000
    assert state.request.model_dump(mode="json") == before
    unknown = state.source["input"]["candidate_payload"]["chapters"][0]["scenes"][0][
        "unknown_authored"
    ]
    assert unknown["flag"] is False and unknown["count"] == 0 and unknown["unset"] is None
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("with_budget", [False, True])
def test_exact_new_wire_cannot_downgrade_to_legacy_on_missing_optin(tmp_path, with_budget):
    state = case(tmp_path)
    state.request.metadata = (
        {"planning_output_budget": budget.output_budget(state.base_role)} if with_budget else {}
    )
    with pytest.raises(ValueError, match="source_complete_optin_missing"):
        check(state)


@pytest.mark.parametrize(
    "field,value",
    [
        ("prompt_version", refs.SCHEMA),
        ("logical_role", "planner"),
        ("prompt_template", "chapter_review"),
        ("prompt_version", "unrelated-wire.v1"),
    ],
)
def test_nearby_routes_do_not_gain_literal_admission_optin(tmp_path, field, value):
    state = case(tmp_path)
    setattr(state.request, field, value)
    assert not budget.is_literal_full_source_request(state.request)
    state.request.metadata.clear()
    assert check(state) == 120_000
    with pytest.raises(ValueError, match="request_contract"):
        rebind(state)


@pytest.mark.parametrize(
    "field",
    [
        "schema",
        "model",
        "answer_tokens",
        "role_output_tokens",
        "total_output_tokens",
        "reasoning_effort",
    ],
)
def test_changed_actual_output_receipt_is_rejected(tmp_path, field):
    state = case(tmp_path)
    state.request.metadata["planning_output_budget"][field] = True
    with llm.bind_project_prompt_audit(state.request.project_id, reason="output receipt fence"):
        with pytest.raises(ValueError, match="output_budget_changed"):
            check(state)


@pytest.mark.parametrize("fault", ["missing_budget", "override", "bool_override", "non_m31"])
def test_new_literal_route_needs_exact_m31_budget_and_output_cap(tmp_path, fault):
    state = case(tmp_path)
    if fault == "missing_budget":
        state.request.metadata.pop("planning_output_budget")
    elif fault == "override":
        state.request.max_tokens_override = 6000
    elif fault == "bool_override":
        state.request.max_tokens_override = True
    else:
        state.base_role = state.base_role.model_copy(update={"model": "openai/unrelated"})
    with pytest.raises(ValueError, match="output_budget_"):
        rebind(state)


@pytest.mark.parametrize("version", [1, 2, 3, 4])
def test_valid_compressed_or_different_codec_is_not_the_literal_route(tmp_path, version):
    state = case(tmp_path)
    if version == 1:
        data = {**state.source, "repeated": ["repeat exact original " * 500] * 2}
        packet = encode_lossless_source(data, version=1)
        assert packet["references"]
    else:
        data = state.source
        packet = (
            _container(data, [], _bytes(data), version=2)
            if version == 2
            else encode_lossless_source(data, version=version)
        )
    assert decode_lossless_source(packet) == data
    state.source = data
    state.request.user_prompt = retention._json(packet)
    state.start, state.end = 0, len(state.request.user_prompt)
    with pytest.raises(ValueError, match="literal_source_required"):
        rebind(state)


@pytest.mark.parametrize("fault", ["tools", "finite", "unknown_route"])
def test_full_tools_model_route_and_finite_capacity_remain_independent(tmp_path, fault):
    state = case(tmp_path)
    if fault == "tools":
        state.request.tools[0]["function"]["description"] = "t" * 900_000
        rebind(state)
    elif fault == "unknown_route":
        state.base_role = state.base_role.model_copy(
            update={"api_base": "https://unknown.invalid/v1"}
        )
        state.role = budget.apply_output_budget(state.request, state.base_role)
        rebind(state)
    finite = (
        bind_run(
            "fixture",
            project_id=state.request.project_id,
            stage="source-review",
            capacity_limit=65536,
        )
        if fault == "finite"
        else nullcontext()
    )
    with (
        finite,
        llm.bind_project_prompt_audit(state.request.project_id, reason="whole request capacity"),
    ):
        with pytest.raises((retention.SourceCompleteRetentionError, ModelRequestCapacityError)):
            check(state)


def test_actual_retainer_keeps_full_native_request_and_raw_history(tmp_path):
    state = case(tmp_path)
    raw = {
        "role": "assistant",
        "content": "",
        "tool_calls": [],
        "reasoning_content": "opaque debug only " * 8000,
        "unknown": {"approved": False, "value": None},
    }
    before = deepcopy((state.request.model_dump(mode="json"), raw))
    with llm.bind_project_prompt_audit(state.request.project_id, reason="actual literal retainer"):
        paths = persist_actual(
            state, content="", raw_message=raw, tool_calls=[], finish_reason="stop"
        )
    assert paths[2:] == (None, True)
    request, response = [json.loads((tmp_path / p).read_bytes()) for p in paths[:2]]
    assert request["replayable"] and not request["messages"]["user"]["truncated"]
    assert request["messages"]["user"]["text"] == state.request.user_prompt
    assert request["requested"]["tools"] == state.request.tools
    assert response["raw_message"] == raw
    assert before == (state.request.model_dump(mode="json"), raw)


def test_actual_retainer_cannot_silently_bound_new_route_when_optin_disappears(tmp_path):
    state = case(tmp_path)
    state.request.metadata.pop(retention.METADATA_KEY)
    with llm.bind_project_prompt_audit(
        state.request.project_id, reason="mandatory literal retention"
    ):
        with pytest.raises(ValueError, match="source_complete_optin_missing"):
            persist_actual(state)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize(
    "fault,code",
    [
        ("retention_disabled", "retention_disabled"),
        ("store_mode_changed", "local_store_required"),
        ("audit_missing", "project_audit_required"),
    ],
)
def test_prepared_literal_retainer_rechecks_requirements_before_any_write(tmp_path, fault, code):
    state = case(tmp_path)
    before = state.request.model_dump(mode="json")
    with llm.bind_project_prompt_audit(state.request.project_id, reason="valid preparation"):
        assert check(state) == 1_000_000
    if fault == "retention_disabled":
        state.settings.artifact_store.retain_prompt_payloads = False
    elif fault == "store_mode_changed":
        state.settings.artifact_store.mode = "s3"
    audit = (
        nullcontext()
        if fault == "audit_missing"
        else llm.bind_project_prompt_audit(state.request.project_id, reason="physical persistence")
    )
    with audit, pytest.raises(retention.SourceCompleteRetentionError, match=code):
        persist_actual(state)
    assert state.request.model_dump(mode="json") == before
    assert not list(tmp_path.iterdir())


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["missing_optin", "changed_wire", "changed_model"])
async def test_actual_physical_attempt_blocks_before_reservation_or_provider(
    tmp_path, monkeypatch, fault
):
    state = case(tmp_path)
    role = state.role
    if fault == "missing_optin":
        state.request.metadata.pop(retention.METADATA_KEY)
    elif fault == "changed_wire":
        state.request.user_prompt += " changed after preparation"
    else:
        role = role.model_copy(update={"model": "openai/unrelated"})
    ledger = SimpleNamespace(
        scope_id="literal-fixture",
        reserve=AsyncMock(side_effect=AssertionError("reservation must not be reached")),
        assert_result_adoptable=AsyncMock(),
    )
    call = llm._ExecutionCall(ledger, object(), settings=state.settings)
    gateway = AsyncMock(side_effect=AssertionError("provider forbidden"))
    monkeypatch.setattr(llm, "_call_litellm", gateway)
    token = llm._execution_call_var.set(call)
    try:
        with llm.bind_project_prompt_audit(
            state.request.project_id, reason="actual physical fence"
        ):
            with pytest.raises(ValueError):
                await llm._paid_transport_attempt_impl(state.request, role)
        ledger.reserve.assert_not_awaited()
        gateway.assert_not_awaited()
        assert call.attempts == []
    finally:
        llm._execution_call_var.reset(token)
