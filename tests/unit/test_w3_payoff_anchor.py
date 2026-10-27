"""W3 回归锁：爽点保全带**有出处的**兑现锚点（2026-09-10）。

病灶（计划 §3 W3 / 根因文档）：``render_hype_preservation_block`` 只有一条
泛化的「三拍不许删」硬约束，没有任何指向**上一稿已经写出来的那一段**的引用：
``classify_hype`` 只返回 ``(type, confidence)``，不返回命中的句子，于是审计
与修复都无法引用「上一稿到底写的是哪一句」，写手只能凭记忆——重写时注意力
被「修上一稿的问题」占满，结算段被挤掉。

修：``hype_engine.locate_realized_payoff`` 在正文里**确定性**定位实际结算句
（返回原文 + 字符区间 + 命中关键词），``render_hype_preservation_block(...,
source_body=...)`` 把它作为**有出处的锚点**附上。两条边界按计划写死：

  * 锚点**只引用事实**，不新增泛化规则，也不要求所有题材套同一三拍公式；
  * 锚点**不拥有否决权**——与已确立事实冲突时以事实为准，可替换，只须留痕。

读不出片段时函数返回空锚点（诚实信号），调用方不得用假片段填充。
"""

from __future__ import annotations

import pytest

from bestseller.services.drafts import render_hype_preservation_block
from bestseller.services.hype_engine import describe_payoff_change, locate_realized_payoff

pytestmark = pytest.mark.unit


class _Chapter:
    def __init__(self, hype_type="face_slap", recipe=None, assigned=None):
        self.hype_type = hype_type
        self.hype_recipe_key = recipe
        self.metadata_json = {"assigned_hype": assigned} if assigned else {}


_ZH_BODY = (
    "他把算盘搁下，先擦了擦手。\n"
    "满堂宾客看着长老僵住，脸色铁青，这一记当众打脸来得又快又狠，"
    "谁都没想到废柴会赢。\n"
    "他把令牌拍在案上，无人再敢出声。\n"
    "夜色一点点沉下去。\n"
)

_EN_BODY = (
    "He said nothing at first.\n"
    "The elder was stunned and humiliated, slapped silent before the whole hall.\n"
    "Night fell.\n"
)


# ── 定位器：确定性、可回指原文 ────────────────────────────────────────────


def test_locate_realized_payoff_finds_the_actual_sentence():
    payoff = locate_realized_payoff(_ZH_BODY, "zh-CN")
    assert payoff is not None
    assert payoff.hype_type == "face_slap"
    assert "打脸" in payoff.sentence
    assert payoff.keyword_hits >= 3
    # 出处必须能回指原文：偏移切片与所引句子一致（strip 后逐字相等）
    assert _ZH_BODY[payoff.char_start : payoff.char_end].strip() == payoff.sentence


def test_locate_prefers_later_sentence_on_tie():
    """命中数相同时取更靠后的一句（结算通常落在动作之后）。"""

    body = "他打脸了。\n他打脸了。\n"
    payoff = locate_realized_payoff(body, "zh-CN")
    assert payoff is not None
    assert payoff.sentence_index == 1


def test_locate_returns_none_when_no_readable_payoff():
    """读不出片段是诚实信号，不假造。"""

    assert locate_realized_payoff("") is None
    assert locate_realized_payoff("   \n  ") is None
    assert locate_realized_payoff("天亮了。他起床洗脸，出门买早饭。") is None


def test_locate_english_body():
    payoff = locate_realized_payoff(_EN_BODY, "en-US")
    assert payoff is not None
    assert payoff.hype_type == "face_slap"
    assert "stunned" in payoff.sentence


def test_locate_payoff_to_dict_roundtrips():
    payoff = locate_realized_payoff(_ZH_BODY, "zh-CN")
    assert payoff is not None
    payload = payoff.to_dict()
    assert payload["sentence"] == payoff.sentence
    assert payload["char_start"] == payoff.char_start
    assert payload["char_end"] == payoff.char_end
    assert payload["hype_type"] == payoff.hype_type


# ── 保全块：追加锚点，且锚点不越权 ────────────────────────────────────────


def test_preservation_block_appends_sourced_anchor():
    text = render_hype_preservation_block(
        _Chapter(), source_body=_ZH_BODY, source_label="原稿"
    )
    assert "爽点保全" in text, "合同本体不能丢"
    assert "有出处" in text, "必须声明锚点有出处"
    assert "出处：原稿" in text
    assert "打脸" in text, "必须引用到原文里的那一句"
    # 不拥有否决权：与事实冲突时以事实为准
    assert "以事实为准" in text
    assert "采用记录" in text


def test_preservation_block_without_source_has_no_anchor():
    """不传 source_body → 退回原合同（向后兼容，且不空喊）。"""

    text = render_hype_preservation_block(_Chapter())
    assert "爽点保全" in text
    assert "有出处" not in text
    assert "不是车轱辘" in text


def test_unreadable_source_does_not_fabricate_anchor():
    """正文里读不出兑现片段时，不得编造锚点。"""

    text = render_hype_preservation_block(
        _Chapter(), source_body="天亮了。他起床洗脸，出门买早饭。"
    )
    assert "爽点保全" in text
    assert "有出处" not in text


def test_empty_contract_stays_empty_even_with_source():
    """无合同章不注入（防空喊）——即使给了正文。"""

    assert (
        render_hype_preservation_block(
            _Chapter(hype_type=None), source_body=_ZH_BODY
        )
        == ""
    )


def test_assigned_only_chapter_still_gets_anchor():
    """只有计划值（未盖戳）的章也应有合同 + 锚点。"""

    text = render_hype_preservation_block(
        _Chapter(hype_type=None, assigned={"type": "reversal", "recipe_key": None}),
        source_body=_ZH_BODY,
    )
    assert "reversal" in text
    assert "有出处" in text


def test_english_anchor_uses_english_labels():
    text = render_hype_preservation_block(
        _Chapter(),
        source_body=_EN_BODY,
        source_label="prior draft",
        language="en-US",
    )
    assert "Payoff already written" in text
    assert "prior draft" in text
    assert "facts win" in text


# ── 采用核对：把「修好了什么 / 丢了什么」分开，且只记录不否决 ──────────────
# 计划 §3 W3：新稿修好了哪些原问题、损失了哪些已有成效必须分开判断；
# 检测分下降只触发复核/证据；稳定、可定位的事实损失才进入有界修复。


def test_describe_payoff_change_kept():
    after = _ZH_BODY + "\n他又补了一句。\n"
    out = describe_payoff_change(_ZH_BODY, after, "zh-CN")
    assert out["verdict"] == "kept"
    assert out["kept_before_sentence"] is True
    assert out["before"]["char_start"] == out["after"]["char_start"], "原句仍在原位"


def test_describe_payoff_change_lost_is_locatable():
    after = "他把水烧开，倒进壶里，看着窗外的雨。" * 40
    out = describe_payoff_change(_ZH_BODY, after, "zh-CN")
    assert out["verdict"] == "lost"
    assert out["had_payoff_before"] is True
    assert out["has_payoff_after"] is False
    # 损失必须是**可定位**的：给出原稿那一句的字符区间
    assert out["before"]["char_start"] == 14
    assert "打脸" in out["before"]["sentence"]


def test_describe_payoff_change_changed():
    after = "他把水烧开。\n长老脸色铁青，当场被这一记打脸压得哑口无言。\n"
    out = describe_payoff_change(_ZH_BODY, after, "zh-CN")
    assert out["verdict"] == "changed", "仍有兑现段但已不是原来那一句"
    assert out["kept_before_sentence"] is False
    assert out["has_payoff_after"] is True


def test_describe_payoff_change_not_applicable():
    out = describe_payoff_change(
        "天亮了。他起床洗脸。", "天亮了。他起床洗脸，出门买早饭。", "zh-CN"
    )
    assert out["verdict"] == "no_payoff_before"
    assert out["before"] is None


def test_chapter_rewrite_receipt_carries_concrete_payoff_outcome():
    """采用回执必须自带**具体可定位**的成效事实，而不是只复述分数。"""

    import inspect

    from bestseller.services import reviews

    src = inspect.getsource(reviews.rewrite_chapter_from_task)
    assert "describe_payoff_change(" in src
    assert '"hype_outcome": _hype_outcome' in src
    # 追加式合并：不得覆盖 rewrite_task 上已有的回执字段
    assert "**(rewrite_task.metadata_json or {})," in src
    # 回执基线取 rewrite_task.metadata_json → hype_outcome 随成功/失败回执落库
    assert "**(rewrite_task.metadata_json or {})," in inspect.getsource(reviews)


def test_hype_regressions_is_append_only_audit_trail():
    """计划 §3 W3：hype_regressions 是审计轨迹，禁止删除、抑制或恒通过归零。"""

    import inspect

    from bestseller.services import drafts

    src = inspect.getsource(drafts.stamp_chapter_hype)
    assert 'list(_meta.get("hype_regressions") or [])' in src, "必须先读旧留痕"
    assert "_regressions.append(" in src, "必须追加"
    # 唯一一次赋值只能是把追加后的列表写回
    assert src.count('["hype_regressions"] =') == 1
    assert '["hype_regressions"] = _regressions' in src
    # 不得截断/清空/删除
    assert '["hype_regressions"] = []' not in src
    assert 'pop("hype_regressions"' not in src


@pytest.mark.asyncio
async def test_hype_regression_append_preserves_history():
    """第二次丢戳必须**追加**，旧留痕一字不删。"""

    from bestseller.services.drafts import stamp_chapter_hype

    class _Ch:
        def __init__(self):
            self.hype_type = "face_slap"
            self.hype_recipe_key = "仙侠-宗门打脸"
            self.hype_intensity = 8.0
            self.metadata_json = {"hype_regressions": [{"lost_type": "level_up"}]}

    class _S:
        def add(self, *a, **k):  # pragma: no cover
            raise AssertionError("清戳路径不应写 session")

    ch = _Ch()
    await stamp_chapter_hype(
        _S(),
        chapter=ch,
        chapter_number=3,
        content_md="他把水烧开，倒进壶里，看着窗外的雨。" * 40,
        project=None,
        scene_drafts=(),
        refresh=True,
    )
    assert ch.hype_type is None, "新稿读不出爽点必须清戳"
    regressions = ch.metadata_json["hype_regressions"]
    assert len(regressions) == 2, "必须追加而不是覆盖"
    assert regressions[0]["lost_type"] == "level_up", "旧留痕不得被删"
    assert regressions[1]["reason"].startswith("re-stamp on new current draft")
