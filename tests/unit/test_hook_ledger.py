"""Tests for the hook ledger view + audit layer (methodology v2 Step 4).

Covers:

  * ``classify_hook_type`` — clue_type and metadata_json override.
  * ``build_hook_ledger_view`` — status transitions (active / resolved /
    overdue / cancelled) and age computation.
  * Four audit functions, each with positive (clean) and negative
    (finding-emitted) cases.
  * Aggregate ``run_hook_ledger_audit`` — closure_rate, by_type, and
    feature-flag default-off behaviour.

The tests use a frozen dataclass ``FakeClue`` to satisfy the
``ClueLike`` Protocol without requiring SQLAlchemy or a database.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any
from unittest import mock

import pytest

from bestseller.services.hook_ledger import (
    DEFAULT_ACTIVE_COUNT_MAX,
    DEFAULT_ACTIVE_COUNT_MIN,
    DEFAULT_MAX_AGE_CHAPTERS,
    AuditFinding,
    HookLedgerAudit,
    HookLedgerEntry,
    HookStatus,
    HookType,
    audit_active_hook_count,
    audit_max_age,
    audit_next_compression_seed,
    audit_per_chapter_balance,
    build_hook_ledger_view,
    classify_hook_type,
    is_methodology_v2_enabled,
    run_hook_ledger_audit,
)


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FakeClue:
    """Minimal frozen stand-in for ``ClueModel`` satisfying ``ClueLike``."""

    clue_code: str
    clue_type: str = "foreshadow"
    label: str = ""
    planted_in_chapter_number: int | None = None
    expected_payoff_by_chapter_number: int | None = None
    actual_paid_off_chapter_number: int | None = None
    status: str = "planted"
    metadata_json: dict[str, Any] = field(default_factory=dict)


def _make_clue(
    code: str,
    *,
    planted: int | None = None,
    paid: int | None = None,
    status: str = "planted",
    clue_type: str = "foreshadow",
    hook_type_meta: str | None = None,
) -> FakeClue:
    meta: dict[str, Any] = {}
    if hook_type_meta is not None:
        meta["hook_type"] = hook_type_meta
    return FakeClue(
        clue_code=code,
        clue_type=clue_type,
        planted_in_chapter_number=planted,
        actual_paid_off_chapter_number=paid,
        status=status,
        metadata_json=meta,
    )


# ---------------------------------------------------------------------------
# classify_hook_type
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestClassifyHookType:
    def test_foreshadow_maps_to_information_gap(self) -> None:
        clue = _make_clue("c1", clue_type="foreshadow")
        assert classify_hook_type(clue) is HookType.INFORMATION_GAP

    @pytest.mark.parametrize(
        "clue_type,expected",
        [
            ("deadline", HookType.DEADLINE),
            ("countdown", HookType.DEADLINE),
            ("mystery", HookType.MYSTERY),
            ("puzzle", HookType.MYSTERY),
            ("desire", HookType.DESIRE),
            ("promise", HookType.DESIRE),
            ("threat", HookType.THREAT),
            ("danger", HookType.THREAT),
        ],
    )
    def test_clue_type_maps_to_hook_type(
        self, clue_type: str, expected: HookType
    ) -> None:
        clue = _make_clue("c1", clue_type=clue_type)
        assert classify_hook_type(clue) is expected

    def test_metadata_override_wins(self) -> None:
        clue = _make_clue(
            "c1", clue_type="foreshadow", hook_type_meta="threat"
        )
        assert classify_hook_type(clue) is HookType.THREAT

    def test_unknown_clue_type_falls_back_to_information_gap(self) -> None:
        clue = _make_clue("c1", clue_type="some_project_specific_type")
        assert classify_hook_type(clue) is HookType.INFORMATION_GAP

    def test_invalid_metadata_override_falls_back(self) -> None:
        clue = _make_clue(
            "c1", clue_type="deadline", hook_type_meta="not_a_real_type"
        )
        # metadata invalid → fall back to clue_type mapping → DEADLINE
        assert classify_hook_type(clue) is HookType.DEADLINE


# ---------------------------------------------------------------------------
# build_hook_ledger_view
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBuildHookLedgerView:
    def test_active_entry_within_max_age(self) -> None:
        clue = _make_clue("c1", planted=10)
        view = build_hook_ledger_view([clue], current_chapter=12)
        assert len(view) == 1
        assert view[0].hook_status is HookStatus.ACTIVE
        assert view[0].age_chapters == 2
        assert view[0].is_active

    def test_resolved_entry(self) -> None:
        clue = _make_clue("c1", planted=10, paid=14)
        view = build_hook_ledger_view([clue], current_chapter=20)
        assert view[0].hook_status is HookStatus.RESOLVED
        assert view[0].is_resolved

    def test_overdue_entry_when_age_exceeds_max(self) -> None:
        clue = _make_clue("c1", planted=1)
        # age 17 > default max 15
        view = build_hook_ledger_view([clue], current_chapter=18)
        assert view[0].hook_status is HookStatus.OVERDUE
        assert view[0].is_overdue
        assert view[0].age_chapters == 17

    def test_overdue_boundary_inclusive(self) -> None:
        """age == max_age should still be ACTIVE, not OVERDUE."""
        clue = _make_clue("c1", planted=1)
        view = build_hook_ledger_view(
            [clue], current_chapter=16, max_age_chapters=15
        )
        # age 15, not yet > 15
        assert view[0].hook_status is HookStatus.ACTIVE

    def test_cancelled_entry(self) -> None:
        clue = _make_clue("c1", planted=10, status="cancelled")
        view = build_hook_ledger_view([clue], current_chapter=20)
        assert view[0].hook_status is HookStatus.CANCELLED
        assert view[0].is_cancelled

    def test_planted_chapter_none_treated_as_active(self) -> None:
        clue = _make_clue("c1", planted=None)
        view = build_hook_ledger_view([clue], current_chapter=10)
        assert view[0].hook_status is HookStatus.ACTIVE
        assert view[0].age_chapters is None

    def test_custom_max_age_overrides_default(self) -> None:
        clue = _make_clue("c1", planted=1)
        view = build_hook_ledger_view(
            [clue], current_chapter=8, max_age_chapters=5
        )
        # age 7 > 5
        assert view[0].is_overdue


# ---------------------------------------------------------------------------
# audit_active_hook_count
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestAuditActiveHookCount:
    def _view_with_n_active(self, n: int) -> tuple[HookLedgerEntry, ...]:
        clues = [_make_clue(f"c{i}", planted=10) for i in range(n)]
        return build_hook_ledger_view(clues, current_chapter=11)

    def test_in_range_emits_no_finding(self) -> None:
        view = self._view_with_n_active(5)
        result = audit_active_hook_count(view, current_chapter=11)
        assert result.active_count == 5
        assert result.findings == ()

    def test_too_few_emits_warn(self) -> None:
        view = self._view_with_n_active(2)
        result = audit_active_hook_count(view, current_chapter=11)
        assert len(result.findings) == 1
        f = result.findings[0]
        assert f.code == "HOOK_ACTIVE_COUNT_TOO_LOW"
        assert f.severity == "warn"
        assert "2 个" in f.detail

    def test_too_many_emits_warn(self) -> None:
        view = self._view_with_n_active(9)
        result = audit_active_hook_count(view, current_chapter=11)
        assert len(result.findings) == 1
        assert result.findings[0].code == "HOOK_ACTIVE_COUNT_TOO_HIGH"

    def test_boundary_min_passes(self) -> None:
        view = self._view_with_n_active(DEFAULT_ACTIVE_COUNT_MIN)
        result = audit_active_hook_count(view, current_chapter=11)
        assert result.findings == ()

    def test_boundary_max_passes(self) -> None:
        view = self._view_with_n_active(DEFAULT_ACTIVE_COUNT_MAX)
        result = audit_active_hook_count(view, current_chapter=11)
        assert result.findings == ()

    def test_custom_bounds(self) -> None:
        view = self._view_with_n_active(2)
        # custom min=1 → 2 is fine
        result = audit_active_hook_count(
            view, current_chapter=11, min_expected=1, max_expected=10
        )
        assert result.findings == ()

    def test_resolved_hooks_excluded_from_count(self) -> None:
        clues = [
            _make_clue("c1", planted=10),  # active
            _make_clue("c2", planted=10, paid=11),  # resolved, not counted
            _make_clue("c3", planted=10, paid=11),  # resolved, not counted
        ]
        view = build_hook_ledger_view(clues, current_chapter=11)
        result = audit_active_hook_count(view, current_chapter=11)
        assert result.active_count == 1


# ---------------------------------------------------------------------------
# audit_per_chapter_balance
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestAuditPerChapterBalance:
    def test_clean_chapter_with_plant_and_resolve(self) -> None:
        clues = [
            _make_clue("c1", planted=5),  # planted earlier
            _make_clue("c1_paid", planted=3, paid=10),  # resolved at 10
            _make_clue("c2_new", planted=10),  # planted at 10
        ]
        view = build_hook_ledger_view(clues, current_chapter=10)
        result = audit_per_chapter_balance(view, current_chapter=10)
        assert result.plant_count == 1
        assert result.resolve_count == 1
        assert result.findings == ()

    def test_missing_plant_flag(self) -> None:
        clues = [_make_clue("c1", planted=3, paid=10)]
        view = build_hook_ledger_view(clues, current_chapter=10)
        result = audit_per_chapter_balance(view, current_chapter=10)
        codes = {f.code for f in result.findings}
        assert "HOOK_PER_CHAPTER_NO_PLANT" in codes

    def test_missing_resolve_flag(self) -> None:
        clues = [_make_clue("c1", planted=10)]
        view = build_hook_ledger_view(clues, current_chapter=10)
        result = audit_per_chapter_balance(view, current_chapter=10)
        codes = {f.code for f in result.findings}
        assert "HOOK_PER_CHAPTER_NO_RESOLVE" in codes

    def test_both_missing_emits_two_findings(self) -> None:
        clues = [_make_clue("c1", planted=5)]  # neither plant nor resolve at 10
        view = build_hook_ledger_view(clues, current_chapter=10)
        result = audit_per_chapter_balance(view, current_chapter=10)
        assert len(result.findings) == 2


# ---------------------------------------------------------------------------
# audit_max_age
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestAuditMaxAge:
    def test_no_overdue_emits_no_finding(self) -> None:
        clues = [_make_clue("c1", planted=10)]
        view = build_hook_ledger_view([*clues], current_chapter=12)
        result = audit_max_age(view, current_chapter=12)
        assert result.overdue_entries == ()
        assert result.findings == ()

    def test_overdue_emits_finding_with_codes_and_ages(self) -> None:
        clues = [
            _make_clue("c_old1", planted=1),
            _make_clue("c_old2", planted=2),
            _make_clue("c_recent", planted=15),
        ]
        view = build_hook_ledger_view(clues, current_chapter=18)
        result = audit_max_age(view, current_chapter=18)
        # c_old1 age 17, c_old2 age 16; both > 15
        overdue_codes = {e.clue_code for e in result.overdue_entries}
        assert overdue_codes == {"c_old1", "c_old2"}
        assert len(result.findings) == 1
        ev = result.findings[0].evidence
        assert set(ev["overdue_codes"]) == {"c_old1", "c_old2"}
        assert sorted(ev["ages"]) == [16, 17]

    def test_custom_max_age(self) -> None:
        clues = [_make_clue("c1", planted=1)]
        view = build_hook_ledger_view(
            clues, current_chapter=8, max_age_chapters=5
        )
        result = audit_max_age(view, current_chapter=8, max_age_chapters=5)
        assert len(result.overdue_entries) == 1


# ---------------------------------------------------------------------------
# audit_next_compression_seed
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestAuditNextCompressionSeed:
    def test_no_prev_payoff_no_finding(self) -> None:
        clues = [_make_clue("c1", planted=5)]
        view = build_hook_ledger_view(clues, current_chapter=10)
        result = audit_next_compression_seed(view, current_chapter=10)
        assert not result.previous_chapter_had_payoff
        assert result.findings == ()

    def test_prev_payoff_with_new_plant_no_finding(self) -> None:
        clues = [
            _make_clue("c1", planted=3, paid=9),  # paid at prev (9)
            _make_clue("c2", planted=10),  # planted this chapter
        ]
        view = build_hook_ledger_view(clues, current_chapter=10)
        result = audit_next_compression_seed(view, current_chapter=10)
        assert result.previous_chapter_had_payoff
        assert result.current_chapter_planted_new
        assert result.findings == ()

    def test_prev_payoff_without_new_plant_emits_finding(self) -> None:
        clues = [
            _make_clue("c1", planted=3, paid=9),  # paid at prev (9)
            # no new plant at chapter 10
        ]
        view = build_hook_ledger_view(clues, current_chapter=10)
        result = audit_next_compression_seed(view, current_chapter=10)
        assert result.previous_chapter_had_payoff
        assert not result.current_chapter_planted_new
        assert len(result.findings) == 1
        assert result.findings[0].code == "HOOK_NEXT_COMPRESSION_SEED_MISSING"


# ---------------------------------------------------------------------------
# run_hook_ledger_audit + aggregates
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRunHookLedgerAudit:
    def test_returns_aggregate_with_all_findings(self) -> None:
        clues = [
            # too few active (only 1) + no plant/resolve this chapter
            _make_clue("c1", planted=5),
        ]
        audit = run_hook_ledger_audit(clues, current_chapter=10)
        codes = {f.code for f in audit.all_findings}
        # 1 active < min(3) → TOO_LOW
        # no plant + no resolve → 2 findings
        assert "HOOK_ACTIVE_COUNT_TOO_LOW" in codes
        assert "HOOK_PER_CHAPTER_NO_PLANT" in codes
        assert "HOOK_PER_CHAPTER_NO_RESOLVE" in codes

    def test_closure_rate_full(self) -> None:
        clues = [
            _make_clue("c1", planted=1, paid=2),
            _make_clue("c2", planted=2, paid=3),
        ]
        audit = run_hook_ledger_audit(clues, current_chapter=10)
        assert audit.closure_rate == 1.0

    def test_closure_rate_partial(self) -> None:
        clues = [
            _make_clue("c1", planted=1, paid=2),  # resolved
            _make_clue("c2", planted=2),  # active
            _make_clue("c3", planted=3),  # active
        ]
        audit = run_hook_ledger_audit(clues, current_chapter=5)
        assert audit.closure_rate == pytest.approx(1 / 3)

    def test_closure_rate_empty(self) -> None:
        audit = run_hook_ledger_audit([], current_chapter=10)
        assert audit.closure_rate == 1.0

    def test_closure_rate_excludes_cancelled(self) -> None:
        clues = [
            _make_clue("c1", planted=1, paid=2),  # resolved
            _make_clue("c2", planted=2, status="cancelled"),
        ]
        audit = run_hook_ledger_audit(clues, current_chapter=10)
        # only c1 counts; resolved 1 / total 1 = 1.0
        assert audit.closure_rate == 1.0

    def test_by_type_counts_active_only(self) -> None:
        clues = [
            _make_clue("c1", clue_type="deadline", planted=10),
            _make_clue("c2", clue_type="threat", planted=10),
            _make_clue("c3", clue_type="threat", planted=10),
            _make_clue("c4", clue_type="mystery", planted=10, paid=11),
        ]
        audit = run_hook_ledger_audit(clues, current_chapter=11)
        counts = audit.by_type()
        assert counts[HookType.DEADLINE] == 1
        assert counts[HookType.THREAT] == 2
        # c4 resolved, not counted
        assert counts[HookType.MYSTERY] == 0
        assert counts[HookType.INFORMATION_GAP] == 0
        assert counts[HookType.DESIRE] == 0


# ---------------------------------------------------------------------------
# Feature flag
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFeatureFlag:
    def test_default_off(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            assert is_methodology_v2_enabled() is False

    def test_explicit_off(self) -> None:
        with mock.patch.dict(os.environ, {"BESTSELLER_METHODOLOGY_V2": "0"}):
            assert is_methodology_v2_enabled() is False

    def test_on(self) -> None:
        with mock.patch.dict(os.environ, {"BESTSELLER_METHODOLOGY_V2": "1"}):
            assert is_methodology_v2_enabled() is True

    def test_other_value_treated_as_off(self) -> None:
        # only "1" enables; everything else disabled. Conservative default
        # because Step 7 plan documents this gate explicitly.
        with mock.patch.dict(
            os.environ, {"BESTSELLER_METHODOLOGY_V2": "true"}
        ):
            assert is_methodology_v2_enabled() is False


# ---------------------------------------------------------------------------
# Smoke / type sanity
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_audit_finding_is_immutable() -> None:
    f = AuditFinding(
        code="X", severity="warn", detail="d", evidence={"k": "v"}
    )
    with pytest.raises((AttributeError, Exception)):
        f.code = "Y"  # type: ignore[misc]


@pytest.mark.unit
def test_hook_ledger_entry_is_immutable() -> None:
    e = HookLedgerEntry(
        clue_code="c",
        hook_type=HookType.INFORMATION_GAP,
        label="",
        planted_chapter=1,
        expected_payoff_chapter=None,
        actual_paid_chapter=None,
        hook_status=HookStatus.ACTIVE,
        age_chapters=0,
    )
    with pytest.raises((AttributeError, Exception)):
        e.clue_code = "x"  # type: ignore[misc]


@pytest.mark.unit
def test_full_audit_result_is_immutable() -> None:
    audit = run_hook_ledger_audit([], current_chapter=1)
    assert isinstance(audit, HookLedgerAudit)
    # frozen dataclasses raise FrozenInstanceError on assignment
    with pytest.raises(Exception):
        audit.view = ()  # type: ignore[misc]
