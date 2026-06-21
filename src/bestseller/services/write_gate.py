"""L6 Pre-Write Gate.

Stands between a completed LLM draft and the disk / DB write. Takes a
``QualityReport`` from L4/L5 and decides — per ``mode_by_violation`` config —
whether any violation actually blocks the write.

Separation of concerns:
    * ``resolve_mode`` + ``assert_writable`` are pure logic (no DB, no side
      effects) so unit tests can exercise gate behavior without fixtures.
    * ``handle_blocked_chapter`` is the side-effectful wrapper that persists
      the quality report, flips chapter state to FAILED, and drops rejected
      drafts for machine repair. The caller wires these in when the pipeline
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
        # NOTE (2026-05-26 architecture cleanup): these used to be block-level.
        # The underlying detectors had hardcoded user-feedback keyword lists
        # (phone/text/heat-sensation words) and were producing false positives
        # that prevented good openings (including v21's canonical opening).
        # Hardcoded keyword lists in drafts.py have been removed; semantic intent
        # is now an audit dimension in chapter_llm_quality_judge.
        "OPENING_SCENE_DRIFT": "audit_only",
        "FRONT10_FORBIDDEN_SIGNAL": "audit_only",
        # FRONT10_RULE_LECTURE_DENSITY remains a useful structural check (it
        # detects "rule-class" exposition density, not specific keywords);
        # but its severity is also relaxed — LLM judge has authority.
        "FRONT10_RULE_LECTURE_DENSITY": "audit_only",
        "FRONT10_SCENE_FORBIDDEN_ACTION": "audit_only",
        "NAMING_OUT_OF_POOL": "audit_only",
        "OPENING_ENTITY_OVERLOAD": "audit_only",
        "POV_DRIFT": "audit_only",
        "CLIFFHANGER_REPEAT": "audit_only",
        "WORD_COUNT_METADATA_MISMATCH": "block",
        "CHAPTER_OPENING_REPETITION": "block",
        "CROSS_CHAPTER_REPETITION": "block",
        "PERSONA_ABANDON_RATE_HIGH": "block",
        "PERSONA_WEIGHTED_SCORE_LOW": "block",
        "PERSONA_PAYOFF_DENSITY_LOW": "block",
        "PAYOFF_LEDGER_LOW": "block",
        "PAYOFF_HOOK_ONLY": "block",
        "CRITIC_MISSING_BODY_EVIDENCE": "block",
        "CRITIC_EMPTY_REVIEW": "block",
        "MILESTONE_CONSISTENCY_FAIL": "block",
        "STORY_BIBLE_INCOMPLETE": "block",
        "STORY_BIBLE_MISSING_FILE": "block",
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
        # Phase B1 — narrative-line rotation.
        # 2026-06-21: demoted LINE_GAP_OVER block → audit_only. Line-monotony
        # is a craft signal, not a correctness defect, and hard-blocking on it
        # bricked whole books: a theme-heavy / low-action book (or any genre
        # the classifier's marker lexicon under-covers) leaves a layer dormant
        # past budget and every chapter from ch11 on fails the gate, with no
        # way for the writer to "rotate back" to a layer the story doesn't use.
        # The regen loop already treats LINE_GAP_OVER as soft; this makes the
        # gate consistent. The rotation NUDGE + audit telemetry still fire, so
        # the author is still guided toward variety — it just never freezes the
        # pipeline. (The ch≤10 warmup demote below is now subsumed but kept.)
        "LINE_GAP_OVER": "audit_only",
        "LINE_GAP_WARN": "audit_only",
    },
    default="audit_only",
)


# Codes whose gate mode is promoted to "block" for the first three chapters
# regardless of the ``mode_by_violation`` config. This is the "golden three
# chapter" policy: certain weak-signal violations that would normally go to
# ``audit_only`` become blocking in chapters 1-3 because the first impressions
# window is too load-bearing to ship with weak signal.
#
# 2026-05-27: ENDING_SENTENCE_WEAK removed from the golden-three set.
# The golden-three promotion was causing infinite auto-repair loops: the LLM
# scene_rewrite would "fix" the ending but overshoot the word limit, producing
# LENGTH_OVER / CHAPTER_LENGTH_BLOCK_HIGH failures that auto-repair can't
# resolve. ENDING_SENTENCE_WEAK is still detected and reported as a minor
# finding; it simply no longer blocks write in any chapter.
_GOLDEN_THREE_BLOCK_CODES: frozenset[str] = frozenset()


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
