"""淘汰赛干涸/冠军被撤销时，下游必须仍然只有一个故事。

真机 custom-xuanhuan-1787738259（用户勾：异世大陆/男频/50章/轻松+喜剧+爽点/纯爽）
出厂时**一本书里有两个主角、两个故事**：

  story_spine.who / logline  → 顾澜：焱城灶头接手人，师父替剑修挡刀，豁耳铁锅
  premise / synopsis         → 纪潮：穿越顶替被全族除名的废物少爷，残识画

顾澜 85 次、纪潮 21 次。我加的身份门按「规范名缺席 3/3 份构思正文」判为分裂
并拦下（blocks_production=true），书进 needs_replan——检出是对的，但损失是
一小时白跑。

分岔点在 conception_log 里一目了然：

  r-1  concept_tournament   顾澜（淘汰赛冠军的厨子故事）
  r1   character_architect  纪潮（另起炉灶）
  r1+  之后全部环节          纪潮

两处结构缺陷：

1. **兜底路径的不对称**。正常冠军路径同时写 ``ctx["description"]`` 与
   ``ctx["high_concept"]``；``_default_family_fallback_winner`` 兜底路径**只写
   high_concept**。而下游第一轮的角色架构师读的正是 ``description``，并且被
   prompt 明确要求「为主角取一个自然、好记的中文名」——拿不到冠军就必然
   重新发明一个主角。

2. **回执写在判定之前**。淘汰赛的 conception_log 条目在本体检查／默认母题检查
   **之前** append，所以被撤销的冠军在回执里和通过的长得一模一样。排查时据此
   误以为「attempt 1 成功、attempt 2 无故覆盖」，绕了一大圈才发现真相。
"""

from __future__ import annotations

import inspect

import pytest

from bestseller.services import conception

pytestmark = pytest.mark.unit


class TestFallbackPathAnchorsDownstream:
    def test_the_fallback_branch_also_writes_description(self):
        """只写 high_concept 等于让下游重新发明主角。"""
        src = inspect.getsource(conception)
        anchor = "_ct_result.winner = _default_family_fallback_winner"
        assert anchor in src
        branch = src[src.index(anchor) : src.index(anchor) + 1600]
        assert 'ctx["description"] =' in branch, (
            "兜底路径必须与正常冠军路径一样写 description——"
            "角色架构师读的是它，不是 high_concept"
        )
        assert 'ctx["high_concept"] =' in branch

    def test_the_fallback_block_is_sanitized_like_the_main_path(self):
        """新增文本通道不得成为默认母题的豁免通道（P1-1 同款教训）。"""
        src = inspect.getsource(conception)
        anchor = "_ct_result.winner = _default_family_fallback_winner"
        branch = src[src.index(anchor) : src.index(anchor) + 1600]
        assert "_sanitize_forbidden_default_motifs(" in branch

    def test_vacuity_the_main_path_has_always_written_both(self):
        """空转检验：正常路径两个都写——不对称确实只在兜底那条。"""
        src = inspect.getsource(conception)
        main = src[src.index('ctx["description"] = f"{ctx.get(\'description\') or \'\'}') :][:400]
        assert 'ctx["high_concept"]' in main


class TestRejectionIsRecorded:
    def test_both_rejection_paths_append_a_receipt(self):
        """撤销必须留痕，否则回执把被拒冠军显示成通过的冠军。"""
        src = inspect.getsource(conception)
        assert src.count('"agent": "concept_tournament_winner_rejected"') == 2, (
            "本体污染与默认母题污染两条撤销路径都必须补记回执"
        )

    def test_the_receipt_names_the_reason(self):
        src = inspect.getsource(conception)
        assert '"reason": "genre_ontology_violation"' in src
        assert '"reason": "default_motif_pollution"' in src

    def test_vacuity_the_tournament_receipt_is_still_written_before_the_checks(self):
        """空转检验：主回执确实早于两处检查——所以才需要补记这一条。

        （不改主回执的位置：它记录的是淘汰赛本身的产物，检查是之后的独立工序。）
        """
        src = inspect.getsource(conception)
        receipt = src.index('"agent": "concept_tournament",')
        ontology = src.index('"reason": "genre_ontology_violation"')
        assert receipt < ontology
