"""Fail-closed tests for conception repair adoption review."""

# Chinese fixture text intentionally uses full-width punctuation.
# ruff: noqa: E501, RUF001
# mypy: ignore-errors

from __future__ import annotations

import asyncio
import json
from uuid import uuid4

import pytest

from bestseller.services import conception_repair_guard as guard
from bestseller.services.llm import LLMCompletionResult

pytestmark = pytest.mark.unit


def _payload() -> tuple[dict, dict, dict]:
    before = {
        "premise": "林舟要在三日内救出妹妹",
        "synopsis": "债主封城，林舟偷渡入城并逼出线索。",
        "story_spine": {"who": "林舟", "wants": "救妹妹", "against": "债主封城"},
    }
    candidate = {
        **before,
        "premise": "林舟要在三日内救出妹妹并查明真相",
        "story_spine": {**before["story_spine"], "stakes": "妹妹性命"},
    }
    intent = {"genre_key": "realistic-life", "user_notes": "轻松但要有明确冲突和行动后果"}
    return before, candidate, intent


def _run(monkeypatch, result, **kwargs):
    async def fake_complete(*_args, **_kwargs):
        return result

    monkeypatch.setattr(guard, "complete_text", fake_complete)
    before, candidate, intent = _payload()
    return asyncio.run(
        guard.review_conception_repair(
            None, object(), before=before, candidate=candidate,
            creation_intent=intent, repair_reason="premise repair", **kwargs,
        )
    )


def test_valid_report_is_bound_to_both_inputs(monkeypatch) -> None:
    response = {"changed_dimensions": ["premise"], "regressions": [], "preserved": True}
    out = _run(monkeypatch, LLMCompletionResult(
        content=json.dumps(response), provider="mock", model_name="judge", finish_reason="stop", llm_run_id=uuid4()
    ))
    assert out["passed"] is True
    assert out["status"] == "passed"
    assert out["before_hash"] != out["candidate_hash"]
    assert out["intent_hash"]
    assert out["llm_run_id"]


@pytest.mark.parametrize("content", ["", "not json", "{}", json.dumps({"preserved": True, "regressions": []})])
def test_invalid_model_output_fails_closed(monkeypatch, content) -> None:
    out = _run(monkeypatch, LLMCompletionResult(
        content=content, provider="mock", model_name="judge", finish_reason="stop", llm_run_id=uuid4()
    ))
    assert out["passed"] is False
    assert out["status"] == "rejected"
    assert out["issues"]


def test_fallback_and_contradictory_report_fail_closed(monkeypatch) -> None:
    response = {"changed_dimensions": [], "regressions": [{"field": "premise", "before_quote": "林舟", "after_quote": "林舟", "reason": "lost identity"}], "preserved": True}
    out = _run(monkeypatch, LLMCompletionResult(
        content=json.dumps(response), provider="mock", model_name="judge", fallback_used=True,
        llm_run_id=uuid4(),
    ))
    assert out["passed"] is False
    assert any("fallback" in issue for issue in out["issues"])


def test_missing_quote_is_reported_and_rejected(monkeypatch) -> None:
    response = {"changed_dimensions": ["premise"], "regressions": [{"field": "premise", "before_quote": "不存在", "after_quote": "林舟", "reason": "bad"}], "preserved": False}
    out = _run(monkeypatch, LLMCompletionResult(
        content=json.dumps(response), provider="mock", model_name="judge", finish_reason="stop", llm_run_id=uuid4()
    ))
    assert out["passed"] is False
    assert any("quote" in issue for issue in out["issues"])


@pytest.mark.parametrize("field,value", [("premise", ""), ("synopsis", " "), ("story_spine", {})])
def test_empty_identity_fields_are_rejected_before_model_call(monkeypatch, field, value) -> None:
    before, candidate, intent = _payload()
    before[field] = value
    calls = []

    async def fail_complete(*args, **kwargs):
        calls.append(True)
        raise AssertionError("invalid input must not call model")

    monkeypatch.setattr(guard, "complete_text", fail_complete)
    out = asyncio.run(guard.review_conception_repair(
        None, object(), before=before, candidate=candidate,
        creation_intent=intent, repair_reason="repair",
    ))
    assert out["passed"] is False
    assert calls == []


def test_empty_quote_and_unproven_deletion_are_rejected(monkeypatch) -> None:
    response = {"changed_dimensions": ["premise"], "regressions": [
        {"field": "premise", "before_quote": "", "after_quote": "", "reason": "deleted"},
    ], "preserved": False}
    out = _run(monkeypatch, LLMCompletionResult(
        content=json.dumps(response), provider="mock", model_name="judge", finish_reason="stop", llm_run_id=uuid4()
    ))
    assert out["passed"] is False
    assert any("empty" in issue for issue in out["issues"])


def test_finish_length_cannot_pass(monkeypatch) -> None:
    response = {"changed_dimensions": [], "regressions": [], "preserved": True}
    out = _run(monkeypatch, LLMCompletionResult(
        content=json.dumps(response), provider="mock", model_name="judge",
        finish_reason="length", llm_run_id=uuid4(),
    ))
    assert out["passed"] is False
    assert any("finish_reason" in issue for issue in out["issues"])


def test_empty_after_quote_only_proves_deletion_when_absent(monkeypatch) -> None:
    response = {"changed_dimensions": ["premise"], "regressions": [
        {"field": "premise", "before_quote": "三日内", "after_quote": "", "reason": "删除期限"},
    ], "preserved": False}
    out = _run(monkeypatch, LLMCompletionResult(
        content=json.dumps(response), provider="mock", model_name="judge", finish_reason="stop", llm_run_id=uuid4()
    ))
    assert out["passed"] is False
    assert any("deletion_unproven" in issue for issue in out["issues"])


def test_unknown_finish_reason_cannot_authorize_adoption(monkeypatch) -> None:
    response = {"changed_dimensions": [], "regressions": [], "preserved": True}
    out = _run(monkeypatch, LLMCompletionResult(
        content=json.dumps(response), provider="mock", model_name="judge", finish_reason=None,
    ))
    assert out["passed"] is False
    assert "abnormal_finish_reason:None" in out["issues"]
