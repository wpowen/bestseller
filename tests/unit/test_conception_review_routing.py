from types import SimpleNamespace

import pytest

from bestseller.services.conception import _logline_regen_rescue
from bestseller.services.conception_review_routing import (
    intent_recheck_passed,
    logline_repair_scope,
)
from bestseller.services.logline_gate import AXIS_KEYS, LoglineAction, LoglineGateVerdict

pytestmark = pytest.mark.unit


def verdict(axis, *, scope=None):
    scores = dict.fromkeys(AXIS_KEYS, 4.5)
    scores[axis] = 2.0
    return SimpleNamespace(
        action=LoglineAction.REJECT,
        scores=scores,
        overall=4.0,
        llm_used=True,
        axis_why={axis: {"repair_scope": scope}} if scope else {},
    )


def test_story_diagnosis_never_routes_to_sentence_editor():
    assert logline_repair_scope(verdict("serial_sustainability", scope="story")) == "story"
    assert logline_repair_scope(verdict("serial_sustainability")) == "insufficient_context"


def test_explicit_expression_issue_can_use_existing_facts():
    assert (
        logline_repair_scope(verdict("serial_sustainability", scope="expression")) == "expression"
    )
    assert logline_repair_scope(verdict("click_hook")) == "expression"


def test_unknown_and_unavailable_do_not_trigger_story_rewrite():
    assert (
        logline_repair_scope(verdict("causal_coherence", scope="insufficient_context"))
        == "insufficient_context"
    )
    item = verdict("click_hook")
    item.llm_used = False
    assert logline_repair_scope(item) == "review_unavailable"


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"items": {}, "counter_elements": []},
        {"items": {"轻松": {"pass": True}}, "counter_elements": []},
        {"items": {"轻松": {"pass": True, "quote": "模型编的引文"}}, "counter_elements": []},
    ],
)
def test_empty_or_ungrounded_recheck_cannot_clear_failed_intent(payload):
    assert not intent_recheck_passed(
        payload,
        intent_tags=["轻松"],
        candidate={"premise": "轻松应对围攻"},
    )


def test_explicit_grounded_recheck_and_cost_check_are_required():
    payload = {"items": {"轻松": {"pass": True, "quote": "轻松应对"}}, "counter_elements": []}
    options = {"intent_tags": ["轻松"], "candidate": {"premise": "轻松应对围攻"}}
    assert intent_recheck_passed(payload, **options)
    assert not intent_recheck_passed(payload, require_cost_check=True, **options)
    payload["cost_violations"] = []
    assert intent_recheck_passed(payload, require_cost_check=True, **options)


def test_overall_only_failure_uses_explicit_diagnosis():
    item = verdict("click_hook", scope="expression")
    item.scores = dict.fromkeys(AXIS_KEYS, 3.4)
    assert logline_repair_scope(item, {"logline_gate": {"pass_floor": 3.0}}) == "expression"


def test_high_scoring_unknown_axis_still_requires_context():
    item = verdict("click_hook", scope="expression")
    item.axis_why["serial_sustainability"] = {"repair_scope": "insufficient_context"}
    assert item.scores["serial_sustainability"] == 4.5
    assert logline_repair_scope(item) == "insufficient_context"


@pytest.mark.asyncio
async def test_rescue_stops_without_calling_wrong_editor():
    item = verdict("serial_sustainability", scope="story")
    calls = []
    diagnostics = []

    async def should_not_run(*args):
        calls.append(args)
        raise AssertionError("wrong repair scope")

    result, text, attempts = await _logline_regen_rescue(
        verdict=item,
        logline="旧梗概",
        rewrite_fn=should_not_run,
        judge_fn=should_not_run,
        repair_scope_fn=logline_repair_scope,
        diagnostics=diagnostics,
    )
    assert result is item and text == "旧梗概" and attempts == 0
    assert not calls
    assert diagnostics[-1]["stop_reason"] == "story_repair_required"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "new_text,reason", [("", "empty_rewrite"), ("字" * 151, "rewrite_too_long")]
)
async def test_discarded_rewrite_keeps_last_reviewed_pair(new_text, reason):
    item = LoglineGateVerdict(LoglineAction.REGENERATE, {}, overall=3.0)
    diagnostics = []

    async def rewrite(*args):
        return new_text

    async def judge(*args):
        raise AssertionError("unreviewable text must not be judged")

    result, text, attempts = await _logline_regen_rescue(
        verdict=item,
        logline="原句",
        rewrite_fn=rewrite,
        judge_fn=judge,
        diagnostics=diagnostics,
    )
    assert result is item and text == "原句" and attempts == 1
    assert diagnostics[-1]["stop_reason"] == reason


@pytest.mark.asyncio
async def test_materialized_failure_retains_candidate_context_and_scoped_report(monkeypatch):
    from bestseller.services import conception, logline_gate
    from bestseller.services.concept_contract import ConceptContractError

    scores = dict.fromkeys(AXIS_KEYS, 4.5)
    scores["serial_sustainability"] = 2.0
    item = LoglineGateVerdict(
        LoglineAction.REJECT,
        scores,
        llm_used=True,
        axis_why={"serial_sustainability": {"repair_scope": "story", "reason": "阶段重复"}},
    )
    seen = []

    async def judge(*args, **kwargs):
        seen.append(kwargs)
        return item

    async def wrong_editor(*args, **kwargs):
        raise AssertionError("story repair must not call the sentence editor")

    monkeypatch.setattr(logline_gate, "evaluate_logline_gate", judge)
    monkeypatch.setattr(conception, "_rewrite_logline_for_gate", wrong_editor)
    context = {"story_spine": {"against": "对手开始适应主角的策略"}}
    intent = {"tone": "light", "user_tags": ["金手指"]}
    with pytest.raises(ConceptContractError) as caught:
        await conception._recheck_materialized_logline(
            None,
            None,
            logline="主角设法破局",
            premise="既有前提",
            synopsis="既有简介",
            genre="仙侠",
            sub_genre="幻想修仙",
            is_en=False,
            config={},
            creation_intent=intent,
            story_context=context,
            conception_log=[{"agent": "earlier_stage"}],
        )
    assert seen[0]["creation_intent"] == intent
    assert seen[0]["story_context"] == context
    receipt = caught.value.conception_log[-1]
    assert receipt["candidate"]["story_context"] == context
    assert receipt["report"]["repair_scope"] == "story"
    assert receipt["report"]["regen_attempts"] == 0
    assert receipt["automatic_resume_supported"] is False
    assert caught.value.conception_log[0]["agent"] == "earlier_stage"


@pytest.mark.asyncio
async def test_sentence_editor_receives_facts_used_by_judge(monkeypatch):
    from bestseller.services import conception

    seen = []

    async def complete(session, settings, request):
        seen.append(request)
        return SimpleNamespace(content="新句")

    monkeypatch.setattr(conception, "complete_text", complete)
    item = LoglineGateVerdict(
        LoglineAction.REGENERATE,
        {},
        axis_why={"click_hook": {"reason": "短句漏了已经写明的选择", "repair_scope": "expression"}},
    )
    await conception._rewrite_logline_for_gate(
        None,
        None,
        logline="旧句",
        verdict=item,
        premise="前提",
        synopsis="简介",
        genre="都市",
        sub_genre="行业",
        is_en=False,
        story_context={"story_spine": {"question": "已存在的同行竞争事实"}},
    )
    assert "已存在的同行竞争事实" in seen[0].user_prompt
    assert "短句漏了已经写明的选择" in seen[0].user_prompt


@pytest.mark.asyncio
async def test_review_exception_retains_unreviewed_candidate():
    original = LoglineGateVerdict(LoglineAction.REGENERATE, {}, overall=3.0)
    diagnostics = []

    async def rewrite(*args):
        return "已经生成但尚未通过审稿的新句"

    async def judge(*args):
        raise TimeoutError("test timeout")

    result, text, _ = await _logline_regen_rescue(
        verdict=original,
        logline="原句",
        rewrite_fn=rewrite,
        judge_fn=judge,
        diagnostics=diagnostics,
    )
    assert result is original and text == "原句"
    assert diagnostics[0]["candidate_logline"] == "已经生成但尚未通过审稿的新句"
    assert diagnostics[0]["candidate_reviewed"] is False
    assert diagnostics[-1]["error_type"] == "TimeoutError"
