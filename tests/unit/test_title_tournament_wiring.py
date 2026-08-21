"""书名淘汰赛必须接在建书主链路上（2026-08-21）。

新建的能力如果不接到书真正走的那条路，就是本项目今天反复定罪的那个形状——
真机上那套 65 候选的 `build_platform_title_workflow` 就是这么废掉的：
它挂在**上架资料导出**路径（`write_platform_title_workflow_artifacts`）上，
建书时根本不走，于是书名来自单次 `title_platform_revision` 调用。

本套测试盯住接线本身，不测 LLM 行为。
"""

from __future__ import annotations

import inspect

import pytest

from bestseller.services import conception

pytestmark = pytest.mark.unit


def test_tournament_is_called_from_the_conception_pipeline():
    src = inspect.getsource(conception.run_conception_pipeline)
    assert "_run_title_tournament(" in src, "淘汰赛必须接在建书主链路上"


def test_tournament_failure_never_blocks_book_creation():
    """书名工序不该有能力阻断建书。"""
    src = inspect.getsource(conception.run_conception_pipeline)
    idx = src.index("_run_title_tournament(")
    tail = src[idx : idx + 1600]
    assert "except Exception:" in tail
    assert "Title tournament failed" in tail


def test_incumbent_competes_rather_than_being_replaced():
    src = inspect.getsource(conception._run_title_tournament)
    assert "incumbent" in src
    assert 'family="现任"' in src, "现任书名要进场比，不是被无条件替换"


def test_receipt_is_persisted_for_later_forensics():
    """⚠️ 这条断言最初写成「源码里出现 "title_tournament" 字符串」并且**绿着**，
    而回执其实正在被 pydantic 的 extra=ignore 吃掉（2026-08-22 真机首跑实测）。
    断言「代码里写了这一行」不等于断言「这一行的结果活下来了」。
    改成盯真正落库的那条路：ConceptionResult 一等字段。
    """

    import dataclasses

    names = {f.name for f in dataclasses.fields(conception.ConceptionResult)}
    assert "title_tournament" in names
    src = inspect.getsource(conception.run_conception_pipeline)
    assert "title_tournament=dict(_title_tournament_receipt" in src


def test_llm_runs_are_recorded():
    src = inspect.getsource(conception.run_conception_pipeline)
    assert "llm_run_ids.extend(_tt_run_ids)" in src


def test_json_parse_failure_is_swallowed_not_raised():
    src = inspect.getsource(conception._safe_extract_json)
    assert "except Exception:" in src and "return {}" in src


def test_english_books_skip_the_chinese_pattern_families():
    src = inspect.getsource(conception._run_title_tournament)
    assert "if is_en:" in src


def test_judge_still_cannot_veto_in_the_wired_path():
    """接线之后判官依然只挣排序权——否决只能来自确定性门。"""
    src = inspect.getsource(conception._run_title_tournament)
    assert "deterministic_title_defects" in src
    assert "apply_arena_verdict" in src
    # 判官结果只喂给 apply_arena_verdict，不参与 rejected_by
    assert "rejected_by = deterministic_title_defects" in src
