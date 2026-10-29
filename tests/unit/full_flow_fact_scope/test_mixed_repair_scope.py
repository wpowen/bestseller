"""RED contract: derive_mixed_repair_scope(parent_text, full_report) -> dict.

Propose verified-mixed-repair-scope.v1, retaining the full report hash, routing,
all verified commercial actions/lineage and all original primary citations.
repair_items reference those complete lists by source_index within ONE task;
authorized_spans are their exact unique paragraph union, capped at 25% UTF-8.
Audit/unresolved never acquire edit spans. No report slicing, synthetic PASS,
new task, round, budget, adoption, grade or credit is authorized here.

Non-mixed/unbounded/unknown input raises mixed_repair_scope_unavailable with
the existing safe input diagnostic shape. Native commercial and primary proof
errors propagate. Existing fact-only derivation remains unchanged. The caller
still authenticates the actual task/report/body and paid return; Volta must
recompute this independent schema in its native derivation consumer.

These fixtures replay real parsers/projectors/verifiers with synthetic returns.
No verifier is stubbed. No DB, network, paid or native commercial quality proof
is established. Main runs RED before source implementation is authorized.
"""

from copy import deepcopy
import hashlib
import json
import socket

import pytest
from sqlalchemy.engine import Engine
from test_commercial_action_projection import slot
from test_commercial_action_projection import stable as stable
from test_commercial_neutral_quorum_consumer import report as neutral_report
from test_primary_review_submission import completion, payload
from test_rewrite_fact_scope import FACT, PARENT, _report, _sources

from bestseller.domain.llm_quality_judge import quality_judge_result_from_mapping
from bestseller.services import drafts, llm
from bestseller.services import rewrite_fact_scope as scopes
from bestseller.services.chapter_draft_candidate_eligibility import (
    _proof_sha,
    verified_commercial_actions,
)
from bestseller.services.commercial_action_projection import project_commercial_actions
from bestseller.services.commercial_judge_recovery import bind_commercial_slot_result
from bestseller.services.commercial_review_tool import submitted_review_payload
from bestseller.services.commercial_review_wire import parse_commercial_wire
from bestseller.services.critic_evidence_gate import build_critic_body_anchors
from bestseller.services.llm import LLMCompletionResult
from bestseller.services.primary_review_submission import parse_primary_review_submission
from bestseller.services.retained_rewrite_projection import project_retained_rewrite
from bestseller.services.rewrite_patch import text_hash
from bestseller.services.write_gate import GateConfig

pytestmark = pytest.mark.unit
__all__ = ["neutral_report", "stable"]

BODY = (
    "\n\n".join(
        [
            FACT,
            "Mara kept the copper key beside the lamp.",
            "The witness paused before naming the empty seat.",
            "Audit-only observation: the window faced the quiet street.",
            *(
                f"Protected paragraph {index}: Cafe\u0301, 咖啡 ☕🙂; retain its punctuation, "
                "spacing and independent account of the journey."
                for index in range(8)
            ),
        ]
    )
    + "  \t\n"
)
FLAGS = (
    "body_edit_authorized",
    "unrestricted_fallback_allowed",
    "adoption_authorized",
    "grades_modified",
    "grants_credit",
)
FIELDS = {
    "schema",
    "parent_sha256",
    "report_payload_sha256",
    "primary_receipt_sha256",
    "action_projection_sha256",
    "authority",
    "routing",
    "commercial_actions",
    "primary_citations",
    "repair_items",
    "authorized_spans",
    "outside_scope",
    "fresh_review_required",
    *FLAGS,
}


@pytest.fixture(autouse=True)
def no_external_io(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("mixed-scope unit forbids DB/network/model execution")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(Engine, "raw_connection", forbidden)
    monkeypatch.setattr(llm, "complete_text", forbidden)


def canonical(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def primary(report, body=BODY, *, verdict="rewrite", numbers=(2,), duplicate_paragraph=False):
    value = payload(numbers)
    value["verdict"] = verdict
    if duplicate_paragraph:
        other = deepcopy(value["citations"][0])
        other.update(dimension="readability", reason="A second independent editorial observation.")
        value["citations"].append(other)
    original = completion(value, raw_message=True).__dict__
    text, receipt, errors = parse_primary_review_submission(original, body)
    assert not errors and receipt["status"] == "verified"
    report.update(
        primary_submission_original=original,
        primary_submission_receipt=receipt,
        critic_response=text,
    )


def mixed_report(body=BODY, *, kind="prior_fact_conflict", numbers=(2,)):
    report = _report(body, kind=kind)
    primary(report, body, numbers=numbers)
    return report


def projectable_mixed_case(*, newline="\n"):
    """Raw-return component positive: P1 fact + P3 primary; P2 stays protected.

    CRLF is legal here only while retained in the raw return. Sanitizer newline
    conversion is a separate negative, not permission to normalize the parent.
    The complete original report/verdict is constructed by the native parsers.
    """
    assert newline in {"\n", "\r\n"}
    body = BODY.replace("\n", newline)
    report = mixed_report(body, numbers=(3,))
    bounded = body.replace("totals five", "totals four").replace("paused before", "waited before")
    returned = bounded.replace("Protected paragraph 7", "Tampered paragraph 7")
    return body, report, bounded, returned


def native_craft_report(*, primary_verdict="pass", craft_number=3, audit=False):
    """Construct fresh wire evidence through native parsers, not cached actions."""
    sources = _sources(BODY)
    original = slot(sources, code="OPAQUE_FACT_47", kind="prior_fact_conflict")
    envelope = json.loads(original.raw_excerpt)
    wire = json.loads(envelope["raw_report"])
    anchors = build_critic_body_anchors(BODY)
    craft = {
        "code": "ENDING_SENTENCE_WEAK",
        "severity": "critical",
        "assessment_kind": "narrative_quality",
        "body_id": anchors[craft_number - 1]["id"],
        "reason": "The cited paragraph leaves the emotional consequence unclear.",
        "required_fix": "Clarify the character's response in the cited paragraph.",
    }
    wire["issues"].append(craft)
    if audit:
        wire["issues"].append(
            {
                **craft,
                "code": "UNREGISTERED_NARRATIVE",
                "body_id": anchors[3]["id"],
            }
        )
    raw = canonical(wire)
    parsed, adapter, errors = parse_commercial_wire(raw, sources)
    assert not errors
    result = quality_judge_result_from_mapping(
        parsed,
        scope="chapter",
        min_overall=0.82,
        min_dimensions={"readability": 0.8},
        llm_run_id=original.llm_run_id,
    )
    physical = LLMCompletionResult(
        content="",
        provider="fixture",
        model_name="fixture",
        tool_calls=[
            {
                "id": "mixed-retained-call",
                "function": {
                    "name": "submit_chapter_review",
                    "arguments": raw,
                },
            }
        ],
    )
    _, submission, errors = submitted_review_payload(physical)
    assert not errors
    adapter["submission"] = submission
    original = bind_commercial_slot_result(
        result,
        raw_report=raw,
        validation_errors=(),
        source_receipt=sources.receipt,
        sample_slot="chapter-quality.independent-v2:0",
        attempt=1,
        adapter_receipt=adapter,
        evaluation_contract=envelope["evaluation_contract"],
    )
    report = _report(BODY, original=original)
    view, proof = project_commercial_actions(
        original,
        sources=sources,
        chapter_number=2,
        gate_config=GateConfig({"ENDING_SENTENCE_WEAK": "block"}),
    )
    assert proof["status"] == "projected"
    report["evidence_summary"].update(
        commercial_action_projection=proof,
        llm_commercial_judge=view,
    )
    primary(report, verdict=primary_verdict)
    return report


def derive(body, report):
    function = getattr(scopes, "derive_mixed_repair_scope", None)
    assert callable(function), "derive_mixed_repair_scope API missing"
    before = deepcopy(report)
    try:
        return function(body, report)
    finally:
        assert report == before


def assert_scope(report, numbers, *, body=BODY):
    result = derive(body, report)
    assert isinstance(result, dict) and set(result) == FIELDS
    assert result["schema"] == "verified-mixed-repair-scope.v1"
    assert result["authority"] == "backend_edit_scope_for_verified_mixed_repair"
    assert result["parent_sha256"] == text_hash(body)
    assert result["report_payload_sha256"] == text_hash(canonical(report))
    receipt = report["primary_submission_receipt"]
    assert result["primary_receipt_sha256"] == text_hash(canonical(receipt))
    summary = report["evidence_summary"]
    assert (
        result["action_projection_sha256"]
        == summary["commercial_action_projection"]["projection_sha256"]
    )
    route = scopes.reader_first_repair_route(body, report)
    assert route["route"] == "mixed"
    assert result["routing"] == route  # Classification and its original debt stay intact.
    actions = list(verified_commercial_actions(summary, body))
    assert result["commercial_actions"] == actions
    citations = receipt["parsed_payload"]["citations"]
    assert result["primary_citations"] == citations
    catalog = list(build_critic_body_anchors(body))
    spans = [
        {
            "id": row["id"],
            "start": row["start"],
            "end": row["end"],
            "quote": row["text"],
            "sha256": text_hash(row["text"]),
        }
        for number, row in enumerate(catalog, 1)
        if number in numbers
    ]
    assert result["authorized_spans"] == spans
    assert sum(len(row["quote"].encode()) for row in spans) <= len(body.encode()) * 0.25
    expected_items = []
    for index, action in enumerate(actions):
        if index in route["audit_action_indexes"]:
            continue
        kind = "fact" if index in route["fact_action_indexes"] else "editorial"
        ids = {
            citation["id"]
            for origin in action["lineage"]
            for citation in origin["issue"]["body_citations"]
        }
        expected_items.append(
            {
                "kind": kind,
                "source": "commercial",
                "source_index": index,
                "span_ids": [row["id"] for row in catalog if row["id"] in ids],
            }
        )
    if receipt["parsed_payload"]["verdict"] == "rewrite":
        expected_items.extend(
            {
                "kind": "editorial",
                "source": "primary",
                "source_index": index,
                "span_ids": [catalog[citation["paragraph_number"] - 1]["id"]],
            }
            for index, citation in enumerate(citations)
        )
    assert result["repair_items"] == expected_items
    assert {key for item in expected_items for key in item["span_ids"]} == {
        row["id"] for row in spans
    }
    assert result["outside_scope"] == "preserve_exact_parent_bytes"
    assert result["fresh_review_required"] is True
    assert all(result[key] is False for key in FLAGS)
    return result


def denied(body, report):
    with pytest.raises(ValueError, match=r"^mixed_repair_scope_unavailable$") as caught:
        derive(body, report)
    error = caught.value
    assert error.code == "mixed_repair_scope_unavailable"
    assert error.args == ("mixed_repair_scope_unavailable",)
    expected = []
    for field, value, encoding in (
        ("parent_text", body, "utf8"),
        ("report_payload", report, "canonical_json"),
    ):
        raw = (value if encoding == "utf8" else canonical(value)).encode("utf-8")
        expected.append(
            {
                "field": field,
                "type": type(value).__name__,
                "encoding": encoding,
                "length": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(),
            }
        )
    assert error.diagnostics == expected
    assert error.differences
    for difference in error.differences:
        assert set(difference) == {"field", "expected", "actual"}
        assert difference["field"].startswith("mixed_scope.")
        for operand in (difference["expected"], difference["actual"]):
            assert set(operand) == {"type", "length", "sha256"}
            assert isinstance(operand["type"], str)
            assert operand["length"] is None or type(operand["length"]) is int
            assert len(operand["sha256"]) == 64
    return error


@pytest.mark.parametrize(
    "kind",
    [
        "current_fact_conflict",
        "prior_fact_conflict",
        "constraint_violation",
    ],
)
def test_primary_rewrite_and_verified_fact_split_without_manufacturing_primary_pass(kind):
    report = mixed_report(kind=kind)
    before = deepcopy(report)
    assert scopes.derive_fact_repair_scope(BODY, report) is None
    with pytest.raises(ValueError, match=r"^fact_repair_scope_unavailable$"):
        scopes.require_fact_repair_scope(BODY, report)
    assert_scope(report, {1, 2})
    assert report == before
    assert report["primary_submission_receipt"]["parsed_payload"]["verdict"] == "rewrite"
    assert report["evidence_summary"]["llm_commercial_judge_original"]["passed"] is False


@pytest.mark.parametrize("verdict", ["pass", "rewrite"])
def test_native_craft_and_facts_split_with_all_original_lineage(verdict):
    report = native_craft_report(primary_verdict=verdict, audit=True)
    assert_scope(report, {1, 3} | ({2} if verdict == "rewrite" else set()))


def test_duplicate_paragraph_proposals_keep_every_issue_but_only_one_edit_span():
    report = mixed_report()
    primary(report, duplicate_paragraph=True)
    result = assert_scope(report, {1, 2})
    assert len(result["primary_citations"]) == 2
    assert len(result["repair_items"]) == 3


def test_fact_and_editorial_overlap_keeps_both_items_without_double_counting_span():
    report = native_craft_report(primary_verdict="rewrite", craft_number=1)
    primary(report, numbers=(1,))
    result = assert_scope(report, {1})
    assert len(result["repair_items"]) == 3


def test_noncontiguous_citations_never_authorize_the_paragraph_between_them():
    result = assert_scope(mixed_report(numbers=(3,)), {1, 3})
    gap = build_critic_body_anchors(BODY)[1]
    assert all(
        not (row["start"] <= gap["start"] < row["end"]) for row in result["authorized_spans"]
    )


def test_mixed_union_consumes_existing_projection_with_exact_unicode_and_crlf_outside():
    body, report, bounded, returned = projectable_mixed_case(newline="\r\n")
    scope = assert_scope(report, {1, 3}, body=body)
    result = project_retained_rewrite(
        body,
        returned,
        authorized_spans=scope["authorized_spans"],
        expected_parent_sha256=scope["parent_sha256"],
        expected_return_sha256=text_hash(returned),
    )
    assert result.accepted, result.failure_codes
    assert result.candidate_text.encode("utf-8") == bounded.encode("utf-8")
    assert result.receipt.discarded_opcodes


def test_repeated_inter_span_separator_still_rejects_ambiguous_alignment():
    body = BODY
    scope = assert_scope(mixed_report(body), {1, 2}, body=body)
    returned = body.replace("totals five", "totals four").replace("copper key", "silver key")
    result = project_retained_rewrite(
        body, returned, authorized_spans=scope["authorized_spans"],
        expected_parent_sha256=scope["parent_sha256"], expected_return_sha256=text_hash(returned),
    )
    assert result.accepted is False
    assert result.failure_codes == ("ambiguous_alignment",)
    assert result.candidate_text == body


def test_crlf_sanitizer_conversion_still_rejects_scope_alignment():
    body, report, _, returned = projectable_mixed_case(newline="\r\n")
    scope = assert_scope(report, {1, 3}, body=body)
    sanitized = drafts.sanitize_novel_markdown_content(returned, language="en")
    assert sanitized != returned and "\r\n" not in sanitized
    result = project_retained_rewrite(
        body, sanitized, authorized_spans=scope["authorized_spans"],
        expected_parent_sha256=scope["parent_sha256"], expected_return_sha256=text_hash(sanitized),
    )
    assert result.accepted is False
    assert result.failure_codes == ("ambiguous_alignment",)
    assert result.candidate_text == body


def test_scope_is_detached_and_binds_the_entire_untrimmed_report():
    report = native_craft_report(primary_verdict="rewrite", audit=True)
    report["unrelated_retained_debt"] = {"status": "Unknown", "keep": [1, 9]}
    before = deepcopy(report)
    result = assert_scope(report, {1, 2, 3})
    result["commercial_actions"][0]["lineage"][0]["issue"]["required_fix"] = "detached"
    result["primary_citations"][0]["reason"] = "detached"
    result["repair_items"][0]["span_ids"].clear()
    result["authorized_spans"][0]["quote"] = "detached"
    assert report == before
    report["unrelated_retained_debt"]["keep"].append(7)
    changed = assert_scope(report, {1, 2, 3})
    assert changed["report_payload_sha256"] != result["report_payload_sha256"]


def test_derivation_replays_full_native_inputs_never_a_trimmed_report_or_synthetic_pass(
    monkeypatch,
):
    report = native_craft_report(primary_verdict="rewrite", audit=True)
    before = deepcopy(report)
    native_primary = scopes.parse_primary_review_submission
    native_actions = scopes.verified_commercial_actions
    observed = set()

    def original_primary(original, body):
        assert body == BODY and original == before["primary_submission_original"]
        observed.add("primary")
        return native_primary(original, body)

    def complete_actions(summary, body):
        assert body == BODY and summary == before["evidence_summary"]
        observed.add("commercial")
        return native_actions(summary, body)

    # Observers delegate to the real validators; no fabricated receipt/result.
    monkeypatch.setattr(scopes, "parse_primary_review_submission", original_primary)
    monkeypatch.setattr(scopes, "verified_commercial_actions", complete_actions)
    result = derive(BODY, report)
    assert result["schema"] == "verified-mixed-repair-scope.v1"
    assert observed == {"primary", "commercial"}
    assert report == before


@pytest.mark.parametrize("code", ["OPAQUE_47", "FACT_TIMELINE_777", "RECEIPT_09_17"])
def test_only_exact_verified_tuples_classify_facts_not_names_or_primary_dimension(code):
    report = _report(BODY, original=slot(_sources(BODY), code=code, kind="prior_fact_conflict"))
    primary(report)
    assert_scope(report, {1, 2})


@pytest.mark.parametrize("route", ["fact_only", "editorial_only", "no_body_edit"])
def test_nonmixed_inputs_do_not_use_mixed_schema_or_change_fact_only_behavior(route):
    report = _report(
        BODY, kind="prior_fact_conflict" if route == "fact_only" else "narrative_quality"
    )
    if route == "editorial_only":
        primary(report)
    assert scopes.reader_first_repair_route(BODY, report)["route"] == route
    previous = scopes.derive_fact_repair_scope(BODY, report)
    denied(BODY, report)
    assert scopes.derive_fact_repair_scope(BODY, report) == previous


@pytest.mark.parametrize("fault", ["missing", "false", "blocking", "infrastructure", "missing_run"])
def test_mixed_debt_with_unavailable_window_never_becomes_body_scope(fault):
    report = mixed_report()
    summary = report["evidence_summary"]
    window = summary["llm_window_quality_judge"]
    if fault == "missing":
        summary.pop("llm_window_quality_judge")
    elif fault == "false":
        window["passed"] = False
    elif fault == "missing_run":
        window["llm_run_id"] = None
    else:
        window["blocking_issues"] = [
            {
                "code": "JUDGE_TIMEOUT" if fault == "infrastructure" else "WINDOW_FACT_CONFLICT",
                "severity": "critical",
                "evidence": "Unresolved window observation",
                "required_fix": "Recover current review evidence.",
            }
        ]
    assert scopes.reader_first_repair_route(BODY, report)["route"] == "unknown"
    denied(BODY, report)


@pytest.mark.asyncio
async def test_retained_threshold_debt_is_not_sliced_off_to_make_a_mixed_scope(stable):
    # With an existing fact blocker the native normalizer synthesizes no
    # threshold blocker. Preserve that policy: use actual independent slots,
    # with threshold-only median representatives and the retained critical fact.
    sources = _sources(BODY)
    original = await stable([
        slot(sources, 0, code="OPAQUE_FACT_47", kind="prior_fact_conflict"),
        slot(sources, 1, score=0.4, issues=False),
        slot(sources, 2, score=0.4, issues=False),
    ])
    report = _report(BODY, original=original)
    primary(report)
    route = scopes.reader_first_repair_route(BODY, report)
    assert route["route"] == "unknown" and route["unresolved_action_indexes"]
    assert route["fact_action_indexes"] and route["primary_editorial_proposal"]
    denied(BODY, report)


@pytest.mark.parametrize(
    "fault", ["repeated_quote", "over_quarter", "utf8_over_quarter", "whole_body"]
)
def test_unprovable_or_oversized_union_denies_instead_of_trimming_report(fault):
    if fault == "repeated_quote":
        body, numbers = BODY + "\n\n" + FACT, (2,)
    elif fault == "whole_body":
        body, numbers = BODY, tuple(range(1, len(build_critic_body_anchors(BODY)) + 1))
    else:
        quote = "x" * 26 if fault == "over_quarter" else "汉" * 11
        body, numbers = quote + "\n\nedit\n\n" + "y" * (94 - len(quote)), (2,)
        anchors = build_critic_body_anchors(body)
        assert sum(len(row["text"].encode()) for row in anchors[:2]) > len(body.encode()) * 0.25
    report = mixed_report(body, numbers=numbers)
    assert scopes.reader_first_repair_route(body, report)["route"] == "mixed"
    error = denied(body, report)
    if fault != "repeated_quote":
        selected = {1, *numbers}
        authorized_bytes = sum(
            len(row["text"].encode("utf-8"))
            for index, row in enumerate(build_critic_body_anchors(body), 1)
            if index in selected
        )
        limit = len(body.encode("utf-8")) // 4
        assert error.differences == [{
            "field": "mixed_scope.authorized_utf8_bytes",
            "expected": {"type": "int", "length": None, "sha256": text_hash(str(limit))},
            "actual": {"type": "int", "length": None, "sha256": text_hash(str(authorized_bytes))},
        }]


@pytest.mark.parametrize(
    "fault",
    [
        "missing_projection",
        "raw_return",
        "authority",
        "lineage",
        "projection_digest",
        "view",
        "parent",
    ],
)
def test_mixed_scope_replays_complete_commercial_proof(fault):
    report = mixed_report()
    body = BODY
    summary = report["evidence_summary"]
    proof = summary["commercial_action_projection"]
    if fault == "missing_projection":
        summary.pop("commercial_action_projection")
    elif fault == "raw_return":
        original = summary["llm_commercial_judge_original"]
        envelope = json.loads(original["raw_excerpt"])
        envelope["raw_report"] += " "
        original["raw_excerpt"] = canonical(envelope)
    elif fault == "authority":
        proof["issue_actions"][0]["authority"] = "craft_only"
    elif fault == "lineage":
        proof["issue_actions"][0]["lineage"][0]["issue"]["body_citations"][0]["quote"] = "foreign"
    elif fault == "projection_digest":
        proof["projection_sha256"] = "0" * 64
    elif fault == "view":
        summary["llm_commercial_judge"]["blocking_issues"] = []
    else:
        body += "\n\nA newer draft."
        primary(report, body)  # Valid new primary cannot rescue stale commercial source proof.
    with pytest.raises(ValueError, match=r"^commercial_action_proof_unverified$"):
        derive(body, report)


@pytest.mark.parametrize(
    "fault", ["missing", "cached_pass", "raw_message", "canonical", "foreign_body"]
)
def test_original_primary_cannot_be_replaced_with_a_convenient_pass_or_citation(fault):
    report = mixed_report()
    if fault == "missing":
        report.pop("primary_submission_original")
    elif fault == "cached_pass":
        report["primary_submission_receipt"]["parsed_payload"]["verdict"] = "pass"
    elif fault == "raw_message":
        report["primary_submission_original"]["raw_message"]["content"] = "unsubmitted"
    elif fault == "canonical":
        report["critic_response"] += "\nUnsubmitted repair direction."
    else:
        primary(report, BODY + "\n\nA newer draft.")
    with pytest.raises(ValueError, match=r"^reader_first_repair_route_unavailable$"):
        derive(BODY, report)


def test_public_craft_policy_rejection_is_not_bypassed_for_mixed_proposals():
    report = _report(
        BODY,
        original=slot(_sources(BODY), code="ENDING_SENTENCE_WEAK", kind="narrative_quality"),
    )
    primary(report)
    with pytest.raises(ValueError, match=r"^commercial_action_policy_unverified$"):
        derive(BODY, report)


@pytest.mark.parametrize("report", [None, {}, ["unverified proposal"]])
def test_missing_full_report_never_yields_none_or_unrestricted_scope(report):
    with pytest.raises(ValueError, match=r"^reader_first_repair_route_unavailable$"):
        derive(BODY, report)


@pytest.mark.asyncio
async def test_verified_majority_lineage_retains_neutral_unknown_without_edit_authority(
    neutral_report,
):
    primary(neutral_report, PARENT, numbers=(1,))
    before = deepcopy(neutral_report)
    result = assert_scope(neutral_report, {1}, body=PARENT)
    proof = neutral_report["evidence_summary"]["commercial_action_projection"]
    assert proof["unknown_sample_slots"] == ["chapter-quality.independent-v2:2"]
    assert all(
        proof["slots"][-1][key] == 0
        for key in (
            "actions_contributed",
            "scores_contributed",
            "domain_issues_contributed",
        )
    )
    assert {
        origin["sample_slot"]
        for action in result["commercial_actions"]
        for origin in action["lineage"]
    } == {
        "chapter-quality.independent-v2:0",
        "chapter-quality.independent-v2:1",
    }
    assert neutral_report == before


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["score", "action", "lineage", "unknown_list"])
async def test_rehashed_unknown_or_forged_lineage_still_denies(neutral_report, fault):
    primary(neutral_report, PARENT, numbers=(1,))
    summary = neutral_report["evidence_summary"]
    proof = summary["commercial_action_projection"]
    if fault in {"score", "action"}:
        proof["slots"][-1]["scores_contributed" if fault == "score" else "actions_contributed"] = 1
    elif fault == "lineage":
        proof["issue_actions"][0]["lineage"][0]["sample_slot"] = proof["unknown_sample_slots"][0]
    else:
        proof["unknown_sample_slots"] = []
    proof["projection_sha256"] = _proof_sha(summary["llm_commercial_judge"])
    with pytest.raises(ValueError, match=r"^commercial_action_proof_unverified$"):
        derive(PARENT, neutral_report)
