"""停止信号在修复循环里也必须逐章生效，不能只在写作循环里生效。

2026-08-19 用户报「停止有延迟」，当时的修复是在写作循环的**章边界**加检查点
（`pipelines.py` 的 chapter loop，注释里写得很清楚）。但 `project_repair` 的
逐章循环漏了 —— 又是本项目的老毛病「豁免只改一处」。

实测（2026-08-24）：发出 `request_stop` 后 `project_repair` 仍从第 21 章一路
走到第 23 章；26 章的书要把每一章都修完才会停。停止只在**任务开始时**被
`_skip_halted_project_if_needed` 检查一次，已经在飞的那一趟不认账。

补上同款检查：同一个事实源、同一种 fail-open（读不到控制状态绝不中断本次运行）。

⚠️ **这些测试断言的是接线位置，不是行为**——它们能抓住「检查被挪到开工之后」
或「break 变成 continue」这类回归，但**不执行那段代码**，所以不能单独作为
「停止真的生效」的证据（本项目已因源码字符串断言吃过假绿）。行为证据在真机：
部署后在修复进行中发出停止，观察它是否在一章之内停下。
"""

from __future__ import annotations

# ruff: noqa: RUF001, RUF002, RUF003 — 中文标点是刻意的。
import inspect

import pytest

from bestseller.services import repair as repair_services

pytestmark = pytest.mark.unit


def _repair_source() -> str:
    return inspect.getsource(repair_services.run_project_repair)


def test_the_repair_chapter_loop_reads_the_same_control_state() -> None:
    source = _repair_source()
    loop = source
    assert "load_control_state" in loop
    assert "_repair_control" in loop


def test_the_check_sits_before_the_chapter_actually_runs() -> None:
    """检查必须排在这一章开工之前，否则等于没停。"""

    source = _repair_source()
    check = source.index("_repair_control = await load_control_state")
    started = source.index('"project_repair_chapter_started"')
    assert check < started, "停止检查排在了这一章开工之后"


def test_reading_the_control_state_can_never_abort_the_run() -> None:
    """fail-open：读不到控制状态时继续跑，与写作循环同款。"""

    source = _repair_source()
    block = source[source.index("_repair_control = await load_control_state") :]
    assert "except Exception:" in block[:400]
    assert "_repair_control = None" in block[:900]


def test_it_breaks_out_rather_than_skipping_one_chapter() -> None:
    """停止要退出整个循环，不是跳过一章继续走下一章。"""

    source = _repair_source()
    tail = source[source.index("_repair_control.halted") :]
    assert "break" in tail[: tail.index("project_repair_chapter_started")]
