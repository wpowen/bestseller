"""用户勾的每一项都没被核对过（2026-08-26 真机 custom-xuanhuan-1787662679）。

用户在建书页勾的是：玄幻／男频／**轻松**基调／**喜剧＋爽点满足**／**纯爽无代价**。
真机契约里这些值确实都在：

    tone_preference = 'light'
    explicit_enhancers.effect_skills = ['comedy_engine', 'hype_satisfaction_engine']
    explicit_enhancers.cost_style    = 'minimal'

而意图对表门读的是**另一组字段**——``user_tags / tags / default_tags``。
用分类选择器建书时这三个**恒为空**（真机实测全是 ``[]``）。于是：

    _intent_tags = []
    if _intent_tags:            # ← 197 行「核对→定罪→修复→复核」整段跳过
        ...
        build_intent_alignment_messages(..., cost_style=_ia_cost_style)

那段 if **没有 else**，也不写回执——真机 metadata 里一条 ``intent_*`` 记录都没有，
所以「跑了没发现」和「压根没跑」无法区分，这个洞才能埋这么久。

连代价档也一起被关在里面，尽管它的判据 docstring 自己写着
「独立检查项，与上面的意图判定分开做」。

修法：勾选项翻成可核对的中文意图项并入判定清单；代价档独立放行；回执恒写。
勾选项**只进判定**，不补进 ``tags`` 元数据——它们不是 taxonomy 公民，
硬塞会污染跨书标签统计（2026-08-01 裁决）。
"""

from __future__ import annotations

import pytest

from bestseller.services.intent_alignment import (
    build_intent_alignment_messages,
    intent_tags_from_contract,
    user_pick_intent_items,
    verifiable_intent_items,
)

pytestmark = pytest.mark.unit

# 真机契约（custom-xuanhuan-1787662679），逐字
_REAL = {
    "genre_intent": {
        "user_tags": [],
        "tags": [],
        "default_tags": [],
        "tone_preference": "light",
        "explicit_enhancers": {
            "cost_style": "minimal",
            "effect_skills": ["comedy_engine", "hype_satisfaction_engine"],
        },
    }
}


class TestTheRealBook:
    def test_vacuity_the_old_field_group_really_was_empty(self):
        """空转检验：确认旧判据在真机契约上确实拿到空列表。"""
        assert intent_tags_from_contract(_REAL) == []

    def test_the_picks_now_become_verifiable_items(self):
        assert verifiable_intent_items(_REAL) == ["轻松基调", "喜剧", "爽点满足"]

    def test_the_judge_prompt_now_carries_them(self):
        _system, user = build_intent_alignment_messages(
            intent_tags=verifiable_intent_items(_REAL),
            genre_label="玄幻",
            premise="青莲宗外门炒菜工陈韭，修为停滞三年。",
            synopsis="【废柴逆袭+玄幻美食】",
            spine={},
            cost_style="minimal",
        )
        for item in ("轻松基调", "喜剧", "爽点满足"):
            assert item in user, item
        assert "代价档判定" in user, "纯爽档必须同时走独立的代价检查"


class TestMergeSemantics:
    def test_taxonomy_tags_and_picks_are_merged_not_replaced(self):
        contract = {
            "genre_intent": {
                "user_tags": ["穿越", "打脸"],
                "tone_preference": "dark",
                "explicit_enhancers": {"effect_skills": ["twist_reversal_engine"]},
            }
        }
        assert verifiable_intent_items(contract) == ["穿越", "打脸", "暗黑基调", "反转"]

    def test_taxonomy_backfill_still_sees_only_taxonomy_tags(self):
        """勾选项不得混进 tags 元数据——它们不是 taxonomy 公民。"""
        assert intent_tags_from_contract(_REAL) == []
        assert user_pick_intent_items(_REAL) == ["轻松基调", "喜剧", "爽点满足"]

    def test_unknown_tone_is_ignored_not_passed_through(self):
        """拼错的调性值不该变成判官眼里的意图项。"""
        contract = {"genre_intent": {"tone_preference": "sparkly"}}
        assert user_pick_intent_items(contract) == []

    def test_unknown_skill_keys_are_dropped(self):
        contract = {
            "genre_intent": {
                "explicit_enhancers": {"effect_skills": ["not_a_real_engine"]}
            }
        }
        assert user_pick_intent_items(contract) == []

    def test_empty_and_missing_contracts_are_safe(self):
        for value in ({}, None, {"genre_intent": {}}):
            assert verifiable_intent_items(value) == []
            assert user_pick_intent_items(value) == []


class TestTheGateNowRuns:
    def test_the_guard_no_longer_hangs_on_taxonomy_tags_alone(self):
        import inspect

        from bestseller.services import conception

        src = inspect.getsource(conception)
        assert "if _ia_items or _ia_cost_checked:" in src, (
            "放行条件必须同时认勾选项与代价档；只认分类标签就是真机那个洞"
        )

    def test_a_receipt_is_written_before_the_guard(self):
        """回执恒写——没有回执时无法区分「跑了没发现」与「压根没跑」。"""
        import inspect

        from bestseller.services import conception

        src = inspect.getsource(conception)
        assert '"agent": "intent_alignment_scope"' in src
        assert '"will_run"' in src
