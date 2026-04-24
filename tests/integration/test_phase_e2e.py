"""End-to-end cross-phase integration test for the 2026-Q2 webnovel-writer
adoption plan (plan: shimmying-soaring-gadget).

This test exercises the five phases as they compose in a real run, without
pulling a live database into scope — everything goes through the pure
helpers exposed by each phase:

* Phase A1 — CheckerReport aggregation via ``merge_reports`` /
  ``aggregate_issue_counts`` / ``blocked_chapters``.
* Phase B1 — ``LINE_GAP_OVER`` soft issue carried through the report
  envelope to the aggregation layer.
* Phase C2 — ``validate_override_proposal`` + ``OverrideStore.create``
  sign off the soft gap violation.
* Phase C3 — ``ChaseDebtLedger.open_debt`` → ``accrue_interest`` for 5
  chapters → verified balance grows ~1.61× (1.10^5) per the plan's
  canonical check.
* Phase D1 — ``render_volume_timeline_markdown`` renders the per-volume
  timeline file body.
* Phase D3 — ``check_countdown_arithmetic`` blocks D-5→D-2 critical
  (``can_override=False``); ``check_time_regression`` fires soft high on
  a backward anchor.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from bestseller.domain.context import ChapterStateSnapshotContext, HardFactContext
from bestseller.services.chase_debt_ledger import (
    ChaseDebtLedger,
    DebtSource,
    DebtStatus,
    compute_principal,
)
from bestseller.services.checker_schema import (
    CheckerIssue,
    CheckerReport,
    aggregate_issue_counts,
    blocked_chapters,
    merge_reports,
    partition_by_chapter,
)
from bestseller.services.continuity import (
    check_countdown_arithmetic,
    check_time_regression,
)
from bestseller.services.override_contract import (
    OverrideContract,
    OverrideRejected,
    OverrideStatus,
    OverrideStore,
    RationaleType,
    validate_override_proposal,
)
from bestseller.services.story_bible import (
    build_volume_timeline_rows,
    render_volume_timeline_markdown,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _countdown(value: str, *, name: str = "末日倒计时") -> HardFactContext:
    return HardFactContext(name=name, value=value, unit="天", kind="countdown")


def _snapshot(
    chapter: int,
    facts: list[HardFactContext] | None = None,
    *,
    time_anchor: str | None = None,
    chapter_time_span: str | None = None,
) -> ChapterStateSnapshotContext:
    return ChapterStateSnapshotContext(
        chapter_number=chapter,
        facts=facts or [],
        time_anchor=time_anchor,
        chapter_time_span=chapter_time_span,
    )


_SOFT_LINE_GAP_ALLOWED = (
    RationaleType.ARC_TIMING.value,
    RationaleType.TRANSITIONAL_SETUP.value,
    RationaleType.GENRE_CONVENTION.value,
)


def _line_gap_report(chapter: int) -> CheckerReport:
    issue = CheckerIssue(
        id="LINE_GAP_OVER",
        type="line_gap",
        severity="high",
        location="全章",
        description="overt line has not dominated for 6 chapters (threshold = 5).",
        suggestion="本章建议以 overt 为底色/主导",
        can_override=True,
        allowed_rationales=_SOFT_LINE_GAP_ALLOWED,
    )
    return CheckerReport(
        agent="line-tracker",
        chapter=chapter,
        overall_score=70,
        passed=False,
        issues=(issue,),
        metrics={"overt_gap": 6, "threshold": 5},
        summary="overt line overdue",
    )


# ---------------------------------------------------------------------------
# Cross-phase composition
# ---------------------------------------------------------------------------


def test_phase_e2e_composition() -> None:
    """Drive all five phases against an in-memory sample project.

    * ch6 — overt line gap soft violation (Phase B1).
    * ch7 — countdown D-5 → D-2 jump (Phase D3 hard block).
    * ch8 — backward time anchor without flashback (Phase D3 soft high).
    """

    project_id = uuid4()

    # --- Phase D3: Countdown arithmetic check -----------------------------
    ch6_prev = _snapshot(6, [_countdown("5")], time_anchor="末世第 5 天 清晨")
    ch7_cur = _snapshot(7, [_countdown("2")], time_anchor="末世第 6 天 清晨")

    countdown_report = check_countdown_arithmetic(ch7_cur, ch6_prev)
    assert countdown_report.passed is False
    assert countdown_report.hard_violations
    assert countdown_report.issues[0].severity == "critical"
    assert countdown_report.issues[0].can_override is False
    assert countdown_report.blocks_write is True

    clean_prev = _snapshot(4, [_countdown("5")])
    clean_cur = _snapshot(5, [_countdown("4")])
    assert check_countdown_arithmetic(clean_cur, clean_prev).passed is True

    # --- Phase D3: Time regression check ----------------------------------
    ch7_prev = _snapshot(7, time_anchor="末世第 8 天 清晨")
    ch8_cur = _snapshot(8, time_anchor="末世第 3 天 傍晚")

    time_report = check_time_regression(ch8_cur, ch7_prev)
    assert time_report.passed is False
    assert time_report.soft_suggestions
    assert time_report.issues[0].severity == "high"
    assert time_report.issues[0].can_override is True
    assert set(time_report.issues[0].allowed_rationales) == {
        "WORLD_RULE_CONSTRAINT",
        "LOGIC_INTEGRITY",
    }

    # --- Phase B1: Line gap soft report -----------------------------------
    line_report = _line_gap_report(6)

    # --- Phase A1: aggregate heterogeneous reports ------------------------
    all_reports = merge_reports([line_report, countdown_report, time_report])
    assert len(all_reports) == 3

    counts = aggregate_issue_counts(all_reports)
    assert counts["LINE_GAP_OVER"] == 1
    assert counts["COUNTDOWN_ARITHMETIC_JUMP"] == 1
    assert counts["TIME_ANCHOR_REGRESSION"] == 1

    by_chapter = partition_by_chapter(all_reports)
    assert set(by_chapter.keys()) == {6, 7, 8}

    blocked = blocked_chapters(all_reports)
    assert 7 in blocked  # hard countdown jump
    assert 6 not in blocked  # soft line gap alone doesn't block
    assert 8 not in blocked  # soft time regression alone doesn't block

    # --- Phase C2: Sign an override for the soft line gap -----------------
    normalised = validate_override_proposal(
        violation_code="LINE_GAP_OVER",
        chapter_no=6,
        due_chapter=12,
        rationale=RationaleType.ARC_TIMING,
        rationale_text="第 12 章的主线大兑现需要把 overt 线下沉",
        payback_plan="第 12 章以 overt 为主导回归",
        soft_constraint_codes={"LINE_GAP_OVER"},
        allowed_rationale_types=_SOFT_LINE_GAP_ALLOWED,
    )
    assert normalised is RationaleType.ARC_TIMING

    store = OverrideStore()
    contract = OverrideContract(
        id=None,
        project_id=str(project_id),
        chapter_no=6,
        violation_code="LINE_GAP_OVER",
        rationale_type=RationaleType.ARC_TIMING,
        rationale_text="第 12 章的主线大兑现需要把 overt 线下沉",
        payback_plan="第 12 章以 overt 为主导回归",
        due_chapter=12,
    )
    created = store.create(contract)
    assert created.status == OverrideStatus.ACTIVE
    assert created.id is not None

    lookup = store.as_lookup(str(project_id))
    assert lookup("LINE_GAP_OVER", 6) is not None

    # --- Phase C3: Debt ledger — 1.10^5 ≈ 1.61051 canonical check --------
    ledger = ChaseDebtLedger()

    base_principal = 25.0
    genre_debt_multiplier = 1.0
    principal = compute_principal(base_principal, genre_debt_multiplier)
    assert principal == 25.0

    debt = ledger.open_debt(
        project_id=project_id,
        chapter_no=6,
        violation_code="LINE_GAP_OVER",
        principal=principal,
        due_chapter=12,
        source=DebtSource.OVERRIDE_CONTRACT,
        override_contract_id=created.id,
        interest_rate=0.10,
    )
    assert debt.balance == principal
    assert debt.status == DebtStatus.ACTIVE

    # Accrue 5 chapter ticks (chapters 7..11). accrue_interest is idempotent
    # and compounds forward based on accrued_through_chapter.
    for ch in range(7, 12):
        ledger.accrue_interest(project_id, current_chapter=ch)

    updated = ledger.list_all(project_id)[0]
    expected = principal * (1.10**5)  # ≈ 40.2627625
    assert abs(updated.balance - expected) < 1e-6
    assert abs(updated.balance - 25.0 * 1.61051) < 1e-4

    # --- Phase D1: render the volume timeline -----------------------------
    snapshots = [
        _snapshot(1, [_countdown("10")], time_anchor="末世第 1 天 清晨", chapter_time_span="半天"),
        _snapshot(2, [_countdown("9")], time_anchor="末世第 2 天 清晨", chapter_time_span="一天"),
        _snapshot(3, [_countdown("8")], time_anchor="末世第 3 天 清晨", chapter_time_span="一天"),
    ]
    rows = build_volume_timeline_rows(
        snapshots,
        chapter_titles={1: "开场", 2: "推进", 3: "转折"},
    )
    md = render_volume_timeline_markdown(
        volume_number=1, volume_title="起源", rows=rows
    )
    assert "# 第 1 卷" in md
    assert "开场" in md and "推进" in md and "转折" in md
    assert "末世第 1 天 清晨" in md
    assert "末日倒计时=10 天" in md
    assert "+1 天" in md


def test_phase_e2e_override_not_applicable_for_hard_violation() -> None:
    """Countdown arithmetic is hard by design — override proposals must be
    rejected before they reach the store.

    Equivalent to the golden-three non-bypassable policy, applied to the
    Phase D3 invariant.
    """

    with pytest.raises(OverrideRejected):
        validate_override_proposal(
            violation_code="COUNTDOWN_ARITHMETIC_JUMP",
            chapter_no=7,
            due_chapter=15,
            rationale=RationaleType.WORLD_RULE_CONSTRAINT,
            rationale_text="时间跳跃",
            payback_plan="下一卷修复",
            soft_constraint_codes={"LINE_GAP_OVER"},
            allowed_rationale_types=(RationaleType.WORLD_RULE_CONSTRAINT.value,),
        )


def test_phase_e2e_flashback_allows_countdown_jump() -> None:
    """Caller-provided flashback flag bypasses the countdown jump check."""

    prev = _snapshot(4, [_countdown("5")])
    cur = _snapshot(5, [_countdown("2")])

    report = check_countdown_arithmetic(cur, prev, is_flashback=True)

    assert report.passed is True
    assert report.issues == ()
