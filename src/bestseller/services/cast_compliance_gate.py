"""Post-write cast compliance gate for chapter retention repair."""

from __future__ import annotations

from dataclasses import dataclass
import re

from bestseller.services.canon_guardrails import CanonGuardrails

CAST_VIOLATION_BLOCK_CODE = "CAST_VIOLATION"


@dataclass(frozen=True)
class CastViolation:
    subject: str
    chapter_position: int
    pattern_matched: str
    severity: str
    detail: str


@dataclass(frozen=True)
class CastComplianceReport:
    chapter_position: int
    violations: tuple[CastViolation, ...]

    @property
    def passed(self) -> bool:
        return not self.violations


def check_cast_compliance(
    chapter_text: str,
    chapter_position: int,
    guardrails: CanonGuardrails,
) -> CastComplianceReport:
    """Detect premature cast/canon leaks after a chapter has been written.

    A state rule with ``applies_after_chapter=N`` normally constrains the
    explicit forbidden patterns only. Some rules also describe a subject that
    should remain a ledger/name/record clue instead of an active on-stage
    person; for those rules, one plain mention is tolerated and two or more
    mentions before the threshold are treated as active presence.
    """

    text = chapter_text or ""
    violations: list[CastViolation] = []

    for term in guardrails.forbidden_terms:
        forbidden = term.term.strip()
        if forbidden and forbidden in text:
            violations.append(
                CastViolation(
                    subject=forbidden,
                    chapter_position=chapter_position,
                    pattern_matched=forbidden,
                    severity="critical",
                    detail=f"absolute forbidden canon term appears: {forbidden}",
                )
            )

    for rule in guardrails.state_rules:
        threshold = rule.applies_after_chapter
        if threshold is None or chapter_position > threshold:
            continue

        subject = rule.subject.strip()
        for pattern in rule.forbidden_patterns:
            if _safe_search(pattern, text):
                violations.append(
                    CastViolation(
                        subject=subject,
                        chapter_position=chapter_position,
                        pattern_matched=pattern,
                        severity="critical",
                        detail=(
                            f"{subject} appears before allowed chapter "
                            f"{threshold + 1}: matched {pattern!r}"
                        ),
                    )
                )

        if subject and _is_presence_limited(rule) and text.count(subject) >= 2:
            violations.append(
                CastViolation(
                    subject=subject,
                    chapter_position=chapter_position,
                    pattern_matched="name_appears_before_threshold",
                    severity="critical",
                    detail=(
                        f"{subject} appears {text.count(subject)} times before "
                        f"allowed chapter {threshold + 1}"
                    ),
                )
            )

    return CastComplianceReport(
        chapter_position=chapter_position,
        violations=tuple(violations),
    )


def _safe_search(pattern: str, text: str) -> bool:
    try:
        return re.search(pattern, text) is not None
    except re.error:
        return pattern in text


def _is_presence_limited(rule: object) -> bool:
    """Return true when a canon rule limits active presence, not just state.

    Existing guardrails use ``applies_after_chapter`` for both lifecycle facts
    ("after chapter 4, 小雨 is rescued") and cast staging facts ("before chapter
    16, 裴镜渊 is only an old ledger name"). Repeated-name blocking is only valid
    for the latter class; lifecycle rules should rely on their explicit
    forbidden patterns.
    """

    haystack = " ".join(
        str(getattr(rule, field, "") or "")
        for field in ("status", "reason", "allowed_next")
    )
    return any(
        marker in haystack
        for marker in (
            "只代表",
            "只作旧",
            "旧账名",
            "不能真人",
            "不能快速真人化",
            "不能现身",
            "不能在第一卷抢走",
            "不能抢走",
            "不能快速",
            "只能作为旧",
        )
    )


__all__ = [
    "CAST_VIOLATION_BLOCK_CODE",
    "CastComplianceReport",
    "CastViolation",
    "check_cast_compliance",
]
