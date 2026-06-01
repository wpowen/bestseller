from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from bestseller.services.mode_b_bridge import (
    ModeBChapterOutcome,
    enqueue_repair_item,
    resolve_mode_b_root,
    sync_progress_yaml,
)

pytestmark = pytest.mark.unit


def _outcome(passed: bool) -> ModeBChapterOutcome:
    return ModeBChapterOutcome(
        chapter_number=7,
        passed=passed,
        requires_human_review=not passed,
        word_count=4200 if passed else 530,
        verdict="pass" if passed else "rewrite",
        block_codes=() if passed else ("WORD_COUNT_METADATA_MISMATCH",),
        output_path="/tmp/ch-007.md",
        next_state="COMMIT_CHAPTER" if passed else "REWRITE_CHAPTER",
    )


def test_resolve_mode_b_root() -> None:
    root = resolve_mode_b_root("fen-xin-jue", output_base_dir="output")
    assert root == Path("output/ai-generated/fen-xin-jue")


def test_sync_progress_writes_truth_on_pass(tmp_path: Path) -> None:
    root = tmp_path / "ai-generated" / "fen-xin-jue"
    root.mkdir(parents=True)
    (root / "progress.yaml").write_text(
        yaml.safe_dump(
            {"project_slug": "fen-xin-jue", "state": "WRITE_CHAPTER", "chapters": {}},
            allow_unicode=True,
        ),
        encoding="utf-8",
    )

    path = sync_progress_yaml(
        "fen-xin-jue", _outcome(True), output_base_dir=str(tmp_path)
    )
    assert path is not None
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert data["state"] == "COMMIT_CHAPTER"
    assert data["chapters"]["007"]["state"] == "committed"
    assert data["chapters"]["007"]["word_count"] == 4200


def test_sync_progress_routes_to_rewrite_on_fail(tmp_path: Path) -> None:
    root = tmp_path / "ai-generated" / "fen-xin-jue"
    root.mkdir(parents=True)
    (root / "progress.yaml").write_text(
        yaml.safe_dump({"chapters": {}}, allow_unicode=True), encoding="utf-8"
    )

    path = sync_progress_yaml(
        "fen-xin-jue", _outcome(False), output_base_dir=str(tmp_path)
    )
    assert path is not None
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert data["state"] == "REWRITE_CHAPTER"
    assert data["chapters"]["007"]["state"] == "rewriting"
    assert "WORD_COUNT_METADATA_MISMATCH" in data["chapters"]["007"]["block_codes"]


def test_sync_progress_noop_when_missing(tmp_path: Path) -> None:
    assert (
        sync_progress_yaml(
            "ghost", _outcome(True), output_base_dir=str(tmp_path)
        )
        is None
    )


def test_enqueue_repair_item_blocks_and_appends(tmp_path: Path) -> None:
    root = tmp_path / "ai-generated" / "fen-xin-jue"
    root.mkdir(parents=True)
    (root / "progress.yaml").write_text(
        yaml.safe_dump({"state": "ADVANCE_CHAPTER", "repair_queue": []}),
        encoding="utf-8",
    )
    path = enqueue_repair_item(
        "fen-xin-jue",
        affected_chapter=20,
        issue_type="consistency_audit",
        description="canon conflict at ch20",
        output_base_dir=str(tmp_path),
    )
    assert path is not None
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert data["state"] == "DRAIN_REPAIR_QUEUE"
    assert len(data["repair_queue"]) == 1
    assert data["repair_queue"][0]["affected_chapter"] == 20
    assert data["repair_queue"][0]["status"] == "pending"
