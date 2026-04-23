"""BaseForge — shared scaffolding for all 5 Forge agents.

A Forge takes a project's genre/sub_genre, queries the global
``material_library`` for seed entries in its dimension(s), runs an LLM
tool loop to produce differentiated project-specific variants, and
persists them to ``project_materials``.

Usage::

    forge = WorldForge()
    result = await forge.run(session, project_id, genre, sub_genre, settings)

Design notes
------------
* **Reads global library, writes project table** — the separation keeps
  the global library clean while each project gets its own distinct set.
* **LLM differentiation pass** — the forge prompt explicitly shows the
  library seed entries and instructs the LLM to produce entries that are
  meaningfully *different* from all seeds (and from each other).
* **tool_loop** — uses the same ``run_tool_loop`` / ``ToolSpec`` machinery
  as the Research Agent so unit tests can mock at the same boundary.
* **Novelty critic hook** — when ``settings.pipeline.enable_novelty_guard``
  is True, :func:`~bestseller.services.novelty_critic.check_novelty` is
  called before persisting each entry and
  :func:`~bestseller.services.novelty_critic.register_fingerprint` is
  called after to seed future cross-project checks.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from textwrap import dedent
from typing import Any, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.services.llm_tool_runtime import ToolRegistry, ToolSpec, run_tool_loop
from bestseller.services.material_library import (
    MaterialEntry,
    insert_entry,
    query_library,
)
from bestseller.settings import AppSettings

logger = logging.getLogger(__name__)

# ── Project material DTO ───────────────────────────────────────────────────


@dataclass(frozen=True)
class ProjectMaterial:
    """A single project-scoped material entry as produced by a Forge."""

    project_id: str
    material_type: str
    slug: str
    name: str
    narrative_summary: str
    content_json: dict[str, Any]
    source_library_ids: list[int] = field(default_factory=list)
    variation_notes: str = ""
    status: str = "active"


@dataclass(frozen=True)
class ForgeResult:
    """Aggregate outcome from one Forge.run() call."""

    project_id: str
    dimension: str
    emitted: tuple[ProjectMaterial, ...]
    rounds: int
    exit_reason: str

    @property
    def emitted_count(self) -> int:
        return len(self.emitted)


# ── DB write helper ────────────────────────────────────────────────────────


async def insert_project_material(
    session: AsyncSession,
    mat: ProjectMaterial,
) -> ProjectMaterial:
    """Persist *mat* into ``project_materials`` and return it.

    Uses SQLAlchemy core INSERT for portability without ORM dependencies
    inside the forge package.
    """
    from sqlalchemy import insert, text  # noqa: PLC0415
    from bestseller.infra.db.models import ProjectMaterialModel  # noqa: PLC0415

    stmt = (
        insert(ProjectMaterialModel)
        .values(
            project_id=mat.project_id,
            material_type=mat.material_type,
            slug=mat.slug,
            name=mat.name,
            narrative_summary=mat.narrative_summary,
            content_json=mat.content_json,
            source_library_ids_json=mat.source_library_ids or [],
            variation_notes=mat.variation_notes or None,
            status=mat.status,
        )
        .on_conflict_do_update(
            constraint="uq_pm_project_type_slug",
            set_={
                "name": text("EXCLUDED.name"),
                "narrative_summary": text("EXCLUDED.narrative_summary"),
                "content_json": text("EXCLUDED.content_json"),
                "source_library_ids_json": text("EXCLUDED.source_library_ids_json"),
                "variation_notes": text("EXCLUDED.variation_notes"),
                "status": text("EXCLUDED.status"),
            },
        )
    )
    await session.execute(stmt)
    return mat


# ── Slug validation helper ─────────────────────────────────────────────────

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


def _slug_valid(slug: str) -> bool:
    return bool(_SLUG_RE.match(slug)) and len(slug) <= 160


# ── Base Forge ─────────────────────────────────────────────────────────────


class BaseForge:
    """Shared structure for all 5 Forge agents.

    Subclasses override:
    * ``dimensions``          — tuple of dimension strings this forge covers
    * ``system_instructions`` — forge-specific LLM guidance
    * ``target_per_dimension``— target entry count per dimension
    * ``content_schema_hint`` — one-liner describing expected content_json shape

    Everything else (query_library, tool loop, emit, persist) is inherited.
    """

    dimensions: tuple[str, ...] = ()
    system_instructions: str = ""
    target_per_dimension: int = 5
    content_schema_hint: str = "任意结构化对象"

    # ── Public entry point ─────────────────────────────────────────────────

    async def run(
        self,
        session: AsyncSession,
        project_id: str,
        genre: str,
        settings: AppSettings,
        *,
        sub_genre: str | None = None,
        existing_materials: dict[str, list[ProjectMaterial]] | None = None,
        max_rounds: int = 10,
    ) -> list[ForgeResult]:
        """Run this forge for all covered dimensions.

        Parameters
        ----------
        session:
            Active SQLAlchemy async session for DB reads/writes.
        project_id:
            The project being forged (slug or UUID string).
        genre:
            Primary genre string (e.g. "仙侠", "都市修仙").
        settings:
            Global AppSettings (provides LLM role config).
        sub_genre:
            Optional sub-genre refinement.
        existing_materials:
            Already-forged project materials keyed by dimension — passed in
            so later Forges can reference earlier forge outputs for
            cross-forge consistency.
        max_rounds:
            Maximum tool-loop rounds per dimension.
        """
        results: list[ForgeResult] = []
        for dim in self.dimensions:
            result = await self._forge_dimension(
                session,
                project_id=project_id,
                dimension=dim,
                genre=genre,
                sub_genre=sub_genre,
                settings=settings,
                existing_materials=existing_materials or {},
                max_rounds=max_rounds,
            )
            results.append(result)
        return results

    # ── Internal per-dimension forge ───────────────────────────────────────

    async def _forge_dimension(
        self,
        session: AsyncSession,
        *,
        project_id: str,
        dimension: str,
        genre: str,
        sub_genre: str | None,
        settings: AppSettings,
        existing_materials: dict[str, list[ProjectMaterial]],
        max_rounds: int,
    ) -> ForgeResult:
        # 1. Retrieve seed entries from the global library
        seeds: list[MaterialEntry] = await query_library(
            session,
            dimension=dimension,
            genre=genre,
            sub_genre=sub_genre,
            query=f"{genre} {dimension}",
            top_k=8,
        )

        # 2. Build user prompt
        user_prompt = self._build_user_prompt(
            dimension=dimension,
            genre=genre,
            sub_genre=sub_genre,
            target_count=self.target_per_dimension,
            seeds=seeds,
            existing_materials=existing_materials,
            project_id=project_id,
        )

        # 3. Collect emitted materials in a mutable box
        outcome_box: list[ProjectMaterial] = []

        # 4. Register tools
        emit_tool = self._build_emit_tool(
            session,
            project_id=project_id,
            dimension=dimension,
            genre=genre,
            settings=settings,
            outcome_box=outcome_box,
            target_count=self.target_per_dimension,
        )
        ql_tool = self._build_query_library_tool(
            session, dimension=dimension, genre=genre, sub_genre=sub_genre
        )
        tools = [ql_tool, emit_tool]

        # 5. Run tool loop
        from bestseller.services.llm import LLMCompletionRequest  # noqa: PLC0415

        request = LLMCompletionRequest(
            system_prompt=self._full_system_prompt(),
            user_prompt=user_prompt,
            logical_role="planner",
            fallback_response="{}",
        )
        registry = ToolRegistry(tools)
        loop_result = await run_tool_loop(
            session,
            settings,
            base_request=request,
            registry=registry,
            max_rounds=max_rounds,
            tool_choice="auto",
        )

        logger.info(
            "forge[%s] project=%s dim=%s: emitted=%d rounds=%d exit=%s",
            self.__class__.__name__,
            project_id,
            dimension,
            len(outcome_box),
            loop_result.rounds,
            loop_result.exit_reason,
        )

        return ForgeResult(
            project_id=project_id,
            dimension=dimension,
            emitted=tuple(outcome_box),
            rounds=loop_result.rounds,
            exit_reason=loop_result.exit_reason,
        )

    # ── Prompt builders ────────────────────────────────────────────────────

    def _full_system_prompt(self) -> str:
        base = dedent(
            """
            你是「创作物料锻造师」。你的任务是为一个特定小说项目锻造
            高度差异化的物料条目，确保本项目的设定与现有全局库完全不同，
            也与同项目的其他物料互不重复。

            工作原则：
            1. **先查询**：用 ``query_library`` 了解库中已有内容，避免雷同。
            2. **强制差异化**：提交的条目必须在命名、核心原理、风格上
               与全部种子条目形成明显区分。不得复制库中的名字或概念。
            3. **尽快提交**：search 工具每个维度最多调用 2 次；之后
               直接调用 ``emit_material`` 产出条目，每轮至少产出一条，
               直到达到 target_count。严禁"只查询不提交"。
            4. ``slug`` 必须是 kebab-case，只能包含 [a-z0-9-]。
            5. 提交完成后用一句话总结，不再调工具。
            """
        ).strip()
        specific = self.system_instructions.strip()
        if specific:
            return f"{base}\n\n{specific}"
        return base

    def _build_user_prompt(
        self,
        *,
        dimension: str,
        genre: str,
        sub_genre: str | None,
        target_count: int,
        seeds: list[MaterialEntry],
        existing_materials: dict[str, list[ProjectMaterial]],
        project_id: str,
    ) -> str:
        seed_block = ""
        if seeds:
            seed_lines = "\n".join(
                f"  - [{s.slug}] {s.name}：{s.narrative_summary}"
                for s in seeds[:6]
            )
            seed_block = f"## 全局库中已有条目（必须差异化）\n{seed_lines}"

        existing_block = ""
        all_existing = [m for ms in existing_materials.values() for m in ms]
        if all_existing:
            ex_lines = "\n".join(
                f"  - {m.slug} ({m.material_type})：{m.narrative_summary[:60]}…"
                for m in all_existing[:8]
            )
            existing_block = f"## 本项目已有物料（避免重复）\n{ex_lines}"

        return dedent(
            f"""
            # 锻造任务
            为项目 `{project_id}` 锻造 **{target_count}** 条 `{dimension}` 维度物料。

            - genre: {genre}
            - sub_genre: {sub_genre or '—'}

            每条必须包含：
            - name：中文名称，独特、有记忆点
            - slug：kebab-case 唯一 ID
            - narrative_summary：1-3 句话概括，供其他 Agent 检索时判断相关性
            - content_json：结构化内容（{self.content_schema_hint}）
            - variation_notes：一句话说明本条与库中同类条目的差异

            {seed_block}

            {existing_block}
            """
        ).strip()

    # ── Tool builders ──────────────────────────────────────────────────────

    def _build_query_library_tool(
        self,
        session: AsyncSession,
        *,
        dimension: str,
        genre: str,
        sub_genre: str | None,
    ) -> ToolSpec:
        async def handler(arguments: dict[str, Any]) -> dict[str, Any]:
            query = str(arguments.get("query") or "").strip()
            if not query:
                return {"error": "empty_query"}
            top_k = min(int(arguments.get("top_k") or 6), 10)
            dim = str(arguments.get("dimension") or dimension)
            entries = await query_library(
                session,
                dimension=dim,
                query=query,
                genre=genre,
                sub_genre=sub_genre,
                top_k=top_k,
            )
            return {
                "count": len(entries),
                "entries": [
                    {
                        "id": e.id,
                        "slug": e.slug,
                        "name": e.name,
                        "narrative_summary": e.narrative_summary,
                        "usage_count": e.usage_count,
                    }
                    for e in entries
                ],
            }

        return ToolSpec(
            name="query_library",
            description=(
                "Semantic search in the global material library to understand "
                "what already exists so you can forge something genuinely different."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "top_k": {"type": "integer", "minimum": 1, "maximum": 10},
                    "dimension": {
                        "type": "string",
                        "description": "Override dimension to search (default: current forge dimension).",
                    },
                },
                "required": ["query"],
            },
            handler=handler,
        )

    def _build_emit_tool(
        self,
        session: AsyncSession,
        *,
        project_id: str,
        dimension: str,
        genre: str,
        settings: AppSettings,
        outcome_box: list[ProjectMaterial],
        target_count: int,
    ) -> ToolSpec:
        async def handler(arguments: dict[str, Any]) -> dict[str, Any]:
            try:
                mat = _coerce_emit_args(
                    arguments,
                    project_id=project_id,
                    dimension=dimension,
                )
            except ValueError as exc:
                return {"error": f"validation:{exc}"}

            # ── Batch 3: novelty guard ─────────────────────────────────────
            if settings.pipeline.enable_novelty_guard:
                from bestseller.services.novelty_critic import (  # noqa: PLC0415
                    check_novelty,
                    register_fingerprint,
                )

                verdict = await check_novelty(
                    session,
                    genre=genre,
                    dimension=mat.material_type,
                    entity_name=mat.name,
                    narrative_summary=mat.narrative_summary,
                    source_library_ids=mat.source_library_ids or [],
                )
                if not verdict.ok:
                    logger.warning(
                        "forge[%s] novelty_block slug=%s reason=%s",
                        self.__class__.__name__,
                        mat.slug,
                        verdict.reason,
                    )
                    return {
                        "error": f"novelty_block:{verdict.reason}",
                        "hint": (
                            "Regenerate this entry with a completely different name, "
                            "origin, and narrative approach — do not reuse the blocked concept."
                        ),
                    }

            saved = await insert_project_material(session, mat)

            # Register fingerprint after successful persist
            if settings.pipeline.enable_novelty_guard:
                from bestseller.services.novelty_critic import register_fingerprint  # noqa: PLC0415

                await register_fingerprint(
                    session,
                    project_id=project_id,
                    genre=genre,
                    dimension=saved.material_type,
                    entity_name=saved.name,
                    slug=saved.slug,
                    narrative_summary=saved.narrative_summary,
                )

            outcome_box.append(saved)
            remaining = max(target_count - len(outcome_box), 0)
            return {
                "status": "ok",
                "slug": saved.slug,
                "dimension": saved.material_type,
                "remaining_to_emit": remaining,
            }

        return ToolSpec(
            name="emit_material",
            description=(
                "Persist one project-specific material entry. Non-terminal — keep "
                "calling until target_count is reached."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "slug": {"type": "string"},
                    "name": {"type": "string"},
                    "narrative_summary": {"type": "string"},
                    "content_json": {"type": "object"},
                    "variation_notes": {"type": "string"},
                    "source_library_ids": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": "IDs from material_library that were used as seeds.",
                    },
                    "dimension": {
                        "type": "string",
                        "description": "Override dimension (defaults to current forge dimension).",
                    },
                },
                "required": ["slug", "name", "narrative_summary", "content_json"],
            },
            handler=handler,
        )


# ── Coerce emit arguments ──────────────────────────────────────────────────


def _coerce_emit_args(
    raw: dict[str, Any],
    *,
    project_id: str,
    dimension: str,
) -> ProjectMaterial:
    slug = str(raw.get("slug") or "").strip().lower()
    if not _slug_valid(slug):
        raise ValueError(f"invalid_slug:{slug!r}")

    name = str(raw.get("name") or "").strip()
    if not name:
        raise ValueError("missing_name")

    summary = str(raw.get("narrative_summary") or "").strip()
    if not summary:
        raise ValueError("missing_narrative_summary")

    content_raw = raw.get("content_json")
    if content_raw is None:
        raise ValueError("missing_content_json")
    if isinstance(content_raw, str):
        try:
            content_json: dict[str, Any] = json.loads(content_raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"content_json_not_json:{exc}") from exc
    else:
        content_json = dict(content_raw)

    variation_notes = str(raw.get("variation_notes") or "").strip() or None

    ids_raw = raw.get("source_library_ids") or []
    if not isinstance(ids_raw, list):
        ids_raw = []
    source_ids = [int(x) for x in ids_raw if isinstance(x, (int, float, str))]

    dim_override = str(raw.get("dimension") or "").strip()
    final_dim = dim_override if dim_override else dimension

    return ProjectMaterial(
        project_id=project_id,
        material_type=final_dim,
        slug=slug,
        name=name,
        narrative_summary=summary,
        content_json=content_json,
        source_library_ids=source_ids,
        variation_notes=variation_notes or "",
    )


