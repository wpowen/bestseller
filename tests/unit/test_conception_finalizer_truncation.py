"""Native completion boundaries with explicit no-provider/no-DB tripwires."""

import hashlib
import json
import socket
from uuid import uuid4

import pytest

from bestseller.services import conception, llm, minimax_m31_protocol, production_strategy
from bestseller.services.budget import ExecutionBudgetError
from bestseller.services.degradation_tracker import DegradationTracker
from bestseller.services.llm import LLMCompletionRequest, LLMCompletionResult
from bestseller.settings import LLMRoleSettings, load_settings

pytestmark = pytest.mark.unit

GENERATION = (
    "conception_finalize",
    "conception_finalize_repair",
    "conception_finalize_reader_first_v1",
    "conception_finalize_reader_first_v1_repair",
    "conception_concept_seed_final_premise_reconciliation",
    "conception_concept_seed_final_premise_reconciliation_repair",
    "conception_explicit_seed_fidelity_repair",
)
RECONCILIATION = (
    "conception_concept_seed_final_premise_reconciliation",
    "conception_concept_seed_final_premise_reconciliation_repair",
)
# The native reconciliation producer calls planner; JSON repair forwards that
# same role. Keep the old editor combinations below as explicit negatives.
GENERATION_ROUTES = [
    ("planner" if template in RECONCILIATION else "editor", template)
    for template in GENERATION
]


class ExternalAccessForbidden(BaseException):
    pass


class NoDatabase:
    def __getattr__(self, name):
        raise ExternalAccessForbidden(f"No database access allowed: {name}")


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        raise ExternalAccessForbidden("No provider/network/DB access allowed")

    async def paid_forbidden(*args, **kwargs):
        forbidden()

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(llm, "complete_text", paid_forbidden)
    monkeypatch.setattr(conception, "complete_text", paid_forbidden)


def completion(content, finish_reason="stop"):
    return LLMCompletionResult(
        content=content,
        provider="openai",
        model_name="openai/MiniMax-M3.1-Flash",
        llm_run_id=uuid4(),
        finish_reason=finish_reason,
        input_tokens=12996,
        output_tokens=32768 if finish_reason == "length" else 512,
    )


def queued_gateway(monkeypatch, *outcomes):
    requests = []
    pending = iter(outcomes)

    async def complete(session, settings, request):
        assert isinstance(session, NoDatabase)
        requests.append(request)
        try:
            outcome = next(pending)
        except StopIteration:
            raise ExternalAccessForbidden("Unbounded extra completion attempted") from None
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    monkeypatch.setattr(conception, "complete_text", complete)
    return requests


async def call_json(tracker=None, *, template="conception_finalize"):
    return await conception._llm_call_json(
        NoDatabase(),
        load_settings(env={"BESTSELLER__LLM__MOCK": "true"}),
        role="editor",
        system_prompt="Return the complete editorial JSON.",
        user_prompt="ORIGINAL_USER_INPUT",
        fallback='{"static_fallback": true}',
        template=template,
        stage="conception.final",
        project_id=uuid4(),
        workflow_run_id=uuid4(),
        degradation_tracker=tracker,
        degradation_component="conception_finalize",
    )


@pytest.mark.parametrize("content", ['{"apparently_complete":true}', '{"salvageable":true', "text"])
async def test_direct_call_rejects_native_length_before_any_json_salvage(monkeypatch, content):
    result = completion(content, "length")
    before = result.model_dump()
    requests = queued_gateway(monkeypatch, result)
    tracker = DegradationTracker()
    with pytest.raises(conception.ConceptionCompletionTruncatedError) as caught:
        await conception._llm_call(
            NoDatabase(),
            load_settings(env={}),
            role="editor",
            system_prompt="system",
            user_prompt="user",
            fallback="{}",
            template="conception_finalize",
            degradation_tracker=tracker,
        )
    error = caught.value
    assert error.finish_reason == "length"
    assert error.llm_run_ids == [result.llm_run_id]
    assert len(error.findings) == 1
    assert error.findings[0].code == "conception_completion_truncated"
    assert error.findings[0].evidence == {
        "finish_reason": "length",
        "llm_run_id": str(result.llm_run_id),
        "input_tokens": 12996,
        "output_tokens": 32768,
        "content_chars": len(content),
        "content_sha256": hashlib.sha256(content.encode()).hexdigest(),
    }
    assert tracker.events[0].reason == "completion_truncated"
    assert not tracker.events[0].fallback
    assert result.model_dump() == before
    assert len(requests) == 1


@pytest.mark.parametrize("content", ['{"truncated_prefix":true}', '{"truncated_prefix":true'])
async def test_length_uses_one_diagnostic_repair_and_preserves_both_native_ids(
    monkeypatch, content
):
    original, repaired = completion(content, "length"), completion('{"repaired":true}')
    snapshots = [r.model_dump() for r in (original, repaired)]
    requests = queued_gateway(monkeypatch, original, repaired)
    parsed = []
    extract = conception._extract_json

    def spy(text):
        parsed.append(text)
        return extract(text)

    monkeypatch.setattr(conception, "_extract_json", spy)
    tracker = DegradationTracker()
    payload, ids = await call_json(tracker)
    assert payload == {"repaired": True}
    assert ids == [original.llm_run_id, repaired.llm_run_id]
    assert parsed == [repaired.content]
    assert [r.prompt_template for r in requests] == [
        "conception_finalize",
        "conception_finalize_repair",
    ]
    assert "ORIGINAL_USER_INPUT" in requests[1].user_prompt
    assert "conception_completion_truncated" in requests[1].user_prompt
    assert "finish_reason=length" in requests[1].user_prompt
    assert str(original.llm_run_id) in requests[1].user_prompt
    assert requests[1].project_id == requests[0].project_id
    assert requests[1].workflow_run_id == requests[0].workflow_run_id
    assert all(r.max_tokens_override is None for r in requests)
    assert [e.reason for e in tracker.events] == ["completion_truncated", "json_repair"]
    assert [r.model_dump() for r in (original, repaired)] == snapshots


@pytest.mark.parametrize("initial_reason", ["length", "stop"])
async def test_repair_length_is_fatal_even_with_valid_json_and_static_fallback(
    monkeypatch, initial_reason
):
    original = completion(
        '{"prefix":true}' if initial_reason == "length" else "not json", initial_reason
    )
    repaired = completion('{"pretend_complete":true}', "length")
    requests = queued_gateway(monkeypatch, original, repaired)
    tracker = DegradationTracker()
    with pytest.raises(conception.ConceptionCompletionTruncatedError) as caught:
        await call_json(tracker)
    assert caught.value.llm_run_ids == [original.llm_run_id, repaired.llm_run_id]
    assert caught.value.template == "conception_finalize_repair"
    assert caught.value.finish_reason == "length"
    assert len(requests) == 2
    assert tracker.events[-1].reason == "completion_truncated"
    assert not any(e.reason in {"static_fallback", "fallback_unparseable"} for e in tracker.events)


async def test_initial_length_then_invalid_repair_cannot_become_a_static_success(monkeypatch):
    original, repaired = completion('{"prefix":true}', "length"), completion("not json")
    requests = queued_gateway(monkeypatch, original, repaired)
    tracker = DegradationTracker()
    with pytest.raises(conception.ConceptionCompletionTruncatedError) as caught:
        await call_json(tracker)
    assert caught.value.llm_run_ids == [original.llm_run_id, repaired.llm_run_id]
    assert len(requests) == 2
    assert tracker.events[-1].reason == "truncated_completion_repair_failed"
    assert not any(e.fallback for e in tracker.events)


@pytest.mark.parametrize("reason", [None, "stop"])
async def test_legacy_good_completion_is_unchanged_without_marker_heuristics(monkeypatch, reason):
    result = completion('{"message":"length is a story word; not a transport failure"}', reason)
    requests = queued_gateway(monkeypatch, result)
    tracker = DegradationTracker()
    payload, ids = await call_json(tracker)
    assert payload == {"message": "length is a story word; not a transport failure"}
    assert ids == [result.llm_run_id]
    assert len(requests) == 1
    assert tracker.events == ()


@pytest.mark.parametrize(
    "repair_content,expected",
    [('{"repaired":true}', {"repaired": True}), ("not json", {"static_fallback": True})],
)
async def test_legacy_nonlength_json_repair_and_fallback_are_preserved(
    monkeypatch, repair_content, expected
):
    original, repaired = completion("not json"), completion(repair_content)
    requests = queued_gateway(monkeypatch, original, repaired)
    tracker = DegradationTracker()
    payload, ids = await call_json(tracker)
    assert payload == expected
    assert ids == [original.llm_run_id, repaired.llm_run_id]
    assert len(requests) == 2
    assert tracker.events[-1].reason == (
        "static_fallback" if "static_fallback" in expected else "json_repair"
    )


async def test_initial_execution_stop_does_not_enter_repair(monkeypatch):
    stop = ExecutionBudgetError("execution_authorization_paused")
    requests = queued_gateway(monkeypatch, stop)
    with pytest.raises(ExecutionBudgetError) as caught:
        await call_json()
    assert caught.value is stop
    assert len(requests) == 1


async def test_repair_execution_stop_preserves_old_native_id_and_exception_type(monkeypatch):
    original = completion('{"prefix":true}', "length")
    stop = ExecutionBudgetError("execution_authorization_paused")
    requests = queued_gateway(monkeypatch, original, stop)
    with pytest.raises(ExecutionBudgetError) as caught:
        await call_json()
    assert caught.value is stop
    assert caught.value.llm_run_ids == [original.llm_run_id]
    assert len(requests) == 2


@pytest.mark.parametrize("template", [
    "conception_finalize_reader_first_v1", "conception_finalize_reader_first_v1_repair",
])
async def test_reader_first_unknown_does_not_request_repair_or_replace_terminal(monkeypatch, template):
    stop = ExecutionBudgetError("execution_result_unknown")
    requests = queued_gateway(monkeypatch, stop)
    with pytest.raises(ExecutionBudgetError) as caught:
        await call_json(template=template)
    assert caught.value is stop
    assert len(requests) == 1 and requests[0].prompt_template == template


@pytest.mark.parametrize("template", [
    "conception_finalize_reader_first_v1", "conception_finalize_reader_first_v1_repair",
])
def test_reader_first_projectless_profile_catalog_has_medium_wire_and_exact_input(
    monkeypatch, tmp_path, template,
):
    """RUN2 policy RED: actual profile/catalog, no project-wide editor fallback."""
    from bestseller.services.model_catalog import ModelCatalogEntry
    from bestseller.settings import LLM_RUNTIME_PROFILE_ENV, apply_runtime_llm_profile

    profile = tmp_path / "llm-runtime-profile.json"
    profile.write_text(json.dumps({"active_profile": "minimax"}))
    monkeypatch.setenv(LLM_RUNTIME_PROFILE_ENV, str(profile))
    settings = apply_runtime_llm_profile(load_settings(env={}))
    entry = ModelCatalogEntry(
        id="minimax-m3.1-flash", display_name="MiniMax M3.1 Flash", vendor="MiniMax",
        model="openai/MiniMax-M3.1-Flash", api_base="https://api.minimaxi.com/v1",
    )
    role = llm._apply_model_override(settings.llm.editor, entry)
    assert role.reasoning_effort is role.thinking_type is None
    assert role.max_tokens == 32768
    request = LLMCompletionRequest(
        logical_role="editor", project_id=None, workflow_run_id=uuid4(),
        prompt_template=template, system_prompt="Return the complete editorial JSON.",
        user_prompt=json.dumps({
            "source": [{"id": n, "value": "A character makes a costly choice.",
                        "unknown": None, "required": False} for n in range(32)],
            "tail": "PRESERVE_FINAL_SOURCE_LEAF  ",
        }, ensure_ascii=False),
        fallback_response="{}",
    )
    before_request, before_role = request.model_dump(), role.model_dump()
    source_sha = hashlib.sha256((request.system_prompt + request.user_prompt).encode()).hexdigest()
    # Preserve the historical/default max operand as a detached value; applying
    # this policy is not a rewrite or settlement of a retained paid request.
    before_payload = llm._effective_input_payload(request, role)
    assert before_payload["reasoning_effort"] == "max"
    actual = production_strategy.apply_production_stream_settings(
        request, role, profile=None, ambient_complete=True,
    )
    effective = llm._effective_input_payload(request, actual)
    wire = minimax_m31_protocol.wire_options(
        {"messages": effective["messages"], "max_tokens": effective["max_tokens"],
         "stream": effective["stream"]}, actual,
    )
    assert actual.reasoning_effort == effective["reasoning_effort"] == wire["reasoning_effort"] == "medium"
    assert actual.max_tokens == effective["max_tokens"] == wire["max_completion_tokens"] == 32768
    assert "max_tokens" not in wire
    assert actual.thinking_type is effective["thinking_type"] is None
    assert actual.stream is effective["stream"] is wire["stream"] is True
    assert wire["messages"] == before_payload["messages"]
    assert hashlib.sha256((request.system_prompt + request.user_prompt).encode()).hexdigest() == source_sha
    assert request.model_dump() == before_request and role.model_dump() == before_role
    assert before_payload["reasoning_effort"] == "max"


@pytest.mark.parametrize("logical_role,template", GENERATION_ROUTES)
@pytest.mark.parametrize("profile,ambient", [(None, True), ("complete", False)])
def test_exact_generation_defaults_to_medium_without_increasing_cap_or_disabling_thinking(
    logical_role, template, profile, ambient
):
    request = LLMCompletionRequest(
        logical_role=logical_role,
        prompt_template=template,
        system_prompt="system",
        user_prompt="user",
        fallback_response="{}",
    )
    role = LLMRoleSettings(
        model="openai/MiniMax-M3.1-Flash",
        temperature=0.4,
        timeout_seconds=60,
        max_tokens=32768,
        thinking_type="enabled",
    )
    before = role.model_dump()
    actual = production_strategy.apply_production_stream_settings(
        request, role, profile=profile, ambient_complete=ambient
    )
    wire = minimax_m31_protocol.wire_options({"max_tokens": 32768, "stream": actual.stream}, actual)
    assert actual.reasoning_effort == wire["reasoning_effort"] == "medium"
    assert wire["max_completion_tokens"] == actual.max_tokens == role.max_tokens == 32768
    assert actual.thinking_type == "enabled" and actual.stream is True
    assert role.model_dump() == before


@pytest.mark.parametrize("logical_role,template", GENERATION_ROUTES)
@pytest.mark.parametrize("effort", ["low", "high", "max"])
def test_generation_explicit_reasoning_effort_wins(logical_role, template, effort):
    request = LLMCompletionRequest(
        logical_role=logical_role,
        prompt_template=template,
        system_prompt="system",
        user_prompt="user",
        fallback_response="{}",
    )
    role = LLMRoleSettings(
        model="openai/MiniMax-M3.1-Flash",
        temperature=0.4,
        timeout_seconds=60,
        max_tokens=32768,
        reasoning_effort=effort,
    )
    actual = production_strategy.apply_production_stream_settings(
        request, role, ambient_complete=True
    )
    assert actual.reasoning_effort == effort


@pytest.mark.parametrize("template", GENERATION)
def test_new_exact_generation_exceptions_do_not_lower_critic_effort(template):
    request = LLMCompletionRequest(
        logical_role="critic",
        prompt_template=template,
        system_prompt="system",
        user_prompt="user",
        fallback_response="{}",
    )
    role = LLMRoleSettings(
        model="openai/MiniMax-M3.1-Flash", temperature=0.4, timeout_seconds=60, max_tokens=32768
    )
    before_request, before_role = request.model_dump(), role.model_dump()
    # These are authored generation routes, not evidence for a critic route.
    with pytest.raises(ValueError, match="^production_template_policy:unregistered_role$"):
        production_strategy.apply_production_stream_settings(request, role, ambient_complete=True)
    assert request.model_dump() == before_request and role.model_dump() == before_role
    assert minimax_m31_protocol.reasoning_effort(role, max_completion_tokens=32768) == "max"


@pytest.mark.parametrize("template", RECONCILIATION)
@pytest.mark.parametrize("profile,ambient,effort", [
    (None, True, None), ("complete", False, None),
    (None, True, "low"), (None, True, "high"), (None, True, "max"),
])
def test_reconciliation_rejects_nonproducer_editor_role(template, profile, ambient, effort):
    request = LLMCompletionRequest(
        logical_role="editor", prompt_template=template,
        system_prompt="system", user_prompt="user", fallback_response="{}",
    )
    role = LLMRoleSettings(
        model="openai/MiniMax-M3.1-Flash", temperature=0.4, timeout_seconds=60,
        max_tokens=32768, reasoning_effort=effort,
    )
    before_request, before_role = request.model_dump(), role.model_dump()
    with pytest.raises(ValueError, match="^production_template_policy:unregistered_role$"):
        production_strategy.apply_production_stream_settings(
            request, role, profile=profile, ambient_complete=ambient,
        )
    assert request.model_dump() == before_request and role.model_dump() == before_role


@pytest.mark.parametrize(
    "template,model,ambient,tools,outcome",
    [
        ("conception_finalize", "openai/MiniMax-M3.1-Flash", False, None, "identity"),
        ("conception_finalize", "openai/MiniMax-M3", True, None, "identity"),
        (
            "conception_finalize",
            "openai/MiniMax-M3.1-Flash",
            True,
            [{"type": "function", "function": {"name": "unrelated"}}],
            "medium_tools",
        ),
        ("conception_finalize_extra", "openai/MiniMax-M3.1-Flash", True, None, "unregistered_template"),
        ("conception_title_arena", "openai/MiniMax-M3.1-Flash", True, None, "unregistered_template"),
        ("conception_final_material_seed_fidelity_judge", "openai/MiniMax-M3.1-Flash", True, None, "unregistered_role"),
        ("conception_finalize_reader_first_v1", "openai/MiniMax-M3.1-Flash", False, None, "identity"),
        ("conception_finalize_reader_first_v1", "openai/MiniMax-M3", True, None, "identity"),
        (
            "conception_finalize_reader_first_v1", "openai/MiniMax-M3.1-Flash", True,
            [{"type": "function", "function": {"name": "unrelated"}}],
            "medium_tools",
        ),
        ("conception_finalize_reader_first_v1_extra", "openai/MiniMax-M3.1-Flash", True, None, "unregistered_template"),
    ],
)
def test_no_prefix_role_model_or_tool_widening(template, model, ambient, tools, outcome):
    request = LLMCompletionRequest(
        logical_role="editor",
        prompt_template=template,
        system_prompt="system",
        user_prompt="user",
        fallback_response="{}",
        tools=tools,
    )
    role = LLMRoleSettings(model=model, temperature=0.4, timeout_seconds=60, max_tokens=32768)
    before_request, before_role = request.model_dump(), role.model_dump()
    if outcome.startswith("unregistered_"):
        with pytest.raises(ValueError, match="^production_template_policy:" + outcome + "$"):
            production_strategy.apply_production_stream_settings(request, role, ambient_complete=ambient)
        assert request.model_dump() == before_request and role.model_dump() == before_role
        return
    actual = production_strategy.apply_production_stream_settings(
        request, role, ambient_complete=ambient
    )
    if outcome == "identity":
        assert actual is role and actual.reasoning_effort is None
    else:
        assert actual.reasoning_effort == "medium"
        assert actual.stream == role.stream
        assert llm._effective_input_payload(request, actual)["stream"] is False
    assert actual.max_tokens == role.max_tokens
    assert request.model_dump() == before_request and role.model_dump() == before_role
