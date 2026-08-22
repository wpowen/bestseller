"""恢复一本被停止的书，必须先清掉那条停止指令。

2026-08-22 真机定罪：点 Stop 再点 Resume，书**立刻自杀并显示「已完成」**。
事件链是

    progressive_autowrite_started
    → autowrite_halted_by_operator
    → project_export_skipped
    → autowrite_completed        ← 0 章，却标成完成

机制：Stop 走 `_cancel_project_task_async` → `request_pause()`，把
`book_production_control.desired_state` 写成 `pause`。注释说得很清楚，
这是**刻意写在流水线覆盖不到的地方**，免得被跑动中的 pipeline 抹掉：

    Record the operator's intent where the pipeline cannot overwrite it.

而 Resume 只恢复了 web 侧的 task 卡片，**从没清过那条 pause**。于是新的
autowrite 一启动就读到操作者要求停止，halt 后按正常收尾流程标 completed。

`production_control.request_run()`（"Clear a halt"）早就实现了——
**全代码库零调用方**。恢复能力长在书不走的那条路上。
"""

from __future__ import annotations

# ruff: noqa: RUF002, RUF003 — 中文标点是刻意的。
import inspect

from bestseller.services import production_control
from bestseller.web import server


def test_request_run_exists_and_clears_the_halt() -> None:
    """前置条件：清除停止指令的函数本来就有。"""

    assert hasattr(production_control, "request_run")
    doc = inspect.getdoc(production_control.request_run) or ""
    assert "Clear a halt" in doc


def test_explicit_start_already_clears_it() -> None:
    """前置条件：显式启动那条路径本来就在清 halt——恢复只是漏了。"""

    source = inspect.getsource(server)
    anchor = source.index("create_autowrite_task(payload)")
    assert "_clear_production_halt_async" in source[max(0, anchor - 1200) : anchor]


def test_the_clearing_is_wired_next_to_the_resume_call() -> None:
    """清除必须发生在恢复那一段，不能只是文件里某处出现过这个词。"""

    source = inspect.getsource(server)
    # 锚在**调用点**上，不是函数定义——第一版抓到了 2725 行的 def，
    # 于是断言总在错误的窗口里找。
    anchor = source.index("task_manager.resume_autowrite_task(")
    window = source[max(0, anchor - 2000) : anchor]
    assert "_clear_production_halt_async" in window
