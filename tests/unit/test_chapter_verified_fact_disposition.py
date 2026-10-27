"""Native wire/projection and review models; no source/proof validator mocks."""

from copy import deepcopy
import hashlib
import json
import socket

import pytest
from test_commercial_action_projection import slot, sources
from test_commercial_action_projection import stable as stable
from test_commercial_projection_quarantine_abstention import cohort_case, passing_slot

from bestseller.domain.llm_quality_judge import quality_judge_result_from_mapping
from bestseller.domain.review import ChapterReviewFinding, ChapterReviewResult, ChapterReviewScores
from bestseller.services.chapter_verified_fact_disposition import (
    EVIDENCE_KEY,
    SCHEMA,
    UNKNOWN_CODE,
    apply_verified_fact_disposition,
    decide_verified_fact_disposition,
)
from bestseller.services.commercial_action_projection import project_commercial_actions
from bestseller.services.commercial_judge_recovery import bind_commercial_slot_result
from bestseller.services.commercial_review_tool import submitted_review_payload
from bestseller.services.commercial_review_wire import parse_commercial_wire
from bestseller.services.commercial_source_evidence import commercial_judge_unavailable
from bestseller.services.llm import LLMCompletionResult
from bestseller.services.write_gate import GateConfig

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("pure factual disposition cannot use network")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)


def packet(
    *,
    kind="current_fact_conflict",
    code="INVENTORY_GAP_MISMATCH",
    config=None,
    score=0.91,
    issues=True,
):
    src = sources(rule=kind == "constraint_violation")
    original = slot(src, kind=kind, code=code, score=score, issues=issues)
    view, proof = project_commercial_actions(
        original, sources=src, gate_config=config or GateConfig({}, "audit_only"), chapter_number=2
    )
    assert proof["status"] == "projected" and view is not None
    return json.loads(src.text)["current_body"], {
        "commercial_action_projection": proof,
        "llm_commercial_judge": view,
        "llm_commercial_judge_original": original.model_dump(mode="json"),
        "unrelated": {"preserve": [None, "raw author data"]},
    }


def review(evidence, *, verdict="pass"):
    return ChapterReviewResult(
        verdict=verdict,
        severity_max="high",
        scores=ChapterReviewScores(**dict.fromkeys(ChapterReviewScores.model_fields, 0.61)),
        findings=[
            ChapterReviewFinding(category="primary", severity="low", message="Keep original")
        ],
        evidence_summary=deepcopy(evidence),
        rewrite_instructions="Preserve exact primary directions.",
    )


def decide(body, evidence):
    return decide_verified_fact_disposition(content_md=body, evidence=evidence)


def assert_deny_only(decision):
    assert decision["schema"] == SCHEMA
    assert all(
        decision["receipt"][key] is False
        for key in (
            "scores_modified",
            "raw_model_modified",
            "acceptance_authorized",
            "adoption_authorized",
            "dispatch_authorized",
            "grants_credit",
            "authorizes_replay",
        )
    )


@pytest.mark.parametrize(
    "kind", ["current_fact_conflict", "prior_fact_conflict", "constraint_violation"]
)
def test_native_verified_factual_and_rule_blocks_deny_without_regrading(kind):
    body, evidence = packet(kind=kind)
    before = deepcopy(evidence)
    result = decide(body, evidence)
    assert result["status"] == "blocked"
    assert result["blocking_codes"] == ["INVENTORY_GAP_MISMATCH"]
    assert (
        result["instructions"]
        == "INVENTORY_GAP_MISMATCH: Delete the unapproved astronomical discovery."
    )
    assert result["receipt"]["body_sha256"] == hashlib.sha256(body.encode()).hexdigest()
    assert (
        result["receipt"]["source_binding"]
        == evidence["commercial_action_projection"]["source_binding"]
    )
    assert_deny_only(result)
    assert evidence == before and decide(body, evidence) == result


@pytest.mark.parametrize(
    "config",
    [
        GateConfig({"ENDING_SENTENCE_WEAK": "block"}),
        GateConfig({"ENDING_SENTENCE_WEAK": "audit_only"}),
    ],
)
def test_craft_action_is_never_promoted_to_factual_veto(config):
    body, evidence = packet(kind="narrative_quality", code="ENDING_SENTENCE_WEAK", config=config)
    result = decide(body, evidence)
    assert result["status"] == "clear" and result["blocking_codes"] == []
    assert result["instructions"] == ""
    assert_deny_only(result)


def test_retained_threshold_failure_and_empty_native_projection_do_not_become_fact_blocks():
    for score in (0.51, 0.91):
        body, evidence = packet(score=score, issues=False)
        result = decide(body, evidence)
        assert result["status"] == "clear" and result["blocking_codes"] == []
        assert_deny_only(result)


@pytest.mark.parametrize("evidence", [{}, {"llm_commercial_judge": {"pass": False}}])
def test_absent_projection_keeps_legacy_review_exact(evidence):
    original = review(evidence)
    before = original.model_dump(mode="json")
    assert decide("legacy body", evidence)["status"] == "no_projection"
    copied = apply_verified_fact_disposition(original, content_md="legacy body")
    assert copied is not original and copied.model_dump(mode="json") == before
    assert original.model_dump(mode="json") == before


@pytest.mark.parametrize(
    "fault",
    [
        "empty_body",
        "changed_body",
        "null_projection",
        "schema",
        "status",
        "body_hash",
        "payload_hash",
        "scores_flag",
        "raw_flag",
        "slots_flag",
        "replay_flag",
        "missing_original",
        "missing_view",
        "authority",
        "illegal_kind",
        "count",
        "missing_index",
        "bool_index",
        "index_range",
        "mapped_code",
        "changed_original",
        "changed_lineage",
    ],
)
def test_present_malformed_native_projection_is_unknown_without_repair_instruction(fault):
    body, evidence = packet()
    proof = evidence["commercial_action_projection"]
    view = evidence["llm_commercial_judge"]
    if fault == "empty_body":
        body = ""
    elif fault == "changed_body":
        body += "\nChanged."
    elif fault == "null_projection":
        evidence["commercial_action_projection"] = None
    elif fault in {"schema", "status"}:
        proof[fault] = "unsupported"
    elif fault == "body_hash":
        proof["source_binding"]["body_sha256"] = "0" * 64
    elif fault == "payload_hash":
        proof["projection_sha256"] = "0" * 64
    elif fault in {"scores_flag", "raw_flag", "slots_flag", "replay_flag"}:
        proof[
            {
                "scores_flag": "scores_modified",
                "raw_flag": "raw_cohort_modified",
                "slots_flag": "grants_slots",
                "replay_flag": "authorizes_replay",
            }[fault]
        ] = True
    elif fault == "missing_original":
        evidence.pop("llm_commercial_judge_original")
    elif fault == "missing_view":
        evidence.pop("llm_commercial_judge")
    elif fault == "authority":
        proof["issue_actions"][0]["authority"] = "craft_only"
    elif fault == "illegal_kind":
        proof["issue_actions"][0]["assessment_kind"] = "established_fact_conflict"
    elif fault == "count":
        proof["effective_blocking_count"] += 1
    elif fault == "changed_original":
        evidence["llm_commercial_judge_original"]["overall_score"] = 0.99
    elif fault == "changed_lineage":
        proof["issue_actions"][0]["lineage"][0]["issue"]["required_fix"] = "forged directive"
    else:
        issue = view["blocking_issues"][0]
        if fault == "missing_index":
            issue.pop("action_receipt_index")
        elif fault == "bool_index":
            issue["action_receipt_index"] = True
        elif fault == "index_range":
            issue["action_receipt_index"] = 100
        else:
            issue["code"] = "FORGED_CODE"
        # An internally rehashed payload still needs its exact native action mapping.
        proof["projection_sha256"] = hashlib.sha256(
            json.dumps(
                view,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode()
        ).hexdigest()
    before = deepcopy(evidence)
    result = decide(body, evidence)
    assert result["status"] == "unknown"
    assert result["blocking_codes"] == [UNKNOWN_CODE] and result["instructions"] == ""
    assert_deny_only(result)
    assert evidence == before


def test_apply_blocks_even_after_primary_pass_without_mutating_scores_raw_or_directions():
    body, evidence = packet()
    original = review(evidence)
    before = original.model_dump(mode="json")
    derived = apply_verified_fact_disposition(original, content_md=body)
    assert derived.verdict == "rewrite" and derived.severity_max == "critical"
    assert derived.scores == original.scores
    assert derived.rewrite_instructions.startswith(original.rewrite_instructions + "\n\n")
    assert "INVENTORY_GAP_MISMATCH" in derived.rewrite_instructions
    assert derived.evidence_summary[EVIDENCE_KEY] == decide(body, evidence)
    assert all(derived.evidence_summary[key] == value for key, value in evidence.items())
    assert (
        derived.findings[0] == original.findings[0] and derived.findings[-1].severity == "critical"
    )
    assert original.model_dump(mode="json") == before
    assert apply_verified_fact_disposition(derived, content_md=body) == derived


@pytest.mark.parametrize("existing_unknown", [False, True])
def test_unknown_evidence_never_returns_prose_repair_instructions(existing_unknown):
    body, evidence = packet()
    evidence["commercial_action_projection"]["status"] = "unsupported"
    original = review(evidence, verdict="unknown" if existing_unknown else "pass")
    before = original.model_dump(mode="json")
    derived = apply_verified_fact_disposition(original, content_md=body)
    assert derived.verdict == "unknown" and derived.rewrite_instructions == ""
    assert derived.scores == original.scores
    assert derived.evidence_summary[EVIDENCE_KEY]["status"] == "unknown"
    assert original.model_dump(mode="json") == before
    assert apply_verified_fact_disposition(derived, content_md=body) == derived


def test_existing_unknown_cannot_be_lowered_to_rewrite_by_known_fact_block():
    body, evidence = packet()
    derived = apply_verified_fact_disposition(review(evidence, verdict="unknown"), content_md=body)
    assert derived.verdict == "unknown" and derived.rewrite_instructions == ""
    assert derived.evidence_summary[EVIDENCE_KEY]["status"] == "blocked"


def test_clear_disposition_does_not_upgrade_existing_rewrite_or_unknown():
    body, evidence = packet(kind="narrative_quality")
    for verdict in ("rewrite", "unknown"):
        original = review(evidence, verdict=verdict)
        derived = apply_verified_fact_disposition(original, content_md=body)
        assert derived.verdict == verdict and derived.scores == original.scores
        assert derived.findings == original.findings
        assert derived.rewrite_instructions == original.rewrite_instructions
        assert derived.evidence_summary[EVIDENCE_KEY]["status"] == "clear"


async def quorum_packet(stable, *, quarantine=True, hard=False, round_no=1):
    if quarantine:
        src, original, closure = await cohort_case(stable, hard=hard, round_no=round_no)
    else:
        src = sources()
        original = await stable(
            [
                passing_slot(src, 0, 0.855),
                slot(src, 1, kind="current_fact_conflict", score=0.86)
                if hard
                else passing_slot(src, 1, 0.86),
                slot(src, 2, score=0.01, unknown=True),
            ]
        )
        closure = None
    view, proof = project_commercial_actions(
        original,
        sources=src,
        gate_config=GateConfig({}),
        chapter_number=2,
        verified_quarantine_slots=closure,
    )
    assert proof["status"] == "projected" and view is not None
    return (
        json.loads(src.text)["current_body"],
        {
            "commercial_action_projection": proof,
            "llm_commercial_judge": view,
            "llm_commercial_judge_original": original.model_dump(mode="json"),
        },
        closure,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("quarantine,round_no", [(False, 1), (True, 1)])
@pytest.mark.parametrize("hard", [False, True])
async def test_native_available_quorums_retain_neutral_abstention_without_losing_fact_blocks(
    stable,
    quarantine,
    round_no,
    hard,
):
    from bestseller.services.chapter_draft_candidate_eligibility import _verified_commercial_actions

    body, evidence, closure = await quorum_packet(
        stable, quarantine=quarantine, round_no=round_no, hard=hard
    )
    before, closure_before = deepcopy(evidence), deepcopy(closure)
    # The old stricter fact-only-edit API remains unchanged and rejects this.
    with pytest.raises(ValueError, match="commercial_action_proof_unverified"):
        _verified_commercial_actions(
            (evidence,), {"digest": hashlib.sha256(body.encode()).hexdigest()}
        )
    result = decide(body, evidence)
    assert result["status"] == ("blocked" if hard else "clear")
    assert result["blocking_codes"] == (
        [evidence["llm_commercial_judge"]["blocking_issues"][0]["code"]] if hard else []
    )
    assert evidence["llm_commercial_judge"]["overall_score"] == (0.855 + 0.86) / 2
    assert_deny_only(result)
    assert evidence == before and closure == closure_before
    copied = apply_verified_fact_disposition(review(evidence), content_md=body)
    assert copied.verdict == ("rewrite" if hard else "pass")
    assert apply_verified_fact_disposition(copied, content_md=body) == copied


def rehash(evidence):
    from bestseller.domain.llm_quality_judge import LLMQualityJudgeResult
    from bestseller.services.chapter_draft_candidate_eligibility import _proof_sha

    original = evidence["llm_commercial_judge_original"]
    proof = evidence["commercial_action_projection"]
    proof["original_aggregate_sha256"] = _proof_sha(
        LLMQualityJudgeResult.model_validate(original).model_dump(mode="json")
    )
    proof["projection_sha256"] = _proof_sha(evidence["llm_commercial_judge"])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault",
    [
        "validity",
        "quorum",
        "valid_count",
        "median",
        "representative",
        "unknown_slots",
        "neutral_score",
        "neutral_action",
        "proof_hash",
        "neutral_report_hash",
        "scope_identity",
        "round",
        "source_binding",
        "contract",
        "answered_lineage",
        "action_index",
    ],
)
async def test_compact_abstention_and_answered_lineage_must_agree_even_after_rehash(stable, fault):
    body, evidence, _ = await quorum_packet(stable, hard=True)
    proof, view = evidence["commercial_action_projection"], evidence["llm_commercial_judge"]
    aggregate = json.loads(evidence["llm_commercial_judge_original"]["raw_excerpt"])
    neutral = proof["slots"][2]
    if fault == "validity":
        aggregate["samples"][2]["valid"] = True
    elif fault == "quorum":
        aggregate["required_valid_samples"] = 1
    elif fault == "valid_count":
        aggregate["valid_samples"] = 3
    elif fault == "median":
        aggregate["median_overall"] = 0.99
    elif fault == "representative":
        aggregate["representative_sample_slot"] = "chapter-quality.independent-v2:2"
    elif fault == "unknown_slots":
        proof["unknown_sample_slots"] = []
    elif fault == "neutral_score":
        neutral["scores_contributed"] = 1
    elif fault == "neutral_action":
        neutral["actions_contributed"] = 1
    elif fault == "proof_hash":
        neutral["quarantine_proof_sha256"] = "0" * 64
    elif fault == "neutral_report_hash":
        neutral["original_report_sha256"] = "0" * 64
    elif fault == "scope_identity":
        neutral["scope_id"] = ""
    elif fault == "round":
        neutral["round"] = 2
    elif fault == "source_binding":
        neutral["source_binding"]["previous_sha256"] = "0" * 64
    elif fault == "contract":
        neutral["evaluation_contract"]["min_overall"] = 0.1
    elif fault == "answered_lineage":
        proof["issue_actions"][0]["lineage"][0]["sample_slot"] = "chapter-quality.independent-v2:2"
    else:
        view["blocking_issues"][0]["action_receipt_index"] = True
    raw = json.dumps(aggregate)
    evidence["llm_commercial_judge_original"]["raw_excerpt"] = raw
    view["raw_excerpt"] = raw
    rehash(evidence)
    before = deepcopy(evidence)
    result = decide(body, evidence)
    assert result["status"] == "unknown" and result["blocking_codes"] == [UNKNOWN_CODE]
    assert result["instructions"] == "" and evidence == before


@pytest.mark.asyncio
async def test_insufficient_native_quorum_remains_unknown_and_cannot_authorize_rewrite(stable):
    src = sources()
    original = await stable([slot(src, 0), slot(src, 1, unknown=True), slot(src, 2, unknown=True)])
    view, proof = project_commercial_actions(
        original, sources=src, gate_config=GateConfig({}), chapter_number=2
    )
    assert view is None and proof["reason"] == "insufficient_verified_quorum"
    evidence = {
        "commercial_action_projection": proof,
        "llm_commercial_judge": original.model_dump(mode="json"),
        "llm_commercial_judge_original": original.model_dump(mode="json"),
    }
    result = decide(json.loads(src.text)["current_body"], evidence)
    assert result["status"] == "unknown" and result["instructions"] == ""


def real_wire_slot(src, number, *, fact=False, invalid_kind=None):
    """Bind actual full-source parsing; valid CITE/RULE IDs stay unmodified."""
    proto = slot(
        src,
        number,
        issues=fact or invalid_kind is not None,
        kind=invalid_kind or "current_fact_conflict",
        score=0.855 if number == 0 else 0.86,
        code="EXISTING_P1_BLOCK",
    )
    envelope = json.loads(proto.raw_excerpt)
    wire = json.loads(envelope["raw_report"])
    wire["pass"] = not fact and invalid_kind is None
    for check in wire.get("checks", {}).values():
        check["result"] = "PASS"  # Actual model wire fixture, parsed against genuine RULE sources.
    if invalid_kind is not None:
        currents = [entry for entry in src.catalog.values() if entry["kind"] == "current"]
        issue = wire["issues"][0]
        issue.update(body_id=currents[1]["id"], severity="invalid-severity")
        if invalid_kind == "current_fact_conflict":
            issue["source_id"] = currents[1]["id"]
    raw = json.dumps(wire)
    parsed, adapter, errors = parse_commercial_wire(raw, src)
    assert errors == (("issues/0: invalid severity",) if invalid_kind else ())
    contract = envelope["evaluation_contract"]
    result = quality_judge_result_from_mapping(
        parsed,
        scope="chapter",
        min_overall=contract["min_overall"],
        min_dimensions=contract["min_dimensions"],
        llm_run_id=proto.llm_run_id,
    )
    if errors:
        result = commercial_judge_unavailable(diagnostic="native invalid severity", result=result)
    physical = LLMCompletionResult(
        content="",
        provider="fixture",
        model_name="fixture",
        tool_calls=[
            {"id": "retained-call", "function": {"name": "submit_chapter_review", "arguments": raw}}
        ],
    )
    _, submission, submission_errors = submitted_review_payload(physical)
    assert not submission_errors
    adapter["submission"] = submission
    return bind_commercial_slot_result(
        result,
        raw_report=raw,
        validation_errors=errors,
        source_receipt=src.receipt,
        sample_slot=f"chapter-quality.independent-v2:{number}",
        attempt=1,
        adapter_receipt=adapter,
        evaluation_contract=contract,
    )


async def valid_citation_quorum(stable, *, kind, hard):
    src = sources(rule=kind == "constraint_violation")
    original = await stable(
        [
            real_wire_slot(src, 0),
            real_wire_slot(src, 1, fact=hard),
            real_wire_slot(src, 2, invalid_kind=kind),
        ]
    )
    view, proof = project_commercial_actions(
        original, sources=src, gate_config=GateConfig({}), chapter_number=2
    )
    assert proof["status"] == "projected" and view is not None
    return json.loads(src.text)["current_body"], {
        "commercial_action_projection": proof,
        "llm_commercial_judge": view,
        "llm_commercial_judge_original": original.model_dump(mode="json"),
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind", ["current_fact_conflict", "prior_fact_conflict", "constraint_violation"]
)
@pytest.mark.parametrize("hard", [False, True])
async def test_unknown_valid_citations_do_not_require_unretained_catalog_entries(
    stable, kind, hard
):
    body, evidence = await valid_citation_quorum(stable, kind=kind, hard=hard)
    before = deepcopy(evidence)
    aggregate = json.loads(evidence["llm_commercial_judge_original"]["raw_excerpt"])
    unavailable = json.loads(aggregate["samples"][2]["report"]["raw_excerpt"])
    assert unavailable["validation_errors"] == ["issues/0: invalid severity"]
    result = decide(body, evidence)
    assert result["status"] == ("blocked" if hard else "clear")
    assert result["blocking_codes"] == (["EXISTING_P1_BLOCK"] if hard else [])
    assert all(
        row["sample_slot"] != "chapter-quality.independent-v2:2"
        for action in evidence["commercial_action_projection"]["issue_actions"]
        for row in action["lineage"]
    )
    assert evidence["llm_commercial_judge"]["overall_score"] == (0.855 + 0.86) / 2
    assert_deny_only(result)
    assert evidence == before


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault",
    [
        "raw_hash",
        "source_binding",
        "source_audit",
        "contract",
        "missing_errors",
        "adapter_status",
        "submission_calls",
        "executed_tools",
        "typed_score",
        "typed_direction",
        "typed_code",
        "slot_envelope_hash",
        "slot_raw_hash",
    ],
)
async def test_ordinary_unknown_identity_and_neutral_protocol_remain_strict(stable, fault):
    body, evidence = await valid_citation_quorum(stable, kind="prior_fact_conflict", hard=False)
    assert decide(body, evidence)["status"] == "clear"
    proof, view = evidence["commercial_action_projection"], evidence["llm_commercial_judge"]
    aggregate = json.loads(evidence["llm_commercial_judge_original"]["raw_excerpt"])
    report = aggregate["samples"][2]["report"]
    envelope = json.loads(report["raw_excerpt"])
    if fault == "raw_hash":
        envelope["raw_report_sha256"] = "0" * 64
    elif fault == "source_binding":
        envelope["source_binding"]["body_sha256"] = "0" * 64
    elif fault == "source_audit":
        envelope["exact_source_audit"]["source_sha256"] = "0" * 64
    elif fault == "contract":
        envelope["evaluation_contract"]["min_overall"] = 0.1
    elif fault == "missing_errors":
        envelope["validation_errors"] = []
    elif fault == "adapter_status":
        envelope["adapter_receipt"]["status"] = "verified"
    elif fault == "submission_calls":
        envelope["adapter_receipt"]["submission"]["calls_received"] = 2
    elif fault == "executed_tools":
        envelope["adapter_receipt"]["submission"]["tools_executed"] = 1
    elif fault == "typed_score":
        report["overall_score"] = 0.5
    elif fault == "typed_direction":
        report["rewrite_plan"]["scope"] = "chapter"
    elif fault == "typed_code":
        report["blocking_issues"][0]["code"] = "FORGED_FACT"
    report["raw_excerpt"] = json.dumps(envelope)
    from bestseller.services.chapter_draft_candidate_eligibility import _proof_sha

    proof["slots"][2]["envelope_sha256"] = _proof_sha(envelope)
    if fault == "slot_envelope_hash":
        proof["slots"][2]["envelope_sha256"] = "0" * 64
    elif fault == "slot_raw_hash":
        proof["slots"][2]["raw_report_sha256"] = "0" * 64
    raw = json.dumps(aggregate)
    evidence["llm_commercial_judge_original"]["raw_excerpt"] = raw
    view["raw_excerpt"] = raw
    rehash(evidence)
    before = deepcopy(evidence)
    result = decide(body, evidence)
    assert result["status"] == "unknown" and result["instructions"] == ""
    assert evidence == before
