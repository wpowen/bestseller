"""Required fact scopes fail closed; real proof parsers, no DB or paid result.

The existing fixture builds synthetic native submissions through the production
parsers and action projector. No scope/proof verifier is mocked. These cases do
not classify repair tasks or authorize a report, edit, adoption, grade or credit.
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
from test_rewrite_fact_scope import FACT, PARENT, _primary, _report, _sources

from bestseller.services import llm
from bestseller.services import rewrite_fact_scope as scopes
from bestseller.services.chapter_draft_candidate_eligibility import (
    _proof_sha,
    verified_commercial_actions,
)
from bestseller.services.rewrite_patch import text_hash

pytestmark = pytest.mark.unit
__all__ = ["neutral_report", "stable"]


@pytest.fixture(autouse=True)
def no_external_io(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("fact-scope unit forbids DB/network/model execution")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(Engine, "raw_connection", forbidden)
    monkeypatch.setattr(llm, "complete_text", forbidden)


def require_scope(body, report):
    required = getattr(scopes, "require_fact_repair_scope", None)
    assert callable(required), "required fact-scope API is missing; None must not widen edits"
    return required(body, report)


def unavailable(body, report):
    before = deepcopy(report)
    assert scopes.derive_fact_repair_scope(body, report) is None
    with pytest.raises(ValueError, match=r"^fact_repair_scope_unavailable$") as caught:
        require_scope(body, report)
    error = caught.value
    assert error.code == "fact_repair_scope_unavailable"
    expected = []
    for field, value, encoding in (
        ("parent_text", body, "utf8"),
        ("report_payload", report, "canonical_json"),
    ):
        raw = (
            value
            if encoding == "utf8"
            else json.dumps(
                value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
            )
        ).encode("utf-8")
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
    assert error.args == ("fact_repair_scope_unavailable",)
    assert body not in str(error) and body not in repr(error.diagnostics)
    assert report == before


@pytest.mark.parametrize(
    "fault",
    [
        "none",
        "text",
        "empty",
        "legacy",
        "summary_missing",
        "summary_list",
        "projection",
        "free_quote",
    ],
)
def test_missing_fact_evidence_cannot_fall_back_to_unbounded_rewrite(fault):
    report = _report()
    if fault == "none":
        report = None
    elif fault == "text":
        report = "The editor says this is a fact repair."
    elif fault == "empty":
        report = {}
    elif fault == "legacy":
        report = {"critic_response": "VERDICT: pass"}
    elif fault == "summary_missing":
        report.pop("evidence_summary")
    elif fault == "summary_list":
        report["evidence_summary"] = []
    else:
        summary = report["evidence_summary"]
        summary.pop("commercial_action_projection")
        if fault == "free_quote":
            summary["free_citation"] = {"quote": FACT, "authority": "verified_source", "pass": True}
    unavailable(PARENT, report)


@pytest.mark.parametrize(
    "fault",
    [
        "missing",
        "free_text",
        "rewrite",
        "other_body",
        "raw_call",
        "raw_message",
        "receipt",
        "canonical",
    ],
)
def test_unknown_or_inapplicable_primary_cannot_authorize_full_rewrite(fault):
    report = _report()
    if fault == "missing":
        report.pop("primary_submission_original")
    elif fault == "free_text":
        report["primary_submission_original"]["content"] = report["critic_response"]
        report["primary_submission_original"]["tool_calls"] = []
    elif fault == "rewrite":
        _primary(report, PARENT, verdict="rewrite")
    elif fault == "other_body":
        _primary(report, PARENT + "\n\nA newer paragraph.")
    elif fault == "raw_call":
        report["primary_submission_original"]["tool_calls"][0]["function"]["arguments"] += " "
    elif fault == "raw_message":
        report["primary_submission_original"]["raw_message"]["content"] = "Different return"
    elif fault == "receipt":
        report["primary_submission_receipt"]["canonical_text_sha256"] = "0" * 64
    else:
        report["critic_response"] += "\nAn extra unsubmitted claim."
    unavailable(PARENT, report)


@pytest.mark.parametrize("fault", ["missing", "false", "blocking", "infrastructure", "missing_run"])
def test_window_unavailable_or_failed_does_not_bless_unknown(fault):
    report = _report()
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
                "required_fix": "Recover the review without editing the body.",
            }
        ]
    unavailable(PARENT, report)


@pytest.mark.parametrize(
    "fault",
    ["audit_only", "mixed_editorial", "repeated_quote", "over_quarter", "utf8_over_quarter"],
)
def test_unprovable_scope_requires_routing_or_review_instead_of_expansion(fault):
    body = PARENT
    if fault == "audit_only":
        report = _report(kind="narrative_quality")
    elif fault == "mixed_editorial":
        report = _report(narrative_block=True)
    else:
        if fault == "repeated_quote":
            body += "\n\n" + FACT
        else:
            quote = "x" * 26 if fault == "over_quarter" else "汉" * 11
            body = quote + "\n\n" + "y" * (98 - len(quote))
            assert len(quote.encode()) > 0.25 * len(body.encode())
        report = _report(body)
    # Whole retained commercial provenance remains verified even when no bounded
    # fact-only contract can be derived; that does not authorize wider editing.
    assert verified_commercial_actions(report["evidence_summary"], body)
    unavailable(body, report)


@pytest.mark.parametrize(
    "fault",
    [
        "projection_digest",
        "projected_view",
        "raw_return",
        "lineage_quote",
        "action_authority",
        "parent",
        "binding",
        "binding_digest",
    ],
)
def test_required_scope_preserves_full_native_commercial_rejection(fault):
    report = _report()
    body = PARENT
    summary = report["evidence_summary"]
    proof = summary["commercial_action_projection"]
    if fault == "projection_digest":
        proof["projection_sha256"] = "0" * 64
    elif fault == "projected_view":
        summary["llm_commercial_judge"]["blocking_issues"] = []
    elif fault == "raw_return":
        original = summary["llm_commercial_judge_original"]
        envelope = json.loads(original["raw_excerpt"])
        envelope["raw_report"] += " "
        original["raw_excerpt"] = json.dumps(envelope)
    elif fault == "lineage_quote":
        proof["issue_actions"][0]["lineage"][0]["issue"]["body_citations"][0]["quote"] = (
            "Foreign body"
        )
    elif fault == "action_authority":
        proof["issue_actions"][0]["authority"] = "craft_only"
    elif fault == "parent":
        body += "\n\nA superseding draft."
    elif fault == "binding":
        proof["source_binding"] = []
    else:
        proof["source_binding"]["previous_sha256"] = "A" * 64
    before = deepcopy(report)
    with pytest.raises(ValueError, match=r"^commercial_action_proof_unverified$"):
        scopes.derive_fact_repair_scope(body, report)
    with pytest.raises(ValueError, match=r"^commercial_action_proof_unverified$"):
        require_scope(body, report)
    assert report == before


def test_unsupported_craft_policy_proof_keeps_its_existing_rejection():
    report = _report(
        kind="narrative_quality",
        original=slot(_sources(), code="ENDING_SENTENCE_WEAK", kind="narrative_quality"),
    )
    before = deepcopy(report)
    with pytest.raises(ValueError, match=r"^commercial_action_policy_unverified$"):
        scopes.derive_fact_repair_scope(PARENT, report)
    with pytest.raises(ValueError, match=r"^commercial_action_policy_unverified$"):
        require_scope(PARENT, report)
    assert report == before


@pytest.mark.parametrize(
    "kind", ["current_fact_conflict", "prior_fact_conflict", "constraint_violation"]
)
def test_required_scope_keeps_exact_detached_contract_and_zero_authority(kind):
    report = _report(kind=kind)
    before = deepcopy(report)
    expected = scopes.derive_fact_repair_scope(PARENT, report)
    assert expected is not None
    actual = require_scope(PARENT, report)
    assert actual == expected
    assert actual["authorized_spans"][0]["quote"] == FACT
    assert actual["parent_sha256"] == text_hash(PARENT)
    assert actual["outside_scope"] == "preserve_exact_parent_bytes"
    assert actual["fresh_review_required"] is True
    assert all(
        actual[key] is False for key in ("adoption_authorized", "grades_modified", "grants_credit")
    )
    actual["authorized_spans"][0]["quote"] = "mutated detached scope"
    actual["blocking_actions"][0]["lineage"][0]["issue"]["required_fix"] = "mutated detached action"
    assert report == before


def test_audit_is_retained_without_becoming_a_body_obligation_or_new_grade():
    report = _report(extra_kind="narrative_quality")
    before = deepcopy(report)
    expected = scopes.derive_fact_repair_scope(PARENT, report)
    assert expected is not None
    actual = require_scope(PARENT, report)
    assert actual == expected
    assert len(actual["blocking_actions"]) == len(actual["authorized_spans"]) == 1
    assert report["evidence_summary"]["llm_commercial_judge_original"]["passed"] is False
    assert report["evidence_summary"]["llm_commercial_judge"]["pass"] is False
    assert report == before


@pytest.mark.parametrize("code", ["OPAQUE_47", "FACT_TIMELINE_777", "RECEIPT_09_17"])
@pytest.mark.parametrize("kind", ["prior_fact_conflict", "narrative_quality"])
def test_scope_uses_verified_typed_authority_not_issue_names_or_keywords(code, kind):
    report = _report(kind=kind, original=slot(_sources(), code=code, kind=kind))
    before = deepcopy(report)
    expected = scopes.derive_fact_repair_scope(PARENT, report)
    if kind == "narrative_quality":
        assert expected is None
        with pytest.raises(ValueError, match=r"^fact_repair_scope_unavailable$"):
            require_scope(PARENT, report)
    else:
        assert expected is not None
        assert require_scope(PARENT, report) == expected
    assert report == before


@pytest.mark.asyncio
async def test_verified_majority_does_not_turn_retained_unknown_slot_into_evidence(neutral_report):
    before = deepcopy(neutral_report)
    expected = scopes.derive_fact_repair_scope(PARENT, neutral_report)
    assert expected is not None
    actual = require_scope(PARENT, neutral_report)
    assert actual == expected
    proof = neutral_report["evidence_summary"]["commercial_action_projection"]
    assert proof["unknown_sample_slots"] == ["chapter-quality.independent-v2:2"]
    assert {
        row["sample_slot"] for action in actual["blocking_actions"] for row in action["lineage"]
    } == {
        "chapter-quality.independent-v2:0",
        "chapter-quality.independent-v2:1",
    }
    assert neutral_report == before


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["unknown_vote", "unknown_score"])
async def test_rehashed_unknown_cannot_acquire_vote_or_score(neutral_report, fault):
    summary = neutral_report["evidence_summary"]
    proof = summary["commercial_action_projection"]
    original, view = summary["llm_commercial_judge_original"], summary["llm_commercial_judge"]
    aggregate = json.loads(original["raw_excerpt"])
    if fault == "unknown_vote":
        aggregate["samples"][-1]["valid"] = True
    else:
        proof["slots"][-1]["scores_contributed"] = 1
    original["raw_excerpt"] = view["raw_excerpt"] = json.dumps(aggregate)
    proof["original_aggregate_sha256"] = _proof_sha(original)
    proof["projection_sha256"] = _proof_sha(view)
    before = deepcopy(neutral_report)
    with pytest.raises(ValueError, match=r"^commercial_action_proof_unverified$"):
        scopes.derive_fact_repair_scope(PARENT, neutral_report)
    with pytest.raises(ValueError, match=r"^commercial_action_proof_unverified$"):
        require_scope(PARENT, neutral_report)
    assert neutral_report == before


@pytest.mark.parametrize("fault", ["absent", "editorial", "ambiguous"])
def test_legacy_optional_api_still_returns_none_without_granting_fact_authority(fault):
    body = PARENT + "\n\n" + FACT if fault == "ambiguous" else PARENT
    report = (
        None
        if fault == "absent"
        else _report(
            body, kind="narrative_quality" if fault == "editorial" else "prior_fact_conflict"
        )
    )
    before = deepcopy(report)
    assert scopes.derive_fact_repair_scope(body, report) is None
    assert report == before
