"""Standalone runner for the quality-trend dashboard.

The orchestrator picks the milestone schedule (every 10 chapters, by
default) and calls :func:`build_dashboard_for_chapters`. This module
keeps the dashboard pure: it takes already-computed per-chapter
snapshots, runs :func:`evaluate_dashboard_window`, and writes the
rendered Markdown summary to disk.

By keeping persistence behind a small interface we let the
orchestrator decide where each window report should live without
this module knowing about the project layout.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Protocol

from bestseller.services.quality_levers.quality_trend_dashboard import (
    ChapterScoreSnapshot,
    DashboardWindow,
    evaluate_dashboard_window,
    load_quality_trend_dashboard,
    render_dashboard_summary,
)


class DashboardSink(Protocol):
    """Plugin point for writing a window report to wherever the project keeps audits."""

    def write(self, *, slug: str, window: DashboardWindow, summary: str) -> Path:
        ...


@dataclass(frozen=True)
class FilesystemDashboardSink:
    """Default sink — drops the Markdown report under ``output/<slug>/audits/dashboard/``."""

    base_path: Path = Path("output")

    def write(self, *, slug: str, window: DashboardWindow, summary: str) -> Path:
        target_dir = self.base_path / slug / "audits" / "dashboard"
        target_dir.mkdir(parents=True, exist_ok=True)
        filename = f"window-{window.start_chapter:03d}-{window.end_chapter:03d}.md"
        target_path = target_dir / filename
        target_path.write_text(summary, encoding="utf-8")
        return target_path


@dataclass(frozen=True)
class DashboardRunResult:
    window: DashboardWindow | None
    summary: str
    output_path: Path | None
    skipped: bool
    skip_reason: str = ""


def build_dashboard_for_chapters(
    snapshots: Iterable[ChapterScoreSnapshot],
    *,
    slug: str,
    sink: DashboardSink | None = None,
) -> DashboardRunResult:
    """Build + render + persist a dashboard window for the given snapshots.

    ``snapshots`` should already cover the full ``window_size`` (default
    10). The orchestrator is responsible for slicing the per-chapter
    audit ledger into windows before calling this function.

    Returns a :class:`DashboardRunResult` so callers know whether the
    window was emitted, where it was written, or why it was skipped.
    """

    items = list(snapshots)
    if not items:
        return DashboardRunResult(
            window=None,
            summary="",
            output_path=None,
            skipped=True,
            skip_reason="empty_snapshot_set",
        )
    window = evaluate_dashboard_window(items)
    if window is None:
        return DashboardRunResult(
            window=None,
            summary="",
            output_path=None,
            skipped=True,
            skip_reason="aggregator_returned_none",
        )
    summary = render_dashboard_summary(window)
    sink_impl = sink or FilesystemDashboardSink()
    output_path = sink_impl.write(slug=slug, window=window, summary=summary)
    return DashboardRunResult(
        window=window,
        summary=summary,
        output_path=output_path,
        skipped=False,
    )


def should_run_dashboard(chapter_number: int) -> bool:
    """Return whether ``chapter_number`` is a configured milestone boundary."""

    if chapter_number <= 0:
        return False
    window_size = load_quality_trend_dashboard().window_size or 10
    return chapter_number % window_size == 0
