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



# =============================================================================
# DB-persistence shim (Phase C+, P0-5)
# =============================================================================
#
# ``OverrideStore`` keeps rows in module-level in-memory ``_rows: list``.
# That is fine while ``quality_gates.yaml:phase_c_overrides.enabled`` stays
# False (the production default), but enabling Phase C in a multi-worker
# deployment would lose state across workers and across restarts.
#
# ``OverrideContractModel`` (``infra/db/models.py``) already defines a
# backing table — the gap is that no code path writes to it.  The two
# helpers below are a stepping stone:
#   * ``persist_to_metadata_json`` snapshots the in-memory store into the
#     project's ``metadata_json["override_contracts"]`` JSONB column.
#   * ``load_from_metadata_json`` rebuilds an ``OverrideStore`` from the
#     same column at worker startup.
#
# Full migration (Phase D-B) will replace these with direct ORM writes.
# For now they are explicit shims so the migration is one PR.
# =============================================================================


def persist_to_metadata_json(
    store: OverrideStore,
    *,
    project_id: str,
) -> list[dict[str, Any]]:
    """Return a JSON-safe snapshot of ``store`` rows for ``project_id``.

    Callers (e.g. the chapter pipeline's checkpoint phase) write this list
    to ``ProjectModel.metadata_json["override_contracts"]``.  The shape
    is compatible with the future ``OverrideContractModel`` ORM write
    path so callers can switch without touching consumers.
    """
    return [
        {
            "id": row.id,
            "project_id": row.project_id,
            "chapter_no": row.chapter_no,
            "violation_code": row.violation_code,
            "rationale_type": row.rationale_type,
            "rationale_text": row.rationale_text,
            "payback_plan": row.payback_plan,
            "due_chapter": row.due_chapter,
            "status": row.status,
            "created_at": row.created_at,
            "is_active": row.is_active,
        }
        for row in store._rows
        if row.project_id == project_id
    ]


def load_from_metadata_json(
    *,
    project_id: str,
    payload: list[dict[str, Any]] | None,
) -> OverrideStore:
    """Rebuild an :class:`OverrideStore` from a JSONB snapshot.

    Empty / None ``payload`` returns a fresh empty store.  This is the
    companion to :func:`persist_to_metadata_json` and is what the worker
    should call at startup before processing any chapter for ``project_id``.
    """
    store = OverrideStore()
    for entry in payload or []:
        if entry.get("project_id") != project_id:
            continue
        store._rows.append(
            OverrideContract(
                id=str(entry.get("id") or ""),
                project_id=str(entry.get("project_id") or ""),
                chapter_no=int(entry.get("chapter_no") or 0),
                violation_code=str(entry.get("violation_code") or ""),
                rationale_type=str(entry.get("rationale_type") or "EDITORIAL_INTENT"),
                rationale_text=str(entry.get("rationale_text") or ""),
                payback_plan=str(entry.get("payback_plan") or ""),
                due_chapter=int(entry.get("due_chapter") or 0) or None,
                status=str(entry.get("status") or "active"),
                created_at=str(entry.get("created_at") or ""),
                is_active=bool(entry.get("is_active", True)),
            )
        )
    return store



# =============================================================================
# Live DB write helpers (T7 — P0-5 落 DB)
# =============================================================================
#
# These functions write/read ``OverrideContract`` rows to
# ``OverrideContractModel`` (defined in ``infra/db/models.py``).  The pair
# ``save_override_store`` + ``load_override_store`` is the canonical
# way to make ``OverrideStore`` survive across worker processes and
# across restarts.  The metadata_json shim above remains in place for
# callers that prefer project-level snapshots.
# =============================================================================


async def save_override_store(
    session: "AsyncSession",
    *,
    project: "ProjectModel",
    store: OverrideStore,
) -> int:
    """Persist all rows of ``store`` that belong to ``project`` to
    ``OverrideContractModel``.

    Returns the number of rows newly inserted.  Existing rows with the
    same ``(project_id, chapter_no, violation_code, status)`` tuple
    are left alone (idempotent).  The function assumes the caller has
    already validated that ``phase_c_overrides`` is enabled.
    """
    from sqlalchemy import select
    from uuid import UUID as _UUID
    from datetime import datetime, timezone
    from bestseller.infra.db.models import OverrideContractModel

    project_id = getattr(project, "id", None)
    if project_id is None:
        return 0

    inserted = 0
    for row in store._rows:
        if str(row.project_id) != str(project_id):
            continue
        # Idempotency: skip if a row with the same composite key already exists
        existing = await session.scalar(
            select(OverrideContractModel).where(
                OverrideContractModel.project_id == project_id,
                OverrideContractModel.chapter_no == row.chapter_no,
                OverrideContractModel.violation_code == row.violation_code,
                OverrideContractModel.status == (row.status or "active"),
            )
        )
        if existing is not None:
            continue
        session.add(
            OverrideContractModel(
                project_id=project_id,
                chapter_no=int(row.chapter_no or 0),
                violation_code=str(row.violation_code or ""),
                rationale_type=str(row.rationale_type or "EDITORIAL_INTENT"),
                rationale_text=str(row.rationale_text or ""),
                payback_plan=str(row.payback_plan or ""),
                due_chapter=int(row.due_chapter or row.chapter_no + 10),
                status=str(row.status or "active"),
            )
        )
        inserted += 1
    return inserted


async def load_override_store(
    session: "AsyncSession",
    *,
    project: "ProjectModel",
) -> OverrideStore:
    """Build an :class:`OverrideStore` from ``OverrideContractModel`` rows
    belonging to ``project``.
    """
    from sqlalchemy import select
    from bestseller.infra.db.models import OverrideContractModel

    project_id = getattr(project, "id", None)
    if project_id is None:
        return OverrideStore()
    rows = list(
        await session.scalars(
            select(OverrideContractModel).where(
                OverrideContractModel.project_id == project_id,
            )
        )
    )
    store = OverrideStore()
    from datetime import datetime as _dt
    for row in rows:
        # Map DB row.status (string) to OverrideStatus enum
        status_value = str(row.status or "active")
        try:
            status_enum = OverrideStatus(status_value)
        except ValueError:
            status_enum = OverrideStatus.ACTIVE
        store._rows.append(
            OverrideContract(
                id=int(row.id or 0),
                project_id=str(row.project_id),
                chapter_no=int(row.chapter_no or 0),
                violation_code=str(row.violation_code or ""),
                rationale_type=str(row.rationale_type or "EDITORIAL_INTENT"),
                rationale_text=str(row.rationale_text or ""),
                payback_plan=str(row.payback_plan or ""),
                due_chapter=int(row.due_chapter or 0),
                status=status_enum,
                created_at=(
                    row.created_at
                    if isinstance(row.created_at, _dt)
                    else _dt.now()
                ),
            )
        )
    return store
