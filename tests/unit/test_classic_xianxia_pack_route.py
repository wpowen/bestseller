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


def test_writing_path_honours_user_selected_pack():
    """写作路径必须沿用建书时用户选的 taxonomy pack（2026-08-19 定罪）。

    建书处正确地把 genre_intent_contract.prompt_pack_key 作为 forced pack，
    写作处却没传——每写一章都重跑关键词推断，污染守卫把用户选择翻掉。
    同一条边界在一处守住、另一处失守。
    """
    import inspect

    from bestseller.services import drafts

    src = inspect.getsource(drafts._resolve_project_writing_profile)
    assert "genre_intent_contract" in src, "写作路径必须读意图契约里的 pack"
    assert "forced_prompt_pack_key=_forced_pack" in src


def test_forced_pack_survives_contradicting_route():
    """有 forced pack 时，推断路由不得改写它（守卫只管 LLM 编的 pack）。"""
    from bestseller.services.writing_profile import resolve_writing_profile

    profile = resolve_writing_profile(
        {"market": {"prompt_pack_key": "suspense-mystery"}},
        genre="古典仙侠",
        sub_genre="古典仙侠",
        audience="male",
        language="zh-CN",
        forced_prompt_pack_key="xianxia-upgrade-core",
    )
    assert profile is not None
