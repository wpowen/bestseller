# Chinese fixture punctuation is intentional.
# ruff: noqa: RUF001
from __future__ import annotations

import asyncio
import json
import re
from types import SimpleNamespace

import pytest

from bestseller.services import logline_gate

pytestmark = pytest.mark.unit


def _strong_scores() -> dict[str, float]:
    return dict.fromkeys(logline_gate.AXIS_KEYS, 4.0)


def test_review_receives_complete_intent_and_story_context_and_preserves_axis_why(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from bestseller.services import llm

    captured = []
    why = {
        "genre_fidelity": {
            "reason": "The approved comic cultivation style permits this mechanism.",
            "evidence": [
                {"field": "creation_intent.allowed_styles", "quote": "轻松喜剧修仙"}
            ],
            "repair_scope": "expression",
        }
    }

    async def fake_complete_text(_session, _settings, request):
        captured.append(request)
        return SimpleNamespace(
            content=json.dumps({"scores": _strong_scores(), "why": why}, ensure_ascii=False)
        )

    monkeypatch.setattr(llm, "complete_text", fake_complete_text)
    tail_marker = "FULL_CONTEXT_TAIL_MARKER"
    creation_intent = {
        "genre": "仙侠",
        "allowed_styles": ["轻松喜剧修仙"],
        "user_constraints": ["允许金手指，不要求正统苦修"],
    }
    story_context = {
        "story_spine": {"conflict": "宗门把误会当神谕，主角只能将错就错"},
        "serial_plan": "x" * 2400 + tail_marker,
    }

    verdict = asyncio.run(
        logline_gate.evaluate_logline_gate(
            object(),
            object(),
            logline="杂役一句玩笑被宗门当成神谕，他只好边圆谎边救下被误判的弟子。",
            premise="误会会触发不同派系的真实行动与反制。",
            genre="仙侠",
            sub_genre="轻松喜剧",
            creation_intent=creation_intent,
            story_context=story_context,
        )
    )

    prompt = captured[0].user_prompt
    assert tail_marker in prompt
    assert "允许金手指，不要求正统苦修" in prompt
    assert "宗门把误会当神谕" in prompt
    assert verdict.axis_why == why
    assert verdict.review_input_fingerprint is not None
    assert re.fullmatch(r"[0-9a-f]{64}", verdict.review_input_fingerprint)
    persisted = verdict.to_dict()
    assert persisted["axis_why"] == why
    assert persisted["review_input_fingerprint"] == verdict.review_input_fingerprint


def test_review_input_fingerprint_changes_with_user_intent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from bestseller.services import llm

    async def fake_complete_text(_session, _settings, _request):
        return SimpleNamespace(content=json.dumps({"scores": _strong_scores()}))

    monkeypatch.setattr(llm, "complete_text", fake_complete_text)

    async def judge(style: str):
        return await logline_gate.evaluate_logline_gate(
            object(),
            object(),
            logline="一个巡检员靠公务工单追查仙城灵气账目。",
            genre="仙侠",
            creation_intent={"allowed_styles": [style]},
            story_context={"story_spine": {"escalation": "账目牵出不同责任人"}},
        )

    first = asyncio.run(judge("修仙职场喜剧"))
    second = asyncio.run(judge("严肃权谋"))

    assert first.review_input_fingerprint
    assert second.review_input_fingerprint
    assert first.review_input_fingerprint != second.review_input_fingerprint
    assert first.axis_why is None
    assert "axis_why" not in first.to_dict()


def test_reader_prompt_separates_expression_gaps_from_story_gaps() -> None:
    prompt = logline_gate._build_reader_judge_system_prompt(
        logline_gate.load_logline_gate_config()
    )

    assert "一句话未表达" in prompt
    assert "故事缺失" in prompt
    assert "用户明确允许" in prompt
    assert "不得用评审者自选的题材观" in prompt
    assert "insufficient_context" in prompt
    assert "不得自动判为通过" in prompt
    assert '"repair_scope":"expression|story|insufficient_context"' in prompt


def test_legacy_string_why_is_preserved_without_inventing_structured_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from bestseller.services import llm

    legacy_why = {"click_hook": "开头冲突出现太晚"}

    async def fake_complete_text(_session, _settings, _request):
        return SimpleNamespace(
            content=json.dumps(
                {"scores": _strong_scores(), "why": legacy_why}, ensure_ascii=False
            )
        )

    monkeypatch.setattr(llm, "complete_text", fake_complete_text)
    verdict = asyncio.run(
        logline_gate.evaluate_logline_gate(
            object(), object(), logline="一名验房师在封楼前发现验收报告被调包。"
        )
    )

    assert verdict.axis_why == legacy_why
    assert isinstance(verdict.axis_why["click_hook"], str)


def test_missing_axis_is_unknown_and_cannot_inherit_a_passing_fallback() -> None:
    scores = logline_gate._parse_scores(
        '{"scores":{"contrast_irony":4.5}}',
        fallback=4.0,
    )

    assert scores["contrast_irony"] == 4.5
    assert scores["causal_coherence"] == 0.0
    assert logline_gate.decide_logline_action(scores).action is logline_gate.LoglineAction.REJECT


def test_explicit_insufficient_context_cannot_expand_despite_high_scores(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from bestseller.services import llm

    why = {
        "causal_coherence": {
            "reason": "The input does not show the mechanism's downstream consequence.",
            "evidence": [],
            "repair_scope": "insufficient_context",
        }
    }

    async def fake_complete_text(_session, _settings, _request):
        return SimpleNamespace(
            content=json.dumps({"scores": _strong_scores(), "why": why}),
            fallback_used=False,
        )

    monkeypatch.setattr(llm, "complete_text", fake_complete_text)
    verdict = asyncio.run(
        logline_gate.evaluate_logline_gate(
            object(), object(), logline="巡检员发现仙城账目被改写。"
        )
    )

    assert verdict.action is logline_gate.LoglineAction.REGENERATE
    assert verdict.scores == _strong_scores()
    assert verdict.axis_why == why
    assert verdict.weakest_axis == "insufficient_context"
    assert any("上下文不足" in reason for reason in verdict.reasons)


@pytest.mark.parametrize(
    "raw",
    [
        '{"scores":{"contrast_irony":NaN}}',
        '{"scores":{"contrast_irony":Infinity}}',
        '{"scores":{"contrast_irony":true}}',
        '{"scores":[]}',
    ],
)
def test_non_finite_boolean_or_non_object_scores_cannot_pass(raw: str) -> None:
    scores = logline_gate._parse_scores(raw, fallback=4.0)

    assert scores["contrast_irony"] == 0.0
    assert logline_gate.decide_logline_action(scores).action is logline_gate.LoglineAction.REJECT


def test_completion_fallback_receipt_is_not_accepted_as_real_review(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from bestseller.services import llm

    async def fake_complete_text(_session, _settings, _request):
        return SimpleNamespace(
            content=json.dumps({"scores": _strong_scores()}),
            fallback_used=True,
        )

    monkeypatch.setattr(llm, "complete_text", fake_complete_text)
    config = logline_gate.load_logline_gate_config()
    config["judge_retry_attempts"] = 1
    verdict = asyncio.run(
        logline_gate.evaluate_logline_gate(
            object(),
            object(),
            logline="巡检员发现仙城账目被改写。",
            config=config,
        )
    )

    assert verdict.action is logline_gate.LoglineAction.REJECT
    assert verdict.llm_used is False
    assert verdict.weakest_axis == "judge_availability"
    assert verdict.review_input_fingerprint
