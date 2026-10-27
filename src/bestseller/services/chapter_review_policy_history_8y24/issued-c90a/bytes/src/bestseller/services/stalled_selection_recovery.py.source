"""Explicit, source-witness-gated reversal of a selection-only chooser regression.

This is not a migration entry point. The caller owns quiescence, the transaction,
external protected/financial witnesses and approval to commit. Capture a witness
with ``build_stalled_selection_witness`` from freshly loaded rows, inspect the
pure ``prove_stalled_selection_recovery`` result, then pass that exact witness
and historical receipt to ``recover_stalled_selection`` with actual settings.
No provider, eligibility, policy alias, score or adoption is created here.

Only the original receipt's expected working source can be restored. A newer
version, score advantage, changed policy or caller-supplied blocker is no proof.
The effective factual blocker is reconstructed from the retained action proof;
the async layer additionally re-verifies both retained inputs against settings.
Receipts are append-only, self-hashed derived metadata; raw evidence is untouched.
Chapter.updated_at is a generated timestamp and is excluded from source hashes.
Row locks cannot establish the absence of an unlogged pointer round trip before
the witness was captured; the caller must establish an idle writer boundary.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from datetime import UTC, date, datetime
from decimal import Decimal
import hashlib
import json
import math
from typing import Any
from uuid import UUID

from sqlalchemy import inspect, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select

from bestseller.domain.review import ChapterReviewResult
from bestseller.infra.db.models import (
    ChapterDraftVersionModel,
    ChapterModel,
    ProjectModel,
    QualityScoreModel,
    ReviewReportModel,
)
from bestseller.services.chapter_draft_candidate_eligibility import (
    AUDIT_KEY,
    verified_commercial_actions,
)
from bestseller.services.retained_review_input_proof import verify_retained_review_inputs
from bestseller.services.reviews import chapter_review_body_fingerprint
from bestseller.settings import AppSettings

SCHEMA = "stalled-selection-recovery.v1"
WITNESS_SCHEMA = "stalled-selection-source-witness.v1"
RECOVERY_KEY = "stalled_selection_recoveries_v1"


def _require(condition: object, code: str) -> None:
    if not condition:
        raise ValueError(f"stalled_selection_recovery_{code}")


def _json_default(value: object) -> object:
    if isinstance(value, UUID | Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.replace(tzinfo=value.tzinfo or UTC).astimezone(UTC).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    raise TypeError("Unsupported source witness value")


def _sha(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
            default=_json_default,
        ).encode()
    ).hexdigest()


def _valid_sha(value: object) -> bool:
    return (
        isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)
    )


def _row(row: object) -> dict[str, Any]:
    state = inspect(row)
    _require(
        not (state.unloaded & {attr.key for attr in state.mapper.column_attrs}),
        "source_columns_not_loaded",
    )
    return {attr.key: deepcopy(getattr(row, attr.key)) for attr in state.mapper.column_attrs}


def _sources(
    *,
    project: ProjectModel,
    chapter: ChapterModel,
    drafts: Sequence[ChapterDraftVersionModel],
    reports: Sequence[ReviewReportModel],
    qualities: Sequence[QualityScoreModel],
) -> dict[str, Any]:
    chapter_row = _row(chapter)
    chapter_row.pop("updated_at", None)
    metadata = chapter_row["metadata_json"]
    _require(isinstance(metadata, dict), "metadata_malformed")
    metadata.pop(RECOVERY_KEY, None)
    return {
        "project": _row(project),
        "chapter": chapter_row,
        **{
            name: sorted((_row(r) for r in rows), key=lambda r: str(r["id"]))
            for name, rows in (("drafts", drafts), ("reports", reports), ("qualities", qualities))
        },
    }


def build_stalled_selection_witness(
    *,
    project: ProjectModel,
    chapter: ChapterModel,
    drafts: Sequence[ChapterDraftVersionModel],
    reports: Sequence[ReviewReportModel],
    qualities: Sequence[QualityScoreModel],
) -> dict[str, Any]:
    """Hash exact persisted columns, including raw UTF-8 bodies and every source row.

    Supply *all* chapter drafts, chapter reports and chapter quality rows. The
    async application loads these independently; a partial witness cannot match.
    A witness is a compare-and-swap fence, never authority to restore a source.
    """
    history = (chapter.metadata_json or {}).get(RECOVERY_KEY, [])
    _require(isinstance(history, list), "recovery_history_malformed")
    return {
        "schema": WITNESS_SCHEMA,
        "project_id": str(project.id),
        "chapter_id": str(chapter.id),
        "source_sha256": _sha(
            _sources(
                project=project,
                chapter=chapter,
                drafts=drafts,
                reports=reports,
                qualities=qualities,
            )
        ),
        "recovery_history_sha256": _sha(history),
    }


def _unique(rows: Sequence[Any], code: str) -> dict[str, Any]:
    result = {str(row.id): row for row in rows}
    _require(len(result) == len(rows), code)
    return result


def _selection_audit(metadata: Mapping[str, Any], receipt: Mapping[str, Any]) -> tuple[int, dict]:
    history = metadata.get(AUDIT_KEY)
    _require(
        isinstance(history, list) and bool(history) and isinstance(receipt, dict), "audit_malformed"
    )
    for item in history:
        _require(
            isinstance(item, dict)
            and item.get("selection_only") is True
            and item.get("adoption_authorized") is False
            and isinstance(item.get("expected_current_draft_id"), str)
            and isinstance(item.get("chosen_draft_id"), str),
            "audit_malformed",
        )
    matches = [index for index, item in enumerate(history) if item == receipt]
    _require(len(matches) == 1, "audit_missing_or_ambiguous")
    index = matches[0]
    _require(
        receipt.get("reason") == "eligible_best_of_n"
        and receipt.get("policy") == "chapter-best-draft-source-eligibility.v1"
        and _valid_sha(receipt.get("policy_source_hash"))
        and _valid_sha(receipt.get("semantic_dependencies_sha256")),
        "audit_malformed",
    )
    chosen = receipt["chosen_draft_id"]
    _require(receipt["expected_current_draft_id"] != chosen, "audit_did_not_change_selection")
    for later in history[index + 1 :]:
        _require(
            later["expected_current_draft_id"] == chosen
            and later["chosen_draft_id"] == chosen
            and later.get("reason") == "eligible_best_of_n"
            and all(
                later.get(key) == receipt[key]
                for key in ("policy", "policy_source_hash", "semantic_dependencies_sha256")
            ),
            "later_selection",
        )
        _require(later.get("candidates") == receipt.get("candidates"), "later_review_selection")
    return index, deepcopy(receipt)


def _bound_review(
    draft: ChapterDraftVersionModel,
    candidate: dict,
    receipt: Mapping[str, Any],
    chapter: ChapterModel,
    reports: dict,
    qualities: dict,
) -> dict[str, Any]:
    body = chapter_review_body_fingerprint(draft.content_md)
    _require(
        candidate.get("eligible") is True
        and candidate.get("reasons") == []
        and candidate.get("body_fingerprint") == body,
        "old_candidate_not_bound",
    )
    cycles = chapter.metadata_json.get("chapter_critic_cycles")
    _require(isinstance(cycles, dict), "cycles_malformed")
    matching = [
        (key, c)
        for key, c in cycles.items()
        if isinstance(c, dict)
        and c.get("source_draft_id") == str(draft.id)
        and c.get("source_body_hash") == body["digest"]
    ]
    _require(len(matching) == 1, "cycle_missing_or_ambiguous")
    key, cycle = matching[0]
    _require(
        key == candidate.get("cycle_id") == cycle.get("cycle_id")
        and cycle.get("schema_version") == "chapter-critic-cycle.v1"
        and cycle.get("state") == "completed"
        and cycle.get("policy_source_hash") == receipt["policy_source_hash"]
        and cycle.get("semantic_dependencies_sha256") == receipt["semantic_dependencies_sha256"]
        and not cycle.get("semantic_dependency_change_detected"),
        "cycle_binding_mismatch",
    )
    # The producer's initial cycle key includes project, chapter and exact body.
    expected_key = hashlib.sha256(
        f"chapter-critic-cycle.v1\n{chapter.project_id}\n{chapter.id}\n{body['digest']}\ninitial".encode()
    ).hexdigest()
    _require(key == expected_key, "cycle_scope_mismatch")
    attempts = cycle.get("attempts")
    _require(isinstance(attempts, list) and 1 <= len(attempts) <= 2, "attempts_malformed")
    for index, attempt in enumerate(attempts, 1):
        _require(
            isinstance(attempt, dict)
            and type(attempt.get("attempt")) is int
            and attempt["attempt"] == index,
            "attempts_malformed",
        )
    matches = [
        a
        for a in attempts
        if a.get("review_report_id") == candidate.get("review_report_id")
        and a.get("quality_score_id") == candidate.get("quality_score_id")
    ]
    _require(len(matches) == 1 and matches[0] is attempts[-1], "attempt_missing_or_ambiguous")
    attempt = matches[0]
    _require(
        attempt.get("state") == "completed"
        and attempt.get("outcome") == "available"
        and attempt.get("draft_id") == str(draft.id)
        and attempt.get("body_fingerprint") == body,
        "attempt_unavailable_or_unbound",
    )
    report = reports.get(candidate.get("review_report_id"))
    quality = qualities.get(candidate.get("quality_score_id"))
    _require(report is not None and quality is not None, "report_or_quality_missing")
    _require(
        report.project_id == quality.project_id == chapter.project_id
        and report.target_type == quality.target_type == "chapter"
        and report.target_id == quality.target_id == chapter.id
        and quality.chapter_draft_version_id == draft.id
        and quality.scene_draft_version_id is None
        and quality.review_report_id == report.id,
        "report_or_quality_scope_mismatch",
    )
    payload = report.structured_output
    _require(
        isinstance(payload, dict)
        and payload.get("draft_id") == str(draft.id)
        and payload.get("reviewed_body_fingerprint") == body,
        "report_body_mismatch",
    )
    try:
        result = ChapterReviewResult.model_validate(attempt["review_result"])
        score, report_score = float(quality.score_overall), float(payload["scores"]["overall"])
        _require(
            math.isfinite(score)
            and 0 <= score <= 1
            and math.isfinite(report_score)
            and abs(score - report_score) <= 0.005001
            and result.scores.overall == report_score,
            "score_binding_mismatch",
        )
        _require(
            result.verdict in {"pass", "rewrite"}
            and result.verdict == report.verdict
            and result.scores.model_dump(mode="json") == payload["scores"]
            and result.evidence_summary == payload.get("evidence_summary"),
            "result_binding_mismatch",
        )
        evidence = payload.get("critic_evidence")
        _require(
            isinstance(evidence, dict)
            and evidence.get("status") in {"verified", "not_run"}
            and (evidence["status"] != "verified" or evidence.get("passed") is True)
            and not result.evidence_summary.get("review_evidence_failure"),
            "unknown_evidence",
        )
        actions = verified_commercial_actions(result.evidence_summary, draft.content_md)
        quality_actions = verified_commercial_actions(quality.evidence_summary, draft.content_md)
        _require(actions == quality_actions, "quality_action_binding_mismatch")
        blockers = [
            a
            for a in actions
            if a["effective_action"] == "block" and a["authority"] == "verified_source"
        ]
    except (KeyError, TypeError, AttributeError, OverflowError) as exc:
        raise ValueError("stalled_selection_recovery_review_malformed") from exc
    _require("commercial_action_projection" in result.evidence_summary, "fact_witness_missing")
    return {
        "draft": draft,
        "report": report,
        "quality": quality,
        "cycle": cycle,
        "attempt": attempt,
        "result": result,
        "blockers": blockers,
        "binding": {
            "draft_id": str(draft.id),
            "body_sha256": hashlib.sha256(draft.content_md.encode()).hexdigest(),
            "body_fingerprint": body,
            "cycle_id": key,
            "cycle_sha256": _sha(cycle),
            "attempt_sha256": _sha(attempt),
            "review_report_id": str(report.id),
            "review_report_sha256": _sha(_row(report)),
            "quality_score_id": str(quality.id),
            "quality_score_sha256": _sha(_row(quality)),
            "verified_actions_sha256": _sha(actions),
            "effective_fact_blockers": blockers,
        },
    }


def prove_stalled_selection_recovery(
    *,
    project: ProjectModel,
    chapter: ChapterModel,
    drafts: Sequence[ChapterDraftVersionModel],
    reports: Sequence[ReviewReportModel],
    qualities: Sequence[QualityScoreModel],
    expected_receipt: dict,
    expected_witness: dict,
) -> dict[str, Any]:
    """Pure proof of a historical regression, not permission to adopt or reuse a grade."""
    _require(project.id == chapter.project_id, "scope_mismatch")
    witness = build_stalled_selection_witness(
        project=project, chapter=chapter, drafts=drafts, reports=reports, qualities=qualities
    )
    _require(witness == expected_witness, "source_witness_changed")
    _require(
        all(d.project_id == project.id and d.chapter_id == chapter.id for d in drafts),
        "draft_scope_mismatch",
    )
    _require(
        all(
            r.project_id == project.id and r.target_type == "chapter" and r.target_id == chapter.id
            for r in (*reports, *qualities)
        ),
        "source_scope_mismatch",
    )
    _require(
        all(
            d.promotion_state in {"candidate", "under_review", "eligible"} and d.promoted_at is None
            for d in drafts
        )
        and chapter.metadata_json.get("numeric_promotion_debt") is None,
        "adopted_or_nonworking_state",
    )
    by_draft, by_report, by_quality = (
        _unique(rows, "duplicate_source_rows") for rows in (drafts, reports, qualities)
    )
    index, receipt = _selection_audit(chapter.metadata_json, expected_receipt)
    current = [d for d in drafts if d.is_current]
    _require(
        len(current) == 1 and str(current[0].id) == receipt["chosen_draft_id"],
        "working_pointer_changed",
    )
    candidates = receipt.get("candidates")
    _require(
        isinstance(candidates, list)
        and all(isinstance(c, dict) and isinstance(c.get("draft_id"), str) for c in candidates),
        "candidates_malformed",
    )
    by_candidate = {c["draft_id"]: c for c in candidates}
    _require(len(by_candidate) == len(candidates), "candidates_ambiguous")
    bindings = {}
    for label, key in (("chosen", "chosen_draft_id"), ("expected", "expected_current_draft_id")):
        identity = receipt[key]
        _require(identity in by_draft and identity in by_candidate, "source_missing")
        bindings[label] = _bound_review(
            by_draft[identity], by_candidate[identity], receipt, chapter, by_report, by_quality
        )
    _require(bool(bindings["chosen"]["blockers"]), "chooser_regression_unproved")
    expected = bindings["expected"]
    _require(not expected["blockers"], "expected_source_fact_blocked")
    hard = {"critical", "block", "blocker", "fatal"}
    _require(
        str(expected["report"].severity_max).lower() not in hard
        and str(expected["result"].severity_max).lower() not in hard
        and not any(f.severity.lower() in hard for f in expected["result"].findings)
        and not any(
            expected["report"].structured_output.get(k)
            for k in ("blocking_codes", "blockers", "blocking_issues")
        ),
        "expected_source_hard_blocked",
    )
    return {
        "schema": SCHEMA,
        "status": "proved",
        "project_id": str(project.id),
        "chapter_id": str(chapter.id),
        "before_witness": deepcopy(witness),
        "selection_receipt_sha256": _sha(receipt),
        "selection_audit_index": index,
        "chosen": bindings["chosen"]["binding"],
        "expected": expected["binding"],
        "selection_only": True,
        "adoption_authorized": False,
        "eligibility_created": False,
        "grades_modified": False,
    }


async def _load(session: AsyncSession, project_id: UUID, chapter_id: UUID) -> dict[str, Any]:
    async def locked(statement: Select) -> list[Any]:
        return list(
            await session.scalars(
                statement.with_for_update().execution_options(
                    populate_existing=True, autoflush=False
                )
            )
        )

    projects = await locked(select(ProjectModel).where(ProjectModel.id == project_id))
    chapters = await locked(select(ChapterModel).where(ChapterModel.id == chapter_id))
    _require(
        len(projects) == len(chapters) == 1 and chapters[0].project_id == project_id,
        "scope_mismatch",
    )
    drafts = await locked(
        select(ChapterDraftVersionModel).where(ChapterDraftVersionModel.chapter_id == chapter_id)
    )
    reports = await locked(
        select(ReviewReportModel).where(
            ReviewReportModel.target_type == "chapter", ReviewReportModel.target_id == chapter_id
        )
    )
    qualities = await locked(
        select(QualityScoreModel).where(
            or_(
                (QualityScoreModel.target_type == "chapter")
                & (QualityScoreModel.target_id == chapter_id),
                QualityScoreModel.chapter_draft_version_id.in_([d.id for d in drafts]),
            )
        )
    )
    return {
        "project": projects[0],
        "chapter": chapters[0],
        "drafts": drafts,
        "reports": reports,
        "qualities": qualities,
    }


def _receipt_hash(receipt: dict) -> str:
    return _sha({k: v for k, v in receipt.items() if k != "receipt_sha256"})


def _input_proof_bound(
    item: object,
    *,
    project_id: UUID,
    chapter_id: UUID,
    binding: dict,
    report: ReviewReportModel,
    cycle: dict,
) -> bool:
    if not isinstance(item, dict):
        return False
    paid = item.get("paid_rows")
    commercial = item.get("commercial")
    return bool(
        item.get("schema") == "retained-review-input-proof.v1"
        and type(item.get("provider_calls")) is int
        and item["provider_calls"] == 0
        and type(item.get("writes")) is int
        and item["writes"] == 0
        and all(
            item.get(key) == value
            for key, value in {
                "project_id": str(project_id),
                "chapter_id": str(chapter_id),
                "draft_id": binding["draft_id"],
                "review_report_id": binding["review_report_id"],
                "cycle_id": binding["cycle_id"],
                "review_workflow_run_id": cycle.get("review_workflow_run_id"),
            }.items()
        )
        and all(
            _valid_sha(item.get(key))
            for key in ("source_sha256", "retained_source_sha256", "catalog_sha256")
        )
        and isinstance(paid, list)
        and paid
        and all(
            isinstance(row, dict)
            and bool(row.get("llm_run_id"))
            and _valid_sha(row.get("effective_input_sha256"))
            for row in paid
        )
        and any(
            row["llm_run_id"] == str(report.llm_run_id)
            and row.get("prompt_template") == "chapter_review"
            for row in paid
        )
        and isinstance(commercial, dict)
        and commercial.get("route_proof") == "retained-journal-effective-critic.v1"
        and _valid_sha(commercial.get("effective_critic_sha256"))
        and isinstance(commercial.get("llm_run_ids"), list)
        and commercial["llm_run_ids"]
    )


async def load_stalled_selection_witness(
    session: AsyncSession, *, project_id: UUID, chapter_id: UUID
) -> dict[str, Any]:
    """Read/lock the complete scope for a caller's explicit compare-and-swap fence."""
    _require(not (session.new or session.dirty or session.deleted), "clean_session_required")
    return build_stalled_selection_witness(**await _load(session, project_id, chapter_id))


async def recover_stalled_selection(
    session: AsyncSession,
    settings: AppSettings,
    *,
    project_id: UUID,
    chapter_id: UUID,
    expected_receipt: dict,
    expected_witness: dict,
    actor: str,
    evidence_ref: str,
) -> dict[str, Any]:
    """Restore working flags and their word count, append receipt and flush; never commit.

    A clean session is required to avoid flushing unrelated caller changes.
    ValueError fails closed. A mutation failure rolls back this helper's savepoint.
    Repeating the identical request returns its original immutable receipt only
    while its exact after-state and receipt chain still match.
    """
    _require(isinstance(settings, AppSettings), "actual_settings_required")
    expected_receipt, expected_witness = deepcopy(expected_receipt), deepcopy(expected_witness)
    _require(
        isinstance(actor, str)
        and actor.strip()
        and isinstance(evidence_ref, str)
        and evidence_ref.strip(),
        "explicit_evidence_required",
    )
    _require(not (session.new or session.dirty or session.deleted), "clean_session_required")
    rows = await _load(session, project_id, chapter_id)
    chapter = rows["chapter"]
    history = chapter.metadata_json.get(RECOVERY_KEY, [])
    _require(isinstance(history, list), "recovery_history_malformed")
    previous = None
    for item in history:
        _require(
            isinstance(item, dict)
            and item.get("schema") == SCHEMA
            and item.get("receipt_sha256") == _receipt_hash(item)
            and item.get("previous_receipt_sha256") == previous,
            "recovery_history_malformed",
        )
        previous = item["receipt_sha256"]
    request = _sha(
        {
            "project_id": str(project_id),
            "chapter_id": str(chapter_id),
            "receipt": expected_receipt,
            "witness": expected_witness,
        }
    )
    repeats = [r for r in history if r.get("request_sha256") == request]
    if repeats:
        _require(len(repeats) == 1 and repeats[0] is history[-1], "recovery_request_ambiguous")
        receipt = repeats[0]
        now = build_stalled_selection_witness(**rows)
        _require(
            receipt.get("before_witness") == expected_witness
            and receipt.get("selection_receipt_sha256") == _sha(expected_receipt)
            and receipt.get("after_source_sha256") == now["source_sha256"],
            "restored_source_changed",
        )
        index, original = _selection_audit(chapter.metadata_json, expected_receipt)
        before = _sources(**rows)
        ids = {
            "chosen": original["chosen_draft_id"],
            "expected": original["expected_current_draft_id"],
        }
        current = [d for d in rows["drafts"] if d.is_current]
        _require(
            len(current) == 1 and str(current[0].id) == ids["expected"], "restored_source_changed"
        )
        _require(
            type(receipt.get("previous_current_word_count")) is int
            and type(receipt.get("restored_current_word_count")) is int
            and chapter.current_word_count
            == receipt["restored_current_word_count"]
            == current[0].word_count,
            "restored_source_changed",
        )
        before["chapter"]["current_word_count"] = receipt["previous_current_word_count"]
        for row in before["drafts"]:
            if str(row["id"]) in ids.values():
                row["is_current"] = str(row["id"]) == ids["chosen"]
        _require(
            _sha(before) == expected_witness.get("source_sha256")
            and _sha(history[:-1]) == expected_witness.get("recovery_history_sha256")
            and receipt.get("status") == "restored"
            and receipt.get("selection_audit_index") == index
            and receipt.get("selection_only") is True
            and all(
                receipt.get(key) is False
                for key in ("adoption_authorized", "eligibility_created", "grades_modified")
            ),
            "restored_source_changed",
        )
        candidates = {c["draft_id"]: c for c in original["candidates"]}
        by_draft = {str(d.id): d for d in rows["drafts"]}
        by_report = {str(r.id): r for r in rows["reports"]}
        by_quality = {str(q.id): q for q in rows["qualities"]}
        for label, identity in ids.items():
            bound = _bound_review(
                by_draft[identity], candidates[identity], original, chapter, by_report, by_quality
            )
            _require(
                receipt.get(label) == bound["binding"]
                and _input_proof_bound(
                    receipt.get("retained_input_proofs", {}).get(label),
                    project_id=project_id,
                    chapter_id=chapter_id,
                    binding=bound["binding"],
                    report=bound["report"],
                    cycle=bound["cycle"],
                ),
                "input_proof_unbound",
            )
        return deepcopy(receipt)
    proof = prove_stalled_selection_recovery(
        **rows, expected_receipt=expected_receipt, expected_witness=expected_witness
    )
    by_draft = {str(d.id): d for d in rows["drafts"]}
    by_report = {str(r.id): r for r in rows["reports"]}
    input_proofs = {}
    for label in ("chosen", "expected"):
        binding = proof[label]
        cycle = chapter.metadata_json["chapter_critic_cycles"][binding["cycle_id"]]
        item = await verify_retained_review_inputs(
            session,
            settings,
            project=rows["project"],
            chapter=chapter,
            draft=by_draft[binding["draft_id"]],
            report=by_report[binding["review_report_id"]],
            cycle=cycle,
        )
        _require(
            _input_proof_bound(
                item,
                project_id=project_id,
                chapter_id=chapter_id,
                binding=binding,
                report=by_report[binding["review_report_id"]],
                cycle=cycle,
            ),
            "input_proof_unbound",
        )
        input_proofs[label] = deepcopy(item)
    _require(
        not (session.new or session.dirty or session.deleted), "input_verifier_mutated_session"
    )
    fresh = await _load(session, project_id, chapter_id)
    _require(
        build_stalled_selection_witness(**fresh) == expected_witness, "source_changed_during_proof"
    )
    # Re-run pure action proof against fresh columns, not a caller's proof result.
    _require(
        prove_stalled_selection_recovery(
            **fresh, expected_receipt=expected_receipt, expected_witness=expected_witness
        )
        == proof,
        "proof_changed",
    )
    chapter = fresh["chapter"]
    by_draft = {str(d.id): d for d in fresh["drafts"]}
    chosen, expected = (by_draft[proof[label]["draft_id"]] for label in ("chosen", "expected"))
    before = _sources(**fresh)
    async with session.begin_nested():
        chosen.is_current = False
        await session.flush()
        expected.is_current = True
        chapter.current_word_count = expected.word_count
        await session.flush()
        after = _sources(**await _load(session, project_id, chapter_id))
        permitted = deepcopy(before)
        permitted["chapter"]["current_word_count"] = expected.word_count
        for row in permitted["drafts"]:
            if row["id"] in {chosen.id, expected.id}:
                row["is_current"] = row["id"] == expected.id
        _require(_sha(after) == _sha(permitted), "unexpected_write")
        receipt = {
            **proof,
            "status": "restored",
            "request_sha256": request,
            "actor": actor,
            "evidence_ref": evidence_ref,
            "at": datetime.now(UTC).isoformat(),
            "previous_receipt_sha256": previous,
            "previous_current_word_count": before["chapter"]["current_word_count"],
            "restored_current_word_count": expected.word_count,
            "after_source_sha256": _sha(after),
            "retained_input_proofs": input_proofs,
            "provider_calls": 0,
            "raw_reports_modified": False,
            "cycles_modified": False,
            "quality_rows_modified": False,
            "promotion_states_modified": False,
        }
        receipt["receipt_sha256"] = _receipt_hash(receipt)
        chapter.metadata_json = {
            **deepcopy(chapter.metadata_json),
            RECOVERY_KEY: [*deepcopy(history), receipt],
        }
        await session.flush()
        final = await _load(session, project_id, chapter_id)
        _require(
            _sha(_sources(**final)) == _sha(after)
            and final["chapter"].metadata_json[RECOVERY_KEY] == [*history, receipt],
            "unexpected_write",
        )
    return deepcopy(receipt)
