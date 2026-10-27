"""Explicit migration of one closed, spent planning slot; no model dispatch."""

from copy import deepcopy

from sqlalchemy import update

from bestseller.infra.db.models import WorkflowRunModel
from bestseller.services.known_terminal_planning_attempt import verify_terminal_planning_slot
from bestseller.services.prompt_source_policy import content_hash

SCHEMA = "terminal-planning-slot-migration.v1"


async def record_terminal_planning_slot(session, settings, *, project, workflow_id, supplied, item):
    """Preserve failed workflow evidence and its original two-slot protocol.

    Accounting reconciliation is a separate explicit operator action. A crash
    after it can resume this idempotent migration without clearing any counter.
    The caller owns commit; this never changes project/control state.
    """
    from bestseller.services import planning_draft_admission as admission
    from bestseller.services.retained_planning_generation import _Reader

    scope = await verify_terminal_planning_slot(session, settings, project=project,
        workflow_id=workflow_id, supplied=supplied, item=item, index=0)
    current = await _Reader(session).get(WorkflowRunModel, workflow_id)
    if (current is None or current.project_id != project.id or current.status != "failed"
            or current.current_step != "execution_result_unknown"
            or current.error_message != "execution_result_unknown"):
        raise ValueError("terminal_planning_migration:failed_owner_required")
    state = deepcopy(current.metadata_json or {})
    protocol = state.get("protocol")
    digest = content_hash(supplied)
    if supplied.get("hard_admission_policy") != admission.HARD_ADMISSION_POLICY:
        raise ValueError("terminal_planning_migration:source_policy_changed")
    if protocol == {}:
        # Older producers checkpointed the workflow before initializing the
        # journal. The exact original paid request is independently proved;
        # this is a spent slot, never a fresh first attempt.
        if state.get("candidate_hash") != content_hash(supplied["candidate_payload"]):
            raise ValueError("terminal_planning_migration:owner_candidate_changed")
        protocol.update(graded_input_hash=digest,
            hard_admission_policy=admission.HARD_ADMISSION_POLICY,
            candidate_hash=content_hash(supplied["candidate_payload"]), attempts=[], status="pending")
    if (not isinstance(protocol, dict) or protocol.get("graded_input_hash") != digest
            or protocol.get("hard_admission_policy") != admission.HARD_ADMISSION_POLICY
            or protocol.get("candidate_hash") != content_hash(supplied["candidate_payload"])):
        raise ValueError("terminal_planning_migration:protocol_binding_changed")
    receipt = {"schema": SCHEMA, "project_id": str(project.id),
        "workflow_run_id": str(workflow_id), "scope_id": scope,
        "graded_input_hash": digest, "evidence_sha256": item["evidence_sha256"],
        "reconciliation_id": item["reconciliation_id"], "journal_item_sha256": content_hash(item),
        "spent_slot": 0, "protocol_attempt_limit": admission.MAX_PROTOCOL_REPAIRS + 1,
        "original_workflow_failure": {"status": current.status,
            "current_step": current.current_step, "error_message": current.error_message},
        "provider_dispatches": 0, "new_allowance_granted": False,
        "grade_granted": False, "automatic_resume": False}
    previous = state.get("terminal_planning_migration")
    if previous is not None:
        if previous != receipt or protocol.get("attempts", [])[:1] != [item]:
            raise ValueError("terminal_planning_migration:existing_receipt_changed")
        return deepcopy(receipt)
    if protocol.get("attempts") != [] or protocol.get("execution_scope") not in (None, scope):
        raise ValueError("terminal_planning_migration:slot_already_occupied")
    protocol.update(attempts=[deepcopy(item)], execution_scope=scope, status="protocol_unknown")
    state["terminal_planning_migration"] = receipt
    with session.no_autoflush:
        changed = await session.execute(update(WorkflowRunModel).where(
            WorkflowRunModel.id == workflow_id, WorkflowRunModel.status == current.status,
            WorkflowRunModel.current_step == current.current_step,
            WorkflowRunModel.metadata_json == current.metadata_json,
        ).values(metadata_json=state).execution_options(synchronize_session=False))
    if changed.rowcount != 1:
        raise ValueError("terminal_planning_migration:owner_changed")
    return deepcopy(receipt)
