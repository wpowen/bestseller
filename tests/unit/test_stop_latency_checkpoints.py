"""停止延迟——检查点粒度锁（2026-08-19 用户报「停止并删除总有延迟」）。

真机链路：用户点停止 → cancel 写 book_production_control 暂停位 → 在飞的
autowrite 只在**卷边界**读它。一卷 8-16 章、每章数分钟，于是停止要等到
下一卷才生效（几十分钟），用户体感=停不掉/延迟很久。

修：章循环每章开始前读同一个事实源（章边界是最细的安全切点——章内停会
留半截草稿）。fail-open 与卷边界同款：停止检查自身永不中断运行。

前端同修：「停止后删除」旧版发完停止就 return、要用户再点一次删除，
两段式被体感成延迟；现在一次点击=停止+轮询等待+自动删除。
"""

from __future__ import annotations

import inspect
import io
import re

import pytest

from bestseller.services import pipelines

pytestmark = pytest.mark.unit


def test_chapter_loop_has_operator_halt_checkpoint():
    src = inspect.getsource(pipelines.run_project_pipeline)
    assert "load_control_state" in src, "章循环必须自带停止检查点（卷边界太粗）"
    assert "chapter_loop_halted_by_operator" in src, "停在章边界必须发事件可观测"
    # 必须在真正写章之前检查，否则等于没提前
    halt_at = src.find("load_control_state")
    write_at = src.find("await run_chapter_pipeline")
    assert 0 < halt_at < write_at, "检查点必须在 run_chapter_pipeline 之前"


def test_chapter_halt_check_is_fail_open():
    src = inspect.getsource(pipelines.run_project_pipeline)
    window = src[src.find("load_control_state") - 400 : src.find("load_control_state") + 800]
    assert "except Exception" in window, "读控制位失败不得中断运行（fail-open）"


def test_volume_boundary_checkpoint_still_present():
    # 章级检查是新增的快路径，卷级权威检查点不许因此被删
    src = inspect.getsource(pipelines.run_progressive_autowrite_pipeline)
    assert "autowrite_halted_by_operator" in src


def test_frontend_stop_then_delete_is_single_click():
    html = io.open(
        "src/bestseller/web/novel_quickstart.html", encoding="utf-8"
    ).read()
    start = html.find("window.deleteProjectFull = async function")
    assert start != -1
    block = html[start : html.find("\n\t  };\n", start)]
    # 旧两段式话术不得回潮
    assert "再点击“完全删除”即可彻底删除" not in block
    # 新流程：停止后自动轮询删除
    assert "自动彻底删除" in block
    assert re.search(r"method:\s*'DELETE'", block), "必须在同一次操作里发删除"
    assert "while (Date.now() < deadlineMs)" in block, "必须轮询等待停稳"
    assert "setTimeout(r, 3000)" in block, "轮询间隔应远快于看板 15s 刷新"
