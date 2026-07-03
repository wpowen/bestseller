"""读者画像机制 + 画像锚定钩子/简介生成 的单测。"""
# ruff: noqa: RUF001

from __future__ import annotations

import pytest

from bestseller.services.genre_persona import (
    build_persona_blurb_messages,
    build_persona_hook_messages,
    resolve_persona,
)


@pytest.mark.unit
def test_resolve_persona_routes_male_and_female_channels():
    male = resolve_persona("高武世界", "都市怪谈", ("赛博修仙", "系统流", "升级"))
    female = resolve_persona("现代言情", "甜宠", ("豪门", "重生"))
    assert male.channel == "男频"
    assert female.channel == "女频"
    # 无任何信号 → 中性通用画像(不再硬套男频爽文,防跨题材固化)
    assert resolve_persona("完全未知题材zzz").channel == "通用"
    # 显式频道仍然生效
    assert resolve_persona("完全未知题材zzz", channel="男频").channel == "男频"


@pytest.mark.unit
def test_hook_messages_anchor_persona_and_forbid_jargon():
    _, user = build_persona_hook_messages(
        genre="高武世界", sub_genre="都市怪谈", premise="废物觉醒诡异能力逆袭复仇。"
    )
    assert "目标读者" in user
    assert "男频" in user
    assert "钩子公式" in user
    assert "黑话" in user or "专业名词" in user  # 去黑话硬约束
    assert "废物觉醒诡异能力逆袭复仇" in user  # 设定带入


@pytest.mark.unit
def test_blurb_messages_carry_hook_and_persona():
    _, user = build_persona_blurb_messages(
        genre="现代言情", sub_genre="重生", premise="女主重生复仇。", hook="重生归来,渣男贱女一个别想跑。"
    )
    assert "女频" in user
    assert "重生归来,渣男贱女一个别想跑。" in user  # 用已定钩子扩写
    assert "黑话" in user or "专业名词" in user
    assert "爽点" in user
