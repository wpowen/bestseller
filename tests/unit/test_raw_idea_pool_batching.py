"""原始创意池的采样结构（2026-08-10）。

跨书「殡葬/阴间」同质化被反复治理多次仍复发。历次治理都在改 prompt 里的**词**
（删禁令 / 加正向锚 / 换措辞），而根因在**采样结构**：

    多样性是集合层面的属性。一次调用只生成一个创意，就没有集合可言——
    N 次独立抽样必然收敛到该题材分布的众数，玄幻的众数就是殡葬/阴间。

单变量实测（东方玄幻/男频/light，其余与生产逐字一致，每格 40-48 样本）：

    无 focus，每次 1 个   → 死亡族 60.4%
    无 focus，一次 12 个  →          7.5%   ← 唯一变量是批量，差 8 倍
    有 focus，每次 1 个   →         41.7%   （focus 只是批量的弱替代品）
    无 focus，一次  8 个  →         20.0%
    有 focus，一次  8 个  →         36.4%   （批量时再加 focus 反而更差）

所以：整池一次生成；focus 只在退化成「每次一个」时才用；批量偶发短产
（实测 83% 产出率，6 轮里有 1 轮空回）由补齐兜底消化，不牺牲原先 100% 的产出率。
"""

from __future__ import annotations

import json

import pytest

from bestseller.services.concept_tournament import (
    _build_raw_idea_pool_messages,
    _build_raw_idea_rank_messages,
    load_concept_tournament_config,
    run_concept_tournament,
)

pytestmark = pytest.mark.unit


def _pool_reply(n: int, tag: str = "创意") -> str:
    return json.dumps(
        {"ideas": [{"lane": "纯题材直觉", "seed": f"{tag}{i}号：一个具体的人遇到异常处境"}
                   for i in range(n)]},
        ensure_ascii=False,
    )


# ── 出厂配置：整池一次生成 ────────────────────────────────────────────────


def test_shipped_config_generates_the_pool_in_one_batch() -> None:
    cfg = load_concept_tournament_config()
    pool_count = 4 * int(cfg["raw_idea_pool_multiplier"])
    assert int(cfg["raw_idea_generation_batch_size"]) >= pool_count, (
        "整池必须一次生成；退回逐个生成会重新触发众数塌缩"
    )
    assert int(cfg["raw_idea_pool_max_tokens"]) >= 6000, (
        "一次 12 条完整 pitch 需要预算；3500 会把池截成一半"
    )
    assert int(cfg["raw_idea_pool_topup_calls"]) >= 1, "短产必须能补齐"


def test_batched_prompt_demands_an_internally_distinct_set() -> None:
    """批量生成时，那条多样性要求才真正有约束对象。"""

    system, user = _build_raw_idea_pool_messages(
        genre="东方玄幻", sub_genre="东方玄幻", count=12,
        audience_orientation="男频", prompt_arm="author_pitch",
    )
    assert "彼此不同的生活场域" in system
    assert "12" in user


# ── focus 只服务于退化情形 ────────────────────────────────────────────────


def test_focus_hint_applies_only_when_generating_one_at_a_time() -> None:
    _, batched = _build_raw_idea_pool_messages(
        genre="东方玄幻", sub_genre="东方玄幻", count=8,
        focus_hint="人物关系、身份错位与私人欲望", prompt_arm="author_pitch",
    )
    _, single = _build_raw_idea_pool_messages(
        genre="东方玄幻", sub_genre="东方玄幻", count=1,
        focus_hint="人物关系、身份错位与私人欲望", prompt_arm="author_pitch",
    )
    # 构造层照传（是否使用由调用方按 batch_count 决定）
    assert "本批优先从" in batched
    assert "本批优先从" in single


@pytest.mark.asyncio
async def test_runner_drops_the_focus_when_the_pool_is_batched() -> None:
    prompts: list[str] = []

    async def generator(system: str, user: str):
        prompts.append(user)
        if '"ideas"' in user:
            return _pool_reply(6), None
        return json.dumps({"concept": "x"}, ensure_ascii=False), None

    await run_concept_tournament(
        None, None, genre="仙侠", sub_genre="古典仙侠", chapter_count=20,
        config={
            "enabled": True, "n_candidates": 3,
            "candidate_prompt_mode": "engine_first",
            "raw_idea_prompt_arm": "minimal",
            "raw_idea_pool_multiplier": 2,
            "raw_idea_generation_batch_size": 6,
            "raw_idea_batch_focuses": ["人物", "职业", "世界"],
            "raw_idea_pool_topup_calls": 0,
        },
        generator=generator,
    )
    pool_prompts = [p for p in prompts if '"ideas"' in p]
    assert pool_prompts, "至少要发生一次池生成"
    for prompt in pool_prompts:
        assert "本批优先从" not in prompt, "批量生成时不得把整池焊死在一个维度上"


# ── 补齐兜底：批量的代价不能是丢创意 ──────────────────────────────────────


@pytest.mark.asyncio
async def test_short_pool_is_topped_up_to_the_target() -> None:
    """真机实测批量产出率 83%，6 轮里有 1 轮空回。batch=1 时代是 100%，不能倒退。"""

    pool_calls = 0

    async def generator(system: str, user: str):
        nonlocal pool_calls
        if '"ideas"' in user:
            pool_calls += 1
            # 首次只给 2 条（欠 4 条），补齐调用再给足
            return _pool_reply(2 if pool_calls == 1 else 4, f"批{pool_calls}"), None
        return json.dumps({"concept": "x"}, ensure_ascii=False), None

    result = await run_concept_tournament(
        None, None, genre="仙侠", sub_genre="古典仙侠", chapter_count=20,
        config={
            "enabled": True, "n_candidates": 3,
            "candidate_prompt_mode": "engine_first",
            "raw_idea_prompt_arm": "minimal",
            "raw_idea_pool_multiplier": 2,
            "raw_idea_generation_batch_size": 6,
            "raw_idea_pool_topup_calls": 2,
        },
        generator=generator,
    )
    assert pool_calls >= 2, "池不足时必须补齐"
    assert len(result.raw_ideas) == 6, result.raw_ideas


@pytest.mark.asyncio
async def test_topup_stops_at_its_call_budget() -> None:
    """兜底不能变成无限循环——模型持续空回时必须收手。"""

    pool_calls = 0

    async def generator(system: str, user: str):
        nonlocal pool_calls
        if '"ideas"' in user:
            pool_calls += 1
            return _pool_reply(1, f"批{pool_calls}"), None
        return json.dumps({"concept": "x"}, ensure_ascii=False), None

    await run_concept_tournament(
        None, None, genre="仙侠", sub_genre="古典仙侠", chapter_count=20,
        config={
            "enabled": True, "n_candidates": 3,
            "candidate_prompt_mode": "engine_first",
            "raw_idea_prompt_arm": "minimal",
            "raw_idea_pool_multiplier": 2,
            "raw_idea_generation_batch_size": 6,
            "raw_idea_pool_topup_calls": 2,
        },
        generator=generator,
    )
    assert pool_calls == 3, f"1 次主调用 + 最多 2 次补齐，实得 {pool_calls}"


def test_pool_prompt_selects_for_desire_not_curiosity() -> None:
    """2026-08-11 用户逐条终审：6 条创意 4 条被毙，共同结构=反讽/规则句/被动主角。

    百本榜单钩子分类的结论相同——头部钩子全是渴望引擎（想看他赢/翻身/兑现/清算），
    没有一个止于好奇或反讽。旧要求「让人想追问」在选文学杂志式处境；本测试钉住
    欲望钩三铁律在生产臂（author_pitch）里，且判官的 click_seed 同步改判渴望。
    """

    _, user = _build_raw_idea_pool_messages(
        genre="东方玄幻", sub_genre="东方玄幻", count=12,
        audience_orientation="男频", prompt_arm="author_pitch",
    )
    for token in ("欲望钩", "主角主动", "不写机制句"):
        assert token in user, token
    assert "让人立刻想追问" not in user

    _, rank_user = _build_raw_idea_rank_messages(
        genre="东方玄幻", sub_genre="东方玄幻",
        ideas=[("纯题材直觉", "一个胚子")], audience_orientation="男频",
    )
    assert "赢什么/翻什么身/兑现什么" in rank_user
    assert "对称机制条款" in rank_user


def test_pool_prompt_demands_events_mechanisms_and_desire_spread() -> None:
    """2026-08-12 四批终审：铁律四事件先行/铁律五机制可信/整批欲望形态多样性。

    悬疑池曾整批押在同一种欲望形态上（同一种事故+同一种追查），玄幻池产出
    『假招其实是绝学』式断言反转。约束全部类别级+正向列举（种词铁律）。
    """

    _, user = _build_raw_idea_pool_messages(
        genre="悬疑灵异", sub_genre="民俗怪谈", count=12,
        audience_orientation="男频", prompt_arm="author_pitch",
    )
    assert "事件先行" in user
    assert "机制可信" in user
    assert "欲望形态必须彼此不同" in user


def test_pool_prompt_spreads_anomaly_sources_for_suspense() -> None:
    """2026-08-12 真机定罪：悬疑池两轮 5/5 候选挤在同一内容族。
    纯正向类别令（不点族名 token）强制异常来源彼此不同。"""

    _, user = _build_raw_idea_pool_messages(
        genre="悬疑灵异", sub_genre="民俗怪谈", count=12,
        audience_orientation="男频", prompt_arm="author_pitch",
    )
    assert "异常来源也必须彼此不同" in user
    # 种词检查：定罪内容族的 token 不许进生成 prompt
    for token in ("尸体", "丧葬", "殡", "棺材", "出殡", "死人"):
        assert token not in user, f"种词泄漏: {token}"


def test_seed_is_a_compass_not_a_template() -> None:
    """2026-08-12 四臂对照定案：旧措辞让 seed 句法被复印成整批骨架
    （同 seed 两臂 24/24 克隆，无 seed 立刻四条四骨架）。"""

    _, user = _build_raw_idea_pool_messages(
        genre="悬疑灵异", sub_genre="民俗怪谈", count=12,
        audience_orientation="男频", seed_concept="某个方向想法",
        prompt_arm="author_pitch",
    )
    assert "禁止沿用它的句式" in user
    assert "保留其职业或核心发现" not in user
