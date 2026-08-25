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


def test_pool_prompt_v2_slims_rulebook_and_splits_fields() -> None:
    """2026-08-24 提示词工程重构（docs/一句话创意提示词工程分析-20260824.md）。

    ① 判据双载修复：生成端只留三条铁律，规则书归判官；② 单句槽位超载修复：
    机制因果义务从 seed 移入 graft（含因果桥第二句）；③ 幸存者模式降级：
    嫁接是默认做法带逃生门，不再 12/12 强制；④ 宁缺毋滥替代硬凑（短产
    topup 兜底本来就在）；⑤ 欲望形态「至多两次」解 12 条 vs 8 形态死结。
    """

    _, user = _build_raw_idea_pool_messages(
        genre="玄幻", sub_genre="东方玄幻", count=12,
        audience_orientation="男频", prompt_arm="author_pitch",
    )
    assert "仅此三条" in user
    assert user.count("铁律") <= 5  # 三条铁律+「铁律（仅此三条…」头，不再七条
    assert "靠什么仍然成立" in user          # graft 因果桥义务
    assert "比嫁接更强的成立方式也可以" in user  # 嫁接逃生门
    assert "宁缺毋滥" in user
    assert "至多出现两次" in user
    assert "字段分工" in user                # seed 三槽，义务分字段
    assert "偏要如何』不算主动" in user       # 铁律二+四合并后的表面合规封堵


def test_pool_prompt_mystery_spread_is_genre_conditional() -> None:
    """悬疑异常来源段与悬念钩例外只在悬疑系题材渲染——纯玄幻里是死重。"""

    _, xuanhuan = _build_raw_idea_pool_messages(
        genre="玄幻", sub_genre="东方玄幻", count=12,
        audience_orientation="男频", prompt_arm="author_pitch",
    )
    assert "异常来源也必须彼此不同" not in xuanhuan
    assert "悬疑品类例外" not in xuanhuan

    _, mystery = _build_raw_idea_pool_messages(
        genre="悬疑灵异", sub_genre="民俗怪谈", count=12,
        audience_orientation="男频", prompt_arm="author_pitch",
    )
    assert "异常来源也必须彼此不同" in mystery
    assert "悬疑品类例外" in mystery


def test_graft_survives_parse_and_reaches_rank_judge() -> None:
    """机制义务搬进 graft 后，解析层与判官必须都看得到它——否则义务白分：
    判官只审 seed 就是在给刚被搬走义务的那个字段打分。"""

    from bestseller.services.concept_tournament import _parse_raw_idea_records

    raw = (
        '{"ideas":[{"lane":"职业处境","seed":"张三把矿卖了",'
        '"graft":"挖矿×修行：矿越挖越多所以越修越强",'
        '"opening":"开篇","why_it_keeps_moving":"动力",'
        '"future_situations":["a","b","c"]}]}'
    )
    records = _parse_raw_idea_records(raw, limit=12)
    assert records and records[0]["graft"].startswith("挖矿×修行")

    _, rank_user = _build_raw_idea_rank_messages(
        genre="玄幻", sub_genre="东方玄幻",
        ideas=[("职业处境", "张三把矿卖了")],
        audience_orientation="男频",
        pitch_by_seed={"张三把矿卖了": records[0]},
    )
    assert "挖矿×修行" in rank_user
    assert "graft 与 seed 对不上" in rank_user
