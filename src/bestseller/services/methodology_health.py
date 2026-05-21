"""Methodology profile coverage and project-health aggregation."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from typing import Any

from bestseller.services.checker_schema import CheckerReport
from bestseller.services.methodology_cards import (
    load_methodology_cards,
    load_methodology_source_set,
    methodology_coverage_summary,
    validate_card_sources,
)
from bestseller.services.methodology_profile import (
    enabled_cards,
    load_methodology_profile,
    load_profile_deck,
    validate_methodology_profile,
)

_METHODOLOGY_PREFIXES = (
    "ACTION_SCENE_",
    "CHEKHOV_",
    "LONGFORM_",
    "METHODOLOGY_",
    "OPENING_",
)


def build_configured_methodology_health_report(
    *,
    checker_reports: Iterable[CheckerReport] = (),
    latest_chapter_number: int = 0,
    longform_inputs: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    try:
        from bestseller.services.quality_gates_config import get_quality_gates_config

        cfg = get_quality_gates_config().methodology_framework
    except Exception:
        return {"enabled": False, "reason": "quality_gates_unavailable"}

    if not cfg.enabled:
        return {"enabled": False, "reason": "methodology_framework_disabled"}

    return build_methodology_health_report(
        profile_id=cfg.profile_id,
        checker_reports=checker_reports,
        latest_chapter_number=latest_chapter_number,
        longform_inputs=longform_inputs,
        longform_chaos_enabled=cfg.longform_chaos_enabled,
        longform_chaos_start_after_chapter=cfg.longform_chaos_start_after_chapter,
    )


def build_methodology_health_report(
    *,
    profile_id: str | None = "plova_structured_writing_v1",
    checker_reports: Iterable[CheckerReport] = (),
    latest_chapter_number: int = 0,
    longform_inputs: Mapping[str, Any] | None = None,
    longform_chaos_enabled: bool = False,
    longform_chaos_start_after_chapter: int = 30,
) -> dict[str, Any]:
    if not profile_id:
        return {"enabled": False, "reason": "methodology_profile_missing"}

    try:
        source_set = load_methodology_source_set()
        cards = load_methodology_cards()
        profile = load_methodology_profile(profile_id)
        deck = load_profile_deck(profile)
    except ValueError as exc:
        return {
            "enabled": False,
            "reason": "methodology_assets_invalid",
            "error": str(exc),
        }

    coverage = methodology_coverage_summary(cards, source_set)
    findings = [
        *validate_card_sources(cards, source_set),
        *validate_methodology_profile(profile, deck),
    ]
    active_cards = [
        *enabled_cards(profile, deck, stage="planning", scope="book"),
        *enabled_cards(profile, deck, stage="review", scope="chapter"),
        *enabled_cards(profile, deck, stage="drafting", scope="scene"),
        *enabled_cards(profile, deck, stage="health", scope="project_health"),
    ]
    active_gates = sorted(
        {
            binding.gate
            for card in active_cards
            for binding in card.gate_bindings
        }
    )
    top_issues = _top_methodology_issues(checker_reports)
    chaos = compute_longform_chaos_index(
        latest_chapter_number=latest_chapter_number,
        inputs=longform_inputs,
        enabled=longform_chaos_enabled,
        start_after_chapter=longform_chaos_start_after_chapter,
    )

    return {
        "enabled": True,
        "methodology_profile_id": profile.profile_id,
        "coverage": coverage,
        "active_gates": active_gates,
        "active_card_count": len({card.id for card in active_cards}),
        "pending_sources": list(profile.pending_sources),
        "findings": [finding.model_dump(mode="json") for finding in findings],
        "top_methodology_issues": top_issues,
        "longform_chaos": chaos,
    }


def compute_longform_chaos_index(
    *,
    latest_chapter_number: int,
    inputs: Mapping[str, Any] | None = None,
    enabled: bool = True,
    start_after_chapter: int = 30,
) -> dict[str, Any]:
    if not enabled:
        return {"enabled": False}

    raw = dict(inputs or {})
    components = {
        "line_balance": _component(raw, "line_balance", default=1.0),
        "foreshadowing_debt": _debt_component(
            raw,
            "foreshadowing_debt",
            count_keys=("overdue_clue_count", "setup_payoff_debt_count"),
        ),
        "timeline_stability": _component(raw, "timeline_stability", default=1.0),
        "entry_freshness": _debt_component(raw, "entry_freshness", count_keys=("stale_truth_count",)),
        "world_reveal_control": _component(raw, "world_reveal_control", default=1.0),
        "outline_executability": _component(raw, "outline_executability", default=1.0),
    }
    health = sum(components.values()) / len(components)
    score = round(max(0.0, min(1.0, 1.0 - health)), 4)
    audit_only = latest_chapter_number < start_after_chapter
    risk_level = "audit_only" if audit_only else _risk_level(score)
    top_repairs = [
        _repair_for_component(name)
        for name, value in sorted(components.items(), key=lambda item: item[1])
        if value < 0.7
    ][:4]

    return {
        "enabled": True,
        "audit_only": audit_only,
        "score": score,
        "risk_level": risk_level,
        "latest_chapter_number": latest_chapter_number,
        "start_after_chapter": start_after_chapter,
        "components": {key: round(value, 4) for key, value in components.items()},
        "top_repairs": top_repairs,
    }


def methodology_repair_actions(report: Mapping[str, Any]) -> list[dict[str, Any]]:
    if not report.get("enabled"):
        return []

    actions: list[dict[str, Any]] = []
    pending = report.get("pending_sources") or []
    if pending:
        actions.append(
            {
                "action": "review_methodology_pending_sources",
                "status": "manual_required",
                "count": len(pending),
            }
        )
    issue_codes = [
        str(item.get("code"))
        for item in report.get("top_methodology_issues", [])
        if isinstance(item, Mapping)
    ]
    if any(code.startswith("OPENING_") for code in issue_codes):
        actions.append({"action": "repair_opening_three_function", "status": "manual_or_rewrite_required"})
    if any(code.startswith("ACTION_SCENE_") for code in issue_codes):
        actions.append({"action": "review_action_scene_structure", "status": "manual_or_rewrite_required"})
    if "CHEKHOV_USE_OVERDUE" in issue_codes:
        actions.append({"action": "review_chekhov_overdue", "status": "manual_required"})
    chaos = report.get("longform_chaos")
    if isinstance(chaos, Mapping) and chaos.get("risk_level") in {"high", "critical"}:
        actions.append(
            {
                "action": "repair_longform_chaos",
                "status": "planning_required",
                "risk_level": chaos.get("risk_level"),
            }
        )
    return actions


def _top_methodology_issues(reports: Iterable[CheckerReport]) -> list[dict[str, Any]]:
    counter: Counter[str] = Counter()
    for report in reports:
        for issue in report.issues:
            if issue.id.startswith(_METHODOLOGY_PREFIXES):
                counter[issue.id] += 1
    return [
        {"code": code, "count": count}
        for code, count in counter.most_common(10)
    ]


def _component(raw: Mapping[str, Any], key: str, *, default: float) -> float:
    value = raw.get(key, default)
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return default


def _debt_component(
    raw: Mapping[str, Any],
    key: str,
    *,
    count_keys: tuple[str, ...],
) -> float:
    if key in raw:
        return _component(raw, key, default=1.0)
    debt_count = 0
    for count_key in count_keys:
        try:
            debt_count += int(raw.get(count_key) or 0)
        except (TypeError, ValueError):
            continue
    return max(0.0, 1.0 - min(debt_count, 10) / 10)


def _risk_level(score: float) -> str:
    if score >= 0.75:
        return "critical"
    if score >= 0.5:
        return "high"
    if score >= 0.25:
        return "medium"
    return "low"


def _repair_for_component(component: str) -> dict[str, str]:
    labels = {
        "entry_freshness": "materialize_or_refresh_truth_assets",
        "foreshadowing_debt": "review_foreshadowing_and_setup_payoff_debt",
        "line_balance": "rebalance_narrative_lines",
        "outline_executability": "repair_outline_executability",
        "timeline_stability": "review_timeline_consistency",
        "world_reveal_control": "reduce_world_reveal_load",
    }
    return {"component": component, "action": labels.get(component, f"review_{component}")}


__all__ = [
    "build_configured_methodology_health_report",
    "build_methodology_health_report",
    "compute_longform_chaos_index",
    "methodology_repair_actions",
]
