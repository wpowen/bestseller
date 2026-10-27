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

import bestseller.services.concept_tournament as tournament
from bestseller.services.concept_tournament import (
    _build_raw_idea_pool_messages,
    _build_raw_idea_rank_messages,
    _ensure_authoritative_seed_in_raw_pool,
    _parse_raw_idea_ranking,
    _select_raw_ideas_for_expansion,
    load_concept_tournament_config,
    run_concept_tournament,
)

pytestmark = pytest.mark.unit


def _pool_reply(n: int, tag: str = "创意") -> str:
    return json.dumps(
        {"ideas": [{
            "lane": "纯题材直觉",
            "seed": f"{tag}{i}号：一个具体的人遇到异常处境",
            "graft": "熟悉机制×意外场景：行动带来后果并受限",
            "opening": "主角在现场主动做出选择",
            "why_it_keeps_moving": "选择改变关系，迫使主角继续行动",
            "future_situations": ["现场", "谈判", "反制"],
        }
                   for i in range(n)]},
        ensure_ascii=False,
    )


def _pitch_reply(n: int, tag: str = "创意") -> str:
    return json.dumps(
        {
            "ideas": [
                {
                    "lane": "职业处境",
                    "seed": f"{tag}{i}号：主角主动接下异常工作并要保住它",
                    "graft": "熟悉行当×意外场景：行动带来可见后果并受限",
                    "opening": "主角在现场做出第一个选择",
                    "why_it_keeps_moving": "选择改变关系，迫使主角继续行动",
                    "future_situations": ["现场", "谈判", "反制"],
                }
                for i in range(n)
            ]
        },
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


def test_retry_feedback_reaches_the_raw_idea_pool() -> None:
    """A failed tournament must rebuild upstream ideas, not only rewrite hooks."""

    system, user = _build_raw_idea_pool_messages(
        genre="异世大陆",
        sub_genre="异世大陆",
        count=8,
        seed_concept="用户已给定的故事",
        authoritative_seed=True,
        prompt_arm="author_pitch",
        retry_feedback="长篇承载失败：动作家族同质",
    )

    prompt = system + user
    assert "长篇承载失败：动作家族同质" in prompt
    assert "重建故事发动机" in prompt


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


@pytest.mark.asyncio
async def test_requested_length_reaches_raw_generation_before_ranking() -> None:
    """The first author must know the real scope, including top-up calls."""
    prompts: list[str] = []

    async def generator(system: str, user: str):
        if '"ideas"' in user:
            prompts.append(system + user)
            return _pool_reply(1, f"batch{len(prompts)}"), None
        return json.dumps({"concept": "x"}), None

    await run_concept_tournament(
        None, None, genre="东方玄幻", sub_genre="东方玄幻", chapter_count=50,
        config={
            "enabled": True, "n_candidates": 2,
            "candidate_prompt_mode": "engine_first",
            "raw_idea_prompt_arm": "author_pitch",
            "raw_idea_pool_multiplier": 1,
            "raw_idea_generation_batch_size": 2,
            "raw_idea_pool_topup_calls": 1,
        },
        generator=generator,
    )
    assert len(prompts) == 2
    assert all("篇幅目标：50章" in prompt for prompt in prompts)
    assert all("开场问题已解决之后" in prompt for prompt in prompts)


@pytest.mark.parametrize("genre", ["东方玄幻", "现实", "言情"])
def test_story_growth_preserves_genre_causality_and_completed_opening(genre: str) -> None:
    system, user = _build_raw_idea_pool_messages(
        genre=genre, sub_genre=genre, count=3, chapter_count=50,
        prompt_arm="author_pitch",
    )
    assert "篇幅目标：50章" in system
    assert "东西只做它物理上真会做的事" not in user
    assert "现实题材则遵守现实条件" in user
    assert "不需要另勾脑洞增强器" in user
    assert "前一次行动留下什么变化" in user
    assert "不要求把故事无限拉长" in system


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
    for token in ("欲望钩", "可信主动性", "不写机制句"):
        assert token in user, token
    assert "让人立刻想追问" not in user

    _, rank_user = _build_raw_idea_rank_messages(
        genre="东方玄幻", sub_genre="东方玄幻",
        ideas=[("纯题材直觉", "一个胚子")], audience_orientation="男频",
    )
    assert "赢什么/翻什么身/兑现什么" in rank_user
    assert "不能因尚未获胜" in rank_user


def test_pool_prompt_demands_events_mechanisms_and_desire_spread() -> None:
    """2026-08-12 四批终审：铁律四事件先行/铁律五机制可信/整批欲望形态多样性。

    悬疑池曾整批押在同一种欲望形态上（同一种事故+同一种追查），玄幻池产出
    『假招其实是绝学』式断言反转。约束全部类别级+正向列举（种词铁律）。
    """

    _, user = _build_raw_idea_pool_messages(
        genre="悬疑灵异", sub_genre="民俗怪谈", count=12,
        audience_orientation="男频", prompt_arm="author_pitch",
    )
    assert "选择与尚未解决的事" in user
    assert "具体后果及其成立条件" in user
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
    assert "嫁接只是一种可选做法" in user  # 不再将嫁接设为默认故事结构
    assert "宁缺毋滥" in user
    assert "至多出现两次" in user
    assert "字段分工" in user                # seed 三槽，义务分字段
    assert "被动遭遇可以作为起点" in user       # 铁律二+四合并后的表面合规封堵


@pytest.mark.asyncio
async def test_active_engine_first_prompt_builders_put_story_before_structure() -> None:
    """The shipped engine_first path must generate story material before its schema evidence."""

    prompts: list[str] = []
    kernel = {
        "protagonist_identity": "矿奴陆衡",
        "protagonist_private_desire": "带工友活着离开矿坑",
        "protagonist_flaw": "只相信自己看见的路线",
        "core_abnormality": "能看见尚未成形的灵脉走向",
        "current_goal": "在封矿前凿出唯一生路",
        "effective_resistance": "宗门要封矿活埋工友",
        "failure_cost": "工友会被活埋",
        "success_cost": "烧毁卖身契后被追捕",
        "irreversible_change": "工友跟随陆衡离开矿坑",
        "reader_promise": "看底层矿奴靠判断带人破局",
        "difference_point": "灵脉尚未成形时就能被看见",
        "deformable_loop": "辨认灵脉、选择路线、应对封堵",
        "opening_crisis": "宗门要封矿活埋工友",
        "emotional_promise": "陆衡带工友抢出生路",
        "scene_seeds": ["看见断脉", "抢挖生路", "应对封矿", "保护工友", "进入古战场"],
        "post_reveal_scene_seeds": ["各宗争夺", "路线反制", "公开选择"],
        "expansion_axes": ["矿脉路线", "工友队伍", "宗门争夺"],
        "opposing_ecology": ["封矿宗门", "争夺入口的各宗"],
    }

    async def generator(system: str, user: str):
        del system
        prompts.append(user)
        if '"ideas"' in user:
            return _pitch_reply(2), None
        if "PREMISE_CARD" in user:
            return json.dumps(kernel, ensure_ascii=False), None
        if "HOOK_DISTILL" in user:
            return json.dumps(
                {"hooks": [{"angle": "action", "concept": "陆衡带工友凿出生路"}]},
                ensure_ascii=False,
            ), None
        return "{}", None

    async def premise_judge(system: str, user: str):
        del system
        prompts.append(user)
        return json.dumps(
            {
                "seed_fidelity": 9,
                "freshness": 9,
                "click_seed": 9,
                "action_conflict": 9,
                "reader_promise": 9,
                "character_choice": 9,
                "scene_generation": 9,
                "promise_survival": 9,
                "deformable_loop": 9,
                "post_reveal_engine": 9,
                "genre_fidelity": 9,
            },
            ensure_ascii=False,
        ), None

    await run_concept_tournament(
        None,
        None,
        genre="仙侠",
        sub_genre="古典仙侠",
        chapter_count=20,
        config={
            "enabled": True,
            "n_candidates": 2,
            "candidate_prompt_mode": "engine_first",
            "raw_idea_prompt_arm": "author_pitch",
            "raw_idea_pool_multiplier": 1,
            "raw_idea_generation_batch_size": 2,
            "raw_idea_pool_topup_calls": 0,
            "story_architecture_contract_enabled": False,
            "premise_card_min_survivors": 0,
        },
        generator=generator,
        premise_judge=premise_judge,
    )

    engine_prompts = [prompt for prompt in prompts if "PREMISE_CARD" in prompt]
    hook_prompts = [prompt for prompt in prompts if "HOOK_DISTILL" in prompt]
    assert engine_prompts and hook_prompts, prompts
    engine_prompt = engine_prompts[0]
    assert "先把故事讲成立，再提取结构证据" in engine_prompt
    assert "结构证据" in engine_prompt
    assert "四个 causal_units" not in engine_prompt
    assert "成长轨1" not in engine_prompt
    assert "counter 可留空" in engine_prompt
    assert "不要强行跨题材嫁接" in prompts[0]
    assert "先把故事讲成立" in prompts[0]
    assert "人物目标、行动与回报" in hook_prompts[0]
    assert '"angle":"identity"' not in hook_prompts[0]
    assert '"angle":"action"' not in hook_prompts[0]
    assert '"angle":"conflict"' not in hook_prompts[0]


@pytest.mark.parametrize(
    ("genre", "sub_genre"),
    [("悬疑灵异", "侦探推理"), ("Mystery", "Mystery"), ("Mystery", "")],
)
def test_detective_pool_uses_case_promise_without_horror_sources(genre, sub_genre) -> None:
    system, user = _build_raw_idea_pool_messages(
        genre=genre, sub_genre=sub_genre, count=12, prompt_arm="author_pitch",
    )
    assert "侦探/推理题材适配" in system
    assert "案件入口与关键证据来源" in user
    assert "恐怖可以来自任何日常" not in system + user
    assert "悬疑/惊悚题材适配" not in system


def test_selected_horror_subgenre_keeps_its_native_promise() -> None:
    system, user = _build_raw_idea_pool_messages(
        genre="悬疑推理", sub_genre="民俗怪谈", count=12, prompt_arm="author_pitch",
    )
    assert "悬疑/惊悚题材适配" in system
    assert "恐怖可以来自任何日常" in user
    assert "侦探/推理题材适配" not in system


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


def test_pool_prompt_uses_native_commitment_for_fantasy_instead_of_realistic_collapse() -> None:
    """幻想池必须露出可行动的超凡承诺，不能只换一层仙侠名词。"""

    fantasy_system, fantasy_user = _build_raw_idea_pool_messages(
        genre="东方玄幻", sub_genre="古典仙侠", count=12, prompt_arm="author_pitch"
    )
    realistic_system, realistic_user = _build_raw_idea_pool_messages(
        genre="现实", sub_genre="生活纪实", count=12, prompt_arm="author_pitch"
    )
    fantasy = fantasy_system + fantasy_user
    realistic = realistic_system + realistic_user
    assert "幻想/武侠题材适配" in fantasy
    assert "修炼或技艺成长" in fantasy
    assert "幻想感必须成为冲突和选择的原因" in fantasy
    assert "幻想/武侠题材适配" not in realistic
    assert "现实题材适配" in realistic


def test_pool_prompt_preserves_explicit_urban_psionic_contract() -> None:
    """都市异能不能被「都市」关键词误路由到普通现实分支。"""

    system, user = _build_raw_idea_pool_messages(
        genre="都市", sub_genre="都市异能", count=12, prompt_arm="author_pitch"
    )
    prompt = system + user
    assert "都市异能题材适配" in prompt
    assert "主动运用、隐藏、交换或承担能力后果" in prompt
    assert "不能靠凭空超能力" not in prompt

    canonical_system, canonical_user = _build_raw_idea_pool_messages(
        genre="urban-psionic", sub_genre="urban-psionic", count=12, prompt_arm="author_pitch"
    )
    assert "都市异能题材适配" in canonical_system + canonical_user


def test_seed_field_forbids_author_self_evaluation_and_allows_non_commercial_desires() -> None:
    """seed 记录人物目标，不替作者写「读者会爽/想看」的宣传判断。"""

    _, user = _build_raw_idea_pool_messages(
        genre="东方玄幻", sub_genre="古典仙侠", count=12, prompt_arm="author_pitch"
    )
    assert "禁止写‘读者会想看’‘很爽’‘很吸引人’等作者自评" in user
    assert "人物要争取或守住的东西必须具体" in user
    assert "seed 只陈述故事事实和人物欲望" in user


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


def test_truncated_pitch_does_not_count_as_a_valid_raw_idea() -> None:
    """JSON repair may recover a seed while the required pitch is truncated."""

    from bestseller.services.concept_tournament import _parse_raw_idea_records

    stats: dict[str, int] = {}
    records = _parse_raw_idea_records(
        '{"ideas":[{"lane":"职业处境","seed":"只剩一句"',
        limit=12,
        require_pitch_fields=True,
        stats=stats,
    )
    assert records == []
    assert stats == {"parsed": 1, "invalid": 1}


def test_raw_pool_parser_counts_malformed_rows_and_rejects_non_string_fields() -> None:
    from bestseller.services.concept_tournament import _parse_raw_idea_records

    stats: dict[str, int] = {}
    records = _parse_raw_idea_records(
        json.dumps({
            "ideas": [
                None,
                {"seed": ""},
                {"seed": {"text": "伪字符串"}},
                {"seed": "完整", "graft": ["错误类型"]},
            ]
        }, ensure_ascii=False),
        limit=12,
        require_pitch_fields=True,
        stats=stats,
    )
    assert records == []
    assert stats == {"parsed": 4, "invalid": 4}


@pytest.mark.asyncio
async def test_exhausted_short_pool_is_reported_and_not_promoted() -> None:
    """Two repaired candidates must not be presented as a complete six-item pool."""

    pool_calls = 0

    async def generator(system: str, user: str):
        nonlocal pool_calls
        if '"ideas"' in user:
            pool_calls += 1
            # The retry repeats the first two ideas, so dedupe still leaves a
            # short pool after the bounded top-up budget is exhausted.
            return _pitch_reply(2, "固定"), None
        raise AssertionError("incomplete raw pool must not reach card expansion")

    result = await run_concept_tournament(
        None,
        None,
        genre="现实",
        sub_genre="生活纪实",
        chapter_count=20,
        config={
            "enabled": True,
            "n_candidates": 3,
            "candidate_prompt_mode": "engine_first",
            "raw_idea_prompt_arm": "author_pitch",
            "raw_idea_pool_multiplier": 2,
            "raw_idea_generation_batch_size": 6,
            "raw_idea_pool_topup_calls": 1,
        },
        generator=generator,
    )
    assert pool_calls == 2
    assert result.raw_ideas == []
    assert result.raw_idea_pool_stats == {
        "target": 6,
        "contract_enforced": True,
        "requested": 10,
        "parsed": 4,
        "valid": 4,
        "invalid": 0,
        "deduplicated": 2,
        "topup_calls": 1,
        "complete": False,
        "failure": "raw_idea_pool_incomplete:2/6",
    }
    assert result.engine_rejections[-1]["failed_axes"] == ["raw_idea_pool_incomplete"]


@pytest.mark.asyncio
async def test_legacy_short_pool_receipt_is_truthful_without_strict_veto() -> None:
    async def generator(system: str, user: str):
        if '"ideas"' in user:
            return _pool_reply(1, "兼容"), None
        return json.dumps({"concept": "x"}, ensure_ascii=False), None

    result = await run_concept_tournament(
        None,
        None,
        genre="仙侠",
        sub_genre="古典仙侠",
        chapter_count=20,
        config={
            "enabled": True,
            "n_candidates": 2,
            "candidate_prompt_mode": "engine_first",
            "raw_idea_prompt_arm": "methodology",
            "raw_idea_pool_multiplier": 2,
            "raw_idea_generation_batch_size": 2,
            "raw_idea_pool_topup_calls": 0,
        },
        generator=generator,
    )
    assert result.raw_idea_pool_stats["contract_enforced"] is False
    assert result.raw_idea_pool_stats["complete"] is False
    assert result.raw_ideas


def test_raw_rank_authority_shows_full_seed_and_preserves_legacy_without_seed() -> None:
    _, anchored = _build_raw_idea_rank_messages(
        genre="东方玄幻",
        sub_genre="东方玄幻",
        ideas=[("切入角度", "陆衡改写同一矿脉能力")],
        authoritative_seed_concept="边城矿奴陆衡能看见尚未成形的灵脉走向，并烧毁卖身契带人凿穿古战场入口。",
    )
    _, legacy = _build_raw_idea_rank_messages(
        genre="东方玄幻",
        sub_genre="东方玄幻",
        ideas=[("切入角度", "一个故事")],
    )
    assert "边城矿奴陆衡能看见尚未成形的灵脉走向" in anchored
    assert "不得用新主角、新能力" in anchored
    assert "seed_fidelity" in anchored
    assert "【上位创意唯一事实锚点】" not in legacy


def test_authoritative_original_seed_is_an_extra_rank_control_row() -> None:
    parsed_pool = [
        ("变体", "换能力的漂移变体一"),
        ("变体", "换能力的漂移变体二"),
        ("变体", "换能力的漂移变体三"),
    ]
    pitches = {seed: {"lane": lane, "seed": seed} for lane, seed in parsed_pool}
    preserved, rejection = _ensure_authoritative_seed_in_raw_pool(
        parsed_pool,
        pitches,
        seed_concept="边城矿奴陆衡能看见灵脉走向，并烧毁卖身契。",
        authoritative_seed=True,
    )
    assert rejection is None
    assert len(preserved) == 4
    assert preserved[-1] == (
        "user_original",
        "边城矿奴陆衡能看见灵脉走向，并烧毁卖身契。",
    )
    assert pitches[preserved[-1][1]]["source"] == "user_original"
    _, rank_prompt = _build_raw_idea_rank_messages(
        genre="东方玄幻",
        sub_genre="东方玄幻",
        ideas=preserved,
        pitch_by_seed=pitches,
        authoritative_seed_concept=preserved[-1][1],
    )
    assert preserved[-1][1] in rank_prompt


def test_authoritative_control_row_keeps_hard_constraints_and_free_mode_unchanged() -> None:
    parsed_pool = [("变体", "一个干净变体")]
    pitches = {"一个干净变体": {"lane": "变体", "seed": "一个干净变体"}}
    free, free_rejection = _ensure_authoritative_seed_in_raw_pool(
        parsed_pool.copy(),
        dict(pitches),
        seed_concept="用户原始创意",
        authoritative_seed=False,
    )
    assert free == parsed_pool
    assert free_rejection is None

    rejected, rejection = _ensure_authoritative_seed_in_raw_pool(
        parsed_pool.copy(),
        dict(pitches),
        seed_concept="主角被迫成为人质，限期处刑，沉河等死。",
        authoritative_seed=True,
        tone_preference="light",
    )
    assert rejected == parsed_pool
    assert rejection is not None
    assert rejection["source"] == "user_original"
    assert rejection["failed_axes"] == ["creation_intent_pollution"]


def test_raw_identity_check_does_not_require_an_engine_only_field() -> None:
    seed = "少年陆衡，能看见未成形的灵脉。"
    candidate = tournament.ConceptCandidate(dimension="变体", concept=seed)
    args = dict(seed_concept=seed, authoritative_seed_concept=seed,
                tone_preference="", effect_skills=())
    assert tournament._candidate_hard_rejection_reason(candidate, **args, raw_idea=True) is None
    assert tournament._candidate_hard_rejection_reason(candidate, **args) is not None
    candidate = tournament.ConceptCandidate(
        dimension="变体", concept="少年李平，能看见未成形的灵脉。",
    )
    assert tournament._candidate_hard_rejection_reason(candidate, **args, raw_idea=True) is not None


def test_duplicate_control_uses_exact_user_text_without_generated_graft() -> None:
    seed = "少年陆衡，能看见未成形的灵脉。"
    variant = seed.replace("，", "， ")
    pitches = {variant: {"lane": "变体", "seed": variant, "graft": "获得第二种能力"}}
    pool, rejection = _ensure_authoritative_seed_in_raw_pool(
        [("变体", variant)], pitches, seed_concept=seed, authoritative_seed=True,
    )
    assert rejection is None
    assert pool == [("user_original", seed)]
    assert pitches == {seed: {"lane": "user_original", "seed": seed, "source": "user_original"}}


@pytest.mark.asyncio
async def test_runner_sends_original_control_row_to_rank_without_extra_generation_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rank_prompts: list[str] = []
    generation_calls = 0

    async def generator(system: str, user: str):
        nonlocal generation_calls
        del system
        if '"ideas"' in user:
            generation_calls += 1
            return _pool_reply(3, "漂移变体"), None
        return json.dumps({"concept": "一个候选"}, ensure_ascii=False), None

    async def fake_default_generator(_session, _settings, *, template, **kwargs):
        del kwargs

        async def call(_system: str, user: str):
            if template == "concept_tournament_raw_idea_rank":
                rank_prompts.append(user)
                scores = {
                    "freshness": 8,
                    "click_seed": 8,
                    "character_logic": 8,
                    "action_seed": 8,
                    "promise_survival": 8,
                    "genre_fidelity": 8,
                    "ai_assembly": 2,
                    "seed_fidelity": 8,
                    "seed_evidence": "陆衡能看见灵脉走向",
                    "candidate_evidence": "陆衡能看见灵脉走向",
                    "after_opening_promise": "继续夺回矿脉",
                    "action_families": ["凿矿", "谈判", "反制"],
                    "growth_surface": "矿脉与关系持续积累",
                }
                return json.dumps(
                    {"ranked": [{"index": index, **scores} for index in range(4)]},
                    ensure_ascii=False,
                ), None
            return json.dumps({"verdicts": []}, ensure_ascii=False), None

        return call

    monkeypatch.setattr(tournament, "_default_generator", fake_default_generator)
    cfg = {
        "enabled": True,
        "n_candidates": 3,
        "candidate_prompt_mode": "engine_first",
        "raw_idea_pool_multiplier": 1,
        "raw_idea_generation_batch_size": 3,
        "raw_idea_pool_topup_calls": 0,
        "raw_idea_judge_model_key": "test-ranker",
        "premise_judge_model_key": "test-premise",
        "raw_idea_rank_batch_size": 8,
        "premise_card_count": 1,
    }
    seed = "边城矿奴陆衡能看见灵脉走向，并烧毁卖身契。"
    authoritative = await run_concept_tournament(
        None,
        None,
        genre="东方玄幻",
        sub_genre="东方玄幻",
        chapter_count=20,
        config=cfg,
        generator=generator,
        seed_concept=seed,
        authoritative_seed=True,
    )
    calls_with_control = generation_calls
    assert rank_prompts and seed in rank_prompts[0]
    assert any(item.get("source") == "user_original" for item in authoritative.raw_ideas)

    rank_prompts.clear()
    baseline = await run_concept_tournament(
        None,
        None,
        genre="东方玄幻",
        sub_genre="东方玄幻",
        chapter_count=20,
        config=cfg,
        generator=generator,
        seed_concept=seed,
        authoritative_seed=False,
    )
    assert generation_calls == calls_with_control * 2
    assert rank_prompts and seed not in rank_prompts[0]
    assert not any(item.get("source") == "user_original" for item in baseline.raw_ideas)


def test_raw_rank_parser_marks_missing_fidelity_unknown_and_keeps_evidence() -> None:
    parsed = _parse_raw_idea_ranking(
        '{"ranked":[{"index":0,"freshness":8,"click_seed":8,"character_logic":8,'
        '"action_seed":8,"promise_survival":8,"genre_fidelity":8,"ai_assembly":2,'
        '"seed_fidelity":null,"seed_evidence":"unknown",'
        '"candidate_evidence":"unknown"}]}'
    )
    assert parsed[0]["seed_fidelity"] is None
    assert parsed[0]["seed_fidelity_known"] is False

    rejected = _parse_raw_idea_ranking(
        '{"ranked":[{"index":0,"freshness":true,"seed_fidelity":NaN}]}'
    )
    assert rejected == []


def test_unknown_authority_evidence_stays_expansion_eligible_but_is_not_a_pass() -> None:
    item = {
        "index": 0,
        "freshness": 10,
        "click_seed": 10,
        "character_logic": 10,
        "action_seed": 10,
        "promise_survival": 10,
        "genre_fidelity": 10,
        "ai_assembly": 0,
        "dumb_cost": False,
        "after_opening_promise": "同一危机持续升级",
        "action_families": ["调查", "谈判", "反制"],
        "growth_surface": "关系与资源持续积累",
        "seed_fidelity": None,
        "seed_evidence": "unknown",
        "candidate_evidence": "unknown",
    }
    selected = _select_raw_ideas_for_expansion(
        [item],
        raw_floor=7,
        progression_floor=5,
        limit=1,
        authoritative_seed_concept="边城矿奴陆衡能看见灵脉走向。",
        authority_candidate_by_index={0: "同一危机持续升级"},
    )
    assert [candidate["index"] for candidate in selected] == [0]
    assert selected[0]["authority_evidence_status"] == "unknown"
    assert selected[0]["authority_evidence_reason"] == "source_evidence_unknown"


def test_authority_fidelity_is_slot_priority_before_literary_score() -> None:
    common = {
        "character_logic": 8,
        "action_seed": 8,
        "ai_assembly": 2,
        "dumb_cost": False,
        "after_opening_promise": "主角持续夺回矿脉并改变工友处境",
        "action_families": ["凿矿", "谈判", "反制"],
        "growth_surface": "矿脉、工友关系和势力位置持续积累",
        "seed_evidence": "陆衡能看见灵脉走向",
        "candidate_evidence": "陆衡继续使用灵脉能力",
    }
    drift = {
        **common,
        "index": 0,
        "freshness": 10,
        "click_seed": 10,
        "promise_survival": 9,
        "genre_fidelity": 9,
        "seed_fidelity": 4,
    }
    loyal = {
        **common,
        "index": 1,
        "freshness": 7,
        "click_seed": 7,
        "promise_survival": 7,
        "genre_fidelity": 7,
        "seed_fidelity": 9,
    }
    selected = _select_raw_ideas_for_expansion(
        [drift, loyal],
        raw_floor=7,
        progression_floor=5,
        limit=1,
        authoritative_seed_concept="边城矿奴陆衡能看见灵脉走向。",
        authority_candidate_by_index={
            0: "陆衡继续使用灵脉能力，主角持续夺回矿脉并改变工友处境",
            1: "陆衡继续使用灵脉能力，主角持续夺回矿脉并改变工友处境",
        },
    )
    assert [item["index"] for item in selected] == [1]


def test_forged_quote_is_unknown_and_verified_low_fidelity_is_last() -> None:
    common = {
        "freshness": 9,
        "click_seed": 9,
        "character_logic": 9,
        "action_seed": 9,
        "promise_survival": 9,
        "genre_fidelity": 9,
        "ai_assembly": 2,
        "dumb_cost": False,
        "after_opening_promise": "同一危机持续升级",
        "action_families": ["调查", "谈判", "反制"],
        "growth_surface": "关系与资源持续积累",
    }
    verified_high = {
        **common,
        "index": 0,
        "seed_fidelity": 9,
        "seed_evidence": "陆衡能看见灵脉走向",
        "candidate_evidence": "陆衡继续使用灵脉能力",
    }
    forged = {
        **common,
        "index": 1,
        "seed_fidelity": 10,
        "seed_evidence": "这句不在上位seed",
        "candidate_evidence": "这句不在候选",
    }
    verified_low = {
        **common,
        "index": 2,
        "seed_fidelity": 3,
        "seed_evidence": "陆衡能看见灵脉走向",
        "candidate_evidence": "陆衡继续使用灵脉能力",
    }
    selected = _select_raw_ideas_for_expansion(
        [verified_low, forged, verified_high],
        raw_floor=7,
        progression_floor=5,
        limit=3,
        authoritative_seed_concept="边城矿奴陆衡能看见灵脉走向。",
        authority_candidate_by_index={
            0: "陆衡继续使用灵脉能力",
            1: "另一个候选",
            2: "陆衡继续使用灵脉能力",
        },
    )
    assert [item["index"] for item in selected] == [0, 1, 2]


@pytest.mark.parametrize('count', [1, 2, 5])
def test_topup_prompt_minimum_never_exceeds_requested_count(count: int) -> None:
    _, user = _build_raw_idea_pool_messages(
        genre='现实', sub_genre='现实百态', count=count, prompt_arm='author_pitch')
    assert f'不得少于{count}条' in user
    assert '不得少于8条' not in user


def test_story_causality_does_not_require_graft_or_power_growth() -> None:
    _, user = _build_raw_idea_pool_messages(
        genre='现实', sub_genre='现实百态', count=12, prompt_arm='author_pitch')
    assert '所以越做越强' not in user
    assert '新鲜感的默认做法是嫁接' not in user
