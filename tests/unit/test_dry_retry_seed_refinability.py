"""同源补强只能修执行层的轴，不能修点子本身。

重试轮拿上一轮的最优近失候选当种子，并附一条指令：

    【定向补强】…保留其故事身份，只修被判不达标的轴，不要另起炉灶。

这条指令是**无条件**加的。当不达标的轴就是「新颖度」或「可预测性」时它自相
矛盾——那两轴度量的正是故事身份本身，保留身份就等于保留问题。

真机取证（2026-07-26，「东方玄幻」空题材建书）：两轮淘汰赛产出 6 个候选，
**全部**是同一个故事（杂役掏沟挖出戴木镯的腕骨＝失踪师姐遗骸），6/6 挂在
新颖度上。原因是 ``_best_dry_tournament_seed`` 按「挂的轴越少越优先」排序，
于是「只挂新颖度」的候选是**最优先**被选中的种子——而那恰恰是改良修不好的
那一个。第 2 轮在结构上就不可能通过。

修法：改良友好的轴（大白话／机制因果／人物决策／故事运动）才配当
种子；一旦挂了新颖度或可预测性，返回空串，让重试轮自由重掷（那条路径已经存在，
且 retry_feedback 仍会把失败候选与理由喂给它）。
"""

from __future__ import annotations

from types import SimpleNamespace as _NS

import pytest

from bestseller.services.conception import _best_dry_tournament_seed

pytestmark = pytest.mark.unit


def _cand(concept: str, rejected: str, **scores):
    base = {
        "judge_freshness": 7.0,
        "judge_click": 7.0,
        "judge_character_logic": 7.0,
        "judge_mechanism_causality": 7.0,
        "judge_genre_fidelity": 7.0,
        "judge_plain_language": 7.0,
        "judge_story_motion": 7.0,
    }
    base.update(scores)
    return _NS(concept=concept, rejected_reason=rejected, **base)


class TestRefinementResistantAxesDisqualify:
    def test_novelty_failure_is_not_seedable(self) -> None:
        """THE field case: 只挂新颖度的候选曾是最优先种子。"""

        candidate = _cand("掏沟杂役挖出师姐腕骨", "钩子硬门失败: 新颖度")
        assert _best_dry_tournament_seed([candidate]) == ""

    def test_predictability_failure_is_not_seedable(self) -> None:
        candidate = _cand("掏沟杂役挖出师姐腕骨", "钩子硬门失败: 可预测性")
        assert _best_dry_tournament_seed([candidate]) == ""

    def test_mixed_failure_containing_novelty_is_not_seedable(self) -> None:
        candidate = _cand("同上", "钩子硬门失败: 新颖度/大白话")
        assert _best_dry_tournament_seed([candidate]) == ""

    def test_genre_fidelity_failure_is_not_seedable(self) -> None:
        """故事身份已偏离所选题材时，保留身份补强只会锁死错误方向。"""

        candidate = _cand("仙侠里替前任代写情书", "钩子硬门失败: 题材保真/想点欲")
        assert _best_dry_tournament_seed([candidate]) == ""

    def test_the_live_six_candidate_dry_run_yields_no_seed(self) -> None:
        """真机六候选全部挂新颖度 → 不该产生任何种子。"""

        candidates = [
            _cand("候选1", "钩子硬门失败: 新颖度/想点欲/题材保真/故事运动"),
            _cand("候选2", "钩子硬门失败: 新颖度/想点欲/大白话/可预测性"),
            _cand("候选3", "钩子硬门失败: 新颖度/想点欲/可预测性"),
            _cand("候选4", "钩子硬门失败: 新颖度/想点欲/可预测性"),
            _cand("候选5", "钩子硬门失败: 新颖度"),
            _cand("候选6", "钩子硬门失败: 新颖度"),
        ]
        assert _best_dry_tournament_seed(candidates) == ""


class TestExecutionAxesStillSeed:
    """改良能修的轴必须继续走同源补强——这是它存在的理由。"""

    def test_plain_language_failure_is_seedable(self) -> None:
        candidate = _cand("设定绕但点子新", "钩子硬门失败: 大白话")
        assert _best_dry_tournament_seed([candidate]) == "设定绕但点子新"

    def test_execution_axis_combination_is_seedable(self) -> None:
        candidate = _cand("可修候选", "钩子硬门失败: 大白话/人物决策/机制因果")
        assert _best_dry_tournament_seed([candidate]) == "可修候选"

    def test_seedable_candidate_wins_over_a_novelty_failed_one(self) -> None:
        """即使新颖度候选挂的轴更少，它也不该赢——改良修不了它。"""

        novelty_only = _cand("点子旧", "钩子硬门失败: 新颖度", judge_click=9.5)
        fixable = _cand("点子新但写糊了", "钩子硬门失败: 大白话/故事运动")
        assert _best_dry_tournament_seed([novelty_only, fixable]) == "点子新但写糊了"


class TestExistingGuaranteesPreserved:
    def test_near_miss_is_promoted_regardless_of_its_vocabulary(self) -> None:
        """2026-08-02: a near-miss is refined on its merits, not its words."""
        candidate = _cand(
            "守墓人用账本追查尸体债务",
            "钩子硬门失败: 大白话",
        )
        assert _best_dry_tournament_seed([candidate]) == "守墓人用账本追查尸体债务"

    def test_explicit_user_theme_can_still_refine_same_theme(self) -> None:
        intentional = _cand(
            "守墓人用账本追查尸体债务",
            "钩子硬门失败: 大白话",
        )
        assert _best_dry_tournament_seed(
            [intentional], allow_debt=True, allow_death=True
        ) == "守墓人用账本追查尸体债务"

    def test_deterministic_ko_still_never_resurrected(self) -> None:
        cliche = _cand("废脉其实是宝脉", "俗套KO: 废脉觉醒是隐藏宝脉")
        assert _best_dry_tournament_seed([cliche]) == ""

    def test_four_plus_axis_failure_is_still_not_a_near_miss(self) -> None:
        weak = _cand("弱", "钩子硬门失败: 大白话/题材保真/机制因果/人物决策")
        assert _best_dry_tournament_seed([weak]) == ""

    def test_fewer_failed_axes_still_wins_among_seedable_candidates(self) -> None:
        one_axis = _cand("一轴", "钩子硬门失败: 大白话")
        three_axis = _cand("三轴", "钩子硬门失败: 大白话/人物决策/故事运动")
        assert _best_dry_tournament_seed([three_axis, one_axis]) == "一轴"

    def test_higher_scores_break_ties_among_seedable_candidates(self) -> None:
        low = _cand("低分", "钩子硬门失败: 大白话", judge_click=6.0)
        high = _cand("高分", "钩子硬门失败: 故事运动", judge_click=9.0)
        assert _best_dry_tournament_seed([low, high]) == "高分"
