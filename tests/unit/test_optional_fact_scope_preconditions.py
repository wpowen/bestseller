"""Optional scope applicability through real proof parsers and SQLite execution.

The existing executor fixtures isolate context/transport only. No primary or
commercial validator is replaced, and the gateway always denies before payment.
"""

from copy import deepcopy
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import select
from test_rewrite_fact_scope import (
    FACT,
    PARENT,
    _primary,
    _report,
    _sources,
    db as db,
    runtime as runtime,
    slot,
    stable as stable,
)

from bestseller.infra.db.models import (
    ChapterDraftVersionModel,
    ChapterQualityReportModel,
    ProjectModel,
    ReviewReportModel,
    RewriteTaskModel,
)
from bestseller.services import reviews
from bestseller.services.budget import ExecutionBudgetError
from bestseller.services.chapter_draft_candidate_eligibility import verified_commercial_actions
from bestseller.services.rewrite_fact_scope import derive_fact_repair_scope
from bestseller.services.rewrite_patch import text_hash
from bestseller.settings import load_settings

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

DENIED = "phase8x_optional_scope_gateway_denied_before_pay"
ORDINARY = (
    "primary_rewrite_mixed",
    "primary_rewrite_unsupported",
    "primary_receipt_unverified",
    "window_missing",
    "window_rewrite",
)


async def _commercial_report(mode, stable):
    """Build retained physical slots, then run the real projection producer."""
    sources = _sources()
    if mode == "mixed":
        physical = [
            slot(sources, 0, code="ARITHMETIC_CONFLICT", kind="prior_fact_conflict"),
            slot(sources, 1, code="ARITHMETIC_CONFLICT", kind="prior_fact_conflict"),
            slot(sources, 2, kind="prior_fact_conflict", unknown=True),
        ]
        raw_before = [deepcopy(row.model_dump(mode="json")) for row in physical]
        retained = await stable(physical)
        report = _report(original=retained)
        proof = report["evidence_summary"]["commercial_action_projection"]
        assert proof["status"] == "projected"
        assert proof["unknown_sample_slots"] == ["chapter-quality.independent-v2:2"]
        assert [row["status"] for row in proof["slots"]] == [
            "verified", "verified", "unknown"
        ]
        assert [row.model_dump(mode="json") for row in physical] == raw_before
        assert not proof["raw_cohort_modified"] and not proof["grants_slots"]
        return report

    if mode == "parent_binding":
        report = _report(PARENT + "\n\nA different reviewed body supersedes this text.")
        _primary(report, PARENT)
        return report

    report = _report()
    summary = report["evidence_summary"]
    if mode == "unsupported":
        # Real registered craft audit: body binding is valid, but this helper
        # cannot authenticate its full gate policy from the retained receipt.
        retained = slot(sources, code="ENDING_SENTENCE_WEAK", kind="narrative_quality")
        raw_before = deepcopy(retained.model_dump(mode="json"))
        report = _report(original=retained)
        summary = report["evidence_summary"]
        proof = summary["commercial_action_projection"]
        assert proof["status"] == "projected" and proof["unknown_sample_slots"] == []
        assert proof["source_binding"]["body_sha256"] == text_hash(PARENT)
        assert proof["issue_actions"][0]["effective_action"] == "audit_only"
        assert proof["issue_actions"][0]["authority"] == "craft_only"
        assert retained.model_dump(mode="json") == raw_before
    elif mode == "corrupt":
        summary["commercial_action_projection"]["projection_sha256"] = "0" * 64
    else:
        assert mode == "valid"
    return report


async def _execute_until_denied(
    db, monkeypatch, report_payload, *, applicable=False, corrupt=False,
    proof_error="commercial_action_proof_unverified",
):
    """Use the actual task/report binding and executor, never synthesize adoption."""
    from test_revision_source_binding_sqlite import seed

    requests = []

    async def deny(_session, _settings, request):
        requests.append(request)
        raise ExecutionBudgetError(DENIED)

    monkeypatch.setattr(reviews, "complete_text", deny)
    async with db() as session:
        chapter, current, task = await seed(session)
        current.content_md = PARENT
        current.word_count = reviews.count_words(PARENT)
        chapter.production_state = "pending"
        chapter.target_word_count = 2000
        bound_report = deepcopy(report_payload)
        bound_report.update(
            draft_id=str(current.id),
            reviewed_body_fingerprint=reviews.chapter_review_body_fingerprint(PARENT),
        )
        source_report = ReviewReportModel(
            id=uuid4(), project_id=chapter.project_id, target_type="chapter",
            target_id=chapter.id, reviewer_type="fixture-real-parsers",
            verdict="rewrite", severity_max="critical", structured_output=bound_report,
        )
        session.add(source_report)
        task.metadata_json = {
            **task.metadata_json,
            "review_report_id": str(source_report.id),
            "parent_draft_hash": text_hash(PARENT),
            "budget_evidence": {"prior_attempts": 1},
        }
        before_metadata = deepcopy(task.metadata_json)
        project = ProjectModel(
            id=chapter.project_id, slug="optional-fact-scope-preconditions-test",
            title="The arithmetic register", genre="science-fiction", language="en",
            target_word_count=8000, target_chapters=4, metadata_json={}, invariants_json=None,
        )
        await session.flush()
        monkeypatch.setattr(
            reviews, "_load_chapter_context",
            AsyncMock(return_value=(project, chapter, None, [], current)),
        )
        exception = ValueError if corrupt else ExecutionBudgetError
        marker = proof_error if corrupt else DENIED
        with pytest.raises(exception, match=marker) as stopped:
            await reviews.rewrite_chapter_from_task(
                session, project.slug, 1, settings=load_settings(env={})
            )
        if not corrupt:
            assert stopped.value.code == DENIED
        expected_scope = None if corrupt else derive_fact_repair_scope(PARENT, bound_report)
        if not corrupt:
            assert (expected_scope is not None) is applicable
        assert len(requests) == (0 if corrupt else 1)
        if requests:
            request = requests[0]
            assert request.logical_role == "editor"
            assert request.prompt_template == "chapter_rewrite"
            assert request.project_id == chapter.project_id
            assert request.metadata["rewrite_task_id"] == str(task.id)
            assert ("BACKEND_FACT_REPAIR_SCOPE_JSON:" in request.user_prompt) is applicable
            assert (request.prompt_version == "3.3-verified-fact-edit-scope") is applicable
        assert source_report.structured_output == bound_report
        assert task.attempts == 1 and task.status == "pending"
        if applicable:
            assert task.metadata_json == {
                **before_metadata, "verified_fact_repair_scope": expected_scope
            }
        else:
            assert task.metadata_json == before_metadata
        await session.commit()
        task_id, report_id, draft_id = task.id, source_report.id, current.id

    async with db() as session:
        stored_task = await session.get(RewriteTaskModel, task_id)
        stored_report = await session.get(ReviewReportModel, report_id)
        drafts = list(await session.scalars(select(ChapterDraftVersionModel)))
        assert stored_report.structured_output == bound_report
        assert stored_task.attempts == 1 and stored_task.status == "pending"
        assert stored_task.metadata_json == (
            {**before_metadata, "verified_fact_repair_scope": expected_scope}
            if applicable else before_metadata
        )
        assert len(drafts) == 1 and drafts[0].id == draft_id and drafts[0].is_current
        assert drafts[0].content_md.encode() == PARENT.encode()
        assert list(await session.scalars(select(ChapterQualityReportModel))) == []


@pytest.mark.parametrize("mode", ORDINARY)
async def test_inapplicable_scope_uses_actual_normal_rewrite_gateway(
    db, monkeypatch, runtime, stable, mode
):
    commercial = (
        "unsupported" if mode == "primary_rewrite_unsupported"
        else "corrupt" if mode == "primary_receipt_unverified" else "mixed"
    )
    report = await _commercial_report(commercial, stable)
    if mode.startswith("primary_rewrite"):
        _primary(report, PARENT, verdict="rewrite")
    elif mode == "primary_receipt_unverified":
        report["primary_submission_receipt"]["canonical_text_sha256"] = "0" * 64
    elif mode == "window_missing":
        report["evidence_summary"].pop("llm_window_quality_judge")
    else:
        report["evidence_summary"]["llm_window_quality_judge"]["passed"] = False
    before = deepcopy(report)
    # The eligibility validator stays strict; only the optional route is skipped.
    proof_error = (
        "commercial_action_policy_unverified" if commercial == "unsupported"
        else "commercial_action_proof_unverified"
    )
    with pytest.raises(ValueError, match=proof_error):
        verified_commercial_actions(report["evidence_summary"], PARENT)
    await _execute_until_denied(db, monkeypatch, report)
    assert derive_fact_repair_scope(PARENT, report) is None
    assert report == before


@pytest.mark.parametrize("commercial", ["corrupt", "mixed", "unsupported", "parent_binding"])
async def test_applicable_primary_and_window_pass_keep_strict_commercial_rejection(
    db, monkeypatch, runtime, stable, commercial
):
    report = await _commercial_report(commercial, stable)
    before = deepcopy(report)
    proof_error = (
        "commercial_action_policy_unverified" if commercial == "unsupported"
        else "commercial_action_proof_unverified"
    )
    with pytest.raises(ValueError, match=proof_error):
        derive_fact_repair_scope(PARENT, report)
    await _execute_until_denied(db, monkeypatch, report, corrupt=True, proof_error=proof_error)
    assert report == before


@pytest.mark.parametrize("binding", ["different_body", "malformed_binding", "malformed_digest"])
async def test_ordinary_rewrite_still_rejects_invalid_immutable_commercial_binding(
    db, monkeypatch, runtime, stable, binding
):
    report = await _commercial_report(
        "parent_binding" if binding == "different_body" else "valid", stable
    )
    _primary(report, PARENT, verdict="rewrite")
    proof = report["evidence_summary"]["commercial_action_projection"]
    if binding == "malformed_binding":
        proof["source_binding"] = []
    elif binding == "malformed_digest":
        proof["source_binding"]["previous_sha256"] = "A" * 64
    before = deepcopy(report)
    with pytest.raises(ValueError, match="commercial_action_proof_unverified"):
        derive_fact_repair_scope(PARENT, report)
    await _execute_until_denied(db, monkeypatch, report, corrupt=True)
    assert report == before


async def test_fully_verified_fact_scope_keeps_exact_scope_and_fresh_review_requirement(
    db, monkeypatch, runtime, stable
):
    report = await _commercial_report("valid", stable)
    before = deepcopy(report)
    scope = derive_fact_repair_scope(PARENT, report)
    assert len(scope["authorized_spans"]) == len(scope["blocking_actions"]) == 1
    assert scope["authorized_spans"][0]["quote"] == FACT
    assert scope["authorized_spans"][0]["sha256"] == text_hash(FACT)
    assert scope["parent_sha256"] == text_hash(PARENT)
    assert scope["outside_scope"] == "preserve_exact_parent_bytes"
    assert scope["fresh_review_required"] is True
    assert scope["adoption_authorized"] is scope["grades_modified"] is scope["grants_credit"] is False
    await _execute_until_denied(db, monkeypatch, report, applicable=True)
    assert report == before
