from __future__ import annotations

from uuid import uuid4

import pytest

from bestseller.infra.db.models import LlmRunModel
from bestseller.services.llm import LLMCompletionRequest, complete_text
from bestseller.settings import load_settings


pytestmark = pytest.mark.unit


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
        "bestseller.services.llm.importlib.import_module",
        lambda name: FakeLiteLLMModule(),
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
        "bestseller.services.llm.importlib.import_module",
        lambda name: FakeLiteLLMModule(),
    )

    import asyncio

    asyncio.run(_run())
