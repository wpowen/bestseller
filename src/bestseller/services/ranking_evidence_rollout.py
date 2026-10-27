"""Fail-closed rollout policy for canonical ranking evidence."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from bestseller.settings import PipelineSettings


class RankingEvidenceMode(StrEnum):
    OFF = "off"
    SHADOW = "shadow"
    ADVISORY = "advisory"
    CANARY = "canary"
    REQUIRED = "required"


@dataclass(frozen=True, slots=True)
class RankingEvidenceRolloutDecision:
    mode: RankingEvidenceMode
    collect_evidence: bool
    show_blockers: bool
    block_canonical_promotion: bool
    require_commercial_claim: bool = True
    reason: str = ""


def resolve_ranking_evidence_rollout(
    settings: PipelineSettings,
    *,
    project_id: str = "",
    genre: str = "",
) -> RankingEvidenceRolloutDecision:
    """Resolve rollout scope without ever weakening the commercial boundary."""

    mode = RankingEvidenceMode(settings.ranking_evidence_mode)
    canary_match = bool(
        (project_id and project_id in set(settings.ranking_evidence_canary_project_ids))
        or (genre and genre in set(settings.ranking_evidence_canary_genres))
    )
    collect = mode is not RankingEvidenceMode.OFF
    show = mode in {
        RankingEvidenceMode.ADVISORY,
        RankingEvidenceMode.CANARY,
        RankingEvidenceMode.REQUIRED,
    }
    block = mode is RankingEvidenceMode.REQUIRED or (
        mode is RankingEvidenceMode.CANARY and canary_match
    )
    reason = {
        RankingEvidenceMode.OFF: "legacy_diagnostics_only",
        RankingEvidenceMode.SHADOW: "collect_without_promotion_block",
        RankingEvidenceMode.ADVISORY: "show_blockers_without_promotion_block",
        RankingEvidenceMode.CANARY: (
            "canary_scope_matched" if canary_match else "outside_canary_scope"
        ),
        RankingEvidenceMode.REQUIRED: "canonical_evidence_required",
    }[mode]
    return RankingEvidenceRolloutDecision(
        mode=mode,
        collect_evidence=collect,
        show_blockers=show,
        block_canonical_promotion=block,
        # M0 is invariant: no mode, including off, authorizes a commercial
        # side effect from a legacy score or a text-only diagnostic.
        require_commercial_claim=True,
        reason=reason,
    )


__all__ = [
    "RankingEvidenceMode",
    "RankingEvidenceRolloutDecision",
    "resolve_ranking_evidence_rollout",
]
