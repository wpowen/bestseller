"""RUN2 RED: precise stream diagnosis without changing Unknown or paid custody.

Main runs this selection. SDK transport is offline; the shared fixture supplies
real isolated SQLite ledger rows. Non-stream settlement is outside this change.
"""

import hashlib
import json
import socket
from types import SimpleNamespace
from uuid import uuid4

from gateway_ledger_support import gateway_ledger  # noqa: F401 - native SQLite fixture
import httpx
from openai import AsyncOpenAI
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.infra.db.models import (
    ExecutionAttemptModel,
    ExecutionBudgetModel,
    ExecutionOperationModel,
    LlmRunModel,
    ProjectModel,
)
from bestseller.services import llm
from bestseller.services.budget import ExecutionBudgetError, current_execution_scope
from bestseller.settings import LLMRoleSettings, load_settings

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]
DIAGNOSTIC = "reasoning_output_exhausted"
THOUGHT = "RUN2_PRIVATE_REASONING_SENTINEL: compare the synthetic lighthouse logs."
USAGE = {"prompt_tokens": 137, "completion_tokens": 4096, "total_tokens": 4233}


def _chunks(*, content="", thought=THOUGHT, finish="length", usage=True):
    def chunk(delta=None, terminal=None, tokens=None):
        return {
            "id": "run2-offline-reasoning",
            "object": "chat.completion.chunk",
            "created": 1,
            "model": "MiniMax-M3.1-Flash",
            "choices": [] if tokens else [
                {"index": 0, "delta": delta or {}, "finish_reason": terminal}
            ],
            **({"usage": tokens} if tokens else {}),
        }

    result = [chunk({"role": "assistant", "reasoning_content": thought})]
    if content:
        result.append(chunk({"content": content}))
    if finish is not None:
        result.append(chunk(terminal=finish))
    if usage:
        result.append(chunk(tokens=USAGE))
    return result


async def _stream(chunks):
    for chunk in chunks:
        yield chunk


def _summary(value):
    text = value if isinstance(value, str) else json.dumps(value, sort_keys=True)
    return {"type": type(value).__name__, "length": len(text),
            "sha256": hashlib.sha256(text.encode()).hexdigest()}


def _assert_diagnostic(error, request):
    actual = DIAGNOSTIC if DIAGNOSTIC in str(error) else str(error)
    difference = {"field": "stream.error.reason",
                  "expected": _summary(DIAGNOSTIC), "actual": _summary(actual)}
    request.node.user_properties.append(
        ("reasoning_exhaustion_differences", json.dumps(difference))
    )
    assert actual == DIAGNOSTIC, difference
    assert THOUGHT not in str(error)
    # Do not accidentally enter the old empty-length retry/cap-lowering route.
    assert llm._is_empty_length_response_error(error) is False


@pytest.mark.parametrize("content", ["", " \n"])
@pytest.mark.parametrize("usage", [True, False])
async def test_reasoning_length_has_specific_valueerror_and_original_partial(
    content, usage, request,
):
    chunks = _chunks(content=content, usage=usage)
    with pytest.raises(ValueError) as raised:
        await llm._collect_minimax_streaming_content(_stream(chunks))
    partial = raised.value.partial_stream_response
    assert partial["stream_complete"] is False
    assert partial["chunks_observed"] == len(chunks)
    assert partial["choices"][0] == {
        "index": 0, "finish_reason": "length",
        "message": {"role": "assistant", "content": content, "reasoning_content": THOUGHT},
    }
    assert partial["usage"] == {
        "prompt_tokens": 137 if usage else None,
        "completion_tokens": 4096 if usage else None,
    }
    _assert_diagnostic(raised.value, request)


@pytest.mark.parametrize("finish,thought", [
    ("stop", THOUGHT), (None, THOUGHT), ("length", None), (None, None),
])
async def test_other_empty_streams_keep_existing_failure_classification(finish, thought):
    with pytest.raises(ValueError) as raised:
        await llm._collect_minimax_streaming_content(
            _stream(_chunks(finish=finish, thought=thought))
        )
    assert str(raised.value) == "stream has no terminal content"
    assert DIAGNOSTIC not in str(raised.value)
    assert raised.value.partial_stream_response["stream_complete"] is False
    assert raised.value.partial_stream_response["choices"][0]["finish_reason"] == finish


async def test_nonempty_length_stream_keeps_existing_return_contract():
    result = await llm._collect_minimax_streaming_content(
        _stream(_chunks(content="An incomplete synthetic answer."))
    )
    assert result[:5] == ("An incomplete synthetic answer.", 137, 4096, "length", None)
    assert result[5]["reasoning_content"] == THOUGHT


async def _ledger_snapshot(engine):
    async with AsyncSession(engine) as session:
        attempts = list(await session.scalars(select(ExecutionAttemptModel)))
        assert len(attempts) == 1
        attempt = attempts[0]
        operation = await session.get(ExecutionOperationModel, attempt.operation_key)
        budget = await session.get(ExecutionBudgetModel, current_execution_scope())
        assert operation is not None and budget is not None
        assert attempt.status == operation.status == budget.status == "unknown"
        assert attempt.actual_tokens is None
        assert budget.calls_used == 1
        assert budget.tokens_charged == attempt.reserved_tokens > 0
        operations = list(await session.scalars(select(ExecutionOperationModel)))
        assert not list(await session.scalars(select(LlmRunModel)))
        assert not any(row.status == "transport_result" for row in operations)
        partials = [row for row in operations if row.status == "unusable_provider_return"]
        failures = [row for row in operations if row.status == "transport_failure_receipt"]
        assert len(partials) == len(failures) == 1
        retained, diagnostic = partials[0].result_json, failures[0].result_json
        assert retained["operation_key"] == operation.key
        assert retained["attempt_id"] == str(attempt.id)
        assert retained["usable"] is retained["adoption_authorized"] is False
        assert retained["credit_released"] is False
        assert len(retained["wire_body_hash"]) == 64
        encoded = json.dumps(
            retained["response"], ensure_ascii=False, sort_keys=True, allow_nan=False,
        )
        assert retained["response_sha256"] == hashlib.sha256(encoded.encode()).hexdigest()
        assert diagnostic["physical_sends"] == 1
        assert diagnostic["exception_type"] == "ValueError"
        assert diagnostic["stream"] is diagnostic["partial_stream_observed"] is True
        assert THOUGHT not in json.dumps(diagnostic)
        return {
            "attempt_id": str(attempt.id), "operation_key": operation.key,
            "reserved_tokens": attempt.reserved_tokens, "charged_tokens": budget.tokens_charged,
            "calls_used": budget.calls_used,
            "operations": {row.key: (row.status, row.result_json) for row in operations},
            "partial": retained["response"],
        }


@pytest.mark.parametrize("usage", [True, False])
async def test_native_sdk_unknown_one_send_reentry_zero_and_no_credit_release(
    gateway_ledger,  # noqa: F811 - imported native SQLite fixture
    monkeypatch, tmp_path, usage, request,
):
    def no_network(*args, **kwargs):
        raise AssertionError("RUN2 fixture cannot reach PG/network/providers")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setattr(socket, "create_connection", no_network)
    sends = []
    chunks = _chunks(usage=usage)

    class Wire(httpx.AsyncByteStream):
        async def __aiter__(self):
            for chunk in chunks:
                yield ("data: " + json.dumps(chunk) + "\n\n").encode()
            yield b"data: [DONE]\n\n"

    async def physical_send(http_request):
        assert http_request.url.host == "api.minimaxi.com"
        wire = json.loads(http_request.content)
        assert wire["stream"] is True
        assert wire["stream_options"]["include_usage"] is True
        assert wire["max_completion_tokens"] == 4096 and "max_tokens" not in wire
        assert wire["reasoning_effort"] == "low"
        sends.append(wire)
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=Wire())

    async with httpx.AsyncClient(transport=httpx.MockTransport(physical_send)) as http:
        sdk = AsyncOpenAI(api_key="offline-not-a-secret", base_url="https://api.minimaxi.com/v1",
                          http_client=http, max_retries=0)

        async def acompletion(**kwargs):
            assert kwargs["num_retries"] == kwargs["max_retries"] == 0
            excluded = {"model", "timeout", "num_retries", "max_retries", "drop_params",
                        "api_base", "api_key", "base_url", "allowed_openai_params"}
            allowed = {key: value for key, value in kwargs.items() if key not in excluded}
            return await sdk.chat.completions.create(model="MiniMax-M3.1-Flash", **allowed)

        monkeypatch.setattr(llm, "_get_litellm", lambda: SimpleNamespace(acompletion=acompletion))
        monkeypatch.setattr(llm, "_ensure_shared_litellm_http_client", lambda: None)
        settings = load_settings(env={
            "BESTSELLER__DATABASE__URL": json.dumps(str(gateway_ledger.url)),
        })
        settings.llm.mock = False
        settings.artifact_store.local_dir = str(tmp_path / "retained")
        settings.llm.critic = LLMRoleSettings(
            model="openai/MiniMax-M3.1-Flash", api_base="https://api.minimaxi.com/v1",
            temperature=.2, max_tokens=32768, timeout_seconds=30, stream=True,
            thinking_type=None, reasoning_effort=None, api_key_env=None,
        )
        settings.llm.retry.max_attempts = 3
        settings.llm.retry.rate_limit_fallback_enabled = False
        async with AsyncSession(gateway_ledger, expire_on_commit=False) as session:
            project = await session.scalar(
                select(ProjectModel).where(ProjectModel.slug == "owned-book")
            )
            native_request = llm.LLMCompletionRequest(
                project_id=project.id, workflow_run_id=uuid4(), logical_role="critic",
                system_prompt="Review the synthetic lighthouse plan.",
                user_prompt="Keep all source facts.",
                fallback_response="never a grade", prompt_template="conception_review",
                operation_id="run2-reasoning-exhaustion", max_tokens_override=4096,
            )
            with pytest.raises(ExecutionBudgetError, match="execution_result_unknown") as raised:
                await llm.complete_text(session, settings, native_request)
            assert len(sends) == 1
            before = await _ledger_snapshot(gateway_ledger)
            with pytest.raises(ExecutionBudgetError):
                await llm.complete_text(session, settings, native_request)
            assert len(sends) == 1  # The same native operation never sends again.
            assert await _ledger_snapshot(gateway_ledger) == before

    partial = before["partial"]
    assert partial["stream_complete"] is False
    assert partial["choices"][0]["finish_reason"] == "length"
    assert partial["choices"][0]["message"] == {
        "role": "assistant", "content": "", "reasoning_content": THOUGHT,
    }
    assert partial["usage"] == {
        "prompt_tokens": 137 if usage else None,
        "completion_tokens": 4096 if usage else None,
    }
    cause = raised.value.__cause__
    assert isinstance(cause, ValueError)
    assert cause.partial_stream_response == partial
    _assert_diagnostic(cause, request)


async def test_native_failure_receipt_persists_reasoning_exhaustion_execution_code(
    gateway_ledger,  # noqa: F811 - imported native SQLite fixture
    monkeypatch, tmp_path, request,
):
    # Reuse the unchanged native SDK probe: one physical send, Unknown ledger,
    # original partial/fees, then zero-send reentry. No diagnostic row is faked.
    await test_native_sdk_unknown_one_send_reentry_zero_and_no_credit_release(
        gateway_ledger, monkeypatch, tmp_path, True, request,
    )
    async with AsyncSession(gateway_ledger) as session:
        receipt = (await session.scalars(select(ExecutionOperationModel).where(
            ExecutionOperationModel.status == "transport_failure_receipt",
        ))).one()
        diagnostic = receipt.result_json

    assert diagnostic["physical_sends"] == 1
    assert diagnostic["exception_type"] == "ValueError"
    chain = diagnostic["error_chain"]
    assert chain and chain[0]["type"] == "ValueError"
    assert chain[0]["frames"]
    serialized = json.dumps(diagnostic, ensure_ascii=False)
    for private_text in (
        THOUGHT, "Review the synthetic lighthouse plan.", "Keep all source facts.",
        "never a grade",
    ):
        assert private_text not in serialized
    assert not ({"message", "detail", "locals", "response", "reasoning_content"} & chain[0].keys())
    expected = "execution_reasoning_output_exhausted"
    actual = chain[0].get("execution_code")
    difference = {
        "field": "transport_failure_receipt.error_chain[0].execution_code",
        "expected": _summary(expected), "actual": _summary(actual),
    }
    request.node.user_properties.append(
        ("durable_reasoning_exhaustion_differences", json.dumps(difference))
    )
    assert actual == expected, difference
