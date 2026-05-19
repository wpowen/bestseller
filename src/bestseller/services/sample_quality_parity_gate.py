from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class SampleQualityParityThresholds:
    min_chapter_count: int = 30
    min_review_overall_score: float = 0.82
    min_scorecard_quality_score: float = 80.0
    min_reference_distance: float = 0.72
    require_final_verdict_pass: bool = True
    require_no_llm_fallback: bool = True
    require_export: bool = True
    require_whole_book_gate_passed: bool = True
    require_category_hard_engine_passed: bool = True

    def to_dict(self) -> dict[str, object]:
        return {
            "min_chapter_count": self.min_chapter_count,
            "min_review_overall_score": self.min_review_overall_score,
            "min_scorecard_quality_score": self.min_scorecard_quality_score,
            "min_reference_distance": self.min_reference_distance,
            "require_final_verdict_pass": self.require_final_verdict_pass,
            "require_no_llm_fallback": self.require_no_llm_fallback,
            "require_export": self.require_export,
            "require_whole_book_gate_passed": self.require_whole_book_gate_passed,
            "require_category_hard_engine_passed": self.require_category_hard_engine_passed,
        }


@dataclass(frozen=True, slots=True)
class SampleQualityParityFinding:
    code: str
    severity: str
    message: str
    path: str
    expected: object | None = None
    actual: object | None = None

    def to_dict(self) -> dict[str, object | None]:
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "path": self.path,
            "expected": self.expected,
            "actual": self.actual,
        }


@dataclass(frozen=True, slots=True)
class SampleQualityParityReport:
    passed: bool
    readiness_level: str
    findings: tuple[SampleQualityParityFinding, ...] = field(default_factory=tuple)
    metrics: Mapping[str, object] = field(default_factory=dict)
    thresholds: SampleQualityParityThresholds = field(
        default_factory=SampleQualityParityThresholds
    )

    def to_dict(self) -> dict[str, object]:
        return {
            "passed": self.passed,
            "readiness_level": self.readiness_level,
            "findings": [finding.to_dict() for finding in self.findings],
            "metrics": dict(self.metrics),
            "thresholds": self.thresholds.to_dict(),
        }


def _as_mapping(value: object | None) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    if hasattr(value, "model_dump"):
        dumped = value.model_dump(mode="json")
        return dumped if isinstance(dumped, Mapping) else {}
    if hasattr(value, "to_dict"):
        dumped = value.to_dict()
        return dumped if isinstance(dumped, Mapping) else {}
    return {}


def _float_or_none(value: object | None) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _is_exported(status: str) -> bool:
    return status.startswith("exported") or status in {"ready", "completed", "success"}


def _report_passed(report: Mapping[str, Any]) -> bool | None:
    if "passed" in report:
        return report.get("passed") is True
    status = str(report.get("status") or "").strip().lower()
    if status in {"passed", "pass", "ready", "success"}:
        return True
    if status in {"failed", "fail", "blocked", "needs_attention"}:
        return False
    return None


def _category_hard_engine_passed(premium_gate_report: Mapping[str, Any]) -> bool | None:
    capability = _as_mapping(premium_gate_report.get("capability_snapshot"))
    hard_engine = _as_mapping(capability.get("category_hard_engine"))
    if hard_engine:
        return hard_engine.get("passed") is True
    hard_engine = _as_mapping(premium_gate_report.get("category_hard_engine"))
    if hard_engine:
        return hard_engine.get("passed") is True
    return None


def _readiness_level(findings: list[SampleQualityParityFinding]) -> str:
    if not findings:
        return "ready"
    if any(finding.severity == "critical" for finding in findings):
        return "blocked"
    return "partial"


def evaluate_sample_quality_parity(
    *,
    category_key: str,
    chapter_count: int,
    final_verdict: str | None,
    review_overall_score: float | None,
    scorecard_quality_score: float | None,
    whole_book_quality_report: Mapping[str, Any] | None,
    premium_gate_report: Mapping[str, Any] | None,
    reference_distance_score: float | None,
    fallback_count: int,
    export_status: str,
    thresholds: SampleQualityParityThresholds | None = None,
) -> SampleQualityParityReport:
    """Evaluate whether a generated pilot can be claimed as sample-level quality.

    The gate is deliberately stricter than the normal pilot checks: it requires
    a finished long pilot, project-level quality evidence, category hard-engine
    evidence, and an anti-copy/reference-distance signal.
    """

    thresholds = thresholds or SampleQualityParityThresholds()
    findings: list[SampleQualityParityFinding] = []
    review_score = _float_or_none(review_overall_score)
    scorecard_score = _float_or_none(scorecard_quality_score)
    reference_distance = _float_or_none(reference_distance_score)
    whole_book_report = _as_mapping(whole_book_quality_report)
    premium_report = _as_mapping(premium_gate_report)

    if chapter_count < thresholds.min_chapter_count:
        findings.append(
            SampleQualityParityFinding(
                code="pilot_chapter_count_below_parity",
                severity="critical",
                message="30-chapter pilot has not reached the sample-parity length bar.",
                path="pilot.chapter_count",
                expected=f">={thresholds.min_chapter_count}",
                actual=chapter_count,
            )
        )
    if thresholds.require_final_verdict_pass and (final_verdict or "") != "pass":
        findings.append(
            SampleQualityParityFinding(
                code="final_verdict_not_pass",
                severity="high",
                message="Sample-level claim requires a clean project review pass.",
                path="pilot.final_verdict",
                expected="pass",
                actual=final_verdict,
            )
        )
    if review_score is None:
        findings.append(
            SampleQualityParityFinding(
                code="review_score_missing",
                severity="critical",
                message="Project review score is required for sample-quality parity.",
                path="review.scores.overall",
                expected=f">={thresholds.min_review_overall_score}",
                actual=None,
            )
        )
    elif review_score < thresholds.min_review_overall_score:
        findings.append(
            SampleQualityParityFinding(
                code="review_score_below_parity",
                severity="high",
                message="Project review score is below the sample-quality parity bar.",
                path="review.scores.overall",
                expected=f">={thresholds.min_review_overall_score}",
                actual=review_score,
            )
        )
    if scorecard_score is None:
        findings.append(
            SampleQualityParityFinding(
                code="scorecard_missing",
                severity="critical",
                message="Scorecard quality score is required for sample-quality parity.",
                path="scorecard.quality_score",
                expected=f">={thresholds.min_scorecard_quality_score}",
                actual=None,
            )
        )
    elif scorecard_score < thresholds.min_scorecard_quality_score:
        findings.append(
            SampleQualityParityFinding(
                code="scorecard_below_parity",
                severity="high",
                message="Scorecard quality score is below the sample-quality parity bar.",
                path="scorecard.quality_score",
                expected=f">={thresholds.min_scorecard_quality_score}",
                actual=scorecard_score,
            )
        )
    if thresholds.require_whole_book_gate_passed:
        whole_book_passed = _report_passed(whole_book_report)
        if whole_book_passed is not True:
            findings.append(
                SampleQualityParityFinding(
                    code="whole_book_gate_not_passed",
                    severity="critical",
                    message="Whole-book quality gate must pass before claiming sample parity.",
                    path="whole_book_quality_report.passed",
                    expected=True,
                    actual=whole_book_passed,
                )
            )
    if thresholds.require_category_hard_engine_passed:
        category_engine_passed = _category_hard_engine_passed(premium_report)
        if category_engine_passed is not True:
            findings.append(
                SampleQualityParityFinding(
                    code="category_hard_engine_not_passed",
                    severity="critical",
                    message="Category hard engine must pass before claiming sample parity.",
                    path="premium_book_gate_report.capability_snapshot.category_hard_engine.passed",
                    expected=True,
                    actual=category_engine_passed,
                )
            )
    if reference_distance is None:
        findings.append(
            SampleQualityParityFinding(
                code="reference_distance_missing",
                severity="critical",
                message="Reference-distance evidence is required to prove abstraction without copying.",
                path="anti_copy.reference_distance_score",
                expected=f">={thresholds.min_reference_distance}",
                actual=None,
            )
        )
    elif reference_distance < thresholds.min_reference_distance:
        findings.append(
            SampleQualityParityFinding(
                code="reference_distance_too_low",
                severity="critical",
                message="Generated work is too close to benchmark references for a safe parity claim.",
                path="anti_copy.reference_distance_score",
                expected=f">={thresholds.min_reference_distance}",
                actual=reference_distance,
            )
        )
    if thresholds.require_no_llm_fallback and fallback_count > 0:
        findings.append(
            SampleQualityParityFinding(
                code="llm_fallback_used",
                severity="high",
                message="Model fallback polluted this parity run.",
                path="usage.fallback_count",
                expected=0,
                actual=fallback_count,
            )
        )
    if thresholds.require_export and not _is_exported(export_status):
        findings.append(
            SampleQualityParityFinding(
                code="export_missing",
                severity="critical",
                message="A complete export is required for sample-quality parity review.",
                path="pilot.export_status",
                expected="exported*",
                actual=export_status,
            )
        )

    readiness = _readiness_level(findings)
    return SampleQualityParityReport(
        passed=readiness == "ready",
        readiness_level=readiness,
        findings=tuple(findings),
        metrics={
            "category_key": category_key,
            "chapter_count": chapter_count,
            "final_verdict": final_verdict,
            "review_overall_score": review_score,
            "scorecard_quality_score": scorecard_score,
            "whole_book_gate_passed": _report_passed(whole_book_report),
            "category_hard_engine_passed": _category_hard_engine_passed(premium_report),
            "reference_distance_score": reference_distance,
            "fallback_count": fallback_count,
            "export_status": export_status,
        },
        thresholds=thresholds,
    )


__all__ = [
    "SampleQualityParityFinding",
    "SampleQualityParityReport",
    "SampleQualityParityThresholds",
    "evaluate_sample_quality_parity",
]
