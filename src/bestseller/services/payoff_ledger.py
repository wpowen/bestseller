"""Payoff ledger view + audit layer for methodology v2.

This is a thin, infra-free view over existing ``PayoffModel`` rows and their
optional source clues. The ledger answers one question for review and health
checks: did the story cash the promises it scheduled, at the right time, with
enough setup distance?
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol

from bestseller.services.hook_ledger import AuditFinding

DEFAULT_MIN_SETUP_DISTANCE_CHAPTERS = 2


class PayoffStatus(StrEnum):
    """Computed payoff lifecycle state at a chapter snapshot."""

    PLANNED = "planned"
    DUE = "due"
    RESOLVED = "resolved"
    OVERDUE = "overdue"
    CANCELLED = "cancelled"


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

    @property
    def is_planned(self) -> bool:
        return self.payoff_status == PayoffStatus.PLANNED

    @property
    def is_due(self) -> bool:
        return self.payoff_status == PayoffStatus.DUE

    @property
    def is_resolved(self) -> bool:
        return self.payoff_status == PayoffStatus.RESOLVED

    @property
    def is_overdue(self) -> bool:
        return self.payoff_status == PayoffStatus.OVERDUE

    @property
    def is_cancelled(self) -> bool:
        return self.payoff_status == PayoffStatus.CANCELLED


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
        anchor_chapter = payoff.actual_chapter_number or payoff.target_chapter_number
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
    min_setup_distance: int = DEFAULT_MIN_SETUP_DISTANCE_CHAPTERS,
) -> tuple[AuditFinding, ...]:
    """Flag resolved payoffs that were not set up far enough ahead."""

    too_short = tuple(
        entry
        for entry in view
        if entry.is_resolved
        and entry.actual_chapter == current_chapter
        and entry.setup_distance_chapters is not None
        and entry.setup_distance_chapters < min_setup_distance
    )
    if not too_short:
        return ()
    return (
        AuditFinding(
            code="PAYOFF_SETUP_TOO_SHORT",
            severity="warn",
            detail=(
                f"{len(too_short)} 个 payoff 的铺垫距离小于 "
                f"{min_setup_distance} 章，兑现缺少蓄力"
            ),
            evidence={
                "chapter": current_chapter,
                "min_setup_distance": min_setup_distance,
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
        denominator = sum(1 for entry in self.view if not entry.is_cancelled)
        if denominator == 0:
            return 1.0
        resolved = sum(1 for entry in self.view if entry.is_resolved)
        return resolved / denominator

    @property
    def due_count(self) -> int:
        return sum(1 for entry in self.view if entry.is_due)

    @property
    def overdue_count(self) -> int:
        return sum(1 for entry in self.view if entry.is_overdue)

    @property
    def resolved_current_chapter_count(self) -> int:
        return sum(1 for entry in self.view if entry.is_resolved)


def run_payoff_ledger_audit(
    payoffs: Iterable[PayoffLike],
    *,
    current_chapter: int,
    source_clues: Iterable[SourceClueLike] = (),
    min_setup_distance: int = DEFAULT_MIN_SETUP_DISTANCE_CHAPTERS,
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
