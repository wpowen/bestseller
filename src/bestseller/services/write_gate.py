"""L6 Pre-Write Gate.

Stands between a completed LLM draft and the disk / DB write. Takes a
``QualityReport`` from L4/L5 and decides — per ``mode_by_violation`` config —
whether any violation actually blocks the write.

Separation of concerns:
    * ``resolve_mode`` + ``assert_writable`` are pure logic (no DB, no side
      effects) so unit tests can exercise gate behavior without fixtures.
    * ``handle_blocked_chapter`` is the side-effectful wrapper that persists
      the quality report, flips chapter state to FAILED, and drops rejected
      drafts for human review. The caller wires these in when the pipeline
      has a session.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Literal, Mapping

from bestseller.services.output_validator import QualityReport, Violation


# Phase C1 — signature for the override lookup callback. Receives the
# violation ``code`` and the current ``chapter_no`` and returns True
# when an active Override Contract covers this violation for this
# chapter (meaning the gate should downgrade block → audit_only and let
# the write proceed while the debt ledger tracks the payback window).
OverrideLookup = Callable[[str, int | None], bool]


GateMode = Literal["block", "audit_only"]


# ---------------------------------------------------------------------------
# Exceptions.
# ---------------------------------------------------------------------------


class ChapterBlocked(Exception):
    """Raised when at least one effective-block violation survived regen.

    ``blocking_violations`` is the filtered subset actually responsible for
    the block — ``report.violations`` may also include ``audit_only`` findings
    which don't stop the write.
    """

    def __init__(
        self,
        chapter_no: int | None,
        report: QualityReport,
        blocking_violations: tuple[Violation, ...],
    ) -> None:
        self.chapter_no = chapter_no
        self.report = report
        self.blocking_violations = blocking_violations
        codes = ", ".join(v.code for v in blocking_violations) or "n/a"
        chap_str = f"chapter {chapter_no}" if chapter_no is not None else "draft"
        super().__init__(f"{chap_str} blocked by: {codes}")


# ---------------------------------------------------------------------------
# Config resolution.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GateConfig:
    """Resolved runtime config for the gate.

    ``mode_by_violation`` maps a violation ``code`` to the mode the gate
    should enforce for that code. ``default`` is the fallback when a code is
    not explicitly configured — Phase 1 uses ``audit_only`` so we don't halt
    production on unexpected new checks.
    """

    mode_by_violation: Mapping[str, GateMode]
    default: GateMode = "audit_only"


# Default ships the decisions from §9 of the architecture plan — the twelve
# codes we have strong signal on go to ``block``; softer checks stay
# ``audit_only`` until Phase 2 promotes them based on observed precision.
DEFAULT_GATE_CONFIG: GateConfig = GateConfig(
    mode_by_violation={
        "LANG_LEAK_CJK_IN_EN": "block",
        "LANG_LEAK_LATIN_IN_ZH": "block",
        "LENGTH_UNDER": "block",
        "LENGTH_OVER": "block",
        "DIALOG_UNPAIRED": "block",
        "REPEATED_EVENT_BEAT": "block",
        "CANON_FORBIDDEN_TERM": "block",
        "CANON_STATE_REGRESSION": "block",
        "CHAPTER_GAP": "block",
        "QUIRK_SLOT_MISSING": "block",
        "TAG_MEMORY_MISSING": "block",
        "CHARACTER_CONTRAST_MISSING": "block",
        "CORE_WOUND_MISSING": "block",
        "CHARACTER_PERSONHOOD_INCOMPLETE": "block",
        "ANTAGONIST_MOTIVE_OVERLAP": "block",
        "WORLD_TAXONOMY_BOILERPLATE": "block",
        "NAMING_POOL_UNDERSIZED": "block",
        "NAMING_OUT_OF_POOL": "audit_only",
        "OPENING_ENTITY_OVERLOAD": "audit_only",
        "POV_DRIFT": "audit_only",
        "CLIFFHANGER_REPEAT": "audit_only",
        # Phase A — character lifecycle
        "CHARACTER_RESURRECTION": "block",
        "STANCE_FLIP_UNJUSTIFIED": "block",
        "STANCE_FLIP_NO_ARC_BEAT": "block",
        "POWER_TIER_REGRESSION": "audit_only",
        # Phase A2 — independent life for supporting characters
        "INDEPENDENT_LIFE_MISSING": "audit_only",
        # Hype engine
        "HYPE_MISSING": "audit_only",
        "HYPE_REPEAT": "audit_only",
        "ENDING_SENTENCE_WEAK": "audit_only",
        "GOLDEN_THREE_WEAK": "audit_only",
        "PLEASURE_HYPE_GAP": "audit_only",
        "PLEASURE_COMEDIC_BEAT_STARVED": "audit_only",
        "PLEASURE_SETUP_PAYOFF_DEBT": "audit_only",
        # Pacing engine — advisory
        "BREATHING_RHYTHM_VIOLATION": "audit_only",
        "WIN_LOSS_MONOTONE": "audit_only",
        "CASE_TYPE_MONOTONE": "audit_only",
        # Phase B1 — narrative-line rotation
        "LINE_GAP_OVER": "block",
        "LINE_GAP_WARN": "audit_only",
    },
    default="audit_only",
)


# Codes whose gate mode is promoted to "block" for the first three chapters
# regardless of the ``mode_by_violation`` config. This is the "golden three
# chapter" policy: certain weak-signal violations that would normally go to
# ``audit_only`` become blocking in chapters 1-3 because the first impressions
# window is too load-bearing to ship with weak signal.
_GOLDEN_THREE_BLOCK_CODES: frozenset[str] = frozenset({"ENDING_SENTENCE_WEAK"})


# Phase B1 — the ``LineGapCheck`` needs a rolling-history window to produce
# meaningful measurements. For the first 10 chapters the gap metric is
# dominated by the "never seen" fallback (gap = chapter_no) which would
# mass-trigger on every project. We demote ``LINE_GAP_OVER`` to
# ``audit_only`` for chapters ≤ ``_LINE_GAP_WARMUP_CHAPTERS`` and only
# enforce it from chapter 11 onward. The demote applies regardless of
# config so projects can't accidentally block their own ramp-up.
_LINE_GAP_WARMUP_CHAPTERS: int = 10
_LINE_GAP_WARMUP_CODES: frozenset[str] = frozenset({"LINE_GAP_OVER"})


def resolve_mode(
    code: str,
    config: GateConfig = DEFAULT_GATE_CONFIG,
    *,
    chapter_no: int | None = None,
    override_lookup: OverrideLookup | None = None,
) -> GateMode:
    """Return the effective gate mode for a given violation ``code``.

    Resolution order:
      1. ``_GOLDEN_THREE_BLOCK_CODES`` override for chapters 1-3
         (first-impressions policy).
      2. Phase B1 ``LINE_GAP`` warm-up demote.
      3. Phase C1 override lookup — if an active Override Contract
         covers ``(code, chapter_no)`` the gate downgrades ``block`` →
         ``audit_only`` so the write proceeds while the Debt Ledger
         tracks payback.
      4. Explicit entry in ``mode_by_violation``.
      5. ``default``.

    The chapter-aware overrides are applied **before** the override
    lookup so the golden-three policy is non-bypassable: an author
    cannot sign away an ``ENDING_SENTENCE_WEAK`` block in the first
    three chapters by opening a contract.
    """

    base = config.mode_by_violation.get(code, config.default)
    if (
        code in _GOLDEN_THREE_BLOCK_CODES
        and chapter_no is not None
        and 1 <= chapter_no <= 3
    ):
        return "block"
    # Phase B1 — demote LINE_GAP_OVER during the warm-up window when
    # the rolling history isn't deep enough for the gap metric to be
    # meaningful. After the warm-up the gate behaves per config.
    if (
        code in _LINE_GAP_WARMUP_CODES
        and chapter_no is not None
        and chapter_no <= _LINE_GAP_WARMUP_CHAPTERS
    ):
        return "audit_only"
    # Phase C1 — if an active Override Contract covers this violation,
    # downgrade block → audit_only so the write proceeds. The ledger
    # still accrues interest until payback so this is not a free pass.
    if (
        base == "block"
        and override_lookup is not None
        and override_lookup(code, chapter_no)
    ):
        return "audit_only"
    return base


# ---------------------------------------------------------------------------
# Pure gate logic.
# ---------------------------------------------------------------------------


def filter_blocking(
    report: QualityReport,
    config: GateConfig = DEFAULT_GATE_CONFIG,
    *,
    chapter_no: int | None = None,
    override_lookup: OverrideLookup | None = None,
) -> tuple[Violation, ...]:
    """Narrow the report down to violations that effectively block the write.

    ``chapter_no`` is threaded to ``resolve_mode`` so per-chapter escalations
    (see ``_GOLDEN_THREE_BLOCK_CODES``) take effect. When ``chapter_no`` is
    unknown the base config mode is used unchanged. ``override_lookup`` is
    threaded to ``resolve_mode`` so active Phase C override contracts can
    downgrade block → audit_only.
    """

    return tuple(
        v
        for v in report.violations
        if resolve_mode(
            v.code,
            config,
            chapter_no=chapter_no,
            override_lookup=override_lookup,
        )
        == "block"
    )


def assert_writable(
    report: QualityReport,
    chapter_no: int | None = None,
    config: GateConfig = DEFAULT_GATE_CONFIG,
    *,
    override_lookup: OverrideLookup | None = None,
) -> None:
    """Raise ``ChapterBlocked`` if any violation effectively blocks the write.

    Side-effect-free: persistence is the caller's responsibility. The caller
    usually catches ``ChapterBlocked`` and hands off to
    ``handle_blocked_chapter`` to record the failure.
    """

    blocking = filter_blocking(
        report,
        config,
        chapter_no=chapter_no,
        override_lookup=override_lookup,
    )
    if blocking:
        raise ChapterBlocked(chapter_no, report, blocking)


def has_audit_only_findings(
    report: QualityReport,
    config: GateConfig = DEFAULT_GATE_CONFIG,
    *,
    chapter_no: int | None = None,
    override_lookup: OverrideLookup | None = None,
) -> bool:
    """True when the draft passes the gate but has audit-only findings worth
    logging (dashboards read this to chart ``true_positive_rate``).
    """

    return any(
        resolve_mode(
            v.code,
            config,
            chapter_no=chapter_no,
            override_lookup=override_lookup,
        )
        != "block"
        for v in report.violations
    )
