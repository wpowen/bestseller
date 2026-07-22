"""Short-sample arena for measuring Chinese fiction AI-flavor prompt strategies.

The production detector is tuned for chapters.  This module supplies a deliberately
small, transparent scorer for 50-120 CJK-character samples and the fixed experiment
matrix used by ``scripts/anti_ai_short_arena.py``.  It is an evaluation helper, not a
publication gate: the final decision also requires blind pairwise review.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from itertools import combinations
import re
from typing import Mapping, Sequence

from bestseller.services.anti_ai_voice_discipline import (
    render_anti_ai_voice_discipline,
)
from bestseller.services.quality_levers.prose_prompt_fusion import (
    render_prose_prompt_fusion_block,
)


@dataclass(frozen=True)
class ShortArenaBrief:
    brief_id: str
    instruction: str
    coverage_groups: tuple[tuple[str, ...], ...]


@dataclass(frozen=True)
class ShortArenaStrategy:
    strategy_id: str
    label: str
    instruction: str


@dataclass(frozen=True)
class ShortSampleScore:
    cjk_chars: int
    risk_score: float
    length_passed: bool
    coverage_ratio: float
    pattern_counts: Mapping[str, int]


_PATTERNS: tuple[tuple[str, re.Pattern[str], float], ...] = (
    (
        "epiphany_announcement",
        re.compile(r"(?:他|她|[\u4e00-\u9fff]{2,3})(?:忽然|突然|终于|这才)?(?:明白|意识到|知道了?)[^。！？\n]{0,24}"),
        9.0,
    ),
    (
        "authorial_conclusion",
        re.compile(r"(?:这|那)(?:不|就)?是(?:一种|一个|一场|一份)|原来|显然|这意味着|这说明|说到底|本质上"),
        8.0,
    ),
    (
        "negated_definition",
        re.compile(
            r"不是[^。！？\n]{1,22}(?:，|,)\s*(?:而)?是"
            r"|不是[^。！？\n]{1,12}是[^。！？\n]{1,18}"
            r"|(?:其实)?没什么[^。！？\n]{1,16}(?:，|,)\s*是"
        ),
        7.0,
    ),
    (
        "negative_action_filler",
        re.compile(r"(?:他|她|[\u4e00-\u9fff]{2,3})(?:没|没有)(?:抬头|回头|说话|吭声|动|停|犹豫|松手|去看|去擦|回答)"),
        5.0,
    ),
    (
        "body_shortcut",
        re.compile(
            r"(?:手腕|腕骨|掌心|手心|指尖|指节|喉结|呼吸|心口|后颈|脊背)"
            r"[^，。！？\n]{0,12}(?:发烫|一烫|滚了一下|动了一下|一紧|一滞|发白|一凉|一麻|一跳|抖了一下)"
            r"|(?:烫|热)[^，。！？\n]{0,6}(?:一下|一瞬|又)"
        ),
        8.0,
    ),
    (
        "generic_micro_action",
        re.compile(r"瞳孔(?:微微)?一?缩|嘴角(?:微微)?勾|眉头(?:微微)?皱|心头一紧|心里一沉|倒吸一口冷气"),
        6.0,
    ),
    (
        "emotion_label",
        re.compile(r"(?:感到|觉得|满是|充满)(?:震惊|紧张|愤怒|悲伤|害怕|恐惧|失望|不安)|(?:震惊|紧张|愤怒|悲伤|恐惧)地"),
        5.0,
    ),
    (
        "explanation_connector",
        re.compile(r"之所以|是因为|换句话说|也就是说|因此可见|由此可见"),
        6.0,
    ),
    (
        "formulaic_simile",
        re.compile(r"仿佛|宛若|犹如|像(?:一把刀|潮水|雷击|针扎|冰块)"),
        4.0,
    ),
)

_META_PREFIX_RE = re.compile(r"^(?:正文|改写后|版本[一二三四五六]?)[：:]\s*")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def build_short_arena_briefs() -> tuple[ShortArenaBrief, ...]:
    """Return five fixed stress cases that tempt the reported AI tics."""

    return (
        ShortArenaBrief(
            brief_id="wrist_heat_clue",
            instruction=(
                "沈砚把师父留下的铜钥插进封门。腕骨旧印随即变热，这是确有其事的线索；"
                "门后却传来师父三年前常用的敲击暗号。写他如何确认并作出一个动作。"
            ),
            coverage_groups=(("铜钥", "钥匙"), ("门", "门缝"), ("敲", "暗号", "声")),
        ),
        ShortArenaBrief(
            brief_id="betrayal_token",
            instruction=(
                "阿禾刚确认一直护着自己的师兄把她卖给了仇家。院里只剩她，手中是师兄临走塞的半块玉佩。"
                "写她处理玉佩并决定下一步。"
            ),
            coverage_groups=(("玉佩",), ("师兄",), ("院", "门", "墙", "地")),
        ),
        ShortArenaBrief(
            brief_id="grief_object",
            instruction=(
                "许川没能救回妹妹。急救员收走担架后，妹妹书包侧袋里的公交卡掉在地上。"
                "写他捡卡到离开这段，不直接说明情绪。"
            ),
            coverage_groups=(("公交卡", "卡"), ("书包", "侧袋"), ("门", "走廊", "地", "担架")),
        ),
        ShortArenaBrief(
            brief_id="debt_doorway",
            instruction=(
                "债主带两个人堵门，床上的妹妹正在发烧。少年只有一张明早才能兑付的工资条。"
                "写他用工资条争到十分钟，必须有对方的可见反应。"
            ),
            coverage_groups=(("工资条",), ("十分钟", "十来分钟"), ("债主", "对方", "门")),
        ),
        ShortArenaBrief(
            brief_id="forged_seal",
            instruction=(
                "女捕快在封存文书上发现一粒蓝盐，而官府印泥一直是红色。值房外脚步正在靠近。"
                "写她验证伪印并藏起证据，不解释推理过程。"
            ),
            coverage_groups=(("蓝盐", "盐"), ("印", "印泥", "封"), ("脚步", "门外", "值房")),
        ),
    )


def build_short_arena_strategies() -> tuple[ShortArenaStrategy, ...]:
    """Return the six controlled prompt treatments."""

    production = (
        render_anti_ai_voice_discipline(language="zh-CN", scope="scene")
        + "\n"
        + render_prose_prompt_fusion_block(language="zh-CN", position=None)
    )
    return (
        ShortArenaStrategy(
            strategy_id="production_control",
            label="当前生产反 AI 规则",
            instruction=production,
        ),
        ShortArenaStrategy(
            strategy_id="blacklist_only",
            label="禁词禁句清单",
            instruction=(
                "禁止结论先行、不是X而是Y、他没X式克制、作者解释因果、总结收尾。"
                "禁止手腕发烫、喉结滚动、指节发白、呼吸一滞、瞳孔收缩等通用身体模板。"
            ),
        ),
        ShortArenaStrategy(
            strategy_id="process_first",
            label="事件过程优先",
            instruction=(
                "正文只走一条可观察的链：外界变化→人物选择→可见后果。解释和情绪判断都放弃；"
                "身体感觉只有在它改变人物的行动能力或选择时才写，并立刻落到后果。"
            ),
        ),
        ShortArenaStrategy(
            strategy_id="reader_inference",
            label="读者推断留白",
            instruction=(
                "给读者两条能看见或听见的证据，让人物只做一个与当前目标有关的选择。"
                "不解释证据的含义，不替人物宣布顿悟，也不用通用身体反应代替选择。"
            ),
        ),
        ShortArenaStrategy(
            strategy_id="scene_specific_choice",
            label="场景特有选择",
            instruction=(
                "每个主要动作都必须依赖本场人物、物件或困境；能原样搬到十个别的场景的动作不要写。"
                "普通感觉用普通词；既定身体异象只有立即改变选择或造成结果时才保留。结尾停在结果上。"
            ),
        ),
        ShortArenaStrategy(
            strategy_id="internal_two_pass",
            label="内部二遍自删",
            instruction=(
                "先在内部写一稿，不输出。第二遍删去作者判断、解释因果、否定式克制、通用身体微动作、"
                "工整对仗和总结句；确认人物、物件、选择、后果仍齐全，只输出二稿。"
            ),
        ),
    )


def build_short_writer_system_prompt(strategy: ShortArenaStrategy) -> str:
    return (
        "你是中文类型小说写手。只输出一段 70–110 个中文字符的小说正文；"
        "不要标题、说明、分析、引号外的元话语。场景事实不可改，不能靠删掉任务来显得简洁。\n\n"
        "【本轮写法】\n"
        + strategy.instruction
    )


def build_short_writer_user_prompt(brief: ShortArenaBrief) -> str:
    return "【场景】\n" + brief.instruction + "\n【输出】70–110 个中文字符，只写正文。"


def clean_short_sample(text: str) -> str:
    cleaned = text.strip().strip("`").strip()
    cleaned = _META_PREFIX_RE.sub("", cleaned)
    return cleaned


def cjk_char_count(text: str) -> int:
    return len(_CJK_RE.findall(text))


def score_short_sample(text: str, brief: ShortArenaBrief) -> ShortSampleScore:
    cleaned = clean_short_sample(text)
    cjk = cjk_char_count(cleaned)
    counts: dict[str, int] = {}
    risk = 0.0
    for key, pattern, weight in _PATTERNS:
        count = len(pattern.findall(cleaned))
        counts[key] = count
        risk += count * weight

    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", cleaned) if p.strip()]
    short_paragraphs = sum(
        1
        for paragraph in paragraphs
        if cjk_char_count(paragraph) <= 12
        and len(re.findall(r"[。！？]", paragraph)) <= 1
    )
    if len(paragraphs) >= 3 and short_paragraphs / len(paragraphs) >= 0.67:
        counts["staccato_saturation"] = 1
        risk += 6.0
    else:
        counts["staccato_saturation"] = 0

    covered = sum(
        1 for alternatives in brief.coverage_groups if any(term in cleaned for term in alternatives)
    )
    coverage_ratio = covered / max(1, len(brief.coverage_groups))
    risk += (1.0 - coverage_ratio) * 24.0

    length_passed = 50 <= cjk <= 120
    if cjk < 50:
        risk += min(24.0, (50 - cjk) * 0.8)
    elif cjk > 120:
        risk += min(24.0, (cjk - 120) * 0.4)

    return ShortSampleScore(
        cjk_chars=cjk,
        risk_score=round(min(100.0, risk), 2),
        length_passed=length_passed,
        coverage_ratio=round(coverage_ratio, 4),
        pattern_counts=counts,
    )


def repeated_ngram_ratio(texts: Sequence[str], *, n: int = 4) -> float:
    """Return cross-sample CJK n-gram reuse ratio for one prompt strategy."""

    if len(texts) < 2:
        return 0.0
    document_grams: list[set[str]] = []
    for text in texts:
        chars = "".join(_CJK_RE.findall(clean_short_sample(text)))
        document_grams.append({chars[index : index + n] for index in range(len(chars) - n + 1)})
    frequency = Counter(gram for grams in document_grams for gram in grams)
    repeated = {gram for gram, count in frequency.items() if count >= 2}
    union = set().union(*document_grams)
    return round(len(repeated) / max(1, len(union)), 4)


def aggregate_strategy_metrics(
    drafts: Mapping[str, str],
    briefs: Sequence[ShortArenaBrief],
) -> dict[str, object]:
    scores = [score_short_sample(drafts[brief.brief_id], brief) for brief in briefs]
    reuse_ratio = repeated_ngram_ratio([drafts[brief.brief_id] for brief in briefs])
    mean_risk = sum(score.risk_score for score in scores) / max(1, len(scores))
    reuse_penalty = reuse_ratio * 100.0
    pattern_totals: Counter[str] = Counter()
    for score in scores:
        pattern_totals.update(score.pattern_counts)
    return {
        "mean_sample_risk": round(mean_risk, 2),
        "cross_sample_reuse_ratio": reuse_ratio,
        "cross_sample_reuse_penalty": round(reuse_penalty, 2),
        "deterministic_risk": round(min(100.0, mean_risk + reuse_penalty), 2),
        "length_pass_rate": round(sum(score.length_passed for score in scores) / len(scores), 4),
        "coverage_pass_rate": round(
            sum(score.coverage_ratio >= 2 / 3 for score in scores) / len(scores), 4
        ),
        "mean_coverage": round(sum(score.coverage_ratio for score in scores) / len(scores), 4),
        "pattern_totals": dict(sorted(pattern_totals.items())),
        "samples": [
            {
                "brief_id": brief.brief_id,
                "cjk_chars": score.cjk_chars,
                "risk_score": score.risk_score,
                "length_passed": score.length_passed,
                "coverage_ratio": score.coverage_ratio,
                "pattern_counts": dict(score.pattern_counts),
            }
            for brief, score in zip(briefs, scores, strict=True)
        ],
    }


def pair_ids(strategy_ids: Sequence[str]) -> tuple[tuple[str, str], ...]:
    return tuple(combinations(strategy_ids, 2))


def compute_acceptance(
    *,
    winner_id: str,
    metrics_by_strategy: Mapping[str, Mapping[str, object]],
    head_to_head_vs_control: float,
    llm_calls: int,
) -> dict[str, object]:
    control = float(metrics_by_strategy["production_control"]["deterministic_risk"])
    winner = float(metrics_by_strategy[winner_id]["deterministic_risk"])
    improvement = 0.0 if control <= 0 else (control - winner) / control
    length_rate = float(metrics_by_strategy[winner_id]["length_pass_rate"])
    coverage_rate = float(metrics_by_strategy[winner_id]["coverage_pass_rate"])
    quality_gain = improvement >= 0.25 or head_to_head_vs_control >= 0.70
    passed = (
        winner_id != "production_control"
        and quality_gain
        and length_rate >= 0.80
        and coverage_rate >= 0.80
        and llm_calls <= 50
    )
    return {
        "passed": passed,
        "winner_id": winner_id,
        "risk_improvement_vs_control": round(improvement, 4),
        "head_to_head_win_rate_vs_control": round(head_to_head_vs_control, 4),
        "winner_length_pass_rate": length_rate,
        "winner_coverage_pass_rate": coverage_rate,
        "llm_calls": llm_calls,
        "max_llm_calls": 50,
        "quality_gain_met": quality_gain,
    }


__all__ = [
    "ShortArenaBrief",
    "ShortArenaStrategy",
    "ShortSampleScore",
    "aggregate_strategy_metrics",
    "build_short_arena_briefs",
    "build_short_arena_strategies",
    "build_short_writer_system_prompt",
    "build_short_writer_user_prompt",
    "clean_short_sample",
    "compute_acceptance",
    "pair_ids",
    "repeated_ngram_ratio",
    "score_short_sample",
]
