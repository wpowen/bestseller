"""CLI for the market validation subsystem (``bestseller market-validation``).

Standalone lane: usable before a project exists (pass --genre/--concept/
--title directly) or against an existing project (--project-slug pulls the
concept, title and blurb from the conception snapshot metadata).
"""

# ruff: noqa: RUF001, RUF002 — Chinese market vocabulary is intentional.
from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Annotated

import typer

from bestseller.services.market_validation.request_builder import (
    build_creation_request,
    resolve_taxonomy_keys,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from bestseller.domain.market_validation import MarketValidationRequest

market_validation_app = typer.Typer(
    help="Advisory market validation: genre heat, competitors, title dedup, verdict."
)


def _echo_json(payload: object) -> None:
    typer.echo(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


async def _request_from_project(
    project_slug: str, session: AsyncSession
) -> MarketValidationRequest:
    from bestseller.services.projects import get_project_by_slug

    project = await get_project_by_slug(session, project_slug)
    if project is None:
        raise typer.BadParameter(f"Project '{project_slug}' was not found.")
    metadata = dict(getattr(project, "metadata_json", None) or {})
    return build_creation_request(
        metadata=metadata,
        genre_label=str(project.genre or ""),
        sub_genre_label=str(project.sub_genre or ""),
        title=str(project.title or ""),
        concept=str(metadata.get("concept_seed") or metadata.get("premise") or ""),
        blurb=str(metadata.get("synopsis") or ""),
        project_slug=project_slug,
    )


@market_validation_app.command("validate")
def validate(
    genre: Annotated[
        str, typer.Option(help="题材（taxonomy key 或中文名，如 xianxia / 修仙）。")
    ] = "",
    sub_genre: Annotated[str, typer.Option(help="子题材（key 或中文名）。")] = "",
    channel: Annotated[str, typer.Option(help="male/female；未给时按题材映射推断。")] = "",
    concept: Annotated[str, typer.Option(help="一句话概念/故事创意。")] = "",
    title: Annotated[
        list[str] | None, typer.Option(help="候选书名，可重复传多个。")
    ] = None,
    blurb: Annotated[str, typer.Option(help="简介文案。")] = "",
    project_slug: Annotated[
        str, typer.Option(help="从已有项目读取题材/书名/概念/简介。")
    ] = "",
    persist: Annotated[
        bool,
        typer.Option(
            "--persist/--no-persist",
            help="把报告落为 planning artifact 并回填 metadata（需 --project-slug）。",
        ),
    ] = False,
    use_llm: Annotated[
        bool,
        typer.Option("--llm/--no-llm", help="启用 LLM 撞车判官（需模型可用）。"),
    ] = True,
) -> None:
    """跑一次完整市场验证，输出 advisory 报告（绝不阻断任何流程）。"""

    if persist and not project_slug:
        raise typer.BadParameter("--persist requires --project-slug.")
    if not project_slug and not genre:
        raise typer.BadParameter("Provide --genre or --project-slug.")

    async def _run() -> None:
        from bestseller.infra.db.session import session_scope
        from bestseller.services.market_validation.repository import (
            persist_market_validation_report,
        )
        from bestseller.services.market_validation.service import (
            run_market_validation,
        )
        from bestseller.services.search_client import build_search_client
        from bestseller.settings import load_settings

        settings = load_settings()
        search_client = build_search_client()
        try:
            async with session_scope(settings) as session:
                if project_slug:
                    request = await _request_from_project(project_slug, session)
                    if genre:
                        genre_key, sub_key = resolve_taxonomy_keys(
                            genre_label=genre,
                            sub_genre_label=sub_genre,
                            fallback_genre_key=genre,
                        )
                        request = request.model_copy(
                            update={"genre_key": genre_key, "sub_genre_key": sub_key}
                        )
                    if concept:
                        request = request.model_copy(update={"concept": concept})
                    if title:
                        request = request.model_copy(
                            update={"title_candidates": tuple(title)}
                        )
                    if blurb:
                        request = request.model_copy(update={"blurb": blurb})
                else:
                    request = build_creation_request(
                        genre_label=genre,
                        sub_genre_label=sub_genre,
                        title=tuple(title or ()),
                        concept=concept,
                        blurb=blurb,
                        channel=channel,
                        fallback_genre_key=genre,
                    )

                report = await run_market_validation(
                    request,
                    settings=settings if use_llm else None,
                    session=session if use_llm else None,
                    search_client=search_client,
                )
                receipt = None
                if persist:
                    receipt = await persist_market_validation_report(
                        session, project_slug, report
                    )
                payload = report.model_dump(mode="json")
                if receipt is not None:
                    payload["_persisted"] = receipt
                _echo_json(payload)
        finally:
            await search_client.close()

    asyncio.run(_run())


@market_validation_app.command("heat")
def heat(
    genre: Annotated[str, typer.Option(help="题材（key 或中文名）。")],
    sub_genre: Annotated[str, typer.Option(help="子题材（key 或中文名）。")] = "",
) -> None:
    """只看题材热度（无 DB、无 LLM、无检索的最快路径）。"""

    async def _run() -> None:
        from bestseller.services.market_validation.service import (
            run_market_validation,
        )

        report = await run_market_validation(
            build_creation_request(
                genre_label=genre,
                sub_genre_label=sub_genre,
                fallback_genre_key=genre,
            )
        )
        _echo_json(
            {
                "genre_heat": report.genre_heat.model_dump(mode="json"),
                "platforms_used": report.platforms_used,
                "data_dates": report.data_dates,
            }
        )

    asyncio.run(_run())


@market_validation_app.command("inspect")
def inspect(
    project_slug: Annotated[str, typer.Argument(help="项目 slug。")],
) -> None:
    """读回项目最近一次市场验证报告。"""

    async def _run() -> None:
        from bestseller.domain.enums import ArtifactType
        from bestseller.infra.db.session import session_scope
        from bestseller.services.projects import get_project_by_slug
        from bestseller.services.workflows import (
            get_latest_planning_artifact,
        )
        from bestseller.settings import load_settings

        settings = load_settings()
        async with session_scope(settings) as session:
            project = await get_project_by_slug(session, project_slug)
            if project is None:
                raise typer.BadParameter(f"Project '{project_slug}' was not found.")
            artifact = await get_latest_planning_artifact(
                session,
                project_id=project.id,
                artifact_type=ArtifactType.MARKET_VALIDATION_REPORT,
            )
            if artifact is None:
                _echo_json({"project": project_slug, "report": None})
                return
            _echo_json(
                {
                    "project": project_slug,
                    "version_no": artifact.version_no,
                    "created_at": artifact.created_at,
                    "report": artifact.content,
                }
            )

    asyncio.run(_run())
