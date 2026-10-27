"""Synchronize project status only for a trusted, explicit web RUN command."""

from contextlib import nullcontext
from copy import deepcopy
from datetime import datetime
import hashlib
import json
from types import SimpleNamespace
from typing import Any
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.infra.db.models import (
    BookProductionControlModel,
    ChapterModel,
    ExecutionAttemptModel,
    ExecutionBudgetModel,
    ExecutionOperationModel,
    LlmRunModel,
    ProjectModel,
    WorkflowRunModel,
)
from bestseller.services.budget import (
    ExecutionBudgetError,
    ExecutionReconciliation,
    execution_scope_block_code,
)
from bestseller.services.gate_registry import (
    project_blocks_all_prose_generation,
    project_resume_is_terminally_blocked,
)
from bestseller.services.production_control import request_run
from bestseller.services.retry_ledger import execution_terminal_code, project_execution_scope

_PLANNING_WORKFLOWS = frozenset({
    "generate_novel_plan", "generate_foundation_plan", "generate_volume_plan",
})
_HISTORY_KEY = "web_resume_status_history"
_RECONCILED_PAUSE = "execution_reconciled_manual_resume_required"


class WebProjectResumeBlockedError(ValueError):
    """The explicit request cannot override a newer command or a safety block."""


async def _control_row(
    session: AsyncSession, project_id: UUID, *, lock: bool,
) -> BookProductionControlModel | None:
    statement = select(BookProductionControlModel).where(
        BookProductionControlModel.project_id == project_id,
    ).execution_options(populate_existing=True, autoflush=False)
    if lock:
        statement = statement.with_for_update()
    return (await session.execute(statement)).scalar_one_or_none()


def _control_stamp(row: BookProductionControlModel | None) -> tuple[str, int] | None:
    return (row.desired_state, row.command_serial) if row is not None else None


def _resumable_metadata(project: ProjectModel) -> None:
    """Recognize only the reconciliation already supported by request_run."""
    metadata = deepcopy(project.metadata_json or {})
    if metadata.get("production_pause_reason") == _RECONCILED_PAUSE:
        history = metadata.get("execution_reconciliation_history")
        last = history[-1] if isinstance(history, list) and history else None
        receipt = last.get("receipt") if isinstance(last, dict) else None
        if (
            metadata.get("execution_terminal")
            or not isinstance(receipt, dict)
            or receipt.get("scope_id") != project_execution_scope(project)
            or receipt.get("remaining_unknown") != 0
            or receipt.get("status") != "active"
        ):
            raise WebProjectResumeBlockedError("web_resume_reconciliation_required")
        metadata.pop("production_paused", None)
        metadata.pop("production_pause_reason", None)
    code = execution_terminal_code(metadata)
    if code:
        raise ExecutionBudgetError(code, project_execution_scope(project))
    from bestseller.services.pipelines import _project_blocked_for_structural_repair

    projection = SimpleNamespace(status=project.status, metadata_json=metadata)
    if (
        project_blocks_all_prose_generation(projection)
        or project_resume_is_terminally_blocked(metadata)
        or _project_blocked_for_structural_repair(projection)
    ):
        raise WebProjectResumeBlockedError("web_resume_structurally_blocked")


async def _reconciled_unknown_receipt(
    session: AsyncSession, project: ProjectModel,
) -> dict[str, Any] | None:
    """Prove the ledger half committed without its project projection.

    A historical receipt alone is never permission. Bind the original failed
    workflow to its actual paid attempt, its stopped operation and the exact
    durable quarantine, then check the current ledger and every scope attempt.
    This reader neither reconciles money nor reopens an operation.
    """
    metadata = project.metadata_json or {}
    terminal = metadata.get("execution_terminal")
    if (not isinstance(terminal, dict) or terminal.get("terminal") is not True
        or terminal.get("code") != "execution_result_unknown"):
        return None
    try:
        workflow_id = UUID(terminal["workflow_id"])
    except (KeyError, ValueError, TypeError, AttributeError):
        return None
    if metadata.get("production_pause_reason") not in (None, "execution_result_unknown"):
        return None
    for name in ("execution_reconciliation_history", "reconciled_pause_resume_receipts"):
        history = metadata.get(name, [])
        if not isinstance(history, list) or any(not isinstance(item, dict) for item in history):
            return None
    if len(metadata.get("execution_reconciliation_history", [])) >= 128:
        return None
    scope = project_execution_scope(project)
    ledger = await session.scalar(
        select(ExecutionBudgetModel).where(ExecutionBudgetModel.scope_id == scope)
        .with_for_update().execution_options(populate_existing=True, autoflush=False),
    )
    if (ledger is None or ledger.status != "active" or not isinstance(ledger.metadata_json, dict)
        or execution_scope_block_code(ledger) or ledger.metadata_json.get("cancel_requested")):
        return None
    records = (ledger.metadata_json or {}).get("reconciliations")
    if not isinstance(records, list):
        return None
    owner = await session.get(WorkflowRunModel, workflow_id, populate_existing=True)
    if owner is None or owner.project_id != project.id or owner.status != "failed":
        return None
    failures = list(await session.scalars(
        select(ExecutionOperationModel).where(
            ExecutionOperationModel.scope_id == scope,
            ExecutionOperationModel.status == "transport_failure_receipt",
            ExecutionOperationModel.result_json["workflow_run_id"].as_string() == str(workflow_id),
        ).execution_options(populate_existing=True, autoflush=False),
    ))
    candidates = []
    for failed in failures:
        proof = await _closed_failure_receipt(session, project, ledger, failed, records)
        if proof is None:
            return None
        index, receipt = proof
        if (receipt is not None and receipt["status"] == "active"
            and receipt["remaining_unknown"] == 0):
            candidates.append((index, receipt))
    if not candidates:
        return None
    unclosed = await session.scalar(
        select(ExecutionAttemptModel.id).outerjoin(
            ExecutionOperationModel,
            ExecutionOperationModel.key == ExecutionAttemptModel.operation_key,
        ).where(ExecutionAttemptModel.scope_id == scope, or_(
            ExecutionAttemptModel.status.not_in([
                "completed", "not_sent", "rejected", "reconciled_charged",
            ]),
            ExecutionOperationModel.key.is_(None), ExecutionOperationModel.scope_id != scope,
            ExecutionOperationModel.status.not_in(["completed", "stopped"]),
        )).limit(1).execution_options(autoflush=False),
    )
    inflight = await session.scalar(select(ExecutionOperationModel.key).where(
        ExecutionOperationModel.scope_id == scope,
        ExecutionOperationModel.status.in_(["pending", "unknown"]),
    ).limit(1).execution_options(autoflush=False))
    if unclosed is not None or inflight is not None:
        return None
    # Index order is the ledger's original append order, never inferred from
    # workflow recency. Earlier fully proved quarantines may have remaining > 0.
    return max(candidates, key=lambda item: item[0])[1]


async def _closed_failure_receipt(
    session: AsyncSession, project: ProjectModel, ledger: ExecutionBudgetModel,
    failed: ExecutionOperationModel, records: list[Any],
) -> tuple[int, dict[str, Any] | None] | None:
    """Prove one paid quarantine, or a definitely free earlier failure."""
    scope = ledger.scope_id
    failure = failed.result_json
    if not isinstance(failure, dict):
        return None
    aid = failure.get("attempt_id")
    if (not isinstance(aid, str) or not aid or failure.get("schema") != "transport-failure.v1"
        or type(failure.get("physical_sends")) is not int
        or failure["physical_sends"] not in {0, 1}
        or failure.get("project_id") != str(project.id)
        or failed.key != hashlib.sha256(
            f"transport-failure.v1\n{scope}\n{aid}".encode()).hexdigest()):
        return None
    attempt = await session.get(ExecutionAttemptModel, aid, populate_existing=True)
    if attempt is None or attempt.scope_id != scope or attempt.reserved_tokens <= 0:
        return None
    operation = await session.get(
        ExecutionOperationModel, attempt.operation_key, populate_existing=True,
    )
    if (operation is None or operation.scope_id != scope
        or failure.get("operation_key") != operation.key
        or attempt.input_hash != operation.input_hash or failed.input_hash != attempt.input_hash):
        return None
    # Even a later immutable paid return conflicts with this no-return proof.
    return_keys = [hashlib.sha256(f"{kind}\n{scope}\n{aid}".encode()).hexdigest()
                   for kind in ("transport-result.v1", "provider-return.v1")]
    if await session.scalar(select(ExecutionOperationModel.key).where(
        ExecutionOperationModel.key.in_(return_keys),
    ).limit(1).execution_options(autoflush=False)) is not None:
        return None
    if attempt.actual_tokens == 0 and attempt.status in {"not_sent", "rejected"}:
        status = failure.get("http_status")
        free = (
            attempt.status == "not_sent" and failure["physical_sends"] == 0
        ) or (
            attempt.status == "rejected" and failure["physical_sends"] == 1
            and type(status) is int and 400 <= status < 500 and status not in {400, 408}
        )
        if not free:
            return None
        # A later legitimate reclaim may change this root's owner and produce
        # a paid sibling. Attribute contradictions to the free attempt itself.
        returned = await session.scalar(select(ExecutionOperationModel.key).where(
            ExecutionOperationModel.status.not_in([
                "transport_failure_receipt", "reconciliation_receipt",
            ]),
            or_(ExecutionOperationModel.result_json["attempt_id"].as_string() == aid,
                ExecutionOperationModel.result_json["receipt"]["attempt_id"].as_string() == aid),
        ).limit(1).execution_options(autoflush=False))
        run = await session.scalar(select(LlmRunModel.id).where(
            LlmRunModel.metadata_json["execution_receipt"]["attempt_id"].as_string() == aid,
        ).limit(1).execution_options(autoflush=False))
        return (-1, None) if returned is None and run is None else None
    if (attempt.status != "reconciled_charged" or attempt.actual_tokens is not None
        or failure["physical_sends"] != 1 or operation.status != "stopped"
        or operation.result_json is not None or failed.owner != operation.owner):
        return None
    # Exact root/attempt attribution, including aliased receipts in other
    # scopes. A shared input hash alone never attributes a paid return.
    linked_return = await session.scalar(select(ExecutionOperationModel.key).where(
        ExecutionOperationModel.key != operation.key,
        ExecutionOperationModel.status.not_in([
            "transport_failure_receipt", "reconciliation_receipt",
        ]),
        or_(
            ExecutionOperationModel.result_json["operation_key"].as_string() == operation.key,
            ExecutionOperationModel.result_json["attempt_id"].as_string() == aid,
            ExecutionOperationModel.result_json["receipt"]["operation_key"].as_string()
            == operation.key,
            ExecutionOperationModel.result_json["receipt"]["attempt_id"].as_string() == aid,
        ),
    ).limit(1).execution_options(autoflush=False))
    linked_run = await session.scalar(select(LlmRunModel.id).where(
        LlmRunModel.metadata_json["execution_receipt"]["operation_key"].as_string()
        == operation.key,
    ).limit(1).execution_options(autoflush=False))
    if linked_return is not None or linked_run is not None:
        return None
    matching = [(index, record) for index, record in enumerate(records) if isinstance(record, dict)
                and isinstance(record.get("receipt"), dict)
                and record["receipt"].get("attempt_id") == aid]
    if len(matching) != 1:
        return None
    index, record = matching[0]
    receipt = record["receipt"]
    identifier = record.get("reconciliation_id")
    if (not isinstance(identifier, str) or not identifier
        or receipt.get("reconciliation_id") != identifier or receipt.get("scope_id") != scope
        or receipt.get("decision") != "quarantine_charged"
        or type(receipt.get("remaining_unknown")) is not int or receipt["remaining_unknown"] < 0
        or receipt.get("status") != ("active" if receipt["remaining_unknown"] == 0 else "unknown")
        or receipt.get("remote_outcome") != "unknown_quarantined"
        or receipt.get("operation_replay_allowed") is not False
        or receipt.get("credit_released") is not False
        or receipt.get("automatic_resume") is not False
        or record.get("evidence_kind") != "dispatcher_exit"
        or record.get("local_dispatcher_stopped") is not True
        or record.get("remote_operation_closed") is not False):
        return None
    if (type(receipt.get("calls_used")) is not int
        or not 0 < receipt["calls_used"] <= ledger.calls_used
        or type(receipt.get("tokens_charged")) is not int
        or not attempt.reserved_tokens <= receipt["tokens_charged"] <= ledger.tokens_charged):
        return None
    try:
        if datetime.fromisoformat(record["at"]).tzinfo is None:
            return None
        evidence = ExecutionReconciliation(
            reconciliation_id=identifier, attempt_id=aid, expected_input_hash=attempt.input_hash,
            decision=receipt["decision"], evidence_kind=record["evidence_kind"],
            evidence_sha256=record.get("evidence_sha256"), actor=record.get("actor"),
            local_dispatcher_stopped=record["local_dispatcher_stopped"],
            remote_operation_closed=record["remote_operation_closed"],
        )
    except (KeyError, ValueError, TypeError):
        return None
    fingerprint = hashlib.sha256(json.dumps(evidence.model_dump(mode="json"),
        ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    saved = await session.get(ExecutionOperationModel, hashlib.sha256(
        f"reconciliation.v1\n{scope}\n{identifier}".encode()).hexdigest(), populate_existing=True)
    if (saved is None or saved.scope_id != scope or saved.status != "reconciliation_receipt"
        or saved.input_hash != fingerprint or record.get("request_hash") != fingerprint
        or saved.result_json != record):
        return None
    return index, receipt


async def _resume_phase(
    session: AsyncSession, project_id: UUID,
) -> tuple[str, dict[str, Any]]:
    latest = await session.scalar(
        select(WorkflowRunModel)
        .where(WorkflowRunModel.project_id == project_id)
        .order_by(WorkflowRunModel.created_at.desc(), WorkflowRunModel.id.desc())
        .limit(1)
        .execution_options(populate_existing=True, autoflush=False)
    )
    evidence: dict[str, Any] = {"latest_workflow": None}
    if latest is not None:
        evidence["latest_workflow"] = {
            "id": str(latest.id), "type": latest.workflow_type, "status": latest.status,
        }
        if latest.workflow_type in _PLANNING_WORKFLOWS and latest.status != "completed":
            return "planning", evidence
    chapter_id = await session.scalar(
        select(ChapterModel.id).where(ChapterModel.project_id == project_id).limit(1),
        execution_options={"autoflush": False},
    )
    evidence["has_materialized_chapter"] = chapter_id is not None
    return ("writing" if chapter_id is not None else "planning"), evidence


async def request_explicit_web_resume(
    session: AsyncSession, *, slug: str, actor: str, reason: str,
) -> bool:
    """Issue RUN and repair its paused projection in the caller's transaction.

    Lock control before project, matching web cancellation. A changed command
    or project between observation and locking is refused; a later pause still
    wins and remains enforced by the unchanged worker/knowledge guards.
    """
    if not actor.strip() or not reason.strip():
        raise WebProjectResumeBlockedError("web_resume_requires_explicit_actor")
    initial = (await session.execute(
        select(ProjectModel.id, ProjectModel.status, ProjectModel.updated_at)
        .where(ProjectModel.slug == slug)
        .execution_options(autoflush=False),
    )).one_or_none()
    if initial is None:
        return False
    observed = _control_stamp(await _control_row(session, initial.id, lock=False))
    control = await _control_row(session, initial.id, lock=True)
    if _control_stamp(control) != observed:
        raise WebProjectResumeBlockedError("web_resume_command_changed")
    project = await session.scalar(
        select(ProjectModel).where(ProjectModel.id == initial.id).with_for_update()
        .execution_options(populate_existing=True, autoflush=False),
    )
    if project is None or (project.status, project.updated_at) != (
        initial.status, initial.updated_at,
    ):
        raise WebProjectResumeBlockedError("web_resume_project_changed")
    # A legacy project may have no control row to lock on the first read.
    # Refuse a pause inserted while acquiring the project lock as well.
    control = await _control_row(session, initial.id, lock=True)
    if _control_stamp(control) != observed:
        raise WebProjectResumeBlockedError("web_resume_command_changed")
    if project.status in {"archived", "cancelled", "completed"}:
        raise WebProjectResumeBlockedError("web_resume_project_terminal")
    if control is not None and control.desired_state not in {"run", "pause", "stop"}:
        raise WebProjectResumeBlockedError("web_resume_unknown_control")
    projection = project
    if execution_terminal_code(project.metadata_json) == "execution_result_unknown":
        try:
            with session.no_autoflush:
                receipt = await _reconciled_unknown_receipt(session, project)
        except Exception as error:
            raise ExecutionBudgetError(
                "execution_ledger_unavailable", project_execution_scope(project),
            ) from error
        if receipt is None:
            _resumable_metadata(project)  # The original unknown guard remains authoritative.
        else:
            # Stage the trusted migration off-row so a structural/history veto
            # cannot leave a half-migrated project in the caller's transaction.
            from bestseller.worker.tasks import clear_reconciled_execution_terminal

            projection = SimpleNamespace(
                id=project.id, status=project.status, metadata_json=deepcopy(project.metadata_json),
            )
            if not await clear_reconciled_execution_terminal(
                session, projection, scope_id=project_execution_scope(project), receipt=receipt,
            ):
                raise WebProjectResumeBlockedError("web_resume_reconciliation_required")
    if execution_terminal_code(project.metadata_json) == "execution_success_audit_missing":
        # Successful completion of the exact failed owner may retire only its
        # stale local stop. Stage it off-row until the normal RUN also passes.
        from bestseller.services.completed_review_stop_recovery import recover_completed_review_stop
        from bestseller.services.creation_configuration import project_settings
        from bestseller.settings import apply_runtime_llm_profile, load_settings

        projection = SimpleNamespace(
            id=project.id, status=project.status, metadata_json=deepcopy(project.metadata_json),
        )
        try:
            proof = await recover_completed_review_stop(
                session,
                project_settings(apply_runtime_llm_profile(load_settings()), project),
                projection,
                actor=actor,
                reason=reason,
            )
        except ExecutionBudgetError:
            raise
        except (ValueError, TypeError, KeyError) as error:
            raise WebProjectResumeBlockedError("web_resume_completed_review_proof_invalid") from error
        except Exception as error:
            raise ExecutionBudgetError(
                "execution_ledger_unavailable", project_execution_scope(project),
            ) from error
        if proof is None:
            raise WebProjectResumeBlockedError("web_resume_completed_review_proof_required")
    # An explicit first RUN must leave a native command receipt; implicit
    # legacy RUN/0 cannot authorize a waiting review's once-only rearm.
    if (projection is project and project.status != "paused"
        and control is not None and control.desired_state == "run"):
        return False
    _resumable_metadata(projection)
    scope = project_execution_scope(project)
    try:
        ledger = await session.scalar(
            select(ExecutionBudgetModel).where(ExecutionBudgetModel.scope_id == scope)
            .execution_options(populate_existing=True, autoflush=False),
        )
    except Exception as error:
        raise ExecutionBudgetError("execution_ledger_unavailable", scope) from error
    if ledger is not None and (code := execution_scope_block_code(ledger)):
        raise ExecutionBudgetError(code, scope)
    old_status = project.status
    phase, evidence = (
        await _resume_phase(session, project.id)
        if old_status == "paused" else (old_status, {})
    )
    detail = deepcopy(control.detail_json or {}) if control is not None else {}
    history = detail.get(_HISTORY_KEY, [])
    if not isinstance(history, list):
        raise WebProjectResumeBlockedError("web_resume_invalid_history")
    old_control = None if control is None else {
        "desired_state": control.desired_state, "command_serial": control.command_serial,
        "reason": control.state_reason, "requested_by": control.requested_by,
        "requested_at": control.requested_at.isoformat() if control.requested_at else None,
        "resume_pending": control.resume_pending,
        "resume_pending_at": (
            control.resume_pending_at.isoformat() if control.resume_pending_at else None
        ),
        "detail": {key: value for key, value in detail.items() if key != _HISTORY_KEY},
    }
    detail[_HISTORY_KEY] = [*history, {
        "from_status": old_status, "to_status": phase, "previous_control": old_control,
        "actor": actor, "reason": reason, "phase_evidence": evidence,
    }]
    # Keep the migration and RUN together even when a caller catches a later
    # refusal and commits its outer transaction. The normal caller still owns
    # the final commit; unrelated local work is not rolled back.
    async with session.begin_nested() if projection is not project else nullcontext():
        if projection is not project:
            project.metadata_json = projection.metadata_json
        await request_run(session, project.id, actor=actor, reason=reason, detail=detail)
        # request_run alone may clear only a proven reconciliation marker; it never
        # grants budget or retires any other structural/financial stop.
        if (project.metadata_json or {}).get("production_pause_reason") == _RECONCILED_PAUSE:
            raise WebProjectResumeBlockedError("web_resume_reconciliation_required")
        _resumable_metadata(project)
        if old_status == "paused":
            project.status = phase
        await session.flush()
    return True
