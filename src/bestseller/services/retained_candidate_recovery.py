"""One durable paragraph-patch successor, never adoption or fresh lineage credit.

Execution explicitly owns commits on a clean, non-nested session. Inspection
does not flush, persist reports, create a workflow, or invoke a provider.
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
from typing import Any, TypeVar
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.infra.db.models import (
    ChapterDraftVersionModel,
    ChapterModel,
    ChapterQualityReportModel,
    LlmRunModel,
    ProjectModel,
    RewriteTaskModel,
    SceneCardModel,
    WorkflowRunModel,
)
from bestseller.services import reviews
from bestseller.services.budget import (
    bind_execution_scope,
    current_execution_scope,
    execution_operation_id,
)
from bestseller.services.context import build_chapter_writer_context
from bestseller.services.deterministic_audit_actions import effective_deterministic_audit_blockers
from bestseller.services.llm import LLMCompletionRequest, complete_text
from bestseller.services.quality_report_claim import graded_text_fingerprint, report_grades_text
from bestseller.services.retained_candidate_patch import (
    FINDING_CODE,
    TOOL_NAME,
    apply_candidate_patch,
    prepare_candidate_patch,
)
from bestseller.services.retry_ledger import project_execution_scope
from bestseller.services.review_source_context import load_review_source_context
from bestseller.services.rewrite_candidate_evidence import (
    CandidateEvidence,
    blocking_deterministic_violations,
    evaluate_rewrite_candidate_evidence,
)
from bestseller.services.rewrite_patch import text_hash
from bestseller.settings import AppSettings

POLICY = "retained-candidate-recovery.v1"
STRATEGY = "retained_candidate_paragraph_patch"
Row = TypeVar("Row")


def _json(value: object) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def _hash(value: object) -> str:
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _require(condition: object, code: str) -> None:
    if not condition:
        raise ValueError("retained_candidate_recovery_" + code)


def prepare_source_complete_prompt(
    *, system_prompt: str, user_prompt: str, source_context: dict[str, Any], output_tokens: int
) -> tuple[str, str, dict[str, Any]]:
    """Resolve the editor's normal wrapper only when constructing a request."""
    from bestseller.services.lossless_rewrite_prompt import (
        prepare_source_complete_prompt as prepare,
    )

    return prepare(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        source_context=source_context,
        output_tokens=output_tokens,
    )


async def _get(
    session: AsyncSession, model: type[Row], identity: UUID, *, lock: bool = False
) -> Row | None:
    query = select(model).where(model.id == identity).execution_options(populate_existing=True)
    return await session.scalar(query.with_for_update() if lock else query)


async def _binding(
    session: AsyncSession,
    *,
    project_id: UUID,
    chapter_id: UUID,
    failed_task_id: UUID,
    candidate_id: UUID,
    lock: bool = False,
) -> tuple[Any, ...]:
    chapter = await _get(session, ChapterModel, chapter_id, lock=lock)
    project = await _get(session, ProjectModel, project_id)
    failed = await _get(session, RewriteTaskModel, failed_task_id, lock=lock)
    candidate = await _get(session, ChapterDraftVersionModel, candidate_id)
    _require(project and chapter and failed and candidate, "missing_row")
    _require(
        chapter.project_id == project_id
        and candidate.project_id == project_id
        and candidate.chapter_id == chapter_id
        and failed.project_id == project_id
        and failed.trigger_source_id == chapter_id
        and failed.status == "failed",
        "scope_or_failed_task",
    )
    meta = failed.metadata_json or {}
    _require(
        meta.get("candidate_chapter_draft_id") == str(candidate_id)
        and meta.get("candidate_body_sha256") == text_hash(candidate.content_md)
        and meta.get("candidate_llm_run_id") == str(candidate.llm_run_id)
        and candidate.llm_run_id is not None
        and not candidate.is_current
        and candidate.promotion_state == "candidate",
        "candidate_binding",
    )
    _require(
        meta.get("candidate_gate_graded") == graded_text_fingerprint(candidate.content_md),
        "candidate_grade",
    )
    parent_id = meta.get("parent_draft_id")
    try:
        parent = await _get(session, ChapterDraftVersionModel, UUID(str(parent_id)))
    except (TypeError, ValueError):
        parent = None
    _require(
        parent
        and parent.project_id == project_id
        and parent.chapter_id == chapter_id
        and parent.id != candidate.id
        and meta.get("draft_id") == str(parent.id)
        and meta.get("parent_draft_hash") == text_hash(parent.content_md),
        "parent_binding",
    )
    root = failed
    seen = set()
    while root.parent_task_id is not None:
        _require(root.id not in seen and len(seen) < 100, "lineage_cycle")
        seen.add(root.id)
        root = await _get(session, RewriteTaskModel, root.parent_task_id, lock=lock)
        _require(
            root and root.project_id == project_id and root.trigger_source_id == chapter_id,
            "lineage_scope",
        )
    _require(root.id not in seen, "lineage_cycle")
    paid = await _get(session, LlmRunModel, candidate.llm_run_id)
    scope = project_execution_scope(project)
    _require(
        paid
        and paid.project_id == project_id
        and paid.logical_role == "editor"
        and paid.workflow_run_id is not None
        and (paid.metadata_json or {}).get("execution_receipt", {}).get("scope_id") == scope,
        "paid_origin",
    )
    paid_workflow = await _get(session, WorkflowRunModel, paid.workflow_run_id)
    _require(paid_workflow and paid_workflow.project_id == project_id, "paid_workflow")
    _require(current_execution_scope() in {None, scope}, "execution_scope")
    identity = {
        "project_id": str(project_id),
        "chapter_id": str(chapter_id),
        "failed_task_id": str(failed_task_id),
        "candidate_id": str(candidate_id),
        "candidate_body_sha256": text_hash(candidate.content_md),
        "parent_id": str(parent.id),
        "parent_body_sha256": text_hash(parent.content_md),
        "lineage_root_task_id": str(root.id),
        "scope_id": scope,
    }
    seed = _json(identity)
    workflow_id = uuid5(NAMESPACE_URL, "retained-candidate-successor/workflow/" + seed)
    child_id = uuid5(NAMESPACE_URL, "retained-candidate-successor/task/" + seed)
    current = list(
        await session.scalars(
            select(ChapterDraftVersionModel)
            .where(
                ChapterDraftVersionModel.project_id == project_id,
                ChapterDraftVersionModel.chapter_id == chapter_id,
                ChapterDraftVersionModel.is_current.is_(True),
            )
            .execution_options(populate_existing=True)
        )
    )
    _require(len(current) == 1, "current_pointer")
    return (
        project,
        chapter,
        failed,
        candidate,
        parent,
        root,
        identity,
        workflow_id,
        child_id,
        current[0],
    )


async def _sources(
    session: AsyncSession,
    settings: AppSettings,
    project: ProjectModel,
    chapter: ChapterModel,
    candidate: ChapterDraftVersionModel,
) -> tuple[dict[str, Any], list[SceneCardModel]]:
    scenes = list(
        await session.scalars(
            select(SceneCardModel)
            .where(
                SceneCardModel.project_id == project.id,
                SceneCardModel.chapter_id == chapter.id,
            )
            .order_by(SceneCardModel.scene_number)
            .execution_options(populate_existing=True)
        )
    )
    _require(scenes, "scenes_missing")
    packet = await build_chapter_writer_context(
        session, settings, project.slug, chapter.chapter_number
    )
    source = await load_review_source_context(
        session,
        project=project,
        chapter=chapter,
        scenes=scenes,
        context_packet=packet,
        content_md=candidate.content_md,
        settings=settings,
    )
    _require(
        isinstance(source, dict)
        and source.get("current_source_channels")
        and isinstance(source.get("adopted_continuity"), dict)
        and source.get("editable_plan"),
        "source_incomplete",
    )
    history = source["adopted_continuity"]
    _require(
        history.get("project_id") == str(project.id)
        and history.get("before_chapter") == chapter.chapter_number
        and isinstance(history.get("recent_adopted_prose"), list),
        "history_scope",
    )
    return source, scenes


def _audit(
    project: ProjectModel,
    chapter: ChapterModel,
    scenes: list[SceneCardModel],
    body: str,
    settings: AppSettings,
) -> tuple[Any, Any]:
    return reviews._audit_chapter_rewrite_candidate(
        project=project, chapter=chapter, scenes=scenes, content_md=body, settings=settings
    )


async def _prepare(
    session: AsyncSession, settings: AppSettings, binding: tuple[Any, ...]
) -> tuple[Any, ...]:
    project, chapter, failed, candidate, *_ = binding
    source, scenes = await _sources(session, settings, project, chapter, candidate)
    report, band = _audit(project, chapter, scenes, candidate.content_md, settings)
    _require(report is not None and isinstance(report.passed, bool), "audit_unavailable")
    effective = effective_deterministic_audit_blockers(
        report,
        settings=reviews.get_quality_gates_config().l6_gate,
        chapter_number=chapter.chapter_number,
    )
    _require(effective and all(f.code == FINDING_CODE for f in effective), "audit_not_copy_only")
    words = reviews.count_words(candidate.content_md)
    _require(band.hard_min <= words <= band.hard_max, "candidate_length")
    meta = failed.metadata_json or {}
    violations = meta.get("candidate_quality_gate_violations")
    _require(
        isinstance(violations, list)
        and violations
        and all(isinstance(v, dict) and v.get("code") == FINDING_CODE for v in violations)
        and not meta.get("candidate_duplicate_gate_findings")
        and not meta.get("candidate_quality_retrofit_findings"),
        "historical_blockers",
    )
    gate_id = meta.get("candidate_gate_evaluation_id")
    reports = list(
        await session.scalars(
            select(ChapterQualityReportModel).where(
                ChapterQualityReportModel.chapter_id == chapter.id,
            )
        )
    )
    matching = [
        r
        for r in reports
        if (r.report_json or {}).get("rewrite_evaluation_id") == gate_id
        and report_grades_text(r.report_json, candidate.content_md)
    ]
    _require(gate_id and len(matching) == 1, "historical_gate_binding")
    payload = matching[0].report_json
    _require(
        set(payload.get("blocking_codes", ())) <= {FINDING_CODE}
        and all(
            v.get("code") == FINDING_CODE
            for v in payload.get("violations", ())
            if str(v.get("severity", "")).lower() in {"high", "critical", "block", "fatal"}
        ),
        "historical_hard_blocker",
    )
    effects = reviews.audit_persisted_story_effect_realization(
        project, chapter, candidate.content_md, scenes=scenes
    )
    _require(effects.get("passed") is True, "candidate_effects")
    prepared = prepare_candidate_patch(candidate.content_md, effective)
    req = prepared["request"]
    system, user, transport = prepare_source_complete_prompt(
        system_prompt=req["system_prompt"],
        user_prompt=req["user_prompt"],
        source_context=source,
        output_tokens=4096,
    )
    # Freeze actual code, source data, and retained predecessor evidence. The
    # identity above deliberately excludes these: upgrades cannot create credit.
    from bestseller.services import pipelines, retained_candidate_patch, review_source_context

    raw_audit = report.to_dict()
    transport = {**transport, "preflight_audit": raw_audit}
    audit_fence = deepcopy(raw_audit)
    audit_fence["summary"].pop("elapsed_ms", None)
    fence = {
        "source_sha256": _hash(source),
        "policy_sha256": pipelines.chapter_review_recovery_source_hash(),
        "recovery_source_sha256": {
            Path(module.__file__).name: hashlib.sha256(
                Path(module.__file__).read_bytes()
            ).hexdigest()
            for module in (retained_candidate_patch, review_source_context)
        },
        "executor_source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "failed_task_sha256": _hash(
            {
                "instructions": failed.instructions,
                "attempts": failed.attempts,
                "metadata": failed.metadata_json,
                "parent_task_id": str(failed.parent_task_id) if failed.parent_task_id else None,
                "error_log": failed.error_log,
            }
        ),
        "patch_catalog_sha256": prepared["catalog_sha256"],
        "preflight_audit": audit_fence,
    }
    return source, scenes, prepared, system, user, transport, fence


async def _evidence(
    session: AsyncSession,
    settings: AppSettings,
    project: ProjectModel,
    chapter: ChapterModel,
    scenes: list[SceneCardModel],
    body: str,
) -> CandidateEvidence:
    async def gate(text: str) -> reviews.RewriteQualityGateReceipt:
        # Normal quality evaluation also registers diversity telemetry. A
        # candidate has not shipped: retain its real report, not those writes.
        savepoint = await session.begin_nested()
        try:
            evaluated = await reviews._evaluate_rewrite_quality_gate(
                session=session,
                project=project,
                chapter_number=chapter.chapter_number,
                content=text,
            )
            row = evaluated.report
            data = (
                {
                    column.key: deepcopy(getattr(row, column.key))
                    for column in ChapterQualityReportModel.__table__.columns
                }
                if row is not None
                else None
            )
            outcome, evaluation_id, graded = (
                evaluated.outcome,
                evaluated.evaluation_id,
                deepcopy(evaluated.graded),
            )
        finally:
            await savepoint.rollback()
        retained = ChapterQualityReportModel(**data) if data else None
        if retained is not None:
            session.add(retained)
            await session.flush()
        return reviews.RewriteQualityGateReceipt(outcome, evaluation_id, graded, retained)

    return await evaluate_rewrite_candidate_evidence(
        body,
        "retained_paragraph_patch",
        audit_candidate=lambda text: _audit(project, chapter, scenes, text, settings),
        classify_audit=lambda report, band, words: blocking_deterministic_violations(
            report,
            band=band,
            word_count=words,
            chapter_number=chapter.chapter_number,
            config=reviews.get_quality_gates_config().l6_gate,
            translate=reviews._deterministic_rewrite_violations,
        ),
        collect_duplicates=lambda text: reviews._collect_post_assembly_duplicate_findings(
            session, project=project, chapter=chapter, content_md=text
        ),
        evaluate_gate=gate,
        count_words=reviews.count_words,
    )


async def _save_draft(
    session: AsyncSession,
    project: ProjectModel,
    chapter: ChapterModel,
    parent: ChapterDraftVersionModel,
    body: str,
    llm_id: UUID | None,
    receipt: dict[str, Any],
    *,
    current: bool = False,
) -> ChapterDraftVersionModel:
    latest = await session.scalar(
        select(func.max(ChapterDraftVersionModel.version_no)).where(
            ChapterDraftVersionModel.chapter_id == chapter.id
        )
    )
    draft = ChapterDraftVersionModel(
        project_id=project.id,
        chapter_id=chapter.id,
        version_no=int(latest or 0) + 1,
        content_md=body,
        word_count=reviews.count_words(body),
        assembled_from_scene_draft_ids=deepcopy(parent.assembled_from_scene_draft_ids or []),
        is_current=False,
        promotion_state="candidate",
        llm_run_id=llm_id,
        promotion_reason_codes=["retained_candidate_patch_requires_new_body_review"],
        promotion_metadata={"retained_candidate_recovery": deepcopy(receipt)},
    )
    session.add(draft)
    await session.flush()
    if current:
        parent.is_current = False
        await session.flush()
        draft.is_current = True
        chapter.current_word_count = draft.word_count
        chapter.status = "review"
        chapter.production_state = "pending"
        await session.flush()
    return draft


async def repair_retained_candidate(
    session: AsyncSession,
    settings: AppSettings,
    *,
    project_id: UUID,
    chapter_id: UUID,
    failed_task_id: UUID,
    candidate_id: UUID,
    requested_by: str,
    execute: bool = False,
) -> dict[str, Any]:
    """Inspect without writes; execute commits a once-only claim before payment.

    Use a dedicated clean session, outside ``session.begin()``. Existing running
    or failed claims are inspectable but never replay a call. Completed claims
    return their original receipt without changing pointers or reports.
    """
    _require(
        type(execute) is bool
        and isinstance(requested_by, str)
        and 0 < len(requested_by.strip()) <= 64,
        "arguments",
    )
    _require(
        not session.new
        and not session.dirty
        and not session.deleted
        and not session.in_nested_transaction()
        and session.sync_session._trans_context_manager is None,
        "clean_session_required",
    )
    args = {
        "project_id": project_id,
        "chapter_id": chapter_id,
        "failed_task_id": failed_task_id,
        "candidate_id": candidate_id,
    }
    with session.no_autoflush:
        binding = await _binding(session, **args, lock=execute)
        (
            project,
            chapter,
            failed,
            candidate,
            parent,
            root,
            identity,
            workflow_id,
            child_id,
            current,
        ) = binding
        siblings = list(
            await session.scalars(
                select(RewriteTaskModel)
                .where(
                    RewriteTaskModel.project_id == project_id,
                    RewriteTaskModel.trigger_source_id == chapter_id,
                    RewriteTaskModel.rewrite_strategy == STRATEGY,
                )
                .execution_options(populate_existing=True)
            )
        )
        claimed = [
            r
            for r in siblings
            if (r.metadata_json or {}).get("lineage_root_task_id") == str(root.id)
        ]
        if claimed:
            _require(len(claimed) == 1 and claimed[0].id == child_id, "lineage_already_claimed")
            child = claimed[0]
            workflow = await _get(session, WorkflowRunModel, workflow_id)
            meta = child.metadata_json or {}
            _require(
                workflow
                and workflow.project_id == project_id
                and child.parent_task_id == failed.id
                and meta.get("binding") == identity
                and meta.get("attempt_claimed") == 1,
                "claim_binding",
            )
            receipt = deepcopy(meta.get("receipt") or {})
            if child.status == "completed":
                expected_current = receipt.get("new_draft_id")
                _require(
                    current.id == UUID(str(expected_current)) and workflow.status == "completed",
                    "completed_pointer_changed",
                )
                _require(
                    text_hash(current.content_md) == receipt.get("result_body_sha256")
                    and current.promotion_state == "candidate",
                    "completed_result_changed",
                )
                replay_prepared = await _prepare(session, settings, binding)
                _require(replay_prepared[-1] == meta.get("fence"), "completed_source_changed")
            else:
                _require(current.id == parent.id, "parent_changed")
            return {
                "schema": POLICY,
                "status": child.status,
                "replay": True,
                "provider_calls": 0,
                "database_writes": 0,
                "receipt": receipt,
                "workflow_id": str(workflow_id),
                "child_task_id": str(child_id),
            }
        _require(current.id == parent.id and current.is_current, "parent_changed")
        prepared_data = await _prepare(session, settings, binding)
        _source, scenes, prepared, system, user, transport, fence = prepared_data
        plan = {
            "schema": POLICY,
            "status": "prepared",
            "binding": identity,
            "fence": fence,
            "workflow_id": str(workflow_id),
            "child_task_id": str(child_id),
            "target_count": len(prepared["targets"]),
            "provider_calls": 0,
            "database_writes": 0,
            "formal_review_required": True,
            "adoption_authorized": False,
        }
        if not execute:
            return plan
    request = LLMCompletionRequest(
        logical_role="editor",
        project_id=project_id,
        workflow_run_id=workflow_id,
        system_prompt=system,
        user_prompt=user,
        fallback_response="Retained candidate patch unavailable; no replacement authorized.",
        prompt_template="retained_candidate_paragraph_patch",
        prompt_version="1.0",
        tools=prepared["request"]["tools"],
        tool_choice={"type": "function", "function": {"name": TOOL_NAME}},
        max_tokens_override=4096,
        operation_id=execution_operation_id(
            "retained_candidate_patch", subject=str(root.id), revision=_hash(identity)
        ),
        sample_slot=0,
        metadata={"binding": identity, "fence": fence, "source_transport": transport},
    )
    claim = {
        "binding": identity,
        "fence": fence,
        "lineage_root_task_id": str(root.id),
        "attempt_claimed": 1,
        "request": request.model_dump(mode="json"),
        "prepared_patch": prepared,
        "receipt": plan,
    }
    workflow = WorkflowRunModel(
        id=workflow_id,
        project_id=project_id,
        workflow_type=STRATEGY,
        status="running",
        scope_type="chapter",
        scope_id=chapter_id,
        requested_by=requested_by,
        current_step="patch_attempt_claimed",
        metadata_json=deepcopy(claim),
    )
    child = RewriteTaskModel(
        id=child_id,
        project_id=project_id,
        parent_task_id=failed_task_id,
        trigger_type="retained_candidate_recovery",
        trigger_source_id=chapter_id,
        rewrite_strategy=STRATEGY,
        priority=1,
        status="running",
        attempts=1,
        instructions=(
            "Apply only the exact retained-candidate paragraph patch; then run all quality gates."
        ),
        context_required=["exact_retained_candidate", "current_adopted_sources"],
        metadata_json=deepcopy(claim),
    )
    session.add_all([workflow, child])
    candidate_body = candidate.content_md
    await session.commit()  # Durable before complete_text can release its transaction.
    body = None
    completion = None
    receipt = {
        **{
            key: value
            for key, value in plan.items()
            if key not in {"provider_calls", "database_writes"}
        },
        "status": "running",
        "complete_text_invocations": 0,
        "database_writes": True,
        "request_sha256": _hash(claim["request"]),
    }
    try:
        before_call = await _binding(session, **args)
        _require(before_call[-1].id == UUID(identity["parent_id"]), "parent_changed_before_call")
        _require(
            (await _prepare(session, settings, before_call))[-1] == fence,
            "source_changed_before_call",
        )
        receipt["complete_text_invocations"] = 1
        with bind_execution_scope(identity["scope_id"]):
            completion = await complete_text(session, settings, request)
        receipt["completion"] = {
            "content": completion.content,
            "tool_calls": deepcopy(completion.tool_calls),
            "raw_message": deepcopy(completion.raw_message),
            "llm_run_id": str(completion.llm_run_id) if completion.llm_run_id else None,
            "provider": completion.provider,
            "finish_reason": completion.finish_reason,
        }
        # Retain the real return and LLM row before parsing/gating can fail or
        # roll back. An interrupted worker still has a spent, inspectable claim.
        child = await _get(session, RewriteTaskModel, child_id, lock=True)
        workflow = await _get(session, WorkflowRunModel, workflow_id)
        child.metadata_json = {**claim, "receipt": deepcopy(receipt)}
        workflow.metadata_json = {**claim, "receipt": deepcopy(receipt)}
        workflow.current_step = "paid_return_retained"
        await session.commit()
        _require(
            not completion.fallback_used
            and completion.provider not in {"fallback", "mock"}
            and completion.finish_reason in {"stop", "tool_calls"}
            and completion.llm_run_id is not None,
            "completion_unavailable",
        )
        calls = completion.tool_calls
        _require(
            isinstance(calls, list) and len(calls) == 1 and isinstance(calls[0], dict), "tool_count"
        )
        call = calls[0]
        function = call.get("function") or {}
        _require(
            call.get("type") == "function"
            and function.get("name") == TOOL_NAME
            and isinstance(function.get("arguments"), str),
            "tool_contract",
        )
        body, patch_receipt = apply_candidate_patch(candidate_body, prepared, function["arguments"])
        receipt["patch"] = patch_receipt
        receipt["result_body_sha256"] = text_hash(body)
        fresh = await _binding(session, **args, lock=True)
        project, chapter, failed, candidate, parent, root, fresh_identity, *_tail, current = fresh
        _require(
            fresh_identity == identity and current.id == parent.id, "parent_changed_after_call"
        )
        _, scenes, _, _, _, _, fresh_fence = await _prepare(session, settings, fresh)
        _require(fresh_fence == fence, "source_changed_after_call")
        paid = await _get(session, LlmRunModel, completion.llm_run_id)
        _require(
            paid
            and paid.project_id == project_id
            and paid.workflow_run_id == workflow_id
            and paid.logical_role == "editor"
            and (paid.metadata_json or {}).get("execution_receipt", {}).get("scope_id")
            == identity["scope_id"],
            "patch_paid_origin",
        )
        evidence = await _evidence(session, settings, project, chapter, scenes, body)
        # The gate savepoint can expire ORM state. Reload the actual rows and
        # fences again before selecting a pointer; never trust identity caches.
        fresh = await _binding(session, **args, lock=True)
        project, chapter, failed, candidate, parent, root, fresh_identity, *_tail, current = fresh
        _require(
            fresh_identity == identity and current.id == parent.id, "parent_changed_after_gate"
        )
        _require(
            (await _prepare(session, settings, fresh))[-1] == fence, "source_changed_after_gate"
        )
        effects = reviews.audit_persisted_story_effect_realization(
            project, chapter, body, scenes=scenes
        )
        receipt.update(evidence=evidence.receipt(), effects=effects)
        passed = (
            evidence.outcome == "ok"
            and evidence.gate_receipt.report is not None
            and not evidence.duplicates
            and not evidence.audit_blockers
            and not evidence.retrofit
            and not any(f.code == FINDING_CODE for f in evidence.audit_report.findings)
            and effects.get("passed") is True
        )
        # The gate is not a model judge or an adoption permission.
        receipt.update(
            status="completed" if passed else "failed",
            working_pointer_selected=passed,
            formal_review_required=True,
            adoption_authorized=False,
        )
        draft = await _save_draft(
            session, project, chapter, parent, body, completion.llm_run_id, receipt, current=passed
        )
        receipt["new_draft_id"] = str(draft.id)
        child = await _get(session, RewriteTaskModel, child_id, lock=True)
        workflow = await _get(session, WorkflowRunModel, workflow_id)
        child.status = workflow.status = receipt["status"]
        child.metadata_json = {**claim, "receipt": deepcopy(receipt)}
        workflow.metadata_json = {**claim, "receipt": deepcopy(receipt)}
        workflow.current_step = "requires_new_body_review" if passed else "patch_rejected"
        await session.commit()
        return {
            "schema": POLICY,
            "status": receipt["status"],
            "replay": False,
            "workflow_id": str(workflow_id),
            "child_task_id": str(child_id),
            "receipt": receipt,
        }
    except Exception as exc:
        await session.rollback()
        receipt.update(
            status="failed", working_pointer_selected=False, error_type=type(exc).__name__
        )
        if isinstance(exc, ValueError) and str(exc).startswith(
            ("retained_candidate_recovery_", "candidate_patch_")
        ):
            receipt["error_code"] = str(exc)[:256]
        child = await _get(session, RewriteTaskModel, child_id, lock=True)
        workflow = await _get(session, WorkflowRunModel, workflow_id)
        _require(child and workflow, "claim_missing_after_failure")
        if body is not None:
            chapter = await _get(session, ChapterModel, chapter_id, lock=True)
            project = await _get(session, ProjectModel, project_id)
            parent = await _get(session, ChapterDraftVersionModel, UUID(identity["parent_id"]))
            draft = await _save_draft(
                session,
                project,
                chapter,
                parent,
                body,
                completion.llm_run_id if completion else None,
                receipt,
            )
            receipt["new_draft_id"] = str(draft.id)
        child.status = workflow.status = "failed"
        child.error_log = (
            "retained candidate patch failed; original evidence and current pointer preserved"
        )
        child.metadata_json = {**claim, "receipt": deepcopy(receipt)}
        workflow.metadata_json = {**claim, "receipt": deepcopy(receipt)}
        workflow.current_step = "patch_failed_no_retry_credit"
        await session.commit()
        return {
            "schema": POLICY,
            "status": "failed",
            "replay": False,
            "workflow_id": str(workflow_id),
            "child_task_id": str(child_id),
            "receipt": receipt,
        }
