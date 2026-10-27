"""Repair a stale concept contract before resuming an empty book.

This module owns the narrow *existing project* recovery boundary.  It is
intentionally separate from conception and planning: conception supplies a
trusted contract builder, while this service guards the project, replaces all
contract mirrors atomically, rebuilds the design snapshot, and retires
derived planning artifacts that could otherwise be replayed after a failed
foundation run.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import UTC, datetime
import hashlib
import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.domain.enums import ArtifactType, WorkflowStatus
from bestseller.infra.db.models import (
    ChapterModel,
    PlanningArtifactVersionModel,
    ProjectModel,
    WorkflowRunModel,
)
from bestseller.services.book_design import ensure_project_book_design_snapshot
from bestseller.services.concept_contract import (
    CONCEPT_CONTRACT_VERSION,
    build_concept_contract,
    validate_concept_contract,
)


class ConceptContractResumeRepairError(RuntimeError):
    """A project is not safe to mutate through the empty-book repair path."""


@dataclass(frozen=True)
class ConceptContractResumeRepairResult:
    status: str
    source_signature: str
    old_contract_hash: str | None
    new_contract_hash: str | None
    superseded_artifact_count: int = 0
    snapshot_id: str | None = None
    reason: str | None = None
    force_foundation_replan: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "source_signature": self.source_signature,
            "old_contract_hash": self.old_contract_hash,
            "new_contract_hash": self.new_contract_hash,
            "superseded_artifact_count": self.superseded_artifact_count,
            "snapshot_id": self.snapshot_id,
            "reason": self.reason,
            "force_foundation_replan": self.force_foundation_replan,
        }


ContractBuilder = Callable[..., Any]

_ACTIVE_WORKFLOW_STATUSES = frozenset(
    {
        WorkflowStatus.PENDING.value,
        WorkflowStatus.QUEUED.value,
        WorkflowStatus.RUNNING.value,
    }
)

# Conception and creation intent are source evidence.  Everything below is a
# derived planning projection and must be regenerated from the repaired
# contract.  Keep this list tied to ArtifactType instead of project-specific
# strings so a different genre/book follows the same rule.
_DERIVED_ARTIFACT_TYPES = frozenset(
    {
        ArtifactType.BOOK_SPEC.value,
        ArtifactType.WORLD_SPEC.value,
        ArtifactType.CAST_SPEC.value,
        ArtifactType.VOLUME_PLAN.value,
        ArtifactType.ACT_PLAN.value,
        ArtifactType.STORY_DESIGN_KERNEL.value,
        ArtifactType.EMOTION_DRIVEN_KERNEL.value,
        ArtifactType.PUBLIC_EMOTION_KERNEL.value,
        ArtifactType.COMPLIANCE_BOUNDARY_KERNEL.value,
        ArtifactType.ENTRY_SYSTEM_KERNEL.value,
        ArtifactType.PREWRITE_READINESS.value,
        ArtifactType.PLAN_VALIDATION.value,
        ArtifactType.CHAPTER_OUTLINE_BATCH.value,
        ArtifactType.VOLUME_CHAPTER_OUTLINE.value,
        ArtifactType.VOLUME_CAST_EXPANSION.value,
        ArtifactType.VOLUME_WORLD_DISCLOSURE.value,
    }
)

_STALE_METADATA_PROJECTIONS = frozenset(
    {
        "book_spec",
        "world_spec",
        "cast_spec",
        "volume_plan",
        "story_design_kernel",
        "story_design",
        "planning_kernel",
        "planning_kernel_report",
        "prewrite_readiness",
        "volume_plan_report",
        "book_spec_report",
        "world_spec_report",
        "cast_spec_report",
        "plan_validation",
        "story_bible",
        "narrative_graph",
        "story_engine",
        "story_engine_v2",
        "story_engine_window_v2",
        "entry_system_kernel",
        "emotion_driven_kernel",
        "public_emotion_kernel",
        "compliance_boundary_kernel",
        "prewrite_readiness_report",
        "prewrite_repair_directives",
        "premium_book_spec",
        "premium_world_spec",
        "premium_cast_spec",
        "premium_volume_plan",
        "premium_state_ledger",
        "premium_state_ledger_report",
        "premium_state_snapshot",
        "premium_capability_pack",
        "premium_capability_updated_at",
    }
)


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _text(value: object) -> str:
    return str(value or "").strip()


def _hash(value: object) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _trusted_inputs(project: ProjectModel) -> dict[str, Any]:
    metadata = _mapping(project.metadata_json)
    contract = _mapping(metadata.get("concept_contract"))
    spine = _mapping(metadata.get("story_spine")) or _mapping(contract.get("story_spine"))
    seed = _text(metadata.get("concept_seed")) or _text(metadata.get("premise"))
    profile = _mapping(metadata.get("writing_profile"))
    authoritative_name = (
        _text(metadata.get("creation_protagonist_name"))
        or _text(metadata.get("protagonist_name"))
        or _text(_mapping(metadata.get("hook_card")).get("protagonist"))
        or _text(_mapping(contract.get("hook_card")).get("protagonist"))
    )
    return {
        "concept_seed": seed,
        "premise": _text(metadata.get("premise")) or seed,
        "synopsis": _text(metadata.get("synopsis")),
        "writing_profile": dict(profile),
        "authoritative_name": authoritative_name,
        "story_spine": dict(spine),
        "target_chapters": int(getattr(project, "target_chapters", 0) or 0),
        "genre": _text(getattr(project, "genre", "")),
        "sub_genre": _text(getattr(project, "sub_genre", "")),
        "language": _text(getattr(project, "language", "zh-CN")) or "zh-CN",
    }


def _source_signature(inputs: Mapping[str, Any]) -> str:
    # Do not include the old contract: the old contract is precisely what this
    # boundary is repairing and must never make the repair non-idempotent.
    return _hash(
        {
            "concept_seed": inputs.get("concept_seed"),
            "premise": inputs.get("premise"),
            "synopsis": inputs.get("synopsis"),
            "target_chapters": inputs.get("target_chapters"),
        }
    )


def _advance_truth_version_for_contract_repair(
    metadata: dict[str, Any],
    *,
    source_signature: str,
) -> None:
    """Make completed bible/graph materializations stale through the truth API.

    A conception repair can leave the premise text unchanged while changing
    the contract fields consumed by those materializers.  Bumping the
    project truth version is therefore required even when a premise hash did
    not change; the next planner run will regenerate the projections.
    """

    current = max(int(metadata.get("truth_version") or 1), 1)
    changed_at = datetime.now(UTC).isoformat()
    metadata["truth_version"] = current + 1
    metadata["truth_updated_at"] = changed_at
    metadata["truth_last_changed_artifact_type"] = "concept_contract"
    fingerprints = metadata.get("_truth_artifact_fingerprints")
    metadata["_truth_artifact_fingerprints"] = (
        dict(fingerprints) if isinstance(fingerprints, Mapping) else {}
    )
    change_log = metadata.get("_truth_change_log")
    history = list(change_log) if isinstance(change_log, list) else []
    history.append(
        {
            "truth_version": current + 1,
            "artifact_type": "concept_contract",
            "source_signature": source_signature,
            "changed_at": changed_at,
        }
    )
    metadata["_truth_change_log"] = history[-20:]


async def _scalar(session: AsyncSession, statement: Any) -> Any:
    return await session.scalar(statement)


async def _execute(session: AsyncSession, statement: Any) -> Any:
    return await session.execute(statement)


async def _assert_empty_safe_project(session: AsyncSession, project: ProjectModel) -> None:
    chapter_id = await _scalar(
        session,
        select(ChapterModel.id).where(ChapterModel.project_id == project.id).limit(1),
    )
    if chapter_id is not None:
        raise ConceptContractResumeRepairError(
            "concept contract resume repair requires a project with zero chapters"
        )
    active = await _scalar(
        session,
        select(WorkflowRunModel)
        .where(
            WorkflowRunModel.project_id == project.id,
            WorkflowRunModel.status.in_(_ACTIVE_WORKFLOW_STATUSES),
        )
        .order_by(WorkflowRunModel.created_at.desc())
        .limit(1),
    )
    if active is not None:
        raise ConceptContractResumeRepairError(
            "concept contract resume repair refused while another workflow is active: "
            f"{active.workflow_type}"
        )


def _builder_result_to_winner(result: Any) -> Mapping[str, Any] | None:
    """Accept the conception helper's winner or a complete contract wrapper."""
    if isinstance(result, tuple) and result:
        result = result[0]
    if not isinstance(result, Mapping):
        return None
    nested = result.get("winner")
    if isinstance(nested, Mapping):
        return nested
    contract = result.get("concept_contract")
    if isinstance(contract, Mapping):
        return contract
    return result


async def _call_builder(
    builder: ContractBuilder,
    *,
    project: ProjectModel,
    inputs: Mapping[str, Any],
) -> Mapping[str, Any]:
    kwargs = {
        "project": project,
        # The previous contract can contain the rejected champion's seriality
        # story.  It is deliberately not sent to the builder/LLM; the helper
        # must reconstruct from the trusted creation seed and final assets.
        "winner": {},
        "concept_seed": inputs["concept_seed"],
        "premise": inputs["premise"],
        "synopsis": inputs["synopsis"],
        "writing_profile": inputs["writing_profile"],
        "authoritative_name": inputs["authoritative_name"],
        "story_spine": inputs["story_spine"],
        "target_chapters": inputs["target_chapters"],
        "genre": inputs["genre"],
        "sub_genre": inputs["sub_genre"],
    }
    result = builder(**kwargs)
    if not isinstance(result, Awaitable):
        raise ConceptContractResumeRepairError(
            "contract_builder must be an async callable returning an awaitable"
        )
    result = await result
    winner = _builder_result_to_winner(result)
    if not winner:
        raise ConceptContractResumeRepairError("concept contract builder returned no winner")
    return winner


async def _supersede_derived_artifacts(
    session: AsyncSession,
    *,
    project: ProjectModel,
    source_signature: str,
) -> int:
    result = await _execute(
        session,
        select(PlanningArtifactVersionModel).where(
            PlanningArtifactVersionModel.project_id == project.id,
            PlanningArtifactVersionModel.artifact_type.in_(_DERIVED_ARTIFACT_TYPES),
            PlanningArtifactVersionModel.status != "superseded",
        ),
    )
    rows = list(result.scalars().all())
    reason = f"superseded_by_concept_contract_resume_repair:{source_signature[:16]}"
    for row in rows:
        row.status = "superseded"
        prior = _text(row.notes)
        row.notes = f"{prior}; {reason}" if prior else reason
    return len(rows)


async def repair_existing_project_concept_contract(
    session: AsyncSession,
    project: ProjectModel,
    *,
    contract_builder: ContractBuilder,
) -> ConceptContractResumeRepairResult:
    """Repair an empty project and retire stale planning projections.

    The caller owns the transaction and should invoke this before marking a
    new planning/autowrite workflow active.  No LLM call is made here unless
    the injected conception builder chooses to make one; the default pipeline
    adapter is responsible for MiniMax routing and source-bound prompts.
    """

    await _assert_empty_safe_project(session, project)
    inputs = _trusted_inputs(project)
    target_chapters = int(inputs["target_chapters"])
    if target_chapters <= 0:
        raise ConceptContractResumeRepairError("target_chapters must be positive")
    signature = _source_signature(inputs)
    metadata_before = deepcopy(dict(project.metadata_json or {}))
    prior_repair = _mapping(metadata_before.get("concept_contract_repair"))
    prior_lineage = _mapping(prior_repair).get("lineage")
    architecture_lineage = _mapping(
        _mapping(_mapping(metadata_before.get("concept_contract")).get("story_architecture"))
        .get("reconciliation_lineage")
    )
    known_lineage = _text(_mapping(prior_lineage).get("source_signature")) or _text(
        architecture_lineage.get("source_signature")
    )
    if known_lineage == signature:
        contract = _mapping(metadata_before.get("concept_contract"))
        violations = validate_concept_contract(
            contract, target_chapters=inputs["target_chapters"]
        )
        expected_hash = _text(_mapping(prior_lineage).get("new_contract_hash"))
        if not violations and contract and (
            not expected_hash or expected_hash == _hash(contract)
        ):
            return ConceptContractResumeRepairResult(
                status="already_repaired",
                source_signature=signature,
                old_contract_hash=_hash(contract),
                new_contract_hash=_hash(contract),
                reason="same trusted source lineage already applied",
                force_foundation_replan=bool(
                    prior_repair.get("force_foundation_replan")
                    or metadata_before.get("foundation_truth_status")
                    == "superseded_pending_materialization"
                    or bool(architecture_lineage)
                ),
            )

    old_contract = _mapping(metadata_before.get("concept_contract"))
    old_hash = _hash(old_contract) if old_contract else None
    old_snapshot = deepcopy(_mapping(metadata_before.get("book_design_snapshot")))
    winner = await _call_builder(contract_builder, project=project, inputs=inputs)

    # A builder may return a full contract; otherwise it returns the canonical
    # champion payload and this boundary owns the one deterministic builder.
    if _text(winner.get("schema_version")) == CONCEPT_CONTRACT_VERSION:
        repaired_contract = deepcopy(dict(winner))
    else:
        repaired_spine_input = {
            "who": _text(winner.get("protagonist_identity"))
            or _text(inputs["story_spine"].get("who")),
            "wants": _text(winner.get("protagonist_private_desire"))
            or _text(inputs["story_spine"].get("wants")),
            "why_now": _text(winner.get("opening_crisis"))
            or _text(inputs["story_spine"].get("why_now")),
            "against": _text(winner.get("opponent_system"))
            or _text(inputs["story_spine"].get("against")),
            "question": _text(winner.get("hook_question"))
            or _text(inputs["story_spine"].get("question")),
            "stakes": _text(winner.get("emotional_promise"))
            or _text(winner.get("decision_proof"))
            or _text(inputs["story_spine"].get("stakes")),
        }
        repaired_contract = build_concept_contract(
            winner=winner,
            story_spine={key: value for key, value in repaired_spine_input.items() if value},
            target_chapters=target_chapters,
            genre=inputs["genre"],
            sub_genre=inputs["sub_genre"],
        )
    violations = validate_concept_contract(repaired_contract, target_chapters=target_chapters)
    if violations:
        raise ConceptContractResumeRepairError(
            "repaired concept contract failed validation: " + "; ".join(map(str, violations))
        )
    repaired_spine = deepcopy(dict(_mapping(repaired_contract.get("story_spine")) or inputs["story_spine"]))
    repaired_hook = deepcopy(dict(_mapping(repaired_contract.get("hook_card"))))
    repaired_seriality = deepcopy(dict(_mapping(repaired_contract.get("seriality_proof"))))
    new_hash = _hash(repaired_contract)

    # Prepare the entire replacement before touching the ORM object.  If
    # snapshot rebuild or artifact selection fails, restore the prior JSON so
    # callers can roll back without a partially migrated contract mirror.
    metadata = deepcopy(metadata_before)
    stale_projection_keys = sorted(
        key for key in _STALE_METADATA_PROJECTIONS if key in metadata
    )
    for key in stale_projection_keys:
        metadata.pop(key, None)
    metadata.update(
        {
            "concept_contract": repaired_contract,
            "concept_contract_version": CONCEPT_CONTRACT_VERSION,
            "hook_card": repaired_hook,
            "story_spine": repaired_spine,
            "seriality_proof": repaired_seriality,
            "foundation_truth_status": "superseded_pending_materialization",
            "concept_contract_repair": {
                "schema_version": "concept-contract-resume-repair.v1",
                "status": "applied",
                "lineage": {
                    "source_signature": signature,
                    "old_contract_hash": old_hash,
                    "new_contract_hash": new_hash,
                    "repaired_at": datetime.now(UTC).isoformat(),
                },
                "superseded": {
                    "reason": "stale_derived_planning_artifacts",
                    "artifact_types": sorted(_DERIVED_ARTIFACT_TYPES),
                    "previous_contract": deepcopy(dict(old_contract)),
                    "previous_book_design_snapshot": old_snapshot,
                    "metadata_projection_keys": stale_projection_keys,
                },
                "force_foundation_replan": True,
            },
        }
    )
    _advance_truth_version_for_contract_repair(
        metadata, source_signature=signature
    )
    project.metadata_json = metadata
    try:
        snapshot = ensure_project_book_design_snapshot(project, force_rebuild=True)
        count = await _supersede_derived_artifacts(
            session, project=project, source_signature=signature
        )
        metadata = dict(project.metadata_json or {})
        repair_meta = dict(_mapping(metadata.get("concept_contract_repair")))
        superseded = dict(_mapping(repair_meta.get("superseded")))
        superseded["count"] = count
        repair_meta["superseded"] = superseded
        metadata["concept_contract_repair"] = repair_meta
        project.metadata_json = metadata
    except Exception:
        project.metadata_json = metadata_before
        raise
    return ConceptContractResumeRepairResult(
        status="repaired",
        source_signature=signature,
        old_contract_hash=old_hash,
        new_contract_hash=new_hash,
        superseded_artifact_count=count,
        snapshot_id=snapshot.snapshot_id,
        force_foundation_replan=True,
    )


async def ensure_project_concept_contract_lineage(
    session: AsyncSession,
    settings: Any,
    project: ProjectModel,
) -> ConceptContractResumeRepairResult | None:
    """Production adapter for the pre-autowrite resume boundary.

    Legacy projects without an explicit creation seed or concept contract are
    left alone.  For a repaired project, the conception reconciler remains the
    only LLM-facing helper and receives an empty winner so rejected contract
    prose cannot leak into its prompt.  The caller should run this before
    ``_mark_project_autowrite_started`` and force foundation replanning when
    the result is ``repaired``.
    """

    metadata = _mapping(project.metadata_json)
    concept_seed = _text(metadata.get("concept_seed"))
    if not concept_seed or not _mapping(metadata.get("concept_contract")):
        return None
    # The production wrapper is intentionally a no-op for books that already
    # contain prose.  The lower-level entry raises for direct misuse, while
    # this compatibility wrapper must leave existing books untouched.
    chapter_id = await _scalar(
        session,
        select(ChapterModel.id).where(ChapterModel.project_id == project.id).limit(1),
    )
    if chapter_id is not None:
        return None

    from bestseller.services.conception import _reconcile_concept_seed_with_final_premise

    receipt_ids: list[str] = []

    async def adapter(**kwargs: Any) -> dict[str, Any]:
        winner, ids = await _reconcile_concept_seed_with_final_premise(
            session,
            settings,
            winner={},
            premise=kwargs["premise"],
            synopsis=kwargs["synopsis"],
            writing_profile=kwargs["writing_profile"],
            authoritative_name=kwargs["authoritative_name"],
            ctx={
                "chapter_count": kwargs["target_chapters"],
                "target_chapters": kwargs["target_chapters"],
                "genre": kwargs["genre"],
                "sub_genre": kwargs["sub_genre"],
                "language": kwargs.get("language") or getattr(project, "language", "zh-CN"),
            },
            concept_seed=kwargs["concept_seed"],
            story_spine=kwargs["story_spine"],
        )
        receipt_ids.extend(str(item) for item in ids)
        return {"winner": winner}

    result = await repair_existing_project_concept_contract(
        session,
        project,
        contract_builder=adapter,
    )
    if result is None or not receipt_ids:
        return result
    metadata = deepcopy(dict(project.metadata_json or {}))
    repair_meta = dict(_mapping(metadata.get("concept_contract_repair")))
    lineage = dict(_mapping(repair_meta.get("lineage")))
    lineage["llm_receipt_ids"] = list(dict.fromkeys(receipt_ids))
    repair_meta["lineage"] = lineage
    metadata["concept_contract_repair"] = repair_meta
    project.metadata_json = metadata
    return replace(result, reason="conception reconciliation applied")


__all__ = [
    "ConceptContractResumeRepairError",
    "ConceptContractResumeRepairResult",
    "ensure_project_concept_contract_lineage",
    "repair_existing_project_concept_contract",
]
