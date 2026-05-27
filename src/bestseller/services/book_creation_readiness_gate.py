from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from bestseller.domain.gate_verdict import AggregateGateReport, GateFinding, GateVerdict


@dataclass(frozen=True, slots=True)
class BookCreationReadinessThresholds:
    require_category_key: bool = True
    require_story_design_grammar_key: bool = True
    require_distilled_strategy_card: bool = True
    min_distilled_strategy_maturity_score: float = 0.30
    require_story_design_kernel_valid: bool = True
    require_volume_plan_passed: bool = True
    require_prewrite_passed: bool = True
    require_forward_state_passed: bool = True
    require_reveal_alignment_passed: bool = True

    def to_dict(self) -> dict[str, object]:
        return {
            "require_category_key": self.require_category_key,
            "require_story_design_grammar_key": self.require_story_design_grammar_key,
            "require_distilled_strategy_card": self.require_distilled_strategy_card,
            "min_distilled_strategy_maturity_score": (
                self.min_distilled_strategy_maturity_score
            ),
            "require_story_design_kernel_valid": self.require_story_design_kernel_valid,
            "require_volume_plan_passed": self.require_volume_plan_passed,
            "require_prewrite_passed": self.require_prewrite_passed,
            "require_forward_state_passed": self.require_forward_state_passed,
            "require_reveal_alignment_passed": self.require_reveal_alignment_passed,
        }


@dataclass(frozen=True, slots=True)
class BookCreationReadinessFinding:
    code: str
    severity: str
    domain: str
    message: str
    path: str
    expected: object | None = None
    actual: object | None = None
    repair_action: str = ""

    def to_dict(self) -> dict[str, object | None]:
        return {
            "code": self.code,
            "severity": self.severity,
            "domain": self.domain,
            "message": self.message,
            "path": self.path,
            "expected": self.expected,
            "actual": self.actual,
            "repair_action": self.repair_action,
        }


@dataclass(frozen=True, slots=True)
class BookCreationDomainStatus:
    domain: str
    passed: bool
    findings: tuple[BookCreationReadinessFinding, ...] = field(default_factory=tuple)
    metrics: Mapping[str, object] = field(default_factory=dict)

    @property
    def readiness_level(self) -> str:
        if not self.findings:
            return "ready"
        if any(finding.severity == "critical" for finding in self.findings):
            return "blocked"
        return "partial"

    @property
    def gate_verdict(self) -> GateVerdict:
        return GateVerdict(
            gate_name=self.domain,
            verdict="pass" if self.passed else "blocked",
            coverage=1.0 if self.passed else 0.0,
            findings=tuple(
                GateFinding(
                    code=finding.code,
                    severity=finding.severity,
                    message=finding.message,
                    path=finding.path,
                    repair_action=finding.repair_action,
                )
                for finding in self.findings
            ),
            metrics=dict(self.metrics),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "domain": self.domain,
            "passed": self.passed,
            "readiness_level": self.readiness_level,
            "findings": [finding.to_dict() for finding in self.findings],
            "metrics": dict(self.metrics),
        }


@dataclass(frozen=True, slots=True)
class BookCreationReadinessReport:
    slug: str
    domain_statuses: tuple[BookCreationDomainStatus, ...]
    findings: tuple[BookCreationReadinessFinding, ...]
    metrics: Mapping[str, object]
    thresholds: BookCreationReadinessThresholds = field(
        default_factory=BookCreationReadinessThresholds
    )

    @property
    def aggregate_gate_report(self) -> AggregateGateReport:
        return AggregateGateReport(
            gate_name="book-creation-readiness",
            gates=tuple(status.gate_verdict for status in self.domain_statuses),
        )

    @property
    def passed(self) -> bool:
        return self.aggregate_gate_report.passed

    @property
    def readiness_level(self) -> str:
        if not self.findings:
            return "ready"
        if any(finding.severity == "critical" for finding in self.findings):
            return "blocked"
        return "partial"

    def to_dict(self) -> dict[str, object]:
        return {
            "slug": self.slug,
            "passed": self.passed,
            "readiness_level": self.readiness_level,
            "aggregate_gate_report": self.aggregate_gate_report.model_dump(mode="json"),
            "domain_statuses": [
                domain_status.to_dict() for domain_status in self.domain_statuses
            ],
            "findings": [finding.to_dict() for finding in self.findings],
            "blocking_issue_codes": [
                finding.code for finding in self.findings if finding.severity == "critical"
            ],
            "metrics": dict(self.metrics),
            "thresholds": self.thresholds.to_dict(),
        }


def evaluate_book_creation_readiness(
    *,
    project_slug: str,
    project_metadata: Mapping[str, Any] | None,
    target_chapters: int,
    planned_chapters: int,
    story_design_kernel: Mapping[str, Any] | None = None,
    planning_kernel_report: Mapping[str, Any] | None = None,
    volume_plan_report: Mapping[str, Any] | None = None,
    prewrite_report: Mapping[str, Any] | None = None,
    forward_state_report: Mapping[str, Any] | None = None,
    reveal_alignment_report: Mapping[str, Any] | None = None,
    thresholds: BookCreationReadinessThresholds | None = None,
) -> BookCreationReadinessReport:
    thresholds = thresholds or BookCreationReadinessThresholds()
    metadata = _as_mapping(project_metadata)
    strict = _is_strict(metadata)

    project_findings = _evaluate_project_identity(
        metadata,
        strict=strict,
        thresholds=thresholds,
    )
    planning_findings = _evaluate_planning_assets(
        target_chapters=target_chapters,
        planned_chapters=planned_chapters,
        story_design_kernel=story_design_kernel,
        planning_kernel_report=planning_kernel_report,
        thresholds=thresholds,
    )
    outline_findings = _evaluate_outline_assets(
        volume_plan_report=volume_plan_report,
        prewrite_report=prewrite_report,
        forward_state_report=forward_state_report,
        reveal_alignment_report=reveal_alignment_report,
        thresholds=thresholds,
    )

    domain_statuses = (
        _domain_status(
            "project_identity",
            project_findings,
            {
                "strict": strict,
                "category_key": str(metadata.get("category_key") or ""),
                "story_design_grammar_key": str(
                    metadata.get("story_design_grammar_key") or ""
                ),
                "distilled_strategy_expected": _distilled_expected(metadata, strict),
            },
        ),
        _domain_status(
            "planning_assets",
            planning_findings,
            {
                "target_chapters": target_chapters,
                "planned_chapters": planned_chapters,
                "story_design_kernel_valid": _story_design_valid(
                    story_design_kernel, planning_kernel_report
                ),
                "planning_kernel_passed": _report_passed(planning_kernel_report),
            },
        ),
        _domain_status(
            "outline_assets",
            outline_findings,
            {
                "volume_plan_passed": _report_passed(volume_plan_report),
                "prewrite_passed": _report_passed(prewrite_report),
                "forward_state_passed": _report_passed(forward_state_report),
                "reveal_alignment_passed": _report_passed(reveal_alignment_report),
            },
        ),
    )
    findings = tuple(
        finding for status in domain_statuses for finding in status.findings
    )
    return BookCreationReadinessReport(
        slug=project_slug,
        domain_statuses=domain_statuses,
        findings=findings,
        metrics={
            "strict": strict,
            "target_chapters": target_chapters,
            "planned_chapters": planned_chapters,
            "category_key": str(metadata.get("category_key") or ""),
            "story_design_grammar_key": str(metadata.get("story_design_grammar_key") or ""),
        },
        thresholds=thresholds,
    )


def _evaluate_project_identity(
    metadata: Mapping[str, Any],
    *,
    strict: bool,
    thresholds: BookCreationReadinessThresholds,
) -> list[BookCreationReadinessFinding]:
    findings: list[BookCreationReadinessFinding] = []
    category_key = str(metadata.get("category_key") or "").strip()
    grammar_key = str(metadata.get("story_design_grammar_key") or "").strip()
    if strict and thresholds.require_category_key and not category_key:
        findings.append(
            _finding(
                code="category_key_missing",
                domain="project_identity",
                message="Strict projects require a resolved category_key before writing.",
                path="project.metadata.category_key",
                expected="non-empty category key",
                actual=category_key,
                repair_action="Resolve novel category and persist project metadata.",
            )
        )
    if strict and thresholds.require_story_design_grammar_key and not grammar_key:
        findings.append(
            _finding(
                code="story_design_grammar_key_missing",
                domain="project_identity",
                message=(
                    "Strict projects require a story design grammar key before writing."
                ),
                path="project.metadata.story_design_grammar_key",
                expected="non-empty grammar key",
                actual=grammar_key,
                repair_action="Resolve story design grammar and persist project metadata.",
            )
        )

    strategy_card = _as_mapping(metadata.get("distilled_strategy_card"))
    if (
        thresholds.require_distilled_strategy_card
        and _distilled_expected(metadata, strict)
        and not strategy_card
    ):
        findings.append(
            _finding(
                code="distilled_strategy_card_missing",
                domain="project_identity",
                message="Project expects distilled strategy but no card is persisted.",
                path="project.metadata.distilled_strategy_card",
                expected="compiled strategy card",
                actual=None,
                repair_action=(
                    "Compile a project-specific distilled strategy card for the "
                    "resolved category."
                ),
            )
        )
    if strategy_card:
        status = str(strategy_card.get("maturity_status") or "").strip().lower()
        score = _float_or_none(strategy_card.get("maturity_score"))
        unsafe = status == "unsafe" or (
            score is not None and score < thresholds.min_distilled_strategy_maturity_score
        )
        if unsafe:
            findings.append(
                _finding(
                    code="distilled_strategy_card_unsafe",
                    domain="project_identity",
                    message="Distilled strategy card is below the strict readiness bar.",
                    path="project.metadata.distilled_strategy_card.maturity",
                    expected=(
                        f">={thresholds.min_distilled_strategy_maturity_score} "
                        "and not unsafe"
                    ),
                    actual={"maturity_status": status, "maturity_score": score},
                    repair_action=(
                        "Use a mature aggregate, improve the aggregate, or explicitly "
                        "downgrade the project out of strict strategy enforcement."
                    ),
                )
            )
    return findings


def _evaluate_planning_assets(
    *,
    target_chapters: int,
    planned_chapters: int,
    story_design_kernel: Mapping[str, Any] | None,
    planning_kernel_report: Mapping[str, Any] | None,
    thresholds: BookCreationReadinessThresholds,
) -> list[BookCreationReadinessFinding]:
    findings: list[BookCreationReadinessFinding] = []
    if target_chapters <= 0:
        findings.append(
            _finding(
                code="target_chapters_missing",
                domain="planning_assets",
                message="Target chapter count must be known before lifecycle generation.",
                path="project.target_chapters",
                expected=">0",
                actual=target_chapters,
                repair_action="Persist target_chapters on the project.",
            )
        )
    if target_chapters > 0 and planned_chapters < target_chapters:
        findings.append(
            _finding(
                code="planned_chapters_below_target",
                domain="planning_assets",
                message="Planned chapters do not cover the full target book.",
                path="planning.planned_chapters",
                expected=f">={target_chapters}",
                actual=planned_chapters,
                repair_action="Extend book, volume, and chapter planning to target length.",
            )
        )
    if (
        thresholds.require_story_design_kernel_valid
        and _story_design_valid(story_design_kernel, planning_kernel_report) is not True
    ):
        findings.append(
            _finding(
                code="story_design_kernel_not_verified",
                domain="planning_assets",
                message="Story design kernel evidence is missing or invalid.",
                path="planning.story_design_kernel.valid",
                expected=True,
                actual=_story_design_valid(story_design_kernel, planning_kernel_report),
                repair_action="Materialize and validate StoryDesignKernel before writing.",
            )
        )
    if planning_kernel_report is not None and _report_passed(planning_kernel_report) is False:
        findings.append(
            _finding(
                code="planning_kernel_not_passed",
                domain="planning_assets",
                message="Planning kernel readiness report is not passed.",
                path="planning_kernel_report.passed",
                expected=True,
                actual=False,
                repair_action="Repair planning kernel findings before drafting.",
            )
        )
    return findings


def _evaluate_outline_assets(
    *,
    volume_plan_report: Mapping[str, Any] | None,
    prewrite_report: Mapping[str, Any] | None,
    forward_state_report: Mapping[str, Any] | None,
    reveal_alignment_report: Mapping[str, Any] | None,
    thresholds: BookCreationReadinessThresholds,
) -> list[BookCreationReadinessFinding]:
    findings: list[BookCreationReadinessFinding] = []
    _append_report_failure(
        findings,
        report=volume_plan_report,
        required=thresholds.require_volume_plan_passed,
        code="volume_plan_gate_not_passed",
        path="volume_plan_report.passed",
        repair_action="Materialize and repair volume-plan-v2 before drafting.",
    )
    _append_report_failure(
        findings,
        report=prewrite_report,
        required=thresholds.require_prewrite_passed,
        code="prewrite_gate_not_passed",
        path="prewrite_report.passed",
        repair_action="Materialize rich prewrite contract before drafting.",
    )
    _append_report_failure(
        findings,
        report=forward_state_report,
        required=thresholds.require_forward_state_passed,
        code="forward_state_gate_not_passed",
        path="forward_state_report.passed",
        repair_action="Extend forward state promises through the required window.",
    )
    _append_report_failure(
        findings,
        report=reveal_alignment_report,
        required=thresholds.require_reveal_alignment_passed,
        code="reveal_alignment_gate_not_passed",
        path="reveal_alignment_report.passed",
        repair_action="Align chapter outlines with reveal-schedule.yaml.",
    )
    return findings


def _append_report_failure(
    findings: list[BookCreationReadinessFinding],
    *,
    report: Mapping[str, Any] | None,
    required: bool,
    code: str,
    path: str,
    repair_action: str,
) -> None:
    if not required:
        return
    passed = _report_passed(report)
    if passed is True:
        return
    findings.append(
        _finding(
            code=code,
            domain="outline_assets",
            message="Required outline asset gate is missing or not passed.",
            path=path,
            expected=True,
            actual=passed,
            repair_action=repair_action,
        )
    )


def _domain_status(
    domain: str,
    findings: list[BookCreationReadinessFinding],
    metrics: Mapping[str, object],
) -> BookCreationDomainStatus:
    return BookCreationDomainStatus(
        domain=domain,
        passed=not findings,
        findings=tuple(findings),
        metrics=metrics,
    )


def _finding(
    *,
    code: str,
    domain: str,
    message: str,
    path: str,
    expected: object | None = None,
    actual: object | None = None,
    repair_action: str = "",
    severity: str = "critical",
) -> BookCreationReadinessFinding:
    return BookCreationReadinessFinding(
        code=code,
        severity=severity,
        domain=domain,
        message=message,
        path=path,
        expected=expected,
        actual=actual,
        repair_action=repair_action,
    )


def _as_mapping(value: object | None) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _is_strict(metadata: Mapping[str, Any]) -> bool:
    mode = str(metadata.get("methodology_contract_mode") or "").strip().lower()
    return mode == "strict" or metadata.get("methodology_contract_strict") is True


def _distilled_expected(metadata: Mapping[str, Any], strict: bool) -> bool:
    return bool(
        metadata.get("distilled_strategy_expected")
        or metadata.get("distilled_strategy_card")
        or metadata.get("distilled_strategy_blocks")
        or (strict and metadata.get("category_key"))
        or strict
    )


def _story_design_valid(
    story_design_kernel: Mapping[str, Any] | None,
    planning_kernel_report: Mapping[str, Any] | None,
) -> bool | None:
    kernel = _as_mapping(story_design_kernel)
    if "valid" in kernel:
        return _bool_or_none(kernel.get("valid"))
    report = _as_mapping(planning_kernel_report)
    for key in ("story_design_kernel", "story_design", "planning_kernel"):
        nested = _as_mapping(report.get(key))
        if "valid" in nested:
            return _bool_or_none(nested.get("valid"))
    if "planning_kernel_valid" in report:
        return _bool_or_none(report.get("planning_kernel_valid"))
    if planning_kernel_report is not None:
        return _report_passed(planning_kernel_report)
    return None


def _report_passed(report: Mapping[str, Any] | None) -> bool | None:
    payload = _as_mapping(report)
    if not payload:
        return None
    if "passed" in payload:
        return _bool_or_none(payload.get("passed"))
    verdict = str(payload.get("verdict") or "").strip().lower()
    if verdict in {"pass", "passed", "ready", "success"}:
        return True
    if verdict in {"blocked", "error", "fail", "failed"}:
        return False
    return None


def _bool_or_none(value: object | None) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "passed", "pass", "ready", "success"}:
            return True
        if normalized in {"false", "failed", "fail", "blocked", "error"}:
            return False
    return None


def _float_or_none(value: object | None) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
