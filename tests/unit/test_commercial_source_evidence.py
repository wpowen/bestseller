from __future__ import annotations

import copy
import json

import pytest

from bestseller.services import chapter_llm_quality_judge as judge
from bestseller.services.commercial_source_evidence import (
    PROMPT_VERSION,
    assemble_commercial_sources,
    commercial_input_receipt,
    validate_commercial_evidence,
)
from bestseller.services.judge_genre_context import JudgeGenreContext
from bestseller.services.llm import LLMCompletionResult
from bestseller.settings import load_settings


def _review_completion(**kwargs):
    # A provider tool response carries the unchanged report as JSON arguments.
    raw = kwargs.get("content", "")
    try:
        report = json.loads(raw)
    except (TypeError, ValueError):
        report = {}
    if isinstance(report, dict) and "wire_version" in report:
        kwargs["tool_calls"] = [{"id": "submission-test", "type": "function",
                                 "function": {"name": "submit_chapter_review", "arguments": raw}}]
    return LLMCompletionResult(**kwargs)



def _sources(body="海员读完了刚收到的天气图。", previous="海员昨日测得风速三节。", source=None):
    return assemble_commercial_sources(
        chapter_number=2,
        content_md=body,
        source=source or {},
        previous_chapters=[{"chapter_number": 1, "content_md": previous}],
    )


def _citation(sources, kind):
    entry = next(value for value in sources.catalog.values() if value["kind"] == kind)
    return {"id": entry["id"], "kind": kind, "quote": entry["text"]}


def _report(sources, *, kind="narrative_quality", score=0.63):
    current = _citation(sources, "current")
    issue = {
        "code": "TEST_ISSUE",
        "severity": "high",
        "evidence": current["quote"],
        "required_fix": "写清选择的原因。",
        "assessment_kind": kind,
        "body_citations": [current],
        "reader_impact": "读者无法理解选择。",
    }
    if kind != "narrative_quality":
        source = _citation(sources, "prior" if kind == "prior_fact_conflict" else "constraint")
        issue.update(
            source_citations=[source],
            source_assertion=source["quote"],
            current_assertion=current["quote"],
            conflict_reason="两个断言指向同一事件却相斥。",
            temporal_relation="same_event_or_state",
        )
    return {
        "pass": False,
        "overall_score": score,
        "dimension_scores": {"readability": score},
        "blocking_issues": [issue],
        "audit_issues": [],
        "binary_checklist": {},
        "rewrite_plan": {"scope": "chapter", "change": ["写清选择的原因。"]},
    }


def _authorized_source():
    return {
        "approved_creation_contracts": {
            "hard_constraints": [
                {
                    "hard": True,
                    "source": "user",
                    "scope": {"chapter_numbers": [2]},
                    "text": "这次航程不得使用无线电。",
                },
            ]
        }
    }


def test_complete_assembly_preserves_tail_fields_prior_and_current():
    source = {"story_bible": {"background": "甲" * 12500, "last_constraint": "尾部来源不能丢失。"}}
    previous = "乙" * 6500 + "\n\n前章尾部有明确交付。"
    body = "丙" * 18500 + "\n\n本章末尾提出新选择。"
    sources = _sources(body, previous, source)
    payload = json.loads(sources.text)
    assert payload["source_context"] == source
    assert payload["previous_chapters"][0]["content_md"] == previous
    assert payload["current_body"] == body
    assert any(entry["text"] == "前章尾部有明确交付。" for entry in sources.catalog.values())
    assert any(entry["text"] == "本章末尾提出新选择。" for entry in sources.catalog.values())
    receipt = commercial_input_receipt(
        sources, system_prompt="test", user_prompt=sources.text, output_tokens=2048
    )
    assert receipt["budget_passed"]
    assert receipt["total_token_budget"] == 65536
    assert receipt["truncated"] is False


def test_projection_keeps_both_explicit_channels_and_approved_contracts():
    projected = judge._commercial_source_projection(
        {
            "schema_version": "chapter-generation-input.v2",
            "approved_creation_contracts": {"first": "retain"},
            "approved_effect_contracts": {"second": "retain"},
            "acceptance_contract": {
                "source_authority_channels": {
                    "explicit_creator_requirements": {"third": "retain"},
                    "established_facts": {"actual": "observation"},
                    "ai_proposals": {"recipe": "not an obligation"},
                }
            },
        }
    )
    assert projected["approved_creation_contracts"] == {"first": "retain"}
    assert projected["explicit_creator_requirements"] == {"third": "retain"}
    assert projected["approved_effect_contracts"] == {"second": "retain"}
    assert "ai_proposals" not in projected


@pytest.mark.parametrize(
    "source",
    [
        {"story_bible": {"approved_story_design": {"secret": "", "growth_curve": None}}},
        {"story_bible": {"role": "陶工", "genre": "田园", "first_three_hook": "试做新釉"}},
        {"established_facts": {"measurements": ["上一轮水位升了两厘米。"]}},
        {"approved_creation_contracts": {"hard_constraints": [{"hard": True, "text": "不得发展"}]}},
    ],
)
def test_missing_plans_roles_and_observations_never_create_prohibitions(source):
    sources = _sources(source=source)
    assert not any(value["kind"] == "constraint" for value in sources.catalog.values())
    report = _report(sources, kind="prior_fact_conflict")
    report["blocking_issues"][0]["assessment_kind"] = "constraint_violation"
    assert validate_commercial_evidence(report, sources)


@pytest.mark.parametrize(
    "mutation",
    [
        {"approved": False},
        {"status": "superseded"},
        {"scope": {"chapter_numbers": [3]}},
        {"scope": {"unknown_scope": "future"}},
    ],
)
def test_unapproved_stale_or_inapplicable_clauses_cannot_block(mutation):
    source = _authorized_source()
    source["approved_creation_contracts"]["hard_constraints"][0].update(mutation)
    assert not any(
        value["kind"] == "constraint" for value in _sources(source=source).catalog.values()
    )


def test_applicable_explicit_rule_and_actual_conflicting_body_are_retained():
    sources = _sources(body="他打开了无线电开始呼叫。", source=_authorized_source())
    assert not validate_commercial_evidence(_report(sources, kind="constraint_violation"), sources)
    original = _sources("天气平静。", "档案记载这艘船从未出港。")
    other = _sources("船已离港。", "档案记载这艘船从未出港。")
    bad = _report(original)
    assert validate_commercial_evidence(bad, other)  # IDs bind to the exact current body.


@pytest.mark.parametrize(
    "body,previous",
    [
        ("新一轮试验把温度升到九十度。", "上一轮温度只有七十度。"),
        ("园丁学会了给梨树嫁接。", "园丁上个月只会修枝。"),
    ],
)
def test_prior_observation_cannot_be_a_future_cap(body, previous):
    sources = _sources(body, previous)
    report = _report(sources, kind="prior_fact_conflict")
    report["blocking_issues"][0]["temporal_relation"] = "later_progression"
    assert any("later event" in error for error in validate_commercial_evidence(report, sources))


@pytest.mark.parametrize(
    "tamper", ["invented_prior_quote", "invented_chapter", "wrong_kind", "invented_assertion"]
)
def test_factual_claims_need_exact_current_and_prior_source(tamper):
    sources = _sources("他承认昨天从未测过风。", "昨天他亲手测了八次风速。")
    report = _report(sources, kind="prior_fact_conflict")
    assert not validate_commercial_evidence(report, sources)
    issue = report["blocking_issues"][0]
    if tamper == "invented_prior_quote":
        issue["source_citations"][0]["quote"] = "此前从未有人准许他碰仪器。"
    elif tamper == "invented_chapter":
        issue["source_citations"][0]["id"] = "PREV10:does-not-exist"
    elif tamper == "wrong_kind":
        issue["source_citations"][0]["kind"] = "constraint"
    else:
        issue["source_assertion"] = "第十次远航时已经建立了这条规则。"
    assert validate_commercial_evidence(report, sources)


def test_prior_excerpt_coverage_is_explicit_and_receipts_track_all_sources():
    sources = assemble_commercial_sources(
        chapter_number=2,
        content_md="新事件。",
        source={},
        previous_chapters=[{"chapter_number": 1, "content_excerpt": "已提供片段。"}],
    )
    assert sources.receipt["previous"][0]["coverage"] == "excerpt"
    assert _sources().receipt["body_sha256"] != _sources("另一段正文。").receipt["body_sha256"]
    assert (
        _sources().receipt["previous_sha256"]
        != _sources(previous="不同前文。").receipt["previous_sha256"]
    )
    assert (
        _sources().receipt["source_sha256"]
        != _sources(source={"late": "变化"}).receipt["source_sha256"]
    )


@pytest.mark.asyncio
async def test_real_scores_and_grounded_quality_rejection_are_preserved(monkeypatch):
    sources = _sources()
    old = _report(sources)
    issue = old["blocking_issues"][0]
    report = {
        "wire_version": "commercial-review-wire.v2",
        "pass": old["pass"],
        "overall_score": old["overall_score"],
        "dimension_scores": old["dimension_scores"],
        "issues": [
            {
                "code": issue["code"],
                "severity": issue["severity"],
                "assessment_kind": "narrative_quality",
                "body_id": issue["body_citations"][0]["id"],
                "reason": issue["reader_impact"],
                "required_fix": issue["required_fix"],
            }
        ],
        "rewrite_plan": old["rewrite_plan"],
    }
    requests = []

    async def complete(session, settings, request):
        requests.append(request)
        return _review_completion(content=json.dumps(report), provider="mock", model_name="mock")

    monkeypatch.setattr(judge, "complete_text", complete)
    result = await judge.judge_chapter_commercial_quality(
        object(),
        load_settings(env={}),
        chapter_number=2,
        content_md="海员读完了刚收到的天气图。",
        previous_chapters=[{"chapter_number": 1, "content_md": "海员昨日测得风速三节。"}],
        sample_slot="slot-2",
    )
    assert result.quality_rejected and result.overall_score == 0.63
    assert result.dimension_scores == {"readability": 0.63}
    assert result.blocking_issues[0].code == "TEST_ISSUE"
    assert len(requests) == 1 and requests[0].sample_slot == "slot-2"
    assert requests[0].prompt_version == PROMPT_VERSION != "v4-source-bound-review"
    assert requests[0].metadata["commercial_source_input_receipt"]["budget_passed"]
    assert (
        requests[0].metadata["commercial_source_input_receipt"]["body_sha256"]
        == sources.receipt["body_sha256"]
    )


@pytest.mark.asyncio
async def test_invalid_issues_are_unavailable_not_deleted_into_pass_or_prose_rework(monkeypatch):
    sources = _sources()
    report = _report(sources, score=0.97)
    report["pass"] = True
    report["audit_issues"] = [copy.deepcopy(report["blocking_issues"][0])]
    report["audit_issues"][0]["body_citations"][0]["quote"] = "不在正文中。"
    raw = json.dumps(report, ensure_ascii=False)
    calls = []

    async def complete(session, settings, request):
        calls.append(request)
        return _review_completion(content=raw, provider="mock", model_name="mock")

    monkeypatch.setattr(judge, "complete_text", complete)
    result = await judge.judge_chapter_commercial_quality(
        object(), load_settings(env={}), chapter_number=2, content_md="海员读完了刚收到的天气图。"
    )
    assert len(calls) == 1  # No unbounded repairs or new writer calls.
    assert result.infrastructure_failure and not result.passed and not result.quality_rejected
    assert result.overall_score == 0.97 and result.dimension_scores == {"readability": 0.97}
    assert json.loads(result.raw_excerpt)["raw_report"] == raw
    assert result.rewrite_plan.scope == "judge" and result.rewrite_plan.change == ()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "content,previous",
    [
        ("超预算正文。" * 20000, []),
        ("本章正文。", [{"chapter_number": 10, "content_md": "不能伪装成已写前章。"}]),
        ("", []),
    ],
)
async def test_incomplete_or_overbudget_input_stops_before_paid_call(
    monkeypatch, content, previous
):
    async def no_paid_call(*args):
        pytest.fail("preflight failure must precede the paid call")

    monkeypatch.setattr(judge, "complete_text", no_paid_call)
    result = await judge.judge_chapter_commercial_quality(
        object(),
        load_settings(env={}),
        chapter_number=2,
        content_md=content,
        previous_chapters=previous,
    )
    assert result.infrastructure_failure and result.rewrite_plan.scope == "judge"


def test_prompt_declares_progression_role_knowledge_and_missing_approval_policy():
    prompt = judge._render_chapter_judge_system_prompt(
        rubric=None,
        reference_block="",
        checklist_block="",
        calibration_block="",
        genre_context=judge._neutral_genre_context(),
    )
    for rule in (
        "不是未来永久上限",
        "不是禁止新能力",
        "不构成知识禁令",
        "一边承认文本正确",
        "same_event_or_state",
    ):
        assert rule in prompt


def test_mid_chapter_observation_keeps_temporal_scope_and_source_reference():
    source = {
        "established_facts": {
            "snapshots": [
                {
                    "text": "访客在东岸码头等船。",
                    "temporal_status": "mid_chapter",
                    "source_ref": {"chapter_number": 1, "paragraph": 3},
                }
            ]
        }
    }
    sources = _sources(source=source)
    entry = next(
        item for item in sources.catalog.values() if item["text"] == "访客在东岸码头等船。"
    )
    assert entry["kind"] == "observation"
    assert entry["temporal_context"]["temporal_status"] == "mid_chapter"
    assert entry["temporal_context"]["source_ref"] == {"chapter_number": 1, "paragraph": 3}
    assert not any(item["kind"] == "constraint" for item in sources.catalog.values())


def test_genuine_current_internal_contradiction_remains_a_quality_rejection():
    sources = _sources(body="她把钥匙锁进柜里。\n\n没有开柜她就把那把钥匙递给了门卫。")
    first, second = [entry for entry in sources.catalog.values() if entry["kind"] == "current"]
    report = _report(sources)
    report["blocking_issues"][0].update(
        assessment_kind="current_fact_conflict",
        evidence=second["text"],
        body_citations=[{"id": second["id"], "kind": "current", "quote": second["text"]}],
        source_citations=[{"id": first["id"], "kind": "current", "quote": first["text"]}],
        source_assertion=first["text"],
        current_assertion=second["text"],
        conflict_reason="同一把钥匙改变位置但没有取出动作。",
        temporal_relation="unexplained_transition",
    )
    assert validate_commercial_evidence(report, sources) == ()


@pytest.mark.parametrize("language", ["zh", "en"])
@pytest.mark.parametrize(
    "category",
    [
        "default",
        "suspense-mystery",
        "action-progression",
        "relationship-driven",
        "strategy-worldbuilding",
    ],
)
def test_genre_outbound_lens_does_not_emit_raw_recipes(language, category):
    context = JudgeGenreContext(
        category_key=category,
        corpus_key="generic",
        display_genre="test genre",
        story_logic_checks_zh=("RAW_UNSELECTED_RECIPE_A",),
        story_logic_checks_en=("RAW_UNSELECTED_RECIPE_B",),
        specialist_terms=("星流",),
        key_objects=("返航图",),
        signal_keywords=(),
        uses_commission_structure=False,
    )
    text = context.render_story_logic_block(language)
    lens = json.loads(text.split("\n", 1)[1])
    assert lens["schema_version"] == "genre-quality-lens.v1"
    assert lens["authority"] == "advisory" and lens["category_key"] == category
    assert {item["id"] for item in lens["dimensions"]} == {
        "causality",
        "continuity",
        "readability",
        "reader_interest",
    }
    assert "RAW_UNSELECTED_RECIPE_A" not in text and "RAW_UNSELECTED_RECIPE_B" not in text
    vocabulary = json.loads(context.render_own_terms_block(language).split("\n", 1)[1])
    assert vocabulary["authority"] == "unverified_context"
    assert vocabulary["specialist_terms"] == ["星流"] and vocabulary["key_objects"] == ["返航图"]
