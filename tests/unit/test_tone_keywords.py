"""调性关键词合并（2026-08-20 真机《罚我守坟》定罪）。

style_guides.tone_keywords 真机落库 ["轻松","幽默","明快","冷","压","悬","慢火"]
——建书页的调性 pick（light→轻松/幽默/明快）与题材画像自带的调性
（冷/压/悬/慢火）被**直接拼接**，无任何自洽校验。这份自相矛盾的清单
逐字进了每一章的 system prompt（PROJECT PROFILE·语气关键词），
写手同时被要求「轻松幽默明快」和「冷、压、慢火」。
而且爽文书里活着一个「慢火」——与《摔下山三次》定罪的「慢热」同族。

第二处病：story_bible 落 book_spec 时 `style_guide.tone_keywords = content['tone']`
是**整体覆盖**，用户在建书页选的调性会被模型自产的 tone 无声抹掉——
与今早 pack 路由「建书守住、写作失守」同形的「同一事实住两地，后写的赢」。
"""

from __future__ import annotations

import pytest

from bestseller.services.tone_keywords import merge_tone_keywords

pytestmark = pytest.mark.unit


def test_light_lead_drops_contradicting_genre_tone():
    merged = merge_tone_keywords(
        lead=["轻松", "幽默", "明快"],
        base=["冷", "压", "悬", "慢火"],
    )
    assert "轻松" in merged
    # 冷/压 与轻快组对立；慢火与明快对立
    assert "冷" not in merged and "压" not in merged and "慢火" not in merged
    # 不对立的题材调性必须留下（调性只在题材边界内生效，不是替换题材）
    assert "悬" in merged


def test_dark_lead_drops_light_genre_tone():
    merged = merge_tone_keywords(
        lead=["暗黑", "冷峻", "压抑"],
        base=["轻松", "市井气", "节奏快"],
    )
    assert "轻松" not in merged
    assert "市井气" in merged


def test_no_lead_is_a_no_op():
    base = ["冷", "压", "悬", "慢火"]
    assert merge_tone_keywords(lead=[], base=base) == base


def test_merged_list_is_capped_and_deduped():
    merged = merge_tone_keywords(
        lead=["热血", "燃", "爽快"],
        base=["爽快", "硬核爽文", "步步反杀", "护人心切", "场面感", "市井气"],
    )
    assert len(merged) <= 6
    assert len(merged) == len(set(merged))
    assert merged[0] == "热血", "用户选的调性领衔"


def test_story_bible_merges_instead_of_overwriting():
    import inspect

    from bestseller.services import story_bible

    src = inspect.getsource(story_bible)
    assert "merge_tone_keywords" in src, (
        "book_spec 落库不得整体覆盖用户在建书页选的调性"
    )
