"""Library Curator — keeps the global material library topped up.

The Research Agent (see :mod:`research_agent`) is the *producer* of
library entries.  The Curator is the *planner*: it surveys every
``(dimension, genre)`` bucket the system cares about, reports which
buckets are under-filled, and drives the Research Agent to fill them.

Design notes
------------

* **Declarative coverage plan.**  :data:`CURATOR_COVERAGE_PLAN` lists the
  ``(genre, sub_genre)`` × dimension combinations the Curator is
  responsible for.  Adding a new genre = appending one entry; the full
  pipeline stays additive.
* **Audit-then-fill.**  :func:`coverage_audit` is pure read (no LLM, no
  HTTP).  :func:`fill_gap` is the only function that invokes
  :func:`research_agent.run_research`.  This separation makes the cron
  audit cheap and keeps the "do real work" path explicit.
* **Per-call budget.**  Each fill_gap invocation is capped at
  ``max_fills_per_run`` entries so a deeply under-filled genre doesn't
  stall the whole audit loop.  The remaining gap is picked up on the
  next cycle.
* **Feature-flag-aware.**  All public entry points are safe to call with
  ``enable_material_library=false``; they return empty reports / no-op
  without touching the DB.  Actual wiring of the flag lives in
  ``pipelines.py`` (B1.7).
* **MCP + search_client are optional.**  The Curator is built to run in
  environments without API keys: it will still audit + log gaps, just
  won't auto-fill.  ``scripts/curate_library.py`` is the manual escape
  hatch for admins.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.services.material_library import (
    CoverageReport,
    MaterialEntry,
    ensure_coverage,
)
from bestseller.services.mcp_bridge import MCPConnectionPool
from bestseller.services.research_agent import ResearchOutcome, run_research
from bestseller.services.search_client import (
    NoopSearchClient,
    WebSearchClient,
    build_search_client,
)
from bestseller.services.skills_loader import (
    ResearchSkill,
    load_skills_for_genre,
)
from bestseller.settings import AppSettings

logger = logging.getLogger(__name__)


# ── Coverage plan ──────────────────────────────────────────────────────
#
# The Curator's responsibility scope — every genre × dimension bucket it
# will audit on each cycle.  Tuning notes:
#
# * ``min_entries``: below this threshold the bucket is reported as a
#   gap.  First-week bootstrap starts at 3 and climbs to 10 once the
#   library has settled.
# * Genres without a ``sub_genre`` serve as the cross-genre baseline so
#   common tropes (world history, factions) are available to every
#   project.


@dataclass(frozen=True)
class CoverageTarget:
    """One bucket the Curator should keep topped up."""

    dimension: str
    genre: str | None
    sub_genre: str | None = None
    min_entries: int = 10


@dataclass(frozen=True)
class CoverageGap:
    """A bucket currently below its target; Curator wants to fill it."""

    target: CoverageTarget
    report: CoverageReport

    @property
    def dimension(self) -> str:
        return self.target.dimension

    @property
    def genre(self) -> str | None:
        return self.target.genre

    @property
    def sub_genre(self) -> str | None:
        return self.target.sub_genre


# Canonical list of material dimensions the library knows about.  The
# Research Agent + Forges dispatch on ``dimension``; this list is the
# source of truth for "what dimensions should the curator care about by
# default".
_MATERIAL_DIMENSIONS: tuple[str, ...] = (
    "world_settings",
    "power_systems",
    "factions",
    "character_archetypes",
    "character_templates",
    "plot_patterns",
    "scene_templates",
    "device_templates",
    "locale_templates",
    "dialogue_styles",
    "emotion_arcs",
    "thematic_motifs",
    "anti_cliche_patterns",
    "real_world_references",
)


def _default_coverage_plan(
    *,
    min_entries: int = 10,
) -> tuple[CoverageTarget, ...]:
    """Generate the default plan covering generic + 3 seed genres."""

    plan: list[CoverageTarget] = []
    # Cross-genre baseline — genre=None means "applies to every project".
    for dim in _MATERIAL_DIMENSIONS:
        plan.append(CoverageTarget(dimension=dim, genre=None, min_entries=min_entries))
    # Seed genres shipped in Batch 1 (matches config/research_skills/).
    for genre in ("仙侠", "都市修仙", "科幻"):
        for dim in _MATERIAL_DIMENSIONS:
            plan.append(
                CoverageTarget(dimension=dim, genre=genre, min_entries=min_entries)
            )
    return tuple(plan)


#: The plan used by the scheduled cron.  Can be overridden in tests / by
#: CLI callers passing an explicit ``plan=`` argument.
CURATOR_COVERAGE_PLAN: tuple[CoverageTarget, ...] = _default_coverage_plan()


# ── Audit ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class AuditResult:
    """Outcome of one coverage sweep."""

    targets_checked: int
    gaps: tuple[CoverageGap, ...] = field(default_factory=tuple)
    satisfied: tuple[CoverageReport, ...] = field(default_factory=tuple)


async def coverage_audit(
    session: AsyncSession,
    *,
    plan: Sequence[CoverageTarget] | None = None,
    ttl_days: int | None = None,
) -> AuditResult:
    """Check every target in ``plan`` and classify it as gap / satisfied."""

    effective_plan = list(plan or CURATOR_COVERAGE_PLAN)
    gaps: list[CoverageGap] = []
    satisfied: list[CoverageReport] = []
    for target in effective_plan:
        report = await ensure_coverage(
            session,
            dimension=target.dimension,
            genre=target.genre,
            sub_genre=target.sub_genre,
            min_entries=target.min_entries,
            ttl_days=ttl_days,
        )
        if report.is_satisfied:
            satisfied.append(report)
        else:
            gaps.append(CoverageGap(target=target, report=report))
    return AuditResult(
        targets_checked=len(effective_plan),
        gaps=tuple(gaps),
        satisfied=tuple(satisfied),
    )


# ── Fill ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class FillOutcome:
    """What one fill_gap call produced."""

    gap: CoverageGap
    emitted: tuple[MaterialEntry, ...]
    rejected_taboos: tuple[tuple[str, str], ...]
    exit_reason: str

    @property
    def emitted_count(self) -> int:
        return len(self.emitted)


async def fill_gap(
    session: AsyncSession,
    settings: AppSettings,
    gap: CoverageGap,
    *,
    search_client: WebSearchClient | None = None,
    mcp_pool: MCPConnectionPool | None = None,
    max_fills_per_run: int | None = None,
    skills: Sequence[ResearchSkill] | None = None,
    max_rounds: int = 10,
) -> FillOutcome:
    """Run the Research Agent for ``gap`` and report what it produced.

    If no search client is passed we build one from the environment — the
    factory yields a :class:`NoopSearchClient` when no API keys are set,
    so this never crashes but will just log "gap unfilled" downstream.
    """

    effective_skills = (
        list(skills)
        if skills is not None
        else load_skills_for_genre(gap.genre or "", gap.sub_genre)
    )
    target = gap.target
    remaining = target.min_entries - gap.report.active_count
    if max_fills_per_run is not None:
        remaining = min(remaining, max_fills_per_run)
    remaining = max(remaining, 1)  # always try at least one

    client = search_client or build_search_client()
    mcp_tools = mcp_pool.as_tool_specs(consumer="curator") if mcp_pool else ()

    outcome: ResearchOutcome = await run_research(
        session,
        settings,
        dimension=target.dimension,
        genre=target.genre,
        sub_genre=target.sub_genre,
        target_count=remaining,
        skills=effective_skills,
        search_client=client,
        extra_tools=mcp_tools,
        max_rounds=max_rounds,
    )

    # If the factory made its own client, close it so we don't leak
    # httpx connections in worker processes.
    if search_client is None and not isinstance(client, NoopSearchClient):
        try:
            await client.close()
        except Exception as exc:  # noqa: BLE001
            logger.warning("search_client.close() raised: %s", exc)

    return FillOutcome(
        gap=gap,
        emitted=outcome.emitted,
        rejected_taboos=outcome.rejected_taboos,
        exit_reason=outcome.exit_reason,
    )


# ── Orchestration ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class CurationReport:
    """Aggregate outcome of one full audit-then-fill sweep."""

    audit: AuditResult
    fills: tuple[FillOutcome, ...] = field(default_factory=tuple)

    @property
    def total_emitted(self) -> int:
        return sum(f.emitted_count for f in self.fills)


async def run_curation(
    session: AsyncSession,
    settings: AppSettings,
    *,
    plan: Sequence[CoverageTarget] | None = None,
    search_client: WebSearchClient | None = None,
    mcp_pool: MCPConnectionPool | None = None,
    fill: bool = True,
    max_gaps: int | None = None,
    max_fills_per_run: int | None = None,
    ttl_days: int | None = None,
) -> CurationReport:
    """Run the complete audit + optional fill sweep once.

    * ``fill=False`` returns an audit-only report — useful for the
      scheduled job when we just want visibility.
    * ``max_gaps`` caps how many gaps we'll try to fill this cycle (so
      a cold-start doesn't explode the LLM budget).
    * ``max_fills_per_run`` caps per-gap entry emissions (forwarded to
      :func:`fill_gap`).
    """

    audit = await coverage_audit(session, plan=plan, ttl_days=ttl_days)
    if not fill or not audit.gaps:
        return CurationReport(audit=audit, fills=())
    gaps = list(audit.gaps)
    if max_gaps is not None:
        gaps = gaps[:max_gaps]
    fills: list[FillOutcome] = []
    for gap in gaps:
        try:
            outcome = await fill_gap(
                session,
                settings,
                gap,
                search_client=search_client,
                mcp_pool=mcp_pool,
                max_fills_per_run=max_fills_per_run,
            )
            fills.append(outcome)
            logger.info(
                "library_curator: filled %s entries for %s (genre=%s, sub=%s)",
                outcome.emitted_count,
                gap.dimension,
                gap.genre,
                gap.sub_genre,
            )
        except Exception as exc:  # noqa: BLE001 — one gap failing can't kill the sweep
            logger.exception(
                "library_curator: fill_gap failed for %s (%s / %s): %s",
                gap.dimension,
                gap.genre,
                gap.sub_genre,
                exc,
            )
    return CurationReport(audit=audit, fills=tuple(fills))


# ── APScheduler entry point ────────────────────────────────────────────
#
# Must live at module level so ``SQLAlchemyJobStore`` can pickle the
# reference (see scheduler/main.py for the same pattern).  Called as
# ``bestseller.services.library_curator:scheduled_weekly_audit``.


async def scheduled_weekly_audit() -> None:  # pragma: no cover - wired in B1.7
    """Entry point APScheduler calls weekly to top up coverage.

    Fully fenced by the ``pipeline.enable_material_library`` feature
    flag; when it's off the job returns immediately.  Falls back to the
    env var ``BESTSELLER_ENABLE_MATERIAL_LIBRARY`` so this module stays
    importable in tests without full settings wiring.
    """

    import os

    # Try typed settings first (production path); fall back to env var.
    enabled = False
    settings = None
    try:
        from bestseller.settings import get_settings

        settings = get_settings()
        enabled = bool(settings.pipeline.enable_material_library)
    except Exception:  # noqa: BLE001 — settings may not load during tests
        enabled = False

    if not enabled:
        env_flag = os.environ.get("BESTSELLER_ENABLE_MATERIAL_LIBRARY", "0")
        if env_flag not in {"1", "true"}:
            logger.info("library_curator: material library disabled; skipping audit")
            return
        # Env override — still need settings for run_curation.
        if settings is None:
            from bestseller.settings import get_settings

            settings = get_settings()

    from bestseller.infra.db.session import get_server_session

    pipeline_cfg = settings.pipeline if settings else None
    max_gaps = (
        pipeline_cfg.curator_max_gaps_per_run
        if pipeline_cfg
        else int(os.environ.get("BESTSELLER_CURATOR_MAX_GAPS", "6"))
    )
    max_fills_per_run = (
        pipeline_cfg.curator_max_fills_per_run
        if pipeline_cfg
        else int(os.environ.get("BESTSELLER_CURATOR_MAX_FILLS_PER_RUN", "5"))
    )

    async with get_server_session() as session:
        report = await run_curation(
            session,
            settings,
            fill=True,
            max_gaps=max_gaps,
            max_fills_per_run=max_fills_per_run,
        )
        logger.info(
            "library_curator: sweep done — %d gaps, %d entries emitted",
            len(report.audit.gaps),
            report.total_emitted,
        )
        await session.commit()


__all__ = [
    "CoverageTarget",
    "CoverageGap",
    "AuditResult",
    "FillOutcome",
    "CurationReport",
    "CURATOR_COVERAGE_PLAN",
    "coverage_audit",
    "fill_gap",
    "run_curation",
    "scheduled_weekly_audit",
]
