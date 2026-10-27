"""Bounded zero-model recovery of native delivery, never prose or paid recovery.

Each run owns fresh sessions and rechecks source/control/finance under native
lock order. Candidate metadata is not authority. No workflow, grant or retry
credit is created. Files left by a failed transaction are retained for audit and
the next native exporter/readback; they cannot certify a rolled-back delivery.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import Enum
from decimal import Decimal
import hashlib
import json
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import event, inspect, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import set_committed_value

from bestseller.infra.db import models as m
from bestseller.services.book_delivery_readiness import read_book_delivery_closure, read_book_delivery_readiness
from bestseller.services.creation_configuration import project_settings
from bestseller.services.production_control import load_control_state


class ExportOnlyStatus(str, Enum):
    ELIGIBLE = "eligible"
    BLOCKED = "blocked"
    ALREADY_COMPLETE = "already_complete"
    COMPLETED = "completed"


@dataclass(frozen=True)
class ExportOnlyRecoveryResult:
    status: ExportOnlyStatus
    project_id: UUID
    reason: str
    source_receipt: dict | None = None
    audit_path: str | None = None
    audit: dict | None = None

    def to_dict(self):
        result = asdict(self)
        result.update(status=self.status.value, project_id=str(self.project_id))
        return json.loads(_json(result))


class _Blocked(ValueError):
    pass


def _need(condition, reason):
    if not condition:
        raise _Blocked(reason)


def _json(value):
    def scalar(item):
        if isinstance(item, (UUID, datetime, Decimal)):
            return str(item)
        raise TypeError(type(item).__name__)
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False, default=scalar)


def _sha(value):
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _row(row):
    return {attr.key: deepcopy(getattr(row, attr.key)) for attr in inspect(row).mapper.column_attrs}


async def _finance(session, project, *, lock):
    """No new credit is needed for export; unresolved payment still blocks it."""
    from bestseller.services.execution_control_purpose import verify_non_dispatch_control
    scope = (project.metadata_json or {}).get("execution_scope_id")
    _need(isinstance(scope, str) and bool(scope), "execution_scope_missing")
    stmt = select(m.ExecutionBudgetModel).where(m.ExecutionBudgetModel.scope_id == scope).execution_options(populate_existing=True)
    budget = await session.scalar(stmt.with_for_update() if lock else stmt)
    operations = list(await session.scalars(select(m.ExecutionOperationModel).where(
        m.ExecutionOperationModel.scope_id == scope).order_by(m.ExecutionOperationModel.key).execution_options(populate_existing=True)))
    attempts = list(await session.scalars(select(m.ExecutionAttemptModel).where(
        m.ExecutionAttemptModel.scope_id == scope).order_by(m.ExecutionAttemptModel.id).execution_options(populate_existing=True)))
    runs = list(await session.scalars(select(m.LlmRunModel).where(
        m.LlmRunModel.project_id == project.id).order_by(m.LlmRunModel.id).execution_options(populate_existing=True)))
    by_key = {row.key: row for row in operations}
    _need(all(a.operation_key in by_key for a in attempts), "orphan_paid_attempt")
    _need(all(a.status in {"completed", "not_sent", "rejected", "reconciled_charged"}
              for a in attempts), "unsettled_paid_attempt")
    closed = {"completed", "stopped", "transport_result", "transport_failure_receipt",
              "reconciliation_receipt", "unsent_reclaim_receipt"}
    for operation in operations:
        if operation.status not in closed:
            _need(await verify_non_dispatch_control(session, operation=operation,
                operations=operations, attempts=attempts) is not None, "unsettled_paid_operation")
    return _sha({"budget": _row(budget) if budget is not None else None,
                 "operations": [_row(r) for r in operations], "attempts": [_row(r) for r in attempts],
                 "llm_runs": [_row(r) for r in runs]})


async def _owners(session, project, requested_by, owner_workflow_run_id, *, lock):
    stmt = select(m.WorkflowRunModel).where(m.WorkflowRunModel.project_id == project.id,
        m.WorkflowRunModel.status.in_(("pending", "queued", "running", "stopping"))).order_by(m.WorkflowRunModel.id).execution_options(populate_existing=True)
    owners = list(await session.scalars(stmt.with_for_update() if lock else stmt))
    for owner in owners:
        _need(owner.id == owner_workflow_run_id and owner.workflow_type == "export_only_recovery"
              and owner.requested_by == requested_by, "active_foreign_owner")
    pending = await session.scalar(select(m.RewriteTaskModel.id).where(
        m.RewriteTaskModel.project_id == project.id,
        m.RewriteTaskModel.status.in_(("pending", "queued", "processing", "running"))).limit(1))
    _need(pending is None, "pending_rewrite")


def _project_source(project):
    meta = project.metadata_json or {}
    # Everything except the documented native delivery projection is conserved,
    # including all Canon/design, failed-title histories, counters and debt.
    allowed = {"fanqie_short_ready_for_upload", "fanqie_whole_export_receipt", "fanqie_whole_export_history"}
    if project.project_type != "fanqie_short":
        allowed = {k for k in meta if k.startswith("completion_") or k == "completed_at"} | {"book_delivery"}
    return {"project": {k: getattr(project, k) for k in (
        "id", "slug", "title", "genre", "language", "project_type", "target_chapters", "target_word_count")},
        "metadata": {k: deepcopy(v) for k, v in meta.items() if k not in allowed}}


async def _source(session, project, *, lock):
    if project.project_type == "fanqie_short":
        from bestseller.services.fanqie_short_source_export import read_adopted_assembly
        assembly = await read_adopted_assembly(session, project.id, lock=lock)
        return {"project": _project_source(project), "manifest": assembly.manifest,
                "grades": await _grades(session, project, assembly.manifest["sources"]),
                "retained_rows_sha256": await _retained_rows(session, project.id, lock=False)}, assembly
    if lock:
        await _retained_rows(session, project.id, lock=True)
    ready = await read_book_delivery_readiness(session, project, lock=lock)
    _need(ready.verdict.body_complete, "adoption_or_knowledge_incomplete")
    return {"project": _project_source(project), "sources": list(ready.sources),
            "grades": await _grades(session, project, ready.sources),
            "retained_rows_sha256": await _retained_rows(session, project.id, lock=False)}, ready


async def _retained_rows(session, project_id, *, lock):
    rows = {}
    for model, order in (
        (m.ChapterModel, (m.ChapterModel.chapter_number, m.ChapterModel.id)),
        (m.ChapterDraftVersionModel, (m.ChapterDraftVersionModel.chapter_id, m.ChapterDraftVersionModel.id)),
        (m.ChapterStateSnapshotModel, (m.ChapterStateSnapshotModel.chapter_id,)),
    ):
        stmt = select(model).where(model.project_id == project_id).order_by(*order).execution_options(populate_existing=True)
        values = list(await session.scalars(stmt.with_for_update() if lock else stmt))
        rows[model.__tablename__] = [_row(row) for row in values]
    return _sha(rows)


async def _grades(session, project, sources):
    """Existing exact-source native review rows only; never a new judgment.

    Verified accepted numeric debt uses the native adoption reader's receipt;
    a promoted flag by itself cannot stand in for a retained PASS report.
    """
    from bestseller.services.reviews import chapter_review_body_fingerprint
    result = []
    for item in sources:
        lineage = item.get("lineage", item)
        if lineage.get("adoption_state") == "accepted_with_debt":
            _need(bool(lineage.get("adoption_receipt")), "adopted_debt_receipt_missing")
            result.append(deepcopy(lineage["adoption_receipt"]))
            continue
        draft_id, chapter_id = UUID(item["draft_id"]), UUID(item["chapter_id"])
        draft = await session.scalar(select(m.ChapterDraftVersionModel).where(
            m.ChapterDraftVersionModel.id == draft_id, m.ChapterDraftVersionModel.project_id == project.id,
            m.ChapterDraftVersionModel.chapter_id == chapter_id).execution_options(populate_existing=True))
        _need(draft is not None, "grade_source_missing")
        scores = list(await session.scalars(select(m.QualityScoreModel).where(
            m.QualityScoreModel.project_id == project.id, m.QualityScoreModel.target_type == "chapter",
            m.QualityScoreModel.target_id == chapter_id, m.QualityScoreModel.chapter_draft_version_id == draft_id,
            m.QualityScoreModel.is_current.is_(True)).execution_options(populate_existing=True)))
        _need(len(scores) == 1, "current_grade_missing_or_ambiguous")
        score = scores[0]
        report = await session.scalar(select(m.ReviewReportModel).where(
            m.ReviewReportModel.id == score.review_report_id, m.ReviewReportModel.project_id == project.id,
            m.ReviewReportModel.target_type == "chapter", m.ReviewReportModel.target_id == chapter_id
        ).execution_options(populate_existing=True))
        _need(report is not None and report.verdict == "pass", "retained_pass_report_required")
        payload = report.structured_output or {}
        _need(payload.get("draft_id") == str(draft_id) and payload.get("reviewed_body_fingerprint")
              == chapter_review_body_fingerprint(draft.content_md), "retained_grade_source_mismatch")
        _need((payload.get("critic_evidence") or {}).get("status") == "verified"
              and (payload.get("critic_evidence") or {}).get("passed") is True, "retained_grade_unverified")
        run = await session.scalar(select(m.LlmRunModel).where(m.LlmRunModel.id == report.llm_run_id,
            m.LlmRunModel.project_id == project.id).execution_options(populate_existing=True))
        _need(run is not None, "retained_grade_native_run_missing")
        owner = await session.scalar(select(m.WorkflowRunModel).where(m.WorkflowRunModel.id == run.workflow_run_id,
            m.WorkflowRunModel.project_id == project.id).execution_options(populate_existing=True))
        _need(owner is not None and owner.status == "completed", "retained_grade_owner_not_closed")
        result.append({"quality_score_sha256": _sha(_row(score)), "report_sha256": _sha(_row(report)),
                       "owner_id": str(owner.id), "native_run_id": str(run.id)})
    return result


async def _gates(session, settings, project, source):
    if project.project_type == "fanqie_short":
        from bestseller.services.fanqie_short_quality import review_whole_fanqie_short_story
        from bestseller.services.fanqie_short_semantic_readiness import load_upload_semantic_context, semantic_readiness_view
        ratio = float((project.metadata_json or {}).get("unlock_line_ratio") or .3)
        protagonist = "我" if (project.metadata_json or {}).get("pov", "first_person") == "first_person" else None
        report = review_whole_fanqie_short_story(source.full_text, title=project.title,
            unlock_line_ratio=ratio, protagonist_name=protagonist)
        context = await load_upload_semantic_context(session, settings=settings, project=project,
            assembly=source, original_report=report)
        view = semantic_readiness_view(context, full_text=source.full_text, title=project.title,
            unlock_line_ratio=ratio, protagonist_name=protagonist)
        _need(report.passed if view is None else not view["semantic_derived_review"]["remaining_blockers"],
              "current_whole_gate_blocked")
    else:
        from bestseller.services.exports import _raise_if_export_blocked, _run_terminal_export_gate
        # Native pure gates annotate chapter metadata. Copies keep this preflight
        # strictly readonly; no text patches or old grade changes are accepted.
        payloads = deepcopy(list(source.payloads))
        _raise_if_export_blocked(project, payloads, debt_warnings=[])
        _run_terminal_export_gate(project, payloads, settings=settings)


async def _inspect(session, settings, project_id, requested_by, owner_workflow_run_id, *, lock):
    _need(bool(requested_by.strip()), "requested_by_required")
    _need(not getattr(session, "new", ()) and not getattr(session, "deleted", ())
          and not any(session.is_modified(row, include_collections=True)
                      for row in getattr(session, "dirty", ())), "pending_caller_state")
    control = await load_control_state(session, project_id, for_update=lock)
    _need(not control.halted, "operator_halted")
    stmt = select(m.ProjectModel).where(m.ProjectModel.id == project_id).execution_options(populate_existing=True)
    project = await session.scalar(stmt.with_for_update() if lock else stmt)
    _need(project is not None, "project_missing")
    _need(project.status in {"planning", "writing", "revising", "completed"}, "project_state_blocked")
    closure = await read_book_delivery_closure(session, project, lock=False)
    if closure.verdict.is_complete:
        receipt = {"project": _project_source(project), "sources": list(closure.sources)}
        receipt["source_sha256"] = _sha(receipt)
        return None, ExportOnlyRecoveryResult(ExportOnlyStatus.ALREADY_COMPLETE, project_id,
            closure.verdict.reason, receipt)
    _need(closure.verdict.body_complete, "adoption_or_knowledge_incomplete")
    receipt, source = await _source(session, project, lock=lock)
    # Native order: control -> project -> all chapters/drafts/knowledge -> owner -> budget.
    await _owners(session, project, requested_by, owner_workflow_run_id, lock=lock)
    finance = await _finance(session, project, lock=lock)
    configured = project_settings(settings, project)
    await _gates(session, configured, project, source)
    receipt.update(control=control.to_payload(), financial_sha256=finance)
    receipt["source_sha256"] = _sha(receipt)
    return (project, configured, source), ExportOnlyRecoveryResult(
        ExportOnlyStatus.ELIGIBLE, project_id, "native_export_only_eligible", receipt)


async def preflight_export_only_recovery(session, settings, *, project_id, requested_by,
                                         owner_workflow_run_id=None):
    """Readonly assessment; eligible is permission to recheck, not a saved grade."""
    try:
        _, result = await _inspect(session, settings, project_id, requested_by, owner_workflow_run_id, lock=False)
        return result
    except Exception as error:
        return ExportOnlyRecoveryResult(ExportOnlyStatus.BLOCKED, project_id, type(error).__name__ + ":" + str(error)[:300])


def _audit_write(path, value):
    root = path.parents[2].resolve()
    def safe_parent():
        _need(all(not directory.is_symlink() and directory.resolve().is_relative_to(root)
                  for directory in (path.parent.parent, path.parent)), "unsafe_audit_path")
        _need(not path.is_symlink() and not path.with_suffix(".tmp").is_symlink(), "unsafe_audit_path")
    safe_parent()
    path.parent.mkdir(parents=True, exist_ok=True)
    safe_parent()
    temporary = path.with_suffix(".tmp")
    temporary.write_text(_json(value), encoding="utf-8")
    temporary.replace(path)


def _protect_chapters(session, sources):
    """Suppress only native export's derived hash annotation, never its gate.

    This session owns no source writes. The native exporter still runs its full
    gate. Its optional chapter annotation must not alter already-adopted rows.
    """
    expected = {UUID(s["chapter_id"]): s["body_sha256"] for s in sources}
    def guard(sync_session, *_args):
        for row in (*sync_session.new, *sync_session.deleted):
            _need(not isinstance(row, (m.ChapterModel, m.ChapterDraftVersionModel, m.ChapterStateSnapshotModel)),
                  "export_attempted_source_write")
        for row in tuple(sync_session.dirty):
            if isinstance(row, m.ChapterModel):
                _need(row.id in expected, "export_attempted_chapter_write")
                history = inspect(row).attrs.metadata_json.history
                old = history.deleted[0] if history.deleted else row.metadata_json
                if row.metadata_json != old:
                    _need(row.metadata_json == {**(old or {}),
                        "terminal_quality_gate_content_hash": expected.get(row.id)}, "export_attempted_chapter_write")
                    set_committed_value(row, "metadata_json", deepcopy(old))
                _need(not sync_session.is_modified(row, include_collections=True), "export_attempted_chapter_write")
            elif isinstance(row, (m.ChapterDraftVersionModel, m.ChapterStateSnapshotModel)):
                _need(not sync_session.is_modified(row, include_collections=True), "export_attempted_source_write")
    event.listen(session.sync_session, "before_flush", guard)


async def run_export_only_recovery(session_factory, settings, *, project_id, requested_by,
                                   owner_workflow_run_id=None):
    """Own the export transaction. No operator witness or model gateway exists.

    Optional owner ID permits only the caller's actual same-project native
    export_only_recovery WF. Every other live owner blocks this path.
    """
    audit_path = None
    audit = {"provider_calls": 0, "requested_by": requested_by, "project_id": str(project_id)}
    receipt = None
    commit_started = False
    committed = False
    try:
        async with session_factory() as session:
            try:
                plan, checked = await _inspect(session, settings, project_id, requested_by, owner_workflow_run_id, lock=True)
                if plan is None:
                    await session.rollback()
                    return checked
                project, configured, source = plan
                receipt = checked.source_receipt
                output = (Path(configured.output.base_dir) / project.slug).resolve()
                _need(output.is_relative_to(Path(configured.output.base_dir).resolve()), "unsafe_output_slug")
                audit_path = output / ".recovery" / "export-only" / (uuid4().hex + ".json")
                audit.update(status="eligible", source_receipt=receipt)
                _audit_write(audit_path, audit)
                # The existing Fanqie checkpoint commit releases a SAVEPOINT,
                # never the owning transaction or its source/control locks.
                async with AsyncSession(bind=await session.connection(), join_transaction_mode="create_savepoint",
                                        autoflush=False, expire_on_commit=False) as publisher:
                    if project.project_type == "fanqie_short":
                        from bestseller.services.fanqie_short_source_export import publish_adopted_assembly, read_fanqie_delivery_evidence
                        result = await publish_adopted_assembly(publisher, project_id=project_id, output_dir=output,
                            expected=source, unlock_line_ratio=float((project.metadata_json or {}).get("unlock_line_ratio") or .3),
                            protagonist_name="我" if (project.metadata_json or {}).get("pov", "first_person") == "first_person" else None,
                            settings=configured)
                        _need(result["whole_book_complete"] is True, "native_publish_not_complete")
                        await read_fanqie_delivery_evidence(publisher, project_id, settings=configured)
                    else:
                        from bestseller.services.book_closure import settle_project_status_on_closure
                        _protect_chapters(publisher, list(source.sources))
                        stored = await publisher.get(m.ProjectModel, project_id)
                        verdict = await settle_project_status_on_closure(publisher, stored, settings=configured,
                            fallback_status=project.status, now_iso=datetime.now(UTC).isoformat())
                        if not verdict.is_complete:
                            audit["native_failure"] = deepcopy((stored.metadata_json or {}).get("book_delivery"))
                        _need(verdict.is_complete, "native_settlement_not_complete")
                    await publisher.commit()
                await session.refresh(project)
                current, _ = await _source(session, project, lock=True)
                _need(current == {k: v for k, v in receipt.items() if k not in {
                    "control", "financial_sha256", "source_sha256"}}, "source_changed_during_export")
                control = await load_control_state(session, project_id, for_update=True)
                _need(control.to_payload() == receipt["control"] and not control.halted, "control_changed_during_export")
                _need(await _finance(session, project, lock=False) == receipt["financial_sha256"], "financial_changed_during_export")
                final = await read_book_delivery_closure(session, project, lock=False)
                _need(final.verdict.is_complete, "native_precommit_readback_failed")
                audit.update(status="commit_attempt_in_progress")
                _audit_write(audit_path, audit)
                commit_started = True
                await session.commit()
                committed = True
            except BaseException:
                await session.rollback()
                raise
        async with session_factory() as fresh:
            stored = await fresh.get(m.ProjectModel, project_id)
            final = await read_book_delivery_closure(fresh, stored, lock=False)
            _need(final.verdict.is_complete, "native_postcommit_readback_failed")
            current, _ = await _source(fresh, stored, lock=False)
            _need(current == {k: v for k, v in receipt.items() if k not in {
                "control", "financial_sha256", "source_sha256"}}, "postcommit_source_changed")
            _need(await _finance(fresh, stored, lock=False) == receipt["financial_sha256"], "postcommit_financial_changed")
            await fresh.rollback()
        audit.update(status="completed")
        _audit_write(audit_path, audit)
        return ExportOnlyRecoveryResult(ExportOnlyStatus.COMPLETED, project_id, "native_delivery_verified", receipt, str(audit_path), audit)
    except BaseException as error:
        audit.update(status="committed_readback_failed" if committed else "commit_outcome_requires_readback" if commit_started else "rolled_back",
                     error_type=type(error).__name__, reason=str(error)[:300])
        if audit_path is not None:
            try:
                _audit_write(audit_path, audit)
            except (OSError, ValueError):
                audit["audit_write_failed"] = True
        if not isinstance(error, Exception):
            raise
        return ExportOnlyRecoveryResult(ExportOnlyStatus.BLOCKED, project_id,
            audit["reason"], receipt, str(audit_path) if audit_path else None, audit)


__all__ = ["ExportOnlyRecoveryResult", "ExportOnlyStatus", "preflight_export_only_recovery", "run_export_only_recovery"]
