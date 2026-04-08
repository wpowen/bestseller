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
        # Xianxia
        ("修仙", "玄幻", "xianxia-upgrade-core"),
        ("升级流", None, "xianxia-upgrade-core"),
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
