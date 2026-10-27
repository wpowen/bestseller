"""Native 2/3 abstentions remain zero-authority inputs to bounded repair."""
from copy import deepcopy
import json
from uuid import uuid4

import pytest
from test_commercial_action_projection import slot
from test_commercial_action_projection import stable as stable
from test_rewrite_fact_scope import PARENT, _report, _sources

from bestseller.domain.llm_quality_judge import LLMQualityJudgeResult
from bestseller.services.chapter_draft_candidate_eligibility import (
    _commercial_effective_fact_blockers,
    _proof_sha,
    verified_commercial_actions,
)
from bestseller.services.commercial_judge_recovery import bind_commercial_slot_result
from bestseller.services.commercial_review_tool import submitted_review_payload
from bestseller.services.commercial_source_evidence import commercial_judge_unavailable
from bestseller.services.llm import LLMCompletionResult
from bestseller.services.rewrite_fact_scope import derive_fact_repair_scope
from bestseller.services.rewrite_patch import text_hash

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


def length_slot(src, template):
    envelope = json.loads(template.raw_excerpt)
    raw = '{"wire_version":'
    physical = LLMCompletionResult(content="", provider="fixture", model_name="fixture",
        finish_reason="length", tool_calls=[{"id": "length-return", "function": {
            "name": "submit_chapter_review", "arguments": raw}}])
    _, submission, _ = submitted_review_payload(physical)
    adapter = {k: envelope["adapter_receipt"][k] for k in (
        "adapter_version", "wire_version", "source_binding")}
    adapter.update(status="not_run_output_truncated", submission=submission)
    neutral = commercial_judge_unavailable(
        diagnostic="commercial judge output truncated at structured-review reserve; judge-only repair",
        result=LLMQualityJudgeResult(llm_run_id=str(uuid4())))
    return bind_commercial_slot_result(neutral, raw_report=raw,
        validation_errors=("output: truncated at structured-review reserve",),
        source_receipt=src.receipt, sample_slot="chapter-quality.independent-v2:2", attempt=1,
        adapter_receipt=adapter, evaluation_contract=envelope["evaluation_contract"])


@pytest.fixture
async def report(stable):
    src = _sources()
    first = slot(src, 0, code="ARITHMETIC_CONFLICT", kind="prior_fact_conflict", severity="high")
    second = slot(src, 1, code="ARITHMETIC_CONFLICT", kind="prior_fact_conflict", severity="high")
    aggregate = await stable([first, second, length_slot(src, first)])
    return _report(original=aggregate)


async def test_native_length_abstention_supports_only_verified_answered_actions(report):
    before = deepcopy(report)
    summary = report["evidence_summary"]
    proof = summary["commercial_action_projection"]
    assert proof["unknown_sample_slots"] == ["chapter-quality.independent-v2:2"]
    actions = verified_commercial_actions(summary, PARENT)
    assert actions and all(a["authority"] == "verified_source" for a in actions)
    assert {row["sample_slot"] for a in actions for row in a["lineage"]} == {
        "chapter-quality.independent-v2:0", "chapter-quality.independent-v2:1"}
    blockers = _commercial_effective_fact_blockers((summary,), {"digest": text_hash(PARENT)})
    assert blockers and all(a["effective_action"] == "block" for a in blockers)
    assert report == before
    actions[0]["lineage"][0]["issue"]["required_fix"] = "detached"
    assert report == before


async def test_fact_only_scope_keeps_exact_spans_and_all_zero_authority_flags(report):
    before = deepcopy(report)
    scope = derive_fact_repair_scope(PARENT, report)
    assert scope is not None
    assert scope["outside_scope"] == "preserve_exact_parent_bytes"
    assert scope["fresh_review_required"] is True
    assert all(scope[k] is False for k in ("adoption_authorized", "grades_modified", "grants_credit"))
    assert sum(len(s["quote"].encode()) for s in scope["authorized_spans"]) <= len(PARENT.encode()) * .25
    assert report == before


@pytest.mark.parametrize("fault", [
    "unknown_list", "unknown_status", "unknown_vote", "unknown_score", "unknown_domain",
    "unknown_action", "neutral_run", "neutral_raw", "neutral_original", "quorum", "lineage",
])
async def test_mutated_neutral_or_majority_proof_still_denies_before_edit(report, fault):
    summary = report["evidence_summary"]
    proof = summary["commercial_action_projection"]
    original, view = summary["llm_commercial_judge_original"], summary["llm_commercial_judge"]
    aggregate = json.loads(original["raw_excerpt"])
    unknown = proof["slots"][-1]
    if fault == "unknown_list": proof["unknown_sample_slots"] = []
    elif fault == "unknown_status": unknown["status"] = "verified"
    elif fault == "unknown_vote": aggregate["samples"][-1]["valid"] = True
    elif fault == "unknown_score": unknown["scores_contributed"] = 1
    elif fault == "unknown_domain": unknown["domain_issues_contributed"] = False
    elif fault == "unknown_action": unknown["actions_contributed"] = 1
    elif fault == "neutral_run": unknown["llm_run_id"] = None
    elif fault == "neutral_raw": unknown["raw_report_sha256"] = "f" * 64
    elif fault == "neutral_original": del unknown["original_report"]
    elif fault == "quorum": aggregate["required_valid_samples"] = 1
    else: proof["issue_actions"][0]["lineage"][0]["sample_slot"] = unknown["sample_slot"]
    original["raw_excerpt"] = view["raw_excerpt"] = json.dumps(aggregate)
    proof["original_aggregate_sha256"] = _proof_sha(original)
    proof["projection_sha256"] = _proof_sha(view)
    with pytest.raises(ValueError, match="commercial_action_proof_unverified"):
        verified_commercial_actions(summary, PARENT)
