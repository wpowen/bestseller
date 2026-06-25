"""Normalize design-time quality reports for the design dossier UI.

The design dossier persists a heap of planning-stage quality signals in project
metadata (prewrite readiness, commercial-planning judge verdicts, opening-gate
reports, identity manifest status, …). Each has its own shape. This module
flattens whatever is present into one frontend-friendly structure so the
"规划产物" tab can surface "判官怎么说 / 哪些门禁亮红" next to the artifacts —
instead of forcing the reader to dig through raw JSON.

Pure / deterministic / zero-token: it only reshapes the metadata dict, so every
function here is unit-testable without a database.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

# ruff: noqa: ANN401, RUF002 — Chinese labels + Any metadata values are intentional.

SCHEMA_VERSION = "design-quality.v1"

# (metadata_key, human label, scope) — the curated set surfaced on the page.
# ``scope`` lets the frontend attach a report to the right artifact group.
_REPORT_SPECS: tuple[tuple[str, str, str], ...] = (
    ("prewrite_readiness_report", "开写前就绪度", "overall"),
    ("commercial_planning_llm_judge", "商业化规划判官", "outline"),
    ("commercial_planning_readiness_report", "商业化规划就绪", "outline"),
    ("commercial_planning_readiness_status", "商业化就绪状态", "outline"),
    ("opening_quality_planning_gate_report", "开篇质量门禁", "opening"),
    ("qimao_planning_gate_report", "七猫开篇门禁", "opening"),
    ("fanqie_long_ranking_readiness", "番茄上榜就绪", "overall"),
    ("identity_manifest_status", "人物身份清单", "cast"),
)

_PASS_WORDS = ("approved", "passed", "pass", "ok", "ready", "ready_to_write", "通过", "就绪")
_FAIL_WORDS = ("blocked", "failed", "fail", "rejected", "not_ready", "拦截", "未通过")


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return list(value)
    return []


def _coerce_score(report: Mapping[str, Any]) -> tuple[float | None, float | None]:
    """Return (raw_score, score_pct) — score_pct normalized to a 0–100 scale."""

    for field in ("score", "overall_score", "total", "win_rate"):
        raw = report.get(field)
        if isinstance(raw, (int, float)) and not isinstance(raw, bool):
            pct = float(raw) * 100 if 0 <= raw <= 1 else float(raw)
            return float(raw), round(pct, 1)
    return None, None


def _verdict_class(*, passed: bool | None, blocking_count: int, warning_count: int) -> str:
    if passed is False or blocking_count > 0:
        return "fail"
    if warning_count > 0:
        return "warn"
    if passed is True:
        return "ok"
    return "info"


def _normalize(key: str, label: str, scope: str, value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        low = value.strip().lower()
        passed: bool | None = (
            True if any(w in low for w in _PASS_WORDS)
            else (False if any(w in low for w in _FAIL_WORDS) else None)
        )
        return {
            "key": key, "label": label, "scope": scope, "kind": "status",
            "status": value.strip(), "passed": passed,
            "score": None, "score_pct": None,
            "warning_count": 0, "blocking_count": 0,
            "warnings": [], "blocking_findings": [],
            "capability_snapshot": None,
            "verdict_class": _verdict_class(passed=passed, blocking_count=0, warning_count=0),
        }

    report = value if isinstance(value, Mapping) else {}
    score, score_pct = _coerce_score(report)
    warnings = _as_list(report.get("warnings"))
    blocking = _as_list(report.get("blocking_findings")) or _as_list(report.get("blocking"))
    passed = report.get("passed")
    if not isinstance(passed, bool):
        passed = None
    status = (
        report.get("status")
        or report.get("verdict")
        or report.get("grade")
        or report.get("readiness_status")
    )
    snapshot = report.get("capability_snapshot")
    return {
        "key": key, "label": label, "scope": scope, "kind": "report",
        "status": str(status).strip() if status else None,
        "passed": passed,
        "score": score, "score_pct": score_pct,
        "warning_count": len(warnings), "blocking_count": len(blocking),
        "warnings": warnings[:20], "blocking_findings": blocking[:20],
        "capability_snapshot": dict(snapshot) if isinstance(snapshot, Mapping) else None,
        "verdict_class": _verdict_class(
            passed=passed, blocking_count=len(blocking), warning_count=len(warnings)
        ),
    }


def build_design_quality_reports(metadata: Mapping[str, Any] | None) -> dict[str, Any]:
    """Flatten present planning-quality signals into a frontend-ready payload.

    Absent reports are skipped, so a half-planned book renders a short list and
    an empty metadata dict yields a stable empty structure (no-op safe).
    """

    meta = metadata or {}
    reports: list[dict[str, Any]] = []
    for key, label, scope in _REPORT_SPECS:
        value = meta.get(key)
        if value in (None, "", {}, []):
            continue
        reports.append(_normalize(key, label, scope, value))

    prewrite = next((r for r in reports if r["key"] == "prewrite_readiness_report"), None)
    headline = None
    capability_snapshot = None
    if prewrite is not None:
        capability_snapshot = prewrite["capability_snapshot"]
        headline = {
            "score": prewrite["score"],
            "score_pct": prewrite["score_pct"],
            "passed": prewrite["passed"],
            "warning_count": prewrite["warning_count"],
            "blocking_count": prewrite["blocking_count"],
        }

    return {
        "schema_version": SCHEMA_VERSION,
        "reports": reports,
        "capability_snapshot": capability_snapshot,
        "headline": headline,
        "report_count": len(reports),
    }


__all__ = ["SCHEMA_VERSION", "build_design_quality_reports"]
