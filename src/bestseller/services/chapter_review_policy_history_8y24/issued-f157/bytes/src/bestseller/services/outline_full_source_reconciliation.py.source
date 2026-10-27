"""Record a real full-outline source replacement without prefix attribution."""

from collections.abc import Mapping
from copy import deepcopy

from bestseller.services.prompt_source_policy import content_hash

MARKER_KEY = "outline_source_admission"
MARKER_HISTORY_KEY = "outline_source_admission_history"


def record_full_source_reconciliation(*, chapter, scenes, previous, source, receipt,
        workflow, requested_by):
    from bestseller.services.chapter_outline_readiness import outline_prefix_projection_hash

    if (not isinstance(receipt, Mapping)
            or receipt.get("source_artifact_id") != str(source.id)
            or receipt.get("source_content_sha256") != content_hash(source.content)
            or not receipt.get("workflow_run_id")
            or (receipt.get("source_admission") or {}).get("draft_eligible") is not True):
        raise ValueError("full_outline_reconciliation:verified_receipt_required")
    metadata = deepcopy(chapter.metadata_json or {})
    marker = {"artifact_id": str(source.id),
        "workflow_run_id": receipt["workflow_run_id"],
        "projection_hash": outline_prefix_projection_hash(chapter, scenes),
        "source_content_sha256": content_hash(source.content)}
    prior = metadata.get(MARKER_KEY)
    event = {"schema": "unadopted-full-outline-reconciliation.v1", **deepcopy(previous),
        "project_id": str(chapter.project_id), "chapter_id": str(chapter.id),
        "source_artifact_id": str(source.id), "source_content_hash": content_hash(source.content),
        "source_admission_receipt": deepcopy(dict(receipt)), "replacement": marker,
        "materialization_workflow_run_id": str(workflow.id), "requested_by": requested_by}
    history = list(metadata.get("outline_materialization_history") or [])
    if previous["previous_projection_hash"] != marker["projection_hash"] or prior != marker:
        owner = deepcopy(workflow.metadata_json or {})
        events = list(owner.get("unadopted_outline_reconciliation_history") or [])
        if event not in events:
            events.append(event)
        workflow.metadata_json = {**owner, "unadopted_outline_reconciliation_history": events}
        compact = {k: deepcopy(v) for k, v in event.items()
            if k not in {"previous_raw", "source_admission_receipt"}}
        if compact not in history:
            history.append(compact)
    markers = list(metadata.get(MARKER_HISTORY_KEY) or [])
    if prior is not None and prior != marker and prior not in markers:
        markers.append(deepcopy(prior))
    # A full-source replacement supersedes an old prefix projection, without
    # deleting its receipt or letting a reader select that stale active marker.
    old_prefix = metadata.pop("outline_prefix_readiness", None)
    if old_prefix is not None:
        prefix_history = list(metadata.get("outline_prefix_readiness_history") or [])
        if old_prefix not in prefix_history:
            prefix_history.append(deepcopy(old_prefix))
        metadata["outline_prefix_readiness_history"] = prefix_history
    chapter.metadata_json = {**metadata, "outline_materialization_history": history,
        MARKER_HISTORY_KEY: markers, MARKER_KEY: marker}
