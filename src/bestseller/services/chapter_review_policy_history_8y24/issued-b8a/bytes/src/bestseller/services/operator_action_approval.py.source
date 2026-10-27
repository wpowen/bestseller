"""Trusted operator-only decisions; never an LLM tool or automatic retry hook.

The caller authenticates the human instruction. Narrative JSON, an active
completion budget, or an empty repair counter cannot create this authority.
No function commits, changes an execution budget, or dispatches a model.
"""
from copy import deepcopy
from uuid import UUID, NAMESPACE_URL, uuid5

from sqlalchemy import select

from bestseller.infra.db.models import WorkflowRunModel
from bestseller.services.prompt_source_policy import content_hash

PURPOSES = {
    "planning_source_resolution": {"resolve_exact_source"},
    "prefix_zero_send_recovery": {"reopen_unspent_transport"},
    "draft_continuity_mode": {"enable_pending_manuscript"},
    "draft_continuity_reference": {"reference_pending_manuscript"},
    "explicit_draft_text_edit": {"apply_exact_text_edits"},
    "bounded_additional_revision": {"select_working_source", "dispatch_once"},
    "working_repair_source_selection": {"select_for_existing_local_repair"},
    "prefix_source_proposal": {"review_exact_prefix"},
    "outline_candidate_edit": {"apply_candidate", "review_candidate"},
    "exact_unknown_new_business": {"isolate_unknown"},
}


def require(condition, code):
    if not condition:
        raise ValueError("operator_action_" + code)


async def record_operator_action_approval(session, *, purpose, binding, capabilities,
        actor, authorization_ref, explicit_user_approval=False):
    """Privileged application API. Absent explicit approval is a hard denial."""
    require(explicit_user_approval is True, "explicit_human_approval_required")
    require(purpose in PURPOSES and isinstance(binding, dict), "purpose_or_binding")
    require(isinstance(capabilities, list) and capabilities
            and len(set(capabilities)) == len(capabilities)
            and set(capabilities) <= PURPOSES[purpose], "capabilities")
    require(all(isinstance(v, str) and v.strip() for v in (actor, authorization_ref)), "actor_reference")
    project_id, target_id = UUID(binding["project_id"]), UUID(binding["target_id"])
    data = {"schema": "operator-action-approval.v1", "purpose": purpose,
        "binding": deepcopy(binding), "capabilities": sorted(capabilities),
        "actor": actor, "authorization_ref": authorization_ref,
        "source": "trusted_operator_api_explicit_human_instruction"}
    identity = uuid5(NAMESPACE_URL, "bestseller:operator-approval:" + content_hash(data))
    existing = await session.get(WorkflowRunModel, identity)
    if existing is not None:
        require(existing.metadata_json == data and existing.status == "completed", "approval_conflict")
        return existing.id
    session.add(WorkflowRunModel(id=identity, project_id=project_id,
        workflow_type="operator_action_approval", scope_type=purpose, scope_id=target_id,
        status="completed", current_step="approved", requested_by=actor[:64], metadata_json=data))
    await session.flush()
    return identity


async def require_operator_action_approval(session, *, approval_id, purpose, binding, capability):
    require(approval_id is not None, "approval_missing")
    row = await session.scalar(select(WorkflowRunModel).where(
        WorkflowRunModel.id == UUID(str(approval_id))).execution_options(populate_existing=True))
    require(row is not None and row.status == "completed"
            and row.workflow_type == "operator_action_approval"
            and row.project_id == UUID(binding["project_id"])
            and row.scope_type == purpose and row.scope_id == UUID(binding["target_id"]), "approval_scope")
    data = row.metadata_json or {}
    require(data.get("schema") == "operator-action-approval.v1"
            and data.get("source") == "trusted_operator_api_explicit_human_instruction"
            and data.get("purpose") == purpose and data.get("binding") == binding
            and capability in data.get("capabilities", [])
            and data.get("actor") and data.get("authorization_ref"), "approval_binding_or_capability")
    return deepcopy(data)
