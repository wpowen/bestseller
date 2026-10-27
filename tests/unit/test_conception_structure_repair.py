"""A structural editor proposes one bounded, attributable candidate."""

# Chinese story fixtures intentionally use full-width punctuation.
# ruff: noqa: RUF001

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any
from uuid import uuid4

import pytest

from bestseller.services import conception_structure_repair as repair
from bestseller.services.llm import LLMCompletionResult

pytestmark = pytest.mark.unit


def _state() -> dict[str, Any]:
    return {
        "premise": "林舟要在三日内救出妹妹",
        "synopsis": "林舟进城寻找妹妹，随后一直重复打听消息。",
        "story_spine": {"who": "林舟", "wants": "救妹妹", "against": "债主封城"},
        "writing_profile": {"world": {"rule": "城门只能由城内人开启"}},
        "ctx": {"tone_preference": "轻松喜剧", "user_notes": "主角不自损"},
        "title": "城门外的哥哥",
    }


def _candidate() -> dict[str, Any]:
    return {
        "premise": "林舟要在三日内救出妹妹",
        "synopsis": "林舟进城寻找妹妹，证人被转移后，他必须争取守门人的合作。",
        "story_spine": {
            "who": "林舟",
            "wants": "救妹妹",
            "against": "债主封城",
            "escalation": "债主转移证人，主角必须改变行动方式",
        },
    }


def _diagnosis() -> dict[str, Any]:
    return {
        "source": "logline_review",
        "scope": "story",
        "issues": {
            "serial_sustainability": {
                "repair_scope": "story",
                "reason": "行动重复",
                "evidence": [{"field": "synopsis", "quote": "一直重复打听消息"}],
            },
        },
    }


def _result(content: str | None = None, **changes: Any) -> LLMCompletionResult:
    values = {
        "content": json.dumps(_candidate(), ensure_ascii=False) if content is None else content,
        "provider": "fixture",
        "model_name": "editor",
        "finish_reason": "stop",
        "llm_run_id": uuid4(),
    }
    values.update(changes)
    return LLMCompletionResult(**values)


def _stub(monkeypatch: pytest.MonkeyPatch, result: LLMCompletionResult) -> list[Any]:
    calls = []

    async def complete(session: Any, settings: Any, request: Any) -> LLMCompletionResult:
        calls.append(request)
        return result

    monkeypatch.setattr(repair, "complete_text", complete)
    return calls


async def test_one_editor_call_preserves_source_and_returns_only_a_proposal(monkeypatch):
    state = _state()
    diagnosis = _diagnosis()
    original = copy.deepcopy(state)
    original_diagnosis = copy.deepcopy(diagnosis)
    result = _result()
    calls = _stub(monkeypatch, result)

    proposal = await repair.propose_conception_structure_repair(
        None,
        None,
        state=state,
        diagnosis=diagnosis,
    )

    assert len(calls) == 1
    request = calls[0]
    assert request.logical_role == "editor"
    assert request.model_tier == "strong"
    assert request.prompt_template == "conception_structure_repair"
    inputs = json.loads(request.user_prompt)
    assert inputs["state"] == original
    assert inputs["diagnosis"] == original_diagnosis
    assert "主角身份" in request.system_prompt
    assert "世界规则" in request.system_prompt
    assert "用户" in request.system_prompt
    assert state == original and diagnosis == original_diagnosis
    expected_hash = hashlib.sha256(
        json.dumps(
            original,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    assert proposal == {
        "candidate": _candidate(),
        "llm_run_id": str(result.llm_run_id),
        "source_hash": expected_hash,
    }
    proposal["candidate"]["story_spine"]["who"] = "只修改返回对象"
    assert state == original


@pytest.mark.parametrize("finish_reason", ["length", "content_filter", "tool_calls", None])
async def test_incomplete_responses_are_rejected(monkeypatch, finish_reason):
    calls = _stub(monkeypatch, _result(finish_reason=finish_reason))
    with pytest.raises(ValueError, match="finish_reason"):
        await repair.propose_conception_structure_repair(
            None,
            None,
            state=_state(),
            diagnosis=_diagnosis(),
        )
    assert len(calls) == 1


@pytest.mark.parametrize(
    "changes,reason",
    [
        ({"fallback_used": True}, "fallback"),
        ({"tool_calls": [{"id": "fixture-call"}]}, "tool_calls"),
        ({"llm_run_id": None}, "llm_run_id"),
    ],
)
async def test_fallback_tools_and_missing_attribution_are_rejected(monkeypatch, changes, reason):
    _stub(monkeypatch, _result(**changes))
    with pytest.raises(ValueError, match=reason):
        await repair.propose_conception_structure_repair(
            None,
            None,
            state=_state(),
            diagnosis=_diagnosis(),
        )


@pytest.mark.parametrize("content", ["", "not JSON", "[]", "{}", "```json\n{}\n```"])
async def test_non_contract_output_is_rejected(monkeypatch, content):
    _stub(monkeypatch, _result(content))
    with pytest.raises(ValueError):
        await repair.propose_conception_structure_repair(
            None,
            None,
            state=_state(),
            diagnosis=_diagnosis(),
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("premise", " "),
        ("premise", {}),
        ("synopsis", ""),
        ("synopsis", 42),
        ("story_spine", {}),
        ("story_spine", "改故事"),
        ("story_spine", {"who": "林舟"}),
        ("title", "新标题"),
        ("writing_profile", {}),
        ("world", {}),
        ("ctx", {}),
    ],
)
async def test_invalid_or_out_of_scope_candidate_is_rejected(monkeypatch, field, value):
    candidate = {**_candidate(), field: value}
    _stub(monkeypatch, _result(json.dumps(candidate, ensure_ascii=False)))
    with pytest.raises(ValueError):
        await repair.propose_conception_structure_repair(
            None,
            None,
            state=_state(),
            diagnosis=_diagnosis(),
        )


async def test_unchanged_candidate_is_rejected_even_with_reordered_keys(monkeypatch):
    state = _state()
    unchanged = {key: state[key] for key in ("story_spine", "synopsis", "premise")}
    unchanged["premise"] = " " + unchanged["premise"] + "\n"
    _stub(monkeypatch, _result(json.dumps(unchanged, ensure_ascii=False)))
    with pytest.raises(ValueError, match="unchanged"):
        await repair.propose_conception_structure_repair(
            None,
            None,
            state=state,
            diagnosis=_diagnosis(),
        )


async def test_end_turn_is_a_valid_completion(monkeypatch):
    _stub(monkeypatch, _result(finish_reason="end_turn"))
    proposal = await repair.propose_conception_structure_repair(
        None,
        None,
        state=_state(),
        diagnosis=_diagnosis(),
    )
    assert proposal["candidate"] == _candidate()


@pytest.mark.parametrize(
    "field,value",
    [
        ("premise", ""),
        ("synopsis", None),
        ("story_spine", {}),
        ("writing_profile", "bad"),
        ("ctx", "bad"),
    ],
)
async def test_invalid_source_never_calls_editor(monkeypatch, field, value):
    calls = _stub(monkeypatch, _result())
    with pytest.raises(ValueError):
        await repair.propose_conception_structure_repair(
            None,
            None,
            state={**_state(), field: value},
            diagnosis=_diagnosis(),
        )
    assert calls == []


async def test_empty_diagnosis_never_calls_editor(monkeypatch):
    calls = _stub(monkeypatch, _result())
    with pytest.raises(ValueError, match="diagnosis"):
        await repair.propose_conception_structure_repair(None, None, state=_state(), diagnosis={})
    assert calls == []
