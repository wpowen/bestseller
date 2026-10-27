"""Zero-provider derivation from an immutable failed native ordinal-two return.

Only the gateway's exact (content='', raw_message.content=None) boundary is
accepted by a source-proven parser fix. No old grade, task, claim or ledger is
rewritten. Inspection is read-only; execution is staged for the caller's commit.
"""

from __future__ import annotations

# These proof/ORM adapters validate heterogeneous JSON at runtime.
# ruff: noqa: ANN001, ANN202, ANN401
import ast
from copy import deepcopy
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import select

from bestseller.infra.db.models import (
    ChapterDraftVersionModel,
    LlmRunModel,
    RewriteTaskModel,
    WorkflowRunModel,
)
from bestseller.services import llm
from bestseller.services import migrated_candidate_continuation as continuation
from bestseller.services import retained_candidate_continuation as custody
from bestseller.services import retained_candidate_native_patch as native
from bestseller.services import retained_candidate_recovery as base
from bestseller.services.rewrite_patch import text_hash

POLICY = "derived-native-result-recovery.v1"
STRATEGY = "derived_native_result_recovery"


def _need(condition, code):
    if not condition:
        raise ValueError("derived_native_result_recovery_" + code)


def _ast(tree):
    return ast.dump(tree, include_attributes=False)


def _code_compatibility(old_path, old_sha):
    old_bytes = Path(old_path).read_bytes()
    new_bytes = Path(native.__file__).read_bytes()
    _need(text_hash(old_bytes.decode()) == old_sha, "old_native_witness_changed")
    old_tree, new_tree = ast.parse(old_bytes), ast.parse(new_bytes)
    # Precisely one expression may change. All other definitions, constants,
    # schemas, prompts, tool arguments and parser checks must remain identical.
    guards = [
        node
        for node in ast.walk(old_tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_need"
        and len(node.args) == 2
        and isinstance(node.args[1], ast.Constant)
        and node.args[1].value == "raw_content_conflict"
    ]
    _need(len(guards) == 1, "old_native_guard_ambiguous")
    original_guard = ast.parse('"content" in raw and raw["content"] == content', mode="eval").body
    _need(_ast(guards[0].args[0]) == _ast(original_guard), "old_native_guard_not_known")
    # The approved fix also emits a truthful normalization receipt. Rebuild
    # precisely those statements from literals, never remove arbitrary new AST.
    replacement = ast.parse("""_need("content" in raw, "raw_content_conflict")
if raw["content"] is None and type(content) is str and content == "":
    content_normalization = {
        "schema": "retained-native-empty-content-normalization.v1",
        "direction": "raw_null_to_completion_empty_string",
        "raw_field": "raw_message.content",
        "raw_json_type": "null",
        "raw_value": None,
        "completion_field": "content",
        "completion_json_type": "string",
        "completion_value": "",
        "wrapper_factory": "bestseller.services.llm._build_raw_assistant_message",
        "raw_message_sha256": _sha(_json(raw)),
        "tool_calls_sha256": _sha(_json(calls)),
        "original_raw_preserved": True,
        "used_as_proposal": False,
    }
else:
    _need(raw["content"] == content, "raw_content_conflict")
""").body
    old_guard = _ast(guards[0])
    counts = [0, 0, 0]

    class ApprovedFix(ast.NodeTransformer):
        def visit_Expr(self, node):
            if _ast(node.value) == old_guard:
                counts[0] += 1
                return deepcopy(replacement)
            if _ast(node) == _ast(ast.parse("_json(calls)").body[0]):
                counts[1] += 1
                return [node, ast.parse("content_normalization = None").body[0]]
            return self.generic_visit(node)

        def visit_Assign(self, node):
            if (
                len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id == "receipt"
                and isinstance(node.value, ast.Dict)
            ):
                keys = [
                    key.value if isinstance(key, ast.Constant) else None for key in node.value.keys
                ]
                if "original_content" in keys:
                    counts[2] += 1
                    index = keys.index("original_content") + 1
                    node.value.keys.insert(index, ast.Constant("content_normalization_receipt"))
                    node.value.values.insert(
                        index, ast.Name(id="content_normalization", ctx=ast.Load())
                    )
            return self.generic_visit(node)

    old_tree = ApprovedFix().visit(old_tree)
    _need(counts == [1, 1, 1], "old_native_fix_shape_unknown")
    _need(_ast(old_tree) == _ast(new_tree), "native_change_not_exact_null_empty_guard")
    return {
        "schema": POLICY + ".code-compatibility",
        "old_native_sha256": old_sha,
        "new_native_sha256": text_hash(new_bytes.decode()),
        "allowed_ast_change": "exact_empty_string_null_guard_and_normalization_receipt",
        "all_other_ast_equal": True,
    }


def _proof_compatibility(old, current, old_path):
    _need(type(old) is dict and set(old) == set(current), "proof_keys_changed")
    key = "retained_candidate_native_patch.py"
    before, after = old.get("code_sha256") or {}, current.get("code_sha256") or {}
    _need(
        set(before) == set(after)
        and key in before
        and all(before[k] == after[k] for k in before if k != key),
        "other_code_changed",
    )
    code = _code_compatibility(old_path, before[key])
    _need(after[key] == code["new_native_sha256"], "current_native_binding")
    a, b = old.get("current_full_fence") or {}, current.get("current_full_fence") or {}
    _need(
        set(a) == set(b)
        and "policy_sha256" in a
        and all(a[k] == b[k] for k in a if k != "policy_sha256"),
        "full_source_or_fence_changed",
    )
    _need(
        all(old[k] == current[k] for k in old if k not in {"code_sha256", "current_full_fence"}),
        "other_proof_changed",
    )
    return {
        **code,
        "old_policy_sha256": a["policy_sha256"],
        "new_policy_sha256": b["policy_sha256"],
        "old_proof_sha256": base._hash(old),
        "current_proof_sha256": base._hash(current),
    }


def _uuid(value, reason):
    try:
        return UUID(str(value))
    except (ValueError, TypeError) as exc:
        raise ValueError("derived_native_result_recovery_" + reason) from exc


def _envelope(status, wid, cid, receipt, replay=False):
    return {
        "schema": POLICY,
        "status": status,
        "replay": replay,
        "workflow_id": str(wid),
        "child_task_id": str(cid),
        "receipt": deepcopy(receipt),
    }


async def _inspect(session, settings, args, *, lock=False):
    lineage_args = {
        key: args[key]
        for key in (
            "project_id",
            "chapter_id",
            "failed_task_id",
            "candidate_id",
            "previous_child_task_id",
            "migration_workflow_id",
            "candidate_from_migration_id",
        )
    }
    checked = await continuation._inspect(session, settings, lineage_args, lock=lock)
    (
        binding,
        target,
        scenes,
        prepared,
        progress,
        current_proof,
        system,
        user,
        _transport,
        final_wid,
        final_cid,
    ) = checked
    project, chapter, _failed, _original, parent, root, identity, *_ = binding
    _need(args["original_final_child_task_id"] == final_cid, "final_child_identity")
    child = await base._get(session, RewriteTaskModel, final_cid, lock=lock)
    workflow = await base._get(session, WorkflowRunModel, final_wid, lock=lock)
    _need(
        child
        and workflow
        and child.project_id == workflow.project_id == project.id
        and child.trigger_source_id == workflow.scope_id == chapter.id
        and workflow.scope_type == "chapter"
        and child.parent_task_id == args["previous_child_task_id"]
        and child.rewrite_strategy == workflow.workflow_type == continuation.STRATEGY
        and child.status == workflow.status == "failed"
        and child.attempts == 1
        and child.metadata_json == workflow.metadata_json,
        "failed_final_custody",
    )
    meta, receipt = child.metadata_json or {}, (child.metadata_json or {}).get("receipt") or {}
    _need(
        meta.get("binding") == identity == receipt.get("binding")
        and meta.get("candidate_id") == str(target.id)
        and meta.get("migration_workflow_id") == str(args["migration_workflow_id"])
        and meta.get("lineage_root_task_id") == str(root.id)
        and meta.get("ordinal") == meta.get("claimed_batches") == meta.get("max_batches") == 2
        and meta.get("attempt_claimed") == 1
        and receipt.get("status") == "failed"
        and receipt.get("working_pointer_selected") is False
        and receipt.get("error_type") == "ValueError"
        and receipt.get("error_code") == "native_candidate_patch:raw_content_conflict"
        and receipt.get("complete_text_invocations") == 1
        and receipt.get("workflow_id") == str(final_wid)
        and receipt.get("child_task_id") == str(final_cid)
        and receipt.get("proof") == meta.get("proof")
        and not receipt.get("new_draft_id")
        and not receipt.get("patch"),
        "not_normalization_only_failure",
    )
    compatibility = _proof_compatibility(
        meta.get("proof"), current_proof, args["original_native_source_path"]
    )
    _need(
        meta.get("prepared_patch") == prepared and receipt.get("progress") == progress,
        "prepared_or_progress_changed",
    )
    request = meta.get("request") or {}
    _need(
        base._hash(request) == receipt.get("request_sha256")
        and request.get("system_prompt") == system
        and request.get("user_prompt") == user
        and request.get("tools") == prepared["request"]["tools"]
        and request.get("tool_choice") == "required"
        and request.get("max_tokens_override") == 4096
        and request.get("project_id") == str(project.id)
        and request.get("workflow_run_id") == str(final_wid)
        and request.get("logical_role") == "editor"
        and request.get("prompt_template") == "migrated_candidate_patch_continuation"
        and request.get("prompt_version") == "1.0"
        and request.get("sample_slot") == 0
        and request.get("operation_id")
        == base.execution_operation_id(
            "retained_candidate_patch", subject=str(root.id), revision="ordinal:2"
        )
        and (request.get("metadata") or {}).get("proof") == meta["proof"],
        "original_request_changed",
    )
    saved_completion = receipt.get("completion") or {}
    run = await base._get(
        session, LlmRunModel, _uuid(saved_completion.get("llm_run_id"), "paid_run_id")
    )
    _need(
        run
        and run.project_id == project.id
        and run.workflow_run_id == final_wid
        and run.logical_role == "editor"
        and run.prompt_template == request["prompt_template"]
        and run.prompt_version == "1.0",
        "paid_run_scope",
    )
    # Verify the ORIGINAL unnormalized file/request/route/root/all attempts and
    # transport before using the new parser. No proof is checked on a made-up raw.
    paid, physical = await custody._physical(
        session, settings, project, run, request, saved_completion, identity["scope_id"]
    )
    _need(
        paid.content == ""
        and type(paid.raw_message) is dict
        and "content" in paid.raw_message
        and paid.raw_message["content"] is None
        and paid.raw_message.get("role") == "assistant"
        and paid.raw_message.get("tool_calls") == paid.tool_calls
        and not paid.raw_message.get("function_call"),
        "not_exact_gateway_null_empty",
    )
    _need(
        llm._build_raw_assistant_message(paid.content, paid.tool_calls) == paid.raw_message,
        "gateway_factory_shape_changed",
    )
    body, application = native.apply_native_candidate_patch(target.content_md, prepared, paid)
    derived_id = uuid5(
        NAMESPACE_URL,
        POLICY
        + "/"
        + base._json(
            {
                "scope_id": identity["scope_id"],
                "project_id": str(project.id),
                "chapter_id": str(chapter.id),
                "original_final_child_task_id": str(final_cid),
                "physical_response_sha256": physical["response_file_sha256"],
                "source_body_sha256": prepared["body_sha256"],
                "catalog_sha256": prepared["catalog_sha256"],
            }
        ),
    )
    plan = {
        "schema": POLICY,
        "status": "prepared",
        "workflow_id": str(derived_id),
        "original_final_child_task_id": str(final_cid),
        "original_final_workflow_id": str(final_wid),
        "original_child_status_preserved": "failed",
        "original_workflow_status_preserved": "failed",
        "binding": identity,
        "migration_workflow_id": str(args["migration_workflow_id"]),
        "candidate_from_migration_id": str(target.id),
        "current_proof": current_proof,
        "compatibility": compatibility,
        "final_paid_physical": physical,
        "old_child_sha256": custody._row_hash(child),
        "old_workflow_sha256": custody._row_hash(workflow),
        "result_body_sha256": text_hash(body),
        "application": application,
        "normalization": {
            "schema": POLICY + ".empty-content-boundary",
            "original_completion_content": "",
            "original_raw_content": None,
            "original_raw_content_key_present": True,
            "original_raw_preserved": True,
            "schema_or_patch_payload_changed": False,
            "old_failed_submission_revalidated": False,
            "normalization_authority": "llm._build_raw_assistant_message:content_or_none",
        },
        "requested_by": args["requested_by"],
        "evidence_ref": args["evidence_ref"],
        "executor_sha256": text_hash(Path(__file__).read_text()),
        "provider_calls": 0,
        "grants_credit": False,
        "third_batch_allowed": False,
        "adoption_authorized": False,
        "formal_review_required": True,
    }
    plan["plan_sha256"] = base._hash(plan)
    return binding, scenes, body, run.id, plan


async def recover_derived_native_result(
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
    original_final_child_task_id: UUID,
    original_native_source_path: str,
    requested_by: str,
    evidence_ref: str,
    execute: bool = False,
    expected_plan_sha256: str | None = None,
) -> dict[str, Any]:
    """Inspect or stage one derived workflow/candidate; no commit or paid call."""
    _need(
        type(execute) is bool
        and type(requested_by) is str
        and 0 < len(requested_by.strip()) <= 64
        and type(evidence_ref) is str
        and 0 < len(evidence_ref.strip()) <= 2048
        and isinstance(original_native_source_path, (str, Path)),
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
        original_final_child_task_id=original_final_child_task_id,
        original_native_source_path=original_native_source_path,
        requested_by=requested_by,
        evidence_ref=evidence_ref,
    )
    with session.no_autoflush:
        binding, scenes, body, run_id, plan = await _inspect(session, settings, args, lock=execute)
        wid, cid = UUID(plan["workflow_id"]), original_final_child_task_id
        rows = list(
            await session.scalars(
                select(WorkflowRunModel)
                .where(
                    WorkflowRunModel.project_id == project_id,
                    WorkflowRunModel.workflow_type == STRATEGY,
                    WorkflowRunModel.metadata_json["original_final_child_task_id"].as_string()
                    == str(cid),
                )
                .execution_options(populate_existing=True)
            )
        )
        _need(len(rows) <= 1, "duplicate_derived_workflows")
        if rows:
            row = rows[0]
            saved = row.metadata_json or {}
            _need(
                row.id == wid
                and row.scope_type == "chapter"
                and row.scope_id == chapter_id
                and row.status in {"completed", "failed"}
                and saved.get("plan") == plan,
                "terminal_binding_changed",
            )
            result = deepcopy(saved.get("receipt") or {})
            draft = await base._get(
                session, ChapterDraftVersionModel, _uuid(result.get("new_draft_id"), "result_id")
            )
            _need(
                draft
                and draft.project_id == project_id
                and draft.chapter_id == chapter_id
                and text_hash(draft.content_md) == plan["result_body_sha256"]
                and draft.llm_run_id == run_id
                and draft.promotion_state == "candidate"
                and draft.promotion_metadata.get("retained_candidate_recovery")
                == {k: v for k, v in result.items() if k != "new_draft_id"},
                "terminal_candidate_changed",
            )
            return _envelope(row.status, wid, cid, {**result, "database_writes": 0}, True)
        _need(
            binding[-1].id == binding[4].id and binding[4].promotion_state == "candidate",
            "parent_changed",
        )
        if not execute:
            return _envelope("prepared", wid, cid, {**plan, "database_writes": 0})
        _need(
            type(expected_plan_sha256) is str and expected_plan_sha256 == plan["plan_sha256"],
            "plan_changed",
        )
    connection = await session.connection()
    if connection.dialect.name == "sqlite":
        if not await connection.run_sync(
            lambda conn: conn.connection.driver_connection.in_transaction
        ):
            await connection.exec_driver_sql("BEGIN")
    async with session.begin_nested():
        wf = WorkflowRunModel(
            id=wid,
            project_id=project_id,
            workflow_type=STRATEGY,
            scope_type="chapter",
            scope_id=chapter_id,
            requested_by=requested_by,
            status="running",
            current_step="derived_native_candidate",
            metadata_json={},
        )
        session.add(wf)
        project, chapter, _failed, _original, parent, *_ = binding
        result = {**deepcopy(plan), "working_pointer_selected": False}
        passed = False
        try:
            async with session.begin_nested():
                evidence = await base._evidence(session, settings, project, chapter, scenes, body)
                effects = base.reviews.audit_persisted_story_effect_realization(
                    project, chapter, body, scenes=scenes
                )
                result.update(evidence=evidence.receipt(), effects=effects)
                passed = (
                    evidence.outcome == "ok"
                    and evidence.gate_receipt.report is not None
                    and not evidence.duplicates
                    and not evidence.audit_blockers
                    and not evidence.retrofit
                    and not any(f.code == base.FINDING_CODE for f in evidence.audit_report.findings)
                    and effects.get("passed") is True
                )
        except Exception as exc:
            result["gate_error_type"] = type(exc).__name__
        with session.no_autoflush:
            fresh = await _inspect(session, settings, args, lock=True)
        _need(
            fresh[-1] == plan and fresh[0][-1].id == fresh[0][4].id,
            "source_or_parent_changed_after_gate",
        )
        project, chapter, _failed, _original, parent, *_ = fresh[0]
        result.update(status="completed" if passed else "failed", working_pointer_selected=passed)
        draft = await base._save_draft(
            session, project, chapter, parent, body, run_id, result, current=passed
        )
        result["new_draft_id"] = str(draft.id)
        wf.status = result["status"]
        wf.current_step = "requires_new_body_review" if passed else "candidate_rejected"
        wf.metadata_json = {
            "original_final_child_task_id": str(cid),
            "plan": deepcopy(plan),
            "receipt": deepcopy(result),
        }
        await session.flush()
    return _envelope(result["status"], wid, cid, {**result, "database_writes": True})
