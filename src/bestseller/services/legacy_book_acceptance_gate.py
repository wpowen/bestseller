from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class LegacyBookAcceptanceThresholds:
    min_scorecard_quality_score: float = 80.0
    max_missing_chapters: int = 0
    max_draftless_chapters: int = 0
    max_blocked_chapters: int = 0
    max_length_cv: float = 0.10
    max_hype_missing_chapters: int = 0
    require_premium_gate_passed: bool = True
    require_model_execution_ready: bool = True

    def to_dict(self) -> dict[str, object]:
        return {
            "min_scorecard_quality_score": self.min_scorecard_quality_score,
            "max_missing_chapters": self.max_missing_chapters,
            "max_draftless_chapters": self.max_draftless_chapters,
            "max_blocked_chapters": self.max_blocked_chapters,
            "max_length_cv": self.max_length_cv,
            "max_hype_missing_chapters": self.max_hype_missing_chapters,
            "require_premium_gate_passed": self.require_premium_gate_passed,
            "require_model_execution_ready": self.require_model_execution_ready,
        }


@dataclass(frozen=True, slots=True)
class LegacyBookAcceptanceFinding:
    code: str
    severity: str
    message: str
    path: str
    expected: object | None = None
    actual: object | None = None
    repair_action: str = ""

    def to_dict(self) -> dict[str, object | None]:
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "path": self.path,
            "expected": self.expected,
            "actual": self.actual,
            "repair_action": self.repair_action,
        }


@dataclass(frozen=True, slots=True)
class LegacyBookAcceptanceReport:
    passed: bool
    readiness_level: str
    findings: tuple[LegacyBookAcceptanceFinding, ...] = field(default_factory=tuple)
    metrics: Mapping[str, object] = field(default_factory=dict)
    thresholds: LegacyBookAcceptanceThresholds = field(
        default_factory=LegacyBookAcceptanceThresholds
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
    if hasattr(value, "to_dict"):
        dumped = value.to_dict()
        return dumped if isinstance(dumped, Mapping) else {}
    if hasattr(value, "model_dump"):
        dumped = value.model_dump(mode="json")
        return dumped if isinstance(dumped, Mapping) else {}
    return {}


def _float(value: object | None, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _int(value: object | None, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _repair_plan_task_count(repair_plan: Mapping[str, Any]) -> int:
    priority_counts = _as_mapping(repair_plan.get("priority_counts"))
    if priority_counts:
        return _int(priority_counts.get("critical")) + _int(priority_counts.get("high"))
    if "task_count" in repair_plan:
        return _int(repair_plan.get("task_count"))
    nested = _as_mapping(repair_plan.get("repair_plan"))
    nested_priority_counts = _as_mapping(nested.get("priority_counts"))
    if nested_priority_counts:
        return _int(nested_priority_counts.get("critical")) + _int(
            nested_priority_counts.get("high")
        )
    return _int(nested.get("task_count"))


def _readiness_level(findings: list[LegacyBookAcceptanceFinding]) -> str:
    if not findings:
        return "ready"
    if any(finding.severity == "critical" for finding in findings):
        return "blocked"
    return "repairable"


def evaluate_legacy_book_acceptance(
    *,
    scorecard: Mapping[str, Any] | object,
    premium_gate_report: Mapping[str, Any] | object,
    repair_plan: Mapping[str, Any] | None = None,
    model_execution_ready: bool = True,
    thresholds: LegacyBookAcceptanceThresholds | None = None,
) -> LegacyBookAcceptanceReport:
    """Evaluate a historical book against the current whole-book standard."""

    thresholds = thresholds or LegacyBookAcceptanceThresholds()
    scorecard_payload = _as_mapping(scorecard)
    premium_payload = _as_mapping(premium_gate_report)
    repair_payload = _as_mapping(repair_plan)
    findings: list[LegacyBookAcceptanceFinding] = []

    premium_passed = premium_payload.get("passed") is True
    if thresholds.require_premium_gate_passed and not premium_passed:
        findings.append(
            LegacyBookAcceptanceFinding(
                code="premium_gate_not_passed",
                severity="critical",
                message="Premium structural gate must pass before historical book acceptance.",
                path="premium_gate_report.passed",
                expected=True,
                actual=premium_payload.get("passed"),
                repair_action=(
                    "Run state bootstrap/category hard-engine repair, "
                    "then rerun premium gate."
                ),
            )
        )

    quality_score = _float(scorecard_payload.get("quality_score"))
    if quality_score < thresholds.min_scorecard_quality_score:
        findings.append(
            LegacyBookAcceptanceFinding(
                code="scorecard_below_acceptance_bar",
                severity="high",
                message="Scorecard quality score is below the current sample-quality bar.",
                path="scorecard.quality_score",
                expected=f">={thresholds.min_scorecard_quality_score}",
                actual=quality_score,
                repair_action="Execute high/critical chapter repairs and rerun scorecard.",
            )
        )

    missing = _int(scorecard_payload.get("missing_chapters"))
    if missing > thresholds.max_missing_chapters:
        findings.append(
            LegacyBookAcceptanceFinding(
                code="book_incomplete",
                severity="critical",
                message="Book has not materialized all target chapters.",
                path="scorecard.missing_chapters",
                expected=f"<={thresholds.max_missing_chapters}",
                actual=missing,
                repair_action=(
                    "Extend the outline and generate missing target chapters before claiming whole-book parity."
                ),
            )
        )

    draftless = _int(scorecard_payload.get("draftless_chapters"))
    if draftless > thresholds.max_draftless_chapters:
        findings.append(
            LegacyBookAcceptanceFinding(
                code="planned_chapters_without_current_drafts",
                severity="critical",
                message="Book has planned chapter rows without current prose drafts.",
                path="scorecard.draftless_chapters",
                expected=f"<={thresholds.max_draftless_chapters}",
                actual=draftless,
                repair_action=(
                    "Run the chapter generation pipeline for planned chapters without current drafts."
                ),
            )
        )

    blocked = _int(scorecard_payload.get("chapters_blocked"))
    if blocked > thresholds.max_blocked_chapters:
        findings.append(
            LegacyBookAcceptanceFinding(
                code="blocked_chapters_remaining",
                severity="high",
                message="One or more chapters still block quality acceptance.",
                path="scorecard.chapters_blocked",
                expected=f"<={thresholds.max_blocked_chapters}",
                actual=blocked,
                repair_action="Prioritize blocked chapters before lower-severity prose polish.",
            )
        )

    length_cv = _float(scorecard_payload.get("length_cv"))
    if length_cv > thresholds.max_length_cv:
        findings.append(
            LegacyBookAcceptanceFinding(
                code="length_stability_below_bar",
                severity="high",
                message="Chapter length variance is above the stability target.",
                path="scorecard.length_cv",
                expected=f"<={thresholds.max_length_cv}",
                actual=length_cv,
                repair_action="Normalize underlength/overlength chapters during repair batches.",
            )
        )

    hype_missing = _int(scorecard_payload.get("hype_missing_chapters"))
    if hype_missing > thresholds.max_hype_missing_chapters:
        findings.append(
            LegacyBookAcceptanceFinding(
                code="hype_assignments_missing",
                severity="high",
                message="Chapters are missing reader-hype assignments.",
                path="scorecard.hype_missing_chapters",
                expected=f"<={thresholds.max_hype_missing_chapters}",
                actual=hype_missing,
                repair_action=(
                    "Backfill hype type/intensity before judging reader-retention parity."
                ),
            )
        )

    repair_task_count = _repair_plan_task_count(repair_payload)
    if repair_task_count > 0:
        findings.append(
            LegacyBookAcceptanceFinding(
                code="repair_tasks_remaining",
                severity="high",
                message="Autonomous repair plan still contains actionable high/critical tasks.",
                path="repair_plan.task_count",
                expected=0,
                actual=repair_task_count,
                repair_action=(
                    "Execute the repair plan, then regenerate audits and acceptance report."
                ),
            )
        )

    if (
        thresholds.require_model_execution_ready
        and repair_task_count > 0
        and not model_execution_ready
    ):
        findings.append(
            LegacyBookAcceptanceFinding(
                code="model_execution_unavailable",
                severity="critical",
                message=(
                    "Automated repair cannot reach final prose quality while real "
                    "LLM execution is unavailable."
                ),
                path="runtime.llm_credentials",
                expected=True,
                actual=False,
                repair_action=(
                    "Provide a real model provider key or run the loop in an "
                    "environment with model access."
                ),
            )
        )

    metrics = {
        "premium_gate_passed": premium_passed,
        "quality_score": quality_score,
        "missing_chapters": missing,
        "draftless_chapters": draftless,
        "chapters_blocked": blocked,
        "length_cv": length_cv,
        "hype_missing_chapters": hype_missing,
        "repair_task_count": repair_task_count,
        "model_execution_ready": model_execution_ready,
    }
    readiness = _readiness_level(findings)
    return LegacyBookAcceptanceReport(
        passed=not findings,
        readiness_level=readiness,
        findings=tuple(findings),
        metrics=metrics,
        thresholds=thresholds,
    )


__all__ = [
    "LegacyBookAcceptanceFinding",
    "LegacyBookAcceptanceReport",
    "LegacyBookAcceptanceThresholds",
    "evaluate_legacy_book_acceptance",
]
