#!/usr/bin/env python
"""一本书的正文体检表——全部判据按密度/比例归一，可跨书直接对比。

2026-08-20 建立。当天在《罚我守坟》上逐项定罪出的缺陷（万物拟人、
第一人称漂移、对话饥饿、语料级词频放大）分散在各处一次性脚本里算过，
换一本书就得重算一遍。这个脚本把它们固化成同一张表，用途是
**修复部署前后拿同样的尺子量两本书**。

铁律（见 memory ai-flavor-score-is-length-biased，我当天踩过并更正）：
只有密度 / 比例归一的判据能跨文本比较；`overall_score` 这种绝对计数总分
在人类语料上同样随长度从 15.1 涨到 56.5，**必须按长度对齐后才能比**。

⚠️ 2026-08-22 更正：本脚本最初用「<2500 / 2500-3500 / …」四个宽段对照，
这本身仍然有偏置——`<2500` 是开放下界，人类语料该段均字 1970（大量
1200-1900 字的章），而我们的短章因为字数下限全部贴在 2100-2470（均 2244）。
同一段内分布不同，偏置就还在段里，只是尺度换小了：它把真实的 +4.3
放大成了 +11.7。**分段不是归一，插值才是。**
现在改为按每章自身字数在人类基线曲线上插值，逐章算 delta。

用法：
    python scripts/measure_book_prose_baseline.py <project-slug> [--json out.json]
"""

from __future__ import annotations

# ruff: noqa: RUF001, RUF002, RUF003 — 中文标点是刻意的。
import argparse
import asyncio
import collections
import itertools
import json
from pathlib import Path
import re
import statistics
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.infra.db.models import (
    ChapterDraftVersionModel,
    ChapterModel,
    ProjectModel,
)
from bestseller.infra.db.session import session_scope
from bestseller.services.ai_flavor.detector import detect
from bestseller.services.chapter_validator import (
    _split_sentences,
    _strip_quoted_dialogue,
)
from bestseller.settings import load_settings

ZH_RUN = re.compile(r"[一-鿿]+")
QUOTE = re.compile(r"[“\"「][^”\"」]{1,400}[”\"」]")

# 人类基线：.distillation_private 抽样，口径与下面各函数逐字一致。
HUMAN = {
    "dialogue_ratio": {"p05": 0.014, "p10": 0.031, "p25": 0.093, "median": 0.207},
    "first_person_sentences_per_40": {"p90": 2, "p99": 5, "max": 7},
    # AI 味总分随长度单调上升的人类基线曲线。
    # 每个锚点 = (该桶人类章的段内均字, 该桶 overall_score 均值)，
    # 由 .distillation_private 分 500 字一桶、每桶 220 章、同一个 detect() 算出
    # （n=2200，seed=23）。逐章按字数在相邻锚点间线性插值，段外取端点值。
    "score_curve": [
        (1296, 15.1),
        (1833, 22.6),
        (2234, 26.0),
        (2735, 30.8),
        (3232, 32.6),
        (3682, 34.1),
        (4309, 42.0),
        (4641, 43.7),
        (5604, 49.9),
        (7304, 56.5),
    ],
}


def human_score_at(chars: int) -> float:
    """人类在该字数上的 AI 味总分期望值（线性插值）。

    这是把「绝对计数总分」变成可比量的唯一正确做法：不分段，按每章
    自身长度取基线。分段之所以不够，是因为段内我们和人类的长度分布
    可以差几百字，而这条曲线每 100 字就值约 0.9 分。
    """

    curve = HUMAN["score_curve"]
    if chars <= curve[0][0]:
        return curve[0][1]
    if chars >= curve[-1][0]:
        return curve[-1][1]
    for (x0, y0), (x1, y1) in itertools.pairwise(curve):
        if x0 <= chars <= x1:
            return y0 + (y1 - y0) * (chars - x0) / (x1 - x0)
    return curve[-1][1]


def _zh_len(text: str) -> int:
    return sum(len(run) for run in ZH_RUN.findall(text))


def dialogue_ratio(text: str) -> float:
    total = _zh_len(text)
    if not total:
        return 0.0
    quoted = sum(_zh_len(m.group(0)) for m in QUOTE.finditer(text))
    return quoted / total


def first_person_share(text: str) -> float:
    """去对白后的叙述句里含「我」的比例——POV 漂移的量具。"""

    sentences = _split_sentences(_strip_quoted_dialogue(text, "zh-CN"), "zh-CN")
    if not sentences:
        return 0.0
    return sum(1 for s in sentences if "我" in s) / len(sentences)


def length_band(chars: int) -> str:
    """仅用于分组展示。判据本身走 :func:`human_score_at` 插值，不依赖分段。"""

    if chars < 2500:
        return "<2500"
    if chars < 3500:
        return "2500-3500"
    if chars < 5000:
        return "3500-5000"
    return ">=5000"


async def _load_current_chapters(session: AsyncSession, slug: str) -> list[tuple[int, str]]:
    project = await session.scalar(select(ProjectModel).where(ProjectModel.slug == slug))
    if project is None:
        raise SystemExit(f"project not found: {slug}")
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
    return [(int(n), md or "") for n, md in rows]


def measure(chapters: list[tuple[int, str]]) -> dict:
    per_chapter = []
    deltas: list[float] = []
    by_band: dict[str, list[float]] = collections.defaultdict(list)
    flags = collections.Counter()
    for number, text in chapters:
        if not text.strip():
            continue
        report = detect(text, language="zh-CN")
        categories = {span.category for span in report.spans}
        for name in (
            "inanimate_agency",
            "dialogue_starvation",
            "corpus_overamplified",
            "moment_slice",
        ):
            if name in categories:
                flags[name] += 1
        # ⚠️ 长度一律用汉字数：人类基线曲线的 x 轴就是汉字数。
        # 混用 len(text)（含标点、空行）会让插值整体偏移一格。
        chars = _zh_len(text)
        band = length_band(chars)
        by_band[band].append(report.overall_score)
        deltas.append(report.overall_score - human_score_at(chars))
        per_chapter.append(
            {
                "chapter": number,
                "chars": chars,
                "band": band,
                "ai_flavor_score": report.overall_score,
                "human_expected": round(human_score_at(chars), 1),
                "delta": round(report.overall_score - human_score_at(chars), 1),
                "dialogue_ratio": round(dialogue_ratio(text), 4),
                "first_person_share": round(first_person_share(text), 4),
                "flags": sorted(categories),
            }
        )

    dialogue = [row["dialogue_ratio"] for row in per_chapter]
    first_person = [row["first_person_share"] for row in per_chapter]
    return {
        "chapters": len(per_chapter),
        "dialogue_ratio": {
            "median": round(statistics.median(dialogue), 4) if dialogue else 0.0,
            "human_median": HUMAN["dialogue_ratio"]["median"],
            "below_human_p05": sum(1 for v in dialogue if v < HUMAN["dialogue_ratio"]["p05"]),
        },
        "first_person_drift": {
            # 全书声明第三人称时，叙述层含「我」超过 15% 即整章写错人称
            "chapters_over_15pct": sum(1 for v in first_person if v >= 0.15),
        },
        # 全书唯一的总分结论：逐章按自身字数插值人类基线后取平均。
        # 它不受章长分布影响——同一本书写长写短都不会改变这个数。
        "ai_flavor_delta": {
            "mean": round(statistics.mean(deltas), 1) if deltas else 0.0,
            "median": round(statistics.median(deltas), 1) if deltas else 0.0,
            "worse_than_human": sum(1 for v in deltas if v > 0),
            "n": len(deltas),
        },
        # 分段一栏只用于看**哪一档在失守**，段内均字必须同时看：
        # 段内我们和人类的长度分布差几百字，段均值就不可比（这是本脚本
        # 2026-08-22 改插值前犯过的错）。
        "ai_flavor_by_band": {
            band: {
                "n": len(values),
                "mean": round(statistics.mean(values), 1),
                "mean_delta": round(
                    statistics.mean([row["delta"] for row in per_chapter if row["band"] == band]),
                    1,
                ),
                "mean_chars": round(
                    statistics.mean([row["chars"] for row in per_chapter if row["band"] == band])
                ),
            }
            for band, values in sorted(by_band.items())
        },
        "detector_flags": dict(flags),
        "per_chapter": per_chapter,
    }


def render(slug: str, result: dict) -> str:
    lines = [f"# 正文体检 · {slug}", "", f"当前稿章数：{result['chapters']}", ""]
    d = result["dialogue_ratio"]
    lines.append(
        f"- 对话占比 中位 {d['median']:.1%}（人类 {d['human_median']:.1%}）；"
        f"低于人类 p05 的章 {d['below_human_p05']}"
    )
    drift = result["first_person_drift"]["chapters_over_15pct"]
    lines.append(f"- 第一人称漂移（声明第三人称时）：{drift} 章")
    ad = result["ai_flavor_delta"]
    lines.append(
        f"- **AI 味 vs 人类（逐章按字数插值基线）：均 {ad['mean']:+.1f} / "
        f"中位 {ad['median']:+.1f}；差于人类的章 {ad['worse_than_human']}/{ad['n']}**"
    )
    lines.append("- 分档明细（看哪一档失守；段内均字必须同看，否则段均值不可比）：")
    for band, stats in result["ai_flavor_by_band"].items():
        lines.append(
            f"    {band:10} n={stats['n']:2d}  段内均字 {stats['mean_chars']:5d}  "
            f"我们 {stats['mean']:5.1f}  较人类 {stats['mean_delta']:+5.1f}"
        )
    if result["detector_flags"]:
        lines.append("- 检测器命中章数：")
        for name, count in sorted(result["detector_flags"].items()):
            lines.append(f"    {name}: {count}")
    return "\n".join(lines)


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("slug")
    parser.add_argument("--json", dest="json_out")
    args = parser.parse_args()

    settings = load_settings()
    async with session_scope(settings) as session:
        chapters = await _load_current_chapters(session, args.slug)
    result = measure(chapters)
    print(render(args.slug, result))
    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )


if __name__ == "__main__":
    asyncio.run(main())
