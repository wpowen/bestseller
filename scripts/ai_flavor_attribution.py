#!/usr/bin/env python
"""Is the AI flavour ours, or the model's?

The question this answers
-------------------------
When a finished chapter reads like AI, there are two very different causes and
they need opposite fixes:

* **The model** writes that way regardless — our prompts are innocent and the
  lever is the model or the genre, not the prompt stack.
* **Our prompt stack** induces it — the framework is teaching the model to write
  badly, and every rule we add makes it worse.

Telling them apart needs a control, so this runs two arms over the *same*
chapter plan and scores both with the same detector:

  A  production   the chapter the framework actually produced (already on disk,
                  costs nothing to evaluate)
  B  bare         the same chapter regenerated from a minimal prompt — roughly
                  what someone would type into a chat window

``B`` deliberately carries no craft rules, no anti-AI discipline, no contract
block. If B scores as badly as A, the flavour is the model's. If B scores
markedly better, the flavour is ours and the prompt stack is the defect.

Read-mostly: one LLM call per sampled chapter for arm B, and the session is
rolled back so nothing is persisted.

    python scripts/ai_flavor_attribution.py --slug <slug> --chapters 1,2,3
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Sequence
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sqlalchemy import select  # noqa: E402

from bestseller.infra.db.models import (  # noqa: E402
    ChapterDraftVersionModel,
    ChapterModel,
    ProjectModel,
)
from bestseller.infra.db.session import (  # noqa: E402
    create_engine,
    create_session_factory,
)
from bestseller.services.ai_flavor.detector import detect  # noqa: E402
from bestseller.services.llm import LLMCompletionRequest, complete_text  # noqa: E402
from bestseller.settings import load_settings  # noqa: E402


#: Sentinel returned when the control-arm LLM call fails. Scoring it would
#: compare our prose against a framework-authored string and invent a verdict.
_BARE_ARM_FALLBACK = "__BARE_ARM_UNAVAILABLE__"


@dataclass
class ArmResult:
    label: str
    chars: int
    score: float
    span_count: int
    top_categories: tuple[str, ...]


@dataclass
class ChapterComparison:
    chapter_number: int
    production: ArmResult
    bare: ArmResult
    draft_versions: int = 0
    production_state: str = ""

    @property
    def delta(self) -> float:
        """Positive = production is worse than the bare model."""

        return self.production.score - self.bare.score

    @property
    def comparable(self) -> bool:
        """Whether this chapter has settled enough to be compared.

        Drafts change under the measurement: chapter 1 of
        custom-xuanhuan-1785980083 scored 8.0 at 2482 chars and was a different
        3680-char text an hour later. Scoring a chapter that the pipeline is
        still rewriting compares an early draft against another chapter's
        finished one, and the difference reads as a prompt effect when it is
        only a difference in how far each chapter got.
        """

        return self.production_state.lower() in ("ok", "published", "final")


def _score_arm(label: str, text: str, chapter_number: int) -> ArmResult:
    report = detect(text, language="zh-CN", chapter_number=chapter_number)
    counts: dict[str, int] = {}
    for span in report.spans:
        counts[span.category] = counts.get(span.category, 0) + 1
    top = tuple(
        name for name, _ in sorted(counts.items(), key=lambda kv: -kv[1])[:4]
    )
    return ArmResult(
        label=label,
        chars=len(text),
        score=float(report.overall_score),
        span_count=len(report.spans),
        top_categories=top,
    )


def _bare_prompts(
    *, project: ProjectModel, chapter: ChapterModel, previous_tail: str
) -> tuple[str, str]:
    """The control arm: what a competent person would type, and nothing more.

    Intentionally free of every framework construct — no craft rules, no
    anti-AI-voice block, no contract JSON, no methodology. Adding any of them
    here would contaminate the control and make the comparison meaningless.
    """

    system = "你是一位中文网络小说作家。"
    parts = [
        f"《{project.title}》，题材：{project.genre}。",
    ]
    if previous_tail:
        parts.append(f"上一章结尾：\n{previous_tail}")
    parts.append(f"请写第 {chapter.chapter_number} 章。")
    if chapter.chapter_goal:
        parts.append(f"本章要发生的事：{chapter.chapter_goal}")
    parts.append("直接输出正文，约 2600 字。")
    return system, "\n\n".join(parts)


async def _run(slug: str, chapter_numbers: list[int]) -> list[ChapterComparison]:
    settings = load_settings()
    engine = create_engine(settings)
    factory = create_session_factory(engine=engine)
    comparisons: list[ChapterComparison] = []
    try:
        async with factory() as session:
            project = (
                await session.execute(
                    select(ProjectModel).where(ProjectModel.slug == slug)
                )
            ).scalar_one_or_none()
            if project is None:
                print(f"找不到书：{slug}")
                return []

            chapters = list(
                await session.scalars(
                    select(ChapterModel)
                    .where(ChapterModel.project_id == project.id)
                    .order_by(ChapterModel.chapter_number)
                )
            )
            by_number = {c.chapter_number: c for c in chapters}
            texts = await _load_prose(session, [c.id for c in chapters])
            version_counts = await _draft_version_counts(
                session, [c.id for c in chapters]
            )

        for number in chapter_numbers:
            chapter = by_number.get(number)
            if chapter is None:
                print(f"第 {number} 章不存在，跳过")
                continue
            production_text = texts.get(chapter.id, "")
            if not production_text.strip():
                print(f"第 {number} 章尚无正文，跳过")
                continue

            previous = by_number.get(number - 1)
            previous_tail = ""
            if previous is not None:
                previous_tail = (texts.get(previous.id, "") or "")[-400:]

            system, user = _bare_prompts(
                project=project, chapter=chapter, previous_tail=previous_tail
            )
            request = LLMCompletionRequest(
                logical_role="writer",
                model_tier="standard",
                system_prompt=system,
                user_prompt=user,
                # A recognisable sentinel, not empty and not prose: if the call
                # falls back, the control arm must be discarded rather than
                # scored as if the bare model had written it.
                fallback_response=_BARE_ARM_FALLBACK,
                prompt_template="ai_flavor_attribution_bare",
                prompt_version="bare-control-v1",
                project_id=project.id,
                max_tokens_override=6000,
            )
            async with factory() as session:
                result = await complete_text(session, settings, request)
                # Control arm must leave no trace on the book.
                await session.rollback()
            bare_text = (result.content or "").strip()
            if not bare_text or _BARE_ARM_FALLBACK in bare_text:
                print(f"第 {number} 章裸模型未返回内容（对照臂不可用），跳过")
                continue

            comparisons.append(
                ChapterComparison(
                    chapter_number=number,
                    production=_score_arm("production", production_text, number),
                    bare=_score_arm("bare", bare_text, number),
                    draft_versions=version_counts.get(chapter.id, 0),
                    production_state=str(
                        getattr(chapter, "production_state", "") or ""
                    ),
                )
            )
            print(f"  第 {number} 章完成")
    finally:
        await engine.dispose()
    return comparisons


async def _load_prose(session: Any, chapter_ids: Sequence[Any]) -> dict[Any, str]:
    """Current prose per chapter id.

    Takes chapter ids, never a project id — passing the wrong one raises a
    confusing SQLAlchemy coercion error rather than returning nothing, which is
    at least loud, but the annotation makes the contract explicit.
    """

    ids = list(chapter_ids)
    if not ids:
        return {}
    rows = list(
        await session.scalars(
            select(ChapterDraftVersionModel).where(
                ChapterDraftVersionModel.chapter_id.in_(ids),
                ChapterDraftVersionModel.is_current.is_(True),
            )
        )
    )
    return {r.chapter_id: str(r.content_md or "") for r in rows}


async def _draft_version_counts(session: Any, chapter_ids: Sequence[Any]) -> dict[Any, int]:
    """How many drafts each chapter went through — the rewrite-depth proxy."""

    ids = list(chapter_ids)
    if not ids:
        return {}
    rows = list(
        await session.scalars(
            select(ChapterDraftVersionModel).where(
                ChapterDraftVersionModel.chapter_id.in_(ids)
            )
        )
    )
    counts: dict[Any, int] = {}
    for row in rows:
        counts[row.chapter_id] = counts.get(row.chapter_id, 0) + 1
    return counts


def _render(comparisons: list[ChapterComparison]) -> str:
    if not comparisons:
        return "无可比较的章节。"
    lines = [
        f"{'章':>3} {'状态':>10} {'稿数':>4} {'生产分':>7} {'裸模型':>7} {'差值':>7}  {'生产字数':>8}",
        "-" * 66,
    ]
    for c in comparisons:
        flag = "" if c.comparable else "  ⚠未定稿"
        lines.append(
            f"{c.chapter_number:>3} {c.production_state or '?':>10} {c.draft_versions:>4} "
            f"{c.production.score:>7.1f} {c.bare.score:>7.1f} "
            f"{c.delta:>+7.1f}  {c.production.chars:>8}{flag}"
        )

    settled = [c for c in comparisons if c.comparable]
    unsettled = len(comparisons) - len(settled)
    lines.append("")
    if unsettled:
        lines.append(
            f"⚠ {unsettled} 章仍在重写循环中，已排除出统计——"
            "拿半成品稿与成品稿相比，差异读起来像提示词效果，其实只是进度不同。"
        )
    if not settled:
        lines.append("没有已定稿章节，无法给出结论。等章节收敛后重跑。")
        return "\n".join(lines)

    deltas = [c.delta for c in settled]
    prod = [c.production.score for c in settled]
    bare = [c.bare.score for c in settled]
    lines.append(f"（统计基于 {len(settled)} 个已定稿章节）")
    lines.append(f"生产均值 {statistics.mean(prod):.1f} / 裸模型均值 {statistics.mean(bare):.1f}")
    lines.append(f"平均差值 {statistics.mean(deltas):+.1f}（正=我们的产出更差）")
    if len(settled) >= 2:
        lines.append(f"差值标准差 {statistics.stdev(deltas):.1f}")
    if len(settled) < 5:
        lines.append(
            f"⚠ 样本仅 {len(settled)} 章，本仓经验此类指标需 N≈10 才稳定，勿据此下结论。"
        )
    lines.append("")
    lines.append("怎么读：")
    lines.append("  差值 ≈ 0        AI 味来自模型本身，提示词不是病根；改提示词收益有限。")
    lines.append("  差值明显为正    我们的提示词把正文写坏了，应当减法而非加法。")
    lines.append("  差值明显为负    提示词确实在起正作用，AI 味另有来源（物料/重写路径）。")

    cats_prod: dict[str, int] = {}
    cats_bare: dict[str, int] = {}
    for c in comparisons:
        for name in c.production.top_categories:
            cats_prod[name] = cats_prod.get(name, 0) + 1
        for name in c.bare.top_categories:
            cats_bare[name] = cats_bare.get(name, 0) + 1
    lines.append("")
    lines.append(f"生产高频类别: {', '.join(sorted(cats_prod, key=lambda k: -cats_prod[k])[:5]) or '(无)'}")
    lines.append(f"裸模型高频类别: {', '.join(sorted(cats_bare, key=lambda k: -cats_bare[k])[:5]) or '(无)'}")
    return "\n".join(lines)


async def _main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--slug", required=True)
    parser.add_argument("--chapters", default="1,2,3", help="逗号分隔，如 1,5,9")
    parser.add_argument("--json", dest="json_path")
    args = parser.parse_args()

    numbers = [int(x) for x in str(args.chapters).split(",") if x.strip()]
    comparisons = await _run(args.slug, numbers)
    print()
    print(_render(comparisons))

    if args.json_path and comparisons:
        Path(args.json_path).write_text(
            json.dumps(
                [
                    {
                        "chapter": c.chapter_number,
                        "production_score": c.production.score,
                        "bare_score": c.bare.score,
                        "delta": c.delta,
                        "production_chars": c.production.chars,
                        "bare_chars": c.bare.chars,
                        "production_categories": list(c.production.top_categories),
                        "bare_categories": list(c.bare.top_categories),
                    }
                    for c in comparisons
                ],
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
