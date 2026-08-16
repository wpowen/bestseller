#!/usr/bin/env python3
"""用**当前版本**的读者判官重跑历史书，拿同源基线。

为什么需要它：`reader_judge` 的 payoff_density 判据在 2026-08-16 换成了读者
三段律（赢落到有名字的人 / 被具体的人看见 / 账上留下能带走的东西），
prompt_version 从 1.1 升到 1.2。升版本换来了归因能力——能证明某本书用的是
哪一版判据——代价是**旧书的读数不能再和新书比**：它们是两把尺子量的。

没有同源基线，「新书 payoff 中位 0.37」这个数字没有意义：不知道是好是坏。

本脚本只读不写：拉历史章的正文，用当前判官重新打分，打印分布。
不落库，不改任何章的 metadata——历史读数保持原样，免得覆盖掉 v1.1 的记录。

⚠️ **必须重复采样**。真机实测：同一段文字（内容 md5 未变、当前稿版本未变）
两次评分给出 0.88 和 0.78——判官单次读数的方差约 0.10。用单次评分去判别
0.1 量级的差异，得到的是噪声。本脚本默认每章评 3 次取均值，并打印章内极差，
让噪声本身可见。

用法：
    python scripts/rejudge_payoff_same_instrument.py <slug> [<slug>...] [-n 20] [-k 3]
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
import statistics
import sys


sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from sqlalchemy import select  # noqa: E402

from bestseller.infra.db.models import (  # noqa: E402
    ChapterDraftVersionModel,
    ChapterModel,
    ProjectModel,
)
from bestseller.infra.db.session import session_scope  # noqa: E402
from bestseller.services.reader_judge import judge_chapter_readability  # noqa: E402
from bestseller.settings import load_settings  # noqa: E402


async def _chapters(session, slug: str, limit: int):
    project = (
        await session.scalars(select(ProjectModel).where(ProjectModel.slug == slug))
    ).first()
    if project is None:
        return None, []
    rows = (
        await session.execute(
            select(ChapterModel.chapter_number, ChapterDraftVersionModel.content_md)
            .join(
                ChapterDraftVersionModel,
                ChapterDraftVersionModel.chapter_id == ChapterModel.id,
            )
            .where(
                ChapterModel.project_id == project.id,
                ChapterDraftVersionModel.is_current.is_(True),
            )
            .order_by(ChapterModel.chapter_number)
        )
    ).all()
    # 均匀抽样而不是取前 N 章：开篇章的爽点密度按设计就更高
    # （黄金三章 min_count=2），只看前 N 章会系统性高估整本书。
    usable = [(n, c) for n, c in rows if c and len(c) > 800]
    if limit and len(usable) > limit:
        step = len(usable) / limit
        usable = [usable[int(i * step)] for i in range(limit)]
    return project, usable


async def _judge_repeated(session, settings, content, number, project_id, k):
    """同一章评 k 次，返回 (均值, 极差, 有效次数)。

    判官的 temperature 是确定性档，但仍有 ~0.10 的读数方差（真机实测：
    同一段文字两次给 0.88 / 0.78）。取均值 + 打印极差，让噪声可见而不是
    被一个漂亮的单次读数掩盖。
    """

    scores = []
    for _ in range(k):
        result = await judge_chapter_readability(
            session, settings, content, chapter_number=number, project_id=project_id
        )
        payoff = result.dimensions.get("payoff_density")
        if payoff is not None and result.used_llm:
            scores.append(float(payoff))
    if not scores:
        return None, None, 0, ""
    spread = max(scores) - min(scores)
    return statistics.fmean(scores), spread, len(scores), result.comment


async def run(slugs: list[str], limit: int, repeats: int) -> int:
    settings = load_settings()
    summary: dict[str, list[float]] = {}

    async with session_scope(settings) as session:
        for slug in slugs:
            project, chapters = await _chapters(session, slug, limit)
            if project is None:
                print(f"[{slug}] 项目不存在")
                continue
            if not chapters:
                print(f"[{slug}] 无可评章节")
                continue

            print(f"\n=== {project.title}（{slug}）· {project.genre} ===")
            print(f"    均匀抽样 {len(chapters)} 章，用当前判官重评（只读，不落库）")
            scores: list[float] = []
            spreads: list[float] = []
            for number, content in chapters:
                mean, spread, got, comment = await _judge_repeated(
                    session, settings, content, number, project.id, repeats
                )
                if mean is None:
                    print(f"    ch{number:<4} —（判官未生效）")
                    continue
                scores.append(mean)
                spreads.append(spread)
                flag = "  ⚠️噪声大" if spread > 0.15 else ""
                print(
                    f"    ch{number:<4} payoff={mean:.2f} (±{spread:.2f}, {got}次)"
                    f"{flag}  『{comment}』"
                )
            if spreads:
                print(f"    —— 章内极差中位 {statistics.median(spreads):.2f}（判别力下限）")
            if scores:
                summary[f"{project.title}｜{project.genre}"] = scores

    if not summary:
        print("\n没有拿到任何读数。")
        return 1

    print("\n" + "=" * 60)
    print(f"{'书':28s} {'n':>3s} {'中位':>6s} {'均值':>6s} {'最低':>6s} {'最高':>6s}")
    for name, scores in summary.items():
        scores_sorted = sorted(scores)
        print(
            f"{name:28s} {len(scores):>3d} "
            f"{statistics.median(scores_sorted):>6.2f} "
            f"{statistics.fmean(scores_sorted):>6.2f} "
            f"{scores_sorted[0]:>6.2f} {scores_sorted[-1]:>6.2f}"
        )
    print("\n同一把尺子量的，可以直接比。")
    print("⚠️ 判别时先看章内极差：小于极差的书间差异是噪声，不是改善或退化。")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("slugs", nargs="+")
    parser.add_argument("-n", "--limit", type=int, default=15)
    parser.add_argument(
        "-k", "--repeats", type=int, default=3,
        help="每章重复评分次数（默认 3；单次评分测不出 0.1 量级差异）",
    )
    args = parser.parse_args()
    return asyncio.run(run(args.slugs, args.limit, args.repeats))


if __name__ == "__main__":
    raise SystemExit(main())
