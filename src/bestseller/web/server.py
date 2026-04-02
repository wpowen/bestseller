from __future__ import annotations

import asyncio
import json
import mimetypes
import socketserver
import threading
import traceback
import webbrowser
from dataclasses import dataclass, field
from datetime import UTC, datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse
from uuid import UUID, uuid4

from bestseller.domain.project import InteractiveFictionConfig, ProjectCreate
from bestseller.infra.db.models import StyleGuideModel
from bestseller.infra.db.session import session_scope
from bestseller.services.exports import build_markdown_reading_stats, markdown_to_html
from bestseller.services.if_generation import run_if_pipeline, run_if_pipeline_integrated
from bestseller.services.inspection import (
    build_project_structure,
    build_project_workflow_overview,
    build_story_bible_overview,
)
from bestseller.services.narrative import build_narrative_overview
from bestseller.services.pipelines import run_autowrite_pipeline
from bestseller.services.projects import get_project_by_slug, list_projects
from bestseller.services.repair import run_project_repair
from bestseller.services.writing_profile import get_project_writing_profile
from bestseller.services.writing_presets import load_writing_preset_catalog, validate_longform_scope
from bestseller.settings import AppSettings, load_settings


_UI_HTML_PATH = Path(__file__).with_name("novel_studio.html")
_READER_HTML_PATH = Path(__file__).with_name("novel_reader.html")
_IF_READER_HTML_PATH = Path(__file__).with_name("novel_if_reader.html")
_ARTIFACT_CACHE: dict[str, tuple[int, int, dict[str, object]]] = {}


class _FastThreadingHTTPServer(ThreadingHTTPServer):
    def server_bind(self) -> None:
        socketserver.TCPServer.server_bind(self)
        host, port = self.socket.getsockname()[:2]
        self.server_name = str(host)
        self.server_port = int(port)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _json_default(value: object) -> object:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _project_output_dir(settings: AppSettings, project_slug: str) -> Path:
    return (Path(settings.output.base_dir) / project_slug).resolve()


def collect_project_artifact_entries(
    settings: AppSettings,
    project_slug: str,
) -> list[dict[str, object]]:
    output_dir = _project_output_dir(settings, project_slug)
    if not output_dir.exists() or not output_dir.is_dir():
        return []
    entries: list[dict[str, object]] = []
    for path in sorted(output_dir.iterdir(), key=lambda item: item.name):
        if not path.is_file():
            continue
        stat = path.stat()
        entry: dict[str, object] = {
            "name": path.name,
            "path": str(path.resolve()),
            "size_bytes": stat.st_size,
            "suffix": path.suffix.lower(),
            "modified_at": datetime.fromtimestamp(stat.st_mtime, UTC).isoformat(),
            "is_previewable": path.suffix.lower() == ".md",
        }
        if path.suffix.lower() == ".md":
            entry.update(_read_markdown_artifact_metadata(path))
        entries.append(entry)
    return entries


def resolve_project_artifact_path(
    settings: AppSettings,
    project_slug: str,
    artifact_name: str,
) -> Path:
    safe_name = Path(artifact_name).name
    artifact_path = (_project_output_dir(settings, project_slug) / safe_name).resolve()
    output_dir = _project_output_dir(settings, project_slug)
    if output_dir not in artifact_path.parents:
        raise ValueError("Artifact path escapes the project output directory.")
    if not artifact_path.exists() or not artifact_path.is_file():
        raise FileNotFoundError(f"Artifact '{safe_name}' was not found for '{project_slug}'.")
    return artifact_path


def _render_preview_html(project_slug: str, artifact_name: str, content_md: str) -> str:
    preview = build_preview_payload(project_slug, artifact_name, content_md)
    body = str(preview["html"])
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>{project_slug} / {artifact_name}</title>
  <style>
    body {{
      margin: 0;
      font-family: "PingFang SC", "Noto Sans SC", sans-serif;
      background: #f5f1e9;
      color: #1d1b18;
    }}
    main {{
      max-width: 920px;
      margin: 0 auto;
      padding: 32px 20px 72px;
    }}
    .meta {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
      gap: 12px;
      margin-bottom: 18px;
    }}
    .meta-card {{
      border-radius: 16px;
      border: 1px solid #d8d0c3;
      background: rgba(255, 253, 248, 0.94);
      padding: 14px 16px;
      box-shadow: 0 12px 32px rgba(43, 32, 20, 0.06);
    }}
    .meta-card strong {{
      display: block;
      font-size: 12px;
      color: #7a6a58;
      margin-bottom: 6px;
      letter-spacing: 0.04em;
    }}
    .meta-card span {{
      display: block;
      font-size: 22px;
      font-weight: 700;
      color: #241b13;
    }}
    .meta-card small {{
      display: block;
      margin-top: 6px;
      color: #6f655b;
      line-height: 1.7;
    }}
    article {{
      background: #fffdf8;
      border: 1px solid #d8d0c3;
      border-radius: 20px;
      padding: 36px 36px 44px;
      box-shadow: 0 12px 32px rgba(43, 32, 20, 0.08);
    }}
    article h1, article h2, article h3 {{
      line-height: 1.25;
    }}
    article h1 {{
      font-size: 34px;
      margin: 0 0 22px;
    }}
    article h2 {{
      margin-top: 40px;
      font-size: 24px;
    }}
    article h3 {{
      margin-top: 28px;
      font-size: 20px;
    }}
    article p, article li {{
      line-height: 1.98;
      font-size: 18px;
      color: #251d15;
    }}
    article p {{
      margin: 0 0 1.05em;
      text-indent: 2em;
    }}
    article p:first-of-type,
    article h1 + p,
    article h2 + p,
    article h3 + p,
    article blockquote p,
    article li p {{
      text-indent: 0;
    }}
    article blockquote {{
      border-left: 4px solid #9b4a2e;
      padding-left: 14px;
      margin-left: 0;
      color: #5d564d;
      background: rgba(155, 74, 46, 0.05);
      border-radius: 0 14px 14px 0;
      padding-top: 8px;
      padding-bottom: 8px;
    }}
    article hr {{
      border: 0;
      border-top: 1px solid #e5dbcb;
      margin: 32px 0;
    }}
    article ul,
    article ol {{
      padding-left: 1.5em;
      margin: 0 0 1em;
    }}
  </style>
</head>
<body>
  <main>
    <section class="meta">
      <div class="meta-card">
        <strong>当前正文</strong>
        <span>{artifact_name}</span>
        <small>{project_slug}</small>
      </div>
      <div class="meta-card">
        <strong>正文总字数</strong>
        <span>{preview["word_count"]}</span>
        <small>中文按阅读字数统计，已排除空白字符。</small>
      </div>
      <div class="meta-card">
        <strong>预计阅读</strong>
        <span>{preview["estimated_read_minutes"]} 分钟</span>
        <small>按 500 字 / 分钟估算。</small>
      </div>
      <div class="meta-card">
        <strong>段落数量</strong>
        <span>{preview["paragraph_count"]}</span>
        <small>可用于判断正文密度和断章节奏。</small>
      </div>
    </section>
    <article>{body}</article>
  </main>
</body>
</html>"""


def _read_markdown_artifact_metadata(path: Path) -> dict[str, object]:
    stat = path.stat()
    cache_key = str(path.resolve())
    cached = _ARTIFACT_CACHE.get(cache_key)
    if cached is not None and cached[0] == stat.st_mtime_ns and cached[1] == stat.st_size:
        return dict(cached[2])
    content_md = path.read_text(encoding="utf-8")
    metadata = build_markdown_reading_stats(content_md)
    _ARTIFACT_CACHE[cache_key] = (stat.st_mtime_ns, stat.st_size, dict(metadata))
    return metadata


def build_preview_payload(
    project_slug: str,
    artifact_name: str,
    content_md: str,
) -> dict[str, object]:
    stats = build_markdown_reading_stats(content_md)
    return {
        "project_slug": project_slug,
        "artifact_name": artifact_name,
        "word_count": stats["word_count"],
        "character_count": stats["character_count"],
        "paragraph_count": stats["paragraph_count"],
        "estimated_read_minutes": stats["estimated_read_minutes"],
        "html": markdown_to_html(content_md),
    }


@dataclass
class WebTaskState:
    task_id: str
    task_type: str
    status: str
    created_at: str
    updated_at: str
    project_slug: str | None = None
    title: str | None = None
    current_stage: str | None = None
    progress_events: list[dict[str, object]] = field(default_factory=list)
    result: dict[str, object] | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "task_id": self.task_id,
            "task_type": self.task_type,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "project_slug": self.project_slug,
            "title": self.title,
            "current_stage": self.current_stage,
            "progress_events": list(self.progress_events),
            "result": self.result,
            "error": self.error,
        }


class WebTaskManager:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._tasks: dict[str, WebTaskState] = {}

    def list_tasks(self) -> list[dict[str, object]]:
        with self._lock:
            tasks = sorted(
                self._tasks.values(),
                key=lambda item: item.created_at,
                reverse=True,
            )
            return [task.to_dict() for task in tasks]

    def get_task(self, task_id: str) -> dict[str, object] | None:
        with self._lock:
            task = self._tasks.get(task_id)
            return task.to_dict() if task is not None else None

    def create_autowrite_task(self, payload: dict[str, object]) -> dict[str, object]:
        task_id = str(uuid4())
        task = WebTaskState(
            task_id=task_id,
            task_type="autowrite",
            status="queued",
            created_at=_utc_now(),
            updated_at=_utc_now(),
            project_slug=str(payload.get("slug") or ""),
            title=str(payload.get("title") or ""),
            current_stage="queued",
        )
        with self._lock:
            self._tasks[task_id] = task
        thread = threading.Thread(
            target=self._run_autowrite_worker,
            args=(task_id, payload),
            daemon=True,
        )
        thread.start()
        return task.to_dict()

    def create_repair_task(self, payload: dict[str, object]) -> dict[str, object]:
        task_id = str(uuid4())
        task = WebTaskState(
            task_id=task_id,
            task_type="repair",
            status="queued",
            created_at=_utc_now(),
            updated_at=_utc_now(),
            project_slug=str(payload.get("project_slug") or ""),
            title=f"Repair {payload.get('project_slug') or ''}",
            current_stage="queued",
        )
        with self._lock:
            self._tasks[task_id] = task
        thread = threading.Thread(
            target=self._run_repair_worker,
            args=(task_id, payload),
            daemon=True,
        )
        thread.start()
        return task.to_dict()

    def _push_progress(
        self,
        task_id: str,
        stage: str,
        payload: dict[str, object] | None = None,
    ) -> None:
        with self._lock:
            task = self._tasks[task_id]
            task.updated_at = _utc_now()
            task.current_stage = stage
            task.progress_events.append(
                {
                    "timestamp": task.updated_at,
                    "stage": stage,
                    "payload": payload or {},
                }
            )
            task.progress_events = task.progress_events[-300:]

    def _mark_running(self, task_id: str) -> None:
        with self._lock:
            task = self._tasks[task_id]
            task.status = "running"
            task.updated_at = _utc_now()
            task.current_stage = "running"

    def _mark_completed(self, task_id: str, result: dict[str, object]) -> None:
        with self._lock:
            task = self._tasks[task_id]
            task.status = "completed"
            task.updated_at = _utc_now()
            task.current_stage = "completed"
            task.result = result

    def _mark_failed(self, task_id: str, error: str) -> None:
        with self._lock:
            task = self._tasks[task_id]
            task.status = "failed"
            task.updated_at = _utc_now()
            task.current_stage = "failed"
            task.error = error

    def _run_autowrite_worker(self, task_id: str, payload: dict[str, object]) -> None:
        self._mark_running(task_id)

        def progress(stage: str, progress_payload: dict[str, Any] | None = None) -> None:
            serialized = json.loads(json.dumps(progress_payload or {}, default=_json_default))
            self._push_progress(task_id, stage, serialized)

        async def runner() -> dict[str, object]:
            settings = load_settings()
            async with session_scope(settings) as session:
                result = await run_autowrite_pipeline(
                    session,
                    settings,
                    project_payload=ProjectCreate(
                        slug=str(payload["slug"]),
                        title=str(payload["title"]),
                        genre=str(payload["genre"]),
                        sub_genre=(str(payload["sub_genre"]) if payload.get("sub_genre") else None),
                        audience=(str(payload["audience"]) if payload.get("audience") else None),
                        language=str(payload.get("language") or "zh-CN"),
                        target_word_count=int(payload["target_words"]),
                        target_chapters=int(payload["target_chapters"]),
                        metadata={"premise": str(payload["premise"])},
                        writing_profile=payload.get("writing_profile"),
                    ),
                    premise=str(payload["premise"]),
                    requested_by="web-ui",
                    export_markdown=bool(payload.get("export_markdown", True)),
                    auto_repair_on_attention=bool(payload.get("auto_repair", True)),
                    progress=progress,
                )
            return json.loads(json.dumps(result.model_dump(mode="json"), default=_json_default))

        try:
            result = asyncio.run(runner())
            self._mark_completed(task_id, result)
        except Exception:
            self._mark_failed(task_id, traceback.format_exc())

    def create_if_task(self, payload: dict[str, object]) -> dict[str, object]:
        task_id = str(uuid4())
        task = WebTaskState(
            task_id=task_id,
            task_type="if_generation",
            status="queued",
            created_at=_utc_now(),
            updated_at=_utc_now(),
            project_slug=str(payload.get("project_slug") or ""),
            title=f"IF Generation: {payload.get('project_slug') or ''}",
            current_stage="queued",
        )
        with self._lock:
            self._tasks[task_id] = task
        thread = threading.Thread(
            target=self._run_if_worker,
            args=(task_id, payload),
            daemon=True,
        )
        thread.start()
        return task.to_dict()

    def _run_if_worker(self, task_id: str, payload: dict[str, object]) -> None:
        self._mark_running(task_id)

        def progress(stage: str, progress_payload: dict[str, Any] | None = None) -> None:
            serialized = json.loads(json.dumps(progress_payload or {}, default=_json_default))
            self._push_progress(task_id, stage, serialized)

        try:
            settings = load_settings()
            project_slug = str(payload["project_slug"])
            resume = bool(payload.get("resume", False))

            raw_cfg = payload.get("if_config") or {}
            cfg_dict = raw_cfg if isinstance(raw_cfg, dict) else {}

            async def get_or_create_project() -> Any:
                from bestseller.domain.enums import ProjectType
                from bestseller.domain.project import WritingProfile
                from bestseller.services.projects import create_project
                async with session_scope(settings) as session:
                    existing = await get_project_by_slug(session, project_slug)
                    if existing is not None:
                        return existing
                    # Auto-create a minimal IF project from the payload
                    if_cfg_for_profile = InteractiveFictionConfig.model_validate({**cfg_dict, "enabled": True})
                    title = str(payload.get("title") or project_slug)
                    genre = cfg_dict.get("if_genre") or "修仙升级"
                    wp_override = WritingProfile(interactive_fiction=if_cfg_for_profile)
                    new_project = await create_project(
                        session,
                        ProjectCreate(
                            slug=project_slug,
                            title=title,
                            genre=str(genre),
                            target_word_count=int(cfg_dict.get("target_chapters", 100)) * 5000,
                            target_chapters=int(cfg_dict.get("target_chapters", 100)),
                            project_type=ProjectType.INTERACTIVE,
                            writing_profile=wp_override,
                        ),
                        settings,
                    )
                    await session.commit()
                    return new_project

            project = asyncio.run(get_or_create_project())

            # Merge stored writing profile IF config with request overrides
            stored = project.metadata_json.get("writing_profile", {})
            stored_if = stored.get("interactive_fiction", {})
            merged_cfg = {**stored_if, **cfg_dict, "enabled": True}
            cfg = InteractiveFictionConfig.model_validate(merged_cfg)

            output_base = Path(settings.output.base_dir)
            out_path = asyncio.run(run_if_pipeline_integrated(
                project,
                cfg,
                output_base,
                settings=settings,
                resume=resume,
                on_progress=progress,
            ))
            self._mark_completed(task_id, {"story_package": str(out_path)})
        except Exception:
            self._mark_failed(task_id, traceback.format_exc())

    def _run_repair_worker(self, task_id: str, payload: dict[str, object]) -> None:
        self._mark_running(task_id)

        def progress(stage: str, progress_payload: dict[str, Any] | None = None) -> None:
            serialized = json.loads(json.dumps(progress_payload or {}, default=_json_default))
            self._push_progress(task_id, stage, serialized)

        async def runner() -> dict[str, object]:
            settings = load_settings()
            project_slug = str(payload["project_slug"])
            async with session_scope(settings) as session:
                result = await run_project_repair(
                    session,
                    settings,
                    project_slug,
                    requested_by="web-ui",
                    refresh_impacts=bool(payload.get("refresh_impacts", True)),
                    export_markdown=bool(payload.get("export_markdown", True)),
                    progress=progress,
                )
            return json.loads(json.dumps(result.model_dump(mode="json"), default=_json_default))

        try:
            result = asyncio.run(runner())
            self._mark_completed(task_id, result)
        except Exception:
            self._mark_failed(task_id, traceback.format_exc())


async def _load_projects_payload(settings: AppSettings) -> list[dict[str, object]]:
    async with session_scope(settings) as session:
        rows = await list_projects(session)
        return [
            {
                "id": str(row.id),
                "slug": row.slug,
                "title": row.title,
                "genre": row.genre,
                "status": row.status,
                "target_word_count": row.target_word_count,
                "target_chapters": row.target_chapters,
            }
            for row in rows
        ]


def _resolve_story_bible_progress(
    story_bible: Any,
    *,
    current_chapter_number: int,
) -> dict[str, object]:
    frontiers = list(getattr(story_bible, "volume_frontiers", []) or [])
    gates = list(getattr(story_bible, "expansion_gates", []) or [])
    current_frontier = None
    if frontiers:
        if current_chapter_number <= 0:
            current_frontier = frontiers[0]
        else:
            current_frontier = next(
                (
                    frontier
                    for frontier in frontiers
                    if frontier.start_chapter_number <= current_chapter_number
                    and (
                        frontier.end_chapter_number is None
                        or frontier.end_chapter_number >= current_chapter_number
                    )
                ),
                None,
            )
            if current_frontier is None:
                current_frontier = next(
                    (
                        frontier
                        for frontier in reversed(frontiers)
                        if frontier.start_chapter_number <= current_chapter_number
                    ),
                    frontiers[0],
                )
    next_gate = next(
        (
            gate
            for gate in sorted(
                gates,
                key=lambda item: (item.unlock_chapter_number, item.unlock_volume_number),
            )
            if gate.status != "unlocked"
        ),
        None,
    )
    unlocked_gate_count = sum(1 for gate in gates if gate.status == "unlocked")
    active_gate_count = sum(1 for gate in gates if gate.status == "active")
    return {
        "has_backbone": getattr(story_bible, "world_backbone", None) is not None,
        "current_frontier": (
            {
                "volume_number": current_frontier.volume_number,
                "title": current_frontier.title,
                "frontier_summary": current_frontier.frontier_summary,
                "expansion_focus": current_frontier.expansion_focus,
                "start_chapter_number": current_frontier.start_chapter_number,
                "end_chapter_number": current_frontier.end_chapter_number,
                "active_locations": list(current_frontier.active_locations),
                "active_factions": list(current_frontier.active_factions),
            }
            if current_frontier is not None
            else None
        ),
        "next_gate": (
            {
                "label": next_gate.label,
                "condition_summary": next_gate.condition_summary,
                "unlocks_summary": next_gate.unlocks_summary,
                "unlock_volume_number": next_gate.unlock_volume_number,
                "unlock_chapter_number": next_gate.unlock_chapter_number,
                "status": next_gate.status,
            }
            if next_gate is not None
            else None
        ),
        "unlocked_gate_count": unlocked_gate_count,
        "active_gate_count": active_gate_count,
    }


async def _load_project_summary_payload(
    settings: AppSettings,
    project_slug: str,
) -> dict[str, object]:
    async with session_scope(settings) as session:
        project = await get_project_by_slug(session, project_slug)
        if project is None:
            raise ValueError(f"Project '{project_slug}' was not found.")
        structure = await build_project_structure(session, project_slug)
        story_bible = await build_story_bible_overview(session, project_slug)
        narrative = await build_narrative_overview(session, project_slug)
        workflow = await build_project_workflow_overview(session, project_slug)
        style_guide = await session.get(StyleGuideModel, project.id)
        writing_profile = get_project_writing_profile(project, style_guide).model_dump(mode="json")
    outputs = collect_project_artifact_entries(settings, project_slug)
    markdown_entries = [item for item in outputs if str(item["suffix"]) == ".md"]
    default_preview_entry = (
        next((item for item in markdown_entries if item["name"] == "project.md"), None)
        or (markdown_entries[0] if markdown_entries else None)
    )
    project_markdown_entry = next((item for item in markdown_entries if item["name"] == "project.md"), None)
    chapter_markdown_entries = [
        item
        for item in markdown_entries
        if str(item["name"]).startswith("chapter-")
    ]
    world_expansion = _resolve_story_bible_progress(
        story_bible,
        current_chapter_number=int(project.current_chapter_number or 0),
    )
    return {
        "project": {
            "id": str(project.id),
            "slug": project.slug,
            "title": project.title,
            "genre": project.genre,
            "sub_genre": project.sub_genre,
            "audience": project.audience,
            "status": project.status,
            "target_word_count": project.target_word_count,
            "target_chapters": project.target_chapters,
            "current_volume_number": project.current_volume_number,
            "current_chapter_number": project.current_chapter_number,
        },
        "writing_profile": writing_profile,
        "structure_summary": {
            "total_chapters": structure.total_chapters,
            "total_scenes": structure.total_scenes,
            "volume_count": len(structure.volumes),
        },
        "story_bible_counts": {
            "has_world_backbone": bool(world_expansion["has_backbone"]),
            "world_rule_count": len(story_bible.world_rules),
            "location_count": len(story_bible.locations),
            "faction_count": len(story_bible.factions),
            "character_count": len(story_bible.characters),
            "relationship_count": len(story_bible.relationships),
            "volume_frontier_count": len(story_bible.volume_frontiers),
            "deferred_reveal_count": len(story_bible.deferred_reveals),
            "expansion_gate_count": len(story_bible.expansion_gates),
        },
        "world_expansion": world_expansion,
        "narrative_counts": {
            "plot_arc_count": len(narrative.plot_arcs),
            "arc_beat_count": len(narrative.arc_beats),
            "clue_count": len(narrative.clues),
            "payoff_count": len(narrative.payoffs),
            "emotion_track_count": len(narrative.emotion_tracks),
            "antagonist_plan_count": len(narrative.antagonist_plans),
            "chapter_contract_count": len(narrative.chapter_contracts),
            "scene_contract_count": len(narrative.scene_contracts),
        },
        "workflow_counts": {
            "run_count": workflow.run_count,
            "completed_run_count": workflow.completed_run_count,
            "failed_run_count": workflow.failed_run_count,
            "latest_run_id": str(workflow.latest_run_id) if workflow.latest_run_id else None,
            "latest_run_status": workflow.latest_run_status,
        },
        "output_stats": {
            "markdown_output_count": len(markdown_entries),
            "project_word_count": int(project_markdown_entry["word_count"]) if project_markdown_entry else 0,
            "chapter_word_count_total": sum(int(item.get("word_count") or 0) for item in chapter_markdown_entries),
            "default_preview_name": str(default_preview_entry["name"]) if default_preview_entry else None,
            "default_preview_word_count": int(default_preview_entry["word_count"]) if default_preview_entry else 0,
            "default_preview_estimated_read_minutes": int(default_preview_entry["estimated_read_minutes"]) if default_preview_entry else 0,
        },
        "outputs": outputs,
        "default_preview_name": (
            str(default_preview_entry["name"]) if default_preview_entry else None
        ),
    }


async def _load_project_structure_payload(
    settings: AppSettings,
    project_slug: str,
) -> dict[str, object]:
    async with session_scope(settings) as session:
        structure = await build_project_structure(session, project_slug)
    return structure.model_dump(mode="json")


async def _load_story_bible_payload(
    settings: AppSettings,
    project_slug: str,
) -> dict[str, object]:
    async with session_scope(settings) as session:
        overview = await build_story_bible_overview(session, project_slug)
    return overview.model_dump(mode="json")


async def _load_narrative_payload(
    settings: AppSettings,
    project_slug: str,
) -> dict[str, object]:
    async with session_scope(settings) as session:
        overview = await build_narrative_overview(session, project_slug)
    return overview.model_dump(mode="json")


async def _load_workflow_payload(
    settings: AppSettings,
    project_slug: str,
) -> dict[str, object]:
    async with session_scope(settings) as session:
        overview = await build_project_workflow_overview(session, project_slug)
    return overview.model_dump(mode="json")


def _read_ui_html() -> str:
    if _UI_HTML_PATH.exists():
        return _UI_HTML_PATH.read_text(encoding="utf-8")
    return "<!DOCTYPE html><html><body><h1>BestSeller UI asset missing.</h1></body></html>"


def _read_reader_html() -> str:
    if _READER_HTML_PATH.exists():
        return _READER_HTML_PATH.read_text(encoding="utf-8")
    return "<!DOCTYPE html><html><body><h1>Reader not found.</h1></body></html>"


def _read_if_reader_html() -> str:
    if _IF_READER_HTML_PATH.exists():
        return _IF_READER_HTML_PATH.read_text(encoding="utf-8")
    return "<!DOCTYPE html><html><body><h1>IF Reader not found.</h1></body></html>"


def _load_if_novels_payload(settings: AppSettings) -> list[dict[str, object]]:
    """Scan the output dir for story_package.json files and return metadata."""
    output_base = Path(settings.output.base_dir)
    results: list[dict[str, object]] = []
    if not output_base.exists():
        return results
    for slug_dir in sorted(output_base.iterdir()):
        if not slug_dir.is_dir():
            continue
        package_path = slug_dir / "if" / "story_package.json"
        if not package_path.exists():
            continue
        try:
            data = json.loads(package_path.read_text(encoding="utf-8"))
            book = data.get("book") or {}
            chapters = data.get("chapters") or []
            results.append({
                "slug": slug_dir.name,
                "title": book.get("title") or slug_dir.name,
                "genre": book.get("genre") or "",
                "synopsis": book.get("synopsis") or "",
                "total_chapters": book.get("total_chapters") or len(chapters),
                "tags": book.get("tags") or [],
                "package_path": str(package_path),
                "modified_at": datetime.fromtimestamp(
                    package_path.stat().st_mtime, UTC
                ).isoformat(),
            })
        except (OSError, json.JSONDecodeError):
            continue
    return results


def _build_chapter_toc(output_dir: Path) -> list[dict[str, object]]:
    """Scan chapter-NNN.md files and extract titles for TOC."""
    import re as _re
    entries: list[dict[str, object]] = []
    for p in sorted(output_dir.glob("chapter-*.md")):
        first_line = ""
        try:
            with p.open(encoding="utf-8") as f:
                for line in f:
                    stripped = line.strip()
                    if stripped:
                        first_line = stripped
                        break
        except OSError:
            continue
        # Strip H1 marker, then remove leading duplicate "第N章 " prefix
        raw = first_line.lstrip("# ").strip() if first_line.startswith("#") else p.stem
        # "第1章 第1章：追线" → "第1章：追线"
        title = _re.sub(r"^第\d+章\s+", "", raw) or raw
        try:
            num = int(p.stem.split("-")[1])
        except (IndexError, ValueError):
            num = len(entries) + 1
        entries.append({"number": num, "title": title, "filename": p.name})
    return entries


def serve_web_app(
    host: str = "127.0.0.1",
    port: int = 8787,
    *,
    open_browser: bool = False,
) -> None:
    settings = load_settings()
    task_manager = WebTaskManager()
    ui_html = _read_ui_html()
    reader_html = _read_reader_html()
    if_reader_html = _read_if_reader_html()

    class RequestHandler(BaseHTTPRequestHandler):
        server_version = "BestSellerWeb/0.1"

        def log_message(self, format: str, *args: object) -> None:  # noqa: A003
            return

        def _send_json(self, payload: object, status: HTTPStatus = HTTPStatus.OK) -> None:
            body = json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default).encode("utf-8")
            self.send_response(status.value)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_text(
            self,
            payload: str,
            *,
            content_type: str = "text/plain; charset=utf-8",
            status: HTTPStatus = HTTPStatus.OK,
        ) -> None:
            body = payload.encode("utf-8")
            self.send_response(status.value)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_file(self, path: Path) -> None:
            mime, _ = mimetypes.guess_type(path.name)
            content = path.read_bytes()
            self.send_response(HTTPStatus.OK.value)
            self.send_header("Content-Type", mime or "application/octet-stream")
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Content-Disposition", f'inline; filename="{path.name}"')
            self.end_headers()
            self.wfile.write(content)

        def _read_json_body(self) -> dict[str, object]:
            length = int(self.headers.get("Content-Length") or "0")
            raw = self.rfile.read(length) if length > 0 else b"{}"
            data = json.loads(raw.decode("utf-8") or "{}")
            return data if isinstance(data, dict) else {}

        def _route_not_found(self) -> None:
            self._send_json({"error": "Not found."}, status=HTTPStatus.NOT_FOUND)

        def _route_error(self, exc: Exception) -> None:
            self._send_json(
                {
                    "error": str(exc),
                    "type": type(exc).__name__,
                },
                status=HTTPStatus.BAD_REQUEST,
            )

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            path = unquote(parsed.path)
            query = parse_qs(parsed.query)
            try:
                if path == "/":
                    self._send_text(ui_html, content_type="text/html; charset=utf-8")
                    return
                if path == "/api/status":
                    self._send_json(
                        {
                            "app": "BestSeller Web Studio",
                            "database_url": settings.database.url,
                            "llm_mock": settings.llm.mock,
                            "planner_model": settings.llm.planner.model,
                            "writer_model": settings.llm.writer.model,
                            "output_base_dir": str(Path(settings.output.base_dir).resolve()),
                        }
                    )
                    return
                if path == "/api/projects":
                    self._send_json(asyncio.run(_load_projects_payload(settings)))
                    return
                if path == "/api/writing-presets":
                    self._send_json(load_writing_preset_catalog().model_dump(mode="json"))
                    return
                if path == "/api/tasks":
                    self._send_json(task_manager.list_tasks())
                    return
                if path.startswith("/api/tasks/"):
                    task_id = path.removeprefix("/api/tasks/")
                    task = task_manager.get_task(task_id)
                    if task is None:
                        self._route_not_found()
                        return
                    self._send_json(task)
                    return
                if path.startswith("/api/projects/") and path.endswith("/summary"):
                    project_slug = path.split("/")[3]
                    self._send_json(asyncio.run(_load_project_summary_payload(settings, project_slug)))
                    return
                if path.startswith("/api/projects/") and path.endswith("/structure"):
                    project_slug = path.split("/")[3]
                    self._send_json(asyncio.run(_load_project_structure_payload(settings, project_slug)))
                    return
                if path.startswith("/api/projects/") and path.endswith("/story-bible"):
                    project_slug = path.split("/")[3]
                    self._send_json(asyncio.run(_load_story_bible_payload(settings, project_slug)))
                    return
                if path.startswith("/api/projects/") and path.endswith("/narrative"):
                    project_slug = path.split("/")[3]
                    self._send_json(asyncio.run(_load_narrative_payload(settings, project_slug)))
                    return
                if path.startswith("/api/projects/") and path.endswith("/workflow"):
                    project_slug = path.split("/")[3]
                    self._send_json(asyncio.run(_load_workflow_payload(settings, project_slug)))
                    return
                if path.startswith("/api/projects/") and path.endswith("/preview"):
                    project_slug = path.split("/")[3]
                    artifact_name = (query.get("name") or ["project.md"])[0]
                    artifact_path = resolve_project_artifact_path(settings, project_slug, artifact_name)
                    content_md = artifact_path.read_text(encoding="utf-8")
                    self._send_text(
                        _render_preview_html(project_slug, artifact_path.name, content_md),
                        content_type="text/html; charset=utf-8",
                    )
                    return
                if path.startswith("/api/projects/") and path.endswith("/preview-data"):
                    project_slug = path.split("/")[3]
                    artifact_name = (query.get("name") or ["project.md"])[0]
                    artifact_path = resolve_project_artifact_path(settings, project_slug, artifact_name)
                    content_md = artifact_path.read_text(encoding="utf-8")
                    payload = build_preview_payload(project_slug, artifact_path.name, content_md)
                    payload.update(
                        {
                            "path": str(artifact_path.resolve()),
                            "size_bytes": artifact_path.stat().st_size,
                            "modified_at": datetime.fromtimestamp(artifact_path.stat().st_mtime, UTC).isoformat(),
                        }
                    )
                    self._send_json(payload)
                    return
                if path.startswith("/api/projects/") and path.endswith("/artifact"):
                    project_slug = path.split("/")[3]
                    artifact_name = (query.get("name") or [None])[0]
                    if artifact_name is None:
                        raise ValueError("Query parameter 'name' is required.")
                    artifact_path = resolve_project_artifact_path(settings, project_slug, artifact_name)
                    self._send_file(artifact_path)
                    return
                if path.startswith("/api/projects/") and path.endswith("/if/status"):
                    project_slug = path.split("/")[3]
                    if_dir = Path(settings.output.base_dir) / project_slug / "if"
                    build_dir = if_dir / "build"
                    progress_path = if_dir / "if_progress.json"
                    chapters_dir = if_dir / "chapters"
                    progress_data: dict[str, object] = {}
                    if progress_path.exists():
                        progress_data = json.loads(progress_path.read_text(encoding="utf-8"))
                    # Count per-chapter files (new format); fallback to state array (old format)
                    if chapters_dir.exists():
                        chapters_done = len(list(chapters_dir.glob("ch*.json")))
                    else:
                        chapters_done = len(progress_data.get("chapters", []) or progress_data.get("chapters_mainline", []))
                    compiled_files = sorted(f.name for f in build_dir.iterdir()) if build_dir.exists() else []
                    self._send_json({
                        "project_slug": project_slug,
                        "has_progress": progress_path.exists(),
                        "has_bible": "bible" in progress_data,
                        "has_arc_plans": "arc_plans" in progress_data or "arc_plans_mainline" in progress_data,
                        "chapters_done": chapters_done,
                        "has_walkthrough": "walkthrough" in progress_data,
                        "compiled_files": compiled_files,
                    })
                    return
                if path.startswith("/api/projects/") and "/if/download/" in path:
                    parts = path.split("/")
                    project_slug = parts[3]
                    filename = parts[-1]
                    if_dir = Path(settings.output.base_dir) / project_slug / "if"
                    # Check build dir first, then root if dir
                    file_path = if_dir / "build" / filename
                    if not file_path.exists():
                        file_path = if_dir / filename
                    if not file_path.exists():
                        self._route_not_found()
                        return
                    self._send_file(file_path)
                    return
                # ── IF Novels ─────────────────────────────────────────────
                if path == "/api/if-novels":
                    self._send_json(_load_if_novels_payload(settings))
                    return
                if path.startswith("/api/if-novels/") and path.endswith("/story-package"):
                    slug = path.split("/")[3]
                    package_path = (
                        Path(settings.output.base_dir) / slug / "if" / "story_package.json"
                    )
                    if not package_path.exists():
                        self._route_not_found()
                        return
                    pkg = json.loads(package_path.read_text(encoding="utf-8"))
                    # Determine chapter source directory based on ?lang= query param.
                    # zh (default) → if/chapters/;  en/ja/ko → translations/{lang}/chapters/
                    lang_param = (query.get("lang") or ["zh"])[0].lower()
                    valid_langs = {"zh", "en", "ja", "ko"}
                    if lang_param not in valid_langs:
                        lang_param = "zh"
                    if lang_param == "zh":
                        chapters_dir = Path(settings.output.base_dir) / slug / "if" / "chapters"
                    else:
                        chapters_dir = Path(settings.output.base_dir) / slug / "translations" / lang_param / "chapters"
                    # Load all individual chapter files (supersedes story_package chapters list)
                    if chapters_dir.exists():
                        chapter_files = sorted(chapters_dir.glob("ch*.json"))
                        if lang_param != "zh" or len(chapter_files) > len(pkg.get("chapters", [])):
                            loaded: list[dict] = []
                            for cf in chapter_files:
                                try:
                                    loaded.append(json.loads(cf.read_text(encoding="utf-8")))
                                except Exception:
                                    pass
                            if loaded:
                                pkg["chapters"] = loaded
                    pkg["content_lang"] = lang_param
                    # Expose which languages have translations available
                    avail: list[str] = ["zh"]
                    trans_base = Path(settings.output.base_dir) / slug / "translations"
                    for lc in ("en", "ja", "ko"):
                        if (trans_base / lc / "chapters").exists() and any((trans_base / lc / "chapters").glob("ch*.json")):
                            avail.append(lc)
                    pkg["available_langs"] = avail
                    self._send_json(pkg)
                    return
                if path.startswith("/api/if-novels/") and "/branches/" in path:
                    parts = path.split("/")
                    # /api/if-novels/{slug}/branches/{route_id}
                    if len(parts) >= 6:
                        slug = parts[3]
                        route_id = parts[5]
                        branch_dir = (
                            Path(settings.output.base_dir) / slug / "if" / "branches" / route_id
                        )
                        if not branch_dir.exists():
                            self._route_not_found()
                            return
                        all_chapters: list[dict] = []
                        route_meta: dict = {}
                        for arc_file in sorted(branch_dir.glob("*.json")):
                            data = json.loads(arc_file.read_text(encoding="utf-8"))
                            if not route_meta:
                                route_meta = {
                                    "route_id": data.get("route_id", route_id),
                                    "route_title": data.get("route_title", route_id),
                                    "branch_start_chapter": data.get("branch_start_chapter"),
                                    "merge_chapter": data.get("merge_chapter"),
                                }
                            all_chapters.extend(data.get("chapters", []))
                        all_chapters.sort(key=lambda c: c.get("number", 0))
                        self._send_json({**route_meta, "chapters": all_chapters})
                    else:
                        self._route_not_found()
                    return
                if path.startswith("/read-if/"):
                    # Validate slug exists
                    parts = path.split("/")
                    slug = parts[2] if len(parts) > 2 else ""
                    if slug:
                        package_path = (
                            Path(settings.output.base_dir) / slug / "if" / "story_package.json"
                        )
                        if not package_path.exists():
                            self._route_not_found()
                            return
                    self._send_text(if_reader_html, content_type="text/html; charset=utf-8")
                    return
                # ── Novel Reader ──────────────────────────────────────────
                if path.startswith("/read/"):
                    # Serve the reader SPA; slug is embedded in URL for JS
                    self._send_text(reader_html, content_type="text/html; charset=utf-8")
                    return
                if path.startswith("/api/reader/") and path.endswith("/toc"):
                    project_slug = path.split("/")[3]
                    output_dir = _project_output_dir(settings, project_slug)
                    toc = _build_chapter_toc(output_dir)
                    self._send_json({"project_slug": project_slug, "chapters": toc})
                    return
                if path.startswith("/api/reader/") and "/chapter/" in path:
                    parts = path.split("/")
                    project_slug = parts[3]
                    chapter_n = int(parts[5])
                    output_dir = _project_output_dir(settings, project_slug)
                    chapter_file = output_dir / f"chapter-{chapter_n:03d}.md"
                    if not chapter_file.exists():
                        self._route_not_found()
                        return
                    md = chapter_file.read_text(encoding="utf-8")
                    html_content = markdown_to_html(md)
                    stats = build_markdown_reading_stats(md)
                    self._send_json({
                        "number": chapter_n,
                        "filename": chapter_file.name,
                        "html": html_content,
                        "word_count": stats["word_count"],
                        "estimated_read_minutes": stats["estimated_read_minutes"],
                    })
                    return
                self._route_not_found()
            except FileNotFoundError:
                self._route_not_found()
            except Exception as exc:
                self._route_error(exc)

        def do_POST(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            path = unquote(parsed.path)
            try:
                if path == "/api/tasks/autowrite":
                    payload = self._read_json_body()
                    required = ["slug", "title", "genre", "target_words", "target_chapters", "premise"]
                    missing = [key for key in required if not payload.get(key)]
                    if missing:
                        raise ValueError(f"Missing required fields: {', '.join(missing)}")
                    validate_longform_scope(int(payload["target_words"]), int(payload["target_chapters"]))
                    task = task_manager.create_autowrite_task(payload)
                    self._send_json(task, status=HTTPStatus.ACCEPTED)
                    return
                if path == "/api/tasks/repair":
                    payload = self._read_json_body()
                    if not payload.get("project_slug"):
                        raise ValueError("Field 'project_slug' is required.")
                    task = task_manager.create_repair_task(payload)
                    self._send_json(task, status=HTTPStatus.ACCEPTED)
                    return
                if path == "/api/tasks/if-generate":
                    payload = self._read_json_body()
                    if not payload.get("project_slug"):
                        raise ValueError("Field 'project_slug' is required.")
                    task = task_manager.create_if_task(payload)
                    self._send_json(task, status=HTTPStatus.ACCEPTED)
                    return
                self._route_not_found()
            except Exception as exc:
                self._route_error(exc)

    httpd = _FastThreadingHTTPServer((host, port), RequestHandler)
    url = f"http://{host}:{port}"
    print(f"BestSeller Web Studio running at {url}")  # noqa: T201
    if open_browser:
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
