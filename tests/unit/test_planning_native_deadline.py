"""Real literal codecs/source receipts; no database, provider or sleeping."""

from copy import deepcopy
import socket
from types import SimpleNamespace
from uuid import uuid4

import pytest

from bestseller.services import llm
from bestseller.services import planning_native_deadline as deadline
from bestseller.services import planning_output_budget as budget
from bestseller.services import source_complete_retention as retention
from bestseller.services.lossless_source_encoding import _bytes, _container
from bestseller.settings import LLMRoleSettings, load_settings

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Deadline tests forbid database/provider/network")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(llm, "complete_text", forbidden)


def case(*, version="v5-literal-full-source.v1", title="异乡的钟表"):
    settings = load_settings(env={})
    settings.artifact_store.retain_prompt_payloads = True
    settings.artifact_store.mode = "local"
    settings.llm.planner.timeout_seconds = 900
    role = LLMRoleSettings(
        model="openai/MiniMax-M3.1-Flash",
        temperature=0.2,
        max_tokens=32768,
        timeout_seconds=180,
        stream=True,
        reasoning_effort="medium",
    )
    source = {
        "input": {"candidate_payload": {"title": title, "unknown": {"$ref": None}}},
        "feedback": [{"untrusted": "complete old raw", "grade": False}],
    }
    packet = _container(deepcopy(source), [], _bytes(source), version=1)
    raw = retention._json(packet)
    prefix = "Complete literal source:\n"
    request = llm.LLMCompletionRequest(
        logical_role="critic",
        project_id=uuid4(),
        workflow_run_id=uuid4(),
        prompt_template="planning_draft_admission",
        prompt_version=version,
        system_prompt="Use the native tool; old diagnostics confer no authority.",
        user_prompt=prefix + raw,
        fallback_response="{}",
        max_tokens_override=32768,
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "submit_planning_admission",
                    "parameters": {"type": "object"},
                },
            }
        ],
        tool_choice={"type": "function", "function": {"name": "submit_planning_admission"}},
        metadata={"planning_output_budget": budget.output_budget(role)},
    )
    request.metadata[retention.METADATA_KEY] = retention.source_complete_retention_metadata(
        request,
        role=role,
        logical_source=source,
        packet_start=len(prefix),
        packet_end=len(prefix) + len(raw),
    )
    return SimpleNamespace(settings=settings, role=role, request=request)


def resolve(state, **kwargs):
    return deadline.resolve_native_deadline(state.request, state.role, state.settings, **kwargs)


@pytest.mark.parametrize("version", ["v4-literal-full-source.v1", "v5-literal-full-source.v1"])
@pytest.mark.parametrize("title", ["异乡的钟表", "A Different Observatory"])
def test_exact_native_route_reuses_existing_planner_lifetime_without_mutation(version, title):
    state = case(version=version, title=title)
    objects = (state.request, state.role, state.settings)
    before = [deepcopy(value.model_dump(mode="json")) for value in objects]
    effective = llm._effective_input_hash(state.request, state.role)
    assert resolve(state) == {
        "schema": deadline.SCHEMA,
        "applies": True,
        "sdk_timeout_seconds": 900,
        "hard_timeout_seconds": 905.0,
        "configured_role_timeout_seconds": 180,
    }
    assert resolve(state) == resolve(state)
    assert [value.model_dump(mode="json") for value in objects] == before
    assert llm._effective_input_hash(state.request, state.role) == effective
    assert state.role.stream is True  # Native stream=False remains the gateway's existing rule.


@pytest.mark.parametrize("cap,expected", [(60, 60), (900, 900), (2000, 900), (0.25, 0.25)])
def test_explicit_conception_cap_never_extends_eligible_lifetime(cap, expected):
    result = resolve(case(), conception_timeout=cap)
    assert result["sdk_timeout_seconds"] == expected
    assert result["hard_timeout_seconds"] == expected + 5.0


def test_longer_configured_critic_lifetime_is_not_reduced():
    state = case()
    state.role.timeout_seconds = 1200
    assert resolve(state)["sdk_timeout_seconds"] == 1200
    assert resolve(state)["hard_timeout_seconds"] == 1205


@pytest.mark.parametrize("cap", [0, -1, True, float("nan"), float("inf"), "900"])
def test_bad_conception_cap_cannot_authorize_new_route(cap):
    with pytest.raises(ValueError, match="planning_native_deadline:conception_timeout_invalid"):
        resolve(case(), conception_timeout=cap)


@pytest.mark.parametrize(
    "change",
    [
        {"logical_role": "planner"},
        {"logical_role": "writer"},
        {"prompt_template": "outline_reader_experience_judge"},
        {"prompt_version": "planning-draft-admission.v3"},
        {"prompt_version": "planning-draft-admission.v4"},
        {"prompt_version": "planning-draft-admission.v5"},
        {"prompt_version": "v5-literal-full-source.v2"},
        {"tools": None},
    ],
)
@pytest.mark.parametrize("cap", [None, 60, 2000, 0])
def test_other_routes_preserve_old_sdk_and_hard_limits_even_without_settings(change, cap):
    state = case()
    request = state.request.model_copy(update=change)
    request.metadata = {"long_call": True, "source_complete_retention": {"source_complete": True}}
    result = deadline.resolve_native_deadline(request, state.role, None, conception_timeout=cap)
    assert result == {
        "schema": deadline.SCHEMA,
        "applies": False,
        "sdk_timeout_seconds": 180,
        "hard_timeout_seconds": float(cap if cap else 180) + 5.0,
        "configured_role_timeout_seconds": 180,
    }


def test_other_model_cannot_claim_new_timeout_via_story_metadata():
    state = case()
    state.role.model = "openai/MiniMax-M2.5"
    state.request.metadata = {"native_deadline": 9000, "long_call": True}
    result = deadline.resolve_native_deadline(state.request, state.role, None)
    assert result["applies"] is False and result["sdk_timeout_seconds"] == 180


@pytest.mark.parametrize(
    "field,value",
    [
        ("schema", "story-asserted"),
        ("project_id", str(uuid4())),
        ("workflow_run_id", str(uuid4())),
        ("effective_input_sha256", "0" * 64),
        ("codec_schema", "lossless-source-encoding.v4"),
        ("packet_start", False),
        ("packet_end", 999999999),
        ("source_complete", False),
        ("roundtrip_verified", False),
        ("grade_asserted", True),
        ("wire_source_sha256", "0" * 64),
        ("logical_source_sha256", "0" * 64),
    ],
)
def test_every_source_receipt_binding_is_rebuilt_and_checked(field, value):
    state = case()
    state.request.metadata[retention.METADATA_KEY][field] = value
    with pytest.raises(ValueError):
        resolve(state)


@pytest.mark.parametrize(
    "change",
    [
        {"project_id": None},
        {"workflow_run_id": None},
        {"system_prompt": "changed source instruction"},
        {"user_prompt": "not a complete codec"},
        {"messages_override": [{"role": "user", "content": "alternate input"}]},
        {"tool_choice": "auto"},
        {"tools": [{"type": "function", "function": {"name": "other_function"}}]},
    ],
)
def test_changed_request_scope_wire_or_native_tools_is_rejected(change):
    state = case()
    state.request = state.request.model_copy(update=change)
    with pytest.raises(ValueError):
        resolve(state)


@pytest.mark.parametrize("metadata", [{}, {retention.METADATA_KEY: {"source_complete": True}}])
def test_missing_or_story_asserted_source_receipt_fails_closed(metadata):
    state = case()
    state.request.metadata = metadata
    with pytest.raises(ValueError, match="source_contract"):
        resolve(state)


@pytest.mark.parametrize("settings", [None, {}, SimpleNamespace(llm={})])
def test_exact_route_requires_real_runtime_settings(settings):
    state = case()
    with pytest.raises(ValueError, match="runtime_settings_required"):
        deadline.resolve_native_deadline(state.request, state.role, settings)


@pytest.mark.parametrize("field,value", [("retain_prompt_payloads", False), ("mode", "s3")])
def test_runtime_retention_must_be_enabled_and_local(field, value):
    state = case()
    setattr(state.settings.artifact_store, field, value)
    with pytest.raises(ValueError):
        resolve(state)


@pytest.mark.parametrize("value", [0, -10, True, float("inf"), float("nan"), "900"])
def test_invalid_planner_timeout_fails_closed(value):
    state = case()
    state.settings.llm.planner.timeout_seconds = value
    with pytest.raises(ValueError, match="planner_timeout_invalid"):
        resolve(state)


def test_output_budget_and_whole_wire_limits_cannot_be_bypassed():
    state = case()
    state.request.metadata["planning_output_budget"]["total_output_tokens"] -= 1
    with pytest.raises(ValueError, match="output_budget_changed"):
        resolve(state)
    state = case()
    state.request.tools[0]["function"]["description"] = "x" * 1_000_000
    receipt = state.request.metadata[retention.METADATA_KEY]
    _, _, source = retention._packet(state.request, receipt["packet_start"], receipt["packet_end"])
    state.request.metadata[retention.METADATA_KEY] = retention.source_complete_retention_metadata(
        state.request,
        role=state.role,
        logical_source=source,
        packet_start=receipt["packet_start"],
        packet_end=receipt["packet_end"],
    )
    with pytest.raises(ValueError, match="wire_character_limit"):
        resolve(state)
