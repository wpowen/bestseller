"""Hook ledger view + audit layer (Step 4 of methodology v2 restructure).

This module is a **thin view over existing ``ClueModel`` data**, not a new
storage system. It implements the 5 hook lifecycle rules from the unified
methodology inventory:

* ``wm.hook_system.types`` — 5 hook types (information_gap / deadline /
  mystery / desire / threat).
* ``wm.hook_system.active_count`` — keep 3-7 active hooks at any given
  chapter.
* ``wm.hook_system.per_chapter_balance`` — each chapter plants ≥1 and
  resolves ≥1.
* ``wm.hook_system.max_age`` — hooks older than 15 chapters are
  considered forgotten / dead.
* ``wm.emotion_engineering.next_compression_seed`` — after a payoff,
  immediately plant the next compression seed.

The module is intentionally infra-decoupled: it consumes any object
implementing :class:`ClueLike` (a Protocol mirroring the relevant fields
of ``infra.db.models.ClueModel``). This keeps the audit logic unit
testable without a database and lets us wire the same primitives into
planner / review / repair later without circular imports.

Activation: gated by ``BESTSELLER_METHODOLOGY_V2=1`` for production
callers via :func:`is_methodology_v2_enabled`. Direct callers (e.g.
unit tests) may invoke audit functions unconditionally; the flag is
only enforced at the planner/review integration points (Step 5).
"""
from __future__ import annotations

import os
from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol

# Defaults from inventory rules (Step 1 evidence).
DEFAULT_MAX_AGE_CHAPTERS = 15  # wm.hook_system.max_age
DEFAULT_ACTIVE_COUNT_MIN = 3  # wm.hook_system.active_count lower bound
DEFAULT_ACTIVE_COUNT_MAX = 7  # wm.hook_system.active_count upper bound

_FEATURE_FLAG_ENV = "BESTSELLER_METHODOLOGY_V2"


class HookType(str, Enum):
    """Five canonical hook types from wm.hook_system.hook_types."""

    INFORMATION_GAP = "information_gap"  # 信息差
    DEADLINE = "deadline"  # 截止日期
    MYSTERY = "mystery"  # 悬念
    DESIRE = "desire"  # 欲望
    THREAT = "threat"  # 威胁


class HookStatus(str, Enum):
    """Computed (not stored) lifecycle state for a hook."""

    ACTIVE = "active"  # planted, not yet paid, within max age
    RESOLVED = "resolved"  # actual_paid_off_chapter_number is set
    OVERDUE = "overdue"  # planted, not paid, past max age
    CANCELLED = "cancelled"  # explicitly cancelled


class ClueLike(Protocol):
    """Structural interface for ``ClueModel``-like records.

    The module reads only these attributes; any duck-typed object works
    (dataclass, dict-wrapper, ORM row, in-memory test fixture).
    """

    clue_code: str
    clue_type: str
    planted_in_chapter_number: int | None
    expected_payoff_by_chapter_number: int | None
    actual_paid_off_chapter_number: int | None
    status: str
    metadata_json: dict[str, Any]


# ---------------------------------------------------------------------------
# Feature flag.
# ---------------------------------------------------------------------------


def is_methodology_v2_enabled() -> bool:
    """Return True when methodology v2 (this module's audits) should run.

    Defaults to False to avoid disturbing in-flight chapter generation
    (e.g. 道种破虚 217-230 on legacy pipeline).
    """
    return os.environ.get(_FEATURE_FLAG_ENV, "0") == "1"


# ---------------------------------------------------------------------------
# Classification.
# ---------------------------------------------------------------------------

# Existing ClueModel.clue_type values observed in the codebase: ``foreshadow``,
# others are project-defined. The map below is the best-effort projection from
# legacy clue types to the canonical five hook types. Project-specific override
# is supported via ``clue.metadata_json["hook_type"]``.
_CLUE_TYPE_TO_HOOK_TYPE: dict[str, HookType] = {
    "foreshadow": HookType.INFORMATION_GAP,
    "information_gap": HookType.INFORMATION_GAP,
    "deadline": HookType.DEADLINE,
    "countdown": HookType.DEADLINE,
    "mystery": HookType.MYSTERY,
    "puzzle": HookType.MYSTERY,
    "desire": HookType.DESIRE,
    "promise": HookType.DESIRE,
    "threat": HookType.THREAT,
    "danger": HookType.THREAT,
}


def classify_hook_type(clue: ClueLike) -> HookType:
    """Classify a clue into one of the five hook types.

    Resolution order:

    1. ``clue.metadata_json["hook_type"]`` if it parses as a HookType.
    2. ``clue.clue_type`` mapped via :data:`_CLUE_TYPE_TO_HOOK_TYPE`.
    3. Fallback to :attr:`HookType.INFORMATION_GAP` (the most generic).
    """
    metadata = clue.metadata_json or {}
    meta_type = metadata.get("hook_type")
    if isinstance(meta_type, str):
        try:
            return HookType(meta_type)
        except ValueError:
            pass
    return _CLUE_TYPE_TO_HOOK_TYPE.get(clue.clue_type, HookType.INFORMATION_GAP)


# ---------------------------------------------------------------------------
# View.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HookLedgerEntry:
    """Immutable view of a single hook at a given chapter snapshot."""

    clue_code: str
    hook_type: HookType
    label: str
    planted_chapter: int | None
    expected_payoff_chapter: int | None
    actual_paid_chapter: int | None
    hook_status: HookStatus
    age_chapters: int | None  # None when planted_chapter is unknown

    @property
    def is_active(self) -> bool:
        return self.hook_status == HookStatus.ACTIVE

    @property
    def is_resolved(self) -> bool:
        return self.hook_status == HookStatus.RESOLVED

    @property
    def is_overdue(self) -> bool:
        return self.hook_status == HookStatus.OVERDUE

    @property
    def is_cancelled(self) -> bool:
        return self.hook_status == HookStatus.CANCELLED


def _compute_hook_status(
    clue: ClueLike,
    current_chapter: int,
    max_age: int,
) -> HookStatus:
    if clue.status == "cancelled":
        return HookStatus.CANCELLED
    if clue.actual_paid_off_chapter_number is not None:
        return HookStatus.RESOLVED
    if clue.planted_in_chapter_number is None:
        # Planted but chapter unknown: treat as active so it's visible
        # to the active_count audit (under-specified data, not silent drop).
        return HookStatus.ACTIVE
    age = current_chapter - clue.planted_in_chapter_number
    if age > max_age:
        return HookStatus.OVERDUE
    return HookStatus.ACTIVE


def build_hook_ledger_view(
    clues: Iterable[ClueLike],
    *,
    current_chapter: int,
    max_age_chapters: int = DEFAULT_MAX_AGE_CHAPTERS,
) -> tuple[HookLedgerEntry, ...]:
    """Build an immutable snapshot view of all hooks at ``current_chapter``.

    Hooks planted *after* ``current_chapter`` (future planning) are
    included but reported with ``age_chapters`` as a negative number; the
    audits ignore them where appropriate (e.g. active count counts them
    as active, since they will become active by the time the chapter is
    rendered).
    """
    entries: list[HookLedgerEntry] = []
    for clue in clues:
        status = _compute_hook_status(clue, current_chapter, max_age_chapters)
        age: int | None
        if clue.planted_in_chapter_number is None:
            age = None
        else:
            age = current_chapter - clue.planted_in_chapter_number
        label = getattr(clue, "label", "") or ""
        entries.append(
            HookLedgerEntry(
                clue_code=clue.clue_code,
                hook_type=classify_hook_type(clue),
                label=label,
                planted_chapter=clue.planted_in_chapter_number,
                expected_payoff_chapter=clue.expected_payoff_by_chapter_number,
                actual_paid_chapter=clue.actual_paid_off_chapter_number,
                hook_status=status,
                age_chapters=age,
            )
        )
    return tuple(entries)


# ---------------------------------------------------------------------------
# Audit result types.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AuditFinding:
    """One observable issue found by an audit function."""

    code: str
    severity: str  # "info" / "warn" / "block"
    detail: str
    evidence: dict[str, Any]


@dataclass(frozen=True)
class ActiveCountAudit:
    active_count: int
    min_expected: int
    max_expected: int
    findings: tuple[AuditFinding, ...]


@dataclass(frozen=True)
class PerChapterBalanceAudit:
    plant_count: int
    resolve_count: int
    findings: tuple[AuditFinding, ...]


@dataclass(frozen=True)
class MaxAgeAudit:
    overdue_entries: tuple[HookLedgerEntry, ...]
    max_age_chapters: int
    findings: tuple[AuditFinding, ...]


@dataclass(frozen=True)
class NextCompressionSeedAudit:
    previous_chapter_had_payoff: bool
    current_chapter_planted_new: bool
    findings: tuple[AuditFinding, ...]


# ---------------------------------------------------------------------------
# Audit functions (each rule a separate function, see inventory mapping).
# ---------------------------------------------------------------------------


def audit_active_hook_count(
    view: tuple[HookLedgerEntry, ...],
    *,
    current_chapter: int,
    min_expected: int = DEFAULT_ACTIVE_COUNT_MIN,
    max_expected: int = DEFAULT_ACTIVE_COUNT_MAX,
) -> ActiveCountAudit:
    """Enforce ``wm.hook_system.active_count``: keep 3-7 active hooks.

    Too few hooks → no suspense; too many → reader can't track them.
    """
    active = [e for e in view if e.is_active]
    findings: list[AuditFinding] = []
    n = len(active)
    if n < min_expected:
        findings.append(
            AuditFinding(
                code="HOOK_ACTIVE_COUNT_TOO_LOW",
                severity="warn",
                detail=(
                    f"活跃钩子 {n} 个，低于下限 {min_expected}，悬念不足"
                ),
                evidence={
                    "active_codes": [e.clue_code for e in active],
                    "chapter": current_chapter,
                },
            )
        )
    elif n > max_expected:
        findings.append(
            AuditFinding(
                code="HOOK_ACTIVE_COUNT_TOO_HIGH",
                severity="warn",
                detail=(
                    f"活跃钩子 {n} 个，高于上限 {max_expected}，读者记不住"
                ),
                evidence={
                    "active_codes": [e.clue_code for e in active],
                    "chapter": current_chapter,
                },
            )
        )
    return ActiveCountAudit(
        active_count=n,
        min_expected=min_expected,
        max_expected=max_expected,
        findings=tuple(findings),
    )


def audit_per_chapter_balance(
    view: tuple[HookLedgerEntry, ...],
    *,
    current_chapter: int,
) -> PerChapterBalanceAudit:
    """Enforce ``wm.hook_system.per_chapter_balance``: ≥1 plant + ≥1 resolve.

    Counts plants and resolves that landed on ``current_chapter`` exactly.
    """
    plants = [e for e in view if e.planted_chapter == current_chapter]
    resolves = [e for e in view if e.actual_paid_chapter == current_chapter]
    findings: list[AuditFinding] = []
    if not plants:
        findings.append(
            AuditFinding(
                code="HOOK_PER_CHAPTER_NO_PLANT",
                severity="warn",
                detail=f"第 {current_chapter} 章未植入新钩子",
                evidence={"chapter": current_chapter},
            )
        )
    if not resolves:
        findings.append(
            AuditFinding(
                code="HOOK_PER_CHAPTER_NO_RESOLVE",
                severity="warn",
                detail=f"第 {current_chapter} 章未消解任何旧钩子",
                evidence={"chapter": current_chapter},
            )
        )
    return PerChapterBalanceAudit(
        plant_count=len(plants),
        resolve_count=len(resolves),
        findings=tuple(findings),
    )


def audit_max_age(
    view: tuple[HookLedgerEntry, ...],
    *,
    current_chapter: int,
    max_age_chapters: int = DEFAULT_MAX_AGE_CHAPTERS,
) -> MaxAgeAudit:
    """Enforce ``wm.hook_system.max_age``: 15 chapters is the dead-hook line.

    Note: the view's ``hook_status`` already reflects ``max_age`` because
    :func:`build_hook_ledger_view` accepts the same parameter; this audit
    simply gathers the OVERDUE entries and emits a single finding when
    any exist.
    """
    overdue = tuple(e for e in view if e.is_overdue)
    findings: list[AuditFinding] = []
    if overdue:
        findings.append(
            AuditFinding(
                code="HOOK_OVERDUE",
                severity="warn",
                detail=(
                    f"{len(overdue)} 个钩子超过 {max_age_chapters} 章存活期，"
                    "已被读者遗忘"
                ),
                evidence={
                    "overdue_codes": [e.clue_code for e in overdue],
                    "ages": [e.age_chapters for e in overdue],
                    "chapter": current_chapter,
                },
            )
        )
    return MaxAgeAudit(
        overdue_entries=overdue,
        max_age_chapters=max_age_chapters,
        findings=tuple(findings),
    )


def audit_next_compression_seed(
    view: tuple[HookLedgerEntry, ...],
    *,
    current_chapter: int,
) -> NextCompressionSeedAudit:
    """Enforce ``wm.emotion_engineering.next_compression_seed``.

    Rule: after the previous chapter delivered a payoff, the current
    chapter must plant at least one new hook ("compression seed") so
    that the reader's emotional flywheel does not stop.
    """
    prev = current_chapter - 1
    prev_had_payoff = any(e.actual_paid_chapter == prev for e in view)
    plants_this_chapter = [e for e in view if e.planted_chapter == current_chapter]
    findings: list[AuditFinding] = []
    if prev_had_payoff and not plants_this_chapter:
        findings.append(
            AuditFinding(
                code="HOOK_NEXT_COMPRESSION_SEED_MISSING",
                severity="warn",
                detail=(
                    f"上一章({prev})有 payoff 但本章({current_chapter})"
                    "未植入新压缩种子"
                ),
                evidence={
                    "prev_chapter": prev,
                    "current_chapter": current_chapter,
                },
            )
        )
    return NextCompressionSeedAudit(
        previous_chapter_had_payoff=prev_had_payoff,
        current_chapter_planted_new=bool(plants_this_chapter),
        findings=tuple(findings),
    )


# ---------------------------------------------------------------------------
# Aggregate.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HookLedgerAudit:
    """One-shot full audit result. Exposes per-audit detail + aggregates."""

    view: tuple[HookLedgerEntry, ...]
    active_count: ActiveCountAudit
    per_chapter_balance: PerChapterBalanceAudit
    max_age: MaxAgeAudit
    next_compression_seed: NextCompressionSeedAudit

    @property
    def all_findings(self) -> tuple[AuditFinding, ...]:
        return (
            self.active_count.findings
            + self.per_chapter_balance.findings
            + self.max_age.findings
            + self.next_compression_seed.findings
        )

    @property
    def closure_rate(self) -> float:
        """``payoff_ledger_closure_rate`` indicator.

        ``resolved / (resolved + active + overdue)``. Cancelled hooks
        are excluded because they no longer carry a promise.
        Returns 1.0 when no hooks exist (vacuous closure).
        """
        denom = sum(1 for e in self.view if not e.is_cancelled)
        if denom == 0:
            return 1.0
        resolved = sum(1 for e in self.view if e.is_resolved)
        return resolved / denom

    def by_type(self) -> dict[HookType, int]:
        """Return active-hook count broken down by :class:`HookType`."""
        counts: dict[HookType, int] = {t: 0 for t in HookType}
        for e in self.view:
            if e.is_active:
                counts[e.hook_type] += 1
        return counts


def run_hook_ledger_audit(
    clues: Iterable[ClueLike],
    *,
    current_chapter: int,
    max_age_chapters: int = DEFAULT_MAX_AGE_CHAPTERS,
    min_active: int = DEFAULT_ACTIVE_COUNT_MIN,
    max_active: int = DEFAULT_ACTIVE_COUNT_MAX,
) -> HookLedgerAudit:
    """Run the full audit suite for a single chapter snapshot.

    Convenience entry point that builds the view once and shares it
    across the four audit functions.
    """
    view = build_hook_ledger_view(
        clues,
        current_chapter=current_chapter,
        max_age_chapters=max_age_chapters,
    )
    return HookLedgerAudit(
        view=view,
        active_count=audit_active_hook_count(
            view,
            current_chapter=current_chapter,
            min_expected=min_active,
            max_expected=max_active,
        ),
        per_chapter_balance=audit_per_chapter_balance(
            view, current_chapter=current_chapter
        ),
        max_age=audit_max_age(
            view,
            current_chapter=current_chapter,
            max_age_chapters=max_age_chapters,
        ),
        next_compression_seed=audit_next_compression_seed(
            view, current_chapter=current_chapter
        ),
    )


__all__ = [
    "DEFAULT_ACTIVE_COUNT_MAX",
    "DEFAULT_ACTIVE_COUNT_MIN",
    "DEFAULT_MAX_AGE_CHAPTERS",
    "ActiveCountAudit",
    "AuditFinding",
    "ClueLike",
    "HookLedgerAudit",
    "HookLedgerEntry",
    "HookStatus",
    "HookType",
    "MaxAgeAudit",
    "NextCompressionSeedAudit",
    "PerChapterBalanceAudit",
    "audit_active_hook_count",
    "audit_max_age",
    "audit_next_compression_seed",
    "audit_per_chapter_balance",
    "build_hook_ledger_view",
    "classify_hook_type",
    "is_methodology_v2_enabled",
    "run_hook_ledger_audit",
]
