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
        # Romance
        ("女频言情", None, "romance-tension-growth"),
        # Unknown
        ("未知题材", None, None),
    ],
)
def test_infer_prompt_pack_key(genre: str, sub_genre: str | None, expected_key: str | None) -> None:
    result = infer_default_prompt_pack_key(genre, sub_genre)
    assert result == expected_key, f"Expected '{expected_key}' for genre='{genre}', sub_genre='{sub_genre}', got '{result}'"
