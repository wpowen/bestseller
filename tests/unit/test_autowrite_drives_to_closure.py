"""一次运行要把书带到终点，而不是修一轮就交给自愈。

``run_autowrite_pipeline`` 只调用一次 ``run_project_repair``。一轮修复通常不足以
让所有章节落定——真机上第一轮结束时常是 1/3 或 2/3 结算——于是这次运行就结束了，
书停在 ``revising``，要等自愈的下一次扫描才继续。

真机取证（2026-07-28，urban-power-reversal-1785201018）：自愈确实在驱动它
（11:02 / 11:06 / 11:10 三次都是 ``worker_self_heal`` 触发），书最终也完结了。
但这是**跨多次扫描、几十分钟**才走完的，而用户的要求是「项目启动之后，它应该
要自动启动并完成闭环的完整流程」。

所以在这一次运行里把修复驱动到收敛：反复修，直到书完结，或者一轮下来**结算数
不再增加**（无进展即停，避免变成无限循环）。自愈仍是兜底——进程被杀、Docker
崩溃这些它接得住——但正常情况下不该靠它才能完成。
"""

from __future__ import annotations

import inspect

import pytest

from bestseller.services import pipelines

pytestmark = pytest.mark.unit


class TestRepairIsDrivenToConvergence:
    def test_autowrite_repairs_in_a_loop(self) -> None:
        source = inspect.getsource(pipelines.run_autowrite_pipeline)
        assert "_drive_repair_to_closure" in source, (
            "一次运行必须把修复驱动到收敛，而不是修一轮就返回"
        )

    def test_the_driver_stops_when_the_book_closes(self) -> None:
        source = inspect.getsource(pipelines._drive_repair_to_closure)
        assert "is_complete" in source

    def test_the_driver_stops_when_a_round_makes_no_progress(self) -> None:
        """无进展即停——否则一本卡住的书会被永远重修。"""

        source = inspect.getsource(pipelines._drive_repair_to_closure)
        assert "settled_chapters" in source, "必须用结算数衡量进展"

    def test_the_driver_is_bounded(self) -> None:
        source = inspect.getsource(pipelines._drive_repair_to_closure)
        assert "max_rounds" in source, "轮数必须有上限，无进展判断之外还要有硬闸"


class TestConvergenceRule:
    """收敛判据本身可测，不藏在管线里。"""

    def test_more_settled_chapters_counts_as_progress(self) -> None:
        assert pipelines._repair_round_made_progress(previous=1, current=2)

    def test_same_settled_count_is_no_progress(self) -> None:
        assert not pipelines._repair_round_made_progress(previous=2, current=2)

    def test_fewer_settled_chapters_is_no_progress(self) -> None:
        """修复重开了已结算的章——这不是前进。"""

        assert not pipelines._repair_round_made_progress(previous=3, current=2)

    def test_first_round_from_zero_counts(self) -> None:
        assert pipelines._repair_round_made_progress(previous=0, current=1)
