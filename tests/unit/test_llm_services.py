from __future__ import annotations

from pathlib import Path
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
    _rate_limit_fallback_until,
    complete_text,
)
from bestseller.settings import (
    LLM_RUNTIME_PROFILE_ENV,
    LLMRoleSettings,
    RetrySettings,
    load_settings,
    set_runtime_llm_profile,
)

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _reset_circuit_breaker() -> None:
    """Prevent cross-test pollution from the module-level circuit breaker."""
    _llm_breaker.reset()
    _rate_limit_fallback_until.clear()


@pytest.fixture(autouse=True)
def _reset_litellm_module_cache() -> None:
    """Reset the cached litellm module between tests so each test can inject its own fake."""
    _llm_mod._litellm_module = None
    yield
    _llm_mod._litellm_module = None


class FakeSession:
    def __init__(self) -> None:
        self.added: list[object] = []
        self.commits = 0
        self.rollbacks = 0

    def add(self, obj: object) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        for obj in self.added:
            table = getattr(obj, "__table__", None)
            if table is None or "id" not in table.c:
                continue
            if getattr(obj, "id", None) is None:
                setattr(obj, "id", uuid4())

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1

    def in_nested_transaction(self) -> bool:
        return False


class FailingFlushSession(FakeSession):
    async def flush(self) -> None:
        raise RuntimeError("connection closed during telemetry flush")


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
        assert session.commits == 0

    import asyncio

    asyncio.run(_run())


def test_mock_scene_writer_uses_apocalypse_supply_prompt_signal() -> None:
    async def _run() -> None:
        session = FakeSession()
        settings = load_settings(env={"BESTSELLER__LLM__MOCK": "true"})
        result = await complete_text(
            session,
            settings,
            LLMCompletionRequest(
                logical_role="writer",
                system_prompt="system",
                user_prompt=(
                    "题材：末日囤货。已选脑洞组合合同：重生囤货、避难所、重建秩序。\n"
                    '当前场景执行合同：{"signature_image":"冷库灯管照亮三箱胰岛素。",'
                    '"cut_point":"门禁卡显示城北私仓正在转移。"}'
                ),
                fallback_response='<!-- scene-draft-fallback project="demo" chapter=1 scene=1 -->',
                prompt_template="scene_writer",
                metadata={
                    "project_slug": "apocalypse-supply-demo",
                    "chapter_number": 1,
                    "scene_number": 1,
                    "context_query": "末日囤货 物资 避难所",
                    "protagonist_name": "林野",
                    "supporting_name": "周眠",
                },
            ),
        )

        assert result.provider == "mock"
        assert "净水片" in result.content
        assert "仓库" in result.content
        assert len(result.content.split("。", 1)[0]) <= 25
        assert "冷库灯管照亮三箱胰岛素。" in result.content
        assert "门禁卡显示城北私仓正在转移。" in result.content
        assert "胸口" in result.content or "心头" in result.content
        assert any(term in result.content[-120:] for term in ("？", "忽然", "突然", "倒计时"))
        assert "星港" not in result.content
        assert "航标" not in result.content
        assert "带来的压力没有重复上一段" not in result.content

        scene_two = await complete_text(
            session,
            settings,
            LLMCompletionRequest(
                logical_role="writer",
                system_prompt="system",
                user_prompt="题材：末日囤货。已选脑洞组合合同：重生囤货、避难所、重建秩序。",
                fallback_response='<!-- scene-draft-fallback project="demo" chapter=1 scene=2 -->',
                prompt_template="scene_writer",
                metadata={
                    "project_slug": "apocalypse-supply-demo",
                    "chapter_number": 1,
                    "scene_number": 2,
                    "context_query": "末日囤货 物资 避难所",
                    "protagonist_name": "林野",
                    "supporting_name": "周眠",
                },
            ),
        )
        assert "地下车库" in scene_two.content
        assert "柴油机" in scene_two.content
        assert "三箱胰岛素" not in scene_two.content

    import asyncio

    asyncio.run(_run())


def test_complete_text_does_not_fail_when_llm_run_logging_flush_fails() -> None:
    async def _run() -> None:
        session = FailingFlushSession()
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
        assert result.content == "fallback output"
        assert result.llm_run_id is None
        assert session.rollbacks == 1
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
        assert session.commits == 1

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


def test_complete_text_uses_runtime_llm_profile_override(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    captured_kwargs: dict[str, object] = {}

    class FakeUsage:
        prompt_tokens = 12
        completion_tokens = 34

    class FakeMessage:
        content = "runtime profile output"

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
        base = load_settings(
            env={
                "BESTSELLER__LLM__MOCK": "false",
                "BESTSELLER__LLM__WRITER__MODEL": "openai/not-used",
                "BESTSELLER__LLM__WRITER__API_BASE": "https://unused.example/v1",
                "BESTSELLER__LLM__WRITER__API_KEY_ENV": "UNUSED_KEY",
                "BESTSELLER__LLM__WRITER__STREAM": "true",
            }
        )
        settings = base.model_copy(
            update={
                "artifact_store": base.artifact_store.model_copy(
                    update={"local_dir": str(tmp_path)}
                )
            }
        )
        set_runtime_llm_profile(settings, "nvidia")

        result = await complete_text(
            FakeSession(),
            settings,
            LLMCompletionRequest(
                logical_role="writer",
                system_prompt="system",
                user_prompt="user",
                fallback_response="fallback output",
            ),
        )

        assert result.model_name == "openai/z-ai/glm-5.1"
        assert captured_kwargs["model"] == "openai/z-ai/glm-5.1"
        assert captured_kwargs["api_base"] == "https://integrate.api.nvidia.com/v1"
        assert captured_kwargs["api_key"] == "test-nvidia-key"
        assert captured_kwargs["stream"] is False

    monkeypatch.setattr(
        "bestseller.services.llm._get_litellm",
        lambda: FakeLiteLLMModule(),
    )
    monkeypatch.setattr(
        "bestseller.services.llm.get_runtime_env_value",
        lambda name: "test-nvidia-key" if name == "NVIDIA_API_KEY" else None,
    )

    import asyncio

    asyncio.run(_run())


def test_complete_text_forwards_deepseek_v4_thinking_controls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_kwargs: dict[str, object] = {}

    class FakeUsage:
        prompt_tokens = 12
        completion_tokens = 34

    class FakeMessage:
        content = "deepseek output"

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
        settings = load_settings(
            env={
                "BESTSELLER__LLM__MOCK": "false",
                "BESTSELLER__LLM__PLANNER__MODEL": "deepseek/deepseek-v4-flash",
                "BESTSELLER__LLM__PLANNER__API_BASE": "https://api.deepseek.com",
                "BESTSELLER__LLM__PLANNER__STREAM": "false",
                "BESTSELLER__LLM__PLANNER__THINKING_TYPE": "enabled",
                "BESTSELLER__LLM__PLANNER__REASONING_EFFORT": "high",
            }
        )
        result = await complete_text(
            FakeSession(),
            settings,
            LLMCompletionRequest(
                logical_role="planner",
                system_prompt="system",
                user_prompt="user",
                fallback_response="fallback output",
            ),
        )

        assert result.content == "deepseek output"
        assert captured_kwargs["model"] == "deepseek/deepseek-v4-flash"
        assert captured_kwargs["api_base"] == "https://api.deepseek.com"
        assert captured_kwargs["extra_body"] == {"thinking": {"type": "enabled"}}
        assert captured_kwargs["reasoning_effort"] == "high"

    monkeypatch.setattr(
        "bestseller.services.llm._get_litellm",
        lambda: FakeLiteLLMModule(),
    )

    import asyncio

    asyncio.run(_run())


def test_complete_text_disables_deepseek_v4_thinking_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_kwargs: dict[str, object] = {}

    class FakeUsage:
        prompt_tokens = 12
        completion_tokens = 34

    class FakeMessage:
        content = "deepseek output"

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
        settings = load_settings(
            env={
                "BESTSELLER__LLM__MOCK": "false",
                "BESTSELLER__LLM__WRITER__MODEL": "deepseek/deepseek-v4-flash",
                "BESTSELLER__LLM__WRITER__API_BASE": "https://api.deepseek.com",
                "BESTSELLER__LLM__WRITER__STREAM": "false",
            }
        )
        result = await complete_text(
            FakeSession(),
            settings,
            LLMCompletionRequest(
                logical_role="writer",
                system_prompt="system",
                user_prompt="user",
                fallback_response="fallback output",
            ),
        )

        assert result.content == "deepseek output"
        assert captured_kwargs["model"] == "deepseek/deepseek-v4-flash"
        assert captured_kwargs["api_base"] == "https://api.deepseek.com"
        assert captured_kwargs["extra_body"] == {"thinking": {"type": "disabled"}}
        assert "reasoning_effort" not in captured_kwargs

    monkeypatch.setattr(
        "bestseller.services.llm._get_litellm",
        lambda: FakeLiteLLMModule(),
    )

    import asyncio

    asyncio.run(_run())


def test_complete_text_disables_minimax_m3_thinking_by_default(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured_kwargs: dict[str, object] = {}
    monkeypatch.setenv(
        LLM_RUNTIME_PROFILE_ENV,
        str(tmp_path / "missing-runtime-profile.json"),
    )

    class FakeUsage:
        prompt_tokens = 12
        completion_tokens = 34

    class FakeMessage:
        content = "m3 output"

    class FakeChoice:
        message = FakeMessage()
        finish_reason = "stop"

    class FakeResponse:
        def __init__(self) -> None:
            self.choices = [FakeChoice()]
            self.usage = FakeUsage()

    class FakeLiteLLMModule:
        @staticmethod
        async def acompletion(**kwargs):
            captured_kwargs.update(kwargs)
            return FakeResponse()

    async def _run() -> None:
        settings = load_settings(
            env={
                "BESTSELLER__LLM__MOCK": "false",
                "BESTSELLER__LLM__WRITER__MODEL": "openai/MiniMax-M3",
                "BESTSELLER__LLM__WRITER__MODEL_OVERRIDE": "openai/MiniMax-M3",
                "BESTSELLER__LLM__WRITER__API_BASE": "https://api.minimaxi.com/v1",
                "BESTSELLER__LLM__WRITER__STREAM": "false",
            }
        )
        result = await complete_text(
            FakeSession(),
            settings,
            LLMCompletionRequest(
                logical_role="writer",
                system_prompt="system",
                user_prompt="user",
                fallback_response="fallback output",
            ),
        )

        assert result.content == "m3 output"
        assert captured_kwargs["model"] == "openai/MiniMax-M3"
        assert captured_kwargs["extra_body"] == {"thinking": {"type": "disabled"}}

    monkeypatch.setattr(
        "bestseller.services.llm._get_litellm",
        lambda: FakeLiteLLMModule(),
    )

    import asyncio

    asyncio.run(_run())


def test_complete_text_applies_request_max_tokens_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_kwargs: dict[str, object] = {}

    class FakeUsage:
        prompt_tokens = 10
        completion_tokens = 20

    class FakeMessage:
        content = "bounded output"

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
                max_tokens_override=321,
            ),
        )

        assert result.content == "bounded output"
        assert captured_kwargs["max_tokens"] == 321
        run = next(obj for obj in session.added if isinstance(obj, LlmRunModel))
        assert run.metadata_json["max_tokens_override"] == 321

    monkeypatch.setattr(
        "bestseller.services.llm._get_litellm",
        lambda: FakeLiteLLMModule(),
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


def test_complete_text_allows_minimax_output_override_above_role_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_kwargs: dict[str, object] = {}

    class FakeUsage:
        prompt_tokens = 10
        completion_tokens = 20

    class FakeMessage:
        content = "full chapter output"

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
                "BESTSELLER__LLM__WRITER__MODEL": "openai/MiniMax-M2.7-highspeed",
                "BESTSELLER__LLM__WRITER__MAX_TOKENS": "8192",
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
                max_tokens_override=12000,
            ),
        )

        assert result.content == "full chapter output"
        assert captured_kwargs["max_tokens"] == 12000
        assert captured_kwargs["extra_body"] == {"thinking": {"type": "disabled"}}

    monkeypatch.setattr(
        "bestseller.services.llm._get_litellm",
        lambda: FakeLiteLLMModule(),
    )

    import asyncio

    asyncio.run(_run())


def test_empty_length_response_retries_with_lower_output_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen_caps: list[int | None] = []

    async def fake_call(request, role_settings):  # type: ignore[no-untyped-def]
        seen_caps.append(request.max_tokens_override)
        if len(seen_caps) == 1:
            raise ValueError(
                "LLM response content is empty (finish_reason='length', "
                "output_tokens=12288)."
            )
        return ("ok", 10, 20, "stop", None, None)

    async def _run() -> None:
        result = await _call_litellm_with_retry(
            LLMCompletionRequest(
                logical_role="writer",
                system_prompt="system",
                user_prompt="user",
                fallback_response="fallback output",
                max_tokens_override=12288,
            ),
            LLMRoleSettings(
                model="openai/MiniMax-M2.7-highspeed",
                temperature=0.7,
                max_tokens=32768,
                timeout_seconds=30,
            ),
            RetrySettings(max_attempts=2),
        )
        assert result[0] == "ok"

    monkeypatch.setattr("bestseller.services.llm._call_litellm", fake_call)

    import asyncio

    asyncio.run(_run())

    assert seen_caps == [12288, 8232]


def test_empty_length_response_keeps_prose_output_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Prose generation that returns empty with finish_reason='length' was
    # TRUNCATED — it needs at least as many tokens next time, not fewer. Prose
    # writer templates must KEEP their cap on the empty-length retry (the
    # keep-cap set was previously empty, so every prose writer wrongly shrank,
    # churning without ever emitting a full scene).
    seen_caps: list[int | None] = []

    async def fake_call(request, role_settings):  # type: ignore[no-untyped-def]
        seen_caps.append(request.max_tokens_override)
        if len(seen_caps) == 1:
            raise ValueError(
                "LLM response content is empty (finish_reason='length', "
                "output_tokens=12288)."
            )
        return ("full chapter", 10, 20, "stop", None, None)

    async def _run() -> None:
        result = await _call_litellm_with_retry(
            LLMCompletionRequest(
                logical_role="writer",
                system_prompt="system",
                user_prompt="user",
                fallback_response="fallback output",
                prompt_template="chapter_first_writer",
                max_tokens_override=12288,
            ),
            LLMRoleSettings(
                model="openai/MiniMax-M2.7-highspeed",
                temperature=0.7,
                max_tokens=32768,
                timeout_seconds=30,
            ),
            RetrySettings(max_attempts=2),
        )
        assert result[0] == "full chapter"

    monkeypatch.setattr("bestseller.services.llm._call_litellm", fake_call)

    import asyncio

    asyncio.run(_run())

    # chapter_first_writer is a prose template -> keep cap, do NOT shrink.
    assert seen_caps == [12288, 12288]


def test_complete_text_fails_over_to_rate_limit_fallback_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, int]] = []

    async def fake_call_with_retry(request, role_settings, retry_settings):  # type: ignore[no-untyped-def]
        calls.append((role_settings.model, retry_settings.rate_limit_max_attempts))
        if role_settings.model == "openai/MiniMax-M2.7-highspeed":
            raise FakeRateLimitError("MiniMax quota exhausted")
        return ("glm output", 11, 22, "stop", None, None)

    async def _run() -> None:
        session = FakeSession()
        settings = load_settings(
            env={
                "BESTSELLER__LLM__MOCK": "false",
                "BESTSELLER__LLM__WRITER__MODEL": "openai/MiniMax-M2.7-highspeed",
                "BESTSELLER__LLM__WRITER__API_BASE": "https://api.minimaxi.com/v1",
                "BESTSELLER__LLM__WRITER__API_KEY_ENV": "MINIMAX_API_KEY",
                "BESTSELLER__LLM__WRITER__STREAM": "false",
                "BESTSELLER__LLM__WRITER__RATE_LIMIT_FALLBACK_MODEL": (
                    "openai/z-ai/glm-5.1"
                ),
                "BESTSELLER__LLM__WRITER__RATE_LIMIT_FALLBACK_API_BASE": (
                    "https://integrate.api.nvidia.com/v1"
                ),
                "BESTSELLER__LLM__WRITER__RATE_LIMIT_FALLBACK_API_KEY_ENV": (
                    "NVIDIA_API_KEY"
                ),
                "BESTSELLER__LLM__RETRY__RATE_LIMIT_FALLBACK_COOLDOWN_SECONDS": "300",
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

        assert result.content == "glm output"
        assert result.model_name == "openai/z-ai/glm-5.1"
        llm_run = next(obj for obj in session.added if isinstance(obj, LlmRunModel))
        assert llm_run.metadata_json["rate_limit_fallback_primary_model"] == (
            "openai/MiniMax-M2.7-highspeed"
        )
        assert "MiniMax quota exhausted" in llm_run.metadata_json[
            "rate_limit_fallback_reason"
        ]

    monkeypatch.setattr(
        "bestseller.services.llm._call_litellm_with_retry",
        fake_call_with_retry,
    )
    monkeypatch.setattr(
        "bestseller.services.llm.get_runtime_env_value",
        lambda name: "key" if name in {"MINIMAX_API_KEY", "NVIDIA_API_KEY"} else None,
    )

    import asyncio

    asyncio.run(_run())

    assert calls == [
        ("openai/MiniMax-M2.7-highspeed", 1),
        ("openai/z-ai/glm-5.1", 60),
    ]


def test_complete_text_uses_active_rate_limit_fallback_then_reprobes_primary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    async def fake_call_with_retry(request, role_settings, retry_settings):  # type: ignore[no-untyped-def]
        calls.append(role_settings.model)
        return (f"ok:{role_settings.model}", 1, 2, "stop", None, None)

    async def _run() -> None:
        settings = load_settings(
            env={
                "BESTSELLER__LLM__MOCK": "false",
                "BESTSELLER__LLM__WRITER__MODEL": "openai/MiniMax-M2.7-highspeed",
                "BESTSELLER__LLM__WRITER__RATE_LIMIT_FALLBACK_MODEL": (
                    "openai/z-ai/glm-5.1"
                ),
                "BESTSELLER__LLM__WRITER__RATE_LIMIT_FALLBACK_API_KEY_ENV": (
                    "NVIDIA_API_KEY"
                ),
                "BESTSELLER__LLM__RETRY__RATE_LIMIT_FALLBACK_COOLDOWN_SECONDS": "300",
            }
        )
        request = LLMCompletionRequest(
            logical_role="writer",
            system_prompt="system",
            user_prompt="user",
            fallback_response="fallback output",
        )

        key = "writer|openai/MiniMax-M2.7-highspeed||"
        _rate_limit_fallback_until[key] = 10**12
        fallback_result = await complete_text(FakeSession(), settings, request)
        assert fallback_result.model_name == "openai/z-ai/glm-5.1"

        _rate_limit_fallback_until.clear()
        primary_result = await complete_text(FakeSession(), settings, request)
        assert primary_result.model_name == "openai/MiniMax-M2.7-highspeed"

    monkeypatch.setattr(
        "bestseller.services.llm._call_litellm_with_retry",
        fake_call_with_retry,
    )
    monkeypatch.setattr(
        "bestseller.services.llm.get_runtime_env_value",
        lambda name: "key" if name == "NVIDIA_API_KEY" else None,
    )

    import asyncio

    asyncio.run(_run())

    assert calls == ["openai/z-ai/glm-5.1", "openai/MiniMax-M2.7-highspeed"]


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


def test_is_rate_limit_error_detects_quota_exhaustion_text() -> None:
    assert _is_rate_limit_error(Exception("MiniMax quota exhausted")) is True


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
