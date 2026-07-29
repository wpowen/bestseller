"""建书按钮永远不能静默失效。

2026-07-28 我给「开始创作」加了一个空创意确认框：

    if (!_confirmEmptyConceptSeed()) return;

意图是好的（不填创意的书至今零成功，两轮淘汰赛的开销白花），但实现方式是错的：
用户点「取消」——或者浏览器屏蔽了弹框，此时 ``confirm()`` **自动返回 false**——
函数就一声不响地返回。不发请求、不报错、按钮状态都不变。

真机后果（2026-07-29）：用户建了一本 50 章的书，「没有创建成功」。排查发现
数据库里没有项目、没有工作流，任务管理器（会持久化到磁盘）也没有任何记录——
**请求从未到达后端**。用户看到的是「点了没反应」。

浏览器屏蔽弹框那种情况更糟：建书会**永久失效**，而且没有任何线索。

判据：警告可以给，但**主操作永远不能被静默拦住**。信息在点击之前就该出现在
界面上，而不是靠一个可能被吞掉的模态框。
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_HTML = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "bestseller"
    / "web"
    / "novel_quickstart.html"
).read_text(encoding="utf-8")


class TestSubmissionIsNeverAbortedSilently:
    def test_no_confirm_gate_on_the_create_button(self) -> None:
        idx = _HTML.index("async function startCreation()")
        body = _HTML[idx : idx + 1200]
        assert "confirm(" not in body, (
            "主操作不能依赖模态框——用户点取消或浏览器屏蔽弹框都会让建书静默失效"
        )

    def test_the_guard_helper_is_gone(self) -> None:
        assert "_confirmEmptyConceptSeed" not in _HTML

    def test_submission_still_happens_without_a_seed(self) -> None:
        """不填创意仍然可以建书——那是用户的选择，不是我们的否决权。"""

        idx = _HTML.index("async function startCreation()")
        body = _HTML[idx : idx + 1500]
        assert "'/api/tasks/quickstart'" in body


class TestTheWarningIsStillVisible:
    def test_the_label_states_the_measured_outcome(self) -> None:
        """代价信息要在点击**之前**就在界面上，而不是点击时才弹。"""

        assert "不填几乎必挂" in _HTML

    def test_the_hint_explains_the_cost(self) -> None:
        assert "两轮" in _HTML or "淘汰赛" in _HTML
