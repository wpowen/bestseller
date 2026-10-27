"""Physical-response type integrity only; no paid/adoption authority is fabricated."""
from copy import deepcopy
import hashlib
import socket

import pytest

from bestseller.services import llm
from bestseller.services import retained_planning_generation as physical

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def no_external_io(monkeypatch):
    def denied(*args, **kwargs):
        pytest.fail("physical response tests must not acquire DB/network/provider IO")

    from sqlalchemy.engine import Engine
    from sqlalchemy.ext.asyncio import AsyncEngine

    monkeypatch.setattr(Engine, "connect", denied)
    monkeypatch.setattr(AsyncEngine, "connect", denied)
    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(llm, "complete_text", denied)


def response_fixture():
    # Explicit synthetic transport-unit input, not a claimed native paid receipt.
    paid = llm.LLMCompletionResult(
        content="X", provider="minimax", model_name="minimax/fixture",
        finish_reason="stop", input_tokens=0, output_tokens=0,
        fallback_used=False, tool_calls=[],
        raw_message={"position": 0, "accepted": False, "ordered": [0, 1]},
    )
    value = {
        "content": {"text": paid.content, "truncated": False, "original_chars": 1},
        "model": paid.model_name, "tool_call_count": 0,
        **{key: getattr(paid, key) for key in (
            "provider", "finish_reason", "fallback_used", "fallback_source",
            "input_tokens", "output_tokens",
        )},
    }
    value["structured_payload_receipts"] = {}
    for key in ("tool_calls", "raw_message"):
        value[key] = deepcopy(getattr(paid, key))
        text = physical._json(value[key])
        value["structured_payload_receipts"][key] = {
            "status": "retained", "encoding": "json-utf8", "truncated": False,
            "original_chars": len(text), "limit_chars": 4096,
            "sha256": hashlib.sha256(text.encode()).hexdigest(),
        }
    return value, paid


def test_original_response_accepts_without_modifying_either_input():
    value, paid = response_fixture()
    before = physical._json(value), paid.model_dump_json()
    physical._response(value, paid)
    physical._response(value, paid)
    assert (physical._json(value), paid.model_dump_json()) == before


@pytest.mark.parametrize("field,replacement", [
    ("input_tokens", False), ("output_tokens", False), ("fallback_used", 0),
    ("tool_call_count", False), ("input_tokens", 0.0),
])
def test_json_equal_but_differently_typed_scalar_is_rejected(field, replacement):
    value, paid = response_fixture()
    assert value[field] == replacement and type(value[field]) is not type(replacement)
    value[field] = replacement
    with pytest.raises(physical.RetainedPlanningGenerationError):
        physical._response(value, paid)


def test_true_is_not_the_one_character_length():
    value, paid = response_fixture()
    value["content"]["original_chars"] = True
    with pytest.raises(physical.RetainedPlanningGenerationError):
        physical._response(value, paid)


@pytest.mark.parametrize("field,replacement", [
    ("position", False), ("accepted", 0), ("position", 0.0),
])
def test_structured_value_must_match_its_original_typed_receipt(field, replacement):
    value, paid = response_fixture()
    value["raw_message"][field] = replacement
    # Python equality alone accepts this corruption; the retained JSON does not.
    assert value["raw_message"] == paid.raw_message
    with pytest.raises(physical.RetainedPlanningGenerationError):
        physical._response(value, paid)


def test_structured_array_order_remains_significant():
    value, paid = response_fixture()
    value["raw_message"]["ordered"].reverse()
    with pytest.raises(physical.RetainedPlanningGenerationError):
        physical._response(value, paid)


def test_structured_length_receipt_is_an_integer_not_an_equal_float():
    value, paid = response_fixture()
    record = value["structured_payload_receipts"]["raw_message"]
    record["original_chars"] = float(record["original_chars"])
    with pytest.raises(physical.RetainedPlanningGenerationError):
        physical._response(value, paid)


def test_nonempty_tool_call_keeps_nested_types_and_original_receipt():
    value, paid = response_fixture()
    calls = [{"id": "call_fixture", "type": "function", "index": 0,
              "function": {"name": "submit", "arguments": "{}"}}]
    paid = paid.model_copy(update={"tool_calls": calls})
    value["tool_calls"] = deepcopy(calls)
    value["tool_call_count"] = 1
    text = physical._json(calls)
    value["structured_payload_receipts"]["tool_calls"].update(
        original_chars=len(text), sha256=hashlib.sha256(text.encode()).hexdigest(),
    )
    physical._response(value, paid)
    value["tool_calls"][0]["index"] = False
    assert value["tool_calls"] == paid.tool_calls
    with pytest.raises(physical.RetainedPlanningGenerationError):
        physical._response(value, paid)


def test_existing_truncated_content_guard_remains_strict():
    value, paid = response_fixture()
    value["content"]["truncated"] = 0
    with pytest.raises(physical.RetainedPlanningGenerationError):
        physical._response(value, paid)
