"""Phase C3 — Chase Debt Ledger.

Every signed override contract (and every legacy setup→payoff promise)
spawns a ``ChaseDebt`` that tracks the *cost* of leaving a soft
constraint unresolved. The balance compounds at ``interest_rate`` per
chapter while the debt is active; once the author writes the promised
payback chapter the debt is closed and disappears from the scorecard.

Two ways a debt gets opened:

* ``source="override_contract"`` — spawned from an
  ``OverrideContract`` at signing time. ``override_contract_id`` points
  back at the contract. ``principal = base_principal ×
  genre.override_config.debt_multiplier``.

* ``source="setup_payoff"`` — legacy planted-hook debts migrated from
  ``setup_payoff_tracker``. ``override_contract_id`` is ``None``;
  ``violation_code="PLEASURE_SETUP_PAYOFF_DEBT"``.

Like ``override_contract.py`` this module ships **without** SQLAlchemy
imports — the dataclass layer stays testable in isolation. Call sites
that persist to the DB use ``ChaseDebtModel`` directly and hand rows
off via ``from_row`` / ``to_row_kwargs``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class DebtSource(str, Enum):
    OVERRIDE_CONTRACT = "override_contract"
    SETUP_PAYOFF = "setup_payoff"


class DebtStatus(str, Enum):
    ACTIVE = "active"
    OVERDUE = "overdue"
    PAID = "paid"


# Default rate charged per chapter while a debt is active. Can be
# overridden per-debt at ``open_debt`` time. 10%/chapter is aggressive
# by financial standards but matches the "debt balloons if you stall"
# intuition — over the default 10-chapter payback window an
# un-serviced debt roughly 2.6×es.
_DEFAULT_INTEREST_RATE: float = 0.10


@dataclass(frozen=True)
class ChaseDebt:
    """In-memory representation of a chase debt row."""

    id: int | None
    project_id: str
    chapter_no: int
    violation_code: str
    source: DebtSource
    principal: float
    balance: float
    interest_rate: float
    accrued_through_chapter: int
    due_chapter: int
    override_contract_id: int | None = None
    status: DebtStatus = DebtStatus.ACTIVE
    created_at: datetime = field(default_factory=lambda: datetime.now(tz=timezone.utc))
    closed_at: datetime | None = None

    @property
    def is_active(self) -> bool:
        return self.status == DebtStatus.ACTIVE

    @property
    def is_overdue(self) -> bool:
        return self.status == DebtStatus.OVERDUE

    @property
    def is_paid(self) -> bool:
        return self.status == DebtStatus.PAID

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "project_id": self.project_id,
            "chapter_no": self.chapter_no,
            "violation_code": self.violation_code,
            "source": self.source.value,
            "principal": self.principal,
            "balance": self.balance,
            "interest_rate": self.interest_rate,
            "accrued_through_chapter": self.accrued_through_chapter,
            "due_chapter": self.due_chapter,
            "override_contract_id": self.override_contract_id,
            "status": self.status.value,
            "created_at": self.created_at.isoformat(),
            "closed_at": self.closed_at.isoformat() if self.closed_at else None,
        }


# ---------------------------------------------------------------------------
# Pure helpers — exposed so call sites that hit the DB directly can reuse
# the arithmetic without importing the store.
# ---------------------------------------------------------------------------


def compute_principal(base: float, debt_multiplier: float) -> float:
    """Return ``base × multiplier``; negative inputs rejected."""

    if base < 0:
        raise ValueError(f"base principal must be ≥ 0, got {base}")
    if debt_multiplier < 0:
        raise ValueError(f"debt_multiplier must be ≥ 0, got {debt_multiplier}")
    return base * debt_multiplier


def compound_balance(
    balance: float,
    interest_rate: float,
    periods: int,
) -> float:
    """Compound ``balance`` at ``interest_rate`` over ``periods`` chapters.

    ``periods == 0`` is a no-op; negative periods raise. Uses the
    simple multiplicative form ``balance * (1 + r)^periods`` because
    the ledger charges per-chapter (no partial accrual).
    """

    if periods < 0:
        raise ValueError(f"periods must be ≥ 0, got {periods}")
    if periods == 0:
        return balance
    return balance * (1.0 + interest_rate) ** periods


# ---------------------------------------------------------------------------
# In-memory store. Mirrors the surface of the eventual DB-backed service
# so call sites can swap one for the other without refactoring.
# ---------------------------------------------------------------------------


@dataclass
class ChaseDebtLedger:
    """Minimal in-memory debt ledger.

    Exposes the same surface as the eventual DB-backed service
    (``open_debt`` / ``accrue_interest`` / ``close_debt`` /
    ``scan_overdue`` / ``list_active`` / ``list_overdue``) so callers
    can swap a real store in without touching their code.

    ``open_debt`` returns the freshly-persisted row; ``accrue_interest``
    returns the number of rows touched; ``close_debt`` returns the
    closed row or ``None`` if the id doesn't exist or is already
    closed.
    """

    _next_id: int = 1
    _rows: list[ChaseDebt] = field(default_factory=list)

    def open_debt(
        self,
        *,
        project_id: str,
        chapter_no: int,
        violation_code: str,
        principal: float,
        due_chapter: int,
        source: DebtSource = DebtSource.OVERRIDE_CONTRACT,
        override_contract_id: int | None = None,
        interest_rate: float = _DEFAULT_INTEREST_RATE,
    ) -> ChaseDebt:
        if due_chapter <= chapter_no:
            raise ValueError(
                f"due_chapter ({due_chapter}) must be > chapter_no ({chapter_no})"
            )
        if principal < 0:
            raise ValueError(f"principal must be ≥ 0, got {principal}")
        if interest_rate < 0:
            raise ValueError(f"interest_rate must be ≥ 0, got {interest_rate}")
        row = ChaseDebt(
            id=self._next_id,
            project_id=project_id,
            chapter_no=chapter_no,
            violation_code=violation_code,
            source=source,
            principal=principal,
            balance=principal,
            interest_rate=interest_rate,
            accrued_through_chapter=chapter_no,
            due_chapter=due_chapter,
            override_contract_id=override_contract_id,
        )
        self._rows.append(row)
        self._next_id += 1
        return row

    def accrue_interest(self, project_id: str, current_chapter: int) -> int:
        """Roll every active debt in ``project_id`` forward to ``current_chapter``.

        For each active row ``r`` where ``current_chapter >
        r.accrued_through_chapter``, multiply ``balance`` by
        ``(1 + interest_rate)^(current_chapter - accrued_through_chapter)``
        and advance the accrual pointer. Returns the number of rows
        touched so pipelines can log the work.

        Idempotent: calling twice with the same ``current_chapter`` is
        a no-op on the second call because ``accrued_through_chapter``
        catches up on the first.
        """

        touched = 0
        for idx, r in enumerate(self._rows):
            if r.project_id != project_id or not r.is_active:
                continue
            periods = current_chapter - r.accrued_through_chapter
            if periods <= 0:
                continue
            new_balance = compound_balance(r.balance, r.interest_rate, periods)
            self._rows[idx] = ChaseDebt(
                id=r.id,
                project_id=r.project_id,
                chapter_no=r.chapter_no,
                violation_code=r.violation_code,
                source=r.source,
                principal=r.principal,
                balance=new_balance,
                interest_rate=r.interest_rate,
                accrued_through_chapter=current_chapter,
                due_chapter=r.due_chapter,
                override_contract_id=r.override_contract_id,
                status=r.status,
                created_at=r.created_at,
                closed_at=r.closed_at,
            )
            touched += 1
        return touched

    def close_debt(
        self,
        debt_id: int,
        *,
        resolution_chapter: int | None = None,
    ) -> ChaseDebt | None:
        """Mark the debt as paid; returns the closed row or ``None``.

        If ``resolution_chapter`` is supplied and is ahead of the
        debt's ``accrued_through_chapter``, interest is accrued up to
        that chapter first so the final balance captures everything
        owed through resolution.
        """

        for idx, r in enumerate(self._rows):
            if r.id == debt_id and r.status in (DebtStatus.ACTIVE, DebtStatus.OVERDUE):
                balance = r.balance
                through = r.accrued_through_chapter
                if resolution_chapter is not None and resolution_chapter > through:
                    balance = compound_balance(
                        balance,
                        r.interest_rate,
                        resolution_chapter - through,
                    )
                    through = resolution_chapter
                closed = ChaseDebt(
                    id=r.id,
                    project_id=r.project_id,
                    chapter_no=r.chapter_no,
                    violation_code=r.violation_code,
                    source=r.source,
                    principal=r.principal,
                    balance=balance,
                    interest_rate=r.interest_rate,
                    accrued_through_chapter=through,
                    due_chapter=r.due_chapter,
                    override_contract_id=r.override_contract_id,
                    status=DebtStatus.PAID,
                    created_at=r.created_at,
                    closed_at=datetime.now(tz=timezone.utc),
                )
                self._rows[idx] = closed
                return closed
        return None

    def scan_overdue(self, project_id: str, current_chapter: int) -> int:
        """Flip ``active`` → ``overdue`` for rows past their due date.

        Returns the number of rows flipped. Typically called once per
        chapter by the pipeline. Already-overdue rows are not touched
        again so the count reflects *newly* overdue debts.
        """

        flipped = 0
        for idx, r in enumerate(self._rows):
            if r.project_id != project_id or not r.is_active:
                continue
            if current_chapter <= r.due_chapter:
                continue
            self._rows[idx] = ChaseDebt(
                id=r.id,
                project_id=r.project_id,
                chapter_no=r.chapter_no,
                violation_code=r.violation_code,
                source=r.source,
                principal=r.principal,
                balance=r.balance,
                interest_rate=r.interest_rate,
                accrued_through_chapter=r.accrued_through_chapter,
                due_chapter=r.due_chapter,
                override_contract_id=r.override_contract_id,
                status=DebtStatus.OVERDUE,
                created_at=r.created_at,
                closed_at=r.closed_at,
            )
            flipped += 1
        return flipped

    def list_active(self, project_id: str) -> tuple[ChaseDebt, ...]:
        return tuple(
            r
            for r in self._rows
            if r.project_id == project_id and r.is_active
        )

    def list_overdue(self, project_id: str) -> tuple[ChaseDebt, ...]:
        return tuple(
            r
            for r in self._rows
            if r.project_id == project_id and r.is_overdue
        )

    def list_all(self, project_id: str) -> tuple[ChaseDebt, ...]:
        return tuple(r for r in self._rows if r.project_id == project_id)

    def counts(self, project_id: str) -> dict[str, int]:
        """Return ``{active, overdue, paid}`` counts for scorecard consumption."""

        active = overdue = paid = 0
        for r in self._rows:
            if r.project_id != project_id:
                continue
            if r.is_active:
                active += 1
            elif r.is_overdue:
                overdue += 1
            elif r.is_paid:
                paid += 1
        return {"active": active, "overdue": overdue, "paid": paid}
