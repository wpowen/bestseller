"""Unit tests for :mod:`bestseller.services.plan_forward_adjustment`.

The module produces **canon-respecting forward-only** recommendations:
it classifies volumes as fully-written / in-progress / unwritten, pins
the "frontier" volume (the first one still needing work), and only
emits adjustments for volume ≥ frontier.

Tests cover:
  * Frontier computation (all three volume states)
  * Antagonist forward-status classification
  * Per-volume overt coverage
  * Forward-only resolution distribution
  * Recommendation rule firings (missing overt, monotonous
    resolution, all-same-antagonist rotation collapse,
    no-active-antagonist, informational summary)
  * Bilingual (zh-CN / en-US) output
"""

from __future__ import annotations

import pytest

from bestseller.services.plan_forward_adjustment import (
    CANON_CHAPTER_STATUSES,
    AntagonistForwardSummary,
    ForwardPlanReport,
    ForwardRecommendation,
    VolumeForwardCoverage,
    build_forward_plan_report,
    compute_frontier_volume,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ch(chapter_number: int, volume_number: int, status: str = "complete") -> dict:
    return {
        "chapter_number": chapter_number,
        "volume_number": volume_number,
        "status": status,
    }


def _make_chapters(per_volume: dict[int, list[str]]) -> list[dict]:
    """Build a chapter list from ``{volume: [status, status, ...]}``."""
    out: list[dict] = []
    n = 1
    for vol, statuses in sorted(per_volume.items()):
        for s in statuses:
            out.append(_ch(n, vol, s))
            n += 1
    return out


def _antag(
    name: str,
    *,
    line_role: str = "overt",
    scope_volume_number: int | None = None,
    stages_of_relevance: list | None = None,
    resolution_type: str = "defeated_and_killed",
) -> dict:
    return {
        "name": name,
        "line_role": line_role,
        "scope_volume_number": scope_volume_number,
        "stages_of_relevance": stages_of_relevance or [],
        "resolution_type": resolution_type,
    }


# ---------------------------------------------------------------------------
# Frontier computation
# ---------------------------------------------------------------------------


def test_frontier_is_first_unwritten_volume() -> None:
    chapters = _make_chapters(
        {
            1: ["complete", "complete"],
            2: ["complete", "complete"],
            3: ["drafting", "planned"],
            4: ["planned", "planned"],
        }
    )
    frontier, fully, in_prog, unwritten = compute_frontier_volume(
        chapters, volume_count=4
    )
    assert frontier == 3
    assert fully == {1, 2}
    assert in_prog == set()
    assert unwritten == {3, 4}


def test_frontier_identifies_in_progress_volume() -> None:
    """A volume with at least one complete chapter AND at least one
    unwritten chapter is ``in_progress``."""
    chapters = _make_chapters(
        {
            1: ["complete", "complete"],
            2: ["complete", "drafting"],  # half done
            3: ["planned"],
        }
    )
    frontier, fully, in_prog, unwritten = compute_frontier_volume(
        chapters, volume_count=3
    )
    assert frontier == 2
    assert fully == {1}
    assert in_prog == {2}
    assert unwritten == {3}


def test_frontier_when_everything_is_written() -> None:
    chapters = _make_chapters(
        {
            1: ["complete"],
            2: ["complete"],
        }
    )
    frontier, fully, in_prog, unwritten = compute_frontier_volume(
        chapters, volume_count=2
    )
    assert frontier == 3  # volume_count + 1 → no forward work
    assert fully == {1, 2}
    assert unwritten == set()


def test_frontier_when_nothing_is_written() -> None:
    chapters = _make_chapters(
        {
            1: ["planned"],
            2: ["planned"],
        }
    )
    frontier, fully, in_prog, unwritten = compute_frontier_volume(
        chapters, volume_count=2
    )
    assert frontier == 1
    assert fully == set()
    assert unwritten == {1, 2}


def test_frontier_volume_with_no_chapters_is_unwritten() -> None:
    """A planned-but-unexpanded volume still counts against the frontier."""
    chapters = _make_chapters({1: ["complete"]})  # volume 2 has no chapters
    frontier, fully, in_prog, unwritten = compute_frontier_volume(
        chapters, volume_count=3
    )
    assert frontier == 2
    assert fully == {1}
    assert unwritten == {2, 3}


def test_canon_statuses_includes_revision() -> None:
    """Revision status counts as canon — the user already asked us not
    to retroactively flag chapters in that state."""
    assert "complete" in CANON_CHAPTER_STATUSES
    assert "revision" in CANON_CHAPTER_STATUSES


# ---------------------------------------------------------------------------
# Antagonist classification
# ---------------------------------------------------------------------------


def test_retired_antagonist_span_ends_before_frontier() -> None:
    chapters = _make_chapters(
        {1: ["complete"], 2: ["complete"], 3: ["planned"], 4: ["planned"]}
    )
    plans = [_antag("元婴老者", scope_volume_number=1)]
    report = build_forward_plan_report(
        project_slug="test",
        volume_count=4,
        chapters=chapters,
        antagonist_plans=plans,
    )
    assert report.frontier_volume == 3
    assert report.antagonist_summaries[0].status_vs_frontier == "retired"


def test_carries_forward_when_span_crosses_frontier() -> None:
    chapters = _make_chapters(
        {1: ["complete"], 2: ["complete"], 3: ["planned"]}
    )
    plans = [
        _antag("蛰伏者", stages_of_relevance=[[1, 3]], line_role="undercurrent"),
    ]
    report = build_forward_plan_report(
        project_slug="test",
        volume_count=3,
        chapters=chapters,
        antagonist_plans=plans,
    )
    assert report.antagonist_summaries[0].status_vs_frontier == "carries_forward"


def test_fully_forward_when_span_starts_at_frontier() -> None:
    chapters = _make_chapters(
        {1: ["complete"], 2: ["planned"], 3: ["planned"]}
    )
    plans = [_antag("新敌", stages_of_relevance=[[2, 3]])]
    report = build_forward_plan_report(
        project_slug="test",
        volume_count=3,
        chapters=chapters,
        antagonist_plans=plans,
    )
    assert report.antagonist_summaries[0].status_vs_frontier == "fully_forward"


def test_book_wide_antagonist_when_no_span_info() -> None:
    chapters = _make_chapters({1: ["complete"], 2: ["planned"]})
    plans = [_antag("终极幕后")]  # no scope, no stages
    report = build_forward_plan_report(
        project_slug="test",
        volume_count=2,
        chapters=chapters,
        antagonist_plans=plans,
    )
    assert report.antagonist_summaries[0].status_vs_frontier == "book_wide"


# ---------------------------------------------------------------------------
# Per-volume coverage
# ---------------------------------------------------------------------------


def test_coverage_reports_only_forward_volumes() -> None:
    chapters = _make_chapters(
        {1: ["complete"], 2: ["complete"], 3: ["planned"], 4: ["planned"]}
    )
    plans = [
        _antag("秦王", scope_volume_number=3),
        _antag("陆骁", scope_volume_number=4),
    ]
    report = build_forward_plan_report(
        project_slug="test",
        volume_count=4,
        chapters=chapters,
        antagonist_plans=plans,
    )
    # Only volumes 3-4 should appear in coverage (1-2 are canon).
    vols = [c.volume_number for c in report.coverage_by_volume]
    assert vols == [3, 4]
    # Each forward volume has its respective overt.
    cov_map = {c.volume_number: c for c in report.coverage_by_volume}
    assert cov_map[3].overt_antagonists == ("秦王",)
    assert cov_map[4].overt_antagonists == ("陆骁",)


def test_uncovered_forward_volume_emits_critical_recommendation() -> None:
    chapters = _make_chapters(
        {1: ["complete"], 2: ["planned"], 3: ["planned"]}
    )
    plans = [_antag("只在V2的敌人", scope_volume_number=2)]
    report = build_forward_plan_report(
        project_slug="test",
        volume_count=3,
        chapters=chapters,
        antagonist_plans=plans,
    )
    codes = [r.code for r in report.recommendations if r.severity == "critical"]
    assert "forward_volume_missing_overt_antagonist" in codes
    # Should flag volume 3 specifically
    flagged = [
        r for r in report.recommendations
        if r.code == "forward_volume_missing_overt_antagonist"
    ]
    assert any(r.volume_number == 3 for r in flagged)


# ---------------------------------------------------------------------------
# Resolution distribution (forward-only)
# ---------------------------------------------------------------------------


def test_forward_monotonous_resolution_fires_warning() -> None:
    chapters = _make_chapters(
        {1: ["complete"], 2: ["planned"], 3: ["planned"], 4: ["planned"]}
    )
    # Every forward antagonist is killed → monotonous.
    plans = [
        _antag("秦王爷", scope_volume_number=2, resolution_type="defeated_and_killed"),
        _antag("陆铁心", scope_volume_number=3, resolution_type="defeated_and_killed"),
        _antag("玄阴老祖", scope_volume_number=4, resolution_type="defeated_and_killed"),
    ]
    report = build_forward_plan_report(
        project_slug="test",
        volume_count=4,
        chapters=chapters,
        antagonist_plans=plans,
    )
    codes = [r.code for r in report.recommendations]
    assert "forward_monotonous_resolution_types" in codes
    assert "forward_lacks_non_killed_outcomes" in codes


def test_forward_resolution_diversity_does_not_fire_warning() -> None:
    chapters = _make_chapters(
        {1: ["complete"], 2: ["planned"], 3: ["planned"], 4: ["planned"]}
    )
    plans = [
        _antag("秦王爷", scope_volume_number=2, resolution_type="defeated_and_killed"),
        _antag("陆铁心", scope_volume_number=3, resolution_type="transformed_to_ally"),
        _antag("玄阴老祖", scope_volume_number=4, resolution_type="outlived"),
    ]
    report = build_forward_plan_report(
        project_slug="test",
        volume_count=4,
        chapters=chapters,
        antagonist_plans=plans,
    )
    codes = [r.code for r in report.recommendations]
    assert "forward_monotonous_resolution_types" not in codes
    assert "forward_lacks_non_killed_outcomes" not in codes


def test_retired_antagonists_excluded_from_forward_distribution() -> None:
    """A retired (pre-frontier) antagonist should not pollute forward-only
    resolution counts."""
    chapters = _make_chapters(
        {1: ["complete"], 2: ["planned"], 3: ["planned"]}
    )
    plans = [
        # Retired — should NOT count
        _antag("旧敌人", scope_volume_number=1, resolution_type="defeated_and_killed"),
        # Forward — should count
        _antag("新敌人", scope_volume_number=2, resolution_type="outlived"),
    ]
    report = build_forward_plan_report(
        project_slug="test",
        volume_count=3,
        chapters=chapters,
        antagonist_plans=plans,
    )
    assert report.resolution_distribution_forward == {"outlived": 1}


# ---------------------------------------------------------------------------
# Rotation-collapse rule
# ---------------------------------------------------------------------------


def test_single_overt_across_all_forward_emits_warning() -> None:
    chapters = _make_chapters(
        {1: ["complete"], 2: ["planned"], 3: ["planned"], 4: ["planned"]}
    )
    plans = [
        _antag("老大哥", stages_of_relevance=[[2, 4]]),
    ]
    report = build_forward_plan_report(
        project_slug="test",
        volume_count=4,
        chapters=chapters,
        antagonist_plans=plans,
    )
    codes = [r.code for r in report.recommendations]
    assert "forward_single_overt_across_all_remaining" in codes


def test_rotation_of_two_overts_does_not_fire() -> None:
    chapters = _make_chapters(
        {1: ["complete"], 2: ["planned"], 3: ["planned"], 4: ["planned"]}
    )
    plans = [
        _antag("秦王爷", scope_volume_number=2),
        _antag("陆铁心", stages_of_relevance=[[3, 4]]),
    ]
    report = build_forward_plan_report(
        project_slug="test",
        volume_count=4,
        chapters=chapters,
        antagonist_plans=plans,
    )
    codes = [r.code for r in report.recommendations]
    assert "forward_single_overt_across_all_remaining" not in codes


def test_ghost_antagonist_alongside_rotating_ones_fires_warning() -> None:
    """The exact 道种破虚 failure mode — one antagonist is present in
    every forward volume while a rotating cast cycles underneath."""
    chapters = _make_chapters(
        {
            1: ["complete"], 2: ["complete"],
            3: ["planned"], 4: ["planned"], 5: ["planned"], 6: ["planned"],
        }
    )
    plans = [
        # Ghost: spans every forward volume
        _antag("元婴老者", stages_of_relevance=[[3, 6]]),
        # Rotating cast
        _antag("萧无咎", scope_volume_number=3),
        _antag("魂渊意志", scope_volume_number=4),
        _antag("宁雪", scope_volume_number=5),
        _antag("赤焚", scope_volume_number=6),
    ]
    report = build_forward_plan_report(
        project_slug="test",
        volume_count=6,
        chapters=chapters,
        antagonist_plans=plans,
    )
    codes = [r.code for r in report.recommendations]
    assert "forward_book_wide_overt_antagonist" in codes
    ghost_rec = [
        r for r in report.recommendations
        if r.code == "forward_book_wide_overt_antagonist"
    ][0]
    assert "元婴老者" in ghost_rec.payload.get("antagonists", [])


def test_no_ghost_when_every_antagonist_is_scoped_to_one_volume() -> None:
    chapters = _make_chapters(
        {1: ["complete"], 2: ["planned"], 3: ["planned"], 4: ["planned"]}
    )
    plans = [
        _antag("秦王爷", scope_volume_number=2),
        _antag("陆铁心", scope_volume_number=3),
        _antag("玄阴老祖", scope_volume_number=4),
    ]
    report = build_forward_plan_report(
        project_slug="test",
        volume_count=4,
        chapters=chapters,
        antagonist_plans=plans,
    )
    codes = [r.code for r in report.recommendations]
    assert "forward_book_wide_overt_antagonist" not in codes


# ---------------------------------------------------------------------------
# No-forward-work fast path
# ---------------------------------------------------------------------------


def test_no_forward_work_returns_info_only() -> None:
    chapters = _make_chapters(
        {1: ["complete"], 2: ["complete"]}
    )
    plans = [_antag("秦王爷", scope_volume_number=1)]
    report = build_forward_plan_report(
        project_slug="done-book",
        volume_count=2,
        chapters=chapters,
        antagonist_plans=plans,
    )
    assert report.forward_volumes == ()
    codes = [r.code for r in report.recommendations]
    assert codes == ["no_forward_work"]
    assert report.critical_count == 0


def test_no_forward_active_antagonists_fires_critical() -> None:
    chapters = _make_chapters(
        {1: ["complete"], 2: ["planned"]}
    )
    plans = [
        _antag("仅V1的敌人", scope_volume_number=1),  # retired
    ]
    report = build_forward_plan_report(
        project_slug="test",
        volume_count=2,
        chapters=chapters,
        antagonist_plans=plans,
    )
    codes = [r.code for r in report.recommendations]
    assert "no_forward_active_antagonists" in codes


# ---------------------------------------------------------------------------
# Output language
# ---------------------------------------------------------------------------


def test_recommendations_are_english_when_requested() -> None:
    chapters = _make_chapters(
        {1: ["complete"], 2: ["planned"], 3: ["planned"]}
    )
    plans = [_antag("Lu Xiao", scope_volume_number=2)]
    report = build_forward_plan_report(
        project_slug="test",
        volume_count=3,
        chapters=chapters,
        antagonist_plans=plans,
        language="en-US",
    )
    # The scope-summary info rec should use English phrasing.
    info = [
        r for r in report.recommendations
        if r.code == "forward_scope_summary"
    ][0]
    assert "Forward scope" in info.message


def test_recommendations_are_chinese_by_default() -> None:
    chapters = _make_chapters(
        {1: ["complete"], 2: ["planned"], 3: ["planned"]}
    )
    plans = [_antag("陆骁", scope_volume_number=2)]
    report = build_forward_plan_report(
        project_slug="test",
        volume_count=3,
        chapters=chapters,
        antagonist_plans=plans,
    )
    info = [
        r for r in report.recommendations
        if r.code == "forward_scope_summary"
    ][0]
    assert "前瞻" in info.message


# ---------------------------------------------------------------------------
# Report structure
# ---------------------------------------------------------------------------


def test_report_is_immutable() -> None:
    chapters = _make_chapters({1: ["complete"], 2: ["planned"]})
    plans = [_antag("秦王爷", scope_volume_number=2)]
    report = build_forward_plan_report(
        project_slug="test",
        volume_count=2,
        chapters=chapters,
        antagonist_plans=plans,
    )
    with pytest.raises(Exception):  # FrozenInstanceError
        report.frontier_volume = 99  # type: ignore[misc]


def test_report_exposes_written_and_unwritten_sets() -> None:
    chapters = _make_chapters(
        {1: ["complete"], 2: ["complete", "drafting"], 3: ["planned"]}
    )
    plans = [_antag("秦王爷", scope_volume_number=3)]
    report = build_forward_plan_report(
        project_slug="test",
        volume_count=3,
        chapters=chapters,
        antagonist_plans=plans,
    )
    assert report.fully_written_volumes == (1,)
    assert report.in_progress_volumes == (2,)
    assert report.unwritten_volumes == (3,)


def test_generic_antagonist_names_are_dropped() -> None:
    """Names on the generic denylist must not pollute the forward view."""
    chapters = _make_chapters({1: ["complete"], 2: ["planned"]})
    plans = [
        _antag("敌人", scope_volume_number=2),  # denylisted
        _antag("有名有姓者", scope_volume_number=2),  # kept
    ]
    report = build_forward_plan_report(
        project_slug="test",
        volume_count=2,
        chapters=chapters,
        antagonist_plans=plans,
    )
    names = [s.name for s in report.antagonist_summaries]
    assert "敌人" not in names
    assert "有名有姓者" in names
