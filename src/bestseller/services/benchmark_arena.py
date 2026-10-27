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

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
import json
import logging
import math
from pathlib import Path
import random
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
    generated_book_id: str = ""
    benchmark_book_id: str = ""
    benchmark_author_id: str = ""
    narrative_stage: str = ""


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
    valid: bool = True
    invalid_reason: str = ""


def adapt_independent_judge_result(pair: ArenaPair, result: Any) -> ArenaMatchResult:
    """Adapt advisory blind-judge evidence to the legacy arena result shape."""

    winner = getattr(result, "winner", None)
    outcome = "win" if winner == "draft_a" else "loss" if winner == "draft_b" else "tie"
    raw_dimensions = getattr(result, "dimension_outcomes", {})
    dimension_outcomes = {
        str(key): ("win" if value == "draft_a" else "loss" if value == "draft_b" else "tie")
        for key, value in raw_dimensions.items()
    }
    return ArenaMatchResult(
        pair=pair,
        outcome=outcome,
        forward=None,
        backward=None,
        dimension_outcomes=dimension_outcomes,
    )


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
            pair=pair,
            outcome="tie",
            forward=forward,
            backward=backward,
            valid=False,
            invalid_reason="judge_parse_failure",
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


# Formal Arena v1 constants.  The legacy API above remains available for
# diagnostic runs, while these contracts are intentionally stricter.
FORMAL_ARENA_ESTIMATOR = "arena-estimator-v1"
FORMAL_ARENA_BOOTSTRAP = "arena-hierarchical-bootstrap-v1"
FORMAL_BOOTSTRAP_ITERATIONS = 10_000
FORMAL_MIN_GENERATED = 8
FORMAL_MIN_BENCHMARK_BOOKS = 12
FORMAL_MIN_AUTHORS = 8
FORMAL_MIN_PAIRS = 96
FORMAL_MAX_CLUSTER_SHARE = 0.20
FORMAL_MAX_INVALID_RATE = 0.05


@dataclass(frozen=True)
class ArenaRequiredCell:
    """Sealed unit of inference: one genre, stage, and benchmark tier."""

    genre: str
    narrative_stage: str
    tier: str

    @property
    def key(self) -> str:
        return f"{self.genre}:{self.narrative_stage}:{self.tier}"


@dataclass(frozen=True)
class FormalArenaPair:
    """A completed sealed pair score in [0, 1]. Invalid pairs score zero."""

    generated_book_id: str
    benchmark_book_id: str
    benchmark_author_id: str
    score: float
    valid: bool = True
    required_observations: int = 2
    valid_observations: int = 2

    @property
    def effective_score(self) -> float:
        return float(self.score) if self.valid else 0.0


@dataclass(frozen=True)
class FormalArenaCellResult:
    cell: ArenaRequiredCell
    point_estimate: float
    ci_lower: float
    ci_upper: float
    invalid_rate: float
    generated_clusters: int
    benchmark_clusters: int
    author_clusters: int
    sealed_pairs: int
    t1_valid: bool
    t2_valid: bool
    status: str
    reasons: tuple[str, ...] = ()
    bootstrap_iterations: int = FORMAL_BOOTSTRAP_ITERATIONS
    estimator: str = FORMAL_ARENA_ESTIMATOR
    h0: float = 0.50
    h1: float = 0.60
    mde: float = 0.10
    planned_required_pairs: int = FORMAL_MIN_PAIRS
    achieved_power: float = 0.0


def _roster_value(item: Any, *keys: str) -> str:
    if isinstance(item, str):
        return item
    if isinstance(item, dict):
        for key in keys:
            value = item.get(key)
            if value is not None and str(value).strip():
                return str(value).strip()
    return ""


def validate_required_cell_roster(
    cell: ArenaRequiredCell,
    roster: dict[str, Any],
    *,
    min_generated: int = FORMAL_MIN_GENERATED,
    min_benchmark_books: int = FORMAL_MIN_BENCHMARK_BOOKS,
    min_authors: int = FORMAL_MIN_AUTHORS,
    min_pairs: int = FORMAL_MIN_PAIRS,
) -> tuple[str, ...]:
    """Validate a sealed cell roster before any scores are interpreted."""
    generated = roster.get("generated_books", roster.get("generated", ())) or ()
    benchmarks = roster.get("benchmark_books", roster.get("benchmarks", ())) or ()
    authors = roster.get("authors", ()) or ()
    pairs = roster.get("pairs", ()) or ()
    generated_ids = {_roster_value(x, "id", "book_id", "generated_book_id") for x in generated}
    benchmark_ids = {_roster_value(x, "id", "book_id", "benchmark_book_id") for x in benchmarks}
    author_ids = {_roster_value(x, "id", "author_id", "benchmark_author_id") for x in authors}
    issues: list[str] = []
    if len(generated_ids - {""}) < min_generated:
        issues.append("GENERATED_CLUSTER_FLOOR")
    if len(benchmark_ids - {""}) < min_benchmark_books:
        issues.append("BENCHMARK_BOOK_CLUSTER_FLOOR")
    if len(author_ids - {""}) < min_authors:
        issues.append("AUTHOR_CLUSTER_FLOOR")
    if len(pairs) < min_pairs:
        issues.append("SEALED_PAIR_FLOOR")
    expected = {(g, b) for g in generated_ids - {""} for b in benchmark_ids - {""}}
    seen = {
        (
            _roster_value(x, "generated_book_id", "generated_id", "generated"),
            _roster_value(x, "benchmark_book_id", "benchmark_id", "benchmark"),
        )
        for x in pairs
        if isinstance(x, dict)
    }
    if not expected.issubset(seen):
        issues.append("SEALED_CROSS_PRODUCT_MISSING")
    if any(
        _roster_value(x, "benchmark_author_id", "author_id", "author") not in author_ids
        for x in benchmarks
    ):
        issues.append("BENCHMARK_AUTHOR_ROSTER_MISMATCH")
    if pairs:
        total = len(pairs)
        for label, values in (
            (
                "GENERATED",
                [
                    _roster_value(x, "generated_book_id", "generated_id", "generated")
                    for x in pairs
                    if isinstance(x, dict)
                ],
            ),
            (
                "BENCHMARK",
                [
                    _roster_value(x, "benchmark_book_id", "benchmark_id", "benchmark")
                    for x in pairs
                    if isinstance(x, dict)
                ],
            ),
            (
                "AUTHOR",
                [
                    _roster_value(x, "benchmark_author_id", "author_id", "author")
                    for x in pairs
                    if isinstance(x, dict)
                ],
            ),
        ):
            if any(values.count(value) / total > FORMAL_MAX_CLUSTER_SHARE for value in set(values)):
                issues.append(f"{label}_CLUSTER_SHARE_OVER_20_PERCENT")
    return tuple(dict.fromkeys(issues))


def _point_estimate(pairs: list[FormalArenaPair]) -> float:
    """m_ga then p_hat; books within author, authors within generated equal."""
    by_generated: dict[str, dict[str, list[FormalArenaPair]]] = {}
    for pair in pairs:
        by_generated.setdefault(pair.generated_book_id, {}).setdefault(
            pair.benchmark_author_id, []
        ).append(pair)
    if not by_generated:
        return 0.0
    generated_means: list[float] = []
    for authors in by_generated.values():
        author_means = [
            sum(p.effective_score for p in books) / len(books)
            for books in authors.values()
            if books
        ]
        generated_means.append(sum(author_means) / len(author_means) if author_means else 0.0)
    return sum(generated_means) / len(generated_means)


def arena_point_estimate(pairs: list[FormalArenaPair]) -> float:
    """Public golden-formula helper for the formal Arena estimator."""
    return _point_estimate(pairs)


def hierarchical_bootstrap_ci(
    pairs: list[FormalArenaPair], *, seed: int, iterations: int = FORMAL_BOOTSTRAP_ITERATIONS
) -> tuple[float, float]:
    """Generated/author/book hierarchical percentile bootstrap CI."""
    if not pairs or iterations <= 0:
        return (0.0, 0.0)
    grouped: dict[str, dict[str, list[FormalArenaPair]]] = {}
    for pair in pairs:
        grouped.setdefault(pair.generated_book_id, {}).setdefault(
            pair.benchmark_author_id, []
        ).append(pair)
    rng = random.Random(seed)
    generated_ids = tuple(grouped)
    samples: list[float] = []
    for _ in range(iterations):
        sampled: list[FormalArenaPair] = []
        for g in (rng.choice(generated_ids) for _ in generated_ids):
            authors = grouped[g]
            author_ids = tuple(authors)
            for author in (rng.choice(author_ids) for _ in author_ids):
                books = authors[author]
                sampled.extend(rng.choice(books) for _ in books)
        samples.append(_point_estimate(sampled))
    samples.sort()
    lo = samples[max(0, math.floor(0.025 * len(samples)))]
    hi = samples[min(len(samples) - 1, math.ceil(0.975 * len(samples)) - 1)]
    return lo, hi


def evaluate_formal_arena_cell(
    cell: ArenaRequiredCell,
    pairs: list[FormalArenaPair],
    *,
    seed: int = 0,
    bootstrap_iterations: int = FORMAL_BOOTSTRAP_ITERATIONS,
    roster_issues: tuple[str, ...] = (),
    achieved_power: float | None = None,
    planned_required_pairs: int = FORMAL_MIN_PAIRS,
) -> FormalArenaCellResult:
    """Evaluate one cell; no tier or cell can be silently omitted."""
    generated = {p.generated_book_id for p in pairs}
    benchmark = {p.benchmark_book_id for p in pairs}
    authors = {p.benchmark_author_id for p in pairs}
    expected_pairs = len(generated) * len(benchmark)
    invalid = sum(not p.valid for p in pairs)
    invalid_rate = invalid / len(pairs) if pairs else 1.0
    reasons = list(roster_issues)
    tier = cell.tier.lower()
    h0, h1 = (0.35, 0.45) if tier == "t1" else (0.50, 0.60)
    power = float(achieved_power) if achieved_power is not None else 0.0
    if len(generated) < FORMAL_MIN_GENERATED:
        reasons.append("GENERATED_CLUSTER_FLOOR")
    if len(benchmark) < FORMAL_MIN_BENCHMARK_BOOKS:
        reasons.append("BENCHMARK_BOOK_CLUSTER_FLOOR")
    if len(authors) < FORMAL_MIN_AUTHORS:
        reasons.append("AUTHOR_CLUSTER_FLOOR")
    if expected_pairs < FORMAL_MIN_PAIRS or len(pairs) < expected_pairs:
        reasons.append("SEALED_CROSS_PRODUCT_INCOMPLETE")
    if any(
        p.required_observations <= 0 or p.valid_observations != p.required_observations
        for p in pairs
    ):
        reasons.append("REQUIRED_JUDGE_POSITION_OBSERVATION_MISSING")
    if invalid_rate > FORMAL_MAX_INVALID_RATE:
        reasons.append("INVALID_RATE_OVER_5_PERCENT")
    point = _point_estimate(pairs)
    lo, hi = hierarchical_bootstrap_ci(pairs, seed=seed, iterations=bootstrap_iterations)
    if tier == "t2":
        tier_valid = point >= 0.60 and lo > 0.50
    elif tier == "t1":
        tier_valid = point >= 0.45 and lo >= 0.35
    else:
        tier_valid = False
        reasons.append("UNKNOWN_TIER")
    structural_reasons = tuple(dict.fromkeys(reasons))
    if power < 0.80:
        reasons.append("POWER_BELOW_0_80")
    if not tier_valid:
        reasons.append(f"{cell.tier.upper()}_THRESHOLD_NOT_MET")
    # A statistically powered, structurally valid cell that misses its fixed
    # target is a real negative result (BLOCKED), not missing evidence.
    if structural_reasons or power < 0.80:
        status = "INCONCLUSIVE"
    elif not tier_valid:
        status = "BLOCKED"
    else:
        status = "PASS"
    return FormalArenaCellResult(
        cell=cell,
        point_estimate=point,
        ci_lower=lo,
        ci_upper=hi,
        invalid_rate=invalid_rate,
        generated_clusters=len(generated),
        benchmark_clusters=len(benchmark),
        author_clusters=len(authors),
        sealed_pairs=len(pairs),
        t1_valid=tier_valid if tier == "t1" and status == "PASS" else False,
        t2_valid=tier_valid if tier == "t2" and status == "PASS" else False,
        status=status,
        reasons=tuple(dict.fromkeys(reasons)),
        bootstrap_iterations=bootstrap_iterations,
        h0=h0,
        h1=h1,
        mde=0.10,
        planned_required_pairs=planned_required_pairs,
        achieved_power=power,
    )


def evaluate_required_arena_cells(
    cells: dict[ArenaRequiredCell, list[FormalArenaPair]],
    *,
    required_cells: set[ArenaRequiredCell] | None = None,
    seed: int = 0,
    bootstrap_iterations: int = FORMAL_BOOTSTRAP_ITERATIONS,
) -> tuple[bool, dict[str, FormalArenaCellResult]]:
    """Require every sealed required cell and both T1/T2 evidence globally."""
    required = required_cells or set(cells)
    results: dict[str, FormalArenaCellResult] = {}
    for cell in sorted(required, key=lambda c: c.key):
        result = evaluate_formal_arena_cell(
            cell,
            cells.get(cell, []),
            seed=seed,
            bootstrap_iterations=bootstrap_iterations,
        )
        results[cell.key] = result
    passed = bool(required) and all(r.status == "PASS" for r in results.values())
    tiers = {r.cell.tier.lower() for r in results.values()}
    passed = passed and {"t1", "t2"}.issubset(tiers)
    return passed, results


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
        vs_t2_win_rate_min=float(section.get("vs_t2_win_rate_min", defaults.vs_t2_win_rate_min)),
        vs_t1_win_rate_min=float(section.get("vs_t1_win_rate_min", defaults.vs_t1_win_rate_min)),
        min_pairs_per_tier=int(section.get("min_pairs_per_tier", defaults.min_pairs_per_tier)),
    )


@dataclass(frozen=True)
class TargetEvaluation:
    passed: bool
    details: dict[str, Any] = field(default_factory=dict)


def evaluate_targets(
    summaries: dict[str, ArenaSummary], targets: BenchmarkTargets | None = None
) -> TargetEvaluation:
    """Legacy diagnostic evaluator.

    Both tiers are required.  Older callers may still use this function, but a
    result with a missing/underpowered tier is explicitly diagnostic and cannot
    be treated as a formal text-parity pass.
    """
    resolved = targets or load_benchmark_targets()
    floors = {"t1": resolved.vs_t1_win_rate_min, "t2": resolved.vs_t2_win_rate_min}
    details: dict[str, Any] = {}
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
        details[tier] = {
            "status": "pass" if ok else "fail",
            "win_rate": summary.win_rate,
            "floor": floor,
            "pairs": summary.pairs,
        }
    passed = all(details[tier].get("status") == "pass" for tier in floors)
    details["evaluation_status"] = (
        "diagnostic_pass" if passed else "diagnostic_inconclusive_or_fail"
    )
    details["formal_claims_allowed"] = False
    return TargetEvaluation(passed=passed, details=details)
