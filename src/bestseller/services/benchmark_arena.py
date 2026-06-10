"""成对盲评 Arena：框架生成章节 vs 真榜单书章节（榜单对标闭环 P1.1）。

设计要点（docs/榜单对标闭环-总体方案与修改计划-20260610.md §1）：

- **同题材同章位**：第 1 章只和第 1 章比、第 50 章只和第 50 章比。
- **position-swap 双向盲评**：每对判两次（A/B 互换），方向不一致记 tie，
  抵消位置偏置。
- **去识别**：prompt 不含书名/作者；并明确指示「不要因为你可能认出出处而
  偏向任何一方」，防止判官认出知名真书。
- **判官注入**：评判函数由调用方注入（脚本侧用 litellm，单测用假判官），
  本模块不直接依赖任何 LLM SDK。

胜率口径：win-rate = (win + 0.5*tie) / pairs —— 框架文本对真书的得分率。
验收线见 ``config/benchmark_targets.yaml``（P1.3）。
"""

from __future__ import annotations

import json
import logging
import statistics
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# 盲评维度：键 → 判官可读描述
ARENA_DIMENSIONS: dict[str, str] = {
    "hook": "钩子与追读欲：结尾/段落是否制造继续阅读的冲动",
    "cinematic": "画面感：具体可视的动作与场景 vs 抽象旁白",
    "character": "人物：动机可信、行为有性格、对话有身份感",
    "prose": "文采：语言质感、句式节奏、金句与意象（不等于辞藻堆砌）",
    "shuangwen_rhythm": "爽点节奏：压抑-释放结构、情绪回报的密度与时机",
    "ai_flavor": "人味：哪段更像真人作者写的（碎句癖/万能比喻/总结腔=AI味）",
}

JudgeFn = Callable[[str, str], Awaitable[str]]
"""(system_prompt, user_prompt) -> raw model text."""


@dataclass(frozen=True)
class ArenaPair:
    """One framework-vs-benchmark comparison unit (same genre, same position)."""

    pair_id: str
    framework_text: str
    benchmark_text: str
    benchmark_tier: str
    category: str
    chapter_number: int
    framework_label: str = ""
    benchmark_label: str = ""


@dataclass(frozen=True)
class ArenaVerdict:
    """Single-direction judge output."""

    winner: str  # "framework" | "benchmark" | "tie"
    dimension_winners: dict[str, str] = field(default_factory=dict)
    reason: str = ""


@dataclass(frozen=True)
class ArenaMatchResult:
    """Swap-consistent result for one pair."""

    pair: ArenaPair
    outcome: str  # "win" | "loss" | "tie"
    forward: ArenaVerdict | None
    backward: ArenaVerdict | None
    dimension_outcomes: dict[str, str] = field(default_factory=dict)


def build_arena_system_prompt() -> str:
    dims = "\n".join(f"- {key}: {desc}" for key, desc in ARENA_DIMENSIONS.items())
    return (
        "你是中文网文平台的资深责编，正在做双盲质量评审。\n"
        "你会看到同题材、同章节位置的两段正文（甲/乙）。任务：判断哪一段更可能"
        "让目标读者继续追读并付费。\n"
        "【重要】两段文本都已去除书名与作者。即使你怀疑认出了某段的出处，也"
        "必须只依据眼前文本的质量评判，不要因为出处偏向任何一方。\n"
        "【风格中立】不要因为排版差异、个别错别字、年代感用语或网文口语化而扣分——"
        "这些是来源载体差异，不是写作质量。同样，辞藻华丽、意象密集**本身不加分**：评判"
        "标准是目标读者的追读冲动，不是文学性。\n"
        "【截断说明】两段都可能含「（中段省略）」标记——这是评审窗口截断，两段同等处理，"
        "不要视为缺陷。\n"
        "【持平优先】只有当差距明确、可指出具体证据时才判出胜负；两段各有所长或差距"
        "微小时判「持平」。\n"
        "逐维度评判：\n"
        f"{dims}\n"
        "输出严格 JSON（不要 markdown 代码块）：\n"
        '{"winner": "甲"|"乙"|"持平", "dimensions": {"hook": "甲"|"乙"|"持平", ...}, '
        '"reason": "≤60字"}'
    )


def build_arena_user_prompt(text_a: str, text_b: str, *, category: str, chapter_number: int) -> str:
    return (
        f"题材：{category}；章节位置：第{chapter_number}章。\n\n"
        f"【甲】\n{text_a}\n\n"
        f"【乙】\n{text_b}\n\n"
        "请输出 JSON 评判。"
    )


def parse_arena_verdict(raw: str, *, framework_is_a: bool) -> ArenaVerdict | None:
    """Map 甲/乙 verdict back to framework/benchmark given the slot assignment."""
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        payload = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None

    def _map(token: Any) -> str:
        label = str(token or "").strip()
        if label in {"甲", "A", "a"}:
            return "framework" if framework_is_a else "benchmark"
        if label in {"乙", "B", "b"}:
            return "benchmark" if framework_is_a else "framework"
        return "tie"

    dimensions = payload.get("dimensions")
    dimension_winners = (
        {str(key): _map(value) for key, value in dimensions.items()}
        if isinstance(dimensions, dict)
        else {}
    )
    return ArenaVerdict(
        winner=_map(payload.get("winner")),
        dimension_winners=dimension_winners,
        reason=str(payload.get("reason") or "")[:200],
    )


def _swap_consistent(forward: str, backward: str) -> str:
    """Combine two directions; only consistent wins count, else tie."""
    if forward == backward == "framework":
        return "win"
    if forward == backward == "benchmark":
        return "loss"
    return "tie"


async def run_arena_match(pair: ArenaPair, judge: JudgeFn) -> ArenaMatchResult:
    """Judge one pair in both slot orders and combine swap-consistently."""
    system = build_arena_system_prompt()
    forward_raw = await judge(
        system,
        build_arena_user_prompt(
            pair.framework_text,
            pair.benchmark_text,
            category=pair.category,
            chapter_number=pair.chapter_number,
        ),
    )
    backward_raw = await judge(
        system,
        build_arena_user_prompt(
            pair.benchmark_text,
            pair.framework_text,
            category=pair.category,
            chapter_number=pair.chapter_number,
        ),
    )
    forward = parse_arena_verdict(forward_raw, framework_is_a=True)
    backward = parse_arena_verdict(backward_raw, framework_is_a=False)
    if forward is None or backward is None:
        logger.warning("arena pair %s: unparseable verdict, scoring tie", pair.pair_id)
        return ArenaMatchResult(
            pair=pair, outcome="tie", forward=forward, backward=backward
        )
    dimension_outcomes = {
        key: _swap_consistent(
            forward.dimension_winners.get(key, "tie"),
            backward.dimension_winners.get(key, "tie"),
        )
        for key in ARENA_DIMENSIONS
    }
    return ArenaMatchResult(
        pair=pair,
        outcome=_swap_consistent(forward.winner, backward.winner),
        forward=forward,
        backward=backward,
        dimension_outcomes=dimension_outcomes,
    )


@dataclass(frozen=True)
class ArenaSummary:
    tier: str
    pairs: int
    wins: int
    losses: int
    ties: int
    win_rate: float
    dimension_win_rates: dict[str, float] = field(default_factory=dict)


def summarize_arena(results: list[ArenaMatchResult], *, tier: str) -> ArenaSummary:
    """Win-rate = (win + 0.5*tie) / pairs for the given benchmark tier."""
    tier_results = [r for r in results if r.pair.benchmark_tier == tier]
    pairs = len(tier_results)
    if pairs == 0:
        return ArenaSummary(tier=tier, pairs=0, wins=0, losses=0, ties=0, win_rate=0.0)
    wins = sum(1 for r in tier_results if r.outcome == "win")
    losses = sum(1 for r in tier_results if r.outcome == "loss")
    ties = pairs - wins - losses
    dimension_win_rates: dict[str, float] = {}
    for key in ARENA_DIMENSIONS:
        dim_wins = sum(1 for r in tier_results if r.dimension_outcomes.get(key) == "win")
        dim_ties = sum(1 for r in tier_results if r.dimension_outcomes.get(key) == "tie")
        dimension_win_rates[key] = round((dim_wins + 0.5 * dim_ties) / pairs, 3)
    return ArenaSummary(
        tier=tier,
        pairs=pairs,
        wins=wins,
        losses=losses,
        ties=ties,
        win_rate=round((wins + 0.5 * ties) / pairs, 3),
        dimension_win_rates=dimension_win_rates,
    )


# ── 验收线（P1.3）───────────────────────────────────────────────────────────

DEFAULT_TARGETS_PATH = Path("config/benchmark_targets.yaml")


@dataclass(frozen=True)
class BenchmarkTargets:
    vs_t2_win_rate_min: float = 0.50
    vs_t1_win_rate_min: float = 0.35
    min_pairs_per_tier: int = 8


def load_benchmark_targets(path: Path | None = None) -> BenchmarkTargets:
    import yaml

    target_path = path or DEFAULT_TARGETS_PATH
    try:
        payload = yaml.safe_load(target_path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return BenchmarkTargets()
    section = payload.get("benchmark_targets") or {}
    defaults = BenchmarkTargets()
    return BenchmarkTargets(
        vs_t2_win_rate_min=float(
            section.get("vs_t2_win_rate_min", defaults.vs_t2_win_rate_min)
        ),
        vs_t1_win_rate_min=float(
            section.get("vs_t1_win_rate_min", defaults.vs_t1_win_rate_min)
        ),
        min_pairs_per_tier=int(
            section.get("min_pairs_per_tier", defaults.min_pairs_per_tier)
        ),
    )


@dataclass(frozen=True)
class TargetEvaluation:
    passed: bool
    details: dict[str, Any] = field(default_factory=dict)


def evaluate_targets(
    summaries: dict[str, ArenaSummary], targets: BenchmarkTargets | None = None
) -> TargetEvaluation:
    """PASS iff every tier with enough pairs meets its win-rate floor.

    Tiers below ``min_pairs_per_tier`` are reported but do not gate (insufficient
    evidence must read as "inconclusive", never as a silent pass of the whole run —
    so if NO tier has enough pairs the evaluation fails).
    """
    resolved = targets or load_benchmark_targets()
    floors = {"t1": resolved.vs_t1_win_rate_min, "t2": resolved.vs_t2_win_rate_min}
    details: dict[str, Any] = {}
    gated: list[bool] = []
    for tier, floor in floors.items():
        summary = summaries.get(tier)
        if summary is None or summary.pairs < resolved.min_pairs_per_tier:
            details[tier] = {
                "status": "inconclusive",
                "pairs": summary.pairs if summary else 0,
                "required_pairs": resolved.min_pairs_per_tier,
            }
            continue
        ok = summary.win_rate >= floor
        gated.append(ok)
        details[tier] = {
            "status": "pass" if ok else "fail",
            "win_rate": summary.win_rate,
            "floor": floor,
            "pairs": summary.pairs,
        }
    passed = bool(gated) and all(gated)
    return TargetEvaluation(passed=passed, details=details)
