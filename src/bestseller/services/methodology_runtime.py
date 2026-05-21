"""Runtime adapters for methodology checker reports.

The methodology gates emit the repo-wide ``CheckerReport`` shape.  Book tasks
persist review results as scene/chapter findings, so this module is the narrow
bridge between those two contracts.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any, TypeVar

from bestseller.domain.review import (
    ChapterReviewFinding,
    ChapterReviewResult,
    SceneReviewFinding,
    SceneReviewResult,
)
from bestseller.services.checker_schema import CheckerIssue, CheckerReport
from bestseller.services.writing_profile import is_english_language

METHODOLOGY_REPORTS_EVIDENCE_KEY = "methodology_checker_reports"

_ReviewResultT = TypeVar("_ReviewResultT", SceneReviewResult, ChapterReviewResult)


def merge_methodology_reports_into_scene_review(
    review_result: SceneReviewResult,
    reports: Iterable[CheckerReport],
    *,
    language: str | None = None,
) -> SceneReviewResult:
    normalized = _normalize_reports(reports)
    findings = _scene_findings(normalized)
    if not findings:
        return _merge_evidence_only(review_result, normalized)

    blocks_review = _has_blocking_issue(normalized)
    rewrite_prefix = (
        _rewrite_prefix(normalized, language=language, scope="scene")
        if blocks_review
        else None
    )
    return SceneReviewResult(
        verdict="rewrite" if blocks_review else review_result.verdict,
        severity_max=_merge_severity(review_result.severity_max, findings),
        scores=review_result.scores,
        findings=[*review_result.findings, *findings],
        evidence_summary=_merge_methodology_evidence(review_result.evidence_summary, normalized),
        rewrite_instructions=_merge_rewrite_instructions(
            review_result.rewrite_instructions,
            rewrite_prefix,
        ),
    )


def merge_methodology_reports_into_chapter_review(
    review_result: ChapterReviewResult,
    reports: Iterable[CheckerReport],
    *,
    language: str | None = None,
) -> ChapterReviewResult:
    normalized = _normalize_reports(reports)
    findings = _chapter_findings(normalized)
    if not findings:
        return _merge_evidence_only(review_result, normalized)

    blocks_review = _has_blocking_issue(normalized)
    rewrite_prefix = (
        _rewrite_prefix(normalized, language=language, scope="chapter")
        if blocks_review
        else None
    )
    return ChapterReviewResult(
        verdict="rewrite" if blocks_review else review_result.verdict,
        severity_max=_merge_severity(review_result.severity_max, findings),
        scores=review_result.scores,
        findings=[*review_result.findings, *findings],
        evidence_summary=_merge_methodology_evidence(review_result.evidence_summary, normalized),
        rewrite_instructions=_merge_rewrite_instructions(
            review_result.rewrite_instructions,
            rewrite_prefix,
        ),
    )


def checker_reports_from_review_payload(payload: Mapping[str, Any]) -> tuple[CheckerReport, ...]:
    """Extract persisted methodology reports from a review structured output."""

    evidence = payload.get("evidence_summary")
    if not isinstance(evidence, Mapping):
        return ()
    raw_reports = evidence.get(METHODOLOGY_REPORTS_EVIDENCE_KEY)
    if not isinstance(raw_reports, list):
        return ()

    reports: list[CheckerReport] = []
    for raw in raw_reports:
        if isinstance(raw, CheckerReport):
            reports.append(raw)
        elif isinstance(raw, Mapping):
            try:
                reports.append(CheckerReport.from_dict(raw))
            except (KeyError, TypeError, ValueError):
                continue
    return tuple(reports)


def _normalize_reports(reports: Iterable[CheckerReport]) -> tuple[CheckerReport, ...]:
    return tuple(report for report in reports if isinstance(report, CheckerReport))


def _scene_findings(reports: tuple[CheckerReport, ...]) -> list[SceneReviewFinding]:
    findings: list[SceneReviewFinding] = []
    for issue in _iter_issues(reports):
        findings.append(
            SceneReviewFinding(
                category=_finding_category(issue),
                severity=_review_severity(issue),
                message=_finding_message(issue),
            )
        )
    return findings


def _chapter_findings(reports: tuple[CheckerReport, ...]) -> list[ChapterReviewFinding]:
    findings: list[ChapterReviewFinding] = []
    for issue in _iter_issues(reports):
        findings.append(
            ChapterReviewFinding(
                category=_finding_category(issue),
                severity=_review_severity(issue),
                message=_finding_message(issue),
            )
        )
    return findings


def _iter_issues(reports: tuple[CheckerReport, ...]) -> Iterable[CheckerIssue]:
    for report in reports:
        yield from report.issues


def _finding_category(issue: CheckerIssue) -> str:
    raw = issue.type or "methodology"
    return raw[:64]


def _finding_message(issue: CheckerIssue) -> str:
    code = issue.id
    description = issue.description.strip()
    suggestion = issue.suggestion.strip()
    if suggestion:
        return f"[{code}] {description} 建议：{suggestion}"
    return f"[{code}] {description}"


def _review_severity(issue: CheckerIssue) -> str:
    if issue.severity == "critical" or not issue.can_override:
        return "critical"
    if issue.severity in {"high", "medium"}:
        return "major"
    return "info"


def _has_blocking_issue(reports: tuple[CheckerReport, ...]) -> bool:
    return any(
        issue.severity == "critical" or not issue.can_override
        for issue in _iter_issues(reports)
    )


def _merge_severity(
    current: str,
    findings: Iterable[SceneReviewFinding | ChapterReviewFinding],
) -> str:
    severity_rank = {"info": 0, "low": 0, "warning": 1, "major": 1, "medium": 1, "high": 2, "critical": 3}
    out = current
    for finding in findings:
        if severity_rank.get(finding.severity, 0) > severity_rank.get(out, 0):
            out = finding.severity
    return out


def _merge_methodology_evidence(
    evidence_summary: Mapping[str, Any],
    reports: tuple[CheckerReport, ...],
) -> dict[str, Any]:
    evidence = dict(evidence_summary)
    existing = evidence.get(METHODOLOGY_REPORTS_EVIDENCE_KEY)
    serialized = [report.to_dict() for report in reports]
    if isinstance(existing, list):
        evidence[METHODOLOGY_REPORTS_EVIDENCE_KEY] = [*existing, *serialized]
    else:
        evidence[METHODOLOGY_REPORTS_EVIDENCE_KEY] = serialized
    return evidence


def _merge_evidence_only(
    review_result: _ReviewResultT,
    reports: tuple[CheckerReport, ...],
) -> _ReviewResultT:
    if not reports:
        return review_result
    updated = review_result.model_copy(
        update={
            "evidence_summary": _merge_methodology_evidence(
                review_result.evidence_summary,
                reports,
            )
        }
    )
    return updated  # type: ignore[return-value]


def _rewrite_prefix(
    reports: tuple[CheckerReport, ...],
    *,
    language: str | None,
    scope: str,
) -> str:
    blocking = [
        issue
        for issue in _iter_issues(reports)
        if issue.severity == "critical" or not issue.can_override
    ]
    issue_lines = "; ".join(f"{issue.id}: {issue.suggestion}" for issue in blocking[:5])
    if is_english_language(language):
        label = "scene" if scope == "scene" else "chapter"
        return (
            f"[methodology gate] Rewrite this {label} to satisfy the required "
            f"methodology checks: {issue_lines}"
        )
    label = "场景" if scope == "scene" else "章节"
    return f"【方法论 Gate】请重写本{label}并修复这些硬性方法论问题：{issue_lines}"


def _merge_rewrite_instructions(current: str | None, prefix: str | None) -> str | None:
    if not prefix:
        return current
    return f"{prefix}\n\n{current}" if current else prefix


__all__ = [
    "METHODOLOGY_REPORTS_EVIDENCE_KEY",
    "checker_reports_from_review_payload",
    "merge_methodology_reports_into_chapter_review",
    "merge_methodology_reports_into_scene_review",
]
