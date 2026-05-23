from __future__ import annotations

import csv
import importlib.util
from pathlib import Path


def _load_build_report():
    path = Path(__file__).resolve().parents[2] / "scripts/audit_qingnang_canon_pipeline_state.py"
    spec = importlib.util.spec_from_file_location("audit_qingnang_canon_pipeline_state", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.build_report


def test_canon_pipeline_state_flags_stale_story_bible(tmp_path):
    story_bible = tmp_path / "story-bible"
    story_bible.mkdir()
    (story_bible / "continuity-ledger.md").write_text(
        "\n".join(
            [
                "# Continuity Ledger",
                "| Chapter | Title | Word Count | Draft Version |",
                "|---:|---|---:|---:|",
            ]
        ),
        encoding="utf-8",
    )
    (story_bible / "event-state-ledger.md").write_text(
        "| 章末 | 事件/人物 | 当前状态 | 下一章只能怎么续 | 禁止回滚 |\n"
        "| --- | --- | --- | --- | --- |\n"
        "| 第 49 章 | 半数归人 | 未归者被吞回 | 接第50章清点 | 不得全救 |\n",
        encoding="utf-8",
    )
    (story_bible / "clue-ledger.md").write_text(
        "| ID | 线索 | 投放章节 | 表面解释 | 真正指向 | 回收计划 |\n"
        "| --- | --- | --- | --- | --- | --- |\n"
        "| C-025 | 林朝宗旧印 | 第31章 | 林家旧印 | 第一账 | 追老宅 |\n",
        encoding="utf-8",
    )
    with (story_bible / "volume-plan.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["volume", "start_chapter", "end_chapter", "status", "goal"])
        writer.writerow(["1", "1", "500", "revising", "broken"])
    with (story_bible / "batch-queue.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["batch", "start_chapter", "end_chapter", "required_callbacks", "status"])
        writer.writerow(["1", "1", "0", "promise", "empty"])

    report = _load_build_report()(story_bible)

    assert report["continuity_ledger_rows"] == 0
    assert report["event_state_last_chapter"] == 49
    assert report["clue_last_id"] == "C-025"
    assert report["volume_plan_valid"] is False
    assert report["passed"] is False
    assert "volume_plan_collapsed_to_full_book" in report["findings"]
