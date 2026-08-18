"""简介形态契约单一来源（2026-08-18《矿脉认主》定罪）。

链路：copywriter 冠军按百本榜单实抓形态产出（标签行+短句分行+陈述收尾），
吸引力闸门修复循环随后调 conception_blurb_polish 打磨——旧版打磨 prompt
自带一套相反的「点击型」规则（首句疑问强钩/结尾留悬念），把冠军改回
单段+问句收尾。真机《矿脉认主》见光简介「矿脉尽头，还藏着什么在等他？」
即此路径产物，在构思档案里零出现。

元病：同一事实（简介形态契约）住两地，后写的赢（2026-07-28 家族）。
修复：render_blurb_form_reminder 是修稿侧形态铁律的唯一来源，
所有「按诊断意见改简介」路径必须拼入它，不得自带形态规则。
"""

from __future__ import annotations

import inspect

from bestseller.services.blurb_copywriter import (
    _polish_champion,
    render_blurb_form_reminder,
)
from bestseller.services.conception import _polish_blurb_synopsis


def test_reminder_carries_board_form_tokens():
    text = render_blurb_form_reminder(lo=140, hi=220)
    for token in ("标签行", "短句分行", "陈述句或名场面截断", "禁止问句收尾", "140-220"):
        assert token in text, f"形态铁律缺 {token}"
    # 不带字数带时也要成立（champion polish 场景）
    assert "标签行" in render_blurb_form_reminder()


def test_conception_polish_consumes_single_source():
    src = inspect.getsource(_polish_blurb_synopsis)
    assert "render_blurb_form_reminder" in src, "打磨路径必须拼入单一来源形态块"
    # 旧「点击型」互斥规则不得回潮：问句钩/悬念收尾正是把冠军改坏的那两条
    for legacy in ("留悬念", "疑问/反差", "点击型"):
        assert legacy not in src, f"旧点击型规则回潮：{legacy}"


def test_champion_polish_consumes_single_source():
    src = inspect.getsource(_polish_champion)
    assert "render_blurb_form_reminder" in src
    assert "保持榜单形态：" not in src, "内联形态副本必须删除（防两地漂移）"
