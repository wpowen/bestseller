"""Pure, immutable contracts for ranking evidence.

This module intentionally has no persistence or provider imports.  It is the
smallest trusted boundary between evidence collection and release authority.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum, StrEnum
import hashlib
import json
import math
from typing import Any
import unicodedata

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class EvidenceLayer(StrEnum):
    STRUCTURE = "structure"
    LIVE_PRODUCTION = "live_production"
    TEXT_PARITY = "text_parity"
    READER = "reader"
    SERIAL_DEPTH = "serial_depth"
    PLATFORM_COHORT = "platform_cohort"
    RANK_RECEIPT = "rank_receipt"


class EvidenceSourceKind(StrEnum):
    FIXTURE = "fixture"
    SYNTHETIC = "synthetic"
    SHADOW_REFERENCE = "shadow_reference"
    LIVE_VERIFIED = "live_verified"
    OFFICIAL_RECEIPT = "official_receipt"


class Maturity(StrEnum):
    UNASSESSED = "UNASSESSED"
    STRUCTURE_VALIDATED = "STRUCTURE_VALIDATED"
    PRODUCTION_VALIDATED = "PRODUCTION_VALIDATED"
    TEXT_PARITY_VALIDATED = "TEXT_PARITY_VALIDATED"
    READER_VALIDATED = "READER_VALIDATED"
    SERIAL_DEPTH_VALIDATED = "SERIAL_DEPTH_VALIDATED"
    RANKING_COMPETITIVE = "RANKING_COMPETITIVE"
    OBSERVED_RANKED = "OBSERVED_RANKED"


class EvidenceOutcome(StrEnum):
    PASS = "PASS"  # noqa: S105 - evidence verdict, not a credential
    FAIL = "FAIL"
    INCONCLUSIVE = "INCONCLUSIVE"
    EXPIRED = "EXPIRED"
    MISSING = "MISSING"


class AllowedClaim(StrEnum):
    NOT_ASSESSED = "NOT_ASSESSED"
    STRUCTURE_PRODUCTION_READY = "STRUCTURE_PRODUCTION_READY"
    PRODUCTION_VALIDATED = "PRODUCTION_VALIDATED"
    TEXT_PARITY_VALIDATED = "TEXT_PARITY_VALIDATED"
    READER_VALIDATED = "READER_VALIDATED"
    SERIAL_DEPTH_VALIDATED = "SERIAL_DEPTH_VALIDATED"
    RANKING_COMPETITIVE = "RANKING_COMPETITIVE"
    OBSERVED_RANKED = "OBSERVED_RANKED"
    COMMERCIAL_RELEASE = "COMMERCIAL_RELEASE"


def _normalise(value: Any) -> Any:  # noqa: ANN401
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("NaN and Infinity are not permitted")
        return format(value, "f")
    if isinstance(value, datetime):
        return canonical_timestamp(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("NaN and Infinity are not permitted")
        return value
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if isinstance(value, Mapping):
        return {str(k): _normalise(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalise(v) for v in value]
    if isinstance(value, (set, frozenset)):
        return sorted(
            (_normalise(v) for v in value),
            key=lambda v: json.dumps(v, ensure_ascii=False, sort_keys=True),
        )
    if isinstance(value, (int, bool)) or value is None:
        return value
    if hasattr(value, "model_dump"):
        return _normalise(value.model_dump(mode="python"))
    return unicodedata.normalize("NFC", str(value))


def canonical_timestamp(value: datetime | str) -> str:
    """Return strict UTC RFC3339 with second precision and ``Z`` suffix."""
    if isinstance(value, str):
        raw = value.strip()
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        try:
            value = datetime.fromisoformat(raw)
        except ValueError as exc:
            raise ValueError("timestamp must be RFC3339 with timezone") from exc
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must include timezone")
    value = value.astimezone(UTC)
    if value.microsecond:
        return value.isoformat(timespec="microseconds").replace("+00:00", "Z")
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")


def canonical_json(value: Any) -> str:  # noqa: ANN401
    """Canonical JSON v1: NFC, sorted keys, compact UTF-8 JSON semantics."""
    return json.dumps(
        _normalise(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def canonical_json_hash(value: Any) -> str:  # noqa: ANN401
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


class FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class EvidenceArtifactRef(FrozenModel):
    artifact_uri: str
    content_hash: str
    layer: EvidenceLayer
    source_kind: EvidenceSourceKind
    producer: str = ""
    sample_count: int = Field(default=0, ge=0)
    cluster_count: int = Field(default=0, ge=0)
    verdict: EvidenceOutcome = EvidenceOutcome.MISSING
    blocking_codes: tuple[str, ...] = ()
    unknowns: tuple[str, ...] = ()
    candidate_revision_hash: str = ""


class RankingExperimentPreregistration(FrozenModel):
    run_id: str
    candidate_revision_hash: str
    analysis_plan_hash: str
    required_cell_manifest_hash: str
    sealed_roster_hash: str
    target_platform: str = ""
    target_list: str = ""
    alpha: float = Field(default=0.05, gt=0, lt=1)
    minimum_effect: float = Field(default=0.10, ge=0)
    frozen_at: str

    @field_validator("frozen_at", mode="before")
    @classmethod
    def _timestamp(cls, value: object) -> str:
        return canonical_timestamp(value if isinstance(value, (datetime, str)) else str(value))


class RequiredCellManifest(FrozenModel):
    cells: tuple[str, ...]
    manifest_version: str = "required-cell-manifest.v1"
    sealed: bool = False
    content_hash: str = ""

    @field_validator("cells", mode="before")
    @classmethod
    def _cells(cls, value: object) -> tuple[str, ...]:
        if not isinstance(value, Iterable) or isinstance(value, (str, bytes)):
            raise ValueError("required cell manifest must be an iterable of cells")
        cells = tuple(sorted({str(v).strip() for v in value if str(v).strip()}))
        if not cells:
            raise ValueError("required cell manifest cannot be empty")
        return cells

    def stable_hash(self) -> str:
        return canonical_json_hash({"manifest_version": self.manifest_version, "cells": self.cells})


class SealedExperimentRoster(FrozenModel):
    member_ids: tuple[str, ...]
    roster_version: str = "sealed-experiment-roster.v1"
    sealed: bool = False
    content_hash: str = ""

    @field_validator("member_ids", mode="before")
    @classmethod
    def _members(cls, value: object) -> tuple[str, ...]:
        if not isinstance(value, Iterable) or isinstance(value, (str, bytes)):
            raise ValueError("sealed roster must be an iterable of member ids")
        members = tuple(sorted({str(v).strip() for v in value if str(v).strip()}))
        if not members:
            raise ValueError("sealed roster cannot be empty")
        return members

    def stable_hash(self) -> str:
        return canonical_json_hash(
            {"roster_version": self.roster_version, "member_ids": self.member_ids}
        )


class RankingEvidenceBundle(FrozenModel):
    run_id: str
    candidate_revision_hash: str
    preregistration: RankingExperimentPreregistration | None = None
    required_cell_manifest: RequiredCellManifest | None = None
    sealed_roster: SealedExperimentRoster | None = None
    artifacts: tuple[EvidenceArtifactRef, ...] = ()
    integrity_violation: bool = False
    integrity_codes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _same_candidate(self) -> RankingEvidenceBundle:
        for artifact in self.artifacts:
            if (
                artifact.candidate_revision_hash
                and artifact.candidate_revision_hash != self.candidate_revision_hash
            ):
                raise ValueError("candidate revision hashes do not match")
        return self

    def artifacts_for(self, layer: EvidenceLayer) -> tuple[EvidenceArtifactRef, ...]:
        return tuple(a for a in self.artifacts if a.layer == layer)


class CanonicalBookReadinessDecision(FrozenModel):
    candidate_revision_hash: str
    highest_contiguous_maturity: Maturity = Maturity.UNASSESSED
    evaluation_status: str = "UNASSESSED"
    claims_allowed: tuple[AllowedClaim, ...] = ()
    report_hash: str = ""
    ledger_root: str = ""
    blocking_codes: tuple[str, ...] = ()
    unknowns: tuple[str, ...] = ()


_ORDER = (
    EvidenceLayer.STRUCTURE,
    EvidenceLayer.LIVE_PRODUCTION,
    EvidenceLayer.TEXT_PARITY,
    EvidenceLayer.READER,
    EvidenceLayer.SERIAL_DEPTH,
    EvidenceLayer.PLATFORM_COHORT,
)
_MATURITY = (
    Maturity.STRUCTURE_VALIDATED,
    Maturity.PRODUCTION_VALIDATED,
    Maturity.TEXT_PARITY_VALIDATED,
    Maturity.READER_VALIDATED,
    Maturity.SERIAL_DEPTH_VALIDATED,
    Maturity.RANKING_COMPETITIVE,
)
_CLAIMS = (
    AllowedClaim.STRUCTURE_PRODUCTION_READY,
    AllowedClaim.PRODUCTION_VALIDATED,
    AllowedClaim.TEXT_PARITY_VALIDATED,
    AllowedClaim.READER_VALIDATED,
    AllowedClaim.SERIAL_DEPTH_VALIDATED,
    AllowedClaim.RANKING_COMPETITIVE,
)


def reduce_maturity(bundle: RankingEvidenceBundle) -> CanonicalBookReadinessDecision:
    if bundle.integrity_violation:
        return CanonicalBookReadinessDecision(
            candidate_revision_hash=bundle.candidate_revision_hash,
            evaluation_status="BLOCKED_EVIDENCE_INTEGRITY",
            blocking_codes=bundle.integrity_codes,
        )
    highest = Maturity.UNASSESSED
    claims: list[AllowedClaim] = []
    for index, layer in enumerate(_ORDER):
        cells = bundle.artifacts_for(layer)
        if not cells:
            return CanonicalBookReadinessDecision(
                candidate_revision_hash=bundle.candidate_revision_hash,
                highest_contiguous_maturity=highest,
                evaluation_status=f"INCONCLUSIVE_{layer.value.upper()}",
                claims_allowed=tuple(claims),
            )
        if layer != EvidenceLayer.STRUCTURE and (
            bundle.preregistration is None
            or bundle.required_cell_manifest is None
            or not bundle.required_cell_manifest.sealed
            or bundle.sealed_roster is None
            or not bundle.sealed_roster.sealed
        ):
            return CanonicalBookReadinessDecision(
                candidate_revision_hash=bundle.candidate_revision_hash,
                highest_contiguous_maturity=highest,
                evaluation_status="INCONCLUSIVE_PREREGISTRATION_NOT_SEALED",
                claims_allowed=tuple(claims),
                unknowns=("preregistration_or_roster_missing",),
            )
        invalid_sources = tuple(
            artifact.source_kind.value
            for artifact in cells
            if layer != EvidenceLayer.STRUCTURE
            and artifact.source_kind
            not in {EvidenceSourceKind.LIVE_VERIFIED, EvidenceSourceKind.OFFICIAL_RECEIPT}
        )
        if invalid_sources:
            return CanonicalBookReadinessDecision(
                candidate_revision_hash=bundle.candidate_revision_hash,
                highest_contiguous_maturity=highest,
                evaluation_status=f"INCONCLUSIVE_{layer.value.upper()}_LIVE_SOURCE",
                claims_allowed=tuple(claims),
                unknowns=tuple(dict.fromkeys(invalid_sources)),
            )
        outcomes = {artifact.verdict for artifact in cells}
        if EvidenceOutcome.FAIL in outcomes:
            code = next(
                (code for artifact in cells for code in artifact.blocking_codes),
                layer.value.upper(),
            )
            return CanonicalBookReadinessDecision(
                candidate_revision_hash=bundle.candidate_revision_hash,
                highest_contiguous_maturity=highest,
                evaluation_status=f"BLOCKED_{code}",
                claims_allowed=tuple(claims),
                blocking_codes=(code,),
            )
        if EvidenceOutcome.EXPIRED in outcomes:
            return CanonicalBookReadinessDecision(
                candidate_revision_hash=bundle.candidate_revision_hash,
                highest_contiguous_maturity=highest,
                evaluation_status=f"EXPIRED_{layer.value.upper()}",
                claims_allowed=tuple(claims),
            )
        if any(outcome != EvidenceOutcome.PASS for outcome in outcomes):
            return CanonicalBookReadinessDecision(
                candidate_revision_hash=bundle.candidate_revision_hash,
                highest_contiguous_maturity=highest,
                evaluation_status=f"INCONCLUSIVE_{layer.value.upper()}",
                claims_allowed=tuple(claims),
            )
        highest = _MATURITY[index]
        claims.append(_CLAIMS[index])
    rank = bundle.artifacts_for(EvidenceLayer.RANK_RECEIPT)
    if rank and all(
        a.verdict == EvidenceOutcome.PASS and a.source_kind == EvidenceSourceKind.OFFICIAL_RECEIPT
        for a in rank
    ):
        return CanonicalBookReadinessDecision(
            candidate_revision_hash=bundle.candidate_revision_hash,
            highest_contiguous_maturity=Maturity.OBSERVED_RANKED,
            evaluation_status="RANK_TARGET_OBSERVED",
            claims_allowed=(
                *claims,
                AllowedClaim.COMMERCIAL_RELEASE,
                AllowedClaim.OBSERVED_RANKED,
            ),
        )
    return CanonicalBookReadinessDecision(
        candidate_revision_hash=bundle.candidate_revision_hash,
        highest_contiguous_maturity=highest,
        evaluation_status="AWAITING_RANK_RECEIPT",
        claims_allowed=(*claims, AllowedClaim.COMMERCIAL_RELEASE),
    )


def invalidate_evidence(changed: str) -> frozenset[EvidenceLayer]:
    key = changed.strip().lower()
    if key in {
        "tags",
        "genre",
        "effect",
        "architecture",
        "chapter",
        "prose",
        "writer_model",
        "writer_prompt",
        "config",
    }:
        return frozenset(_ORDER)
    if key in {"title", "intro", "cover", "packaging"}:
        return frozenset(
            {EvidenceLayer.READER, EvidenceLayer.PLATFORM_COHORT, EvidenceLayer.RANK_RECEIPT}
        )
    if key in {"judge_model", "judge_prompt", "rubric", "benchmark", "dataset"}:
        return frozenset(
            {
                EvidenceLayer.TEXT_PARITY,
                EvidenceLayer.READER,
                EvidenceLayer.PLATFORM_COHORT,
                EvidenceLayer.RANK_RECEIPT,
            }
        )
    if key in {"reader_protocol", "reader_sample"}:
        return frozenset(
            {EvidenceLayer.READER, EvidenceLayer.PLATFORM_COHORT, EvidenceLayer.RANK_RECEIPT}
        )
    if key in {"platform", "list", "traffic", "time_window", "threshold"}:
        return frozenset({EvidenceLayer.PLATFORM_COHORT, EvidenceLayer.RANK_RECEIPT})
    return frozenset()


__all__ = [
    "AllowedClaim",
    "CanonicalBookReadinessDecision",
    "EvidenceArtifactRef",
    "EvidenceLayer",
    "EvidenceOutcome",
    "EvidenceSourceKind",
    "Maturity",
    "RankingEvidenceBundle",
    "RankingExperimentPreregistration",
    "RequiredCellManifest",
    "SealedExperimentRoster",
    "canonical_json",
    "canonical_json_hash",
    "canonical_timestamp",
    "invalidate_evidence",
    "reduce_maturity",
]
