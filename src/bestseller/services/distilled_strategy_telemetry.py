"""Telemetry helpers for distilled strategy usage.

Phase 1 stores a ``DistilledStrategyCard``.  This module summarizes later
chapter/review outcomes against that card without adding database tables.  It
accepts plain mappings so workflow metadata, checker reports, or future DB rows
can all feed the same aggregation path.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from bestseller.services.checker_schema import CheckerReport
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


def _coerce_card(card: DistilledStrategyCard | Mapping[str, Any]) -> DistilledStrategyCard:
    if isinstance(card, DistilledStrategyCard):
        return card
    return distilled_strategy_card_from_dict(card)


def _report_issue_ids(report: object) -> list[str]:
    if isinstance(report, CheckerReport):
        return [issue.id for issue in report.issues]
    data = _as_mapping(report)
    return [
        _text(item.get("id"))
        for item in (_as_mapping(raw) for raw in _as_list(data.get("issues")))
        if _text(item.get("id"))
    ]


def summarize_distilled_strategy_outcomes(
    strategy_card: DistilledStrategyCard | Mapping[str, Any],
    *,
    usage_records: Sequence[Mapping[str, Any]] = (),
    checker_reports: Sequence[object] = (),
) -> dict[str, Any]:
    """Summarize mechanism usage, rewrites, quality deltas, and copy-risk hits."""

    card = _coerce_card(strategy_card)
    mechanism_ids = [item.mechanism_id for item in card.selected_mechanisms]
    per_mechanism: dict[str, dict[str, Any]] = {
        mechanism_id: {
            "mechanism_id": mechanism_id,
            "planned_uses": 0,
            "passed_uses": 0,
            "rewrite_triggered_uses": 0,
            "copy_risk_incidents": 0,
            "quality_deltas": [],
        }
        for mechanism_id in mechanism_ids
    }

    for record in usage_records:
        mechanism_id = _text(record.get("mechanism_id"))
        if mechanism_id not in per_mechanism:
            continue
        row = per_mechanism[mechanism_id]
        row["planned_uses"] += 1
        if bool(record.get("passed")):
            row["passed_uses"] += 1
        if bool(record.get("rewrite_triggered")):
            row["rewrite_triggered_uses"] += 1
        if bool(record.get("copy_risk")):
            row["copy_risk_incidents"] += 1
        try:
            row["quality_deltas"].append(float(record.get("quality_delta")))
        except (TypeError, ValueError):
            pass

    report_copy_risks = 0
    for report in checker_reports:
        issue_ids = _report_issue_ids(report)
        if any("COPY_RISK" in issue_id for issue_id in issue_ids):
            report_copy_risks += 1

    mechanism_rows: list[dict[str, Any]] = []
    for row in per_mechanism.values():
        deltas = list(row.pop("quality_deltas"))
        row["average_quality_delta"] = (
            round(sum(deltas) / len(deltas), 3) if deltas else None
        )
        mechanism_rows.append(row)

    totals = {
        "planned_uses": sum(int(row["planned_uses"]) for row in mechanism_rows),
        "passed_uses": sum(int(row["passed_uses"]) for row in mechanism_rows),
        "rewrite_triggered_uses": sum(
            int(row["rewrite_triggered_uses"]) for row in mechanism_rows
        ),
        "copy_risk_incidents": sum(
            int(row["copy_risk_incidents"]) for row in mechanism_rows
        )
        + report_copy_risks,
    }
    return {
        "aggregate_key": card.aggregate_key,
        "maturity_score": card.maturity_score,
        "maturity_status": card.maturity_status,
        "mechanisms": mechanism_rows,
        "totals": totals,
    }


__all__ = ["summarize_distilled_strategy_outcomes"]
