"""弃书曲线对比：框架书 vs 真榜单书 前 N 章逐章弃书概率（榜单对标闭环 P1.2）。

读者仿真判官对每一章输出「读完本章后继续读下一章的概率」(0-1)。绝对值不可
靠，但同一判官、同一 prompt 跨臂的**相对曲线**可比：框架书曲线不应低于
T2 真书均值曲线。

用法：
  python scripts/benchmark_dropoff_curve.py \
      --fw-dir output/xianxia-upgrade-1781078378 \
      --max-chapter 10 --books-per-tier 5
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
load_dotenv(".env")

import litellm  # noqa: E402

from bestseller.services.benchmark_corpus import (  # noqa: E402
    load_benchmark_chapter,
    load_benchmark_corpus,
)
from bestseller.settings import get_settings  # noqa: E402

litellm.suppress_debug_info = True

SETTINGS = get_settings()
CRITIC = SETTINGS.llm.critic
CRITIC_KEY = os.environ.get(getattr(CRITIC, "api_key_env", "") or "")

_MAX_CHARS = 5000

_SYSTEM = (
    "你是中文网文平台的真实读者行为模拟器。你会看到某本书的一章正文（书名已去除）。\n"
    "结合该章在书中的位置，估计一个刚读完这一章的目标读者继续点开下一章的概率。\n"
    "评估依据：章末钩子强度、本章情绪回报、信息密度、人物吸引力、阅读阻力"
    "（流水账/说明书腔/混乱会提高弃书率）。\n"
    "前3章读者最没耐心，标准最苛刻。\n"
    '输出严格 JSON：{"continue_probability": 0.0-1.0, "main_risk": "≤30字弃书主因"}'
)


def _parse(raw: str) -> dict[str, Any] | None:
    text = raw.strip().strip("`")
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        payload = json.loads(text[start : end + 1])
        probability = float(payload.get("continue_probability"))
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    if not 0.0 <= probability <= 1.0:
        return None
    return {"p": probability, "risk": str(payload.get("main_risk") or "")[:60]}


async def _judge_chapter(text: str, chapter_no: int, semaphore: asyncio.Semaphore) -> dict[str, Any] | None:
    user = f"章节位置：第{chapter_no}章。\n\n正文：\n{text[:_MAX_CHARS]}\n\n请输出 JSON。"
    async with semaphore:
        try:
            response = await litellm.acompletion(
                model=CRITIC.model,
                api_base=CRITIC.api_base,
                api_key=CRITIC_KEY,
                messages=[
                    {"role": "system", "content": _SYSTEM},
                    {"role": "user", "content": user},
                ],
                temperature=0.0,
                # reasoning 模型的思考计入 max_tokens，给足余量防 content 截断
                max_tokens=2048,
                timeout=300,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"    judge failed ch{chapter_no}: {type(exc).__name__}")
            return None
    message = response.choices[0].message
    content = (message.content or "").strip() or (
        getattr(message, "reasoning_content", None) or ""
    ).strip()
    return _parse(content)


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fw-dir", required=True)
    parser.add_argument("--max-chapter", type=int, default=10)
    parser.add_argument("--books-per-tier", type=int, default=5)
    parser.add_argument("--category", default=None)
    parser.add_argument("--concurrency", type=int, default=6)
    args = parser.parse_args()

    corpus = load_benchmark_corpus()
    if not corpus.available():
        print("FATAL: corpus unavailable — mount /Volumes/书籍")
        sys.exit(1)

    semaphore = asyncio.Semaphore(args.concurrency)
    arms: dict[str, list[dict[str, Any]]] = {"fw": [], "t1": [], "t2": []}

    # 框架臂
    fw_dir = Path(args.fw_dir)
    fw_jobs = []
    for chapter_no in range(1, args.max_chapter + 1):
        path = fw_dir / f"chapter-{chapter_no:03d}.md"
        if path.is_file():
            fw_jobs.append((fw_dir.name, chapter_no, path.read_text(encoding="utf-8")))

    # 真书臂
    real_jobs: list[tuple[str, str, int, str]] = []
    for tier in ("t1", "t2"):
        books = [
            book
            for book in corpus.by_tier(tier)
            if not args.category or book.category == args.category
        ] or list(corpus.by_tier(tier))
        for book in books[: args.books_per_tier]:
            for chapter_no in range(1, args.max_chapter + 1):
                body = load_benchmark_chapter(book, chapter_no)
                if body and len(body) > 800:
                    real_jobs.append((tier, book.title_key, chapter_no, body))

    print(f"dropoff: fw={len(fw_jobs)} t1/t2 chapters={len(real_jobs)} on {CRITIC.model}")

    async def _run_fw(label: str, chapter_no: int, text: str) -> None:
        verdict = await _judge_chapter(text, chapter_no, semaphore)
        if verdict:
            arms["fw"].append({"book": label, "chapter": chapter_no, **verdict})

    async def _run_real(tier: str, label: str, chapter_no: int, text: str) -> None:
        verdict = await _judge_chapter(text, chapter_no, semaphore)
        if verdict:
            arms[tier].append({"book": label, "chapter": chapter_no, **verdict})

    await asyncio.gather(
        *(_run_fw(*job) for job in fw_jobs),
        *(_run_real(*job) for job in real_jobs),
    )

    # 曲线汇总：每臂每章位的 continue_probability 中位
    def _curve(rows: list[dict[str, Any]]) -> dict[int, float]:
        curve: dict[int, float] = {}
        for chapter_no in range(1, args.max_chapter + 1):
            values = [row["p"] for row in rows if row["chapter"] == chapter_no]
            if values:
                curve[chapter_no] = round(statistics.median(values), 3)
        return curve

    curves = {arm: _curve(rows) for arm, rows in arms.items()}
    out_dir = Path("output/benchmark_dropoff") / time.strftime("%Y%m%dT%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "dropoff.json").write_text(
        json.dumps({"curves": curves, "rows": arms}, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )

    print("\n=== 弃书曲线（继续读概率中位，越高越好）===")
    print("章位 | " + " | ".join(f"{arm}" for arm in ("fw", "t1", "t2")))
    below = 0
    comparable = 0
    for chapter_no in range(1, args.max_chapter + 1):
        fw_p = curves["fw"].get(chapter_no)
        t2_p = curves["t2"].get(chapter_no)
        row = f"ch{chapter_no:02d} | " + " | ".join(
            f"{curves[arm].get(chapter_no, '-')}" for arm in ("fw", "t1", "t2")
        )
        if fw_p is not None and t2_p is not None:
            comparable += 1
            if fw_p < t2_p:
                below += 1
                row += "  ← 低于T2"
        print(row)
    if comparable:
        verdict = "PASS" if below <= comparable * 0.3 else "FAIL"
        print(f"\n判定：{verdict}（{below}/{comparable} 章位低于 T2 中位，阈值 30%）")
    print(f"artifacts: {out_dir}/")


if __name__ == "__main__":
    asyncio.run(main())
