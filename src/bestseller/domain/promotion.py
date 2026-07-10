"""Pure domain contract for quality-gated draft promotion."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from bestseller.domain.enums import DraftPromotionState


class PromotionValidationError(ValueError):
    """Raised when a promotion transition lacks evidence or audit context."""


@dataclass(frozen=True)
class PromotionEvidence:
    draft_id: UUID
    score_draft_id: UUID | None
    score_overall: float
    core_scores: tuple[float, ...]
    hard_gates_passed: bool
    blocking_codes: tuple[str, ...]


@dataclass(frozen=True)
class PromotionCandidate:
    draft_id: UUID
    version_no: int
    score_overall: float
    core_scores: tuple[float, ...]


def is_promotion_eligible(
    evidence: PromotionEvidence,
    *,
    min_overall: float,
    min_core: float,
) -> bool:
    """Require an exact-version score, every hard gate, and no blockers."""
    if evidence.score_draft_id != evidence.draft_id:
        return False
    if not evidence.hard_gates_passed or evidence.blocking_codes:
        return False
    if evidence.score_overall < min_overall or not evidence.core_scores:
        return False
    return min(evidence.core_scores) >= min_core


def promotion_rank_key(candidate: PromotionCandidate) -> tuple[float, float, int]:
    """Rank by overall, then weakest core dimension, then earlier version."""
    min_core = min(candidate.core_scores) if candidate.core_scores else float("-inf")
    return candidate.score_overall, min_core, -candidate.version_no


_AUTOMATED_TRANSITIONS: dict[DraftPromotionState, frozenset[DraftPromotionState]] = {
    DraftPromotionState.LEGACY_UNVERIFIED: frozenset(
        {DraftPromotionState.UNDER_REVIEW, DraftPromotionState.QUARANTINED}
    ),
    DraftPromotionState.CANDIDATE: frozenset(
        {
            DraftPromotionState.UNDER_REVIEW,
            DraftPromotionState.REJECTED,
            DraftPromotionState.QUARANTINED,
        }
    ),
    DraftPromotionState.UNDER_REVIEW: frozenset(
        {
            DraftPromotionState.ELIGIBLE,
            DraftPromotionState.REJECTED,
            DraftPromotionState.QUARANTINED,
        }
    ),
    DraftPromotionState.ELIGIBLE: frozenset(
        {
            DraftPromotionState.PROMOTED,
            DraftPromotionState.REJECTED,
            DraftPromotionState.QUARANTINED,
        }
    ),
    DraftPromotionState.PROMOTED: frozenset(),
    DraftPromotionState.SUPERSEDED: frozenset(),
    DraftPromotionState.REJECTED: frozenset(),
    DraftPromotionState.QUARANTINED: frozenset(),
}


def validate_promotion_transition(
    from_state: DraftPromotionState | str,
    to_state: DraftPromotionState | str,
    *,
    decision_source: str,
    actor: str | None = None,
    reason: str | None = None,
    evidence: dict[str, object] | None = None,
) -> None:
    """Validate state movement and require complete human-override audit data."""
    source_state = DraftPromotionState(from_state)
    target_state = DraftPromotionState(to_state)
    if decision_source == "human_override":
        if (
            not actor
            or not actor.strip()
            or not reason
            or not reason.strip()
            or not evidence
        ):
            raise PromotionValidationError(
                "human_override requires non-empty actor, reason, and evidence"
            )
        if source_state not in {
            DraftPromotionState.REJECTED,
            DraftPromotionState.QUARANTINED,
        } or target_state is not DraftPromotionState.UNDER_REVIEW:
            raise PromotionValidationError(
                "human_override may only reopen rejected/quarantined drafts to under_review"
            )
        return
    if decision_source == "replacement":
        if (
            source_state is not DraftPromotionState.PROMOTED
            or target_state is not DraftPromotionState.SUPERSEDED
            or not evidence
        ):
            raise PromotionValidationError(
                "replacement may only supersede a promoted incumbent with evidence"
            )
        return
    if target_state not in _AUTOMATED_TRANSITIONS[source_state]:
        raise PromotionValidationError(
            f"invalid automated promotion transition: {source_state.value} -> "
            f"{target_state.value}"
        )
