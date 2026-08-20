#!/usr/bin/env python
"""一本书的正文体检表——全部判据按密度/比例归一，可跨书直接对比。

2026-08-20 建立。当天在《罚我守坟》上逐项定罪出的缺陷（万物拟人、
第一人称漂移、对话饥饿、语料级词频放大）分散在各处一次性脚本里算过，
换一本书就得重算一遍。这个脚本把它们固化成同一张表，用途是
**修复部署前后拿同样的尺子量两本书**。

铁律（见 memory ai-flavor-score-is-length-biased，我当天踩过并更正）：
只有密度 / 比例归一的判据能跨文本比较；`overall_score` 这种绝对计数总分
在人类语料上同样随长度从 18.9 涨到 56.0，**只能在同长度段内比**。
所以这里输出的每一项都带人类基线，且总分一栏强制按字数分段呈现。

用法：
    python scripts/measure_book_prose_baseline.py <project-slug> [--json out.json]
"""

from __future__ import annotations

import argparse
import asyncio
import collections
import json
import re
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

from bestseller.infra.db.models import (  # noqa: E402
    ChapterDraftVersionModel,
    ChapterModel,
    ProjectModel,
)
from bestseller.infra.db.session import session_scope  # noqa: E402
from bestseller.services.ai_flavor.detector import detect  # noqa: E402
from bestseller.services.chapter_validator import (  # noqa: E402
    _split_sentences,
    _strip_quoted_dialogue,
)
from bestseller.settings import load_settings  # noqa: E402

ZH_RUN = re.compile(r"[一-鿿]+")
QUOTE = re.compile(r"[“\"「][^”\"」]{1,400}[”\"」]")

# 人类基线：.distillation_private 抽样，口径与下面各函数逐字一致。
HUMAN = {
    "dialogue_ratio": {"p05": 0.014, "p10": 0.031, "p25": 0.093, "median": 0.207},
    "first_person_sentences_per_40": {"p90": 2, "p99": 5, "max": 7},
    "score_by_length": {
        "<2500": 18.9,
        "2500-3500": 27.9,
        "3500-5000": 37.5,
        ">=5000": 56.0,
    },
}


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
    if chars < 2500:
        return "<2500"
    if chars < 3500:
        return "2500-3500"
    if chars < 5000:
        return "3500-5000"
    return ">=5000"


async def _load_current_chapters(
    session: AsyncSession, slug: str
) -> list[tuple[int, str]]:
    project = await session.scalar(
        select(ProjectModel).where(ProjectModel.slug == slug)
    )
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
        band = length_band(len(text))
        by_band[band].append(report.overall_score)
        per_chapter.append(
            {
                "chapter": number,
                "chars": len(text),
                "band": band,
                "ai_flavor_score": report.overall_score,
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
            "below_human_p05": sum(
                1 for v in dialogue if v < HUMAN["dialogue_ratio"]["p05"]
            ),
        },
        "first_person_drift": {
            # 全书声明第三人称时，叙述层含「我」超过 15% 即整章写错人称
            "chapters_over_15pct": sum(1 for v in first_person if v >= 0.15),
        },
        "ai_flavor_score_by_band": {
            band: {
                "n": len(values),
                "mean": round(statistics.mean(values), 1),
                "human_mean": HUMAN["score_by_length"][band],
                "delta": round(statistics.mean(values) - HUMAN["score_by_length"][band], 1),
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
    lines.append(
        f"- 第一人称漂移（声明第三人称时）：{result['first_person_drift']['chapters_over_15pct']} 章"
    )
    lines.append("- AI 味总分**必须分字数段看**（该分数随长度漂移，跨段比较无效）：")
    for band, stats in result["ai_flavor_score_by_band"].items():
        lines.append(
            f"    {band:10} n={stats['n']:2d}  我们 {stats['mean']:5.1f}  "
            f"人类 {stats['human_mean']:5.1f}  差 {stats['delta']:+5.1f}"
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
