"""Phase A1 — Unified Checker Schema.

Every audit surface (bible gate, output validator, chapter validator,
pacing engine, hype engine, line tracker, continuity) emits the same
``CheckerReport`` dataclass so the scorecard layer can aggregate across
checkers without per-checker adapters.

This schema was borrowed from lingfengQAQ/webnovel-writer's
``checker-output-schema.md`` and adapted to our 4-layer narrative model
and the Override Contract / Debt Ledger vocabulary used by Phase C.

Shape (frozen, JSON round-trippable):

    CheckerReport {
        agent: "consistency-checker" / "pacing-checker" / ...
        chapter: 1-based chapter number
        overall_score: 0-100
        passed: bool
        issues: tuple[CheckerIssue, ...]
        metrics: Mapping[str, Any]
        summary: str
        hard_violations: tuple[CheckerIssue, ...]      # can_override=False
        soft_suggestions: tuple[CheckerIssue, ...]     # can_override=True
    }

Severity convention:
    critical → hard block, regen must fix
    high     → hard block when can_override=False; soft audit otherwise
    medium   → soft audit with optional override
    low      → info only, never blocks
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
import json
from typing import Any, Literal

Severity = Literal["critical", "high", "medium", "low"]
GateVerdictStatus = Literal["pass", "warn_only", "blocked", "not_run", "error"]


# ---------------------------------------------------------------------------
# CheckerIssue — one finding from one checker.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CheckerIssue:
    """A single finding from an audit checker.

    ``id`` is a stable code (e.g. ``"HARD_001"``, ``"SOFT_HOOK_STRENGTH"``)
    consumed by ``write_gate.resolve_mode`` to decide block/audit/override.

    ``can_override = False`` marks hard invariants (countdown arithmetic,
    bible completeness, CJK leak). ``True`` marks soft rules that allow an
    Override Contract with a signed rationale + payback plan (Phase C).

    ``allowed_rationales`` is the whitelist of ``RationaleType`` codes the
    author may cite when opening an override; empty when override is not
    applicable. Stored as a tuple of strings (not the enum) so this module
    has zero dependency on ``override_contract`` and can be imported from
    any layer.
    """

    id: str
    type: str
    severity: Severity
    location: str
    description: str
    suggestion: str
    can_override: bool
    allowed_rationales: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "severity": self.severity,
            "location": self.location,
            "description": self.description,
            "suggestion": self.suggestion,
            "can_override": self.can_override,
            "allowed_rationales": list(self.allowed_rationales),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> CheckerIssue:
        return cls(
            id=str(data["id"]),
            type=str(data["type"]),
            severity=_coerce_severity(data.get("severity", "medium")),
            location=str(data.get("location", "")),
            description=str(data.get("description", "")),
            suggestion=str(data.get("suggestion", "")),
            can_override=bool(data.get("can_override", False)),
            allowed_rationales=tuple(str(r) for r in data.get("allowed_rationales", ())),
        )


# ---------------------------------------------------------------------------
# CheckerReport — one checker's run on one chapter.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CheckerReport:
    """Unified envelope every audit checker returns."""

    agent: str
    chapter: int
    overall_score: int
    passed: bool
    issues: tuple[CheckerIssue, ...] = ()
    metrics: Mapping[str, Any] = field(default_factory=dict)
    summary: str = ""
    hard_violations: tuple[CheckerIssue, ...] = ()
    soft_suggestions: tuple[CheckerIssue, ...] = ()

    def __post_init__(self) -> None:
        # If the caller didn't pre-partition, fill hard/soft from issues.
        # Uses object.__setattr__ because this is a frozen dataclass.
        if not self.hard_violations and not self.soft_suggestions and self.issues:
            hard = tuple(i for i in self.issues if not i.can_override)
            soft = tuple(i for i in self.issues if i.can_override)
            object.__setattr__(self, "hard_violations", hard)
            object.__setattr__(self, "soft_suggestions", soft)

    @property
    def has_hard_violations(self) -> bool:
        return bool(self.hard_violations)

    @property
    def blocks_write(self) -> bool:
        """True when the report should stop a write — any hard + any
        critical issue. Soft-only reports never block."""
        if self.hard_violations:
            return True
        return any(i.severity == "critical" for i in self.issues)

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent": self.agent,
            "chapter": self.chapter,
            "overall_score": self.overall_score,
            "passed": self.passed,
            "issues": [i.to_dict() for i in self.issues],
            "metrics": dict(self.metrics),
            "summary": self.summary,
            "hard_violations": [i.to_dict() for i in self.hard_violations],
            "soft_suggestions": [i.to_dict() for i in self.soft_suggestions],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> CheckerReport:
        issues = tuple(CheckerIssue.from_dict(i) for i in data.get("issues", ()))
        hard = tuple(CheckerIssue.from_dict(i) for i in data.get("hard_violations", ()))
        soft = tuple(CheckerIssue.from_dict(i) for i in data.get("soft_suggestions", ()))
        return cls(
            agent=str(data["agent"]),
            chapter=int(data["chapter"]),
            overall_score=int(data.get("overall_score", 0)),
            passed=bool(data.get("passed", False)),
            issues=issues,
            metrics=dict(data.get("metrics", {})),
            summary=str(data.get("summary", "")),
            hard_violations=hard,
            soft_suggestions=soft,
        )

    @classmethod
    def from_json(cls, payload: str) -> CheckerReport:
        return cls.from_dict(json.loads(payload))


# ---------------------------------------------------------------------------
# GateVerdict — system-wide gate signal contract.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GateFinding:
    """One finding in the GateVerdict v2 schema."""

    code: str
    severity: Severity
    message: str
    path: str = ""
    repair_action: str = ""

    @property
    def critical(self) -> bool:
        return self.severity == "critical"

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "path": self.path,
            "repair_action": self.repair_action,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> GateFinding:
        return cls(
            code=str(data.get("code") or data.get("id") or "finding"),
            severity=_coerce_severity(data.get("severity", "medium")),
            message=str(data.get("message") or data.get("description") or ""),
            path=str(data.get("path") or data.get("location") or ""),
            repair_action=str(data.get("repair_action") or data.get("suggestion") or ""),
        )


@dataclass(frozen=True)
class GateVerdict:
    """Unified v2 gate verdict.

    ``passed`` is derived, not caller-controlled: only full pass with
    coverage >= 0.95 can be treated as a green light.
    """

    gate_name: str
    verdict: GateVerdictStatus
    coverage: float
    findings: tuple[GateFinding, ...] = ()
    metrics: Mapping[str, Any] = field(default_factory=dict)
    schema_version: str = "gate-verdict.v2"
    summary: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "coverage", _clamp_coverage(self.coverage))

    @property
    def passed(self) -> bool:
        return self.verdict == "pass" and self.coverage >= 0.95

    @property
    def critical(self) -> bool:
        return self.verdict in {"blocked", "error"} or any(f.critical for f in self.findings)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "gate_name": self.gate_name,
            "verdict": self.verdict,
            "coverage": self.coverage,
            "passed": self.passed,
            "critical": self.critical,
            "findings": [finding.to_dict() for finding in self.findings],
            "metrics": dict(self.metrics),
            "summary": self.summary,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> GateVerdict:
        findings = tuple(GateFinding.from_dict(item) for item in data.get("findings", ()))
        verdict = _coerce_gate_status(data.get("verdict"))
        coverage = _float_or_default(data.get("coverage"), 1.0 if verdict == "pass" else 0.0)
        return cls(
            gate_name=str(data.get("gate_name") or data.get("agent") or "unknown_gate"),
            verdict=verdict,
            coverage=coverage,
            findings=findings,
            metrics=dict(data.get("metrics", {})),
            schema_version=str(data.get("schema_version") or "gate-verdict.v2"),
            summary=str(data.get("summary") or ""),
        )

    @classmethod
    def from_checker_report(cls, report: CheckerReport) -> GateVerdict:
        findings = tuple(
            GateFinding(
                code=issue.id,
                severity=issue.severity,
                message=issue.description,
                path=issue.location,
                repair_action=issue.suggestion,
            )
            for issue in report.issues
        )
        verdict: GateVerdictStatus
        if report.blocks_write:
            verdict = "blocked"
        elif report.passed:
            verdict = "pass"
        else:
            verdict = "warn_only"
        return cls(
            gate_name=report.agent,
            verdict=verdict,
            coverage=_clamp_coverage(report.overall_score / 100),
            findings=findings,
            metrics=report.metrics,
            summary=report.summary,
        )


@dataclass(frozen=True)
class AggregateGateReport:
    """Composite gate output backed by component GateVerdicts."""

    gate_name: str
    components: tuple[GateVerdict, ...]
    schema_version: str = "aggregate-gate-report.v1"
    summary: str = ""

    @property
    def coverage(self) -> float:
        if not self.components:
            return 0.0
        return min(component.coverage for component in self.components)

    @property
    def overall_score(self) -> int:
        return round(self.coverage * 100)

    @property
    def readiness(self) -> str:
        if any(component.critical for component in self.components):
            return "blocked"
        return "not_blocked"

    @property
    def verdict(self) -> GateVerdictStatus:
        if not self.components:
            return "not_run"
        if any(component.verdict == "error" for component in self.components):
            return "error"
        if any(component.critical for component in self.components):
            return "blocked"
        if all(component.passed for component in self.components):
            return "pass"
        return "warn_only"

    @property
    def passed(self) -> bool:
        return self.verdict == "pass" and self.coverage >= 0.95

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "gate_name": self.gate_name,
            "verdict": self.verdict,
            "coverage": self.coverage,
            "overall_score": self.overall_score,
            "readiness": self.readiness,
            "passed": self.passed,
            "components": [component.to_dict() for component in self.components],
            "summary": self.summary,
        }


def classify_gate_verdict(
    *,
    gate_name: str,
    findings: Iterable[GateFinding | Mapping[str, Any]] = (),
    coverage: float = 1.0,
    metrics: Mapping[str, Any] | None = None,
    applied: bool | None = None,
    summary: str = "",
) -> GateVerdict:
    """Classify a gate run and demote false-green patterns to warn_only."""

    normalized_findings = tuple(
        item if isinstance(item, GateFinding) else GateFinding.from_dict(item)
        for item in findings
    )
    metric_map = dict(metrics or {})
    if any(f.critical for f in normalized_findings):
        verdict: GateVerdictStatus = "blocked"
    elif normalized_findings:
        verdict = "warn_only"
    else:
        verdict = "pass"

    quality_score = _float_or_default(metric_map.get("quality_score"), 100.0)
    readiness = str(metric_map.get("readiness") or "").lower()
    if verdict == "pass" and (
        coverage < 0.95
        or quality_score < 70
        or readiness == "blocked"
        or applied is False
        or metric_map.get("applied") is False
    ):
        verdict = "warn_only"

    return GateVerdict(
        gate_name=gate_name,
        verdict=verdict,
        coverage=coverage,
        findings=normalized_findings,
        metrics=metric_map,
        summary=summary,
    )


# ---------------------------------------------------------------------------
# Aggregation helpers — used by Phase A1 scorecard update.
# ---------------------------------------------------------------------------


def merge_reports(reports: Iterable[CheckerReport]) -> tuple[CheckerReport, ...]:
    """Normalize a heterogeneous iterable of reports into a tuple.

    Nothing fancy — this is the contract point where callers confirm that
    everything handed to ``scorecard`` is already CheckerReport-shaped.
    """

    out: list[CheckerReport] = []
    for r in reports:
        if isinstance(r, CheckerReport):
            out.append(r)
        elif isinstance(r, Mapping):
            out.append(CheckerReport.from_dict(r))
        else:
            raise TypeError(
                f"merge_reports: expected CheckerReport or Mapping, got {type(r).__name__}"
            )
    return tuple(out)


def merge_gate_verdicts(
    verdicts: Iterable[GateVerdict | Mapping[str, Any]],
) -> tuple[GateVerdict, ...]:
    """Normalize component gate verdict payloads for aggregate gates."""

    out: list[GateVerdict] = []
    for verdict in verdicts:
        if isinstance(verdict, GateVerdict):
            out.append(verdict)
        elif isinstance(verdict, Mapping):
            out.append(GateVerdict.from_dict(verdict))
        else:
            raise TypeError(
                "merge_gate_verdicts: expected GateVerdict or Mapping, "
                f"got {type(verdict).__name__}"
            )
    return tuple(out)


def aggregate_issue_counts(
    reports: Iterable[CheckerReport],
) -> dict[str, int]:
    """Count issues by ``id`` across all reports. Used by scorecard to
    surface the top violation codes driving the quality score down."""

    counts: dict[str, int] = {}
    for r in reports:
        for issue in r.issues:
            counts[issue.id] = counts.get(issue.id, 0) + 1
    return counts


def partition_by_chapter(
    reports: Iterable[CheckerReport],
) -> dict[int, tuple[CheckerReport, ...]]:
    """Bucket reports by chapter number — scorecard uses this to know
    which chapters are "blocked" (≥1 hard violation) vs clean."""

    bucket: dict[int, list[CheckerReport]] = {}
    for r in reports:
        bucket.setdefault(r.chapter, []).append(r)
    return {ch: tuple(rs) for ch, rs in bucket.items()}


def blocked_chapters(reports: Iterable[CheckerReport]) -> frozenset[int]:
    """Chapters with ≥1 blocking report (hard violation or critical issue)."""

    out: set[int] = set()
    for r in reports:
        if r.blocks_write:
            out.add(r.chapter)
    return frozenset(out)


# ---------------------------------------------------------------------------
# Internal helpers.
# ---------------------------------------------------------------------------


_VALID_SEVERITIES = frozenset({"critical", "high", "medium", "low"})


def _coerce_severity(value: object) -> Severity:
    s = str(value).lower()
    if s not in _VALID_SEVERITIES:
        return "medium"
    return s  # type: ignore[return-value]


_VALID_GATE_STATUSES = frozenset({"pass", "warn_only", "blocked", "not_run", "error"})


def _coerce_gate_status(value: object) -> GateVerdictStatus:
    status = str(value or "not_run").lower()
    if status in {"passed", "success", "ready"}:
        status = "pass"
    if status in {"failed", "fail", "critical"}:
        status = "blocked"
    if status not in _VALID_GATE_STATUSES:
        return "not_run"
    return status  # type: ignore[return-value]


def _clamp_coverage(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _float_or_default(value: object, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
