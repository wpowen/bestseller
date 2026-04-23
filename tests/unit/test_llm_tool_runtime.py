"""Unit tests for ``bestseller.services.llm_tool_runtime``.

We exercise :func:`run_tool_loop` against a fake litellm module that
responds with scripted sequences of tool_call / plain-text completions,
verifying:

* plain-text termination on round 1,
* multi-round tool dispatch + result replay,
* ``is_terminal`` tool short-circuits the loop,
* ``max_rounds`` clamp,
* parallel dispatch within one round,
* unknown tool + handler exceptions surface as JSON ``{"error":...}``.

The fake litellm reads one scripted response per ``acompletion`` call so
the loop's message array is observable between rounds.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from uuid import uuid4

import pytest

import bestseller.services.llm as _llm_mod
from bestseller.services.llm import LLMCompletionRequest
from bestseller.services.llm_tool_runtime import (
    ToolLoopResult,
    ToolRegistry,
    ToolSpec,
    run_tool_loop,
)
from bestseller.settings import load_settings


pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _reset_litellm_module_cache() -> None:
    _llm_mod._litellm_module = None
    yield
    _llm_mod._litellm_module = None


@pytest.fixture(autouse=True)
def _reset_circuit_breaker() -> None:
    from bestseller.services.llm import _llm_breaker

    _llm_breaker.reset()


class FakeSession:
    def __init__(self) -> None:
        self.added: list[Any] = []

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        for obj in self.added:
            table = getattr(obj, "__table__", None)
            if table is None or "id" not in table.c:
                continue
            if getattr(obj, "id", None) is None:
                setattr(obj, "id", uuid4())


def _make_message(
    content: str | None = "",
    tool_calls: list[dict[str, Any]] | None = None,
) -> Any:
    class _Msg:
        pass

    msg = _Msg()
    msg.content = content
    if tool_calls is not None:
        msg.tool_calls = tool_calls
    return msg


def _make_response(
    content: str | None = "",
    tool_calls: list[dict[str, Any]] | None = None,
    finish_reason: str = "stop",
    prompt_tokens: int = 10,
    completion_tokens: int = 5,
) -> Any:
    class _Usage:
        pass

    class _Choice:
        pass

    class _Resp:
        pass

    usage = _Usage()
    usage.prompt_tokens = prompt_tokens
    usage.completion_tokens = completion_tokens

    choice = _Choice()
    choice.message = _make_message(content=content, tool_calls=tool_calls)
    choice.finish_reason = finish_reason

    resp = _Resp()
    resp.choices = [choice]
    resp.usage = usage
    return resp


class ScriptedLiteLLM:
    """Replay a pre-built queue of responses, one per ``acompletion`` call.

    Also records the ``messages`` array + ``tools`` passed in for assertion.
    """

    def __init__(self, scripted_responses: list[Any]) -> None:
        self._responses = list(scripted_responses)
        self.calls: list[dict[str, Any]] = []

    async def acompletion(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if not self._responses:
            raise AssertionError("ScriptedLiteLLM exhausted but acompletion called")
        return self._responses.pop(0)


def _settings_for_real_call() -> Any:
    return load_settings(
        env={
            "BESTSELLER__LLM__MOCK": "false",
            "BESTSELLER__LLM__WRITER__MODEL": "openai/fake-model",
            "BESTSELLER__LLM__WRITER__STREAM": "false",
            "BESTSELLER__LLM__WRITER__TIMEOUT_SECONDS": "10",
            "BESTSELLER__LLM__WRITER__TEMPERATURE": "0.2",
            "BESTSELLER__LLM__WRITER__MAX_TOKENS": "256",
        }
    )


def _base_request() -> LLMCompletionRequest:
    return LLMCompletionRequest(
        logical_role="writer",
        system_prompt="SYSTEM",
        user_prompt="USER",
        fallback_response="FALLBACK",
    )


# ── Handlers used across tests ─────────────────────────────────────────


async def _ok_handler(args: dict[str, Any]) -> dict[str, Any]:
    return {"echo": args}


async def _raising_handler(_: dict[str, Any]) -> dict[str, Any]:
    raise RuntimeError("deliberate boom")


# ── Tests ───────────────────────────────────────────────────────────────


def test_run_tool_loop_exits_immediately_when_model_returns_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scripted = ScriptedLiteLLM([_make_response(content="just plain text")])
    monkeypatch.setattr("bestseller.services.llm._get_litellm", lambda: scripted)

    async def _run() -> ToolLoopResult:
        registry = ToolRegistry(
            [
                ToolSpec(
                    name="noop",
                    description="does nothing",
                    parameters={"type": "object", "properties": {}},
                    handler=_ok_handler,
                )
            ]
        )
        return await run_tool_loop(
            FakeSession(),
            _settings_for_real_call(),
            base_request=_base_request(),
            registry=registry,
            max_rounds=3,
        )

    result = asyncio.run(_run())
    assert result.exit_reason == "text"
    assert result.final_content == "just plain text"
    assert result.rounds == 1
    assert result.trace == []
    # The model was told about the registered tool on round 1.
    call = scripted.calls[0]
    assert call["tools"][0]["function"]["name"] == "noop"
    assert call["tool_choice"] == "auto"
    assert call["stream"] is False


def test_run_tool_loop_dispatches_then_replays_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Round 1: model calls two tools in parallel.
    # Round 2: model emits plain text.
    round1 = _make_response(
        content="",
        tool_calls=[
            {
                "id": "call_a",
                "type": "function",
                "function": {"name": "search", "arguments": '{"q":"alpha"}'},
            },
            {
                "id": "call_b",
                "type": "function",
                "function": {"name": "search", "arguments": '{"q":"beta"}'},
            },
        ],
    )
    round2 = _make_response(content="done")
    scripted = ScriptedLiteLLM([round1, round2])
    monkeypatch.setattr("bestseller.services.llm._get_litellm", lambda: scripted)

    async def _run() -> ToolLoopResult:
        registry = ToolRegistry(
            [
                ToolSpec(
                    name="search",
                    description="search the web",
                    parameters={
                        "type": "object",
                        "properties": {"q": {"type": "string"}},
                        "required": ["q"],
                    },
                    handler=_ok_handler,
                )
            ]
        )
        return await run_tool_loop(
            FakeSession(),
            _settings_for_real_call(),
            base_request=_base_request(),
            registry=registry,
            max_rounds=3,
        )

    result = asyncio.run(_run())
    assert result.exit_reason == "text"
    assert result.final_content == "done"
    assert result.rounds == 2
    assert len(result.trace) == 2
    assert [rec.tool_name for rec in result.trace] == ["search", "search"]
    assert [rec.arguments for rec in result.trace] == [{"q": "alpha"}, {"q": "beta"}]

    # Round 2 message array must carry the assistant tool_calls turn +
    # both tool responses, in order.
    round2_messages = scripted.calls[1]["messages"]
    assert round2_messages[0]["role"] == "system"
    assert round2_messages[1]["role"] == "user"
    assert round2_messages[2]["role"] == "assistant"
    assert round2_messages[2]["tool_calls"] is not None
    assert round2_messages[3]["role"] == "tool"
    assert round2_messages[3]["tool_call_id"] == "call_a"
    assert round2_messages[4]["role"] == "tool"
    assert round2_messages[4]["tool_call_id"] == "call_b"
    # Tool message body is JSON-serialised handler result.
    assert json.loads(round2_messages[3]["content"]) == {"echo": {"q": "alpha"}}


def test_run_tool_loop_terminal_tool_short_circuits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Model calls the terminal tool once; loop should end even though
    # we also scripted a plain-text response (never reached).
    round1 = _make_response(
        content="",
        tool_calls=[
            {
                "id": "call_fin",
                "type": "function",
                "function": {
                    "name": "finalize",
                    "arguments": '{"answer":"42"}',
                },
            }
        ],
    )
    scripted = ScriptedLiteLLM([round1, _make_response(content="never-seen")])
    monkeypatch.setattr("bestseller.services.llm._get_litellm", lambda: scripted)

    async def _run() -> ToolLoopResult:
        registry = ToolRegistry(
            [
                ToolSpec(
                    name="finalize",
                    description="finalize",
                    parameters={"type": "object", "properties": {}},
                    handler=_ok_handler,
                    is_terminal=True,
                )
            ]
        )
        return await run_tool_loop(
            FakeSession(),
            _settings_for_real_call(),
            base_request=_base_request(),
            registry=registry,
            max_rounds=5,
        )

    result = asyncio.run(_run())
    assert result.exit_reason == "terminal_tool"
    assert result.final_content == ""
    assert "finalize" in result.final_tool_results
    assert result.final_tool_results["finalize"] == {"echo": {"answer": "42"}}
    # Only one acompletion call was made — we stopped after the terminal.
    assert len(scripted.calls) == 1


def test_run_tool_loop_hits_max_rounds(monkeypatch: pytest.MonkeyPatch) -> None:
    # Model keeps calling the tool forever — loop should terminate at cap.
    def forever_call(i: int) -> Any:
        return _make_response(
            content="",
            tool_calls=[
                {
                    "id": f"call_{i}",
                    "type": "function",
                    "function": {"name": "loop", "arguments": "{}"},
                }
            ],
        )

    scripted = ScriptedLiteLLM([forever_call(i) for i in range(5)])
    monkeypatch.setattr("bestseller.services.llm._get_litellm", lambda: scripted)

    async def _run() -> ToolLoopResult:
        registry = ToolRegistry(
            [
                ToolSpec(
                    name="loop",
                    description="infinite",
                    parameters={"type": "object", "properties": {}},
                    handler=_ok_handler,
                )
            ]
        )
        return await run_tool_loop(
            FakeSession(),
            _settings_for_real_call(),
            base_request=_base_request(),
            registry=registry,
            max_rounds=3,
        )

    result = asyncio.run(_run())
    assert result.exit_reason == "max_rounds"
    assert result.rounds == 3
    assert len(scripted.calls) == 3


def test_run_tool_loop_handles_unknown_tool_and_handler_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Round 1: model requests an unknown tool + a handler that raises.
    # Round 2: model emits text acknowledging the error.
    round1 = _make_response(
        content="",
        tool_calls=[
            {
                "id": "call_x",
                "type": "function",
                "function": {"name": "mystery", "arguments": "{}"},
            },
            {
                "id": "call_y",
                "type": "function",
                "function": {"name": "crashy", "arguments": "{}"},
            },
        ],
    )
    round2 = _make_response(content="recovered")
    scripted = ScriptedLiteLLM([round1, round2])
    monkeypatch.setattr("bestseller.services.llm._get_litellm", lambda: scripted)

    async def _run() -> ToolLoopResult:
        registry = ToolRegistry(
            [
                ToolSpec(
                    name="crashy",
                    description="raises",
                    parameters={"type": "object", "properties": {}},
                    handler=_raising_handler,
                )
            ]
        )
        return await run_tool_loop(
            FakeSession(),
            _settings_for_real_call(),
            base_request=_base_request(),
            registry=registry,
            max_rounds=3,
        )

    result = asyncio.run(_run())
    assert result.exit_reason == "text"
    assert result.final_content == "recovered"
    assert result.rounds == 2
    assert len(result.trace) == 2

    unknown = next(r for r in result.trace if r.tool_name == "mystery")
    assert unknown.error == "unknown_tool"
    assert unknown.result == {"error": "unknown_tool:mystery"}

    crashy = next(r for r in result.trace if r.tool_name == "crashy")
    assert crashy.error == "deliberate boom"
    assert "error" in crashy.result

    # Round 2 should carry both tool responses with the error payload.
    round2_messages = scripted.calls[1]["messages"]
    tool_msgs = [m for m in round2_messages if m["role"] == "tool"]
    assert len(tool_msgs) == 2
    assert json.loads(tool_msgs[0]["content"]) == {"error": "unknown_tool:mystery"}
    assert "error" in json.loads(tool_msgs[1]["content"])


def test_run_tool_loop_raises_on_empty_registry() -> None:
    async def _run() -> None:
        await run_tool_loop(
            FakeSession(),
            _settings_for_real_call(),
            base_request=_base_request(),
            registry=ToolRegistry(),
            max_rounds=1,
        )

    with pytest.raises(ValueError, match="at least one registered tool"):
        asyncio.run(_run())


def test_tool_spec_to_openai_schema_defaults_parameters() -> None:
    spec = ToolSpec(
        name="bare",
        description="no params",
        handler=_ok_handler,
    )
    schema = spec.to_openai_schema()
    assert schema["type"] == "function"
    assert schema["function"]["name"] == "bare"
    assert schema["function"]["parameters"] == {
        "type": "object",
        "properties": {},
    }


def test_tool_registry_rejects_duplicate_name() -> None:
    spec = ToolSpec(name="dup", description="x", handler=_ok_handler)
    registry = ToolRegistry([spec])
    with pytest.raises(ValueError, match="already registered"):
        registry.register(
            ToolSpec(name="dup", description="y", handler=_ok_handler)
        )
