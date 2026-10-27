"""Formal, sealed Benchmark Arena experiment helpers.

This module is a stable import surface for orchestration code.  The statistical
implementation lives beside the legacy Arena API so existing diagnostic jobs
continue to work without being mistaken for formal evidence.
"""

from __future__ import annotations

from collections.abc import Iterable
import copy
from dataclasses import dataclass
import hashlib
import json

from bestseller.services.benchmark_arena import (
    ArenaRequiredCell,
    FormalArenaCellResult,
    FormalArenaPair,
    arena_point_estimate,
    evaluate_formal_arena_cell,
    evaluate_required_arena_cells,
    hierarchical_bootstrap_ci,
    validate_required_cell_roster,
)

DEFAULT_ARENA_GENRES = (
    "异世界经营喜剧",
    "都市悬疑",
    "古言权谋",
    "科幻生存",
)
DEFAULT_ARENA_STAGES = (
    "opening_ch1",
    "opening_ch2_3",
    "early_serial_ch8_10",
    "mid_serial_ch28_30",
)
DEFAULT_ARENA_TIERS = ("T1", "T2")


def canonical_json(value: object) -> str:
    """canonical-json-v1 used for experiment seals."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def content_hash(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class SealedExperimentManifest:
    """Snapshot whose hash is checked before it can be consumed as evidence."""

    payload: object
    manifest_hash: str

    @classmethod
    def seal(cls, payload: object) -> SealedExperimentManifest:
        snapshot = copy.deepcopy(payload)
        return cls(snapshot, content_hash(snapshot))

    def verify(self) -> None:
        actual = content_hash(self.payload)
        if actual != self.manifest_hash:
            raise ValueError("SEALED_ROSTER_MISMATCH")


def seal_required_cell_manifest(cells: Iterable[ArenaRequiredCell]) -> SealedExperimentManifest:
    payload = [
        {"genre": c.genre, "narrative_stage": c.narrative_stage, "tier": c.tier} for c in cells
    ]
    payload.sort(key=lambda item: (item["genre"], item["narrative_stage"], item["tier"]))
    return SealedExperimentManifest.seal(payload)


def seal_experiment_roster(roster: object) -> SealedExperimentManifest:
    return SealedExperimentManifest.seal(roster)


def build_required_cell_manifest(
    *,
    genres: Iterable[str] = DEFAULT_ARENA_GENRES,
    narrative_stages: Iterable[str] = DEFAULT_ARENA_STAGES,
    tiers: Iterable[str] = DEFAULT_ARENA_TIERS,
) -> frozenset[ArenaRequiredCell]:
    """Build the complete sealed Arena universe; no cross-cell fallback."""
    return frozenset(
        ArenaRequiredCell(str(genre), str(stage), str(tier))
        for genre in genres
        for stage in narrative_stages
        for tier in tiers
    )


def validate_sealed_roster(cell: ArenaRequiredCell, roster: dict[str, object]) -> tuple[str, ...]:
    """Alias used by experiment runners and evidence validators."""
    return validate_required_cell_roster(cell, roster)


__all__ = [
    "DEFAULT_ARENA_GENRES",
    "DEFAULT_ARENA_STAGES",
    "DEFAULT_ARENA_TIERS",
    "ArenaRequiredCell",
    "FormalArenaCellResult",
    "FormalArenaPair",
    "SealedExperimentManifest",
    "arena_point_estimate",
    "build_required_cell_manifest",
    "canonical_json",
    "content_hash",
    "evaluate_formal_arena_cell",
    "evaluate_required_arena_cells",
    "hierarchical_bootstrap_ci",
    "seal_experiment_roster",
    "seal_required_cell_manifest",
    "validate_required_cell_roster",
    "validate_sealed_roster",
]
