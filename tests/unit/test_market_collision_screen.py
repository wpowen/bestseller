"""竞品调研前移到选题层（2026-08-10）。

市场验证子系统一直能用（真机 30.6s 出分），但它跑在构思**之后**，只写一份
metadata 摘要——书名/premise/简介都已定稿，它改不动任何东西。全库
``metadata ? 'market_validation_summary'`` 计数为 **0**：从没有一本书落过它。
文档流程的第一问「竞品有没有这本书」于是从未被真正问过。

现在榜单在淘汰赛挑候选之前就位，撞车的胚子拿不到展开位。

阈值校准（真机 2026-08-10）：东方玄幻/都市异能/仙侠三个榜单共 36 本不同的真实
在榜书，全部 630 组两两重合度——**不同书之间最高 0.080**（p99 0.056，中位
0.012）。阈值 0.15 ≈ 该上限的 2 倍，所以命中的含义是「与某一本在榜书的重合度，
远超任意两本在榜书之间的重合度」。这个判断只依赖负样本分布。
"""

from __future__ import annotations

import inspect

import pytest

from bestseller.services import concept_tournament as ct
from bestseller.services.market_validation.analyzer import (
    MARKET_COLLISION_THRESHOLD,
    concept_market_collisions,
    concept_market_overlap,
)

pytestmark = pytest.mark.unit

ON_BOARD = [
    {
        "title": "我在殡仪馆给人化妆",
        "intro": "殡葬馆学徒替死人整理遗容，某天接单时发现停尸台上的脸和自己一模一样。",
        "tags": ["殡葬", "灵异", "悬疑"],
    },
    {
        "title": "开局长生万古，苟到天荒地老",
        "intro": "穿越玄幻世界开局获得长生，主角只想苟着不出头，平淡日常轻松诙谐。",
        "tags": ["长生", "苟道"],
    },
]


def _rank_item(index: int, **overrides: object) -> dict:
    item: dict = {
        "index": index,
        "domain": f"域{index}",
        "freshness": 8.0,
        "click_seed": 8.0,
        "character_logic": 8.0,
        "action_seed": 8.0,
        "promise_survival": 8.0,
        "genre_fidelity": 8.0,
        "ai_assembly": 0.0,
        "dumb_cost": False,
        "after_opening_promise": "开局之后仍有承诺",
        "action_families": ["行动一", "行动二", "行动三"],
        "growth_surface": "持续积累面",
    }
    item.update(overrides)
    return item


# ── 重合度基元 ───────────────────────────────────────────────────────────


def test_threshold_sits_above_the_measured_noise_ceiling() -> None:
    """不同在榜书之间实测最高 0.080；阈值必须明显在其之上。"""

    assert MARKET_COLLISION_THRESHOLD >= 0.12
    assert MARKET_COLLISION_THRESHOLD / 0.080 >= 1.5


def test_unrelated_concept_does_not_collide() -> None:
    idea = "一个牧羊少年发现自己踩出的路只有他自己走得通，外人多看一眼路就多长一截封不回的编号。"
    assert concept_market_collisions(idea, ON_BOARD) == []


def test_a_concept_the_board_already_carries_collides() -> None:
    idea = "殡葬馆学徒替死人整理遗容，接单时发现停尸台上那张脸和自己一模一样。"
    hits = concept_market_collisions(idea, ON_BOARD)
    assert hits, "榜单已有同一本书，必须报"
    assert hits[0][0] == "我在殡仪馆给人化妆"
    assert hits[0][1] >= MARKET_COLLISION_THRESHOLD


def test_overlap_is_symmetric_and_bounded() -> None:
    a, b = "少年拿破炉子炼器", "少年拿破炉子炼器"
    assert concept_market_overlap(a, b) == pytest.approx(1.0)
    assert concept_market_overlap(a, "") == 0.0
    assert concept_market_overlap("", "") == 0.0


# ── 选择：撞车的排到后面，但绝不清空 ────────────────────────────────────


def test_colliding_ideas_are_demoted_behind_fresh_ones() -> None:
    ranking = [
        _rank_item(0, market_collision=[{"title": "我在殡仪馆给人化妆", "overlap": 0.3}]),
        _rank_item(1),
        _rank_item(2),
    ]
    picked = ct._select_raw_ideas_for_expansion(
        ranking, raw_floor=7.0, progression_floor=5.0, limit=3
    )
    # 撞车那条分数一样高，但必须排到最后。
    assert [item["index"] for item in picked] == [1, 2, 0]


def test_all_colliding_still_returns_candidates() -> None:
    """全撞车也要给展开位——硬清空等于杀书，这个代码库为此付过学费。"""

    ranking = [
        _rank_item(i, market_collision=[{"title": "撞", "overlap": 0.3}])
        for i in range(3)
    ]
    picked = ct._select_raw_ideas_for_expansion(
        ranking, raw_floor=7.0, progression_floor=5.0, limit=2
    )
    assert len(picked) == 2


def test_no_market_data_changes_nothing() -> None:
    """拿不到榜单时行为与从前逐字一致。"""

    ranking = [_rank_item(0), _rank_item(1)]
    assert [i["index"] for i in ct._select_raw_ideas_for_expansion(
        ranking, raw_floor=7.0, progression_floor=5.0, limit=2
    )] == [0, 1]


# ── 接线：榜单必须在淘汰赛之前就位，且永不进 prompt ──────────────────────


def test_board_is_fetched_before_the_tournament_picks() -> None:
    from bestseller.services import conception

    src = inspect.getsource(conception.run_conception_pipeline)
    assert "_prefetch_market_competitors(" in src
    assert "market_competitors=_market_competitor_rows(ctx)" in src
    assert src.index("_prefetch_market_competitors(") < src.index(
        "run_concept_tournament("
    ), "榜单必须在淘汰赛挑候选之前就位，否则又是一张收据"


def test_competitor_text_never_reaches_a_generation_prompt() -> None:
    """引用竞品原文给生成端 = 种词。整个筛选之所以是确定性的就是为了这个。"""

    banned = ("我在殡仪馆给人化妆", "殡葬馆学徒替死人整理遗容")
    system, user = ct._build_raw_idea_pool_messages(
        genre="东方玄幻", sub_genre="东方玄幻", count=8,
        audience_orientation="男频", prompt_arm="author_pitch",
    )
    for token in banned:
        assert token not in system + user
    # 内核 prompt 同样不得携带
    ks, ku = ct._build_engine_kernel_messages(
        genre="东方玄幻", sub_genre="东方玄幻", lane="纯题材直觉",
        chapter_count=50, seed_concept="牧羊少年踩路",
    )
    for token in banned:
        assert token not in ks + ku


def test_prefetch_is_a_noop_when_the_flag_is_off() -> None:
    import asyncio
    from types import SimpleNamespace

    from bestseller.services import conception

    ctx: dict = {"genre": "东方玄幻"}
    settings = SimpleNamespace(pipeline=SimpleNamespace(enable_market_validation=False))
    asyncio.run(conception._prefetch_market_competitors(None, settings, ctx))
    assert conception._market_competitor_rows(ctx) == ()
