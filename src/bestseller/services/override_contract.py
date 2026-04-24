"""Phase C2 — Override Contract.

An Override Contract is a signed, typed waiver that lets a soft
constraint violation (``LINE_GAP_OVER``, ``PLEASURE_SETUP_PAYOFF_DEBT``,
genre-specific pacing rules) pass the write gate for a specific chapter
provided the author commits to a payback plan by a due chapter.

The contract is persisted in ``OverrideContractModel`` (migration
0025) and consumed by ``write_gate.resolve_mode`` via the
``override_lookup`` callback. Each contract spawns a sibling
``ChaseDebtModel`` row (Phase C3) that accrues interest until the debt
is closed.

This module deliberately ships **without** SQLAlchemy imports — the
core types (``RationaleType``, ``OverrideStatus``,
``OverrideContract``) are pure dataclasses so the service layer stays
testable in isolation. Call sites that need DB I/O import the
``OverrideContractModel`` separately and hand rows off to
``from_row`` / ``to_row_kwargs``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Iterable, Mapping


class RationaleType(str, Enum):
    """Why a soft constraint may be waived for a specific chapter.

    Each genre's ``override_config.allowed_rationale_types`` is a subset
    of this enum; ``write_gate`` rejects override creation when the cited
    rationale isn't in the genre's whitelist.
    """

    TRANSITIONAL_SETUP = "TRANSITIONAL_SETUP"
    LOGIC_INTEGRITY = "LOGIC_INTEGRITY"
    CHARACTER_CREDIBILITY = "CHARACTER_CREDIBILITY"
    WORLD_RULE_CONSTRAINT = "WORLD_RULE_CONSTRAINT"
    ARC_TIMING = "ARC_TIMING"
    GENRE_CONVENTION = "GENRE_CONVENTION"
    EDITORIAL_INTENT = "EDITORIAL_INTENT"


class OverrideStatus(str, Enum):
    ACTIVE = "active"
    RESOLVED = "resolved"
    EXPIRED = "expired"


@dataclass(frozen=True)
class OverrideContract:
    """In-memory representation of a signed override."""

    id: int | None
    project_id: str
    chapter_no: int
    violation_code: str
    rationale_type: RationaleType
    rationale_text: str
    payback_plan: str
    due_chapter: int
    status: OverrideStatus = OverrideStatus.ACTIVE
    created_at: datetime = field(default_factory=lambda: datetime.now(tz=timezone.utc))

    @property
    def is_active(self) -> bool:
        return self.status == OverrideStatus.ACTIVE

    @property
    def is_resolved(self) -> bool:
        return self.status == OverrideStatus.RESOLVED

    def is_overdue(self, current_chapter: int) -> bool:
        """True when the due chapter has passed and the override is still active."""

        return self.is_active and current_chapter > self.due_chapter

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "project_id": self.project_id,
            "chapter_no": self.chapter_no,
            "violation_code": self.violation_code,
            "rationale_type": self.rationale_type.value,
            "rationale_text": self.rationale_text,
            "payback_plan": self.payback_plan,
            "due_chapter": self.due_chapter,
            "status": self.status.value,
            "created_at": self.created_at.isoformat(),
        }


# ---------------------------------------------------------------------------
# Validation — at contract creation time.
# ---------------------------------------------------------------------------


class OverrideRejected(ValueError):
    """Raised when a proposed override fails validation.

    Callers catch this, surface the reason to the author, and give them
    a chance to either cite a different rationale or let the regen loop
    keep trying.
    """


def _normalise_rationale(rationale: Any) -> RationaleType:
    """Coerce a string / enum to ``RationaleType`` or raise."""

    if isinstance(rationale, RationaleType):
        return rationale
    if isinstance(rationale, str):
        try:
            return RationaleType(rationale.upper())
        except ValueError as exc:
            raise OverrideRejected(
                f"unknown rationale type: {rationale!r}"
            ) from exc
    raise OverrideRejected(f"rationale must be RationaleType or str, got {type(rationale)}")


def validate_override_proposal(
    *,
    violation_code: str,
    chapter_no: int,
    due_chapter: int,
    rationale: Any,
    rationale_text: str,
    payback_plan: str,
    soft_constraint_codes: Iterable[str],
    allowed_rationale_types: Iterable[str],
    payback_window: int | None = None,
) -> RationaleType:
    """Validate a proposed override before persisting.

    Raises ``OverrideRejected`` when:
      * the violation code is *hard* (not in ``soft_constraint_codes``);
      * the rationale type isn't in the genre's whitelist;
      * the rationale text or payback plan is empty;
      * ``due_chapter`` is not strictly greater than ``chapter_no``;
      * ``due_chapter - chapter_no`` exceeds the genre's
        ``payback_window_default`` (when supplied).

    Returns the normalised ``RationaleType`` enum on success.
    """

    if violation_code not in set(soft_constraint_codes):
        raise OverrideRejected(
            f"violation {violation_code} is hard; cannot be overridden"
        )
    rt = _normalise_rationale(rationale)
    allowed = {v.upper() for v in allowed_rationale_types}
    if rt.value not in allowed:
        raise OverrideRejected(
            f"rationale {rt.value} not in genre whitelist: {sorted(allowed)}"
        )
    if not rationale_text or not rationale_text.strip():
        raise OverrideRejected("rationale_text may not be empty")
    if not payback_plan or not payback_plan.strip():
        raise OverrideRejected("payback_plan may not be empty")
    if due_chapter <= chapter_no:
        raise OverrideRejected(
            f"due_chapter ({due_chapter}) must be > chapter_no ({chapter_no})"
        )
    if payback_window is not None:
        span = due_chapter - chapter_no
        if span > payback_window:
            raise OverrideRejected(
                f"payback window {span} exceeds genre budget {payback_window}"
            )
    return rt


# ---------------------------------------------------------------------------
# In-memory service (used by call sites that hold a session). DB I/O is
# left to the caller so we can unit-test the logic without a fixture.
# ---------------------------------------------------------------------------


@dataclass
class OverrideStore:
    """Minimal in-memory contract store.

    Exposes the same surface as the eventual DB-backed service
    (``create`` / ``list_active`` / ``list_overdue`` / ``resolve``) so
    callers can swap a real store in without touching their code.
    Primarily used for unit tests and for the write_gate
    ``override_lookup`` callback in offline scenarios.
    """

    _next_id: int = 1
    _rows: list[OverrideContract] = field(default_factory=list)

    def create(self, contract: OverrideContract) -> OverrideContract:
        row = OverrideContract(
            id=contract.id or self._next_id,
            project_id=contract.project_id,
            chapter_no=contract.chapter_no,
            violation_code=contract.violation_code,
            rationale_type=contract.rationale_type,
            rationale_text=contract.rationale_text,
            payback_plan=contract.payback_plan,
            due_chapter=contract.due_chapter,
            status=contract.status,
            created_at=contract.created_at,
        )
        self._rows.append(row)
        if contract.id is None:
            self._next_id += 1
        return row

    def list_active(self, project_id: str) -> tuple[OverrideContract, ...]:
        return tuple(
            r
            for r in self._rows
            if r.project_id == project_id and r.is_active
        )

    def list_overdue(
        self,
        project_id: str,
        current_chapter: int,
    ) -> tuple[OverrideContract, ...]:
        return tuple(
            r
            for r in self._rows
            if r.project_id == project_id and r.is_overdue(current_chapter)
        )

    def resolve(self, override_id: int) -> OverrideContract | None:
        for idx, r in enumerate(self._rows):
            if r.id == override_id and r.is_active:
                resolved = OverrideContract(
                    id=r.id,
                    project_id=r.project_id,
                    chapter_no=r.chapter_no,
                    violation_code=r.violation_code,
                    rationale_type=r.rationale_type,
                    rationale_text=r.rationale_text,
                    payback_plan=r.payback_plan,
                    due_chapter=r.due_chapter,
                    status=OverrideStatus.RESOLVED,
                    created_at=r.created_at,
                )
                self._rows[idx] = resolved
                return resolved
        return None

    def expire_overdue(self, project_id: str, current_chapter: int) -> int:
        """Flip overdue contracts to ``EXPIRED`` status.

        Returns the number of rows flipped. Typically called once per
        chapter by the pipeline so the debt ledger can surface overdue
        debts in the next scorecard.
        """

        flipped = 0
        for idx, r in enumerate(self._rows):
            if r.project_id != project_id or not r.is_overdue(current_chapter):
                continue
            self._rows[idx] = OverrideContract(
                id=r.id,
                project_id=r.project_id,
                chapter_no=r.chapter_no,
                violation_code=r.violation_code,
                rationale_type=r.rationale_type,
                rationale_text=r.rationale_text,
                payback_plan=r.payback_plan,
                due_chapter=r.due_chapter,
                status=OverrideStatus.EXPIRED,
                created_at=r.created_at,
            )
            flipped += 1
        return flipped

    def as_lookup(self, project_id: str) -> "OverrideLookup":
        """Return a ``write_gate.OverrideLookup``-shaped callback.

        The returned closure answers ``True`` when an *active* contract
        exists for ``(project_id, code)`` covering ``chapter_no``; the
        gate uses this to downgrade ``block → audit_only``.
        """

        def _lookup(code: str, chapter_no: int | None) -> bool:
            if chapter_no is None:
                return False
            for r in self._rows:
                if (
                    r.project_id == project_id
                    and r.is_active
                    and r.violation_code == code
                    and r.chapter_no <= chapter_no <= r.due_chapter
                ):
                    return True
            return False

        return _lookup


# Re-export the callback type so call sites can type their variables.
from bestseller.services.write_gate import OverrideLookup  # noqa: E402
