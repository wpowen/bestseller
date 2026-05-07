"""Per-volume audit digest — quality signal for the next volume's planner.

Why this exists
---------------
After each volume is written, we accumulate signals (lifecycle violations,
title pattern heat, chapter-length variance) that the next volume's planner
SHOULD see but currently doesn't. This thin module collects those signals,
writes a human-readable markdown digest to
``output/<slug>/audits/v{N}.md``, and returns a short text block that
``pipelines.py`` can prepend to ``prior_feedback_summary``.

Design choices:
* **No new external dependencies** — reuses existing DB models and
  queries from ``contradiction.py`` / ``diversity_budget.py``.
* **Read-only** — never writes back to the DB (digest is on disk + the
  passed-back string). The pipeline may persist it to
  ``project.metadata_json["audit_history"]`` if it wants longitudinal tracking.
* **Best-effort** — every sub-check is wrapped; partial failures produce
  degraded output rather than crashing the pipeline.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.infra.db.models import (
    CharacterModel,
    CharacterStateSnapshotModel,
    ChapterModel,
    DiversityBudgetModel,
    ProjectModel,
    VolumeModel,
)
from bestseller.services.projects import get_project_by_slug


logger = logging.getLogger(__name__)


async def run_volume_audit(
    session: AsyncSession,
    project_slug: str,
    volume_number: int,
    *,
    output_root: Path | None = None,
) -> str:
    """Run a lightweight quality audit for ``volume_number`` and return a digest.

    The digest is a short (≤ 400 char) plain-text block suitable for
    prepending to the next volume's ``prior_feedback_summary``. A full
    markdown report is written to
    ``output_root/<slug>/audits/v{volume_number}.md`` when ``output_root``
    is provided.

    All sub-checks are best-effort: unexpected DB errors produce a ``(scan
    error: ...)`` note in the digest rather than propagating upward.
    """
    try:
        project = await get_project_by_slug(session, project_slug)
        if project is None:
            return f"[audit v{volume_number}] project not found"

        chapters = await _volume_chapters(session, project.id, volume_number)
        if not chapters:
            return f"[audit v{volume_number}] no chapters found"

        chapter_numbers = [c.chapter_number for c in chapters]
        ch_min = min(chapter_numbers)
        ch_max = max(chapter_numbers)

        char_count = await _character_count(session, project.id)
        resurrection_hits = await _resurrection_scan(session, project.id, ch_min, ch_max)
        power_tier_drift = await _power_tier_drift(session, project.id, ch_min, ch_max)
        title_heat = await _title_heat(session, project.id, chapters)
        avg_len, min_len, max_len = _chapter_length_stats(chapters)

        lines: list[str] = [
            f"# Volume {volume_number} Audit — {project.title}",
            "",
            f"**章节范围**: ch{ch_min}–{ch_max} ({len(chapters)} 章)",
            f"**角色总数**: {char_count}",
            "",
            "## 生命周期",
            f"- 死而复活疑似违规: **{resurrection_hits}** 处",
            f"- 实力层级不稳定快照: **{power_tier_drift}** 处",
            "",
            "## 篇幅",
            f"- 平均字数: {avg_len:,.0f}",
            f"- 最短/最长: {min_len:,} / {max_len:,}",
            "",
            "## 标题热词 (出现 ≥ 2 次)",
        ]
        if title_heat:
            for gram, count in sorted(title_heat.items(), key=lambda kv: -kv[1])[:10]:
                lines.append(f"- 「{gram}」×{count}")
        else:
            lines.append("- (无明显重复)")
        lines.append("")

        verdict_parts: list[str] = []
        if resurrection_hits > 0:
            verdict_parts.append(f"{resurrection_hits} 死亡角色出现")
        if power_tier_drift > 10:
            verdict_parts.append(f"实力描述漂移 {power_tier_drift} 处")
        if title_heat:
            hot = ", ".join(f"「{g}」" for g in list(title_heat)[:3])
            verdict_parts.append(f"标题热词 {hot}")
        if avg_len < 2000:
            verdict_parts.append(f"平均字数偏低 ({avg_len:.0f})")

        verdict = "注意：" + "；".join(verdict_parts) if verdict_parts else "通过"
        lines += ["## 综合评定", verdict, ""]

        md = "\n".join(lines)

        if output_root is not None:
            audit_dir = output_root / project_slug / "audits"
            audit_dir.mkdir(parents=True, exist_ok=True)
            (audit_dir / f"v{volume_number}.md").write_text(md, encoding="utf-8")

        short_verdict = verdict[:200]
        return f"[v{volume_number} audit] {short_verdict}"

    except Exception as exc:
        logger.warning("volume_audit v%s failed for %s: %s", volume_number, project_slug, exc)
        return f"[audit v{volume_number}] scan error: {exc!s:.100}"


# ---------------------------------------------------------------------------
# Sub-checks
# ---------------------------------------------------------------------------

async def _volume_chapters(
    session: AsyncSession,
    project_id: Any,
    volume_number: int,
) -> list[ChapterModel]:
    volume_number = int(volume_number)
    volume_id = await session.scalar(
        select(VolumeModel.id).where(
            VolumeModel.project_id == project_id,
            VolumeModel.volume_number == volume_number,
        )
    )
    if volume_id is not None:
        rows = await session.scalars(
            select(ChapterModel)
            .where(
                ChapterModel.project_id == project_id,
                ChapterModel.volume_id == volume_id,
            )
            .order_by(ChapterModel.chapter_number.asc())
        )
        return list(rows)

    rows = await session.scalars(
        select(ChapterModel)
        .where(ChapterModel.project_id == project_id)
        .order_by(ChapterModel.chapter_number.asc())
    )
    all_chapters = list(rows)

    # Fallback: infer volume from chapter_number range (50 chapters/volume default)
    chapters_per_vol = 50
    ch_start = (volume_number - 1) * chapters_per_vol + 1
    ch_end = volume_number * chapters_per_vol
    return [c for c in all_chapters if ch_start <= c.chapter_number <= ch_end]


async def _character_count(session: AsyncSession, project_id: Any) -> int:
    result = await session.scalar(
        select(func.count()).where(CharacterModel.project_id == project_id)
    )
    return int(result or 0)


async def _resurrection_scan(
    session: AsyncSession,
    project_id: Any,
    ch_min: int,
    ch_max: int,
) -> int:
    deceased = list(
        await session.scalars(
            select(CharacterModel).where(
                CharacterModel.project_id == project_id,
                CharacterModel.death_chapter_number.is_not(None),
                CharacterModel.death_chapter_number < ch_max,
            )
        )
    )
    if not deceased:
        return 0

    dead_ids = [c.id for c in deceased]
    death_map = {c.id: c.death_chapter_number for c in deceased}

    count = 0
    for char_id in dead_ids:
        death_ch = death_map[char_id]
        if death_ch is None:
            continue
        # Any snapshot AFTER death in the current volume range is a hit.
        snap_count = await session.scalar(
            select(func.count()).where(
                CharacterStateSnapshotModel.project_id == project_id,
                CharacterStateSnapshotModel.character_id == char_id,
                CharacterStateSnapshotModel.chapter_number > death_ch,
                CharacterStateSnapshotModel.chapter_number <= ch_max,
            )
        )
        if (snap_count or 0) > 0:
            count += 1
    return count


async def _power_tier_drift(
    session: AsyncSession,
    project_id: Any,
    ch_min: int,
    ch_max: int,
) -> int:
    rows = list(
        await session.scalars(
            select(CharacterStateSnapshotModel).where(
                CharacterStateSnapshotModel.project_id == project_id,
                CharacterStateSnapshotModel.chapter_number >= ch_min,
                CharacterStateSnapshotModel.chapter_number <= ch_max,
                CharacterStateSnapshotModel.power_tier.is_not(None),
            )
        )
    )
    if not rows:
        return 0

    by_char: dict[Any, list[str]] = {}
    for snap in rows:
        by_char.setdefault(snap.character_id, []).append(snap.power_tier)

    drift = 0
    for tiers in by_char.values():
        unique = len(set(tiers))
        if unique > 2:
            drift += unique - 2
    return drift


async def _title_heat(
    session: AsyncSession,
    project_id: Any,
    chapters: list[ChapterModel],
) -> dict[str, int]:
    from bestseller.services.diversity_budget import extract_title_ngrams  # noqa: PLC0415

    titles = [c.title for c in chapters if c.title]

    # Also try the diversity budget for historical title registry.
    db_budget = await session.scalar(
        select(DiversityBudgetModel).where(
            DiversityBudgetModel.project_id == project_id
        )
    )
    if db_budget is not None:
        budget_titles = db_budget.titles_used or []
        titles = list(titles) + [str(t) for t in budget_titles if t]

    gram_counts: dict[str, int] = {}
    for title in titles:
        for gram in extract_title_ngrams(title):
            gram_counts[gram] = gram_counts.get(gram, 0) + 1

    return {g: c for g, c in gram_counts.items() if c >= 2}


def _chapter_length_stats(chapters: list[ChapterModel]) -> tuple[float, int, int]:
    lengths = [
        int(c.current_word_count or 0)
        for c in chapters
        if int(c.current_word_count or 0) > 0
    ]
    if not lengths:
        return 0.0, 0, 0
    return sum(lengths) / len(lengths), min(lengths), max(lengths)
