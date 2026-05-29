from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from bestseller.services.payoff_ledger import (
    PayoffStatus,
    audit_due_payoffs,
    audit_setup_distance,
    build_payoff_ledger_view,
    run_payoff_ledger_audit,
)


@dataclass(frozen=True)
class FakeClue:
    id: str
    clue_code: str
    planted_in_chapter_number: int | None = None


@dataclass(frozen=True)
class FakePayoff:
    payoff_code: str
    label: str = ""
    description: str = ""
    source_clue_id: Any | None = None
    target_chapter_number: int | None = None
    actual_chapter_number: int | None = None
    status: str = "planned"
    metadata_json: dict[str, Any] = field(default_factory=dict)


def test_build_payoff_ledger_view_computes_due_overdue_and_resolved() -> None:
    view = build_payoff_ledger_view(
        [
            FakePayoff("due", target_chapter_number=5),
            FakePayoff("late", target_chapter_number=4),
            FakePayoff("done", target_chapter_number=5, actual_chapter_number=5),
        ],
        current_chapter=5,
    )

    assert [entry.payoff_status for entry in view] == [
        PayoffStatus.DUE,
        PayoffStatus.OVERDUE,
        PayoffStatus.RESOLVED,
    ]


def test_source_clue_sets_setup_distance() -> None:
    view = build_payoff_ledger_view(
        [
            FakePayoff(
                "p1",
                source_clue_id="c1",
                target_chapter_number=7,
                actual_chapter_number=7,
            )
        ],
        current_chapter=7,
        source_clues=[FakeClue("c1", "clue-1", planted_in_chapter_number=3)],
    )

    assert view[0].source_clue_code == "clue-1"
    assert view[0].setup_distance_chapters == 4


def test_due_audit_emits_due_and_overdue_findings() -> None:
    view = build_payoff_ledger_view(
        [
            FakePayoff("due", target_chapter_number=5),
            FakePayoff("late", target_chapter_number=4),
        ],
        current_chapter=5,
    )

    findings = audit_due_payoffs(view, current_chapter=5)

    assert [finding.code for finding in findings] == [
        "PAYOFF_DUE_UNRESOLVED",
        "PAYOFF_OVERDUE",
    ]


def test_setup_distance_audit_flags_current_short_payoff() -> None:
    view = build_payoff_ledger_view(
        [
            FakePayoff(
                "short",
                source_clue_id="c1",
                target_chapter_number=5,
                actual_chapter_number=5,
            )
        ],
        current_chapter=5,
        source_clues=[FakeClue("c1", "clue-1", planted_in_chapter_number=4)],
    )

    findings = audit_setup_distance(view, current_chapter=5)

    assert len(findings) == 1
    assert findings[0].code == "PAYOFF_SETUP_TOO_SHORT"
    assert findings[0].evidence["setup_distances"] == [1]


def test_run_payoff_ledger_audit_reports_closure_rate() -> None:
    audit = run_payoff_ledger_audit(
        [
            FakePayoff("done", actual_chapter_number=5),
            FakePayoff("future", target_chapter_number=7),
            FakePayoff("cancelled", status="cancelled"),
        ],
        current_chapter=5,
    )

    assert audit.closure_rate == 0.5
    assert audit.due_count == 0
    assert audit.overdue_count == 0
