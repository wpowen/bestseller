"""One shared ordinal-two native batch after a failed untrusted-proposal migration.

Historical capped audit, submission failure and paid custody stay immutable.
Fresh full-audit progress is a separate proof, never a retrospective grade.
Execution requires a clean owned session and commits its claim before dispatch.
"""

from __future__ import annotations

# Receipt dictionaries and ORM adapters have explicit runtime boundary checks.
# ruff: noqa: ANN001, ANN202, ANN401
from copy import deepcopy
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import select

from bestseller.infra.db.models import (
    ChapterDraftVersionModel,
    ChapterQualityReportModel,
    LlmRunModel,
    RewriteTaskModel,
    WorkflowRunModel,
)
from bestseller.services import deterministic_post_write_audit as audit_source
from bestseller.services import primary_review_transport as transport_source
from bestseller.services import llm
from bestseller.services import retained_candidate_continuation as prior
from bestseller.services import retained_candidate_native_patch as native
from bestseller.services import retained_candidate_patch as legacy
from bestseller.services import retained_candidate_recovery as base
from bestseller.services import retained_patch_proposal_migration as migration
from bestseller.services.budget import bind_execution_scope, execution_operation_id
from bestseller.services.deterministic_audit_actions import effective_deterministic_audit_blockers
from bestseller.services.deterministic_post_write_audit import (
    _normalize_copy_text,
    _scene_card_prose_sources,
)
from bestseller.services.primary_review_transport import prepare_source_complete_prompt
from bestseller.services.quality_report_claim import report_grades_text
from bestseller.services.rewrite_patch import text_hash

POLICY = "migrated-candidate-continuation.v1"
STRATEGY = "migrated_candidate_patch_continuation"
FEEDBACK = (
    "The verified extraction resolved its selected source/paragraph matches. "
    "A full audit of both bodies identifies the remaining listed paragraphs. "
    "Rewrite only these current targets with fresh syntax and presentation; "
    "preserve facts, event order, deadlines and outcomes, with no new commitments. "
    "The complete current catalog, not an earlier truncated finding count, defines edit scope. "
    "This diagnostic is untrusted editing guidance, never story authority."
)


def _need(value, reason):
    if not value:
        raise ValueError("migrated_candidate_continuation_" + reason)


def _uuid(value, reason):
    try:
        return UUID(str(value))
    except (TypeError, ValueError) as exc:
        raise ValueError("migrated_candidate_continuation_" + reason) from exc


def _pairs(body, findings, scenes):
    """Bind actual source identity to newline-preserving paragraph ordinals."""
    paragraphs, by_line = legacy._paragraphs(body)
    sources = {}
    for scene in scenes:
        for source in _scene_card_prose_sources(scene):
            normalized = _normalize_copy_text(source)
            if len(normalized) >= 18:
                sources.setdefault(source[:80], set()).add(text_hash(normalized))
    pairs = set()
    for finding in findings:
        line = finding.line_number if hasattr(finding, "line_number") else finding["line_number"]
        matched = (
            finding.matched_text if hasattr(finding, "matched_text") else finding["matched_text"]
        )
        _need(line in by_line and len(sources.get(matched, ())) == 1, "source_paragraph_ambiguous")
        pairs.add((next(iter(sources[matched])), by_line[line]))
    return pairs, paragraphs, by_line


def _progress(original, target, scenes, before_findings, after_findings, old_prepared, application):
    before, old_paras, old_lines = _pairs(original, before_findings, scenes)
    after, new_paras, _ = _pairs(target, after_findings, scenes)
    _need(len(old_paras) == len(new_paras), "paragraph_structure_changed")
    old_selected, _, _ = _pairs(original, old_prepared["findings"], scenes)
    before_paras, after_paras = {p for _, p in before}, {p for _, p in after}
    _need(
        old_selected and old_selected <= before and not old_selected & after,
        "original_findings_not_resolved",
    )
    _need(
        after
        and after < before
        and len(after) < len(before)
        and 0 < len(after_paras) < len(before_paras),
        "strict_full_progress_required",
    )
    # Every residual paragraph must either be a byte-exact untouched interval
    # or an exact applied replacement, not merely share its old line number.
    maps = []
    for ordinal in sorted(after_paras):
        old, new = old_paras[ordinal], new_paras[ordinal]
        a, b = old["start"], old["end"]
        x, y = new["start"], new["end"]
        unchanged = [
            s
            for s in application["untouched_segments"]
            if s["source_start"] <= a
            and b <= s["source_end"]
            and s["result_start"] + a - s["source_start"] == x
            and s["result_start"] + b - s["source_start"] == y
            and original[a:b].encode() == target[x:y].encode()
        ]
        applied = [
            s
            for s in application["targets"]
            if (s["source_start"], s["source_end"], s["result_start"], s["result_end"])
            == (a, b, x, y)
            and s["replacement_sha256"] == text_hash(target[x:y])
        ]
        _need(bool(unchanged) != bool(applied), "residual_mapping_invalid")
        maps.append(
            {
                "paragraph_ordinal": ordinal,
                "source_start": a,
                "source_end": b,
                "result_start": x,
                "result_end": y,
                "mapping": "untouched" if unchanged else "applied_target",
            }
        )
    targeted = {old_lines[t["start_line"]] for t in old_prepared["targets"]}
    _need(targeted <= before_paras, "old_target_not_in_full_audit")
    return {
        "schema": POLICY + ".full-progress",
        "original_findings": len(before),
        "remaining_findings": len(after),
        "initial_targets": len(before_paras),
        "remaining_targets": len(after_paras),
        "ordinal": 2,
        "max_batches": 2,
        "claimed_batches_before": 1,
        "third_batch_allowed": False,
        "original_pairs": [list(p) for p in sorted(before)],
        "remaining_pairs": [list(p) for p in sorted(after)],
        "resolved_original_pairs": [list(p) for p in sorted(old_selected)],
        "residual_mappings": maps,
        "whole_body_replayed": True,
    }


def _effective(report, chapter):
    _need(report is not None and type(report.passed) is bool, "audit_unavailable")
    return effective_deterministic_audit_blockers(
        report,
        settings=base.reviews.get_quality_gates_config().l6_gate,
        chapter_number=chapter.chapter_number,
    )


async def _inspect(session, settings, args, *, lock=False):
    binding = await base._binding(
        session,
        project_id=args["project_id"],
        chapter_id=args["chapter_id"],
        failed_task_id=args["failed_task_id"],
        candidate_id=args["candidate_id"],
        lock=lock,
    )
    project, chapter, failed, original, parent, root, identity, old_wid, old_cid, current = binding
    wid, cid = prior._ids(identity)  # SAME ordinal-two IDs across both routes.
    await prior._inventory(session, binding, wid, cid)
    _need(args["previous_child_task_id"] == old_cid, "original_child_identity")
    child = await base._get(session, RewriteTaskModel, old_cid, lock=lock)
    old_wf = await base._get(session, WorkflowRunModel, old_wid, lock=lock)
    _need(
        child
        and old_wf
        and child.status == old_wf.status == "failed"
        and child.project_id == old_wf.project_id == project.id
        and child.trigger_source_id == old_wf.scope_id == chapter.id
        and old_wf.scope_type == "chapter"
        and child.parent_task_id == failed.id
        and child.rewrite_strategy == old_wf.workflow_type == base.STRATEGY
        and child.attempts == 1
        and child.metadata_json == old_wf.metadata_json,
        "old_claim_custody",
    )
    meta = child.metadata_json or {}
    old_receipt = meta.get("receipt") or {}
    _need(
        meta.get("binding") == identity == old_receipt.get("binding")
        and meta.get("lineage_root_task_id") == str(root.id)
        and type(meta.get("attempt_claimed")) is int
        and meta["attempt_claimed"] == 1
        and old_receipt.get("workflow_id") == str(old_wid)
        and old_receipt.get("child_task_id") == str(old_cid)
        and old_receipt.get("status") == "failed"
        and old_receipt.get("working_pointer_selected") is False
        and old_receipt.get("complete_text_invocations") == 1
        and old_receipt.get("error_type") == "ValueError"
        and old_receipt.get("error_code") == "candidate_patch_envelope_invalid"
        and not old_receipt.get("new_draft_id")
        and not old_receipt.get("patch"),
        "old_failure_not_schema_only",
    )
    mwf = await base._get(session, WorkflowRunModel, args["migration_workflow_id"], lock=lock)
    target = await base._get(session, ChapterDraftVersionModel, args["candidate_from_migration_id"])
    _need(
        mwf
        and target
        and mwf.project_id == target.project_id == project.id
        and mwf.scope_type == "chapter"
        and mwf.scope_id == target.chapter_id == chapter.id
        and mwf.workflow_type == migration.STRATEGY
        and mwf.status == "failed"
        and not target.is_current
        and target.promotion_state == "candidate",
        "migration_scope",
    )
    saved = mwf.metadata_json or {}
    plan, mr = saved.get("plan") or {}, saved.get("receipt") or {}
    _need(
        saved.get("original_child_task_id") == str(old_cid)
        and plan.get("schema") == migration.POLICY
        and plan.get("plan_sha256")
        == base._hash({k: v for k, v in plan.items() if k != "plan_sha256"})
        and plan.get("binding") == identity
        and plan.get("original_child_task_id") == str(old_cid)
        and plan.get("workflow_id") == str(mwf.id)
        and mr.get("status") == "failed"
        and mr.get("working_pointer_selected") is False
        and mr.get("original_failed_submission_revalidated") is False
        and all(mr.get(k) == v for k, v in plan.items() if k != "status")
        and mr.get("new_draft_id") == str(target.id)
        and target.promotion_metadata.get("retained_candidate_recovery")
        == {k: v for k, v in mr.items() if k != "new_draft_id"}
        and target.content_md == (mr.get("evidence") or {}).get("content_md")
        and text_hash(target.content_md) == mr.get("result_body_sha256")
        and plan.get("provider_calls") == 0
        and plan.get("grants_credit") is False
        and plan.get("adoption_authorized") is False
        and plan.get("custody")
        == {"child": prior._row_hash(child), "workflow": prior._row_hash(old_wf)},
        "migration_receipt_binding",
    )
    # New full audit is intentionally separate from the immutable capped audit.
    source, scenes, full_prepared, _system, _user, _transport, current_fence = await base._prepare(
        session, settings, binding
    )
    old_fence = meta.get("fence")
    _need(
        type(old_fence) is dict
        and old_fence == old_receipt.get("fence")
        and old_fence == plan.get("original_fence")
        and set(old_fence) == set(current_fence)
        and all(
            old_fence[k] == current_fence[k]
            for k in current_fence
            if k not in {"policy_sha256", "preflight_audit", "patch_catalog_sha256"}
        )
        and base._hash(source) == old_fence.get("source_sha256"),
        "source_or_executor_changed",
    )
    frozen = meta.get("prepared_patch")
    _need(
        type(frozen) is dict
        and frozen == legacy.prepare_candidate_patch(original.content_md, frozen.get("findings")),
        "frozen_prepared_changed",
    )
    _need(
        old_fence.get("patch_catalog_sha256") == frozen["catalog_sha256"]
        and current_fence.get("patch_catalog_sha256") == full_prepared["catalog_sha256"],
        "audit_catalog_binding",
    )
    old_req = meta.get("request") or {}
    req = frozen["request"]
    system, user, _ = base.prepare_source_complete_prompt(
        system_prompt=req["system_prompt"],
        user_prompt=req["user_prompt"],
        source_context=source,
        output_tokens=4096,
    )
    _need(
        base._hash(old_req) == old_receipt.get("request_sha256")
        and old_req.get("system_prompt") == system
        and old_req.get("user_prompt") == user
        and old_req.get("tools") == req["tools"]
        and old_req.get("tool_choice")
        == {"type": "function", "function": {"name": legacy.TOOL_NAME}}
        and old_req.get("max_tokens_override") == 4096
        and old_req.get("project_id") == str(project.id)
        and old_req.get("workflow_run_id") == str(old_wid)
        and old_req.get("logical_role") == "editor"
        and old_req.get("prompt_template") == "retained_candidate_paragraph_patch"
        and old_req.get("prompt_version") == "1.0"
        and old_req.get("sample_slot") == 0
        and old_req.get("operation_id")
        == execution_operation_id(
            "retained_candidate_patch", subject=str(root.id), revision=base._hash(identity)
        ),
        "old_request_changed",
    )
    completion = old_receipt.get("completion") or {}
    run = await base._get(session, LlmRunModel, _uuid(completion.get("llm_run_id"), "paid_id"))
    _need(
        run
        and run.project_id == project.id
        and run.workflow_run_id == old_wid
        and run.logical_role == "editor"
        and run.prompt_template == "retained_candidate_paragraph_patch"
        and run.prompt_version == "1.0"
        and target.llm_run_id == run.id,
        "paid_origin",
    )
    paid, physical = await prior._physical(
        session, settings, project, run, old_req, completion, identity["scope_id"]
    )
    replayed, proposal, extraction, application = migration._extract(
        paid, source, user, frozen, original.content_md
    )
    _need(
        replayed == target.content_md
        and plan.get("physical") == physical
        and plan.get("proposal") == proposal
        and plan.get("extraction") == extraction
        and plan.get("application") == application,
        "migration_replay_changed",
    )
    # Validate full report custody of the FAILED candidate, without upgrading it.
    evidence = mr.get("evidence") or {}
    _need(
        evidence.get("body_sha256") == text_hash(target.content_md)
        and evidence.get("gate_outcome") == "blocked"
        and not evidence.get("duplicate_findings")
        and not evidence.get("quality_retrofit_findings")
        and evidence.get("quality_gate_violations")
        and all(v.get("code") == legacy.FINDING_CODE for v in evidence["quality_gate_violations"])
        and mr.get("effects", {}).get("passed") is True,
        "migration_not_copy_only",
    )
    gate = await base._get(
        session,
        ChapterQualityReportModel,
        _uuid(evidence.get("gate_report_id"), "migration_gate_id"),
    )
    _need(
        gate
        and gate.chapter_id == chapter.id
        and report_grades_text(gate.report_json, target.content_md)
        and gate.report_json.get("rewrite_evaluation_id") == evidence.get("gate_evaluation_id")
        and set(gate.report_json.get("blocking_codes", ())) <= {legacy.FINDING_CODE},
        "migration_gate_binding",
    )
    report, band = base._audit(project, chapter, scenes, target.content_md, settings)
    remaining = _effective(report, chapter)
    _need(remaining and all(f.code == legacy.FINDING_CODE for f in remaining), "not_copy_only")
    _need(
        band.hard_min <= base.reviews.count_words(target.content_md) <= band.hard_max,
        "target_length",
    )
    _need(
        not await base.reviews._collect_post_assembly_duplicate_findings(
            session, project=project, chapter=chapter, content_md=target.content_md
        ),
        "target_duplicates",
    )
    _need(
        base.reviews.audit_persisted_story_effect_realization(
            project, chapter, target.content_md, scenes=scenes
        ).get("passed")
        is True,
        "target_effects",
    )
    progress = _progress(
        original.content_md,
        target.content_md,
        scenes,
        full_prepared["findings"],
        remaining,
        frozen,
        application,
    )
    prepared = native.prepare_native_candidate_patch(target.content_md, remaining)
    req = prepared["request"]
    system, user, transport = prepare_source_complete_prompt(
        system_prompt=req["system_prompt"] + "\n\n" + FEEDBACK,
        user_prompt=req["user_prompt"],
        source_context=source,
        output_tokens=4096,
        tools=req["tools"],
    )
    proof = {
        "original_fence": deepcopy(old_fence),
        "current_full_fence": current_fence,
        "old_child_sha256": prior._row_hash(child),
        "old_workflow_sha256": prior._row_hash(old_wf),
        "migration_workflow_sha256": prior._row_hash(mwf),
        "candidate_sha256": prior._row_hash(target),
        "migration_gate_sha256": prior._row_hash(gate),
        "first_paid": physical,
        "original_catalog_sha256": full_prepared["catalog_sha256"],
        "remaining_catalog_sha256": prepared["catalog_sha256"],
        "code_sha256": {
            Path(m.__file__).name: text_hash(Path(m.__file__).read_text())
            for m in (native, legacy, base, prior, migration, audit_source, transport_source)
        },
        "executor_sha256": text_hash(Path(__file__).read_text()),
    }
    return binding, target, scenes, prepared, progress, proof, system, user, transport, wid, cid


def _envelope(status, wid, cid, receipt, *, replay=False):
    return {
        "schema": POLICY,
        "status": status,
        "replay": replay,
        "workflow_id": str(wid),
        "child_task_id": str(cid),
        "receipt": deepcopy(receipt),
    }


async def continue_migrated_candidate_recovery(
    session,
    settings,
    *,
    project_id: UUID,
    chapter_id: UUID,
    failed_task_id: UUID,
    candidate_id: UUID,
    previous_child_task_id: UUID,
    migration_workflow_id: UUID,
    candidate_from_migration_id: UUID,
    requested_by: str,
    execute: bool = False,
) -> dict[str, Any]:
    """Inspect, or claim the shared final ordinal before a single native dispatch."""
    _need(
        type(execute) is bool and type(requested_by) is str and 0 < len(requested_by.strip()) <= 64,
        "arguments",
    )
    _need(
        not session.new
        and not session.dirty
        and not session.deleted
        and not session.in_nested_transaction()
        and session.sync_session._trans_context_manager is None,
        "clean_session_required",
    )
    args = dict(
        project_id=project_id,
        chapter_id=chapter_id,
        failed_task_id=failed_task_id,
        candidate_id=candidate_id,
        previous_child_task_id=previous_child_task_id,
        migration_workflow_id=migration_workflow_id,
        candidate_from_migration_id=candidate_from_migration_id,
    )
    with session.no_autoflush:
        checked = await _inspect(session, settings, args, lock=execute)
        binding, target, scenes, prepared, progress, proof, system, user, transport, wid, cid = (
            checked
        )
        project, chapter, _failed, _original, parent, root, identity, *_ = binding
        child = await base._get(session, RewriteTaskModel, cid, lock=execute)
        workflow = await base._get(session, WorkflowRunModel, wid, lock=execute)
        if child is not None or workflow is not None:
            _need(
                child
                and workflow
                and child.project_id == workflow.project_id == project_id
                and child.trigger_source_id == workflow.scope_id == chapter_id
                and workflow.scope_type == "chapter"
                and child.parent_task_id == previous_child_task_id
                and child.status == workflow.status
                and child.status in {"running", "failed", "completed"}
                and child.rewrite_strategy == workflow.workflow_type == STRATEGY
                and child.attempts == 1
                and child.metadata_json == workflow.metadata_json,
                "ordinal_two_already_claimed",
            )
            saved = child.metadata_json or {}
            _need(
                saved.get("proof") == proof
                and saved.get("binding") == identity
                and saved.get("candidate_id") == str(candidate_from_migration_id)
                and saved.get("migration_workflow_id") == str(migration_workflow_id)
                and saved.get("ordinal") == saved.get("max_batches") == 2
                and saved.get("attempt_claimed") == 1,
                "replay_binding_changed",
            )
            result = deepcopy(saved["receipt"])
            if child.status == "completed":
                _need(
                    str(binding[-1].id) == result.get("new_draft_id")
                    and text_hash(binding[-1].content_md) == result.get("result_body_sha256")
                    and binding[-1].promotion_state == "candidate",
                    "completed_pointer_changed",
                )
            else:
                _need(binding[-1].id == parent.id, "parent_changed")
            return _envelope(child.status, wid, cid, result, replay=True)
        _need(
            binding[-1].id == parent.id and parent.promotion_state == "candidate", "parent_changed"
        )
        request = llm.LLMCompletionRequest(
            logical_role="editor",
            project_id=project_id,
            workflow_run_id=wid,
            system_prompt=system,
            user_prompt=user,
            fallback_response="Patch unavailable; no replacement.",
            prompt_template="migrated_candidate_patch_continuation",
            prompt_version="1.0",
            max_tokens_override=4096,
            tools=prepared["request"]["tools"],
            tool_choice="required",
            operation_id=execution_operation_id(
                "retained_candidate_patch", subject=str(root.id), revision="ordinal:2"
            ),
            sample_slot=0,
            metadata={"proof": proof, "progress": progress, "source_transport": transport},
        )
        # An orphan paid/cursor claim from ANY route blocks a new task claim.
        from bestseller.infra.db.models import ExecutionAttemptModel, ExecutionOperationModel
        import hashlib

        key = hashlib.sha256(
            f"{identity['scope_id']}\n{request.operation_id}:sample:0".encode()
        ).hexdigest()
        _need(
            await session.get(ExecutionOperationModel, key) is None
            and await session.scalar(
                select(ExecutionAttemptModel.id)
                .where(ExecutionAttemptModel.operation_key == key)
                .limit(1)
            )
            is None,
            "ordinal_two_paid_orphan",
        )
        plan = {
            "schema": POLICY,
            "status": "prepared",
            "binding": identity,
            "proof": proof,
            "candidate_id": str(candidate_from_migration_id),
            "migration_workflow_id": str(migration_workflow_id),
            "progress": progress,
            "workflow_id": str(wid),
            "child_task_id": str(cid),
            "request_sha256": base._hash(request.model_dump(mode="json")),
            "source_transport": transport,
            "provider_calls": 0,
            "database_writes": 0,
            "formal_review_required": True,
            "adoption_authorized": False,
            "third_batch_allowed": False,
        }
        if not execute:
            return _envelope("prepared", wid, cid, plan)
    claim = {
        "binding": identity,
        "proof": proof,
        "candidate_id": str(candidate_from_migration_id),
        "migration_workflow_id": str(migration_workflow_id),
        "lineage_root_task_id": str(root.id),
        "ordinal": 2,
        "claimed_batches": 2,
        "max_batches": 2,
        "attempt_claimed": 1,
        "request": request.model_dump(mode="json"),
        "prepared_patch": prepared,
        "receipt": plan,
    }
    child = RewriteTaskModel(
        id=cid,
        project_id=project_id,
        parent_task_id=previous_child_task_id,
        trigger_type="migrated_candidate_continuation",
        trigger_source_id=chapter_id,
        rewrite_strategy=STRATEGY,
        status="running",
        attempts=1,
        priority=1,
        instructions=FEEDBACK,
        context_required=["exact_migrated_candidate", "current_adopted_sources"],
        metadata_json=deepcopy(claim),
    )
    workflow = WorkflowRunModel(
        id=wid,
        project_id=project_id,
        scope_type="chapter",
        scope_id=chapter_id,
        workflow_type=STRATEGY,
        status="running",
        requested_by=requested_by,
        current_step="ordinal_2_claimed",
        metadata_json=deepcopy(claim),
    )
    session.add_all([child, workflow])
    await session.commit()
    result = deepcopy(plan)
    result.pop("provider_calls", None)  # Invocation count is not a provider-call claim.
    result.update(status="running", complete_text_invocations=0, database_writes=True)
    body = None
    completion = None
    interrupted = None

    async def refresh():
        with session.no_autoflush:
            fresh = await _inspect(session, settings, args, lock=True)
        _need(fresh[5] == proof and fresh[0][-1].id == fresh[0][4].id, "source_or_parent_changed")
        return fresh

    try:
        await refresh()
        result["complete_text_invocations"] = 1
        with bind_execution_scope(identity["scope_id"]):
            completion = await llm.complete_text(session, settings, request)
        result["completion"] = {
            "content": completion.content,
            "tool_calls": deepcopy(completion.tool_calls),
            "raw_message": deepcopy(completion.raw_message),
            "llm_run_id": str(completion.llm_run_id) if completion.llm_run_id else None,
            "provider": completion.provider,
            "finish_reason": completion.finish_reason,
        }
        child = await base._get(session, RewriteTaskModel, cid, lock=True)
        workflow = await base._get(session, WorkflowRunModel, wid)
        child.metadata_json = workflow.metadata_json = {**claim, "receipt": deepcopy(result)}
        workflow.current_step = "paid_return_retained"
        await session.commit()
        _need(
            not completion.fallback_used
            and completion.provider not in {"mock", "fallback", "test"}
            and completion.llm_run_id is not None
            and completion.finish_reason in {"stop", "tool_calls"},
            "completion_unavailable",
        )
        body, patch = native.apply_native_candidate_patch(target.content_md, prepared, completion)
        result.update(patch=patch, result_body_sha256=text_hash(body))
        await refresh()
        run = await base._get(session, LlmRunModel, completion.llm_run_id)
        _need(
            run
            and run.project_id == project_id
            and run.workflow_run_id == wid
            and run.logical_role == "editor",
            "second_paid_origin",
        )
        # Same full route/request/root/attempt/transport verification as ordinal one;
        # the native function name differs, so only this exact verified count is used.
        _, result["paid_proof"] = await prior._physical(
            session,
            settings,
            project,
            run,
            claim["request"],
            result["completion"],
            identity["scope_id"],
        )
        evidence = await base._evidence(session, settings, project, chapter, scenes, body)
        fresh = await refresh()
        project, chapter, _failed, _original, parent, *_ = fresh[0]
        effects = base.reviews.audit_persisted_story_effect_realization(
            project, chapter, body, scenes=fresh[2]
        )
        passed = (
            evidence.outcome == "ok"
            and evidence.gate_receipt.report is not None
            and not evidence.duplicates
            and not evidence.audit_blockers
            and not evidence.retrofit
            and not any(f.code == legacy.FINDING_CODE for f in evidence.audit_report.findings)
            and effects.get("passed") is True
        )
        result.update(
            status="completed" if passed else "failed",
            evidence=evidence.receipt(),
            effects=effects,
            working_pointer_selected=passed,
        )
        draft = await base._save_draft(
            session, project, chapter, parent, body, completion.llm_run_id, result, current=passed
        )
        result["new_draft_id"] = str(draft.id)
    except BaseException as exc:
        await session.rollback()
        if not isinstance(exc, Exception):
            interrupted = exc
            result.pop("new_draft_id", None)
            result.update(
                interrupted=True,
                error_code="migrated_candidate_continuation_interrupted",
                candidate_persisted=False,
                uncommitted_candidate_rolled_back=True,
                financial_operations_modified=False,
            )
            if "evidence" in result:
                result["gate_evidence_rolled_back"] = True
        result.update(
            status="failed", working_pointer_selected=False, error_type=type(exc).__name__
        )
        if isinstance(exc, ValueError) and str(exc).startswith(
            (
                "migrated_candidate_",
                "candidate_patch_",
                "native_candidate_patch:",
                "retained_review_",
            )
        ):
            result["error_code"] = str(exc)[:256]
        if body is not None and interrupted is None:
            from bestseller.infra.db.models import ChapterModel, ProjectModel

            project = await base._get(session, ProjectModel, project_id)
            chapter = await base._get(session, ChapterModel, chapter_id, lock=True)
            parent = await base._get(session, ChapterDraftVersionModel, UUID(identity["parent_id"]))
            draft = await base._save_draft(
                session,
                project,
                chapter,
                parent,
                body,
                completion.llm_run_id if completion else None,
                result,
            )
            result["new_draft_id"] = str(draft.id)
    child = await base._get(session, RewriteTaskModel, cid, lock=True)
    workflow = await base._get(session, WorkflowRunModel, wid)
    _need(child and workflow, "claim_missing")
    child.status = workflow.status = result["status"]
    child.metadata_json = workflow.metadata_json = {**claim, "receipt": deepcopy(result)}
    workflow.current_step = (
        "requires_new_body_review"
        if result["status"] == "completed"
        else "final_batch_failed_no_retry"
    )
    if result["status"] == "failed":
        child.error_log = "Final migrated-candidate native batch failed; no further batch."
    await session.commit()
    if interrupted is not None:
        raise interrupted
    return _envelope(result["status"], wid, cid, result)
