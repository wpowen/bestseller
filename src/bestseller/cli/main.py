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
from bestseller.domain.project import (
    ChapterCreate,
    InteractiveFictionConfig,
    ProjectCreate,
    SceneCardCreate,
)
from bestseller.domain.workflow import ChapterOutlineBatchInput
from bestseller.infra.db.schema import initialize_database, render_schema_sql
from bestseller.infra.db.session import create_engine, session_scope
from bestseller.services.consistency import review_project_consistency
from bestseller.services.context import build_chapter_writer_context, build_scene_writer_context
from bestseller.services.drafts import assemble_chapter_draft, generate_scene_draft
from bestseller.services.evaluation import (
    list_benchmark_suites,
    load_benchmark_suite,
    run_benchmark_suite,
)
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
from bestseller.services.if_generation import run_if_pipeline, run_if_pipeline_integrated
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
from bestseller.services.audit_loop import (
    build_full_audit,
    build_phase1_audit,
    persist_audit_findings,
)
from bestseller.services.scorecard import compute_scorecard, save_scorecard
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
from bestseller.services.prompt_packs import get_prompt_pack, list_prompt_packs
from bestseller.services.publishing.amazon_kdp import (
    init_amazon_kdp_profile,
    package_amazon_kdp_project,
    show_amazon_kdp_profile,
    validate_amazon_kdp_project,
)
from bestseller.services.repair import run_project_repair
from bestseller.services.project_health import build_project_health_report, repair_project_health
from bestseller.services.retrieval import refresh_project_retrieval_index, search_project_retrieval
from bestseller.services.reviews import (
    review_chapter_draft,
    review_scene_draft,
    rewrite_chapter_from_task,
    rewrite_scene_from_task,
)
from bestseller.services.rewrite_cascade import run_rewrite_cascade
from bestseller.services.rewrite_impacts import list_rewrite_impacts, refresh_rewrite_impacts
from bestseller.services.workflows import (
    get_workflow_run,
    list_workflow_runs,
    materialize_chapter_outline_batch,
    materialize_latest_chapter_outline_batch,
    materialize_latest_narrative_graph,
    materialize_latest_story_bible,
    materialize_narrative_graph,
    materialize_narrative_tree,
    materialize_story_bible,
)
from bestseller.services.writing_presets import (
    get_genre_preset,
    list_genre_presets,
    list_hot_genre_presets,
    list_length_presets,
    list_platform_presets,
    load_writing_preset_catalog,
    validate_longform_scope,
)
from bestseller.settings import DEFAULT_CONFIG_PATH, load_settings, settings_to_dict
from bestseller.web import serve_web_app

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
export_amazon_kdp_app = typer.Typer(help="Amazon KDP export operations.")
canon_app = typer.Typer(help="Canon fact operations.")
timeline_app = typer.Typer(help="Timeline operations.")
rewrite_app = typer.Typer(help="Rewrite task operations.")
retrieval_app = typer.Typer(help="Retrieval operations.")
story_bible_app = typer.Typer(help="Story bible inspection operations.")
narrative_app = typer.Typer(help="Narrative graph inspection operations.")
benchmark_app = typer.Typer(help="Benchmark and evaluation operations.")
ui_app = typer.Typer(help="Web UI operations.")
prompt_pack_app = typer.Typer(help="Prompt pack operations.")
writing_preset_app = typer.Typer(help="Writing preset operations.")
if_app = typer.Typer(help="Interactive fiction (LifeScript) operations.")
publish_profile_app = typer.Typer(help="Publication profile operations.")

app.add_typer(db_app, name="db")
app.add_typer(project_app, name="project")
app.add_typer(planning_app, name="planning")
app.add_typer(chapter_app, name="chapter")
app.add_typer(scene_app, name="scene")
app.add_typer(workflow_app, name="workflow")
app.add_typer(export_app, name="export")
app.add_typer(publish_profile_app, name="publish-profile")
app.add_typer(canon_app, name="canon")
app.add_typer(timeline_app, name="timeline")
app.add_typer(rewrite_app, name="rewrite")
app.add_typer(retrieval_app, name="retrieval")
app.add_typer(story_bible_app, name="story-bible")
app.add_typer(narrative_app, name="narrative")
app.add_typer(benchmark_app, name="benchmark")
app.add_typer(ui_app, name="ui")
app.add_typer(prompt_pack_app, name="prompt-pack")
app.add_typer(writing_preset_app, name="writing-preset")
app.add_typer(if_app, name="if")
export_app.add_typer(export_amazon_kdp_app, name="amazon-kdp")


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


def _load_structured_payload_file(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise typer.BadParameter(f"{path} must contain a JSON/YAML object.")
    return raw


def _apply_prompt_pack_to_profile_payload(
    writing_profile_payload: dict[str, Any] | None,
    prompt_pack: str | None,
) -> dict[str, Any] | None:
    if not prompt_pack:
        return writing_profile_payload
    payload = dict(writing_profile_payload or {})
    market = payload.get("market")
    if not isinstance(market, dict):
        market = {}
    market = dict(market)
    market["prompt_pack_key"] = prompt_pack
    payload["market"] = market
    return payload


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


def _benchmark_progress_printer(stage: str, payload: dict[str, Any] | None = None) -> None:
    labels = {
        "benchmark_case_started": "开始 benchmark case",
        "benchmark_case_completed": "benchmark case 完成",
        "benchmark_suite_completed": "benchmark suite 完成",
    }
    typer.secho(
        f"[benchmark] {labels.get(stage, stage)}{_format_progress_details(payload)}",
        err=True,
        fg=typer.colors.GREEN,
    )


def _audit_finding_sort_key(code: str):
    """For LENGTH_* findings surface the largest deviation first so
    catastrophic outliers (e.g. a 229-char chapter) lead the top-10 preview
    instead of being buried behind smaller near-envelope misses.
    """

    import re

    length_code = code in {"LENGTH_UNDER", "LENGTH_OVER"}

    def key(finding):
        if length_code:
            nums = re.findall(r"\d+", finding.detail)
            if len(nums) >= 2:
                actual, threshold = int(nums[0]), int(nums[1])
                return (-abs(actual - threshold), finding.chapter_no or 0)
        return (0, finding.chapter_no or 0)

    return key


@app.command("audit")
def audit(
    slug: str = typer.Argument(..., help="Project slug to audit."),
    dry_run: bool = typer.Option(
        True,
        "--dry-run/--auto-repair",
        help="Dry-run prints findings; --auto-repair attempts repair (Phase 1: GapRepairer reports only).",
    ),
    output_format: str = typer.Option(
        "text", "--format", help="Output format: text | json."
    ),
    profile: str = typer.Option(
        "full",
        "--profile",
        help=(
            "Validator profile: 'full' runs L4+L5 (language/length/naming/entity/"
            "dialog/POV/cliffhanger). 'phase1' runs only the narrow L4 subset "
            "(language + length) — useful when you want near-zero false positives."
        ),
    ),
) -> None:
    """Run L7 continuous audit against an existing project.

    Defaults to the full L4 + L5 retrospective sweep so pre-gate
    productions surface CJK leaks, length outliers, rogue names, entity
    overload, dialog breaks, and POV drift all in one pass. Use
    ``--profile phase1`` to fall back to the legacy CJK+length-only
    profile (higher precision, lower recall).

    ``--auto-repair`` is non-destructive: regenerating flagged chapters
    happens through the full pipeline CLI.
    """

    async def _run() -> None:
        settings = load_settings()
        async with session_scope(settings) as session:
            project = await get_project_by_slug(session, slug)
            if project is None:
                typer.secho(f"Project '{slug}' was not found.", err=True, fg=typer.colors.RED)
                raise typer.Exit(code=1)

            if profile == "phase1":
                audit = build_phase1_audit()
            elif profile == "full":
                audit = build_full_audit()
            else:
                typer.secho(
                    f"Unknown --profile '{profile}'. Use 'full' or 'phase1'.",
                    err=True,
                    fg=typer.colors.RED,
                )
                raise typer.Exit(code=2)
            report = await audit.scan(session, project.id)
            # Persist for L8 scorecard / dashboard. Failures here are
            # telemetry-only — don't block the CLI output.
            try:
                await persist_audit_findings(session, report)
            except Exception:
                pass

            if output_format == "json":
                payload = {
                    "project_slug": slug,
                    "project_id": str(project.id),
                    "findings": [
                        {
                            "auditor": f.auditor,
                            "code": f.code,
                            "severity": f.severity,
                            "chapter_no": f.chapter_no,
                            "detail": f.detail,
                            "auto_repairable": f.auto_repairable,
                        }
                        for f in report.findings
                    ],
                }
                typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
            else:
                typer.secho(
                    f"[audit] {slug}: {len(report.findings)} finding(s)",
                    fg=typer.colors.CYAN,
                )
                by_code = report.by_code()
                for code, findings in sorted(by_code.items()):
                    typer.secho(
                        f"  - {code} ({findings[0].severity}): {len(findings)} case(s)",
                        fg=typer.colors.YELLOW if findings[0].severity == "critical" else None,
                    )
                    ordered = sorted(findings, key=_audit_finding_sort_key(code))
                    for finding in ordered[:10]:
                        chap = f"ch-{finding.chapter_no}" if finding.chapter_no is not None else "—"
                        typer.echo(f"      {chap}  {finding.detail}")
                    if len(findings) > 10:
                        typer.echo(f"      ... {len(findings) - 10} more")

            if not dry_run:
                typer.secho(
                    "[audit] --auto-repair: Phase 1 repair is not self-contained. "
                    "Use `bestseller project autowrite` or `bestseller chapter write` "
                    "to regenerate flagged chapters through the full pipeline.",
                    fg=typer.colors.YELLOW,
                )

    asyncio.run(_run())


@app.command("scorecard")
def scorecard(
    slug: str = typer.Argument(..., help="Project slug to score."),
    output_format: str = typer.Option(
        "text", "--format", help="Output format: text | json | markdown."
    ),
    save: bool = typer.Option(
        True,
        "--save/--no-save",
        help="Persist the snapshot to novel_scorecards (default: save).",
    ),
) -> None:
    """Compute (and persist) the L8 NovelScorecard for an existing project.

    Pulls evidence from chapter lengths, quality reports, audit findings,
    and the diversity budget into a single 0-100 score + metric snapshot.
    Pipelines auto-run this as Stage 11; this CLI is for ad-hoc inspection.
    """

    async def _run() -> None:
        settings = load_settings()
        async with session_scope(settings) as session:
            project = await get_project_by_slug(session, slug)
            if project is None:
                typer.secho(
                    f"Project '{slug}' was not found.", err=True, fg=typer.colors.RED
                )
                raise typer.Exit(code=1)

            card = await compute_scorecard(
                session,
                project.id,
                expected_chapter_count=project.target_chapters,
            )
            if save:
                await save_scorecard(session, card)

            snapshot = card.to_dict()
            if output_format == "json":
                typer.echo(json.dumps(snapshot, ensure_ascii=False, indent=2))
            elif output_format == "markdown":
                typer.echo(f"# Scorecard — {slug}")
                typer.echo("")
                typer.echo(f"**Quality Score**: {snapshot['quality_score']} / 100")
                typer.echo("")
                typer.echo("| Metric | Value |")
                typer.echo("|---|---|")
                for key, value in snapshot.items():
                    if key in {"project_id", "quality_score", "top_overused_words"}:
                        continue
                    typer.echo(f"| {key} | {value} |")
                if snapshot["top_overused_words"]:
                    typer.echo("")
                    typer.echo("**Top overused words**:")
                    for word, count in snapshot["top_overused_words"]:
                        typer.echo(f"- `{word}` ×{count}")
            else:
                typer.secho(
                    f"[scorecard] {slug}: quality_score = "
                    f"{snapshot['quality_score']} / 100",
                    fg=typer.colors.CYAN,
                )
                typer.echo(
                    f"  chapters: total={snapshot['total_chapters']} "
                    f"missing={snapshot['missing_chapters']} "
                    f"blocked={snapshot['chapters_blocked']}"
                )
                typer.echo(
                    f"  length: mean={snapshot['length_mean']:.0f} "
                    f"stddev={snapshot['length_stddev']:.0f} "
                    f"cv={snapshot['length_cv']:.3f}"
                )
                typer.echo(
                    f"  defects: cjk={snapshot['cjk_leak_chapters']} "
                    f"dialog={snapshot['dialog_integrity_violations']} "
                    f"pov_drift={snapshot['pov_drift_chapters']}"
                )
                typer.echo(
                    f"  diversity: opening_H={snapshot['opening_archetype_entropy']:.2f} "
                    f"cliffhanger_H={snapshot['cliffhanger_entropy']:.2f} "
                    f"vocab_HHI={snapshot['vocab_hhi']:.3f}"
                )
                if snapshot["top_overused_words"]:
                    typer.echo("  top overused:")
                    for word, count in snapshot["top_overused_words"][:5]:
                        typer.echo(f"    {word} ×{count}")

    asyncio.run(_run())


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


@prompt_pack_app.command("list")
def prompt_pack_list() -> None:
    """List available prompt packs."""
    packs = list_prompt_packs()
    typer.echo(
        json.dumps(
            [
                {
                    "key": pack.key,
                    "name": pack.name,
                    "version": pack.version,
                    "description": pack.description,
                    "genres": pack.genres,
                    "tags": pack.tags,
                }
                for pack in packs
            ],
            ensure_ascii=False,
            indent=2,
        )
    )


@prompt_pack_app.command("show")
def prompt_pack_show(key: str) -> None:
    """Show one prompt pack in detail."""
    pack = get_prompt_pack(key)
    if pack is None:
        raise typer.BadParameter(f"Prompt pack '{key}' was not found.")
    typer.echo(json.dumps(pack.model_dump(mode="json"), ensure_ascii=False, indent=2))


@writing_preset_app.command("list")
def writing_preset_list() -> None:
    """List built-in writing presets for platform, genre, and length."""
    catalog = load_writing_preset_catalog()
    typer.echo(
        json.dumps(
            {
                "chapter_word_policy": catalog.chapter_word_policy.model_dump(mode="json"),
                "platform_presets": [
                    {
                        "key": preset.key,
                        "name": preset.name,
                        "recommended_genres": preset.recommended_genres,
                        "recommended_audiences": preset.recommended_audiences,
                    }
                    for preset in list_platform_presets()
                ],
                "genre_presets": [
                    {
                        "key": preset.key,
                        "name": preset.name,
                        "genre": preset.genre,
                        "sub_genre": preset.sub_genre,
                        "recommended_platforms": preset.recommended_platforms,
                        "target_word_options": preset.target_word_options,
                        "target_chapter_options": preset.target_chapter_options,
                    }
                    for preset in list_genre_presets()
                ],
                "length_presets": [
                    preset.model_dump(mode="json")
                    for preset in list_length_presets()
                ],
                "source_notes": catalog.source_notes,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


@writing_preset_app.command("show")
def writing_preset_show(
    key: str,
    kind: str = typer.Option(
        "genre",
        "--kind",
        help="platform, genre, or length",
    ),
) -> None:
    """Show one built-in writing preset in detail."""
    normalized = kind.strip().lower()
    if normalized == "platform":
        preset = next((item for item in list_platform_presets() if item.key == key), None)
    elif normalized == "length":
        preset = next((item for item in list_length_presets() if item.key == key), None)
    else:
        preset = get_genre_preset(key)
    if preset is None:
        raise typer.BadParameter(f"Writing preset '{key}' was not found in {normalized}.")
    typer.echo(json.dumps(preset.model_dump(mode="json"), ensure_ascii=False, indent=2))


@writing_preset_app.command("hot")
def writing_preset_hot(
    limit: int = typer.Option(8, "--limit", min=1, max=50),
) -> None:
    """Show currently recommended hot genre presets."""
    typer.echo(
        json.dumps(
            [
                {
                    "key": preset.key,
                    "name": preset.name,
                    "genre": preset.genre,
                    "sub_genre": preset.sub_genre,
                    "trend_score": preset.trend_score,
                    "trend_window": preset.trend_window,
                    "trend_summary": preset.trend_summary,
                    "trend_keywords": preset.trend_keywords,
                    "recommended_platforms": preset.recommended_platforms,
                }
                for preset in list_hot_genre_presets(limit=limit)
            ],
            ensure_ascii=False,
            indent=2,
        )
    )


@ui_app.command("serve")
def ui_serve(
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(8787, "--port", min=1, max=65535),
    open_browser: bool = typer.Option(
        False,
        "--open-browser/--no-open-browser",
        help="Open the local Novel Studio page in the default browser after startup.",
    ),
) -> None:
    """Run the local HTML Novel Studio for interactive project generation and preview."""
    serve_web_app(host=host, port=port, open_browser=open_browser)


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
    profile_file: Path | None = typer.Option(
        None,
        "--profile-file",
        exists=True,
        file_okay=True,
        dir_okay=False,
        help="Optional JSON/YAML writing profile file.",
    ),
    prompt_pack: str | None = typer.Option(
        None,
        "--prompt-pack",
        help="Optional prompt pack key. Use `bestseller prompt-pack list` to inspect available packs.",
    ),
) -> None:
    """Create a project and its default style guide."""

    validate_longform_scope(target_words, target_chapters, language=language)

    writing_profile_payload = _apply_prompt_pack_to_profile_payload(
        _load_structured_payload_file(profile_file),
        prompt_pack,
    )

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
                    writing_profile=writing_profile_payload,
                ),
                settings,
            )
            typer.echo(
                json.dumps(
                    {
                        "id": str(project.id),
                        "slug": project.slug,
                        "writing_profile_configured": bool(writing_profile_payload),
                        "prompt_pack": (
                            (writing_profile_payload or {}).get("market", {}).get("prompt_pack_key")
                            if isinstance((writing_profile_payload or {}).get("market"), dict)
                            else None
                        ),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )

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


@project_app.command("health")
def project_health(project_slug: str) -> None:
    """Show the project health snapshot: truth staleness, overdue clues, hook reuse, and payoff debt."""

    async def _run() -> None:
        settings = load_settings()
        async with session_scope(settings) as session:
            report = await build_project_health_report(session, settings, project_slug)
            typer.echo(json.dumps(report, ensure_ascii=False, indent=2))

    asyncio.run(_run())


@project_app.command("health-repair")
def project_health_repair(
    project_slug: str,
    requested_by: str = "system",
    dry_run: bool = typer.Option(
        True,
        "--dry-run/--apply",
        help="Preview actions by default; --apply writes safe materialization repairs.",
    ),
    materialize_truth: bool = typer.Option(
        True,
        "--materialize-truth/--no-materialize-truth",
        help="Re-materialize stale story bible, outline, and narrative graph components.",
    ),
) -> None:
    """Plan or apply safe project-health repairs."""

    async def _run() -> None:
        settings = load_settings()
        async with session_scope(settings) as session:
            report = await repair_project_health(
                session,
                settings,
                project_slug,
                requested_by=requested_by,
                dry_run=dry_run,
                materialize_truth=materialize_truth,
            )
            typer.echo(json.dumps(report, ensure_ascii=False, indent=2))

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
    profile_file: Path | None = typer.Option(
        None,
        "--profile-file",
        exists=True,
        file_okay=True,
        dir_okay=False,
        help="Optional JSON/YAML writing profile file.",
    ),
    prompt_pack: str | None = typer.Option(
        None,
        "--prompt-pack",
        help="Optional prompt pack key. Use `bestseller prompt-pack list` to inspect available packs.",
    ),
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

    validate_longform_scope(target_words, target_chapters, language=language)

    writing_profile_payload = _apply_prompt_pack_to_profile_payload(
        _load_structured_payload_file(profile_file),
        prompt_pack,
    )

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
                    metadata={"premise": premise},
                    writing_profile=writing_profile_payload,
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


@benchmark_app.command("list")
def benchmark_list() -> None:
    """List bundled benchmark suites."""
    payload = [item.model_dump(mode="json") for item in list_benchmark_suites()]
    typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))


@benchmark_app.command("run")
def benchmark_run(
    suite_id: str = typer.Argument("sample-books"),
    suite_file: Path | None = typer.Option(None, "--suite-file", exists=True, file_okay=True, dir_okay=False),
    case: list[str] | None = typer.Option(None, "--case", help="Only run selected benchmark case ids."),
    slug_prefix: str = typer.Option("benchmark", "--slug-prefix"),
    show_progress: bool = typer.Option(True, "--progress/--no-progress"),
) -> None:
    """Run one benchmark suite and emit a structured JSON report."""

    async def _run() -> None:
        settings = load_settings()
        suite = load_benchmark_suite(suite_id, suite_file=suite_file)
        async with session_scope(settings) as session:
            result = await run_benchmark_suite(
                session,
                settings,
                suite=suite,
                case_ids=list(case or []),
                slug_prefix=slug_prefix,
                progress=_benchmark_progress_printer if show_progress else None,
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
    target_words: int = 5500,
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
            draft = await assemble_chapter_draft(session, project_slug, chapter_number, settings=settings)
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


@publish_profile_app.command("init")
def publish_profile_init(
    project_slug: str,
    target: str = typer.Option("amazon-kdp", "--target", help="Publication target to initialize."),
    overwrite: bool = typer.Option(False, "--overwrite", help="Overwrite an existing stored profile."),
) -> None:
    """Initialize a publication profile from current project metadata."""

    if target != "amazon-kdp":
        raise typer.BadParameter("Only --target amazon-kdp is currently supported.")

    async def _run() -> None:
        settings = load_settings()
        async with session_scope(settings) as session:
            profile = await init_amazon_kdp_profile(session, project_slug, overwrite=overwrite)
            typer.echo(json.dumps(profile.model_dump(mode="json", exclude_none=True), ensure_ascii=False, indent=2))

    asyncio.run(_run())


@publish_profile_app.command("show")
def publish_profile_show(
    project_slug: str,
    target: str = typer.Option("amazon-kdp", "--target", help="Publication target to inspect."),
) -> None:
    """Show the stored publication profile."""

    if target != "amazon-kdp":
        raise typer.BadParameter("Only --target amazon-kdp is currently supported.")

    async def _run() -> None:
        settings = load_settings()
        async with session_scope(settings) as session:
            profile = await show_amazon_kdp_profile(session, project_slug)
            typer.echo(json.dumps(profile.model_dump(mode="json", exclude_none=True), ensure_ascii=False, indent=2))

    asyncio.run(_run())


@export_amazon_kdp_app.command("validate")
def export_amazon_kdp_validate(
    project_slug: str,
) -> None:
    """Validate whether the current project is ready for Amazon KDP ebook packaging."""

    async def _run() -> None:
        settings = load_settings()
        async with session_scope(settings) as session:
            report = await validate_amazon_kdp_project(session, project_slug)
            typer.echo(json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2))

    asyncio.run(_run())


@export_amazon_kdp_app.command("package")
def export_amazon_kdp_package(
    project_slug: str,
    strict: bool = typer.Option(
        True,
        "--strict/--no-strict",
        help="Block package generation when validation has blocking findings.",
    ),
) -> None:
    """Build an upload-ready Amazon KDP ebook package."""

    async def _run() -> None:
        settings = load_settings()
        async with session_scope(settings) as session:
            result = await package_amazon_kdp_project(session, settings, project_slug, strict=strict)
            typer.echo(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2))

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# IF (Interactive Fiction) commands
# ---------------------------------------------------------------------------

@if_app.command("run")
def if_run(
    project_slug: str = typer.Argument(..., help="Project slug to generate IF for."),
    resume: bool = typer.Option(False, "--resume", help="Resume from last checkpoint."),
    genre: str = typer.Option("修仙升级", "--genre", help="IF genre (修仙升级|都市逆袭|悬疑生存|职场商战|末日爽文)."),
    chapters: int = typer.Option(100, "--chapters", min=10, max=2000, help="Target chapter count."),
    free_chapters: int = typer.Option(20, "--free-chapters", min=5, max=100, help="Free (non-paid) chapter count."),
    premise: str = typer.Option("", "--premise", help="Story premise override."),
    protagonist: str = typer.Option("", "--protagonist", help="Protagonist description."),
    core_conflict: str = typer.Option("", "--core-conflict", help="Core conflict description."),
    tone: str = typer.Option("爽快、热血、有悬念", "--tone", help="Story tone."),
    text_length: str = typer.Option("", "--text-length", help="Total text chars per chapter, e.g. '2500-3500'. Defaults to config value."),
    node_length: str = typer.Option("", "--node-length", help="Chars per individual text node, e.g. '150-250'. Defaults to config value."),
    output_dir: str = typer.Option("./output", "--output-dir", help="Output base directory."),
    integrated: bool = typer.Option(True, "--integrated/--no-integrated", help="Use integrated pipeline with DB context injection and summarizer."),
) -> None:
    """Run the LifeScript interactive fiction generation pipeline for a project."""
    settings = load_settings()

    async def _get_project() -> Any:
        async with session_scope(settings) as session:
            return await get_project_by_slug(session, project_slug)

    project = asyncio.run(_get_project())
    if project is None:
        typer.echo(f"Project '{project_slug}' not found — creating it automatically.")
        from bestseller.domain.enums import ProjectType
        from bestseller.domain.project import WritingProfile
        from bestseller.services.projects import create_project
        auto_cfg = InteractiveFictionConfig.model_validate({
            "enabled": True, "if_genre": genre, "target_chapters": chapters,
            "free_chapters": free_chapters, "tone": tone,
            **({"premise": premise} if premise else {}),
            **({"protagonist": protagonist} if protagonist else {}),
            **({"core_conflict": core_conflict} if core_conflict else {}),
        })
        async def _create() -> Any:
            async with session_scope(settings) as session:
                p = await create_project(session, ProjectCreate(
                    slug=project_slug, title=project_slug.replace("-", " ").title(),
                    genre=genre, target_word_count=chapters * 3000,
                    target_chapters=chapters, project_type=ProjectType.INTERACTIVE,
                    writing_profile=WritingProfile(interactive_fiction=auto_cfg),
                ), settings)
                await session.commit()
                return p
        project = asyncio.run(_create())
        typer.echo(f"Created project '{project_slug}'.")

    # Merge stored IF config with CLI overrides
    stored_wp = project.metadata_json.get("writing_profile", {})
    stored_if = stored_wp.get("interactive_fiction", {})
    cfg_dict: dict[str, Any] = {**stored_if, "enabled": True, "if_genre": genre, "target_chapters": chapters, "free_chapters": free_chapters, "tone": tone}
    if premise:
        cfg_dict["premise"] = premise
    if protagonist:
        cfg_dict["protagonist"] = protagonist
    if core_conflict:
        cfg_dict["core_conflict"] = core_conflict
    if text_length:
        cfg_dict["chapter_text_length"] = text_length
    if node_length:
        cfg_dict["text_node_length"] = node_length
    cfg = InteractiveFictionConfig.model_validate(cfg_dict)

    def on_progress(phase: str, payload: dict[str, Any]) -> None:
        status = payload.get("status", "")
        extra = ""
        if "arc" in payload:
            extra = f" arc {payload['arc']}/{payload.get('total', '?')}"
        elif "chapter" in payload:
            extra = f" ch {payload['chapter']}/{payload.get('total', '?')}"
        typer.echo(f"[{phase}] {status}{extra}")

    try:
        if integrated:
            typer.echo("[integrated] Using DB-backed pipeline with context injection + summarizer.")
            out_path = asyncio.run(run_if_pipeline_integrated(
                project,
                cfg,
                Path(output_dir),
                settings=settings,
                resume=resume,
                on_progress=on_progress,
            ))
        else:
            out_path = run_if_pipeline(
                project,
                cfg,
                Path(output_dir),
                settings=settings,
                resume=resume,
                on_progress=on_progress,
            )
        typer.secho(f"\nDone! story_package.json → {out_path}", fg=typer.colors.GREEN)
    except Exception as exc:
        typer.secho(f"Pipeline failed: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc


@if_app.command("status")
def if_status(
    project_slug: str = typer.Argument(..., help="Project slug."),
    output_dir: str = typer.Option("./output", "--output-dir"),
) -> None:
    """Show IF generation progress / checkpoint state for a project."""
    import json as _json
    if_dir = Path(output_dir) / project_slug / "if"
    progress_path = if_dir / "if_progress.json"
    if not progress_path.exists():
        typer.echo("No progress file found. Run 'if run' first.")
        return
    state = _json.loads(progress_path.read_text(encoding="utf-8"))
    chapters_done = len(state.get("chapters", []))
    typer.echo(f"Project       : {project_slug}")
    typer.echo(f"Story Bible   : {'✓' if 'bible' in state else '—'}")
    typer.echo(f"Arc Plans     : {'✓' if 'arc_plans' in state else '—'}")
    typer.echo(f"Chapters done : {chapters_done}")
    typer.echo(f"Walkthrough   : {'✓' if 'walkthrough' in state else '—'}")
    build_dir = if_dir / "build"
    if build_dir.exists():
        files = sorted(f.name for f in build_dir.iterdir())
        typer.echo(f"Compiled files: {', '.join(files)}")
