"""Retention Safety Gate — post-assembly evaluator for auto-repair.

After a chapter draft is assembled, this gate runs three retention-critical
checks and, on critical findings, stamps standardized block codes onto the
chapter's metadata so the existing auto-repair loop in ``pipelines.py``
picks them up and triggers regeneration.

Block codes (added to ``auto_repair_last_block_codes``):
    * ``HOOK_ECHO_MISSING`` — current chapter opening does not echo prev
      chapter's hooks; triggers rewrite with hook_echo_block injected.
    * ``SIGNATURE_SCENE_MISSING`` — a chapter at a signature-scene slot
      did not include the mandated archetype's images or lines.
    * ``EXPOSITION_DUMP`` — exposition ratio over the band-specific
      ceiling, blocks readers' engagement.

Gate severity → auto-repair behavior:
    * critical → block code stamped + production_state="blocked"
      → auto-repair fires
    * high → metadata recorded but production_state stays "ok"
      → audit-only (no rewrite)
    * info → no-op
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
import logging
from typing import Any

from bestseller.services.progress_context import emit_gate_result
from bestseller.services.canon_guardrails import CanonGuardrails
from bestseller.services.cast_compliance_gate import (
    CAST_VIOLATION_BLOCK_CODE,
    check_cast_compliance,
)
from bestseller.services.chapter_duplicate_gate import (
    CHAPTER_BODY_TEMPLATE_REPEAT,
    CHAPTER_OPENING_DUPLICATE,
    check_chapter_duplicates,
)
from bestseller.services.chapter_length_gate import (
    CHAPTER_BELOW_TARGET_BLOCK_CODE,
    CHAPTER_LENGTH_BLOCK_HIGH_CODE,
    CHAPTER_TOO_SHORT_BLOCK_CODE,
    check_chapter_length,
)
from bestseller.services.chapter_word_count_truth import (
    WORD_COUNT_METADATA_MISMATCH,
    check_word_count_metadata_truth,
)
from bestseller.services.payoff_ledger_gate import (
    PAYOFF_HOOK_ONLY,
    PAYOFF_LEDGER_LOW,
    evaluate_payoff_ledger,
)
from bestseller.services.persona_quality_gate import (
    PERSONA_ABANDON_RATE_HIGH,
    PERSONA_PAYOFF_DENSITY_LOW,
    PERSONA_WEIGHTED_SCORE_LOW,
)
from bestseller.services.character_role_gate import (
    CHARACTER_ROLE_DRIFT_BLOCK_CODE,
    CharacterProfile,
    check_character_role_compliance,
)
from bestseller.services.dialogue_voice_gate import (
    DIALOGUE_AI_FLAVOR_BLOCK_CODE,
    check_dialogue_voice,
)
from bestseller.services.exposition_density_gate import (
    check_exposition_density,
)
from bestseller.services.hook_echo_gate import check_hook_echo
from bestseller.services.scene_coherence_gate import (
    SCENE_JUMP_BLOCK_CODE,
    check_scene_coherence,
)
from bestseller.services.signature_scene_critic import (
    judge_signature_scene_semantics,
)
from bestseller.services.signature_scene_planner import (
    plan_signature_scenes,
)
from bestseller.services.timeline_consistency_gate import (
    TIMELINE_INCONSISTENT_BLOCK_CODE,
    TimelineCanon,
    check_timeline_consistency,
)

logger = logging.getLogger(__name__)


HOOK_ECHO_BLOCK_CODE = "HOOK_ECHO_MISSING"
HOOK_ECHO_LOW_BLOCK_CODE = "HOOK_ECHO_LOW"
SIGNATURE_SCENE_BLOCK_CODE = "SIGNATURE_SCENE_MISSING"
EXPOSITION_DUMP_BLOCK_CODE = "EXPOSITION_DUMP"

# These codes are eligible for auto-repair.
AUTO_REPAIR_RETENTION_CODES: tuple[str, ...] = (
    HOOK_ECHO_BLOCK_CODE,
    HOOK_ECHO_LOW_BLOCK_CODE,
    SIGNATURE_SCENE_BLOCK_CODE,
    EXPOSITION_DUMP_BLOCK_CODE,
    CAST_VIOLATION_BLOCK_CODE,
    TIMELINE_INCONSISTENT_BLOCK_CODE,
    SCENE_JUMP_BLOCK_CODE,
    CHARACTER_ROLE_DRIFT_BLOCK_CODE,
    DIALOGUE_AI_FLAVOR_BLOCK_CODE,
    CHAPTER_TOO_SHORT_BLOCK_CODE,
    CHAPTER_LENGTH_BLOCK_HIGH_CODE,
    CHAPTER_BELOW_TARGET_BLOCK_CODE,
    CHAPTER_OPENING_DUPLICATE,
    CHAPTER_BODY_TEMPLATE_REPEAT,
    WORD_COUNT_METADATA_MISMATCH,
    PAYOFF_LEDGER_LOW,
    PAYOFF_HOOK_ONLY,
    PERSONA_ABANDON_RATE_HIGH,
    PERSONA_WEIGHTED_SCORE_LOW,
    PERSONA_PAYOFF_DENSITY_LOW,
)

# Deterministic post-write-audit codes that describe reader-retention quality
# (as opposed to text-integrity defects). They participate in chapter
# auto-repair via ``chapter_auto_repair_repairable_codes`` and are re-derived
# on every assembly, so for repair-targeting purposes they count as "fresh"
# alongside AUTO_REPAIR_RETENTION_CODES. Shared by the soft-retention fuse
# (pipelines) and the repair-target builder (drafts).
RETENTION_AUDIT_SOFT_CODES: frozenset[str] = frozenset(
    {
        "SIGNATURE_IMAGE_MISSING",
        "ENDING_HOOK_MISSING",
        "OPENING_PRESSURE_THIN",
    }
)

# Emitted when enough independent sub-checks crash that a "clean pass" can no
# longer be distinguished from "the gate ran blind". Surfaced as a visible,
# logged finding (not a hard block: on an autonomous run a systematically
# buggy check should alert loudly, not stall every chapter into repair).
RETENTION_GATE_DEGRADED_CODE = "RETENTION_GATE_DEGRADED"
_RETENTION_DEGRADED_MIN_ERRORS = 3


@dataclass(frozen=True)
class RetentionGateFinding:
    """A single gate finding."""

    code: str
    severity: str
    detail: str
    coverage: float | None = None
    exposition_ratio: float | None = None
    evidence: dict[str, Any] | None = None


@dataclass(frozen=True)
class RetentionGateReport:
    """Aggregated retention safety gate report for one chapter."""

    chapter_position: int
    findings: tuple[RetentionGateFinding, ...]
    auto_repair_codes: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return not self.auto_repair_codes

    @property
    def has_critical(self) -> bool:
        return any(f.severity == "critical" for f in self.findings)


def evaluate_retention_safety(
    *,
    chapter_position: int,
    chapter_text: str,
    prev_chapter_text: str | None = None,
    prev_chapter_position: int | None = None,
    total_chapters: int = 500,
    signature_cadence: int = 10,
    guardrails: CanonGuardrails | None = None,
    timeline_canon: TimelineCanon | None = None,
    character_profiles: tuple[CharacterProfile, ...] | None = None,
    skip_signature: bool = False,
    skip_hook_echo: bool = False,
    skip_exposition: bool = False,
    skip_cast_compliance: bool = False,
    skip_timeline: bool = False,
    skip_scene_coherence: bool = False,
    skip_character_role: bool = False,
    skip_dialogue_voice: bool = False,
    skip_chapter_length: bool = False,
    chapter_length_hard_floor: int | None = None,
    chapter_length_soft_warning: int | None = None,
    chapter_length_hard_max: int | None = None,
    block_below_target: bool = False,
    payoff_block: bool = False,
    stored_word_count: int | None = None,
    draft_word_count: int | None = None,
    skip_word_count_truth: bool = False,
    skip_duplicate_check: bool = False,
    skip_payoff_ledger: bool = False,
    opening_similarity_threshold: float = 0.82,
    body_similarity_threshold: float = 0.88,
    min_payoff_density: float = 0.18,
    hook_domain_tokens: Sequence[str] = (),
) -> RetentionGateReport:
    """Run the 3 retention gates on an assembled chapter.

    Returns a report listing any findings + which codes should trigger
    auto-repair. Caller is responsible for stamping codes onto chapter
    metadata + setting production_state.
    """

    findings: list[RetentionGateFinding] = []
    auto_repair: list[str] = []
    checks_errored = 0

    # Hook Echo
    if not skip_hook_echo and prev_chapter_text and chapter_position >= 2:
        try:
            he = check_hook_echo(
                prev_chapter_text=prev_chapter_text,
                current_chapter_text=chapter_text,
                current_chapter_position=chapter_position,
                prev_chapter_position=prev_chapter_position
                or (chapter_position - 1),
                # Must match the production side (prepare_chapter_context):
                # both source from the book's imagery anchors so the duty
                # block and this validation extract the same token set.
                extra_domain_tokens=hook_domain_tokens,
            )
            severity = he.finding.severity
            if severity == "critical":
                findings.append(
                    RetentionGateFinding(
                        code=HOOK_ECHO_BLOCK_CODE,
                        severity=severity,
                        detail=he.finding.detail,
                        coverage=he.coverage,
                        evidence={
                            "prev_hook_tokens": list(he.finding.prev_hook_tokens),
                            "matched_tokens": list(he.finding.matched_tokens),
                            "missed_tokens": list(he.finding.missed_tokens),
                        },
                    )
                )
                auto_repair.append(HOOK_ECHO_BLOCK_CODE)
            elif severity == "high":
                findings.append(
                    RetentionGateFinding(
                        code=HOOK_ECHO_LOW_BLOCK_CODE,
                        severity=severity,
                        detail=he.finding.detail,
                        coverage=he.coverage,
                        evidence={
                            "prev_hook_tokens": list(he.finding.prev_hook_tokens),
                            "matched_tokens": list(he.finding.matched_tokens),
                            "missed_tokens": list(he.finding.missed_tokens),
                        },
                    )
                )
        except Exception as exc:
            checks_errored += 1
            logger.warning(
                "hook echo evaluation failed for ch%d: %s",
                chapter_position,
                exc,
            )

    # Signature Scene
    if not skip_signature:
        try:
            plan = plan_signature_scenes(
                total_chapters=max(total_chapters, chapter_position),
                cadence=signature_cadence,
            )
            mandate = plan.mandate_for_chapter(chapter_position)
            if mandate is not None and getattr(mandate, "is_skeleton", False):
                # R25: a skeleton mandate has no concrete target and was
                # never rendered into the writer prompt — grading the
                # chapter against an empty standard is self-harm, not QA.
                mandate = None
            if mandate is not None:
                line_hits = sum(
                    1 for hint in mandate.must_include_line if hint in chapter_text
                )
                image_hits = sum(
                    1 for hint in mandate.must_include_image if hint in chapter_text
                )
                if line_hits == 0 and image_hits == 0:
                    critic = judge_signature_scene_semantics(chapter_text, mandate)
                    if not critic.passed:
                        findings.append(
                            RetentionGateFinding(
                                code=SIGNATURE_SCENE_BLOCK_CODE,
                                severity="critical",
                                detail=(
                                    f"chapter at signature-scene slot ({mandate.archetype.value}/"
                                    f"{mandate.stake.value}) does not contain literal hints "
                                    f"and failed semantic critic: {critic.detail}"
                                ),
                            )
                        )
                        auto_repair.append(SIGNATURE_SCENE_BLOCK_CODE)
        except Exception as exc:
            checks_errored += 1
            logger.warning(
                "signature compliance evaluation failed for ch%d: %s",
                chapter_position,
                exc,
            )

    # Exposition Density
    if not skip_exposition:
        try:
            ed = check_exposition_density(
                chapter_text, chapter_position=chapter_position
            )
            severity = ed.finding.severity
            if severity == "critical":
                findings.append(
                    RetentionGateFinding(
                        code=EXPOSITION_DUMP_BLOCK_CODE,
                        severity=severity,
                        detail=ed.finding.detail,
                        exposition_ratio=ed.finding.exposition_ratio,
                    )
                )
                auto_repair.append(EXPOSITION_DUMP_BLOCK_CODE)
            elif severity == "high":
                findings.append(
                    RetentionGateFinding(
                        code="EXPOSITION_HIGH",
                        severity=severity,
                        detail=ed.finding.detail,
                        exposition_ratio=ed.finding.exposition_ratio,
                    )
                )
        except Exception as exc:
            checks_errored += 1
            logger.warning(
                "exposition density evaluation failed for ch%d: %s",
                chapter_position,
                exc,
            )

    # Cast Compliance
    if not skip_cast_compliance and guardrails is not None and not guardrails.is_empty:
        try:
            cast_report = check_cast_compliance(
                chapter_text=chapter_text,
                chapter_position=chapter_position,
                guardrails=guardrails,
            )
            for violation in cast_report.violations:
                findings.append(
                    RetentionGateFinding(
                        code=CAST_VIOLATION_BLOCK_CODE,
                        severity=violation.severity,
                        detail=violation.detail,
                    )
                )
            if cast_report.violations:
                auto_repair.append(CAST_VIOLATION_BLOCK_CODE)
        except Exception as exc:
            checks_errored += 1
            logger.warning(
                "cast compliance evaluation failed for ch%d: %s",
                chapter_position,
                exc,
            )

    # Timeline Consistency
    if not skip_timeline and timeline_canon is not None:
        try:
            tr = check_timeline_consistency(
                chapter_text,
                chapter_position=chapter_position,
                canon=timeline_canon,
            )
            if tr.has_critical:
                findings.append(
                    RetentionGateFinding(
                        code=TIMELINE_INCONSISTENT_BLOCK_CODE,
                        severity="critical",
                        detail=(
                            f"timeline inconsistencies: "
                            f"{len(tr.violations)} violations"
                        ),
                        evidence={
                            "violations": [
                                {
                                    "code": v.code,
                                    "found_anchor": v.found_anchor,
                                    "canonical_anchor": v.canonical_anchor,
                                    "paragraph_idx": v.paragraph_idx,
                                    "detail": v.detail,
                                }
                                for v in tr.violations
                            ],
                        },
                    )
                )
                auto_repair.append(TIMELINE_INCONSISTENT_BLOCK_CODE)
        except Exception as exc:
            checks_errored += 1
            logger.warning(
                "timeline consistency check failed for ch%d: %s",
                chapter_position,
                exc,
            )

    # Scene Coherence
    if not skip_scene_coherence:
        try:
            sc = check_scene_coherence(
                chapter_text, chapter_position=chapter_position
            )
            if sc.has_critical:
                findings.append(
                    RetentionGateFinding(
                        code=SCENE_JUMP_BLOCK_CODE,
                        severity="critical",
                        detail=(
                            f"scene jumps without transitions: "
                            f"{len([j for j in sc.jumps if j.severity == 'critical'])} critical"
                        ),
                        evidence={
                            "jumps": [
                                {
                                    "from": j.from_location,
                                    "to": j.to_location,
                                    "paragraph_idx": j.paragraph_idx,
                                    "severity": j.severity,
                                    "detail": j.detail,
                                }
                                for j in sc.jumps
                            ],
                        },
                    )
                )
                auto_repair.append(SCENE_JUMP_BLOCK_CODE)
        except Exception as exc:
            checks_errored += 1
            logger.warning(
                "scene coherence check failed for ch%d: %s",
                chapter_position,
                exc,
            )

    # Character Role Compliance
    if not skip_character_role and character_profiles:
        try:
            cr = check_character_role_compliance(
                chapter_text,
                chapter_position=chapter_position,
                profiles=character_profiles,
            )
            if cr.has_critical:
                findings.append(
                    RetentionGateFinding(
                        code=CHARACTER_ROLE_DRIFT_BLOCK_CODE,
                        severity="critical",
                        detail=(
                            f"character role drift: "
                            f"{len([f for f in cr.findings if f.severity == 'critical'])} critical"
                        ),
                        evidence={
                            "findings": [
                                {
                                    "character": f.character,
                                    "drift_type": f.drift_type,
                                    "severity": f.severity,
                                    "detail": f.detail,
                                }
                                for f in cr.findings
                            ],
                        },
                    )
                )
                auto_repair.append(CHARACTER_ROLE_DRIFT_BLOCK_CODE)
        except Exception as exc:
            checks_errored += 1
            logger.warning(
                "character role check failed for ch%d: %s",
                chapter_position,
                exc,
            )

    # Dialogue Voice Gate
    if not skip_dialogue_voice and character_profiles:
        voice_profiles = tuple(
            profile.dialogue_voice
            for profile in character_profiles
            if profile.dialogue_voice is not None
        )
        if voice_profiles:
            try:
                dv = check_dialogue_voice(
                    chapter_text,
                    chapter_position=chapter_position,
                    profiles=voice_profiles,
                )
                critical = [finding for finding in dv.findings if finding.severity == "critical"]
                if critical:
                    findings.append(
                        RetentionGateFinding(
                            code=DIALOGUE_AI_FLAVOR_BLOCK_CODE,
                            severity="critical",
                            detail=f"dialogue AI flavor: {len(critical)} critical finding(s)",
                            evidence={
                                "findings": [
                                    {
                                        "code": finding.code,
                                        "character": finding.character,
                                        "severity": finding.severity,
                                        "detail": finding.detail,
                                        "line_index": finding.line_index,
                                        "evidence": finding.evidence,
                                    }
                                    for finding in dv.findings
                                ],
                            },
                        )
                    )
                    auto_repair.append(DIALOGUE_AI_FLAVOR_BLOCK_CODE)
                else:
                    for finding in dv.findings:
                        if finding.severity == "high":
                            findings.append(
                                RetentionGateFinding(
                                    code=finding.code,
                                    severity=finding.severity,
                                    detail=finding.detail,
                                    evidence={
                                        "character": finding.character,
                                        "line_index": finding.line_index,
                                        "evidence": finding.evidence,
                                    },
                                )
                            )
            except Exception as exc:
                checks_errored += 1
                logger.warning(
                    "dialogue voice check failed for ch%d: %s",
                    chapter_position,
                    exc,
                )

    # Chapter Length — guard against "省事感" short chapters.
    # 2026-05-23: added because the framework had no length gate, allowing
    # 1300-zh-char chapters to ship.
    if not skip_chapter_length:
        try:
            length_kwargs: dict[str, int] = {}
            if chapter_length_hard_floor is not None:
                length_kwargs["hard_floor"] = chapter_length_hard_floor
            if chapter_length_soft_warning is not None:
                length_kwargs["soft_warning"] = chapter_length_soft_warning
            if chapter_length_hard_max is not None:
                length_kwargs["hard_max"] = chapter_length_hard_max
            length_report = check_chapter_length(
                chapter_text,
                chapter_position=chapter_position,
                **length_kwargs,
            )
            if length_report.has_critical:
                findings.append(
                    RetentionGateFinding(
                        code=length_report.finding.code,
                        severity="critical",
                        detail=length_report.finding.detail,
                        evidence={
                            "zh_char_count": length_report.finding.zh_char_count,
                            "hard_floor": length_report.finding.hard_floor,
                            "soft_warning": length_report.finding.soft_warning,
                            "hard_max": length_report.finding.hard_max,
                        },
                    )
                )
                auto_repair.append(length_report.finding.code)
            elif length_report.finding.severity == "high":
                below_severity = "critical" if block_below_target else "high"
                findings.append(
                    RetentionGateFinding(
                        code=CHAPTER_BELOW_TARGET_BLOCK_CODE,
                        severity=below_severity,
                        detail=length_report.finding.detail,
                        evidence={
                            "zh_char_count": length_report.finding.zh_char_count,
                            "hard_floor": length_report.finding.hard_floor,
                            "soft_warning": length_report.finding.soft_warning,
                            "hard_max": length_report.finding.hard_max,
                        },
                    )
                )
                if block_below_target:
                    auto_repair.append(CHAPTER_BELOW_TARGET_BLOCK_CODE)
        except Exception as exc:
            checks_errored += 1
            logger.warning(
                "chapter length check failed for ch%d: %s",
                chapter_position,
                exc,
            )

    if not skip_word_count_truth:
        try:
            truth = check_word_count_metadata_truth(
                chapter_text,
                stored_word_count=stored_word_count,
                draft_word_count=draft_word_count,
            )
            if truth.finding.severity == "critical":
                findings.append(
                    RetentionGateFinding(
                        code=truth.finding.code,
                        severity="critical",
                        detail=truth.finding.detail,
                        evidence={
                            "actual_zh_chars": truth.actual_zh_chars,
                            "stored_word_count": truth.finding.stored_word_count,
                            "draft_word_count": truth.finding.draft_word_count,
                        },
                    )
                )
                auto_repair.append(WORD_COUNT_METADATA_MISMATCH)
        except Exception as exc:
            checks_errored += 1
            logger.warning(
                "word count truth check failed for ch%d: %s",
                chapter_position,
                exc,
            )

    if not skip_duplicate_check and prev_chapter_text:
        try:
            dup = check_chapter_duplicates(
                chapter_position=chapter_position,
                chapter_text=chapter_text,
                prev_chapter_text=prev_chapter_text,
                opening_similarity_threshold=opening_similarity_threshold,
                body_similarity_threshold=body_similarity_threshold,
            )
            for item in dup.findings:
                findings.append(
                    RetentionGateFinding(
                        code=item.code,
                        severity=item.severity,
                        detail=item.detail,
                        evidence=dict(item.evidence),
                    )
                )
                if item.severity == "critical":
                    auto_repair.append(item.code)
        except Exception as exc:
            checks_errored += 1
            logger.warning(
                "duplicate check failed for ch%d: %s",
                chapter_position,
                exc,
            )

    if not skip_payoff_ledger:
        try:
            ledger = evaluate_payoff_ledger(
                chapter_text,
                chapter_position=chapter_position,
                min_payoff_density=min_payoff_density,
            )
            if ledger.finding.severity == "critical":
                # The deterministic keyword heuristic is noisy on legitimately
                # hook-heavy chapters, so it is advisory by default. The real
                # payoff enforcement is the persona / reader-judge gate; set
                # ``payoff_block`` to hard-enforce after calibration.
                findings.append(
                    RetentionGateFinding(
                        code=ledger.finding.code,
                        severity="critical" if payoff_block else "high",
                        detail=ledger.finding.detail,
                        evidence={
                            "hook_hits": ledger.finding.hook_hits,
                            "payoff_hits": ledger.finding.payoff_hits,
                            "payoff_density": ledger.finding.payoff_density,
                        },
                    )
                )
                if payoff_block:
                    auto_repair.append(ledger.finding.code)
        except Exception as exc:
            checks_errored += 1
            logger.warning(
                "payoff ledger check failed for ch%d: %s",
                chapter_position,
                exc,
            )

    if checks_errored >= _RETENTION_DEGRADED_MIN_ERRORS:
        # Fail-open ambiguity guard: enough sub-checks crashed that an empty
        # findings list no longer means "clean". Surface it explicitly so a
        # caller / operator can tell a genuine pass from a blind one.
        logger.error(
            "retention_safety_gate degraded for ch%d: %d sub-check(s) errored; "
            "result is not a reliable clean pass",
            chapter_position,
            checks_errored,
        )
        findings.append(
            RetentionGateFinding(
                code=RETENTION_GATE_DEGRADED_CODE,
                severity="warning",
                detail=(
                    f"{checks_errored} retention sub-check(s) failed to run; "
                    "gate result is degraded and must not be read as a clean pass"
                ),
                evidence={"checks_errored": checks_errored},
            )
        )

    _auto_repair_codes = tuple(dict.fromkeys(auto_repair))
    emit_gate_result(
        "retention_safety_gate",
        verdict="pass" if not _auto_repair_codes else "blocked",
        severity="critical" if _auto_repair_codes else ("high" if findings else "info"),
        score=100 if not _auto_repair_codes else 0,
        reasons=[f.detail for f in findings],
        chapter=chapter_position,
    )
    return RetentionGateReport(
        chapter_position=chapter_position,
        findings=tuple(findings),
        auto_repair_codes=_auto_repair_codes,
    )


def stamp_retention_block_codes(
    chapter: Any,
    report: RetentionGateReport,
) -> bool:
    """Stamp critical findings onto chapter metadata for auto-repair.

    Returns True if the chapter was marked blocked (one or more critical
    codes); False otherwise.
    """

    metadata = dict(getattr(chapter, "metadata_json", None) or {})
    existing_codes = list(metadata.get("auto_repair_last_block_codes") or [])
    retention_code_set = set(AUTO_REPAIR_RETENTION_CODES)

    if not report.has_critical:
        cleaned_codes = [code for code in existing_codes if code not in retention_code_set]
        if cleaned_codes:
            metadata["auto_repair_last_block_codes"] = cleaned_codes
        else:
            metadata.pop("auto_repair_last_block_codes", None)
        if metadata.get("production_block_code") in retention_code_set:
            metadata.pop("production_block_code", None)
        chapter.metadata_json = metadata
        return False

    # Keep unrelated repair codes but replace retention codes with the current
    # report. Otherwise stale CAST/HOOK codes keep poisoning later repair prompts
    # after that specific gate has already been fixed.
    existing_codes = [code for code in existing_codes if code not in retention_code_set]
    for code in report.auto_repair_codes:
        if code not in existing_codes:
            existing_codes.append(code)

    metadata["auto_repair_last_block_codes"] = existing_codes
    metadata["retention_gate_last_findings"] = [
        {
            "code": f.code,
            "severity": f.severity,
            "detail": f.detail,
            **({"coverage": f.coverage} if f.coverage is not None else {}),
            **({"exposition_ratio": f.exposition_ratio} if f.exposition_ratio is not None else {}),
            **({"evidence": f.evidence} if f.evidence else {}),
        }
        for f in report.findings
    ]
    metadata["production_block_code"] = report.auto_repair_codes[0]

    chapter.metadata_json = metadata
    chapter.production_state = "blocked"
    return True


__all__ = [
    "AUTO_REPAIR_RETENTION_CODES",
    "CAST_VIOLATION_BLOCK_CODE",
    "CHAPTER_BELOW_TARGET_BLOCK_CODE",
    "CHAPTER_TOO_SHORT_BLOCK_CODE",
    "DIALOGUE_AI_FLAVOR_BLOCK_CODE",
    "EXPOSITION_DUMP_BLOCK_CODE",
    "HOOK_ECHO_BLOCK_CODE",
    "SIGNATURE_SCENE_BLOCK_CODE",
    "RETENTION_GATE_DEGRADED_CODE",
    "RetentionGateFinding",
    "RetentionGateReport",
    "evaluate_retention_safety",
    "stamp_retention_block_codes",
]
