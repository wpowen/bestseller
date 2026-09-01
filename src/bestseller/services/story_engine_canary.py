"""Evidence-gated StoryEngine dual-write and canary evaluation.

This module evaluates observed receipt ledgers.  It never generates prose and
never turns fixture evidence into a live rollout claim.  A structurally clean
fixture may prove the harness, while only a complete live cohort may reach
``canary_validated`` maturity.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, is_dataclass
from enum import StrEnum
from itertools import pairwise
from typing import Any, cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.domain.story_engine import StoryEngineMaturity, canonical_json_hash
from bestseller.infra.db.models import StoryEngineCanaryCampaignModel


class CanaryEvidenceSource(StrEnum):
    FIXTURE = "fixture"
    LIVE = "live"


@dataclass(frozen=True, slots=True)
class StoryEngineCanaryCellSpec:
    genre: str
    seed: str
    legacy_project_id: UUID
    engine_project_id: UUID

    def __post_init__(self) -> None:
        if not self.genre.strip() or not self.seed.strip():
            raise ValueError("canary cell requires genre and seed")
        if self.legacy_project_id == self.engine_project_id:
            raise ValueError("canary cell variants require different projects")

    def to_mapping(self) -> dict[str, str]:
        return {
            "genre": self.genre.strip(),
            "seed": self.seed.strip(),
            "legacy_project_id": str(self.legacy_project_id),
            "engine_project_id": str(self.engine_project_id),
        }


@dataclass(frozen=True, slots=True)
class StoryEngineCanaryCampaignManifest:
    campaign_key: str
    experiment: str
    model: str
    generation_unit: str
    budget_tokens_per_variant: int
    chapter_count: int
    cells: tuple[StoryEngineCanaryCellSpec, ...]

    def __post_init__(self) -> None:
        if not self.campaign_key.strip() or not self.model.strip():
            raise ValueError("canary campaign requires key and fixed model")
        if self.experiment not in {"E1", "E2"}:
            raise ValueError("canary campaign experiment must be E1 or E2")
        if self.experiment == "E1" and self.generation_unit not in {
            "scene",
            "chapter",
        }:
            raise ValueError("E1 canary generation unit must be scene or chapter")
        if self.experiment == "E2" and self.generation_unit != "paired":
            raise ValueError("E2 canary generation unit must be paired")
        if self.budget_tokens_per_variant <= 0:
            raise ValueError("canary campaign requires a positive token budget")
        if self.chapter_count != 10:
            raise ValueError("canary campaign requires exactly ten chapters")

        seeds_by_genre: dict[str, set[str]] = defaultdict(set)
        identities: set[tuple[str, str]] = set()
        project_ids: set[UUID] = set()
        for cell in self.cells:
            genre = cell.genre.strip()
            seed = cell.seed.strip()
            seeds_by_genre[genre].add(seed)
            identities.add((genre, seed))
            project_ids.update((cell.legacy_project_id, cell.engine_project_id))
        if len(seeds_by_genre) != 3:
            raise ValueError("canary campaign requires exactly three genres")
        if any(len(seeds) < 2 for seeds in seeds_by_genre.values()):
            raise ValueError("canary campaign requires two seeds per genre")
        if len(self.cells) != 6 or len(identities) != 6:
            raise ValueError("canary campaign requires six unique genre-seed cells")
        if len(project_ids) != 12:
            raise ValueError("canary campaign requires isolated variant projects")

    def to_mapping(self) -> dict[str, Any]:
        return {
            "schema_version": "story-engine-canary-campaign/v1",
            "campaign_key": self.campaign_key.strip(),
            "experiment": self.experiment,
            "model": self.model.strip(),
            "generation_unit": self.generation_unit,
            "budget_tokens_per_variant": self.budget_tokens_per_variant,
            "chapter_count": self.chapter_count,
            "cells": [cell.to_mapping() for cell in self.cells],
        }

    def stable_hash(self) -> str:
        return canonical_json_hash(self.to_mapping())


@dataclass(frozen=True, slots=True)
class StoryEngineCanaryCellObservation:
    evidence_source: CanaryEvidenceSource
    receipts: tuple[Mapping[str, Any], ...]
    engine_prompt_tokens: int
    legacy_prompt_tokens: int
    engine_hard_failures: int
    legacy_hard_failures: int

    def __post_init__(self) -> None:
        if self.engine_prompt_tokens < 0 or self.legacy_prompt_tokens < 0:
            raise ValueError("canary prompt token counts cannot be negative")
        if self.engine_hard_failures < 0 or self.legacy_hard_failures < 0:
            raise ValueError("canary hard failure counts cannot be negative")


@dataclass(frozen=True)
class StoryEngineCanaryCellReport:
    genre: str
    seed: str
    evidence_source: CanaryEvidenceSource
    chapter_count: int
    receipt_replay_rate: float
    chapter_reset_count: int
    repeated_choice_fingerprint_count: int
    concrete_state_delta_coverage: float
    opponent_or_obligation_coverage: float
    prompt_token_ratio: float | None
    engine_hard_failures: int
    legacy_hard_failures: int
    blocking_codes: tuple[str, ...]
    structure_passed: bool
    canary_ready: bool
    maturity: StoryEngineMaturity
    release_status: str


@dataclass(frozen=True)
class StoryEngineCanaryCohortReport:
    cell_count: int
    genre_count: int
    minimum_seeds_per_genre: int
    live_cell_count: int
    blocking_codes: tuple[str, ...]
    structure_passed: bool
    canary_ready: bool
    maturity: StoryEngineMaturity
    release_status: str


def _mapping(value: Any) -> Mapping[str, Any]:  # noqa: ANN401
    return value if isinstance(value, Mapping) else {}


def _sequence(value: Any) -> Sequence[Any]:  # noqa: ANN401
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return value
    return ()


def _chapter_has_concrete_delta(content: Mapping[str, Any]) -> bool:
    receipt = _mapping(content.get("receipt"))
    for transition in _sequence(receipt.get("observed_transitions")):
        row = _mapping(transition)
        if (
            str(row.get("key") or "").strip()
            and str(row.get("operator") or "").strip()
            and row.get("before") != row.get("after")
        ):
            return True
    return False


def _chapter_has_counteraction_or_obligation(content: Mapping[str, Any]) -> bool:
    receipt = _mapping(content.get("receipt"))
    if str(receipt.get("opponent_counteraction") or "").strip():
        return True
    return any(
        str(item).strip() for item in _sequence(receipt.get("new_obligations"))
    )


def _choice_fingerprint(content: Mapping[str, Any]) -> str:
    receipt = _mapping(content.get("receipt"))
    fingerprint = str(receipt.get("fingerprint") or "").strip()
    if fingerprint:
        return fingerprint
    return str(_mapping(content.get("_meta")).get("projection_hash") or "").strip()


def evaluate_story_engine_canary_cell(
    receipts: Sequence[Mapping[str, Any]],
    *,
    genre: str,
    seed: str,
    evidence_source: CanaryEvidenceSource,
    engine_prompt_tokens: int,
    legacy_prompt_tokens: int,
    engine_hard_failures: int,
    legacy_hard_failures: int,
) -> StoryEngineCanaryCellReport:
    """Evaluate one fixed-genre, fixed-seed, ten-chapter E1 cell."""

    ordered = sorted(
        (dict(item) for item in receipts),
        key=lambda item: int(item.get("chapter_number") or 0),
    )
    chapter_count = len(ordered)
    blocking_codes: list[str] = []
    chapter_numbers = [int(item.get("chapter_number") or 0) for item in ordered]
    if chapter_count != 10 or chapter_numbers != list(range(1, 11)):
        blocking_codes.append("CANARY_TEN_CONTIGUOUS_CHAPTERS_REQUIRED")

    replay_count = sum(
        1
        for item in ordered
        if item.get("verdict") == "matched"
        and item.get("replay_passed") is True
        and not item.get("blocking_codes")
    )
    receipt_replay_rate = replay_count / chapter_count if chapter_count else 0.0
    if receipt_replay_rate != 1.0:
        blocking_codes.append("CANARY_RECEIPT_REPLAY_INCOMPLETE")

    chapter_reset_count = 0
    for previous, current in pairwise(ordered):
        if str(current.get("pre_state_hash") or "") != str(
            previous.get("post_state_hash") or ""
        ):
            chapter_reset_count += 1
    if chapter_reset_count:
        blocking_codes.append("CANARY_CHAPTER_RESET")

    fingerprints = [value for item in ordered if (value := _choice_fingerprint(item))]
    repeated_choice_fingerprint_count = sum(
        count - 1 for count in Counter(fingerprints).values() if count > 1
    )
    if repeated_choice_fingerprint_count > 1:
        blocking_codes.append("CANARY_REPEATED_CHOICE_FINGERPRINT")

    concrete_count = sum(_chapter_has_concrete_delta(item) for item in ordered)
    concrete_state_delta_coverage = (
        concrete_count / chapter_count if chapter_count else 0.0
    )
    if concrete_state_delta_coverage != 1.0:
        blocking_codes.append("CANARY_CONCRETE_STATE_DELTA_INCOMPLETE")

    response_count = sum(
        _chapter_has_counteraction_or_obligation(item) for item in ordered
    )
    opponent_or_obligation_coverage = (
        response_count / chapter_count if chapter_count else 0.0
    )
    if opponent_or_obligation_coverage < 0.8:
        blocking_codes.append("CANARY_OPPONENT_OR_OBLIGATION_COVERAGE_LOW")

    prompt_token_ratio: float | None = None
    if legacy_prompt_tokens <= 0 or engine_prompt_tokens < 0:
        blocking_codes.append("CANARY_PROMPT_TOKEN_EVIDENCE_MISSING")
    else:
        prompt_token_ratio = round(engine_prompt_tokens / legacy_prompt_tokens, 4)
        if prompt_token_ratio > 1.05:
            blocking_codes.append("CANARY_PROMPT_TOKEN_REGRESSION")

    normalized_engine_failures = max(0, int(engine_hard_failures))
    normalized_legacy_failures = max(0, int(legacy_hard_failures))
    if normalized_engine_failures > normalized_legacy_failures:
        blocking_codes.append("CANARY_HARD_INTEGRITY_REGRESSION")

    blocking_codes = list(dict.fromkeys(blocking_codes))
    structure_passed = not blocking_codes
    if structure_passed and evidence_source is CanaryEvidenceSource.LIVE:
        canary_ready = True
        maturity = StoryEngineMaturity.CANARY_VALIDATED
        release_status = "PASS_LIVE_CANARY_CELL"
    elif structure_passed:
        canary_ready = False
        maturity = StoryEngineMaturity.SHADOW_VALIDATED
        blocking_codes.append("LIVE_CANARY_EVIDENCE_REQUIRED")
        release_status = "PASS_FIXTURE"
    else:
        canary_ready = False
        maturity = StoryEngineMaturity.STRUCTURE_ONLY
        release_status = "BLOCKED_CANARY_CELL"

    return StoryEngineCanaryCellReport(
        genre=str(genre).strip(),
        seed=str(seed).strip(),
        evidence_source=CanaryEvidenceSource(evidence_source),
        chapter_count=chapter_count,
        receipt_replay_rate=round(receipt_replay_rate, 4),
        chapter_reset_count=chapter_reset_count,
        repeated_choice_fingerprint_count=repeated_choice_fingerprint_count,
        concrete_state_delta_coverage=round(concrete_state_delta_coverage, 4),
        opponent_or_obligation_coverage=round(
            opponent_or_obligation_coverage, 4
        ),
        prompt_token_ratio=prompt_token_ratio,
        engine_hard_failures=normalized_engine_failures,
        legacy_hard_failures=normalized_legacy_failures,
        blocking_codes=tuple(blocking_codes),
        structure_passed=structure_passed,
        canary_ready=canary_ready,
        maturity=maturity,
        release_status=release_status,
    )


def aggregate_story_engine_canary_cells(
    cells: Sequence[StoryEngineCanaryCellReport],
) -> StoryEngineCanaryCohortReport:
    """Apply the three-genre, two-seed cohort gate without evidence inflation."""

    normalized = tuple(cells)
    seeds_by_genre: dict[str, set[str]] = defaultdict(set)
    for cell in normalized:
        seeds_by_genre[cell.genre].add(cell.seed)
    genre_count = len(seeds_by_genre)
    minimum_seeds_per_genre = (
        min((len(seeds) for seeds in seeds_by_genre.values()), default=0)
    )
    live_cell_count = sum(
        cell.evidence_source is CanaryEvidenceSource.LIVE for cell in normalized
    )
    structure_passed = bool(normalized) and all(
        cell.structure_passed for cell in normalized
    )

    blocking_codes: list[str] = []
    for cell in normalized:
        blocking_codes.extend(cell.blocking_codes)
    if len(normalized) < 6 or genre_count < 3 or minimum_seeds_per_genre < 2:
        blocking_codes.append("CANARY_COHORT_COVERAGE_INCOMPLETE")
    complete_live_cohort = (
        len(normalized) >= 6
        and genre_count >= 3
        and minimum_seeds_per_genre >= 2
        and live_cell_count == len(normalized)
    )
    canary_ready = (
        structure_passed
        and complete_live_cohort
        and all(cell.canary_ready for cell in normalized)
    )
    if canary_ready:
        maturity = StoryEngineMaturity.CANARY_VALIDATED
        release_status = "PASS_LIVE_CANARY"
    elif structure_passed and normalized:
        maturity = StoryEngineMaturity.SHADOW_VALIDATED
        blocking_codes.append("LIVE_CANARY_EVIDENCE_REQUIRED")
        release_status = "PASS_FIXTURE_BLOCKED_LIVE_CANARY"
    else:
        maturity = StoryEngineMaturity.STRUCTURE_ONLY
        release_status = "BLOCKED_CANARY_COHORT"

    return StoryEngineCanaryCohortReport(
        cell_count=len(normalized),
        genre_count=genre_count,
        minimum_seeds_per_genre=minimum_seeds_per_genre,
        live_cell_count=live_cell_count,
        blocking_codes=tuple(dict.fromkeys(blocking_codes)),
        structure_passed=structure_passed,
        canary_ready=canary_ready,
        maturity=maturity,
        release_status=release_status,
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


async def create_story_engine_canary_campaign(
    session: AsyncSession,
    *,
    manifest: StoryEngineCanaryCampaignManifest,
    source_run_id: UUID | None = None,
) -> StoryEngineCanaryCampaignModel:
    """Create or reuse one frozen campaign manifest with strict idempotency."""

    manifest_json = manifest.to_mapping()
    manifest_hash = manifest.stable_hash()
    existing = await session.scalar(
        select(StoryEngineCanaryCampaignModel).where(
            StoryEngineCanaryCampaignModel.campaign_key == manifest.campaign_key
        )
    )
    if existing is not None:
        if (
            existing.manifest_hash != manifest_hash
            or canonical_json_hash(existing.manifest_json) != manifest_hash
        ):
            raise ValueError("campaign key already refers to a different manifest")
        return existing

    campaign = StoryEngineCanaryCampaignModel(
        campaign_key=manifest.campaign_key,
        experiment=manifest.experiment,
        status="planned",
        evidence_source="pending",
        manifest_hash=manifest_hash,
        manifest_json=manifest_json,
        report_json={},
        source_run_id=source_run_id,
    )
    try:
        async with session.begin_nested():
            session.add(campaign)
            await session.flush()
    except IntegrityError:
        existing = await session.scalar(
            select(StoryEngineCanaryCampaignModel).where(
                StoryEngineCanaryCampaignModel.campaign_key == manifest.campaign_key
            )
        )
        if existing is None:
            raise
        if (
            existing.manifest_hash != manifest_hash
            or canonical_json_hash(existing.manifest_json) != manifest_hash
        ):
            raise ValueError(
                "campaign key already refers to a different manifest"
            ) from None
        return cast(StoryEngineCanaryCampaignModel, existing)
    return campaign


async def execute_story_engine_canary_campaign(
    session: AsyncSession,
    *,
    campaign: StoryEngineCanaryCampaignModel,
    manifest: StoryEngineCanaryCampaignManifest,
    run_cell: Callable[
        [StoryEngineCanaryCellSpec],
        Awaitable[StoryEngineCanaryCellObservation],
    ],
) -> StoryEngineCanaryCampaignModel:
    """Execute six isolated cells and persist one evidence-bounded report."""

    if campaign.manifest_hash != manifest.stable_hash() or (
        canonical_json_hash(campaign.manifest_json) != manifest.stable_hash()
    ):
        raise ValueError("campaign manifest does not match persisted campaign")
    if manifest.experiment != "E1":
        campaign.status = "blocked"
        campaign.evidence_source = "unavailable"
        campaign.report_json = {
            "schema_version": "story-engine-canary-report/v1",
            "campaign_key": manifest.campaign_key,
            "manifest_hash": manifest.stable_hash(),
            "experiment": manifest.experiment,
            "blocking_codes": ["E2_REQUIRES_GENERATION_MODE_REPORT"],
        }
        await session.flush()
        raise ValueError("E2 generation-mode report requires the dedicated finalizer")
    if campaign.status in {"blocked", "fixture_validated", "canary_validated"}:
        return campaign

    campaign.status = "running"
    await session.flush()
    cell_reports: list[StoryEngineCanaryCellReport] = []
    sources: set[CanaryEvidenceSource] = set()
    for cell in manifest.cells:
        try:
            observation = await run_cell(cell)
        except Exception as exc:
            campaign.status = "blocked"
            campaign.evidence_source = "unavailable"
            campaign.report_json = {
                "schema_version": "story-engine-canary-report/v1",
                "campaign_key": manifest.campaign_key,
                "manifest_hash": manifest.stable_hash(),
                "evidence_source": "unavailable",
                "blocking_codes": ["CANARY_CELL_EXECUTION_FAILED"],
                "failed_cell": {
                    "genre": cell.genre,
                    "seed": cell.seed,
                },
                "error_type": type(exc).__name__,
                "error": str(exc)[:500],
            }
            await session.flush()
            raise
        sources.add(CanaryEvidenceSource(observation.evidence_source))
        cell_reports.append(
            evaluate_story_engine_canary_cell(
                observation.receipts,
                genre=cell.genre,
                seed=cell.seed,
                evidence_source=observation.evidence_source,
                engine_prompt_tokens=observation.engine_prompt_tokens,
                legacy_prompt_tokens=observation.legacy_prompt_tokens,
                engine_hard_failures=observation.engine_hard_failures,
                legacy_hard_failures=observation.legacy_hard_failures,
            )
        )

    cohort = aggregate_story_engine_canary_cells(cell_reports)
    mixed_sources = len(sources) != 1
    source = next(iter(sources)).value if not mixed_sources else "mixed"
    cohort_payload = _jsonable(cohort)
    if mixed_sources:
        cohort_payload["canary_ready"] = False
        cohort_payload["maturity"] = StoryEngineMaturity.STRUCTURE_ONLY.value
        cohort_payload["release_status"] = "BLOCKED_MIXED_EVIDENCE_SOURCE"
        cohort_payload["blocking_codes"] = list(
            dict.fromkeys(
                [
                    *cohort_payload.get("blocking_codes", []),
                    "CANARY_MIXED_EVIDENCE_SOURCE",
                ]
            )
        )

    campaign.evidence_source = source
    campaign.report_json = {
        "schema_version": "story-engine-canary-report/v1",
        "campaign_key": manifest.campaign_key,
        "manifest_hash": manifest.stable_hash(),
        "evidence_source": source,
        "cells": [_jsonable(report) for report in cell_reports],
        "cohort": cohort_payload,
    }
    if cohort_payload["canary_ready"] is True:
        campaign.status = "canary_validated"
    elif (
        source == CanaryEvidenceSource.FIXTURE.value
        and cohort_payload["structure_passed"] is True
    ):
        campaign.status = "fixture_validated"
    else:
        campaign.status = "blocked"
    await session.flush()
    return campaign


async def finalize_story_engine_e2_campaign(
    session: AsyncSession,
    *,
    campaign: StoryEngineCanaryCampaignModel,
    manifest: StoryEngineCanaryCampaignManifest,
    evidence_source: CanaryEvidenceSource,
    generation_mode_report: Mapping[str, Any],
) -> StoryEngineCanaryCampaignModel:
    """Persist one dedicated E2 verdict without reusing E1 receipt metrics."""

    manifest_hash = manifest.stable_hash()
    if (
        campaign.manifest_hash != manifest_hash
        or canonical_json_hash(campaign.manifest_json) != manifest_hash
    ):
        raise ValueError("campaign manifest does not match persisted campaign")
    if manifest.experiment != "E2" or campaign.experiment != "E2":
        raise ValueError("E2 finalizer requires an E2 campaign")
    gate = _mapping(generation_mode_report.get("e2_gate"))
    release_status = str(gate.get("release_status") or "")
    recommended_mode = str(gate.get("recommended_mode") or "")
    blocking_codes = [str(item) for item in _sequence(gate.get("blocking_codes"))]
    conclusive_statuses = {"PASS_E2_CHAPTER_FIRST", "PASS_E2_KEEP_SCENE"}
    evidence_blockers = {
        "E2_PAIRED_SAMPLES_INCOMPLETE",
        "E2_INDEPENDENT_REVIEW_PATHS_INSUFFICIENT",
        "E2_POSITION_SWAP_AGREEMENT_INSUFFICIENT",
    }
    conclusive = (
        release_status in conclusive_statuses
        and recommended_mode in {"chapter_first", "scene_by_scene"}
        and not any(code in evidence_blockers for code in blocking_codes)
    )
    source = CanaryEvidenceSource(evidence_source)
    campaign.evidence_source = source.value
    campaign.report_json = {
        "schema_version": "story-engine-e2-report/v1",
        "campaign_key": manifest.campaign_key,
        "manifest_hash": manifest_hash,
        "experiment": "E2",
        "evidence_source": source.value,
        "decision": str(generation_mode_report.get("decision") or "inconclusive"),
        "e2_gate": _jsonable(gate),
        "generation_mode_report": _jsonable(generation_mode_report),
    }
    if conclusive and source is CanaryEvidenceSource.LIVE:
        campaign.status = "canary_validated"
    elif conclusive and source is CanaryEvidenceSource.FIXTURE:
        campaign.status = "fixture_validated"
    else:
        campaign.status = "blocked"
    await session.flush()
    return campaign


__all__ = [
    "CanaryEvidenceSource",
    "StoryEngineCanaryCampaignManifest",
    "StoryEngineCanaryCellObservation",
    "StoryEngineCanaryCellReport",
    "StoryEngineCanaryCellSpec",
    "StoryEngineCanaryCohortReport",
    "aggregate_story_engine_canary_cells",
    "create_story_engine_canary_campaign",
    "evaluate_story_engine_canary_cell",
    "execute_story_engine_canary_campaign",
    "finalize_story_engine_e2_campaign",
]
