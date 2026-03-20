from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from uuid import UUID

import typer
import yaml

from bestseller import __version__
from bestseller.domain.enums import ArtifactType
from bestseller.domain.planning import PlanningArtifactCreate
from bestseller.domain.project import ChapterCreate, ProjectCreate, SceneCardCreate
from bestseller.domain.workflow import ChapterOutlineBatchInput
from bestseller.infra.db.schema import initialize_database, render_schema_sql
from bestseller.infra.db.session import create_engine, session_scope
from bestseller.services.drafts import assemble_chapter_draft, generate_scene_draft
from bestseller.services.consistency import review_project_consistency
from bestseller.services.context import build_chapter_writer_context, build_scene_writer_context
from bestseller.services.exports import (
    export_chapter_docx,
    export_chapter_epub,
    export_chapter_markdown,
    export_chapter_pdf,
    export_project_docx,
    export_project_epub,
    export_project_markdown,
    export_project_pdf,
)
from bestseller.services.inspection import (
    build_project_structure,
    build_story_bible_overview,
    get_planning_artifact_detail,
    list_planning_artifacts,
)
from bestseller.services.knowledge import list_canon_facts, list_timeline_events
from bestseller.services.narrative import build_narrative_overview
from bestseller.services.narrative_tree import (
    build_narrative_tree_overview,
    get_narrative_tree_node_by_path,
    search_narrative_tree_for_project,
)
from bestseller.services.pipelines import (
    run_autowrite_pipeline,
    run_chapter_pipeline,
    run_project_pipeline,
    run_scene_pipeline,
)
from bestseller.services.planner import generate_novel_plan
from bestseller.services.projects import (
    create_chapter,
    create_project,
    create_scene_card,
    get_project_by_slug,
    import_planning_artifact,
    list_projects,
    load_json_file,
)
from bestseller.services.repair import run_project_repair
from bestseller.services.retrieval import refresh_project_retrieval_index, search_project_retrieval
from bestseller.services.rewrite_impacts import list_rewrite_impacts, refresh_rewrite_impacts
from bestseller.services.rewrite_cascade import run_rewrite_cascade
from bestseller.services.reviews import (
    review_chapter_draft,
    review_scene_draft,
    rewrite_chapter_from_task,
    rewrite_scene_from_task,
)
from bestseller.services.workflows import (
    get_workflow_run,
    list_workflow_runs,
    materialize_chapter_outline_batch,
    materialize_latest_chapter_outline_batch,
    materialize_latest_narrative_graph,
    materialize_latest_narrative_tree,
    materialize_latest_story_bible,
    materialize_narrative_graph,
    materialize_narrative_tree,
    materialize_story_bible,
)
from bestseller.settings import DEFAULT_CONFIG_PATH, load_settings, settings_to_dict


app = typer.Typer(
    help="BestSeller CLI for the PostgreSQL-first long-form novel framework."
)
db_app = typer.Typer(help="Database operations.")
project_app = typer.Typer(help="Project operations.")
planning_app = typer.Typer(help="Planning artifact operations.")
chapter_app = typer.Typer(help="Chapter operations.")
scene_app = typer.Typer(help="Scene operations.")
workflow_app = typer.Typer(help="Workflow operations.")
export_app = typer.Typer(help="Export operations.")
canon_app = typer.Typer(help="Canon fact operations.")
timeline_app = typer.Typer(help="Timeline operations.")
rewrite_app = typer.Typer(help="Rewrite task operations.")
retrieval_app = typer.Typer(help="Retrieval operations.")
story_bible_app = typer.Typer(help="Story bible inspection operations.")
narrative_app = typer.Typer(help="Narrative graph inspection operations.")

app.add_typer(db_app, name="db")
app.add_typer(project_app, name="project")
app.add_typer(planning_app, name="planning")
app.add_typer(chapter_app, name="chapter")
app.add_typer(scene_app, name="scene")
app.add_typer(workflow_app, name="workflow")
app.add_typer(export_app, name="export")
app.add_typer(canon_app, name="canon")
app.add_typer(timeline_app, name="timeline")
app.add_typer(rewrite_app, name="rewrite")
app.add_typer(retrieval_app, name="retrieval")
app.add_typer(story_bible_app, name="story-bible")
app.add_typer(narrative_app, name="narrative")


@app.callback()
def main() -> None:
    """Bootstrap the BestSeller command line interface."""


def _format_progress_details(payload: dict[str, Any] | None) -> str:
    if not payload:
        return ""
    details: list[str] = []
    for key, value in payload.items():
        if value is None:
            continue
        if isinstance(value, list):
            rendered = ", ".join(str(item) for item in value)
            details.append(f"{key}=[{rendered}]")
        else:
            details.append(f"{key}={value}")
    return " | " + " | ".join(details) if details else ""


def _autowrite_progress_printer(stage: str, payload: dict[str, Any] | None = None) -> None:
    labels = {
        "project_creation_started": "创建项目",
        "project_creation_completed": "项目已创建",
        "planning_started": "生成规划",
        "planning_completed": "规划生成完成",
        "story_bible_materialization_started": "物化故事圣经",
        "story_bible_materialization_completed": "故事圣经已物化",
        "outline_materialization_started": "物化章节大纲",
        "outline_materialization_completed": "章节大纲已物化",
        "narrative_graph_materialization_started": "物化叙事图谱",
        "narrative_graph_materialization_completed": "叙事图谱已物化",
        "narrative_tree_materialization_started": "物化叙事树",
        "narrative_tree_materialization_completed": "叙事树已物化",
        "project_pipeline_started": "开始整书流水线",
        "chapter_pipeline_started": "开始章节流水线",
        "chapter_pipeline_completed": "章节流水线完成",
        "project_export_started": "开始整书导出",
        "project_export_completed": "整书导出完成",
        "project_export_skipped": "跳过整书导出",
        "project_consistency_review_completed": "整书一致性审校完成",
        "project_pipeline_completed": "整书流水线结束",
        "auto_repair_started": "开始自动修复",
        "auto_repair_completed": "自动修复结束",
        "project_repair_started": "开始 repair 流程",
        "project_repair_targets_collected": "repair 影响面已收集",
        "project_repair_tasks_superseded": "旧 rewrite 任务已 supersede",
        "project_repair_chapter_started": "开始 repair 章节",
        "project_repair_chapter_completed": "repair 章节完成",
        "project_repair_export_started": "开始 repair 后导出",
        "project_repair_export_completed": "repair 后导出完成",
        "project_repair_review_completed": "repair 后一致性审校完成",
        "project_repair_completed": "repair 流程结束",
        "autowrite_completed": "autowrite 完成",
    }
    label = labels.get(stage, stage)
    typer.secho(
        f"[bestseller] {label}{_format_progress_details(payload)}",
        err=True,
        fg=typer.colors.CYAN,
    )


@app.command("status")
def status() -> None:
    """Show the current project scaffold status."""
    settings = load_settings()
    typer.echo(
        json.dumps(
            {
                "version": __version__,
                "config_path": str(DEFAULT_CONFIG_PATH),
                "database_url": settings.database.url,
                "retrieval_provider": settings.retrieval.provider,
                "llm_mock": settings.llm.mock,
                "llm_planner_model": settings.llm.planner.model,
                "llm_writer_model": settings.llm.writer.model,
                "llm_writer_api_base": settings.llm.writer.api_base,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


@app.command("config-show")
def config_show(
    config_path: Path = typer.Option(DEFAULT_CONFIG_PATH, exists=True, file_okay=True, dir_okay=False),
    format: str = typer.Option("json", "--format", help="json or yaml"),
) -> None:
    """Render the effective configuration."""
    settings = load_settings(config_path=config_path)
    payload = settings_to_dict(settings)
    if format == "yaml":
        typer.echo(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False))
        return
    typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))


@db_app.command("render-sql")
def db_render_sql() -> None:
    """Print PostgreSQL schema SQL for the current metadata."""
    typer.echo(render_schema_sql())


@db_app.command("init")
def db_init() -> None:
    """Create PostgreSQL extensions and tables in the configured database."""

    async def _run() -> None:
        settings = load_settings()
        engine = create_engine(settings)
        try:
            await initialize_database(engine)
        finally:
            await engine.dispose()

    asyncio.run(_run())
    typer.echo("Database initialization completed.")


@project_app.command("create")
def project_create(
    slug: str,
    title: str,
    genre: str,
    target_words: int,
    target_chapters: int,
    sub_genre: str | None = None,
    audience: str | None = None,
    language: str = "zh-CN",
) -> None:
    """Create a project and its default style guide."""

    async def _run() -> None:
        settings = load_settings()
        async with session_scope(settings) as session:
            project = await create_project(
                session,
                ProjectCreate(
                    slug=slug,
                    title=title,
                    genre=genre,
                    sub_genre=sub_genre,
                    audience=audience,
                    language=language,
                    target_word_count=target_words,
                    target_chapters=target_chapters,
                ),
                settings,
            )
            typer.echo(json.dumps({"id": str(project.id), "slug": project.slug}, indent=2))

    asyncio.run(_run())


@project_app.command("list")
def project_list() -> None:
    """List projects."""

    async def _run() -> None:
        settings = load_settings()
        async with session_scope(settings) as session:
            projects = await list_projects(session)
            payload = [
                {
                    "id": str(project.id),
                    "slug": project.slug,
                    "title": project.title,
                    "status": project.status,
                }
                for project in projects
            ]
            typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))

    asyncio.run(_run())


@project_app.command("show")
def project_show(slug: str) -> None:
    """Show one project by slug."""

    async def _run() -> None:
        settings = load_settings()
        async with session_scope(settings) as session:
            project = await get_project_by_slug(session, slug)
            if project is None:
                raise typer.BadParameter(f"Project '{slug}' was not found.")
            typer.echo(
                json.dumps(
                    {
                        "id": str(project.id),
                        "slug": project.slug,
                        "title": project.title,
                        "genre": project.genre,
                        "sub_genre": project.sub_genre,
                        "status": project.status,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )

    asyncio.run(_run())


@project_app.command("structure")
def project_structure(project_slug: str) -> None:
    """Show the current volume/chapter/scene structure for one project."""

    async def _run() -> None:
        settings = load_settings()
        async with session_scope(settings) as session:
            structure = await build_project_structure(session, project_slug)
            typer.echo(json.dumps(structure.model_dump(mode="json"), ensure_ascii=False, indent=2))

    asyncio.run(_run())


@project_app.command("review")
def project_review(project_slug: str) -> None:
    """Run one project-level consistency review."""

    async def _run() -> None:
        settings = load_settings()
        async with session_scope(settings) as session:
            review_result, report, quality = await review_project_consistency(
                session,
                settings,
                project_slug,
                expect_project_export=False,
            )
            typer.echo(
                json.dumps(
                    {
                        "report_id": str(report.id),
                        "quality_score_id": str(quality.id),
                        "verdict": review_result.verdict,
                        "severity_max": review_result.severity_max,
                        "scores": review_result.scores.model_dump(mode="json"),
                        "findings_count": len(review_result.findings),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )

    asyncio.run(_run())


@project_app.command("pipeline")
def project_pipeline(
    project_slug: str,
    requested_by: str = "system",
    materialize_story_bible: bool = typer.Option(
        False,
        "--materialize-story-bible/--no-materialize-story-bible",
        help="Whether to materialize book/world/cast/volume planning artifacts before running the project pipeline.",
    ),
    materialize_outline: bool = typer.Option(
        False,
        "--materialize-outline/--no-materialize-outline",
        help="Whether to materialize chapter outline into chapter/scene rows before running the project pipeline.",
    ),
    file: Path | None = typer.Option(
        None,
        exists=True,
        file_okay=True,
        dir_okay=False,
        help="Optional outline JSON file used when materializing structure.",
    ),
    export_markdown: bool = typer.Option(
        True,
        "--export-markdown/--no-export-markdown",
        help="Whether to export chapter and project markdown artifacts.",
    ),
) -> None:
    """Run the project-level pipeline across all current chapters."""

    async def _run() -> None:
        settings = load_settings()
        async with session_scope(settings) as session:
            result = await run_project_pipeline(
                session,
                settings,
                project_slug,
                requested_by=requested_by,
                materialize_story_bible=materialize_story_bible,
                materialize_outline=materialize_outline,
                outline_file=file,
                export_markdown=export_markdown,
            )
            typer.echo(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2))

    asyncio.run(_run())


@project_app.command("repair")
def project_repair(
    project_slug: str,
    requested_by: str = "system",
    refresh_impacts: bool = typer.Option(
        True,
        "--refresh-impacts/--no-refresh-impacts",
        help="Whether to recompute rewrite impacts for pending scene rewrite tasks before repair.",
    ),
    export_markdown: bool = typer.Option(
        True,
        "--export-markdown/--no-export-markdown",
        help="Whether to export the repaired project markdown artifact when repair finishes cleanly.",
    ),
    show_progress: bool = typer.Option(
        True,
        "--progress/--no-progress",
        help="Print repair stage updates and artifact paths to stderr while the pipeline is running.",
    ),
) -> None:
    """Supersede pending rewrite tasks, rerun affected chapters, and refresh project review."""

    async def _run() -> None:
        settings = load_settings()
        async with session_scope(settings) as session:
            result = await run_project_repair(
                session,
                settings,
                project_slug,
                requested_by=requested_by,
                refresh_impacts=refresh_impacts,
                export_markdown=export_markdown,
                progress=_autowrite_progress_printer if show_progress else None,
            )
            typer.echo(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2))

    asyncio.run(_run())


@project_app.command("autowrite")
def project_autowrite(
    slug: str,
    title: str,
    genre: str,
    target_words: int,
    target_chapters: int,
    premise: str = typer.Option(..., help="One-sentence or one-paragraph premise used to generate the whole novel plan."),
    sub_genre: str | None = None,
    audience: str | None = None,
    language: str = "zh-CN",
    requested_by: str = "system",
    export_markdown: bool = typer.Option(
        True,
        "--export-markdown/--no-export-markdown",
        help="Whether to export chapter and project markdown artifacts.",
    ),
    auto_repair: bool = typer.Option(
        True,
        "--auto-repair/--no-auto-repair",
        help="Automatically run one project repair pass when the first consistency review does not pass.",
    ),
    show_progress: bool = typer.Option(
        True,
        "--progress/--no-progress",
        help="Print stage updates and artifact paths to stderr while the pipeline is running.",
    ),
) -> None:
    """Create a project if needed, generate the full plan, and run the whole novel pipeline."""

    async def _run() -> None:
        settings = load_settings()
        async with session_scope(settings) as session:
            result = await run_autowrite_pipeline(
                session,
                settings,
                project_payload=ProjectCreate(
                    slug=slug,
                    title=title,
                    genre=genre,
                    sub_genre=sub_genre,
                    audience=audience,
                    language=language,
                    target_word_count=target_words,
                    target_chapters=target_chapters,
                ),
                premise=premise,
                requested_by=requested_by,
                export_markdown=export_markdown,
                auto_repair_on_attention=auto_repair,
                progress=_autowrite_progress_printer if show_progress else None,
            )
            typer.echo(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2))

    asyncio.run(_run())


@planning_app.command("import")
def planning_import(
    project_slug: str,
    artifact_type: ArtifactType,
    file: Path = typer.Option(..., exists=True, file_okay=True, dir_okay=False),
) -> None:
    """Store a planning artifact version from a JSON file."""

    async def _run() -> None:
        settings = load_settings()
        async with session_scope(settings) as session:
            artifact = await import_planning_artifact(
                session,
                project_slug,
                PlanningArtifactCreate(
                    artifact_type=artifact_type,
                    content=load_json_file(file),
                ),
            )
            typer.echo(
                json.dumps(
                    {
                        "id": str(artifact.id),
                        "artifact_type": artifact.artifact_type,
                        "version_no": artifact.version_no,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )

    asyncio.run(_run())


@planning_app.command("generate")
def planning_generate(
    project_slug: str,
    premise: str = typer.Option(..., help="The core premise used to auto-generate the novel plan."),
    requested_by: str = "system",
) -> None:
    """Generate premise/book/world/cast/volume/outline artifacts for one project."""

    async def _run() -> None:
        settings = load_settings()
        async with session_scope(settings) as session:
            result = await generate_novel_plan(
                session,
                settings,
                project_slug,
                premise,
                requested_by=requested_by,
            )
            typer.echo(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2))

    asyncio.run(_run())


@planning_app.command("list")
def planning_list(
    project_slug: str,
    artifact_type: ArtifactType | None = typer.Option(
        None,
        "--artifact-type",
        help="Optional planning artifact type filter.",
    ),
) -> None:
    """List stored planning artifact versions."""

    async def _run() -> None:
        settings = load_settings()
        async with session_scope(settings) as session:
            artifacts = await list_planning_artifacts(
                session,
                project_slug,
                artifact_type=artifact_type,
            )
            typer.echo(
                json.dumps(
                    [artifact.model_dump(mode="json") for artifact in artifacts],
                    ensure_ascii=False,
                    indent=2,
                )
            )

    asyncio.run(_run())


@planning_app.command("show")
def planning_show(
    project_slug: str,
    artifact_type: ArtifactType,
    version_no: int | None = typer.Option(
        None,
        "--version-no",
        min=1,
        help="Optional specific version number. Defaults to the latest version.",
    ),
) -> None:
    """Show one planning artifact version."""

    async def _run() -> None:
        settings = load_settings()
        async with session_scope(settings) as session:
            artifact = await get_planning_artifact_detail(
                session,
                project_slug,
                artifact_type,
                version_no=version_no,
            )
            if artifact is None:
                raise typer.BadParameter(
                    f"No planning artifact '{artifact_type.value}' was found for '{project_slug}'."
                )
            typer.echo(json.dumps(artifact.model_dump(mode="json"), ensure_ascii=False, indent=2))

    asyncio.run(_run())


@canon_app.command("list")
def canon_list(
    project_slug: str,
    current_only: bool = typer.Option(
        True,
        "--current-only/--all-versions",
        help="Whether to return only current canon facts.",
    ),
    subject_label: str | None = typer.Option(
        None,
        "--subject-label",
        help="Optional exact subject label filter, for example one character name.",
    ),
    chapter_number: int | None = typer.Option(
        None,
        "--chapter-number",
        help="Optional chapter number filter against canon validity window.",
    ),
) -> None:
    """List canon facts for one project."""

    async def _run() -> None:
        settings = load_settings()
        async with session_scope(settings) as session:
            facts = await list_canon_facts(
                session,
                project_slug,
                current_only=current_only,
                subject_label=subject_label,
                chapter_number=chapter_number,
            )
            payload = [
                {
                    "id": str(fact.id),
                    "subject_type": fact.subject_type,
                    "subject_label": fact.subject_label,
                    "predicate": fact.predicate,
                    "fact_type": fact.fact_type,
                    "value": fact.value_json,
                    "valid_from_chapter_no": fact.valid_from_chapter_no,
                    "valid_to_chapter_no": fact.valid_to_chapter_no,
                    "source_scene_id": str(fact.source_scene_id) if fact.source_scene_id else None,
                    "tags": fact.tags,
                    "is_current": fact.is_current,
                }
                for fact in facts
            ]
            typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))

    asyncio.run(_run())


@timeline_app.command("list")
def timeline_list(
    project_slug: str,
    chapter_number: int | None = typer.Option(
        None,
        "--chapter-number",
        help="Optional chapter number filter.",
    ),
) -> None:
    """List timeline events for one project."""

    async def _run() -> None:
        settings = load_settings()
        async with session_scope(settings) as session:
            events = await list_timeline_events(
                session,
                project_slug,
                chapter_number=chapter_number,
            )
            payload = [
                {
                    "id": str(event.id),
                    "event_name": event.event_name,
                    "event_type": event.event_type,
                    "story_time_label": event.story_time_label,
                    "story_order": float(event.story_order),
                    "participant_labels": list(event.participant_ids),
                    "consequences": list(event.consequences),
                    "chapter_id": str(event.chapter_id) if event.chapter_id else None,
                    "scene_card_id": str(event.scene_card_id) if event.scene_card_id else None,
                }
                for event in events
            ]
            typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))

    asyncio.run(_run())


@story_bible_app.command("show")
def story_bible_show(
    project_slug: str,
    before_chapter_number: int | None = typer.Option(
        None,
        "--before-chapter-number",
        min=0,
        help="Optional chapter boundary used to inspect historical character state.",
    ),
    before_scene_number: int | None = typer.Option(
        None,
        "--before-scene-number",
        min=0,
        help="Optional scene boundary used with --before-chapter-number.",
    ),
) -> None:
    """Show the current executable story bible for one project."""

    async def _run() -> None:
        settings = load_settings()
        async with session_scope(settings) as session:
            overview = await build_story_bible_overview(
                session,
                project_slug,
                before_chapter_number=before_chapter_number,
                before_scene_number=before_scene_number,
            )
            typer.echo(json.dumps(overview.model_dump(mode="json"), ensure_ascii=False, indent=2))

    asyncio.run(_run())


@narrative_app.command("show")
def narrative_show(project_slug: str) -> None:
    """Show the current narrative graph for one project."""

    async def _run() -> None:
        settings = load_settings()
        async with session_scope(settings) as session:
            overview = await build_narrative_overview(session, project_slug)
            typer.echo(json.dumps(overview.model_dump(mode="json"), ensure_ascii=False, indent=2))

    asyncio.run(_run())


@narrative_app.command("tree-show")
def narrative_tree_show(project_slug: str) -> None:
    """Show the current narrative tree index for one project."""

    async def _run() -> None:
        settings = load_settings()
        async with session_scope(settings) as session:
            overview = await build_narrative_tree_overview(session, project_slug)
            typer.echo(json.dumps(overview.model_dump(mode="json"), ensure_ascii=False, indent=2))

    asyncio.run(_run())


@narrative_app.command("path-show")
def narrative_path_show(
    project_slug: str,
    path: str = typer.Option(..., "--path", help="Narrative tree path such as /chapters/001/contract."),
) -> None:
    """Show one narrative tree node by deterministic path."""

    async def _run() -> None:
        settings = load_settings()
        async with session_scope(settings) as session:
            node = await get_narrative_tree_node_by_path(session, project_slug, path)
            typer.echo(
                json.dumps(
                    node.model_dump(mode="json") if node is not None else None,
                    ensure_ascii=False,
                    indent=2,
                )
            )

    asyncio.run(_run())


@narrative_app.command("search")
def narrative_search(
    project_slug: str,
    query: str = typer.Option(..., help="Query text used for narrative tree search."),
    path: list[str] | None = typer.Option(
        None,
        "--path",
        help="Optional preferred path scopes. Repeatable.",
    ),
    top_k: int = typer.Option(6, min=1, max=20),
) -> None:
    """Search the narrative tree with deterministic-path preference."""

    async def _run() -> None:
        settings = load_settings()
        async with session_scope(settings) as session:
            project = await get_project_by_slug(session, project_slug)
            if project is None:
                raise ValueError(f"Project '{project_slug}' was not found.")
            result = await search_narrative_tree_for_project(
                session,
                project,
                query,
                preferred_paths=list(path or []),
                top_k=top_k,
            )
            typer.echo(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2))

    asyncio.run(_run())


@retrieval_app.command("refresh")
def retrieval_refresh(project_slug: str) -> None:
    """Rebuild retrieval chunks for one project."""

    async def _run() -> None:
        settings = load_settings()
        async with session_scope(settings) as session:
            chunk_count = await refresh_project_retrieval_index(session, settings, project_slug)
            typer.echo(json.dumps({"project_slug": project_slug, "chunk_count": chunk_count}, ensure_ascii=False, indent=2))

    asyncio.run(_run())


@retrieval_app.command("search")
def retrieval_search(
    project_slug: str,
    query: str = typer.Option(..., help="Query text used for hybrid retrieval."),
    top_k: int | None = typer.Option(None, help="Override the configured top_k."),
) -> None:
    """Search retrieval chunks for one project."""

    async def _run() -> None:
        settings = load_settings()
        async with session_scope(settings) as session:
            result = await search_project_retrieval(
                session,
                settings,
                project_slug,
                query,
                top_k=top_k,
            )
            typer.echo(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2))

    asyncio.run(_run())


@chapter_app.command("add")
def chapter_add(
    project_slug: str,
    chapter_number: int,
    chapter_goal: str,
    title: str | None = None,
    volume_number: int = 1,
    target_words: int = 3000,
) -> None:
    """Create a chapter structure row."""

    async def _run() -> None:
        settings = load_settings()
        async with session_scope(settings) as session:
            chapter = await create_chapter(
                session,
                project_slug,
                ChapterCreate(
                    chapter_number=chapter_number,
                    title=title,
                    chapter_goal=chapter_goal,
                    volume_number=volume_number,
                    target_word_count=target_words,
                ),
            )
            typer.echo(
                json.dumps(
                    {
                        "id": str(chapter.id),
                        "chapter_number": chapter.chapter_number,
                        "status": chapter.status,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )

    asyncio.run(_run())


@chapter_app.command("assemble")
def chapter_assemble(project_slug: str, chapter_number: int) -> None:
    """Assemble the current scene drafts into one chapter draft."""

    async def _run() -> None:
        settings = load_settings()
        async with session_scope(settings) as session:
            draft = await assemble_chapter_draft(session, project_slug, chapter_number)
            typer.echo(
                json.dumps(
                    {
                        "id": str(draft.id),
                        "chapter_id": str(draft.chapter_id),
                        "version_no": draft.version_no,
                        "word_count": draft.word_count,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )

    asyncio.run(_run())


@chapter_app.command("context")
def chapter_context(project_slug: str, chapter_number: int) -> None:
    """Show the assembled writer context for one chapter."""

    async def _run() -> None:
        settings = load_settings()
        async with session_scope(settings) as session:
            packet = await build_chapter_writer_context(
                session,
                settings,
                project_slug,
                chapter_number,
            )
            typer.echo(json.dumps(packet.model_dump(mode="json"), ensure_ascii=False, indent=2))

    asyncio.run(_run())


@chapter_app.command("review")
def chapter_review(project_slug: str, chapter_number: int) -> None:
    """Review the current chapter draft and persist review artifacts."""

    async def _run() -> None:
        settings = load_settings()
        async with session_scope(settings) as session:
            review_result, report, quality, rewrite_task = await review_chapter_draft(
                session,
                settings,
                project_slug,
                chapter_number,
            )
            typer.echo(
                json.dumps(
                    {
                        "report_id": str(report.id),
                        "quality_score_id": str(quality.id),
                        "verdict": review_result.verdict,
                        "severity_max": review_result.severity_max,
                        "scores": review_result.scores.model_dump(mode="json"),
                        "rewrite_task_id": str(rewrite_task.id) if rewrite_task is not None else None,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )

    asyncio.run(_run())


@chapter_app.command("rewrite")
def chapter_rewrite(
    project_slug: str,
    chapter_number: int,
    rewrite_task_id: str | None = None,
) -> None:
    """Rewrite one chapter from the latest pending chapter rewrite task."""

    async def _run() -> None:
        settings = load_settings()
        async with session_scope(settings) as session:
            draft, rewrite_task = await rewrite_chapter_from_task(
                session,
                project_slug,
                chapter_number,
                rewrite_task_id=UUID(rewrite_task_id) if rewrite_task_id is not None else None,
                settings=settings,
            )
            typer.echo(
                json.dumps(
                    {
                        "draft_id": str(draft.id),
                        "version_no": draft.version_no,
                        "word_count": draft.word_count,
                        "rewrite_task_id": str(rewrite_task.id),
                        "rewrite_task_status": rewrite_task.status,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )

    asyncio.run(_run())


@scene_app.command("add")
def scene_add(
    project_slug: str,
    chapter_number: int,
    scene_number: int,
    scene_type: str,
    title: str | None = None,
    target_words: int = 1000,
) -> None:
    """Create a scene card row."""

    async def _run() -> None:
        settings = load_settings()
        async with session_scope(settings) as session:
            scene = await create_scene_card(
                session,
                project_slug,
                chapter_number,
                SceneCardCreate(
                    scene_number=scene_number,
                    scene_type=scene_type,
                    title=title,
                    target_word_count=target_words,
                ),
            )
            typer.echo(
                json.dumps(
                    {
                        "id": str(scene.id),
                        "scene_number": scene.scene_number,
                        "status": scene.status,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )

    asyncio.run(_run())


@scene_app.command("draft")
def scene_draft(project_slug: str, chapter_number: int, scene_number: int) -> None:
    """Generate a scene draft from one scene card."""

    async def _run() -> None:
        settings = load_settings()
        async with session_scope(settings) as session:
            draft = await generate_scene_draft(
                session,
                project_slug,
                chapter_number,
                scene_number,
                settings=settings,
            )
            typer.echo(
                json.dumps(
                    {
                        "id": str(draft.id),
                        "scene_card_id": str(draft.scene_card_id),
                        "version_no": draft.version_no,
                        "word_count": draft.word_count,
                        "model_name": draft.model_name,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )

    asyncio.run(_run())


@scene_app.command("context")
def scene_context(project_slug: str, chapter_number: int, scene_number: int) -> None:
    """Show the assembled writer context for one scene."""

    async def _run() -> None:
        settings = load_settings()
        async with session_scope(settings) as session:
            packet = await build_scene_writer_context(
                session,
                settings,
                project_slug,
                chapter_number,
                scene_number,
            )
            typer.echo(json.dumps(packet.model_dump(mode="json"), ensure_ascii=False, indent=2))

    asyncio.run(_run())


@scene_app.command("pipeline")
def scene_pipeline(
    project_slug: str,
    chapter_number: int,
    scene_number: int,
    requested_by: str = "system",
) -> None:
    """Run draft -> review -> rewrite loop for one scene until pass or revision limit."""

    async def _run() -> None:
        settings = load_settings()
        async with session_scope(settings) as session:
            result = await run_scene_pipeline(
                session,
                settings,
                project_slug,
                chapter_number,
                scene_number,
                requested_by=requested_by,
            )
            typer.echo(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2))

    asyncio.run(_run())


@scene_app.command("review")
def scene_review(project_slug: str, chapter_number: int, scene_number: int) -> None:
    """Review the current scene draft and persist quality/rewrite artifacts."""

    async def _run() -> None:
        settings = load_settings()
        async with session_scope(settings) as session:
            review_result, report, quality, rewrite_task = await review_scene_draft(
                session,
                settings,
                project_slug,
                chapter_number,
                scene_number,
            )
            rewrite_impacts = []
            if rewrite_task is not None:
                rewrite_impacts = await list_rewrite_impacts(
                    session,
                    project_slug,
                    rewrite_task_id=rewrite_task.id,
                )
            typer.echo(
                json.dumps(
                    {
                        "report_id": str(report.id),
                        "quality_score_id": str(quality.id),
                        "verdict": review_result.verdict,
                        "severity_max": review_result.severity_max,
                        "scores": review_result.scores.model_dump(mode="json"),
                        "rewrite_task_id": str(rewrite_task.id) if rewrite_task is not None else None,
                        "rewrite_impact_count": len(rewrite_impacts),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )

    asyncio.run(_run())


@scene_app.command("rewrite")
def scene_rewrite(
    project_slug: str,
    chapter_number: int,
    scene_number: int,
    rewrite_task_id: str | None = None,
) -> None:
    """Rewrite one scene from the latest pending rewrite task."""

    async def _run() -> None:
        settings = load_settings()
        async with session_scope(settings) as session:
            draft, rewrite_task = await rewrite_scene_from_task(
                session,
                project_slug,
                chapter_number,
                scene_number,
                rewrite_task_id=UUID(rewrite_task_id) if rewrite_task_id is not None else None,
                settings=settings,
            )
            typer.echo(
                json.dumps(
                    {
                        "draft_id": str(draft.id),
                        "version_no": draft.version_no,
                        "word_count": draft.word_count,
                        "rewrite_task_id": str(rewrite_task.id),
                        "rewrite_task_status": rewrite_task.status,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )

    asyncio.run(_run())


@rewrite_app.command("impacts")
def rewrite_impacts(
    project_slug: str,
    rewrite_task_id: str | None = typer.Option(None, help="Specific rewrite task UUID."),
    chapter_number: int | None = typer.Option(None, help="Chapter number of the source scene."),
    scene_number: int | None = typer.Option(None, help="Scene number of the source scene."),
    refresh: bool = typer.Option(
        True,
        "--refresh/--no-refresh",
        help="Recompute impacts before listing them.",
    ),
) -> None:
    """List or recompute rewrite impacts for one rewrite task."""

    async def _run() -> None:
        settings = load_settings()
        async with session_scope(settings) as session:
            if refresh:
                result = await refresh_rewrite_impacts(
                    session,
                    project_slug,
                    rewrite_task_id=UUID(rewrite_task_id) if rewrite_task_id is not None else None,
                    chapter_number=chapter_number,
                    scene_number=scene_number,
                )
                payload = result.model_dump(mode="json")
            else:
                if rewrite_task_id is None:
                    raise typer.BadParameter("rewrite_task_id is required when --no-refresh is used.")
                impacts = await list_rewrite_impacts(
                    session,
                    project_slug,
                    rewrite_task_id=UUID(rewrite_task_id),
                )
                payload = {
                    "rewrite_task_id": rewrite_task_id,
                    "impact_count": len(impacts),
                    "max_impact_level": (
                        "must"
                        if any(impact.impact_level == "must" for impact in impacts)
                        else "should"
                        if any(impact.impact_level == "should" for impact in impacts)
                        else "may"
                        if impacts
                        else "none"
                    ),
                    "impacts": [
                        {
                            "id": str(impact.id),
                            "impacted_type": impact.impacted_type,
                            "impacted_id": str(impact.impacted_id),
                            "impact_level": impact.impact_level,
                            "impact_score": float(impact.impact_score),
                            "reason": impact.reason,
                        }
                        for impact in impacts
                    ],
                }
            typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))

    asyncio.run(_run())


@rewrite_app.command("cascade")
def rewrite_cascade(
    project_slug: str,
    rewrite_task_id: str | None = typer.Option(None, help="Specific rewrite task UUID."),
    chapter_number: int | None = typer.Option(None, help="Chapter number of the source scene."),
    scene_number: int | None = typer.Option(None, help="Scene number of the source scene."),
    requested_by: str = "system",
    refresh: bool = typer.Option(
        True,
        "--refresh/--no-refresh",
        help="Recompute impacts before cascading chapter reruns.",
    ),
    export_markdown: bool = typer.Option(
        False,
        "--export-markdown/--no-export-markdown",
        help="Whether to export rerun chapters during cascade processing.",
    ),
) -> None:
    """Process rewrite impacts by rerunning affected chapters."""

    async def _run() -> None:
        settings = load_settings()
        async with session_scope(settings) as session:
            result = await run_rewrite_cascade(
                session,
                settings,
                project_slug,
                rewrite_task_id=UUID(rewrite_task_id) if rewrite_task_id is not None else None,
                chapter_number=chapter_number,
                scene_number=scene_number,
                requested_by=requested_by,
                refresh=refresh,
                export_markdown=export_markdown,
            )
            typer.echo(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2))

    asyncio.run(_run())


@chapter_app.command("pipeline")
def chapter_pipeline(
    project_slug: str,
    chapter_number: int,
    requested_by: str = "system",
    export_markdown: bool = typer.Option(
        False,
        "--export-markdown/--no-export-markdown",
        help="Whether to export the assembled chapter draft to Markdown.",
    ),
) -> None:
    """Run all scene pipelines in one chapter and assemble the chapter draft."""

    async def _run() -> None:
        settings = load_settings()
        async with session_scope(settings) as session:
            result = await run_chapter_pipeline(
                session,
                settings,
                project_slug,
                chapter_number,
                requested_by=requested_by,
                export_markdown=export_markdown,
            )
            typer.echo(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2))

    asyncio.run(_run())


@workflow_app.command("materialize-outline")
def workflow_materialize_outline(
    project_slug: str,
    file: Path | None = typer.Option(
        None,
        exists=True,
        file_okay=True,
        dir_okay=False,
        help="Optional JSON outline file. If omitted, the latest stored chapter_outline_batch artifact is used.",
    ),
    requested_by: str = "system",
) -> None:
    """Materialize chapter and scene structure from a chapter outline batch."""

    async def _run() -> None:
        settings = load_settings()
        async with session_scope(settings) as session:
            if file is not None:
                batch = ChapterOutlineBatchInput.model_validate(load_json_file(file))
                result = await materialize_chapter_outline_batch(
                    session,
                    project_slug,
                    batch,
                    requested_by=requested_by,
                )
            else:
                result = await materialize_latest_chapter_outline_batch(
                    session,
                    project_slug,
                    requested_by=requested_by,
                )
            typer.echo(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2))

    asyncio.run(_run())


@workflow_app.command("materialize-story-bible")
def workflow_materialize_story_bible(
    project_slug: str,
    book_spec_file: Path | None = typer.Option(
        None,
        exists=True,
        file_okay=True,
        dir_okay=False,
        help="Optional book spec JSON file.",
    ),
    world_spec_file: Path | None = typer.Option(
        None,
        exists=True,
        file_okay=True,
        dir_okay=False,
        help="Optional world spec JSON file.",
    ),
    cast_spec_file: Path | None = typer.Option(
        None,
        exists=True,
        file_okay=True,
        dir_okay=False,
        help="Optional cast spec JSON file.",
    ),
    volume_plan_file: Path | None = typer.Option(
        None,
        exists=True,
        file_okay=True,
        dir_okay=False,
        help="Optional volume plan JSON file.",
    ),
    requested_by: str = "system",
) -> None:
    """Materialize story bible artifacts into executable project entities."""

    async def _run() -> None:
        settings = load_settings()
        async with session_scope(settings) as session:
            if any(
                item is not None
                for item in (book_spec_file, world_spec_file, cast_spec_file, volume_plan_file)
            ):
                result = await materialize_story_bible(
                    session,
                    project_slug,
                    requested_by=requested_by,
                    book_spec_content=load_json_file(book_spec_file) if book_spec_file is not None else None,
                    world_spec_content=load_json_file(world_spec_file) if world_spec_file is not None else None,
                    cast_spec_content=load_json_file(cast_spec_file) if cast_spec_file is not None else None,
                    volume_plan_content=load_json_file(volume_plan_file) if volume_plan_file is not None else None,
                )
            else:
                result = await materialize_latest_story_bible(
                    session,
                    project_slug,
                    requested_by=requested_by,
                )
            typer.echo(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2))

    asyncio.run(_run())


@workflow_app.command("materialize-narrative-graph")
def workflow_materialize_narrative_graph(
    project_slug: str,
    volume_plan_file: Path | None = typer.Option(
        None,
        exists=True,
        file_okay=True,
        dir_okay=False,
        help="Optional volume plan JSON file. If omitted, the latest stored volume_plan artifact is used when available.",
    ),
    requested_by: str = "system",
) -> None:
    """Materialize plot arcs, beats, clues, payoffs and contracts."""

    async def _run() -> None:
        settings = load_settings()
        async with session_scope(settings) as session:
            if volume_plan_file is not None:
                result = await materialize_narrative_graph(
                    session,
                    project_slug,
                    requested_by=requested_by,
                    volume_plan_content=load_json_file(volume_plan_file),
                )
            else:
                result = await materialize_latest_narrative_graph(
                    session,
                    project_slug,
                    requested_by=requested_by,
                )
            typer.echo(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2))

    asyncio.run(_run())


@workflow_app.command("materialize-narrative-tree")
def workflow_materialize_narrative_tree(
    project_slug: str,
    requested_by: str = "system",
) -> None:
    """Materialize the narrative tree index from current project state."""

    async def _run() -> None:
        settings = load_settings()
        async with session_scope(settings) as session:
            result = await materialize_narrative_tree(
                session,
                project_slug,
                requested_by=requested_by,
            )
            typer.echo(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2))

    asyncio.run(_run())


@workflow_app.command("list")
def workflow_list(project_slug: str) -> None:
    """List workflow runs for one project."""

    async def _run() -> None:
        settings = load_settings()
        async with session_scope(settings) as session:
            workflow_runs = await list_workflow_runs(session, project_slug)
            payload = [
                {
                    "id": str(workflow_run.id),
                    "workflow_type": workflow_run.workflow_type,
                    "status": workflow_run.status,
                    "current_step": workflow_run.current_step,
                }
                for workflow_run in workflow_runs
            ]
            typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))

    asyncio.run(_run())


@workflow_app.command("show")
def workflow_show(workflow_run_id: str) -> None:
    """Show one workflow run by UUID."""

    async def _run() -> None:
        settings = load_settings()
        async with session_scope(settings) as session:
            workflow_run = await get_workflow_run(session, UUID(workflow_run_id))
            if workflow_run is None:
                raise typer.BadParameter(f"Workflow run '{workflow_run_id}' was not found.")
            typer.echo(
                json.dumps(
                    {
                        "id": str(workflow_run.id),
                        "project_id": str(workflow_run.project_id)
                        if workflow_run.project_id is not None
                        else None,
                        "workflow_type": workflow_run.workflow_type,
                        "status": workflow_run.status,
                        "scope_type": workflow_run.scope_type,
                        "scope_id": str(workflow_run.scope_id)
                        if workflow_run.scope_id is not None
                        else None,
                        "requested_by": workflow_run.requested_by,
                        "current_step": workflow_run.current_step,
                        "error_message": workflow_run.error_message,
                        "metadata": workflow_run.metadata_json,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )

    asyncio.run(_run())


@export_app.command("markdown")
def export_markdown(project_slug: str, chapter_number: int | None = None) -> None:
    """Export current chapter draft or project draft to Markdown."""

    async def _run() -> None:
        settings = load_settings()
        async with session_scope(settings) as session:
            if chapter_number is not None:
                artifact, output_path = await export_chapter_markdown(
                    session,
                    settings,
                    project_slug,
                    chapter_number,
                )
            else:
                artifact, output_path = await export_project_markdown(
                    session,
                    settings,
                    project_slug,
                )

            typer.echo(
                json.dumps(
                    {
                        "id": str(artifact.id),
                        "storage_uri": artifact.storage_uri,
                        "checksum": artifact.checksum,
                        "version_label": artifact.version_label,
                        "output_path": str(output_path.resolve()),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )

    asyncio.run(_run())


@export_app.command("docx")
def export_docx(project_slug: str, chapter_number: int | None = None) -> None:
    """Export current chapter draft or project draft to DOCX."""

    async def _run() -> None:
        settings = load_settings()
        async with session_scope(settings) as session:
            if chapter_number is not None:
                artifact, output_path = await export_chapter_docx(
                    session,
                    settings,
                    project_slug,
                    chapter_number,
                )
            else:
                artifact, output_path = await export_project_docx(
                    session,
                    settings,
                    project_slug,
                )
            typer.echo(
                json.dumps(
                    {
                        "id": str(artifact.id),
                        "storage_uri": artifact.storage_uri,
                        "checksum": artifact.checksum,
                        "version_label": artifact.version_label,
                        "output_path": str(output_path.resolve()),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )

    asyncio.run(_run())


@export_app.command("epub")
def export_epub(project_slug: str, chapter_number: int | None = None) -> None:
    """Export current chapter draft or project draft to EPUB."""

    async def _run() -> None:
        settings = load_settings()
        async with session_scope(settings) as session:
            if chapter_number is not None:
                artifact, output_path = await export_chapter_epub(
                    session,
                    settings,
                    project_slug,
                    chapter_number,
                )
            else:
                artifact, output_path = await export_project_epub(
                    session,
                    settings,
                    project_slug,
                )
            typer.echo(
                json.dumps(
                    {
                        "id": str(artifact.id),
                        "storage_uri": artifact.storage_uri,
                        "checksum": artifact.checksum,
                        "version_label": artifact.version_label,
                        "output_path": str(output_path.resolve()),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )

    asyncio.run(_run())


@export_app.command("pdf")
def export_pdf(project_slug: str, chapter_number: int | None = None) -> None:
    """Export current chapter draft or project draft to PDF."""

    async def _run() -> None:
        settings = load_settings()
        async with session_scope(settings) as session:
            if chapter_number is not None:
                artifact, output_path = await export_chapter_pdf(
                    session,
                    settings,
                    project_slug,
                    chapter_number,
                )
            else:
                artifact, output_path = await export_project_pdf(
                    session,
                    settings,
                    project_slug,
                )
            typer.echo(
                json.dumps(
                    {
                        "id": str(artifact.id),
                        "storage_uri": artifact.storage_uri,
                        "checksum": artifact.checksum,
                        "version_label": artifact.version_label,
                        "output_path": str(output_path.resolve()),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )

    try:
        asyncio.run(_run())
    except RuntimeError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from exc
