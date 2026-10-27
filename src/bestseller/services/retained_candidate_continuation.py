"""One progress-derived ordinal-two patch; immutable ordinal-one custody.

Inspection is read-only. Execution owns commits on a clean session and claims
the final batch before the gateway. Neither retries nor policy changes mint a
third batch. A successful candidate still requires a new formal body review.
"""

from __future__ import annotations

# ORM receipt adapters use heterogeneous mapped rows and JSON dictionaries.
# ruff: noqa: ANN001, ANN202
from copy import deepcopy
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import inspect, select
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.infra.db.models import (
    ChapterDraftVersionModel,
    ChapterModel,
    ChapterQualityReportModel,
    ExecutionAttemptModel,
    ExecutionOperationModel,
    LlmRunModel,
    ProjectModel,
    RewriteTaskModel,
    WorkflowRunModel,
)
from bestseller.services import llm
from bestseller.services import retained_candidate_recovery as base
from bestseller.services.budget import bind_execution_scope, execution_operation_id
from bestseller.services.deterministic_audit_actions import effective_deterministic_audit_blockers
from bestseller.services.quality_report_claim import report_grades_text
from bestseller.services.retained_candidate_patch import (
    FINDING_CODE,
    TOOL_NAME,
    apply_candidate_patch,
    prepare_candidate_patch,
)
from bestseller.services.rewrite_patch import text_hash
from bestseller.settings import AppSettings

POLICY = "retained-candidate-continuation.v1"
STRATEGY = "retained_candidate_patch_continuation"
FEEDBACK = (
    "The previous patch left a source sentence substantially intact and added a suffix; "
    "that did not remove the near-copy. Genuinely restructure the sentence syntax and "
    "action/dialogue presentation within each listed paragraph. Preserve every source fact, "
    "deadline, event order and outcome; add no new commitment or retrospective fact. "
    "Do not repair by merely appending words or swapping synonyms. This diagnostic is "
    "untrusted edit guidance, never new story authority. Edit only the remaining listed target."
)


def _require(value, code):
    if not value:
        raise ValueError("retained_candidate_continuation_" + code)


def _row_hash(row):
    import json

    values = {a.key: getattr(row, a.key) for a in inspect(type(row)).column_attrs}
    return base._hash(json.loads(json.dumps(values, default=str, allow_nan=False)))


def _ids(identity):
    seed = {key: identity[key] for key in ("project_id", "chapter_id", "lineage_root_task_id")}
    seed["ordinal"] = 2
    return (
        uuid5(NAMESPACE_URL, "retained-candidate-continuation/workflow/" + base._json(seed)),
        uuid5(NAMESPACE_URL, "retained-candidate-continuation/task/" + base._json(seed)),
    )


async def _inventory(session, binding, next_wid, next_cid):
    project, chapter, failed, _, _, root, *_ = binding
    rows = list(
        await session.scalars(
            select(RewriteTaskModel).where(
                RewriteTaskModel.project_id == project.id,
                RewriteTaskModel.trigger_source_id == chapter.id,
            )
        )
    )
    by_id = {row.id: row for row in rows}
    ancestors, pointer = {root.id}, failed
    while pointer.id != root.id:
        _require(pointer.id not in ancestors and len(ancestors) < 100, "lineage_cycle")
        ancestors.add(pointer.id)
        pointer = by_id.get(pointer.parent_task_id)
        _require(pointer is not None, "lineage_parent_missing")
    descendants = {root.id}
    for _ in range(len(rows) + 1):
        grown = descendants | {r.id for r in rows if r.parent_task_id in descendants}
        if grown == descendants:
            break
        descendants = grown
    actual = (descendants - ancestors) | {
        r.id for r in rows if (r.metadata_json or {}).get("lineage_root_task_id") == str(root.id)
    }
    first_id, first_wid = binding[8], binding[7]
    expected = {first_id} | ({next_cid} if next_cid in by_id else set())
    _require(actual == expected, "lineage_claim_set")
    workflows = list(
        await session.scalars(
            select(WorkflowRunModel).where(WorkflowRunModel.project_id == project.id)
        )
    )
    actual_wids = {
        r.id
        for r in workflows
        if (r.metadata_json or {}).get("lineage_root_task_id") == str(root.id)
    }
    _require(
        actual_wids == {first_wid} | ({next_wid} if next_cid in by_id else set()),
        "lineage_workflow_set",
    )


def _payload(settings, run, kind):
    import hashlib
    import json

    _require(settings.artifact_store.mode == "local", "local_physical_store_required")
    root = Path(settings.artifact_store.local_dir).expanduser().resolve()
    ref = getattr(run, kind + "_payload_ref")
    _require(isinstance(ref, str) and ref, "physical_ref_missing")
    path = (root / ref).resolve()
    _require(path.is_relative_to(root), "physical_path_outside_store")
    raw = path.read_bytes()

    def unique(pairs):
        result = {}
        for key, value in pairs:
            _require(key not in result, "physical_duplicate_key")
            result[key] = value
        return result

    value = json.loads(
        raw,
        object_pairs_hook=unique,
        parse_constant=lambda _: (_ for _ in ()).throw(ValueError("physical_nonfinite")),
    )
    _require(
        value.get("schema_version") == "llm-payload.v1"
        and value.get("kind") == kind
        and value.get("replayable") is True
        and value.get("replayability_reasons") == [],
        "physical_not_replayable",
    )
    for key, expected in (
        ("llm_run_id", str(run.id)),
        ("workflow_run_id", str(run.workflow_run_id)),
        ("project_id", str(run.project_id)),
        ("logical_role", run.logical_role),
    ):
        _require(value.get(key) == expected, "physical_identity")
    return value, hashlib.sha256(raw).hexdigest()


async def _physical(session, settings, project, run, request_dict, saved_completion, scope):
    from bestseller.services import retained_review_input_proof as proof

    _, request_sha = _payload(settings, run, "request")
    response, response_sha = _payload(settings, run, "response")
    request = proof._retained_request(settings, run)
    claimed = llm.LLMCompletionRequest.model_validate(request_dict)
    for field in (
        "project_id",
        "workflow_run_id",
        "step_run_id",
        "logical_role",
        "system_prompt",
        "user_prompt",
        "prompt_template",
        "prompt_version",
        "model_tier",
        "model_catalog_key",
        "temperature_override",
        "max_tokens_override",
        "cache_system",
        "tools",
        "tool_choice",
    ):
        _require(getattr(request, field) == getattr(claimed, field), "physical_request_contract")
    catalog, _ = proof._fresh_catalog()
    role = proof._fresh_role(settings, project, request, catalog)
    # The parent completion authorization remains MiniMax-only, on its current
    # configured route; receipt validation cannot broaden it to another model.
    _require("minimax" in role.model.lower(), "minimax_route_required")
    digest = llm._effective_input_hash(request, role)
    paid, key = await proof._physical_return(
        session, run, scope=scope, expected_hash=digest, expected_model=role.model
    )
    import hashlib

    _require(
        claimed.operation_id is not None
        and claimed.sample_slot == 0
        and key == hashlib.sha256(f"{scope}\n{claimed.operation_id}:sample:0".encode()).hexdigest(),
        "physical_operation_identity",
    )
    attempts = list(
        await session.scalars(
            select(ExecutionAttemptModel).where(ExecutionAttemptModel.operation_key == key)
        )
    )
    receipt_attempts = (run.metadata_json or {}).get("execution_receipt", {}).get("attempts", [])
    _require(
        len(attempts) == len(receipt_attempts) == 1
        and {a.id for a in attempts} == {a.get("attempt_id") for a in receipt_attempts}
        and all(a.status == "completed" for a in attempts),
        "physical_attempt_set",
    )
    content = response.get("content") or {}
    _require(
        content.get("truncated") is False
        and content.get("text") == paid.content
        and content.get("original_chars") == len(paid.content),
        "physical_content",
    )
    for field in ("tool_calls", "raw_message"):
        value = getattr(paid, field)
        record = (response.get("structured_payload_receipts") or {}).get(field) or {}
        encoded = base._json(value)
        _require(
            response.get(field) == value
            and record.get("status") == "retained"
            and record.get("encoding") == "json-utf8"
            and record.get("truncated") is False
            and record.get("original_chars") == len(encoded)
            and type(record.get("limit_chars")) is int
            and record["limit_chars"] >= len(encoded)
            and record.get("sha256") == text_hash(encoded),
            "physical_structured_field",
        )
    for field in (
        "finish_reason",
        "provider",
        "input_tokens",
        "output_tokens",
        "fallback_used",
        "fallback_source",
    ):
        _require(response.get(field) == getattr(paid, field), "physical_response_field")
    _require(
        response.get("model") == paid.model_name and response.get("tool_call_count") == 1,
        "physical_response_model",
    )
    expected = {
        "content": paid.content,
        "tool_calls": paid.tool_calls,
        "raw_message": paid.raw_message,
        "llm_run_id": str(run.id),
        "provider": paid.provider,
        "finish_reason": paid.finish_reason,
    }
    _require(
        saved_completion == expected and paid.finish_reason in {"stop", "tool_calls"},
        "retained_completion_mismatch",
    )
    operation = await session.get(ExecutionOperationModel, key)
    transport_key = hashlib.sha256(
        f"transport-result.v1\n{scope}\n{attempts[0].id}".encode()
    ).hexdigest()
    transport = await session.get(ExecutionOperationModel, transport_key)
    return paid, {
        "request_file_sha256": request_sha,
        "response_file_sha256": response_sha,
        "effective_input_sha256": digest,
        "root_row_sha256": _row_hash(operation),
        "transport_row_sha256": _row_hash(transport),
        "llm_row_sha256": _row_hash(run),
        "attempt_rows_sha256": base._hash([_row_hash(a) for a in attempts]),
    }


def _arguments(paid):
    calls = paid.tool_calls
    _require(
        isinstance(calls, list) and len(calls) == 1 and isinstance(calls[0], dict), "tool_count"
    )
    call = calls[0]
    function = call.get("function")
    _require(
        call.get("type") == "function"
        and isinstance(function, dict)
        and function.get("name") == TOOL_NAME
        and isinstance(function.get("arguments"), str),
        "tool_contract",
    )
    return function["arguments"]


def _progress(original_body, first_prepared, first_arguments, first_receipt, target_body, findings):
    body, replayed = apply_candidate_patch(original_body, first_prepared, first_arguments)
    _require(
        body == target_body
        and replayed == first_receipt["patch"]
        and text_hash(body) == first_receipt.get("result_body_sha256"),
        "first_patch_replay",
    )
    initial = first_prepared["targets"]
    remaining = prepare_candidate_patch(target_body, findings)
    _require(
        type(first_receipt.get("target_count")) is int
        and first_receipt["target_count"] == len(initial)
        and len(initial) >= 2
        and 0 < len(remaining["targets"]) < len(initial)
        and 0 < len(findings) < len(first_prepared["findings"]),
        "strict_progress_required",
    )
    mapped = []
    for target in remaining["targets"]:
        matches = [
            p
            for p in replayed["targets"]
            if p["result_start"] == target["start"]
            and p["result_end"] == target["end"]
            and p["replacement_sha256"] == target["original_sha256"]
        ]
        _require(len(matches) == 1, "remaining_scope_not_original")
        mapped.append(matches[0]["target_id"])
    _require(
        len(set(mapped)) == len(mapped) and set(mapped) < {t["target_id"] for t in initial},
        "remaining_scope_not_strict_subset",
    )
    return remaining, {
        "initial_targets": len(initial),
        "remaining_targets": len(remaining["targets"]),
        "initial_findings": len(first_prepared["findings"]),
        "remaining_findings": len(findings),
        "natural_batch_bound": len(initial),
        "max_supported_batches": min(2, len(initial)),
        "max_batches": min(2, len(initial)),
        "claimed_batches_before": 1,
        "ordinal": 2,
        "remaining_original_target_ids": mapped,
        "first_patch_receipt_sha256": replayed["receipt_sha256"],
        "whole_body_replayed": True,
        "third_batch_allowed": False,
    }


async def _inspect(session, settings, args, *, lock=False):
    old = await base._get(session, RewriteTaskModel, args["failed_task_id"], lock=lock)
    _require(old is not None, "original_task_missing")
    initial_id = UUID((old.metadata_json or {})["candidate_chapter_draft_id"])
    binding = await base._binding(
        session,
        project_id=args["project_id"],
        chapter_id=args["chapter_id"],
        failed_task_id=old.id,
        candidate_id=initial_id,
        lock=lock,
    )
    project, chapter, failed, initial, _parent, root, identity, first_wid, first_id, current = (
        binding
    )
    wid, cid = _ids(identity)
    await _inventory(session, binding, wid, cid)
    _require(args["previous_child_task_id"] == first_id, "first_child_identity")
    first = await base._get(session, RewriteTaskModel, first_id, lock=lock)
    workflow = await base._get(session, WorkflowRunModel, first_wid)
    _require(
        first
        and workflow
        and first.status == workflow.status == "failed"
        and first.attempts == 1
        and first.parent_task_id == failed.id
        and first.project_id == workflow.project_id == project.id
        and first.trigger_source_id == workflow.scope_id == chapter.id
        and workflow.scope_type == "chapter"
        and first.rewrite_strategy == base.STRATEGY
        and workflow.workflow_type == base.STRATEGY
        and first.metadata_json == workflow.metadata_json,
        "first_claim_custody",
    )
    meta = first.metadata_json or {}
    receipt = meta.get("receipt") or {}
    _require(
        meta.get("binding") == receipt.get("binding") == identity
        and meta.get("lineage_root_task_id") == str(root.id)
        and type(meta.get("attempt_claimed")) is int
        and meta["attempt_claimed"] == 1
        and receipt.get("workflow_id") == str(first_wid)
        and receipt.get("child_task_id") == str(first_id)
        and receipt.get("status") == "failed"
        and receipt.get("working_pointer_selected") is False
        and receipt.get("complete_text_invocations") == 1,
        "first_receipt_binding",
    )
    _require(
        meta["request"].get("operation_id")
        == execution_operation_id(
            "retained_candidate_patch", subject=str(root.id), revision=base._hash(identity)
        ),
        "first_operation_identity",
    )
    target = await base._get(session, ChapterDraftVersionModel, args["candidate_id"])
    _require(
        target
        and target.project_id == project.id
        and target.chapter_id == chapter.id
        and str(target.id) == receipt.get("new_draft_id")
        and not target.is_current
        and target.promotion_state == "candidate"
        and target.id != initial.id
        and target.content_md == (receipt.get("evidence") or {}).get("content_md")
        and text_hash(target.content_md) == receipt.get("result_body_sha256")
        and str(target.llm_run_id) == (receipt.get("completion") or {}).get("llm_run_id"),
        "target_binding",
    )
    first_run = await base._get(session, LlmRunModel, target.llm_run_id)
    _require(
        first_run
        and first_run.project_id == project.id
        and first_run.workflow_run_id == first_wid
        and first_run.logical_role == "editor"
        and first_run.prompt_template == "retained_candidate_paragraph_patch"
        and first_run.prompt_version == "1.0",
        "first_paid_binding",
    )
    _require(base._hash(meta["request"]) == receipt.get("request_sha256"), "first_request_digest")
    first_prepared = meta["prepared_patch"]
    _require(
        first_prepared["request"]["user_prompt"] in meta["request"]["user_prompt"]
        and first_prepared["request"]["system_prompt"] in meta["request"]["system_prompt"]
        and first_prepared["request"]["tools"] == meta["request"]["tools"],
        "first_prepared_request",
    )
    paid, physical = await _physical(
        session,
        settings,
        project,
        first_run,
        meta["request"],
        receipt["completion"],
        identity["scope_id"],
    )
    prepared_data = await base._prepare(session, settings, binding)
    _require(
        prepared_data[-1] == meta.get("fence") == receipt.get("fence"), "original_source_changed"
    )
    source, scenes = await base._sources(session, settings, project, chapter, target)
    report, band = base._audit(project, chapter, scenes, target.content_md, settings)
    _require(report is not None and isinstance(report.passed, bool), "audit_unavailable")
    effective = effective_deterministic_audit_blockers(
        report,
        settings=base.reviews.get_quality_gates_config().l6_gate,
        chapter_number=chapter.chapter_number,
    )
    _require(effective and all(f.code == FINDING_CODE for f in effective), "not_copy_only")
    evidence = receipt.get("evidence") or {}
    _require(
        evidence.get("body_sha256") == text_hash(target.content_md)
        and evidence.get("gate_outcome") == "blocked"
        and not evidence.get("duplicate_findings")
        and not evidence.get("quality_retrofit_findings")
        and receipt.get("effects", {}).get("passed") is True
        and evidence.get("quality_gate_violations")
        and all(v.get("code") == FINDING_CODE for v in evidence["quality_gate_violations"]),
        "first_hard_blocker",
    )
    gate = await base._get(session, ChapterQualityReportModel, UUID(evidence["gate_report_id"]))
    _require(
        gate
        and gate.chapter_id == chapter.id
        and report_grades_text(gate.report_json, target.content_md)
        and gate.report_json.get("rewrite_evaluation_id") == evidence.get("gate_evaluation_id")
        and set(gate.report_json.get("blocking_codes", ())) <= {FINDING_CODE},
        "first_gate_binding",
    )
    gate_blockers = effective_deterministic_audit_blockers(
        {"findings": gate.report_json.get("violations", [])},
        settings=base.reviews.get_quality_gates_config().l6_gate,
        chapter_number=chapter.chapter_number,
    )
    _require(all(v["code"] == FINDING_CODE for v in gate_blockers), "first_gate_hard_blocker")
    words = base.reviews.count_words(target.content_md)
    _require(band.hard_min <= words <= band.hard_max, "target_length")
    _require(
        not await base.reviews._collect_post_assembly_duplicate_findings(
            session, project=project, chapter=chapter, content_md=target.content_md
        ),
        "target_duplicates",
    )
    effects = base.reviews.audit_persisted_story_effect_realization(
        project, chapter, target.content_md, scenes=scenes
    )
    _require(effects.get("passed") is True, "target_effects")
    prepared, progress = _progress(
        initial.content_md,
        meta["prepared_patch"],
        _arguments(paid),
        receipt,
        target.content_md,
        effective,
    )
    proof = {
        "original_fence": prepared_data[-1],
        "target_source_sha256": base._hash(source),
        "first_child_row_sha256": _row_hash(first),
        "first_workflow_row_sha256": _row_hash(workflow),
        "target_row_sha256": _row_hash(target),
        "first_paid": physical,
        "first_gate_row_sha256": _row_hash(gate),
        "executor_sha256": text_hash(Path(__file__).read_text()),
    }
    return binding, target, source, scenes, prepared, progress, proof, wid, cid, current, report


async def continue_retained_candidate_recovery(
    session: AsyncSession,
    settings: AppSettings,
    *,
    project_id: UUID,
    chapter_id: UUID,
    failed_task_id: UUID,
    previous_child_task_id: UUID,
    candidate_id: UUID,
    requested_by: str,
    execute: bool = False,
) -> dict[str, Any]:
    """Read-only plan, or final ordinal-two claim and one finite patch invocation."""
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
        "previous_child_task_id": previous_child_task_id,
        "candidate_id": candidate_id,
    }
    with session.no_autoflush:
        old = await base._get(session, RewriteTaskModel, failed_task_id)
        _require(old is not None, "original_task_missing")
        initial_id = UUID((old.metadata_json or {})["candidate_chapter_draft_id"])
        early = await base._binding(
            session,
            project_id=project_id,
            chapter_id=chapter_id,
            failed_task_id=failed_task_id,
            candidate_id=initial_id,
            lock=execute,
        )
        early_wid, early_cid = _ids(early[6])
        await _inventory(session, early, early_wid, early_cid)
        early_child = await base._get(session, RewriteTaskModel, early_cid)
        early_workflow = await base._get(session, WorkflowRunModel, early_wid)
        if early_child is not None or early_workflow is not None:
            _require(
                early_child
                and early_workflow
                and early_child.project_id == early_workflow.project_id == project_id
                and early_child.trigger_source_id == early_workflow.scope_id == chapter_id
                and early_workflow.scope_type == "chapter"
                and early_child.parent_task_id == previous_child_task_id
                and early_child.status == early_workflow.status
                and early_child.status in {"running", "failed", "completed"}
                and early_child.rewrite_strategy == early_workflow.workflow_type == STRATEGY
                and early_child.attempts == 1
                and early_child.metadata_json == early_workflow.metadata_json,
                "claim_custody",
            )
            saved = early_child.metadata_json or {}
            _require(
                saved.get("binding") == early[6]
                and saved.get("candidate_id") == str(candidate_id)
                and saved.get("ordinal")
                == saved.get("claimed_batches")
                == saved.get("max_batches")
                == 2
                and saved.get("attempt_claimed") == 1,
                "replay_binding",
            )
            first = await base._get(session, RewriteTaskModel, previous_child_task_id)
            first_workflow = await base._get(session, WorkflowRunModel, early[7])
            target = await base._get(session, ChapterDraftVersionModel, candidate_id)
            _require(
                first
                and first_workflow
                and target
                and previous_child_task_id == early[8]
                and _row_hash(first) == saved["proof"]["first_child_row_sha256"]
                and _row_hash(first_workflow) == saved["proof"]["first_workflow_row_sha256"]
                and _row_hash(target) == saved["proof"]["target_row_sha256"],
                "replay_predecessor_changed",
            )
            if early_child.status != "completed":
                _require(early[-1].id == early[4].id, "parent_changed")
                return {
                    "schema": POLICY,
                    "status": early_child.status,
                    "workflow_id": str(early_wid),
                    "child_task_id": str(early_cid),
                    "replay": True,
                    "provider_calls": 0,
                    "database_writes": 0,
                    "receipt": deepcopy(saved["receipt"]),
                }
        inspected = await _inspect(session, settings, args, lock=execute)
        binding, target, source, scenes, prepared, progress, proof, wid, cid, current, report = (
            inspected
        )
        project, chapter, _old, _initial, parent, root, identity, *_ = binding
        child = await base._get(session, RewriteTaskModel, cid)
        workflow = await base._get(session, WorkflowRunModel, wid)
        if child is not None or workflow is not None:
            _require(
                child
                and workflow
                and child.parent_task_id == previous_child_task_id
                and child.project_id == workflow.project_id == project_id
                and child.trigger_source_id == chapter_id
                and child.rewrite_strategy == workflow.workflow_type == STRATEGY
                and child.status == workflow.status
                and child.status in {"running", "failed", "completed"}
                and child.attempts == 1
                and child.metadata_json == workflow.metadata_json,
                "claim_custody",
            )
            meta = child.metadata_json or {}
            _require(
                meta.get("proof") == proof
                and meta.get("ordinal") == 2
                and meta.get("candidate_id") == str(candidate_id)
                and meta.get("binding") == identity,
                "replay_binding",
            )
            receipt = deepcopy(meta["receipt"])
            if child.status == "completed":
                _require(
                    str(current.id) == receipt.get("new_draft_id")
                    and text_hash(current.content_md) == receipt.get("result_body_sha256")
                    and current.promotion_state == "candidate",
                    "completed_pointer_changed",
                )
            else:
                _require(current.id == parent.id, "parent_changed")
            return {
                "schema": POLICY,
                "status": child.status,
                "workflow_id": str(wid),
                "child_task_id": str(cid),
                "replay": True,
                "provider_calls": 0,
                "database_writes": 0,
                "receipt": receipt,
            }
        _require(current.id == parent.id, "parent_changed")
        siblings = list(
            await session.scalars(
                select(RewriteTaskModel).where(
                    RewriteTaskModel.project_id == project_id,
                    RewriteTaskModel.trigger_source_id == chapter_id,
                    RewriteTaskModel.rewrite_strategy.in_((base.STRATEGY, STRATEGY)),
                )
            )
        )
        root_claims = [
            r
            for r in siblings
            if (r.metadata_json or {}).get("lineage_root_task_id") == str(root.id)
        ]
        _require(
            len(root_claims) == 1 and root_claims[0].id == previous_child_task_id,
            "lineage_not_single_first_batch",
        )
        plan = {
            "schema": POLICY,
            "status": "prepared",
            "binding": identity,
            "proof": proof,
            "candidate_id": str(candidate_id),
            "progress": progress,
            "workflow_id": str(wid),
            "child_task_id": str(cid),
            "provider_calls": 0,
            "database_writes": 0,
            "formal_review_required": True,
            "adoption_authorized": False,
        }
        if not execute:
            return plan
    req = prepared["request"]
    system, user, transport = base.prepare_source_complete_prompt(
        system_prompt=req["system_prompt"] + "\n\n" + FEEDBACK,
        user_prompt=req["user_prompt"],
        source_context=source,
        output_tokens=4096,
    )
    request = llm.LLMCompletionRequest(
        logical_role="editor",
        project_id=project_id,
        workflow_run_id=wid,
        system_prompt=system,
        user_prompt=user,
        fallback_response="Patch unavailable; no replacement authorized.",
        prompt_template="retained_candidate_patch_continuation",
        prompt_version="1.0",
        max_tokens_override=4096,
        tools=req["tools"],
        tool_choice={"type": "function", "function": {"name": TOOL_NAME}},
        operation_id=execution_operation_id(
            "retained_candidate_patch", subject=str(root.id), revision="ordinal:2"
        ),
        sample_slot=0,
        metadata={
            "progress": progress,
            "proof": proof,
            "source_transport": transport,
            "preflight_audit": report.to_dict(),
            "generic_failure_feedback": FEEDBACK,
        },
    )
    claim = {
        "binding": identity,
        "proof": proof,
        "candidate_id": str(candidate_id),
        "ordinal": 2,
        "lineage_root_task_id": str(root.id),
        "attempt_claimed": 1,
        "claimed_batches": 2,
        "max_batches": progress["max_batches"],
        "request": request.model_dump(mode="json"),
        "prepared_patch": prepared,
        "receipt": plan,
    }
    child = RewriteTaskModel(
        id=cid,
        project_id=project_id,
        parent_task_id=previous_child_task_id,
        trigger_type="retained_candidate_continuation",
        trigger_source_id=chapter_id,
        rewrite_strategy=STRATEGY,
        status="running",
        attempts=1,
        priority=1,
        instructions=FEEDBACK,
        context_required=["exact_retained_candidate", "current_adopted_sources"],
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
    body_before = target.content_md
    session.add_all([child, workflow])
    await session.commit()
    result = {
        k: deepcopy(v) for k, v in plan.items() if k not in {"provider_calls", "database_writes"}
    }
    result.update(status="running", complete_text_invocations=0, database_writes=True)
    body = None
    completion = None

    async def refresh():
        with session.no_autoflush:
            checked = await _inspect(session, settings, args, lock=True)
        _require(
            checked[6] == proof and checked[9].id == UUID(identity["parent_id"]),
            "source_or_parent_changed",
        )
        return checked

    try:
        await refresh()
        result["complete_text_invocations"] = 1
        with bind_execution_scope(identity["scope_id"]):
            completion = await llm.complete_text(session, settings, request)
        result["completion"] = {
            "content": completion.content,
            "tool_calls": deepcopy(completion.tool_calls),
            "raw_message": deepcopy(completion.raw_message),
            "llm_run_id": str(completion.llm_run_id),
            "provider": completion.provider,
            "finish_reason": completion.finish_reason,
        }
        child = await base._get(session, RewriteTaskModel, cid, lock=True)
        workflow = await base._get(session, WorkflowRunModel, wid)
        child.metadata_json = workflow.metadata_json = {**claim, "receipt": deepcopy(result)}
        workflow.current_step = "paid_return_retained"
        await session.commit()
        _require(
            not completion.fallback_used
            and completion.provider not in {"fallback", "mock"}
            and completion.llm_run_id is not None
            and completion.finish_reason in {"tool_calls", "stop"},
            "completion_unavailable",
        )
        body, patch = apply_candidate_patch(body_before, prepared, _arguments(completion))
        result.update(patch=patch, result_body_sha256=text_hash(body))
        checked = await refresh()
        binding, _, _, scenes, *_ = checked
        project, chapter, _, _, parent, *_ = binding
        run = await base._get(session, LlmRunModel, completion.llm_run_id)
        _require(
            run
            and run.project_id == project_id
            and run.workflow_run_id == wid
            and run.logical_role == "editor",
            "second_paid_binding",
        )
        _, result["paid_proof"] = await _physical(
            session,
            settings,
            project,
            run,
            claim["request"],
            result["completion"],
            identity["scope_id"],
        )
        evidence = await base._evidence(session, settings, project, chapter, scenes, body)
        checked = await refresh()
        binding, _, _, scenes, *_ = checked
        project, chapter, _, _, parent, *_ = binding
        effects = base.reviews.audit_persisted_story_effect_realization(
            project, chapter, body, scenes=scenes
        )
        passed = (
            evidence.outcome == "ok"
            and evidence.gate_receipt.report is not None
            and not evidence.duplicates
            and not evidence.audit_blockers
            and not evidence.retrofit
            and not any(f.code == FINDING_CODE for f in evidence.audit_report.findings)
            and effects.get("passed") is True
        )
        result.update(
            status="completed" if passed else "failed",
            evidence=evidence.receipt(),
            effects=effects,
            working_pointer_selected=passed,
            third_batch_allowed=False,
        )
        draft = await base._save_draft(
            session, project, chapter, parent, body, completion.llm_run_id, result, current=passed
        )
        result["new_draft_id"] = str(draft.id)
    except Exception as exc:
        await session.rollback()
        result.update(
            status="failed",
            working_pointer_selected=False,
            third_batch_allowed=False,
            error_type=type(exc).__name__,
        )
        if isinstance(exc, ValueError) and str(exc).startswith(
            ("retained_candidate_", "candidate_patch_", "retained_review_")
        ):
            result["error_code"] = str(exc)[:256]
        if body is not None:
            chapter = await base._get(session, ChapterModel, chapter_id, lock=True)
            project = await base._get(session, ProjectModel, project_id)
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
    _require(child and workflow, "claim_missing")
    child.status = workflow.status = result["status"]
    child.metadata_json = workflow.metadata_json = {**claim, "receipt": deepcopy(result)}
    workflow.current_step = (
        "requires_new_body_review"
        if result["status"] == "completed"
        else "final_batch_failed_no_retry"
    )
    if result["status"] == "failed":
        child.error_log = "Final bounded paragraph patch failed; no further batch authorized."
    await session.commit()
    return {
        "schema": POLICY,
        "status": result["status"],
        "workflow_id": str(wid),
        "child_task_id": str(cid),
        "replay": False,
        "receipt": result,
    }
