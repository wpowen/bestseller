"""Check an anticipated retained critic cycle before pre-review processing."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import hashlib
import json
import re
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import select

from bestseller.infra.db.models import ChapterDraftVersionModel, WorkflowRunModel

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from bestseller.infra.db.models import ChapterModel, ProjectModel
    from bestseller.settings import AppSettings


async def validate_retained_review_before_processing(
    session: AsyncSession,
    settings: AppSettings,
    chapter: ChapterModel,
) -> dict | None:
    """Normal reuse always requires the actual current policy; no override API."""
    from bestseller.services import pipelines

    current_policy = pipelines.chapter_review_recovery_source_hash()
    proof = await _validate_retained_review_before_processing(
        session,
        settings,
        chapter,
        expected_policy_hash=current_policy,
    )
    if proof is None:
        return None
    from bestseller.services import review_issuer_view

    review_issuer_view._require_equal(
        "/preflight/current_runtime_policy_sha256", current_policy,
        pipelines.chapter_review_recovery_source_hash(), code="runtime_policy_changed",
    )
    return {
        **proof,
        "issued_runtime_policy_sha256": proof["policy_source_hash"],
        "current_runtime_policy_sha256": current_policy,
    }


async def validate_retained_review_candidate(
    session, settings, *, project_id, chapter_id, draft_id, cycle_id
) -> dict | None:
    """Validate an explicit native candidate with the common retained guards.

    Selecting a draft does not change the current pointer. No source, issuer
    proof, expected policy, grade or permission may be supplied by the caller.
    """
    from bestseller.infra.db import models as db
    from bestseller.services import pipelines, recorded_review_sources, review_issuer_view

    ids = review_issuer_view._ids(project_id, chapter_id, draft_id, cycle_id)
    reader = recorded_review_sources._Reader(session)
    chapter = await reader.get(db.ChapterModel, chapter_id)
    if chapter is None:
        review_issuer_view._reject(
            "native_scope_changed", "/candidate/chapter",
            {"type": "native row", "length": None, "sha256": None}, None,
        )
    review_issuer_view._require_equal(
        "/candidate/chapter/project_id", project_id, chapter.project_id,
        code="native_scope_changed",
    )
    candidate_metadata = chapter.metadata_json or {}
    review_issuer_view._require_type(
        "/candidate/chapter/metadata", candidate_metadata, dict, code="native_cycle_changed",
    )
    candidate_cycles = candidate_metadata.get("chapter_critic_cycles", review_issuer_view._MISSING)
    review_issuer_view._require_type(
        "/candidate/cycles", candidate_cycles, dict, code="native_cycle_changed",
    )
    candidate_cycle = deepcopy(candidate_cycles.get(cycle_id, review_issuer_view._MISSING))
    review_issuer_view._require_type(
        "/candidate/cycle", candidate_cycle, dict, code="native_cycle_changed",
    )
    current_policy = pipelines.chapter_review_recovery_source_hash()
    proof = await _validate_retained_review_before_processing(
        session, settings, chapter, expected_policy_hash=current_policy, _selected_ids=ids,
    )
    if proof is None:
        return None
    review_issuer_view._require_equal(
        "/candidate/reuse_kind", "available_review", proof["reuse_kind"],
        code="candidate_review_unavailable",
    )
    for key, value in ids.items():
        review_issuer_view._require_equal(
            "/candidate/" + key, str(value), proof[key], code="native_source_changed",
        )
    review_issuer_view._require_equal(
        "/candidate/current_runtime_policy_sha256", current_policy,
        pipelines.chapter_review_recovery_source_hash(), code="runtime_policy_changed",
    )
    latest = await reader.get(db.ChapterModel, chapter_id)
    review_issuer_view._require_equal(
        "/candidate/cycle", candidate_cycle,
        (latest.metadata_json or {}).get("chapter_critic_cycles", {}).get(cycle_id)
        if latest is not None else None,
        code="native_cycle_changed",
    )
    return {
        **proof,
        "native_cycle": candidate_cycle,
        "issued_runtime_policy_sha256": proof["policy_source_hash"],
        "current_runtime_policy_sha256": current_policy,
        "selection_only": True,
        "grade_granted": False, "new_credit_allowed": False,
        "dispatch_authorized": False, "adoption_authorized": False,
    }


async def _validate_retained_review_before_processing(
    session: AsyncSession,
    settings: AppSettings,
    chapter: ChapterModel,
    *,
    expected_policy_hash: str,
    _selected_ids: Mapping | None = None,
) -> dict | None:
    """Validate existing source/policy fences without consuming or creating a cycle.

    A returned proof authorizes only pre-review processing deferral on this
    exact current automatic or matching active recovery cycle; it never
    authorizes adoption or new credit.
    State, outcomes, attempts, scores and adoption remain the durable review
    path's responsibility. Check the canonical current-body cycle even when a
    recovery pointer still refers to an older body. A new body with no cycle
    has no anticipated reuse here. Caller owns locking and transaction completion.
    """
    metadata = chapter.metadata_json or {}
    active = metadata.get("chapter_review_active_cycle")
    cycles = metadata.get("chapter_critic_cycles")
    if (active is not None and not isinstance(active, Mapping)) or (
        cycles is not None and not isinstance(cycles, Mapping)
    ):
        raise ValueError("chapter_critic_cycle_source_mismatch")
    if not active and not cycles:
        return None
    if active and (
        not isinstance(active, Mapping)
        or not isinstance(active.get("cycle_id"), str)
        or not active["cycle_id"]
        or not isinstance(active.get("source_body_hash"), str)
        or len(active["source_body_hash"]) != 64
        or any(c not in "0123456789abcdef" for c in active["source_body_hash"])
    ):
        raise ValueError("chapter_critic_cycle_source_mismatch")
    if any(
        isinstance(row, ChapterDraftVersionModel)
        and row.project_id == chapter.project_id
        and row.chapter_id == chapter.id
        for row in session.dirty
    ):
        raise ValueError("chapter_critic_cycle_uncommitted_source")
    with session.no_autoflush:
        current_rows = list(
            await session.scalars(
                select(ChapterDraftVersionModel)
                .where(
                    ChapterDraftVersionModel.project_id == chapter.project_id,
                    ChapterDraftVersionModel.chapter_id == chapter.id,
                    ChapterDraftVersionModel.is_current.is_(True),
                )
                .execution_options(populate_existing=True)
            )
        )
    if len(current_rows) > 1:
        raise ValueError("chapter_critic_cycle_source_mismatch")
    current = current_rows[0] if current_rows else None
    if _selected_ids is not None:
        from bestseller.services import recorded_review_sources, review_issuer_view

        current = await recorded_review_sources._Reader(session).get(
            ChapterDraftVersionModel, _selected_ids["draft_id"],
        )
        if current is None:
            review_issuer_view._reject(
                "native_scope_changed", "/candidate/draft",
                {"type": "native row", "length": None, "sha256": None}, None,
            )
        for key, expected, actual in (
            ("project_id", chapter.project_id, current.project_id),
            ("chapter_id", chapter.id, current.chapter_id),
        ):
            review_issuer_view._require_equal(
                "/candidate/draft/" + key, expected, actual, code="native_scope_changed",
            )
    if current is None:
        return
    from bestseller.services import pipelines
    from bestseller.services.chapter_review_prerequisite import (
        WAITING,
        validate_waiting_record,
        wait_binding,
        waiting_history,
    )

    body_hash = pipelines.chapter_review_body_fingerprint(current.content_md or "")["digest"]
    matching_active = bool(
        _selected_ids is None and active and active["source_body_hash"] == body_hash
    )
    if matching_active and active.get("f0_supersession_receipt_sha256"):
        from bestseller.services.review_source_ordering import read_fresh_review_preflight
        fresh = await read_fresh_review_preflight(session, settings, chapter=chapter, draft=current)
        if fresh is not None:
            return fresh
    automatic_cycle_id = pipelines._chapter_critic_cycle_id(
        chapter.project_id,
        chapter.id,
        body_hash,
    )
    cycle_id = active["cycle_id"] if matching_active else automatic_cycle_id
    if _selected_ids is not None:
        cycle_id = _selected_ids["cycle_id"]
    cycle = cycles.get(cycle_id) if isinstance(cycles, Mapping) else None
    if not matching_active and cycle is None and _selected_ids is None:
        return
    if (
        not isinstance(cycle, Mapping)
        or cycle.get("cycle_id") != cycle_id
        or (matching_active and active.get("source_draft_id") != str(current.id))
        or cycle.get("source_draft_id") != str(current.id)
        or cycle.get("source_body_hash") != body_hash
    ):
        raise ValueError("chapter_critic_cycle_source_mismatch")
    issuer_policy = None
    issuer_inputs = None
    issuer_report = None
    # Only the actual current-policy route may authenticate a new native
    # issuer. Historical private overrides retain their fixed-byte guards.
    attempts_for_issuer = cycle.get("attempts")
    if (
        expected_policy_hash == pipelines.chapter_review_recovery_source_hash()
        and cycle.get("state") == "completed"
        and isinstance(attempts_for_issuer, list)
        and attempts_for_issuer
        and isinstance(attempts_for_issuer[-1], Mapping)
        and attempts_for_issuer[-1].get("state") == "completed"
        and attempts_for_issuer[-1].get("outcome") == "available"
    ):
        from bestseller.infra.db import models as db
        from bestseller.services import recorded_review_sources, retained_review_input_proof
        from bestseller.services import review_issuer_view

        issuer_ids = {
            "project_id": chapter.project_id, "chapter_id": chapter.id,
            "draft_id": current.id, "cycle_id": cycle_id,
        }
        issuer_policy = await review_issuer_view.require_cycle_policy(
            session, settings, **issuer_ids,
        )
        if issuer_policy is not None:
            reader = recorded_review_sources._Reader(session)
            native_chapter = await reader.get(db.ChapterModel, chapter.id)
            if native_chapter is None:
                review_issuer_view._reject(
                    "native_scope_changed", "/preflight/chapter",
                    {"type": "native row", "length": None, "sha256": None}, None,
                )
            native_cycle = (native_chapter.metadata_json or {}).get(
                "chapter_critic_cycles", {}
            ).get(cycle_id)
            issuer_active = deepcopy(active)
            review_issuer_view._require_equal(
                "/preflight/active_cycle", issuer_active,
                (native_chapter.metadata_json or {}).get("chapter_review_active_cycle"),
                code="native_cycle_changed",
            )
            review_issuer_view._require_equal(
                "/preflight/cycle", cycle, native_cycle, code="native_cycle_changed",
            )
            cycle = deepcopy(native_cycle)
            native_project = await reader.get(db.ProjectModel, chapter.project_id)
            native_report = await reader.get(
                db.ReviewReportModel, UUID(issuer_policy["binding"]["report_id"]),
            )
            for label, native_row in (("project", native_project), ("report", native_report)):
                if native_row is None:
                    review_issuer_view._reject(
                        "native_scope_changed", "/preflight/" + label,
                        {"type": "native row", "length": None, "sha256": None}, None,
                    )
            issuer_report = deepcopy(vars(native_report))
            issuer_report["created_at"] = native_report.created_at.isoformat()
            issuer_inputs = await retained_review_input_proof.verify_retained_review_inputs(
                session, settings, project=native_project, chapter=native_chapter,
                draft=current, report=native_report, cycle=cycle,
            )
    if cycle.get("policy_source_hash") != expected_policy_hash and issuer_policy is None:
        raise ValueError("chapter_critic_policy_changed_requires_explicit_recovery")
    if cycle.get("native_runtime_policy_alias") is not None:
        from bestseller.services.chapter_review_reconciliation import validate_native_policy_alias
        validate_native_policy_alias(cycle, project_id=chapter.project_id,
            chapter_id=chapter.id, draft_id=current.id, body_sha256=body_hash)
    with session.no_autoflush:
        semantic = await pipelines._chapter_critic_semantic_dependencies(session, settings, chapter)
    if cycle.get("semantic_dependencies_sha256") != semantic or cycle.get(
        "semantic_dependency_change_detected"
    ):
        raise ValueError("chapter_critic_semantic_dependencies_changed")
    state = cycle.get("state")
    if state not in {"prepared", "running", "completed", WAITING}:
        raise ValueError("chapter_critic_cycle_state_invalid")
    attempts = cycle.get("attempts")
    if (
        not isinstance(attempts, list)
        or len(attempts) > 2
        or any(not isinstance(item, Mapping) for item in attempts)
    ):
        raise ValueError("chapter_critic_cycle_attempts_invalid")
    try:
        owner_id = UUID(cycle["review_workflow_run_id"])
    except (KeyError, TypeError, AttributeError, ValueError) as exc:
        raise ValueError("chapter_critic_cycle_workflow_binding_mismatch") from exc
    with session.no_autoflush:
        owner = await session.get(WorkflowRunModel, owner_id)
    if (
        owner is None
        or owner.project_id != chapter.project_id
        or owner.scope_type != "chapter"
        or owner.scope_id != chapter.id
        or owner.workflow_type != pipelines.WORKFLOW_TYPE_CHAPTER_PIPELINE
    ):
        raise ValueError("chapter_critic_cycle_workflow_binding_mismatch")
    fingerprint = pipelines.chapter_review_body_fingerprint(current.content_md or "")
    retained = None
    for index, item in enumerate(attempts, 1):
        if (
            type(item.get("attempt")) is not int
            or item["attempt"] != index
            or item.get("operation_namespace") != f"chapter-critic:{cycle_id}:{index}"
            or ("cycle_id" in item and item["cycle_id"] != cycle_id)
        ):
            raise ValueError("chapter_critic_cycle_attempt_binding_mismatch")
        if ("draft_id" in item and item["draft_id"] != str(current.id)) or (
            "body_fingerprint" in item and item["body_fingerprint"] != fingerprint
        ):
            raise ValueError("chapter_critic_cycle_attempt_binding_mismatch")
        item_state = item.get("state")
        if item_state == WAITING:
            if index != len(attempts):
                raise ValueError("chapter_critic_waiting_receipt_invalid")
            binding = wait_binding(
                cycle,
                item,
                project_id=chapter.project_id,
                chapter_id=chapter.id,
                body_fingerprint=fingerprint,
            )
            for record in waiting_history(cycle, item):
                with session.no_autoflush:
                    retained = await pipelines._retained_chapter_critic_result(
                        session, current, record
                    )
                result, report, quality, task = retained
                historical_binding = binding
                if cycle.get("native_runtime_policy_alias") is not None:
                    from bestseller.services.chapter_review_reconciliation import native_waiting_record_binding
                    historical_binding = native_waiting_record_binding(cycle, item, record, binding)
                validate_waiting_record(report, record=record, binding=historical_binding)
                if result.verdict != "unknown" or task is not None:
                    raise ValueError("chapter_critic_waiting_receipt_invalid")
            continue
        if item_state in {"prepared", "running"}:
            if (
                state == "completed"
                or index != len(attempts)
                or any(
                    key in item
                    for key in (
                        "outcome",
                        "review_result",
                        "review_report_id",
                        "quality_score_id",
                        "rewrite_task_id",
                    )
                )
            ):
                raise ValueError("chapter_critic_cycle_attempts_invalid")
            continue
        if item_state != "completed":
            raise ValueError("chapter_critic_cycle_attempts_invalid")
        if item.get("draft_id") != str(current.id) or item.get("body_fingerprint") != fingerprint:
            raise ValueError("chapter_critic_cycle_attempt_binding_mismatch")
        outcome = item.get("outcome")
        if outcome not in {"unknown", "available"}:
            raise ValueError("chapter_critic_cycle_attempts_invalid")
        if outcome == "available" and (state != "completed" or index != len(attempts)):
            raise ValueError("chapter_critic_cycle_attempts_invalid")
        try:
            with session.no_autoflush:
                retained = await pipelines._retained_chapter_critic_result(session, current, item)
        except (KeyError, TypeError, AttributeError) as exc:
            raise ValueError("chapter_critic_cycle_result_binding_mismatch") from exc
        result, report, quality, task = retained
        if outcome == "unknown":
            if result.verdict != "unknown" or task is not None or item.get("rewrite_task_id"):
                raise ValueError("chapter_critic_cycle_result_binding_mismatch")
        elif (
            result.verdict == "unknown"
            or pipelines._chapter_critic_evidence_unknown(report)
            or report.verdict != result.verdict
        ):
            raise ValueError("chapter_critic_cycle_result_binding_mismatch")
    proof = {
        "schema": "chapter-review-first-reuse.v1",
        "project_id": str(chapter.project_id),
        "chapter_id": str(chapter.id),
        "draft_id": str(current.id),
        "body_sha256": body_hash,
        "cycle_id": cycle_id,
        "policy_source_hash": cycle["policy_source_hash"],
        "semantic_dependencies_sha256": semantic,
        "report_id": None,
        "quality_score_id": None,
        "task_id": None,
        "verdict": None,
        "reuse_kind": "resume_pending_review",
    }
    if state == WAITING:
        if not attempts or attempts[-1].get("state") != WAITING or retained is None:
            raise ValueError("chapter_critic_waiting_receipt_invalid")
        _, report, quality, _ = retained
        return {
            **proof,
            "reuse_kind": WAITING,
            "verdict": "unknown",
            "report_id": str(report.id),
            "quality_score_id": str(quality.id),
            "review_workflow_run_id": str(owner.id),
            "requires_human_review": bool((owner.metadata_json or {}).get("requires_human_review")),
            "dispatch_authorized": False,
            "credit_released": False,
        }
    if state != "completed":
        return proof
    if not attempts or retained is None:
        raise ValueError("chapter_critic_cycle_attempts_invalid")
    result, report, quality, task = retained
    if attempts[-1]["outcome"] == "unknown":
        # The durable loop can consume terminal unknown only in its final
        # round; a completed one-round unknown would otherwise try to reopen.
        if len(attempts) != 2:
            raise ValueError("chapter_critic_cycle_attempts_invalid")
        return {
            **proof,
            "reuse_kind": "consume_retained_unknown",
            "verdict": "unknown",
            "report_id": str(report.id),
            "quality_score_id": str(quality.id),
        }
    if task is not None:
        from bestseller.services.rewrite_patch import text_hash

        task_metadata = task.metadata_json or {}
        if (
            task.trigger_type != "chapter_review"
            or task.trigger_source_id != chapter.id
            or task_metadata.get("chapter_id") != str(chapter.id)
            or task_metadata.get("draft_id") != str(current.id)
            or task_metadata.get("parent_draft_id") != str(current.id)
            or task_metadata.get("parent_draft_hash") != text_hash(current.content_md or "")
        ):
            raise ValueError("chapter_critic_cycle_rewrite_binding_mismatch")
    if result.verdict == "rewrite":
        if task is None or task.status != "pending":
            raise ValueError("chapter_critic_cycle_rewrite_not_pending")
    elif result.verdict != "pass" or task is not None:
        raise ValueError("chapter_critic_cycle_result_binding_mismatch")
    if issuer_policy is not None:
        # Rebuild the full source after the unchanged semantic/result/task
        # guards, then compare native cycle/report and runtime again. A capsule
        # alone never supplies a successful quality result or a replacement ID.
        source_renderer = (
            review_issuer_view.derive_current_primary_source
            if _selected_ids is None else review_issuer_view.derive_selected_primary_source
        )
        final_source = await source_renderer(
            session, settings, **issuer_ids,
        )
        review_issuer_view._require_type(
            "/preflight/final_source", final_source, dict, code="native_source_changed",
        )
        for key in ("binding", "paid_calls", "capsule", "current_runtime_policy_sha256"):
            review_issuer_view._require_equal(
                "/preflight/" + key, issuer_policy[key], final_source[key],
                code="native_source_changed",
            )
        latest_chapter = await reader.get(db.ChapterModel, chapter.id)
        if latest_chapter is None:
            review_issuer_view._reject(
                "native_scope_changed", "/preflight/chapter",
                {"type": "native row", "length": None, "sha256": None}, None,
            )
        review_issuer_view._require_equal(
            "/preflight/cycle", cycle,
            (latest_chapter.metadata_json or {}).get("chapter_critic_cycles", {}).get(cycle_id),
            code="native_cycle_changed",
        )
        review_issuer_view._require_equal(
            "/preflight/active_cycle", issuer_active,
            (latest_chapter.metadata_json or {}).get("chapter_review_active_cycle"),
            code="native_cycle_changed",
        )
        latest_report = await reader.get(db.ReviewReportModel, report.id)
        if latest_report is None:
            review_issuer_view._reject(
                "native_scope_changed", "/preflight/report",
                {"type": "native row", "length": None, "sha256": None}, None,
            )
        latest_report_fields = deepcopy(vars(latest_report))
        latest_report_fields["created_at"] = latest_report.created_at.isoformat()
        review_issuer_view._require_equal(
            "/preflight/report", issuer_report, latest_report_fields,
            code="native_source_changed",
        )
    return {
        **proof,
        "reuse_kind": "available_review",
        "report_id": str(report.id),
        "quality_score_id": str(quality.id),
        "task_id": str(task.id) if task is not None else None,
        "verdict": result.verdict,
        **({"issuer_policy": issuer_policy, "retained_input_proof": issuer_inputs}
           if issuer_policy is not None else {}),
    }


def _waiting_need(value: object, code: str) -> None:
    if not value:
        raise ValueError("chapter_waiting_review_policy_source_" + code)


def _waiting_digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
    ).hexdigest()


async def validate_waiting_review_policy_source(
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
    """Read-only explicit policy migration of the latest exact waiting body.

    The private core admits the stored old policy only after binding the actual
    new policy and current source. Native recovery still owns failed lineage,
    locking, allowance and dispatch. Normal preflight has no policy override.
    """
    try:
        with session.no_autoflush:
            return await _validate_waiting_policy_source(
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
        raise ValueError("chapter_waiting_review_policy_source_invalid_shape") from None
    except ValueError as error:
        code = str(error)
        if code.startswith("chapter_waiting_review_policy_source_"):
            raise
        detail = (
            code
            if code.startswith(("chapter_critic_", "waiting_chapter_review_resume_"))
            else "invalid_source"
        )
        raise ValueError("chapter_waiting_review_policy_source_" + detail) from error


async def _validate_waiting_policy_source(
    session: AsyncSession,
    settings: AppSettings,
    project: ProjectModel,
    chapter: ChapterModel,
    draft: ChapterDraftVersionModel,
    previous: WorkflowRunModel,
    scope: str,
    policy: str,
) -> dict:
    from bestseller.infra.db.models import (
        BookProductionControlModel,
        ExecutionBudgetModel,
        LlmRunModel,
        ReviewReportModel,
    )
    from bestseller.services import pipelines
    from bestseller.services.budget import completion_authorization, current_execution_scope
    from bestseller.services.chapter_review_prerequisite import WAITING
    from bestseller.services.feedback import validate_adopted_knowledge_source
    from bestseller.services.waiting_chapter_review_resume import _ledger

    _waiting_need(
        not session.new and not session.dirty and not session.deleted, "uncommitted_source"
    )
    _waiting_need(
        isinstance(policy, str)
        and re.fullmatch(r"[0-9a-f]{64}", policy)
        and policy == pipelines.chapter_review_recovery_source_hash(),
        "current_policy_hash",
    )
    _waiting_need(
        isinstance(scope, str)
        and 0 < len(scope) <= 256
        and current_execution_scope() in {None, scope}
        and chapter.project_id == draft.project_id == previous.project_id == project.id
        and draft.chapter_id == previous.scope_id == chapter.id
        and previous.scope_type == "chapter"
        and previous.workflow_type == "chapter_pipeline"
        and previous.status == "machine_blocked"
        and previous.current_step == WAITING,
        "waiting_owner_binding",
    )
    for model, row, fields in (
        (type(project), project, ("metadata_json",)),
        (type(chapter), chapter, ("project_id", "metadata_json")),
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
                "metadata_json",
            ),
        ),
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
    ):
        stored = (
            await session.execute(
                select(*(getattr(model, k) for k in fields)).where(model.id == row.id)
            )
        ).one_or_none()
        _waiting_need(
            stored is not None and all(getattr(stored, k) == getattr(row, k) for k in fields),
            "stored_source",
        )
    _waiting_need(
        draft.is_current
        and isinstance(draft.content_md, str)
        and draft.content_md.strip()
        and draft.promoted_at is None
        and draft.promotion_state
        in {"candidate", "under_review", "eligible", "rejected", "superseded", "quarantined"},
        "current_unpromoted_body",
    )
    fingerprint = pipelines.chapter_review_body_fingerprint(draft.content_md)
    cm, wm = chapter.metadata_json or {}, previous.metadata_json or {}
    _waiting_need(
        (project.metadata_json or {}).get("execution_scope_id") == scope
        and wm.get("execution_scope_id") == scope,
        "project_scope",
    )
    cycles = cm.get("chapter_critic_cycles")
    _waiting_need(isinstance(cycles, Mapping), "cycles_required")
    active = cm.get("chapter_review_active_cycle")
    key = (
        active.get("cycle_id")
        if isinstance(active, Mapping) and active.get("source_body_hash") == fingerprint["digest"]
        else pipelines._chapter_critic_cycle_id(project.id, chapter.id, fingerprint["digest"])
    )
    cycle = cycles.get(key)
    _waiting_need(
        isinstance(cycle, Mapping)
        and cycle.get("schema_version") == "chapter-critic-cycle.v1"
        and cycle.get("cycle_id") == key
        and cycle.get("state") == WAITING
        and cycle.get("source_draft_id") == str(draft.id)
        and cycle.get("source_body_hash") == fingerprint["digest"]
        and cycle.get("review_workflow_run_id") == str(previous.id)
        and wm.get("critic_cycle_id") == key,
        "latest_cycle_binding",
    )
    old = cycle.get("policy_source_hash")
    _waiting_need(
        isinstance(old, str) and re.fullmatch(r"[0-9a-f]{64}", old) and old != policy,
        "actual_policy_change_required",
    )
    _waiting_need(
        key
        in {
            pipelines._chapter_critic_cycle_id(project.id, chapter.id, fingerprint["digest"]),
            pipelines._chapter_critic_cycle_id(project.id, chapter.id, fingerprint["digest"], old),
        }
        and sum(
            isinstance(c, Mapping)
            and c.get("review_workflow_run_id") == str(previous.id)
            and c.get("source_draft_id") == str(draft.id)
            for c in cycles.values()
        )
        == 1,
        "canonical_cycle_required",
    )
    retained = await _validate_retained_review_before_processing(
        session,
        settings,
        chapter,
        expected_policy_hash=old,
    )
    _waiting_need(
        isinstance(retained, Mapping)
        and retained.get("reuse_kind") == WAITING
        and retained.get("draft_id") == str(draft.id)
        and retained.get("cycle_id") == key
        and retained.get("review_workflow_run_id") == str(previous.id)
        and retained.get("requires_human_review") is False
        and cm.get("chapter_review_evidence_status") == "unknown"
        and cm.get("chapter_review_evidence_report_id") == retained.get("report_id")
        and wm.get("review_prerequisite_wait") == retained
        and wm.get("review_report_id") == retained.get("report_id")
        and wm.get("quality_score_id") == retained.get("quality_score_id")
        and wm.get("chapter_critic_evidence_unknown") is True
        and wm.get("requires_human_review") is False,
        "latest_waiting_receipt",
    )
    report = await session.get(ReviewReportModel, UUID(retained["report_id"]))
    reports = list(
        await session.scalars(
            select(ReviewReportModel).where(
                ReviewReportModel.project_id == project.id,
                ReviewReportModel.target_type == "chapter",
                ReviewReportModel.target_id == chapter.id,
            )
        )
    )
    current_reports = [
        r
        for r in reports
        if (r.structured_output or {}).get("draft_id") == str(draft.id)
        or (r.structured_output or {}).get("reviewed_body_fingerprint") == fingerprint
    ]
    _waiting_need(
        report is not None
        and current_reports
        and all(
            r.created_at <= report.created_at
            and r.verdict == "unknown"
            and r.llm_run_id is None
            and (r.structured_output or {}).get("draft_id") == str(draft.id)
            and (r.structured_output or {}).get("reviewed_body_fingerprint") == fingerprint
            and all(
                (r.structured_output or {}).get(k) is None
                for k in (
                    "primary_submission_receipt",
                    "primary_submission_original",
                    "rewrite_instructions",
                )
            )
            for r in current_reports
        ),
        "latest_unknown_report",
    )
    primary = list(
        await session.scalars(
            select(LlmRunModel).where(
                LlmRunModel.project_id == project.id,
                LlmRunModel.workflow_run_id == previous.id,
                LlmRunModel.prompt_template == "chapter_review",
            )
        )
    )
    raw_body = hashlib.sha256(draft.content_md.encode()).hexdigest()
    _waiting_need(
        all(
            isinstance((r.metadata_json or {}).get("primary_review_body_sha256"), str)
            and re.fullmatch(r"[0-9a-f]{64}", r.metadata_json["primary_review_body_sha256"])
            and r.metadata_json["primary_review_body_sha256"] != raw_body
            for r in primary
        ),
        "primary_returned_or_unattributed",
    )
    _, rejection = await validate_adopted_knowledge_source(
        session,
        project_id=project.id,
        chapter_id=chapter.id,
        source_chapter_draft_version_id=draft.id,
        chapter_md=draft.content_md,
        lock=False,
    )
    _waiting_need(rejection == "source_not_adopted", "source_adopted_or_ambiguous")
    _waiting_need(
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
    _waiting_need(
        control is not None
        and control.desired_state == "run"
        and type(control.command_serial) is int
        and control.command_serial > 0,
        "explicit_run",
    )
    ledger = await _ledger(session, scope, lock=False)
    budget = await session.get(ExecutionBudgetModel, scope)
    auth = completion_authorization(budget) if budget is not None else None
    _waiting_need(
        budget is not None
        and budget.status in {"active", "exhausted"}
        and auth is not None
        and auth.get("status") == "active",
        "completion_authorization",
    )
    proof = {
        "schema": "waiting-review-policy-source.v1",
        "status": "verified_waiting_review_origin",
        "project_id": str(project.id),
        "chapter_id": str(chapter.id),
        "draft_id": str(draft.id),
        "previous_workflow_run_id": str(previous.id),
        "previous_status": previous.status,
        "previous_step": previous.current_step,
        "previous_metadata_sha256": _waiting_digest(wm),
        "execution_scope_id": scope,
        "source_body_fingerprint": fingerprint,
        "source_cycle_id": key,
        "source_cycle_sha256": _waiting_digest(cycle),
        "previous_policy_source_hash": old,
        "current_policy_source_hash": policy,
        "semantic_dependencies_sha256": retained["semantic_dependencies_sha256"],
        "latest_report_id": retained["report_id"],
        "latest_quality_score_id": retained["quality_score_id"],
        "settled_ledger_sha256": _waiting_digest(ledger),
        "control_command_serial": control.command_serial,
        "authorization_ref": auth["authorization_ref"],
        "authorization_sha256": _waiting_digest(auth),
        "budget_usage": {"calls_used": budget.calls_used, "tokens_charged": budget.tokens_charged},
        "origin_only": True,
        "grade_reuse_allowed": False,
        "adoption_allowed": False,
        "attempt_reset_allowed": False,
        "new_credit_allowed": False,
        "dispatch_allowed": False,
    }
    proof["proof_sha256"] = _waiting_digest(proof)
    return proof
