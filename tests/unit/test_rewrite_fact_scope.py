"""Real retained-proof replay and SQLite fact-scope execution; no paid proof.

Only provider/context and deterministic downstream gate fixtures are isolated.
Primary parsing, raw commercial projection, proof verification, scope derivation,
retained rewrite projection, candidate evidence and ORM readback execute normally.
"""

from copy import deepcopy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import select
from test_commercial_action_projection import slot
from test_commercial_action_projection import stable as stable
from test_primary_review_submission import completion, payload
from test_revision_source_binding_sqlite import db as db
from test_revision_source_binding_sqlite import seed
from test_rewrite_atomic_evidence_sqlite import _audit
from test_rewrite_atomic_evidence_sqlite import runtime as runtime

from bestseller.domain.llm_quality_judge import quality_judge_result_from_mapping
from bestseller.infra.db.models import (
    ChapterDraftVersionModel,
    ChapterQualityReportModel,
    ProjectModel,
    ReviewReportModel,
    RewriteTaskModel,
)
from bestseller.services import reviews
from bestseller.services.chapter_draft_candidate_eligibility import verified_commercial_actions
from bestseller.services.commercial_action_projection import project_commercial_actions
from bestseller.services.commercial_judge_recovery import bind_commercial_slot_result
from bestseller.services.commercial_review_tool import submitted_review_payload
from bestseller.services.commercial_review_wire import parse_commercial_wire
from bestseller.services.commercial_source_evidence import assemble_commercial_sources
from bestseller.services.critic_evidence_gate import build_critic_body_anchors
from bestseller.services.llm import LLMCompletionResult
from bestseller.services.primary_review_submission import parse_primary_review_submission
from bestseller.services.quality_report_claim import graded_text_fingerprint
from bestseller.services.retained_rewrite_projection import project_retained_rewrite
from bestseller.services.rewrite_fact_scope import derive_fact_repair_scope
from bestseller.services.rewrite_patch import text_hash
from bestseller.services.write_gate import GateConfig
from bestseller.services.write_safety_gate import WriteSafetyFinding
from bestseller.settings import load_settings

pytestmark = pytest.mark.unit

FACT = "Two plus two totals five."
FIXES = (
    "Two plus two totals four.",
    "Two plus two equals four.",
    "Two plus two is four.",
)
PARENT = (
    FACT + "\n\n"
    "The receipt carried a square stamp. Every other entry belongs to the prior record.\n\n"
    "The courier checked the address, spelling and punctuation before filing the register.\n\n"
    "Closing note: Cafe\u0301, 咖啡 ☕🙂; preserve every trailing character and signature."
)


def _json(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def _primary(report, body, *, verdict="pass"):
    value = payload()
    value["verdict"] = verdict
    original = completion(value, raw_message=True).__dict__
    canonical, receipt, errors = parse_primary_review_submission(original, body)
    assert errors == () and receipt["status"] == "verified"
    report.update(
        primary_submission_original=original,
        primary_submission_receipt=receipt,
        critic_response=canonical,
    )


def _sources(body=PARENT, *, kind="prior_fact_conflict"):
    return assemble_commercial_sources(
        chapter_number=2,
        content_md=body,
        previous_chapters=[
            {"chapter_number": 1, "body": "The register states that two plus two is four."}
        ],
        source={
            "approved_chapter_contract": {
                "text": "Use third person.",
                "source": "user",
                "hard": True,
            }
        }
        if kind == "constraint_violation"
        else {},
    )


def _report(
    body=PARENT,
    *,
    kind="prior_fact_conflict",
    score=0.91,
    extra_kind=None,
    narrative_block=False,
    original=None,
):
    """Produce proof through both actual raw parsers, never fabricate a receipt."""
    sources = _sources(body, kind=kind)
    if original is None:
        original = slot(sources, code="ARITHMETIC_CONFLICT", kind=kind, score=score)
    if extra_kind is not None or narrative_block:
        envelope = json.loads(original.raw_excerpt)
        wire = json.loads(envelope["raw_report"])
        extra = deepcopy(wire["issues"][0])
        extra.update(
            code="ENDING_SENTENCE_WEAK" if narrative_block else "UNREGISTERED_NARRATIVE",
            assessment_kind=extra_kind or "narrative_quality",
        )
        for key in ("source_id", "temporal_relation"):
            extra.pop(key, None)
        if extra_kind == "prior_fact_conflict":
            extra.update(
                source_id=next(e["id"] for e in sources.catalog.values() if e["kind"] == "prior"),
                temporal_relation="same_event_or_state",
            )
        wire["issues"].append(extra)
        raw = json.dumps(wire, ensure_ascii=False)
        parsed, adapter, errors = parse_commercial_wire(raw, sources)
        assert errors == ()
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
                    "id": "retained-call",
                    "function": {"name": "submit_chapter_review", "arguments": raw},
                }
            ],
        )
        _, submission, errors = submitted_review_payload(physical)
        assert errors == ()
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
    view, proof = project_commercial_actions(
        original,
        sources=sources,
        chapter_number=2,
        gate_config=GateConfig(
            {"ENDING_SENTENCE_WEAK": "block" if narrative_block else "audit_only"}
        ),
    )
    assert proof["status"] == "projected", proof
    window = quality_judge_result_from_mapping(
        {"pass": True, "overall_score": 0.94, "dimension_scores": {}, "issues": []},
        scope="window",
        min_overall=0.82,
        llm_run_id=str(uuid4()),
    )
    report = {
        "evidence_summary": {
            "commercial_action_projection": proof,
            "llm_commercial_judge": view,
            "llm_commercial_judge_original": original.model_dump(mode="json"),
            "llm_window_quality_judge": window.model_dump(mode="json"),
        }
    }
    _primary(report, body)
    return report


@pytest.mark.parametrize(
    "kind", ["current_fact_conflict", "prior_fact_conflict", "constraint_violation"]
)
def test_real_verified_source_blockers_derive_exact_detached_scope(kind):
    report = _report(kind=kind)
    before = deepcopy(report)
    actions = verified_commercial_actions(report["evidence_summary"], PARENT)
    scope = derive_fact_repair_scope(PARENT, report)
    anchor = build_critic_body_anchors(PARENT)[0]
    assert scope["schema"] == "verified-fact-repair-scope.v1"
    assert scope["authorized_spans"] == [
        {
            "id": anchor["id"],
            "start": anchor["start"],
            "end": anchor["end"],
            "quote": FACT,
            "sha256": text_hash(FACT),
        }
    ]
    assert scope["blocking_actions"] == list(actions)
    assert all(
        a["authority"] == "verified_source" and a["effective_action"] == "block" for a in actions
    )
    assert scope["parent_sha256"] == text_hash(PARENT)
    assert scope["report_payload_sha256"] == text_hash(_json(report))
    assert (
        scope["action_projection_sha256"]
        == report["evidence_summary"]["commercial_action_projection"]["projection_sha256"]
    )
    assert scope["outside_scope"] == "preserve_exact_parent_bytes"
    assert scope["fresh_review_required"] is True
    assert (
        scope["adoption_authorized"] is scope["grades_modified"] is scope["grants_credit"] is False
    )
    assert report == before
    scope["blocking_actions"][0]["lineage"][0]["issue"]["required_fix"] = "changed detached copy"
    scope["authorized_spans"][0]["quote"] = "changed detached copy"
    assert report == before


def test_craft_audit_does_not_widen_fact_scope_or_regrade_raw_failure():
    report = _report(extra_kind="narrative_quality")
    summary = report["evidence_summary"]
    scope = derive_fact_repair_scope(PARENT, report)
    assert scope is not None
    assert len(scope["blocking_actions"]) == len(scope["authorized_spans"]) == 1
    assert {
        a["effective_action"] for a in summary["commercial_action_projection"]["issue_actions"]
    } == {"block", "audit_only"}
    assert summary["llm_commercial_judge_original"]["passed"] is False
    assert summary["llm_commercial_judge"]["pass"] is False
    assert scope["grades_modified"] is False


@pytest.mark.parametrize(
    "mode",
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
def test_primary_pass_requires_exact_physical_submission_receipt_and_canonical(mode):
    report = _report()
    if mode == "missing":
        report.pop("primary_submission_original")
    elif mode == "free_text":
        report["primary_submission_original"]["content"] = report["critic_response"]
        report["primary_submission_original"]["tool_calls"] = []
    elif mode == "rewrite":
        _primary(report, PARENT, verdict="rewrite")
    elif mode == "other_body":
        _primary(report, PARENT + "\n\nA different current paragraph.")
    elif mode == "raw_call":
        report["primary_submission_original"]["tool_calls"][0]["function"]["arguments"] += " "
    elif mode == "raw_message":
        report["primary_submission_original"]["raw_message"]["content"] = "Changed physical return"
    elif mode == "receipt":
        report["primary_submission_receipt"]["canonical_text_sha256"] = "0" * 64
    else:
        report["critic_response"] += "\nFree commentary cannot replace the canonical return."
    assert derive_fact_repair_scope(PARENT, report) is None


@pytest.mark.parametrize("mode", ["missing", "false", "blocking", "infrastructure", "missing_run"])
def test_window_must_be_available_pass_without_effective_blockers(mode):
    report = _report()
    summary = report["evidence_summary"]
    window = summary["llm_window_quality_judge"]
    if mode == "missing":
        summary.pop("llm_window_quality_judge")
    elif mode == "false":
        window["passed"] = False
    elif mode == "missing_run":
        window["llm_run_id"] = None
    else:
        window["blocking_issues"] = [
            {
                "code": "JUDGE_TIMEOUT" if mode == "infrastructure" else "WINDOW_FACT_CONFLICT",
                "severity": "critical",
                "evidence": "Unresolved window observation",
                "required_fix": "Review current context.",
            }
        ]
    assert derive_fact_repair_scope(PARENT, report) is None


@pytest.mark.parametrize("mode", ["none", "legacy", "missing_summary", "missing_projection"])
def test_absent_or_legacy_report_does_not_acquire_fact_authority(mode):
    report = _report()
    if mode == "none":
        report = None
    elif mode == "legacy":
        report = {"critic_response": "VERDICT: pass\nEVIDENCE: Two plus two totals five."}
    elif mode == "missing_summary":
        report.pop("evidence_summary")
    else:
        report["evidence_summary"].pop("commercial_action_projection")
    assert derive_fact_repair_scope(PARENT, report) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["retained_threshold", "registered_narrative"])
async def test_all_effective_blockers_must_have_verified_source_authority(mode, stable):
    if mode == "retained_threshold":
        sources = _sources()
        # The actual stable aggregator retains the threshold-only representative
        # together with independent critical factual findings from other slots.
        original = await stable(
            [
                slot(sources, 0, score=0.4, issues=False),
                slot(sources, 1, code="ARITHMETIC_CONFLICT", kind="prior_fact_conflict", score=0.4),
                slot(sources, 2, code="ARITHMETIC_CONFLICT", kind="prior_fact_conflict", score=0.4),
            ]
        )
        report = _report(original=original)
    else:
        report = _report(narrative_block=True)
    actions = verified_commercial_actions(report["evidence_summary"], PARENT)
    blocking = [a for a in actions if a["effective_action"] == "block"]
    assert any(a["authority"] == "verified_source" for a in blocking)
    assert any(a["authority"] != "verified_source" for a in blocking)
    assert derive_fact_repair_scope(PARENT, report) is None


@pytest.mark.parametrize(
    "mode",
    [
        "projection_digest",
        "projected_view",
        "raw_return",
        "lineage_quote",
        "action_authority",
        "parent",
    ],
)
def test_corrupt_commercial_proof_raises_instead_of_opening_unrestricted_rewrite(mode):
    report = _report()
    summary = report["evidence_summary"]
    proof = summary["commercial_action_projection"]
    body = PARENT
    if mode == "projection_digest":
        proof["projection_sha256"] = "0" * 64
    elif mode == "projected_view":
        summary["llm_commercial_judge"]["blocking_issues"] = []
    elif mode == "raw_return":
        original = summary["llm_commercial_judge_original"]
        envelope = json.loads(original["raw_excerpt"])
        envelope["raw_report"] += " "
        original["raw_excerpt"] = json.dumps(envelope)
    elif mode == "lineage_quote":
        proof["issue_actions"][0]["lineage"][0]["issue"]["body_citations"][0]["quote"] = (
            "Foreign book text"
        )
    elif mode == "action_authority":
        proof["issue_actions"][0]["authority"] = "craft_only"
    else:
        body += "\n\nA newer draft supersedes the reviewed body."
    with pytest.raises(ValueError, match="commercial_action_proof_unverified"):
        derive_fact_repair_scope(body, report)


def test_repeated_current_quote_cannot_select_one_of_two_occurrences():
    body = PARENT + "\n\n" + FACT
    report = _report(body)
    assert verified_commercial_actions(report["evidence_summary"], body)
    assert derive_fact_repair_scope(body, report) is None


@pytest.mark.parametrize("quote", ["x" * 25, "x" * 26, "汉" * 10])
def test_scope_limit_uses_utf8_bytes_and_includes_exact_quarter(quote):
    body = quote + "\n\n" + "y" * (98 - len(quote))
    report = _report(body)
    scope = derive_fact_repair_scope(body, report)
    within = len(quote.encode()) <= 0.25 * len(body.encode())
    assert (scope is not None) is within
    if scope:
        assert scope["authorized_spans"][0]["quote"] == quote
        assert len(quote.encode()) / len(body.encode()) == 0.25


@pytest.mark.parametrize("kind", ["narrative_quality", "prior_fact_conflict"])
def test_free_quotation_and_primary_pass_alone_do_not_authorize_fact_edits(kind):
    report = _report(kind=kind)
    if kind == "prior_fact_conflict":
        summary = report["evidence_summary"]
        summary.pop("commercial_action_projection")
        summary["free_citation"] = {"quote": FACT, "authority": "verified_source", "pass": True}
    else:
        assert all(
            a["effective_action"] == "audit_only"
            for a in verified_commercial_actions(report["evidence_summary"], PARENT)
        )
    assert derive_fact_repair_scope(PARENT, report) is None


def _returned(fix):
    # A provider returns a complete chapter and also changes unrelated prose.
    return PARENT.replace(FACT, fix).replace("Closing note:", "Closing memo:")


def _assert_complement(candidate, replacement):
    expected = PARENT.replace(FACT, replacement)
    assert candidate.encode("utf-8") == expected.encode("utf-8")
    assert candidate[len(replacement) :].encode("utf-8") == PARENT[len(FACT) :].encode("utf-8")


@pytest.mark.parametrize(
    "separator,replacement",
    [
        ("\n", FIXES[0]),
        ("\r\n", FIXES[1]),
        ("\n", "Two plus two has always equaled four."),
    ],
    ids=["unicode-complement", "crlf-complement", "expanded-fact-only"],
)
def test_actual_derived_scope_projects_complete_return_with_exact_parent_complement(
    separator, replacement
):
    parent = PARENT.replace("\n", separator)
    report = _report(parent)
    scope = derive_fact_repair_scope(parent, report)
    returned = parent.replace(FACT, replacement).replace("Closing note:", "Closing memo:")
    projected = project_retained_rewrite(
        parent,
        returned,
        authorized_spans=scope["authorized_spans"],
        expected_parent_sha256=scope["parent_sha256"],
        expected_return_sha256=text_hash(returned),
    )
    assert projected.accepted, projected.failure_codes
    assert projected.candidate_text.encode() == parent.replace(FACT, replacement).encode()
    assert projected.candidate_text[len(replacement) :].encode() == parent[len(FACT) :].encode()
    assert projected.receipt.discarded_opcodes
    assert projected.receipt.fresh_review_required is True
    assert projected.receipt.automatic_promotion_allowed is False
    assert projected.receipt.returned_sha256 == text_hash(returned)
    assert projected.receipt.candidate_sha256 == text_hash(projected.candidate_text)


async def _run_scoped(db, monkeypatch, band, *, stage, rejected=False, stale=False):
    observed = {"audit": [], "gate": [], "duplicates": [], "retrofit": [], "requests": []}
    first = PARENT.replace(FACT, FIXES[0])
    second = PARENT.replace(FACT, FIXES[1])
    final = PARENT.replace(FACT, FIXES[2])
    target = {
        "initial": 0,
        "semantic_repair": 1,
        "retrofit_repair": 1,
        "candidate_trim": 2,
        "incumbent_trim": 2,
    }[stage]
    returns = [_returned(fix) for fix in FIXES]
    if rejected:
        returns[target] = PARENT.replace("Closing note:", "Closing memo:")
    expected_scope = None
    async with db() as session:
        chapter, current, task = await seed(session)
        current.content_md = PARENT
        current.word_count = reviews.count_words(PARENT)
        chapter.production_state = "pending"
        chapter.target_word_count = 2000
        report_payload = _report()
        report_payload.update(
            draft_id=str(current.id),
            reviewed_body_fingerprint=reviews.chapter_review_body_fingerprint(PARENT),
        )
        report = ReviewReportModel(
            id=uuid4(),
            project_id=chapter.project_id,
            target_type="chapter",
            target_id=chapter.id,
            reviewer_type="fixture-real-parsers",
            verdict="rewrite",
            severity_max="critical",
            structured_output=report_payload,
        )
        session.add(report)
        task.metadata_json = {
            **task.metadata_json,
            "review_report_id": str(report.id),
            "parent_draft_hash": text_hash(PARENT),
            "budget_evidence": {"prior_attempts": 1},
        }
        if stale:
            report.structured_output = {
                **report_payload,
                "reviewed_body_fingerprint": reviews.chapter_review_body_fingerprint(
                    PARENT + " stale"
                ),
            }
        project = ProjectModel(
            id=chapter.project_id,
            slug="independent-fact-repair-test",
            title="The arithmetic register",
            genre="science-fiction",
            language="en",
            target_word_count=8000,
            target_chapters=4,
            metadata_json={},
            invariants_json=None,
        )
        await session.flush()
        expected_scope = derive_fact_repair_scope(PARENT, report_payload)
        assert expected_scope is not None
        monkeypatch.setattr(
            reviews,
            "_load_chapter_context",
            AsyncMock(return_value=(project, chapter, None, [], current)),
        )

        async def complete(*args):
            observed["requests"].append(args[2])
            index = len(observed["requests"]) - 1
            assert index < 2, "Unexpected extra fixture provider call"
            return LLMCompletionResult(
                content=returns[index], provider="fixture", model_name="fixture"
            )

        def audit(**kw):
            observed["audit"].append(kw["content_md"])
            return _audit(), band

        async def duplicates(_session, **kw):
            text = kw["content_md"]
            observed["duplicates"].append(text)
            if stage == "semantic_repair" and text == first:
                return (
                    WriteSafetyFinding(
                        source="fixture",
                        code="INTRA_CHAPTER_REPETITION",
                        severity="major",
                        message="Repair a deterministic candidate blocker.",
                        evidence=FIXES[0],
                        payload={},
                    ),
                )
            return ()

        async def gate(**kw):
            text = kw["content"]
            observed["gate"].append(text)
            violations = []
            if stage == "candidate_trim" and text in {first, second}:
                violations = [{"code": "LENGTH_OVER", "severity": "block", "detail": "max=2000"}]
            elif stage == "incumbent_trim":
                if text in {first, second}:
                    violations = [{"code": "CANON_STATE_REGRESSION", "severity": "block"}]
                elif text == PARENT:
                    violations = [
                        {"code": "LENGTH_OVER", "severity": "block", "detail": "max=2000"}
                    ]
            extra = kw.get("extra_report_payload")
            if extra:
                session.add(
                    ChapterQualityReportModel(
                        chapter_id=chapter.id,
                        blocks_write=bool(violations),
                        report_json={
                            **extra,
                            "graded": graded_text_fingerprint(text),
                            "violations": violations,
                            "blocking_codes": [v["code"] for v in violations],
                        },
                    )
                )
                await session.flush()
            return "blocked" if violations else "ok"

        def retrofit(text, *args, **kw):
            observed["retrofit"].append(text)
            return (
                [
                    {
                        "code": "QUALITY_RETROFIT_BLOCKED",
                        "detail": "Resolve the candidate fixture blocker.",
                    }
                ]
                if stage == "retrofit_repair" and text == first
                else []
            )

        monkeypatch.setattr(reviews, "complete_text", complete)
        monkeypatch.setattr(reviews, "_audit_chapter_rewrite_candidate", audit)
        monkeypatch.setattr(reviews, "_collect_post_assembly_duplicate_findings", duplicates)
        monkeypatch.setattr(reviews, "_evaluate_chapter_quality_gate", gate)
        monkeypatch.setattr(reviews, "_quality_retrofit_candidate_findings", retrofit)
        monkeypatch.setattr(
            reviews,
            "_length_over_max_from_violations",
            lambda values: 2000 if any(v.get("code") == "LENGTH_OVER" for v in values) else None,
        )
        monkeypatch.setattr(
            reviews,
            "_micro_trim_overlength_chapter_text",
            lambda *a, **k: (
                returns[2],
                {"applied": True, "before_word_count": 2100, "after_word_count": 1900},
            ),
        )
        if stale:
            with pytest.raises(ValueError, match="source binding"):
                await reviews.rewrite_chapter_from_task(
                    session, project.slug, 1, settings=load_settings(env={})
                )
        elif rejected and stage == "initial":
            with pytest.raises(ValueError, match="chapter_fact_repair_scope_rejected"):
                await reviews.rewrite_chapter_from_task(
                    session, project.slug, 1, settings=load_settings(env={})
                )
        else:
            await reviews.rewrite_chapter_from_task(
                session, project.slug, 1, settings=load_settings(env={})
            )
        await session.commit()
        task_id, original_id, report_id = task.id, current.id, report.id
    async with db() as session:
        stored_task = await session.get(RewriteTaskModel, task_id)
        rows = list(
            await session.scalars(
                select(ChapterDraftVersionModel).order_by(ChapterDraftVersionModel.version_no)
            )
        )
        reports = list(await session.scalars(select(ChapterQualityReportModel)))
        stored_report = await session.get(ReviewReportModel, report_id)
        return SimpleNamespace(
            task=stored_task,
            rows=rows,
            reports=reports,
            source_report=stored_report,
            observed=observed,
            returns=returns,
            scope=expected_scope,
            original_id=original_id,
            expected=(
                first
                if stage == "initial"
                else second
                if stage in {"semantic_repair", "retrofit_repair"}
                else final
            ),
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stage", ["initial", "semantic_repair", "retrofit_repair", "candidate_trim", "incumbent_trim"]
)
@pytest.mark.parametrize(
    "rejected", [False, True], ids=["projects-full-return", "rejects-no-local-repair"]
)
async def test_real_executor_projects_or_rejects_every_replacement_before_evidence(
    db,
    monkeypatch,
    runtime,
    stage,
    rejected,
):
    saved = await _run_scoped(db, monkeypatch, runtime, stage=stage, rejected=rejected)
    metadata = saved.task.metadata_json
    assert metadata["verified_fact_repair_scope"] == saved.scope
    assert metadata["budget_evidence"] == {"prior_attempts": 1}
    assert (
        text_hash(_json(saved.source_report.structured_output))
        == saved.scope["report_payload_sha256"]
    )
    assert saved.rows[0].id == saved.original_id and saved.rows[0].content_md == PARENT
    assert (
        saved.source_report.structured_output["primary_submission_receipt"]["status"] == "verified"
    )
    requests = saved.observed["requests"]
    assert requests[0].prompt_version == "3.3-verified-fact-edit-scope"
    prompt_scope = requests[0].user_prompt.split("BACKEND_FACT_REPAIR_SCOPE_JSON:\n", 1)[1]
    prompt_scope, _ = json.JSONDecoder().raw_decode(prompt_scope)
    assert prompt_scope == saved.scope
    history = metadata["candidate_evidence_history"]
    projections = [r for r in history if r.get("kind") == "verified_fact_repair_projection"]
    target = next(r for r in projections if r["stage"] == stage)
    target_index = {
        "initial": 0,
        "semantic_repair": 1,
        "retrofit_repair": 1,
        "candidate_trim": 2,
        "incumbent_trim": 2,
    }[stage]
    assert target["returned_content_md"] == saved.returns[target_index]
    assert len(requests) == (1 if stage == "initial" else 2)
    assert target["accepted"] is not rejected
    assert target["edit_scope"] == saved.scope
    assert target["source_report_id"] == str(saved.source_report.id)
    assert target["projection"]["fresh_review_required"] is True
    assert target["projection"]["automatic_promotion_allowed"] is False
    for row in projections:
        assert row["edit_scope"] == saved.scope
        assert row["projection"]["parent_sha256"] == text_hash(PARENT)
        assert row["projection"]["returned_sha256"] == text_hash(row["returned_content_md"])
        # Independently replay with the real projection, including rejected returns.
        replay = project_retained_rewrite(
            PARENT,
            row["returned_content_md"],
            authorized_spans=saved.scope["authorized_spans"],
            expected_parent_sha256=text_hash(PARENT),
            expected_return_sha256=text_hash(row["returned_content_md"]),
        )
        assert replay.accepted is row["accepted"]
        assert replay.receipt.candidate_sha256 == row["projection"]["candidate_sha256"]
        if replay.accepted:
            assert replay.candidate_text != row["returned_content_md"]
            assert replay.receipt.discarded_opcodes
            assert replay.candidate_text in saved.observed["audit"]
            assert replay.candidate_text in saved.observed["duplicates"]
            assert replay.candidate_text in saved.observed["gate"]
    # None of the raw complete rewrites may reach downstream evidence checks.
    for key in ("audit", "gate", "duplicates", "retrofit"):
        assert not set(saved.observed[key]).intersection(saved.returns)
        for body in saved.observed[key]:
            assert body == PARENT or body in [PARENT.replace(FACT, fix) for fix in FIXES]
    if rejected:
        assert target["failure_codes"]
        assert any(
            r.get("stage") == stage and r.get("status") == "evaluation_unavailable" for r in history
        )
        assert saved.rows[0].is_current
        assert not any(r.get("stage") == stage and r.get("activated") for r in history)
        if stage == "initial":
            assert len(saved.rows) == 1
            assert (
                saved.observed["audit"]
                == saved.observed["gate"]
                == saved.observed["duplicates"]
                == []
            )
            assert saved.task.attempts == 1
            return
        assert saved.task.status == "failed"
        assert not saved.rows[-1].is_current
    else:
        assert saved.rows[-1].is_current and not saved.rows[0].is_current
        assert saved.rows[-1].content_md == saved.expected
        assert any(r.get("stage") == stage and r.get("activated") for r in history)
    candidate = saved.rows[-1]
    replacement = next(fix for fix in FIXES if candidate.content_md == PARENT.replace(FACT, fix))
    _assert_complement(candidate.content_md, replacement)
    assert metadata["candidate_body_sha256"] == text_hash(candidate.content_md)
    assert metadata["candidate_gate_graded"] == graded_text_fingerprint(candidate.content_md)
    assert metadata["candidate_effect_realization_gate"]["tested_body_sha256"] == text_hash(
        candidate.content_md
    )
    assert runtime.effect_checked_bodies[-1] == candidate.content_md
    active = [r for r in history if r.get("activated")]
    assert active[-1]["body_sha256"] == text_hash(candidate.content_md)
    assert active[-1]["content_md"] == candidate.content_md
    assert active[-1]["deterministic_audit"] == metadata["deterministic_audit"]
    gate_report = next(r for r in saved.reports if str(r.id) == active[-1]["gate_report_id"])
    assert gate_report.report_json["graded"] == graded_text_fingerprint(candidate.content_md)
    assert (
        gate_report.report_json["rewrite_evaluation_id"] == metadata["candidate_gate_evaluation_id"]
    )
    assert history[-1]["stage"] == "final"
    assert history[-1]["body_sha256"] == text_hash(candidate.content_md)
    assert saved.task.attempts == 2


@pytest.mark.asyncio
async def test_real_parent_validation_rejects_stale_report_before_scope_or_provider(
    db, monkeypatch, runtime
):
    saved = await _run_scoped(db, monkeypatch, runtime, stage="initial", stale=True)
    assert saved.task.status == "stale"
    assert saved.task.attempts == 1
    assert "verified_fact_repair_scope" not in saved.task.metadata_json
    assert saved.observed["requests"] == []
    assert len(saved.rows) == 1 and saved.rows[0].is_current
    assert saved.rows[0].content_md.encode() == PARENT.encode()
