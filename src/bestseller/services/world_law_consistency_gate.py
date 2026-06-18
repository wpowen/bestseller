"""Advisory gate: does the prose obey the book's derived world laws?

This is the gate that catches "everyone can fly, yet the hero drives a car" —
prose that silently reverts to the baseline a world law forbids. It is SOFT:
a hit stamps advisory metrics and routes nothing; it never blocks the chapter
(tier=advanced, continuation_impact=local). It reads the same active-law
selection the prose injection uses, so prose and gate share one source of truth.

The deterministic detector handles the common enforceable patterns of the
``enforcement`` assertions the deriver emits ("出现X须…理由" / "不得Y"). A richer
LLM judge can be injected via ``judge=`` without changing the call sites.
"""

# ruff: noqa: E501

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
import re
from typing import Any

from bestseller.domain.world_model import WorldLaw
from bestseller.services.world_model_injection import select_active_laws

# Markers that introduce a constrained trigger in an enforcement assertion.
_TRIGGER_MARKERS = ("出现", "涉及", "使用", "持有", "出行", "采用", "选择")
# A conditional-with-justification requires a reason cue near the trigger.
_CONDITIONAL_MARKERS = ("须", "应", "需", "除非", "才能", "方可", "理由", "说明", "解释")
_PROHIBITION_MARKERS = ("不得", "禁止", "严禁", "不可", "不能")
_JUSTIFICATION_CUES = ("因为", "由于", "原因", "理由", "为了", "不得不", "只能", "除非", "为此", "缘于", "之所以")
_CJK_RUN = re.compile(r"[一-鿿]{2,}")
_MAX_ACTIVE_LAWS = 6


@dataclass(frozen=True)
class CheckerIssue:
    id: str
    message: str
    severity: str = "advisory"


@dataclass(frozen=True)
class CheckerReport:
    passed: bool
    issues: tuple[CheckerIssue, ...]
    metrics: dict[str, Any]


@dataclass(frozen=True)
class WorldLawViolation:
    dimension: str
    enforcement: str
    trigger: str
    kind: str  # "prohibition" | "missing_justification"


@dataclass(frozen=True)
class WorldLawConsistencyReport:
    passed: bool
    violations: tuple[WorldLawViolation, ...] = ()
    active_law_count: int = 0

    def to_checker_report(self) -> CheckerReport:
        issues = tuple(
            CheckerIssue(
                id=f"world_law_violation:{v.dimension}",
                message=(
                    f"[{v.dimension}] 正文可能违背世界规律:出现「{v.trigger}」"
                    + ("(被该规律禁止)" if v.kind == "prohibition" else "(未给出规律要求的理由)")
                    + f"。约束:{v.enforcement}"
                ),
            )
            for v in self.violations
        )
        return CheckerReport(
            passed=self.passed,
            issues=issues,
            metrics={
                "active_law_count": self.active_law_count,
                "violation_count": len(self.violations),
                "violated_dimensions": sorted({v.dimension for v in self.violations}),
            },
        )


def _trigger_ngrams(phrase: str) -> set[str]:
    """2-4 char shingles of a trigger phrase, for surface contact detection."""

    out: set[str] = set()
    phrase = phrase.strip()
    for run in _CJK_RUN.findall(phrase):
        for size in (4, 3, 2):
            for i in range(len(run) - size + 1):
                out.add(run[i : i + size])
    return out


def _split_paragraphs(text: str) -> list[str]:
    return [p for p in re.split(r"\n+", text or "") if p.strip()]


def _extract_constraints(enforcement: str) -> list[tuple[str, str]]:
    """Return ``(trigger_phrase, kind)`` constraints parsed from an enforcement.

    Conservative: only emits a constraint when a trigger marker or a prohibition
    marker is followed by a concrete CJK phrase.
    """

    constraints: list[tuple[str, str]] = []
    if not enforcement:
        return constraints

    for marker in _PROHIBITION_MARKERS:
        for m in re.finditer(re.escape(marker), enforcement):
            tail = enforcement[m.end() : m.end() + 16]
            run = _CJK_RUN.search(tail)
            if run:
                constraints.append((run.group(0), "prohibition"))

    is_conditional = any(c in enforcement for c in _CONDITIONAL_MARKERS)
    if is_conditional:
        for marker in _TRIGGER_MARKERS:
            for m in re.finditer(re.escape(marker), enforcement):
                tail = enforcement[m.end() : m.end() + 24]
                # Isolate the trigger noun: cut at the first conditional marker so
                # cue words ("须…理由") don't leak into the trigger phrase.
                cut = len(tail)
                for cond in _CONDITIONAL_MARKERS:
                    idx = tail.find(cond)
                    if idx != -1:
                        cut = min(cut, idx)
                run = _CJK_RUN.search(tail[:cut])
                if run:
                    constraints.append((run.group(0), "missing_justification"))
    return constraints


def detect_world_law_violations(
    text: str, laws: Sequence[WorldLaw]
) -> list[WorldLawViolation]:
    """Deterministic, conservative detection of prose that contradicts a law."""

    violations: list[WorldLawViolation] = []
    paragraphs = _split_paragraphs(text)
    seen: set[tuple[str, str]] = set()
    for law in laws:
        for trigger_phrase, kind in _extract_constraints(law.enforcement):
            ngrams = _trigger_ngrams(trigger_phrase)
            if not ngrams:
                continue
            for para in paragraphs:
                hit = next((g for g in ngrams if g in para), None)
                if hit is None:
                    continue
                if kind == "missing_justification" and any(
                    cue in para for cue in _JUSTIFICATION_CUES
                ):
                    continue  # a reason is present → not a violation
                key = (law.dimension, hit)
                if key in seen:
                    continue
                seen.add(key)
                violations.append(
                    WorldLawViolation(
                        dimension=law.dimension,
                        enforcement=law.enforcement,
                        trigger=hit,
                        kind=kind,
                    )
                )
                break
    return violations


def check_world_law_consistency_gate(
    text: str,
    *,
    chapter_position: int = 1,
    world_model: Mapping[str, Any] | None = None,
    judge: Callable[[str, Sequence[WorldLaw]], list[WorldLawViolation]] | None = None,
) -> WorldLawConsistencyReport:
    """Advisory check that the prose obeys the active world laws.

    ``judge`` (optional) replaces the deterministic detector with a richer
    semantic check (e.g. LLM-backed). Always returns a report; never raises.
    """

    if not text or not world_model:
        return WorldLawConsistencyReport(passed=True)
    try:
        laws = select_active_laws(world_model, context_text=text, max_laws=_MAX_ACTIVE_LAWS)
    except Exception:
        return WorldLawConsistencyReport(passed=True)
    if not laws:
        return WorldLawConsistencyReport(passed=True)
    try:
        detector = judge or detect_world_law_violations
        violations = detector(text, laws)
    except Exception:
        violations = []
    return WorldLawConsistencyReport(
        passed=not violations,
        violations=tuple(violations),
        active_law_count=len(laws),
    )


__all__ = [
    "CheckerIssue",
    "CheckerReport",
    "WorldLawConsistencyReport",
    "WorldLawViolation",
    "check_world_law_consistency_gate",
    "detect_world_law_violations",
]
