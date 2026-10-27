"""Real OpenAI SDK and ledger against an offline SSE wire; no paid calls."""
import asyncio
import json
from types import SimpleNamespace

import httpx
from openai import AsyncOpenAI
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.services import llm, production_strategy as strategy, minimax_m31_protocol as protocol
from bestseller.services.budget import ExecutionBudgetError
from bestseller.infra.db.models import ExecutionOperationModel, ExecutionAttemptModel
from bestseller.settings import load_settings, LLMRoleSettings
from gateway_ledger_support import gateway_ledger, LedgerBoundSession

pytestmark = [pytest.mark.unit, pytest.mark.filterwarnings(
    "ignore:Accessing the 'model_(computed_)?fields' attribute on the instance is deprecated.*:pydantic.warnings.PydanticDeprecatedSince211")]
# Installed LiteLLM inspects Pydantic Delta attributes. Scope its known upstream
# deprecation to these SDK integration fixtures; application errors stay fatal.


def packet(delta=None, finish=None, usage=None):
    return ("data: " + json.dumps({"id": "offline-stream", "object": "chat.completion.chunk",
        "created": 1, "model": "MiniMax-M3.1-Flash",
        "choices": [] if usage else [{"index": 0, "delta": delta or {}, "finish_reason": finish}],
        **({"usage": usage} if usage else {})}) + "\n\n").encode()


class Wire(httpx.AsyncByteStream):
    def __init__(self, interrupt):
        self.interrupt = interrupt

    async def __aiter__(self):
        yield packet({"role": "assistant", "reasoning_content": "retained reasoning"})
        yield packet({"content": "actual offline response"})
        if self.interrupt:
            raise httpx.ReadTimeout("offline synthetic stream interruption")
        yield packet(finish="stop")
        yield packet(usage={"prompt_tokens": 100, "completion_tokens": 30, "total_tokens": 130})
        yield b"data: [DONE]\n\n"


@pytest.mark.asyncio
@pytest.mark.parametrize("interrupt", [False, True])
@pytest.mark.parametrize("actual_litellm", [False, True])
async def test_sdk_stream_keeps_reasoning_usage_or_quarantined_partial(gateway_ledger, monkeypatch, interrupt, actual_litellm):
    seen = []

    async def wire(request):
        assert request.url.host == "api.minimaxi.com"
        body = json.loads(request.content)
        seen.append(body)
        assert body["stream"] is True and body["stream_options"]["include_usage"] is True
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=Wire(interrupt))

    async with httpx.AsyncClient(transport=httpx.MockTransport(wire)) as http:
        sdk = AsyncOpenAI(api_key="offline-fixture", base_url="https://api.minimaxi.com/v1", http_client=http, max_retries=0)

        async def acompletion(**kwargs):
            assert kwargs["num_retries"] == kwargs["max_retries"] == 0
            allowed = {k: v for k, v in kwargs.items() if k not in {
                "model", "timeout", "num_retries", "max_retries", "drop_params", "api_base", "api_key", "base_url", "allowed_openai_params"}}
            return await sdk.chat.completions.create(model="MiniMax-M3.1-Flash", **allowed)

        if actual_litellm:
            monkeypatch.setenv("LITELLM_LOCAL_MODEL_COST_MAP", "True")
            import litellm
            from litellm.caching.llm_caching_handler import LLMClientCache

            # Each fixture owns a short-lived HTTP client. SDK clients cached
            # by a prior event-loop id may otherwise retain its closed client.
            monkeypatch.setattr(litellm, "in_memory_llm_clients_cache", LLMClientCache())
            monkeypatch.setattr(litellm, "telemetry", False)
            monkeypatch.setattr(litellm, "aclient_session", http)
            monkeypatch.setattr(llm, "_get_litellm", lambda: litellm)
            monkeypatch.setattr(llm, "get_runtime_env_value", lambda key: "offline-fixture" if key == "TEST_STREAM_KEY" else None)
        else:
            monkeypatch.setattr(llm, "_get_litellm", lambda: SimpleNamespace(acompletion=acompletion))
        monkeypatch.setattr(llm, "_ensure_shared_litellm_http_client", lambda: None)
        settings = load_settings(env={})
        settings.llm.mock = False
        settings.llm.critic = LLMRoleSettings(model="openai/MiniMax-M3.1-Flash", temperature=1,
            max_tokens=6500, timeout_seconds=30, api_base="https://api.minimaxi.com/v1", api_key_env="TEST_STREAM_KEY")
        settings.llm.retry.max_attempts = 1
        settings.llm.retry.rate_limit_fallback_enabled = False
        request = llm.LLMCompletionRequest(logical_role="critic", system_prompt="Synthetic fixture.",
            user_prompt="A supplied story.", fallback_response="not an approval", prompt_template="conception_review")
        # New complete-mode requests opt into documented plain-text streaming;
        # default role configuration and legacy envelopes remain unchanged.
        async with AsyncSession(gateway_ledger, expire_on_commit=False) as session:
          with strategy.protect_downstream("complete", 4), llm.bind_execution_operation("offline-conception-resume"):
              if interrupt:
                  with pytest.raises(ExecutionBudgetError, match="execution_result_unknown"):
                      await llm.complete_text(session, settings, request)
                  with pytest.raises(ExecutionBudgetError):
                      await llm.complete_text(session, settings, request)
              else:
                  result = await llm.complete_text(session, settings, request)
                  assert result.content == "actual offline response"
                  assert result.raw_message["reasoning_content"] == "retained reasoning"
                  assert (result.input_tokens, result.output_tokens, result.finish_reason) == (100, 30, "stop")
                  reused = await llm.complete_text(session, settings, request)
                  assert reused.content == result.content
    assert len(seen) == 1
    async with AsyncSession(gateway_ledger) as session:
        attempts = list(await session.scalars(select(ExecutionAttemptModel)))
        assert len(attempts) == 1
        if interrupt:
            assert attempts[0].status == "unknown" and attempts[0].actual_tokens is None
            partial = await session.scalar(select(ExecutionOperationModel).where(ExecutionOperationModel.status == "unusable_provider_return"))
            assert partial.result_json["adoption_authorized"] is False
            body = partial.result_json["response"]
            assert body["stream_complete"] is False
            assert body["choices"][0]["message"]["content"] == "actual offline response"
            assert body["choices"][0]["finish_reason"] is None


@pytest.mark.asyncio
async def test_wall_timeout_partial_is_never_promoted():
    async def slow():
        yield {"id": "partial", "choices": [{"delta": {"reasoning_content": "thinking"}}]}
        await asyncio.Event().wait()
    with pytest.raises(TimeoutError) as raised:
        await asyncio.wait_for(llm._collect_minimax_streaming_content(slow()), timeout=.02)
    partial = raised.value.__cause__.partial_stream_response
    assert partial["stream_complete"] is False and partial["choices"][0]["finish_reason"] is None


def test_tool_messages_keep_complete_path_even_with_streaming_role():
    role = LLMRoleSettings(model="openai/MiniMax-M3.1-Flash", temperature=1, max_tokens=6500, timeout_seconds=30, stream=True)
    kwargs = protocol.wire_options({"max_tokens": 6500, "stream": True, "tools": [{"type": "function"}]}, role)
    assert kwargs["stream"] is False and "stream_options" not in kwargs
