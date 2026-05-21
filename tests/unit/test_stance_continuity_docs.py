"""Unit tests for stance_continuity_docs (file-based stance reversal check)."""

from __future__ import annotations

from pathlib import Path

import pytest

from bestseller.services.stance_continuity_docs import (
    build_stance_reversal_repair_prompt,
    detect_stance_reversals,
)

pytestmark = pytest.mark.unit


def _write_snapshot(dir_: Path, ch: int, body: str) -> None:
    dir_.mkdir(parents=True, exist_ok=True)
    (dir_ / f"after-ch-{ch:03d}.md").write_text(body, encoding="utf-8")


def test_no_snapshots_returns_empty(tmp_path: Path) -> None:
    assert detect_stance_reversals(tmp_path) == []


def test_clean_progression_yields_no_findings(tmp_path: Path) -> None:
    snap_dir = tmp_path / "knowledge" / "character-snapshots"
    _write_snapshot(snap_dir, 1, """
## 苏瑶

- stance_toward_宁尘: enemy
""")
    _write_snapshot(snap_dir, 2, """
## 苏瑶

- stance_toward_宁尘: enemy
""")
    assert detect_stance_reversals(tmp_path) == []


def test_unjustified_enemy_to_ally_flip_is_flagged(tmp_path: Path) -> None:
    snap_dir = tmp_path / "knowledge" / "character-snapshots"
    _write_snapshot(snap_dir, 1, """
## 苏瑶

- stance_toward_宁尘: enemy
""")
    _write_snapshot(snap_dir, 3, """
## 苏瑶

- stance_toward_宁尘: ally
""")
    # No timeline.md exists -> no triggers -> violation severity
    findings = detect_stance_reversals(tmp_path)
    assert len(findings) == 1
    f = findings[0]
    assert f.severity == "violation"
    assert f.transition.character_a == "苏瑶"
    assert f.transition.character_b == "宁尘"
    assert f.transition.prev_stance == "hostile"
    assert f.transition.curr_stance == "friendly"


def test_reversal_with_timeline_trigger_is_downgraded_to_warning(tmp_path: Path) -> None:
    snap_dir = tmp_path / "knowledge" / "character-snapshots"
    _write_snapshot(snap_dir, 1, """
## 苏瑶

- stance_toward_宁尘: enemy
""")
    _write_snapshot(snap_dir, 3, """
## 苏瑶

- stance_toward_宁尘: ally
""")
    timeline = tmp_path / "knowledge" / "timeline.md"
    timeline.write_text(
        """
## Chapter 2

- event_type: reconciliation
  story_time_label: 春末
  consequences: [苏瑶被宁尘救下]
""",
        encoding="utf-8",
    )
    findings = detect_stance_reversals(tmp_path)
    assert len(findings) == 1
    assert findings[0].severity == "warning"
    assert "ch2:reconciliation" in findings[0].found_triggers


def test_trust_map_numeric_reversal_detected(tmp_path: Path) -> None:
    snap_dir = tmp_path / "knowledge" / "character-snapshots"
    _write_snapshot(snap_dir, 1, """
## 苏瑶

- trust_map_宁尘: -0.7
""")
    _write_snapshot(snap_dir, 3, """
## 苏瑶

- trust_map_宁尘: 0.6
""")
    findings = detect_stance_reversals(tmp_path)
    assert len(findings) == 1
    assert findings[0].transition.prev_stance == "hostile"
    assert findings[0].transition.curr_stance == "friendly"


def test_repair_prompt_only_includes_violations(tmp_path: Path) -> None:
    snap_dir = tmp_path / "knowledge" / "character-snapshots"
    _write_snapshot(snap_dir, 1, """
## 苏瑶

- stance_toward_宁尘: enemy
""")
    _write_snapshot(snap_dir, 3, """
## 苏瑶

- stance_toward_宁尘: ally
""")
    findings = detect_stance_reversals(tmp_path)
    prompt = build_stance_reversal_repair_prompt(findings)
    assert "角色立场逆转修复" in prompt
    assert "苏瑶" in prompt
    assert "宁尘" in prompt
