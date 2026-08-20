"""古典仙侠不得被路由成东方美学（2026-08-19 真机定罪）。

genre_taxonomy 里「古典仙侠」的官方 pack 是 xianxia-upgrade-core
(action-progression 升级爽文)，但关键词推断函数把「古典仙侠」列在
东方美学路由(国风/水墨/诗词，文艺向)。同一个词两处定性相反 →
污染守卫按跨家族处理并采信推断路由，推翻用户在建书页选的 pack，
正文按文艺美学写，与勾选的爽点引擎/轻松基调冲突。
"""

from __future__ import annotations

import pytest

from bestseller.services.genre_taxonomy import pack_category
from bestseller.services.prompt_packs import infer_default_prompt_pack_key

pytestmark = pytest.mark.unit


def test_classic_xianxia_stays_in_cultivation_family():
    for genre, sub in (("古典仙侠", "古典仙侠"), ("仙侠", "古典仙侠")):
        key = infer_default_prompt_pack_key(genre, sub)
        assert key != "eastern-aesthetic", f"{genre}/{sub} 被误判成东方美学"
        # 必须与 taxonomy 官方 pack 同家族，否则污染守卫仍会翻掉用户选择
        assert pack_category(key) == pack_category("xianxia-upgrade-core")


def test_real_aesthetic_tokens_still_route_there():
    for genre, sub in (("东方美学", ""), ("国风", ""), ("仙侠", "水墨")):
        assert infer_default_prompt_pack_key(genre, sub) == "eastern-aesthetic"
