"""Market validation orchestrator.

Pulls platform observations through the adapters, runs the deterministic
analyzers, optionally consults the LLM collision judge, and assembles the
advisory report. Every stage fails open: a dead source degrades its section
and the run still completes.
"""

# ruff: noqa: RUF001 — Chinese market vocabulary is intentional.
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import httpx

from bestseller.domain.market_validation import (
    CompetitorScanSection,
    GenreHeatSection,
    MarketBookObservation,
    MarketSectionStatus,
    MarketValidationReport,
    MarketValidationRequest,
)
from bestseller.services.market_validation.adapters.fanqiehub import (
    fetch_fanqiehub_dataset,
    filter_fanqie_observations,
)
from bestseller.services.market_validation.adapters.qimao import fetch_qimao_board
from bestseller.services.market_validation.adapters.websearch import (
    search_title_occupancy,
)
from bestseller.services.market_validation.analyzer import (
    benchmark_blurb,
    build_genre_heat,
    check_titles,
)
from bestseller.services.market_validation.category_map import (
    qimao_match_labels,
    resolve_fanqie_categories,
)
from bestseller.services.market_validation.config import (
    MarketValidationConfig,
    load_market_validation_config,
)
from bestseller.services.market_validation.judge import judge_collisions, score_verdict

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from bestseller.services.search_client import WebSearchClient
    from bestseller.settings import AppSettings

logger = logging.getLogger(__name__)

_CHANNEL_TO_LABEL = {"male": "男频", "female": "女频", "general": "男频"}


def _taxonomy_labels(request: MarketValidationRequest) -> tuple[str, tuple[str, ...]]:
    """Best-effort genre/sub-genre display labels from the taxonomy."""

    genre_label = request.genre_label
    sub_labels: tuple[str, ...] = ()
    try:
        from bestseller.services.genre_taxonomy import get_genre

        genre = get_genre(request.genre_key) if request.genre_key else None
        if genre is not None:
            genre_label = genre_label or genre.label
            sub_labels = tuple(sub.label for sub in genre.sub_genres)
    except Exception:
        logger.debug("Taxonomy label lookup failed", exc_info=True)
    if request.sub_genre_label:
        sub_labels = (*sub_labels, request.sub_genre_label)
    return genre_label, sub_labels


def _dedupe_by_title(
    observations: list[MarketBookObservation],
) -> list[MarketBookObservation]:
    seen: set[str] = set()
    unique: list[MarketBookObservation] = []
    for obs in observations:
        if obs.title not in seen:
            seen.add(obs.title)
            unique.append(obs)
    return unique


async def run_market_validation(
    request: MarketValidationRequest,
    *,
    settings: AppSettings | None = None,
    session: AsyncSession | None = None,
    config: MarketValidationConfig | None = None,
    http_client: httpx.AsyncClient | None = None,
    search_client: WebSearchClient | None = None,
    project_id: UUID | None = None,
) -> MarketValidationReport:
    """Run the full advisory validation for one request."""

    config = config or load_market_validation_config()
    if not config.enabled:
        return MarketValidationReport(
            request=request,
            genre_heat=GenreHeatSection(
                status=MarketSectionStatus.SKIPPED, reason="市场验证能力未启用"
            ),
        )

    genre_label, sub_labels = _taxonomy_labels(request)
    fanqie_refs = resolve_fanqie_categories(
        config, genre_key=request.genre_key, sub_genre_key=request.sub_genre_key
    )
    channel_label = (
        fanqie_refs[0].channel_label
        if fanqie_refs
        else _CHANNEL_TO_LABEL.get(request.channel, "男频")
    )

    platforms_used: list[str] = []
    data_dates: dict[str, str] = {}
    fanqie_all: list[MarketBookObservation] = []
    fanqie_matched: list[MarketBookObservation] = []
    qimao_all: list[MarketBookObservation] = []
    qimao_matched: list[MarketBookObservation] = []

    if config.sources.fanqiehub.enabled:
        fanqie_all, fanqie_date = await fetch_fanqiehub_dataset(
            config.sources.fanqiehub, client=http_client
        )
        if fanqie_all:
            platforms_used.append("fanqie")
            data_dates["fanqie"] = fanqie_date
            if fanqie_refs:
                fanqie_matched = filter_fanqie_observations(
                    fanqie_all,
                    channel=channel_label,
                    category_labels=[ref.category_label for ref in fanqie_refs],
                )

    if config.sources.qimao.enabled:
        is_girl = 1 if channel_label == "女频" else 0
        for rank_type, board_type in ((1, "大热榜"), (2, "新书榜")):
            qimao_all.extend(
                await fetch_qimao_board(
                    config.sources.qimao,
                    is_girl=is_girl,
                    rank_type=rank_type,
                    board_type=board_type,
                    channel=channel_label,
                    client=http_client,
                )
            )
        if qimao_all:
            platforms_used.append("qimao")
            match_labels = qimao_match_labels(
                config,
                genre_key=request.genre_key,
                genre_label=genre_label,
                sub_genre_labels=sub_labels,
            )
            qimao_matched = [
                obs for obs in qimao_all if obs.category in match_labels
            ]

    # --- genre heat (fanqie primary; qimao appended as a note, heat scales
    # are not comparable across platforms) -------------------------------
    genre_heat = build_genre_heat(
        fanqie_matched,
        fanqie_refs,
        min_sample=config.genre_heat.min_sample,
        top_books=config.genre_heat.top_books,
    )
    if qimao_matched:
        heats = sorted(obs.heat for obs in qimao_matched)
        genre_heat = genre_heat.model_copy(
            update={
                "notes": [
                    *genre_heat.notes,
                    f"七猫榜单同题材在榜 {len(qimao_matched)} 本，"
                    f"热度中位 {heats[len(heats) // 2]}",
                ]
            }
        )

    # --- competitor scan -------------------------------------------------
    competitor_pool = _dedupe_by_title(
        sorted(
            [*fanqie_matched, *qimao_matched], key=lambda obs: obs.heat, reverse=True
        )
    )[: config.competitor_scan.max_candidates]
    competitors_shown = competitor_pool[: request.max_competitors]

    collisions: list = []
    collisions_judged = False
    if request.concept and competitor_pool:
        if (
            config.verdict.llm_enabled
            and session is not None
            and settings is not None
        ):
            collisions, collisions_judged = await judge_collisions(
                session,
                settings,
                concept=request.concept,
                competitors=competitor_pool,
                project_id=project_id,
            )
        competitor_scan = CompetitorScanSection(
            status=(
                MarketSectionStatus.OK
                if collisions_judged
                else MarketSectionStatus.DEGRADED
            ),
            reason="" if collisions_judged else "LLM 判官不可用，撞车扫描未执行",
            competitors=competitors_shown,
            collisions=collisions,
        )
    elif competitor_pool:
        competitor_scan = CompetitorScanSection(
            status=MarketSectionStatus.DEGRADED,
            reason="未提供概念，仅列出同题材竞品",
            competitors=competitors_shown,
        )
    else:
        competitor_scan = CompetitorScanSection(
            status=MarketSectionStatus.SKIPPED, reason="无同题材竞品数据"
        )

    # --- title check (dedup runs platform-wide, not category-limited) ----
    web_hits: dict[str, list[str]] = {}
    if (
        request.title_candidates
        and config.sources.websearch.enabled
        and search_client is not None
    ):
        for candidate in request.title_candidates:
            hits = await search_title_occupancy(
                search_client,
                title=candidate,
                site_filters=config.sources.websearch.site_filters,
            )
            if hits:
                web_hits[candidate] = hits
    title_board = [*fanqie_all, *qimao_all]
    title_check = check_titles(
        list(request.title_candidates), title_board, web_hits, config.title_check
    )
    if request.title_candidates and not title_board:
        title_check = title_check.model_copy(
            update={
                "status": MarketSectionStatus.DEGRADED,
                "reason": "平台榜单不可用，仅 Web 查重",
            }
        )

    # --- blurb benchmark -------------------------------------------------
    board_intros = [obs.intro for obs in fanqie_matched if obs.intro]
    blurb_benchmark = benchmark_blurb(
        request.blurb,
        board_intros,
        min_board_samples=config.blurb_benchmark.min_board_samples,
    )

    # --- verdict ----------------------------------------------------------
    verdict = score_verdict(
        genre_heat=genre_heat,
        title_check=title_check,
        collisions=collisions,
        collisions_judged=collisions_judged,
        blurb=blurb_benchmark,
        has_concept=bool(request.concept),
    )

    return MarketValidationReport(
        request=request,
        platforms_used=platforms_used,
        data_dates=data_dates,
        genre_heat=genre_heat,
        competitor_scan=competitor_scan,
        title_check=title_check,
        blurb_benchmark=blurb_benchmark,
        verdict=verdict,
    )
