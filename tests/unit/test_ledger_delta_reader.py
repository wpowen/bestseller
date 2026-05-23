from __future__ import annotations

import pytest

from bestseller.services.ledger_delta_reader import (
    LedgerDeltaStaleError,
    read_ledger_delta,
)


def test_ledger_delta_reader_includes_previous_five_event_state_rows(tmp_path) -> None:
    story_bible = tmp_path / "story-bible"
    story_bible.mkdir()
    (story_bible / "event-state-ledger.md").write_text(
        "\n".join(
            [
                "| chapter | state |",
                "| --- | --- |",
                "| 46 | 旧案压力升级 |",
                "| 47 | 王守真欠下新债 |",
                "| 48 | 青囊线索转入镜局 |",
                "| 49 | 沈家旧卷被重读 |",
                "| 50 | 审讯室释放手续补齐 |",
            ]
        ),
        encoding="utf-8",
    )
    (story_bible / "clue-ledger.md").write_text(
        "| C-026 | 第 48 章 | 沈家旧卷 | 未解 |\n",
        encoding="utf-8",
    )

    block = read_ledger_delta(story_bible, chapter_no=51)

    assert {row.chapter_no for row in block.rows if row.kind == "event_state"} == {
        46,
        47,
        48,
        49,
        50,
    }
    assert "LedgerDelta" in block.render()
    assert "沈家旧卷" in block.render()


def test_ledger_delta_reader_blocks_when_event_state_window_is_stale(tmp_path) -> None:
    story_bible = tmp_path / "story-bible"
    story_bible.mkdir()
    (story_bible / "event-state-ledger.md").write_text(
        "| 46 | only one row |\n",
        encoding="utf-8",
    )

    with pytest.raises(LedgerDeltaStaleError, match="gate_error_ledger_stale"):
        read_ledger_delta(story_bible, chapter_no=51)
