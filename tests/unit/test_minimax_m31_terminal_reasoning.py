"""Spent terminal reasoning is transport evidence, never a planning grade."""

from copy import deepcopy
import json
import socket
from types import SimpleNamespace
from uuid import uuid4

from gateway_ledger_support import gateway_ledger  # noqa: F401 - shared SQLite fixture
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
from bestseller.services import planning_draft_admission as admission
from bestseller.services.budget import ExecutionBudgetError, current_execution_scope
from bestseller.settings import LLMRoleSettings, load_settings

pytestmark = pytest.mark.unit


def role():
    return LLMRoleSettings(
        model="openai/MiniMax-M3.1-Flash",
        temperature=0.2,
        max_tokens=8192,
        timeout_seconds=30,
        api_base="https://api.minimaxi.com/v1",
        api_key_env=None,
    )


def body(content=""):
    return {
        "id": "offline-terminal-reasoning",
        "object": "chat.completion",
        "created": 1,
        "model": "MiniMax-M3.1-Flash",
        "choices": [
            {
                "index": 0,
                "finish_reason": "length",
                "message": {
                    "role": "assistant",
                    "content": content,
                    "tool_calls": None,
                    "function_call": None,
                    "reasoning_content": "Compare the lighthouse logs. " * 120,
                    "unknown_provider_tree": {"preserve": None},
                },
            }
        ],
        "usage": {
            "prompt_tokens": 137,
            "completion_tokens": 6000,
            "total_tokens": 6137,
            "completion_tokens_details": {"reasoning_tokens": 6000},
        },
    }


def response(raw):
    return SimpleNamespace(
        model=raw["model"],
        usage=raw.get("usage"),
        choices=[SimpleNamespace(**choice) for choice in raw["choices"]],
    )


@pytest.mark.parametrize("content", ["", None])
def test_complete_length_reasoning_retains_entire_original_native_message(content):
    raw = body(content)
    before = deepcopy(raw)
    parsed = llm._parse_complete_provider_response(response(raw), role())
    assert parsed[:5] == ("", 137, 6000, "length", None)
    assert parsed[5] == before["choices"][0]["message"]
    assert raw == before
    parsed[5]["unknown_provider_tree"]["preserve"] = "detached"
    assert raw == before


@pytest.mark.parametrize("field", ["prompt_tokens", "completion_tokens"])
@pytest.mark.parametrize("value", [None, True, False, 0, -1, 1.5, "137"])
def test_known_terminal_requires_positive_nonbool_usage_without_coercion(field, value):
    raw = body()
    raw["usage"][field] = value
    with pytest.raises(ValueError):
        llm._parse_complete_provider_response(response(raw), role())


@pytest.mark.parametrize(
    "fault",
    [
        "stop",
        "unknown_finish",
        "no_usage",
        "missing_prompt",
        "missing_completion",
        "no_reasoning",
        "blank_reasoning",
        "bad_reasoning",
        "role",
        "legacy_function",
        "bad_tools",
        "filtered_tools",
        "conflicting_tools",
        "wrong_model",
        "other_role_model",
        "missing_model",
        "multiple_choices",
        "nontext_content",
        "whitespace_content",
    ],
)
def test_other_empty_or_malformed_returns_still_fail_closed(fault):
    raw = body()
    message = raw["choices"][0]["message"]
    selected = role()
    if fault == "stop":
        raw["choices"][0]["finish_reason"] = "stop"
    elif fault == "unknown_finish":
        raw["choices"][0]["finish_reason"] = None
    elif fault == "no_usage":
        raw["usage"] = None
    elif fault == "missing_prompt":
        raw["usage"].pop("prompt_tokens")
    elif fault == "missing_completion":
        raw["usage"].pop("completion_tokens")
    elif fault == "no_reasoning":
        message.pop("reasoning_content")
    elif fault == "blank_reasoning":
        message["reasoning_content"] = " \n"
    elif fault == "bad_reasoning":
        message["reasoning_content"] = ["not text"]
    elif fault == "role":
        message["role"] = "user"
    elif fault == "legacy_function":
        message["function_call"] = {"name": "legacy", "arguments": "{}"}
    elif fault == "bad_tools":
        message["tool_calls"] = {}
    elif fault == "filtered_tools":
        message["tool_calls"] = [{"id": "opaque", "function": {"arguments": "{}"}}]
    elif fault == "conflicting_tools":
        message["tool_calls"] = [
            {
                "id": "opaque",
                "type": "function",
                "function": {
                    "name": "submit_planning_admission",
                    "arguments": {"unexpected": True},
                },
            }
        ]
    elif fault == "wrong_model":
        raw["model"] = "MiniMax-M3"
    elif fault == "other_role_model":
        selected = selected.model_copy(update={"model": "openai/MiniMax-M3"})
    elif fault == "missing_model":
        raw["model"] = None
    elif fault == "multiple_choices":
        raw["choices"].append(deepcopy(raw["choices"][0]))
    elif fault == "nontext_content":
        message["content"] = []
    elif fault == "whitespace_content":
        message["content"] = " \n"
    supplied = response(raw)
    if fault == "multiple_choices":
        for choice in supplied.choices:
            choice.message = SimpleNamespace(**choice.message)
    with pytest.raises(ValueError):
        llm._parse_complete_provider_response(supplied, selected)


def test_existing_native_tool_return_and_nonempty_length_remain_unchanged():
    raw = body()
    raw["choices"][0]["finish_reason"] = "tool_calls"
    raw["choices"][0]["message"]["tool_calls"] = [
        {
            "id": "native",
            "type": "function",
            "function": {"name": "submit_planning_admission", "arguments": "{}"},
        }
    ]
    parsed = llm._parse_complete_provider_response(response(raw), role())
    assert parsed[3] == "tool_calls" and parsed[4] == raw["choices"][0]["message"]["tool_calls"]
    raw = body("retained partial answer")
    assert (
        llm._parse_complete_provider_response(response(raw), role())[0] == "retained partial answer"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault", [None, "stop", "missing_usage", "no_reasoning", "legacy_function"]
)
async def test_real_sdk_gateway_sqlite_spent_invalid_or_unknown_no_extra_send(
    gateway_ledger,  # noqa: F811 - imported SQLite fixture
    monkeypatch,
    tmp_path,
    fault,
):
    def no_network(*args, **kwargs):
        raise AssertionError("No PG/network/provider in terminal-reasoning regression")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    raw = body()
    if fault == "stop":
        raw["choices"][0]["finish_reason"] = "stop"
    elif fault == "missing_usage":
        raw["usage"] = None
    elif fault == "no_reasoning":
        raw["choices"][0]["message"]["reasoning_content"] = None
    elif fault == "legacy_function":
        raw["choices"][0]["message"]["function_call"] = {"name": "legacy", "arguments": "{}"}
    sends, sdk_messages = [], []

    async def physical_send(request):
        assert request.url.host == "api.minimaxi.com"
        wire = json.loads(request.content)
        assert wire["max_completion_tokens"] == 6000
        assert (
            wire["stream"] is False
            and wire["tool_choice"]["function"]["name"] == "submit_planning_admission"
        )
        sends.append(wire)
        return httpx.Response(200, json=raw)

    async with httpx.AsyncClient(transport=httpx.MockTransport(physical_send)) as http:
        sdk = AsyncOpenAI(
            api_key="offline-not-a-secret",
            base_url="https://api.minimaxi.com/v1",
            http_client=http,
            max_retries=0,
        )

        async def acompletion(**kwargs):
            allowed = {
                k: v
                for k, v in kwargs.items()
                if k
                not in {
                    "model",
                    "timeout",
                    "num_retries",
                    "max_retries",
                    "drop_params",
                    "api_base",
                    "api_key",
                    "base_url",
                    "allowed_openai_params",
                }
            }
            result = await sdk.chat.completions.create(model="MiniMax-M3.1-Flash", **allowed)
            sdk_messages.extend(
                choice.message.model_dump(mode="json", exclude_none=False)
                for choice in result.choices
            )
            return result

        monkeypatch.setattr(llm, "_get_litellm", lambda: SimpleNamespace(acompletion=acompletion))
        monkeypatch.setattr(llm, "_ensure_shared_litellm_http_client", lambda: None)
        settings = load_settings(env={})
        settings.llm.mock = False
        settings.llm.critic = role()
        settings.llm.retry.max_attempts = 3
        settings.llm.retry.rate_limit_fallback_enabled = False
        settings.artifact_store.mode = "local"
        settings.artifact_store.local_dir = str(tmp_path / "retained")
        settings.artifact_store.retain_prompt_payloads = True
        async with AsyncSession(gateway_ledger, expire_on_commit=False) as session:
            project = await session.scalar(
                select(ProjectModel).where(ProjectModel.slug == "owned-book")
            )
            req = llm.LLMCompletionRequest(
                project_id=project.id,
                workflow_run_id=uuid4(),
                logical_role="critic",
                system_prompt="Review the museum schedule.",
                user_prompt="Inspect the supplied lighthouse plan.",
                fallback_response="never a grade",
                prompt_template=admission.TEMPLATE,
                prompt_version=admission.SCHEMA,
                operation_id="terminal-reasoning-native-fixture",
                max_tokens_override=6000,
                tools=[
                    {
                        "type": "function",
                        "function": {
                            "name": "submit_planning_admission",
                            "parameters": {
                                "type": "object",
                                "properties": {},
                                "additionalProperties": False,
                            },
                        },
                    }
                ],
                tool_choice={"type": "function", "function": {"name": "submit_planning_admission"}},
            )
            if fault is not None:
                for _ in range(2):
                    with pytest.raises(ExecutionBudgetError, match="execution_result_unknown"):
                        await llm.complete_text(session, settings, req)
            else:
                with llm.bind_project_prompt_audit(
                    project.id, reason="SQLite terminal reasoning proof"
                ):
                    completion = await llm.complete_text(session, settings, req)
                await session.commit()
                assert completion.content == "" and completion.finish_reason == "length"
                assert completion.raw_message == sdk_messages[0]
                assert completion.tool_calls is None
                error = admission._protocol_error(completion.model_dump(mode="json"), {}, {})
                assert error["detail"] == "admission_incomplete_completion"
                replay = await llm.complete_text(session, settings, req)
                assert replay.raw_message == completion.raw_message
                assert replay.finish_reason == "length" and replay.content == ""
                await session.commit()
        assert len(sends) == 1
    async with AsyncSession(gateway_ledger) as session:
        attempt = (await session.scalars(select(ExecutionAttemptModel))).one()
        operation = await session.get(ExecutionOperationModel, attempt.operation_key)
        budget = await session.get(ExecutionBudgetModel, current_execution_scope())
        assert budget.calls_used == 1
        runs = list(await session.scalars(select(LlmRunModel)))
        if fault is None:
            assert attempt.status == operation.status == "completed"
            assert attempt.actual_tokens == budget.tokens_charged == 6137
            assert budget.status == "active"
            assert operation.result_json["raw_message"] == sdk_messages[0]
            assert operation.result_json["finish_reason"] == "length"
            assert len(runs) == 1 and runs[0].finish_reason == "length"
            assert (runs[0].input_tokens, runs[0].output_tokens) == (137, 6000)
            retained = json.loads(
                (tmp_path / "retained" / runs[0].response_payload_ref).read_text()
            )
            assert retained["raw_message"] == sdk_messages[0]
            assert retained["structured_payload_receipts"]["raw_message"]["truncated"] is False
            assert "draft_eligible" not in operation.result_json
        else:
            assert attempt.status == operation.status == budget.status == "unknown"
            assert (
                attempt.actual_tokens is None and budget.tokens_charged == attempt.reserved_tokens
            )
            assert not runs
