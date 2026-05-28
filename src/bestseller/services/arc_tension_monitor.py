from __future__ import annotations

# ruff: noqa: RUF001
from collections.abc import Sequence
from dataclasses import dataclass
import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.infra.db.models import ChapterDraftVersionModel, ChapterModel, ProjectModel


@dataclass(frozen=True)
class ChapterTensionScore:
    chapter_number: int
    cliffhanger_strength: float
    information_release_density: float
    conflict_intensity: float
    overall_tension: float


@dataclass(frozen=True)
class ArcTensionReport:
    per_chapter: tuple[ChapterTensionScore, ...]
    sagging_intervals: tuple[tuple[int, int], ...]
    suggestions_for_next: dict[int, list[str]]


async def compute_arc_tension(
    session: AsyncSession,
    project: ProjectModel,
    *,
    chapter_number_upto: int | None = None,
) -> ArcTensionReport:
    query = (
        select(ChapterModel.chapter_number, ChapterDraftVersionModel.content_md)
        .join(ChapterDraftVersionModel, ChapterDraftVersionModel.chapter_id == ChapterModel.id)
        .where(
            ChapterModel.project_id == project.id,
            ChapterDraftVersionModel.is_current.is_(True),
        )
        .order_by(ChapterModel.chapter_number.asc())
    )
    if chapter_number_upto is not None:
        query = query.where(ChapterModel.chapter_number < int(chapter_number_upto))
    result = await session.execute(query)
    return compute_arc_tension_from_texts(
        tuple((int(ch), str(text or "")) for ch, text in result.all())
    )


def compute_arc_tension_from_texts(chapter_texts: Sequence[tuple[int, str]]) -> ArcTensionReport:
    scores = tuple(_score_chapter(chapter, text) for chapter, text in chapter_texts)
    if not scores:
        return ArcTensionReport((), (), {})
    mean = sum(item.overall_tension for item in scores) / len(scores)
    sagging = _find_sagging(scores, mean)
    suggestions: dict[int, list[str]] = {}
    for _start, end in sagging:
        suggestions[end + 1] = [
            "兑现至少一条既有伏笔或释放新的可验证信息。",
            "制造具体对抗升级或人物代价。",
            "章末钩子落到可见威胁，不要只写抽象情绪。",
        ]
    return ArcTensionReport(scores, sagging, suggestions)


def render_arc_tension_block(
    report: ArcTensionReport,
    *,
    chapter_number: int,
    language: str = "zh-CN",
) -> str:
    suggestions = report.suggestions_for_next.get(int(chapter_number), [])
    if not suggestions:
        return ""
    if str(language or "").lower().startswith("en"):
        return "[Arc tension warning]\n" + "\n".join(f"- {item}" for item in suggestions)
    return "【整体弧线状态】\n警告：近期章节张力低于均值。本章必须提升 tension：\n" + "\n".join(
        f"- {item}" for item in suggestions
    )


def _score_chapter(chapter_number: int, text: str) -> ChapterTensionScore:
    tail = re.sub(r"\s+", "", text or "")[-160:]
    cliff_terms = ("？", "什么", "谁", "死", "血", "响", "裂")
    info_terms = ("发现", "证据", "真相", "账", "线索", "照片")
    conflict_terms = ("冲", "撞", "抓", "逼", "威胁", "死", "痛", "挡")
    cliff = min(1.0, sum(tail.count(term) for term in cliff_terms) / 4)
    info = min(1.0, sum((text or "").count(term) for term in info_terms) / 8)
    conflict = min(1.0, sum((text or "").count(term) for term in conflict_terms) / 10)
    overall = round(cliff * 0.4 + info * 0.3 + conflict * 0.3, 4)
    return ChapterTensionScore(chapter_number, cliff, info, conflict, overall)


def _find_sagging(
    scores: Sequence[ChapterTensionScore],
    mean: float,
) -> tuple[tuple[int, int], ...]:
    intervals: list[tuple[int, int]] = []
    start: int | None = None
    previous: int | None = None
    for score in scores:
        if score.overall_tension < mean:
            if start is None:
                start = score.chapter_number
            previous = score.chapter_number
            continue
        if start is not None and previous is not None and previous - start + 1 >= 3:
            intervals.append((start, previous))
        start = None
        previous = None
    if start is not None and previous is not None and previous - start + 1 >= 3:
        intervals.append((start, previous))
    return tuple(intervals)


__all__ = [
    "ArcTensionReport",
    "ChapterTensionScore",
    "compute_arc_tension",
    "compute_arc_tension_from_texts",
    "render_arc_tension_block",
]
