"""Explicit additive-catalog provenance transition for retained source reviews.

Current physical source proofs are still recomputed by the caller. This receipt
only explains three catalog-file digests; it never changes an old paid request,
source, verdict, admission, or generation allowance. No provider or commit here.
"""

from copy import deepcopy
import hashlib
from uuid import NAMESPACE_URL, UUID, uuid5
import yaml
from bestseller.infra.db.models import WorkflowRunModel
from bestseller.services.prompt_source_policy import content_hash

SCHEMA = "foundation-additive-catalog-transition.v2"
PURPOSE = "foundation_catalog_transition"
# Reuse the existing privileged source-resolution capability; changing the
# global purpose registry invalidates unrelated historical source-plan policies.
APPROVAL_PURPOSE = "planning_source_resolution"
CAPABILITY = "resolve_exact_source"
PATHS = (
    ("legacy_source_reviews", "world"),
    ("legacy_source_reviews", "cast"),
    ("unchanged_component_axes",),
)


def need(value, code):
    if not value:
        raise ValueError("foundation_catalog_transition_" + code)


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def _entries(text):
    data = yaml.safe_load(text)
    need(isinstance(data, dict) and isinstance(data.get("models"), list), "catalog_shape")
    entries = {}
    from bestseller.services.model_catalog import ModelCatalogEntry

    for item in data["models"]:
        entry = ModelCatalogEntry.model_validate(item)
        need(entry.id not in entries, "duplicate_catalog_id")
        entries[entry.id] = item
    return entries


def prove_catalog_component_transition(
    *, components, original_component_sha256, original_catalog_text, current_catalog_text
):
    """Compare all components exactly, permitting only three approved leaves."""
    old, current = _entries(original_catalog_text), _entries(current_catalog_text)
    need(
        set(old) < set(current) and all(current[key] == value for key, value in old.items()),
        "catalog_not_additive",
    )
    # Top-level settings must also be identical. No unrelated policy additions.
    old_data, new_data = yaml.safe_load(original_catalog_text), yaml.safe_load(current_catalog_text)
    need(
        {k: v for k, v in old_data.items() if k != "models"}
        == {k: v for k, v in new_data.items() if k != "models"},
        "catalog_policy_changed",
    )
    need(components.get("schema") == "verified-volume-source-components.v1", "component_schema")
    historical = deepcopy(components)
    old_sha, new_sha = sha(original_catalog_text), sha(current_catalog_text)
    for path in PATHS:
        node = historical
        for key in path:
            need(isinstance(node, dict) and key in node, "component_shape")
            node = node[key]
        need(
            isinstance(node, dict) and node.get("catalog_sha256") == new_sha,
            "current_catalog_binding",
        )
        node["catalog_sha256"] = old_sha
    need(content_hash(historical) == original_component_sha256, "noncatalog_components_changed")
    return {
        "schema": SCHEMA,
        "original_component_sha256": original_component_sha256,
        "current_component_sha256": content_hash(components),
        "original_catalog_sha256": old_sha,
        "current_catalog_sha256": new_sha,
        "added_model_ids": sorted(set(current) - set(old)),
        "changed_paths": ["/" + "/".join(path) + "/catalog_sha256" for path in PATHS],
        "original_routes_unchanged": True,
        "old_grade_relabelled": False,
        "new_grade_or_credit_granted": False,
    }


def identity(project_id, review_id, current_component_sha256):
    return uuid5(
        NAMESPACE_URL,
        SCHEMA + ":" + str(project_id) + ":" + str(review_id) + ":" + current_component_sha256,
    )


def _binding(project_id, review_id, proof):
    target = identity(project_id, review_id, proof["current_component_sha256"])
    return {
        "project_id": str(project_id),
        "target_id": str(target),
        "admission_workflow_id": str(review_id),
        "proof_sha256": content_hash(proof),
    }


async def record_catalog_component_transition(
    session,
    *,
    project_id,
    review_id,
    components,
    original_catalog_text,
    actor,
    authorization_ref,
    explicit_user_approval=False,
):
    from bestseller.services import model_catalog
    from bestseller.services.operator_action_approval import record_operator_action_approval

    need(explicit_user_approval is True, "explicit_authority_required")
    review = await session.get(WorkflowRunModel, UUID(str(review_id)), populate_existing=True)
    need(
        review is not None
        and review.project_id == project_id
        and review.workflow_type == "review_volume_source_admission"
        and review.scope_type == "project"
        and review.scope_id == project_id,
        "admission_scope",
    )
    need(
        components.get("project_id") == str(project_id)
        and components.get("revision_id")
        == review.metadata_json.get("source_dependency_revision_id"),
        "component_scope",
    )
    original_hash = review.metadata_json.get("source_component_receipt_hash")
    proof = prove_catalog_component_transition(
        components=components,
        original_component_sha256=original_hash,
        original_catalog_text=original_catalog_text,
        current_catalog_text=model_catalog._CATALOG_PATH.read_text(),
    )
    binding = _binding(project_id, review.id, proof)
    approval = await record_operator_action_approval(
        session,
        purpose=APPROVAL_PURPOSE,
        binding=binding,
        capabilities=[CAPABILITY],
        actor=actor,
        authorization_ref=authorization_ref,
        explicit_user_approval=True,
    )
    data = {
        "schema": SCHEMA,
        "binding": binding,
        "proof": proof,
        "original_catalog_text": original_catalog_text,
        "approval_id": str(approval),
    }
    target = UUID(binding["target_id"])
    row = await session.get(WorkflowRunModel, target, populate_existing=True)
    if row is None:
        session.add(
            WorkflowRunModel(
                id=target,
                project_id=project_id,
                workflow_type=PURPOSE,
                scope_type="project",
                scope_id=project_id,
                status="completed",
                current_step="historical_provenance_verified",
                requested_by=actor[:64],
                metadata_json=data,
            )
        )
        await session.flush()
    else:
        need(row.status == "completed" and row.metadata_json == data, "receipt_conflict")
    return {"workflow_id": str(target), "approval_id": str(approval), "proof": proof}


async def require_catalog_component_transition(session, *, project, review, components):
    from bestseller.services import model_catalog
    from bestseller.services.operator_action_approval import require_operator_action_approval

    target = identity(project.id, review.id, content_hash(components))
    row = await session.get(WorkflowRunModel, target, populate_existing=True)
    need(
        row is not None
        and row.project_id == project.id
        and row.scope_id == project.id
        and row.scope_type == "project"
        and row.workflow_type == PURPOSE
        and row.status == "completed",
        "receipt_missing_or_scope",
    )
    data = row.metadata_json or {}
    need(
        data.get("schema") == SCHEMA and isinstance(data.get("original_catalog_text"), str),
        "receipt_shape",
    )
    proof = prove_catalog_component_transition(
        components=components,
        original_component_sha256=review.metadata_json.get("source_component_receipt_hash"),
        original_catalog_text=data["original_catalog_text"],
        current_catalog_text=model_catalog._CATALOG_PATH.read_text(),
    )
    need(
        data.get("proof") == proof
        and data.get("binding") == _binding(project.id, review.id, proof),
        "receipt_changed",
    )
    await require_operator_action_approval(
        session,
        approval_id=data.get("approval_id"),
        purpose=APPROVAL_PURPOSE,
        binding=data["binding"],
        capability=CAPABILITY,
    )
    return proof["original_component_sha256"]


async def verified_component_identity(session, *, project, review, components):
    """One consumer boundary for both foundation selection and volume input."""
    digest = content_hash(components)
    if digest == review.metadata_json.get("source_component_receipt_hash"):
        return digest
    return await require_catalog_component_transition(
        session, project=project, review=review, components=components
    )
