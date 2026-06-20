"""L1 unit tests for the canonical genre taxonomy (genre_taxonomy.py).

Guards the contract that the taxonomy converges the framework's five genre
layers:
* every sub-genre routes to a real ``novel_category`` and ``prompt_pack``;
* every default tag exists in the trope-tag pool;
* ``canonicalize`` covers every free-form genre string the ``material_library``
  is seeded under (the 0-hit fix) and every ZH preset card;
* ``resolve_selection`` composes the correct downstream carriers.
"""

from __future__ import annotations

import pytest

from bestseller.services import genre_taxonomy as gt
from bestseller.services.novel_categories import load_novel_category_registry
from bestseller.services.prompt_packs import load_prompt_pack_registry
from bestseller.services.writing_presets import load_writing_preset_catalog


# The free-form genre strings the material_library is seeded under (from the 56
# seed batches). Every one must canonicalize to a genre OR be a known tag,
# otherwise material retrieval silently 0-hits for that genre.
MATERIAL_GENRE_STRINGS = [
    "历史", "末日", "玄幻", "武侠", "言情", "悬疑", "都市", "西方奇幻",
    "校园", "宫斗", "灵异", "洪荒", "娱乐圈", "赛博朋克", "美食", "科幻",
    "快穿", "女尊", "游戏", "穿书", "种田", "萌宠", "无限流", "仙侠",
    "重生", "心理惊悚", "机甲", "末世", "现代", "都市修仙", "谍战",
    "网游", "电竞", "御兽", "古言",
]


def test_taxonomy_loads_with_expected_shape():
    tax = gt.load_genre_taxonomy()
    assert tax.version == 1
    assert {c.key for c in tax.channels} == {"male", "female", "general"}
    assert len(tax.genres) >= 19
    # Loading is cached / idempotent.
    assert gt.load_genre_taxonomy() is tax


def test_every_sub_genre_category_is_real():
    valid = set(load_novel_category_registry().keys())
    assert valid, "novel_category registry should not be empty"
    for genre, sub in gt.iter_sub_genres():
        cat = sub.category or genre.category_default
        assert cat in valid, f"{genre.key}/{sub.key} → unknown category {cat!r}"


def test_every_genre_default_category_is_real():
    valid = set(load_novel_category_registry().keys())
    for genre in gt.load_genre_taxonomy().genres:
        assert genre.category_default in valid, genre.key


def test_every_sub_genre_pack_is_real():
    valid = set(load_prompt_pack_registry().keys())
    assert valid, "prompt_pack registry should not be empty"
    for genre, sub in gt.iter_sub_genres():
        pack = sub.pack or genre.pack_default
        if pack is None:
            continue
        assert pack in valid, f"{genre.key}/{sub.key} → unknown pack {pack!r}"


def test_default_tags_subset_of_tag_pool():
    pool = gt.tag_pool()
    assert pool, "tag pool should not be empty"
    for genre, sub in gt.iter_sub_genres():
        for tag in sub.default_tags:
            assert tag in pool, f"{genre.key}/{sub.key} default tag {tag!r} not in pool"


@pytest.mark.parametrize("material_string", MATERIAL_GENRE_STRINGS)
def test_canonicalize_covers_material_strings(material_string):
    canon = gt.canonicalize(material_string)
    assert canon is not None or gt.is_known_tag(material_string), (
        f"material genre {material_string!r} maps to neither a canonical genre "
        f"nor a known tag → would 0-hit retrieval"
    )


def test_canonicalize_covers_all_zh_preset_cards():
    catalog = load_writing_preset_catalog()
    unmapped = []
    for preset in catalog.genre_presets:
        if not (preset.language or "").lower().startswith("zh"):
            continue  # English tree deferred (design §6.2)
        if gt.canonicalize(preset.genre, preset.sub_genre) is None:
            unmapped.append((preset.key, preset.genre, preset.sub_genre))
    assert not unmapped, f"ZH presets not landing on the tree: {unmapped}"


def test_canonicalize_priority_apocalypse_beats_scifi():
    # "末日科幻" must route to apocalypse, not sci-fi (matches infer_default).
    assert gt.canonicalize("末日科幻") == "apocalypse"


def test_canonicalize_returns_none_for_unknown():
    assert gt.canonicalize("完全不存在的题材xyz") is None


def test_retrieval_aliases_bridges_apocalypse_buckets():
    aliases = gt.retrieval_aliases("天灾囤货")
    # A 天灾囤货 book must be able to draw on both 末日 and 末世 buckets.
    assert "末日" in aliases
    assert "末世" in aliases


def test_resolve_selection_apocalypse_hoarding():
    sel = gt.resolve_selection("male", "apocalypse", "disaster-hoarding", ["升级流"])
    assert sel.category == "action-progression"
    assert sel.pack == "apocalypse-supply-chain"
    assert sel.genre_str == "天灾囤货"
    assert sel.sub_genre_str == "天灾囤货"
    assert "囤货" in sel.tags and "升级流" in sel.tags


def test_resolve_selection_xuanhuan_isekai():
    # The previously-unexpressible "any xuanhuan sub-genre" path.
    sel = gt.resolve_selection("male", "xuanhuan", "otherworld-continent")
    assert sel.category == "otherworld-cross-system"
    assert sel.pack == "xuanhuan-power-fantasy"
    assert sel.genre_str == "异世大陆"


def test_resolve_selection_accepts_free_form_genre_label():
    # Passing a label instead of a key still resolves.
    sel = gt.resolve_selection("male", "末世", None)
    assert sel.genre_key == "apocalypse"
    assert sel.category == "action-progression"


def test_resolve_selection_unknown_genre_is_graceful():
    sel = gt.resolve_selection(None, "未知题材zzz", None)
    assert sel.genre_str == "未知题材zzz"
    assert sel.category is None  # no crash; downstream falls back
