"""Collision judge and verdict synthesis for market validation.

Division of labor, learned from this repo's gate history:

- The **score is deterministic** (``score_verdict``) — every point traces to a
  section of evidence and can be recomputed offline. LLM opinions never move
  the number.
- The **LLM judges only what rules cannot**: semantic similarity between our
  concept and on-board competitor intros. Its output is grounded — returned
  titles must exist in the input competitor list, hallucinated ones are
  dropped.
- Everything fails open: no LLM, no network → sections degrade, never raise.
"""

# ruff: noqa: RUF001, RUF003 — Chinese market vocabulary is intentional.
from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from bestseller.domain.market_validation import (
    BlurbBenchmarkSection,
    CompetitorSimilarity,
    GenreHeatSection,
    MarketBookObservation,
    MarketSectionStatus,
    MarketVerdictBand,
    MarketVerdictSection,
    TitleCheckSection,
)

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from bestseller.settings import AppSettings

logger = logging.getLogger(__name__)

_COLLISION_FALLBACK = '{"collisions": []}'


def build_collision_messages(
    *, concept: str, competitors: list[MarketBookObservation]
) -> tuple[str, str]:
    system = (
        "你是网文市场分析师。任务：判断「我们的概念」与榜单在售书的撞车程度。\n"
        "只输出 JSON：{\"collisions\": [{\"title\": 榜单书名原文, "
        "\"similarity\": \"high|medium\", \"overlap_points\": [重叠点], "
        "\"differentiation\": [我们仍可差异化的点]}]}\n"
        "规则：similarity=high 表示核心设定+主线卖点基本同构（读者会认为是同一本书）；"
        "medium 表示同一赛道但卖点可区分。低相似度的书不要输出。"
        "title 必须逐字来自给出的榜单书名，禁止编造。"
    )
    lines = [f"【我们的概念】{concept.strip()}", "", "【榜单在售书】"]
    for index, book in enumerate(competitors, start=1):
        intro = (book.intro or "").replace("\n", " ")[:150]
        lines.append(f"{index}. 《{book.title}》：{intro}")
    return system, "\n".join(lines)


def parse_collisions(
    content: str, *, known_titles: set[str]
) -> tuple[list[CompetitorSimilarity], int]:
    """Parse judge output; drop entries whose title is not in the input list."""

    try:
        from json_repair import repair_json

        payload = json.loads(repair_json(content or "{}"))
    except Exception:
        return [], 0
    raw_items = payload.get("collisions") if isinstance(payload, dict) else None
    if not isinstance(raw_items, list):
        return [], 0
    collisions: list[CompetitorSimilarity] = []
    dropped = 0
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip().strip("《》")
        if title not in known_titles:
            dropped += 1
            continue
        similarity = str(item.get("similarity") or "").strip().lower()
        if similarity not in ("high", "medium"):
            continue
        collisions.append(
            CompetitorSimilarity(
                title=title,
                similarity=similarity,
                overlap_points=[
                    str(point).strip()
                    for point in (item.get("overlap_points") or [])
                    if str(point).strip()
                ][:5],
                differentiation=[
                    str(point).strip()
                    for point in (item.get("differentiation") or [])
                    if str(point).strip()
                ][:5],
            )
        )
    return collisions, dropped


async def judge_collisions(
    session: AsyncSession,
    settings: AppSettings,
    *,
    concept: str,
    competitors: list[MarketBookObservation],
    project_id: UUID | None = None,
) -> tuple[list[CompetitorSimilarity], bool]:
    """LLM concept-collision scan. Returns (collisions, llm_used)."""

    if not (concept or "").strip() or not competitors:
        return [], False
    try:
        from bestseller.services.llm import (
            LLMCompletionRequest,
            complete_text,
        )

        system, user = build_collision_messages(
            concept=concept, competitors=competitors
        )
        completion = await complete_text(
            session,
            settings,
            LLMCompletionRequest(
                logical_role="critic",
                model_tier="standard",
                system_prompt=system,
                user_prompt=user,
                fallback_response=_COLLISION_FALLBACK,
                prompt_template="market_validation_collision_judge",
                prompt_version="v1",
                max_tokens_override=900,
                project_id=project_id,
            ),
        )
        if getattr(completion, "fallback_used", False):
            return [], False
        collisions, dropped = parse_collisions(
            completion.content or "",
            known_titles={book.title for book in competitors},
        )
        if dropped:
            logger.info("Collision judge hallucinated %s titles (dropped)", dropped)
        return collisions, True
    except Exception:
        logger.warning("Collision judge failed (fail-open)", exc_info=True)
        return [], False


def score_verdict(
    *,
    genre_heat: GenreHeatSection,
    title_check: TitleCheckSection,
    collisions: list[CompetitorSimilarity],
    collisions_judged: bool,
    blurb: BlurbBenchmarkSection,
    has_concept: bool,
) -> MarketVerdictSection:
    """Deterministic advisory score. Every adjustment is written to rationale.

    Explicitly a risk screen over survivor-only board data — not a hit
    predictor. The score is clamped to 5..95 to avoid false certainty.
    """

    score = 60
    rationale: list[str] = ["基准分 60（中性起点；榜单只有幸存者，无失败样本）"]
    risks: list[str] = []
    opportunities: list[str] = []

    if genre_heat.status != MarketSectionStatus.SKIPPED and genre_heat.sample_size:
        if genre_heat.heat_p50 >= 300_000:
            score += 12
            rationale.append(f"+12 品类热度充足（在读中位 {genre_heat.heat_p50}）")
        elif genre_heat.heat_p50 >= 100_000:
            score += 6
            rationale.append(f"+6 品类热度尚可（在读中位 {genre_heat.heat_p50}）")
        elif genre_heat.heat_p50 < 30_000:
            score -= 8
            rationale.append(f"-8 品类整体流量薄（在读中位 {genre_heat.heat_p50}）")
            risks.append("品类整体流量薄，天花板低")
        if genre_heat.new_entry_share >= 0.15:
            score += 6
            rationale.append(
                f"+6 新书能进榜（新入榜占比 {genre_heat.new_entry_share:.0%}）"
            )
            opportunities.append("品类对新书开放，进榜通道活跃")
        elif genre_heat.new_entry_share < 0.05 and genre_heat.rising_share < 0.2:
            # 单日快照里「今天没人新进榜」很常见，只有叠加「几乎没人在涨」
            # 才构成固化证据。
            score -= 6
            rationale.append(
                f"-6 榜单固化（新入榜 {genre_heat.new_entry_share:.0%}，"
                f"上升占比 {genre_heat.rising_share:.0%}）"
            )
            risks.append("榜单头部固化，新书难挤进")
        if genre_heat.rising_share >= 0.5:
            score += 4
            rationale.append(
                f"+4 品类在升温（上升占比 {genre_heat.rising_share:.0%}）"
            )
    else:
        score -= 5
        rationale.append("-5 无可用热度数据")

    findings = title_check.findings
    if findings:
        verdicts = {finding.verdict for finding in findings}
        if "fail" in verdicts and "pass" not in verdicts:
            score -= 20
            rationale.append("-20 全部候选书名不过查重/同壳评估")
            risks.extend(
                reason for finding in findings for reason in finding.reasons[:1]
            )
        elif "fail" in verdicts or "caution" in verdicts:
            score -= 8
            rationale.append("-8 部分候选书名有查重/同壳风险")
        else:
            score += 5
            rationale.append("+5 候选书名查重干净")
            opportunities.append("书名无占用，可抢注概念壳")

    high_collisions = [item for item in collisions if item.similarity == "high"]
    if high_collisions:
        score -= 12
        rationale.append(
            f"-12 概念与在榜书高度撞车：{[item.title for item in high_collisions[:3]]}"
        )
        risks.append("核心概念已有在榜书占位，读者会视为跟风")
    elif collisions:
        score -= 5
        rationale.append(f"-5 同赛道竞品 {len(collisions)} 本（卖点尚可区分）")
    elif has_concept and collisions_judged:
        score += 6
        rationale.append("+6 概念查无直接撞车")
        opportunities.append("概念位空置，先发优势")

    if blurb.status == MarketSectionStatus.OK and blurb.warnings:
        penalty = min(8, 4 * len(blurb.warnings))
        score -= penalty
        rationale.append(f"-{penalty} 简介形态偏离榜单惯例：{blurb.warnings[:2]}")

    score = max(5, min(95, score))
    if score >= 70:
        band = MarketVerdictBand.GO
    elif score >= 45:
        band = MarketVerdictBand.REVISE
    else:
        band = MarketVerdictBand.NO_GO

    return MarketVerdictSection(
        status=MarketSectionStatus.OK,
        score=score,
        band=band,
        rationale=rationale,
        risks=risks,
        opportunities=opportunities,
    )
