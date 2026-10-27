"""Freeze a source-bound recompilation for review, without canonical writes."""

from collections.abc import Mapping
from copy import deepcopy
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

from bestseller.services.story_rebuild_workspace import source_content_hash

if TYPE_CHECKING:
    from bestseller.infra.db.models import PlanningArtifactVersionModel, ProjectModel

RECOMPILE_VERSION = "world-recompile-candidate.v1"
_PROJECT_FIELDS = (
    "title",
    "slug",
    "language",
    "genre",
    "sub_genre",
    "audience",
    "target_chapters",
)
# Inputs read by the two compilers and their project-dependent helpers. Never
# copy provider credentials, mutable production state, or unrelated metadata.
_METADATA_FIELDS = (
    "book_design_snapshot",
    "book_design_snapshot_hash",
    "book_design_snapshot_id",
    "book_design_snapshot_status",
    "creation_protagonist_name",
    "hook_card",
    "writing_profile",
    "logline",
    "golden_finger",
    "growth_curve",
    "story_enhancers",
    "creation_intent_contract",
    "genre_intent_contract",
)


def build_world_recompile_candidate(
    project: "ProjectModel",
    *,
    premise: str,
    source_artifact: "PlanningArtifactVersionModel",
    premise_artifact_id: str,
) -> dict[str, Any]:
    """Compile from detached, explicitly frozen inputs; retain all conflicts."""
    from bestseller.services.planner import (
        _compile_source_bound_book_spec,
        _compile_source_bound_world_spec,
    )

    metadata = getattr(project, "metadata_json", None)
    if not isinstance(metadata, Mapping):
        raise ValueError("RECOMPILE_SNAPSHOT_MISSING")
    snapshot = metadata.get("book_design_snapshot")
    current_hash = metadata.get("book_design_snapshot_hash")
    if (
        not isinstance(snapshot, Mapping)
        or not isinstance(current_hash, str)
        or not current_hash.strip()
        or metadata.get("book_design_snapshot_status") != "locked"
        or snapshot.get("source_hash") != current_hash
        or not snapshot.get("snapshot_id")
        or not snapshot.get("schema_version")
    ):
        raise ValueError("RECOMPILE_SNAPSHOT_NOT_LOCKED_OR_INCONSISTENT")
    if not isinstance(premise, str) or not premise.strip() or not premise_artifact_id:
        raise ValueError("RECOMPILE_PREMISE_MISSING")
    if not isinstance(getattr(source_artifact, "content", None), Mapping):
        raise ValueError("RECOMPILE_SOURCE_INVALID")
    if getattr(source_artifact, "status", None) != "approved":
        raise ValueError("RECOMPILE_APPROVED_WORLD_REQUIRED")
    inputs: dict[str, Any] = deepcopy(
        {
            "project": {key: getattr(project, key, None) for key in _PROJECT_FIELDS},
            "metadata": {key: metadata[key] for key in _METADATA_FIELDS if key in metadata},
            "premise": premise,
            "premise_artifact_id": str(premise_artifact_id),
        }
    )
    detached = cast(
        "ProjectModel",
        SimpleNamespace(
            **deepcopy(inputs["project"]),
            metadata_json=deepcopy(inputs["metadata"]),
        ),
    )
    book = _compile_source_bound_book_spec(detached, premise)
    world = _compile_source_bound_world_spec(detached, premise, book)
    world["_meta"].update(
        {
            "source_snapshot_id": snapshot["snapshot_id"],
            "source_snapshot_hash": current_hash,
            "source_snapshot_version": snapshot["schema_version"],
            "recompile_input_hash": source_content_hash(inputs),
        }
    )
    source: dict[str, Any] = {
        "id": str(source_artifact.id),
        "version_no": source_artifact.version_no,
        "status": source_artifact.status,
        "schema_version": getattr(source_artifact, "schema_version", None),
        "content": deepcopy(dict(source_artifact.content)),
    }
    source_meta = source["content"].get("_meta")
    old_hash = source_meta.get("source_snapshot_hash") if isinstance(source_meta, Mapping) else None
    return {
        "version": RECOMPILE_VERSION,
        "status": "needs_review",
        "project_id": str(project.id),
        "source_artifact": source,
        "source_hash": source_content_hash(source),
        "source_snapshot_matches_current": old_hash == current_hash,
        "frozen_inputs": inputs,
        "input_hash": source_content_hash(inputs),
        "candidate_book": book,
        "book_hash": source_content_hash(book),
        "candidate_world": world,
        "candidate_hash": source_content_hash(world),
        "production_ready": False,
        "promotion_allowed": False,
        "new_llm_calls": 0,
        "pending_gates": ["semantic_conflict_review", "source_alias_sync", "atomic_promotion"],
    }


def verify_world_recompile_candidate(candidate: Mapping[str, Any]) -> None:
    """Check stored receipt integrity, not source authority or semantic quality."""
    if candidate.get("version") != RECOMPILE_VERSION:
        raise ValueError("RECOMPILE_VERSION_MISMATCH")
    if (
        candidate.get("status") != "needs_review"
        or candidate.get("production_ready") is not False
        or candidate.get("promotion_allowed") is not False
        or candidate.get("new_llm_calls") != 0
    ):
        raise ValueError("RECOMPILE_NOT_A_DIAGNOSTIC")
    for field, hash_key in (
        ("source_artifact", "source_hash"),
        ("frozen_inputs", "input_hash"),
        ("candidate_book", "book_hash"),
        ("candidate_world", "candidate_hash"),
    ):
        if not isinstance(candidate.get(field), Mapping) or (
            source_content_hash(candidate[field]) != candidate.get(hash_key)
        ):
            raise ValueError(f"RECOMPILE_HASH_MISMATCH:{field}")


def revise_world_recompile_candidate(
    parent: Mapping[str, Any], *, parent_report_id: str, edit: Mapping[str, Any],
) -> dict[str, Any]:
    """Propose one upstream deletion, never borrow semantic approval.

    A deletion can still change meaning. Preserve its exact spans and require
    fresh semantic review. The original
    project, immutable creation snapshot, and canonical artifact are untouched.
    """
    from bestseller.services.world_source_patch import _deleted_spans

    verify_world_recompile_candidate(parent)
    if not parent_report_id or "revision" in parent:
        raise ValueError("WORLD_INPUT_REVISION_REQUIRES_BASE_PARENT")
    if set(edit) != {"path", "before", "after", "rationale"}:
        raise ValueError("WORLD_INPUT_REVISION_INVALID_EDIT")
    path = ["writing_profile", "world", "power_system_style"]
    if edit["path"] != path:
        raise ValueError("WORLD_INPUT_REVISION_PROTECTED_FIELD")
    if any(not isinstance(edit[key], str) or not edit[key].strip()
           for key in ("before", "after", "rationale")):
        raise ValueError("WORLD_INPUT_REVISION_EMPTY_TEXT")
    if len(edit["after"]) > 12000 or len(edit["rationale"]) > 2000:
        raise ValueError("WORLD_INPUT_REVISION_EXCESSIVE_TEXT")
    if edit["before"] == edit["after"]:
        raise ValueError("WORLD_INPUT_REVISION_NO_CHANGE")
    deleted_spans = _deleted_spans(edit["before"], edit["after"])
    inputs = deepcopy(parent["frozen_inputs"])
    node = inputs["metadata"]
    for key in path[:-1]:
        if not isinstance(node.get(key), Mapping):
            raise ValueError("WORLD_INPUT_REVISION_MISSING_PATH")
        node = node[key]
    if node.get(path[-1]) != edit["before"]:
        raise ValueError("WORLD_INPUT_REVISION_STALE_TEXT")
    node[path[-1]] = edit["after"]
    detached = cast("ProjectModel", SimpleNamespace(
        **inputs["project"], id=parent["project_id"], metadata_json=inputs["metadata"],
    ))
    source = cast("PlanningArtifactVersionModel", SimpleNamespace(**parent["source_artifact"]))
    result = build_world_recompile_candidate(
        detached, premise=inputs["premise"], source_artifact=source,
        premise_artifact_id=inputs["premise_artifact_id"],
    )
    result["revision"] = {
        "parent_report_id": parent_report_id,
        "parent_receipt_hash": source_content_hash(parent),
        "edit": deepcopy(dict(edit)),
        "deleted_spans": deleted_spans,
        "authority": "proposal_only_requires_semantic_review",
    }
    return result


def verify_world_input_revision(
    candidate: Mapping[str, Any], *, parent: Mapping[str, Any], parent_report_id: str,
) -> None:
    """Rebuild the complete proposal from its bound parent before consuming it."""
    revision = candidate.get("revision")
    if not isinstance(revision, Mapping) or not isinstance(revision.get("edit"), Mapping):
        raise ValueError("WORLD_INPUT_REVISION_MISSING_RECEIPT")
    rebuilt = revise_world_recompile_candidate(
        parent, parent_report_id=parent_report_id, edit=revision["edit"],
    )
    if dict(candidate) != rebuilt:
        raise ValueError("WORLD_INPUT_REVISION_RECEIPT_MISMATCH")
