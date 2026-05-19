from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class BookLifecycleQualityThresholds:
    min_scorecard_quality_score: float = 80.0
    min_identity_registry_coverage: float = 0.90
    min_personhood_coverage: float = 0.60
    max_missing_chapters: int = 0
    max_draftless_chapters: int = 0
    max_blocked_chapters: int = 0
    max_repair_tasks: int = 0
    max_length_cv: float = 0.10
    min_reference_distance: float = 0.72
    require_model_execution_ready: bool = True
    require_premium_gate_passed: bool = True
    require_acceptance_passed: bool = True
    require_prewrite_readiness_passed: bool = True
    require_reverse_outline_gate_passed: bool = True
    require_planning_kernel_valid: bool = True
    require_character_gate_passed: bool = True
    require_identity_manifest: bool = True
    require_reference_distance: bool = True
    require_real_generation_audit: bool = True

    def to_dict(self) -> dict[str, object]:
        return {
            "min_scorecard_quality_score": self.min_scorecard_quality_score,
            "min_identity_registry_coverage": self.min_identity_registry_coverage,
            "min_personhood_coverage": self.min_personhood_coverage,
            "max_missing_chapters": self.max_missing_chapters,
            "max_draftless_chapters": self.max_draftless_chapters,
            "max_blocked_chapters": self.max_blocked_chapters,
            "max_repair_tasks": self.max_repair_tasks,
            "max_length_cv": self.max_length_cv,
            "min_reference_distance": self.min_reference_distance,
            "require_model_execution_ready": self.require_model_execution_ready,
            "require_premium_gate_passed": self.require_premium_gate_passed,
            "require_acceptance_passed": self.require_acceptance_passed,
            "require_prewrite_readiness_passed": self.require_prewrite_readiness_passed,
            "require_reverse_outline_gate_passed": self.require_reverse_outline_gate_passed,
            "require_planning_kernel_valid": self.require_planning_kernel_valid,
            "require_character_gate_passed": self.require_character_gate_passed,
            "require_identity_manifest": self.require_identity_manifest,
            "require_reference_distance": self.require_reference_distance,
            "require_real_generation_audit": self.require_real_generation_audit,
        }


@dataclass(frozen=True, slots=True)
class BookLifecycleQualityFinding:
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
class BookLifecycleDomainStatus:
    domain: str
    passed: bool
    readiness_level: str
    findings: tuple[BookLifecycleQualityFinding, ...] = field(default_factory=tuple)
    metrics: Mapping[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "domain": self.domain,
            "passed": self.passed,
            "readiness_level": self.readiness_level,
            "findings": [finding.to_dict() for finding in self.findings],
            "metrics": dict(self.metrics),
        }


@dataclass(frozen=True, slots=True)
class BookLifecycleQualityReport:
    slug: str
    passed: bool
    readiness_level: str
    domain_statuses: tuple[BookLifecycleDomainStatus, ...] = field(default_factory=tuple)
    findings: tuple[BookLifecycleQualityFinding, ...] = field(default_factory=tuple)
    metrics: Mapping[str, object] = field(default_factory=dict)
    thresholds: BookLifecycleQualityThresholds = field(
        default_factory=BookLifecycleQualityThresholds
    )

    def to_dict(self) -> dict[str, object]:
        return {
            "slug": self.slug,
            "passed": self.passed,
            "readiness_level": self.readiness_level,
            "domain_statuses": [
                domain_status.to_dict() for domain_status in self.domain_statuses
            ],
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


def _float_or_none(value: object | None) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int(value: object | None, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _bool_or_none(value: object | None) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "passed", "pass", "ready", "verified", "success"}:
            return True
        if normalized in {"false", "failed", "fail", "blocked", "missing"}:
            return False
    return None


def _report_passed(report: Mapping[str, Any]) -> bool | None:
    if "passed" in report:
        return _bool_or_none(report.get("passed"))
    status = str(report.get("status") or report.get("readiness_level") or "").lower()
    if status in {"passed", "pass", "ready", "success", "verified"}:
        return True
    if status in {"failed", "fail", "blocked", "repairable", "partial"}:
        return False
    return None


def _readiness_level(findings: list[BookLifecycleQualityFinding]) -> str:
    if not findings:
        return "ready"
    if any(finding.severity == "critical" for finding in findings):
        return "blocked"
    return "partial"


def _finding(
    *,
    code: str,
    severity: str,
    domain: str,
    message: str,
    path: str,
    expected: object | None = None,
    actual: object | None = None,
    repair_action: str = "",
) -> BookLifecycleQualityFinding:
    return BookLifecycleQualityFinding(
        code=code,
        severity=severity,
        domain=domain,
        message=message,
        path=path,
        expected=expected,
        actual=actual,
        repair_action=repair_action,
    )


def _domain_status(
    domain: str,
    findings: list[BookLifecycleQualityFinding],
    metrics: Mapping[str, object],
) -> BookLifecycleDomainStatus:
    readiness = _readiness_level(findings)
    return BookLifecycleDomainStatus(
        domain=domain,
        passed=readiness == "ready",
        readiness_level=readiness,
        findings=tuple(findings),
        metrics=metrics,
    )


def _repair_plan_task_count(repair_plan: Mapping[str, Any]) -> int:
    plan = _as_mapping(repair_plan.get("repair_plan")) or repair_plan
    priority_counts = _as_mapping(plan.get("priority_counts"))
    if priority_counts:
        return _int(priority_counts.get("critical")) + _int(priority_counts.get("high"))
    return _int(plan.get("task_count"))


def _nested_report_passed(payload: Mapping[str, Any], *keys: str) -> bool | None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, Mapping):
            passed = _report_passed(value)
            if passed is not None:
                return passed
    return None


def _planning_kernel_passed(planning_report: Mapping[str, Any]) -> bool | None:
    for key in ("planning_kernel_valid", "story_design_kernel_valid"):
        passed = _bool_or_none(planning_report.get(key))
        if passed is not None:
            return passed
    for key in ("story_design_kernel", "planning_kernel", "emotion_driven_kernel"):
        kernel = _as_mapping(planning_report.get(key))
        if not kernel:
            continue
        passed = _bool_or_none(kernel.get("valid"))
        if passed is not None:
            return passed
        status = _bool_or_none(kernel.get("status"))
        if status is not None:
            return status
        if key == "planning_kernel":
            story_design = _as_mapping(kernel.get("story_design"))
            emotion_driven = _as_mapping(kernel.get("emotion_driven"))
            if story_design or emotion_driven:
                return (
                    story_design.get("valid") is True
                    and emotion_driven.get("valid") is True
                )
    return None


def _reverse_outline_passed(planning_report: Mapping[str, Any]) -> bool | None:
    explicit = _bool_or_none(planning_report.get("reverse_outline_gate_passed"))
    if explicit is not None:
        return explicit
    status = _bool_or_none(planning_report.get("reverse_outline_status"))
    if status is not None:
        return status
    return _nested_report_passed(
        planning_report,
        "reverse_outline_gate_report",
        "reverse_outline_report",
    )


def _identity_manifest_count(character_report: Mapping[str, Any]) -> int | None:
    for key in ("identity_manifest_count", "character_identity_count"):
        if key in character_report:
            return _int(character_report.get(key))
    for key in ("identity_manifest", "character_identity_manifest"):
        value = character_report.get(key)
        if isinstance(value, list):
            return len(value)
    return None


def _reference_distance(anti_copy_report: Mapping[str, Any]) -> float | None:
    for key in (
        "reference_distance_score",
        "min_reference_distance_score",
        "sample_distance_score",
    ):
        score = _float_or_none(anti_copy_report.get(key))
        if score is not None:
            return score
    parity = _as_mapping(anti_copy_report.get("sample_quality_parity"))
    metrics = _as_mapping(parity.get("metrics"))
    return _float_or_none(metrics.get("reference_distance_score"))


def _sum_audit_field(reports: tuple[Mapping[str, Any], ...], key: str) -> int:
    return sum(_int(report.get(key)) for report in reports)


def evaluate_book_lifecycle_quality(
    *,
    slug: str,
    planning_report: Mapping[str, Any] | object | None = None,
    character_report: Mapping[str, Any] | object | None = None,
    chapter_report: Mapping[str, Any] | object | None = None,
    whole_book_report: Mapping[str, Any] | object | None = None,
    anti_copy_report: Mapping[str, Any] | object | None = None,
    thresholds: BookLifecycleQualityThresholds | None = None,
) -> BookLifecycleQualityReport:
    """Evaluate whether a book can be claimed as full lifecycle premium quality.

    This is an aggregator gate. It does not replace chapter, premium, acceptance,
    reverse-outline, bible, or anti-copy checks; it prevents any one successful
    layer from being misread as proof that the whole book is sample-level ready.
    """

    thresholds = thresholds or BookLifecycleQualityThresholds()
    planning = _as_mapping(planning_report)
    character = _as_mapping(character_report)
    chapter = _as_mapping(chapter_report)
    whole_book = _as_mapping(whole_book_report)
    anti_copy = _as_mapping(anti_copy_report)

    scorecard = _as_mapping(chapter.get("scorecard")) or chapter
    repair_plan = _as_mapping(chapter.get("repair_plan"))
    acceptance = _as_mapping(whole_book.get("acceptance"))
    premium_gate = _as_mapping(whole_book.get("premium_gate"))
    model_preflight = _as_mapping(whole_book.get("model_preflight"))
    rewrite_audit = _as_mapping(whole_book.get("rewrite_generation_audit"))
    chapter_generation_audit = _as_mapping(whole_book.get("chapter_generation_audit"))
    audits = (rewrite_audit, chapter_generation_audit)

    target_chapters = _int(
        planning.get("target_chapters")
        or chapter.get("target_chapters")
        or whole_book.get("target_chapters")
        or scorecard.get("target_chapters")
    )
    planned_chapters = _int(
        planning.get("planned_chapters")
        or chapter.get("planned_chapters")
        or whole_book.get("planned_chapters")
        or scorecard.get("total_chapters")
    )
    current_chapters = _int(
        chapter.get("current_chapters")
        or whole_book.get("current_chapters")
        or scorecard.get("current_chapters")
    )
    category_key = str(
        whole_book.get("category")
        or chapter.get("category")
        or planning.get("category")
        or ""
    )

    planning_findings: list[BookLifecycleQualityFinding] = []
    if target_chapters <= 0:
        planning_findings.append(
            _finding(
                code="target_chapters_missing",
                severity="critical",
                domain="planning",
                message="Target chapter count is required for whole-book lifecycle acceptance.",
                path="planning.target_chapters",
                expected=">0",
                actual=target_chapters,
                repair_action="Persist project target_chapters before lifecycle evaluation.",
            )
        )
    if planned_chapters < target_chapters:
        planning_findings.append(
            _finding(
                code="planning_outline_below_target",
                severity="critical",
                domain="planning",
                message="The outline does not yet cover the full target book length.",
                path="planning.planned_chapters",
                expected=f">={target_chapters}",
                actual=planned_chapters,
                repair_action=(
                    "Extend volume/arc/chapter planning to the target length, then rerun "
                    "reverse-outline and state-ledger checks."
                ),
            )
        )
    prewrite_report = _as_mapping(planning.get("prewrite_readiness_report"))
    prewrite_passed = _report_passed(prewrite_report)
    if thresholds.require_prewrite_readiness_passed and prewrite_passed is not True:
        planning_findings.append(
            _finding(
                code="prewrite_readiness_not_passed",
                severity="critical",
                domain="planning",
                message="Prewrite readiness gate is missing or not passed.",
                path="planning.prewrite_readiness_report.passed",
                expected=True,
                actual=prewrite_passed,
                repair_action=(
                    "Repair long-arc capacity, volume differentiation, and story "
                    "design readiness before continuing full-book generation."
                ),
            )
        )
    reverse_passed = _reverse_outline_passed(planning)
    if thresholds.require_reverse_outline_gate_passed and reverse_passed is not True:
        planning_findings.append(
            _finding(
                code="reverse_outline_gate_not_passed",
                severity="critical",
                domain="planning",
                message="Reverse-outline evidence is missing or not passed.",
                path="planning.reverse_outline_gate_report.passed",
                expected=True,
                actual=reverse_passed,
                repair_action=(
                    "Run reverse_outline_gate over the full outline and repair chapters "
                    "that lack durable state changes."
                ),
            )
        )
    planning_kernel_passed = _planning_kernel_passed(planning)
    if thresholds.require_planning_kernel_valid and planning_kernel_passed is not True:
        planning_findings.append(
            _finding(
                code="planning_kernel_not_verified",
                severity="critical",
                domain="planning",
                message="Story design / emotion kernel evidence is missing or invalid.",
                path="planning.story_design_kernel.valid",
                expected=True,
                actual=planning_kernel_passed,
                repair_action=(
                    "Materialize the story design kernel, emotion engine, and category "
                    "state variables before writing more chapters."
                ),
            )
        )
    planning_status = _domain_status(
        "planning",
        planning_findings,
        {
            "target_chapters": target_chapters,
            "planned_chapters": planned_chapters,
            "prewrite_readiness_passed": prewrite_passed,
            "reverse_outline_gate_passed": reverse_passed,
            "planning_kernel_valid": planning_kernel_passed,
        },
    )

    character_findings: list[BookLifecycleQualityFinding] = []
    bible_passed = _nested_report_passed(
        character,
        "bible_gate_report",
        "character_gate_report",
        "personhood_gate_report",
    )
    explicit_character_passed = _bool_or_none(character.get("character_gate_passed"))
    character_gate_passed = (
        explicit_character_passed
        if explicit_character_passed is not None
        else bible_passed
    )
    if thresholds.require_character_gate_passed and character_gate_passed is not True:
        character_findings.append(
            _finding(
                code="character_gate_evidence_missing",
                severity="critical",
                domain="character",
                message="Character bible/personhood gate evidence is missing or not passed.",
                path="character.bible_gate_report.passed",
                expected=True,
                actual=character_gate_passed,
                repair_action=(
                    "Run bible/personhood gates and repair identity, desire, cost, "
                    "relationship, and naming-pool gaps before lifecycle acceptance."
                ),
            )
        )
    identity_count = _identity_manifest_count(character)
    if thresholds.require_identity_manifest and (identity_count is None or identity_count <= 0):
        character_findings.append(
            _finding(
                code="identity_manifest_missing",
                severity="critical",
                domain="character",
                message="No frozen identity manifest evidence was provided.",
                path="character.identity_manifest",
                expected="non-empty",
                actual=identity_count,
                repair_action=(
                    "Persist a canonical identity manifest and use it in every chapter "
                    "write-safety pass to prevent name/title/status drift."
                ),
            )
        )
    identity_coverage = _float_or_none(character.get("identity_registry_coverage"))
    if (
        identity_coverage is not None
        and identity_coverage < thresholds.min_identity_registry_coverage
    ):
        character_findings.append(
            _finding(
                code="identity_registry_coverage_below_bar",
                severity="critical",
                domain="character",
                message="Character identity registry coverage is below lifecycle requirements.",
                path="character.identity_registry_coverage",
                expected=f">={thresholds.min_identity_registry_coverage}",
                actual=identity_coverage,
                repair_action=(
                    "Backfill frozen gender/pronoun/alias identity data for every "
                    "named character row before continuing generation."
                ),
            )
        )
    personhood_coverage = _float_or_none(character.get("personhood_coverage"))
    if (
        personhood_coverage is not None
        and personhood_coverage < thresholds.min_personhood_coverage
    ):
        character_findings.append(
            _finding(
                code="personhood_coverage_below_bar",
                severity="high",
                domain="character",
                message="Character personhood coverage is below lifecycle requirements.",
                path="character.personhood_coverage",
                expected=f">={thresholds.min_personhood_coverage}",
                actual=personhood_coverage,
                repair_action=(
                    "Backfill desire, fear, flaw, strength, voice, and independent-life "
                    "anchors for named characters used by future batches."
                ),
            )
        )
    character_status = _domain_status(
        "character",
        character_findings,
        {
            "character_gate_passed": character_gate_passed,
            "identity_manifest_count": identity_count,
            "identity_registry_coverage": identity_coverage,
            "personhood_coverage": personhood_coverage,
        },
    )

    chapter_findings: list[BookLifecycleQualityFinding] = []
    quality_score = _float_or_none(scorecard.get("quality_score"))
    if quality_score is None:
        chapter_findings.append(
            _finding(
                code="scorecard_missing",
                severity="critical",
                domain="chapter_execution",
                message="Scorecard evidence is required for chapter-level lifecycle acceptance.",
                path="chapter.scorecard.quality_score",
                expected=f">={thresholds.min_scorecard_quality_score}",
                actual=None,
                repair_action="Run bestseller scorecard for the current project.",
            )
        )
    elif quality_score < thresholds.min_scorecard_quality_score:
        chapter_findings.append(
            _finding(
                code="scorecard_below_lifecycle_bar",
                severity="high",
                domain="chapter_execution",
                message="Current chapter set is below the lifecycle quality bar.",
                path="chapter.scorecard.quality_score",
                expected=f">={thresholds.min_scorecard_quality_score}",
                actual=quality_score,
                repair_action=(
                    "Continue targeted chapter rewrites, chapter quality gates, and "
                    "scorecard reruns until the score reaches the acceptance bar."
                ),
            )
        )
    missing = _int(scorecard.get("missing_chapters"))
    if missing > thresholds.max_missing_chapters:
        chapter_findings.append(
            _finding(
                code="book_incomplete",
                severity="critical",
                domain="chapter_execution",
                message="The target book is not fully materialized.",
                path="chapter.scorecard.missing_chapters",
                expected=f"<={thresholds.max_missing_chapters}",
                actual=missing,
                repair_action=(
                    "Generate missing planned chapters under state-ledger constraints, "
                    "then extend outline for any unplanned target chapters."
                ),
            )
        )
    draftless = _int(scorecard.get("draftless_chapters"))
    if draftless > thresholds.max_draftless_chapters:
        chapter_findings.append(
            _finding(
                code="planned_chapters_without_current_drafts",
                severity="critical",
                domain="chapter_execution",
                message="Planned chapters exist without current prose drafts.",
                path="chapter.scorecard.draftless_chapters",
                expected=f"<={thresholds.max_draftless_chapters}",
                actual=draftless,
                repair_action="Generate or recover drafts for all planned chapters.",
            )
        )
    blocked = _int(scorecard.get("chapters_blocked"))
    if blocked > thresholds.max_blocked_chapters:
        chapter_findings.append(
            _finding(
                code="blocked_chapters_remaining",
                severity="critical",
                domain="chapter_execution",
                message="Some chapters still block quality acceptance.",
                path="chapter.scorecard.chapters_blocked",
                expected=f"<={thresholds.max_blocked_chapters}",
                actual=blocked,
                repair_action="Repair blocked chapters before polishing lower-risk text.",
            )
        )
    repair_task_count = _repair_plan_task_count(repair_plan)
    if repair_task_count > thresholds.max_repair_tasks:
        chapter_findings.append(
            _finding(
                code="repair_tasks_remaining",
                severity="high",
                domain="chapter_execution",
                message="High or critical autonomous repair tasks are still pending.",
                path="chapter.repair_plan.task_count",
                expected=f"<={thresholds.max_repair_tasks}",
                actual=repair_task_count,
                repair_action="Execute the repair queue and rerun lifecycle acceptance.",
            )
        )
    length_cv = _float_or_none(scorecard.get("length_cv"))
    if length_cv is not None and length_cv > thresholds.max_length_cv:
        chapter_findings.append(
            _finding(
                code="length_stability_below_bar",
                severity="high",
                domain="chapter_execution",
                message="Chapter length variance is above the lifecycle stability bar.",
                path="chapter.scorecard.length_cv",
                expected=f"<={thresholds.max_length_cv}",
                actual=length_cv,
                repair_action=(
                    "Normalize short/long chapters during rewrites and continuation batches."
                ),
            )
        )
    invalid_generation_count = _sum_audit_field(audits, "invalid")
    gate_rejected_count = _sum_audit_field(audits, "gate_rejected")
    if thresholds.require_real_generation_audit and invalid_generation_count > 0:
        invalid_modes = [
            str(mode)
            for report in audits
            for mode in report.get("invalid_generation_modes", [])
            if str(mode)
        ]
        chapter_findings.append(
            _finding(
                code="invalid_generation_mode_detected",
                severity="critical",
                domain="chapter_execution",
                message=(
                    "At least one rewrite/generation used missing, mock, or "
                    "fallback model evidence."
                ),
                path="chapter.generation_audit.invalid",
                expected=0,
                actual=invalid_generation_count,
                repair_action=(
                    "Discard fallback-polluted outputs, fix provider execution, and regenerate."
                ),
            )
        )
        if invalid_modes:
            chapter_findings.append(
                _finding(
                    code="invalid_generation_modes_present",
                    severity="high",
                    domain="chapter_execution",
                    message="Invalid generation modes were recorded in the lifecycle run.",
                    path="chapter.generation_audit.invalid_generation_modes",
                    expected=[],
                    actual=sorted(set(invalid_modes)),
                    repair_action="Regenerate affected chapters with a real provider.",
                )
            )
    if gate_rejected_count > 0:
        chapter_findings.append(
            _finding(
                code="generation_gate_rejections_remaining",
                severity="high",
                domain="chapter_execution",
                message="Recent generation attempts were rejected by downstream gates.",
                path="chapter.generation_audit.gate_rejected",
                expected=0,
                actual=gate_rejected_count,
                repair_action=(
                    "Feed the rejection reasons back into the next rewrite prompt and rerun gates."
                ),
            )
        )
    chapter_status = _domain_status(
        "chapter_execution",
        chapter_findings,
        {
            "current_chapters": current_chapters,
            "target_chapters": target_chapters,
            "quality_score": quality_score,
            "missing_chapters": missing,
            "draftless_chapters": draftless,
            "blocked_chapters": blocked,
            "repair_task_count": repair_task_count,
            "length_cv": length_cv,
            "invalid_generation_count": invalid_generation_count,
            "gate_rejected_count": gate_rejected_count,
        },
    )

    whole_findings: list[BookLifecycleQualityFinding] = []
    acceptance_passed = _report_passed(acceptance)
    if thresholds.require_acceptance_passed and acceptance_passed is not True:
        whole_findings.append(
            _finding(
                code="whole_book_acceptance_not_passed",
                severity="critical",
                domain="whole_book",
                message="Whole-book acceptance gate has not passed.",
                path="whole_book.acceptance.passed",
                expected=True,
                actual=acceptance_passed,
                repair_action="Continue the closure loop until acceptance.passed is true.",
            )
        )
    premium_passed = _report_passed(premium_gate)
    if thresholds.require_premium_gate_passed and premium_passed is not True:
        whole_findings.append(
            _finding(
                code="premium_gate_not_passed",
                severity="critical",
                domain="whole_book",
                message="Premium structural gate has not passed.",
                path="whole_book.premium_gate.passed",
                expected=True,
                actual=premium_passed,
                repair_action="Repair premium state ledgers and category hard-engine gaps.",
            )
        )
    model_ready = _bool_or_none(model_preflight.get("ready"))
    if thresholds.require_model_execution_ready and model_ready is not True:
        whole_findings.append(
            _finding(
                code="model_execution_unavailable",
                severity="critical",
                domain="whole_book",
                message=(
                    "Lifecycle repair/generation cannot claim success without "
                    "a real model preflight."
                ),
                path="whole_book.model_preflight.ready",
                expected=True,
                actual=model_ready,
                repair_action="Fix LLM provider configuration and rerun the closure loop.",
            )
        )
    whole_status = _domain_status(
        "whole_book",
        whole_findings,
        {
            "acceptance_passed": acceptance_passed,
            "premium_gate_passed": premium_passed,
            "model_execution_ready": model_ready,
        },
    )

    anti_copy_findings: list[BookLifecycleQualityFinding] = []
    reference_distance = _reference_distance(anti_copy)
    if thresholds.require_reference_distance and reference_distance is None:
        anti_copy_findings.append(
            _finding(
                code="reference_distance_missing",
                severity="critical",
                domain="anti_copy",
                message="Reference-distance evidence is required before claiming benchmark parity.",
                path="anti_copy.reference_distance_score",
                expected=f">={thresholds.min_reference_distance}",
                actual=None,
                repair_action=(
                    "Run sample-quality parity / reference-distance checks on the exported book."
                ),
            )
        )
    elif reference_distance is not None and reference_distance < thresholds.min_reference_distance:
        anti_copy_findings.append(
            _finding(
                code="reference_distance_too_low",
                severity="critical",
                domain="anti_copy",
                message="The book is too close to benchmark references for a safe parity claim.",
                path="anti_copy.reference_distance_score",
                expected=f">={thresholds.min_reference_distance}",
                actual=reference_distance,
                repair_action="Rewrite overlapping mechanisms as abstracted variants.",
            )
        )
    source_leak_count = _int(anti_copy.get("source_leak_count"))
    protected_phrase_leak_count = _int(anti_copy.get("protected_phrase_leak_count"))
    if source_leak_count > 0:
        anti_copy_findings.append(
            _finding(
                code="source_identity_leak_detected",
                severity="critical",
                domain="anti_copy",
                message="Private source identity leakage was detected in lifecycle artifacts.",
                path="anti_copy.source_leak_count",
                expected=0,
                actual=source_leak_count,
                repair_action="Remove source-identifying names and rerun privacy checks.",
            )
        )
    if protected_phrase_leak_count > 0:
        anti_copy_findings.append(
            _finding(
                code="protected_phrase_leak_detected",
                severity="critical",
                domain="anti_copy",
                message="Protected expression leakage was detected in lifecycle artifacts.",
                path="anti_copy.protected_phrase_leak_count",
                expected=0,
                actual=protected_phrase_leak_count,
                repair_action="Rewrite leaked phrases and rerun anti-copy checks.",
            )
        )
    anti_copy_status = _domain_status(
        "anti_copy",
        anti_copy_findings,
        {
            "reference_distance_score": reference_distance,
            "source_leak_count": source_leak_count,
            "protected_phrase_leak_count": protected_phrase_leak_count,
        },
    )

    domain_statuses = (
        planning_status,
        character_status,
        chapter_status,
        whole_status,
        anti_copy_status,
    )
    findings = tuple(
        finding
        for domain_status in domain_statuses
        for finding in domain_status.findings
    )
    readiness = _readiness_level(list(findings))
    return BookLifecycleQualityReport(
        slug=slug,
        passed=readiness == "ready",
        readiness_level=readiness,
        domain_statuses=domain_statuses,
        findings=findings,
        metrics={
            "category_key": category_key,
            "target_chapters": target_chapters,
            "planned_chapters": planned_chapters,
            "current_chapters": current_chapters,
            "quality_score": quality_score,
            "missing_chapters": missing,
            "draftless_chapters": draftless,
            "blocked_chapters": blocked,
            "repair_task_count": repair_task_count,
            "length_cv": length_cv,
            "acceptance_passed": acceptance_passed,
            "premium_gate_passed": premium_passed,
            "model_execution_ready": model_ready,
            "reference_distance_score": reference_distance,
        },
        thresholds=thresholds,
    )


def build_lifecycle_quality_report_from_closure(
    closure_report: Mapping[str, Any] | object,
    *,
    thresholds: BookLifecycleQualityThresholds | None = None,
) -> BookLifecycleQualityReport:
    closure = _as_mapping(closure_report)
    lifecycle_evidence = _as_mapping(closure.get("lifecycle_evidence"))
    after_acceptance = _as_mapping(closure.get("after_acceptance"))
    if not after_acceptance:
        after_acceptance = closure
    scorecard = _as_mapping(after_acceptance.get("scorecard"))
    repair_plan = (
        _as_mapping(after_acceptance.get("repair_plan"))
        or _as_mapping(closure.get("repair_plan"))
    )
    continuation_plan = _as_mapping(closure.get("continuation_plan"))
    slug = str(closure.get("slug") or after_acceptance.get("slug") or "")

    planning_evidence = _as_mapping(lifecycle_evidence.get("planning_report"))
    character_evidence = _as_mapping(lifecycle_evidence.get("character_report"))
    anti_copy_evidence = _as_mapping(lifecycle_evidence.get("anti_copy_report"))

    planning_report: dict[str, Any] = {
        "category": after_acceptance.get("category"),
        "target_chapters": after_acceptance.get("target_chapters"),
        "planned_chapters": (
            continuation_plan.get("planned_chapters")
            if continuation_plan
            else scorecard.get("total_chapters")
        ),
        **dict(planning_evidence),
        **dict(_as_mapping(closure.get("planning_report"))),
        **dict(_as_mapping(closure.get("planning"))),
    }
    character_report = {
        **dict(character_evidence),
        **dict(_as_mapping(closure.get("character_report"))),
        **dict(_as_mapping(closure.get("character"))),
    }
    chapter_report = {
        "category": after_acceptance.get("category"),
        "target_chapters": after_acceptance.get("target_chapters"),
        "current_chapters": after_acceptance.get("current_chapters"),
        "planned_chapters": planning_report.get("planned_chapters"),
        "scorecard": scorecard,
        "repair_plan": repair_plan,
    }
    whole_book_report = {
        "category": after_acceptance.get("category"),
        "target_chapters": after_acceptance.get("target_chapters"),
        "current_chapters": after_acceptance.get("current_chapters"),
        "acceptance": _as_mapping(after_acceptance.get("acceptance")),
        "premium_gate": _as_mapping(after_acceptance.get("premium_gate")),
        "model_preflight": _as_mapping(closure.get("model_preflight")),
        "rewrite_generation_audit": _as_mapping(closure.get("rewrite_generation_audit")),
        "chapter_generation_audit": _as_mapping(closure.get("chapter_generation_audit")),
    }
    anti_copy_report = {
        **dict(anti_copy_evidence),
        **dict(_as_mapping(closure.get("anti_copy_report"))),
        **dict(_as_mapping(closure.get("anti_copy"))),
    }

    return evaluate_book_lifecycle_quality(
        slug=slug,
        planning_report=planning_report,
        character_report=character_report,
        chapter_report=chapter_report,
        whole_book_report=whole_book_report,
        anti_copy_report=anti_copy_report,
        thresholds=thresholds,
    )


__all__ = [
    "BookLifecycleDomainStatus",
    "BookLifecycleQualityFinding",
    "BookLifecycleQualityReport",
    "BookLifecycleQualityThresholds",
    "build_lifecycle_quality_report_from_closure",
    "evaluate_book_lifecycle_quality",
]
