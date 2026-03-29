"""
if_context.py — Three-tier memory system for long-running IF generation.

Solves the "chapter 500 doesn't know what happened in chapter 50" problem
by assembling layered context from DB records before each chapter is written.

Tiers:
  hot  (~800 tokens):   Last 5 chapter summaries + current arc goal
  warm (~1200 tokens):  Last 3 arc summaries + active relationship changes
  cold (~2000 tokens):  Latest world snapshot + critical canon facts
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.infra.db.models import (
    IFArcSummaryModel,
    IFCanonFactModel,
    IFWorldStateSnapshotModel,
    ProjectModel,
)


class ContextAssembler:
    """
    Assembles tiered context for IF chapter generation.

    Usage:
        assembler = ContextAssembler()
        ctx = await assembler.assemble(
            chapter_number=51,
            route_id="mainline",
            session=session,
            project=project,
            run_id=run_id,
            tier="tiered",          # "basic" | "tiered" | "full"
            arc_index=1,
            act_id="act_01",
        )
        prompt = chapter_prompt(card, prev_hook, bible, context_text=ctx)
    """

    async def assemble(
        self,
        chapter_number: int,
        route_id: str,
        session: AsyncSession,
        project: ProjectModel,
        run_id: UUID | None,
        tier: str = "tiered",
        arc_index: int = 0,
        act_id: str | None = None,
        arc_goal: str = "",
    ) -> str:
        """
        Build the context string for a chapter prompt.

        tier="basic"  → hot only (last 5 summaries)
        tier="tiered" → hot + warm (default, balances cost and quality)
        tier="full"   → hot + warm + cold (best for chapters > 100)
        """
        parts: list[str] = []

        hot = await self._load_hot_context(
            chapter_number, route_id, session, project, run_id, arc_goal
        )
        if hot:
            parts.append(hot)

        if tier in ("tiered", "full"):
            warm = await self._load_warm_context(
                arc_index, route_id, session, project, run_id
            )
            if warm:
                parts.append(warm)

        if tier == "full":
            cold = await self._load_cold_context(
                chapter_number, route_id, session, project, run_id
            )
            if cold:
                parts.append(cold)

        return "\n\n".join(parts)

    # ------------------------------------------------------------------
    # Hot context: last 5 chapter summaries + current arc goal
    # ------------------------------------------------------------------

    async def _load_hot_context(
        self,
        chapter_number: int,
        route_id: str,
        session: AsyncSession,
        project: ProjectModel,
        run_id: UUID | None,
        arc_goal: str = "",
    ) -> str:
        rows = await self._query_canon_facts(
            session,
            project.id,
            run_id,
            route_ids=[route_id, "all"],
            fact_types=["chapter_summary"],
            before_chapter=chapter_number,
            limit=5,
        )

        if not rows:
            return ""

        lines = [
            f"  第{r.chapter_number}章《{r.subject_label}》：{r.fact_body}"
            for r in rows
        ]
        result = f"前情提要（最近{len(lines)}章）：\n" + "\n".join(lines)

        if arc_goal:
            result += f"\n当前弧线目标：{arc_goal}"

        return result

    # ------------------------------------------------------------------
    # Warm context: last 3 arc summaries + relationship changes
    # ------------------------------------------------------------------

    async def _load_warm_context(
        self,
        arc_index: int,
        route_id: str,
        session: AsyncSession,
        project: ProjectModel,
        run_id: UUID | None,
    ) -> str:
        if run_id is None:
            return ""

        stmt = (
            select(IFArcSummaryModel)
            .where(
                IFArcSummaryModel.project_id == project.id,
                IFArcSummaryModel.run_id == run_id,
                IFArcSummaryModel.route_id.in_([route_id, "mainline"]),
                IFArcSummaryModel.arc_index < arc_index,
            )
            .order_by(IFArcSummaryModel.arc_index.desc())
            .limit(3)
        )
        rows = list(await session.scalars(stmt))
        if not rows:
            return ""

        parts: list[str] = []
        for r in reversed(rows):
            entry = (
                f"  第{r.chapter_start}-{r.chapter_end}章弧线总结：\n"
                f"    主角成长：{r.protagonist_growth or 'N/A'}\n"
                f"    当前实力：{r.power_level_summary or 'N/A'}"
            )
            if r.unresolved_threads:
                entry += f"\n    未解伏线：{', '.join(str(t) for t in r.unresolved_threads[:3])}"
            parts.append(entry)

        return "近期弧线回顾：\n" + "\n".join(parts)

    # ------------------------------------------------------------------
    # Cold context: world snapshot + critical canon facts
    # ------------------------------------------------------------------

    async def _load_cold_context(
        self,
        chapter_number: int,
        route_id: str,
        session: AsyncSession,
        project: ProjectModel,
        run_id: UUID | None,
    ) -> str:
        parts: list[str] = []

        # World snapshot (latest before this chapter)
        snapshot = await self.get_snapshot_at_or_before(
            chapter_number, route_id, session, project, run_id
        )
        if snapshot and snapshot.world_summary:
            parts.append(f"世界状态（第{snapshot.snapshot_chapter}章时）：\n  {snapshot.world_summary}")

        # Critical canon facts (all routes)
        if run_id is not None:
            crit_rows = await self._query_canon_facts(
                session,
                project.id,
                run_id,
                route_ids=[route_id, "all"],
                fact_types=["event", "world_rule", "character_state"],
                before_chapter=chapter_number,
                importance="critical",
                limit=10,
            )
            if crit_rows:
                lines = [f"  [{r.fact_type}] {r.subject_label}：{r.fact_body}" for r in crit_rows]
                parts.append("关键世界事实：\n" + "\n".join(lines))

        return "\n\n".join(parts)

    # ------------------------------------------------------------------
    # World snapshot query
    # ------------------------------------------------------------------

    async def get_snapshot_at_or_before(
        self,
        chapter_number: int,
        route_id: str,
        session: AsyncSession,
        project: ProjectModel,
        run_id: UUID | None,
    ) -> IFWorldStateSnapshotModel | None:
        if run_id is None:
            return None

        # Try route-specific first, then mainline
        for rid in ([route_id, "mainline"] if route_id != "mainline" else ["mainline"]):
            stmt = (
                select(IFWorldStateSnapshotModel)
                .where(
                    IFWorldStateSnapshotModel.project_id == project.id,
                    IFWorldStateSnapshotModel.run_id == run_id,
                    IFWorldStateSnapshotModel.route_id == rid,
                    IFWorldStateSnapshotModel.snapshot_chapter <= chapter_number,
                )
                .order_by(IFWorldStateSnapshotModel.snapshot_chapter.desc())
                .limit(1)
            )
            result = await session.scalar(stmt)
            if result is not None:
                return result

        return None

    # ------------------------------------------------------------------
    # Helper: store a chapter summary into IFCanonFactModel
    # ------------------------------------------------------------------

    async def store_chapter_summary(
        self,
        session: AsyncSession,
        project: ProjectModel,
        run_id: UUID,
        route_id: str,
        chapter_number: int,
        chapter_title: str,
        summary_text: str,
    ) -> None:
        """Store a chapter summary as an IFCanonFact for future context injection."""
        fact = IFCanonFactModel(
            project_id=project.id,
            run_id=run_id,
            route_id=route_id,
            chapter_number=chapter_number,
            fact_type="chapter_summary",
            subject_label=chapter_title,
            fact_body=summary_text,
            importance="major",
        )
        session.add(fact)
        await session.flush()

    # ------------------------------------------------------------------
    # Helper: store a world state snapshot
    # ------------------------------------------------------------------

    async def store_world_snapshot(
        self,
        session: AsyncSession,
        project: ProjectModel,
        run_id: UUID,
        route_id: str,
        arc_index: int,
        snapshot_chapter: int,
        snapshot_data: dict[str, Any],
    ) -> IFWorldStateSnapshotModel:
        """Persist a world state snapshot after arc generation."""
        record = IFWorldStateSnapshotModel(
            project_id=project.id,
            run_id=run_id,
            route_id=route_id,
            snapshot_chapter=snapshot_chapter,
            arc_index=arc_index,
            character_states=snapshot_data.get("character_states", {}),
            faction_states=snapshot_data.get("faction_states", {}),
            revealed_truths=snapshot_data.get("revealed_truths", []),
            active_threats=snapshot_data.get("active_threats", []),
            planted_unrevealed=snapshot_data.get("planted_unrevealed", []),
            power_rankings=snapshot_data.get("power_rankings", []),
            world_summary=snapshot_data.get("world_summary"),
        )
        session.add(record)
        await session.flush()
        return record

    # ------------------------------------------------------------------
    # Helper: store an arc summary
    # ------------------------------------------------------------------

    async def store_arc_summary(
        self,
        session: AsyncSession,
        project: ProjectModel,
        run_id: UUID,
        route_id: str,
        arc_index: int,
        chapter_start: int,
        chapter_end: int,
        act_id: str | None,
        summary_data: dict[str, Any],
    ) -> IFArcSummaryModel:
        """Persist an arc summary after all arc chapters are generated."""
        record = IFArcSummaryModel(
            project_id=project.id,
            run_id=run_id,
            route_id=route_id,
            arc_index=arc_index,
            chapter_start=chapter_start,
            chapter_end=chapter_end,
            act_id=act_id,
            protagonist_growth=summary_data.get("protagonist_growth"),
            relationship_changes=summary_data.get("relationship_changes", []),
            unresolved_threads=summary_data.get("unresolved_threads", []),
            power_level_summary=summary_data.get("power_level_summary"),
            next_arc_setup=summary_data.get("next_arc_setup"),
            open_clues=summary_data.get("open_clues", []),
            resolved_clues=summary_data.get("resolved_clues", []),
        )
        session.add(record)
        await session.flush()
        return record

    # ------------------------------------------------------------------
    # Internal query helper
    # ------------------------------------------------------------------

    async def _query_canon_facts(
        self,
        session: AsyncSession,
        project_id: UUID,
        run_id: UUID | None,
        route_ids: list[str],
        fact_types: list[str],
        before_chapter: int,
        limit: int = 10,
        importance: str | None = None,
    ) -> list[IFCanonFactModel]:
        if run_id is None:
            return []

        stmt = (
            select(IFCanonFactModel)
            .where(
                IFCanonFactModel.project_id == project_id,
                IFCanonFactModel.run_id == run_id,
                IFCanonFactModel.route_id.in_(route_ids),
                IFCanonFactModel.fact_type.in_(fact_types),
                IFCanonFactModel.chapter_number < before_chapter,
            )
        )
        if importance:
            stmt = stmt.where(IFCanonFactModel.importance == importance)

        stmt = stmt.order_by(IFCanonFactModel.chapter_number.desc()).limit(limit)
        rows = list(await session.scalars(stmt))
        return list(reversed(rows))
