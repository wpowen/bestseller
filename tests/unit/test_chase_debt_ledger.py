"""Phase C3 unit tests for chase_debt_ledger."""

from __future__ import annotations

import math

import pytest

from bestseller.services.chase_debt_ledger import (
    ChaseDebt,
    ChaseDebtLedger,
    DebtSource,
    DebtStatus,
    compound_balance,
    compute_principal,
)


# ---------------------------------------------------------------------------
# Pure helpers.
# ---------------------------------------------------------------------------


class TestComputePrincipal:
    def test_basic_multiply(self) -> None:
        assert compute_principal(100.0, 1.5) == 150.0

    def test_zero_base(self) -> None:
        assert compute_principal(0.0, 2.0) == 0.0

    def test_zero_multiplier(self) -> None:
        assert compute_principal(100.0, 0.0) == 0.0

    def test_negative_base_rejected(self) -> None:
        with pytest.raises(ValueError, match="base"):
            compute_principal(-1.0, 1.0)

    def test_negative_multiplier_rejected(self) -> None:
        with pytest.raises(ValueError, match="debt_multiplier"):
            compute_principal(1.0, -1.0)


class TestCompoundBalance:
    def test_zero_periods_noop(self) -> None:
        assert compound_balance(100.0, 0.1, 0) == 100.0

    def test_one_period(self) -> None:
        assert compound_balance(100.0, 0.10, 1) == pytest.approx(110.0)

    def test_five_periods_at_10pct(self) -> None:
        # 1.10^5 ≈ 1.61051
        assert compound_balance(100.0, 0.10, 5) == pytest.approx(161.051)

    def test_zero_interest(self) -> None:
        assert compound_balance(100.0, 0.0, 99) == 100.0

    def test_negative_periods_rejected(self) -> None:
        with pytest.raises(ValueError, match="periods"):
            compound_balance(100.0, 0.1, -1)


# ---------------------------------------------------------------------------
# ChaseDebt dataclass props.
# ---------------------------------------------------------------------------


class TestChaseDebt:
    def _debt(self, **overrides) -> ChaseDebt:
        base = dict(
            id=1,
            project_id="p1",
            chapter_no=5,
            violation_code="LINE_GAP_OVER",
            source=DebtSource.OVERRIDE_CONTRACT,
            principal=100.0,
            balance=100.0,
            interest_rate=0.10,
            accrued_through_chapter=5,
            due_chapter=10,
        )
        base.update(overrides)
        return ChaseDebt(**base)

    def test_is_active(self) -> None:
        d = self._debt()
        assert d.is_active
        assert not d.is_overdue
        assert not d.is_paid

    def test_is_overdue(self) -> None:
        d = self._debt(status=DebtStatus.OVERDUE)
        assert d.is_overdue and not d.is_active

    def test_is_paid(self) -> None:
        d = self._debt(status=DebtStatus.PAID)
        assert d.is_paid and not d.is_active

    def test_to_dict_shape(self) -> None:
        d = self._debt()
        out = d.to_dict()
        assert out["source"] == "override_contract"
        assert out["status"] == "active"
        assert out["balance"] == 100.0
        assert out["closed_at"] is None


# ---------------------------------------------------------------------------
# ChaseDebtLedger — open_debt and validation.
# ---------------------------------------------------------------------------


class TestOpenDebt:
    def test_basic_open(self) -> None:
        led = ChaseDebtLedger()
        d = led.open_debt(
            project_id="p1",
            chapter_no=5,
            violation_code="LINE_GAP_OVER",
            principal=100.0,
            due_chapter=10,
            override_contract_id=42,
        )
        assert d.id == 1
        assert d.status is DebtStatus.ACTIVE
        assert d.balance == 100.0
        assert d.accrued_through_chapter == 5
        assert d.source is DebtSource.OVERRIDE_CONTRACT
        assert d.override_contract_id == 42

    def test_ids_increment(self) -> None:
        led = ChaseDebtLedger()
        a = led.open_debt(
            project_id="p1", chapter_no=5, violation_code="X",
            principal=50, due_chapter=10,
        )
        b = led.open_debt(
            project_id="p1", chapter_no=6, violation_code="Y",
            principal=50, due_chapter=12,
        )
        assert (a.id, b.id) == (1, 2)

    def test_setup_payoff_source(self) -> None:
        led = ChaseDebtLedger()
        d = led.open_debt(
            project_id="p1", chapter_no=3, violation_code="PLEASURE_SETUP_PAYOFF_DEBT",
            principal=50, due_chapter=8, source=DebtSource.SETUP_PAYOFF,
        )
        assert d.source is DebtSource.SETUP_PAYOFF
        assert d.override_contract_id is None

    def test_due_not_after_chapter(self) -> None:
        led = ChaseDebtLedger()
        with pytest.raises(ValueError, match="due_chapter"):
            led.open_debt(
                project_id="p1", chapter_no=10, violation_code="X",
                principal=1.0, due_chapter=10,
            )

    def test_negative_principal_rejected(self) -> None:
        led = ChaseDebtLedger()
        with pytest.raises(ValueError, match="principal"):
            led.open_debt(
                project_id="p1", chapter_no=5, violation_code="X",
                principal=-1.0, due_chapter=10,
            )

    def test_negative_rate_rejected(self) -> None:
        led = ChaseDebtLedger()
        with pytest.raises(ValueError, match="interest_rate"):
            led.open_debt(
                project_id="p1", chapter_no=5, violation_code="X",
                principal=1.0, due_chapter=10, interest_rate=-0.01,
            )


# ---------------------------------------------------------------------------
# accrue_interest.
# ---------------------------------------------------------------------------


class TestAccrueInterest:
    def test_compounds_over_five_chapters(self) -> None:
        """1.10^5 ≈ 1.61051 — the canonical plan example."""
        led = ChaseDebtLedger()
        d = led.open_debt(
            project_id="p1", chapter_no=5, violation_code="X",
            principal=100.0, due_chapter=20, interest_rate=0.10,
        )
        touched = led.accrue_interest("p1", current_chapter=10)
        assert touched == 1
        active = led.list_active("p1")
        assert len(active) == 1
        assert active[0].balance == pytest.approx(161.051)
        assert active[0].accrued_through_chapter == 10

    def test_idempotent_same_chapter(self) -> None:
        led = ChaseDebtLedger()
        led.open_debt(
            project_id="p1", chapter_no=5, violation_code="X",
            principal=100.0, due_chapter=20,
        )
        led.accrue_interest("p1", 10)
        first_balance = led.list_active("p1")[0].balance
        # Second call at the same chapter must be a no-op.
        touched = led.accrue_interest("p1", 10)
        assert touched == 0
        assert led.list_active("p1")[0].balance == first_balance

    def test_incremental_accrual(self) -> None:
        """Two calls at +2 and then +3 should equal one call at +5."""
        led = ChaseDebtLedger()
        led.open_debt(
            project_id="p1", chapter_no=5, violation_code="X",
            principal=100.0, due_chapter=20,
        )
        led.accrue_interest("p1", 7)   # +2 chapters
        led.accrue_interest("p1", 10)  # +3 chapters, total +5
        assert led.list_active("p1")[0].balance == pytest.approx(161.051)

    def test_skips_other_project(self) -> None:
        led = ChaseDebtLedger()
        led.open_debt(
            project_id="p1", chapter_no=5, violation_code="X",
            principal=100.0, due_chapter=20,
        )
        led.open_debt(
            project_id="p2", chapter_no=5, violation_code="X",
            principal=100.0, due_chapter=20,
        )
        led.accrue_interest("p1", 10)
        p2_debts = led.list_active("p2")
        assert p2_debts[0].balance == 100.0  # untouched

    def test_skips_paid(self) -> None:
        led = ChaseDebtLedger()
        led.open_debt(
            project_id="p1", chapter_no=5, violation_code="X",
            principal=100.0, due_chapter=20,
        )
        led.close_debt(1)
        touched = led.accrue_interest("p1", 10)
        assert touched == 0


# ---------------------------------------------------------------------------
# close_debt.
# ---------------------------------------------------------------------------


class TestCloseDebt:
    def test_basic_close(self) -> None:
        led = ChaseDebtLedger()
        led.open_debt(
            project_id="p1", chapter_no=5, violation_code="X",
            principal=100.0, due_chapter=10,
        )
        closed = led.close_debt(1)
        assert closed is not None
        assert closed.is_paid
        assert closed.closed_at is not None
        assert led.list_active("p1") == ()

    def test_close_with_resolution_chapter_accrues_first(self) -> None:
        led = ChaseDebtLedger()
        led.open_debt(
            project_id="p1", chapter_no=5, violation_code="X",
            principal=100.0, due_chapter=20, interest_rate=0.10,
        )
        closed = led.close_debt(1, resolution_chapter=10)
        assert closed is not None
        assert closed.balance == pytest.approx(161.051)
        assert closed.accrued_through_chapter == 10

    def test_close_already_closed_returns_none(self) -> None:
        led = ChaseDebtLedger()
        led.open_debt(
            project_id="p1", chapter_no=5, violation_code="X",
            principal=100.0, due_chapter=10,
        )
        led.close_debt(1)
        assert led.close_debt(1) is None

    def test_close_missing_id_returns_none(self) -> None:
        led = ChaseDebtLedger()
        assert led.close_debt(999) is None

    def test_close_overdue_debt(self) -> None:
        led = ChaseDebtLedger()
        led.open_debt(
            project_id="p1", chapter_no=5, violation_code="X",
            principal=100.0, due_chapter=10,
        )
        led.scan_overdue("p1", current_chapter=11)
        closed = led.close_debt(1)
        assert closed is not None
        assert closed.is_paid


# ---------------------------------------------------------------------------
# scan_overdue.
# ---------------------------------------------------------------------------


class TestScanOverdue:
    def test_flips_past_due(self) -> None:
        led = ChaseDebtLedger()
        led.open_debt(
            project_id="p1", chapter_no=5, violation_code="X",
            principal=100.0, due_chapter=10,
        )
        led.open_debt(
            project_id="p1", chapter_no=5, violation_code="X",
            principal=100.0, due_chapter=20,
        )
        flipped = led.scan_overdue("p1", current_chapter=11)
        assert flipped == 1
        overdue = led.list_overdue("p1")
        assert len(overdue) == 1
        assert overdue[0].due_chapter == 10

    def test_at_due_chapter_is_not_overdue(self) -> None:
        led = ChaseDebtLedger()
        led.open_debt(
            project_id="p1", chapter_no=5, violation_code="X",
            principal=100.0, due_chapter=10,
        )
        flipped = led.scan_overdue("p1", current_chapter=10)
        assert flipped == 0

    def test_does_not_reflip_overdue(self) -> None:
        led = ChaseDebtLedger()
        led.open_debt(
            project_id="p1", chapter_no=5, violation_code="X",
            principal=100.0, due_chapter=10,
        )
        led.scan_overdue("p1", 11)
        # A second scan at a later chapter should not recount rows.
        flipped = led.scan_overdue("p1", 12)
        assert flipped == 0

    def test_paid_debts_skipped(self) -> None:
        led = ChaseDebtLedger()
        led.open_debt(
            project_id="p1", chapter_no=5, violation_code="X",
            principal=100.0, due_chapter=10,
        )
        led.close_debt(1)
        assert led.scan_overdue("p1", 99) == 0


# ---------------------------------------------------------------------------
# counts — shape consumed by scorecard.
# ---------------------------------------------------------------------------


class TestCounts:
    def test_counts_shape(self) -> None:
        led = ChaseDebtLedger()
        led.open_debt(
            project_id="p1", chapter_no=5, violation_code="X",
            principal=100.0, due_chapter=10,
        )
        led.open_debt(
            project_id="p1", chapter_no=5, violation_code="X",
            principal=100.0, due_chapter=20,
        )
        led.open_debt(
            project_id="p1", chapter_no=5, violation_code="X",
            principal=100.0, due_chapter=30,
        )
        led.scan_overdue("p1", 11)  # flip debt #1 to overdue
        led.close_debt(2)            # close debt #2 → paid
        c = led.counts("p1")
        assert c == {"active": 1, "overdue": 1, "paid": 1}


# ---------------------------------------------------------------------------
# Integration — OverrideContract + ChaseDebtLedger end-to-end shape.
# ---------------------------------------------------------------------------


class TestIntegration:
    def test_override_spawns_debt_ledger_interaction(self) -> None:
        """Sanity: opening a debt immediately after signing an override,
        accruing for a stretch, then paying. No DB — just confirms the
        two services compose as the plan describes.
        """
        from bestseller.services.override_contract import (
            OverrideContract,
            OverrideStatus,
            OverrideStore,
            RationaleType,
        )

        store = OverrideStore()
        contract = store.create(
            OverrideContract(
                id=None,
                project_id="p1",
                chapter_no=5,
                violation_code="LINE_GAP_OVER",
                rationale_type=RationaleType.ARC_TIMING,
                rationale_text="bridge",
                payback_plan="大兑现 at ch10",
                due_chapter=10,
            )
        )
        led = ChaseDebtLedger()
        debt = led.open_debt(
            project_id="p1",
            chapter_no=contract.chapter_no,
            violation_code=contract.violation_code,
            principal=compute_principal(100.0, 1.0),
            due_chapter=contract.due_chapter,
            override_contract_id=contract.id,
        )
        assert debt.override_contract_id == contract.id

        # Advance 3 chapters — 1.10^3 = 1.331
        led.accrue_interest("p1", current_chapter=8)
        assert led.list_active("p1")[0].balance == pytest.approx(133.1)

        # Close at ch10: accrue 2 more chapters first → 1.10^5 ≈ 1.61051
        closed = led.close_debt(debt.id, resolution_chapter=10)
        assert closed is not None
        assert closed.balance == pytest.approx(161.051)
