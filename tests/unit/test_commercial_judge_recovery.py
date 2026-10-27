from __future__ import annotations

from copy import deepcopy
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from bestseller.domain.llm_quality_judge import LLMQualityJudgeResult
from bestseller.infra.db.models import LlmRunModel, ReviewReportModel
from bestseller.services import chapter_llm_quality_judge as judge
from bestseller.services.commercial_judge_recovery import (
    bind_commercial_slot_result,
    load_commercial_retry_context,
    prepare_commercial_slot_retry,
)
from bestseller.services.commercial_review_wire import WIRE_VERSION, parse_commercial_wire
from bestseller.services.commercial_source_evidence import assemble_commercial_sources
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
        kwargs["tool_calls"] = [
            {
                "id": "submission-test",
                "type": "function",
                "function": {"name": "submit_chapter_review", "arguments": raw},
            }
        ]
    return LLMCompletionResult(**kwargs)


BODY = "海员把钥匙放在桌上。"
SLOT = "chapter-quality.independent-v2:1"


def sources(body=BODY, source=None):
    return assemble_commercial_sources(
        chapter_number=2,
        content_md=body,
        source=judge._commercial_source_projection(source or {}, chapter_number=2, content_md=body),
        previous_chapters=[],
    )


def report(score=0.62):
    anchor = next(iter(sources().catalog.values()))
    return {
        "wire_version": WIRE_VERSION,
        "pass": False,
        "overall_score": score,
        "dimension_scores": {"readability": score},
        "issues": [
            {
                "code": "UNEXPLAINED_CHOICE",
                "severity": "high",
                "assessment_kind": "narrative_quality",
                "body_id": anchor["id"],
                "reason": "选择的原因尚不清楚。",
                "required_fix": "说明选择的原因。",
            }
        ],
        "rewrite_plan": {"scope": "chapter"},
    }


def context(result, slot=SLOT):
    envelope = json.loads(result.raw_excerpt)
    return {
        "attempt": 2,
        "prior_report_id": str(uuid4()),
        "slots": {
            slot: {
                "report": result.model_dump(mode="json"),
                "raw_report": envelope["raw_report"],
                "source_binding": envelope["source_binding"],
                "exact_source_audit": envelope["exact_source_audit"],
                "prior_validation_errors": envelope["validation_errors"],
                "evaluation_contract": envelope.get("evaluation_contract") or {},
            }
        },
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("second_invalid", [False, True])
async def test_same_slot_receives_exact_errors_and_original_report_without_score_repair(
    monkeypatch,
    second_invalid,
):
    invalid = report()
    invalid["issues"][0]["body_id"] = "Dforeign:P0001"
    raw = json.dumps(invalid, ensure_ascii=False)
    captures = []

    async def complete(session, settings, request):
        captures.append(request)
        return _review_completion(
            content=raw if len(captures) == 1 or second_invalid else json.dumps(report(0.58)),
            provider="test",
            model_name="test",
            finish_reason="stop",
            llm_run_id=uuid4(),
        )

    monkeypatch.setattr(judge, "complete_text", complete)
    settings = load_settings(env={})
    first = await judge.judge_chapter_commercial_quality(
        object(),
        settings,
        chapter_number=2,
        content_md=BODY,
        sample_slot=SLOT,
        generation_input={"runtime": {"attempt": 1}},
    )
    assert first.infrastructure_failure and first.overall_score == 0.62
    snapshot = first.model_dump(mode="json")
    receipt = json.loads(first.raw_excerpt)
    second = await judge.judge_chapter_commercial_quality(
        object(),
        settings,
        chapter_number=2,
        content_md=BODY,
        sample_slot=SLOT,
        commercial_retry_context=context(first),
        generation_input={"runtime": {"attempt": 2}},
    )
    assert len(captures) == 2 and captures[0].sample_slot == captures[1].sample_slot == SLOT
    assert captures[0].user_prompt != captures[1].user_prompt
    first_source = captures[0].metadata["commercial_source_input_receipt"]
    second_source = captures[1].metadata["commercial_source_input_receipt"]
    assert first_source["source_sha256"] != second_source["source_sha256"]
    for key in (
        "body_sha256",
        "previous_sha256",
        "semantic_source_sha256",
        "source_protocol_sha256",
    ):
        assert first_source[key] == second_source[key]
    feedback = json.loads(captures[1].user_prompt.split("round 2/2]", 1)[1].split("\n", 2)[2])
    assert feedback["raw_invalid_report"] == raw
    assert feedback["validation_errors"] == receipt["validation_errors"]
    assert captures[1].metadata["commercial_evidence_recovery"]["sample_slot"] == SLOT
    if second_invalid:
        assert second.infrastructure_failure and second.overall_score == 0.62
        assert json.loads(second.raw_excerpt)["raw_report"] == raw
    else:
        assert second.quality_rejected and second.overall_score == 0.58
        adapter = json.loads(second.raw_excerpt)["adapter_receipt"]
        assert adapter["status"] == "verified" and not adapter["scores_modified"]
        assert (
            "evidence" not in json.loads(json.loads(second.raw_excerpt)["raw_report"])["issues"][0]
        )
    assert first.model_dump(mode="json") == snapshot


@pytest.mark.asyncio
async def test_verified_slot_is_reused_and_mismatched_slot_body_or_round_never_pays(monkeypatch):
    raw = json.dumps(report())

    async def first_paid_call(*args):
        return _review_completion(
            content=raw, provider="test", model_name="test", finish_reason="stop"
        )

    monkeypatch.setattr(judge, "complete_text", first_paid_call)
    valid = await judge.judge_chapter_commercial_quality(
        object(),
        load_settings(env={}),
        chapter_number=2,
        content_md=BODY,
        sample_slot=SLOT,
    )
    assert not valid.infrastructure_failure

    async def no_paid_call(*args):
        pytest.fail("verified or unbound slots must not pay for a blind repeat")

    monkeypatch.setattr(judge, "complete_text", no_paid_call)
    settings = load_settings(env={})
    reused = await judge.judge_chapter_commercial_quality(
        object(),
        settings,
        chapter_number=2,
        content_md=BODY,
        sample_slot=SLOT,
        commercial_retry_context=context(valid),
    )
    assert reused.model_dump() == valid.model_dump()
    for changed in (
        "slot",
        "body",
        "source",
        "protocol",
        "previous",
        "round",
        "tools",
        "model",
        "threshold",
        "domain",
    ):
        retry = context(valid)
        body, slot = BODY, SLOT
        if changed == "slot":
            slot = "chapter-quality.independent-v2:0"
        if changed == "body":
            body += "他离开了。"
        if changed == "source":
            retry["slots"][SLOT]["source_binding"]["semantic_source_sha256"] = "stale"
        if changed == "protocol":
            retry["slots"][SLOT]["source_binding"]["source_protocol_sha256"] = "stale"
        if changed == "previous":
            retry["slots"][SLOT]["source_binding"]["previous_sha256"] = "stale"
        if changed == "round":
            retry["attempt"] = 3
        if changed in {"tools", "model", "threshold"}:
            contract = retry["slots"][SLOT]["evaluation_contract"]
            contract[
                {
                    "tools": "tools_sha256",
                    "model": "critic_configuration_sha256",
                    "threshold": "min_overall",
                }[changed]
            ] = "changed"
        if changed == "domain":
            retry["slots"][SLOT]["report"]["overall_score"] = 0.99
        stopped = await judge.judge_chapter_commercial_quality(
            object(),
            settings,
            chapter_number=2,
            content_md=body,
            sample_slot=slot,
            commercial_retry_context=retry,
        )
        assert stopped.infrastructure_failure and not stopped.passed


def test_runtime_audit_changes_preserve_binding_but_actual_facts_and_clauses_do_not():
    original = {"runtime": {"attempt": 1}}
    first = sources(source=original)
    valid = bind_commercial_slot_result(
        LLMQualityJudgeResult.model_validate(
            parse_commercial_wire(json.dumps(report()), sources())[0]
        ),
        raw_report=json.dumps(report()),
        validation_errors=(),
        source_receipt=first.receipt,
        sample_slot=SLOT,
        attempt=1,
    )
    retry = context(valid)
    audit_only = sources(source={"runtime": {"attempt": 2}})
    assert first.receipt["source_sha256"] != audit_only.receipt["source_sha256"]
    assert first.receipt["assembled_source_sha256"] != audit_only.receipt["assembled_source_sha256"]
    assert first.receipt["semantic_source_sha256"] == audit_only.receipt["semantic_source_sha256"]
    reused, prompt, _ = prepare_commercial_slot_retry(retry, sample_slot=SLOT, sources=audit_only)
    assert reused == valid and prompt == ""
    for changed in (
        {"established_facts": {"facts": [{"text": "港口已经关闭。"}]}},
        {"approved_chapter_contract": {"hard": True, "source": "user", "text": "保持第一人称。"}},
        {"story_bible": {"participants": [{"name": "海员", "latest_state": "左臂受伤"}]}},
    ):
        with pytest.raises(ValueError, match="source_binding_changed"):
            prepare_commercial_slot_retry(retry, sample_slot=SLOT, sources=sources(source=changed))


def test_narrative_label_cannot_hide_factual_assertions():
    payload = report()
    payload["issues"][0]["source_assertion"] = "这是改写而非逐字来源。"
    assert any(
        "legacy fields forbidden" in error
        for error in parse_commercial_wire(json.dumps(payload), sources())[2]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy", [False, True])
async def test_loads_previous_cohort_with_project_draft_and_paid_source_binding(legacy):
    project_id, chapter_id, draft_id, prior_id, run_id = [uuid4() for _ in range(5)]
    payload = report()
    payload["issues"][0]["body_id"] = "Dforeign:P0001"
    raw = json.dumps(payload)
    invalid = LLMQualityJudgeResult(
        passed=False,
        overall_score=0.62,
        llm_run_id=str(run_id),
        blocking_issues=[
            {"code": "CHAPTER_JUDGE_UNAVAILABLE", "evidence": "original validation failure"}
        ],
        raw_excerpt=raw,
    )
    if not legacy:
        invalid = bind_commercial_slot_result(
            invalid,
            raw_report=raw,
            validation_errors=parse_commercial_wire(json.dumps(payload), sources())[2],
            source_receipt=sources().receipt,
            sample_slot=SLOT,
            attempt=1,
        )
    prior = SimpleNamespace(
        id=prior_id,
        project_id=project_id,
        target_type="chapter",
        target_id=chapter_id,
        structured_output={
            "draft_id": str(draft_id),
            "reviewed_body_fingerprint": {"digest": sources().receipt["body_sha256"]},
            "evidence_summary": {
                "llm_commercial_judge": {
                    "raw_excerpt": json.dumps(
                        {
                            "schema_version": "chapter-quality-aggregation.v1",
                            "samples": [
                                {"sample_slot": SLOT, "report": invalid.model_dump(mode="json")}
                            ],
                        }
                    )
                }
            },
        },
    )
    before = deepcopy(prior.structured_output)
    run_receipt = dict(sources().receipt)
    if legacy:
        run_receipt.pop("semantic_source_sha256")
        run_receipt.pop("source_protocol_sha256")
        run_receipt["prompt_version"] = "v7-deduplicated-source"

    class Session:
        async def get(self, model, identity):
            if model is ReviewReportModel and identity == prior_id:
                return prior
            if model is LlmRunModel and identity == run_id:
                return SimpleNamespace(
                    project_id=project_id,
                    metadata_json={"commercial_source_input_receipt": run_receipt},
                )
            return None

    retry = await load_commercial_retry_context(
        Session(),
        project_id=project_id,
        chapter_id=chapter_id,
        draft_id=draft_id,
        content_md=BODY,
        retry_context={"attempt": 2, "prior_report_id": str(prior_id)},
    )
    reused, prompt, metadata = prepare_commercial_slot_retry(
        retry, sample_slot=SLOT, sources=sources()
    )
    assert reused is None and raw in json.loads(prompt.split("\n")[-1])["raw_invalid_report"]
    assert metadata["validation_errors"] == list(
        parse_commercial_wire(json.dumps(payload), sources())[2]
    )
    assert prior.structured_output == before
    with pytest.raises(ValueError, match="scope_mismatch"):
        await load_commercial_retry_context(
            Session(),
            project_id=uuid4(),
            chapter_id=chapter_id,
            draft_id=draft_id,
            content_md=BODY,
            retry_context={"attempt": 2, "prior_report_id": str(prior_id)},
        )
