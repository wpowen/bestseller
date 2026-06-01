from __future__ import annotations

from pathlib import Path

import pytest

from bestseller.services.story_bible_write_gate import (
    STORY_BIBLE_INCOMPLETE,
    STORY_BIBLE_MISSING_FILE,
    evaluate_story_bible_write_readiness,
)

pytestmark = pytest.mark.unit


def _write_bible(root: Path, *, premise: str, world: str, chars: str, vol: str) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "premise.md").write_text(premise, encoding="utf-8")
    (root / "world.md").write_text(world, encoding="utf-8")
    (root / "characters.md").write_text(chars, encoding="utf-8")
    (root / "volume-plan.md").write_text(vol, encoding="utf-8")


def test_thin_bible_blocks(tmp_path: Path) -> None:
    root = tmp_path / "story-bible"
    _write_bible(
        root,
        premise="少年逆袭。",
        world="一个世界。",
        chars="- 主角",
        vol="第一卷。",
    )
    report = evaluate_story_bible_write_readiness(root)
    assert not report.passed
    assert STORY_BIBLE_INCOMPLETE in report.blocking_codes


def test_missing_files_block(tmp_path: Path) -> None:
    root = tmp_path / "story-bible"
    root.mkdir(parents=True)
    (root / "premise.md").write_text("x" * 300, encoding="utf-8")
    report = evaluate_story_bible_write_readiness(root)
    assert not report.passed
    assert STORY_BIBLE_MISSING_FILE in report.blocking_codes


def test_complete_bible_passes(tmp_path: Path) -> None:
    root = tmp_path / "story-bible"
    _write_bible(
        root,
        premise="前提设定" * 80,
        world="世界设定规则" * 80,
        chars="人物档案" * 80,
        vol="逐章大纲" * 80,
    )
    report = evaluate_story_bible_write_readiness(root)
    assert report.passed, report.findings
