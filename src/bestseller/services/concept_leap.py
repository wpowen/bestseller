"""Concept Leap Generator — force cross-domain mashups.

Pipeline:
    pools (≥ 2 disjoint domain pools)
        -> draw one seed from each
        -> score combination on (novelty, coherence, anti-saturation)
        -> repeat N times
        -> rank, return top K candidates

The default ``DEFAULT_CONCEPT_POOLS`` catalogue covers the eight domain
buckets that have historically powered top-tier Chinese serialized fiction
mashups. Callers can override with their own pools (e.g. project-specific
catalogue) by passing ``pools=`` to ``generate_concept_leap``.

Determinism: every run with the same ``seed`` produces the same ranked
output. This lets the conception pipeline cache and reproduce premise
candidates, and lets tests assert exact orderings.
"""

from __future__ import annotations

import logging
import random
from collections.abc import Mapping, Sequence
from typing import Any

from bestseller.domain.concept_leap import (
    ConceptCandidate,
    ConceptLeapResult,
    ConceptPool,
    ConceptSeed,
)

logger = logging.getLogger(__name__)


# ---------- the default eight-pool catalogue ----------
#
# Each seed has a saturation_score in 0..1 representing how flooded that
# concept is in current bestseller lists (1 =审美疲劳, 0 = fresh).
# The generator penalizes high-saturation seeds in the final ranking.
#
# Saturation is a moving signal — for a real deployment this should be
# kept in a YAML/DB and updated from Fanqie market data. The values
# below are reasonable defaults as of the framework's authoring window
# and should be refreshed periodically.


def _seed(key: str, label: str, *, saturation: float = 0.3, **kwargs: Any) -> ConceptSeed:
    return ConceptSeed(key=key, label=label, saturation_score=saturation, **kwargs)


_MYTHOLOGY_POOL = ConceptPool(
    name="mythology",
    label="神话/民俗池",
    description="中外神话、宗教、民俗、玄怪的根性素材。",
    seeds=[
        _seed("kunlun", "昆仑山系神话", saturation=0.55),
        _seed("shanhai", "山海经/异兽志怪", saturation=0.50),
        _seed("cthulhu", "克苏鲁/古旧者神话", saturation=0.45),
        _seed("norse", "北欧神话/世界树", saturation=0.40),
        _seed("egypt", "古埃及/亡灵神话", saturation=0.35),
        _seed("tibetan_bardo", "藏传中阴/度亡", saturation=0.15),
        _seed("yokai", "日本妖怪百物语", saturation=0.30),
        _seed("river_lords", "中国民间河神/城隍", saturation=0.20),
    ],
)

_SCIENCE_POOL = ConceptPool(
    name="science",
    label="现代科学/技术池",
    description="物理、生物、信息、神经科学的硬概念。",
    seeds=[
        _seed("quantum_observer", "量子观察者效应", saturation=0.40),
        _seed("crispr", "基因编辑/CRISPR", saturation=0.30),
        _seed("neural_dust", "脑机接口/神经尘埃", saturation=0.25),
        _seed("p_vs_np", "P vs NP / 算法困局", saturation=0.10),
        _seed("dark_forest", "宇宙黑暗森林", saturation=0.65),
        _seed("entropy_arrow", "熵增/时间箭头", saturation=0.25),
        _seed("gene_drive", "基因驱动/物种工程", saturation=0.10),
        _seed("foldit", "蛋白折叠/合成生物", saturation=0.10),
    ],
)

_SUBCULTURE_POOL = ConceptPool(
    name="subculture",
    label="亚文化/边缘领域池",
    description="小众但有人群基础的圈层素材。",
    seeds=[
        _seed("competitive_eating", "竞技吃播/职业大胃王", saturation=0.10),
        _seed("e_sports_governance", "电竞俱乐部内政", saturation=0.20),
        _seed("rave_culture", "电子音乐/Rave 场域", saturation=0.10),
        _seed("perfumery", "调香师/嗅觉档案", saturation=0.15),
        _seed("urban_explore", "城市探险/废墟摄影", saturation=0.15),
        _seed("speedrunning", "速通竞速/任天堂世代", saturation=0.10),
        _seed("street_culture", "街头滑板/Skate文化", saturation=0.10),
        _seed("conlang", "构造语言/虚构文字学", saturation=0.05),
    ],
)

_HISTORY_POOL = ConceptPool(
    name="history",
    label="历史/冷知识池",
    description="不入主流教材但有戏剧能量的历史片段。",
    seeds=[
        _seed("song_jiao_fang", "宋代教坊司/官伎制", saturation=0.10),
        _seed("ming_dongchang", "明代东厂/锦衣卫", saturation=0.55),
        _seed("yuan_zhanchi", "元代占赤民族迁徙", saturation=0.05),
        _seed("tang_shaman", "唐代萨满/巫祝", saturation=0.10),
        _seed("byzantine_intrigue", "拜占庭宫廷政变", saturation=0.10),
        _seed("medieval_guild", "中世纪行会/秘传工艺", saturation=0.20),
        _seed("qing_yangwu", "晚清洋务局", saturation=0.10),
        _seed("renaissance_assassins", "文艺复兴雇佣刺客", saturation=0.15),
    ],
)

_STRUCTURE_POOL = ConceptPool(
    name="structure",
    label="叙事/结构池",
    description="情节骨架与叙事结构母题。",
    seeds=[
        _seed("case_of_the_week", "单元案推主线", saturation=0.50),
        _seed("vault_heist", "盗匣/团队劫案", saturation=0.30),
        _seed("forbidden_tournament", "禁忌大赛/年度淘汰", saturation=0.60),
        _seed("ledger_of_souls", "因果账本/善恶薄", saturation=0.20),
        _seed("identity_swap", "身份调换/双重生活", saturation=0.30),
        _seed("memory_palace", "记忆宫殿/碎片回溯", saturation=0.15),
        _seed("oracle_pact", "占卜契约/预言代价", saturation=0.20),
        _seed("succession_war", "继承战争/家族博弈", saturation=0.35),
    ],
)

_ECONOMICS_POOL = ConceptPool(
    name="economics",
    label="经济/制度池",
    description="货币、贸易、权力分配的非显性主题。",
    seeds=[
        _seed("hard_currency", "硬通货/金本位崩溃", saturation=0.10),
        _seed("guild_monopoly", "行会垄断/卡特尔", saturation=0.10),
        _seed("information_arbitrage", "信息套利/前瞻交易", saturation=0.10),
        _seed("rentier_state", "食利者国家", saturation=0.05),
        _seed("debt_bondage", "债务束缚/身份典押", saturation=0.10),
        _seed("crypto_dao", "加密 DAO/去中心治理", saturation=0.15),
        _seed("private_court", "私设法庭/民间裁判", saturation=0.10),
        _seed("salt_iron_monopoly", "盐铁专卖/国家货殖", saturation=0.05),
    ],
)

_EMOTION_POOL = ConceptPool(
    name="emotion",
    label="情感/关系池",
    description="情感张力来源 — 是否上头取决于此。",
    seeds=[
        _seed("master_apprentice", "师徒缘灭", saturation=0.30),
        _seed("sworn_enemies_lovers", "宿敌/相爱相杀", saturation=0.55),
        _seed("loyal_betrayal", "忠诚之下的背叛", saturation=0.30),
        _seed("redemption_for_one", "为一人赎罪", saturation=0.25),
        _seed("found_family", "拼凑式家人", saturation=0.30),
        _seed("rival_sibling", "兄妹/姐弟天命对峙", saturation=0.20),
        _seed("dying_for_promise", "为一句承诺赴死", saturation=0.20),
        _seed("unrequited_devotion", "单向深情", saturation=0.30),
    ],
)

_SPATIAL_POOL = ConceptPool(
    name="spatial",
    label="空间/场域池",
    description="独特物理或概念空间，能成为故事的'地理性主角'。",
    seeds=[
        _seed("vertical_city", "垂直城市/塔层社会", saturation=0.20),
        _seed("seasonal_kingdom", "随季节迁移的王国", saturation=0.05),
        _seed("underwater_court", "海底朝廷/水下文明", saturation=0.10),
        _seed("mobile_school", "迁徙学府/移动学院", saturation=0.10),
        _seed("clock_tower_world", "钟楼内嵌世界", saturation=0.05),
        _seed("border_market", "边境黑市/灰色地带", saturation=0.20),
        _seed("library_labyrinth", "图书馆迷宫/活态书库", saturation=0.10),
        _seed("dream_archipelago", "梦境群岛/集体潜识场", saturation=0.10),
    ],
)


DEFAULT_CONCEPT_POOLS: tuple[ConceptPool, ...] = (
    _MYTHOLOGY_POOL,
    _SCIENCE_POOL,
    _SUBCULTURE_POOL,
    _HISTORY_POOL,
    _STRUCTURE_POOL,
    _ECONOMICS_POOL,
    _EMOTION_POOL,
    _SPATIAL_POOL,
)


# ---------- generator ----------


def generate_concept_leap(
    *,
    pools: Sequence[ConceptPool] | None = None,
    pool_names: Sequence[str] | None = None,
    pools_per_candidate: int = 4,
    sample_size: int = 60,
    top_k: int = 5,
    seed: int | None = None,
    forbidden_seed_keys: Sequence[str] = (),
    saturation_cutoff: float = 0.7,
) -> ConceptLeapResult:
    """Generate ranked cross-domain concept candidates.

    Parameters
    ----------
    pools
        Custom pool catalogue. Defaults to ``DEFAULT_CONCEPT_POOLS``.
    pool_names
        Optional subset of pool names to include. If both ``pools`` and
        ``pool_names`` are given, ``pool_names`` filters ``pools``.
    pools_per_candidate
        How many pools each candidate mashup spans. Default 4 (matches
        the4-way mashup most榜单 books exhibit).
    sample_size
        Number of mashup candidates to evaluate before ranking. Higher
        means broader exploration but slower; default 60 covers the
        space well for 8 pools × 4 picks.
    top_k
        Number of top-scored candidates to return.
    seed
        RNG seed for reproducibility. None → wall-clock random.
    forbidden_seed_keys
        Concept keys to exclude (e.g. seeds already used in another book
        of the same series). Matched against ``ConceptSeed.key``.
    saturation_cutoff
        Discard candidates whose average saturation exceeds this value.
        Default 0.7 — anything beyond is so flooded that even mashups
        won't differentiate.
    """

    pool_catalog = list(pools or DEFAULT_CONCEPT_POOLS)
    if pool_names:
        wanted = {n.strip() for n in pool_names if n and n.strip()}
        pool_catalog = [p for p in pool_catalog if p.name in wanted]

    if len(pool_catalog) < pools_per_candidate:
        raise ValueError(
            f"need at least {pools_per_candidate} pools, got {len(pool_catalog)}"
        )

    forbidden_keys = {k.strip() for k in forbidden_seed_keys if k and k.strip()}
    rng = random.Random(seed)

    def _candidate_pools() -> list[ConceptPool]:
        return rng.sample(pool_catalog, pools_per_candidate)

    def _pick_seed(pool: ConceptPool) -> ConceptSeed | None:
        eligible = [s for s in pool.seeds if s.key not in forbidden_keys]
        if not eligible:
            return None
        # Weighted away from saturated picks — keeps the engine fresh
        # without entirely banning popular concepts.
        weights = [max(0.05, 1.0 - s.saturation_score) for s in eligible]
        return rng.choices(eligible, weights=weights, k=1)[0]

    seen_signatures: set[str] = set()
    candidates: list[ConceptCandidate] = []

    for _ in range(sample_size):
        chosen_pools = _candidate_pools()
        seeds: list[ConceptSeed] = []
        for pool in chosen_pools:
            seed_pick = _pick_seed(pool)
            if seed_pick is None:
                break
            seeds.append(seed_pick)
        if len(seeds) != pools_per_candidate:
            continue

        signature = " × ".join(sorted(s.key for s in seeds))
        if signature in seen_signatures:
            continue
        seen_signatures.add(signature)

        saturation_penalty = sum(s.saturation_score for s in seeds) / len(seeds)
        if saturation_penalty > saturation_cutoff:
            continue

        novelty_score = 1.0 - saturation_penalty
        coherence_score = _coherence_score(seeds, chosen_pools)
        combined = _combined_score(novelty_score, coherence_score, saturation_penalty)

        rationale = _rationale_for(seeds, chosen_pools, novelty_score, coherence_score)
        premise_hint = _premise_hint(seeds, chosen_pools)
        forbidden_overlap = _forbidden_overlap(seeds, pool_catalog)

        candidates.append(
            ConceptCandidate(
                seeds=seeds,
                pools=[p.name for p in chosen_pools],
                novelty_score=novelty_score,
                coherence_score=coherence_score,
                saturation_penalty=saturation_penalty,
                combined_score=combined,
                rationale=rationale,
                premise_hint=premise_hint,
                forbidden_overlap=forbidden_overlap,
            )
        )

    candidates.sort(key=lambda c: c.combined_score, reverse=True)
    top = candidates[:top_k]

    return ConceptLeapResult(
        candidates=top,
        pools_sampled=[p.name for p in pool_catalog],
        samples_evaluated=sample_size,
        seed=seed,
    )


def render_concept_candidate_block(
    candidate: ConceptCandidate | Mapping[str, Any] | None,
    *,
    language: str = "zh-CN",
) -> str:
    """Render a single candidate as a prompt-ready text block."""

    payload = _to_payload(candidate)
    if not payload:
        return ""

    signature = payload.get("signature") or ""
    seeds = payload.get("seeds") or []
    novelty = payload.get("novelty_score")
    coherence = payload.get("coherence_score")
    combined = payload.get("combined_score")
    rationale = payload.get("rationale") or []
    premise_hint = payload.get("premise_hint") or ""

    if language.lower().startswith("zh"):
        lines = ["【概念跨界候选 — 整本书的原始 DNA】"]
        if signature:
            lines.append(f"- 组合签名: {signature}")
        for seed in seeds:
            if isinstance(seed, Mapping):
                key = seed.get("key", "")
                label = seed.get("label", "")
                desc = seed.get("description", "")
                line = f"  · [{key}] {label}"
                if desc:
                    line += f" — {desc}"
                lines.append(line)
        if novelty is not None and coherence is not None and combined is not None:
            lines.append(
                f"- 评分: 新颖度{float(novelty):.2f} / 内聚度{float(coherence):.2f} / 综合{float(combined):.2f}"
            )
        if rationale:
            lines.append("- 跨界依据:")
            for note in rationale[:5]:
                lines.append(f"  · {note}")
        if premise_hint:
            lines.append(f"- 前提提示: {premise_hint}")
        lines.append(
            "- 本书 conception 必须把以上四个池的概念真实糅合进 premise/setting/character，"
            "不得退化为'单一池子内独自展开'。"
        )
        return "\n".join(lines)

    lines = ["[Concept Leap Candidate — book's originating DNA]"]
    if signature:
        lines.append(f"- Signature: {signature}")
    return "\n".join(lines)


# ---------- internals ----------


def _coherence_score(
    seeds: Sequence[ConceptSeed], pools: Sequence[ConceptPool]
) -> float:
    """Estimate whether the seeds *can* coexist in one story.

    Pure heuristic: count tag overlap as positive (related concepts can
    bond), pool diversity as positive (one seed per pool = max diversity =
    max coherence opportunity), penalize "all-saturated" combos as
    incoherent because they've been tried and feel like cliché.
    """

    total = len(seeds)
    if total == 0:
        return 0.0

    tag_counter: dict[str, int] = {}
    for s in seeds:
        for tag in s.tags:
            tag_counter[tag] = tag_counter.get(tag, 0) + 1
    overlap_tags = sum(1 for v in tag_counter.values() if v >= 2)
    diversity = min(1.0, len({p.name for p in pools}) / max(1, total))

    avg_saturation = sum(s.saturation_score for s in seeds) / total
    base = 0.55 + 0.10 * min(overlap_tags, 4) + 0.20 * diversity
    base -= 0.25 * avg_saturation
    return max(0.0, min(1.0, base))


def _combined_score(
    novelty: float, coherence: float, saturation_penalty: float
) -> float:
    raw = 0.5 * novelty + 0.4 * coherence - 0.25 * saturation_penalty
    return max(0.0, min(1.0, raw + 0.1))


def _rationale_for(
    seeds: Sequence[ConceptSeed],
    pools: Sequence[ConceptPool],
    novelty: float,
    coherence: float,
) -> list[str]:
    bits: list[str] = []
    pool_labels = {p.name: p.label for p in pools}
    pool_names = [p.name for p in pools]
    bits.append(
        "跨越 " + " / ".join(pool_labels.get(n, n) for n in pool_names)
        + " 四个互不相关的领域池"
    )
    seed_descriptions = []
    for s in seeds:
        seed_descriptions.append(f"{s.label}（饱和度 {s.saturation_score:.2f}）")
    bits.append("素材: " + " + ".join(seed_descriptions))
    if novelty > 0.7:
        bits.append("新颖度高，远离 saturated 套路")
    if coherence > 0.65:
        bits.append("素材之间存在 tag/池跨度，可被同一世界观吸收")
    return bits


def _premise_hint(
    seeds: Sequence[ConceptSeed], pools: Sequence[ConceptPool]
) -> str:
    """One-liner suggesting how the mashup *might* be unified into a premise."""

    if not seeds:
        return ""
    pool_to_seed = dict(zip([p.name for p in pools], seeds, strict=False))
    parts = [f"{label}（{seed.label}）" for label, seed in zip(
        [p.label for p in pools],
        seeds,
        strict=False,
    )]
    return (
        "把 " + "、".join(parts) + " 糅合到同一故事母题中——"
        "建议以情感池的关系驱动主角行动，"
        "用空间池作为'地理性主角'，"
        "用结构池决定章回循环，"
        "其余池作为世界规则与冲突来源。"
    )


def _forbidden_overlap(
    seeds: Sequence[ConceptSeed], pool_catalog: Sequence[ConceptPool]
) -> list[str]:
    """Flag seeds whose tags overlap so heavily with existing榜单 books
    that the resulting mashup likely re-creates them.
    """

    used_keys = {s.key for s in seeds}
    flagged: list[str] = []
    for pool in pool_catalog:
        for seed in pool.seeds:
            if seed.key in used_keys and seed.saturation_score >= 0.55:
                flagged.append(seed.key)
    return sorted(set(flagged))


def _to_payload(value: object) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, ConceptCandidate):
        return value.to_prompt_card()
    if isinstance(value, Mapping):
        return dict(value)
    if hasattr(value, "model_dump"):
        dumped = value.model_dump(mode="json")
        return dict(dumped) if isinstance(dumped, Mapping) else {}
    return {}


__all__ = [
    "DEFAULT_CONCEPT_POOLS",
    "generate_concept_leap",
    "render_concept_candidate_block",
]
