"""PostgreSQL-backed promotion boundary for frozen book packaging contracts."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.infra.db.models import (
    BookPackagingContractModel as BookPackagingContractRow,
)
from bestseller.infra.db.models import (
    ProjectModel,
)
from bestseller.services.book_packaging_contract import (
    BookPackagingContractModel,
    build_book_packaging_contract,
    canonical_hash,
    validate_packaging_contract,
)


class PackagingContractIntegrityError(RuntimeError):
    pass


def _mapping(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


async def promote_creation_packaging_contract_if_ready(
    session: AsyncSession,
    *,
    project: ProjectModel,
) -> BookPackagingContractRow | None:
    """Promote the conception-approved package at the project boundary.

    This is intentionally a no-op for legacy/simple project creation.  A v2
    conception which carries the final promotion receipt, however, must leave
    creation with one immutable active contract; errors are allowed to escape
    so callers cannot accidentally publish an unbound package.
    """
    metadata = _mapping(project.metadata_json)
    if str(metadata.get("concept_contract_version") or "") != "2":
        return None
    artifacts = _mapping(metadata.get("conception_artifacts"))
    appeal = _mapping(artifacts.get("story_appeal"))
    promotion = _mapping(appeal.get("final_packaging_promotion"))
    if promotion.get("passed") is not True:
        return None

    # Idempotent retry after a flush/retry must not create duplicate active
    # revisions.  Never trust the pointer by itself: prove that the row, hash,
    # title and active variant still agree before treating the retry as done.
    if metadata.get("active_packaging_contract_id"):
        target_platform = str(
            metadata.get("active_packaging_target_platform")
            or metadata.get("platform_target")
            or "general"
        ).strip() or "general"
        await load_active_packaging_contract(
            session,
            project=project,
            target_platform=target_platform,
            listing_surface="long-form",
        )
        return None

    concept = _mapping(metadata.get("concept_contract"))
    hook = _mapping(concept.get("hook_card"))
    spine = _mapping(concept.get("story_spine"))
    one_liner = str(hook.get("one_liner") or "").strip()
    protagonist = str(hook.get("protagonist") or "").strip()
    situation = str(hook.get("abnormality") or hook.get("opening_event") or "").strip()
    action = str(hook.get("story_motion") or hook.get("decision_proof") or "").strip()
    result = str(
        spine.get("terminal_state")
        or spine.get("endgame")
        or spine.get("endgame_direction")
        or spine.get("reader_promise")
        or metadata.get("reader_promise")
        or ""
    ).strip()
    if not all((one_liner, protagonist, situation, action, result)):
        raise PackagingContractIntegrityError("PACKAGING_CONTRACT_SOURCE_FIELDS_MISSING")

    genre_intent = _mapping(metadata.get("genre_intent_contract"))
    enhancers = _mapping(genre_intent.get("explicit_enhancers"))
    effect_skills = enhancers.get("effect_skills") or metadata.get("effect_skills") or ()
    tags = metadata.get("tags") or metadata.get("trope_keywords") or ()
    architecture = _mapping(
        concept.get("story_architecture") or metadata.get("story_architecture")
    )
    refs: list[str] = []
    champion = str(concept.get("champion_id") or "").strip()
    if champion:
        refs.append(f"concept:{champion}")
    for unit in architecture.get("causal_units") or ():
        if isinstance(unit, dict) and str(unit.get("unit_id") or "").strip():
            refs.append(f"causal:{unit['unit_id']}")
    if not refs:
        refs.append(f"concept:{canonical_hash(concept)[:16]}")

    synopsis = str(
        _mapping(promotion.get("quality")).get("synopsis")
        or metadata.get("synopsis")
        or metadata.get("premise")
        or one_liner
    ).strip()
    platform = str(metadata.get("platform_target") or "general").strip() or "general"
    surface = "long-form"
    from bestseller.services.book_packaging_contract import IntroVariant, PackagingSurface

    variant = IntroVariant(
        variant_id=f"{platform}-long-v1",
        text=synopsis,
        promise_refs=(*refs, *(f"effect:{x}" for x in effect_skills if str(x).strip())),
    )
    contract = build_book_packaging_contract(
        project_id=str(project.id),
        title=str(project.title).strip(),
        logline=one_liner,
        title_promise=one_liner,
        protagonist=protagonist,
        situation=situation,
        action=action,
        result=result,
        genre=str(project.genre).strip(),
        tags=tags,
        effect_skills=effect_skills,
        architecture_refs=refs,
        intro_variants=(variant,),
        surfaces=(PackagingSurface(
            target_platform=platform,
            listing_surface=surface,
            active_intro_variant_id=variant.variant_id,
            active_intro_variant_hash=variant.content_hash,
        ),),
    )
    source_hash = str(metadata.get("book_design_snapshot_hash") or "").strip()
    if len(source_hash) != 64:
        source_hash = canonical_hash(concept)
    row = await persist_packaging_contract(
        session,
        project=project,
        contract=contract,
        target_platform=platform,
        source_design_snapshot_hash=source_hash,
        realization_refs={"source": "conception.final_packaging_promotion"},
    )
    await activate_packaging_contract(session, project=project, row=row)
    return row


def _contract_from_row(row: BookPackagingContractRow) -> BookPackagingContractModel:
    payload = dict(row.canonical_payload_json or {})
    payload.pop("contract_hash", None)
    try:
        contract = BookPackagingContractModel.model_validate(payload)
    except ValueError as exc:
        raise PackagingContractIntegrityError("PACKAGING_CONTRACT_PAYLOAD_INVALID") from exc
    if contract.contract_hash != row.canonical_payload_hash:
        raise PackagingContractIntegrityError("PACKAGING_CONTRACT_HASH_MISMATCH")
    if row.primary_title != contract.title:
        raise PackagingContractIntegrityError("PACKAGING_TITLE_CACHE_MISMATCH")
    errors = validate_packaging_contract(contract)
    if errors:
        raise PackagingContractIntegrityError(",".join(errors))
    return contract


async def persist_packaging_contract(
    session: AsyncSession,
    *,
    project: ProjectModel,
    contract: BookPackagingContractModel,
    target_platform: str,
    source_design_snapshot_hash: str,
    title_candidate_decision_ids: tuple[str, ...] = (),
    realization_refs: dict[str, Any] | None = None,
    supersedes_id: UUID | None = None,
) -> BookPackagingContractRow:
    if str(project.id) != contract.project_id:
        raise ValueError("PACKAGING_PROJECT_MISMATCH")
    active_map = {
        item.listing_surface: {
            "active_intro_variant_id": item.active_intro_variant_id,
            "active_intro_variant_hash": item.active_intro_variant_hash,
        }
        for item in contract.surfaces
        if item.target_platform == target_platform
    }
    if not active_map:
        raise ValueError("ACTIVE_INTRO_VARIANT_MISSING")
    payload = contract.to_artifact()
    row = BookPackagingContractRow(
        project_id=project.id,
        contract_revision=str(contract.revision),
        target_platform=target_platform,
        source_design_snapshot_hash=source_design_snapshot_hash,
        primary_title=contract.title,
        intro_variants_json={
            "variants": [item.model_dump(mode="json") for item in contract.intro_variants]
        },
        title_candidate_decision_ids=list(title_candidate_decision_ids),
        active_intro_variant_map=active_map,
        tag_effect_architecture_refs={
            "tags": list(contract.tags),
            "effect_skills": list(contract.effect_skills),
            "architecture_refs": list(contract.architecture_refs),
        },
        realization_refs=dict(realization_refs or {}),
        canonical_payload_json=payload,
        canonical_payload_hash=contract.contract_hash,
        status="draft",
        supersedes_id=supersedes_id,
    )
    session.add(row)
    await session.flush()
    return row


async def activate_packaging_contract(
    session: AsyncSession,
    *,
    project: ProjectModel,
    row: BookPackagingContractRow,
) -> dict[str, Any]:
    if row.project_id != project.id:
        raise ValueError("PACKAGING_PROJECT_MISMATCH")
    contract = _contract_from_row(row)
    existing = (
        await session.scalars(
            select(BookPackagingContractRow).where(
                BookPackagingContractRow.project_id == project.id,
                BookPackagingContractRow.target_platform == row.target_platform,
                BookPackagingContractRow.status == "active",
                BookPackagingContractRow.id != row.id,
            )
        )
    ).all()
    for prior in existing:
        prior.status = "superseded"
    row.status = "active"
    row.frozen_at = datetime.now(UTC)
    metadata = dict(project.metadata_json or {})
    listing_payloads = {
        f"{surface.target_platform}:{surface.listing_surface}": contract.active_payload(
            surface.target_platform,
            surface.listing_surface,
        )
        for surface in contract.surfaces
    }
    metadata.update(
        {
            "active_packaging_contract_id": str(row.id),
            "active_packaging_contract_hash": contract.contract_hash,
            "active_packaging_target_platform": row.target_platform,
            "active_packaging_listing_payloads": listing_payloads,
            "ranking_evidence_invalidated_layers": [
                "reader",
                "platform_cohort",
                "rank_receipt",
            ],
            "ranking_evidence_invalidation_reason": "packaging_contract_changed",
        }
    )
    project.title = contract.title
    project.metadata_json = metadata
    await session.flush()
    return contract.to_artifact()


async def load_active_packaging_contract(
    session: AsyncSession,
    *,
    project: ProjectModel,
    target_platform: str,
    listing_surface: str,
) -> dict[str, Any]:
    metadata = dict(project.metadata_json or {})
    pointer = metadata.get("active_packaging_contract_id")
    if not pointer:
        raise PackagingContractIntegrityError("ACTIVE_PACKAGING_CONTRACT_MISSING")
    try:
        pointer_id = UUID(str(pointer))
    except ValueError as exc:
        raise PackagingContractIntegrityError("ACTIVE_PACKAGING_POINTER_INVALID") from exc
    row = await session.scalar(
        select(BookPackagingContractRow).where(
            BookPackagingContractRow.id == pointer_id,
            BookPackagingContractRow.project_id == project.id,
            BookPackagingContractRow.target_platform == target_platform,
            BookPackagingContractRow.status == "active",
        )
    )
    if row is None:
        raise PackagingContractIntegrityError("ACTIVE_PACKAGING_CONTRACT_MISSING")
    contract = _contract_from_row(row)
    if metadata.get("active_packaging_contract_hash") != contract.contract_hash:
        raise PackagingContractIntegrityError("STALE_PACKAGING_CONSUMER")
    if project.title != contract.title:
        raise PackagingContractIntegrityError("PACKAGING_TITLE_CACHE_MISMATCH")
    return contract.active_payload(target_platform, listing_surface)


__all__ = [
    "PackagingContractIntegrityError",
    "activate_packaging_contract",
    "load_active_packaging_contract",
    "persist_packaging_contract",
    "promote_creation_packaging_contract_if_ready",
]
