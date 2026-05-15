"""Deterministic gate for distilled-strategy consumption.

The compiler creates a project-specific ``DistilledStrategyCard``.  This gate
checks whether downstream planning artifacts actually consume that card safely:
state variables should appear in the plan, selected mechanisms should be bound
to concrete project choices, and anti-copy boundaries must not be violated.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import json
from typing import Any

from bestseller.services.checker_schema import CheckerIssue, CheckerReport
from bestseller.services.distilled_strategy_compiler import (
    DistilledStrategyCard,
    distilled_strategy_card_from_dict,
)


def _as_mapping(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _as_list(value: object) -> list[object]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return []


def _text(value: object) -> str:
    return str(value).strip() if value is not None else ""


def _string_items(value: object) -> list[str]:
    return [_text(item) for item in _as_list(value) if _text(item)]


def _json_blob(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except TypeError:
        return str(value)


def _contains(blob: str, needle: str) -> bool:
    text = needle.strip()
    return bool(text) and text.lower() in blob.lower()


def _present_items(blob: str, values: Sequence[str]) -> list[str]:
    return [value for value in values if _contains(blob, value)]


def _issue(
    issue_id: str,
    *,
    severity: str,
    location: str,
    description: str,
    suggestion: str,
    can_override: bool = False,
) -> CheckerIssue:
    return CheckerIssue(
        id=issue_id,
        type="distilled_strategy",
        severity=severity,  # type: ignore[arg-type]
        location=location,
        description=description,
        suggestion=suggestion,
        can_override=can_override,
    )


def _coerce_card(
    card: DistilledStrategyCard | Mapping[str, Any] | None,
) -> DistilledStrategyCard | None:
    if card is None:
        return None
    if isinstance(card, DistilledStrategyCard):
        return card
    return distilled_strategy_card_from_dict(card)


def evaluate_distilled_strategy_consumption(
    strategy_card: DistilledStrategyCard | Mapping[str, Any] | None,
    *,
    story_design_kernel: object | None = None,
    volume_plan: object | None = None,
    chapter_outlines: object | None = None,
    plan_text: str | None = None,
) -> CheckerReport:
    """Return a CheckerReport for strategy use in planning artifacts."""

    card = _coerce_card(strategy_card)
    if card is None:
        missing = _issue(
            "DISTILLED_STRATEGY_MISSING",
            severity="medium",
            location="project.metadata.distilled_strategy_card",
            description="Distilled references are expected but no strategy card is present.",
            suggestion="Compile and persist DistilledStrategyCard before final planning.",
            can_override=True,
        )
        return CheckerReport(
            agent="distilled-strategy-gate",
            chapter=0,
            overall_score=88,
            passed=True,
            issues=(missing,),
            metrics={"strategy_present": False},
            summary="Distilled strategy card is missing.",
        )

    blob = "\n".join(
        item
        for item in (
            _json_blob(story_design_kernel),
            _json_blob(volume_plan),
            _json_blob(chapter_outlines),
            plan_text or "",
        )
        if item
    )
    issues: list[CheckerIssue] = []

    if card.maturity_status == "unsafe" or card.maturity_score <= 0:
        issues.append(
            _issue(
                "DISTILLED_STRATEGY_UNSAFE",
                severity="high",
                location="distilled_strategy_card.maturity",
                description="Distilled aggregate is not mature enough for planning use.",
                suggestion=(
                    "Rebuild aggregate with anti-copy coverage and validated "
                    "source packages."
                ),
            )
        )
    elif card.maturity_score < 0.3 or card.maturity_status == "pilot":
        issues.append(
            _issue(
                "DISTILLED_STRATEGY_LOW_MATURITY",
                severity="low",
                location="distilled_strategy_card.maturity",
                description="Distilled aggregate is a low-maturity pilot reference.",
                suggestion="Use it as directional inspiration, not as a hard template.",
                can_override=True,
            )
        )

    fallback_hits = [
        marker
        for marker in ("fallback aggregation", "llm output fallback")
        if marker in blob.lower()
    ]
    if fallback_hits:
        issues.append(
            _issue(
                "DISTILLED_STRATEGY_FALLBACK_LEAK",
                severity="high",
                location="planning_artifacts",
                description="Fallback distillation placeholders leaked into planning artifacts.",
                suggestion=(
                    "Regenerate aggregate or quarantine fallback volume rows "
                    "before planning."
                ),
            )
        )

    blocked_hits = _present_items(blob, card.anti_copy_boundaries)
    if blocked_hits:
        issues.append(
            _issue(
                "DISTILLED_STRATEGY_COPY_RISK",
                severity="critical",
                location="planning_artifacts",
                description=(
                    "Planning artifacts contain anti-copy boundary terms or "
                    "blocked combinations."
                ),
                suggestion=(
                    "Rewrite the plan by changing names, opening chain, "
                    "mechanism bundle, or scenario order."
                ),
                can_override=False,
            )
        )

    required_states = _string_items(card.required_state_variables)
    required_vectors = _string_items(card.required_change_vectors)
    reader_rewards = _string_items(card.reader_reward_mix)
    mechanism_ids = [
        item.mechanism_id
        for item in card.selected_mechanisms
        if item.mechanism_id
    ]
    present_states = _present_items(blob, required_states)
    present_vectors = _present_items(blob, required_vectors)
    present_rewards = _present_items(blob, reader_rewards)
    present_mechanisms = _present_items(blob, mechanism_ids)
    has_planning_material = bool(blob.strip())
    consumed_signal_count = (
        len(present_states)
        + len(present_vectors)
        + len(present_rewards)
        + len(present_mechanisms)
    )
    if has_planning_material and card.selected_mechanisms and consumed_signal_count == 0:
        issues.append(
            _issue(
                "DISTILLED_STRATEGY_NOT_CONSUMED",
                severity="high",
                location="planning_artifacts",
                description=(
                    "Planning artifacts do not show transformed use of the "
                    "selected strategy."
                ),
                suggestion=(
                    "Bind selected mechanisms to project-specific world rules, "
                    "character choices, resource costs, or payoff windows."
                ),
            )
        )
    elif has_planning_material and required_states and not present_states:
        issues.append(
            _issue(
                "DISTILLED_STRATEGY_STATE_VARIABLES_MISSING",
                severity="medium",
                location="planning_artifacts.state_variables",
                description="No required distilled state variable appears in planning artifacts.",
                suggestion=(
                    "Add measurable state variables to StoryDesignKernel, "
                    "VolumePlan, or chapter outlines."
                ),
                can_override=True,
            )
        )

    severity_penalties = {"critical": 25, "high": 15, "medium": 8, "low": 3}
    penalty = sum(severity_penalties[issue.severity] for issue in issues)
    passed = not any(issue.severity in {"critical", "high"} for issue in issues)
    return CheckerReport(
        agent="distilled-strategy-gate",
        chapter=0,
        overall_score=max(0, 100 - penalty),
        passed=passed,
        issues=tuple(issues),
        metrics={
            "strategy_present": True,
            "aggregate_key": card.aggregate_key,
            "maturity_score": card.maturity_score,
            "maturity_status": card.maturity_status,
            "selected_mechanism_count": len(card.selected_mechanisms),
            "present_mechanisms": present_mechanisms,
            "present_state_variables": present_states,
            "present_change_vectors": present_vectors,
            "present_reader_rewards": present_rewards,
            "copy_risk_hits": blocked_hits,
            "consumed_signal_count": consumed_signal_count,
        },
        summary=(
            "Distilled strategy consumed safely."
            if passed
            else "Distilled strategy needs planning repair."
        ),
    )


def distilled_strategy_gate_snapshot(report: CheckerReport) -> dict[str, Any]:
    """Compact kernel-friendly snapshot from a full CheckerReport."""

    return {
        "present": bool(report.metrics.get("strategy_present")),
        "passed": report.passed,
        "score": report.overall_score,
        "maturity_score": report.metrics.get("maturity_score"),
        "maturity_status": report.metrics.get("maturity_status"),
        "selected_mechanism_count": report.metrics.get("selected_mechanism_count", 0),
        "consumed_signal_count": report.metrics.get("consumed_signal_count", 0),
        "issue_codes": [issue.id for issue in report.issues],
        "report": report.to_dict(),
    }


__all__ = [
    "distilled_strategy_gate_snapshot",
    "evaluate_distilled_strategy_consumption",
]
