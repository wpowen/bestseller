"""Operator-only attribution of explicitly selected, workflow-bound LLM rows.

No discovery, commits, model calls, ledger changes or guessed historical owners.
The caller owns the transaction and must retain the dry-run and apply results.
"""
from collections.abc import Mapping
from copy import deepcopy
from datetime import UTC, datetime
import hashlib
import json
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.infra.db.models import LlmRunModel, ProjectModel, WorkflowRunModel
from bestseller.services.retry_ledger import project_execution_scope

_PROJECT_RECEIPTS = "llm_project_attribution_receipts"
_ROW_RECEIPTS = "project_attribution_receipts"
_SCHEMA = "llm-project-attribution.v1"


def _json_default(value: object) -> str:
    if isinstance(value, datetime):
        return value.replace(tzinfo=UTC).isoformat() if value.tzinfo is None else (
            value.astimezone(UTC).isoformat()
        )
    if isinstance(value, UUID):
        return str(value)
    raise TypeError(f"unsupported attribution hash type: {type(value).__name__}")


def _hash(value: object) -> str:
    return hashlib.sha256(json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        default=_json_default, allow_nan=False,
    ).encode()).hexdigest()


def _row_payload(row: LlmRunModel) -> dict[str, Any]:
    # Column attribute keys include metadata_json, not the SQL name metadata.
    return {attr.key: deepcopy(getattr(row, attr.key))
            for attr in row.__mapper__.column_attrs}


def llm_attribution_row_hash(row: LlmRunModel) -> str:
    """Hash every persisted LLM column, including all existing metadata."""
    return _hash(_row_payload(row))


def _receipts(metadata: Mapping[str, Any], key: str) -> list[dict[str, Any]]:
    value = metadata.get(key, [])
    if not isinstance(value, list) or any(not isinstance(r, dict) for r in value):
        raise ValueError("attribution_receipt_history_invalid")
    return deepcopy(value)


async def reconcile_llm_project_attribution(
    session: AsyncSession, *, project_id: UUID, scope_id: str,
    expected_row_hashes: Mapping[UUID | str, str], actor: str,
    evidence_ref: str, idempotency_key: str, apply: bool = False,
) -> dict[str, Any]:
    """Validate the whole batch before filling only NULL project IDs.

    Full before/after LLM row hashes live in the project's append-only receipt
    list. Each LLM row appends its original-NULL evidence and a receipt pointer.
    Keeping the full after hash outside that row avoids a self-hashing receipt.
    Dry-run computes planned hashes without modifying ORM state. No commit.
    """
    if not all(isinstance(v, str) and v.strip() and len(v) <= 1024
               for v in (scope_id, actor, evidence_ref, idempotency_key)):
        raise ValueError("explicit_attribution_operator_evidence_required")
    if type(apply) is not bool:
        raise ValueError("explicit_boolean_apply_required")
    if not 1 <= len(expected_row_hashes) <= 100:
        raise ValueError("explicit_bounded_row_selection_required")
    expected = {UUID(str(key)): value for key, value in expected_row_hashes.items()}
    if len(expected) != len(expected_row_hashes) or any(
        not isinstance(value, str) or len(value) != 64
        or any(char not in "0123456789abcdef" for char in value)
        for value in expected.values()
    ):
        raise ValueError("exact_row_sha256_required")
    project_id = UUID(str(project_id))
    request = {
        "project_id": str(project_id), "scope_id": scope_id, "actor": actor,
        "evidence_ref": evidence_ref,
        "rows": {str(key): expected[key] for key in sorted(expected, key=str)},
    }
    request_hash = _hash(request)
    with session.no_autoflush:
        project = await session.scalar(select(ProjectModel).where(
            ProjectModel.id == project_id,
        ).with_for_update().execution_options(populate_existing=True))
        if project is None or project_execution_scope(project) != scope_id:
            raise ValueError("attribution_project_scope_mismatch")
        project_meta = deepcopy(project.metadata_json or {})
        history = _receipts(project_meta, _PROJECT_RECEIPTS)
        prior = [r for r in history if r.get("idempotency_key") == idempotency_key]
        if len(prior) > 1 or (prior and prior[0].get("request_sha256") != request_hash):
            raise ValueError("attribution_idempotency_conflict")
        rows = list(await session.scalars(select(LlmRunModel).where(
            LlmRunModel.id.in_(expected),
        ).order_by(LlmRunModel.id).with_for_update().execution_options(populate_existing=True)))
        if len(rows) != len(expected):
            raise ValueError("attribution_row_not_found")
        workflow_ids = {row.workflow_run_id for row in rows}
        if None in workflow_ids:
            raise ValueError("attribution_workflow_required")
        workflows = {row.id: row for row in await session.scalars(
            select(WorkflowRunModel).where(WorkflowRunModel.id.in_(workflow_ids))
            .order_by(WorkflowRunModel.id).with_for_update()
            .execution_options(populate_existing=True)
        )}

    planned = []
    changes = []
    recorded_at = datetime.now(UTC).isoformat()
    for row in rows:
        workflow = workflows.get(row.workflow_run_id)
        if workflow is None or workflow.project_id != project_id:
            raise ValueError("attribution_workflow_project_mismatch")
        if row.project_id not in {None, project_id}:
            raise ValueError("attribution_existing_project_conflict")
        metadata = deepcopy(row.metadata_json or {})
        execution = metadata.get("execution_receipt")
        if not isinstance(execution, Mapping) or execution.get("scope_id") != scope_id:
            raise ValueError("attribution_llm_scope_mismatch")
        row_history = _receipts(metadata, _ROW_RECEIPTS)
        current_hash = llm_attribution_row_hash(row)
        prior_row = next((r for r in prior[0].get("rows", [])
                          if r.get("row_id") == str(row.id)), None) if prior else None
        if prior:
            if (row.project_id != project_id or prior_row is None
                or prior_row.get("after_row_sha256") != current_hash
                or (prior_row.get("status") == "bound" and not any(
                    r.get("attribution_id") == idempotency_key for r in row_history))):
                raise ValueError("attribution_applied_row_changed")
        elif expected[row.id] != current_hash:
            raise ValueError("attribution_row_hash_mismatch")
        if row.project_id == project_id:
            planned.append({"row_id": str(row.id), "status": "already_bound",
                            "before_row_sha256": current_hash, "after_row_sha256": current_hash})
            continue
        note = {
            "schema": _SCHEMA, "attribution_id": idempotency_key,
            "original_project_id": None, "project_id": str(project_id),
            "workflow_run_id": str(row.workflow_run_id), "scope_id": scope_id,
            "actor": actor, "evidence_ref": evidence_ref, "recorded_at": recorded_at,
            "before_row_sha256": current_hash,
        }
        metadata[_ROW_RECEIPTS] = [*row_history, note]
        after_payload = _row_payload(row)
        after_payload.update(project_id=project_id, metadata_json=metadata)
        planned.append({
            "row_id": str(row.id), "workflow_run_id": str(row.workflow_run_id),
            "original_project_id": None, "project_id": str(project_id),
            "status": "bound" if apply else "would_bind",
            "before_row_sha256": current_hash, "after_row_sha256": _hash(after_payload),
        })
        changes.append((row, metadata))
    receipt = prior[0] if prior else {
        "schema": _SCHEMA, "idempotency_key": idempotency_key,
        "request_sha256": request_hash, "request": request,
        "actor": actor, "evidence_ref": evidence_ref, "recorded_at": recorded_at,
        "rows": planned,
    }
    if apply and changes:
        for row, metadata in changes:
            row.project_id = project_id
            row.metadata_json = metadata
        project.metadata_json = {**project_meta, _PROJECT_RECEIPTS: [*history, receipt]}
        await session.flush([project, *(row for row, _ in changes)])
    return {
        "status": ("applied" if apply else "dry_run") if changes else "already_bound",
        "apply": apply, "changed": len(changes) if apply else 0,
        "would_change": len(changes), "receipt": receipt, "rows": planned,
    }
