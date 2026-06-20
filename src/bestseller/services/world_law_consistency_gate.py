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

# ruff: noqa: E501, S110, ANN401

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
import logging
import re
from typing import Any

from bestseller.domain.world_model import WorldLaw
from bestseller.services.world_model_injection import select_active_laws

logger = logging.getLogger(__name__)

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
        _why = {
            "prohibition": "(被该规律禁止)",
            "missing_justification": "(未给出规律要求的理由)",
            "tier_mismatch": "(与世界阶梯数值矛盾)",
            "semantic": "(语义判定违背)",
        }
        issues = tuple(
            CheckerIssue(
                id=f"world_law_violation:{v.dimension}",
                message=(
                    f"[{v.dimension}] 正文可能违背世界规律:「{v.trigger}」"
                    + _why.get(v.kind, "")
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
    """3-4 char shingles of a trigger phrase, for surface contact detection.

    Deliberately drops 2-char shingles: they fire on incidental words (灵石/饮食)
    and were the source of false positives. 3+ chars keeps precision acceptable
    for the deterministic offline fallback (the LLM judge is the precise path).
    """

    out: set[str] = set()
    phrase = phrase.strip()
    for run in _CJK_RUN.findall(phrase):
        for size in (4, 3):
            for i in range(len(run) - size + 1):
                out.add(run[i : i + size])
    return out


_NUM_RE = re.compile(r"[0-9零一二三四五六七八九十百千万两]+")


def detect_tier_violations(text: str, laws: Sequence[WorldLaw]) -> list[WorldLawViolation]:
    """Flag prose that states a tier value contradicting the law's ladder.

    Precise + low-false-positive: only fires when a tier name appears in the prose
    immediately followed by a number that is NOT the ladder's number for that tier
    (e.g. ladder 筑基=三百岁 but prose "筑基…四百年").
    """

    violations: list[WorldLawViolation] = []
    for law in laws:
        for step in law.tiers:
            tier, value = step.tier, step.value
            ladder_nums = set(_NUM_RE.findall(value))
            if not tier or not ladder_nums or tier not in text:
                continue
            for m in re.finditer(re.escape(tier), text):
                window = text[m.end() : m.end() + 12]
                prose_nums = set(_NUM_RE.findall(window))
                if prose_nums and not (prose_nums & ladder_nums):
                    violations.append(
                        WorldLawViolation(
                            dimension=law.dimension,
                            enforcement=f"阶梯 {tier}={value}",
                            trigger=f"{tier}{''.join(sorted(prose_nums))}",
                            kind="tier_mismatch",
                        )
                    )
                    break
    return violations


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
        # Advisory gate: never raises, but don't hide a real selection bug.
        logger.warning("world-law gate: active-law selection failed; passing", exc_info=True)
        return WorldLawConsistencyReport(passed=True)
    if not laws:
        return WorldLawConsistencyReport(passed=True)
    try:
        detector = judge or detect_world_law_violations
        violations = list(detector(text, laws))
    except Exception:
        logger.warning("world-law gate: violation detector failed; treating as clean", exc_info=True)
        violations = []
    try:
        violations.extend(detect_tier_violations(text, laws))  # precise numeric check, always on
    except Exception:
        logger.debug("world-law gate: tier-violation check failed", exc_info=True)
    return WorldLawConsistencyReport(
        passed=not violations,
        violations=tuple(violations),
        active_law_count=len(laws),
    )


# ---------------------------------------------------------------------------
# LLM semantic judge (high recall + precision) — the production path
# ---------------------------------------------------------------------------


def build_world_law_judge_prompts(text: str, laws: Sequence[WorldLaw]) -> tuple[str, str]:
    """System + user prompts for the LLM violation judge."""

    system = (
        "你是世界观一致性审校。给定本书生效的『世界规律』和一段正文,只找出**明确违背**规律的地方"
        "(例如规律说飞行需到某境界,正文却让低境界角色随意飞行;规律说以灵石计价,正文却用纸币)。"
        "只输出 JSON:{\"violations\":[{\"dimension\":\"...\",\"reason\":\"≤30字\"}]};没有违背就输出 "
        '{"violations":[]}。不要臆测,拿不准就不报。'
    )
    law_lines = "\n".join(f"- [{law.dimension}] {law.enforcement}" for law in laws)
    user = f"【世界规律】\n{law_lines}\n\n【正文】\n{text[:2400]}\n\n只输出 JSON。"
    return system, user


def _parse_judge_violations(content: str, laws: Sequence[WorldLaw]) -> list[WorldLawViolation]:
    import json

    valid_dims = {law.dimension: law for law in laws}
    stripped = re.sub(r"^```(?:json)?\s*|\s*```$", "", (content or "").strip(), flags=re.I | re.S)
    match = re.search(r"\{.*\}", stripped, flags=re.S)
    payload: Any = {}
    for candidate in (stripped, match.group(0) if match else ""):
        if not candidate:
            continue
        try:
            payload = json.loads(candidate)
            break
        except json.JSONDecodeError:
            continue
    out: list[WorldLawViolation] = []
    for item in (payload or {}).get("violations", []) if isinstance(payload, dict) else []:
        if not isinstance(item, Mapping):
            continue
        dim = str(item.get("dimension") or "").strip()
        law = valid_dims.get(dim)
        if law is None:
            continue
        out.append(
            WorldLawViolation(
                dimension=dim,
                enforcement=law.enforcement,
                trigger=str(item.get("reason") or "语义违背")[:30],
                kind="semantic",
            )
        )
    return out


async def evaluate_world_law_consistency_llm(
    session: Any,
    settings: Any,
    text: str,
    *,
    chapter_position: int = 1,
    world_model: Mapping[str, Any] | None = None,
    language: str = "zh",
) -> WorldLawConsistencyReport:
    """Production gate: LLM semantic judge + deterministic tier check (fallback-safe).

    Never raises; on any LLM failure it degrades to the deterministic detector.
    """

    if not text or not world_model:
        return WorldLawConsistencyReport(passed=True)
    try:
        laws = select_active_laws(world_model, context_text=text, max_laws=_MAX_ACTIVE_LAWS)
    except Exception:
        logger.warning("world-law gate: active-law selection failed; passing", exc_info=True)
        return WorldLawConsistencyReport(passed=True)
    if not laws:
        return WorldLawConsistencyReport(passed=True)

    violations: list[WorldLawViolation] = []
    try:
        from bestseller.services.llm import LLMCompletionRequest, complete_text

        system_prompt, user_prompt = build_world_law_judge_prompts(text, laws)
        completion = await complete_text(
            session,
            settings,
            LLMCompletionRequest(
                logical_role="critic",
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                fallback_response='{"violations":[]}',
                prompt_template="world_law_consistency",
                prompt_version="v1",
                max_tokens_override=600,
            ),
        )
        violations.extend(_parse_judge_violations(completion.content, laws))
    except Exception:
        # LLM unavailable → fall back to the deterministic enforcement detector.
        logger.debug("world-law gate: LLM judge failed; using deterministic detector", exc_info=True)
        try:
            violations.extend(detect_world_law_violations(text, laws))
        except Exception:
            logger.warning("world-law gate: deterministic detector also failed", exc_info=True)
    try:
        violations.extend(detect_tier_violations(text, laws))
    except Exception:
        pass
    # De-dup by (dimension, kind).
    seen: set[tuple[str, str]] = set()
    deduped: list[WorldLawViolation] = []
    for v in violations:
        key = (v.dimension, v.kind)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(v)
    return WorldLawConsistencyReport(
        passed=not deduped,
        violations=tuple(deduped),
        active_law_count=len(laws),
    )


__all__ = [
    "CheckerIssue",
    "CheckerReport",
    "WorldLawConsistencyReport",
    "WorldLawViolation",
    "build_world_law_judge_prompts",
    "check_world_law_consistency_gate",
    "detect_tier_violations",
    "detect_world_law_violations",
    "evaluate_world_law_consistency_llm",
]
