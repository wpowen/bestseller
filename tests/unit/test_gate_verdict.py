from __future__ import annotations

from bestseller.domain.gate_verdict import (
    AggregateGateReport,
    GateFinding,
    GateVerdict,
)


def test_gate_verdict_pass_requires_high_coverage_and_no_critical_findings() -> None:
    low_coverage = GateVerdict(
        gate_name="commercial_novel_gate",
        verdict="pass",
        coverage=0.94,
        metrics={"quality_score": 95},
    )
    critical = GateVerdict(
        gate_name="commercial_novel_gate",
        verdict="pass",
        coverage=1.0,
        findings=(
            GateFinding(
                code="CANON_LEAK",
                severity="critical",
                message="forbidden term leaked",
            ),
        ),
        metrics={"quality_score": 95},
    )
    clean = GateVerdict(
        gate_name="commercial_novel_gate",
        verdict="pass",
        coverage=0.95,
        metrics={"quality_score": 95},
    )

    assert low_coverage.verdict == "warn_only"
    assert low_coverage.passed is False
    assert critical.passed is False
    assert clean.passed is True


def test_gate_verdict_demotes_quality_score_below_contract_floor() -> None:
    verdict = GateVerdict(
        gate_name="commercial_novel_gate",
        verdict="pass",
        coverage=1.0,
        metrics={"quality_score": 69.9},
    )

    assert verdict.verdict == "warn_only"
    assert verdict.passed is False


def test_aggregate_report_passes_only_when_required_gates_pass() -> None:
    report = AggregateGateReport(
        gate_name="lifecycle-quality",
        gates=(
            GateVerdict(gate_name="package-integrity", verdict="pass", coverage=1.0),
            GateVerdict(
                gate_name="narrative-richness-audit",
                verdict="not_run",
                coverage=0.0,
                required=False,
            ),
        ),
    )

    assert report.verdict == "pass"
    assert report.readiness == "not_blocked"
    assert report.passed is True


def test_aggregate_report_blocks_on_required_critical_gate() -> None:
    report = AggregateGateReport(
        gate_name="lifecycle-quality",
        gates=(
            GateVerdict(gate_name="package-integrity", verdict="pass", coverage=1.0),
            GateVerdict(
                gate_name="commercial_novel_gate",
                verdict="blocked",
                coverage=0.8,
                findings=(
                    GateFinding(
                        code="GENRE_DRIFT",
                        severity="critical",
                        message="drift",
                    ),
                ),
            ),
        ),
    )

    assert report.verdict == "blocked"
    assert report.readiness == "blocked"
    assert report.passed is False
    assert report.overall_score == 80
