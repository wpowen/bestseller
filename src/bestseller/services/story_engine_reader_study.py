"""Fail-closed persistence and evaluation for StoryEngine blind-reader studies."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, is_dataclass
from enum import StrEnum
from math import sqrt
from typing import Any, cast
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.domain.story_engine import canonical_json_hash
from bestseller.infra.db.models import (
    StoryEngineCanaryCampaignModel,
    StoryEngineReaderResponseModel,
    StoryEngineReaderStudyModel,
)


class ReaderEvidenceSource(StrEnum):
    FIXTURE = "fixture"
    LIVE = "live"


@dataclass(frozen=True, slots=True)
class StoryEngineReaderStudyCellSpec:
    cell_key: str
    experiment: str
    genre: str
    seed: str
    reading_scope: str
    baseline_project_id: UUID
    engine_project_id: UUID

    def __post_init__(self) -> None:
        if not self.cell_key.strip() or not self.genre.strip() or not self.seed.strip():
            raise ValueError("reader study cell requires key, genre, and seed")
        if self.experiment not in {"E1", "E2"}:
            raise ValueError("reader study cell experiment must be E1 or E2")
        if self.reading_scope not in {"first_3", "first_10"}:
            raise ValueError("reader study scope must be first_3 or first_10")
        if self.baseline_project_id == self.engine_project_id:
            raise ValueError("reader study variants require different projects")

    def to_mapping(self) -> dict[str, str]:
        return {
            "cell_key": self.cell_key.strip(),
            "experiment": self.experiment,
            "genre": self.genre.strip(),
            "seed": self.seed.strip(),
            "reading_scope": self.reading_scope,
            "baseline_project_id": str(self.baseline_project_id),
            "engine_project_id": str(self.engine_project_id),
        }


@dataclass(frozen=True, slots=True)
class StoryEngineReaderStudyManifest:
    study_key: str
    canary_campaign_id: UUID
    target_cohort: str
    blind_protocol: str
    cells: tuple[StoryEngineReaderStudyCellSpec, ...]

    def __post_init__(self) -> None:
        if not self.study_key.strip() or not self.target_cohort.strip():
            raise ValueError("reader study requires key and target cohort")
        if not self.blind_protocol.strip():
            raise ValueError("reader study requires an explicit blind protocol")
        if not self.cells:
            raise ValueError("reader study requires comparison cells")

        cell_keys = [cell.cell_key.strip() for cell in self.cells]
        if len(set(cell_keys)) != len(cell_keys):
            raise ValueError("reader study cell keys must be unique")
        scopes_by_comparison: dict[tuple[object, ...], set[str]] = defaultdict(set)
        for cell in self.cells:
            comparison = (
                cell.experiment,
                cell.genre.strip(),
                cell.seed.strip(),
                cell.baseline_project_id,
                cell.engine_project_id,
            )
            scopes_by_comparison[comparison].add(cell.reading_scope)
        required_scopes = {"first_3", "first_10"}
        if any(scopes != required_scopes for scopes in scopes_by_comparison.values()):
            raise ValueError(
                "each reader comparison requires first_3 and first_10 cells"
            )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "schema_version": "story-engine-reader-study/v1",
            "study_key": self.study_key.strip(),
            "canary_campaign_id": str(self.canary_campaign_id),
            "target_cohort": self.target_cohort.strip(),
            "blind_protocol": self.blind_protocol.strip(),
            "cells": [cell.to_mapping() for cell in self.cells],
        }

    def stable_hash(self) -> str:
        return canonical_json_hash(self.to_mapping())


@dataclass(frozen=True, slots=True)
class StoryEngineReaderResponse:
    response_key: str
    participant_hash: str
    cell_key: str
    assigned_order: str
    preferred_variant: str
    engine_recall_accurate: bool
    baseline_recall_accurate: bool
    engine_severe_abandonment: bool
    baseline_severe_abandonment: bool
    evidence_source: ReaderEvidenceSource

    def __post_init__(self) -> None:
        if not self.response_key.strip() or not self.cell_key.strip():
            raise ValueError("reader response requires response and cell keys")
        normalized_hash = self.participant_hash.strip().lower()
        if len(normalized_hash) != 64:
            raise ValueError("reader participant hash must be a SHA-256 hex digest")
        try:
            int(normalized_hash, 16)
        except ValueError as exc:
            raise ValueError(
                "reader participant hash must be a SHA-256 hex digest"
            ) from exc
        if self.assigned_order not in {"baseline_first", "engine_first"}:
            raise ValueError("reader assigned order is invalid")
        if self.preferred_variant not in {"baseline", "engine", "tie"}:
            raise ValueError("reader preferred variant is invalid")
        ReaderEvidenceSource(self.evidence_source)

    def to_mapping(self) -> dict[str, Any]:
        return {
            "response_key": self.response_key.strip(),
            "participant_hash": self.participant_hash.strip().lower(),
            "cell_key": self.cell_key.strip(),
            "assigned_order": self.assigned_order,
            "preferred_variant": self.preferred_variant,
            "engine_recall_accurate": self.engine_recall_accurate,
            "baseline_recall_accurate": self.baseline_recall_accurate,
            "engine_severe_abandonment": self.engine_severe_abandonment,
            "baseline_severe_abandonment": self.baseline_severe_abandonment,
            "evidence_source": ReaderEvidenceSource(self.evidence_source).value,
        }


@dataclass(frozen=True, slots=True)
class StoryEngineReaderCellReport:
    cell_key: str
    reading_scope: str
    sample_size: int
    live_response_count: int
    engine_preference_count: int
    engine_preference_rate: float
    engine_preference_wilson_lower: float
    engine_recall_rate: float
    baseline_recall_rate: float
    engine_severe_abandonment_rate: float
    baseline_severe_abandonment_rate: float
    blocking_codes: tuple[str, ...]
    reader_ready: bool


@dataclass(frozen=True, slots=True)
class StoryEngineReaderStudyReport:
    study_key: str
    cell_count: int
    sample_size: int
    live_response_count: int
    blocking_codes: tuple[str, ...]
    reader_ready: bool
    release_status: str
    cells: tuple[StoryEngineReaderCellReport, ...]


def _wilson_lower_bound(successes: int, total: int) -> float:
    if total <= 0:
        return 0.0
    proportion = successes / total
    z = 1.959963984540054
    denominator = 1 + (z**2 / total)
    centre = proportion + (z**2 / (2 * total))
    margin = z * sqrt(
        (proportion * (1 - proportion) / total) + (z**2 / (4 * total**2))
    )
    return (centre - margin) / denominator


def evaluate_story_engine_reader_study(
    manifest: StoryEngineReaderStudyManifest,
    responses: Sequence[StoryEngineReaderResponse],
) -> StoryEngineReaderStudyReport:
    """Evaluate live reader evidence without treating fixtures as validation."""

    normalized = tuple(responses)
    manifest_cells = {cell.cell_key: cell for cell in manifest.cells}
    responses_by_cell: dict[str, list[StoryEngineReaderResponse]] = defaultdict(list)
    blocking_codes: list[str] = []
    seen_participants: set[str] = set()
    for response in normalized:
        if response.cell_key not in manifest_cells:
            blocking_codes.append("READER_RESPONSE_CELL_UNKNOWN")
            continue
        participant_hash = response.participant_hash.strip().lower()
        if participant_hash in seen_participants:
            blocking_codes.append("READER_PARTICIPANT_REUSED")
            continue
        seen_participants.add(participant_hash)
        responses_by_cell[response.cell_key].append(response)

    cell_reports: list[StoryEngineReaderCellReport] = []
    for cell in manifest.cells:
        cell_responses = tuple(responses_by_cell.get(cell.cell_key, ()))
        sample_size = len(cell_responses)
        live_count = sum(
            response.evidence_source is ReaderEvidenceSource.LIVE
            for response in cell_responses
        )
        engine_preferences = sum(
            response.preferred_variant == "engine" for response in cell_responses
        )
        engine_recall_count = sum(
            response.engine_recall_accurate for response in cell_responses
        )
        baseline_recall_count = sum(
            response.baseline_recall_accurate for response in cell_responses
        )
        engine_abandonment_count = sum(
            response.engine_severe_abandonment for response in cell_responses
        )
        baseline_abandonment_count = sum(
            response.baseline_severe_abandonment for response in cell_responses
        )

        preference_rate = engine_preferences / sample_size if sample_size else 0.0
        wilson_lower = _wilson_lower_bound(engine_preferences, sample_size)
        engine_recall_rate = engine_recall_count / sample_size if sample_size else 0.0
        baseline_recall_rate = (
            baseline_recall_count / sample_size if sample_size else 0.0
        )
        engine_abandonment_rate = (
            engine_abandonment_count / sample_size if sample_size else 0.0
        )
        baseline_abandonment_rate = (
            baseline_abandonment_count / sample_size if sample_size else 0.0
        )
        cell_blockers: list[str] = []
        if sample_size < 30:
            cell_blockers.append("READER_SAMPLE_SIZE_INSUFFICIENT")
        if live_count != sample_size:
            cell_blockers.append("LIVE_READER_EVIDENCE_REQUIRED")
        if wilson_lower <= 0.5:
            cell_blockers.append("READER_PREFERENCE_CONFIDENCE_INSUFFICIENT")
        if engine_recall_rate < 0.8:
            cell_blockers.append("READER_RECALL_ACCURACY_INSUFFICIENT")
        if engine_abandonment_rate > baseline_abandonment_rate:
            cell_blockers.append("READER_SEVERE_ABANDONMENT_INCREASED")
        blocking_codes.extend(cell_blockers)
        cell_reports.append(
            StoryEngineReaderCellReport(
                cell_key=cell.cell_key,
                reading_scope=cell.reading_scope,
                sample_size=sample_size,
                live_response_count=live_count,
                engine_preference_count=engine_preferences,
                engine_preference_rate=round(preference_rate, 4),
                engine_preference_wilson_lower=round(wilson_lower, 4),
                engine_recall_rate=round(engine_recall_rate, 4),
                baseline_recall_rate=round(baseline_recall_rate, 4),
                engine_severe_abandonment_rate=round(engine_abandonment_rate, 4),
                baseline_severe_abandonment_rate=round(
                    baseline_abandonment_rate, 4
                ),
                blocking_codes=tuple(dict.fromkeys(cell_blockers)),
                reader_ready=not cell_blockers,
            )
        )

    all_fixture = bool(normalized) and all(
        response.evidence_source is ReaderEvidenceSource.FIXTURE
        for response in normalized
    )
    reader_ready = bool(cell_reports) and not blocking_codes and all(
        cell.reader_ready for cell in cell_reports
    )
    if reader_ready:
        release_status = "PASS_LIVE_READER"
    elif all_fixture and all(
        blocker in {"LIVE_READER_EVIDENCE_REQUIRED"}
        for cell in cell_reports
        for blocker in cell.blocking_codes
    ):
        release_status = "PASS_FIXTURE_BLOCKED_LIVE_READER"
    elif all_fixture:
        release_status = "PASS_FIXTURE_BLOCKED_LIVE_READER"
    else:
        release_status = "BLOCKED_READER_VALIDATION"
    return StoryEngineReaderStudyReport(
        study_key=manifest.study_key,
        cell_count=len(cell_reports),
        sample_size=len(normalized),
        live_response_count=sum(
            response.evidence_source is ReaderEvidenceSource.LIVE
            for response in normalized
        ),
        blocking_codes=tuple(dict.fromkeys(blocking_codes)),
        reader_ready=reader_ready,
        release_status=release_status,
        cells=tuple(cell_reports),
    )


def _jsonable(value: Any) -> Any:  # noqa: ANN401
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, UUID):
        return str(value)
    if is_dataclass(value) and not isinstance(value, type):
        return _jsonable(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def _canary_is_live_and_validated(campaign: StoryEngineCanaryCampaignModel) -> bool:
    cohort = (
        campaign.report_json.get("cohort")
        if isinstance(campaign.report_json, Mapping)
        else None
    )
    report_hash = (
        str(campaign.report_json.get("manifest_hash") or "")
        if isinstance(campaign.report_json, Mapping)
        else ""
    )
    return (
        campaign.status == "canary_validated"
        and campaign.evidence_source == "live"
        and isinstance(cohort, Mapping)
        and cohort.get("canary_ready") is True
        and canonical_json_hash(campaign.manifest_json) == campaign.manifest_hash
        and report_hash == campaign.manifest_hash
    )


def _reader_manifest_matches_canary_campaign(
    manifest: StoryEngineReaderStudyManifest,
    campaign: StoryEngineCanaryCampaignModel,
) -> bool:
    campaign_manifest = (
        campaign.manifest_json if isinstance(campaign.manifest_json, Mapping) else {}
    )
    campaign_experiment = str(campaign_manifest.get("experiment") or "")
    campaign_cells: set[tuple[str, str, UUID, UUID]] = set()
    raw_cells = campaign_manifest.get("cells")
    if isinstance(raw_cells, Sequence) and not isinstance(raw_cells, (str, bytes)):
        for raw_cell in raw_cells:
            if not isinstance(raw_cell, Mapping):
                continue
            try:
                baseline_id = UUID(str(raw_cell.get("legacy_project_id") or ""))
                engine_id = UUID(str(raw_cell.get("engine_project_id") or ""))
            except (TypeError, ValueError):
                continue
            campaign_cells.add(
                (
                    str(raw_cell.get("genre") or "").strip(),
                    str(raw_cell.get("seed") or "").strip(),
                    baseline_id,
                    engine_id,
                )
            )
    return all(
        cell.experiment == campaign_experiment
        and (
            cell.genre.strip(),
            cell.seed.strip(),
            cell.baseline_project_id,
            cell.engine_project_id,
        )
        in campaign_cells
        for cell in manifest.cells
    )


async def create_story_engine_reader_study(
    session: AsyncSession,
    *,
    manifest: StoryEngineReaderStudyManifest,
    source_run_id: UUID | None = None,
) -> StoryEngineReaderStudyModel:
    """Create an immutable study only after its source canary is live-valid."""

    campaign = await session.get(
        StoryEngineCanaryCampaignModel,
        manifest.canary_campaign_id,
    )
    if campaign is None or not _canary_is_live_and_validated(campaign):
        raise ValueError("reader study requires a matching live canary campaign")
    if not _reader_manifest_matches_canary_campaign(manifest, campaign):
        raise ValueError("reader study cells do not match the source canary campaign")

    manifest_hash = manifest.stable_hash()
    existing = await session.scalar(
        select(StoryEngineReaderStudyModel).where(
            StoryEngineReaderStudyModel.study_key == manifest.study_key
        )
    )
    if existing is not None:
        if (
            existing.manifest_hash != manifest_hash
            or canonical_json_hash(existing.manifest_json) != manifest_hash
        ):
            raise ValueError("reader study key already refers to a different manifest")
        return existing

    study = StoryEngineReaderStudyModel(
        study_key=manifest.study_key,
        canary_campaign_id=manifest.canary_campaign_id,
        status="planned",
        evidence_source="pending",
        manifest_hash=manifest_hash,
        manifest_json=manifest.to_mapping(),
        report_json={},
        source_run_id=source_run_id,
    )
    try:
        async with session.begin_nested():
            session.add(study)
            await session.flush()
    except IntegrityError:
        existing = await session.scalar(
            select(StoryEngineReaderStudyModel).where(
                StoryEngineReaderStudyModel.study_key == manifest.study_key
            )
        )
        if existing is None:
            raise
        if (
            existing.manifest_hash != manifest_hash
            or canonical_json_hash(existing.manifest_json) != manifest_hash
        ):
            raise ValueError(
                "reader study key already refers to a different manifest"
            ) from None
        return cast(StoryEngineReaderStudyModel, existing)
    return study


async def record_story_engine_reader_response(
    session: AsyncSession,
    *,
    study: StoryEngineReaderStudyModel,
    manifest: StoryEngineReaderStudyManifest,
    response: StoryEngineReaderResponse,
) -> StoryEngineReaderResponseModel:
    """Append one pseudonymous response with strict key/participant idempotency."""

    if study.manifest_hash != manifest.stable_hash():
        raise ValueError("reader response manifest does not match persisted study")
    if response.cell_key not in {cell.cell_key for cell in manifest.cells}:
        raise ValueError("reader response cell is not present in study manifest")
    existing = await session.scalar(
        select(StoryEngineReaderResponseModel).where(
            StoryEngineReaderResponseModel.study_id == study.id,
            or_(
                StoryEngineReaderResponseModel.response_key
                == response.response_key.strip(),
                StoryEngineReaderResponseModel.participant_hash
                == response.participant_hash.strip().lower(),
            ),
        )
    )
    payload = response.to_mapping()
    if existing is not None:
        existing_payload = {
            key: getattr(existing, key)
            for key in payload
            if key not in {"response_key", "participant_hash"}
        }
        incoming_payload = {
            key: value
            for key, value in payload.items()
            if key not in {"response_key", "participant_hash"}
        }
        if (
            existing.response_key != payload["response_key"]
            or existing.participant_hash != payload["participant_hash"]
            or existing_payload != incoming_payload
        ):
            raise ValueError("reader response key or participant already has other data")
        return existing

    row = StoryEngineReaderResponseModel(
        study_id=study.id,
        **payload,
    )
    session.add(row)
    await session.flush()
    study.status = "collecting"
    return row


def _response_from_model(row: StoryEngineReaderResponseModel) -> StoryEngineReaderResponse:
    return StoryEngineReaderResponse(
        response_key=row.response_key,
        participant_hash=row.participant_hash,
        cell_key=row.cell_key,
        assigned_order=row.assigned_order,
        preferred_variant=row.preferred_variant,
        engine_recall_accurate=row.engine_recall_accurate,
        baseline_recall_accurate=row.baseline_recall_accurate,
        engine_severe_abandonment=row.engine_severe_abandonment,
        baseline_severe_abandonment=row.baseline_severe_abandonment,
        evidence_source=ReaderEvidenceSource(row.evidence_source),
    )


async def finalize_story_engine_reader_study(
    session: AsyncSession,
    *,
    study: StoryEngineReaderStudyModel,
    manifest: StoryEngineReaderStudyManifest,
) -> StoryEngineReaderStudyModel:
    """Evaluate persisted responses and store an evidence-bounded maturity report."""

    if (
        study.manifest_hash != manifest.stable_hash()
        or canonical_json_hash(study.manifest_json) != manifest.stable_hash()
    ):
        raise ValueError("reader study manifest does not match persisted study")
    rows = tuple(
        (
            await session.scalars(
                select(StoryEngineReaderResponseModel).where(
                    StoryEngineReaderResponseModel.study_id == study.id
                )
            )
        ).all()
    )
    responses = tuple(_response_from_model(row) for row in rows)
    report = evaluate_story_engine_reader_study(manifest, responses)
    sources = {response.evidence_source for response in responses}
    source = next(iter(sources)).value if len(sources) == 1 else "mixed"
    if not sources:
        source = "unavailable"
    study.evidence_source = source
    study.report_json = {
        "schema_version": "story-engine-reader-study-report/v1",
        "study_key": manifest.study_key,
        "manifest_hash": manifest.stable_hash(),
        "canary_campaign_id": str(manifest.canary_campaign_id),
        "response_evidence_hash": canonical_json_hash(
            sorted(
                (response.to_mapping() for response in responses),
                key=lambda item: str(item["response_key"]),
            )
        ),
        "report": _jsonable(report),
    }
    if report.reader_ready:
        study.status = "reader_validated"
    elif "READER_SAMPLE_SIZE_INSUFFICIENT" in report.blocking_codes or source in {
        "fixture",
        "unavailable",
    }:
        study.status = "insufficient_data"
    else:
        study.status = "blocked"
    await session.flush()
    return study


__all__ = [
    "ReaderEvidenceSource",
    "StoryEngineReaderCellReport",
    "StoryEngineReaderResponse",
    "StoryEngineReaderStudyCellSpec",
    "StoryEngineReaderStudyManifest",
    "StoryEngineReaderStudyReport",
    "create_story_engine_reader_study",
    "evaluate_story_engine_reader_study",
    "finalize_story_engine_reader_study",
    "record_story_engine_reader_response",
]
