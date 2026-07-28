"""空创意种子要在花钱之前拦一下，不是花完之后解释。

「故事创意」字段标着「可选，强烈推荐」。按实测这话不成立：**没有种子的建书
至今零成功**。2026-07-26 起连续多本空题材书全部死在构思，最近一次
（07-28 13:49《玄幻》）四个候选在 大白话 4/4、想点欲 4/4、故事运动 4/4 上全挂
——不是新颖度不够，是从一个空题材出发根本生成不出站得住的概念。

系统已经做了部分快速失败（在市场/角色/世界观生成之前就停），但仍然**跑满两轮
淘汰赛**才告诉用户。那两轮是真金白银的 LLM 调用，而这条路径的历史成功率是 0。

所以在提交前把这个事实摆出来，并保留用户执意继续的权利——它是提示，不是禁令。
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


class TestEmptySeedIsFlaggedBeforeSubmit:
    def test_submit_checks_for_an_empty_seed(self) -> None:
        assert "_confirmEmptyConceptSeed" in _HTML, (
            "提交前必须检查空种子——事后解释花的是用户的 token"
        )

    def test_the_check_runs_before_the_request_is_sent(self) -> None:
        """只看**建书**路径。

        HTML 里另有两处 POST /api/tasks/quickstart，都是续传（带 project_slug
        复活已有书），与创意种子无关，不该被这条断言波及。
        """

        start = _HTML.index("async function startCreation()")
        body = _HTML[start : start + 3000]
        guard = body.index("_confirmEmptyConceptSeed")
        post = body.index("'/api/tasks/quickstart'")
        assert guard < post, "确认必须发生在发出请求之前"

    def test_resume_paths_are_not_interrupted(self) -> None:
        """续传一本已有的书不该被问「你没填创意」。"""

        resume = _HTML.index("window.resumeProjectBySlug")
        body = _HTML[resume : resume + 1200]
        assert "_confirmEmptyConceptSeed" not in body

    def test_the_warning_states_the_actual_outcome(self) -> None:
        """含糊的「建议填写」正是现状，没用。要给出后果。"""

        assert "两轮" in _HTML or "淘汰赛" in _HTML


class TestTheUserCanStillProceed:
    def test_it_is_a_confirm_not_a_block(self) -> None:
        """这是提示不是禁令——用户有权坚持。"""

        idx = _HTML.index("function _confirmEmptyConceptSeed")
        body = _HTML[idx : idx + 900]
        assert "confirm(" in body, "必须是可确认继续的对话框，不是硬拦截"

    def test_a_filled_seed_is_never_interrupted(self) -> None:
        idx = _HTML.index("function _confirmEmptyConceptSeed")
        body = _HTML[idx : idx + 900]
        assert "return true" in body, "填了种子必须直接放行，不打扰"


class TestTheLabelTellsTheTruth:
    def test_the_field_no_longer_calls_itself_merely_optional(self) -> None:
        """「可选，强烈推荐」与「零成功」不符。"""

        assert "故事创意（可选，强烈推荐）" not in _HTML
