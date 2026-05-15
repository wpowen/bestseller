"""Unit tests for ``quality_levers.dashboard_runner``."""

from __future__ import annotations

from pathlib import Path

import pytest

from bestseller.services.quality_levers.dashboard_runner import (
    DashboardSink,
    FilesystemDashboardSink,
    build_dashboard_for_chapters,
    should_run_dashboard,
)
from bestseller.services.quality_levers.quality_trend_dashboard import (
    ChapterScoreSnapshot,
    DashboardWindow,
)


pytestmark = pytest.mark.unit


class _RecordingSink:
    """Test sink that captures the rendered summary instead of writing to disk."""

    def __init__(self) -> None:
        self.records: list[tuple[str, DashboardWindow, str]] = []

    def write(self, *, slug: str, window: DashboardWindow, summary: str) -> Path:
        self.records.append((slug, window, summary))
        return Path(f"/fake/{slug}/window-{window.start_chapter}-{window.end_chapter}.md")


def _snapshot(chapter: int, *, peer_author_score: float = 0.80) -> ChapterScoreSnapshot:
    return ChapterScoreSnapshot(
        chapter_number=chapter,
        persona_scores={
            "platform_editor": 0.80,
            "new_reader": 0.78,
            "loyal_reader": 0.80,
            "peer_author": peer_author_score,
        },
        anti_pattern_hits=0,
        signature_types_present=("golden_line",),
    )


def test_build_dashboard_skips_when_no_snapshots() -> None:
    result = build_dashboard_for_chapters([], slug="qingya", sink=_RecordingSink())
    assert result.skipped is True
    assert result.skip_reason == "empty_snapshot_set"
    assert result.window is None


def test_build_dashboard_writes_to_recording_sink() -> None:
    sink = _RecordingSink()
    snapshots = [_snapshot(i + 1) for i in range(10)]
    result = build_dashboard_for_chapters(snapshots, slug="qingya", sink=sink)
    assert result.skipped is False
    assert result.window is not None
    assert result.window.start_chapter == 1
    assert result.window.end_chapter == 10
    assert "Quality Trend Window" in result.summary
    assert len(sink.records) == 1
    slug, window, summary = sink.records[0]
    assert slug == "qingya"
    assert window.end_chapter == 10
    assert summary == result.summary


def test_build_dashboard_red_alert_propagates_into_window() -> None:
    sink = _RecordingSink()
    # Drop peer_author scores well under the 0.70 red threshold.
    snapshots = [_snapshot(i + 1, peer_author_score=0.55) for i in range(10)]
    result = build_dashboard_for_chapters(snapshots, slug="qingya", sink=sink)
    assert result.window is not None
    severities = {alert.severity for alert in result.window.alerts}
    assert "red" in severities


def test_filesystem_dashboard_sink_writes_file(tmp_path: Path) -> None:
    sink = FilesystemDashboardSink(base_path=tmp_path)
    snapshots = [_snapshot(i + 1) for i in range(10)]
    result = build_dashboard_for_chapters(snapshots, slug="qingya", sink=sink)
    assert result.output_path is not None
    assert result.output_path.exists()
    content = result.output_path.read_text(encoding="utf-8")
    assert "Quality Trend Window" in content
    # Default base layout: output/<slug>/audits/dashboard/window-001-010.md
    assert result.output_path.name == "window-001-010.md"
    assert "qingya" in str(result.output_path)


def test_should_run_dashboard_respects_window_size() -> None:
    # Default window size from quality_trend_dashboard.yaml is 10
    assert should_run_dashboard(0) is False
    assert should_run_dashboard(5) is False
    assert should_run_dashboard(10) is True
    assert should_run_dashboard(11) is False
    assert should_run_dashboard(20) is True
    assert should_run_dashboard(100) is True


def test_should_run_dashboard_handles_negative_and_zero() -> None:
    assert should_run_dashboard(-1) is False
    assert should_run_dashboard(0) is False
