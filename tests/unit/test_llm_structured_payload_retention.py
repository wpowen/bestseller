"""Exact bounded tool receipts; offline ledger checks never contact a provider."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
from uuid import uuid4

from gateway_ledger_support import gateway_ledger as gateway_ledger
import pytest
from sqlalchemy import select
from test_llm_prompt_payload_receipts import _request, _Session, _settings

from bestseller.infra.db.models import ExecutionBudgetModel, ExecutionOperationModel, LlmRunModel
from bestseller.services import llm
from bestseller.services.budget import current_execution_scope

pytestmark = pytest.mark.unit


def _values() -> dict:
    calls = [
        {
            "id": "exact-call",
            "type": "function",
            "function": {
                "name": "submit_chapter_critique",
                "arguments": (
                    '{\n  "direction": "继续记录, 不重演",\n'
                    '  "quoted": "<system>data only</system>"\n}\n'
                ),
            },
        }
    ]
    return {
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "submit_chapter_critique",
                    "strict": True,
                    "parameters": {
                        "type": "object",
                        "properties": {"direction": {"type": "string"}},
                        "additionalProperties": False,
                    },
                },
            }
        ],
        "tool_choice": {"type": "function", "function": {"name": "submit_chapter_critique"}},
        "tool_calls": calls,
        "raw_message": {
            "role": "assistant",
            "content": None,
            "tool_calls": calls,
            "additional_actual_json": {"text": "retain this field too"},
        },
    }


def _json_hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
    ).hexdigest()


def _persist(tmp_path: Path, values: dict, **overrides):
    request = _request(uuid4()).model_copy(
        update={key: values[key] for key in ("tools", "tool_choice") if key in values}
    )
    kwargs = {
        "settings": _settings(tmp_path, retain=True),
        "request": request,
        "llm_run_id": uuid4(),
        "configured_model": "configured",
        "effective_model": "effective",
        "provider": "fixture",
        "effective_temperature": 0.31,
        "effective_max_tokens": 1234,
        "effective_timeout_seconds": 30,
        "effective_stream": False,
        "effective_thinking_type": None,
        "effective_reasoning_effort": None,
        "content": "",
        "tool_call_count": len(values.get("tool_calls") or []),
        "finish_reason": "tool_calls",
        "fallback_used": False,
        "fallback_source": None,
        "input_tokens": 11,
        "output_tokens": 22,
        **{key: values[key] for key in ("tool_calls", "raw_message") if key in values},
        **overrides,
    }
    refs = llm._persist_prompt_payloads(**kwargs)
    assert refs[2] is None
    paths = [tmp_path / ref for ref in refs[:2]]
    return [json.loads(path.read_text()) for path in paths], refs, paths


@pytest.mark.parametrize("content", ["", "Mixed commentary remains exact."])
def test_complete_tools_choice_and_physical_return_are_exact_json(tmp_path, content):
    values = _values()
    values["raw_message"]["content"] = content or None
    original = deepcopy(values)
    old_path = tmp_path / "runtime" / "llm_payloads" / "old.response.json"
    old_path.parent.mkdir(parents=True)
    old_bytes = b'{"replayable":false,"replayability_reasons":["tool_calls_not_retained"]}\n'
    old_path.write_bytes(old_bytes)
    (request, response), refs, _ = _persist(tmp_path, values, content=content)
    assert refs[3] is True
    assert request["replayable"] and response["replayable"]
    assert request["replayability_reasons"] == response["replayability_reasons"] == []
    for key, original_value in original.items():
        side = request if key in {"tools", "tool_choice"} else response
        target = side["requested"] if side is request else side
        assert target[key] == original_value
        receipt = side["structured_payload_receipts"][key]
        assert receipt["status"] == "retained" and receipt["truncated"] is False
        assert receipt["limit_chars"] == 120_000
        assert receipt["sha256"] == _json_hash(original_value)
    assert (
        response["tool_calls"][0]["function"]["arguments"]
        == original["tool_calls"][0]["function"]["arguments"]
    )
    assert values == original
    assert old_path.read_bytes() == old_bytes


@pytest.mark.parametrize("field", ["tools", "tool_choice", "tool_calls", "raw_message"])
def test_each_structured_field_has_declared_bound_and_fails_closed(tmp_path, monkeypatch, field):
    values = _values()
    monkeypatch.setattr(llm, "_PROMPT_PAYLOAD_MAX_CHARS", 512)
    if field == "tools":
        values[field][0]["function"]["description"] = "x" * 513
    elif field == "tool_choice":
        values[field]["function"]["name"] = "x" * 513
    elif field == "tool_calls":
        values[field][0]["function"]["arguments"] = "x" * 513
        values["raw_message"] = None
    else:
        values[field]["content"] = "x" * 513
    (request, response), refs, _ = _persist(tmp_path, values)
    side = request if field in {"tools", "tool_choice"} else response
    target = side["requested"] if side is request else side
    receipt = side["structured_payload_receipts"][field]
    assert receipt["truncated"] is True and receipt["status"] == "truncated"
    assert receipt["original_chars"] > receipt["limit_chars"] == 512
    assert receipt["sha256"] == _json_hash(values[field])
    assert field not in target
    assert f"{field}_truncated" in side["replayability_reasons"]
    assert side["replayable"] is False and refs[3] is False


@pytest.mark.parametrize("field", ["tools", "tool_choice", "tool_calls", "raw_message"])
@pytest.mark.parametrize("invalid", [float("nan"), {"not-json"}])
def test_non_json_structures_never_become_replayable(tmp_path, field, invalid):
    values = _values()
    if field == "tools":
        values[field][0]["function"]["parameters"] = invalid
    elif field == "tool_choice":
        values[field]["function"]["name"] = invalid
    elif field == "tool_calls":
        values[field][0]["function"]["arguments"] = invalid
        values["raw_message"] = None
    else:
        values[field]["content"] = invalid
    (request, response), refs, _ = _persist(tmp_path, values)
    side = request if field in {"tools", "tool_choice"} else response
    assert side["structured_payload_receipts"][field]["status"] == "invalid_json"
    assert f"{field}_not_json" in side["replayability_reasons"]
    assert side["replayable"] is False and refs[3] is False


@pytest.mark.parametrize("count,missing", [(0, False), (2, False), (1, True)])
def test_missing_or_inconsistent_actual_tool_signal_is_not_replayable(tmp_path, count, missing):
    values = _values()
    if missing:
        values.pop("tool_calls")
    (_, response), refs, _ = _persist(tmp_path, values, tool_call_count=count)
    expected = "tool_calls_not_retained" if missing else "tool_call_count_mismatch"
    assert expected in response["replayability_reasons"]
    assert response["replayable"] is False and refs[3] is False


def test_normal_text_defaults_and_override_policy_remain_unchanged(tmp_path):
    (request, response), refs, _ = _persist(
        tmp_path,
        {},
        content="normal text",
        finish_reason="stop",
    )
    assert refs[3] is True
    assert "structured_payload_receipts" not in request
    assert "structured_payload_receipts" not in response
    assert "tools" not in request["requested"] and "tool_choice" not in request["requested"]
    assert "tool_calls" not in response and "raw_message" not in response
    request_with_override = _request(uuid4()).model_copy(
        update={
            "tools": _values()["tools"],
            "tool_choice": "required",
            "messages_override": [{"role": "user", "content": "unretained override"}],
        }
    )
    (request, _), refs, _ = _persist(
        tmp_path,
        _values(),
        request=request_with_override,
    )
    assert request["replayability_reasons"] == ["messages_override_not_retained"]
    assert request["requested"]["tools"] == request_with_override.tools
    assert refs[3] is False


def test_full_tool_retention_does_not_hide_content_truncation(tmp_path):
    (_, response), refs, _ = _persist(tmp_path, _values(), content="x" * 120_001)
    assert response["replayability_reasons"] == ["response_truncated"]
    assert response["tool_calls"] == _values()["tool_calls"]
    assert refs[3] is False


@pytest.mark.parametrize("field", ["tools", "tool_choice", "tool_calls", "raw_message"])
def test_exact_serialized_json_boundary_is_retained(tmp_path, monkeypatch, field):
    values = _values()
    encoded = json.dumps(values[field], ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    monkeypatch.setattr(llm, "_PROMPT_PAYLOAD_MAX_CHARS", len(encoded))
    (request, response), _, _ = _persist(tmp_path, values)
    side = request if field in {"tools", "tool_choice"} else response
    target = side["requested"] if side is request else side
    receipt = side["structured_payload_receipts"][field]
    assert receipt["original_chars"] == receipt["limit_chars"]
    assert receipt["status"] == "retained" and not receipt["truncated"]
    assert target[field] == values[field]


@pytest.mark.parametrize("invalid", [("silently", "coerced"), {1: "coerced-key"}])
def test_silent_non_json_type_conversion_is_not_exact_retention(tmp_path, invalid):
    values = _values()
    values["tools"][0]["function"]["parameters"] = invalid
    (request, _), refs, _ = _persist(tmp_path, values)
    assert request["structured_payload_receipts"]["tools"]["status"] == "invalid_json"
    assert refs[3] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("content", ["", "mixed physical response"])
async def test_actual_caller_retains_tools_and_return_without_changing_paid_ledger(
    tmp_path,
    monkeypatch,
    gateway_ledger,
    content,
):
    values = _values()

    async def offline_gateway(*args, **kwargs):
        return (
            content,
            11,
            22,
            "tool_calls",
            values["tool_calls"],
            {
                "role": "assistant",
                "content": content or None,
                "tool_calls": values["tool_calls"],
            },
        )

    monkeypatch.setattr(llm, "_call_litellm", offline_gateway)
    request = _request(uuid4()).model_copy(
        update={
            "tools": values["tools"],
            "tool_choice": "required",
        }
    )
    session = _Session()
    result = await llm.complete_text(session, _settings(tmp_path, retain=True, mock=False), request)
    run = next(row for row in session.added if isinstance(row, LlmRunModel))
    retained_request = json.loads((tmp_path / run.request_payload_ref).read_text())
    retained_response = json.loads((tmp_path / run.response_payload_ref).read_text())
    assert retained_request["requested"]["tools"] == values["tools"]
    assert retained_request["requested"]["tool_choice"] == "required"
    assert retained_response["tool_calls"] == result.tool_calls
    assert retained_response["raw_message"] == result.raw_message
    assert retained_request["replayable"] and retained_response["replayable"]
    assert run.metadata_json["prompt_payload_receipt"]["replayable"] is True
    operation = await session.scalar(select(ExecutionOperationModel))
    assert operation.result_json["tool_calls"] == result.tool_calls
    assert operation.result_json["raw_message"] == result.raw_message
    budget = await session.get(ExecutionBudgetModel, current_execution_scope())
    assert budget.calls_used == 1 and budget.tokens_charged == 33
