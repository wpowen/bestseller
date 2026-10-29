"""Main-run RED for exact reader-first aliases in the existing retry policy.

Pure producer/retry-boundary tests: no DB, SDK, provider, or fabricated ledger.
The controlled ValueError exercises only an already-allowed retry branch; an
ExecutionBudgetError must stop before it. Native Unknown custody is covered by
the separate RUN2 reasoning-exhaustion SDK/ledger selection.
"""

import hashlib
import json
from types import SimpleNamespace

import pytest

from bestseller.services import concept_tournament as tournament
from bestseller.services import llm
from bestseller.services.budget import ExecutionBudgetError
from bestseller.services.source_bound_planning_content import new_reader_first_binding
from bestseller.settings import LLMRoleSettings, RetrySettings

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]
BASE_NAMES = (
    "concept_tournament_raw_idea_rank",
    "concept_tournament_premise_judge",
    "concept_tournament_finalist_judge",
    "concept_tournament_finalist_seriality_judge",
)
READER_SUFFIX = "_reader_first_v1"
CAP = 8192


def _role():
    return LLMRoleSettings(
        model="openai/MiniMax-M3.1-Flash", api_base="https://api.minimaxi.com/v1",
        temperature=.2, max_tokens=32768, timeout_seconds=30,
        thinking_type=None, reasoning_effort=None, api_key_env=None,
    )


def _request(template):
    return llm.LLMCompletionRequest(
        logical_role="critic", prompt_template=template,
        system_prompt="Synthetic tournament policy input.",
        user_prompt="Compare the supplied ideas.",
        fallback_response="{}", max_tokens_override=CAP,
    )


def _length_error():
    return ValueError(
        "LLM response content is empty (finish_reason='length', output_tokens=8192)."
    )


def _summary(value):
    wire = json.dumps(value, sort_keys=True, ensure_ascii=False)
    return {"type": type(value).__name__, "length": len(wire),
            "sha256": hashlib.sha256(wire.encode()).hexdigest()}


def _equal(request, field, expected, actual):
    difference = {"field": field, "expected": _summary(expected), "actual": _summary(actual)}
    request.node.user_properties.append(("retry_cap_differences", json.dumps(difference)))
    assert type(actual) is type(expected) and actual == expected, difference


async def _allowed_retry_caps(monkeypatch, native_request):
    """Observe the existing retry loop after its transport boundary classified an error."""
    before = native_request.model_dump(mode="json")
    caps, waits = [], []
    result = ('{"ranked": []}', 10, 20, "stop", None, None)

    async def classified_attempt(request, role):
        caps.append(llm._effective_request_max_tokens(role, request))
        if len(caps) == 1:
            raise _length_error()
        return result

    async def backoff(seconds):
        waits.append(seconds)

    monkeypatch.setattr(llm, "_paid_transport_attempt", classified_attempt)
    monkeypatch.setattr(llm, "_execution_backoff", backoff)
    monkeypatch.setattr(llm, "_llm_breaker", SimpleNamespace(
        record_failure=lambda: None, record_success=lambda: None,
    ))
    returned = await llm._call_litellm_with_retry(
        native_request, _role(), RetrySettings(max_attempts=2, rate_limit_fallback_enabled=False),
    )
    assert returned == result and len(waits) == 1
    assert native_request.model_dump(mode="json") == before
    return caps


@pytest.mark.parametrize("base", BASE_NAMES)
async def test_native_reader_alias_keeps_same_retry_cap_as_existing_name(
    base, monkeypatch, request,
):
    captured = []

    async def capture(_session, _settings, native_request):
        captured.append(native_request)
        return SimpleNamespace(content="{}", llm_run_id=None)

    monkeypatch.setattr(llm, "complete_text", capture)
    binding = new_reader_first_binding(
        "Synthetic tournament reader intent.", protocol="reader-first-planning.v2",
    )
    for marker in (None, binding):
        token = tournament._reader_first_tournament_context.set((marker, "zh"))
        try:
            generator = await tournament._default_generator(
                None, None, template=base, logical_role="critic", max_tokens=CAP,
                model_catalog_key="minimax-m3.1-flash",
            )
            await generator("Synthetic fixed system.", "Synthetic fixed user.")
        finally:
            tournament._reader_first_tournament_context.reset(token)

    original, alias = captured
    assert original.prompt_template == base
    assert alias.prompt_template == base + READER_SUFFIX
    assert original.model_dump(exclude={"prompt_template"}) == alias.model_dump(
        exclude={"prompt_template"},
    )
    old_caps = await _allowed_retry_caps(monkeypatch, original)
    alias_caps = await _allowed_retry_caps(monkeypatch, alias)
    assert old_caps == [CAP, CAP]
    _equal(request, "retry." + alias.prompt_template + ".effective_caps", old_caps, alias_caps)


@pytest.mark.parametrize("base", BASE_NAMES)
@pytest.mark.parametrize("suffix", ["_reader_first_v2", "_reader_first_v1_extra"])
async def test_unrecognized_suffix_retains_existing_cap_lowering(base, suffix, monkeypatch):
    native_request = _request(base + suffix)
    assert llm._should_lower_max_tokens_after_empty_length(native_request) is True
    caps = await _allowed_retry_caps(monkeypatch, native_request)
    assert caps == [CAP, max(4096, int(CAP * .67))]


@pytest.mark.parametrize("base", BASE_NAMES)
@pytest.mark.parametrize("suffix", [READER_SUFFIX, "_reader_first_v2"])
async def test_unknown_stops_before_retry_or_cap_change(base, suffix, monkeypatch):
    native_request = _request(base + suffix)
    before = native_request.model_dump(mode="json")
    unknown = ExecutionBudgetError("execution_result_unknown", "unit-reader-tournament")
    attempts, waits = [], []

    async def classified_unknown(request, role):
        attempts.append((request, llm._effective_request_max_tokens(role, request)))
        # A matching empty-length cause still cannot turn native Unknown into retry authority.
        raise unknown from _length_error()

    async def unexpected_backoff(seconds):
        waits.append(seconds)

    def unexpected_breaker_change():
        raise AssertionError("Unknown must not reach generic success/failure routing")

    monkeypatch.setattr(llm, "_paid_transport_attempt", classified_unknown)
    monkeypatch.setattr(llm, "_execution_backoff", unexpected_backoff)
    monkeypatch.setattr(llm, "_llm_breaker", SimpleNamespace(
        record_failure=unexpected_breaker_change, record_success=unexpected_breaker_change,
    ))
    with pytest.raises(ExecutionBudgetError) as raised:
        await llm._call_litellm_with_retry(
            native_request, _role(),
            RetrySettings(max_attempts=3, rate_limit_fallback_enabled=True),
        )
    assert raised.value is unknown
    assert raised.value.code == "execution_result_unknown"
    assert isinstance(raised.value.__cause__, ValueError)
    assert llm._is_empty_length_response_error(raised.value.__cause__) is True
    assert attempts == [(native_request, CAP)] and waits == []
    assert native_request.model_dump(mode="json") == before
