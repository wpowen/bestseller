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


# ── 调性服从（2026-08-13《摸一摸，救我妹》定罪：选题必须服从用户选项）────


from bestseller.services.concept_tournament import (
    _coercion_stake_hits,
    _creation_intent_content_violations,
)


def test_coercion_stakes_violate_light_tone_without_mood_words() -> None:
    """情绪词表测不出用事件写的沉重：人质+限期+沉河零情绪词照样定罪。"""

    premise = (
        "沈鲤替他垫付旧债欠下赌坊三吊钱，被漕帮盐枭扣做人质，"
        "逼沈砚一夜摸完三百箱私盐，否则天亮沈鲤沉河。"
    )
    violations = _creation_intent_content_violations(premise, tone_preference="light")
    assert any("胁迫式生死赌注" in v for v in violations)


def test_coercion_check_only_applies_to_light_tone() -> None:
    premise = "他被扣做人质，否则天亮沉河。"
    assert _creation_intent_content_violations(premise, tone_preference="") == ()
    assert _creation_intent_content_violations(premise, tone_preference="hot") == ()


def test_light_board_hooks_do_not_trip_coercion() -> None:
    """轻松系真实榜单钩子零误报（100 本全量简介实测 0 命中）。"""

    for text in (
        "白野穿越到了二百年后的大灾变时代，他每天能静止时间一分钟。",
        "季白随手选了北大录取通知书加北京一套房——系统绑定完成，消费成功。",
        "三岁半小魔童在修仙界超凶逆袭，一路找娘。",
    ):
        assert _coercion_stake_hits(text) == ()


def test_tone_conflicting_ideas_are_demoted_at_selection() -> None:
    ranking = [
        _rank_item(0, tone_conflict=True),
        _rank_item(1),
    ]
    picked = ct._select_raw_ideas_for_expansion(
        ranking, raw_floor=7.0, progression_floor=5.0, limit=2
    )
    assert [item["index"] for item in picked] == [1, 0]


# ── 默认债务/丧葬族复活（2026-08-13：连续两本用户书撞同族，双保险死代码）──


from bestseller.services.anti_default_motif import (
    default_debt_family_hits,
    is_debt_dominated,
    user_requested_debt,
)

_BOOK_A = "沈鲤替他垫付旧债欠下赌坊三吊钱，孙疤脸把欠条拍在木桩上，被扣做人质。"
_BOOK_B = "十六岁少年随母在旧县灵堂被刁难开棺，每替母亲讨回一桩旧账就多解一层，从死不认账到抢功办丧。"


def test_both_real_champions_are_debt_dominated() -> None:
    assert is_debt_dominated(_BOOK_A)
    assert is_debt_dominated(_BOOK_B)


def test_single_incidental_mention_is_not_dominated() -> None:
    """退役档案第二死因防复发：一处顺带提及不是污染。"""

    cfo = "她是丈夫家族企业的隐形CFO，十年来所有对外账目和税务全出自她手；离婚那天她转身去了对手公司。"
    assert not is_debt_dominated(cfo)
    assert not is_debt_dominated("凡骨许太平誓要向修行界证明：凡骨亦能登仙。")


def test_user_intent_probe_is_honest_now() -> None:
    assert user_requested_debt({"description": "我想写一个讨债师傅的故事"}) is True
    assert user_requested_debt({"allow_debt_theme": True}) is True
    assert user_requested_debt({"description": "轻松爽文，少年在游戏里挖矿"}) is False
    assert user_requested_debt(None) is False


def test_default_family_ideas_sink_below_clean_but_above_nothing() -> None:
    ranking = [
        _rank_item(0, default_family=True),
        _rank_item(1, market_collision=[{"title": "撞", "overlap": 0.3}]),
        _rank_item(2),
    ]
    picked = ct._select_raw_ideas_for_expansion(
        ranking, raw_floor=7.0, progression_floor=5.0, limit=3
    )
    # 干净 > 默认族 > 撞车？不——默认族沉得比撞车浅一档以上：condemned > tone > family > collision
    assert [item["index"] for item in picked] == [2, 1, 0]


def test_debt_feedback_renders_without_family_vocabulary() -> None:
    from bestseller.services.conception import _render_debt_rewrite_feedback

    fb = _render_debt_rewrite_feedback(is_en=False)
    assert "必须重写" in fb
    # 种词铁律：反馈不得携带族内词汇
    for token in ("债", "账", "灵堂", "棺", "丧", "寿"):
        assert token not in fb, token


# ── 2026-08-14 真机误杀：门不该杀书，阈值不该对长文本失明 ──────────────────


def test_incidental_family_word_in_a_long_blob_is_not_dominated() -> None:
    """被误杀的真实冠军：弃婴测灵根，通篇只偶尔出现一次「旧账」。

    旧规则「全族合计≥3 次」对 finalize 那种 premise+synopsis+金手指拼起来的
    长 blob 近乎恒真——绝对计数对变长文本量级失明。
    """

    long_blob = "猎户村弃婴测灵根散脉指骨认主，梦里女人替他挡刀。" * 80 + "旧账，旧账，旧账。"
    assert not is_debt_dominated(long_blob)


def test_density_threshold_is_calibrated_above_human_board_corpus() -> None:
    """阈值由 90 本榜单简介标定：中位0.00/p90 2.02/p95 6.49。
    拍脑袋的 2.0 会误伤 9/90 本真在榜书，故取 8.0。"""

    from bestseller.services import anti_default_motif as M

    assert M._DEBT_DENSITY_PER_1K >= 6.5
    assert M._DEBT_DENSITY_MIN_CHARS >= 100


def test_default_family_never_kills_the_book() -> None:
    """8·2 母题警察的死因就是硬杀书；靶向复活只许挣一次重生。

    ``detected`` 会变成 ``_detected_concept_guard`` 并 raise
    ConceptContractError（整本书死在构思阶段）——debt_hit 不得进入该列表。
    """

    import inspect
    import re as _re

    from bestseller.services import conception

    src = inspect.getsource(conception)
    block = src[src.index("detected: list[str] = []"):]
    block = block[: block.index("_detected_concept_guard = tuple")]
    # debt_hit 可以出现在注释里解释原因，但不得有 `if debt_hit:` 的追加分支
    assert not _re.search(r"if debt_hit:\s*\n\s*detected\.append", block)


def test_default_family_winner_rejection_stops_at_the_last_attempt() -> None:
    """默认族只在还有重试机会时作废冠军（2026-08-14 真机）。

    最后一轮仍命中就带案底发货：否则 winner=None 会掉进
    「N 轮均未产出合格冠军」的 ConceptContractError，把整本书打死——
    用户创意种子为空时这条路径必然触发。
    """

    import inspect

    from bestseller.services import conception

    src = inspect.getsource(conception)
    idx = src.index('_pollution_reasons.append("UNREQUESTED_DEFAULT_MOTIF_A")')
    window = src[max(0, idx - 900) : idx]
    assert "_is_last_concept_attempt" in window
    assert "default_family_winner_advisory" in window


def test_rejected_default_family_champion_is_kept_as_fallback() -> None:
    """2026-08-14 真机死书链：第1轮有冠军→被默认族(品味门)拒→第2轮候选全挂
    在钩子硬门→「2轮均未产出合格冠军」→raise→整本书死。

    被口味门拒掉的冠军是完整可用产物，必须留作兜底：后续轮次颗粒无收时
    带案底发货，绝不因一项偏好杀书。
    """

    import inspect

    from bestseller.services import conception

    src = inspect.getsource(conception)
    # 拒绝时入栈
    assert "_default_family_fallback_winner = _ct_result.winner" in src
    # 无冠军时优先兜底，而不是直接落到 raise 分支
    assert "elif _default_family_fallback_winner is not None:" in src
    fallback_idx = src.index("elif _default_family_fallback_winner is not None:")
    raise_idx = src.index("概念淘汰赛 ")
    assert fallback_idx < raise_idx, "兜底分支必须在硬失败分支之前"


def test_retry_refinement_seed_is_quota_capped() -> None:
    """2026-08-14 真机：重试轮 6 个候选里 4 个是同一个故事的复述，
    6/6 挂在新颖度 → 无冠军 → 整本书死。

    根因是两条指令自相矛盾：seed 块要求「禁止沿用身份/骨架」，重试反馈却说
    「保留其故事身份，不要另起炉灶」。近失补强要留（饥饿悖论），但必须限额，
    其余名额换骨架。
    """

    import inspect

    from bestseller.services import conception

    src = inspect.getsource(conception)
    assert "定向补强·限额" in src
    assert "只用 2 个名额" in src
    assert "其余名额必须是完全不同的故事" in src
    # 旧的整池锚定措辞不得复活
    assert "不要另起炉灶。\"\n" not in src or "限额" in src
