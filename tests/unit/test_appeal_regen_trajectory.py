"""达标门的重生轨迹必须留痕：收敛还是原地打转，事后要能看出来。

2026-08-23 真机（验证书 8）：构思跑满有界重生仍未达标（简介 65.0 < 68），
整本书被拦下不进规划。而 `conception_log` 里**一条重生记录都没有**——15 条
日志覆盖了淘汰赛、回声门、脊柱门、契约门，唯独这道真正毙掉书的门没有轨迹。
于是无法回答最关键的问题：它是在稳步逼近 68（该加预算）还是每轮都在同一个
分数上打转（该换打法）。

同一教训在本项目出现过：2026-08-19「污染门毙书后什么档案都没留，撞了哪本
书只能从任务事件里人肉挖」。**能毙书的门必须留下它的判据轨迹。**
"""

from __future__ import annotations

# ruff: noqa: RUF002, RUF003 — 中文标点是刻意的。
from bestseller.services.conception import build_appeal_regen_trace


class TestTraceShape:
    def test_trace_records_every_attempt_with_scores(self) -> None:
        trace = build_appeal_regen_trace(
            [
                {"attempt": 0, "premise": 70.0, "blurb": 61.2, "title": 94.8,
                 "meets_bar": False, "persona_click_rate": 0.0},
                {"attempt": 1, "premise": 70.0, "blurb": 63.5, "title": 94.8,
                 "meets_bar": False, "persona_click_rate": 0.0},
                {"attempt": 2, "premise": 70.0, "blurb": 65.0, "title": 94.8,
                 "meets_bar": False, "persona_click_rate": 0.0},
            ],
            blurb_min=68.0,
        )
        assert trace["attempts"] == 3
        assert trace["blurb_first"] == 61.2
        assert trace["blurb_last"] == 65.0
        assert trace["blurb_best"] == 65.0
        assert trace["blurb_min"] == 68.0
        assert trace["gap_to_bar"] == 3.0
        # 三轮共涨 3.8 分且单调上升 —— 判为「在收敛」，该给预算而不是换打法。
        assert trace["verdict"] == "converging"
        assert len(trace["rounds"]) == 3

    def test_flat_trajectory_is_named_stuck(self) -> None:
        trace = build_appeal_regen_trace(
            [
                {"attempt": 0, "blurb": 65.0, "meets_bar": False},
                {"attempt": 1, "blurb": 65.1, "meets_bar": False},
                {"attempt": 2, "blurb": 64.9, "meets_bar": False},
            ],
            blurb_min=68.0,
        )
        # 总涨幅 <1 分 = 原地打转：加预算没用，得换打法。
        assert trace["verdict"] == "stuck"

    def test_passing_run_is_named_passed(self) -> None:
        trace = build_appeal_regen_trace(
            [
                {"attempt": 0, "blurb": 61.0, "meets_bar": False},
                {"attempt": 1, "blurb": 70.0, "meets_bar": True},
            ],
            blurb_min=68.0,
        )
        assert trace["verdict"] == "passed"

    def test_empty_history_is_safe(self) -> None:
        trace = build_appeal_regen_trace([], blurb_min=68.0)
        assert trace["attempts"] == 0
        assert trace["verdict"] == "no_regen"
        assert trace["rounds"] == []


class TestWiring:
    def test_conception_records_the_trace_into_the_log(self) -> None:
        """接线钉：轨迹必须真的写进 conception_log，而不只是存在一个函数。"""

        import inspect

        from bestseller.services import conception

        src = inspect.getsource(conception.run_conception_pipeline)
        assert "build_appeal_regen_trace(" in src
        assert "appeal_regen_gate" in src


class TestBlockedMessageReportsRealThresholds:
    """拦截文案必须说出真实阈值，且指认真正没过的那一项。

    2026-08-23 真机：文案硬编码「未达【榜单达标线 80 分】」，而简介达标线
    是 68、80 是书名的线；当时简介 65.0（真的没过）、书名 94.8（过了），
    文案却把人引向书名。报错说错阈值 = 把排查引向错误的现场。
    """

    def test_message_is_not_hardcoded_to_eighty(self) -> None:
        import inspect
        import re

        from bestseller.web import server

        src = inspect.getsource(server)
        assert "榜单达标线 80 分" not in src
        # 阈值必须来自配置
        window = src[src.find("未达榜单达标线") - 2000 : src.find("未达榜单达标线") + 500]
        assert "load_story_appeal_config" in window
        assert re.search(r"blurb_min", window)
