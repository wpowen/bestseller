"""跨窗事件台账重建（2026-08-18《九姓井口只认我》规划层定罪）。

病灶：consumed_event_entries 是 run 内变量——每个滚动窗口的 outline run
从空表开始，跨窗全部失忆。真机后果：窗2 在前 9 章已成稿的情况下把 ch1-3
事件原样重规划（ch10=ch1 低头交桶、ch11=ch3 浮字、ch12=ch3 高潮），卷高潮
兑付三次、出现两个「第一次」、时间线倒流。后半段劣化的生成机制=每开新窗
失忆一次。事实源一直在 DB（chapter_contracts.chapter_goal/hook_description）
没人读——「病只在作用域：把 run 内变量当了 book 级事实源」。

修：窗口起点 >1 时按台账同一格式从 DB 重建种子；fail-open。
"""

from __future__ import annotations

import inspect

import pytest

from bestseller.services import planner

pytestmark = pytest.mark.unit


def test_ledger_entry_format_accepts_contract_row_keys():
    # DB 行以 chapter_goal/hook_description 键喂入，必须产出与 run 内条目
    # 完全同格式的 line（prompt 注入两侧同构，才能被 R5 与判官同样消费）
    entries = planner._outline_consumed_event_entries(
        [
            {
                "chapter_number": 1,
                "chapter_goal": "陆沉当众低头交桶换半桶水",
                "hook_description": "井沿浮出半个爹字",
            }
        ]
    )
    assert len(entries) == 1
    assert entries[0]["line"].startswith("ch1 | 陆沉当众低头交桶")
    assert "井沿浮出半个爹字" in entries[0]["line"]


def test_batched_generator_rebuilds_ledger_from_db():
    src = inspect.getsource(planner._generate_volume_outline_batched)
    # 事实源=chapters 行（chapter_goal/hook_description 物化时抄写在章行；
    # chapter_contracts 没有这两列——2026-08-19 窗 2 真机 AttributeError 定罪）
    assert "ChapterModel.chapter_goal" in src, "必须从 chapters 事实源重建"
    assert "ChapterModel.hook_description" in src
    assert "_outline_consumed_event_entries(_seed_chapters)" in src, "种子必须走同一格式函数"
    # 窗1（从第1章起）不查库；fail-open 不阻断规划
    assert "int(_first_batch_start) > 1" in src
    assert "fail-open" in src
