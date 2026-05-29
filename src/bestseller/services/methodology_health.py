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

CAPABILITY_SLOTS = (
    "premise_engine",
    "character_change_tracker",
    "worldview_theme",
    "scene_causality_engine",
    "hook_ledger",
    "payoff_ledger",
    "pacing_compression_engine",
    "opening_three_function",
    "pov_distance_controller",
    "dialogue_subtext_engine",
    "revision_repair_engine",
)


def build_configured_methodology_health_report(
    *,
    checker_reports: Iterable[CheckerReport] = (),
    review_payloads: Iterable[Mapping[str, Any]] = (),
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
        review_payloads=review_payloads,
        latest_chapter_number=latest_chapter_number,
        longform_inputs=longform_inputs,
        longform_chaos_enabled=cfg.longform_chaos_enabled,
        longform_chaos_start_after_chapter=cfg.longform_chaos_start_after_chapter,
    )


def build_methodology_health_report(
    *,
    profile_id: str | None = "plova_structured_writing_v1",
    checker_reports: Iterable[CheckerReport] = (),
    review_payloads: Iterable[Mapping[str, Any]] = (),
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
    lineage_slots = build_lineage_slot_health(review_payloads)

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
        "lineage_slots": lineage_slots,
    }


def build_lineage_slot_health(
    review_payloads: Iterable[Mapping[str, Any]],
    *,
    coverage_min: float = 0.2,
    evidence_min: float = 0.5,
    failure_rate_max: float = 0.4,
) -> dict[str, Any]:
    payloads = [payload for payload in review_payloads if isinstance(payload, Mapping)]
    slot_stats: dict[str, dict[str, Any]] = {
        slot: {
            "coverage_count": 0,
            "rule_count": 0,
            "scored_count": 0,
            "evidence_count": 0,
            "failure_count": 0,
            "gate_block_count": 0,
            "rule_ids": set(),
        }
        for slot in CAPABILITY_SLOTS
    }

    for payload in payloads:
        evidence_summary = _review_evidence_summary(payload)
        covered_slots: set[str] = set()
        lineage = evidence_summary.get("methodology_lineage_evidence")
        if isinstance(lineage, Mapping):
            for rule in _iter_mapping_items(lineage.get("rules")):
                slot = str(rule.get("slot") or "")
                if slot not in slot_stats:
                    continue
                covered_slots.add(slot)
                stats = slot_stats[slot]
                stats["rule_count"] += 1
                rule_id = str(rule.get("rule_id") or "").strip()
                if rule_id:
                    stats["rule_ids"].add(rule_id)
                if rule.get("gate_mode") == "block":
                    stats["gate_block_count"] += 1
                score = _numeric_score(rule.get("score"))
                if score is None:
                    continue
                stats["scored_count"] += 1
                if score >= evidence_min:
                    stats["evidence_count"] += 1
                else:
                    stats["failure_count"] += 1

        _record_ledger_health_signal(
            evidence_summary,
            audit_key="hook_ledger_audit",
            slot="hook_ledger",
            covered_slots=covered_slots,
            slot_stats=slot_stats,
        )
        _record_ledger_health_signal(
            evidence_summary,
            audit_key="payoff_ledger_audit",
            slot="payoff_ledger",
            covered_slots=covered_slots,
            slot_stats=slot_stats,
        )

        for slot in covered_slots:
            slot_stats[slot]["coverage_count"] += 1

    total_reviews = len(payloads)
    slot_reports: dict[str, dict[str, Any]] = {}
    dormant_candidates: list[str] = []
    inactive_slots: list[str] = []
    denominator = max(total_reviews, 1)
    for slot, stats in slot_stats.items():
        coverage_count = int(stats["coverage_count"])
        scored_count = int(stats["scored_count"])
        evidence_count = int(stats["evidence_count"])
        failure_count = int(stats["failure_count"])
        coverage_ratio = coverage_count / denominator
        evidence_rate = evidence_count / scored_count if scored_count else 0.0
        failure_rate = failure_count / scored_count if scored_count else 0.0
        is_candidate = (
            coverage_count > 0
            and coverage_ratio >= coverage_min
            and evidence_rate >= evidence_min
            and failure_rate <= failure_rate_max
        )
        if coverage_count == 0:
            status = "inactive"
            inactive_slots.append(slot)
        elif is_candidate:
            status = "dormant_to_active_candidate"
            dormant_candidates.append(slot)
        elif failure_rate > failure_rate_max:
            status = "unstable"
        elif evidence_rate < evidence_min:
            status = "low_evidence"
        else:
            status = "observed"

        slot_reports[slot] = {
            "status": status,
            "coverage_count": coverage_count,
            "rule_count": int(stats["rule_count"]),
            "scored_count": scored_count,
            "evidence_count": evidence_count,
            "failure_count": failure_count,
            "gate_block_count": int(stats["gate_block_count"]),
            "coverage_ratio": round(coverage_ratio, 3),
            "evidence_rate": round(evidence_rate, 3),
            "failure_rate": round(failure_rate, 3),
            "rule_ids": sorted(stats["rule_ids"]),
        }

    return {
        "review_count": total_reviews,
        "slots": slot_reports,
        "dormant_to_active_candidates": dormant_candidates,
        "inactive_slots": inactive_slots,
        "thresholds": {
            "coverage_min": coverage_min,
            "evidence_min": evidence_min,
            "failure_rate_max": failure_rate_max,
        },
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
        "entry_freshness": _debt_component(
            raw,
            "entry_freshness",
            count_keys=("stale_truth_count",),
        ),
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
        actions.append(
            {
                "action": "repair_opening_three_function",
                "status": "manual_or_rewrite_required",
            }
        )
    if any(code.startswith("ACTION_SCENE_") for code in issue_codes):
        actions.append(
            {
                "action": "review_action_scene_structure",
                "status": "manual_or_rewrite_required",
            }
        )
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
    lineage_slots = report.get("lineage_slots")
    if isinstance(lineage_slots, Mapping):
        candidates = lineage_slots.get("dormant_to_active_candidates") or []
        inactive = lineage_slots.get("inactive_slots") or []
        if candidates:
            actions.append(
                {
                    "action": "promote_dormant_methodology_slots",
                    "status": "planning_required",
                    "slots": list(candidates)[:10],
                }
            )
        if inactive:
            actions.append(
                {
                    "action": "review_inactive_methodology_slots",
                    "status": "manual_required",
                    "count": len(inactive),
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


def _review_evidence_summary(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    direct = payload.get("evidence_summary")
    if isinstance(direct, Mapping):
        return direct
    nested = payload.get("structured_output")
    if isinstance(nested, Mapping):
        nested_summary = nested.get("evidence_summary")
        if isinstance(nested_summary, Mapping):
            return nested_summary
    return {}


def _iter_mapping_items(value: object) -> Iterable[Mapping[str, Any]]:
    if isinstance(value, Mapping):
        yield value
        return
    if isinstance(value, str) or not isinstance(value, Iterable):
        return
    for item in value:
        if isinstance(item, Mapping):
            yield item


def _numeric_score(value: object) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _record_ledger_health_signal(
    evidence_summary: Mapping[str, Any],
    *,
    audit_key: str,
    slot: str,
    covered_slots: set[str],
    slot_stats: Mapping[str, dict[str, Any]],
) -> None:
    audit = evidence_summary.get(audit_key)
    if not isinstance(audit, Mapping) or slot not in slot_stats:
        return
    covered_slots.add(slot)
    stats = slot_stats[slot]
    stats["scored_count"] += 1
    findings = audit.get("findings")
    has_findings = (
        bool(findings)
        if isinstance(findings, Iterable) and not isinstance(findings, str)
        else False
    )
    if has_findings:
        stats["failure_count"] += 1
    else:
        stats["evidence_count"] += 1


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
