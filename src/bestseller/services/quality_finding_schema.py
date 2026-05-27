"""Normalized quality finding schema.

Every gate can keep its legacy payload, but the chapter pipeline needs one
shape for closure, dashboards, and export decisions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from bestseller.services.quality_contract_registry import contract_for_code

_BLOCKING_SEVERITIES = {"block", "critical", "major", "high"}


@dataclass(frozen=True)
class QualityFinding:
    code: str
    severity: str
    source: str
    chapter_number: int | None = None
    scene_number: int | None = None
    evidence: dict[str, Any] = field(default_factory=dict)
    repair_hint: str = ""
    repair_scope: str = "chapter"
    blocking: bool = True

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "code": self.code,
            "severity": self.severity,
            "source": self.source,
            "evidence": self.evidence,
            "repair_hint": self.repair_hint,
            "repair_scope": self.repair_scope,
            "blocking": self.blocking,
        }
        if self.chapter_number is not None:
            payload["chapter_number"] = self.chapter_number
        if self.scene_number is not None:
            payload["scene_number"] = self.scene_number
        return payload


def quality_finding_from_write_safety(
    finding: Any,
    *,
    chapter_number: int | None = None,
    scene_number: int | None = None,
    commercial_strict: bool = True,
) -> QualityFinding:
    code = str(getattr(finding, "code", "") or "UNKNOWN_WRITE_SAFETY")
    contract = contract_for_code(code, commercial_strict=commercial_strict)
    payload = getattr(finding, "payload", None)
    evidence: dict[str, Any] = dict(payload) if isinstance(payload, dict) else {}
    raw_evidence = str(getattr(finding, "evidence", "") or "")
    if raw_evidence and "text" not in evidence:
        evidence["text"] = raw_evidence
    severity = str(getattr(finding, "severity", "") or contract.severity)
    return QualityFinding(
        code=code,
        severity=severity,
        source=str(getattr(finding, "source", "") or "write_safety"),
        chapter_number=chapter_number,
        scene_number=scene_number,
        evidence=evidence,
        repair_hint=str(getattr(finding, "message", "") or contract.pass_condition),
        repair_scope=contract.repair_scope,
        blocking=severity.lower() in _BLOCKING_SEVERITIES,
    )


def quality_finding_from_retention(
    finding: Any,
    *,
    chapter_number: int | None = None,
    commercial_strict: bool = True,
) -> QualityFinding:
    code = str(getattr(finding, "code", "") or "UNKNOWN_RETENTION")
    contract = contract_for_code(code, commercial_strict=commercial_strict)
    raw_evidence = getattr(finding, "evidence", None)
    evidence: dict[str, Any] = dict(raw_evidence) if isinstance(raw_evidence, dict) else {}
    coverage = getattr(finding, "coverage", None)
    if coverage is not None:
        evidence["coverage"] = coverage
    exposition_ratio = getattr(finding, "exposition_ratio", None)
    if exposition_ratio is not None:
        evidence["exposition_ratio"] = exposition_ratio
    severity = str(getattr(finding, "severity", "") or contract.severity)
    return QualityFinding(
        code=code,
        severity=severity,
        source="retention_safety_gate",
        chapter_number=chapter_number,
        evidence=evidence,
        repair_hint=str(getattr(finding, "detail", "") or contract.pass_condition),
        repair_scope=contract.repair_scope,
        blocking=severity.lower() in _BLOCKING_SEVERITIES,
    )


def quality_findings_from_report_json(
    report_json: Mapping[str, Any],
    *,
    chapter_number: int | None = None,
    source: str = "chapter_quality_report",
    commercial_strict: bool = True,
) -> tuple[QualityFinding, ...]:
    blocking_codes = {
        str(code)
        for code in report_json.get("blocking_codes", ())
        if str(code).strip()
    }
    findings: list[QualityFinding] = []
    violations = report_json.get("violations", ())
    if isinstance(violations, list):
        for violation in violations:
            if not isinstance(violation, Mapping):
                continue
            code = str(violation.get("code") or "UNKNOWN_QUALITY_REPORT")
            contract = contract_for_code(code, commercial_strict=commercial_strict)
            severity = str(violation.get("severity") or contract.severity)
            findings.append(
                QualityFinding(
                    code=code,
                    severity=severity,
                    source=source,
                    chapter_number=chapter_number,
                    evidence=dict(violation),
                    repair_hint=str(violation.get("detail") or contract.pass_condition),
                    repair_scope=contract.repair_scope,
                    blocking=code in blocking_codes
                    or severity.lower() in {"block", "critical", "major"},
                )
            )
    for code in sorted(blocking_codes - {finding.code for finding in findings}):
        contract = contract_for_code(code, commercial_strict=commercial_strict)
        findings.append(
            QualityFinding(
                code=code,
                severity=contract.severity,
                source=source,
                chapter_number=chapter_number,
                evidence={"blocking_code": code},
                repair_hint=contract.pass_condition,
                repair_scope=contract.repair_scope,
                blocking=True,
            )
        )
    return tuple(findings)


def dump_quality_findings(
    findings: tuple[QualityFinding, ...] | list[QualityFinding],
) -> list[dict[str, Any]]:
    return [finding.to_dict() for finding in findings]


__all__ = [
    "QualityFinding",
    "dump_quality_findings",
    "quality_finding_from_retention",
    "quality_finding_from_write_safety",
    "quality_findings_from_report_json",
]
