"""Phase D3 — CountdownArithmeticCheck + TimeRegressionCheck.

Covers the three canonical scenarios from the plan:

* D-5 → D-4 passes (countdown advanced by exactly 1 unit).
* D-5 → D-2 blocks with ``severity = critical`` and ``can_override = False``
  (countdown jumped > 1 unit without a flashback tag).
* Backward ``time_anchor`` without flashback → ``severity = high``,
  ``can_override = True`` with the plan-mandated allowed rationales.

Plus the surrounding edge cases (missing previous snapshot, first-chapter
case, reset detection, non-numeric countdowns, unparseable anchors,
flashback override).
"""

from __future__ import annotations

import pytest

from bestseller.domain.context import ChapterStateSnapshotContext, HardFactContext
from bestseller.services.continuity import (
    _parse_time_anchor,
    check_countdown_arithmetic,
    check_time_regression,
)
from bestseller.services.story_bible import (
    TimelineRow,
    build_volume_timeline_rows,
    render_volume_timeline_markdown,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _countdown(value: str, *, name: str = "末日倒计时", notes: str | None = None) -> HardFactContext:
    return HardFactContext(
        name=name,
        value=value,
        unit="天",
        kind="countdown",
        notes=notes,
    )


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


# ---------------------------------------------------------------------------
# CountdownArithmeticCheck
# ---------------------------------------------------------------------------


class TestCountdownArithmeticCheck:
    def test_d5_to_d4_passes(self) -> None:
        """D-5 → D-4 is the canonical clean advance (delta == 1)."""

        prev = _snapshot(4, [_countdown("5")])
        cur = _snapshot(5, [_countdown("4")])

        report = check_countdown_arithmetic(cur, prev)

        assert report.passed is True
        assert report.issues == ()
        assert report.overall_score == 100
        assert report.metrics["countdowns_checked"] == 1
        assert report.metrics["jumps_detected"] == 0
        assert report.agent == "time-continuity"
        assert report.chapter == 5

    def test_same_value_passes(self) -> None:
        """delta == 0 (countdown unchanged across the chapter) is legal."""

        prev = _snapshot(4, [_countdown("5")])
        cur = _snapshot(5, [_countdown("5")])

        report = check_countdown_arithmetic(cur, prev)

        assert report.passed is True
        assert report.issues == ()
        assert report.metrics["countdowns_checked"] == 1

    def test_d5_to_d2_blocks_critical_non_overridable(self) -> None:
        """D-5 → D-2: hard jump, critical severity, no override path."""

        prev = _snapshot(4, [_countdown("5")])
        cur = _snapshot(5, [_countdown("2")])

        report = check_countdown_arithmetic(cur, prev)

        assert report.passed is False
        assert len(report.issues) == 1

        issue = report.issues[0]
        assert issue.id == "COUNTDOWN_ARITHMETIC_JUMP"
        assert issue.severity == "critical"
        assert issue.can_override is False
        assert issue.allowed_rationales == ()
        assert "末日倒计时" in issue.description
        assert "5" in issue.description and "2" in issue.description

        # Feeds into write_gate via the hard-violations partition.
        assert report.hard_violations == (issue,)
        assert report.soft_suggestions == ()
        assert report.blocks_write is True
        assert report.metrics["jumps_detected"] == 1

    def test_flashback_flag_allows_jump(self) -> None:
        """Caller-provided flashback flag bypasses the arithmetic check."""

        prev = _snapshot(4, [_countdown("5")])
        cur = _snapshot(5, [_countdown("2")])

        report = check_countdown_arithmetic(cur, prev, is_flashback=True)

        assert report.passed is True
        assert report.issues == ()

    def test_flashback_inferred_from_notes(self) -> None:
        """``flashback=true`` / ``reset=true`` in fact notes auto-flags."""

        prev = _snapshot(4, [_countdown("5")])
        cur = _snapshot(
            5,
            [_countdown("2", notes="reset=true — 外部干预重置倒计时")],
        )

        report = check_countdown_arithmetic(cur, prev)

        assert report.passed is True

    def test_countdown_reset_flagged_when_unmarked(self) -> None:
        """Countdown value INCREASED (went up) — always critical unless flashback."""

        prev = _snapshot(4, [_countdown("3")])
        cur = _snapshot(5, [_countdown("7")])

        report = check_countdown_arithmetic(cur, prev)

        assert report.passed is False
        assert len(report.issues) == 1
        issue = report.issues[0]
        assert issue.id == "COUNTDOWN_RESET"
        assert issue.severity == "critical"
        assert issue.can_override is False

    def test_non_numeric_countdown_skipped(self) -> None:
        """Non-numeric values (e.g. 'soon') can't be checked — skip silently."""

        prev = _snapshot(4, [_countdown("soon")])
        cur = _snapshot(5, [_countdown("imminent")])

        report = check_countdown_arithmetic(cur, prev)

        assert report.passed is True
        assert report.metrics["countdowns_checked"] == 0

    def test_missing_in_previous_snapshot_is_skipped(self) -> None:
        """New countdown first appears in current — no baseline to compare."""

        prev = _snapshot(4, [])
        cur = _snapshot(5, [_countdown("10")])

        report = check_countdown_arithmetic(cur, prev)

        assert report.passed is True
        assert report.metrics["countdowns_checked"] == 0

    def test_first_chapter_no_previous_snapshot(self) -> None:
        """``previous_snapshot = None`` → trivial pass with informative summary."""

        cur = _snapshot(1, [_countdown("10")])

        report = check_countdown_arithmetic(cur, None)

        assert report.passed is True
        assert "skipped" in report.summary.lower()

    def test_multiple_countdowns_tracked_independently(self) -> None:
        prev = _snapshot(
            4,
            [_countdown("5", name="末日倒计时"), _countdown("10", name="物资耗尽")],
        )
        cur = _snapshot(
            5,
            [_countdown("4", name="末日倒计时"), _countdown("2", name="物资耗尽")],
        )

        report = check_countdown_arithmetic(cur, prev)

        assert report.passed is False
        assert len(report.issues) == 1
        assert "物资耗尽" in report.issues[0].description
        assert report.metrics["countdowns_checked"] == 2
        assert report.metrics["jumps_detected"] == 1

    def test_non_countdown_facts_ignored(self) -> None:
        """Level/resource/other kinds aren't part of countdown arithmetic."""

        prev = _snapshot(
            4,
            [
                HardFactContext(name="修为", value="3", kind="level"),
                HardFactContext(name="灵石", value="100", kind="resource"),
            ],
        )
        cur = _snapshot(
            5,
            [
                HardFactContext(name="修为", value="4", kind="level"),
                HardFactContext(name="灵石", value="50", kind="resource"),
            ],
        )

        report = check_countdown_arithmetic(cur, prev)

        assert report.passed is True
        assert report.metrics["countdowns_checked"] == 0


# ---------------------------------------------------------------------------
# TimeRegressionCheck
# ---------------------------------------------------------------------------


class TestTimeRegressionCheck:
    def test_forward_time_passes(self) -> None:
        prev = _snapshot(4, time_anchor="末世第 3 天 下午")
        cur = _snapshot(5, time_anchor="末世第 4 天 清晨")

        report = check_time_regression(cur, prev)

        assert report.passed is True
        assert report.issues == ()

    def test_same_day_later_part_passes(self) -> None:
        prev = _snapshot(4, time_anchor="末世第 4 天 清晨")
        cur = _snapshot(5, time_anchor="末世第 4 天 傍晚")

        report = check_time_regression(cur, prev)

        assert report.passed is True

    def test_backward_anchor_without_flashback_high_soft(self) -> None:
        """Plan scenario: backward anchor, no flashback → high + soft override."""

        prev = _snapshot(4, time_anchor="末世第 5 天 清晨")
        cur = _snapshot(5, time_anchor="末世第 3 天 傍晚")

        report = check_time_regression(cur, prev)

        assert report.passed is False
        assert len(report.issues) == 1

        issue = report.issues[0]
        assert issue.id == "TIME_ANCHOR_REGRESSION"
        assert issue.severity == "high"
        assert issue.can_override is True
        assert set(issue.allowed_rationales) == {
            "WORLD_RULE_CONSTRAINT",
            "LOGIC_INTEGRITY",
        }

        # Soft → goes into soft_suggestions, not hard_violations.
        assert report.soft_suggestions == (issue,)
        assert report.hard_violations == ()
        # High-severity soft shouldn't auto-block; the override contract
        # pipeline decides whether to let it through.
        assert report.blocks_write is False

    def test_backward_with_flashback_flag_passes(self) -> None:
        prev = _snapshot(4, time_anchor="末世第 5 天 清晨")
        cur = _snapshot(5, time_anchor="末世第 2 天 傍晚")

        report = check_time_regression(cur, prev, is_flashback=True)

        assert report.passed is True
        assert report.metrics["flashback_detected"] is True

    def test_backward_with_inferred_flashback_passes(self) -> None:
        prev = _snapshot(4, time_anchor="末世第 5 天")
        cur = _snapshot(
            5,
            [
                HardFactContext(
                    name="倒叙",
                    value="回忆",
                    kind="other",
                    notes="flashback=true — 回忆起末世第 2 天发生的事",
                )
            ],
            time_anchor="末世第 2 天 傍晚",
        )

        report = check_time_regression(cur, prev)

        assert report.passed is True

    def test_first_chapter_no_previous_snapshot(self) -> None:
        cur = _snapshot(1, time_anchor="末世第 1 天 清晨")

        report = check_time_regression(cur, None)

        assert report.passed is True
        assert "skipped" in report.summary.lower()

    def test_unparseable_anchor_skipped(self) -> None:
        prev = _snapshot(4, time_anchor="???")
        cur = _snapshot(5, time_anchor="某个时候")

        report = check_time_regression(cur, prev)

        assert report.passed is True
        assert "Unparseable" in report.summary or "skipped" in report.summary.lower()

    def test_missing_anchors_skipped(self) -> None:
        prev = _snapshot(4)
        cur = _snapshot(5)

        report = check_time_regression(cur, prev)

        assert report.passed is True
        assert report.issues == ()


# ---------------------------------------------------------------------------
# Time anchor parser
# ---------------------------------------------------------------------------


class TestParseTimeAnchor:
    @pytest.mark.parametrize(
        "anchor,expected_day",
        [
            ("末世第 4 天 清晨", 4),
            ("末世第4天", 4),
            ("第 12 天", 12),
            ("Day 7", 7),
            ("D-5", 5),
            ("day5 morning", 5),
        ],
    )
    def test_parses_day(self, anchor: str, expected_day: int) -> None:
        parsed = _parse_time_anchor(anchor)
        assert parsed is not None
        assert parsed[0] == expected_day

    def test_part_of_day_orders(self) -> None:
        morning = _parse_time_anchor("末世第 4 天 清晨")
        evening = _parse_time_anchor("末世第 4 天 傍晚")
        assert morning is not None and evening is not None
        assert morning < evening

    def test_none_on_no_number(self) -> None:
        assert _parse_time_anchor("某天") is None
        assert _parse_time_anchor("") is None
        assert _parse_time_anchor(None) is None

    def test_bare_integer_fallback(self) -> None:
        parsed = _parse_time_anchor("12 · 清晨")
        assert parsed is not None
        assert parsed[0] == 12


# ---------------------------------------------------------------------------
# Phase D1 — per-volume timeline renderer
# ---------------------------------------------------------------------------


class TestBuildVolumeTimelineRows:
    def test_rows_in_chapter_order(self) -> None:
        snapshots = [
            _snapshot(3, [_countdown("3")], time_anchor="末世第 5 天 清晨"),
            _snapshot(1, [_countdown("5")], time_anchor="末世第 3 天 清晨"),
            _snapshot(2, [_countdown("4")], time_anchor="末世第 4 天 清晨"),
        ]

        rows = build_volume_timeline_rows(snapshots)

        assert [r.chapter_number for r in rows] == [1, 2, 3]

    def test_delta_computation(self) -> None:
        snapshots = [
            _snapshot(1, time_anchor="末世第 3 天 清晨"),
            _snapshot(2, time_anchor="末世第 4 天 清晨"),
            _snapshot(3, time_anchor="末世第 4 天 傍晚"),
        ]

        rows = build_volume_timeline_rows(snapshots)

        assert rows[0].delta_from_previous == "起点"
        assert rows[1].delta_from_previous == "+1 天"
        assert "同一" in rows[2].delta_from_previous or "当天" in rows[2].delta_from_previous

    def test_countdown_states_surfaced(self) -> None:
        snapshots = [
            _snapshot(
                1,
                [
                    _countdown("5", name="末日倒计时"),
                    _countdown("10", name="物资耗尽"),
                    HardFactContext(name="修为", value="3", kind="level"),
                ],
            ),
        ]

        rows = build_volume_timeline_rows(snapshots)

        assert len(rows[0].countdown_states) == 2
        joined = "；".join(rows[0].countdown_states)
        assert "末日倒计时=5" in joined
        assert "物资耗尽=10" in joined
        # Level fact should not appear.
        assert "修为" not in joined

    def test_chapter_titles_applied(self) -> None:
        snapshots = [_snapshot(1, time_anchor="末世第 1 天")]
        titles = {1: "开场"}

        rows = build_volume_timeline_rows(snapshots, chapter_titles=titles)

        assert rows[0].chapter_title == "开场"

    def test_unparseable_anchor_leaves_delta_empty(self) -> None:
        snapshots = [
            _snapshot(1, time_anchor="某天"),
            _snapshot(2, time_anchor="末世第 4 天"),
        ]
        rows = build_volume_timeline_rows(snapshots)
        assert rows[0].delta_from_previous is None
        # Prev is unparsed → ch2 should show as '起点' (first parseable anchor).
        assert rows[1].delta_from_previous == "起点"


class TestRenderVolumeTimelineMarkdown:
    def test_header_and_columns(self) -> None:
        rows = [
            TimelineRow(
                chapter_number=1,
                chapter_title="开场",
                time_anchor="末世第 1 天 清晨",
                chapter_time_span="约 3 小时",
                delta_from_previous="起点",
                countdown_states=("末日倒计时=5 天",),
            ),
        ]

        md = render_volume_timeline_markdown(volume_number=1, volume_title="起源", rows=rows)

        assert "# 第 1 卷" in md
        assert "起源" in md
        # Columns:
        assert "时间锚点" in md
        assert "章内时间跨度" in md
        assert "与上章时间差" in md
        assert "倒计时状态" in md
        # Row content:
        assert "第 1 章" in md
        assert "开场" in md
        assert "末世第 1 天 清晨" in md
        assert "约 3 小时" in md
        assert "起点" in md
        assert "末日倒计时=5 天" in md

    def test_empty_rows_renders_placeholder(self) -> None:
        md = render_volume_timeline_markdown(
            volume_number=2, volume_title=None, rows=[]
        )
        assert "# 第 2 卷" in md
        # Should still contain a placeholder row so the file has a visible table.
        assert "| — | — | — | — | — | — |" in md

    def test_missing_cells_collapse_to_dash(self) -> None:
        rows = [
            TimelineRow(
                chapter_number=5,
                chapter_title=None,
                time_anchor=None,
                chapter_time_span=None,
                delta_from_previous=None,
                countdown_states=(),
            )
        ]
        md = render_volume_timeline_markdown(volume_number=1, volume_title=None, rows=rows)
        assert "第 5 章" in md
        # Multiple dashes for the empty cells on that row.
        assert md.count("—") >= 5
