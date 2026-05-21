"""Persistence boundary for Fanqie market intelligence."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.domain.enums import ArtifactType
from bestseller.domain.fanqie_market import (
    FanqieCategoryProfile,
    FanqieCompetitorProfile,
    FanqieMarketAnalysisBundle,
    FanqieRankingSnapshot,
)
from bestseller.domain.planning import PlanningArtifactCreate
from bestseller.infra.db.models import (
    ChapterDraftVersionModel,
    ChapterModel,
    FanqieCategoryProfileModel,
    FanqieCompetitorProfileModel,
    FanqieRankingSnapshotModel,
    PlanningArtifactVersionModel,
)
from bestseller.services.fanqie_long_ranking_gate import evaluate_fanqie_long_ranking_gate
from bestseller.services.fanqie_market_analyzer import build_market_analysis_bundle
from bestseller.services.fanqie_market_client import normalize_fanqiehub_snapshot
from bestseller.services.fanqie_seed_profiles import (
    load_fanqie_seed_profile,
    seed_profile_to_artifacts,
)
from bestseller.services.projects import get_project_by_slug, import_planning_artifact


@dataclass(frozen=True)
class FanqieMarketImportResult:
    snapshot_id: UUID | None
    category_profile_id: UUID | None
    competitor_profile_ids: list[UUID] = field(default_factory=list)
    artifact_ids: list[UUID] = field(default_factory=list)
    analysis: FanqieMarketAnalysisBundle | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "snapshot_id": str(self.snapshot_id) if self.snapshot_id else None,
            "category_profile_id": (
                str(self.category_profile_id) if self.category_profile_id else None
            ),
            "competitor_profile_ids": [
                str(profile_id) for profile_id in self.competitor_profile_ids
            ],
            "artifact_ids": [str(artifact_id) for artifact_id in self.artifact_ids],
            "summary": self.analysis.summary() if self.analysis else {},
        }


async def import_fanqie_market_payload(
    session: AsyncSession,
    payload: Mapping[str, Any] | Sequence[Mapping[str, Any]],
    *,
    category: str = "",
    board_type: str = "reading",
    channel: str = "fanqie",
    source_url: str = "",
    project_slug: str | None = None,
    persist_artifacts: bool = False,
    competitor_limit: int | None = None,
) -> FanqieMarketImportResult:
    """Normalize, analyze, persist, and optionally store planning artifacts."""

    snapshot = normalize_fanqiehub_snapshot(
        payload,
        board_type=board_type,
        category=category,
        channel=channel,
        source_url=source_url,
    )
    analysis = build_market_analysis_bundle(snapshot, competitor_limit=competitor_limit)
    snapshot_model = await upsert_ranking_snapshot(session, snapshot)
    competitor_models = await upsert_competitor_profiles(
        session,
        snapshot_model,
        analysis.competitor_profiles,
    )
    category_model = await upsert_category_profile(
        session,
        snapshot_model,
        analysis.category_profile,
    )
    artifacts: list[PlanningArtifactVersionModel] = []
    if persist_artifacts:
        if not project_slug:
            raise ValueError("project_slug is required when persist_artifacts=True")
        artifacts = await persist_market_planning_artifacts(
            session,
            project_slug=project_slug,
            analysis=analysis,
        )
    return FanqieMarketImportResult(
        snapshot_id=snapshot_model.id,
        category_profile_id=category_model.id,
        competitor_profile_ids=[model.id for model in competitor_models if model.id],
        artifact_ids=[artifact.id for artifact in artifacts if artifact.id],
        analysis=analysis,
    )


async def upsert_ranking_snapshot(
    session: AsyncSession,
    snapshot: FanqieRankingSnapshot,
) -> FanqieRankingSnapshotModel:
    existing = await session.scalar(
        select(FanqieRankingSnapshotModel).where(
            FanqieRankingSnapshotModel.source == snapshot.source,
            FanqieRankingSnapshotModel.board_type == snapshot.board_type,
            FanqieRankingSnapshotModel.category == snapshot.category,
            FanqieRankingSnapshotModel.channel == snapshot.channel,
            FanqieRankingSnapshotModel.data_date == snapshot.data_date,
        )
    )
    payload_json = snapshot.raw_payload or snapshot.model_dump(mode="json")
    normalized_books_json = [book.model_dump(mode="json") for book in snapshot.books]
    if existing is None:
        existing = FanqieRankingSnapshotModel(
            source=snapshot.source,
            source_url=snapshot.source_url,
            board_type=snapshot.board_type,
            category=snapshot.category,
            channel=snapshot.channel,
            data_date=snapshot.data_date,
            fetched_at=snapshot.fetched_at,
            payload_json=payload_json,
            normalized_books_json=normalized_books_json,
            sample_size=snapshot.sample_size,
        )
        session.add(existing)
    else:
        existing.source_url = snapshot.source_url
        existing.fetched_at = snapshot.fetched_at
        existing.payload_json = payload_json
        existing.normalized_books_json = normalized_books_json
        existing.sample_size = snapshot.sample_size
    await session.flush()
    return existing


async def upsert_competitor_profiles(
    session: AsyncSession,
    snapshot_model: FanqieRankingSnapshotModel,
    profiles: list[FanqieCompetitorProfile],
) -> list[FanqieCompetitorProfileModel]:
    models: list[FanqieCompetitorProfileModel] = []
    for profile in profiles:
        existing = await session.scalar(
            select(FanqieCompetitorProfileModel).where(
                FanqieCompetitorProfileModel.snapshot_id == snapshot_model.id,
                FanqieCompetitorProfileModel.source_book_id == profile.source_book_id,
            )
        )
        profile_json = profile.model_dump(mode="json")
        if existing is None:
            existing = FanqieCompetitorProfileModel(
                snapshot_id=snapshot_model.id,
                source_book_id=profile.source_book_id,
                title=profile.title,
                author=profile.author,
                category=profile.category,
                board_type=profile.board_type,
                rank=profile.rank,
                reader_count=profile.reader_count,
                profile_json=profile_json,
                evidence_json=profile.evidence,
                confidence=profile.confidence,
            )
            session.add(existing)
        else:
            existing.title = profile.title
            existing.author = profile.author
            existing.category = profile.category
            existing.board_type = profile.board_type
            existing.rank = profile.rank
            existing.reader_count = profile.reader_count
            existing.profile_json = profile_json
            existing.evidence_json = profile.evidence
            existing.confidence = profile.confidence
        models.append(existing)
    await session.flush()
    return models


async def upsert_category_profile(
    session: AsyncSession,
    snapshot_model: FanqieRankingSnapshotModel,
    profile: FanqieCategoryProfile,
) -> FanqieCategoryProfileModel:
    existing = await session.scalar(
        select(FanqieCategoryProfileModel).where(
            FanqieCategoryProfileModel.snapshot_id == snapshot_model.id,
            FanqieCategoryProfileModel.category == profile.category,
            FanqieCategoryProfileModel.board_type == profile.board_type,
        )
    )
    profile_json = profile.model_dump(mode="json")
    if existing is None:
        existing = FanqieCategoryProfileModel(
            snapshot_id=snapshot_model.id,
            category=profile.category,
            board_type=profile.board_type,
            channel=profile.channel,
            data_date=profile.data_date,
            sample_size=profile.sample_size,
            profile_json=profile_json,
            confidence=profile.confidence,
        )
        session.add(existing)
    else:
        existing.channel = profile.channel
        existing.data_date = profile.data_date
        existing.sample_size = profile.sample_size
        existing.profile_json = profile_json
        existing.confidence = profile.confidence
    await session.flush()
    return existing


async def persist_market_planning_artifacts(
    session: AsyncSession,
    *,
    project_slug: str,
    analysis: FanqieMarketAnalysisBundle,
) -> list[PlanningArtifactVersionModel]:
    """Store market analysis as project planning artifact versions."""

    artifacts: list[PlanningArtifactVersionModel] = []
    payload = analysis.to_artifact_payload()
    artifacts.append(
        await import_planning_artifact(
            session,
            project_slug,
            PlanningArtifactCreate(
                artifact_type=ArtifactType.FANQIE_MARKET_SNAPSHOT,
                content={
                    "snapshot": payload["snapshot"],
                    "competitor_profiles": payload["competitor_profiles"],
                },
                notes="Imported Fanqie ranking snapshot and competitor profiles.",
            ),
        )
    )
    artifacts.append(
        await import_planning_artifact(
            session,
            project_slug,
            PlanningArtifactCreate(
                artifact_type=ArtifactType.FANQIE_MARKET_PROFILE,
                content={
                    "summary": analysis.summary(),
                    "category_profile": payload["category_profile"],
                    "craft_profile": payload["craft_profile"],
                },
                notes="Selected Fanqie market profile for project planning.",
            ),
        )
    )
    artifacts.append(
        await import_planning_artifact(
            session,
            project_slug,
            PlanningArtifactCreate(
                artifact_type=ArtifactType.FANQIE_CATEGORY_PROFILE,
                content=payload["category_profile"],
                notes="Compiled Fanqie category market profile.",
            ),
        )
    )
    artifacts.append(
        await import_planning_artifact(
            session,
            project_slug,
            PlanningArtifactCreate(
                artifact_type=ArtifactType.FANQIE_CRAFT_PROFILE,
                content=payload["craft_profile"],
                notes="Compiled anonymous Fanqie craft profile for planning prompts.",
            ),
        )
    )
    project = await get_project_by_slug(session, project_slug)
    if project is not None:
        metadata = dict(getattr(project, "metadata_json", None) or {})
        metadata["fanqie_market_summary"] = analysis.summary()
        metadata["fanqie_category_profile"] = payload["category_profile"]
        metadata["fanqie_craft_profile"] = payload["craft_profile"]
        project.metadata_json = metadata
    return artifacts


async def evaluate_and_persist_fanqie_long_readiness(
    session: AsyncSession,
    *,
    project_slug: str,
    chapter_texts: Mapping[int, str] | Sequence[str],
    protagonist_name: str | None = None,
) -> PlanningArtifactVersionModel:
    """Evaluate and store the long-form Fanqie readiness report."""

    report = evaluate_fanqie_long_ranking_gate(
        chapter_texts,
        project_slug=project_slug,
        protagonist_name=protagonist_name,
    )
    return await import_planning_artifact(
        session,
        project_slug,
        PlanningArtifactCreate(
            artifact_type=ArtifactType.FANQIE_LONG_RANKING_READINESS,
            content=report.model_dump(mode="json"),
            notes="Evaluated long-form Fanqie ranking readiness.",
        ),
    )


async def apply_fanqie_seed_profile(
    session: AsyncSession,
    *,
    project_slug: str,
    profile_key: str,
) -> dict[str, Any]:
    """Apply an offline Fanqie seed profile to a project."""

    project = await get_project_by_slug(session, project_slug)
    if project is None:
        raise ValueError(f"Project '{project_slug}' was not found.")

    seed_payload = load_fanqie_seed_profile(profile_key)
    artifact_payload = seed_profile_to_artifacts(seed_payload)
    artifacts = [
        await import_planning_artifact(
            session,
            project_slug,
            PlanningArtifactCreate(
                artifact_type=ArtifactType.FANQIE_MARKET_PROFILE,
                content={
                    "summary": artifact_payload["summary"],
                    "category_profile": artifact_payload["category_profile"],
                    "craft_profile": artifact_payload["craft_profile"],
                },
                notes=f"Applied Fanqie seed market profile '{profile_key}'.",
            ),
        ),
        await import_planning_artifact(
            session,
            project_slug,
            PlanningArtifactCreate(
                artifact_type=ArtifactType.FANQIE_CATEGORY_PROFILE,
                content=artifact_payload["category_profile"],
                notes=f"Applied Fanqie seed category profile '{profile_key}'.",
            ),
        ),
        await import_planning_artifact(
            session,
            project_slug,
            PlanningArtifactCreate(
                artifact_type=ArtifactType.FANQIE_CRAFT_PROFILE,
                content=artifact_payload["craft_profile"],
                notes=f"Applied Fanqie seed craft profile '{profile_key}'.",
            ),
        ),
    ]

    metadata = dict(getattr(project, "metadata_json", None) or {})
    metadata["fanqie_market_summary"] = artifact_payload["summary"]
    metadata["fanqie_category_profile"] = artifact_payload["category_profile"]
    metadata["fanqie_craft_profile"] = artifact_payload["craft_profile"]
    metadata["fanqie_seed_profile_key"] = profile_key
    project.metadata_json = metadata
    await session.flush()
    return {
        "project_slug": project_slug,
        "profile_key": profile_key,
        "category": artifact_payload["summary"]["category"],
        "artifact_ids": [str(artifact.id) for artifact in artifacts if artifact.id],
        "summary": artifact_payload["summary"],
    }


async def inspect_fanqie_market_project(
    session: AsyncSession,
    *,
    project_slug: str,
) -> dict[str, Any]:
    """Return the latest Fanqie market artifacts and metadata for a project."""

    project = await get_project_by_slug(session, project_slug)
    if project is None:
        raise ValueError(f"Project '{project_slug}' was not found.")

    artifact_types = (
        ArtifactType.FANQIE_MARKET_PROFILE,
        ArtifactType.FANQIE_CATEGORY_PROFILE,
        ArtifactType.FANQIE_CRAFT_PROFILE,
        ArtifactType.FANQIE_LONG_RANKING_READINESS,
    )
    artifacts: dict[str, dict[str, Any] | None] = {}
    for artifact_type in artifact_types:
        artifact = await session.scalar(
            select(PlanningArtifactVersionModel)
            .where(
                PlanningArtifactVersionModel.project_id == project.id,
                PlanningArtifactVersionModel.artifact_type == artifact_type.value,
            )
            .order_by(
                PlanningArtifactVersionModel.version_no.desc(),
                PlanningArtifactVersionModel.created_at.desc(),
            )
            .limit(1)
        )
        artifacts[artifact_type.value] = (
            {
                "artifact_id": str(artifact.id) if artifact.id else None,
                "version_no": artifact.version_no,
                "created_at": artifact.created_at.isoformat()
                if artifact.created_at
                else None,
                "status": artifact.status,
                "notes": artifact.notes,
                "content": artifact.content,
            }
            if artifact is not None
            else None
        )

    metadata = dict(getattr(project, "metadata_json", None) or {})
    return {
        "project_slug": project_slug,
        "fanqie_seed_profile_key": metadata.get("fanqie_seed_profile_key"),
        "metadata_summary": metadata.get("fanqie_market_summary"),
        "metadata_craft_profile": metadata.get("fanqie_craft_profile"),
        "artifacts": artifacts,
    }


async def load_current_chapter_texts_for_fanqie_gate(
    session: AsyncSession,
    *,
    project_slug: str,
    through_chapter: int | None = None,
) -> dict[int, str]:
    """Load current chapter drafts for Fanqie's long-form gate."""

    project = await get_project_by_slug(session, project_slug)
    if project is None:
        raise ValueError(f"Project '{project_slug}' was not found.")

    stmt = (
        select(ChapterModel.chapter_number, ChapterDraftVersionModel.content_md)
        .join(
            ChapterDraftVersionModel,
            ChapterDraftVersionModel.chapter_id == ChapterModel.id,
        )
        .where(
            ChapterModel.project_id == project.id,
            ChapterDraftVersionModel.is_current.is_(True),
        )
        .order_by(ChapterModel.chapter_number.asc())
    )
    if through_chapter is not None:
        stmt = stmt.where(ChapterModel.chapter_number <= through_chapter)
    rows = list((await session.execute(stmt)).all())
    return {int(chapter_number): str(content_md or "") for chapter_number, content_md in rows}
