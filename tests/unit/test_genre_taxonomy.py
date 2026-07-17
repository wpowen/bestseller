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


# ── 建书选项传导回归网 (2026-07-15) ─────────────────────────────────────────
# 前端"组合题材"曾整层失效:get_sub_genre 只比 sub.key,而模板卡/CLI/REST 传的
# 都是中文 label ⇒ 37/62 张卡的子题材静默解析成 None,连带丢掉该子题材的
# prompt pack / power_system / default_tags。


@pytest.mark.unit
def test_get_sub_genre_accepts_key_label_and_alias() -> None:
    """docstring 承诺 "canonical keys or free-form labels" —— 必须两者都认。"""

    by_key = gt.get_sub_genre("xuanhuan", "eastern-xuanhuan")
    assert by_key is not None and by_key.key == "eastern-xuanhuan"
    # label(前端之外的调用方——模板卡/CLI/REST——传的就是它)
    by_label = gt.get_sub_genre("xuanhuan", "东方玄幻")
    assert by_label is not None and by_label.key == "eastern-xuanhuan"
    assert gt.get_sub_genre("xuanhuan", "异世大陆").key == "otherworld-continent"
    assert gt.get_sub_genre("xuanhuan", "高武世界").key == "high-martial"
    # 不存在的仍返回 None(别把匹配放宽成乱撞)
    assert gt.get_sub_genre("xuanhuan", "根本不存在的子题材zzz") is None


@pytest.mark.unit
def test_sub_genre_label_carries_pack_power_system_and_default_tags() -> None:
    """label 命中后,子题材承载的四样真东西必须都跟过来(丢了等于没选)。"""

    sel = gt.resolve_selection("male", "玄幻", "东方玄幻", [])
    assert sel.sub_genre_key == "eastern-xuanhuan"
    assert sel.power_system == "bloodline"
    assert sel.pack  # 子题材自己的 pack
    for tag in ("废柴逆袭", "升级流", "血脉觉醒"):
        assert tag in sel.tags


@pytest.mark.unit
def test_resolve_genre_uses_sub_genre_for_canonicalisation() -> None:
    """canonicalize 是为(genre, sub)双参设计的;只喂 genre 会让部分组合解析失败。"""

    assert gt.canonicalize("青春成长", "校园群像") is not None
    sel = gt.resolve_selection(None, "青春成长", "校园群像", [])
    assert sel.genre_key is not None  # 修前:None → 建契约 raise → 建书 500


@pytest.mark.unit
def test_sub_genre_rescues_but_never_hijacks_the_picked_genre() -> None:
    """子题材参与 canonicalize 只许"救场",不许"抢票"。

    把 (genre, sub) 一起交给 canonicalize 会让子题材标签里的外来 token 盖过用户
    真正选的题材:惊悚灵异+驱魔探案综合 →「探案」把它拽进 suspense;历史宫廷+
    宫廷悬疑 →「悬疑」拽进 suspense。用户选的题材静默失效 = 本轮要根治的病本身。
    规则:题材自己能解析 → 题材说了算;解析不出来 → 才让子题材救。
    """

    # 救场:题材单独解析不出,靠子题材补齐(这条是双参存在的理由,不能丢)
    assert gt.resolve_selection(None, "青春成长", None, []).genre_key is None
    assert gt.resolve_selection(None, "青春成长", "校园群像", []).genre_key == "light-novel"

    # 抢票:题材单独解析得出时,子题材不得改写它
    for genre, sub in (("惊悚灵异", "驱魔探案综合"), ("历史宫廷", "宫廷悬疑")):
        solo = gt.resolve_selection(None, genre, None, []).genre_key
        paired = gt.resolve_selection(None, genre, sub, []).genre_key
        assert solo is not None
        assert paired == solo, f"{genre} 被子题材 {sub} 从 {solo} 劫持到 {paired}"


@pytest.mark.unit
def test_every_preset_card_creates_a_book_without_crashing() -> None:
    """62 张模板卡曾有 25 张(全部英文卡)在建契约时 raise → 建书直接 500。

    契约建不出来是可接受的降级(英文书本就不属于中文 taxonomy 的市场),
    但**绝不允许抛异常打死建书**——server 必须能拿到契约或干净地拿到 None。
    """

    from bestseller.services.genre_intent_contract import build_genre_intent_contract
    from bestseller.web.server import _channel_of_genre

    try:
        from bestseller.services.writing_presets import list_genre_presets
    except Exception:  # pragma: no cover - preset module moved
        from bestseller.services.genre_presets import list_genre_presets

    presets = list_genre_presets()
    assert presets, "preset catalog is empty"
    unexpected: list[str] = []
    for preset in presets:
        try:
            build_genre_intent_contract(
                gt.resolve_selection(
                    _channel_of_genre(preset.genre), preset.genre, preset.sub_genre, []
                ),
                source="legacy_inferred",
            )
        except ValueError:
            pass  # 可接受:降级为无契约的 legacy 路径(server 已 try/except)
        except Exception as exc:  # noqa: BLE001 - 任何其它异常都是真 bug
            unexpected.append(f"{preset.key}: {type(exc).__name__}: {exc}")
    assert not unexpected, "模板卡建契约抛出非 ValueError 异常：\n" + "\n".join(unexpected)


@pytest.mark.unit
def test_channel_is_derivable_from_genre_for_legacy_preset_path() -> None:
    """legacy 路径曾硬传 channel=None ⇒ 每本模板卡书的 contract.channel_key 都是空。"""

    from bestseller.web.server import _channel_of_genre

    assert _channel_of_genre("玄幻") == "male"
    assert _channel_of_genre("古言") == "female"
    assert _channel_of_genre("Romance") is None  # 不在中文 taxonomy 里,优雅返回 None
