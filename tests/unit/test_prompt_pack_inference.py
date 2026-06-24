from __future__ import annotations

import pytest

from bestseller.services.prompt_packs import infer_default_prompt_pack_key


@pytest.mark.parametrize(
    "genre,sub_genre,expected_key",
    [
        # Apocalypse — must match before sci-fi
        ("末日科幻", "重生囤货", "apocalypse-supply-chain"),
        ("废土生存", None, "apocalypse-supply-chain"),
        # Suspense & mystery
        ("推理探案", None, "suspense-mystery"),
        ("规则怪谈", "民俗诡事", "suspense-mystery"),
        ("规则生存 / meta博弈", "末日科幻规则实验", "suspense-mystery"),
        ("悬疑惊悚", None, "suspense-mystery"),
        # History
        ("历史争霸", None, "history-strategy"),
        ("穿越考据", "重生经商", "history-strategy"),
        ("三国权谋", None, "history-strategy"),
        # Sci-fi
        ("星海大战", None, "scifi-starwar"),
        ("黑科技", "机甲", "scifi-starwar"),
        ("科幻", None, "scifi-starwar"),
        # Game & esport
        ("游戏电竞", None, "game-esport"),
        ("无限流", "副本系统流", "game-esport"),
        # Female palace
        ("宫斗权谋", None, "female-palace"),
        ("大女主", None, "female-palace"),
        # Eastern aesthetic
        ("东方美学幻想", None, "eastern-aesthetic"),
        ("国风", "水墨仙侠", "eastern-aesthetic"),
        # Xianxia sub-genre fan-out (L1 de-homogenisation)
        # — revenge-driven → history-strategy
        ("仙侠", "复仇仙侠", "history-strategy"),
        ("玄幻", "灭门修仙", "history-strategy"),
        ("修真", "血海深仇", "history-strategy"),
        # — sect management → game-esport
        ("仙侠", "宗门经营", "game-esport"),
        ("玄幻", "掌门仙侠", "game-esport"),
        # — antihero / demonic → psychological-thriller
        ("仙侠", "魔修", "psychological-thriller"),
        ("玄幻", "黑化仙侠", "psychological-thriller"),
        ("修真", "魔道仙侠", "psychological-thriller"),
        # — crafting (alchemy/forging) → litrpg-progression
        ("仙侠", "炼丹仙侠", "litrpg-progression"),
        ("玄幻", "炼器仙侠", "litrpg-progression"),
        ("修真", "符修", "litrpg-progression"),
        # — cozy / farming → cozy-fantasy
        ("仙侠", "种田仙侠", "cozy-fantasy"),
        ("玄幻", "田园仙侠", "cozy-fantasy"),
        # Generic Xianxia catch-all (must still work)
        ("修仙", "玄幻", "xianxia-upgrade-core"),
        ("升级流", None, "xianxia-upgrade-core"),
        ("仙侠", None, "xianxia-upgrade-core"),
        ("玄幻", None, "xianxia-upgrade-core"),
        # Urban
        ("都市异能", None, "urban-power-reversal"),
        ("都市异能", "身份反转", "urban-power-reversal"),
        # Urban book whose generated sub-genre/tone mentions 升级 must NOT be
        # hijacked by the generic xianxia "升级" catch-all (regression: 误读成神).
        ("都市异能", "迪化升级", "urban-power-reversal"),
        ("都市异能", "系统升级流", "urban-power-reversal"),
        ("异能", "现实升级打脸", "urban-power-reversal"),
        # Romance
        ("女频言情", None, "romance-tension-growth"),
        # Unknown
        ("未知题材", None, None),
    ],
)
def test_infer_prompt_pack_key(genre: str, sub_genre: str | None, expected_key: str | None) -> None:
    result = infer_default_prompt_pack_key(genre, sub_genre)
    assert result == expected_key, f"Expected '{expected_key}' for genre='{genre}', sub_genre='{sub_genre}', got '{result}'"


# ── Cross-genre prompt-pack contamination guard ─────────────────────────────
# Regression for the 《喜事公关》(都市修真2.0 喜剧) incident: a conception-stage
# writing profile carried ``market.prompt_pack_key = "suspense-mystery"``
# (inherited from prior detective/suspense projects) and, because the explicit
# pack used to win over the genre route, it poisoned premise/world/cast/outline
# and every drafted chapter with detective DNA. The genre route must win when an
# explicit pack contradicts the book's own genre.


@pytest.mark.parametrize(
    "genre,sub_genre,explicit_pack,expected",
    [
        # Contamination: explicit suspense pack on an urban-cultivation book →
        # genre route wins.
        ("都市修真·职场升级流", "修仙2.0", "suspense-mystery", "urban-cultivation-2.0"),
        # Different urban genre, different contaminating pack → still routes to
        # the book's own (urban) family, never the foreign suspense/thriller pack.
        ("都市异能", "现代都市", "psychological-thriller", "urban-power-reversal"),
        # Genre-consistent explicit pack is preserved (no spurious override).
        ("都市修真", "修仙2.0", "urban-cultivation-2.0", "urban-cultivation-2.0"),
        # Unrecognised genre has no route → explicit pack is honoured as fallback.
        ("完全未知的题材标签zzz", None, "suspense-mystery", "suspense-mystery"),
    ],
)
def test_resolve_writing_profile_contamination_guard(
    genre: str, sub_genre: str | None, explicit_pack: str, expected: str
) -> None:
    from bestseller.services.writing_profile import resolve_writing_profile

    profile = resolve_writing_profile(
        {"market": {"prompt_pack_key": explicit_pack}},
        genre=genre,
        sub_genre=sub_genre,
    )
    assert profile.market.prompt_pack_key == expected


def test_resolve_writing_profile_no_explicit_uses_genre_route() -> None:
    """With no explicit pack, the genre route alone drives the resolution."""
    from bestseller.services.writing_profile import resolve_writing_profile

    profile = resolve_writing_profile(
        None, genre="都市修真·职场升级流", sub_genre="修仙2.0"
    )
    assert profile.market.prompt_pack_key == "urban-cultivation-2.0"


# Cultivation/power-fantasy packs that are all action-progression-family: a
# 诡异修仙/高武 + 规则怪谈 hybrid must land on ONE of these (the canonical layer
# picks xuanhuan-power-fantasy when 高武世界 is present, xianxia-upgrade-core for
# plain 修仙), NEVER on game-esport / suspense-mystery.
_CULTIVATION_PACKS = {"xianxia-upgrade-core", "xuanhuan-power-fantasy", "urban-cultivation-2.0"}


def test_rule_horror_on_cultivation_spine_routes_to_cultivation_pack() -> None:
    """诡异修仙/高武/升级流 + 规则怪谈 must route to a cultivation pack, NOT
    suspense-mystery or game-esport. The 规则怪谈/宗门经营 tokens are flavor on a
    cultivation spine; without the guard they hijack the writer's methodology."""
    from bestseller.services.prompt_packs import infer_default_prompt_pack_key

    assert (
        infer_default_prompt_pack_key("诡异修仙 / 高武极道 / 规则怪谈 / 升级流", "高武世界")
        in _CULTIVATION_PACKS
    )
    assert infer_default_prompt_pack_key("修仙 规则怪谈 恐怖", None) in _CULTIVATION_PACKS
    # The exact 宗门经营 hijack that triggered the framework fix:
    assert (
        infer_default_prompt_pack_key("诡异修仙 宗门经营 规则怪谈 幕后黑手 数据流", "")
        in _CULTIVATION_PACKS
    )


def test_pure_rule_horror_without_cultivation_still_suspense() -> None:
    """Guard is narrow: rule-horror / folk-horror WITHOUT a cultivation spine
    keeps routing to suspense-mystery."""
    from bestseller.services.prompt_packs import infer_default_prompt_pack_key

    assert infer_default_prompt_pack_key("规则怪谈", None) == "suspense-mystery"
    assert infer_default_prompt_pack_key("规则生存 无限流", None) == "suspense-mystery"
    assert infer_default_prompt_pack_key("都市怪谈 民俗", None) == "suspense-mystery"


def test_cultivation_spine_book_writer_pack_not_overridden() -> None:
    """End-to-end: the contamination guard must NOT override the correct
    explicit xianxia pack for a 诡异修仙+规则怪谈 hybrid (regression for the
    misroute that gave the writer detective prompts)."""
    from bestseller.services.writing_profile import resolve_writing_profile

    profile = resolve_writing_profile(
        {"market": {"prompt_pack_key": "xianxia-upgrade-core"}},
        genre="诡异修仙 / 高武极道 / 规则怪谈 / 升级流",
        sub_genre="高武世界",
    )
    # The writer must get a cultivation pack (not detective/game); the canonical
    # genre route may refine xianxia→xuanhuan for a 高武世界 book — both are valid.
    assert profile.market.prompt_pack_key in _CULTIVATION_PACKS


def test_cross_resolver_genre_consistency() -> None:
    """Recurrence guard for the genre-misroute CLASS: the writer pack and the
    review profile must AGREE on a genre's family. A cultivation-spine book
    (whatever flavor/management tags ride along) must get a cultivation pack AND
    an action-progression review profile — they must not diverge (the bug where
    宗门经营→game-esport pack but action-progression review, or 规则怪谈→suspense
    pack but action-progression review). Pure flavor genres stay flavor on both.
    If a future change re-introduces divergence on any of these, this fails.
    """
    from bestseller.services.genre_review_profiles import resolve_genre_review_profile
    from bestseller.services.prompt_packs import infer_default_prompt_pack_key

    cultivation_genres = [
        "诡异修仙 宗门经营 规则怪谈 幕后黑手 数据流",
        "诡异修仙 规则怪谈",
        "诡异修仙",
        "高武世界",
        "修仙 规则怪谈 恐怖",
    ]
    for g in cultivation_genres:
        pack = infer_default_prompt_pack_key(g, "")
        review = resolve_genre_review_profile(g, "").category_key
        assert pack in _CULTIVATION_PACKS, f"{g}: pack {pack} not cultivation"
        assert review == "action-progression", f"{g}: review {review} not action-progression"

    # Pure flavor genres (no cultivation spine) stay on the flavor family.
    assert infer_default_prompt_pack_key("规则怪谈", "") == "suspense-mystery"
    assert (
        resolve_genre_review_profile("规则怪谈", "").category_key == "suspense-mystery"
    )
