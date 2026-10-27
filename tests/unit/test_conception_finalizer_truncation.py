"""Native completion boundaries with explicit no-provider/no-DB tripwires."""

import hashlib
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
    "conception_concept_seed_final_premise_reconciliation",
    "conception_concept_seed_final_premise_reconciliation_repair",
    "conception_explicit_seed_fidelity_repair",
)
GENERATION_ROUTES = [("editor", template) for template in GENERATION] + [
    ("planner", "conception_concept_seed_final_premise_reconciliation"),
    ("planner", "conception_concept_seed_final_premise_reconciliation_repair"),
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


async def call_json(tracker=None):
    return await conception._llm_call_json(
        NoDatabase(),
        load_settings(env={"BESTSELLER__LLM__MOCK": "true"}),
        role="editor",
        system_prompt="Return the complete editorial JSON.",
        user_prompt="ORIGINAL_USER_INPUT",
        fallback='{"static_fallback": true}',
        template="conception_finalize",
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
    actual = production_strategy.apply_production_stream_settings(
        request, role, ambient_complete=True
    )
    assert actual.reasoning_effort is None
    assert minimax_m31_protocol.reasoning_effort(actual, max_completion_tokens=32768) == "max"


@pytest.mark.parametrize(
    "template,model,ambient,tools",
    [
        ("conception_finalize", "openai/MiniMax-M3.1-Flash", False, None),
        ("conception_finalize", "openai/MiniMax-M3", True, None),
        (
            "conception_finalize",
            "openai/MiniMax-M3.1-Flash",
            True,
            [{"type": "function", "function": {"name": "unrelated"}}],
        ),
        ("conception_finalize_extra", "openai/MiniMax-M3.1-Flash", True, None),
        ("conception_title_arena", "openai/MiniMax-M3.1-Flash", True, None),
        ("conception_final_material_seed_fidelity_judge", "openai/MiniMax-M3.1-Flash", True, None),
    ],
)
def test_no_prefix_role_model_or_tool_widening(template, model, ambient, tools):
    request = LLMCompletionRequest(
        logical_role="editor",
        prompt_template=template,
        system_prompt="system",
        user_prompt="user",
        fallback_response="{}",
        tools=tools,
    )
    role = LLMRoleSettings(model=model, temperature=0.4, timeout_seconds=60, max_tokens=32768)
    actual = production_strategy.apply_production_stream_settings(
        request, role, ambient_complete=ambient
    )
    assert actual.reasoning_effort is None
    assert actual.max_tokens == role.max_tokens
