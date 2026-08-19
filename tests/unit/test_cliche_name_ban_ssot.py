"""烂名黑名单单一来源 + 下沉到概念展开层（2026-08-18《九姓井口只认我》定罪）。

禁令原本只挂在 cast prompt（conception._naming_constraint_block），而主角名
在概念淘汰赛展开层（protagonist_identity）就铸死了——陆沉一路进了书名、
前提、简介，cast 层守卫扑空。守卫必须站在名字的出生地。
"""

from __future__ import annotations

import pytest

from bestseller.services.concept_tournament import _build_engine_kernel_messages
from bestseller.services.naming_normalizer import (
    CLICHE_NAME_BLOCKLIST,
    render_protagonist_name_ban,
)

pytestmark = pytest.mark.unit


def test_blocklist_single_source():
    from bestseller.services.conception import _CLICHE_NAME_BLOCKLIST

    assert _CLICHE_NAME_BLOCKLIST is CLICHE_NAME_BLOCKLIST, "词表必须同一对象，不许复制"
    assert "陆沉" in CLICHE_NAME_BLOCKLIST


def test_engine_kernel_prompt_carries_name_ban():
    _, user = _build_engine_kernel_messages(
        genre="东方玄幻",
        sub_genre="东方玄幻",
        lane="纯题材直觉",
        chapter_count=50,
        seed_concept="全村共用的老井只认他一家打水",
    )
    assert "陆沉" in user and "命名" in user, "展开层必须带烂名禁令（名字的出生地）"


def test_ban_renderers():
    compact = render_protagonist_name_ban(compact=True)
    block = render_protagonist_name_ban()
    for text in (compact, block):
        assert "陆沉" in text
        assert "近似变体" in text or "雷同变体" in text
    assert len(compact) < len(block) or "硬约束" in compact


# ── 长篇容量铁律：阶梯随目标章节数伸缩（2026-08-19 用户定案）──────────────


def test_constraint_ladder_tier_scaling():
    from bestseller.services.concept_tournament import constraint_ladder_tier_target

    assert constraint_ladder_tier_target(50) == 3
    assert constraint_ladder_tier_target(200) == 3
    assert constraint_ladder_tier_target(500) == 5
    assert constraint_ladder_tier_target(1000) == 8
    assert constraint_ladder_tier_target(5000) == 8, "上限8阶防阶梯表爆炸"


def test_kernel_prompt_carries_scaled_ladder():
    _, user_50 = _build_engine_kernel_messages(
        genre="东方玄幻", sub_genre="东方玄幻", lane="纯题材直觉",
        chapter_count=50, seed_concept="一支笔",
    )
    _, user_1000 = _build_engine_kernel_messages(
        genre="东方玄幻", sub_genre="东方玄幻", lane="纯题材直觉",
        chapter_count=1000, seed_concept="一支笔",
    )
    assert "目标 50 章" in user_50 and "3 阶" in user_50
    assert "目标 1000 章" in user_1000 and "8 阶" in user_1000
    assert "constraint_ladder" in user_50, "输出契约必须带阶梯字段"
    assert "不锁世界" in user_50
