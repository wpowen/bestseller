"""Project-bound conception options and the pending-snapshot approval bridge.

This module deliberately projects only evidence already produced by the
conception snapshot/tournament. It never generates hooks and never promotes a
premise card without a complete concept contract.
"""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
from typing import TYPE_CHECKING, Any
from uuid import UUID

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from bestseller.infra.db.models import ProjectModel


def _hash(value: object) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _contract_complete(content: Mapping[str, Any], *, target_chapters: int | None = None) -> bool:
    contract = content.get("concept_contract")
    if not isinstance(contract, Mapping):
        return False
    hook = contract.get("hook_card")
    seriality = contract.get("seriality_proof")
    spine = contract.get("story_spine") or content.get("story_spine")
    shape_complete = (
        isinstance(hook, Mapping)
        and bool(str(hook.get("one_liner") or "").strip())
        and isinstance(seriality, Mapping)
        and isinstance(spine, Mapping)
    )
    if not shape_complete:
        return False
    if target_chapters is None or contract.get("schema_version") != "concept-contract.v2":
        return False
    from bestseller.services.concept_contract import validate_concept_contract

    return not validate_concept_contract(contract, target_chapters=int(target_chapters))


def _candidate_option(
    candidate: Mapping[str, Any], *, attempt: int, log_hash: str, target_chapters: int
) -> dict[str, Any] | None:
    if candidate.get("rejected_reason"):
        return None
    contract = candidate.get("concept_contract")
    if not isinstance(contract, Mapping) or not _contract_complete(
        {"concept_contract": contract, "story_spine": contract.get("story_spine")},
        target_chapters=target_chapters,
    ):
        return None
    hook = contract.get("hook_card")
    if not isinstance(hook, Mapping):
        return None
    candidate_hash = _hash(candidate)
    return {
        "option_id": f"candidate:{attempt}:{candidate_hash[:16]}",
        "hook_card": dict(hook),
        "concept_contract": dict(contract),
        "source": {"attempt": attempt, "log_hash": log_hash, "candidate_hash": candidate_hash},
        "quality": {
            "scores": {key: value for key, value in candidate.items() if key.startswith("judge_")},
            "seriality_report": candidate.get("seriality_report"),
        },
    }


def build_conception_options(
    *,
    project: Mapping[str, Any],
    snapshot: Mapping[str, Any],
    conception_log: Mapping[str, Any] | None = None,
    authoritative_seed: str | None = None,
) -> dict[str, Any]:
    """Build a fail-closed, project-bound read projection."""
    content = snapshot.get("content") if isinstance(snapshot.get("content"), Mapping) else snapshot
    if not isinstance(content, Mapping):
        raise ValueError("conception snapshot content is invalid")
    snapshot_id = str(snapshot.get("id") or "")
    content_hash = _hash(content)
    contract = content.get("concept_contract")
    hook = contract.get("hook_card") if isinstance(contract, Mapping) else None
    target_chapters = project.get("target_chapters")
    if target_chapters in (None, "") or not _contract_complete(
        content, target_chapters=int(target_chapters)
    ):
        raise ValueError("pending conception snapshot lacks a complete concept contract")
    canonical = {
        "option_id": f"canonical:{snapshot_id}:{content_hash[:16]}",
        "hook_card": dict(hook),
        "concept_contract": dict(contract),
        "source": {
            "snapshot_id": snapshot_id,
            "snapshot_hash": content_hash,
            "attempt_id": content.get("attempt_id"),
            "workflow_run_id": snapshot.get("source_run_id"),
        },
        "quality": {"seriality_proof": contract.get("seriality_proof")},
    }
    alternatives: list[dict[str, Any]] = []
    log_hash = _hash(conception_log) if conception_log is not None else None
    if isinstance(conception_log, Mapping):
        for event in conception_log.get("conception_log", []):
            if not isinstance(event, Mapping) or event.get("agent") != "concept_tournament":
                continue
            attempt = int(event.get("attempt") or 0)
            for candidate in event.get("candidates", []):
                if isinstance(candidate, Mapping):
                    option = _candidate_option(
                        candidate,
                        attempt=attempt,
                        log_hash=log_hash or "",
                        target_chapters=int(target_chapters),
                    )
                    if option is not None:
                        alternatives.append(option)
    seed = str(authoritative_seed or "").strip()
    return {
        "project_slug": str(project.get("slug") or project.get("project_slug") or ""),
        "snapshot": {
            "id": snapshot_id,
            "status": snapshot.get("status"),
            "version_no": snapshot.get("version_no"),
            "content_hash": content_hash,
            "lock_version": project.get("lock_version"),
        },
        "authoritative_seed": {
            "text": seed,
            "hash": _hash(seed) if seed else None,
            "available": bool(seed),
            "source": "creation_intent" if seed else "unavailable",
        },
        "canonical": canonical,
        "alternatives": alternatives,
        "alternatives_source": {
            "available": conception_log is not None,
            "kind": "workflow_conception_log" if conception_log is not None else None,
        },
    }


def validate_canonical_approval_request(
    *,
    options: Mapping[str, Any],
    snapshot_id: str,
    source_hash: str,
    option_id: str,
) -> None:
    snapshot = options.get("snapshot")
    canonical = options.get("canonical")
    if not isinstance(snapshot, Mapping) or not isinstance(canonical, Mapping):
        raise ValueError("conception options are incomplete")
    if str(snapshot.get("id") or "") != snapshot_id:
        raise ValueError("snapshot_id does not match current project snapshot")
    if str(canonical.get("option_id") or "") != option_id:
        raise ValueError("option_id is not the current canonical option")
    actual = str(canonical.get("source", {}).get("snapshot_hash") or "")
    if actual != source_hash or actual != str(snapshot.get("content_hash") or ""):
        raise ValueError("conception source hash is stale")


def assert_conception_approved(project: ProjectModel) -> None:
    """Block writing only for projects explicitly stopped at conception."""
    metadata = getattr(project, "metadata_json", None)
    metadata = metadata if isinstance(metadata, Mapping) else {}
    awaiting = (
        str(metadata.get("planning_status") or "").strip().lower() == "awaiting_concept_approval"
    )
    if (metadata.get("conception_only") is True or awaiting) and metadata.get(
        "conception_approved"
    ) is not True:
        raise PermissionError("conception approval is required before entering writing")


def canonical_snapshot_premise(snapshot: Mapping[str, Any]) -> str:
    """Return premise only from a canonical, complete conception snapshot."""
    if str(snapshot.get("status") or "").strip().lower() != "canonical":
        raise ValueError("conception snapshot is not canonical")
    content = snapshot.get("content")
    if not isinstance(content, Mapping):
        raise ValueError("canonical conception snapshot content is invalid")
    premise = str(content.get("premise") or content.get("authoritative_seed") or "").strip()
    contract = content.get("concept_contract")
    target_chapters = contract.get("target_chapters") if isinstance(contract, Mapping) else None
    if (
        target_chapters in (None, "")
        or not premise
        or not _contract_complete(content, target_chapters=int(target_chapters))
    ):
        raise ValueError("canonical conception snapshot lacks a complete concept contract")
    return premise


def canonical_snapshot_content_hash(snapshot: Mapping[str, Any]) -> str:
    """Hash the exact canonical content used by downstream planning."""
    if str(snapshot.get("status") or "").strip().lower() != "canonical":
        raise ValueError("conception snapshot is not canonical")
    content = snapshot.get("content")
    if not isinstance(content, Mapping):
        raise ValueError("canonical conception snapshot content is invalid")
    return _hash(content)


async def prepare_adopted_design_snapshot(session: AsyncSession, project: ProjectModel) -> bool:
    """Hydrate the planning lock from an exact canonical adoption, without LLMs.

    Legacy or unnamed concepts keep their existing identity-resolution path.
    Missing/stale canonical evidence must never fall through to regeneration.
    This projection is an adopted model candidate, not a reconstructed human
    intent contract. Incomplete legacy intent provenance remains unchanged.
    """
    from sqlalchemy import select
    from bestseller.infra.db.models import PlanningArtifactVersionModel
    from bestseller.services.book_design import (
        ensure_project_book_design_snapshot, extract_creation_protagonist_name,
        _is_generic_protagonist_name,
    )

    metadata = dict(project.metadata_json or {})
    if metadata.get("conception_approved") is not True:
        return False
    source_id = metadata.get("creation_canonical_snapshot_id")
    if not source_id:
        return False
    snapshot = await session.scalar(select(PlanningArtifactVersionModel).where(
        PlanningArtifactVersionModel.id == UUID(str(source_id)),
        PlanningArtifactVersionModel.project_id == project.id,
        PlanningArtifactVersionModel.artifact_type == "conception_snapshot",
    ).with_for_update().execution_options(populate_existing=True))
    if snapshot is None or snapshot.status != "canonical":
        raise ValueError("canonical conception source is missing or superseded")
    source_hash = _hash(snapshot.content)
    if source_hash != metadata.get("creation_canonical_snapshot_hash"):
        raise ValueError("canonical conception source hash is stale")
    if not _contract_complete(snapshot.content, target_chapters=project.target_chapters):
        raise ValueError("canonical conception contract is incomplete")
    projected = _project_canonical_metadata(metadata, snapshot.content, snapshot.content["concept_contract"])
    projected["premise"] = canonical_snapshot_premise(
        {"status": snapshot.status, "content": snapshot.content}
    )
    name = extract_creation_protagonist_name({
        "premise": projected["premise"], "story_spine": projected["story_spine"],
    })
    cached_name = extract_creation_protagonist_name(projected)
    if name and cached_name and name != cached_name:
        raise ValueError("canonical conception conflicts with cached protagonist identity")
    if not name or _is_generic_protagonist_name(name):
        # Canonical premise still wins over a stale caller envelope. Identity
        # resolution remains a separate creative operation when no name exists.
        project.metadata_json = projected
        await session.flush()
        return False
    existing = metadata.get("book_design_adoption_source")
    receipt = {"kind": "adopted_conception", "snapshot_id": str(snapshot.id),
               "content_hash": source_hash}
    if existing and existing != receipt:
        raise ValueError("design lock belongs to a different conception adoption")
    existing_design = metadata.get("book_design_snapshot")
    if isinstance(existing_design, Mapping):
        design_source = existing_design.get("approved_story_design") or {}
        protagonist = existing_design.get("protagonist") or {}
        if (
            not isinstance(design_source, Mapping)
            or not isinstance(protagonist, Mapping)
            or design_source.get("source_contract_hash") != snapshot.content["concept_contract"].get("input_hash")
            or protagonist.get("name") != name
        ):
            raise ValueError("design lock belongs to a different conception adoption")
    project.metadata_json = projected
    ensure_project_book_design_snapshot(project, protagonist_name=name)
    project.metadata_json = {**project.metadata_json, "book_design_adoption_source": receipt}
    await session.flush()
    return True


def _project_canonical_metadata(
    metadata: Mapping[str, Any], content: Mapping[str, Any], contract: Mapping[str, Any]
) -> dict[str, Any]:
    """Project the canonical package into the metadata keys used by planning."""
    result = dict(metadata)
    result.update(
        {
            "concept_contract_version": "2",
            "concept_contract": dict(contract),
            "hook_card": dict(contract.get("hook_card") or {}),
            "seriality_proof": dict(contract.get("seriality_proof") or {}),
            "story_spine": dict(contract.get("story_spine") or content.get("story_spine") or {}),
        }
    )
    for key in (
        "hook_spec",
        "conception_artifacts",
        "synopsis",
        "tags",
        "concept_methodology",
        "conception_degraded",
        "conception_degradation_events",
    ):
        if key in content:
            result[key] = content[key]
    if "commercial_brief" in content:
        result["conception_brief"] = content["commercial_brief"]
    return result


def build_planning_handoff_payload(
    payload: Mapping[str, Any],
    *,
    project: ProjectModel,
    canonical_snapshot: Mapping[str, Any],
) -> dict[str, Any]:
    """Rebase the existing task envelope onto the approved snapshot."""
    assert_conception_approved(project)
    metadata = getattr(project, "metadata_json", None)
    metadata = metadata if isinstance(metadata, Mapping) else {}
    if (
        metadata.get("conception_approved") is not True
        or str(metadata.get("planning_status") or "").lower() != "conception_approved"
    ):
        raise PermissionError("approved conception is not ready for planning handoff")
    snapshot_id = str(canonical_snapshot.get("id") or "")
    if snapshot_id != str(metadata.get("creation_canonical_snapshot_id") or ""):
        raise ValueError("planning handoff snapshot is not the approved canonical source")
    content = canonical_snapshot.get("content")
    snapshot = {"status": canonical_snapshot.get("status"), "content": content}
    if not isinstance(content, Mapping) or not isinstance(content.get("concept_contract"), Mapping):
        raise ValueError("planning handoff canonical snapshot lacks a concept contract")
    content_hash = canonical_snapshot_content_hash(snapshot)
    if content_hash != str(metadata.get("creation_canonical_snapshot_hash") or ""):
        raise ValueError("planning handoff snapshot hash is inconsistent")
    result = dict(payload)
    result["metadata"] = _project_canonical_metadata(
        result.get("metadata") if isinstance(result.get("metadata"), Mapping) else {},
        content,
        content["concept_contract"],
    )
    result["premise"] = canonical_snapshot_premise(snapshot)
    result["approved_conception_snapshot_id"] = snapshot_id
    result["approved_conception_snapshot_hash"] = content_hash
    result["_run_conception"] = False
    result["planning_handoff"] = True
    result.pop("prewrite_review_approved", None)
    return result


async def approve_pending_conception(
    session: AsyncSession,
    *,
    project_id: UUID,
    snapshot_id: UUID,
    expected_lock_version: int,
    source_hash: str,
    option_id: str,
    idempotency_key: str,
    actor: str,
    reason: str,
) -> dict[str, Any]:
    """Atomically approve the existing snapshot; no premise-card promotion."""
    if not idempotency_key.strip():
        raise ValueError("idempotency_key is required")
    from sqlalchemy import select

    from bestseller.infra.db.models import PlanningArtifactVersionModel, ProjectModel
    from bestseller.services.conception_snapshots import promote_conception_snapshot, snapshot_hash

    project_query = select(ProjectModel).where(ProjectModel.id == project_id).with_for_update()
    project = await session.scalar(project_query)
    if project is None:
        raise ValueError("project not found")
    metadata = dict(project.metadata_json or {})
    prior = metadata.get("conception_approval_idempotency")
    request_fingerprint = {
        "snapshot_id": str(snapshot_id),
        "source_hash": source_hash,
        "option_id": option_id,
    }
    if isinstance(prior, Mapping) and prior.get("key") == idempotency_key:
        if prior.get("request") != request_fingerprint:
            raise ValueError("idempotency_key is already bound to a different approval request")
        return {
            "status": "already_approved",
            "snapshot_id": prior.get("snapshot_id"),
            "content_hash": prior.get("content_hash"),
        }
    snapshot_query = (
        select(PlanningArtifactVersionModel)
        .where(
            PlanningArtifactVersionModel.id == snapshot_id,
            PlanningArtifactVersionModel.project_id == project_id,
            PlanningArtifactVersionModel.artifact_type == "conception_snapshot",
        )
        .with_for_update()
    )
    snapshot = await session.scalar(snapshot_query)
    if snapshot is None or snapshot.project_id != project_id:
        raise ValueError("conception snapshot not found for project")
    if snapshot.artifact_type != "conception_snapshot":
        raise ValueError("snapshot is not a conception snapshot")
    if snapshot.status != "pending_user_approval":
        raise ValueError("snapshot is not pending user approval")
    if str(metadata.get("conception_snapshot_id") or "") != str(snapshot.id):
        raise ValueError("snapshot is not the current project conception")
    actual_hash = snapshot_hash(snapshot.content)
    if actual_hash != source_hash:
        raise ValueError("conception source hash is stale")
    if project.lock_version != expected_lock_version:
        raise ValueError("project lock_version changed; refusing stale approval")
    contract = (
        snapshot.content.get("concept_contract") if isinstance(snapshot.content, Mapping) else None
    )
    if not _contract_complete(
        snapshot.content, target_chapters=int(project.target_chapters)
    ) or not isinstance(contract, Mapping):
        raise ValueError("snapshot lacks complete concept contract")
    expected_option = f"canonical:{snapshot.id}:{actual_hash[:16]}"
    if option_id != expected_option:
        raise ValueError("option_id does not identify this snapshot")
    await promote_conception_snapshot(
        session,
        project_id,
        snapshot_id,
        expected_lock_version=expected_lock_version,
        actor=actor,
        reason=reason,
    )
    # Promotion appends its audit entry to snapshot.content, so the canonical
    # hash is intentionally recomputed after the transition.
    canonical_hash = snapshot_hash(snapshot.content)
    metadata = dict(project.metadata_json or {})
    metadata.pop("conception_only", None)
    metadata["planning_status"] = "conception_approved"
    metadata["conception_approved"] = True
    metadata["premise"] = str(snapshot.content.get("premise") or metadata.get("premise") or "")
    metadata["concept_contract_version"] = "2"
    metadata["concept_contract"] = dict(contract)
    metadata["hook_card"] = dict(contract.get("hook_card") or {})
    metadata["seriality_proof"] = dict(contract.get("seriality_proof") or {})
    metadata["story_spine"] = dict(
        contract.get("story_spine") or snapshot.content.get("story_spine") or {}
    )
    for key in (
        "hook_spec",
        "conception_artifacts",
        "commercial_brief",
        "synopsis",
        "tags",
        "concept_methodology",
        "conception_degraded",
        "conception_degradation_events",
    ):
        if key in snapshot.content:
            metadata["conception_brief" if key == "commercial_brief" else key] = snapshot.content[
                key
            ]
    intent_id = metadata.get("creation_intent_snapshot_id")
    if intent_id:
        intent_query = (
            select(PlanningArtifactVersionModel)
            .where(
                PlanningArtifactVersionModel.id == UUID(str(intent_id)),
                PlanningArtifactVersionModel.project_id == project_id,
                PlanningArtifactVersionModel.artifact_type == "creation_intent",
            )
            .with_for_update()
        )
        intent = await session.scalar(intent_query)
        if intent is None:
            raise ValueError("creation intent snapshot is missing")
        if intent.status != "validated" or intent.schema_version != "creation-intent.v1":
            raise ValueError("creation intent snapshot is not a validated creation contract")
        intent_content = intent.content if isinstance(intent.content, Mapping) else {}
        intent_hash = _hash(intent_content)
        if intent_hash != str(
            metadata.get("creation_intent_contract_hash") or ""
        ) or intent_hash != str(snapshot.content.get("creation_intent_hash") or ""):
            raise ValueError("creation intent snapshot hash does not match conception source")
        metadata["creation_intent_contract"] = dict(intent_content)
        metadata["concept_seed"] = str(intent_content.get("concept_seed") or "") or None
    metadata["creation_canonical_snapshot_id"] = str(snapshot.id)
    metadata["creation_canonical_snapshot_hash"] = canonical_hash
    metadata["conception_approval_idempotency"] = {
        "key": idempotency_key,
        "snapshot_id": str(snapshot.id),
        "content_hash": canonical_hash,
        "request": request_fingerprint,
    }
    project.metadata_json = metadata
    await session.flush()
    return {
        "status": "approved",
        "snapshot_id": str(snapshot.id),
        "content_hash": canonical_hash,
        "planning_status": metadata["planning_status"],
    }
