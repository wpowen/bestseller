from __future__ import annotations

import asyncio
import json
from pathlib import Path
from uuid import uuid4

import pytest

from bestseller.infra.db.models import LlmRunModel
import bestseller.services.llm as llm
from bestseller.services.llm import (
    LLMCompletionRequest,
    bind_conception_model,
    complete_text,
)
from bestseller.settings import load_settings

pytestmark = pytest.mark.unit

from gateway_ledger_support import (
    gateway_ledger as gateway_ledger, LedgerBoundSession,
)


@pytest.fixture(autouse=True)
def _isolated_ledger_contract(gateway_ledger):
    """Every real-mode fake SDK call still reserves against durable SQLite."""
    return gateway_ledger


class _Session(LedgerBoundSession):
    def __init__(self) -> None:
        self.added: list[object] = []

    def add(self, obj: object) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        for obj in self.added:
            if isinstance(obj, LlmRunModel) and obj.id is None:
                obj.id = uuid4()

    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None

    def in_nested_transaction(self) -> bool:
        return False


def _settings(tmp_path: Path, *, retain: bool, mock: bool = True):
    base = load_settings(env={})
    return base.model_copy(
        update={
            "llm": base.llm.model_copy(update={"mock": mock}),
            "artifact_store": base.artifact_store.model_copy(
                update={"retain_prompt_payloads": retain, "local_dir": str(tmp_path)}
            ),
        }
    )


def _request(workflow_id):
    return LLMCompletionRequest(
        logical_role="planner",
        prompt_template="concept_test",
        prompt_version="v7",
        system_prompt="system prompt",
        user_prompt="user prompt",
        fallback_response="fallback response",
        workflow_run_id=workflow_id,
        metadata={"api_key": "must-not-be-copied"},
        temperature_override=0.31,
        max_tokens_override=1234,
    )


@pytest.mark.asyncio
async def test_retained_conception_payloads_are_atomic_and_joinable(tmp_path: Path) -> None:
    workflow_id = uuid4()
    session = _Session()
    result = await complete_text(session, _settings(tmp_path, retain=True), _request(workflow_id))

    run = next(item for item in session.added if isinstance(item, LlmRunModel))
    assert run.request_payload_ref and run.response_payload_ref
    request_path = tmp_path / run.request_payload_ref
    response_path = tmp_path / run.response_payload_ref
    assert request_path.is_file() and response_path.is_file()
    assert not list(tmp_path.rglob(".*.request.json.*"))

    request_payload = json.loads(request_path.read_text(encoding="utf-8"))
    response_payload = json.loads(response_path.read_text(encoding="utf-8"))
    assert request_payload["workflow_run_id"] == str(workflow_id)
    assert request_payload["prompt_version"] == "v7"
    assert request_payload["requested"]["temperature_override"] == 0.31
    assert request_payload["effective"]["max_tokens"] == 1234
    assert request_payload["messages"]["system"]["text"] == "system prompt"
    assert request_payload["replayable"] is True
    assert response_payload["replayable"] is True
    assert response_payload["content"]["text"] == result.content
    assert "must-not-be-copied" not in request_path.read_text(encoding="utf-8")
    assert run.metadata_json["prompt_payload_receipt"]["status"] == "stored"
    assert run.metadata_json["prompt_payload_receipt"]["replayable"] is True


@pytest.mark.asyncio
async def test_true_gateway_receipt_records_effective_model_and_fallback(
    monkeypatch, tmp_path: Path
) -> None:
    seen = {}

    async def fake_gateway(*_args, **_kwargs):
        seen["role_settings"] = _args[1]
        return (
            "gateway content",
            11,
            22,
            "stop",
            None,
            {"role": "assistant", "content": "gateway content"},
        )

    monkeypatch.setattr(llm, "_call_litellm", fake_gateway)
    settings = _settings(tmp_path, retain=True, mock=False)
    session = _Session()
    result = await complete_text(session, settings, _request(uuid4()))
    run = next(item for item in session.added if isinstance(item, LlmRunModel))
    payload = json.loads((tmp_path / run.response_payload_ref).read_text(encoding="utf-8"))
    assert result.content == "gateway content"
    assert payload["finish_reason"] == "stop"
    assert payload["fallback_used"] is False
    request_payload = json.loads((tmp_path / run.request_payload_ref).read_text(encoding="utf-8"))
    assert request_payload["effective"]["model"] == run.model_name
    assert request_payload["effective"]["provider"] == run.provider
    effective = request_payload["effective"]
    role_settings = seen["role_settings"]
    assert effective["temperature"] == 0.31
    assert effective["max_tokens"] == 1234
    assert effective["timeout_seconds"] == role_settings.timeout_seconds
    assert effective["stream"] == role_settings.stream
    from bestseller.infra.db.models import ExecutionBudgetModel
    from bestseller.services.budget import current_execution_scope
    budget = await session.get(ExecutionBudgetModel, current_execution_scope())
    assert budget.calls_used == 1 and budget.tokens_charged == 33


@pytest.mark.asyncio
async def test_concurrent_workflows_get_distinct_receipts(tmp_path: Path) -> None:
    settings = _settings(tmp_path, retain=True)

    async def one() -> tuple[str, str]:
        session = _Session()
        workflow_id = uuid4()
        with bind_conception_model(None, workflow_run_id=workflow_id):
            await complete_text(
                session,
                settings,
                LLMCompletionRequest(
                    logical_role="critic",
                    system_prompt="s",
                    user_prompt=str(workflow_id),
                    fallback_response="f",
                ),
            )
        run = next(item for item in session.added if isinstance(item, LlmRunModel))
        payload = json.loads((tmp_path / run.request_payload_ref).read_text(encoding="utf-8"))
        return str(workflow_id), payload["workflow_run_id"]

    pairs = await asyncio.gather(one(), one())
    assert pairs[0][0] == pairs[0][1]
    assert pairs[1][0] == pairs[1][1]
    assert pairs[0][0] != pairs[1][0]
    assert len(list((tmp_path / "runtime" / "llm_payloads").glob("*.request.json"))) == 2


@pytest.mark.asyncio
async def test_retention_off_does_not_write_payloads(tmp_path: Path) -> None:
    session = _Session()
    result = await complete_text(session, _settings(tmp_path, retain=False), _request(uuid4()))
    run = next(item for item in session.added if isinstance(item, LlmRunModel))
    assert run.request_payload_ref is None
    assert run.response_payload_ref is None
    assert not list(tmp_path.rglob("*.json"))
    assert result.metadata["prompt_payload_receipt"] == {
        "enabled": False,
        "status": "disabled",
        "replayable": False,
        "reason": "disabled",
    }


@pytest.mark.asyncio
async def test_payload_write_failure_is_explicit_but_non_fatal(
    monkeypatch, tmp_path: Path
) -> None:
    def fail_write(*_args, **_kwargs):
        raise OSError("permission denied: secret path")

    monkeypatch.setattr(llm, "_write_json_atomically", fail_write)
    session = _Session()
    result = await complete_text(session, _settings(tmp_path, retain=True), _request(uuid4()))
    run = next(item for item in session.added if isinstance(item, LlmRunModel))
    assert result.content
    assert run.request_payload_ref is None
    assert run.response_payload_ref is None
    assert result.metadata["prompt_payload_receipt"] == {
        "enabled": True,
        "status": "missing",
        "replayable": False,
        "reason": "write_failed",
    }


@pytest.mark.asyncio
async def test_override_stays_unavailable_while_tool_schema_is_retained(tmp_path: Path) -> None:
    settings = _settings(tmp_path, retain=True)
    session = _Session()
    request = _request(uuid4()).model_copy(
        update={
            "messages_override": [{"role": "system", "content": "private override"}],
            "tools": [{"type": "function", "function": {"name": "lookup"}}],
            "tool_choice": "auto",
        }
    )
    await complete_text(session, settings, request)
    run = next(item for item in session.added if isinstance(item, LlmRunModel))
    payload = json.loads((tmp_path / run.request_payload_ref).read_text(encoding="utf-8"))
    assert payload["replayable"] is False
    assert set(payload["replayability_reasons"]) == {
        "messages_override_not_retained",
    }
    assert payload["requested"]["tools"] == request.tools
    assert payload["requested"]["tool_choice"] == request.tool_choice
    assert payload["messages"] == {
        "source": "messages_override",
        "replayable": False,
        "reason": "messages_override_not_retained",
    }
    assert "private override" not in json.dumps(payload, ensure_ascii=False)
    assert run.metadata_json["prompt_payload_receipt"]["replayable"] is False


@pytest.mark.asyncio
async def test_prompt_truncation_marks_receipt_not_replayable(tmp_path: Path) -> None:
    settings = _settings(tmp_path, retain=True)
    session = _Session()
    request = _request(uuid4()).model_copy(update={"system_prompt": "x" * 120_001})
    await complete_text(session, settings, request)
    run = next(item for item in session.added if isinstance(item, LlmRunModel))
    payload = json.loads((tmp_path / run.request_payload_ref).read_text(encoding="utf-8"))
    assert payload["replayable"] is False
    assert "prompt_truncated" in payload["replayability_reasons"]
    assert payload["messages"]["system"]["truncated"] is True
    assert run.metadata_json["prompt_payload_receipt"]["replayable"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "content,tool_call_count,finish_reason",
    [
        ("plain response", 0, "stop"),
        ("text despite finish label", 0, "tool_calls"),
        ("mixed response", 1, "stop"),
        ("", 2, "tool_calls"),
        ("x" * 120_001, 0, "length"),
        ("x" * 120_001, 1, "length"),
    ],
    ids=["text", "signal-not-finish-label", "mixed", "pure-tools", "truncated", "both-reasons"],
)
async def test_full_response_receipt_tracks_actual_tool_calls(
    monkeypatch, tmp_path: Path, content: str, tool_call_count: int, finish_reason: str,
) -> None:
    from sqlalchemy import select

    from bestseller.infra.db.models import ExecutionBudgetModel, ExecutionOperationModel
    from bestseller.services.budget import current_execution_scope

    calls = [
        {"id": f"call-{index}", "type": "function", "function": {
            "name": "lookup", "arguments": json.dumps({"query": "retained-tool-argument"}),
        }}
        for index in range(tool_call_count)
    ]

    async def fake_gateway(*_args, **_kwargs):
        # Match a native SDK dump's nullable fields to the normalized return;
        # an empty tool signal must not conflict with tool_calls=None.
        return (
            content, 11, 22, finish_reason, calls or None,
            {"role": "assistant", "content": content or None, "tool_calls": calls or None},
        )

    monkeypatch.setattr(llm, "_call_litellm", fake_gateway)
    old_path = tmp_path / "runtime" / "llm_payloads" / f"{uuid4()}.response.json"
    old_path.parent.mkdir(parents=True)
    old_bytes = b'{"schema_version":"llm-payload.v1","replayable":true}'
    old_path.write_bytes(old_bytes)
    session = _Session()
    result = await complete_text(
        session, _settings(tmp_path, retain=True, mock=False), _request(uuid4())
    )
    run = next(item for item in session.added if isinstance(item, LlmRunModel))
    response_text = (tmp_path / run.response_payload_ref).read_text(encoding="utf-8")
    response = json.loads(response_text)
    request = json.loads((tmp_path / run.request_payload_ref).read_text(encoding="utf-8"))
    expected_reasons = (["response_truncated", "raw_message_truncated"]
                        if len(content) > 120_000 else [])
    assert request["replayable"] is True  # The response signal must decide independently.
    assert response["tool_call_count"] == tool_call_count
    assert response["replayability_reasons"] == expected_reasons
    assert response["replayable"] is (not expected_reasons)
    assert run.metadata_json["prompt_payload_receipt"]["replayable"] is (not expected_reasons)
    assert ("retained-tool-argument" in response_text) is bool(tool_call_count)
    assert response["tool_calls"] == (calls or None)
    if not expected_reasons:
        assert response["raw_message"] == result.raw_message
    assert result.tool_calls == (calls or None)
    assert old_path.read_bytes() == old_bytes

    # Receipt truth must not alter the usable paid result or charge another call.
    operation = await session.scalar(select(ExecutionOperationModel))
    assert operation.result_json["tool_calls"] == (calls or None)
    assert operation.result_json["raw_message"]["content"] == (content or None)
    if calls:
        assert operation.result_json["raw_message"]["tool_calls"] == calls
    budget = await session.get(ExecutionBudgetModel, current_execution_scope())
    assert budget.calls_used == 1 and budget.tokens_charged == 33
