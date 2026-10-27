"""Source-bound explicit candidate edits, separate from automatic repair credit."""
from copy import deepcopy
import hashlib
from pathlib import Path
import re
from uuid import UUID, NAMESPACE_URL, uuid5

from sqlalchemy import inspect, update

from bestseller.infra.db.models import ProjectModel, WorkflowRunModel
from bestseller.services import chapter_outline_readiness as ready, planner
from bestseller.services.creation_contract_authority import select_creation_contracts
from bestseller.services.operator_action_approval import require, require_operator_action_approval
from bestseller.services.prompt_source_policy import content_hash
from bestseller.services.retry_ledger import project_execution_scope

SCHEMA = "operator-outline-edit.v1"


def _policy():
    directory = Path(__file__).parent
    return {name: hashlib.sha256((directory / name).read_bytes()).hexdigest() for name in (
        "operator_outline_edit.py", "operator_action_approval.py", "chapter_outline_readiness.py",
        "source_bound_outline_integrity.py")}


def _source_fence(project, state):
    source, volume = ready._source_record(project, state["source_payload"])
    return content_hash({"source_record": source,
        "window_binding": ready._window_binding(project, state["source_payload"], volume),
        "book_design_snapshot": (project.metadata_json or {}).get("book_design_snapshot"),
        "creation_contracts": select_creation_contracts(project.metadata_json or {},
            source_bound=planner._source_bound_cast_enabled(project))})


def _apply(candidate, patches):
    require(isinstance(patches, list) and patches, "patches_required")
    result, seen = deepcopy(candidate), set()
    protocol = ready._projection_patch_protocol(candidate)
    require(len(patches) <= protocol["max_patches"], "patch_capacity")
    for patch in patches:
        require(isinstance(patch, dict) and set(patch) == {"op", "path", "value"}
                and patch["op"] == "replace", "replace_only")
        path = patch["path"]
        require(isinstance(path, str) and path not in seen
                and re.fullmatch(r"/chapters/(0|[1-9][0-9]*)(?:/[A-Za-z_][A-Za-z_0-9]*|/(0|[1-9][0-9]*))+", path), "patch_path")
        seen.add(path)
        tokens = path.strip("/").split("/")
        require("_meta" not in tokens, "producer_metadata_immutable")
        if tokens[2] == "scenes":
            require(len(tokens) >= 5 and tokens[3].isdigit()
                    and tokens[4] in protocol["fields"]["scene"], "scene_field")
        else:
            require(tokens[2] in protocol["fields"]["chapter"], "chapter_field")
        cursor = result
        for token in tokens[:-1]:
            cursor = cursor[int(token)] if isinstance(cursor, list) else cursor[token]
        key = int(tokens[-1]) if isinstance(cursor, list) else tokens[-1]
        require(type(cursor[key]) is type(patch["value"]), "field_type")
        require(cursor[key] != patch["value"], "no_op_patch")
        cursor[key] = deepcopy(patch["value"])
    return result


async def _context(session, project_id, workflow_id, lock=False):
    require(not session.new and not session.dirty and not session.deleted, "clean_session_required")
    project = await session.get(ProjectModel, project_id, populate_existing=True, with_for_update=lock)
    workflow = await session.get(WorkflowRunModel, workflow_id, populate_existing=True, with_for_update=lock)
    require(project is not None and workflow is not None
            and ready._workflow_scope(workflow, project.id), "outline_scope")
    return project, workflow


async def prepare_operator_outline_edit(session, *, project_id, workflow_id,
        expected_candidate_sha256, patches):
    project, workflow = await _context(session, project_id, workflow_id)
    state = deepcopy(workflow.metadata_json or {})
    require(workflow.status == "machine_blocked" and state.get("phase") == "rejected", "blocked_candidate_required")
    require(state.get("candidates"), "candidate_missing")
    parent = state["candidates"][-1]
    require(parent["candidate_hash"] == expected_candidate_sha256
            == content_hash(parent["candidate"]), "candidate_changed")
    require(ready.prefix_json_lineage_status(state)["status"] == "current", "parent_lineage")
    foundation, contents = await ready._foundation(session, project)
    require(foundation == state["foundation"] == state["identity"]["foundation"], "source_changed")
    await ready._assert_unadopted_range(session, project_id=project.id, numbers=state["chapter_numbers"])
    proposed = _apply(parent["candidate"], patches)
    clone = ProjectModel(**{field.key: deepcopy(getattr(project, field.key))
        for field in inspect(ProjectModel).column_attrs})
    validated = ready._validate(deepcopy(proposed), project=clone, state=deepcopy(state), cast=contents["cast_spec"])
    require(validated == proposed, "validator_changed_proposal")
    plan = {"schema": SCHEMA, "project_id": str(project.id), "target_id": str(workflow.id),
        "scope": project_execution_scope(project), "before_state_sha256": content_hash(state),
        "before_status": workflow.status, "before_step": workflow.current_step,
        "parent_sha256": expected_candidate_sha256, "candidate_sha256": content_hash(proposed),
        "patches": deepcopy(patches), "source_identity": deepcopy(state["identity"]),
        "foundation": foundation, "source_snapshot_sha256": _source_fence(project, state),
        "policy": _policy(), "candidate": proposed,
        "automatic_attempts_sha256": content_hash({k: state.get(k, []) for k in ("patch_attempts", "hard_patch_attempts")}),
        "old_report_sha256": content_hash(parent.get("report")),
        "automatic_credit_granted": False, "adoption_authorized": False}
    plan["plan_sha256"] = content_hash(plan)
    return plan


def approval_binding(plan):
    return {key: deepcopy(plan[key]) for key in ("project_id", "target_id", "scope", "plan_sha256",
        "parent_sha256", "candidate_sha256", "foundation", "policy")}


async def apply_operator_outline_edit(session, *, project_id, workflow_id,
        expected_candidate_sha256, patches, expected_plan_sha256, approval_id=None):
    require(approval_id is not None, "approval_missing")
    project, workflow = await _context(session, project_id, workflow_id, lock=True)
    receipt_id = uuid5(NAMESPACE_URL, "bestseller:outline-edit:" + str(approval_id))
    prior = await session.get(WorkflowRunModel, receipt_id)
    if prior is not None:
        data = prior.metadata_json
        require(data["plan"]["plan_sha256"] == expected_plan_sha256
                and data["plan"]["patches"] == patches and prior.project_id == project_id
                and prior.scope_id == workflow_id, "edit_replay_binding")
        await require_operator_action_approval(session, approval_id=approval_id,
            purpose="outline_candidate_edit", binding=approval_binding(data["plan"]), capability="apply_candidate")
        return {"status": "already_applied", "receipt_id": str(receipt_id)}
    plan = await prepare_operator_outline_edit(session, project_id=project_id, workflow_id=workflow_id,
        expected_candidate_sha256=expected_candidate_sha256, patches=patches)
    require(plan["plan_sha256"] == expected_plan_sha256, "plan_changed")
    await require_operator_action_approval(session, approval_id=approval_id,
        purpose="outline_candidate_edit", binding=approval_binding(plan), capability="apply_candidate")
    old = deepcopy(workflow.metadata_json)
    state = deepcopy(old)
    ref = {"receipt_id": str(receipt_id), "approval_id": str(approval_id),
           "candidate_sha256": plan["candidate_sha256"], "plan_sha256": plan["plan_sha256"]}
    state.setdefault("operator_outline_edits", []).append(ref)
    state["candidates"].append({"candidate": deepcopy(plan["candidate"]),
        "candidate_hash": plan["candidate_sha256"], "validated": True, "operator_edit": ref})
    state.setdefault("operator_edit_prior_outcomes", []).append({
        key: state.pop(key) for key in ("final_report", "failure", "reason", "artifact_id", "receipt") if key in state})
    state["phase"] = "awaiting_operator_review_authorization"
    result = await session.execute(update(WorkflowRunModel).where(
        WorkflowRunModel.id == workflow.id, WorkflowRunModel.metadata_json == old,
        WorkflowRunModel.status == plan["before_status"], WorkflowRunModel.current_step == plan["before_step"]
    ).values(metadata_json=state, current_step=state["phase"]).execution_options(synchronize_session=False))
    require(result.rowcount == 1, "concurrent_state_changed")
    session.add(WorkflowRunModel(id=receipt_id, project_id=project.id, workflow_type="operator_outline_edit",
        scope_type="workflow", scope_id=workflow.id, status="completed", current_step="candidate_only",
        metadata_json={"schema": SCHEMA, "plan": plan, "approval_id": str(approval_id),
            "previous_state": old, "old_report_applies_to_candidate": False,
            "model_result_claimed": False, "automatic_credit_granted": False}))
    await session.flush()
    await session.refresh(workflow)
    return {"status": "applied_candidate_only", "receipt_id": str(receipt_id),
            "candidate_sha256": plan["candidate_sha256"], "new_model_calls": 0}


async def validate_operator_candidate_for_review(session, *, project, workflow, state):
    current = state["candidates"][-1]
    entries = state.get("operator_outline_edits", [])
    if not current.get("operator_edit") and not entries:
        return
    matches = [ref for ref in entries if ref.get("candidate_sha256") == current["candidate_hash"]]
    if not current.get("operator_edit") and not matches:
        return
    require(len(matches) == 1 and matches[0] == current.get("operator_edit"), "edit_lineage_missing")
    ref = matches[0]
    row = await session.get(WorkflowRunModel, UUID(ref["receipt_id"]), populate_existing=True)
    require(row is not None and row.workflow_type == "operator_outline_edit"
            and row.project_id == project.id and row.scope_id == workflow.id and row.status == "completed", "edit_receipt")
    plan = row.metadata_json["plan"]
    require(content_hash({k: v for k, v in plan.items() if k != "plan_sha256"}) == plan.get("plan_sha256"),
            "edit_plan_corrupt")
    require(plan["plan_sha256"] == ref["plan_sha256"]
            and content_hash(current["candidate"]) == plan["candidate_sha256"]
            and current["candidate"] == plan["candidate"] and state["identity"] == plan["source_identity"]
            and _policy() == plan["policy"], "edit_source_or_body_changed")
    parents = [c for c in state["candidates"] if c["candidate_hash"] == plan["parent_sha256"]]
    require(len(parents) == 1 and content_hash(parents[0]["candidate"]) == plan["parent_sha256"]
            and _apply(parents[0]["candidate"], plan["patches"]) == current["candidate"], "edit_parent_changed")
    require(content_hash({k: state.get(k, []) for k in ("patch_attempts", "hard_patch_attempts")})
            == plan["automatic_attempts_sha256"], "automatic_attempts_changed")
    foundation, _ = await ready._foundation(session, project)
    require(foundation == plan["foundation"], "source_changed")
    require(_source_fence(project, state) == plan["source_snapshot_sha256"], "creator_source_changed")
    await require_operator_action_approval(session, approval_id=ref["approval_id"],
        purpose="outline_candidate_edit", binding=approval_binding(plan), capability="review_candidate")
