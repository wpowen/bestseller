"""Explicit extraction of an untrusted candidate, never validation of an old submission.

Inspection writes nothing. Execution stages one terminal migration in a savepoint;
the caller commits. No task, paid operation, retry allowance or adoption is created.
"""

from __future__ import annotations

# Internal ORM/JSON proof boundaries deliberately carry heterogeneous values.
# ruff: noqa: ANN001, ANN202, ANN401
from copy import deepcopy
import json
import re
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import select

from bestseller.infra.db.models import (
    ChapterDraftVersionModel,
    LlmRunModel,
    RewriteTaskModel,
    WorkflowRunModel,
)
from bestseller.services import retained_candidate_continuation as custody
from bestseller.services import retained_candidate_patch as legacy
from bestseller.services import retained_candidate_recovery as base
from bestseller.services.rewrite_patch import text_hash

POLICY = "retained-patch-proposal-migration.v2"
STRATEGY = "retained_patch_proposal_migration"


def _need(value, reason):
    if not value:
        raise ValueError("retained_patch_proposal_migration_" + reason)


def _load(text):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            _need(key not in result, "duplicate_json_key")
            result[key] = value
        return result

    def nonfinite(_value):
        raise ValueError("retained_patch_proposal_migration_nonfinite_json")

    return json.loads(text, object_pairs_hook=unique, parse_constant=nonfinite)


def _paid_source(user, source):
    """Locate the full logical source, possibly in an existing lossless container."""
    from bestseller.services.lossless_adopted_span_encoding import decode_adopted_spans
    from bestseller.services.lossless_source_encoding import decode_lossless_source

    found, offset = [], 0
    decoder = json.JSONDecoder()
    while (start := user.find("{", offset)) >= 0:
        try:
            _, end = decoder.raw_decode(user, start)
        except ValueError:
            offset = start + 1
            continue
        value = _load(user[start:end])
        offset = end
        if type(value) is not dict:
            continue
        logical = value
        schema = value.get("schema", "")
        if schema in {
            "lossless-source-encoding.v1",
            "lossless-source-encoding.v2",
            "lossless-source-encoding.v3",
        }:
            logical = decode_lossless_source(value)
        elif schema == "lossless-adopted-spans.v1":
            logical = decode_adopted_spans(value)
        if base._json(logical) == base._json(source):
            found.append(logical)
    _need(len(found) == 1, "paid_source_not_unique")
    _need(type(found[0].get("schema")) is str and found[0]["schema"], "source_schema_missing")
    return found[0]


def _commentary(content):
    """Discard prose as proposal input; reject ambiguous structured alternatives.

    This is intentionally conservative: brackets and code fences fail closed,
    even when they could have been harmless prose. No prose becomes authority.
    """
    _need(content is None or type(content) is str, "content_type")
    text = content or ""
    _need(
        not any(marker in text for marker in ("{", "}", "[", "]", "```", "~~~")),
        "competing_structured_content",
    )
    _need(
        not re.search(
            r"(?i)(?:\b(?:schema|patch_json|patches|body_sha256|catalog_sha256|target_id|replacement)"
            r"[\s\"'`]*[:=]|<\s*/?\s*(?:patch|patches|patch_json|tool_call|function_call|schema)\b"
            r"|(?:^|\n)\s*CP-[a-f0-9]{64}\s*[:=])",
            text,
        ),
        "competing_structured_content",
    )
    try:
        _load(text.strip())
    except (ValueError, TypeError, RecursionError):
        pass
    else:
        _need(False, "competing_structured_content")
    return {
        "schema": "retained-patch-nonauthoritative-commentary.v1",
        "status": "non_authoritative" if text.strip() else "absent",
        "content_sha256": text_hash(text),
        "content_chars": len(text),
        "used_as_proposal": False,
        "original_content_preserved": True,
        "selected_proposal_origin": "unique_native_tool_arguments",
    }


def _extract(paid, source, user, prepared, body):
    commentary = _commentary(paid.content)
    arguments = custody._arguments(paid)
    raw = paid.raw_message
    _need(raw is not None or not (paid.content or "").strip(), "commentary_raw_message_missing")
    if raw is not None:
        _need(
            type(raw) is dict
            and raw.get("role") == "assistant"
            and raw.get("content") == paid.content
            and raw.get("tool_calls") == paid.tool_calls
            and not raw.get("function_call"),
            "raw_message_conflict",
        )
    outer = _load(arguments)
    _need(
        type(outer) is dict and set(outer) == {"patch_json"} and type(outer["patch_json"]) is str,
        "scalar_tool_required",
    )
    original = _load(outer["patch_json"])
    _need(
        type(original) is dict
        and set(original) == {"schema", "body_sha256", "catalog_sha256", "patches"},
        "original_envelope_shape",
    )
    actual_source = _paid_source(user, source)
    _need(
        original["schema"] == actual_source["schema"] and original["schema"] != legacy.POLICY,
        "foreign_schema",
    )
    try:
        legacy.parse_candidate_patch(arguments)
    except ValueError as exc:
        _need(str(exc) == "candidate_patch_envelope_invalid", "original_failure_changed")
    else:
        _need(False, "original_not_invalid")
    proposal = {**deepcopy(original), "schema": legacy.POLICY}
    result, application = legacy.apply_candidate_patch(body, prepared, proposal)
    extraction = {
        "schema": POLICY + ".extraction",
        "original_invalid_schema": original["schema"],
        "new_proposal_schema": legacy.POLICY,
        "original_arguments_sha256": text_hash(arguments),
        "original_payload_sha256": base._hash(original),
        "commentary": commentary,
        "proposal_sha256": base._hash(proposal),
        "unchanged_patch_fields_sha256": base._hash(
            {key: original[key] for key in ("body_sha256", "catalog_sha256", "patches")}
        ),
        "original_submission_still_invalid": True,
        "source_facts_verified": False,
        "adoption_authorized": False,
        "grants_credit": False,
    }
    return result, proposal, extraction, application


async def _inspect(session, settings, args, *, lock=False):
    child = await base._get(session, RewriteTaskModel, args["original_child_task_id"], lock=lock)
    _need(child is not None, "child_missing")
    meta = child.metadata_json or {}
    identity = meta.get("binding") or {}
    try:
        failed_id, candidate_id = UUID(identity["failed_task_id"]), UUID(identity["candidate_id"])
    except (KeyError, ValueError, TypeError) as exc:
        raise ValueError("retained_patch_proposal_migration_binding_invalid") from exc
    binding = await base._binding(
        session,
        project_id=args["project_id"],
        chapter_id=args["chapter_id"],
        failed_task_id=failed_id,
        candidate_id=candidate_id,
        lock=lock,
    )
    project, chapter, failed, candidate, parent, root, current_identity, wid, cid, current = binding
    old_wf = await base._get(session, WorkflowRunModel, wid, lock=lock)
    receipt = meta.get("receipt") or {}
    _need(
        child.id == cid
        and old_wf
        and child.parent_task_id == failed.id
        and child.project_id == old_wf.project_id == project.id
        and child.trigger_source_id == old_wf.scope_id == chapter.id
        and old_wf.scope_type == "chapter"
        and child.rewrite_strategy == old_wf.workflow_type == base.STRATEGY
        and child.status == old_wf.status == "failed"
        and child.attempts == 1
        and child.metadata_json == old_wf.metadata_json,
        "child_custody",
    )
    _need(
        identity == current_identity == receipt.get("binding")
        and meta.get("lineage_root_task_id") == str(root.id)
        and type(meta.get("attempt_claimed")) is int
        and meta["attempt_claimed"] == 1
        and receipt.get("workflow_id") == str(wid)
        and receipt.get("child_task_id") == str(cid)
        and receipt.get("status") == "failed"
        and receipt.get("working_pointer_selected") is False
        and receipt.get("complete_text_invocations") == 1
        and receipt.get("error_type") == "ValueError"
        and receipt.get("error_code") == "candidate_patch_envelope_invalid"
        and not receipt.get("new_draft_id")
        and not receipt.get("patch"),
        "failure_not_schema_only",
    )
    source, scenes, prepared, system, user, transport, fence = await base._prepare(
        session, settings, binding
    )
    old_fence = meta.get("fence")
    _need(
        type(old_fence) is dict
        and old_fence == receipt.get("fence")
        and set(old_fence) == set(fence)
        and all(old_fence[key] == fence[key] for key in fence if key != "policy_sha256")
        and type(old_fence.get("policy_sha256")) is str
        and bool(old_fence["policy_sha256"]),
        "source_or_executor_changed",
    )
    _need(meta.get("prepared_patch") == prepared, "prepared_changed")
    request = meta.get("request") or {}
    _need(
        base._hash(request) == receipt.get("request_sha256")
        and request.get("project_id") == str(project.id)
        and request.get("workflow_run_id") == str(wid)
        and request.get("logical_role") == "editor"
        and request.get("prompt_template") == "retained_candidate_paragraph_patch"
        and request.get("prompt_version") == "1.0"
        and request.get("system_prompt") == system
        and request.get("user_prompt") == user
        and request.get("tools") == prepared["request"]["tools"]
        and request.get("tool_choice")
        == {"type": "function", "function": {"name": legacy.TOOL_NAME}}
        and request.get("max_tokens_override") == 4096
        and (request.get("metadata") or {}).get("binding") == identity
        and request["metadata"].get("fence") == old_fence
        and request.get("operation_id")
        == base.execution_operation_id(
            "retained_candidate_patch", subject=str(root.id), revision=base._hash(identity)
        ),
        "request_changed",
    )
    completion = receipt.get("completion") or {}
    try:
        run_id = UUID(completion["llm_run_id"])
    except (ValueError, TypeError, KeyError) as exc:
        raise ValueError("retained_patch_proposal_migration_paid_run_missing") from exc
    run = await base._get(session, LlmRunModel, run_id)
    _need(
        run
        and run.project_id == project.id
        and run.workflow_run_id == wid
        and run.logical_role == "editor"
        and run.prompt_template == "retained_candidate_paragraph_patch"
        and run.prompt_version == "1.0",
        "paid_run_binding",
    )
    paid, physical = await custody._physical(
        session, settings, project, run, request, completion, identity["scope_id"]
    )
    body, proposal, extraction, application = _extract(
        paid, source, user, prepared, candidate.content_md
    )
    migration_id = uuid5(
        NAMESPACE_URL,
        POLICY
        + "/"
        + base._json(
            {
                "project_id": str(project.id),
                "chapter_id": str(chapter.id),
                "scope_id": identity["scope_id"],
                "response_sha256": physical["response_file_sha256"],
                "body_sha256": prepared["body_sha256"],
                "catalog_sha256": prepared["catalog_sha256"],
            }
        ),
    )
    plan = {
        "schema": POLICY,
        "status": "prepared",
        "workflow_id": str(migration_id),
        "original_child_task_id": str(child.id),
        "binding": identity,
        "fence": fence,
        "original_fence": old_fence,
        "physical": physical,
        "extraction": extraction,
        "application": application,
        "proposal": proposal,
        "result_body_sha256": text_hash(body),
        "custody": {"child": custody._row_hash(child), "workflow": custody._row_hash(old_wf)},
        "requested_by": args["requested_by"],
        "evidence_ref": args["evidence_ref"],
        "provider_calls": 0,
        "grants_credit": False,
        "adoption_authorized": False,
        "formal_review_required": True,
    }
    plan["plan_sha256"] = base._hash(plan)
    return binding, scenes, body, run_id, plan


async def migrate_untrusted_patch_proposal(
    session,
    settings,
    *,
    project_id: UUID,
    chapter_id: UUID,
    original_child_task_id: UUID,
    requested_by: str,
    evidence_ref: str,
    execute: bool = False,
    expected_plan_sha256: str | None = None,
) -> dict[str, Any]:
    """Inspect, or stage one zero-provider candidate extraction; caller commits."""
    _need(
        type(execute) is bool
        and type(requested_by) is str
        and 0 < len(requested_by.strip()) <= 64
        and type(evidence_ref) is str
        and 0 < len(evidence_ref.strip()) <= 2048,
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
        original_child_task_id=original_child_task_id,
        requested_by=requested_by,
        evidence_ref=evidence_ref,
    )
    with session.no_autoflush:
        checked = await _inspect(session, settings, args, lock=execute)
        binding, scenes, body, run_id, plan = checked
        migration_id = UUID(plan["workflow_id"])
        existing = await session.scalar(
            select(WorkflowRunModel)
            .where(
                WorkflowRunModel.project_id == project_id,
                WorkflowRunModel.workflow_type == STRATEGY,
                WorkflowRunModel.metadata_json["original_child_task_id"].as_string()
                == str(original_child_task_id),
            )
            .execution_options(populate_existing=True)
        )
        if existing is not None:
            saved = existing.metadata_json or {}
            _need(
                existing.id == migration_id
                and existing.scope_type == "chapter"
                and existing.scope_id == chapter_id
                and existing.status in {"completed", "failed"}
                and saved.get("plan") == plan,
                "terminal_binding_changed",
            )
            result = deepcopy(saved.get("receipt") or {})
            draft = await base._get(session, ChapterDraftVersionModel, UUID(result["new_draft_id"]))
            _need(
                draft
                and draft.project_id == project_id
                and draft.chapter_id == chapter_id
                and text_hash(draft.content_md) == plan["result_body_sha256"]
                and draft.promotion_state == "candidate"
                and draft.llm_run_id == run_id
                and draft.promotion_metadata.get("retained_candidate_recovery")
                == {key: value for key, value in result.items() if key != "new_draft_id"},
                "terminal_candidate_changed",
            )
            return {**result, "replay": True, "database_writes": 0}
        _need(binding[-1].id == binding[4].id, "parent_changed")
        if not execute:
            return {**plan, "database_writes": 0}
        _need(
            type(expected_plan_sha256) is str and expected_plan_sha256 == plan["plan_sha256"],
            "plan_changed",
        )
    # SQLite's legacy driver defers BEGIN even after a SELECT. Otherwise the
    # first SAVEPOINT/RELEASE would commit our work without the caller's commit.
    # PostgreSQL already has a real outer transaction from the locked reads.
    connection = await session.connection()
    if connection.dialect.name == "sqlite":
        begun = await connection.run_sync(
            lambda conn: conn.connection.driver_connection.in_transaction
        )
        if not begun:
            await connection.exec_driver_sql("BEGIN")
    async with session.begin_nested():
        workflow = WorkflowRunModel(
            id=migration_id,
            project_id=project_id,
            workflow_type=STRATEGY,
            scope_type="chapter",
            scope_id=chapter_id,
            requested_by=requested_by,
            status="running",
            current_step="untrusted_proposal_extracted",
            metadata_json={"original_child_task_id": str(original_child_task_id), "plan": plan},
        )
        session.add(workflow)
        project, chapter, _failed, _candidate, parent, *_ = binding
        result = {
            **deepcopy(plan),
            "working_pointer_selected": False,
            "original_failed_submission_revalidated": False,
        }
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
                    and not any(
                        f.code == legacy.FINDING_CODE for f in evidence.audit_report.findings
                    )
                    and effects.get("passed") is True
                )
        except Exception as exc:
            result.update(gate_error_type=type(exc).__name__)
        with session.no_autoflush:
            fresh = await _inspect(session, settings, args, lock=True)
        _need(
            fresh[-1] == plan and fresh[0][-1].id == fresh[0][4].id,
            "source_or_parent_changed_after_gate",
        )
        project, chapter, _failed, _candidate, parent, *_ = fresh[0]
        result.update(status="completed" if passed else "failed", working_pointer_selected=passed)
        draft = await base._save_draft(
            session, project, chapter, parent, body, run_id, result, current=passed
        )
        result["new_draft_id"] = str(draft.id)
        workflow.status = result["status"]
        workflow.current_step = "requires_new_body_review" if passed else "candidate_rejected"
        workflow.metadata_json = {
            "original_child_task_id": str(original_child_task_id),
            "plan": deepcopy(plan),
            "receipt": deepcopy(result),
        }
        await session.flush()
    return {**result, "replay": False, "database_writes": True}
