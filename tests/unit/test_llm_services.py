from __future__ import annotations

from uuid import uuid4

import pytest

from bestseller.infra.db.models import LlmRunModel
import bestseller.services.llm as _llm_mod
from bestseller.services.llm import (
    LLMCompletionRequest,
    _call_litellm_with_retry,
    _extract_retry_after_seconds,
    _is_rate_limit_error,
    _llm_breaker,
    complete_text,
)
from bestseller.settings import LLMRoleSettings, RetrySettings, load_settings


pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _reset_circuit_breaker() -> None:
    """Prevent cross-test pollution from the module-level circuit breaker."""
    _llm_breaker.reset()


@pytest.fixture(autouse=True)
def _reset_litellm_module_cache() -> None:
    """Reset the cached litellm module between tests so each test can inject its own fake."""
    _llm_mod._litellm_module = None
    yield
    _llm_mod._litellm_module = None


class FakeSession:
    def __init__(self) -> None:
        self.added: list[object] = []

    def add(self, obj: object) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        for obj in self.added:
            table = getattr(obj, "__table__", None)
            if table is None or "id" not in table.c:
                continue
            if getattr(obj, "id", None) is None:
                setattr(obj, "id", uuid4())


def test_complete_text_records_mock_run_when_mock_enabled() -> None:
    async def _run() -> None:
        session = FakeSession()
        settings = load_settings(env={"BESTSELLER__LLM__MOCK": "true"})
        result = await complete_text(
            session,
            settings,
            LLMCompletionRequest(
                logical_role="writer",
                system_prompt="system",
                user_prompt="user",
                fallback_response="fallback output",
            ),
        )

        assert result.provider == "mock"
        assert result.model_name == "mock-writer"
        assert result.llm_run_id is not None
        assert any(isinstance(obj, LlmRunModel) for obj in session.added)

    import asyncio

    asyncio.run(_run())


def test_complete_text_falls_back_when_litellm_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _run() -> None:
        session = FakeSession()
        settings = load_settings(env={})
        result = await complete_text(
            session,
            settings,
            LLMCompletionRequest(
                logical_role="critic",
                system_prompt="system",
                user_prompt="user",
                fallback_response="critic fallback",
            ),
        )

        llm_runs = [obj for obj in session.added if isinstance(obj, LlmRunModel)]
        assert result.provider == "fallback"
        assert result.model_name == "fallback-critic"
        assert result.llm_run_id is not None
        assert len(llm_runs) == 1
        assert "fallback_reason" in llm_runs[0].metadata_json

    def fake_import_module(name: str):
        raise ModuleNotFoundError(name)

    monkeypatch.setattr("bestseller.services.llm.importlib.import_module", fake_import_module)

    import asyncio

    asyncio.run(_run())


def test_complete_text_uses_api_base_and_api_key_env_for_real_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_kwargs: dict[str, object] = {}

    class FakeUsage:
        prompt_tokens = 123
        completion_tokens = 456

    class FakeMessage:
        content = "real gemini output"

    class FakeChoice:
        message = FakeMessage()
        finish_reason = "stop"

    class FakeResponse:
        choices = [FakeChoice()]
        usage = FakeUsage()

    class FakeLiteLLMModule:
        @staticmethod
        async def acompletion(**kwargs):
            captured_kwargs.update(kwargs)
            return FakeResponse()

    async def _run() -> None:
        session = FakeSession()
        settings = load_settings(
            env={
                "BESTSELLER__LLM__MOCK": "false",
                "BESTSELLER__LLM__WRITER__MODEL": "openai/gemini-2.5-flash",
                "BESTSELLER__LLM__WRITER__API_BASE": "https://generativelanguage.googleapis.com/v1beta/openai/",
                "BESTSELLER__LLM__WRITER__API_KEY_ENV": "GEMINI_API_KEY",
                "BESTSELLER__LLM__WRITER__STREAM": "false",
            }
        )
        result = await complete_text(
            session,
            settings,
            LLMCompletionRequest(
                logical_role="writer",
                system_prompt="system",
                user_prompt="user",
                fallback_response="fallback output",
            ),
        )

        assert result.provider == "openai"
        assert result.model_name == "openai/gemini-2.5-flash"
        assert result.content == "real gemini output"
        assert result.input_tokens == 123
        assert result.output_tokens == 456
        assert captured_kwargs["api_base"] == "https://generativelanguage.googleapis.com/v1beta/openai/"
        assert captured_kwargs["api_key"] == "test-gemini-key"
        assert captured_kwargs["stream"] is False

    monkeypatch.setattr(
        "bestseller.services.llm._get_litellm",
        lambda: FakeLiteLLMModule(),
    )
    monkeypatch.setattr(
        "bestseller.services.llm.get_runtime_env_value",
        lambda name: "test-gemini-key" if name == "GEMINI_API_KEY" else None,
    )

    import asyncio

    asyncio.run(_run())


def test_complete_text_collects_streaming_chunks(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeChunk:
        def __init__(self, content: str = "", finish_reason: str | None = None, usage=None) -> None:
            delta = type("Delta", (), {"content": content})()
            choice = type("Choice", (), {"delta": delta, "finish_reason": finish_reason})()
            self.choices = [choice]
            self.usage = usage

    class FakeUsage:
        prompt_tokens = 12
        completion_tokens = 34

    class FakeStream:
        def __init__(self) -> None:
            self._chunks = iter(
                [
                    FakeChunk("第", None),
                    FakeChunk("一", None),
                    FakeChunk("章", "stop", FakeUsage()),
                ]
            )

        def __aiter__(self):
            return self

        async def __anext__(self):
            try:
                return next(self._chunks)
            except StopIteration as exc:
                raise StopAsyncIteration from exc

    class FakeLiteLLMModule:
        @staticmethod
        async def acompletion(**kwargs):
            return FakeStream()

    async def _run() -> None:
        session = FakeSession()
        settings = load_settings(
            env={
                "BESTSELLER__LLM__MOCK": "false",
                "BESTSELLER__LLM__WRITER__MODEL": "openai/gemini-2.5-flash",
                "BESTSELLER__LLM__WRITER__STREAM": "true",
            }
        )
        result = await complete_text(
            session,
            settings,
            LLMCompletionRequest(
                logical_role="writer",
                system_prompt="system",
                user_prompt="user",
                fallback_response="fallback output",
            ),
        )

        assert result.provider == "openai"
        assert result.content == "第一章"
        assert result.input_tokens == 12
        assert result.output_tokens == 34
        assert result.finish_reason == "stop"

    monkeypatch.setattr(
        "bestseller.services.llm._get_litellm",
        lambda: FakeLiteLLMModule(),
    )

    import asyncio

    asyncio.run(_run())


# ── 429 rate-limit handling ─────────────────────────────────────────────


class _FakeRateLimitError(Exception):
    """Mimics litellm.exceptions.RateLimitError for detection tests."""

    def __init__(self, message: str = "429 Too Many Requests", *, retry_after: str | None = None) -> None:
        super().__init__(message)
        self.status_code = 429
        if retry_after is not None:
            self.response = type(
                "FakeResp",
                (),
                {"headers": {"Retry-After": retry_after}},
            )()


# The class name is scanned by ``_is_rate_limit_error`` — keep the suffix.
class FakeRateLimitError(_FakeRateLimitError):
    pass


def test_is_rate_limit_error_detects_class_suffix() -> None:
    assert _is_rate_limit_error(FakeRateLimitError()) is True


def test_is_rate_limit_error_detects_status_code() -> None:
    exc = Exception("boom")
    setattr(exc, "status_code", 429)
    assert _is_rate_limit_error(exc) is True


def test_is_rate_limit_error_detects_message_text() -> None:
    assert _is_rate_limit_error(Exception("HTTP 429: rate limit exceeded")) is True


def test_is_rate_limit_error_rejects_generic_error() -> None:
    assert _is_rate_limit_error(Exception("internal server error")) is False


def test_extract_retry_after_seconds_parses_numeric() -> None:
    exc = FakeRateLimitError(retry_after="12")
    assert _extract_retry_after_seconds(exc) == 12.0


def test_extract_retry_after_seconds_missing_returns_none() -> None:
    assert _extract_retry_after_seconds(FakeRateLimitError()) is None


def _make_role_settings() -> LLMRoleSettings:
    return LLMRoleSettings(
        model="openai/test",
        temperature=0.5,
        max_tokens=128,
        timeout_seconds=10,
        stream=False,
    )


def test_retry_loop_succeeds_after_429_burst(monkeypatch: pytest.MonkeyPatch) -> None:
    """429 errors must be retried (not fall back) until the call succeeds."""
    call_count = {"n": 0}

    async def fake_call_litellm(request, role_settings):  # type: ignore[no-untyped-def]
        call_count["n"] += 1
        if call_count["n"] < 4:
            raise FakeRateLimitError("rate limited", retry_after="0")
        return ("ok", 10, 20, "stop")

    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr("bestseller.services.llm._call_litellm", fake_call_litellm)
    monkeypatch.setattr("bestseller.services.llm.asyncio.sleep", fake_sleep)

    _llm_breaker.reset()
    initial_failures = _llm_breaker._consecutive_failures

    retry = RetrySettings(
        max_attempts=3,
        wait_min_seconds=1,
        wait_max_seconds=10,
        rate_limit_max_attempts=10,
        rate_limit_wait_min_seconds=1,
        rate_limit_wait_max_seconds=5,
    )

    request = LLMCompletionRequest(
        logical_role="writer",
        system_prompt="s",
        user_prompt="u",
        fallback_response="fb",
    )

    import asyncio

    content, _, _, _ = asyncio.run(
        _call_litellm_with_retry(request, _make_role_settings(), retry)
    )

    assert content == "ok"
    assert call_count["n"] == 4
    # Three 429s produced three sleeps before the successful attempt.
    assert len(sleeps) == 3
    # Circuit breaker must NOT have been tripped by 429s.
    assert _llm_breaker._consecutive_failures == initial_failures


def test_retry_loop_raises_after_rate_limit_budget_exhausted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_call_litellm(request, role_settings):  # type: ignore[no-untyped-def]
        raise FakeRateLimitError()

    async def fake_sleep(seconds: float) -> None:
        return None

    monkeypatch.setattr("bestseller.services.llm._call_litellm", fake_call_litellm)
    monkeypatch.setattr("bestseller.services.llm.asyncio.sleep", fake_sleep)

    retry = RetrySettings(
        max_attempts=3,
        wait_min_seconds=1,
        wait_max_seconds=2,
        rate_limit_max_attempts=3,
        rate_limit_wait_min_seconds=1,
        rate_limit_wait_max_seconds=2,
    )

    request = LLMCompletionRequest(
        logical_role="writer",
        system_prompt="s",
        user_prompt="u",
        fallback_response="fb",
    )

    import asyncio

    with pytest.raises(FakeRateLimitError):
        asyncio.run(
            _call_litellm_with_retry(request, _make_role_settings(), retry)
        )
