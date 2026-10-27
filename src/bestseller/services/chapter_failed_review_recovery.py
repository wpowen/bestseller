"""Read-only failed-review origin proof; no grade, retry credit or dispatch."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import hashlib
import json
import re
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.infra.db.models import (
    BookProductionControlModel,
    ChapterDraftVersionModel,
    ChapterModel,
    ExecutionAttemptModel,
    ExecutionBudgetModel,
    ExecutionOperationModel,
    LlmRunModel,
    ProjectModel,
    QualityScoreModel,
    ReviewReportModel,
    WorkflowRunModel,
)
from bestseller.services.adopted_chapter_source import select_adopted_chapter_source
from bestseller.services.budget import completion_authorization, execution_scope_block_code
from bestseller.settings import AppSettings

SCHEMA = "failed-review-policy-source.v1"


def _require(value: object, code: str) -> None:
    if not value:
        raise ValueError("chapter_failed_review_recovery_" + code)


def _sha(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


async def validate_failed_review_policy_source(
    session: AsyncSession,
    *,
    settings: AppSettings,
    project: ProjectModel,
    chapter: ChapterModel,
    draft: ChapterDraftVersionModel,
    previous: WorkflowRunModel,
    execution_scope_id: str,
    current_policy_hash: str,
) -> dict[str, Any]:
    """Prove one failed current-body cycle, after native full-charge quarantine.

    The existing policy-change entrypoint owns locking, actual policy hash,
    allowance and checkpoint. No caller-supplied semantic hash is accepted.
    """
    try:
        with session.no_autoflush:
            return await _validate(
                session,
                settings,
                project,
                chapter,
                draft,
                previous,
                execution_scope_id,
                current_policy_hash,
            )
    except (TypeError, KeyError, AttributeError, OverflowError, RecursionError):
        raise ValueError("chapter_failed_review_recovery_invalid_source_shape") from None
    except ValueError as error:
        if str(error).startswith("chapter_failed_review_recovery_"):
            raise
        raise ValueError("chapter_failed_review_recovery_invalid_source_shape") from None


async def _validate(
    session: AsyncSession,
    settings: AppSettings,
    project: ProjectModel,
    chapter: ChapterModel,
    draft: ChapterDraftVersionModel,
    previous: WorkflowRunModel,
    scope: str,
    policy: str,
) -> dict[str, Any]:
    from bestseller.services.pipelines import (
        _chapter_critic_cycle_id,
        _chapter_critic_semantic_dependencies,
        chapter_review_body_fingerprint,
    )

    _require(not session.new and not session.dirty and not session.deleted, "uncommitted_source")
    _require(_sha(policy), "current_policy_hash")
    # Scope identity is verified against storage, not a historical prefix list.
    _require(isinstance(scope, str) and 0 < len(scope) <= 256, "execution_scope")
    _require(
        chapter.project_id == draft.project_id == previous.project_id == project.id
        and draft.chapter_id == previous.scope_id == chapter.id
        and previous.scope_type == "chapter"
        and previous.workflow_type == "chapter_pipeline"
        and previous.status == "failed"
        and previous.error_message == "workflow_failed"
        and isinstance(previous.current_step, str)
        and re.fullmatch(r"review_chapter(?:_v[1-9][0-9]*)?", previous.current_step),
        "source_binding",
    )
    for model, row, fields in (
        (ProjectModel, project, ("metadata_json",)),
        (ChapterModel, chapter, ("project_id", "metadata_json")),
        (
            WorkflowRunModel,
            previous,
            (
                "project_id",
                "scope_type",
                "scope_id",
                "workflow_type",
                "status",
                "current_step",
                "error_message",
                "metadata_json",
            ),
        ),
    ):
        stored = (
            await session.execute(
                select(*(getattr(model, k) for k in fields)).where(model.id == row.id)
            )
        ).one_or_none()
        _require(
            stored is not None and all(getattr(stored, k) == getattr(row, k) for k in fields),
            "stored_origin",
        )
    wm, cm = previous.metadata_json or {}, chapter.metadata_json or {}
    _require(
        (project.metadata_json or {}).get("execution_scope_id") == scope
        and wm.get("execution_scope_id") == scope,
        "project_scope",
    )
    failures = wm.get("execution_failure_receipts")
    _require(isinstance(failures, list) and failures, "durable_failure_required")
    failure = failures[-1]
    _require(
        isinstance(failure, Mapping)
        and failure.get("code") == "workflow_failed"
        and failure.get("recoverable") is True
        and failure.get("error_type") == "ValueError"
        and failure.get("workflow_id") == str(previous.id)
        and failure.get("project_id") == str(project.id)
        and failure.get("workflow_type") == "chapter_pipeline"
        and failure.get("step") == previous.current_step,
        "durable_failure_binding",
    )
    current = (
        await session.execute(
            select(
                ChapterDraftVersionModel.id,
                ChapterDraftVersionModel.content_md,
                ChapterDraftVersionModel.promotion_state,
                ChapterDraftVersionModel.promoted_at,
            ).where(
                ChapterDraftVersionModel.project_id == project.id,
                ChapterDraftVersionModel.chapter_id == chapter.id,
                ChapterDraftVersionModel.is_current.is_(True),
            )
        )
    ).all()
    _require(
        len(current) == 1
        and current[0].id == draft.id
        and draft.is_current
        and isinstance(draft.content_md, str)
        and draft.content_md.strip()
        and current[0].content_md == draft.content_md
        and current[0].promotion_state == draft.promotion_state
        and current[0].promoted_at is None
        and draft.promoted_at is None
        and draft.promotion_state
        in {"candidate", "under_review", "eligible", "rejected", "superseded", "quarantined"},
        "current_unpromoted_body",
    )
    fingerprint = chapter_review_body_fingerprint(draft.content_md)
    cycles = cm.get("chapter_critic_cycles")
    _require(isinstance(cycles, Mapping), "stored_cycles_required")
    matching = [
        c
        for k, c in cycles.items()
        if isinstance(c, Mapping)
        and c.get("cycle_id") == k
        and c.get("source_draft_id") == str(draft.id)
        and c.get("source_body_hash") == fingerprint["digest"]
        and c.get("review_workflow_run_id") == str(previous.id)
    ]
    _require(len(matching) == 1, "unique_current_failed_cycle_required")
    cycle = matching[0]
    old_policy = cycle.get("policy_source_hash")
    _require(_sha(old_policy) and old_policy != policy, "actual_policy_change_required")
    _require(
        cycle.get("schema_version") == "chapter-critic-cycle.v1"
        and cycle.get("state") in {"running", "completed"}
        and wm.get("critic_cycle_id") == cycle["cycle_id"]
        and cycle["cycle_id"]
        in {
            _chapter_critic_cycle_id(project.id, chapter.id, fingerprint["digest"]),
            _chapter_critic_cycle_id(project.id, chapter.id, fingerprint["digest"], old_policy),
        },
        "cycle_protocol",
    )
    attempts = cycle.get("attempts")
    _require(isinstance(attempts, list) and 1 <= len(attempts) <= 2, "attempts")
    completed = []
    for index, entry in enumerate(attempts, 1):
        _require(
            isinstance(entry, Mapping)
            and type(entry.get("attempt")) is int
            and entry["attempt"] == index
            and entry.get("operation_namespace") == f"chapter-critic:{cycle['cycle_id']}:{index}",
            "attempt_binding",
        )
        if entry.get("state") == "completed":
            _require(
                entry.get("outcome") == "unknown"
                and entry.get("draft_id") == str(draft.id)
                and entry.get("body_fingerprint") == fingerprint
                and entry.get("rewrite_task_id") is None,
                "unknown_only_current_cycle",
            )
            completed.append(entry)
        else:
            _require(
                entry.get("state") in {"prepared", "running"}
                and index == len(attempts)
                and completed
                and not any(
                    entry.get(k) is not None
                    for k in ("outcome", "review_report_id", "quality_score_id", "rewrite_task_id")
                ),
                "unreturned_tail_attempt",
            )
    _require(
        completed and (cycle["state"] != "completed" or attempts[-1].get("state") == "completed"),
        "unknown_report_required",
    )
    last = completed[-1]
    _require(
        cm.get("chapter_review_evidence_status") == "unknown"
        and cm.get("chapter_review_evidence_report_id") == last.get("review_report_id"),
        "latest_unknown_report_binding",
    )
    reports = list(
        await session.scalars(
            select(ReviewReportModel)
            .where(
                ReviewReportModel.project_id == project.id,
                ReviewReportModel.target_type == "chapter",
                ReviewReportModel.target_id == chapter.id,
            )
            .order_by(ReviewReportModel.created_at)
        )
    )
    current_reports = [
        r for r in reports if (r.structured_output or {}).get("draft_id") == str(draft.id)
    ]
    _require(
        current_reports
        and {str(r.id) for r in current_reports} == {e.get("review_report_id") for e in completed}
        and str(current_reports[-1].id) == last["review_report_id"],
        "current_report_inventory",
    )
    for report in current_reports:
        payload = report.structured_output or {}
        evidence = payload.get("critic_evidence") or {}
        _require(
            report.verdict == "unknown"
            and report.llm_run_id is None
            and payload.get("reviewed_body_fingerprint") == fingerprint
            and evidence.get("status") in {"unavailable", "evidence_invalid", "fallback"}
            and evidence.get("passed") is False
            and payload.get("rewrite_instructions") is None
            and payload.get("primary_submission_receipt") is None
            and payload.get("primary_submission_original") is None,
            "no_available_current_grade",
        )
    raw_body_sha256 = hashlib.sha256(draft.content_md.encode()).hexdigest()
    known_prior_primary = {
        r.llm_run_id
        for r in reports
        if (r.structured_output or {}).get("draft_id") not in {None, str(draft.id)}
        and r.llm_run_id is not None
    }
    primary_runs = list(
        await session.scalars(
            select(LlmRunModel).where(
                LlmRunModel.project_id == project.id,
                LlmRunModel.workflow_run_id == previous.id,
                LlmRunModel.prompt_template == "chapter_review",
            )
        )
    )
    for run in primary_runs:
        body_sha = (run.metadata_json or {}).get("primary_review_body_sha256")
        _require(body_sha != raw_body_sha256, "primary_result_available_or_unattributed")
        if run.id in known_prior_primary:
            continue
        _require(
            _sha(body_sha) and body_sha != raw_body_sha256,
            "primary_result_available_or_unattributed",
        )
    for entry in completed:
        quality = await session.get(QualityScoreModel, UUID(entry["quality_score_id"]))
        _require(
            quality is not None
            and quality.project_id == project.id
            and quality.target_type == "chapter"
            and quality.target_id == chapter.id
            and quality.chapter_draft_version_id == draft.id
            and str(quality.review_report_id) == entry["review_report_id"],
            "unknown_quality_binding",
        )
        _require(
            any(
                isinstance(e, Mapping)
                and all(
                    e.get(k) == entry.get(k)
                    for k in (
                        "attempt",
                        "operation_namespace",
                        "outcome",
                        "draft_id",
                        "body_fingerprint",
                        "review_report_id",
                        "quality_score_id",
                        "rewrite_task_id",
                    )
                )
                for e in wm.get("critic_evidence_attempts", [])
            ),
            "workflow_evidence_binding",
        )
    quality_ids = set(
        await session.scalars(
            select(QualityScoreModel.id).where(
                QualityScoreModel.project_id == project.id,
                QualityScoreModel.target_type == "chapter",
                QualityScoreModel.target_id == chapter.id,
                QualityScoreModel.chapter_draft_version_id == draft.id,
            )
        )
    )
    _require(
        {str(identity) for identity in quality_ids}
        == {entry["quality_score_id"] for entry in completed},
        "current_quality_inventory",
    )
    _require(
        await session.scalar(
            select(WorkflowRunModel.id)
            .where(
                WorkflowRunModel.project_id == project.id,
                WorkflowRunModel.status.in_(("pending", "queued", "running")),
            )
            .limit(1)
        )
        is None,
        "no_active_workflow_required",
    )
    control = (
        await session.execute(
            select(
                BookProductionControlModel.desired_state, BookProductionControlModel.command_serial
            ).where(BookProductionControlModel.project_id == project.id)
        )
    ).one_or_none()
    _require(
        control is not None
        and control.desired_state == "run"
        and type(control.command_serial) is int
        and control.command_serial > 0,
        "explicit_project_run_required",
    )
    budget = await session.get(ExecutionBudgetModel, scope)
    authorization = completion_authorization(budget) if budget is not None else None
    _require(
        budget is not None
        and budget.status in {"active", "exhausted"}
        and authorization is not None
        and authorization.get("status") == "active"
        and execution_scope_block_code(budget) is None,
        "active_completion_authorization_required",
    )
    operations = list(
        await session.scalars(
            select(ExecutionOperationModel).where(ExecutionOperationModel.scope_id == scope)
        )
    )
    physical = list(
        await session.scalars(
            select(ExecutionAttemptModel).where(ExecutionAttemptModel.scope_id == scope)
        )
    )
    _require(
        not any(o.status in {"pending", "unknown", "reserved", "running"} for o in operations),
        "settled_scope_required",
    )
    _require(
        not any(a.status in {"reserved", "pending", "unknown", "running"} for a in physical),
        "settled_attempts_required",
    )
    by_op, by_attempt = {o.key: o for o in operations}, {a.id: a for a in physical}
    stopped = []
    for transport in operations:
        t = transport.result_json or {}
        if transport.status != "transport_failure_receipt" or t.get("workflow_run_id") != str(
            previous.id
        ):
            continue
        _require(
            t.get("schema") == "transport-failure.v1"
            and t.get("project_id") == str(project.id)
            and t.get("logical_role") == "critic",
            "transport_origin",
        )
        op, attempt = by_op.get(t.get("operation_key")), by_attempt.get(t.get("attempt_id"))
        _require(
            op is not None
            and attempt is not None
            and op.status == "stopped"
            and op.result_json is None
            and attempt.operation_key == op.key
            and attempt.input_hash == op.input_hash
            and attempt.status == "reconciled_charged",
            "quarantined_transport_required",
        )
        receipts = [
            o
            for o in operations
            if o.status == "reconciliation_receipt"
            and isinstance(o.result_json, Mapping)
            and (o.result_json.get("receipt") or {}).get("attempt_id") == attempt.id
        ]
        _require(len(receipts) == 1, "durable_dispatcher_exit_required")
        record = receipts[0].result_json
        receipt = record.get("receipt") or {}
        _require(
            record.get("evidence_kind") == "dispatcher_exit"
            and record.get("local_dispatcher_stopped") is True
            and record.get("remote_operation_closed") is False
            and _sha(record.get("evidence_sha256"))
            and record.get("actor")
            and receipt.get("scope_id") == scope
            and receipt.get("decision") == "quarantine_charged"
            and receipt.get("remaining_unknown") == 0
            and receipt.get("credit_released") is False
            and receipt.get("automatic_resume") is False
            and receipt.get("remote_outcome") == "unknown_quarantined"
            and receipt.get("operation_replay_allowed") is False
            and type(receipt.get("calls_used")) is int
            and 0 < receipt["calls_used"] <= budget.calls_used
            and type(receipt.get("tokens_charged")) is int
            and type(attempt.reserved_tokens) is int
            and attempt.reserved_tokens > 0
            and attempt.reserved_tokens <= receipt["tokens_charged"] <= budget.tokens_charged,
            "full_charge_dispatcher_exit",
        )
        stopped.append(
            {
                "operation_key": op.key,
                "attempt_id": attempt.id,
                "reserved_tokens": attempt.reserved_tokens,
                "receipt_sha256": _digest(record),
            }
        )
    _require(stopped, "local_exit_proof_required")
    adopted, numeric = await select_adopted_chapter_source(session, chapter=chapter, lock=False)
    _require(adopted is None or adopted.id != draft.id, "target_already_adopted")
    _require(cm.get("numeric_promotion_debt") is None or numeric is not None, "adoption_ambiguous")
    if adopted is None:
        _require(
            await session.scalar(
                select(ChapterDraftVersionModel.id)
                .where(
                    ChapterDraftVersionModel.project_id == project.id,
                    ChapterDraftVersionModel.chapter_id == chapter.id,
                    ChapterDraftVersionModel.promotion_state == "promoted",
                )
                .limit(1)
            )
            is None,
            "adoption_ambiguous",
        )
    # This runtime function applies the actual LLM profile and hashes story,
    # adopted history, effective settings and calibration assets. No supplied hash.
    semantic = await _chapter_critic_semantic_dependencies(session, settings, chapter)
    _require(
        _sha(semantic)
        and semantic == cycle.get("semantic_dependencies_sha256")
        and not cycle.get("semantic_dependency_change_detected"),
        "semantic_dependencies_changed",
    )
    proof = {
        "schema": SCHEMA,
        "status": "verified_failed_review_origin",
        "project_id": str(project.id),
        "chapter_id": str(chapter.id),
        "draft_id": str(draft.id),
        "previous_workflow_run_id": str(previous.id),
        "previous_status": previous.status,
        "previous_step": previous.current_step,
        "failure_receipt": deepcopy(dict(failure)),
        "previous_metadata_sha256": _digest(wm),
        "execution_scope_id": scope,
        "source_body_fingerprint": fingerprint,
        "source_cycle_id": cycle["cycle_id"],
        "source_cycle": deepcopy(dict(cycle)),
        "source_cycle_sha256": _digest(cycle),
        "previous_policy_source_hash": old_policy,
        "current_policy_source_hash": policy,
        "semantic_dependencies_sha256": semantic,
        "stopped_transports": stopped,
        "control_command_serial": control.command_serial,
        "authorization_ref": authorization["authorization_ref"],
        "authorization_sha256": _digest(authorization),
        "budget_usage": {"calls_used": budget.calls_used, "tokens_charged": budget.tokens_charged},
        "origin_only": True,
        "grade_reuse_allowed": False,
        "adoption_allowed": False,
        "attempt_reset_allowed": False,
        "new_credit_allowed": False,
        "dispatch_allowed": False,
    }
    proof["proof_sha256"] = _digest(proof)
    return proof
