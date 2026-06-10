"""每书自动对标回归（榜单对标闭环 P4.2）。

一本书跑完后自动抽样章位与真书语料做成对盲评，win-rate 写入
``output/<slug>/audits/benchmark/``（``arena.json`` 最新一次 +
``arena_history.jsonl`` 趋势），供 dossier 展示与跨书回归。

判官口径：默认 DeepSeek（跨家族，抗自偏好偏置 —— 同源判官实测高估
win-rate 0.15-0.20，见 config/benchmark_targets.yaml）；无 DEEPSEEK_API_KEY
时回退项目 critic 并在产物中标注 ``judge_family_warning``。

全程 advisory：语料卷未挂载 / 无 key / 判官失败都安静返回 None，
绝不影响成书流程。
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

from bestseller.services.benchmark_arena import (
    ArenaMatchResult,
    ArenaPair,
    evaluate_targets,
    load_benchmark_targets,
    run_arena_match,
    summarize_arena,
)
from bestseller.services.benchmark_corpus import (
    load_benchmark_chapter,
    load_benchmark_corpus,
)

logger = logging.getLogger(__name__)

AUTO_BENCHMARK_ENV = "AUTO_BENCHMARK_REGRESSION"

_MAX_CHARS_PER_TEXT = 4500
_WINDOW_HEAD = 2200
_WINDOW_TAIL = 2000


def auto_benchmark_regression_enabled() -> bool:
    return os.getenv(AUTO_BENCHMARK_ENV, "1").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


def _window(text: str) -> str:
    """对称头尾窗口（与 scripts/benchmark_pairwise_arena.py 同口径）。"""
    body = text.strip()
    if len(body) <= _MAX_CHARS_PER_TEXT:
        return body
    return (
        body[:_WINDOW_HEAD].rstrip()
        + "\n……（中段省略）……\n"
        + body[-_WINDOW_TAIL:].lstrip()
    )


def _resolve_judge() -> dict[str, Any] | None:
    """Pick the cross-family judge; fall back to project critic with warning."""
    deepseek_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if deepseek_key:
        return {
            "model": "deepseek/deepseek-chat",
            "api_base": None,
            "api_key": deepseek_key,
            "cross_family": True,
        }
    try:
        from bestseller.settings import get_settings

        critic = get_settings().llm.critic
        critic_key = os.environ.get(getattr(critic, "api_key_env", "") or "", "")
        if critic_key:
            return {
                "model": critic.model,
                "api_base": critic.api_base,
                "api_key": critic_key,
                "cross_family": False,
            }
    except Exception:  # noqa: BLE001
        logger.debug("benchmark regression: critic fallback unavailable", exc_info=True)
    return None


def _build_pairs(
    book_dir: Path, chapter_numbers: list[int], per_tier: int
) -> list[ArenaPair]:
    corpus = load_benchmark_corpus()
    if not corpus.available():
        return []
    pairs: list[ArenaPair] = []
    for chapter_no in chapter_numbers:
        fw_path = book_dir / f"chapter-{chapter_no:03d}.md"
        if not fw_path.is_file():
            continue
        try:
            fw_text = _window(fw_path.read_text(encoding="utf-8"))
        except OSError:
            continue
        if len(fw_text) < 800:
            continue
        for tier in ("t1", "t2"):
            used = 0
            for book in corpus.by_tier(tier):
                if used >= per_tier:
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
                        framework_label=book_dir.name,
                        benchmark_label=book.title_key,
                    )
                )
                used += 1
    return pairs


async def run_benchmark_regression(
    project_slug: str,
    *,
    output_base_dir: str | Path = "output",
    chapter_numbers: list[int] | None = None,
    per_tier: int = 4,
    concurrency: int = 3,
) -> dict[str, Any] | None:
    """Run the post-book benchmark arena; returns the report dict or None."""
    import asyncio

    import litellm

    judge_config = _resolve_judge()
    if judge_config is None:
        logger.info("benchmark regression skipped: no judge credentials")
        return None
    book_dir = Path(output_base_dir) / project_slug
    chapters = chapter_numbers or [1, 10]
    pairs = _build_pairs(book_dir, chapters, per_tier)
    if not pairs:
        logger.info(
            "benchmark regression skipped for %s: no pairs (volume unmounted or no chapters)",
            project_slug,
        )
        return None

    async def _judge(system: str, user: str) -> str:
        response = await litellm.acompletion(
            model=judge_config["model"],
            api_base=judge_config["api_base"],
            api_key=judge_config["api_key"],
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0.0,
            max_tokens=4096,
            timeout=300,
        )
        message = response.choices[0].message
        return (message.content or "").strip() or (
            getattr(message, "reasoning_content", None) or ""
        ).strip()

    semaphore = asyncio.Semaphore(concurrency)

    async def _run(pair: ArenaPair) -> ArenaMatchResult:
        async with semaphore:
            return await run_arena_match(pair, _judge)

    results = list(await asyncio.gather(*(_run(pair) for pair in pairs)))
    summaries = {tier: summarize_arena(results, tier=tier) for tier in ("t1", "t2")}
    evaluation = evaluate_targets(summaries, load_benchmark_targets())

    report: dict[str, Any] = {
        "project_slug": project_slug,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "judge_model": judge_config["model"],
        "chapters": chapters,
        "pairs": len(pairs),
        "summaries": {
            tier: {
                "pairs": summary.pairs,
                "wins": summary.wins,
                "losses": summary.losses,
                "ties": summary.ties,
                "win_rate": summary.win_rate,
                "dimension_win_rates": summary.dimension_win_rates,
            }
            for tier, summary in summaries.items()
        },
        "evaluation": {"passed": evaluation.passed, "details": evaluation.details},
    }
    if not judge_config["cross_family"]:
        report["judge_family_warning"] = (
            "judge 与写手同源，win-rate 系统性偏高 ~0.15-0.20，仅作趋势参考"
        )

    audit_dir = book_dir / "audits" / "benchmark"
    try:
        audit_dir.mkdir(parents=True, exist_ok=True)
        (audit_dir / "arena.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        with (audit_dir / "arena_history.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(report, ensure_ascii=False) + "\n")
    except OSError:
        logger.warning("benchmark regression artifacts not written", exc_info=True)
    logger.info(
        "benchmark regression %s: vs_t1=%s vs_t2=%s passed=%s",
        project_slug,
        report["summaries"]["t1"]["win_rate"],
        report["summaries"]["t2"]["win_rate"],
        evaluation.passed,
    )
    return report


def load_benchmark_report(
    project_slug: str, *, output_base_dir: str | Path = "output"
) -> dict[str, Any] | None:
    """Load the latest arena report for a project (None when absent)."""
    path = Path(output_base_dir) / project_slug / "audits" / "benchmark" / "arena.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None
