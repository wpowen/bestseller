"""Quality Trend Dashboard (``config/quality_trend_dashboard.yaml``).

Long-form trend monitor. Runs once every ``trigger_schedule.rule``
chapters (default 10). Consumes per-chapter audit data the pipeline
already persists in :class:`ReviewReportModel` and emits a typed
:class:`DashboardWindow` for the orchestrator to act on.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Iterable

from bestseller.services.quality_levers._loader import (
    as_dict,
    as_int,
    as_str,
    as_str_tuple,
    load_yaml,
)


_CONFIG_FILENAME = "quality_trend_dashboard.yaml"


@dataclass(frozen=True)
class AlertRule:
    """One rule under ``metrics.<id>.alert_rules``."""

    rule: str
    severity: str
    action: str


@dataclass(frozen=True)
class MetricDefinition:
    metric_id: str
    description: str
    window: str
    alert_rules: tuple[AlertRule, ...]


@dataclass(frozen=True)
class QualityTrendDashboardConfig:
    version: str
    metrics: dict[str, MetricDefinition]
    window_size: int
    storage_path_template: str


def _parse_alert_rules(raw: object) -> tuple[AlertRule, ...]:
    if not isinstance(raw, list):
        return ()
    out: list[AlertRule] = []
    for entry in raw:
        body = as_dict(entry)
        out.append(
            AlertRule(
                rule=as_str(body.get("rule")),
                severity=as_str(body.get("severity"), default="yellow"),
                action=as_str(body.get("action")),
            )
        )
    return tuple(out)


def _parse_metric(metric_id: str, raw: object) -> MetricDefinition:
    data = as_dict(raw)
    return MetricDefinition(
        metric_id=metric_id,
        description=as_str(data.get("description")),
        window=as_str(data.get("window")),
        alert_rules=_parse_alert_rules(data.get("alert_rules")),
    )


def _extract_window_size(raw: object) -> int:
    text = as_str(as_dict(raw).get("rule"))
    digits = "".join(ch if ch.isdigit() else " " for ch in text).split()
    if digits:
        try:
            return int(digits[0])
        except ValueError:
            return 10
    return 10


@lru_cache(maxsize=1)
def load_quality_trend_dashboard() -> QualityTrendDashboardConfig:
    """Return the typed view."""

    raw = load_yaml(_CONFIG_FILENAME)
    metrics_raw = as_dict(raw.get("metrics"))
    metrics: dict[str, MetricDefinition] = {}
    for metric_id, body in metrics_raw.items():
        canonical = as_str(metric_id)
        if not canonical:
            continue
        metrics[canonical] = _parse_metric(canonical, body)

    report_format = as_dict(raw.get("report_format"))
    storage = as_str(report_format.get("storage_path"), default="dashboard/window-{N}-{M}.md")
    schedule = raw.get("trigger_schedule")

    return QualityTrendDashboardConfig(
        version=as_str(raw.get("version")),
        metrics=metrics,
        window_size=_extract_window_size(schedule),
        storage_path_template=storage,
    )


# ---------------------------------------------------------------------------
# Trend evaluation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ChapterScoreSnapshot:
    """One chapter's already-computed quality metrics."""

    chapter_number: int
    persona_scores: dict[str, float]
    anti_pattern_hits: int = 0
    clue_payoff_ratio: float | None = None
    open_questions_count: int = 0
    signature_types_present: tuple[str, ...] = ()
    voice_drift_vs_ch1: float | None = None


@dataclass(frozen=True)
class DashboardAlert:
    severity: str  # "red" | "yellow"
    metric_id: str
    message: str
    action: str


@dataclass(frozen=True)
class DashboardWindow:
    """Aggregated metrics + alerts for one window."""

    start_chapter: int
    end_chapter: int
    persona_avg_scores: dict[str, float]
    anti_pattern_total: int
    signature_type_diversity: int
    overdue_clue_payoff: bool
    voice_drift_alert: bool
    open_question_breaches: int
    alerts: tuple[DashboardAlert, ...]


def evaluate_dashboard_window(
    chapters: Iterable[ChapterScoreSnapshot],
) -> DashboardWindow | None:
    """Roll up a window of per-chapter snapshots into a dashboard report.

    Returns ``None`` when the input is empty so the orchestrator can
    skip persistence for an empty window.
    """

    items = list(chapters)
    if not items:
        return None
    items.sort(key=lambda c: c.chapter_number)

    persona_buckets: dict[str, list[float]] = {}
    anti_pattern_total = 0
    signature_types: set[str] = set()
    overdue_clue = False
    voice_drift_alert = False
    open_question_breaches = 0

    for snapshot in items:
        for persona_id, score in snapshot.persona_scores.items():
            persona_buckets.setdefault(persona_id, []).append(score)
        anti_pattern_total += snapshot.anti_pattern_hits
        signature_types.update(snapshot.signature_types_present)
        if (
            snapshot.clue_payoff_ratio is not None
            and snapshot.clue_payoff_ratio < 0.50
        ):
            overdue_clue = True
        if (
            snapshot.voice_drift_vs_ch1 is not None
            and snapshot.voice_drift_vs_ch1 < 0.85
        ):
            voice_drift_alert = True
        if snapshot.open_questions_count > 8:
            open_question_breaches += 1

    persona_avg = {
        persona_id: statistics.mean(values) if values else 0.0
        for persona_id, values in persona_buckets.items()
    }

    alerts: list[DashboardAlert] = []
    for persona_id, avg in persona_avg.items():
        if avg < 0.70:
            alerts.append(
                DashboardAlert(
                    severity="red",
                    metric_id=f"persona_avg::{persona_id}",
                    message=f"{persona_id} window avg {avg:.2f} < 0.70",
                    action="halt_writing_and_revisit_previous_window",
                )
            )
    if overdue_clue:
        alerts.append(
            DashboardAlert(
                severity="red",
                metric_id="clue_payoff_ratio",
                message="cumulative payoff ratio below 0.50",
                action="pause_new_foreshadow_planting_until_paid_off",
            )
        )
    if voice_drift_alert:
        alerts.append(
            DashboardAlert(
                severity="red",
                metric_id="voice_drift_vs_ch1",
                message="protagonist voice similarity vs ch1 < 0.85",
                action="run_pov_voice_drift_recovery",
            )
        )
    if open_question_breaches:
        alerts.append(
            DashboardAlert(
                severity="yellow",
                metric_id="open_question_ceiling",
                message=f"{open_question_breaches} chapter(s) exceeded 8 open questions",
                action="resolve_at_least_one_curiosity_next_chapter",
            )
        )
    if len(signature_types) < 3:
        alerts.append(
            DashboardAlert(
                severity="yellow",
                metric_id="signature_type_diversity",
                message=f"only {len(signature_types)} signature_type(s) in window",
                action="diversify_signature_types_in_next_chapter",
            )
        )

    return DashboardWindow(
        start_chapter=items[0].chapter_number,
        end_chapter=items[-1].chapter_number,
        persona_avg_scores=persona_avg,
        anti_pattern_total=anti_pattern_total,
        signature_type_diversity=len(signature_types),
        overdue_clue_payoff=overdue_clue,
        voice_drift_alert=voice_drift_alert,
        open_question_breaches=open_question_breaches,
        alerts=tuple(alerts),
    )


def render_dashboard_summary(window: DashboardWindow) -> str:
    """Render a human-readable summary for ``audits/dashboard/window-N-M.md``."""

    lines: list[str] = [
        f"# Quality Trend Window · ch{window.start_chapter}-ch{window.end_chapter}",
        "",
        "## Persona avg scores",
    ]
    for persona_id, avg in window.persona_avg_scores.items():
        lines.append(f"- {persona_id}: {avg:.2f}")
    lines.append("")
    lines.append(f"- anti_pattern_total: {window.anti_pattern_total}")
    lines.append(
        f"- signature_type_diversity: {window.signature_type_diversity}"
    )
    lines.append(f"- overdue_clue_payoff: {window.overdue_clue_payoff}")
    lines.append(f"- voice_drift_alert: {window.voice_drift_alert}")
    lines.append(
        f"- open_question_breaches: {window.open_question_breaches}"
    )
    lines.append("")
    lines.append("## Alerts")
    if not window.alerts:
        lines.append("- (none)")
    else:
        for alert in window.alerts:
            lines.append(
                f"- [{alert.severity.upper()}] {alert.metric_id}: "
                f"{alert.message} → {alert.action}"
            )
    return "\n".join(lines)
