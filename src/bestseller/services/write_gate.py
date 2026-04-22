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
from typing import Literal, Mapping

from bestseller.services.output_validator import QualityReport, Violation


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
        "CHAPTER_GAP": "block",
        "QUIRK_SLOT_MISSING": "block",
        "ANTAGONIST_MOTIVE_OVERLAP": "block",
        "WORLD_TAXONOMY_BOILERPLATE": "block",
        "NAMING_POOL_UNDERSIZED": "block",
        "NAMING_OUT_OF_POOL": "audit_only",
        "OPENING_ENTITY_OVERLOAD": "audit_only",
        "POV_DRIFT": "audit_only",
        "CLIFFHANGER_REPEAT": "audit_only",
        # Hype engine — all audit_only by default; the per-violation
        # severity escalates chapters 1-3 for ENDING_SENTENCE_WEAK.
        "HYPE_MISSING": "audit_only",
        "HYPE_REPEAT": "audit_only",
        "ENDING_SENTENCE_WEAK": "audit_only",
        "GOLDEN_THREE_WEAK": "audit_only",
        "PLEASURE_HYPE_GAP": "audit_only",
        "PLEASURE_COMEDIC_BEAT_STARVED": "audit_only",
        "PLEASURE_SETUP_PAYOFF_DEBT": "audit_only",
    },
    default="audit_only",
)


# Codes whose gate mode is promoted to "block" for the first three chapters
# regardless of the ``mode_by_violation`` config. This is the "golden three
# chapter" policy: certain weak-signal violations that would normally go to
# ``audit_only`` become blocking in chapters 1-3 because the first impressions
# window is too load-bearing to ship with weak signal.
_GOLDEN_THREE_BLOCK_CODES: frozenset[str] = frozenset({"ENDING_SENTENCE_WEAK"})


def resolve_mode(
    code: str,
    config: GateConfig = DEFAULT_GATE_CONFIG,
    *,
    chapter_no: int | None = None,
) -> GateMode:
    """Return the effective gate mode for a given violation ``code``.

    Resolution order: ``_GOLDEN_THREE_BLOCK_CODES`` override for chapters 1-3
    → explicit entry in ``mode_by_violation`` → ``default``.

    The chapter-aware override implements plan §2's "EndingSentenceImpactCheck
    前三章 block" decision: the config stays ``audit_only`` (so chapters 4+
    only log the finding), but the gate forces ``block`` for the first three
    chapters where a weak ending sentence is a first-impressions killer.
    """

    base = config.mode_by_violation.get(code, config.default)
    if (
        code in _GOLDEN_THREE_BLOCK_CODES
        and chapter_no is not None
        and 1 <= chapter_no <= 3
    ):
        return "block"
    return base


# ---------------------------------------------------------------------------
# Pure gate logic.
# ---------------------------------------------------------------------------


def filter_blocking(
    report: QualityReport,
    config: GateConfig = DEFAULT_GATE_CONFIG,
    *,
    chapter_no: int | None = None,
) -> tuple[Violation, ...]:
    """Narrow the report down to violations that effectively block the write.

    ``chapter_no`` is threaded to ``resolve_mode`` so per-chapter escalations
    (see ``_GOLDEN_THREE_BLOCK_CODES``) take effect. When ``chapter_no`` is
    unknown the base config mode is used unchanged.
    """

    return tuple(
        v
        for v in report.violations
        if resolve_mode(v.code, config, chapter_no=chapter_no) == "block"
    )


def assert_writable(
    report: QualityReport,
    chapter_no: int | None = None,
    config: GateConfig = DEFAULT_GATE_CONFIG,
) -> None:
    """Raise ``ChapterBlocked`` if any violation effectively blocks the write.

    Side-effect-free: persistence is the caller's responsibility. The caller
    usually catches ``ChapterBlocked`` and hands off to
    ``handle_blocked_chapter`` to record the failure.
    """

    blocking = filter_blocking(report, config, chapter_no=chapter_no)
    if blocking:
        raise ChapterBlocked(chapter_no, report, blocking)


def has_audit_only_findings(
    report: QualityReport,
    config: GateConfig = DEFAULT_GATE_CONFIG,
    *,
    chapter_no: int | None = None,
) -> bool:
    """True when the draft passes the gate but has audit-only findings worth
    logging (dashboards read this to chart ``true_positive_rate``).
    """

    return any(
        resolve_mode(v.code, config, chapter_no=chapter_no) != "block"
        for v in report.violations
    )
