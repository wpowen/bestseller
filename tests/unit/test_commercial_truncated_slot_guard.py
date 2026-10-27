"""Actual v12 empty-tool guard witness plus offline cohort controls; no IO dispatch."""

from copy import deepcopy
import json
from pathlib import Path
import socket

import pytest
from sqlalchemy.engine import Engine
from test_commercial_truncated_source_receipt import native_cohort as native_cohort

from bestseller.services import commercial_action_projection as projection
from bestseller.services.commercial_source_evidence import CommercialSources
from bestseller.services.write_gate import GateConfig

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]
FIXTURE = (
    Path(__file__).resolve().parents[2]
    / "reports/project-reliability-review-2026-10-24/implementation/ch4-v12-slot-guard-repair"
    / "actual-truncated-slot-sanitized.json"
)


@pytest.fixture(autouse=True)
def no_database_or_network(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("No DB, network or provider call is authorized")

    monkeypatch.setattr(Engine, "raw_connection", denied)
    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket.socket, "connect_ex", denied)
    monkeypatch.setattr(socket, "create_connection", denied)


def actual_slot():
    data = json.loads(FIXTURE.read_text())
    return data, CommercialSources(text="", catalog={}, receipt=data["source_receipt"])


@pytest.mark.parametrize("tool_calls", [None, []])
async def test_actual_v12_rejected_length_slot_is_only_an_unknown_abstention(tool_calls):
    data, src = actual_slot()
    physical = data["physical_metadata"]
    assert physical["roots"][0]["finish_reason"] == "length"
    assert physical["roots"][0]["status"] == "completed"
    assert physical["attempts"][0]["status"] == "completed"
    assert physical["files"]["response"]["tool_call_count"] == 0
    assert data["envelope"]["validation_errors"] == [
        "output: truncated at structured-review reserve"
    ]
    envelope = data["envelope"]
    envelope["adapter_receipt"]["submission"]["tool_calls_sha256"] = projection._sha(tool_calls)
    report = data["report"]
    report["raw_excerpt"] = json.dumps(envelope, ensure_ascii=False)
    before, source_before = deepcopy(report), deepcopy(src)
    domains, receipts = {}, []
    result, contract = projection._slot(report, data["slot"], src, domains, receipts)
    assert result.infrastructure_failure and not result.passed
    assert result.overall_score == 0 and not result.dimension_scores
    assert result.llm_run_id == "9dcf4c61-c90d-4fd3-bfa9-5339edc535be"
    assert contract == envelope["evaluation_contract"]
    assert not domains
    receipt = receipts[0]
    assert receipt["status"] == "unknown" and receipt["abstains"] is True
    assert receipt["scores_contributed"] == receipt["actions_contributed"] == 0
    assert receipt["domain_issues_contributed"] == 0
    assert receipt["original_report"] == before
    assert report == before and src == source_before


@pytest.mark.parametrize(
    "fault",
    [
        "missing_submission",
        "null_submission",
        "received_zero",
        "executed_tools",
        "unknown_tools_hash",
        "bool_calls",
        "extra_submission_field",
        "missing_binding",
        "changed_binding",
        "missing_audit",
        "changed_audit",
        "wrong_error",
        "wrong_status",
        "extra_adapter_field",
        "missing_run",
        "fake_run",
        "fake_score",
        "normal_invalid_submission",
    ],
)
async def test_fake_truncation_and_incomplete_receipts_stay_closed(fault):
    data, src = actual_slot()
    report, env = data["report"], data["envelope"]
    adapter = env["adapter_receipt"]
    submission = adapter["submission"]
    if fault == "missing_submission":
        del adapter["submission"]
    elif fault == "null_submission":
        adapter["submission"] = None
    elif fault == "received_zero":
        submission["status"] = "received"
    elif fault == "executed_tools":
        submission["tools_executed"] = 1
    elif fault == "unknown_tools_hash":
        submission["tool_calls_sha256"] = "c" * 64
    elif fault == "bool_calls":
        submission["calls_received"] = False
    elif fault == "extra_submission_field":
        submission["invented_authority"] = True
    elif fault == "missing_binding":
        del adapter["source_binding"]
    elif fault == "changed_binding":
        adapter["source_binding"]["body_sha256"] = "0" * 64
    elif fault == "missing_audit":
        del env["exact_source_audit"]
    elif fault == "changed_audit":
        env["exact_source_audit"]["source_sha256"] = "0" * 64
    elif fault == "wrong_error":
        env["validation_errors"] = ["wire: raw output is not strict JSON"]
    elif fault == "wrong_status":
        adapter["status"] = "verified"
    elif fault == "extra_adapter_field":
        adapter["invented_authority"] = True
    elif fault == "missing_run":
        report["llm_run_id"] = None
    elif fault == "fake_run":
        report["llm_run_id"] = "trusted-true"
    elif fault == "fake_score":
        report["overall_score"] = env["original_overall_score"] = 0.86
    else:
        adapter["status"] = "rejected"
        env["validation_errors"] = ["submission: exactly one review tool call required"]
    report["raw_excerpt"] = json.dumps(env, ensure_ascii=False)
    before = deepcopy(report)
    receipts, domains = [], {}
    with pytest.raises(ValueError):
        projection._slot(report, data["slot"], src, domains, receipts)
    assert not receipts and not domains and report == before


@pytest.mark.parametrize("tools", [None, []])
async def test_public_cohort_keeps_actual_scalar_values_and_existing_two_vote_quorum(
    native_cohort, monkeypatch, tools
):
    from bestseller.services import chapter_llm_quality_judge as judge
    from bestseller.services.llm import LLMCompletionResult

    # This cohort is a synthetic source/control fixture using the observed
    # 0.74 / 0.86 values; it is NOT the unavailable full production prompt.
    _, _, _, _ = await native_cohort()
    original_complete = judge.complete_text

    async def complete(session, settings, request):
        result = await original_complete(session, settings, request)
        if request.sample_slot.endswith(":2"):
            return LLMCompletionResult(
                content="",
                tool_calls=tools,
                finish_reason="length",
                provider="fixture",
                model_name="fixture",
                llm_run_id=result.llm_run_id,
            )
        raw = json.loads(result.tool_calls[0]["function"]["arguments"])
        raw["overall_score"] = raw["dimension_scores"]["readability"] = (
            0.74 if request.sample_slot.endswith(":0") else 0.86
        )
        raw["pass"] = request.sample_slot.endswith(":1")
        result.tool_calls[0]["function"]["arguments"] = json.dumps(raw, ensure_ascii=False)
        return result

    monkeypatch.setattr(judge, "complete_text", complete)
    monkeypatch.setattr(
        judge, "chapter_commercial_thresholds", lambda *a: (0.85, {"readability": 0.8})
    )
    from test_commercial_judge_recovery import BODY, sources

    from bestseller.settings import load_settings

    original = await judge.judge_chapter_commercial_quality_stable(
        object(), load_settings(env={}), chapter_number=2, content_md=BODY, samples=3
    )
    before = original.model_dump(mode="json")
    cohort = json.loads(original.raw_excerpt)
    assert [r["report"]["overall_score"] for r in cohort["samples"]] == [0.74, 0.86, 0.0]
    assert [r["valid"] for r in cohort["samples"]] == [True, True, False]
    assert cohort["valid_samples"] == cohort["required_valid_samples"] == 2
    assert not original.passed and not original.infrastructure_failure
    assert original.overall_score == pytest.approx(0.8)
    view, receipt = projection.project_commercial_actions(
        original, sources=sources(), gate_config=GateConfig({}), chapter_number=2
    )
    assert receipt["status"] == "projected" and view is not None
    assert receipt["unknown_sample_slots"] == ["chapter-quality.independent-v2:2"]
    assert view["pass"] is False and view["overall_score"] == original.overall_score
    assert view["dimension_scores"] == original.dimension_scores
    assert original.model_dump(mode="json") == before
    assert not receipt["grants_slots"] and not receipt["authorizes_replay"]
    assert not receipt["scores_modified"] and not receipt["raw_cohort_modified"]
