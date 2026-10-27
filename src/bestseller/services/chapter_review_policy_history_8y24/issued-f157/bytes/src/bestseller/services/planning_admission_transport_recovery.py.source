"""Read-only proof and proposal for a zero-dispatch V4 wire migration.

The caller owns the whole-row CAS and commit. No source, paid history, financial
row, protocol status or native attempt allowance is changed by this module.
"""

# ruff: noqa: ANN001, ANN201, ANN202 -- Canonically guarded heterogeneous receipts.
from copy import deepcopy
from datetime import UTC, datetime
import hashlib
from uuid import UUID

from sqlalchemy import String, cast, or_

from bestseller.infra.db import models as db
from bestseller.services import planning_draft_admission as admission
from bestseller.services.foundation_selection import load_selected_foundation_artifacts
from bestseller.services.materialized_outline_source_recovery import (
    SCHEMA as SOURCE_SCHEMA,
)
from bestseller.services.materialized_outline_source_recovery import (
    WORKFLOW_TYPE,
    _read_sources,
    admission_workflow_id,
)
from bestseller.services.outline_revision_lifecycle import SCHEMA as REVISION_SCHEMA
from bestseller.services.outline_revision_lifecycle import _runnable, _unadopted
from bestseller.services.planner import _without_planning_meta
from bestseller.services.planning_admission_ref_protocol import POLICY, reference_contract
from bestseller.services.planning_admission_ref_protocol import SCHEMA as GRADE_SCHEMA
from bestseller.services.planning_capacity_continuation import _accounting, _row_hash
from bestseller.services.planning_output_budget import LITERAL_FULL_SOURCE_VERSION
from bestseller.services.prompt_source_policy import content_hash
from bestseller.services.retained_planning_generation import _Reader

SCHEMA = "planning-admission-zero-paid-wire-migration.v1"
INVENTORY_SCHEMA = "planning-admission-zero-paid-inventory.v1"
WIRE_VERSION = LITERAL_FULL_SOURCE_VERSION


def _need(ok, code):
    if not ok:
        raise ValueError("admission_transport_migration:" + code)


def _same(left, right):
    return content_hash(left) == content_hash(right)


def _utc(value):
    _need(isinstance(value, datetime), "attempt_timestamp_missing")
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _hashes(rows, model, key):
    return {
        str(getattr(row, key)): _row_hash(row, model)
        for row in sorted(rows, key=lambda row: str(getattr(row, key)))
    }


def _mentions(value, marker):
    if isinstance(value, dict):
        return any(marker in str(key) or _mentions(item, marker) for key, item in value.items())
    if isinstance(value, (list, tuple)):
        return any(_mentions(item, marker) for item in value)
    return isinstance(value, str) and marker in value


def _clean_session(session):
    # Even a SELECT in an upstream source reader must not autoflush caller edits.
    protected = (
        db.ProjectModel,
        db.PlanningArtifactVersionModel,
        db.WorkflowRunModel,
        db.BookProductionControlModel,
        db.ChapterModel,
        db.SceneCardModel,
        db.ChapterDraftVersionModel,
        db.ExecutionBudgetModel,
        db.ExecutionOperationModel,
        db.ExecutionAttemptModel,
        db.LlmRunModel,
    )
    _need(
        not any(
            isinstance(row, protected)
            and (row in session.new or row in session.deleted or session.is_modified(row))
            for row in tuple(session.new) + tuple(session.dirty) + tuple(session.deleted)
        ),
        "pending_protected_changes",
    )


async def _context(session, settings, workflow_run_id):
    reader = _Reader(session)
    workflow = await reader.get(db.WorkflowRunModel, UUID(str(workflow_run_id)))
    _need(
        workflow is not None
        and workflow.project_id is not None
        and workflow.workflow_type == WORKFLOW_TYPE
        and workflow.scope_type == "planning_artifact"
        and workflow.status == "machine_blocked",
        "blocked_source_owner_required",
    )
    state = deepcopy(workflow.metadata_json or {})
    protocol = state.get("protocol")
    _need(
        state.get("schema") == SOURCE_SCHEMA
        and type(protocol) is dict
        and protocol.get("status") == "input_budget_blocked"
        and type(protocol.get("attempts")) is list
        and protocol["attempts"] == []
        and state.get("source_admission") is None,
        "zero_slot_capacity_block_required",
    )
    capacity = protocol.get("input_capacity")
    _need(
        type(capacity) is dict
        and capacity.get("budget_passed") is False
        and capacity.get("truncated") is False
        and capacity.get("error_type") == "SourceBoundPlanningCapacityError"
        and capacity.get("boundary") == "complete source and native tools capacity",
        "original_capacity_failure_required",
    )
    _need(
        "wire_version" not in protocol and not state.get("transport_migrations"),
        "existing_transport_migration",
    )
    revision = state.get("full_outline_revision")
    _need(
        type(revision) is dict
        and revision.get("schema") == REVISION_SCHEMA
        and revision.get("admission_schema") == GRADE_SCHEMA
        and revision.get("phase") == "protocol_unknown",
        "v4_revision_required",
    )
    project = await reader.get(db.ProjectModel, workflow.project_id)
    _need(project is not None, "project_missing")
    project = await _runnable(session, project)
    _need(
        state.get("execution_scope_id") == project.metadata_json.get("execution_scope_id"),
        "original_scope_changed",
    )
    artifact = await reader.get(db.PlanningArtifactVersionModel, workflow.scope_id)
    _need(
        artifact is not None
        and artifact.project_id == project.id
        and artifact.artifact_type == "chapter_outline_batch"
        and artifact.status == "draft"
        and artifact.scope_ref_id is None
        and type(artifact.content) is dict
        and artifact.source_run_id == workflow.id
        and workflow.id == admission_workflow_id(project.id, artifact.id)
        and state.get("source_artifact_id") == str(artifact.id)
        and state.get("source_content_sha256") == content_hash(artifact.content),
        "artifact_binding_changed",
    )
    candidate = _without_planning_meta(artifact.content)
    _need(
        state.get("candidate_hash") == protocol.get("candidate_hash") == content_hash(candidate),
        "candidate_changed",
    )
    chapters = await _unadopted(
        session, project.id, [c["chapter_number"] for c in candidate["chapters"]]
    )
    _, base, _, foundation, binding = await _read_sources(
        session,
        settings,
        project_slug=project.slug,
        artifact_id=revision["base_artifact_id"],
        materialization_workflow_id=revision["base_materialization_workflow_run_id"],
    )
    _need(
        _same(binding, revision.get("base_binding"))
        and revision.get("base_source_content_sha256") == content_hash(base.content)
        and revision.get("base_candidate_hash") == binding["candidate_hash"],
        "base_source_changed",
    )
    from bestseller.services.outline_source_entry import current_outline_artifact

    for chapter in chapters:
        current = await current_outline_artifact(session, project=project, chapter=chapter)
        _need(current is not None and current.id == base.id, "current_outline_source_changed")
    selected = await load_selected_foundation_artifacts(session, project=project, settings=settings)
    _need(
        selected is not None
        and _same(state.get("foundation"), binding["foundation"])
        and _same(
            {
                k: {"id": str(v.id), "content_hash": content_hash(v.content)}
                for k, v in selected.items()
            },
            binding["foundation"],
        )
        and all(
            _same(state.get(key), binding[key])
            for key in (
                "canonical_id",
                "canonical_hash",
                "chapter_projection_hashes",
                "historical_editorial_observations",
            )
        ),
        "source_selection_changed",
    )
    supplied = await admission._input(
        session,
        project=project,
        workflow_run_id=workflow.id,
        candidate_payload=candidate,
        book_spec=foundation["book_spec"].content,
        world_spec=foundation["world_spec"].content,
        cast_spec=foundation["cast_spec"].content,
        commercial_report={
            "schema": "historical-plan-review-observations.v1",
            **deepcopy(binding["historical_editorial_observations"]),
        },
        check_initial_context=True,
    )
    supplied = admission._input_for_contract(supplied, GRADE_SCHEMA)
    _need(
        protocol.get("graded_input_hash") == content_hash(supplied)
        and protocol.get("hard_admission_policy") == POLICY,
        "graded_input_or_policy_changed",
    )
    return project, workflow, artifact, state, supplied, reference_contract(supplied)


async def _inventory(session, project, workflow, supplied):
    reader = _Reader(session)
    scope = project.metadata_json["execution_scope_id"]
    wid = str(workflow.id)
    operation_id = admission._protocol_operation(
        project, workflow.id, content_hash(supplied), [], 0
    )
    root = hashlib.sha256(f"{scope}\n{operation_id}".encode()).hexdigest()
    # A physical return precedes telemetry. Query root keys AND JSON custody,
    # including orphaned transport/failure/provider-return rows and other scopes.
    operations = await reader.rows(
        db.ExecutionOperationModel,
        or_(
            db.ExecutionOperationModel.scope_id == scope,
            db.ExecutionOperationModel.key == root,
            cast(db.ExecutionOperationModel.result_json, String).contains(wid),
            cast(db.ExecutionOperationModel.result_json, String).contains(root),
        ),
    )
    related = [
        row
        for row in operations
        if row.key == root or _mentions(row.result_json, wid) or _mentions(row.result_json, root)
    ]
    _need(not related, "target_operation_or_physical_evidence")
    attempts = await reader.rows(
        db.ExecutionAttemptModel,
        or_(
            db.ExecutionAttemptModel.scope_id == scope,
            db.ExecutionAttemptModel.operation_key == root,
        ),
    )
    _need(not any(row.operation_key == root for row in attempts), "target_attempt_evidence")
    runs = await reader.rows(
        db.LlmRunModel,
        or_(
            db.LlmRunModel.workflow_run_id == workflow.id,
            cast(db.LlmRunModel.metadata_json, String).contains(wid),
            cast(db.LlmRunModel.metadata_json, String).contains(root),
        ),
    )
    _need(not runs, "target_telemetry_evidence")
    for owner in await reader.rows(
        db.WorkflowRunModel, db.WorkflowRunModel.project_id == project.id
    ):
        if (
            owner.id != workflow.id
            and (owner.metadata_json or {}).get("execution_scope_id") == scope
        ):
            _need(
                owner.status not in {"running", "pending", "queued", "stopping"},
                "other_workflow_active",
            )
    scoped_ops = [row for row in operations if row.scope_id == scope]
    _need(
        not any(row.status in {"pending", "unknown"} for row in scoped_ops),
        "scope_operation_unsettled",
    )
    budget = await reader.get(db.ExecutionBudgetModel, scope)
    _need(
        budget is not None and not _mentions(budget.metadata_json, wid),
        "target_budget_authority_evidence",
    )
    accounting = await _accounting(reader, scope, {})
    # Unknown attribution cannot be dismissed merely because telemetry is absent.
    # This narrow pre-claim migration requires the original quiescent inventory.
    _need(
        all(_utc(row.created_at) < _utc(workflow.created_at) for row in attempts),
        "late_scope_attempt_requires_attribution",
    )
    return {
        "schema": INVENTORY_SCHEMA,
        "workflow_run_id": wid,
        "scope_id": scope,
        "slot0_operation_id": operation_id,
        "slot0_root_key": root,
        "target_run_ids": [],
        "target_operation_keys": [],
        "target_attempt_ids": [],
        "accounting": accounting,
        "scope_attempts": _hashes(attempts, db.ExecutionAttemptModel, "id"),
        "scope_operations": _hashes(scoped_ops, db.ExecutionOperationModel, "key"),
        "latest_attempt_at": max((_utc(row.created_at) for row in attempts), default=None),
        "workflow_created_at": _utc(workflow.created_at),
        "reconciled_reserved_holds": sorted(
            row.id
            for row in attempts
            if row.status == "reconciled_charged" and row.actual_tokens is None
        ),
        "provider_dispatches": 0,
        "new_allowance_granted": False,
    }


async def verify_zero_paid_admission_inventory(session, settings, *, workflow_run_id):
    """Fresh source/control and complete zero-call proof; no builder or writes."""
    _clean_session(session)
    with session.no_autoflush:
        project, workflow, artifact, state, supplied, catalog = await _context(
            session, settings, workflow_run_id
        )
        inventory = await _inventory(session, project, workflow, supplied)
        return {
            "inventory": inventory,
            "observed_workflow_sha256": _row_hash(workflow, db.WorkflowRunModel),
            "artifact_id": str(artifact.id),
            "graded_input_hash": content_hash(supplied),
            "candidate_hash": state["candidate_hash"],
            "hard_admission_policy": POLICY,
            "reference_contract_sha256": content_hash(catalog),
        }


async def prepare_literal_admission_migration(
    session, settings, *, workflow_run_id, actor, authorization_ref, explicit_user_approval
):
    """Propose only metadata additions; the operator owns all CAS/commit work."""
    _need(
        explicit_user_approval is True
        and type(actor) is str
        and bool(actor.strip())
        and type(authorization_ref) is str
        and bool(authorization_ref.strip()),
        "explicit_authorization_required",
    )
    _clean_session(session)
    with session.no_autoflush:
        project, workflow, artifact, state, supplied, catalog = await _context(
            session, settings, workflow_run_id
        )
        inventory = await _inventory(session, project, workflow, supplied)
        builder = getattr(admission, "build_literal_admission_request", None)
        _need(callable(builder), "literal_builder_unavailable")
        request, contract = builder(
            settings,
            project=project,
            workflow_run_id=workflow.id,
            supplied=supplied,
            journal=[],
            index=0,
        )
        _need(
            request.workflow_run_id == workflow.id
            and request.project_id == project.id
            and request.logical_role == "critic"
            and request.prompt_template == admission.TEMPLATE
            and request.prompt_version == WIRE_VERSION
            and request.operation_id == inventory["slot0_operation_id"],
            "literal_request_identity_changed",
        )
        # Re-read after role/catalog/file work. Only one stable before-image may
        # be handed to the caller's whole-row CAS and financial fingerprint check.
        again = await _context(session, settings, workflow_run_id)
        after = await _inventory(session, again[0], again[1], again[4])
        _need(
            _same(inventory, after)
            and _row_hash(workflow, db.WorkflowRunModel) == _row_hash(again[1], db.WorkflowRunModel)
            and _same(state, again[3])
            and _same(supplied, again[4]),
            "context_changed_during_preparation",
        )
        old_protocol = deepcopy(state["protocol"])
        receipt = {
            "schema": SCHEMA,
            "wire_version": WIRE_VERSION,
            "workflow_run_id": str(workflow.id),
            "artifact_id": str(artifact.id),
            "observed_workflow_sha256": _row_hash(workflow, db.WorkflowRunModel),
            "original_state_sha256": content_hash(state),
            "original_state": deepcopy(state),
            "original_protocol_sha256": content_hash(old_protocol),
            "original_protocol": old_protocol,
            "graded_input_hash": content_hash(supplied),
            "candidate_hash": state["candidate_hash"],
            "hard_admission_policy": POLICY,
            "grade_schema": GRADE_SCHEMA,
            "reference_contract_sha256": content_hash(catalog),
            "inventory_sha256": content_hash(inventory),
            "request_contract": deepcopy(contract),
            "actor": actor,
            "authorization_ref": authorization_ref,
            "provider_dispatches": 0,
            "new_allowance_granted": False,
            "native_attempt_cap": 2,
        }
        proposed = deepcopy(state)
        proposed["protocol"]["wire_version"] = WIRE_VERSION
        proposed.setdefault("transport_migrations", []).append(deepcopy(receipt))
        return {
            "proposed_state": proposed,
            "inventory": inventory,
            "receipt": receipt,
            "observed_workflow_sha256": receipt["observed_workflow_sha256"],
            "provider_dispatches": 0,
            "new_allowance_granted": False,
        }
