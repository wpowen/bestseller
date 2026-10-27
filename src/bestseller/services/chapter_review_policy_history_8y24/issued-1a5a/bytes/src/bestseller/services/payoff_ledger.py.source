"""Payoff ledger view + audit layer for methodology v2.

This is a thin, infra-free view over existing ``PayoffModel`` rows and their
optional source clues. The ledger answers one question for review and health
checks: did the story cash the promises it scheduled, at the right time, with
enough setup distance?
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol

from bestseller.services.hook_ledger import AuditFinding
from bestseller.services.narrative_obligation_migration import is_reference_obligation

# Setup distance is a contract choice, not a universal story obligation.
DEFAULT_MIN_SETUP_DISTANCE_CHAPTERS = 0


class PayoffStatus(StrEnum):
    """Computed payoff lifecycle state at a chapter snapshot."""

    PLANNED = "planned"
    DUE = "due"
    RESOLVED = "resolved"
    OVERDUE = "overdue"
    CANCELLED = "cancelled"
    SUPERSEDED = "superseded"
    REFERENCE = "reference"


class PayoffLike(Protocol):
    """Structural interface for ``PayoffModel``-like records."""

    payoff_code: str
    label: str
    description: str
    source_clue_id: Any | None
    target_chapter_number: int | None
    actual_chapter_number: int | None
    status: str
    metadata_json: dict[str, Any]


class SourceClueLike(Protocol):
    """Structural interface for source clue rows used by payoff audits."""

    id: Any
    clue_code: str
    planted_in_chapter_number: int | None


@dataclass(frozen=True)
class PayoffLedgerEntry:
    payoff_code: str
    label: str
    description: str
    target_chapter: int | None
    actual_chapter: int | None
    source_clue_code: str | None
    source_planted_chapter: int | None
    payoff_status: PayoffStatus
    setup_distance_chapters: int | None
    is_reference: bool = False
    min_setup_distance: int | None = None

    @property
    def is_obligation(self) -> bool:
        return not self.is_reference and not self.is_cancelled and not self.is_superseded

    @property
    def is_planned(self) -> bool:
        return self.payoff_status == PayoffStatus.PLANNED

    @property
    def is_due(self) -> bool:
        return self.is_obligation and self.payoff_status == PayoffStatus.DUE

    @property
    def is_resolved(self) -> bool:
        return self.payoff_status == PayoffStatus.RESOLVED

    @property
    def is_overdue(self) -> bool:
        return self.is_obligation and self.payoff_status == PayoffStatus.OVERDUE

    @property
    def is_cancelled(self) -> bool:
        return self.payoff_status == PayoffStatus.CANCELLED

    @property
    def is_superseded(self) -> bool:
        return self.payoff_status == PayoffStatus.SUPERSEDED


def build_payoff_ledger_view(
    payoffs: Iterable[PayoffLike],
    *,
    current_chapter: int,
    source_clues: Iterable[SourceClueLike] = (),
) -> tuple[PayoffLedgerEntry, ...]:
    """Build an immutable payoff snapshot for ``current_chapter``."""

    clues_by_id = {str(clue.id): clue for clue in source_clues}
    entries: list[PayoffLedgerEntry] = []
    for payoff in payoffs:
        metadata = payoff.metadata_json or {}
        is_reference = is_reference_obligation({"status": payoff.status, "metadata_json": metadata})
        source_clue = clues_by_id.get(str(payoff.source_clue_id))
        source_clue_code = (
            source_clue.clue_code
            if source_clue is not None
            else _clean_optional_str(metadata.get("source_clue_code"))
        )
        source_planted_chapter = (
            source_clue.planted_in_chapter_number
            if source_clue is not None
            else _clean_optional_int(metadata.get("source_planted_chapter"))
        )
        status = _compute_payoff_status(payoff, current_chapter=current_chapter)
        anchor_chapter = (
            payoff.actual_chapter_number
            if payoff.actual_chapter_number is not None
            else payoff.target_chapter_number
        )
        setup_distance = (
            anchor_chapter - source_planted_chapter
            if anchor_chapter is not None and source_planted_chapter is not None
            else None
        )
        entries.append(
            PayoffLedgerEntry(
                payoff_code=payoff.payoff_code,
                label=payoff.label or "",
                description=payoff.description or "",
                target_chapter=payoff.target_chapter_number,
                actual_chapter=payoff.actual_chapter_number,
                source_clue_code=source_clue_code,
                source_planted_chapter=source_planted_chapter,
                payoff_status=status,
                setup_distance_chapters=setup_distance,
                is_reference=is_reference,
                min_setup_distance=_source_bound_min_setup_distance(metadata),
            )
        )
    return tuple(entries)


def audit_due_payoffs(
    view: tuple[PayoffLedgerEntry, ...],
    *,
    current_chapter: int,
) -> tuple[AuditFinding, ...]:
    """Find scheduled payoffs that are due or already late."""

    due = tuple(entry for entry in view if entry.is_due)
    overdue = tuple(entry for entry in view if entry.is_overdue)
    findings: list[AuditFinding] = []
    if due:
        findings.append(
            AuditFinding(
                code="PAYOFF_DUE_UNRESOLVED",
                severity="warn",
                detail=f"第 {current_chapter} 章有 {len(due)} 个 payoff 到期但未兑现",
                evidence={
                    "chapter": current_chapter,
                    "payoff_codes": [entry.payoff_code for entry in due],
                },
            )
        )
    if overdue:
        findings.append(
            AuditFinding(
                code="PAYOFF_OVERDUE",
                severity="block",
                detail=f"{len(overdue)} 个 payoff 已超过目标章节仍未兑现",
                evidence={
                    "chapter": current_chapter,
                    "payoff_codes": [entry.payoff_code for entry in overdue],
                    "target_chapters": [entry.target_chapter for entry in overdue],
                },
            )
        )
    return tuple(findings)


def audit_setup_distance(
    view: tuple[PayoffLedgerEntry, ...],
    *,
    current_chapter: int,
    min_setup_distance: int | None = None,
) -> tuple[AuditFinding, ...]:
    """Check only explicit setup-distance requirements.

    ``None``/zero adds no global requirement. A positive explicit API argument
    remains supported; it cannot relax a stronger source-bound entry contract.
    Same-chapter setup and payment are otherwise legitimate, including chapter 1.
    """

    if min_setup_distance is not None and (
        isinstance(min_setup_distance, bool)
        or not isinstance(min_setup_distance, int)
        or min_setup_distance < 0
    ):
        raise ValueError("min_setup_distance must be a non-negative integer or None")
    too_short: list[PayoffLedgerEntry] = []
    requirements: dict[str, int] = {}
    for entry in view:
        required = max(min_setup_distance or 0, entry.min_setup_distance or 0)
        if (
            required > 0
            and entry.is_obligation
            and entry.is_resolved
            and entry.actual_chapter == current_chapter
            and entry.setup_distance_chapters is not None
            and entry.setup_distance_chapters < required
        ):
            too_short.append(entry)
            requirements[entry.payoff_code] = required
    if not too_short:
        return ()
    return (
        AuditFinding(
            code="PAYOFF_SETUP_TOO_SHORT",
            severity="warn",
            detail=(f"{len(too_short)} 个 payoff 未满足显式合同约定的铺垫距离"),
            evidence={
                "chapter": current_chapter,
                "min_setup_distance": min_setup_distance,
                "required_setup_distances": requirements,
                "payoff_codes": [entry.payoff_code for entry in too_short],
                "setup_distances": [entry.setup_distance_chapters for entry in too_short],
            },
        ),
    )


@dataclass(frozen=True)
class PayoffLedgerAudit:
    view: tuple[PayoffLedgerEntry, ...]
    due_findings: tuple[AuditFinding, ...]
    setup_distance_findings: tuple[AuditFinding, ...]

    @property
    def all_findings(self) -> tuple[AuditFinding, ...]:
        return self.due_findings + self.setup_distance_findings

    @property
    def closure_rate(self) -> float:
        denominator = sum(1 for entry in self.view if entry.is_obligation)
        if denominator == 0:
            return 1.0
        resolved = sum(1 for entry in self.view if entry.is_obligation and entry.is_resolved)
        return resolved / denominator

    @property
    def due_count(self) -> int:
        return sum(1 for entry in self.view if entry.is_due)

    @property
    def overdue_count(self) -> int:
        return sum(1 for entry in self.view if entry.is_overdue)

    @property
    def resolved_current_chapter_count(self) -> int:
        return sum(1 for entry in self.view if entry.is_obligation and entry.is_resolved)


def run_payoff_ledger_audit(
    payoffs: Iterable[PayoffLike],
    *,
    current_chapter: int,
    source_clues: Iterable[SourceClueLike] = (),
    min_setup_distance: int | None = None,
) -> PayoffLedgerAudit:
    """Run payoff-ledger audits for a single chapter snapshot."""

    view = build_payoff_ledger_view(
        payoffs,
        current_chapter=current_chapter,
        source_clues=source_clues,
    )
    return PayoffLedgerAudit(
        view=view,
        due_findings=audit_due_payoffs(view, current_chapter=current_chapter),
        setup_distance_findings=audit_setup_distance(
            view,
            current_chapter=current_chapter,
            min_setup_distance=min_setup_distance,
        ),
    )


def _compute_payoff_status(
    payoff: PayoffLike,
    *,
    current_chapter: int,
) -> PayoffStatus:
    raw_status = (payoff.status or "").strip().lower()
    if raw_status == "reference":
        return PayoffStatus.REFERENCE
    if raw_status == "superseded":
        return PayoffStatus.SUPERSEDED
    if raw_status == "cancelled":
        return PayoffStatus.CANCELLED
    if payoff.actual_chapter_number is not None or raw_status in {
        "resolved",
        "paid",
        "complete",
        "completed",
    }:
        return PayoffStatus.RESOLVED
    if payoff.target_chapter_number is None:
        return PayoffStatus.PLANNED
    if payoff.target_chapter_number < current_chapter:
        return PayoffStatus.OVERDUE
    if payoff.target_chapter_number == current_chapter:
        return PayoffStatus.DUE
    return PayoffStatus.PLANNED


def _clean_optional_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _source_bound_min_setup_distance(metadata: Mapping[str, Any]) -> int | None:
    """Read an explicit distance, guarded by an exported source reference.

    Preferred form: ``obligation_contract: {source: {...}, min_setup_distance: N}``.
    A top-level distance may use that same guard, or a commitment's
    ``narrative_obligation.source``. Bare distances and empty source placeholders
    confer no authority. The producer is responsible for verifying the source;
    this offline view never manufactures an approval or a contract from prose.
    """
    contract = metadata.get("obligation_contract")
    if not isinstance(contract, Mapping):
        contract = metadata.get("narrative_obligation")
        if not isinstance(contract, Mapping) or contract.get("kind") != "commitment":
            return None
    source = contract.get("source")
    if not isinstance(source, Mapping) or not any(
        isinstance(value, str) and value.strip() for value in source.values()
    ):
        return None
    distance = contract.get("min_setup_distance", metadata.get("min_setup_distance"))
    if isinstance(distance, bool) or not isinstance(distance, int) or distance < 0:
        return None
    return distance


def _clean_optional_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


__all__ = [
    "DEFAULT_MIN_SETUP_DISTANCE_CHAPTERS",
    "PayoffLedgerAudit",
    "PayoffLedgerEntry",
    "PayoffLike",
    "PayoffStatus",
    "SourceClueLike",
    "audit_due_payoffs",
    "audit_setup_distance",
    "build_payoff_ledger_view",
    "run_payoff_ledger_audit",
]
