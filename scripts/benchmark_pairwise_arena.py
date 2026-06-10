"""成对盲评 Arena 跑批：框架书章节 vs 真榜单书章节（榜单对标闭环 P1.1）。

用法：
  python scripts/benchmark_pairwise_arena.py \
      --fw-dir output/xianxia-upgrade-1781078378 \
      --category action-progression \
      --chapters 1,10 --per-tier 8

每个 (框架章节, 真书章节) 对做 position-swap 双向盲评；输出各层 win-rate、
分维度 win-rate，并按 config/benchmark_targets.yaml 判定 PASS/FAIL。
真书文本只在内存中使用，产物 JSON 不落真书正文。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
load_dotenv(".env")

import litellm  # noqa: E402

from bestseller.services.benchmark_arena import (  # noqa: E402
    ArenaMatchResult,
    ArenaPair,
    evaluate_targets,
    load_benchmark_targets,
    run_arena_match,
    summarize_arena,
)
from bestseller.services.benchmark_corpus import (  # noqa: E402
    load_benchmark_chapter,
    load_benchmark_corpus,
)
from bestseller.settings import get_settings  # noqa: E402

litellm.suppress_debug_info = True

SETTINGS = get_settings()
CRITIC = SETTINGS.llm.critic
CRITIC_KEY = os.environ.get(getattr(CRITIC, "api_key_env", "") or "")

# 跨家族判官（--judge deepseek）：判官与框架写手同源会产生自偏好偏置 ——
# 首跑 minimax 判官给出 87.5% 完胜《亵渎》级头部书的不可信结果。换家族复核。
_JUDGE_PRESETS: dict[str, dict[str, str | None]] = {
    "critic": {
        "model": CRITIC.model,
        "api_base": CRITIC.api_base,
        "api_key_env": getattr(CRITIC, "api_key_env", "") or "",
    },
    "deepseek": {
        "model": "deepseek/deepseek-chat",
        "api_base": None,
        "api_key_env": "DEEPSEEK_API_KEY",
    },
}
_JUDGE: dict[str, str | None] = dict(_JUDGE_PRESETS["critic"])

# 判官输入限长：两段正文 + 系统prompt 控制在 ~12k token 内
_MAX_CHARS_PER_TEXT = 4500
_WINDOW_HEAD = 2200
_WINDOW_TAIL = 2000


def _window(text: str) -> str:
    """对称头尾窗口：保留开头与**结尾**（钩子所在），中段省略。

    旧版从头部硬截断会把真书章节拦腰斩断（结尾钩子被砍掉），而框架章节
    短于限长保持完整 —— hook 维度被系统性白送。头尾窗口对两臂一视同仁。
    """
    body = text.strip()
    if len(body) <= _MAX_CHARS_PER_TEXT:
        return body
    return (
        body[:_WINDOW_HEAD].rstrip()
        + "\n……（中段省略）……\n"
        + body[-_WINDOW_TAIL:].lstrip()
    )


async def _llm_judge(system: str, user: str) -> str:
    response = await litellm.acompletion(
        model=_JUDGE["model"],
        api_base=_JUDGE["api_base"],
        api_key=os.environ.get(_JUDGE["api_key_env"] or "") or None,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=0.0,
        # reasoning 模型（MiniMax-M2.7）的思考也计入 max_tokens —— 600 会被
        # 长文本对的思考耗尽导致 content 为空、全场误判 tie。放宽到 4096。
        max_tokens=4096,
        timeout=300,
    )
    message = response.choices[0].message
    content = (message.content or "").strip()
    if not content:
        # 思考截断兜底：reasoning 末尾常已含结论 JSON
        content = (getattr(message, "reasoning_content", None) or "").strip()
    return content


def _build_pairs(args: argparse.Namespace) -> list[ArenaPair]:
    corpus = load_benchmark_corpus()
    if not corpus.available():
        print("FATAL: benchmark corpus unavailable — mount /Volumes/书籍")
        sys.exit(1)
    chapter_numbers = [int(x) for x in args.chapters.split(",") if x.strip()]
    fw_dir = Path(args.fw_dir)
    pairs: list[ArenaPair] = []
    for chapter_no in chapter_numbers:
        fw_path = fw_dir / f"chapter-{chapter_no:03d}.md"
        if not fw_path.is_file():
            print(f"skip ch{chapter_no}: {fw_path} missing")
            continue
        fw_text = _window(fw_path.read_text(encoding="utf-8"))
        for tier in ("t1", "t2"):
            tier_books = [
                book
                for book in corpus.by_tier(tier)
                if not args.category or book.category == args.category
            ]
            if not tier_books:
                # 同题材无书时回退全题材（报告中标注 category 仍为真书自身题材）
                tier_books = list(corpus.by_tier(tier))
            used = 0
            for book in tier_books:
                if used >= args.per_tier:
                    break
                body = load_benchmark_chapter(book, chapter_no)
                if not body or len(body) < 800:
                    continue
                pairs.append(
                    ArenaPair(
                        pair_id=f"{tier}-{book.source_id}-ch{chapter_no}",
                        framework_text=fw_text,
                        benchmark_text=_window(body),
                        benchmark_tier=tier,
                        category=book.category,
                        chapter_number=chapter_no,
                        framework_label=fw_dir.name,
                        benchmark_label=book.title_key,
                    )
                )
                used += 1
    return pairs


def _result_row(result: ArenaMatchResult) -> dict[str, Any]:
    return {
        "pair_id": result.pair.pair_id,
        "tier": result.pair.benchmark_tier,
        "category": result.pair.category,
        "chapter": result.pair.chapter_number,
        "benchmark": result.pair.benchmark_label,
        "outcome": result.outcome,
        "dimensions": result.dimension_outcomes,
        "forward_reason": result.forward.reason if result.forward else None,
        "backward_reason": result.backward.reason if result.backward else None,
    }


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fw-dir", required=True, help="框架书目录（含 chapter-NNN.md）")
    parser.add_argument("--category", default=None, help="对标题材（canonical_category）")
    parser.add_argument("--chapters", default="1", help="逗号分隔章位")
    parser.add_argument("--per-tier", type=int, default=8, help="每层每章位对战书数")
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument(
        "--judge",
        default="critic",
        choices=sorted(_JUDGE_PRESETS),
        help="判官预设：critic=项目critic模型；deepseek=跨家族复核（抗自偏好偏置）",
    )
    args = parser.parse_args()
    _JUDGE.update(_JUDGE_PRESETS[args.judge])

    pairs = _build_pairs(args)
    if not pairs:
        print("FATAL: no pairs built")
        sys.exit(1)
    print(f"arena: {len(pairs)} pairs × 2 directions on {_JUDGE['model']}")

    semaphore = asyncio.Semaphore(args.concurrency)

    async def _run(pair: ArenaPair) -> ArenaMatchResult:
        async with semaphore:
            result = await run_arena_match(pair, _llm_judge)
        print(f"  [{result.outcome:4s}] {pair.pair_id} vs {pair.benchmark_label[:20]}")
        return result

    results = list(await asyncio.gather(*(_run(pair) for pair in pairs)))

    summaries = {tier: summarize_arena(results, tier=tier) for tier in ("t1", "t2")}
    targets = load_benchmark_targets()
    evaluation = evaluate_targets(summaries, targets)

    out_dir = Path("output/benchmark_arena") / time.strftime("%Y%m%dT%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "fw_dir": args.fw_dir,
        "chapters": args.chapters,
        "model": _JUDGE["model"],
        "summaries": {
            tier: {
                "pairs": s.pairs,
                "wins": s.wins,
                "losses": s.losses,
                "ties": s.ties,
                "win_rate": s.win_rate,
                "dimension_win_rates": s.dimension_win_rates,
            }
            for tier, s in summaries.items()
        },
        "evaluation": {"passed": evaluation.passed, "details": evaluation.details},
        "results": [_result_row(r) for r in results],
    }
    (out_dir / "arena.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8"
    )

    print("\n=== Arena 结果 ===")
    for tier, summary in summaries.items():
        print(
            f"vs {tier.upper()}: win-rate {summary.win_rate:.3f} "
            f"({summary.wins}W/{summary.ties}T/{summary.losses}L, n={summary.pairs})"
        )
        for key, rate in summary.dimension_win_rates.items():
            print(f"    {key:18s} {rate:.3f}")
    print(f"\n验收判定：{'PASS' if evaluation.passed else 'FAIL'} — {evaluation.details}")
    print(f"artifacts: {out_dir}/")


if __name__ == "__main__":
    asyncio.run(main())
