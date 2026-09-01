"""Fail-closed adapters between legacy planning state and StoryEngine V2."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
import json
from typing import Any, Protocol, cast
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.domain.enums import ArtifactType
from bestseller.domain.story_engine import (
    StoryEngineDefinition,
    StoryEngineMaturity,
    StoryEngineWindow,
    canonical_json_hash,
    story_engine_definition_to_mapping,
    story_engine_window_to_mapping,
    validate_engine_definition,
)
from bestseller.domain.story_state import StateCategory
from bestseller.infra.db.models import (
    PlanningArtifactVersionModel,
    StoryEngineCanaryCampaignModel,
    StoryEngineReaderStudyModel,
)
from bestseller.services.premium_state_ledger import materialize_premium_state_snapshot
from bestseller.services.story_design_kernel import (
    StoryDesignKernel,
    story_design_kernel_from_dict,
    story_design_kernel_to_dict,
)

STORY_ENGINE_ARTIFACT_TYPE = ArtifactType.STORY_ENGINE_V2.value
STORY_ENGINE_ARTIFACT_SCHEMA_VERSION = "2.0"
STORY_ENGINE_WINDOW_ARTIFACT_TYPE = ArtifactType.STORY_ENGINE_WINDOW_V2.value
STORY_ENGINE_WINDOW_ARTIFACT_SCHEMA_VERSION = "2.0"
STORY_ENGINE_ARTIFACT_STATUSES = frozenset(
    {
        "structure_only",
        "shadow_validated",
        "canary_validated",
        "reader_validated",
        "canonical",
        "needs_replan",
    }
)
STORY_ENGINE_ACTIVE_MATURITIES = frozenset(
    {
        StoryEngineMaturity.CANARY_VALIDATED,
        StoryEngineMaturity.READER_VALIDATED,
        StoryEngineMaturity.CANONICAL,
    }
)
STORY_ENGINE_MODES = frozenset({"off", "shadow", "dual_write", "canary", "canonical"})


class _MetadataCarrier(Protocol):
    metadata_json: object


@dataclass(frozen=True, slots=True)
class StoryEngineRolloutDecision:
    """One fail-closed rollout decision with machine-readable diagnostics."""

    requested_mode: str
    effective_mode: str
    authorized: bool
    blocking_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class StoryEngineVerifiedRolloutEvidence:
    """Database-resolved evidence IDs allowed to authorize canonical mode."""

    canary_campaign_id: UUID | None = None
    reader_study_id: UUID | None = None


class LegacyProjectionStatus(StrEnum):
    STRUCTURE_ONLY = "structure_only"
    NEEDS_REPLAN = "needs_replan"


def _configured_story_engine_mode(project: object, settings: object) -> str:
    """Resolve the requested mode before rollout authority is applied."""

    metadata = _mapping(getattr(project, "metadata_json", None))
    override = str(metadata.get("story_engine_mode") or "").strip().lower()
    if override in STORY_ENGINE_MODES:
        return override
    pipeline = getattr(settings, "pipeline", None)
    configured = str(getattr(pipeline, "story_engine_mode", "shadow") or "shadow")
    configured = configured.strip().lower()
    return configured if configured in STORY_ENGINE_MODES else "off"


def _normalized_setting_values(settings: object, key: str) -> frozenset[str]:
    pipeline = getattr(settings, "pipeline", None)
    raw = getattr(pipeline, key, ())
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        return frozenset()
    return frozenset(str(item).strip().casefold() for item in raw if str(item).strip())


def _live_evidence_reference_passed(value: object) -> bool:
    evidence = _mapping(value)
    status = str(evidence.get("status") or "").strip().lower()
    source = str(evidence.get("evidence_source") or "").strip().lower()
    artifact_id = str(evidence.get("artifact_id") or "").strip()
    if status not in {"pass", "passed", "validated"} or source != "live":
        return False
    try:
        UUID(artifact_id)
    except (TypeError, ValueError):
        return False
    return True


def _evidence_reference_id(value: object) -> UUID | None:
    if not _live_evidence_reference_passed(value):
        return None
    try:
        return UUID(str(_mapping(value).get("artifact_id") or "").strip())
    except (TypeError, ValueError):
        return None


def _verified_reference_matches(
    reference: object,
    verified_id: UUID | None,
) -> bool:
    reference_id = _evidence_reference_id(reference)
    return reference_id is not None and reference_id == verified_id


def resolve_story_engine_rollout_decision(
    project: object,
    settings: object,
    *,
    chapter_number: int | None = None,
    verified_evidence: StoryEngineVerifiedRolloutEvidence | None = None,
) -> StoryEngineRolloutDecision:
    """Authorize one rollout mode without trusting a project override by itself."""

    requested = _configured_story_engine_mode(project, settings)
    metadata = _mapping(getattr(project, "metadata_json", None))
    target_chapter = chapter_number
    if target_chapter is None:
        current = int(getattr(project, "current_chapter_number", 0) or 0)
        target_chapter = current + 1

    rollout_lock = _mapping(metadata.get("story_engine_rollout_lock"))
    locked_mode = str(rollout_lock.get("mode") or "").strip().lower()
    try:
        locked_chapter = int(str(rollout_lock.get("chapter_number") or 0))
    except ValueError:
        locked_chapter = 0
    if (
        locked_mode in STORY_ENGINE_MODES
        and locked_chapter == target_chapter
        and locked_mode != requested
    ):
        return StoryEngineRolloutDecision(
            requested_mode=requested,
            effective_mode=locked_mode,
            authorized=False,
            blocking_codes=("STORY_ENGINE_MODE_SWITCH_DURING_CHAPTER",),
        )

    blocking_codes: list[str] = []
    if requested in {"canary", "canonical"}:
        project_ids = _normalized_setting_values(
            settings,
            "story_engine_canary_project_ids",
        )
        project_id = str(getattr(project, "id", "") or "").strip().casefold()
        if not project_id or project_id not in project_ids:
            blocking_codes.append("STORY_ENGINE_CANARY_PROJECT_NOT_ALLOWLISTED")

        genres = _normalized_setting_values(settings, "story_engine_canary_genres")
        genre = str(getattr(project, "genre", "") or "").strip().casefold()
        if genres and genre not in genres:
            blocking_codes.append("STORY_ENGINE_CANARY_GENRE_NOT_ALLOWLISTED")

    if requested == "canonical":
        if not _verified_reference_matches(
            metadata.get("story_engine_canary_validation"),
            verified_evidence.canary_campaign_id if verified_evidence else None,
        ):
            blocking_codes.append("STORY_ENGINE_LIVE_CANARY_EVIDENCE_REQUIRED")
        pipeline = getattr(settings, "pipeline", None)
        require_reader = bool(
            getattr(
                pipeline,
                "story_engine_require_reader_validation_for_cutover",
                True,
            )
        )
        if require_reader and not _verified_reference_matches(
            metadata.get("story_engine_reader_validation"),
            verified_evidence.reader_study_id if verified_evidence else None,
        ):
            blocking_codes.append("STORY_ENGINE_READER_EVIDENCE_REQUIRED")

    blockers = tuple(dict.fromkeys(blocking_codes))
    return StoryEngineRolloutDecision(
        requested_mode=requested,
        effective_mode=requested if not blockers else "shadow",
        authorized=not blockers,
        blocking_codes=blockers,
    )


def resolve_story_engine_mode(
    project: object,
    settings: object,
    *,
    chapter_number: int | None = None,
) -> str:
    """Return the evidence-authorized rollout mode for one chapter boundary."""

    return resolve_story_engine_rollout_decision(
        project,
        settings,
        chapter_number=chapter_number,
    ).effective_mode


def _manifest_contains_engine_project(
    manifest_json: object,
    project_id: UUID,
) -> bool:
    for raw_cell in _sequence(_mapping(manifest_json).get("cells")):
        cell = _mapping(raw_cell)
        try:
            candidate = UUID(str(cell.get("engine_project_id") or "").strip())
        except (TypeError, ValueError):
            continue
        if candidate == project_id:
            return True
    return False


def _canary_campaign_authorizes_project(
    campaign: StoryEngineCanaryCampaignModel | None,
    *,
    project_id: UUID,
) -> bool:
    if campaign is None:
        return False
    report = _mapping(campaign.report_json)
    cohort = _mapping(report.get("cohort"))
    return (
        campaign.status == "canary_validated"
        and campaign.evidence_source == "live"
        and cohort.get("canary_ready") is True
        and canonical_json_hash(campaign.manifest_json) == campaign.manifest_hash
        and str(report.get("manifest_hash") or "") == campaign.manifest_hash
        and _manifest_contains_engine_project(campaign.manifest_json, project_id)
    )


def _reader_study_authorizes_project(
    study: StoryEngineReaderStudyModel | None,
    *,
    project_id: UUID,
    canary_campaign_id: UUID,
) -> bool:
    if study is None:
        return False
    report = _mapping(study.report_json)
    evaluation = _mapping(report.get("report"))
    evidence_hash = str(report.get("response_evidence_hash") or "")
    return (
        study.status == "reader_validated"
        and study.evidence_source == "live"
        and study.canary_campaign_id == canary_campaign_id
        and evaluation.get("reader_ready") is True
        and canonical_json_hash(study.manifest_json) == study.manifest_hash
        and str(report.get("manifest_hash") or "") == study.manifest_hash
        and len(evidence_hash) == 64
        and _manifest_contains_engine_project(study.manifest_json, project_id)
    )


async def resolve_story_engine_rollout_decision_from_db(
    session: AsyncSession,
    project: object,
    settings: object,
    *,
    chapter_number: int | None = None,
) -> StoryEngineRolloutDecision:
    """Resolve canonical authority from persisted live canary and reader evidence."""

    requested = _configured_story_engine_mode(project, settings)
    if requested != "canonical":
        return resolve_story_engine_rollout_decision(
            project,
            settings,
            chapter_number=chapter_number,
        )

    project_id_raw = getattr(project, "id", None)
    try:
        project_id = (
            project_id_raw
            if isinstance(project_id_raw, UUID)
            else UUID(str(project_id_raw or "").strip())
        )
    except (TypeError, ValueError):
        return resolve_story_engine_rollout_decision(
            project,
            settings,
            chapter_number=chapter_number,
        )

    metadata = _mapping(getattr(project, "metadata_json", None))
    canary_id = _evidence_reference_id(
        metadata.get("story_engine_canary_validation")
    )
    reader_id = _evidence_reference_id(
        metadata.get("story_engine_reader_validation")
    )
    verified_canary_id: UUID | None = None
    verified_reader_id: UUID | None = None
    if canary_id is not None:
        campaign = await session.get(StoryEngineCanaryCampaignModel, canary_id)
        if _canary_campaign_authorizes_project(campaign, project_id=project_id):
            verified_canary_id = canary_id
            if reader_id is not None:
                study = await session.get(StoryEngineReaderStudyModel, reader_id)
                if _reader_study_authorizes_project(
                    study,
                    project_id=project_id,
                    canary_campaign_id=canary_id,
                ):
                    verified_reader_id = reader_id

    return resolve_story_engine_rollout_decision(
        project,
        settings,
        chapter_number=chapter_number,
        verified_evidence=StoryEngineVerifiedRolloutEvidence(
            canary_campaign_id=verified_canary_id,
            reader_study_id=verified_reader_id,
        ),
    )


@dataclass(frozen=True, slots=True)
class LegacyStoryEngineProjection:
    status: LegacyProjectionStatus
    maturity: StoryEngineMaturity
    engine: StoryEngineDefinition | None
    source_hash: str
    blocking_codes: tuple[str, ...]
    warnings: tuple[str, ...] = ()

    @property
    def can_drive_generation(self) -> bool:
        """Legacy projection is shadow evidence, never a generation authority."""

        return False


def _mapping(value: object) -> dict[str, object]:
    return dict(value) if isinstance(value, Mapping) else {}


def _sequence(value: object) -> list[object]:
    return list(value) if isinstance(value, (list, tuple)) else []


def _numeric(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _put_state(
    state: dict[str, dict[str, object]],
    *,
    key: str,
    category: StateCategory,
    value: object,
    blocking_codes: list[str],
) -> None:
    candidate = {"category": category.value, "value": value}
    existing = state.get(key)
    if existing is not None and existing != candidate:
        blocking_codes.append("LEGACY_STATE_KEY_CONFLICT")
        return
    state[key] = candidate


def _project_resource_balances(
    snapshot: Mapping[str, object],
    state: dict[str, dict[str, object]],
    blocking_codes: list[str],
) -> None:
    for owner, raw_balances in _mapping(snapshot.get("resource_balances")).items():
        balances = _mapping(raw_balances)
        for resource, raw_value in balances.items():
            value = _numeric(raw_value)
            if value is None:
                blocking_codes.append("LEGACY_RESOURCE_BALANCE_INVALID")
                continue
            _put_state(
                state,
                key=f"resource:{owner}:{resource}",
                category=StateCategory.RESOURCE,
                value=value,
                blocking_codes=blocking_codes,
            )


def _project_relationship_state(
    snapshot: Mapping[str, object],
    state: dict[str, dict[str, object]],
    blocking_codes: list[str],
) -> None:
    for relationship, raw_entry in _mapping(snapshot.get("relationship_state")).items():
        entry = _mapping(raw_entry)
        axes = _mapping(entry.get("axes"))
        for axis, value in axes.items():
            _put_state(
                state,
                key=f"relationship:{relationship}:{axis}",
                category=StateCategory.RELATIONSHIP,
                value=value,
                blocking_codes=blocking_codes,
            )


def _project_rule_state(
    snapshot: Mapping[str, object],
    state: dict[str, dict[str, object]],
    blocking_codes: list[str],
) -> None:
    for rule_key, raw_entry in _mapping(snapshot.get("rule_state")).items():
        entry = _mapping(raw_entry)
        if not entry:
            blocking_codes.append("LEGACY_RULE_STATE_INVALID")
            continue
        _put_state(
            state,
            key=f"knowledge:rule:{rule_key}",
            category=StateCategory.KNOWLEDGE,
            value=entry,
            blocking_codes=blocking_codes,
        )


def _project_agency_debts(
    snapshot: Mapping[str, object],
    state: dict[str, dict[str, object]],
    blocking_codes: list[str],
) -> None:
    for index, raw_entry in enumerate(_sequence(snapshot.get("open_agency_debts"))):
        entry = _mapping(raw_entry)
        owner = str(entry.get("owner") or "").strip()
        debt = str(entry.get("debt") or "").strip()
        if not owner or not debt:
            blocking_codes.append("LEGACY_AGENCY_DEBT_INVALID")
            continue
        _put_state(
            state,
            key=f"debt:{owner}:{index}",
            category=StateCategory.DEBT,
            value=entry,
            blocking_codes=blocking_codes,
        )


def _project_faction_pressure(
    snapshot: Mapping[str, object],
    state: dict[str, dict[str, object]],
    blocking_codes: list[str],
) -> None:
    for index, raw_entry in enumerate(_sequence(snapshot.get("faction_pressure_queue"))):
        entry = _mapping(raw_entry)
        faction = str(entry.get("faction") or "").strip()
        if not faction or not str(entry.get("reaction") or "").strip():
            blocking_codes.append("LEGACY_FACTION_PRESSURE_INVALID")
            continue
        _put_state(
            state,
            key=f"exposure:faction:{faction}:{index}",
            category=StateCategory.EXPOSURE,
            value=entry,
            blocking_codes=blocking_codes,
        )


def _project_initial_state(
    snapshot: Mapping[str, object],
) -> tuple[dict[str, dict[str, object]], tuple[str, ...]]:
    state: dict[str, dict[str, object]] = {}
    blocking_codes: list[str] = []
    _project_resource_balances(snapshot, state, blocking_codes)
    _project_relationship_state(snapshot, state, blocking_codes)
    _project_rule_state(snapshot, state, blocking_codes)
    _project_agency_debts(snapshot, state, blocking_codes)
    _project_faction_pressure(snapshot, state, blocking_codes)
    if not state:
        blocking_codes.append("LEGACY_PREMIUM_STATE_EMPTY")
    return state, tuple(dict.fromkeys(blocking_codes))


def _engine_invariants(kernel: StoryDesignKernel) -> tuple[str, ...]:
    invariants: list[str] = [*kernel.uniqueness_constraints]
    freshness_rule = kernel.structure_strategy.freshness_rule.strip()
    if freshness_rule:
        invariants.append(freshness_rule)
    if kernel.worldview_kernel is not None:
        invariants.extend(item.rule for item in kernel.worldview_kernel.invariants)
    return tuple(dict.fromkeys(item.strip() for item in invariants if item.strip()))


def _needs_replan(
    *,
    source_hash: str,
    blocking_codes: Sequence[str],
) -> LegacyStoryEngineProjection:
    return LegacyStoryEngineProjection(
        status=LegacyProjectionStatus.NEEDS_REPLAN,
        maturity=StoryEngineMaturity.STRUCTURE_ONLY,
        engine=None,
        source_hash=source_hash,
        blocking_codes=tuple(dict.fromkeys(blocking_codes)),
    )


def project_legacy_story_engine(
    *,
    engine_id: str,
    kernel: StoryDesignKernel | Mapping[str, Any],
    premium_state_snapshot: Mapping[str, object] | None,
    premium_state_ledger: Mapping[str, object] | None = None,
) -> LegacyStoryEngineProjection:
    """Project legacy contracts for shadow use without inventing story facts."""

    raw_kernel: Mapping[str, Any]
    if isinstance(kernel, StoryDesignKernel):
        hydrated_kernel = kernel
        raw_kernel = story_design_kernel_to_dict(kernel)
    else:
        raw_kernel = kernel
        try:
            hydrated_kernel = story_design_kernel_from_dict(dict(kernel))
        except Exception:
            source_hash = canonical_json_hash(
                {"kernel": raw_kernel, "premium_state_snapshot": premium_state_snapshot}
            )
            return _needs_replan(
                source_hash=source_hash,
                blocking_codes=("LEGACY_STORY_DESIGN_KERNEL_INVALID",),
            )

    snapshot: Mapping[str, object] | None = premium_state_snapshot
    if snapshot is None and premium_state_ledger is not None:
        snapshot = materialize_premium_state_snapshot(premium_state_ledger)
    source_hash = canonical_json_hash(
        {
            "kernel": raw_kernel,
            "premium_state_snapshot": snapshot,
            "premium_state_ledger": premium_state_ledger,
        }
    )
    if not engine_id.strip():
        return _needs_replan(
            source_hash=source_hash,
            blocking_codes=("LEGACY_ENGINE_ID_MISSING",),
        )
    if snapshot is None:
        return _needs_replan(
            source_hash=source_hash,
            blocking_codes=("LEGACY_PREMIUM_STATE_SNAPSHOT_MISSING",),
        )
    if snapshot.get("passed") is not True:
        return _needs_replan(
            source_hash=source_hash,
            blocking_codes=("LEGACY_PREMIUM_STATE_SNAPSHOT_INVALID",),
        )

    initial_state, state_blockers = _project_initial_state(snapshot)
    if state_blockers:
        return _needs_replan(source_hash=source_hash, blocking_codes=state_blockers)

    engine = StoryEngineDefinition(
        engine_id=engine_id,
        version=max(1, hydrated_kernel.version),
        initial_state=initial_state,
        reader_promise=hydrated_kernel.reader_promise,
        change_vectors=tuple(hydrated_kernel.change_vectors),
        engine_invariants=_engine_invariants(hydrated_kernel),
    )
    return LegacyStoryEngineProjection(
        status=LegacyProjectionStatus.STRUCTURE_ONLY,
        maturity=StoryEngineMaturity.STRUCTURE_ONLY,
        engine=engine,
        source_hash=source_hash,
        blocking_codes=("LEGACY_REAL_CHOICES_UNAVAILABLE",),
        warnings=(
            "legacy projection has no observed choice alternatives or chapter receipts",
        ),
    )


def build_story_engine_artifact_content(
    projection: LegacyStoryEngineProjection,
    *,
    source_snapshot_hash: str,
    legacy_kernel_hash: str | None = None,
) -> dict[str, Any]:
    """Build a JSON-compatible immutable artifact envelope."""

    engine_payload = (
        story_engine_definition_to_mapping(projection.engine)
        if projection.engine is not None
        else None
    )
    engine_hash = canonical_json_hash(engine_payload) if engine_payload is not None else None
    kernel_hash = (legacy_kernel_hash or projection.source_hash).strip()
    lineage = {
        "source_snapshot_hash": source_snapshot_hash.strip(),
        "legacy_kernel_hash": kernel_hash,
        "engine_hash": engine_hash,
        "fallback_source": "legacy_projection",
    }
    input_hash = canonical_json_hash(
        {
            "engine": engine_payload,
            "projection_source_hash": projection.source_hash,
            **lineage,
        }
    )
    return {
        "artifact_type": STORY_ENGINE_ARTIFACT_TYPE,
        "schema_version": STORY_ENGINE_ARTIFACT_SCHEMA_VERSION,
        "projection_status": projection.status.value,
        "maturity": projection.maturity.value,
        "validity": "valid" if engine_payload is not None else "blocked",
        "can_drive_generation": projection.can_drive_generation,
        "blocking_codes": list(projection.blocking_codes),
        "warnings": list(projection.warnings),
        "source_hash": projection.source_hash,
        "engine": engine_payload,
        "_meta": {**lineage, "input_hash": input_hash},
    }


def build_story_engine_window_artifact_content(
    window: StoryEngineWindow | Mapping[str, Any],
    *,
    maturity: StoryEngineMaturity,
    can_drive_generation: bool,
) -> dict[str, Any]:
    """Build a bounded rolling-window envelope with explicit authority."""

    normalized = (
        window if isinstance(window, StoryEngineWindow) else StoryEngineWindow.from_mapping(window)
    )
    window_payload = story_engine_window_to_mapping(normalized)
    normalized_maturity = StoryEngineMaturity(maturity)
    if can_drive_generation and normalized_maturity not in STORY_ENGINE_ACTIVE_MATURITIES:
        raise ValueError("only canary-validated story engine windows may drive generation")
    window_hash = canonical_json_hash(window_payload)
    return {
        "artifact_type": STORY_ENGINE_WINDOW_ARTIFACT_TYPE,
        "schema_version": STORY_ENGINE_WINDOW_ARTIFACT_SCHEMA_VERSION,
        "status": normalized_maturity.value,
        "maturity": normalized_maturity.value,
        "can_drive_generation": bool(can_drive_generation),
        "window": window_payload,
        "_meta": {
            "engine_artifact_id": normalized.engine_artifact_id,
            "engine_hash": normalized.source_engine_hash,
            "window_hash": window_hash,
            "input_hash": canonical_json_hash(
                {
                    "engine_artifact_id": normalized.engine_artifact_id,
                    "engine_hash": normalized.source_engine_hash,
                    "window_hash": window_hash,
                }
            ),
        },
    }


def _validate_story_engine_window_content(content: Mapping[str, Any]) -> StoryEngineWindow:
    if content.get("artifact_type") != STORY_ENGINE_WINDOW_ARTIFACT_TYPE:
        raise ValueError("invalid story engine window artifact type")
    if content.get("schema_version") != STORY_ENGINE_WINDOW_ARTIFACT_SCHEMA_VERSION:
        raise ValueError("invalid story engine window schema version")
    try:
        maturity = StoryEngineMaturity(str(content.get("maturity") or ""))
    except ValueError as exc:
        raise ValueError("invalid story engine window maturity") from exc
    if content.get("can_drive_generation") is True and (
        maturity not in STORY_ENGINE_ACTIVE_MATURITIES
    ):
        raise ValueError("unvalidated story engine window cannot drive generation")
    raw_window = content.get("window")
    if not isinstance(raw_window, Mapping):
        raise ValueError("story engine window artifact requires window payload")
    window = StoryEngineWindow.from_mapping(raw_window)
    meta = content.get("_meta")
    if not isinstance(meta, Mapping):
        raise ValueError("story engine window artifact requires lineage metadata")
    if str(meta.get("engine_artifact_id") or "") != window.engine_artifact_id:
        raise ValueError("story engine window artifact lineage mismatch")
    if str(meta.get("engine_hash") or "") != window.source_engine_hash:
        raise ValueError("story engine window source hash mismatch")
    normalized_payload = story_engine_window_to_mapping(window)
    if str(meta.get("window_hash") or "") != canonical_json_hash(normalized_payload):
        raise ValueError("story engine window hash mismatch")
    return window


def chapter_creative_core_from_window_content(
    content: Mapping[str, Any],
    *,
    chapter_number: int,
    window_artifact_id: UUID,
) -> dict[str, Any] | None:
    """Project exactly one chapter; never expose the future window to a writer."""

    if content.get("can_drive_generation") is not True:
        return None
    return _chapter_core_from_window_content(
        content,
        chapter_number=chapter_number,
        window_artifact_id=window_artifact_id,
        can_drive_generation=True,
    )


def chapter_observation_core_from_window_content(
    content: Mapping[str, Any],
    *,
    chapter_number: int,
    window_artifact_id: UUID,
) -> dict[str, Any] | None:
    """Project one shadow row for review without granting writer authority."""

    return _chapter_core_from_window_content(
        content,
        chapter_number=chapter_number,
        window_artifact_id=window_artifact_id,
        can_drive_generation=False,
    )


def _chapter_core_from_window_content(
    content: Mapping[str, Any],
    *,
    chapter_number: int,
    window_artifact_id: UUID,
    can_drive_generation: bool,
) -> dict[str, Any] | None:
    window = _validate_story_engine_window_content(content)
    projections = cast(tuple[Any, ...], window.projections)
    projection = next(
        (item for item in projections if item.chapter_number == chapter_number),
        None,
    )
    if projection is None:
        return None
    payload = story_engine_window_to_mapping(
        StoryEngineWindow(
            window_id=window.window_id,
            engine_id=window.engine_id,
            engine_version=window.engine_version,
            engine_artifact_id=window.engine_artifact_id,
            source_engine_hash=window.source_engine_hash,
            projections=(projection,),
        )
    )["projections"][0]
    options = [
        {
            "choice_id": str(option.get("choice_id") or ""),
            "label": str(option.get("label") or ""),
        }
        for option in payload.get("options", [])
        if isinstance(option, Mapping)
    ]
    return {
        "engine_artifact_id": window.engine_artifact_id,
        "engine_version": window.engine_version,
        "window_artifact_id": str(window_artifact_id),
        "chapter_number": payload["chapter_number"],
        "choice_id": payload["choice_id"],
        "pre_state": payload["pre_state"],
        "pre_state_hash": payload["pre_state_hash"],
        "known_facts": payload["known_facts"],
        "pressure": payload["pressure"],
        "options": options,
        "chosen_path": payload["chosen_path"],
        "alternative_costs": payload["alternative_costs"],
        "opponent_strategy": payload["opponent_strategy"],
        "due_obligations": payload["due_obligations"],
        "required_state_changes": payload["required_state_changes"],
        "expected_post_state_hash": payload["expected_post_state_hash"],
        "projection_hash": payload["projection_hash"],
        "can_drive_generation": can_drive_generation,
    }


def apply_story_engine_projection_to_chapter(
    chapter: object,
    *,
    scenes: Sequence[object],
    creative_core: Mapping[str, Any],
) -> None:
    """Attach one validated projection while preserving unrelated metadata."""

    if creative_core.get("can_drive_generation") is not True:
        raise ValueError("non-authoritative story engine projection cannot be materialized")
    chapter_number = int(creative_core.get("chapter_number") or 0)
    choice_id = str(creative_core.get("choice_id") or "").strip()
    projection_hash = str(creative_core.get("projection_hash") or "").strip()
    if chapter_number < 1 or not choice_id or not projection_hash:
        raise ValueError("story engine projection reference is incomplete")
    chapter_carrier = cast(_MetadataCarrier, chapter)
    chapter_carrier.metadata_json = {
        **_mapping(getattr(chapter, "metadata_json", None)),
        "story_engine_projection": dict(creative_core),
    }
    projection_ref = {
        "chapter_number": chapter_number,
        "choice_id": choice_id,
        "projection_hash": projection_hash,
    }
    for scene in scenes:
        scene_carrier = cast(_MetadataCarrier, scene)
        scene_carrier.metadata_json = {
            **_mapping(getattr(scene, "metadata_json", None)),
            "story_engine_projection_ref": projection_ref,
        }


def render_story_engine_creative_core_block(
    creative_core: Mapping[str, Any] | None,
    *,
    language: str | None = None,
) -> str:
    """Render only the current chapter's causal packet for a prose writer."""

    if not isinstance(creative_core, Mapping):
        return ""
    if creative_core.get("can_drive_generation") is not True:
        return ""
    is_en = str(language or "").lower().startswith("en")
    options = [
        dict(item)
        for item in _sequence(creative_core.get("options"))
        if isinstance(item, Mapping)
    ]
    transitions = [
        dict(item)
        for item in _sequence(creative_core.get("required_state_changes"))
        if isinstance(item, Mapping)
    ]
    known_facts = [str(item) for item in _sequence(creative_core.get("known_facts"))]
    alternative_costs = [
        str(item) for item in _sequence(creative_core.get("alternative_costs"))
    ]
    obligations = [
        str(item) for item in _sequence(creative_core.get("due_obligations"))
    ]
    pre_state_json = json.dumps(
        creative_core.get("pre_state", {}),
        ensure_ascii=False,
        sort_keys=True,
    )
    transitions_json = json.dumps(
        transitions,
        ensure_ascii=False,
        sort_keys=True,
    )
    if is_en:
        lines = [
            "[StoryEngine creative core | TIER 0 | DO NOT TRIM]",
            "Use only this chapter packet. Never infer or reveal future-window facts.",
            f"- Engine version: {creative_core.get('engine_version')}",
            f"- Choice id: {creative_core.get('choice_id')}",
            f"- Pre-state hash: {creative_core.get('pre_state_hash')}",
            f"- Current pre-state: {pre_state_json}",
            f"- Known facts: {'; '.join(known_facts)}",
            f"- Pressure: {creative_core.get('pressure')}",
            "- Real options: "
            + "; ".join(
                f"{item.get('choice_id')}: {item.get('label')}" for item in options
            ),
            f"- Chosen path: {creative_core.get('chosen_path')}",
            f"- Cost of unchosen paths: {'; '.join(alternative_costs)}",
            f"- Opponent strategy: {creative_core.get('opponent_strategy')}",
            f"- Due obligations: {'; '.join(obligations)}",
            f"- Required state changes: {transitions_json}",
            f"- Expected post-state hash: {creative_core.get('expected_post_state_hash')}",
        ]
    else:
        lines = [
            "【StoryEngine 本章创意核心 | Tier 0 | 不可裁剪】",
            "只执行当前章投影,不得推断或泄漏滚动窗口中的未来章节事实。",
            f"- 引擎版本: {creative_core.get('engine_version')}",
            f"- 选择编号: {creative_core.get('choice_id')}",
            f"- 前置状态哈希: {creative_core.get('pre_state_hash')}",
            f"- 当前前置状态: {pre_state_json}",
            f"- 当前已知事实: {'; '.join(known_facts)}",
            f"- 本章压力: {creative_core.get('pressure')}",
            "- 真实可选路径: "
            + "; ".join(
                f"{item.get('choice_id')}: {item.get('label')}" for item in options
            ),
            f"- 已选路径: {creative_core.get('chosen_path')}",
            f"- 未选路径代价: {'; '.join(alternative_costs)}",
            f"- 对手策略: {creative_core.get('opponent_strategy')}",
            f"- 本章到期义务: {'; '.join(obligations)}",
            f"- 必须发生的状态变化: {transitions_json}",
            f"- 预期后置状态哈希: {creative_core.get('expected_post_state_hash')}",
        ]
    return "\n".join(lines)


def _validate_story_engine_artifact_content(content: Mapping[str, Any]) -> str:
    if content.get("artifact_type") != STORY_ENGINE_ARTIFACT_TYPE:
        raise ValueError("invalid story engine artifact type")
    if content.get("schema_version") != STORY_ENGINE_ARTIFACT_SCHEMA_VERSION:
        raise ValueError("invalid story engine artifact schema version")
    status = str(content.get("projection_status") or "").strip()
    if status not in STORY_ENGINE_ARTIFACT_STATUSES:
        raise ValueError("invalid story engine artifact status")
    meta = content.get("_meta")
    if not isinstance(meta, Mapping):
        raise ValueError("story engine artifact requires lineage metadata")
    if not str(meta.get("source_snapshot_hash") or "").strip():
        raise ValueError("story engine artifact requires source_snapshot_hash")
    if not str(meta.get("legacy_kernel_hash") or "").strip():
        raise ValueError("story engine artifact requires legacy_kernel_hash")
    return status


async def create_story_engine_artifact(
    session: AsyncSession,
    *,
    project_id: UUID,
    content: dict[str, Any],
    idempotency_key: str,
    source_run_id: UUID | None = None,
    notes: str | None = None,
    created_by: str = "story_engine_shadow",
) -> PlanningArtifactVersionModel:
    """Create or reuse one project-level StoryEngine artifact safely."""

    status = _validate_story_engine_artifact_content(content)
    key = idempotency_key.strip()
    if not key:
        raise ValueError("idempotency_key is required for story engine artifacts")
    existing = cast(
        PlanningArtifactVersionModel | None,
        await session.scalar(
            select(PlanningArtifactVersionModel).where(
                PlanningArtifactVersionModel.project_id == project_id,
                PlanningArtifactVersionModel.artifact_type == STORY_ENGINE_ARTIFACT_TYPE,
                PlanningArtifactVersionModel.scope_ref_id.is_(None),
                PlanningArtifactVersionModel.idempotency_key == key,
            )
        )
    )
    if existing is not None:
        if canonical_json_hash(existing.content) != canonical_json_hash(content):
            raise ValueError(
                "idempotency_key already refers to different story engine content"
            )
        return existing

    version_no = int(
        (
            await session.scalar(
                select(
                    func.coalesce(func.max(PlanningArtifactVersionModel.version_no), 0)
                ).where(
                    PlanningArtifactVersionModel.project_id == project_id,
                    PlanningArtifactVersionModel.artifact_type
                    == STORY_ENGINE_ARTIFACT_TYPE,
                    PlanningArtifactVersionModel.scope_ref_id.is_(None),
                )
            )
        )
        or 0
    ) + 1
    artifact = PlanningArtifactVersionModel(
        project_id=project_id,
        artifact_type=STORY_ENGINE_ARTIFACT_TYPE,
        scope_ref_id=None,
        version_no=version_no,
        status=status,
        schema_version=STORY_ENGINE_ARTIFACT_SCHEMA_VERSION,
        content=content,
        source_run_id=source_run_id,
        idempotency_key=key,
        notes=notes,
        created_by=created_by,
    )
    try:
        async with session.begin_nested():
            session.add(artifact)
            await session.flush()
    except IntegrityError:
        existing = cast(
            PlanningArtifactVersionModel | None,
            await session.scalar(
                select(PlanningArtifactVersionModel).where(
                    PlanningArtifactVersionModel.project_id == project_id,
                    PlanningArtifactVersionModel.artifact_type
                    == STORY_ENGINE_ARTIFACT_TYPE,
                    PlanningArtifactVersionModel.scope_ref_id.is_(None),
                    PlanningArtifactVersionModel.idempotency_key == key,
                )
            )
        )
        if existing is None:
            raise
        if canonical_json_hash(existing.content) != canonical_json_hash(content):
            raise ValueError(
                "idempotency_key already refers to different story engine content"
            ) from None
        return existing
    return artifact


async def create_story_engine_window_artifact(
    session: AsyncSession,
    *,
    project_id: UUID,
    content: dict[str, Any],
    idempotency_key: str,
    source_run_id: UUID | None = None,
    notes: str | None = None,
    created_by: str = "story_engine_window",
) -> PlanningArtifactVersionModel:
    """Create or reuse one bounded window using strict idempotency."""

    _validate_story_engine_window_content(content)
    status = str(content.get("status") or content.get("maturity") or "").strip()
    key = idempotency_key.strip()
    if not key:
        raise ValueError("idempotency_key is required for story engine windows")
    existing = cast(
        PlanningArtifactVersionModel | None,
        await session.scalar(
            select(PlanningArtifactVersionModel).where(
                PlanningArtifactVersionModel.project_id == project_id,
                PlanningArtifactVersionModel.artifact_type
                == STORY_ENGINE_WINDOW_ARTIFACT_TYPE,
                PlanningArtifactVersionModel.scope_ref_id.is_(None),
                PlanningArtifactVersionModel.idempotency_key == key,
            )
        ),
    )
    if existing is not None:
        if canonical_json_hash(existing.content) != canonical_json_hash(content):
            raise ValueError(
                "idempotency_key already refers to different story engine window content"
            )
        return existing
    version_no = int(
        (
            await session.scalar(
                select(
                    func.coalesce(func.max(PlanningArtifactVersionModel.version_no), 0)
                ).where(
                    PlanningArtifactVersionModel.project_id == project_id,
                    PlanningArtifactVersionModel.artifact_type
                    == STORY_ENGINE_WINDOW_ARTIFACT_TYPE,
                    PlanningArtifactVersionModel.scope_ref_id.is_(None),
                )
            )
        )
        or 0
    ) + 1
    artifact = PlanningArtifactVersionModel(
        project_id=project_id,
        artifact_type=STORY_ENGINE_WINDOW_ARTIFACT_TYPE,
        scope_ref_id=None,
        version_no=version_no,
        status=status,
        schema_version=STORY_ENGINE_WINDOW_ARTIFACT_SCHEMA_VERSION,
        content=content,
        source_run_id=source_run_id,
        idempotency_key=key,
        notes=notes,
        created_by=created_by,
    )
    try:
        async with session.begin_nested():
            session.add(artifact)
            await session.flush()
    except IntegrityError:
        existing = cast(
            PlanningArtifactVersionModel | None,
            await session.scalar(
                select(PlanningArtifactVersionModel).where(
                    PlanningArtifactVersionModel.project_id == project_id,
                    PlanningArtifactVersionModel.artifact_type
                    == STORY_ENGINE_WINDOW_ARTIFACT_TYPE,
                    PlanningArtifactVersionModel.scope_ref_id.is_(None),
                    PlanningArtifactVersionModel.idempotency_key == key,
                )
            ),
        )
        if existing is None:
            raise
        if canonical_json_hash(existing.content) != canonical_json_hash(content):
            raise ValueError(
                "idempotency_key already refers to different story engine window content"
            ) from None
        return existing
    return artifact


async def resolve_latest_story_engine_window_artifact(
    session: AsyncSession,
    *,
    project_id: UUID,
    require_generation_authority: bool = True,
) -> PlanningArtifactVersionModel | None:
    """Resolve an intact window, optionally requiring writer authority."""

    allowed_statuses = (
        tuple(item.value for item in STORY_ENGINE_ACTIVE_MATURITIES)
        if require_generation_authority
        else (
            StoryEngineMaturity.STRUCTURE_ONLY.value,
            StoryEngineMaturity.SHADOW_VALIDATED.value,
            StoryEngineMaturity.CANARY_VALIDATED.value,
            StoryEngineMaturity.READER_VALIDATED.value,
            StoryEngineMaturity.CANONICAL.value,
        )
    )

    artifact = cast(
        PlanningArtifactVersionModel | None,
        await session.scalar(
            select(PlanningArtifactVersionModel)
            .where(
                PlanningArtifactVersionModel.project_id == project_id,
                PlanningArtifactVersionModel.artifact_type
                == STORY_ENGINE_WINDOW_ARTIFACT_TYPE,
                PlanningArtifactVersionModel.scope_ref_id.is_(None),
                PlanningArtifactVersionModel.status.in_(allowed_statuses),
            )
            .order_by(
                PlanningArtifactVersionModel.version_no.desc(),
                PlanningArtifactVersionModel.created_at.desc(),
            )
            .limit(1)
        ),
    )
    if artifact is None or not isinstance(artifact.content, Mapping):
        return None
    if require_generation_authority and (
        artifact.content.get("can_drive_generation") is not True
    ):
        return None
    try:
        _validate_story_engine_window_content(artifact.content)
    except ValueError:
        return None
    return artifact


async def resolve_latest_story_engine_artifact(
    session: AsyncSession,
    *,
    project_id: UUID,
    source_snapshot_hash: str,
) -> PlanningArtifactVersionModel | None:
    """Resolve the latest non-blocked artifact with intact lineage and hash."""

    artifact = await session.scalar(
        select(PlanningArtifactVersionModel)
        .where(
            PlanningArtifactVersionModel.project_id == project_id,
            PlanningArtifactVersionModel.artifact_type == STORY_ENGINE_ARTIFACT_TYPE,
            PlanningArtifactVersionModel.scope_ref_id.is_(None),
            PlanningArtifactVersionModel.status.in_(
                {
                    "structure_only",
                    "shadow_validated",
                    "canary_validated",
                    "reader_validated",
                    "canonical",
                }
            ),
        )
        .order_by(
            PlanningArtifactVersionModel.version_no.desc(),
            PlanningArtifactVersionModel.created_at.desc(),
        )
        .limit(1)
    )
    if artifact is None or not isinstance(artifact.content, Mapping):
        return None
    try:
        _validate_story_engine_artifact_content(artifact.content)
    except ValueError:
        return None
    meta = artifact.content.get("_meta")
    if not isinstance(meta, Mapping):
        return None
    if meta.get("source_snapshot_hash") != source_snapshot_hash:
        return None
    engine_payload = artifact.content.get("engine")
    if not isinstance(engine_payload, Mapping):
        return None
    if meta.get("engine_hash") != canonical_json_hash(engine_payload):
        return None
    try:
        engine = StoryEngineDefinition.from_mapping(engine_payload)
        validate_engine_definition(engine)
    except (TypeError, ValueError):
        return None
    return artifact


async def persist_legacy_story_engine_shadow(
    session: AsyncSession,
    *,
    project: object,
    kernel: StoryDesignKernel | Mapping[str, Any],
    source_run_id: UUID | None = None,
) -> PlanningArtifactVersionModel:
    """Persist a non-authoritative legacy projection for planner telemetry."""

    project_id = getattr(project, "id", None)
    if not isinstance(project_id, UUID):
        raise ValueError("story engine shadow requires project id")
    metadata = _mapping(getattr(project, "metadata_json", None))
    premium_snapshot = metadata.get("premium_state_snapshot")
    premium_ledger = metadata.get("premium_state_ledger")
    snapshot = premium_snapshot if isinstance(premium_snapshot, Mapping) else None
    ledger = premium_ledger if isinstance(premium_ledger, Mapping) else None
    source_snapshot_hash = str(metadata.get("book_design_snapshot_hash") or "").strip()
    if not source_snapshot_hash:
        source_snapshot_hash = canonical_json_hash(
            {"premium_state_snapshot": snapshot, "premium_state_ledger": ledger}
        )
    raw_kernel = (
        story_design_kernel_to_dict(kernel)
        if isinstance(kernel, StoryDesignKernel)
        else dict(kernel)
    )
    legacy_kernel_hash = canonical_json_hash(raw_kernel)
    projection = project_legacy_story_engine(
        engine_id=f"{project_id}:story-engine-v2",
        kernel=kernel,
        premium_state_snapshot=snapshot,
        premium_state_ledger=ledger,
    )
    content = build_story_engine_artifact_content(
        projection,
        source_snapshot_hash=source_snapshot_hash,
        legacy_kernel_hash=legacy_kernel_hash,
    )
    idempotency_key = canonical_json_hash(
        {
            "project_id": str(project_id),
            "source_snapshot_hash": source_snapshot_hash,
            "legacy_kernel_hash": legacy_kernel_hash,
        }
    )
    return await create_story_engine_artifact(
        session,
        project_id=project_id,
        content=content,
        idempotency_key=idempotency_key,
        source_run_id=source_run_id,
    )


__all__ = [
    "STORY_ENGINE_ARTIFACT_TYPE",
    "STORY_ENGINE_WINDOW_ARTIFACT_TYPE",
    "LegacyProjectionStatus",
    "LegacyStoryEngineProjection",
    "StoryEngineRolloutDecision",
    "StoryEngineVerifiedRolloutEvidence",
    "apply_story_engine_projection_to_chapter",
    "build_story_engine_artifact_content",
    "build_story_engine_window_artifact_content",
    "chapter_creative_core_from_window_content",
    "chapter_observation_core_from_window_content",
    "create_story_engine_artifact",
    "create_story_engine_window_artifact",
    "persist_legacy_story_engine_shadow",
    "project_legacy_story_engine",
    "render_story_engine_creative_core_block",
    "resolve_latest_story_engine_artifact",
    "resolve_latest_story_engine_window_artifact",
    "resolve_story_engine_mode",
    "resolve_story_engine_rollout_decision",
    "resolve_story_engine_rollout_decision_from_db",
]
