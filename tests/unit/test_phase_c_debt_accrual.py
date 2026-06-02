"""Phase C debt accrual — DB-backed engine fix.

The real Phase C path writes ``ChaseDebtModel`` rows directly to the DB
(``_auto_sign_override_contracts``). Before this fix the post-chapter tick
accrued interest on an *empty in-memory* ``ChaseDebtLedger`` global, so DB
debt balances never compounded and never went overdue — the debt-balloon
engine was functionally dead.

``accrue_debt_rows`` is the pure arithmetic the DB call site
(``pipelines._apply_post_chapter_phase_c``) now runs over the loaded rows,
mutating them in place so the ORM session flush persists the change.
"""

from types import SimpleNamespace

from bestseller.services.chase_debt_ledger import accrue_debt_rows


def _row(**kw):
    base = dict(
        status="active",
        balance=1.0,
        interest_rate=0.10,
        accrued_through_chapter=1,
        due_chapter=10,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def test_accrues_active_debt_balance_forward():
    row = _row(balance=1.0, interest_rate=0.10, accrued_through_chapter=1, due_chapter=10)
    accrued, overdue = accrue_debt_rows([row], current_chapter=4)
    assert accrued == 1
    assert overdue == 0
    # 1.0 * 1.1^(4-1) = 1.331
    assert round(row.balance, 4) == 1.331
    assert row.accrued_through_chapter == 4
    assert row.status == "active"


def test_idempotent_per_chapter():
    row = _row(balance=1.0, accrued_through_chapter=1, due_chapter=10)
    accrue_debt_rows([row], current_chapter=4)
    balance_after_first = row.balance
    accrued, _ = accrue_debt_rows([row], current_chapter=4)
    assert accrued == 0  # second call same chapter is a no-op
    assert row.balance == balance_after_first


def test_flips_active_to_overdue_past_due_chapter():
    row = _row(balance=1.0, accrued_through_chapter=5, due_chapter=6)
    accrued, overdue = accrue_debt_rows([row], current_chapter=8)
    assert overdue == 1
    assert row.status == "overdue"
    # still accrued before the flip
    assert accrued == 1
    assert round(row.balance, 4) == round(1.0 * 1.1 ** 3, 4)


def test_overdue_rows_keep_accruing_but_not_recounted_as_newly_overdue():
    row = _row(status="overdue", balance=2.0, accrued_through_chapter=7, due_chapter=6)
    accrued, overdue = accrue_debt_rows([row], current_chapter=9)
    assert accrued == 1
    assert overdue == 0  # already overdue — not newly flipped
    assert round(row.balance, 4) == round(2.0 * 1.1 ** 2, 4)


def test_paid_rows_are_skipped():
    row = _row(status="paid", balance=5.0, accrued_through_chapter=3, due_chapter=10)
    accrued, overdue = accrue_debt_rows([row], current_chapter=20)
    assert (accrued, overdue) == (0, 0)
    assert row.balance == 5.0


def test_mixed_batch_counts():
    rows = [
        _row(balance=1.0, accrued_through_chapter=1, due_chapter=10),   # accrues
        _row(balance=1.0, accrued_through_chapter=5, due_chapter=4),    # accrues + overdue
        _row(status="paid", balance=1.0, accrued_through_chapter=1),    # skipped
    ]
    accrued, overdue = accrue_debt_rows(rows, current_chapter=6)
    assert accrued == 2
    assert overdue == 1


def test_no_accrual_when_chapter_not_advanced():
    row = _row(balance=1.0, accrued_through_chapter=6, due_chapter=10)
    accrued, overdue = accrue_debt_rows([row], current_chapter=6)
    assert (accrued, overdue) == (0, 0)
    assert row.balance == 1.0
