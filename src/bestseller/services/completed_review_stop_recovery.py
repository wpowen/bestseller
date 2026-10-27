"""Clear one stale local stop using the same owner's completed current review.

The caller supplies actual project/runtime settings and owns the transaction.
This also accepts a detached projection with id/status/metadata_json: only that
object is changed. Control is locked before project, matching explicit web RUN.
Existing exact-source preflight plus owner completion is authority for clearing
this local stop, not a physical prompt proof, new grade, adoption or dispatch.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from datetime import datetime, timezone
import json
from uuid import UUID

from sqlalchemy import inspect, or_, select

from bestseller.infra.db.models import (
    BookProductionControlModel,
    ChapterDraftVersionModel,
    ChapterModel,
    ProjectModel,
    QualityScoreModel,
    ReviewReportModel,
    WorkflowRunModel,
)
from bestseller.services.chapter_review_preflight import validate_retained_review_before_processing
from bestseller.services.retry_ledger import execution_terminal_code, project_execution_scope

CODE = "execution_success_audit_missing"
SCHEMA = "completed-review-stop-recovery.v1"


def _need(condition, code):
    if not condition:
        raise ValueError("completed_review_stop_recovery_" + code)


async def _locked_row(session, model, identity):
    return await session.scalar(
        select(model).where(model.id == identity).with_for_update()
        .execution_options(populate_existing=True, autoflush=False)
    )


async def recover_completed_review_stop(session, settings, project, *, actor, reason) -> dict | None:
    """Append a receipt and remove only execution_terminal; never commit/flush.

    No stop is an idempotent no-op. A present but malformed, foreign or partial
    stop fails closed. Pending session writes are rejected before the existing
    preflight (whose semantic reader can flush) or ORM refreshes are invoked.
    """
    metadata = project.metadata_json
    _need(isinstance(metadata, Mapping), "project_metadata_invalid")
    if "execution_terminal" not in metadata:
        return None
    try:
        with session.no_autoflush:
            return await _recover(session, settings, project, actor=actor, reason=reason)
    except (KeyError, TypeError, AttributeError, OverflowError, RecursionError) as error:
        raise ValueError("completed_review_stop_recovery_invalid_shape") from error


async def _recover(session, settings, project, *, actor, reason):
    from bestseller.services import pipelines
    from bestseller.services.budget import current_execution_scope
    from bestseller.services.waiting_chapter_review_resume import _ledger, _row, _sha

    _need(isinstance(actor, str) and actor.strip()
          and isinstance(reason, str) and reason.strip(), "actor_and_reason_required")
    _need(not session.new and not session.dirty and not session.deleted, "uncommitted_source")
    metadata = deepcopy(dict(project.metadata_json))
    terminal = metadata["execution_terminal"]
    _need(isinstance(terminal, Mapping) and terminal.get("code") == CODE
          and terminal.get("terminal") is True, "unsupported_terminal")
    try:
        owner_id = UUID(terminal["workflow_id"])
    except (ValueError, TypeError, AttributeError, KeyError) as error:
        raise ValueError("completed_review_stop_recovery_terminal_workflow_invalid") from error
    _need(project.status == "paused", "project_not_paused")
    # Other independent stop channels must not be hidden by the primary latch.
    for key, value in metadata.items():
        if key == "execution_terminal":
            continue
        code = execution_terminal_code({key: value})
        _need(code is None or code == CODE, "conflicting_terminal")
    if "reason" in terminal:
        _need(terminal["reason"] in (None, CODE), "conflicting_terminal")
    _need(terminal.get("status") not in {
        "exhausted", "unknown", "cancelled", "ledger_unavailable", "expired",
    }, "conflicting_terminal")
    history = metadata.get("execution_programmatic_recovery_history", [])
    _need(isinstance(history, list) and all(isinstance(x, dict) for x in history),
          "history_invalid")

    control = await session.scalar(
        select(BookProductionControlModel)
        .where(BookProductionControlModel.project_id == project.id).with_for_update()
        .execution_options(populate_existing=True, autoflush=False)
    )
    _need(control is not None and control.desired_state == "run", "existing_run_control_required")
    # Select columns so a projection never causes writes/refreshes of the real
    # project. The locked stored value must exactly match the caller's snapshot.
    stored = (await session.execute(
        select(ProjectModel.status, ProjectModel.metadata_json)
        .where(ProjectModel.id == project.id).with_for_update()
        .execution_options(autoflush=False)
    )).one_or_none()
    _need(stored is not None and stored.status == project.status
          and stored.metadata_json == metadata, "project_changed")
    scope = project_execution_scope(project)
    _need(metadata.get("execution_scope_id", scope) == scope
          and current_execution_scope() in (None, scope), "execution_scope_mismatch")
    owner = await _locked_row(session, WorkflowRunModel, owner_id)
    _need(owner is not None and owner.project_id == project.id
          and owner.workflow_type == "chapter_pipeline" and owner.scope_type == "chapter"
          and owner.status == "completed" and owner.scope_id is not None,
          "completed_owner_required")
    owner_metadata = owner.metadata_json
    _need(isinstance(owner_metadata, Mapping)
          and owner_metadata.get("execution_scope_id") == scope, "owner_scope_mismatch")
    chapter = await _locked_row(session, ChapterModel, owner.scope_id)
    _need(chapter is not None and chapter.project_id == project.id, "chapter_owner_mismatch")
    drafts = list(await session.scalars(
        select(ChapterDraftVersionModel).where(
            ChapterDraftVersionModel.project_id == project.id,
            ChapterDraftVersionModel.chapter_id == chapter.id,
            ChapterDraftVersionModel.is_current.is_(True),
        ).with_for_update().execution_options(populate_existing=True, autoflush=False)
    ))
    _need(len(drafts) == 1 and isinstance(drafts[0].content_md, str)
          and drafts[0].content_md.strip(), "exact_current_source_required")
    draft = drafts[0]
    busy = await session.scalar(select(WorkflowRunModel.id).where(
        or_(WorkflowRunModel.project_id == project.id,
            (WorkflowRunModel.scope_type == "project") & (WorkflowRunModel.scope_id == project.id)),
        WorkflowRunModel.status.in_(("pending", "queued", "running", "stopping")),
    ).limit(1))
    _need(busy is None, "active_workflow")
    protected = [(chapter, _row(chapter)), (draft, _row(draft)), (owner, _row(owner)),
                 (control, _row(control))]
    proof = await validate_retained_review_before_processing(session, settings, chapter)
    _need(isinstance(proof, dict) and proof.get("reuse_kind") == "available_review",
          "available_current_review_required")
    cycle = (chapter.metadata_json or {}).get("chapter_critic_cycles", {}).get(proof["cycle_id"])
    fingerprint = pipelines.chapter_review_body_fingerprint(draft.content_md)
    _need(isinstance(cycle, Mapping) and cycle.get("state") == "completed"
          and cycle.get("review_workflow_run_id") == str(owner.id)
          and cycle.get("source_draft_id") == proof["draft_id"] == str(draft.id)
          and cycle.get("source_body_hash") == proof["body_sha256"] == fingerprint["digest"]
          and proof["project_id"] == str(project.id) and proof["chapter_id"] == str(chapter.id),
          "exact_owner_cycle_required")
    _need(owner_metadata.get("critic_cycle_id") == proof["cycle_id"]
          and owner_metadata.get("review_report_id") == proof["report_id"]
          and owner_metadata.get("quality_score_id") == proof["quality_score_id"],
          "owner_result_mismatch")
    for key, expected in (("source_draft_id", str(draft.id)), ("chapter_draft_id", str(draft.id)),
                          ("source_body_hash", fingerprint["digest"]),
                          ("final_verdict", proof["verdict"])):
        _need(key not in owner_metadata or owner_metadata[key] == expected, "owner_source_mismatch")
    report = await _locked_row(session, ReviewReportModel, UUID(proof["report_id"]))
    quality = await _locked_row(session, QualityScoreModel, UUID(proof["quality_score_id"]))
    _need(report is not None and quality is not None, "result_rows_required")
    protected.extend([(report, _row(report)), (quality, _row(quality))])
    ledger = await _ledger(session, scope, lock=True)
    # Re-run current policy/semantic/source validation after ledger inspection.
    _need(await validate_retained_review_before_processing(session, settings, chapter) == proof,
          "source_changed_during_validation")
    _need(not session.new and not session.dirty and not session.deleted, "source_changed_during_validation")
    for row, before in protected:
        model = type(row)
        identity = row.project_id if model is BookProductionControlModel else row.id
        key = model.project_id if model is BookProductionControlModel else model.id
        columns = [getattr(model, attr.key).label(attr.key) for attr in inspect(model).column_attrs]
        actual = (await session.execute(select(*columns).where(key == identity))).mappings().one_or_none()
        # _row serializes UUID/datetime column values for durable receipt hashes.
        current = json.loads(json.dumps(dict(actual), default=str)) if actual is not None else None
        _need(current == before and _row(row) == before, "source_changed_during_validation")
    latest = (await session.execute(select(ProjectModel.status, ProjectModel.metadata_json)
                                   .where(ProjectModel.id == project.id))).one()
    _need(latest.status == "paused" and latest.metadata_json == metadata
          and project.status == "paused" and project.metadata_json == metadata,
          "project_changed")
    receipt = {
        "schema": SCHEMA,
        "actor": actor,
        "reason": reason,
        "recovered_at": datetime.now(timezone.utc).isoformat(),
        "project_id": str(project.id),
        "execution_scope_id": scope,
        "review_workflow_run_id": str(owner.id),
        "previous_terminal": deepcopy(terminal),
        "previous_terminal_sha256": _sha(terminal),
        "owner_binding": {
            "project_id": str(owner.project_id),
            "scope_type": owner.scope_type,
            "scope_id": str(owner.scope_id),
            "workflow_type": owner.workflow_type,
            "status": owner.status,
            "current_step": owner.current_step,
        },
        "owner_sha256": _sha(_row(owner)),
        "binding": deepcopy(proof),
        "cycle_sha256": _sha(cycle),
        "source_sha256": _sha(_row(draft)),
        "report_sha256": _sha(_row(report)),
        "quality_sha256": _sha(_row(quality)),
        "ledger_sha256": _sha(ledger),
        "control_sha256": _sha(_row(control)),
        "physical_prompt_proof": False,
        "grade_changed": False,
        "adoption_created": False,
        "credit_released": False,
        "provider_calls": 0,
        "dispatch_authorized": False,
    }
    receipt["receipt_sha256"] = _sha(receipt)
    metadata["execution_programmatic_recovery_history"] = [*history, deepcopy(receipt)]
    metadata.pop("execution_terminal")
    project.metadata_json = metadata
    return receipt
