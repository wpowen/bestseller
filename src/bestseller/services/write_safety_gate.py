from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Iterable, Sequence

from bestseller.domain.contradiction import ContradictionCheckResult
from bestseller.services.identity_guard import IdentityViolation

if TYPE_CHECKING:
    from bestseller.services.reader_power import GoldenThreeReport


@dataclass(frozen=True)
class WriteSafetyFinding:
    source: str
    code: str
    severity: str
    message: str
    evidence: str = ""
    payload: dict[str, Any] = field(default_factory=dict)


class WriteSafetyBlockError(ValueError):
    def __init__(
        self,
        message: str,
        *,
        findings: Sequence[WriteSafetyFinding],
    ) -> None:
        super().__init__(message)
        self.findings = tuple(findings)


def findings_from_contradiction_result(
    result: ContradictionCheckResult,
    *,
    block_on_violation: bool = True,
) -> tuple[WriteSafetyFinding, ...]:
    if not block_on_violation:
        return ()
    return tuple(
        WriteSafetyFinding(
            source="contradiction",
            code=violation.check_type,
            severity=violation.severity,
            message=violation.message,
            evidence=violation.evidence,
        )
        for violation in result.violations
    )


def findings_from_identity_violations(
    violations: Iterable[IdentityViolation],
    *,
    block_on_violation: bool = True,
    blocked_severities: Iterable[str] = ("critical", "major"),
) -> tuple[WriteSafetyFinding, ...]:
    if not block_on_violation:
        return ()

    blocked = {severity.strip().lower() for severity in blocked_severities if severity}
    if not blocked:
        return ()

    findings: list[WriteSafetyFinding] = []
    for violation in violations:
        severity = str(violation.severity or "").lower()
        if severity not in blocked:
            continue
        findings.append(
            WriteSafetyFinding(
                source="identity",
                code=violation.violation_type,
                severity=severity,
                message=(
                    f"{violation.character_name}: expected {violation.expected}, "
                    f"found {violation.found}"
                ),
                evidence=violation.evidence,
                payload={"character_name": violation.character_name},
            )
        )
    return tuple(findings)


_GOLDEN_THREE_ISSUE_SEVERITY: dict[str, str] = {
    "GOLDEN_THREE_LOW_HYPE": "critical",
    "GOLDEN_THREE_WEAK_ENDING_HOOKS": "major",
    "GOLDEN_THREE_WEAK_OPEN_CONFLICT": "major",
    "GOLDEN_THREE_INCOMPLETE": "minor",
}


def findings_from_golden_three_report(
    report: "GoldenThreeReport | None",
    *,
    block_on_violation: bool = True,
    blocked_severities: Iterable[str] = ("critical",),
) -> tuple[WriteSafetyFinding, ...]:
    """Surface Golden-Three issues as write-safety findings.

    The Golden-Three analyzer is the earliest canary that a new book is
    DOA — if chapters 1-3 have no readable hype signal or no ending hook,
    even a passing scorecard is a lie. Wiring its report into the write-safety
    gate lets the pipeline hard-stop BEFORE chapter 4 ships, instead of
    producing another blood-twins ``scorecard=64, reads-flat`` outcome.

    Only the ``blocked_severities`` set will actually block; everything else
    is logged upstream but not returned here.
    """
    if not block_on_violation or report is None:
        return ()
    if not getattr(report, "enabled", False):
        return ()

    blocked = {severity.strip().lower() for severity in blocked_severities if severity}
    if not blocked:
        return ()

    findings: list[WriteSafetyFinding] = []
    for code in getattr(report, "issue_codes", ()) or ():
        severity = _GOLDEN_THREE_ISSUE_SEVERITY.get(str(code), "minor")
        if severity not in blocked:
            continue
        findings.append(
            WriteSafetyFinding(
                source="golden_three",
                code=str(code),
                severity=severity,
                message=_golden_three_message(str(code), report),
                evidence=_golden_three_evidence(report),
                payload={
                    "chapters_checked": int(getattr(report, "chapters_checked", 0) or 0),
                    "strong_hype_chapters": int(
                        getattr(report, "strong_hype_chapters", 0) or 0
                    ),
                    "ending_hook_chapters": int(
                        getattr(report, "ending_hook_chapters", 0) or 0
                    ),
                },
            )
        )
    return tuple(findings)


def _golden_three_message(code: str, report: "GoldenThreeReport") -> str:
    checked = int(getattr(report, "chapters_checked", 0) or 0)
    strong = int(getattr(report, "strong_hype_chapters", 0) or 0)
    hooks = int(getattr(report, "ending_hook_chapters", 0) or 0)
    if code == "GOLDEN_THREE_LOW_HYPE":
        return (
            f"Golden-3 payoff starvation: only {strong}/{checked} of the "
            "opening chapters contain a classifiable hype signal."
        )
    if code == "GOLDEN_THREE_WEAK_ENDING_HOOKS":
        return (
            f"Golden-3 ending-hook starvation: only {hooks}/{checked} of the "
            "opening chapters close on a hook the reader can feel."
        )
    if code == "GOLDEN_THREE_WEAK_OPEN_CONFLICT":
        return (
            "Golden-3 opening chapters missing explicit stakes / conflict "
            "keywords — readers will bounce before chapter 4."
        )
    if code == "GOLDEN_THREE_INCOMPLETE":
        return (
            f"Golden-3 coverage incomplete: only {checked} of the first 3 "
            "chapters have text available for analysis."
        )
    return f"Golden-3 issue: {code}"


def _golden_three_evidence(report: "GoldenThreeReport") -> str:
    signals = getattr(report, "chapter_signals", ()) or ()
    fragments: list[str] = []
    for signal in signals:
        code_set = ",".join(getattr(signal, "issue_codes", ()) or ())
        if code_set:
            fragments.append(f"ch{signal.chapter_number}:{code_set}")
    return "; ".join(fragments)


def serialize_write_safety_findings(
    findings: Iterable[WriteSafetyFinding],
) -> list[dict[str, Any]]:
    return [
        {
            "source": finding.source,
            "code": finding.code,
            "severity": finding.severity,
            "message": finding.message,
            "evidence": finding.evidence,
            "payload": dict(finding.payload),
        }
        for finding in findings
    ]


def describe_write_safety_findings(
    findings: Sequence[WriteSafetyFinding],
    *,
    limit: int = 3,
) -> str:
    shown = findings[: max(0, limit)]
    summary = "; ".join(
        f"[{finding.source}:{finding.code}:{finding.severity}] {finding.message}"
        for finding in shown
    )
    remaining = len(findings) - len(shown)
    if remaining > 0:
        summary = f"{summary}; +{remaining} more"
    return summary


def assert_no_write_safety_blocks(
    findings: Sequence[WriteSafetyFinding],
    *,
    project_slug: str,
    chapter_number: int,
    scene_number: int,
) -> None:
    if not findings:
        return
    summary = describe_write_safety_findings(findings)
    raise WriteSafetyBlockError(
        (
            f"Scene {project_slug} {chapter_number}.{scene_number} blocked by "
            f"write-safety gate: {summary}"
        ),
        findings=findings,
    )


__all__ = [
    "WriteSafetyBlockError",
    "WriteSafetyFinding",
    "assert_no_write_safety_blocks",
    "describe_write_safety_findings",
    "findings_from_contradiction_result",
    "findings_from_identity_violations",
    "serialize_write_safety_findings",
]
