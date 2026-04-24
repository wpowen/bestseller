"""Unit tests for Phase A1 unified checker schema."""

from __future__ import annotations

import pytest

from bestseller.services.checker_schema import (
    CheckerIssue,
    CheckerReport,
    aggregate_issue_counts,
    blocked_chapters,
    merge_reports,
    partition_by_chapter,
)


# ---------------------------------------------------------------------------
# CheckerIssue
# ---------------------------------------------------------------------------


class TestCheckerIssue:
    def test_round_trip_dict(self) -> None:
        issue = CheckerIssue(
            id="HARD_001",
            type="bible_gate",
            severity="critical",
            location="ch1",
            description="Bible missing theme",
            suggestion="Add theme_statement",
            can_override=False,
            allowed_rationales=(),
        )
        restored = CheckerIssue.from_dict(issue.to_dict())
        assert restored == issue

    def test_round_trip_with_rationales(self) -> None:
        issue = CheckerIssue(
            id="SOFT_LINE_GAP",
            type="pacing",
            severity="high",
            location="ch7",
            description="Overt line gap 6 exceeds 5",
            suggestion="Make ch8 overt-dominant",
            can_override=True,
            allowed_rationales=("ARC_TIMING", "GENRE_CONVENTION"),
        )
        restored = CheckerIssue.from_dict(issue.to_dict())
        assert restored == issue
        assert restored.allowed_rationales == ("ARC_TIMING", "GENRE_CONVENTION")

    def test_invalid_severity_falls_back_to_medium(self) -> None:
        issue = CheckerIssue.from_dict(
            {
                "id": "X",
                "type": "t",
                "severity": "EXTREME",  # invalid
                "location": "",
                "description": "",
                "suggestion": "",
                "can_override": False,
            }
        )
        assert issue.severity == "medium"


# ---------------------------------------------------------------------------
# CheckerReport
# ---------------------------------------------------------------------------


class TestCheckerReport:
    def _hard(self, cid: str = "H1") -> CheckerIssue:
        return CheckerIssue(
            id=cid,
            type="bible_gate",
            severity="critical",
            location="ch1",
            description="hard",
            suggestion="fix",
            can_override=False,
        )

    def _soft(self, cid: str = "S1") -> CheckerIssue:
        return CheckerIssue(
            id=cid,
            type="pacing",
            severity="medium",
            location="ch1",
            description="soft",
            suggestion="consider",
            can_override=True,
            allowed_rationales=("ARC_TIMING",),
        )

    def test_auto_partition_hard_soft(self) -> None:
        report = CheckerReport(
            agent="test",
            chapter=1,
            overall_score=70,
            passed=False,
            issues=(self._hard("H1"), self._soft("S1"), self._hard("H2")),
        )
        assert len(report.hard_violations) == 2
        assert len(report.soft_suggestions) == 1
        assert report.hard_violations[0].id == "H1"
        assert report.soft_suggestions[0].id == "S1"

    def test_explicit_partition_wins(self) -> None:
        """If caller pre-sets hard/soft, __post_init__ leaves them alone."""

        preset = (self._hard(),)
        report = CheckerReport(
            agent="test",
            chapter=1,
            overall_score=50,
            passed=False,
            issues=(self._hard(), self._soft()),
            hard_violations=preset,
        )
        assert report.hard_violations is preset

    def test_blocks_write_on_hard(self) -> None:
        r = CheckerReport(
            agent="a",
            chapter=1,
            overall_score=50,
            passed=False,
            issues=(self._hard(),),
        )
        assert r.blocks_write is True
        assert r.has_hard_violations is True

    def test_blocks_write_on_critical_even_if_soft(self) -> None:
        critical_soft = CheckerIssue(
            id="X",
            type="t",
            severity="critical",
            location="",
            description="",
            suggestion="",
            can_override=True,
        )
        r = CheckerReport(
            agent="a",
            chapter=1,
            overall_score=10,
            passed=False,
            issues=(critical_soft,),
        )
        assert r.blocks_write is True
        assert r.has_hard_violations is False

    def test_no_block_on_medium_soft(self) -> None:
        r = CheckerReport(
            agent="a",
            chapter=1,
            overall_score=80,
            passed=True,
            issues=(self._soft(),),
        )
        assert r.blocks_write is False

    def test_json_round_trip(self) -> None:
        r = CheckerReport(
            agent="bible-gate",
            chapter=2,
            overall_score=85,
            passed=True,
            issues=(self._soft("S1"), self._soft("S2")),
            metrics={"deficiency_count": 0, "chars": 3500},
            summary="ok",
        )
        restored = CheckerReport.from_json(r.to_json())
        assert restored.agent == r.agent
        assert restored.chapter == r.chapter
        assert restored.overall_score == r.overall_score
        assert restored.summary == r.summary
        assert dict(restored.metrics) == dict(r.metrics)
        assert len(restored.issues) == 2

    def test_empty_report(self) -> None:
        r = CheckerReport(agent="a", chapter=1, overall_score=100, passed=True)
        assert r.issues == ()
        assert r.hard_violations == ()
        assert r.soft_suggestions == ()
        assert r.blocks_write is False


# ---------------------------------------------------------------------------
# Aggregation helpers
# ---------------------------------------------------------------------------


class TestAggregation:
    def _make(self, agent: str, chapter: int, issues: tuple[CheckerIssue, ...]) -> CheckerReport:
        return CheckerReport(
            agent=agent,
            chapter=chapter,
            overall_score=50,
            passed=False,
            issues=issues,
        )

    def test_merge_rejects_unknown_types(self) -> None:
        with pytest.raises(TypeError):
            merge_reports([42])  # type: ignore[list-item]

    def test_merge_accepts_dicts(self) -> None:
        d = {
            "agent": "a",
            "chapter": 1,
            "overall_score": 90,
            "passed": True,
            "issues": [],
            "metrics": {},
            "summary": "",
            "hard_violations": [],
            "soft_suggestions": [],
        }
        out = merge_reports([d])
        assert len(out) == 1
        assert out[0].agent == "a"

    def test_aggregate_issue_counts(self) -> None:
        hard = CheckerIssue(
            id="H1",
            type="t",
            severity="critical",
            location="",
            description="",
            suggestion="",
            can_override=False,
        )
        reports = (
            self._make("a", 1, (hard,)),
            self._make("a", 2, (hard,)),
            self._make("b", 3, (hard,)),
        )
        counts = aggregate_issue_counts(reports)
        assert counts == {"H1": 3}

    def test_partition_by_chapter(self) -> None:
        reports = (
            self._make("a", 1, ()),
            self._make("b", 1, ()),
            self._make("a", 2, ()),
        )
        bucket = partition_by_chapter(reports)
        assert set(bucket.keys()) == {1, 2}
        assert len(bucket[1]) == 2
        assert len(bucket[2]) == 1

    def test_blocked_chapters(self) -> None:
        hard = CheckerIssue(
            id="H1",
            type="t",
            severity="critical",
            location="",
            description="",
            suggestion="",
            can_override=False,
        )
        soft = CheckerIssue(
            id="S1",
            type="t",
            severity="medium",
            location="",
            description="",
            suggestion="",
            can_override=True,
        )
        reports = (
            self._make("a", 1, (hard,)),
            self._make("a", 2, (soft,)),
            self._make("a", 3, ()),
        )
        blocked = blocked_chapters(reports)
        assert blocked == frozenset({1})


# ---------------------------------------------------------------------------
# Adapter smoke tests
# ---------------------------------------------------------------------------


def test_bible_deficiency_adapter() -> None:
    from bestseller.services.bible_gate import BibleDeficiency

    d = BibleDeficiency(
        code="CHARACTER_IP_ANCHOR_MISSING",
        location="主角·李小明",
        detail="缺少 core_wound",
        prompt_feedback="补齐 core_wound 字段：一句话说明心理创伤",
    )
    issue = d.as_checker_issue()
    assert issue.id == "CHARACTER_IP_ANCHOR_MISSING"
    assert issue.can_override is False
    assert issue.severity == "high"


def test_bible_completeness_report_adapter() -> None:
    from bestseller.services.bible_gate import (
        BibleCompletenessReport,
        BibleDeficiency,
    )

    deficiencies = (
        BibleDeficiency(code="A", location="l1", detail="d1", prompt_feedback="f1"),
        BibleDeficiency(code="B", location="l2", detail="d2", prompt_feedback="f2"),
    )
    report = BibleCompletenessReport(deficiencies=deficiencies)
    cr = report.as_checker_report(chapter=0)
    assert cr.agent == "bible-gate"
    assert cr.passed is False
    assert cr.overall_score == 80  # 100 - 2*10
    assert len(cr.hard_violations) == 2
    assert len(cr.soft_suggestions) == 0


def test_violation_adapter_block_is_hard() -> None:
    from bestseller.services.output_validator import Violation

    v = Violation(
        code="LANG_LEAK_CJK_IN_EN",
        severity="block",
        location="ch3",
        detail="CJK ratio 5%",
        prompt_feedback="Remove all CJK glyphs",
    )
    issue = v.as_checker_issue()
    assert issue.severity == "critical"
    assert issue.can_override is False


def test_violation_adapter_warn_is_soft() -> None:
    from bestseller.services.output_validator import Violation

    v = Violation(
        code="NAMING_INCONSISTENCY",
        severity="warn",
        location="ch3",
        detail="unknown name",
        prompt_feedback="...",
    )
    issue = v.as_checker_issue()
    assert issue.severity == "medium"
    assert issue.can_override is True


def test_quality_report_adapter() -> None:
    from bestseller.services.output_validator import QualityReport, Violation

    violations = (
        Violation(code="A", severity="block", location="", detail="", prompt_feedback=""),
        Violation(code="B", severity="warn", location="", detail="", prompt_feedback=""),
    )
    qr = QualityReport(violations=violations)
    cr = qr.as_checker_report(chapter=5)
    assert cr.chapter == 5
    assert cr.passed is False
    assert cr.metrics["block_count"] == 1
    assert cr.metrics["warn_count"] == 1
    assert cr.overall_score == 75  # 100 - 20 - 5


def test_pacing_checker_report_adapter() -> None:
    from bestseller.services.pacing_engine import build_pacing_checker_report

    r = build_pacing_checker_report(
        chapter=5,
        tension_score=4.0,
        target_tension=7.5,
        hook_diversity={"forbid": ["crisis"], "suggested": ["twist"]},
    )
    assert r.agent == "pacing-engine"
    assert r.chapter == 5
    assert any(i.id == "SOFT_TENSION_OFF_TARGET" for i in r.issues)
    assert any(i.id == "SOFT_HOOK_REPEAT" for i in r.issues)
    assert all(i.can_override for i in r.issues)


def test_hype_checker_report_adapter() -> None:
    from bestseller.services.hype_engine import build_hype_checker_report

    r = build_hype_checker_report(
        chapter=2,
        observed_intensity=4.0,
        target_intensity=7.5,
        observed_count=0,
        min_count=2,
        missing_types=("face_slap",),
        golden_three_weak=True,
    )
    assert r.agent == "hype-engine"
    ids = {i.id for i in r.issues}
    assert "SOFT_HYPE_DENSITY_LOW" in ids
    assert "SOFT_HYPE_INTENSITY_LOW" in ids
    assert "SOFT_HYPE_TYPE_MISSING" in ids
    assert "SOFT_GOLDEN_THREE_WEAK" in ids
    # All hype issues are soft
    assert all(i.can_override for i in r.issues)
