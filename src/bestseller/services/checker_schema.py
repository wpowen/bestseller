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

import json
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable, Literal, Mapping


Severity = Literal["critical", "high", "medium", "low"]


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
    def from_dict(cls, data: Mapping[str, Any]) -> "CheckerIssue":
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
    def from_dict(cls, data: Mapping[str, Any]) -> "CheckerReport":
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
    def from_json(cls, payload: str) -> "CheckerReport":
        return cls.from_dict(json.loads(payload))


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


def _coerce_severity(value: Any) -> Severity:
    s = str(value).lower()
    if s not in _VALID_SEVERITIES:
        return "medium"
    return s  # type: ignore[return-value]
