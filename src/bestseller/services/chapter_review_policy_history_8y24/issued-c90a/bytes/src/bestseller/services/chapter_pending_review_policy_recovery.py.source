"""Explicit policy rebind of one failed, unreturned native critic slot.

No retry, new cycle, allowance, grade, workflow reset, provider call or commit.
Validation is read-only. Apply revalidates an exact witness under locks, changes
only three runtime policy fields and appends a source-bound audit receipt.
"""

from __future__ import annotations

import ast
from collections.abc import Mapping
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
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
from bestseller.services.budget import (
    completion_authorization,
    current_execution_scope,
    execution_operation_id,
)
from bestseller.services.chapter_review_preflight import _validate_retained_review_before_processing
from bestseller.services.chapter_review_prerequisite import (
    WAITING,
    validate_waiting_record,
    wait_binding,
    waiting_history,
)
from bestseller.services.commercial_protocol_transition_runtime import _row
from bestseller.services.waiting_chapter_review_resume import _ledger
from bestseller.settings import AppSettings

SCHEMA = "pending-review-policy-source.v1"
RECEIPT_SCHEMA = "pending-review-policy-rebind.v1"
HISTORY = "policy_rebind_history"
_DENIALS = (
    "grade_reuse_allowed",
    "adoption_allowed",
    "new_credit_allowed",
    "dispatch_allowed",
    "attempt_reset_allowed",
    "new_cycle_allowed",
)


def _need(value: object, code: str) -> None:
    if not value:
        raise ValueError("chapter_pending_review_policy_" + code)


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def _sha(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _signed(value: Mapping, schema: str, field: str) -> None:
    plain = dict(value)
    recorded = plain.pop(field, None)
    _need(
        value.get("schema") == schema and _sha(recorded) and _digest(plain) == recorded,
        "receipt_digest",
    )


def _records(metadata: Mapping, cycle_id: str) -> tuple[dict, int]:
    histories = metadata.get("chapter_review_recovery_cycles")
    _need(isinstance(histories, list), "recovery_history")
    matching = [
        i
        for i, item in enumerate(histories)
        if isinstance(item, Mapping) and item.get("cycle_id") == cycle_id
    ]
    _need(len(matching) == 1, "recovery_ambiguity")
    index = matching[0]
    records = {
        "critic": metadata["chapter_critic_cycles"][cycle_id],
        "active": metadata.get("chapter_review_active_cycle"),
        "recovery": histories[index],
    }
    _need(
        isinstance(records["active"], Mapping) and records["active"] == records["recovery"],
        "active_recovery_binding",
    )
    return deepcopy(records), index


def _after(records: Mapping, receipt: Mapping) -> dict:
    changed = deepcopy(dict(records))
    for record in changed.values():
        record["policy_source_hash"] = receipt["proof"]["current_policy_source_hash"]
    changed["critic"][HISTORY] = [*(changed["critic"].get(HISTORY) or []), deepcopy(dict(receipt))]
    return changed


def _audit_chain(records: Mapping) -> Mapping:
    history = records["critic"].get(HISTORY) or []
    _need(isinstance(history, list), "audit_history")
    prior_after = None
    original = records
    for index, receipt in enumerate(history):
        _need(
            isinstance(receipt, Mapping) and isinstance(receipt.get("proof"), Mapping),
            "audit_shape",
        )
        _signed(receipt, RECEIPT_SCHEMA, "receipt_sha256")
        proof = receipt["proof"]
        _signed(proof, SCHEMA, "proof_sha256")
        _need(
            proof.get("policy_rebind_only") is True
            and all(proof.get(k) is False for k in _DENIALS),
            "audit_authority",
        )
        before = proof["before_records"]
        _need(
            (prior_after is None or before == prior_after)
            and before["critic"]["cycle_id"] == records["critic"]["cycle_id"]
            and proof["previous_policy_source_hash"] == before["critic"]["policy_source_hash"],
            "audit_chain",
        )
        if index == 0:
            original = before
        prior_after = _after(before, receipt)
    _need(prior_after is None or prior_after == records, "audit_current_records")
    return original


def _primary_versions() -> set[str | None]:
    """Read native literal versions; do not guess a future primary wire route."""
    from bestseller.services import reviews

    versions = set()
    for node in ast.walk(ast.parse(Path(reviews.__file__).read_text())):
        if (
            not isinstance(node, ast.Call)
            or not isinstance(node.func, ast.Name)
            or node.func.id != "LLMCompletionRequest"
        ):
            continue
        args = {k.arg: k.value.value for k in node.keywords if isinstance(k.value, ast.Constant)}
        if args.get("logical_role") == "critic" and args.get("prompt_template") == "chapter_review":
            _need(isinstance(args.get("prompt_version"), str), "primary_route_not_literal")
            versions.add(args["prompt_version"])
    _need(bool(versions), "primary_route_missing")
    return versions


def _operation_key(scope: str, namespace: str, input_hash: str, version: str | None) -> str:
    identity = execution_operation_id(
        namespace, subject=f"critic:chapter_review:{version}", revision=input_hash
    )
    return hashlib.sha256(f"{scope}\n{identity}:sample:0".encode()).hexdigest()


async def _primary_absence(
    session: AsyncSession,
    settings: AppSettings,
    project: ProjectModel,
    chapter: ChapterModel,
    owner: WorkflowRunModel,
    scope: str,
    cycle_id: str,
    body: str,
) -> str:
    """Inspect roots as well as telemetry: a paid return need not have an LlmRun."""
    from bestseller.services.commercial_request_purpose import prove_known_noncommercial_request

    operations = list(
        await session.scalars(
            select(ExecutionOperationModel).where(ExecutionOperationModel.scope_id == scope)
        )
    )
    attempts = list(
        await session.scalars(
            select(ExecutionAttemptModel).where(
                (ExecutionAttemptModel.scope_id == scope)
                | ExecutionAttemptModel.operation_key.in_({o.key for o in operations})
            )
        )
    )
    aliases = list(
        await session.scalars(
            select(ExecutionOperationModel).where(
                ExecutionOperationModel.status.in_(
                    ("transport_result", "transport_failure_receipt", "reconciliation_receipt")
                ),
                (
                    ExecutionOperationModel.result_json["operation_key"]
                    .as_string()
                    .in_({o.key for o in operations})
                    | ExecutionOperationModel.result_json["attempt_id"]
                    .as_string()
                    .in_({a.id for a in attempts})
                    | ExecutionOperationModel.result_json["receipt"]["attempt_id"]
                    .as_string()
                    .in_({a.id for a in attempts})
                ),
            )
        )
    )
    _need(all(o.scope_id == scope for o in aliases), "transport_scope_alias")
    operations = list({o.key: o for o in [*operations, *aliases]}.values())
    roots = {op.key: op for op in operations if op.status in {"completed", "stopped"}}
    _need(
        all(
            a.scope_id == scope
            and a.operation_key in roots
            and roots[a.operation_key].input_hash == a.input_hash
            for a in attempts
        ),
        "physical_root_binding",
    )
    runs = list(
        await session.scalars(
            select(LlmRunModel).where(
                (LlmRunModel.workflow_run_id == owner.id)
                | LlmRunModel.prompt_hash.in_({o.input_hash for o in roots.values()})
                | (
                    (LlmRunModel.project_id == project.id)
                    & (LlmRunModel.prompt_template == "chapter_review")
                )
            )
        )
    )
    versions = _primary_versions() | {
        r.prompt_version for r in runs if r.prompt_template == "chapter_review"
    }
    for op in roots.values():
        origin = ((op.result_json or {}).get("metadata") or {}).get("paid_call_origin") or {}
        if origin.get("prompt_template") == "chapter_review":
            versions.add(origin.get("prompt_version"))
    _need(all(v is None or isinstance(v, str) for v in versions), "primary_version_shape")
    namespaces = {f"chapter-critic:{cycle_id}:{i}" for i in (1, 2)}
    _need(
        not any(
            op.key == _operation_key(scope, ns, op.input_hash, version)
            for op in roots.values()
            for ns in namespaces
            for version in versions
        ),
        "primary_root_in_cycle",
    )
    body_sha = hashlib.sha256(body.encode()).hexdigest()
    for run in runs:
        metadata = run.metadata_json or {}
        origin = metadata.get("paid_call_origin") or {}
        primary = (
            run.prompt_template == "chapter_review"
            or origin.get("prompt_template") == "chapter_review"
        )
        if not primary:
            continue
        execution = metadata.get("execution_receipt") or {}
        _need(
            run.workflow_run_id != owner.id and origin.get("workflow_run_id") != str(owner.id),
            "primary_run_in_cycle",
        )
        if execution.get("scope_id") == scope:
            _need(execution.get("operation_key") in roots, "primary_orphan_run")
        if run.project_id == project.id and not execution.get("operation_key"):
            _need(
                _sha(metadata.get("primary_review_body_sha256"))
                and metadata["primary_review_body_sha256"] != body_sha,
                "primary_unattributed_run",
            )
    by_id = {a.id: a for a in attempts}
    for key, op in roots.items():
        physical = [a for a in attempts if a.operation_key == key]
        linked = [
            r
            for r in runs
            if ((r.metadata_json or {}).get("execution_receipt") or {}).get("operation_key") == key
        ]
        transports = [
            o
            for o in operations
            if o.status in {"transport_result", "transport_failure_receipt"}
            and (o.result_json or {}).get("operation_key") == key
        ]
        routes = []
        results = [op.result_json] if isinstance(op.result_json, Mapping) else []
        for transport in transports:
            t = transport.result_json
            attempt = by_id.get(t.get("attempt_id"))
            schema = (
                "transport-result.v1"
                if transport.status == "transport_result"
                else "transport-failure.v1"
            )
            _need(
                attempt is not None
                and attempt.operation_key == key
                and transport.input_hash == op.input_hash
                and t.get("schema") == schema
                and transport.key
                == hashlib.sha256(f"{schema}\n{scope}\n{attempt.id}".encode()).hexdigest(),
                "transport_origin",
            )
            if transport.status == "transport_result":
                results.append(t.get("result"))
            else:
                routes.append(
                    (t.get("logical_role"), t.get("prompt_template"), t.get("workflow_run_id"))
                )
        for result in results:
            _need(isinstance(result, Mapping), "root_result_shape")
            metadata = result.get("metadata") or {}
            origin = metadata.get("paid_call_origin") or {}
            if origin.get("status") != "recorded":
                continue
            execution = metadata.get("execution_receipt") or {}
            _need(
                origin.get("schema") == "paid-call-origin.v1"
                and origin.get("prompt_hash") == op.input_hash
                and execution.get("scope_id") == scope
                and execution.get("operation_key") == key,
                "immutable_origin_binding",
            )
            routes.append(
                (
                    origin.get("logical_role"),
                    origin.get("prompt_template"),
                    origin.get("workflow_run_id"),
                )
            )
        if not routes:
            _need(
                prove_known_noncommercial_request(
                    operation=_row(op),
                    actual_attempts=[_row(a) for a in physical],
                    original_runs=[_row(r) for r in linked],
                    transport_results=[
                        _row(o) for o in transports if o.status == "transport_result"
                    ],
                    failure_receipts=[
                        _row(o) for o in transports if o.status == "transport_failure_receipt"
                    ],
                ),
                "root_purpose_ambiguous",
            )
        elif any(v is None for route in routes for v in route[:2]):
            from bestseller.services.legacy_request_purpose import (
                prove_unlabelled_beatsheet_request,
            )

            # NULL attribution alone proves nothing. Reconstruct the complete
            # original request and verify every retained send/return witness.
            _need(
                all(t.status == "transport_result" for t in transports),
                "root_purpose_conflict",
            )
            legacy = await prove_unlabelled_beatsheet_request(
                session,
                settings,
                operation=op,
                actual_attempts=physical,
                original_runs=linked,
                transport_results=transports,
            )
            _need(legacy is not None, "root_purpose_conflict")
        else:
            _need(
                len({r[:2] for r in routes}) == 1
                and all(isinstance(v, str) and v for r in routes for v in r[:2]),
                "root_purpose_conflict",
            )
            if routes[0][:2] == ("critic", "chapter_review"):
                _need(
                    all(r[2] and r[2] != str(owner.id) for r in routes), "primary_origin_ambiguous"
                )
    return _digest(
        {
            "operations": [_row(o) for o in sorted(operations, key=lambda r: r.key)],
            "attempts": [_row(a) for a in sorted(attempts, key=lambda r: r.id)],
            "runs": [_row(r) for r in sorted(runs, key=lambda r: str(r.id))],
        }
    )


async def _prior_reports(
    session: AsyncSession,
    chapter: ChapterModel,
    draft: ChapterDraftVersionModel,
    owner: WorkflowRunModel,
    current: Mapping,
    semantic: str,
) -> str:
    from bestseller.services import pipelines

    fingerprint = pipelines.chapter_review_body_fingerprint(draft.content_md)
    report_ids, quality_ids = set(), set()
    for stored_key, cycle in chapter.metadata_json["chapter_critic_cycles"].items():
        if not isinstance(cycle, Mapping) or cycle.get("source_body_hash") != fingerprint["digest"]:
            continue
        _need(
            cycle.get("cycle_id") == stored_key
            and cycle.get("schema_version") == "chapter-critic-cycle.v1"
            and _sha(cycle.get("policy_source_hash")),
            "prior_cycle_identity",
        )
        _need(cycle.get("source_draft_id") == str(draft.id), "other_draft_cycle")
        is_current = cycle.get("cycle_id") == current["cycle_id"]
        _need(is_current or cycle.get("state") == WAITING, "other_new_cycle")
        _need(
            cycle.get("semantic_dependencies_sha256") == semantic
            and not cycle.get("semantic_dependency_change_detected"),
            "prior_semantic",
        )
        prior_owner = await session.get(WorkflowRunModel, UUID(cycle["review_workflow_run_id"]))
        _need(
            prior_owner is not None
            and prior_owner.project_id == draft.project_id
            and prior_owner.scope_id == chapter.id
            and prior_owner.scope_type == "chapter"
            and prior_owner.workflow_type == "chapter_pipeline"
            and (
                (prior_owner.metadata_json or {}).get("critic_cycle_id") == cycle["cycle_id"]
                or (
                    is_current
                    and prior_owner.id == owner.id
                    and "critic_cycle_id" not in (prior_owner.metadata_json or {})
                )
            ),
            "prior_owner",
        )
        entries = cycle.get("attempts")
        _need(isinstance(entries, list) and 1 <= len(entries) <= 2, "prior_attempts")
        for index, entry in enumerate(entries, 1):
            _need(
                entry.get("attempt") == index
                and entry.get("operation_namespace")
                == f"chapter-critic:{cycle['cycle_id']}:{index}",
                "prior_namespace",
            )
            if is_current and index == len(entries):
                continue
            if entry.get("state") == WAITING:
                records = waiting_history(cycle, entry)
            else:
                _need(
                    entry.get("state") == "completed" and entry.get("outcome") == "unknown",
                    "prior_unknown_only",
                )
                records = [entry]
            for record in records:
                result, report, quality, task = await pipelines._retained_chapter_critic_result(
                    session, draft, record
                )
                _need(
                    result.verdict == report.verdict == "unknown"
                    and task is None
                    and report.llm_run_id is None,
                    "prior_grade_available",
                )
                if entry.get("state") == WAITING:
                    validate_waiting_record(
                        report,
                        record=record,
                        binding=wait_binding(
                            cycle,
                            entry,
                            project_id=draft.project_id,
                            chapter_id=chapter.id,
                            body_fingerprint=fingerprint,
                        ),
                    )
                _need(
                    str(report.id) not in report_ids and str(quality.id) not in quality_ids,
                    "duplicate_prior_result",
                )
                report_ids.add(str(report.id))
                quality_ids.add(str(quality.id))
    reports = list(
        await session.scalars(
            select(ReviewReportModel).where(
                ReviewReportModel.project_id == draft.project_id,
                ReviewReportModel.target_type == "chapter",
                ReviewReportModel.target_id == chapter.id,
            )
        )
    )
    reports = [
        r
        for r in reports
        if (r.structured_output or {}).get("draft_id") == str(draft.id)
        or (r.structured_output or {}).get("reviewed_body_fingerprint") == fingerprint
    ]
    qualities = list(
        await session.scalars(
            select(QualityScoreModel).where(
                QualityScoreModel.project_id == draft.project_id,
                QualityScoreModel.target_type == "chapter",
                QualityScoreModel.target_id == chapter.id,
                QualityScoreModel.chapter_draft_version_id == draft.id,
            )
        )
    )
    _need(
        {str(r.id) for r in reports} == report_ids
        and {str(q.id) for q in qualities} == quality_ids,
        "report_quality_inventory",
    )
    _need(
        not report_ids
        or (
            chapter.metadata_json.get("chapter_review_evidence_status") == "unknown"
            and chapter.metadata_json.get("chapter_review_evidence_report_id") in report_ids
        ),
        "latest_prior_unknown",
    )
    return _digest(
        {
            "reports": [_row(r) for r in sorted(reports, key=lambda r: str(r.id))],
            "quality": [_row(q) for q in sorted(qualities, key=lambda q: str(q.id))],
        }
    )


async def validate_pending_review_policy_recovery(
    session: AsyncSession,
    *,
    settings: AppSettings,
    project: ProjectModel,
    chapter: ChapterModel,
    draft: ChapterDraftVersionModel,
    previous: WorkflowRunModel,
    execution_scope_id: str,
    current_policy_hash: str,
) -> dict:
    """Prove a same-cycle policy rebind only; no public normal-policy override."""
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
        raise ValueError("chapter_pending_review_policy_invalid_shape") from None


async def _validate(
    session: AsyncSession,
    settings: AppSettings,
    project: ProjectModel,
    chapter: ChapterModel,
    draft: ChapterDraftVersionModel,
    previous: WorkflowRunModel,
    scope: str,
    policy: str,
    replay: Mapping | None = None,
) -> dict:
    from bestseller.services import pipelines
    from bestseller.services.feedback import validate_adopted_knowledge_source

    _need(not session.new and not session.dirty and not session.deleted, "uncommitted_source")
    _need(
        _sha(policy) and policy == pipelines.chapter_review_recovery_source_hash(),
        "actual_current_policy",
    )
    _need(
        isinstance(scope, str)
        and 0 < len(scope) <= 256
        and current_execution_scope() in {None, scope},
        "scope",
    )
    _need(
        chapter.project_id == draft.project_id == previous.project_id == project.id
        and draft.chapter_id == previous.scope_id == chapter.id
        and previous.scope_type == "chapter"
        and previous.workflow_type == "chapter_pipeline"
        and previous.status == "failed"
        and previous.error_message == "workflow_failed"
        and isinstance(previous.current_step, str)
        and re.fullmatch(r"review_chapter(?:_v[1-9][0-9]*)?", previous.current_step),
        "failed_review_origin",
    )
    for model, row, fields in (
        (ProjectModel, project, ("metadata_json",)),
        (ChapterModel, chapter, ("project_id", "metadata_json")),
        (
            ChapterDraftVersionModel,
            draft,
            (
                "project_id",
                "chapter_id",
                "content_md",
                "is_current",
                "promotion_state",
                "promoted_at",
            ),
        ),
        (
            WorkflowRunModel,
            previous,
            (
                "project_id",
                "scope_id",
                "scope_type",
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
                select(*(getattr(model, f) for f in fields)).where(model.id == row.id)
            )
        ).one_or_none()
        _need(
            stored is not None and all(getattr(stored, f) == getattr(row, f) for f in fields),
            "stored_source",
        )
    cm, wm = deepcopy(chapter.metadata_json or {}), previous.metadata_json or {}
    _need(
        (project.metadata_json or {}).get("execution_scope_id")
        == wm.get("execution_scope_id")
        == scope,
        "stored_scope",
    )
    failures = wm.get("execution_failure_receipts")
    _need(isinstance(failures, list) and failures, "durable_failure")
    failure = failures[-1]
    _need(
        isinstance(failure, Mapping)
        and failure.get("workflow_id") == str(previous.id)
        and failure.get("project_id") == str(project.id)
        and failure.get("workflow_type") == "chapter_pipeline"
        and failure.get("step") == previous.current_step
        and failure.get("code") == "workflow_failed"
        and failure.get("recoverable") is True
        and failure.get("error_type") == "ValueError",
        "durable_failure",
    )
    _need(
        draft.is_current
        and isinstance(draft.content_md, str)
        and draft.content_md.strip()
        and draft.promotion_state != "promoted"
        and draft.promoted_at is None,
        "current_unpromoted_body",
    )
    fingerprint = pipelines.chapter_review_body_fingerprint(draft.content_md)
    _need(
        wm.get("review_recovery_source")
        == {"draft_id": str(draft.id), "body_hash": fingerprint["digest"]},
        "workflow_source_binding",
    )
    active = cm.get("chapter_review_active_cycle") or {}
    cycle_id = active.get("cycle_id")
    _need(_sha(cycle_id), "active_cycle")
    records, index = _records(cm, cycle_id)
    original = _audit_chain(records)
    native_source = await session.get(
        WorkflowRunModel, UUID(original["recovery"]["previous_workflow_run_id"])
    )
    prepared = deepcopy(original["recovery"])
    prepared["state"] = "prepared"
    prepared.pop("result_workflow_run_id", None)
    _need(
        native_source is not None
        and native_source.project_id == project.id
        and native_source.scope_type == "chapter"
        and native_source.scope_id == chapter.id
        and native_source.workflow_type == "chapter_pipeline"
        and (native_source.metadata_json or {}).get("review_recovery_policy") == prepared,
        "native_prepared_receipt",
    )
    cycle, recovery = records["critic"], records["recovery"]
    # The native producer writes this metadata key only after a report returns.
    # The current unreturned slot is bound below; historical owners remain strict.
    _need(
        cycle.get("schema_version") == "chapter-critic-cycle.v1"
        and cycle.get("cycle_id") == cycle_id
        and cycle.get("review_workflow_run_id") == str(previous.id)
        and ("critic_cycle_id" not in wm or wm["critic_cycle_id"] == cycle_id)
        and cycle.get("source_draft_id") == recovery.get("source_draft_id") == str(draft.id)
        and cycle.get("source_body_hash")
        == recovery.get("source_body_hash")
        == fingerprint["digest"]
        and cycle.get("state") in {"prepared", "running"}
        and recovery.get("state") in {"prepared", "running"}
        and recovery.get("execution_scope_id") == scope
        and recovery.get("result_workflow_run_id") in {None, str(previous.id)}
        and cycle.get("policy_source_hash") == recovery.get("policy_source_hash")
        and type(recovery.get("attempts_base")) is int
        and recovery["attempts_base"] >= 0,
        "pending_cycle_binding",
    )
    _need(
        not any(
            isinstance(item, Mapping)
            and item.get("cycle_id") != cycle_id
            and item.get("source_body_hash") == fingerprint["digest"]
            and item.get("state") in {"prepared", "running"}
            for item in cm["chapter_review_recovery_cycles"]
        ),
        "other_pending_recovery",
    )
    old = cycle["policy_source_hash"]
    _need(_sha(old) and (old != policy or replay is not None), "policy_already_current")
    _need(
        cycle_id
        == pipelines._chapter_critic_cycle_id(
            project.id, chapter.id, fingerprint["digest"], original["critic"]["policy_source_hash"]
        ),
        "native_cycle_identity",
    )
    attempts = cycle.get("attempts")
    _need(isinstance(attempts, list) and 1 <= len(attempts) <= 2, "pending_attempts")
    pending = attempts[-1]
    _need(
        type(pending.get("attempt")) is int
        and pending["attempt"] == len(attempts)
        and pending.get("state") in {"prepared", "running"}
        and pending.get("operation_namespace") == f"chapter-critic:{cycle_id}:{len(attempts)}"
        and not any(
            k in pending
            for k in (
                "outcome",
                "review_result",
                "review_report_id",
                "quality_score_id",
                "rewrite_task_id",
                "received_report_id",
                "infrastructure_history",
            )
        ),
        "unreturned_slot",
    )
    core = await _validate_retained_review_before_processing(
        session, settings, chapter, expected_policy_hash=old
    )
    _need(
        core is not None
        and core.get("reuse_kind") == "resume_pending_review"
        and core.get("cycle_id") == cycle_id
        and core.get("draft_id") == str(draft.id)
        and core.get("body_sha256") == fingerprint["digest"],
        "pending_preflight",
    )
    report_hash = await _prior_reports(
        session, chapter, draft, previous, cycle, core["semantic_dependencies_sha256"]
    )
    _need(
        await session.scalar(
            select(WorkflowRunModel.id)
            .where(
                WorkflowRunModel.project_id == project.id,
                WorkflowRunModel.status.in_(("pending", "queued", "running")),
            )
            .limit(1)
        )
        is None,
        "active_workflow",
    )
    control = await session.get(BookProductionControlModel, project.id)
    _need(
        control is not None
        and control.desired_state == "run"
        and type(control.command_serial) is int
        and control.command_serial > 0,
        "explicit_run",
    )
    ledger = await _ledger(session, scope, lock=False)
    budget = await session.get(ExecutionBudgetModel, scope)
    auth = completion_authorization(budget) if budget is not None else None
    _need(
        budget is not None
        and auth is not None
        and auth.get("status") == "active"
        and budget.status in {"active", "exhausted"},
        "completion_authorization",
    )
    financial_hash = await _primary_absence(
        session, settings, project, chapter, previous, scope, cycle_id, draft.content_md
    )
    _, rejection = await validate_adopted_knowledge_source(
        session,
        project_id=project.id,
        chapter_id=chapter.id,
        source_chapter_draft_version_id=draft.id,
        chapter_md=draft.content_md,
        lock=False,
    )
    _need(rejection == "source_not_adopted", "source_adopted_or_ambiguous")
    if replay is not None:
        _need(
            old == policy and cycle.get(HISTORY) and cycle[HISTORY][-1] == replay, "replay_binding"
        )
        before = replay["proof"]["before_records"]
        _need(_after(before, replay) == records, "replay_changed_records")
        records = deepcopy(before)
        old = records["critic"]["policy_source_hash"]
        cm["chapter_critic_cycles"][cycle_id] = records["critic"]
        cm["chapter_review_active_cycle"] = records["active"]
        cm["chapter_review_recovery_cycles"][index] = records["recovery"]
    proof = {
        "schema": SCHEMA,
        "service_source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "status": "verified_pending_review_policy_source",
        "policy_rebind_only": True,
        "project_id": str(project.id),
        "chapter_id": str(chapter.id),
        "draft_id": str(draft.id),
        "previous_workflow_run_id": str(previous.id),
        "execution_scope_id": scope,
        "source_body_fingerprint": fingerprint,
        "cycle_id": cycle_id,
        "operation_namespace": pending["operation_namespace"],
        "attempt": pending["attempt"],
        "previous_policy_source_hash": old,
        "current_policy_source_hash": policy,
        "semantic_dependencies_sha256": core["semantic_dependencies_sha256"],
        "chapter_metadata_sha256": _digest(cm),
        "owner_metadata_sha256": _digest(wm),
        "native_source_metadata_sha256": _digest(native_source.metadata_json or {}),
        "before_records": records,
        "failure_receipt": deepcopy(failure),
        "prior_reports_sha256": report_hash,
        "ledger_sha256": _digest(ledger),
        "primary_absence_sha256": financial_hash,
        "control_command_serial": control.command_serial,
        "authorization_sha256": _digest(auth),
        "budget_usage": {"calls_used": budget.calls_used, "tokens_charged": budget.tokens_charged},
        **dict.fromkeys(_DENIALS, False),
    }
    proof["proof_sha256"] = _digest(proof)
    return proof


async def apply_pending_review_policy_recovery(
    session: AsyncSession,
    *,
    settings: AppSettings,
    project: ProjectModel,
    chapter: ChapterModel,
    draft: ChapterDraftVersionModel,
    previous: WorkflowRunModel,
    execution_scope_id: str,
    current_policy_hash: str,
    witness: Mapping,
    actor: str,
    reason: str,
) -> dict:
    """Lock/revalidate, flush one bound rebind, leave transaction commit to caller."""
    _need(
        isinstance(actor, str)
        and 0 < len(actor.strip()) <= 256
        and isinstance(reason, str)
        and 0 < len(reason.strip()) <= 2048,
        "actor_reason",
    )
    _need(not session.new and not session.dirty and not session.deleted, "uncommitted_source")
    _signed(witness, SCHEMA, "proof_sha256")
    for model, identity in (
        (ProjectModel, project.id),
        (ChapterModel, chapter.id),
        (ChapterDraftVersionModel, draft.id),
        (WorkflowRunModel, previous.id),
    ):
        await session.scalar(
            select(model)
            .where(model.id == identity)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    await session.scalar(
        select(ExecutionBudgetModel)
        .where(ExecutionBudgetModel.scope_id == execution_scope_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    await session.scalar(
        select(BookProductionControlModel)
        .where(BookProductionControlModel.project_id == project.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    cycle_id = witness["cycle_id"]
    stored = ((chapter.metadata_json or {}).get("chapter_critic_cycles") or {}).get(cycle_id) or {}
    history = stored.get(HISTORY) or []
    replay = (
        history[-1]
        if history and history[-1].get("proof", {}).get("proof_sha256") == witness["proof_sha256"]
        else None
    )
    with session.no_autoflush:
        fresh = await _validate(
            session,
            settings,
            project,
            chapter,
            draft,
            previous,
            execution_scope_id,
            current_policy_hash,
            replay=replay,
        )
    _need(fresh == witness, "stale_witness")
    from bestseller.services.pipelines import chapter_review_recovery_source_hash

    _need(current_policy_hash == chapter_review_recovery_source_hash(), "actual_current_policy")
    if replay is not None:
        _need(
            replay.get("actor") == actor.strip() and replay.get("reason") == reason.strip(),
            "replay_actor_reason",
        )
        return deepcopy(replay)
    receipt = {
        "schema": RECEIPT_SCHEMA,
        "actor": actor.strip(),
        "reason": reason.strip(),
        "proof": fresh,
        "model_calls": 0,
        "new_attempt_credit": False,
        "grade_granted": False,
        "dispatch_authorized": False,
    }
    receipt["receipt_sha256"] = _digest(receipt)
    metadata = deepcopy(chapter.metadata_json)
    _, index = _records(metadata, cycle_id)
    changed = _after(fresh["before_records"], receipt)
    metadata["chapter_critic_cycles"][cycle_id] = changed["critic"]
    metadata["chapter_review_active_cycle"] = changed["active"]
    metadata["chapter_review_recovery_cycles"][index] = changed["recovery"]
    chapter.metadata_json = metadata
    await session.flush()
    return receipt
