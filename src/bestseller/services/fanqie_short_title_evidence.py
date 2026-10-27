"""Trusted read-only title evidence acquisition, not title/semantic approval.

No engine, transaction, model, write, scan, recovery or replay is created here.
The caller owns a fresh read-only session; the v4 mapper validates the resulting
original native values against the current assembly/canonical/design afterwards.
"""

from __future__ import annotations

import base64
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import select

from bestseller.infra.db.models import (
    ExecutionAttemptModel,
    ExecutionOperationModel,
    LlmRunModel,
    ProjectModel,
    WorkflowRunModel,
    WorkflowStepRunModel,
)
from bestseller.services.fanqie_short_semantic_inputs import project_title_projection
from bestseller.services.native_title_operator import title_operator as producer

AUDIT_DIRECTORY = Path(".framework-recovery/title-operator/audit")
SCHEMA = "native-publishing-title-evidence.v1"
SELECTION_SCHEMA = "native-paid-title-selection-evidence.v1"


class TitleEvidenceError(ValueError):
    pass


def _need(ok, code):
    if not ok:
        raise TitleEvidenceError("native_title_evidence:" + code)


def _id(value):
    value = str(value)
    _need(str(UUID(value)) == value and UUID(value).int != 0, "identity")
    return value


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _digest(value):
    return _sha(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    )


def _same(a, b):
    return _digest(a) == _digest(b)


def _json(raw):
    def pairs(items):
        value = {}
        for key, item in items:
            _need(key not in value, "duplicate_json_key")
            value[key] = item
        return value

    def constant(_):
        raise TitleEvidenceError("native_title_evidence:nonfinite_json")

    value = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs, parse_constant=constant)
    _digest(value)  # Reject overflowing exponents too.
    return value


def _read(root, relative, *, limit):
    """Open every path component via dir_fd/O_NOFOLLOW; no resolve-then-open race."""
    _need(not relative.is_absolute() and not {".", ".."} & set(relative.parts), "relative_path")
    parts = (*root.parts[1:], *relative.parts)
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    fd = None
    try:
        fd = os.open("/", directory_flags)
        for part in parts[:-1]:
            child = os.open(part, directory_flags, dir_fd=fd)
            os.close(fd)
            fd = child
        child = os.open(
            parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK, dir_fd=fd
        )
        os.close(fd)
        fd = child
        before = os.fstat(fd)
        _need(stat.S_ISREG(before.st_mode) and 0 < before.st_size <= limit, "physical_size_or_type")
        chunks, size = [], 0
        while True:
            block = os.read(fd, min(65536, limit + 1 - size))
            if not block:
                break
            chunks.append(block)
            size += len(block)
            _need(size <= limit, "physical_capacity")
        after = os.fstat(fd)
        fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
        _need(
            size == before.st_size and all(getattr(before, k) == getattr(after, k) for k in fields),
            "physical_changed_during_read",
        )
        return b"".join(chunks)
    except OSError:
        raise TitleEvidenceError("native_title_evidence:physical_file_unavailable") from None
    finally:
        if fd is not None:
            os.close(fd)


def _row(row, fields, *, ids=()):
    return {k: _id(getattr(row, k)) if k in ids else deepcopy(getattr(row, k)) for k in fields}


def _identity(run):
    # Actual producer prepare puts source_binding_sha256 in request.metadata;
    # llm._complete_text_impl starts from dict(request.metadata), so the original
    # LlmRun preserves it. A restored row without it grants no guessed path.
    meta = run.metadata_json
    identity = meta.get("source_binding_sha256")
    operation = meta.get("operation_id")
    if operation is not None:
        _need(
            isinstance(operation, str)
            and re.fullmatch(r"native-title-repair\.v1:[0-9a-f]{64}", operation),
            "original_operation_id",
        )
        candidate = operation.rsplit(":", 1)[1]
        _need(identity is None or identity == candidate, "original_identity_conflict")
        identity = candidate
    _need(
        isinstance(identity, str) and re.fullmatch(r"[0-9a-f]{64}", identity),
        "missing_original_request_identity",
    )
    return identity


async def load_native_title_evidence(session, settings, *, project):
    """Return Kant's exact native evidence shape, or None if no title lineage.

    Reads the producer's fixed local_dir/.framework-recovery/title-operator/
    audit/<original identity>/paid-return.json. It never accepts a caller audit
    path, searches directories or replaces the immutable paid-return object.
    """
    try:
        return await _load(session, settings, project)
    except TitleEvidenceError:
        raise
    except producer.Stop as error:
        raise TitleEvidenceError("native_title_evidence:paid:" + str(error)) from None
    except (ValueError, KeyError, TypeError, AttributeError, OverflowError, RecursionError):
        raise TitleEvidenceError("native_title_evidence:malformed_native_evidence") from None


async def _load(session, settings, project):
    _need(not session.new and not session.dirty, "pending_native_changes")
    pid = _id(project.id)
    with session.no_autoflush:
        current = await session.get(ProjectModel, UUID(pid), populate_existing=True)
        _need(current is not None and _id(current.id) == pid, "project_missing")
        projected = project_title_projection(current)
        _need(_same(project_title_projection(project), projected), "stale_project")
        meta = current.metadata_json
    if "title_paid_selection_lineage" in meta:
        return await _load_selection(session, settings, projected=projected, metadata=meta)
    return await _load_original(session, settings, projected=projected, metadata=meta)


async def _load_original(session, settings, *, projected, metadata):
    """Read original rows/files under an explicitly preserved original title.

    For a selection wrapper the archived projection is an immutable historical
    node, not a replacement for the actual current Project row. All original
    WF/run/step/ledger/physical data are still fetched from their real sources.
    """
    pid = _id(projected["id"])
    with session.no_autoflush:
        meta = metadata
        if "title_repair_lineage" not in meta:
            return None
        lineage = meta["title_repair_lineage"]
        _need(
            type(lineage) is dict
            and lineage.get("schema") == producer.SCHEMA
            and lineage.get("project_id") == pid
            and lineage.get("adopted_title") == projected["title"],
            "current_lineage",
        )
        wf_id, run_id = _id(lineage["workflow_run_id"]), _id(lineage["llm_run_id"])
        workflow = await session.get(WorkflowRunModel, UUID(wf_id), populate_existing=True)
        run = await session.get(LlmRunModel, UUID(run_id), populate_existing=True)
        _need(
            workflow is not None
            and _id(workflow.id) == wf_id
            and _id(workflow.project_id) == pid
            and workflow.workflow_type == "title_repair_operator"
            and workflow.status == "completed"
            and workflow.current_step == "title_repair_adopted"
            and not workflow.error_message
            and workflow.scope_type == "project"
            and _id(workflow.scope_id) == pid,
            "completed_title_workflow",
        )
        _need(
            run is not None
            and _id(run.id) == run_id
            and _id(run.project_id) == pid
            and _id(run.workflow_run_id) == wf_id
            and run.logical_role == "editor"
            and run.prompt_template == "title_story_grounded_revision"
            and run.prompt_version == "1.0",
            "original_title_run",
        )
        _need("execution_recovery" not in run.metadata_json, "recovered_run_not_original")
        steps = list(
            await session.scalars(
                select(WorkflowStepRunModel)
                .where(WorkflowStepRunModel.workflow_run_id == UUID(wf_id))
                .order_by(WorkflowStepRunModel.step_order)
            )
        )
        _need(len(steps) == 1, "title_step_inventory")
        step = steps[0]
        _need(
            _id(step.workflow_run_id) == wf_id
            and step.step_name == "story_grounded_title_revision"
            and type(step.step_order) is int
            and step.step_order == 1
            and step.status == "completed"
            and not step.error_message,
            "completed_title_step",
        )
        scope, key = meta["execution_scope_id"], lineage["operation_key"]
        _need(
            isinstance(scope, str)
            and bool(scope)
            and workflow.metadata_json["execution_scope_id"] == scope,
            "execution_scope",
        )
        identity = _identity(run)
        operation_id = "native-title-repair.v1:" + identity
        _need(
            wf_id == str(uuid5(NAMESPACE_URL, "bestseller:" + operation_id))
            and key == _sha((scope + "\n" + operation_id + ":sample:0").encode()),
            "operation_identity",
        )
        root = await session.get(ExecutionOperationModel, key, populate_existing=True)
        _need(root is not None and root.key == key and root.scope_id == scope, "original_paid_root")
        attempts = list(
            await session.scalars(
                select(ExecutionAttemptModel)
                .where(ExecutionAttemptModel.operation_key == key)
                .order_by(ExecutionAttemptModel.id)
            )
        )
        transports = list(
            await session.scalars(
                select(ExecutionOperationModel)
                .where(
                    ExecutionOperationModel.scope_id == scope,
                    ExecutionOperationModel.status == "transport_result",
                    ExecutionOperationModel.result_json["operation_key"].as_string() == key,
                )
                .order_by(ExecutionOperationModel.key)
            )
        )

    _need(settings.artifact_store.mode == "local", "local_retention_required")
    root_dir = Path(os.path.abspath(os.path.expanduser(str(settings.artifact_store.local_dir))))
    audit = _json(
        _read(root_dir, AUDIT_DIRECTORY / identity / "paid-return.json", limit=12_000_000)
    )
    _need(
        type(audit) is dict
        and audit["audit_sha256"]
        == lineage["paid_audit_sha256"]
        == _digest({k: v for k, v in audit.items() if k != "audit_sha256"}),
        "paid_audit_hash",
    )
    plan, completion = audit["plan"], audit["completion"]
    _need(
        audit.get("schema") == plan.get("schema") == producer.SCHEMA
        and workflow.metadata_json.get("schema") == producer.SCHEMA,
        "native_producer_schema",
    )
    _need(
        plan["identity"] == identity
        and plan["request"]["operation_id"] == operation_id
        and plan["workflow_run_id"] == wf_id
        and plan["operation_key"] == key
        and plan["plan_sha256"]
        == lineage["plan_sha256"]
        == workflow.metadata_json["plan_sha256"]
        == _digest({k: v for k, v in plan.items() if k != "plan_sha256"})
        and _same(plan["literal_source"], workflow.metadata_json["literal_source"]),
        "original_paid_plan",
    )
    _need(
        _same(lineage, workflow.metadata_json["title_repair_lineage"])
        and all(_same(step.output_ref.get(k), v) for k, v in lineage.items()),
        "native_lineage_conflict",
    )
    _need(
        completion["llm_run_id"] == run_id and run.prompt_hash == plan["effective_input_hash"],
        "paid_run_binding",
    )
    for k in ("provider", "model_name", "finish_reason", "input_tokens", "output_tokens"):
        _need(_same(getattr(run, k), completion[k]), "original_run_result")
    for k, v in completion["metadata"].items():
        _need(_same(run.metadata_json.get(k), v), "original_run_metadata")

    ledger = {
        "root": {
            **_row(root, ("key", "scope_id", "input_hash", "status")),
            "result_json": producer.safe_completion(root.result_json),
        },
        "attempts": [
            _row(
                a,
                (
                    "id",
                    "operation_key",
                    "scope_id",
                    "model_name",
                    "input_hash",
                    "actual_tokens",
                    "status",
                ),
            )
            for a in attempts
        ],
        "transports": [],
    }
    known = {a.id for a in attempts}
    for t in transports:
        record = deepcopy(t.result_json)
        aid = record["attempt_id"]
        _need(
            aid in known
            and t.key == _sha(("transport-result.v1\n" + scope + "\n" + aid).encode())
            and t.scope_id == scope
            and t.input_hash == plan["effective_input_hash"],
            "transport_identity",
        )
        record["result"] = producer.safe_completion(record["result"])
        ledger["transports"].append(record)
    _need(_same(ledger, audit["ledger"]), "current_ledger_changed")
    physical = {}
    for kind in ("request", "response"):
        ref = f"runtime/llm_payloads/{run_id}.{kind}.json"
        _need(
            getattr(run, kind + "_payload_ref") == ref
            and completion["metadata"]["prompt_payload_receipt"][kind + "_ref"] == ref,
            "physical_reference",
        )
        raw = _read(root_dir, Path(ref), limit=3_000_000)
        _need(_same(_json(raw), audit["retained_payloads"][kind]), "original_physical_payload")
        physical[kind] = {
            "ref": ref,
            "sha256": _sha(raw),
            "bytes_b64": base64.b64encode(raw).decode("ascii"),
        }
    producer.validate_paid(plan, completion, audit["retained_payloads"], ledger)
    return {
        "schema": SCHEMA,
        "project": projected,
        "workflow": _row(
            workflow,
            (
                "id",
                "project_id",
                "workflow_type",
                "status",
                "scope_type",
                "scope_id",
                "current_step",
                "error_message",
                "metadata_json",
            ),
            ids=("id", "project_id", "scope_id"),
        ),
        "step": _row(
            step,
            (
                "id",
                "workflow_run_id",
                "step_name",
                "step_order",
                "status",
                "error_message",
                "input_ref",
                "output_ref",
            ),
            ids=("id", "workflow_run_id"),
        ),
        "llm_run": _row(
            run,
            (
                "id",
                "project_id",
                "workflow_run_id",
                "logical_role",
                "prompt_template",
                "prompt_version",
                "prompt_hash",
                "provider",
                "model_name",
                "finish_reason",
                "input_tokens",
                "output_tokens",
                "request_payload_ref",
                "response_payload_ref",
                "metadata_json",
            ),
            ids=("id", "project_id", "workflow_run_id"),
        ),
        "paid_return": audit,
        "ledger": ledger,
        "physical_files": physical,
    }


async def _load_selection(session, settings, *, projected, metadata):
    from types import SimpleNamespace
    from bestseller.services import native_paid_title_selection as selection

    pid = _id(projected["id"])
    lineage = metadata[selection.LINEAGE_KEY]
    _need(type(lineage) is dict and lineage.get("schema") == selection.SCHEMA
          and lineage.get("project_id") == pid
          and lineage.get("selected_title") == projected["title"], "selection_current_lineage")
    wf_id = _id(lineage["workflow_run_id"])
    with session.no_autoflush:
        workflow = await session.get(WorkflowRunModel, UUID(wf_id), populate_existing=True)
        _need(workflow is not None and _id(workflow.id) == wf_id
              and _id(workflow.project_id) == pid and workflow.workflow_type == selection.WORKFLOW_TYPE
              and workflow.status == "completed" and workflow.current_step == "paid_title_candidate_adopted"
              and workflow.scope_type == "project" and _id(workflow.scope_id) == pid
              and workflow.requested_by == "assistant" and not workflow.error_message,
              "selection_completed_workflow")
        steps = list(await session.scalars(select(WorkflowStepRunModel).where(
            WorkflowStepRunModel.workflow_run_id == UUID(wf_id)
        ).order_by(WorkflowStepRunModel.step_order)))
    _need(len(steps) == 1, "selection_step_inventory")
    step = steps[0]
    wm = workflow.metadata_json
    plan, before = wm["selection_plan"], wm["selection_metadata_before"]
    original_project = wm["origin_project_projection"]
    _need(plan.get("schema") == selection.SCHEMA
          and plan["workflow_run_id"] == wf_id
          and plan["plan_sha256"] == lineage["plan_sha256"] == _digest({
              k: v for k, v in plan.items() if k != "plan_sha256"})
          and _digest(before) == plan["binding"]["project_metadata_sha256"], "selection_plan_before_metadata")
    _need(_same(lineage, wm[selection.LINEAGE_KEY])
          and _same(lineage, step.output_ref.get(selection.LINEAGE_KEY)), "selection_lineage_conflict")
    _need(original_project["id"] == pid and original_project["title"] == plan["binding"]["old_title"]
          and original_project["title"] != projected["title"], "selection_historical_origin_title")
    old_projection = project_title_projection(SimpleNamespace(
        id=pid, title=original_project["title"], metadata_json=before))
    _need(_same(old_projection, original_project), "selection_archived_origin_projection")
    # Read the REAL old WF/run/root/physical files, preserving original six-char
    # project metadata. No committed flag/new engine: flushed rows in this same
    # caller transaction are intentionally visible to the pre-commit verifier.
    origin = await _load_original(session, settings, projected=original_project, metadata=before)
    _need(origin is not None and _digest(origin) == plan["binding"]["origin_evidence_sha256"],
          "selection_original_evidence_changed")
    evidence = {
        "schema": SELECTION_SCHEMA, "project": deepcopy(projected),
        "selection_workflow": _row(workflow, (
            "id", "project_id", "workflow_type", "status", "scope_type", "scope_id",
            "requested_by", "current_step", "error_message", "metadata_json",
        ), ids=("id", "project_id", "scope_id")),
        "selection_step": _row(step, (
            "id", "workflow_run_id", "step_name", "step_order", "status", "error_message",
            "input_ref", "output_ref",
        ), ids=("id", "workflow_run_id")),
        "origin": origin,
    }
    # Verify joins/grades/debt/protected metadata against actual native objects.
    # The final mapper also binds the fresh full body/canonical/control manifest.
    try:
        expected_meta, expected_lineage, record = selection.selection_metadata(
            before, plan=plan, origin_project=origin["project"])
    except selection.PaidTitleSelectionError as error:
        raise TitleEvidenceError("native_title_evidence:selection:" + str(error)) from None
    expected_project = project_title_projection(SimpleNamespace(
        id=pid, title=plan["binding"]["selected_title"], metadata_json=expected_meta))
    _need(_same(projected, expected_project)
          and _same(lineage, expected_lineage), "selection_current_projection_changed")
    _need(_id(step.id) == str(uuid5(UUID(wf_id), "paid_title_candidate_selection"))
          and _id(step.workflow_run_id) == wf_id
          and step.step_name == "adopt_existing_paid_title_candidate"
          and type(step.step_order) is int and step.step_order == 1
          and step.status == "completed" and not step.error_message
          and _same(step.input_ref, {"plan_sha256": plan["plan_sha256"], "source_manifest": plan["binding"]["manifest_before"]})
          and _same(step.output_ref, {selection.LINEAGE_KEY: lineage, "title_record": record})
          and wm.get("schema") == selection.SCHEMA and wm.get("execution_scope_id") == producer.SCOPE
          and wm.get("original_task_id") == producer.TASK_ID
          and _same(wm.get("editorial_quality_debt"), plan["editorial_quality_debt"]), "selection_step_and_debt")
    return evidence
