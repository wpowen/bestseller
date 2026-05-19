from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
import html
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
import mimetypes
import os
from pathlib import Path
import socketserver
import threading
import traceback
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse
from uuid import UUID, uuid4
import webbrowser

from bestseller.domain.project import InteractiveFictionConfig, ProjectCreate
from bestseller.infra.db.models import StyleGuideModel
from bestseller.infra.db.session import session_scope
from bestseller.services.book_listing import build_book_listing_profile
from bestseller.services.exports import build_markdown_reading_stats, markdown_to_html
from bestseller.services.if_generation import run_if_pipeline_integrated
from bestseller.services.inspection import (
    build_project_structure,
    build_project_workflow_overview,
    build_story_bible_overview,
)
from bestseller.services.narrative import build_narrative_overview
from bestseller.services.pipelines import ProjectRepairPauseError, run_autowrite_pipeline
from bestseller.services.projects import (
    delete_project_completely,
    get_project_by_slug,
    list_projects,
)
from bestseller.services.genre_creativity import (
    creative_direction_to_user_hints,
    get_genre_creative_direction,
    get_genre_creativity_catalog_payload,
)
from bestseller.services.repair import run_project_repair
from bestseller.services.writing_presets import (
    get_genre_preset_dimensions,
    load_writing_preset_catalog,
    validate_longform_scope,
)
from bestseller.services.writing_profile import (
    get_project_writing_profile,
    sanitize_genre_story_overrides,
)
from bestseller.settings import AppSettings, load_settings

logger = logging.getLogger(__name__)

_READER_HTML_PATH = Path(__file__).with_name("novel_reader.html")
_IF_READER_HTML_PATH = Path(__file__).with_name("novel_if_reader.html")
_QUICKSTART_HTML_PATH = Path(__file__).with_name("novel_quickstart.html")
_LIBRARY_HTML_PATH = Path(__file__).with_name("novel_library.html")
# Bounded LRU cache for markdown artifact metadata.  Previous unbounded dict
# grew without limit over long server runs.  512 entries is enough for a few
# large projects while capping memory usage.
_ARTIFACT_CACHE_MAX = 512
_ARTIFACT_CACHE: dict[str, tuple[int, int, dict[str, object]]] = {}


class _FastThreadingHTTPServer(ThreadingHTTPServer):
    def server_bind(self) -> None:
        socketserver.TCPServer.server_bind(self)
        host, port = self.socket.getsockname()[:2]
        self.server_name = str(host)
        self.server_port = int(port)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _timestamp_seconds(value: object) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _json_default(value: object) -> object:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


_SELF_HEAL_SCAN_DONE_KEY = "bestseller:self_heal:scan_done"
_HEAL_KEY_PREFIXES: dict[str, tuple[str, ...]] = {
    "autowrite": (
        "arq:job:autowrite:heal:",
        "arq:in-progress:autowrite:heal:",
        "arq:retry:autowrite:heal:",
    ),
    "repair": (
        "arq:job:repair:heal:",
        "arq:in-progress:repair:heal:",
        "arq:retry:repair:heal:",
    ),
}


def _wait_for_self_heal_scan(
    redis_url: str,
    *,
    timeout_seconds: float = 90.0,
    poll_interval: float = 1.5,
) -> bool:
    """Block until the worker publishes ``SELF_HEAL_SCAN_DONE_KEY`` or we
    hit ``timeout_seconds``. Returns ``True`` if the marker appeared.

    Without this wait, web's ``auto_resume_zombies`` fires the instant
    Redis is reachable — before worker has finished populating
    ``arq:job:autowrite:heal:*`` — so the heal-owner check sees an empty
    set and the old fail-open path would spawn threads that collide with
    the heal jobs worker is about to enqueue. Waiting lets us trust the
    heal-key scan.
    """
    import time

    import redis as _redis

    try:
        client = _redis.from_url(redis_url, decode_responses=True, socket_timeout=2)
    except Exception:
        logger.warning("auto-resume: redis unreachable, cannot wait for self-heal marker")
        return False

    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            if client.get(_SELF_HEAL_SCAN_DONE_KEY):
                return True
        except Exception:
            logger.exception("auto-resume: redis read failed while waiting for self-heal marker")
            return False
        time.sleep(poll_interval)
    logger.warning(
        "auto-resume: self-heal scan-done marker did not appear within %.0fs; "
        "proceeding with whatever heal keys are present (may miss delegation)",
        timeout_seconds,
    )
    return False


def _fetch_heal_owned_slugs_by_kind(redis_url: str, heal_kind: str) -> set[str]:
    """Return slugs with an active worker self-heal job for one task kind.

    Self-heal uses deterministic ARQ job ids of the form
    ``autowrite:heal:<slug>`` or ``repair:heal:<slug>`` (see
    ``worker/self_heal.py``). A queued, in-progress, or retrying job leaves
    keys under those prefixes.

    The kind split matters: a repair heal job must not make an autowrite task
    for the same slug look owned, and vice versa.

    Returns an empty set on any Redis error. Callers MUST treat an empty
    result as "unknown" rather than "no heal jobs exist" — see
    ``_wait_for_self_heal_scan`` for the startup-race mitigation.
    """
    import redis as _redis

    try:
        client = _redis.from_url(redis_url, decode_responses=True, socket_timeout=2)
    except Exception:
        logger.warning("auto-resume: redis unreachable, skipping heal-owner check")
        return set()

    prefixes = _HEAL_KEY_PREFIXES.get(heal_kind)
    if prefixes is None:
        return set()
    slugs: set[str] = set()
    try:
        for prefix in prefixes:
            for key in client.scan_iter(match=prefix + "*", count=200):
                slug = key[len(prefix) :]
                if slug:
                    slugs.add(slug)
    except Exception:
        logger.exception("auto-resume: redis scan failed — assuming no heal owners")
        return set()
    return slugs


def _fetch_heal_owned_slugs(redis_url: str) -> set[str]:
    """Return slugs with any active worker self-heal job."""
    slugs: set[str] = set()
    for heal_kind in _HEAL_KEY_PREFIXES:
        slugs.update(_fetch_heal_owned_slugs_by_kind(redis_url, heal_kind))
    return slugs


def _worker_heal_job_id(task_type: str, slug: str) -> str | None:
    slug = (slug or "").strip()
    if not slug:
        return None
    kind = "repair" if task_type == "repair" else "autowrite"
    return f"{kind}:heal:{slug}"


def _worker_heal_job_is_active(redis_url: str, job_id: str) -> bool:
    """Return True when ARQ still owns a deterministic self-heal job."""
    try:
        import redis as _redis
    except Exception:
        return False

    try:
        client = _redis.from_url(redis_url, decode_responses=True, socket_timeout=2)
    except Exception:
        return False
    try:
        if client.exists(
            f"arq:job:{job_id}",
            f"arq:in-progress:{job_id}",
            f"arq:retry:{job_id}",
        ):
            return True
        return client.zscore("arq:queue", job_id) is not None
    except Exception:
        return False
    finally:
        try:
            client.close()
        except Exception:
            pass


def _worker_heal_job_state_from_redis_client(client: Any, job_id: str) -> str | None:
    """Return the live ARQ ownership state for a deterministic heal job."""

    if client.exists(f"arq:in-progress:{job_id}"):
        return "running"
    if client.exists(f"arq:job:{job_id}", f"arq:retry:{job_id}"):
        return "queued"
    if client.zscore("arq:queue", job_id) is not None:
        return "queued"
    if client.exists(f"arq:result:{job_id}"):
        return "result"
    return None


def _normalise_worker_progress_events(
    raw_events: list[str],
) -> tuple[list[dict[str, object]], str | None, dict[str, object]]:
    """Convert RedisProgressReporter JSON rows into WebTaskState events."""

    events: list[dict[str, object]] = []
    latest_stage: str | None = None
    latest_payload: dict[str, object] = {}
    for raw in raw_events:
        try:
            evt = json.loads(raw)
        except Exception:
            continue
        ts = evt.get("ts", evt.get("timestamp"))
        if not isinstance(ts, (int, float, str)):
            continue
        stage = evt.get("message") or evt.get("stage")
        if not isinstance(stage, str) or not stage:
            continue
        payload = evt.get("data") if isinstance(evt.get("data"), dict) else evt.get("payload")
        payload_dict = payload if isinstance(payload, dict) else {}
        events.append(
            {
                "timestamp": ts,
                "stage": stage,
                "payload": payload_dict,
            }
        )
        latest_stage = stage
        latest_payload = payload_dict
    return events, latest_stage, latest_payload


def _load_worker_heal_progress_snapshot(
    redis_url: str,
    job_id: str,
    *,
    limit: int = 120,
) -> dict[str, object] | None:
    """Load current ARQ state plus recent RedisProgressReporter events."""

    try:
        import redis as _redis
    except Exception:
        return None

    try:
        client = _redis.from_url(redis_url, decode_responses=True, socket_timeout=2)
    except Exception:
        return None
    try:
        job_state = _worker_heal_job_state_from_redis_client(client, job_id)
        if job_state is None:
            return None
        raw_events = client.lrange(f"task:{job_id}:progress", -limit, -1)
    except Exception:
        return None
    finally:
        try:
            client.close()
        except Exception:
            pass

    events, latest_stage, latest_payload = _normalise_worker_progress_events(raw_events or [])
    if job_state == "running":
        status = "running"
    elif job_state == "queued":
        status = "queued"
    else:
        status = "incomplete"
    if latest_stage in {"failed", "error"}:
        status = "failed"
    elif latest_stage in {"waiting_human", "waiting_human_review", "blocked_generation_gate"}:
        status = "incomplete"
    elif latest_stage in {"completed", "done", "finished"} and job_state == "result":
        status = "completed"
    return {
        "job_state": job_state,
        "status": status,
        "current_stage": latest_stage or "delegated_to_worker_self_heal",
        "progress_events": events,
        "latest_payload": latest_payload,
    }


def _merge_worker_progress_into_db_repair_summary(
    summary: dict[str, object],
    worker_progress: dict[str, object] | None,
) -> dict[str, object]:
    """Attach live worker progress to a synthetic db-repair task summary."""

    if not worker_progress:
        return summary
    summary["status"] = worker_progress.get("status") or summary.get("status")
    summary["current_stage"] = worker_progress.get("current_stage") or summary.get(
        "current_stage"
    )
    worker_events = worker_progress.get("progress_events")
    if isinstance(worker_events, list) and worker_events:
        existing_events = summary.get("progress_events")
        base_events = existing_events if isinstance(existing_events, list) else []
        summary["progress_events"] = (base_events + worker_events)[-300:]
        latest_event = worker_events[-1] if isinstance(worker_events[-1], dict) else {}
        latest_ts = latest_event.get("timestamp")
        if isinstance(latest_ts, (int, float)):
            summary["updated_at"] = datetime.fromtimestamp(float(latest_ts), UTC).isoformat()
        elif isinstance(latest_ts, str):
            summary["updated_at"] = latest_ts
    latest_payload = worker_progress.get("latest_payload")
    if (
        summary.get("status") == "failed"
        and isinstance(latest_payload, dict)
        and latest_payload.get("error")
    ):
        summary["error"] = str(latest_payload.get("error"))
    elif summary.get("status") in {"running", "queued"}:
        summary["error"] = None
    return summary


def _task_progress_payload(task: dict[str, object]) -> dict[str, object]:
    events = task.get("progress_events")
    if not isinstance(events, list):
        events = []
    current_stage = task.get("current_stage")
    latest_event = next(
        (
            event
            for event in reversed(events)
            if isinstance(event, dict)
            and current_stage
            and event.get("stage") == current_stage
        ),
        events[-1] if events else None,
    )
    if not isinstance(latest_event, dict):
        latest_event = None
    return {
        "task_id": task.get("task_id"),
        "status": task.get("status"),
        "task_type": task.get("task_type"),
        "title": task.get("title"),
        "project_slug": task.get("project_slug"),
        "current_stage": task.get("current_stage"),
        "current_stage_label": task.get("current_stage_label"),
        "progress_events": events,
        "latest_event": latest_event,
        "repair_status": task.get("repair_status"),
        "chapter_word_stats": task.get("chapter_word_stats"),
        "error": task.get("error"),
        "result": task.get("result"),
    }


def _load_worker_task_summary(
    settings: AppSettings,
    task_id: str,
) -> dict[str, object] | None:
    if task_id.startswith("autowrite:heal:"):
        task_type = "autowrite"
        slug = task_id.removeprefix("autowrite:heal:")
    elif task_id.startswith("repair:heal:"):
        task_type = "repair"
        slug = task_id.removeprefix("repair:heal:")
    else:
        return None
    if not slug:
        return None
    worker_progress = _load_worker_heal_progress_snapshot(settings.redis.url, task_id)
    if not worker_progress:
        return None
    return {
        "task_id": task_id,
        "worker_job_id": task_id,
        "task_type": task_type,
        "status": worker_progress.get("status") or "running",
        "title": slug,
        "project_title": slug,
        "project_slug": slug,
        "current_stage": worker_progress.get("current_stage")
        or "delegated_to_worker_self_heal",
        "progress_events": worker_progress.get("progress_events") or [],
        "result": worker_progress.get("latest_payload") or {},
        "error": None,
        "synthetic_worker_task": True,
    }


def _arq_redis_settings_from_url(redis_url: str) -> Any:
    from arq.connections import RedisSettings

    parsed = urlparse(redis_url)
    return RedisSettings(
        host=parsed.hostname or "localhost",
        port=parsed.port or 6379,
        database=int((parsed.path or "/0").lstrip("/") or "0"),
        password=parsed.password,
    )


async def _enqueue_autowrite_heal_job_async(redis_url: str, slug: str) -> str | None:
    """Push a web-recovered autowrite zombie into worker self-heal.

    This intentionally reuses ``worker.self_heal._requeue_autowrite`` so the
    web process does not maintain a second, subtly different ARQ job contract.
    """
    from arq.connections import create_pool

    from bestseller.worker.self_heal import (
        StuckProject,
        _autowrite_heal_job_id,
        _requeue_autowrite,
    )

    pool = await create_pool(_arq_redis_settings_from_url(redis_url))
    try:
        stuck = StuckProject(
            project_id=None,
            slug=slug,
            reason="web_auto_resume_zombie",
            stuck_at_chapter=None,
            chapters_total=0,
            chapters_with_draft=0,
        )
        job_id = await _requeue_autowrite(pool, stuck)
        if job_id is None and slug in _fetch_heal_owned_slugs_by_kind(
            redis_url,
            "autowrite",
        ):
            return _autowrite_heal_job_id(slug)
        return job_id
    finally:
        closer = getattr(pool, "aclose", None) or getattr(pool, "close", None)
        if closer is not None:
            result = closer()
            if asyncio.iscoroutine(result):
                await result


def _enqueue_autowrite_heal_job(redis_url: str, slug: str) -> str | None:
    """Synchronously enqueue a deterministic worker self-heal autowrite job."""
    if not redis_url or not slug:
        return None
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        logger.warning("auto-resume: cannot enqueue ARQ job from an active event loop")
        return None
    try:
        return asyncio.run(_enqueue_autowrite_heal_job_async(redis_url, slug))
    except Exception:
        logger.exception(
            "auto-resume: failed to enqueue worker self-heal job for slug=%s",
            slug,
        )
        return None


async def _enqueue_repair_heal_job_async(redis_url: str, slug: str) -> str | None:
    """Push a DB-backed repair summary into the worker repair queue."""
    from arq.connections import create_pool

    from bestseller.worker.self_heal import (
        StuckProject,
        _repair_heal_job_id,
        _requeue_repair,
    )

    pool = await create_pool(_arq_redis_settings_from_url(redis_url))
    try:
        stuck = StuckProject(
            project_id=None,
            slug=slug,
            reason="web_db_repair_resume",
            stuck_at_chapter=None,
            chapters_total=0,
            chapters_with_draft=0,
            heal_kind="repair",
        )
        job_id = await _requeue_repair(pool, stuck)
        if job_id is None and slug in _fetch_heal_owned_slugs_by_kind(redis_url, "repair"):
            return _repair_heal_job_id(slug)
        return job_id
    finally:
        closer = getattr(pool, "aclose", None) or getattr(pool, "close", None)
        if closer is not None:
            result = closer()
            if asyncio.iscoroutine(result):
                await result


def _enqueue_repair_heal_job(redis_url: str, slug: str) -> str | None:
    """Synchronously enqueue a deterministic worker repair self-heal job."""
    if not redis_url or not slug:
        return None
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        logger.warning("repair resume: cannot enqueue ARQ job from an active event loop")
        return None
    try:
        return asyncio.run(_enqueue_repair_heal_job_async(redis_url, slug))
    except Exception:
        logger.exception(
            "repair resume: failed to enqueue worker repair job for slug=%s",
            slug,
        )
        return None


def _sanitize_preset_payload(item: dict[str, object]) -> dict[str, object]:
    payload = dict(item)
    payload["writing_profile_overrides"] = sanitize_genre_story_overrides(
        payload.get("writing_profile_overrides")
        if isinstance(payload.get("writing_profile_overrides"), dict)
        else None
    )
    return payload


def _public_writing_preset_catalog_payload() -> dict[str, object]:
    """Return a web-safe preset catalog without story-specific seed content."""
    catalog = load_writing_preset_catalog().model_dump(mode="json")
    catalog["genre_dimensions"] = get_genre_preset_dimensions()
    catalog["genre_creativity"] = get_genre_creativity_catalog_payload()
    catalog["platform_presets"] = [
        _sanitize_preset_payload(item) if isinstance(item, dict) else item
        for item in (catalog.get("platform_presets") or [])
    ]
    catalog["genre_presets"] = [
        _sanitize_preset_payload(item) if isinstance(item, dict) else item
        for item in (catalog.get("genre_presets") or [])
    ]
    return catalog


def _project_output_dir(settings: AppSettings, project_slug: str) -> Path:
    return (Path(settings.output.base_dir) / project_slug).resolve()


def _match_project_route(path: str, suffix: str) -> str | None:
    """Extract project slug from /api/projects/<slug>/<suffix>.

    Returns None if the path does not match the expected structure.
    """
    prefix = "/api/projects/"
    if not path.startswith(prefix) or not path.endswith(f"/{suffix}"):
        return None
    middle = path[len(prefix) : -len(f"/{suffix}")]
    if not middle or "/" in middle:
        return None
    return middle


def _delete_project_full(
    slug: str,
    task_manager: WebTaskManager,
    settings: AppSettings,
) -> dict[str, object]:
    """Delete task records + DB rows + disk artifacts for a single project slug.

    Skips (returns ok=False) when the project still has active tasks.
    Safe to call for unknown slugs — reports via ``errors`` field.
    """
    out: dict[str, object] = {
        "slug": slug,
        "ok": False,
        "tasks_deleted": 0,
        "db_deleted": False,
        "fs_deleted": False,
        "path": None,
        "errors": [],
    }
    if task_manager.has_active_task_for_project(slug):
        out["errors"].append("project has running or queued tasks")
        return out
    try:
        tasks_deleted = task_manager.delete_tasks_by_project(slug)
        out["tasks_deleted"] = tasks_deleted
    except Exception as exc:
        out["errors"].append(f"task_cleanup_failed: {exc}")

    async def _run() -> dict[str, object]:
        async with session_scope(settings) as session:
            return await delete_project_completely(session, settings, slug)

    try:
        svc_result = asyncio.run(_run())
    except Exception as exc:
        out["errors"].append(f"service_error: {exc}")
        return out

    out["db_deleted"] = bool(svc_result.get("db_deleted"))
    out["fs_deleted"] = bool(svc_result.get("fs_deleted"))
    out["path"] = svc_result.get("path")
    svc_errors = svc_result.get("errors") or []
    if svc_errors:
        out["errors"].extend(svc_errors)
    # A project_not_found_in_db is tolerable if disk was also empty or cleaned.
    only_missing = svc_errors == ["project_not_found_in_db"] and out["fs_deleted"]
    out["ok"] = (out["db_deleted"] and out["fs_deleted"]) or only_missing
    return out


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
    # For chapter markdown files, always sync from DB so edits (dedup fixes,
    # quality rewrites) are reflected immediately without a full export run.
    fresh_content = _try_load_chapter_draft_from_db(settings, project_slug, safe_name)
    if fresh_content is not None:
        output_dir.mkdir(parents=True, exist_ok=True)
        # Only write if content changed to avoid unnecessary disk writes
        if not artifact_path.exists() or artifact_path.read_text(encoding="utf-8") != fresh_content:
            artifact_path.write_text(fresh_content, encoding="utf-8")
        return artifact_path
    if not artifact_path.exists() or not artifact_path.is_file():
        raise FileNotFoundError(f"Artifact '{safe_name}' was not found for '{project_slug}'.")
    return artifact_path


def _try_load_chapter_draft_from_db(
    settings: AppSettings,
    project_slug: str,
    artifact_name: str,
) -> str | None:
    """Return markdown content for a chapter draft if it exists in the DB.

    Matches filenames like ``chapter-001.md``.  Returns *None* if the file
    name doesn't look like a chapter export or no draft is found.
    """
    import re

    m = re.match(r"chapter-(\d{3,4})\.md$", artifact_name)
    if m is None:
        return None
    chapter_number = int(m.group(1))

    from sqlalchemy import select

    from bestseller.infra.db.models import (
        ChapterDraftVersionModel,
        ChapterModel,
        ProjectModel,
    )
    from bestseller.services.exports import format_chapter_heading

    async def _fetch() -> str | None:
        async with session_scope(settings) as session:
            proj = (
                await session.execute(select(ProjectModel).where(ProjectModel.slug == project_slug))
            ).scalar_one_or_none()
            if proj is None:
                return None
            chapter = (
                await session.execute(
                    select(ChapterModel).where(
                        ChapterModel.project_id == proj.id,
                        ChapterModel.chapter_number == chapter_number,
                    )
                )
            ).scalar_one_or_none()
            if chapter is None:
                return None
            draft = (
                await session.execute(
                    select(ChapterDraftVersionModel).where(
                        ChapterDraftVersionModel.chapter_id == chapter.id,
                        ChapterDraftVersionModel.is_current.is_(True),
                    )
                )
            ).scalar_one_or_none()
            if draft is None:
                return None
            heading = format_chapter_heading(
                chapter.chapter_number,
                chapter.title,
                language=proj.language,
            )
            return f"{heading}\n\n{draft.content_md}"

    try:
        return asyncio.run(_fetch())
    except Exception:
        logger.warning(
            "Failed to load chapter %d draft from DB for %s",
            chapter_number,
            project_slug,
            exc_info=True,
        )
        return None


def _render_preview_html(project_slug: str, artifact_name: str, content_md: str) -> str:
    preview = build_preview_payload(project_slug, artifact_name, content_md)
    body = str(preview["html"])
    safe_slug = html.escape(project_slug)
    safe_artifact = html.escape(artifact_name)
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>{safe_slug} / {safe_artifact}</title>
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
        <span>{safe_artifact}</span>
        <small>{safe_slug}</small>
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
    # Evict oldest entries when cache exceeds max size
    if len(_ARTIFACT_CACHE) >= _ARTIFACT_CACHE_MAX:
        # Remove ~25% of entries (oldest insertions via dict ordering)
        evict_count = _ARTIFACT_CACHE_MAX // 4
        for _evict_key in list(_ARTIFACT_CACHE)[:evict_count]:
            del _ARTIFACT_CACHE[_evict_key]
    _ARTIFACT_CACHE[cache_key] = (stat.st_mtime_ns, stat.st_size, dict(metadata))
    return metadata


def build_preview_payload(
    project_slug: str,
    artifact_name: str,
    content_md: str,
    *,
    language: str | None = None,
) -> dict[str, object]:
    stats = build_markdown_reading_stats(content_md)
    return {
        "project_slug": project_slug,
        "artifact_name": artifact_name,
        "word_count": stats["word_count"],
        "character_count": stats["character_count"],
        "paragraph_count": stats["paragraph_count"],
        "estimated_read_minutes": stats["estimated_read_minutes"],
        "html": markdown_to_html(content_md, language=language),
    }


class TaskCancelledError(Exception):
    """Raised inside a task worker when the user requested cancellation."""


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
    cancel_requested: bool = False
    payload: dict[str, object] | None = None  # original payload for resume

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
            "cancel_requested": self.cancel_requested,
            "payload": self.payload,
        }


_WATCHDOG_STALE_PREFIX = "Task watchdog: no progress for >"
_HUMAN_REVIEW_STAGES = frozenset(
    {
        "chapter_pipeline_paused_for_human_review",
        "scene_pipeline_paused_for_human_review",
        "waiting_human",
        "waiting_human_review",
        "blocked_generation_gate",
        "exported_requires_human_review",
        "skipped_requires_human_review",
    }
)
_ATTENTION_VERDICTS = frozenset(
    {
        "attention",
        "needs_attention",
        "waiting_human",
        "waiting_human_review",
        "requires_human_review",
        "exported_requires_human_review",
        "skipped_requires_human_review",
    }
)
_TERMINAL_ATTENTION_STAGES = frozenset(
    {
        "autowrite_completed",
        "completed",
        "done",
        "finished",
        "project_repair_completed",
        "project_pipeline_completed",
    }
)


def _task_has_human_review_gate(task: WebTaskState) -> bool:
    for event in reversed(task.progress_events or []):
        if not isinstance(event, dict):
            continue
        stage = str(event.get("stage") or "")
        if stage in _HUMAN_REVIEW_STAGES or "human_review" in stage:
            return True
        payload = event.get("payload")
        if not isinstance(payload, dict):
            continue
        if payload.get("requires_human_review") is True:
            return True
        verdict = (
            str(
                payload.get("final_verdict")
                or payload.get("verdict")
                or payload.get("status")
                or payload.get("export_status")
                or ""
            )
            .strip()
            .lower()
        )
        if verdict in _ATTENTION_VERDICTS:
            return True
    return False


def _payload_requires_human_attention(payload: dict[str, Any]) -> bool:
    if payload.get("requires_human_review") is True:
        return True
    verdict = (
        str(
            payload.get("final_verdict")
            or payload.get("verdict")
            or payload.get("status")
            or payload.get("export_status")
            or ""
        )
        .strip()
        .lower()
    )
    return verdict in _ATTENTION_VERDICTS


class WebTaskManager:
    def __init__(self, persist_path: Path | None = None) -> None:
        self._lock = threading.Lock()
        self._tasks: dict[str, WebTaskState] = {}
        self._persist_path = persist_path
        # Bounded concurrency for long-running writing tasks. Each autowrite /
        # repair / interactive-fiction task acquires one slot before doing any
        # real work; extra tasks visibly queue until a slot frees up. This
        # prevents LLM-rate-limit/DB-pool pileups when users queue many books.
        max_concurrent = max(1, int(os.getenv("WEB_MAX_CONCURRENT_TASKS", "5")))
        self._task_slots = threading.BoundedSemaphore(max_concurrent)
        self._max_concurrent_tasks = max_concurrent
        # IDs of tasks that were mid-flight when the server last shut down and
        # still carry a rebuildable payload.  ``serve_web`` reads this list
        # after startup recovery runs and re-queues them so the user does not
        # have to manually click Resume after every container restart.
        self._pending_auto_resume_ids: list[str] = []
        if persist_path is not None:
            self._load_from_disk()

    def _run_with_slot(
        self,
        task_id: str,
        worker: callable[[str, dict[str, object]], None],
        payload: dict[str, object],
    ) -> None:
        """Acquire a concurrency slot then run *worker*, always releasing."""
        # Keep the task visibly in "queued" state until a slot is acquired.
        self._task_slots.acquire()
        try:
            worker(task_id, payload)
        finally:
            self._task_slots.release()

    # ── Disk persistence ──────────────────────────────────────────────────

    def _load_from_disk(self) -> None:
        """Restore tasks from a JSON file on disk."""
        if self._persist_path is None or not self._persist_path.exists():
            return
        try:
            data = json.loads(self._persist_path.read_text(encoding="utf-8"))
            changed = False
            for item in data:
                task = WebTaskState(
                    task_id=item["task_id"],
                    task_type=item.get("task_type", "autowrite"),
                    status=item.get("status", "completed"),
                    created_at=item.get("created_at", ""),
                    updated_at=item.get("updated_at", ""),
                    project_slug=item.get("project_slug"),
                    title=item.get("title"),
                    current_stage=item.get("current_stage"),
                    progress_events=item.get("progress_events") or [],
                    result=item.get("result"),
                    error=item.get("error"),
                    cancel_requested=bool(item.get("cancel_requested", False)),
                    payload=item.get("payload"),
                )
                if (
                    task.status == "failed"
                    and str(task.error or "").startswith(_WATCHDOG_STALE_PREFIX)
                    and _task_has_human_review_gate(task)
                ):
                    task.status = "incomplete"
                    task.current_stage = "waiting_human_review"
                    task.error = (
                        "Task reached a human-review or attention gate; "
                        "normalized from an old stale-watchdog failure."
                    )
                    task.progress_events.append(
                        {
                            "timestamp": _utc_now(),
                            "stage": "watchdog_failure_normalized",
                            "payload": {"reason": "human_review_gate"},
                        }
                    )
                    task.progress_events = task.progress_events[-300:]
                    changed = True
                # Running/queued tasks from a previous session need recovery.
                # If the task has a rebuildable payload, move it to queued and
                # stage it for auto-resume by ``serve_web`` once the event
                # loop is up; otherwise we have no way to continue, so mark
                # it failed and let the user decide.
                if task.status in ("running", "queued"):
                    if task.payload and task.task_type in ("autowrite", "repair"):
                        task.status = "queued"
                        task.current_stage = "auto_resume_pending"
                        task.error = None
                        task.cancel_requested = False
                        task.progress_events.append(
                            {
                                "timestamp": _utc_now(),
                                "stage": "auto_resume_queued",
                                "payload": {"reason": "server restart"},
                            }
                        )
                        task.progress_events = task.progress_events[-300:]
                        self._pending_auto_resume_ids.append(task.task_id)
                    else:
                        task.status = "failed"
                        task.current_stage = "failed"
                        task.error = task.error or (
                            "Server restarted while task was running (no payload to resume)"
                        )
                    changed = True
                self._tasks[task.task_id] = task
            # Persist the recovered state so the file on disk matches memory.
            # Without this, a crash before the next _save_to_disk would leave
            # orphaned "running" entries that never get cleaned up.
            if changed:
                self._save_to_disk()
        except (OSError, json.JSONDecodeError, KeyError):
            pass  # corrupt file — start fresh

    def _save_to_disk(self) -> None:
        """Persist current task states to a JSON file.  Caller must hold *_lock*."""
        if self._persist_path is None:
            return
        try:
            self._persist_path.parent.mkdir(parents=True, exist_ok=True)
            payload = [task.to_dict() for task in self._tasks.values()]
            tmp = self._persist_path.with_suffix(".tmp")
            tmp.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default),
                encoding="utf-8",
            )
            tmp.replace(self._persist_path)
        except OSError:
            pass  # best-effort

    # ── Task recovery from output directory ─────────────────────────────

    async def _recover_tasks_from_output(self, settings: AppSettings) -> int:
        """Scan output directory and create synthetic task records for projects
        that have chapter files on disk but no corresponding task entry.

        Looks up each project in the database so the synthesised task has
        an accurate ``target_chapters`` target, a rebuildable ``payload``, and
        a status that correctly reflects whether the project is complete.

        Returns the number of recovered tasks.
        """
        import re as _re

        output_dir = Path(settings.output.base_dir)
        if not output_dir.exists():
            return 0

        # Collect existing project_slugs already tracked
        existing_slugs: set[str] = set()
        with self._lock:
            for task in self._tasks.values():
                if task.project_slug:
                    existing_slugs.add(task.project_slug)

        # Pre-compute dir list + chapter files (pure I/O, no DB yet)
        candidates: list[tuple[Path, list[Path]]] = []
        for slug_dir in sorted(output_dir.iterdir()):
            if not slug_dir.is_dir() or slug_dir.name.startswith("."):
                continue
            if slug_dir.name in existing_slugs:
                continue
            chapter_files = sorted(slug_dir.glob("chapter-*.md"))
            if not chapter_files:
                continue
            candidates.append((slug_dir, chapter_files))

        if not candidates:
            return 0

        recovered_count = 0

        async with session_scope(settings) as session:
            for slug_dir, chapter_files in candidates:
                slug = slug_dir.name
                num_chapters = len(chapter_files)

                # Look up the authoritative project record.
                project = await get_project_by_slug(session, slug)

                # Derive target_chapters: prefer DB, then outline, then disk count.
                if project is not None:
                    target_chapters = int(project.target_chapters or num_chapters)
                    title = project.title or slug
                else:
                    outline_target = _extract_target_chapters_from_project_md(
                        slug_dir / "project.md",
                    )
                    target_chapters = outline_target or num_chapters
                    title = slug

                # If title is still just the slug (missing in DB or placeholder),
                # try to parse the real novel name from project.md's H1 heading.
                if title == slug:
                    project_md = slug_dir / "project.md"
                    if project_md.exists():
                        try:
                            first_lines = project_md.read_text(encoding="utf-8")[:500]
                            title_match = _re.search(r"^#\s+(.+)$", first_lines, _re.MULTILINE)
                            if title_match:
                                title = title_match.group(1).strip() or slug
                        except OSError:
                            pass

                block_payload = (
                    _project_autowrite_block_payload(project) if project is not None else None
                )
                is_incomplete = num_chapters < target_chapters
                if block_payload:
                    status = "cancelled"
                    current_stage = "blocked_structural_repair"
                    error = str(block_payload.get("error") or "")
                else:
                    # ``incomplete`` is a dedicated status for recovered projects
                    # that have some chapters on disk but never reached their
                    # target — distinct from user-initiated ``cancelled``.
                    status = "incomplete" if is_incomplete else "completed"
                    current_stage = (
                        "incomplete_recovered" if is_incomplete else "project_pipeline_completed"
                    )
                    error = (
                        f"Recovered: {num_chapters}/{target_chapters} chapters on disk"
                        if is_incomplete
                        else None
                    )

                # Directory mtime → timestamps
                try:
                    dir_mtime = slug_dir.stat().st_mtime
                except OSError:
                    dir_mtime = 0.0
                dir_ts = datetime.fromtimestamp(dir_mtime, UTC).isoformat()

                # Build progress events so the UI can render a real progress bar.
                events: list[dict[str, object]] = [
                    {
                        "timestamp": dir_ts,
                        "stage": "project_pipeline_started",
                        "payload": {"chapter_count": target_chapters},
                    },
                ]
                for ch_file in chapter_files:
                    ch_match = _re.search(r"chapter-(\d+)", ch_file.name)
                    ch_num = int(ch_match.group(1)) if ch_match else 0
                    if ch_num <= 0:
                        continue
                    try:
                        ch_mtime = ch_file.stat().st_mtime
                    except OSError:
                        ch_mtime = dir_mtime
                    ch_ts = datetime.fromtimestamp(ch_mtime, UTC).isoformat()
                    events.append(
                        {
                            "timestamp": ch_ts,
                            "stage": "chapter_pipeline_completed",
                            "payload": {"chapter_number": ch_num},
                        }
                    )
                if block_payload:
                    events.append(
                        {
                            "timestamp": dir_ts,
                            "stage": "blocked_structural_repair",
                            "payload": {"reason": block_payload.get("reason")},
                        }
                    )

                # Rebuild a resumable payload when we can — this is the whole
                # point of this pass: recovered tasks that the user wants to
                # continue later need a payload ready for ``create_autowrite_task``.
                payload: dict[str, object] | None = None
                if project is not None:
                    payload = _payload_from_project_model(project)

                task = WebTaskState(
                    task_id=f"recovered-{slug}",
                    task_type="autowrite",
                    status=status,
                    created_at=dir_ts,
                    updated_at=dir_ts,
                    project_slug=slug,
                    title=title,
                    current_stage=current_stage,
                    progress_events=events,
                    result={
                        "project_slug": slug,
                        "chapters_written": num_chapters,
                        "target_chapters": target_chapters,
                    },
                    error=error,
                    payload=payload,
                )

                with self._lock:
                    self._tasks[task.task_id] = task
                recovered_count += 1

        if recovered_count > 0:
            with self._lock:
                self._save_to_disk()

        return recovered_count

    # ── Public API ────────────────────────────────────────────────────────

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
            self._save_to_disk()
        thread = threading.Thread(
            target=self._run_with_slot,
            args=(task_id, self._run_autowrite_worker, payload),
            daemon=True,
        )
        thread.start()
        return task.to_dict()

    def resume_autowrite_task(
        self,
        task_id: str,
        payload: dict[str, object],
        *,
        delegate_to_self_heal: bool = False,
        heal_owned: bool = False,
    ) -> dict[str, object] | str | None:
        """Resume a stopped autowrite task *in place*, reusing the same task_id.

        Returns the updated task dict on success, the sentinel string
        ``"busy"`` if the task is currently running or queued, or ``None`` if
        the task does not exist. When ``delegate_to_self_heal`` is true, no
        web thread is spawned; the task is marked as owned by worker self-heal.
        The caller is responsible for mapping these to HTTP responses.
        """
        serialized_payload = json.loads(json.dumps(payload, default=_json_default))
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return None
            if task.status in ("running", "queued"):
                return "busy"
            now = _utc_now()
            if delegate_to_self_heal:
                reason = (
                    "ARQ heal job already active"
                    if heal_owned
                    else "worker self-heal owns resume; web will not spawn a competing thread"
                )
                task.status = "running"
                task.current_stage = "delegated_to_worker_self_heal"
                task.error = None
                task.result = None
                task.cancel_requested = False
                task.updated_at = now
                task.payload = serialized_payload
                task.progress_events.append(
                    {
                        "timestamp": now,
                        "stage": "delegated_to_worker_self_heal",
                        "payload": {"reason": reason, "heal_owned": heal_owned},
                    }
                )
                task.progress_events = task.progress_events[-300:]
                self._save_to_disk()
                return task.to_dict()
            task.status = "queued"
            task.current_stage = "queued"
            task.error = None
            task.result = None
            task.cancel_requested = False
            task.updated_at = now
            task.payload = serialized_payload
            task.progress_events.append(
                {
                    "timestamp": now,
                    "stage": "resume_requested",
                    "payload": {},
                }
            )
            task.progress_events = task.progress_events[-300:]
            self._save_to_disk()
            task_snapshot = task.to_dict()
        thread = threading.Thread(
            target=self._run_with_slot,
            args=(task_id, self._run_autowrite_worker, payload),
            daemon=True,
        )
        thread.start()
        return task_snapshot

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
            payload=json.loads(json.dumps(payload, default=_json_default)),
        )
        with self._lock:
            self._tasks[task_id] = task
            self._save_to_disk()
        thread = threading.Thread(
            target=self._run_with_slot,
            args=(task_id, self._run_repair_worker, payload),
            daemon=True,
        )
        thread.start()
        return task.to_dict()

    # Stages that warrant an immediate disk persist (state transitions,
    # milestones).  High-frequency progress events (scene_draft_completed
    # etc.) are kept in memory and flushed every _PROGRESS_FLUSH_INTERVAL
    # events to avoid thrashing the disk on large novels.
    _PERSIST_STAGES: frozenset[str] = frozenset(
        {
            "running",
            "queued",
            "completed",
            "failed",
            "cancelled",
            "chapter_pipeline_completed",
            "chapter_pipeline_started",
            "conception_complete",
            "story_architect_complete",
            "resume_requested",
            "cancel_requested",
            "blocked_structural_repair",
            "blocked_generation_gate",
            "waiting_human",
            "waiting_human_review",
            "periodic_consistency_check_started",
            "periodic_consistency_check_completed",
            "volume_complete",
        }
    )
    _PROGRESS_FLUSH_INTERVAL: int = 10

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
            normalized_stage = stage.lower()
            if (
                task.status in {"failed", "incomplete", "queued"}
                and normalized_stage
                not in {
                    "failed",
                    "error",
                    "cancelled",
                    "waiting_human",
                    "waiting_human_review",
                    "blocked_generation_gate",
                }
            ):
                task.status = "running"
                task.error = None
            task.progress_events.append(
                {
                    "timestamp": task.updated_at,
                    "stage": stage,
                    "payload": payload or {},
                }
            )
            task.progress_events = task.progress_events[-300:]
            # Persist immediately for key milestones; batch minor updates
            # to avoid excessive disk I/O during large pipelines.
            if (
                stage in self._PERSIST_STAGES
                or len(task.progress_events) % self._PROGRESS_FLUSH_INTERVAL == 0
            ):
                self._save_to_disk()

    def _update_task_title(self, task_id: str, title: str) -> None:
        """Persist a freshly resolved novel title onto the task record."""
        title = (title or "").strip()
        if not title:
            return
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None or task.title == title:
                return
            task.title = title
            task.updated_at = _utc_now()
            self._save_to_disk()
        logger.info("Task %s retitled to %r", task_id, title)

    def _mark_running(self, task_id: str) -> None:
        with self._lock:
            task = self._tasks[task_id]
            task.status = "running"
            task.updated_at = _utc_now()
            task.current_stage = "running"
            task.error = None
            self._save_to_disk()

    def _mark_completed(self, task_id: str, result: dict[str, object]) -> None:
        with self._lock:
            task = self._tasks[task_id]
            task.updated_at = _utc_now()
            task.result = result
            if _payload_requires_human_attention(result):
                task.status = "incomplete"
                task.current_stage = "waiting_human_review"
                task.error = str(
                    result.get("error")
                    or result.get("message")
                    or result.get("reason")
                    or "Task reached a human-review or attention gate."
                )
            else:
                task.status = "completed"
                task.current_stage = "completed"
                task.error = None
            self._save_to_disk()

    def _mark_failed(self, task_id: str, error: str) -> None:
        with self._lock:
            task = self._tasks[task_id]
            task.status = "failed"
            task.updated_at = _utc_now()
            task.current_stage = "failed"
            task.error = error
            self._save_to_disk()
        try:
            logger.error("Task %s failed: %s", task_id, error.splitlines()[-1] if error else "")
        except Exception:
            pass

    def mark_task_blocked_by_structural_repair(self, task_id: str, error: str) -> None:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return
            task.status = "cancelled"
            task.updated_at = _utc_now()
            task.current_stage = "blocked_structural_repair"
            task.error = error
            task.cancel_requested = False
            task.progress_events.append(
                {
                    "timestamp": task.updated_at,
                    "stage": "blocked_structural_repair",
                    "payload": {"reason": error},
                }
            )
            task.progress_events = task.progress_events[-300:]
            self._save_to_disk()
        logger.info("Task %s blocked by structural repair pause", task_id)

    def mark_task_blocked_by_project_gate(
        self,
        task_id: str,
        *,
        stage: str,
        error: str,
    ) -> None:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return
            task.status = "incomplete"
            task.updated_at = _utc_now()
            task.current_stage = stage
            task.error = error
            task.cancel_requested = False
            task.progress_events.append(
                {
                    "timestamp": task.updated_at,
                    "stage": stage,
                    "payload": {"reason": error},
                }
            )
            task.progress_events = task.progress_events[-300:]
            self._save_to_disk()
        logger.info("Task %s blocked by project gate %s", task_id, stage)

    def request_cancel(self, task_id: str) -> bool:
        """Mark a task for cancellation. Worker checks the flag at next progress event.

        Returns True if a running/queued task was marked for cancellation."""
        force = False
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return False
            if task.status not in ("running", "queued"):
                return False
            force = bool(task.cancel_requested)
            task.cancel_requested = True
            task.updated_at = _utc_now()
            task.progress_events.append(
                {
                    "timestamp": task.updated_at,
                    "stage": "cancel_requested",
                    "payload": {"force": force},
                }
            )
            task.progress_events = task.progress_events[-300:]
            self._save_to_disk()
        logger.info("Cancellation requested for task %s (force=%s)", task_id, force)
        if force:
            self._mark_cancelled(task_id)
        return True

    def _mark_cancelled(self, task_id: str) -> None:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return
            task.status = "cancelled"
            task.updated_at = _utc_now()
            task.current_stage = "cancelled"
            task.error = task.error or "Task cancelled by user"
            self._save_to_disk()
        logger.info("Task %s cancelled", task_id)

    def delete_task(self, task_id: str, *, force: bool = False) -> str:
        """Remove a task record from memory and the persisted file.

        Returns one of ``"deleted"``, ``"not_found"``, or ``"running"``.
        Does NOT touch the project output directory or DB row — only the
        task entry is removed so the user can continue to see/read the
        underlying novel.
        """
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return "not_found"
            if task.status in ("running", "queued") and not force:
                return "running"
            del self._tasks[task_id]
            self._save_to_disk()
        logger.info("Deleted task %s", task_id)
        return "deleted"

    def delete_tasks_by_project(self, project_slug: str) -> int:
        """Delete all task records associated with *project_slug*.

        Running / queued tasks are kept — the caller is expected to verify
        that no active tasks exist for the project before invoking this.
        Returns the number of task records removed.
        """
        if not project_slug:
            return 0
        removed = 0
        with self._lock:
            survivors: dict[str, WebTaskState] = {}
            for tid, t in self._tasks.items():
                if t.project_slug == project_slug and t.status not in ("running", "queued"):
                    removed += 1
                    continue
                survivors[tid] = t
            if removed:
                self._tasks = survivors
                self._save_to_disk()
        if removed:
            logger.info("Deleted %d task record(s) for project %s", removed, project_slug)
        return removed

    def has_active_task_for_project(self, project_slug: str) -> bool:
        if not project_slug:
            return False
        with self._lock:
            return any(
                t.project_slug == project_slug and t.status in ("running", "queued")
                for t in self._tasks.values()
            )

    def cleanup_tasks(self, statuses: set[str]) -> int:
        """Bulk-delete all tasks whose status is in *statuses*.

        Tasks in ``running`` / ``queued`` are always skipped regardless of
        what the caller asks for — they must be cancelled first.
        Returns the number of tasks removed.
        """
        protected = {"running", "queued"}
        allowed = statuses - protected
        removed = 0
        with self._lock:
            survivors: dict[str, WebTaskState] = {}
            for tid, t in self._tasks.items():
                if t.status in allowed:
                    removed += 1
                    continue
                survivors[tid] = t
            if removed:
                self._tasks = survivors
                self._save_to_disk()
        if removed:
            logger.info("Cleaned up %d task(s) with statuses %s", removed, sorted(allowed))
        return removed

    def evict_old_tasks(self, max_age_seconds: int = 86400) -> int:
        """Remove finished tasks older than *max_age_seconds*.

        Only tasks in terminal states (completed, failed, cancelled) are
        eligible.  This prevents the ``_tasks`` dict from growing without
        bound in long-running server processes.

        Returns the number of tasks evicted.
        """
        now = datetime.now(UTC)
        removed = 0
        with self._lock:
            survivors: dict[str, WebTaskState] = {}
            for tid, t in self._tasks.items():
                if t.status in ("running", "queued"):
                    survivors[tid] = t
                    continue
                try:
                    updated = datetime.fromisoformat(t.updated_at)
                    age = (now - updated).total_seconds()
                except (ValueError, TypeError):
                    age = max_age_seconds + 1  # treat unparseable as old
                if age > max_age_seconds:
                    removed += 1
                else:
                    survivors[tid] = t
            if removed:
                self._tasks = survivors
                self._save_to_disk()
        return removed

    def _check_cancelled(self, task_id: str) -> None:
        """Raise TaskCancelledError if cancellation has been requested."""
        with self._lock:
            task = self._tasks.get(task_id)
            if task is not None and task.cancel_requested:
                raise TaskCancelledError(f"Task {task_id} cancelled by user")

    def watchdog_sweep(self, stale_after_seconds: int = 2700) -> int:
        """Mark running tasks as failed if no progress event for *stale_after_seconds*.

        Before declaring a task stale, consult a disk-level heartbeat: if the
        project directory has a ``chapter-*.md`` file whose mtime is within the
        stale window, the pipeline is still alive (just deep inside a long
        LLM call) and the task is rescued instead of failed.

        Returns the number of tasks marked failed.
        """
        now = datetime.now(UTC)
        stale_ids: list[str] = []
        with self._lock:
            for task in self._tasks.values():
                if task.status != "running":
                    continue
                try:
                    updated = datetime.fromisoformat(task.updated_at)
                except ValueError:
                    continue
                age = (now - updated).total_seconds()
                if age > stale_after_seconds:
                    stale_ids.append(task.task_id)

        stale_count = 0
        for tid in stale_ids:
            if self._rescue_from_disk_heartbeat(tid, stale_after_seconds):
                continue
            if self._rescue_from_worker_heal_job(tid):
                continue
            if self._mark_unowned_delegated_task_incomplete(tid):
                continue
            if self._mark_human_review_gate_incomplete(tid):
                continue
            self._mark_failed(
                tid,
                f"Task watchdog: no progress for >{stale_after_seconds}s, marking as failed",
            )
            stale_count += 1
        return stale_count

    def _mark_human_review_gate_incomplete(self, task_id: str) -> bool:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None or not _task_has_human_review_gate(task):
                return False
            task.status = "incomplete"
            task.current_stage = "waiting_human_review"
            task.error = "Task is waiting for human review or attention-gate repair."
            task.updated_at = _utc_now()
            task.progress_events.append(
                {
                    "timestamp": task.updated_at,
                    "stage": "waiting_human_review",
                    "payload": {"reason": "watchdog_preserved_attention_gate"},
                }
            )
            task.progress_events = task.progress_events[-300:]
            self._save_to_disk()
        logger.info("Task %s is waiting for human review; marked incomplete", task_id)
        return True

    def _rescue_from_worker_heal_job(self, task_id: str) -> bool:
        """Refresh delegated tasks while their deterministic ARQ job is active."""
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None or task.status != "running":
                return False
            if task.current_stage != "delegated_to_worker_self_heal":
                return False
            job_id = _worker_heal_job_id(task.task_type, task.project_slug or "")
            if job_id is None:
                return False

        try:
            redis_url = load_settings().redis.url
        except Exception:
            return False
        if not _worker_heal_job_is_active(redis_url, job_id):
            return False

        with self._lock:
            task = self._tasks.get(task_id)
            if task is not None and task.status == "running":
                task.updated_at = _utc_now()
                self._save_to_disk()
        logger.info("Task %s rescued by active worker self-heal job %s", task_id, job_id)
        return True

    def _mark_unowned_delegated_task_incomplete(self, task_id: str) -> bool:
        """Stop showing delegated tasks as running once worker ownership is gone."""
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None or task.status != "running":
                return False
            if task.current_stage != "delegated_to_worker_self_heal":
                return False
            now = _utc_now()
            task.status = "incomplete"
            task.updated_at = now
            task.current_stage = "auto_resume_not_claimed"
            task.error = (
                "Worker self-heal no longer has an active job for this task; "
                "resume manually after reviewing the latest gate or repair state."
            )
            task.progress_events.append(
                {
                    "timestamp": now,
                    "stage": "auto_resume_not_claimed",
                    "payload": {"reason": "worker self-heal job no longer active"},
                }
            )
            task.progress_events = task.progress_events[-300:]
            self._save_to_disk()
        logger.info("Task %s no longer has a worker self-heal owner; marked incomplete", task_id)
        return True

    def _rescue_from_disk_heartbeat(
        self,
        task_id: str,
        stale_after_seconds: int,
    ) -> bool:
        """Refresh *task_id*'s ``updated_at`` if a chapter file was recently written.

        Returns True when the task was rescued (pipeline still alive), False
        when no recent chapter mtime was found and the task should be failed.
        """
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None or not task.project_slug:
                return False
            slug = task.project_slug
        try:
            settings = load_settings()
            proj_dir = _project_output_dir(settings, slug)
            if not proj_dir.exists():
                return False
            now_ts = datetime.now(UTC).timestamp()
            latest = 0.0
            for ch in proj_dir.glob("chapter-*.md"):
                try:
                    latest = max(latest, ch.stat().st_mtime)
                except OSError:
                    continue
            if latest <= 0 or (now_ts - latest) > stale_after_seconds:
                return False
            with self._lock:
                t = self._tasks.get(task_id)
                if t is not None:
                    t.updated_at = datetime.fromtimestamp(latest, UTC).isoformat()
                    self._save_to_disk()
            logger.info(
                "Task %s rescued by disk heartbeat: chapter mtime %.0fs ago",
                task_id,
                now_ts - latest,
            )
            return True
        except Exception:
            logger.exception("disk heartbeat rescue failed for %s", task_id)
            return False

    def auto_resume_zombies(self, redis_url: str | None = None) -> list[str]:
        """Re-dispatch every task ``_load_from_disk`` flagged as a zombie.

        Called once from ``serve_web`` after startup-recovery finishes.
        Idempotent — after the first call the pending list is cleared.

        Autowrite zombies are handed to the worker self-heal path. If the
        worker already owns the deterministic ``autowrite:heal:<slug>`` ARQ
        job id, web only marks the task as delegated. If the startup scan did
        not claim the slug, web enqueues that same deterministic job id itself
        instead of starting an in-process writer thread. This preserves the
        single worker owner for row-lock coordination while making restart
        recovery automatic. Only when Redis/ARQ is unavailable does the task
        become ``incomplete`` for manual recovery.

        Repair tasks are delegated to worker self-heal when a deterministic
        ``repair:heal:<slug>`` job exists. That avoids web-side repair threads
        racing the worker repair owner after a compose restart.

        Returns the list of task IDs that were handed off or re-dispatched.
        """
        with self._lock:
            pending = list(self._pending_auto_resume_ids)
            self._pending_auto_resume_ids = []

        # Wait for worker to publish its scan-done marker before trusting
        # the heal-owner set. Without this wait the marker would still be
        # missing on a fresh compose up — web races ahead and sees an
        # empty set → fail-open path would crash on collision.
        scan_ready = False
        if redis_url:
            scan_ready = _wait_for_self_heal_scan(redis_url)
        autowrite_heal_owned_slugs = (
            _fetch_heal_owned_slugs_by_kind(redis_url, "autowrite") if redis_url else set()
        )
        repair_heal_owned_slugs = (
            _fetch_heal_owned_slugs_by_kind(redis_url, "repair") if redis_url else set()
        )

        delegated: list[str] = []
        for task_id in pending:
            repair_payload: dict[str, object] | None = None
            enqueue_autowrite_slug: str | None = None
            with self._lock:
                task = self._tasks.get(task_id)
                if task is None or not task.payload:
                    continue
                if task.status != "queued":
                    # Already transitioned (e.g. user cancelled between
                    # startup and this call) — skip.
                    continue
                slug = (task.project_slug or "").strip()
                if task.task_type == "repair":
                    heal_owned = bool(slug and slug in repair_heal_owned_slugs)
                else:
                    heal_owned = bool(
                        slug
                        and (
                            slug in autowrite_heal_owned_slugs
                            or slug in repair_heal_owned_slugs
                        )
                    )
                if task.task_type == "repair" and not heal_owned:
                    task.current_stage = "queued"
                    task.error = None
                    task.cancel_requested = False
                    task.progress_events.append(
                        {
                            "timestamp": _utc_now(),
                            "stage": "resume_requested",
                            "payload": {"reason": "server restart"},
                        }
                    )
                    task.progress_events = task.progress_events[-300:]
                    repair_payload = dict(task.payload)
                    self._save_to_disk()
                    delegated.append(task_id)
                    logger.info("Resuming repair zombie task %s via web worker", task_id)
                elif heal_owned:
                    reason = "ARQ heal job already active"
                    task.status = "running"
                    task.current_stage = "delegated_to_worker_self_heal"
                    task.error = None
                    task.cancel_requested = False
                    task.progress_events.append(
                        {
                            "timestamp": _utc_now(),
                            "stage": "delegated_to_worker_self_heal",
                            "payload": {"reason": reason, "heal_owned": heal_owned},
                        }
                    )
                    task.progress_events = task.progress_events[-300:]
                    self._save_to_disk()
                    delegated.append(task_id)
                    logger.info(
                        "Delegated zombie task %s (slug=%s, heal_owned=%s) to worker self-heal",
                        task_id,
                        slug,
                        heal_owned,
                    )
                elif task.task_type == "autowrite" and redis_url and slug:
                    enqueue_autowrite_slug = slug
                else:
                    reason = (
                        "worker self-heal scan completed but did not claim this task"
                        if scan_ready
                        else "worker self-heal ownership could not be confirmed"
                    )
                    task.status = "incomplete"
                    task.current_stage = "auto_resume_not_claimed"
                    task.error = (
                        "Auto-resume was not claimed by worker self-heal; "
                        "resume manually to continue."
                    )
                    task.cancel_requested = False
                    task.progress_events.append(
                        {
                            "timestamp": _utc_now(),
                            "stage": "auto_resume_not_claimed",
                            "payload": {"reason": reason, "heal_owned": False},
                        }
                    )
                    task.progress_events = task.progress_events[-300:]
                    self._save_to_disk()
                    logger.info(
                        "Zombie task %s (slug=%s) was not claimed by worker "
                        "self-heal; marked incomplete for manual resume",
                        task_id,
                        slug,
                    )
            if enqueue_autowrite_slug is not None and redis_url:
                job_id = _enqueue_autowrite_heal_job(redis_url, enqueue_autowrite_slug)
                with self._lock:
                    task = self._tasks.get(task_id)
                    if task is None or task.status != "queued":
                        continue
                    if job_id:
                        now = _utc_now()
                        task.status = "running"
                        task.current_stage = "delegated_to_worker_self_heal"
                        task.error = None
                        task.cancel_requested = False
                        task.progress_events.append(
                            {
                                "timestamp": now,
                                "stage": "delegated_to_worker_self_heal",
                                "payload": {
                                    "reason": "web auto-resume enqueued worker self-heal job",
                                    "heal_owned": True,
                                    "enqueued_by_web": True,
                                    "job_id": job_id,
                                },
                            }
                        )
                        task.progress_events = task.progress_events[-300:]
                        self._save_to_disk()
                        delegated.append(task_id)
                        logger.info(
                            "Auto-resume enqueued worker self-heal job %s for "
                            "zombie task %s (slug=%s)",
                            job_id,
                            task_id,
                            enqueue_autowrite_slug,
                        )
                    else:
                        reason = (
                            "worker self-heal scan completed but did not claim this task"
                            if scan_ready
                            else "worker self-heal ownership could not be confirmed"
                        )
                        task.status = "incomplete"
                        task.current_stage = "auto_resume_not_claimed"
                        task.error = (
                            "Auto-resume could not enqueue a worker self-heal job; "
                            "resume manually to continue."
                        )
                        task.cancel_requested = False
                        task.progress_events.append(
                            {
                                "timestamp": _utc_now(),
                                "stage": "auto_resume_not_claimed",
                                "payload": {
                                    "reason": reason,
                                    "heal_owned": False,
                                    "enqueue_failed": True,
                                },
                            }
                        )
                        task.progress_events = task.progress_events[-300:]
                        self._save_to_disk()
                        logger.info(
                            "Zombie task %s (slug=%s) could not be enqueued "
                            "for worker self-heal; marked incomplete",
                            task_id,
                            enqueue_autowrite_slug,
                        )
            if repair_payload is not None:
                thread = threading.Thread(
                    target=self._run_with_slot,
                    args=(task_id, self._run_repair_worker, repair_payload),
                    daemon=True,
                )
                thread.start()

        if delegated:
            logger.info(
                "Delegated %d zombie task(s) to worker self-heal (no web thread)",
                len(delegated),
            )

        return delegated

    def _run_autowrite_worker(self, task_id: str, payload: dict[str, object]) -> None:
        self._mark_running(task_id)
        # Persist payload so resume after restart can re-build the task
        with self._lock:
            task = self._tasks.get(task_id)
            if task is not None:
                task.payload = json.loads(json.dumps(payload, default=_json_default))
                self._save_to_disk()

        def progress(stage: str, progress_payload: dict[str, Any] | None = None) -> None:
            # Check cancellation before each progress event — gives the pipeline
            # a graceful shutdown point at every meaningful checkpoint.
            self._check_cancelled(task_id)
            serialized = json.loads(json.dumps(progress_payload or {}, default=_json_default))
            self._push_progress(task_id, stage, serialized)

        async def runner() -> dict[str, object]:
            settings = load_settings()

            # Run AI conception pipeline for quickstart tasks
            run_conception = bool(payload.get("_run_conception", False))
            effective_premise = str(payload["premise"])
            effective_title = str(payload["title"])
            effective_writing_profile = payload.get("writing_profile")
            user_hints = (
                payload.get("user_hints")
                if isinstance(payload.get("user_hints"), dict)
                else None
            )
            conception_brief: dict[str, object] | None = None
            conception_log: list[dict[str, object]] | None = None
            story_facets_obj = None

            # Use a single session scope for both conception and autowrite
            # to ensure transactional consistency.
            async with session_scope(settings) as session:
                if run_conception:
                    from bestseller.services.conception import (
                        run_conception_pipeline,
                    )

                    genre_key = str(payload.get("_genre_key", ""))
                    if genre_key:
                        # ── Story Architect: generate StoryFacets before conception ──
                        story_facets_obj = None
                        try:
                            from bestseller.services.story_architect import (
                                architect_story_facets,
                            )

                            story_facets_obj = await architect_story_facets(
                                session,
                                settings,
                                primary_genre=genre_key,
                                language=str(payload.get("language", "zh-CN")),
                                genre_key=genre_key,
                                user_hints=user_hints,
                            )
                            progress(
                                "story_architect_complete",
                                {
                                    "tone": story_facets_obj.tone,
                                    "narrative_drive": story_facets_obj.narrative_drive,
                                    "trope_tags": list(story_facets_obj.trope_tags),
                                    "setting": story_facets_obj.setting,
                                    "source": story_facets_obj.generation_source,
                                },
                            )
                        except Exception:
                            logger.warning(
                                "Story Architect failed; proceeding without facets", exc_info=True
                            )

                        conception_result = await run_conception_pipeline(
                            session,
                            settings,
                            genre_key=genre_key,
                            chapter_count=int(payload["target_chapters"]),
                            user_hints=user_hints,
                            story_facets=story_facets_obj,
                            progress=progress,
                        )
                        effective_premise = conception_result.premise
                        effective_title = conception_result.title
                        self._update_task_title(task_id, effective_title)
                        effective_writing_profile = conception_result.writing_profile
                        conception_brief = conception_result.commercial_brief
                        conception_log = conception_result.conception_log
                        effective_synopsis = conception_result.synopsis
                        effective_tags = conception_result.tags
                        progress(
                            "conception_complete",
                            {
                                "title": effective_title,
                                "premise_preview": effective_premise[:200],
                                "synopsis_preview": effective_synopsis[:200]
                                if effective_synopsis
                                else "",
                                "tags": effective_tags,
                                "profile_keys": list(conception_result.writing_profile.keys()),
                                "commercial_brief_keys": list(
                                    conception_result.commercial_brief.keys()
                                ),
                            },
                        )

                project_metadata: dict[str, object] = {"premise": effective_premise}
                if run_conception:
                    project_metadata["synopsis"] = effective_synopsis
                    project_metadata["tags"] = effective_tags
                # Store StoryFacets in metadata for future reference and anti-repetition
                if story_facets_obj is not None:
                    project_metadata["story_facets"] = story_facets_obj.model_dump(mode="json")
                if conception_brief:
                    project_metadata["commercial_brief"] = conception_brief
                    project_metadata["benchmark_works"] = conception_brief.get(
                        "benchmark_works", []
                    )
                    project_metadata["target_audiences"] = conception_brief.get(
                        "target_audiences", []
                    )
                if conception_log:
                    project_metadata["conception_log"] = conception_log
                creative_brief = payload.get("creative_brief")
                if isinstance(creative_brief, dict) and creative_brief:
                    project_metadata["creative_brief"] = creative_brief
                if payload.get("draft_mode"):
                    settings.quality.draft_mode = True
                target_chapters = int(payload["target_chapters"])
                project_create = ProjectCreate(
                    slug=str(payload["slug"]),
                    title=effective_title,
                    genre=str(payload["genre"]),
                    sub_genre=(str(payload["sub_genre"]) if payload.get("sub_genre") else None),
                    audience=(str(payload["audience"]) if payload.get("audience") else None),
                    language=str(payload.get("language") or "zh-CN"),
                    target_word_count=int(payload["target_words"]),
                    target_chapters=target_chapters,
                    metadata=project_metadata,
                    writing_profile=effective_writing_profile,
                )
                common_kwargs: dict[str, object] = {
                    "session": session,
                    "settings": settings,
                    "project_payload": project_create,
                    "premise": effective_premise,
                    "requested_by": "web-ui",
                    "export_markdown": bool(payload.get("export_markdown", True)),
                    "auto_repair_on_attention": bool(payload.get("auto_repair", True)),
                    "progress": progress,
                }
                # Progressive routing (large novels → per-volume plan/write
                # feedback loop) lives inside run_autowrite_pipeline so the
                # web-ui path and worker self-heal path pick the same pipeline
                # for the same target_chapters. See PROGRESSIVE_CHAPTER_THRESHOLD
                # in services.pipelines.
                result = await run_autowrite_pipeline(**common_kwargs)
            return json.loads(json.dumps(result.model_dump(mode="json"), default=_json_default))

        # Overall pipeline cap: 24h. Long enough for 100+ chapters.
        pipeline_timeout = float(os.environ.get("BESTSELLER_PIPELINE_TIMEOUT", "86400"))

        async def guarded_runner() -> dict[str, object]:
            return await asyncio.wait_for(runner(), timeout=pipeline_timeout)

        try:
            result = asyncio.run(guarded_runner())
            self._mark_completed(task_id, result)
        except TaskCancelledError as exc:
            self._mark_cancelled(task_id)
            logger.info("Task %s exited via cancellation: %s", task_id, exc)
        except TimeoutError:
            self._mark_failed(
                task_id,
                f"Pipeline exceeded timeout of {pipeline_timeout:.0f}s",
            )
        except ProjectRepairPauseError as exc:
            project_slug = str(payload.get("slug") or "")
            self.mark_task_blocked_by_structural_repair(
                task_id,
                _format_project_repair_pause_error(
                    project_slug,
                    raw_detail=str(exc),
                ),
            )
            logger.info("Autowrite task %s blocked by structural repair pause: %s", task_id, exc)
        except Exception:
            tb = traceback.format_exc()
            logger.exception("Autowrite task %s crashed", task_id)
            self._mark_failed(task_id, tb)

    def create_quickstart_task(self, payload: dict[str, object]) -> dict[str, object]:
        """Quickstart: genre_key + optional chapter_count → fully automated novel.

        Supports resume: pass ``project_slug`` to continue an existing project
        instead of creating a new one.
        """
        from bestseller.services.writing_presets import (
            list_genre_presets,
            list_length_presets,
        )

        genre_key = str(payload["genre_key"])
        genre_presets = {p.key: p for p in list_genre_presets()}
        genre_preset = genre_presets.get(genre_key)
        if genre_preset is None:
            raise ValueError(
                f"Unknown genre_key: {genre_key}. Available: {list(genre_presets.keys())}"
            )

        # Resolve chapter count
        chapter_count = int(payload.get("chapter_count") or 0)
        length_key = str(payload.get("length_key") or "")
        if chapter_count <= 0 and length_key:
            length_presets = {p.key: p for p in list_length_presets()}
            lp = length_presets.get(length_key)
            if lp:
                chapter_count = lp.target_chapters
        if chapter_count <= 0:
            chapter_count = (
                genre_preset.target_chapter_options[0]
                if genre_preset.target_chapter_options
                else 30
            )

        words_per_chapter = load_settings().generation.words_per_chapter.target
        target_words = chapter_count * words_per_chapter

        # Resume: reuse existing project slug if provided
        resume_slug = str(payload.get("project_slug") or "")
        is_en = genre_preset.language.startswith("en")
        placeholder = (
            f"{genre_preset.name} - Drafting {datetime.now().strftime('%m-%d %H:%M')}"
            if is_en
            else f"{genre_preset.name}·构思中 {datetime.now().strftime('%m-%d %H:%M')}"
        )
        if resume_slug:
            slug = resume_slug
            title = placeholder
        else:
            import time

            slug = f"{genre_key}-{int(time.time())}"
            title = placeholder

        # For new projects, use AI conception to generate premise + writing_profile.
        # For resume, use the stored premise from the existing project.
        is_new_project = not resume_slug
        creative_direction = (
            get_genre_creative_direction(genre_key, str(payload.get("creative_key") or ""))
            if is_new_project
            else None
        )
        creative_hints = creative_direction_to_user_hints(creative_direction)
        premise = (
            f"A {genre_preset.genre} ({genre_preset.sub_genre}) novel: {genre_preset.description}"
            if is_en
            else (
                f"基于{genre_preset.genre}（{genre_preset.sub_genre}）题材，"
                f"{genre_preset.description}"
            )
        )

        autowrite_payload: dict[str, object] = {
            "slug": slug,
            "title": title,
            "genre": genre_preset.genre,
            "sub_genre": genre_preset.sub_genre,
            "target_words": target_words,
            "target_chapters": chapter_count,
            "premise": premise,
            "export_markdown": True,
            "auto_repair": True,
            "draft_mode": bool(payload.get("draft_mode", False)),
            "language": genre_preset.language,
            "writing_profile": sanitize_genre_story_overrides(
                genre_preset.writing_profile_overrides
            ),
            "creative_key": creative_direction.key if creative_direction else "",
            "creative_brief": (
                creative_direction.model_dump(mode="json") if creative_direction else {}
            ),
            "user_hints": creative_hints,
            # Enable AI conception for new projects (not resume)
            "_run_conception": is_new_project,
            "_genre_key": genre_key,
        }
        task = self.create_autowrite_task(autowrite_payload)
        # Enrich response with quickstart metadata
        task["quickstart_meta"] = {
            "genre_key": genre_key,
            "genre_name": genre_preset.name,
            "heat_domains": genre_preset.heat_domains,
            "reader_rewards": genre_preset.reader_rewards,
            "narrative_drives": genre_preset.narrative_drives,
            "creative_key": creative_direction.key if creative_direction else "",
            "creative_title": creative_direction.title if creative_direction else "",
            "chapter_count": chapter_count,
            "target_words": target_words,
        }
        return task

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
            self._save_to_disk()
        thread = threading.Thread(
            target=self._run_with_slot,
            args=(task_id, self._run_if_worker, payload),
            daemon=True,
        )
        thread.start()
        return task.to_dict()

    def _run_if_worker(self, task_id: str, payload: dict[str, object]) -> None:
        self._mark_running(task_id)
        with self._lock:
            t = self._tasks.get(task_id)
            if t is not None:
                t.payload = json.loads(json.dumps(payload, default=_json_default))
                self._save_to_disk()

        def progress(stage: str, progress_payload: dict[str, Any] | None = None) -> None:
            self._check_cancelled(task_id)
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
                    if_cfg_for_profile = InteractiveFictionConfig.model_validate(
                        {**cfg_dict, "enabled": True}
                    )
                    title = str(payload.get("title") or project_slug)
                    genre = cfg_dict.get("if_genre") or "修仙升级"
                    wp_override = WritingProfile(interactive_fiction=if_cfg_for_profile)
                    new_project = await create_project(
                        session,
                        ProjectCreate(
                            slug=project_slug,
                            title=title,
                            genre=str(genre),
                            target_word_count=int(cfg_dict.get("target_chapters", 100)) * 2000,
                            target_chapters=int(cfg_dict.get("target_chapters", 100)),
                            project_type=ProjectType.INTERACTIVE,
                            writing_profile=wp_override,
                        ),
                        settings,
                    )
                    await session.commit()
                    return new_project

            # Run everything in a single asyncio.run() to avoid creating
            # multiple event loops (each leaks an httpx client + DB engine).
            async def _run_if() -> str:
                project = await get_or_create_project()
                # Merge stored writing profile IF config with request overrides
                stored = project.metadata_json.get("writing_profile", {})
                stored_if = stored.get("interactive_fiction", {})
                merged_cfg = {**stored_if, **cfg_dict, "enabled": True}
                cfg = InteractiveFictionConfig.model_validate(merged_cfg)

                output_base = Path(settings.output.base_dir)
                out_path = await run_if_pipeline_integrated(
                    project,
                    cfg,
                    output_base,
                    settings=settings,
                    resume=resume,
                    on_progress=progress,
                )
                return str(out_path)

            out_path = asyncio.run(_run_if())
            self._mark_completed(task_id, {"story_package": out_path})
        except TaskCancelledError:
            self._mark_cancelled(task_id)
        except Exception:
            tb = traceback.format_exc()
            logger.exception("IF task %s crashed", task_id)
            self._mark_failed(task_id, tb)

    def _run_repair_worker(self, task_id: str, payload: dict[str, object]) -> None:
        self._mark_running(task_id)
        with self._lock:
            t = self._tasks.get(task_id)
            if t is not None:
                t.payload = json.loads(json.dumps(payload, default=_json_default))
                self._save_to_disk()

        def progress(stage: str, progress_payload: dict[str, Any] | None = None) -> None:
            self._check_cancelled(task_id)
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
                    include_pending_rewrite_tasks=bool(
                        payload.get("include_pending_rewrite_tasks", False)
                    ),
                    scan_publication_gate_candidates=bool(
                        payload.get("scan_publication_gate_candidates", False)
                    ),
                    progress=progress,
                )
            return json.loads(json.dumps(result.model_dump(mode="json"), default=_json_default))

        try:
            result = asyncio.run(runner())
            self._mark_completed(task_id, result)
        except TaskCancelledError:
            self._mark_cancelled(task_id)
        except Exception:
            tb = traceback.format_exc()
            logger.exception("Repair task %s crashed", task_id)
            self._mark_failed(task_id, tb)

    # ------------------------------------------------------------------
    # Bridge arq-worker progress (Redis list) → in-memory task.current_stage
    # ------------------------------------------------------------------
    # Novel generation runs on two independent code paths:
    #   (1) UI-initiated       → web server thread → direct _push_progress().
    #   (2) self-heal / resume → arq worker container → RedisProgressReporter
    #                            writes to Redis list ``task:<tid>:progress``.
    # The UI reads ``task.current_stage`` from the web process only, so path (2)
    # events never surface — every self-healed task appeared frozen at
    # ``volume_planning_started``. This sweep reads the worker's Redis list
    # and re-applies events into memory so the UI reflects real progress.

    def sync_progress_from_worker_redis(self, redis_url: str) -> int:
        """Pull fresh progress events from Redis for tasks that the arq
        worker is driving, and update ``current_stage`` / ``progress_events``
        in memory. Returns the number of tasks updated.

        No-ops when Redis is unreachable — the UI degrades to the stale
        in-memory view rather than crashing the whole server.
        """
        import redis as _redis

        try:
            client = _redis.from_url(redis_url, decode_responses=True, socket_timeout=2)
        except Exception:
            return 0

        def _worker_job_redis_state(job_id: str) -> str | None:
            try:
                if client.exists(
                    f"arq:job:{job_id}",
                    f"arq:in-progress:{job_id}",
                    f"arq:retry:{job_id}",
                ):
                    return "active"
                if client.zscore("arq:queue", job_id) is not None:
                    return "active"
                if client.exists(f"arq:result:{job_id}"):
                    return "result"
                return None
            except Exception:
                return None

        # Snapshot task_id → (project_slug, task_type, known_event_ts_set) to avoid
        # holding the manager lock across Redis I/O.
        with self._lock:
            snapshot: list[tuple[str, str, str, set[float], float]] = []
            for tid, task in self._tasks.items():
                if task.status not in ("running", "queued", "failed", "incomplete"):
                    continue
                slug = (task.project_slug or "").strip()
                if not slug:
                    continue
                known_ts: set[float] = set()
                for evt in task.progress_events:
                    ts = evt.get("timestamp") if isinstance(evt, dict) else None
                    if isinstance(ts, (int, float)):
                        known_ts.add(float(ts))
                snapshot.append(
                    (
                        tid,
                        slug,
                        task.task_type,
                        known_ts,
                        _timestamp_seconds(task.updated_at) or 0.0,
                    )
                )

        updated = 0
        for tid, slug, task_type, known_ts, task_updated_ts in snapshot:
            # The arq worker task id follows either ``autowrite:heal:<slug>``
            # or ``repair:heal:<slug>``. A recovered project normally has one
            # durable autowrite card in the UI; when self-heal chooses a repair
            # job for that same project, surface that repair progress on the
            # existing book card instead of leaving it frozen as incomplete.
            if task_type == "repair":
                candidates = [
                    (
                        f"repair:heal:{slug}",
                        f"task:repair:heal:{slug}:progress",
                    )
                ]
            else:
                candidates = [
                    (
                        f"autowrite:heal:{slug}",
                        f"task:autowrite:heal:{slug}:progress",
                    ),
                    (
                        f"repair:heal:{slug}",
                        f"task:repair:heal:{slug}:progress",
                    ),
                ]
            selected: tuple[str, str] | None = None
            raw_events: list[str] = []
            selected_latest_ts = -1.0
            selected_state_rank = -1
            for job_id, redis_key in candidates:
                job_state = _worker_job_redis_state(job_id)
                if job_state is None:
                    continue
                try:
                    # Last 100 events are plenty — earlier ones are already in
                    # task.progress_events (and we only keep the last 300 anyway).
                    candidate_events = client.lrange(redis_key, -100, -1)
                except Exception:
                    continue
                candidate_latest_ts = -1.0
                for raw in candidate_events:
                    try:
                        evt = json.loads(raw)
                    except Exception:
                        continue
                    ts = evt.get("ts")
                    if isinstance(ts, (int, float)):
                        candidate_latest_ts = max(candidate_latest_ts, float(ts))
                state_rank = 1 if job_state == "active" else 0
                if (
                    job_state == "result"
                    and candidate_latest_ts > 0
                    and candidate_latest_ts <= task_updated_ts
                ):
                    continue
                if state_rank > selected_state_rank or (
                    state_rank == selected_state_rank
                    and candidate_latest_ts > selected_latest_ts
                ):
                    selected = (job_id, redis_key)
                    raw_events = candidate_events
                    selected_latest_ts = candidate_latest_ts
                    selected_state_rank = state_rank
            if selected is None or not raw_events:
                continue

            fresh: list[dict[str, Any]] = []
            latest_stage: str | None = None
            latest_payload: dict[str, Any] = {}
            latest_ts: float = 0.0
            for raw in raw_events:
                try:
                    evt = json.loads(raw)
                except Exception:
                    continue
                ts = evt.get("ts")
                if not isinstance(ts, (int, float)):
                    continue
                ts_f = float(ts)
                msg = evt.get("message")
                if not isinstance(msg, str):
                    continue
                if ts_f > latest_ts:
                    latest_ts = ts_f
                    latest_stage = msg
                    latest_payload = evt.get("data") if isinstance(evt.get("data"), dict) else {}
                if ts_f in known_ts:
                    continue
                fresh.append(
                    {
                        "timestamp": ts_f,
                        "stage": msg,
                        "payload": evt.get("data") or {},
                    }
                )

            if not fresh and latest_stage is None:
                continue

            with self._lock:
                task = self._tasks.get(tid)
                if task is None:
                    continue
                if fresh:
                    task.progress_events = (task.progress_events + fresh)[-300:]
                if latest_stage is not None:
                    normalized_stage = latest_stage.lower()
                    task.current_stage = latest_stage
                    is_human_review_stage = (
                        normalized_stage in _HUMAN_REVIEW_STAGES
                        or "human_review" in normalized_stage
                    )
                    if is_human_review_stage or (
                        normalized_stage in _TERMINAL_ATTENTION_STAGES
                        and _payload_requires_human_attention(latest_payload)
                    ):
                        task.current_stage = (
                            "waiting_human_review"
                            if normalized_stage in _TERMINAL_ATTENTION_STAGES
                            else latest_stage
                        )
                        task.status = "incomplete"
                        error = latest_payload.get("error") or latest_payload.get("message")
                        reason = latest_payload.get("reason")
                        task.error = str(
                            error
                            or reason
                            or "Task reached a human-review or attention gate."
                        )
                    elif normalized_stage in {"completed", "done", "finished"}:
                        task.status = "completed"
                        task.result = latest_payload or task.result
                        task.error = None
                    elif normalized_stage in {"failed", "error"}:
                        task.status = "failed"
                        error = latest_payload.get("error") or latest_payload.get("message")
                        task.error = str(error or "Worker self-heal task failed")
                    elif normalized_stage in {
                        "waiting_human",
                        "waiting_human_review",
                        "blocked_generation_gate",
                    }:
                        task.status = "incomplete"
                        error = latest_payload.get("error") or latest_payload.get("message")
                        reason = latest_payload.get("reason")
                        task.error = str(
                            error
                            or reason
                            or "Task is waiting for planning-gate repair."
                        )
                        # When the worker emitted a structured sub-code (e.g.
                        # ``write_safety_gate_failed:identity_pronoun_mismatch``),
                        # tack the actionable suffix onto the stage so the UI
                        # badge can surface the precise cause without the user
                        # having to expand the error blob.
                        if reason and ":" in str(reason):
                            subcode = str(reason).split(":", 1)[1].strip()
                            if subcode:
                                task.current_stage = (
                                    f"{normalized_stage}:{subcode}"
                                )
                    elif task.status in {"failed", "incomplete"}:
                        task.status = "running"
                        task.error = None
                task.updated_at = _utc_now()
                self._save_to_disk()
                updated += 1

        try:
            client.close()
        except Exception:
            pass
        return updated


def _extract_target_chapters_from_project_md(project_md: Path) -> int | None:
    """Parse the project plan markdown for the highest chapter number referenced
    in an H2 heading (e.g. ``## 第 12 章 ...`` or ``## 12. ...``).

    Returns ``None`` if the file cannot be read or no chapter heading is found.
    """
    import re as _re

    if not project_md.exists():
        return None
    try:
        text = project_md.read_text(encoding="utf-8")
    except OSError:
        return None
    max_ch = 0
    for m in _re.finditer(r"^##\s+(?:\u7b2c\s*)?(\d+)", text, _re.MULTILINE):
        try:
            n = int(m.group(1))
        except ValueError:
            continue
        if n > max_ch:
            max_ch = n
    return max_ch if max_ch > 0 else None


def _infer_genre_key_from_slug(slug: str) -> str:
    """Derive a plausible genre_key from a slug like ``apocalypse-supply-1234567890``.

    Strips a trailing numeric suffix and validates against the installed
    genre presets. Returns an empty string when no preset matches.
    """
    from bestseller.services.writing_presets import list_genre_presets

    candidate = slug.rsplit("-", 1)[0] if "-" in slug else slug
    valid_keys = {p.key for p in list_genre_presets()}
    if candidate in valid_keys:
        return candidate
    if slug in valid_keys:
        return slug
    return ""


def _payload_from_project_model(project: Any) -> dict[str, object]:
    """Build an autowrite payload from a stored ``ProjectModel`` so hung/recovered
    tasks can be resumed without relying on a previously persisted payload.
    """
    metadata = project.metadata_json or {}
    genre_key = str(metadata.get("genre_key") or _infer_genre_key_from_slug(project.slug))
    premise = str(
        metadata.get("premise") or f"\u7ee7\u7eed\u300a{project.title}\u300b\u7684\u521b\u4f5c"
    )
    return {
        "slug": project.slug,
        "title": project.title,
        "genre": project.genre,
        "sub_genre": project.sub_genre,
        "target_words": project.target_word_count,
        "target_chapters": project.target_chapters,
        "premise": premise,
        "export_markdown": True,
        "auto_repair": True,
        "writing_profile": metadata.get("writing_profile"),
        "_run_conception": False,
        "_genre_key": genre_key,
    }


async def _rebuild_payload_from_db(
    settings: AppSettings,
    slug: str,
) -> dict[str, object] | None:
    """Look up a project by slug and return an autowrite-ready payload.

    Used as a fallback for the ``/api/tasks/{id}/resume`` endpoint when the
    original task had no persisted payload (e.g. tasks synthesised by
    ``_recover_tasks_from_output`` before the payload field existed).
    """
    async with session_scope(settings) as session:
        project = await get_project_by_slug(session, slug)
        if project is None:
            return None
        return _payload_from_project_model(project)


async def _load_project_autowrite_block_payload(
    settings: AppSettings,
    slug: str,
) -> dict[str, object] | None:
    """Return a user-facing block payload when normal writing is paused.

    This keeps web entrypoints aligned with the pipeline-level
    ``ProjectRepairPauseError`` guard: paused structural-repair projects
    should not even launch a writing worker.
    """
    slug = (slug or "").strip()
    if not slug:
        return None
    async with session_scope(settings) as session:
        project = await get_project_by_slug(session, slug)
        if project is None:
            return None
        return _project_autowrite_block_payload(project)


async def _recover_projects_from_output(settings: AppSettings) -> list[dict[str, object]]:
    """Scan the output directory and re-create DB records for any projects
    that have chapter files on disk but no corresponding DB entry.

    Returns a list of recovered project summaries.
    """
    import re as _re

    output_base = Path(settings.output.base_dir)
    if not output_base.exists():
        return []

    recovered: list[dict[str, object]] = []

    async with session_scope(settings) as session:
        existing_projects = await list_projects(session)
        existing_slugs = {p.slug for p in existing_projects}

        for slug_dir in sorted(output_base.iterdir()):
            if not slug_dir.is_dir() or slug_dir.name.startswith("."):
                continue
            slug = slug_dir.name
            if slug in existing_slugs:
                continue

            # Check for chapter files (chapter-001.md, etc.) or project.md
            chapter_files = sorted(slug_dir.glob("chapter-*.md"))
            project_md = slug_dir / "project.md"
            if not chapter_files and not project_md.exists():
                continue

            # Extract title from project.md or first chapter
            title = slug
            genre = "general-fiction"
            if project_md.exists():
                try:
                    first_lines = project_md.read_text(encoding="utf-8")[:500]
                    title_match = _re.search(r"^#\s+(.+)$", first_lines, _re.MULTILINE)
                    if title_match:
                        title = title_match.group(1).strip()
                    genre_match = _re.search(
                        r">\s*\u7c7b\u578b\uff1a(.+)$", first_lines, _re.MULTILINE
                    )
                    if genre_match:
                        genre = genre_match.group(1).strip()
                except OSError:
                    pass

            chapter_count = len(chapter_files)
            total_words = 0
            for cf in chapter_files[:5]:  # sample first 5 for avg
                try:
                    total_words += len(cf.read_text(encoding="utf-8"))
                except OSError:
                    pass
            avg_words_per_ch = total_words // max(len(chapter_files[:5]), 1)

            # Derive the *true* chapter target:
            #   1) count H2 outline entries in project.md (``## 第 N 章 …``)
            #   2) fall back to chapter_count, but never below 30 so a
            #      mid-run recovery doesn't collapse the goal to "what we
            #      already wrote" and block resume.
            outline_target = _extract_target_chapters_from_project_md(project_md)
            target_chapters = outline_target or max(chapter_count, 30)
            estimated_total = avg_words_per_ch * target_chapters

            # Store a validated genre_key so resume doesn't need regex guessing.
            genre_key = _infer_genre_key_from_slug(slug)
            recovered_status = "completed" if chapter_count >= target_chapters else "in_progress"

            from bestseller.domain.enums import ChapterStatus
            from bestseller.infra.db.models import ChapterModel, ProjectModel

            project = ProjectModel(
                slug=slug,
                title=title,
                genre=genre,
                sub_genre=None,
                target_word_count=estimated_total or target_chapters * 2000,
                target_chapters=target_chapters,
                status=recovered_status,
                metadata_json={
                    "recovered": True,
                    "source": f"output/{slug}",
                    "genre_key": genre_key,
                    "chapters_on_disk": chapter_count,
                },
            )
            session.add(project)
            # Flush to allocate project.id so we can link chapter rows.
            await session.flush()

            # Materialize ChapterModel rows for every chapter-*.md on disk
            # marked as COMPLETE, so ``run_autowrite_pipeline``'s resume
            # filter (which checks ``ChapterStatus.COMPLETE`` in DB, not disk)
            # correctly skips them and continues from the next chapter.
            for ch_file in chapter_files:
                ch_num_match = _re.search(r"chapter-(\d+)", ch_file.name)
                if not ch_num_match:
                    continue
                ch_num = int(ch_num_match.group(1))
                try:
                    body = ch_file.read_text(encoding="utf-8")
                except OSError:
                    body = ""
                ch_title: str | None = None
                h1 = _re.search(r"^#\s+(.+)$", body[:500], _re.MULTILINE)
                if h1:
                    ch_title = h1.group(1).strip()[:200]
                session.add(
                    ChapterModel(
                        project_id=project.id,
                        chapter_number=ch_num,
                        title=ch_title,
                        chapter_goal="(recovered from disk)",
                        information_revealed=[],
                        information_withheld=[],
                        foreshadowing_actions={},
                        current_word_count=len(body),
                        target_word_count=2000,
                        status=ChapterStatus.COMPLETE.value,
                        metadata_json={"recovered": True, "source_file": ch_file.name},
                    )
                )
            recovered.append(
                {
                    "slug": slug,
                    "title": title,
                    "genre": genre,
                    "chapters_found": chapter_count,
                    "target_chapters": target_chapters,
                }
            )

    return recovered


async def _load_projects_payload(settings: AppSettings) -> list[dict[str, object]]:
    async with session_scope(settings) as session:
        rows = await list_projects(session)
        repair_counts = await _load_project_chapter_state_counts(
            session,
            [row.id for row in rows],
        )
        autonomous_repair_counts = await _load_project_autonomous_repair_task_counts(
            session,
            [row.id for row in rows],
        )
        return [
            {
                "id": str(row.id),
                "slug": row.slug,
                "title": row.title,
                "genre": row.genre,
                "status": row.status,
                "created_at": row.created_at.isoformat() if row.created_at else None,
                "updated_at": row.updated_at.isoformat() if row.updated_at else None,
                "target_word_count": row.target_word_count,
                "target_chapters": row.target_chapters,
                "repair_status": _build_project_repair_status_payload(
                    row,
                    repair_counts.get(row.id, []),
                    autonomous_repair_counts.get(row.id, {}),
                ),
            }
            for row in rows
        ]


async def _load_library_payload(settings: AppSettings) -> list[dict[str, object]]:
    """Return a richer per-project payload for the library page.

    Combines DB fields with filesystem chapter stats so the library can show
    "all in-progress + completed books" with click-through to the reader.
    """
    output_base = Path(settings.output.base_dir)
    async with session_scope(settings) as session:
        rows = await list_projects(session)
        repair_counts = await _load_project_chapter_state_counts(
            session,
            [row.id for row in rows],
        )
        autonomous_repair_counts = await _load_project_autonomous_repair_task_counts(
            session,
            [row.id for row in rows],
        )
        results: list[dict[str, object]] = []
        for row in rows:
            slug = row.slug
            slug_dir = output_base / slug if slug else None
            chapter_files: list[Path] = []
            project_md_exists = False
            if slug_dir and slug_dir.exists():
                chapter_files = sorted(slug_dir.glob("chapter-*.md"))
                project_md_exists = (slug_dir / "project.md").exists()
            chapters_on_disk = len(chapter_files)
            words_on_disk = 0
            for cf in chapter_files:
                try:
                    words_on_disk += len(cf.read_text(encoding="utf-8"))
                except OSError:
                    continue

            # DB-level chapter counters
            db_counters = repair_counts.get(row.id, [])
            db_completed = sum(
                int(c.get("count") or 0)
                for c in db_counters
                if str(c.get("status") or "").lower() in {"complete", "completed"}
            )
            db_total = sum(int(c.get("count") or 0) for c in db_counters)
            completed_chapters = max(db_completed, chapters_on_disk)

            # Last updated — prefer the most recent chapter file mtime
            last_mtime = 0.0
            for cf in chapter_files:
                try:
                    last_mtime = max(last_mtime, cf.stat().st_mtime)
                except OSError:
                    continue
            last_updated_iso = (
                datetime.fromtimestamp(last_mtime, tz=UTC).isoformat() if last_mtime > 0 else None
            )

            # Surface tags / category from listing profile if it exists
            listing_path = slug_dir / "listing_profile.json" if slug_dir else None
            tags: list[str] = []
            tagline = None
            primary_category = None
            if listing_path and listing_path.exists():
                try:
                    listing = json.loads(listing_path.read_text(encoding="utf-8"))
                    raw_tags = listing.get("tags") or []
                    if isinstance(raw_tags, list):
                        tags = [str(t) for t in raw_tags[:10] if t]
                    tagline = listing.get("logline") or listing.get("short_intro") or None
                    primary_category = listing.get("primary_category")
                except (OSError, ValueError):
                    pass

            results.append(
                {
                    "id": str(row.id),
                    "slug": slug,
                    "title": row.title,
                    "genre": row.genre,
                    "primary_category": primary_category,
                    "status": row.status,
                    "target_chapters": row.target_chapters,
                    "target_word_count": row.target_word_count,
                    "completed_chapters": completed_chapters,
                    "chapters_on_disk": chapters_on_disk,
                    "db_chapter_total": db_total,
                    "words_on_disk": words_on_disk,
                    "last_updated": last_updated_iso,
                    "has_project_md": project_md_exists,
                    "tags": tags,
                    "tagline": tagline,
                    "repair_status": _build_project_repair_status_payload(
                        row,
                        db_counters,
                        autonomous_repair_counts.get(row.id, {}),
                    ),
                }
            )
        # Sort: in-progress first (most recently updated), then completed
        results.sort(
            key=lambda r: (
                0
                if str(r.get("status") or "").lower()
                in {"running", "in_progress", "queued", "draft"}
                else 1,
                -1
                * (
                    datetime.fromisoformat(r["last_updated"]).timestamp()
                    if r.get("last_updated")
                    else 0
                ),
            )
        )
        return results


async def _load_project_chapter_state_counts(
    session: Any,
    project_ids: list[UUID],
) -> dict[UUID, list[dict[str, object]]]:
    if not project_ids:
        return {}
    from sqlalchemy import func, select

    from bestseller.infra.db.models import ChapterModel

    rows = await session.execute(
        select(
            ChapterModel.project_id,
            ChapterModel.status,
            ChapterModel.production_state,
            func.count(),
        )
        .where(ChapterModel.project_id.in_(project_ids))
        .group_by(
            ChapterModel.project_id,
            ChapterModel.status,
            ChapterModel.production_state,
        )
    )
    grouped: dict[UUID, list[dict[str, object]]] = {}
    for project_id, status, production_state, count in rows:
        grouped.setdefault(project_id, []).append(
            {
                "status": status,
                "production_state": production_state,
                "count": int(count or 0),
            }
        )
    return grouped


async def _load_project_autonomous_repair_task_counts(
    session: Any,
    project_ids: list[UUID],
) -> dict[UUID, dict[str, int]]:
    if not project_ids:
        return {}
    from sqlalchemy import func, select

    from bestseller.infra.db.models import RewriteTaskModel
    from bestseller.services.autonomous_book_repair import AUTONOMOUS_REPAIR_TRIGGER

    from bestseller.infra.db.models import ChapterModel

    result = await session.execute(
        select(
            RewriteTaskModel.project_id,
            RewriteTaskModel.status,
            RewriteTaskModel.error_log,
            RewriteTaskModel.metadata_json,
            ChapterModel.production_state,
        )
        .outerjoin(ChapterModel, ChapterModel.id == RewriteTaskModel.trigger_source_id)
        .where(
            RewriteTaskModel.project_id.in_(project_ids),
            RewriteTaskModel.trigger_type == AUTONOMOUS_REPAIR_TRIGGER,
        )
    )
    grouped: dict[UUID, dict[str, int]] = {}
    for project_id, status, error_log, metadata, chapter_production_state in result.all():
        bucket = grouped.setdefault(project_id, {})
        status_key = str(status or "unknown")
        if status_key == "failed" and _is_nonblocking_rejected_candidate(
            error_log=error_log,
            metadata=metadata,
            chapter_production_state=chapter_production_state,
        ):
            status_key = "rejected_candidate"
        bucket[status_key] = int(bucket.get(status_key, 0)) + 1
    return grouped


def _is_nonblocking_rejected_candidate(
    *,
    error_log: object,
    metadata: object,
    chapter_production_state: object,
) -> bool:
    """Classify protected rejected candidates separately from repair failures.

    A failed autonomous rewrite task can be a healthy safety outcome: the LLM
    produced a candidate, the quality gate rejected it, and the current draft
    stayed publishable. Those records should remain inspectable, but they must
    not make the whole book look terminally failed.
    """

    error_text = str(error_log or "")
    if "chapter rewrite rejected by quality gate" not in error_text:
        return False
    metadata_dict = metadata if isinstance(metadata, dict) else {}
    preserved = str(
        metadata_dict.get("preserved_current_quality_gate_outcome") or ""
    ).lower()
    production_state = str(chapter_production_state or "").lower()
    return preserved == "ok" or production_state == "ok"


def _build_db_repair_task_summary(
    project: Any,
    repair_status: dict[str, object],
) -> dict[str, object] | None:
    counts = repair_status.get("autonomous_repair_tasks")
    task_counts = counts if isinstance(counts, dict) else {}
    pending = _safe_int(task_counts.get("pending"))
    queued = _safe_int(task_counts.get("queued"))
    failed = _safe_int(task_counts.get("failed"))
    rejected = _safe_int(task_counts.get("rejected_candidate"))
    active_total = pending + queued + failed
    phase = str(repair_status.get("phase") or "")
    if active_total <= 0 and phase == "normal":
        return None

    slug = str(getattr(project, "slug", "") or "")
    if not slug:
        return None
    title = str(getattr(project, "title", "") or slug)
    if queued > 0:
        status = "queued"
        stage = "legacy_quality_closure_repair_queued"
        error = None
    elif pending > 0:
        status = "incomplete"
        stage = "legacy_quality_closure_repair_pending"
        error = None
    elif failed > 0:
        status = "failed"
        stage = "legacy_quality_closure_repair_failed"
        error = f"{failed} autonomous repair task(s) failed"
    else:
        status = "incomplete"
        stage = f"legacy_quality_closure_{phase or 'repair'}"
        error = str(repair_status.get("detail") or "") or None

    updated_at = getattr(project, "updated_at", None)
    created_at = getattr(project, "created_at", None) or updated_at
    updated_text = updated_at.isoformat() if isinstance(updated_at, datetime) else _utc_now()
    created_text = created_at.isoformat() if isinstance(created_at, datetime) else updated_text
    return {
        "task_id": f"db-repair:{slug}",
        "task_type": "repair",
        "status": status,
        "created_at": created_text,
        "updated_at": updated_text,
        "project_slug": slug,
        "title": title,
        "project_title": title,
        "current_stage": stage,
        "progress_events": [
            {
                "timestamp": updated_text,
                "stage": stage,
                "payload": {
                    "project_slug": slug,
                    "pending_autonomous_repair_tasks": pending,
                    "queued_autonomous_repair_tasks": queued,
                    "failed_autonomous_repair_tasks": failed,
                    "rejected_autonomous_repair_candidates": rejected,
                    "blocked_chapters": repair_status.get("blocked_chapters"),
                    "repair_phase": phase,
                },
            }
        ],
        "result": {
            "project_slug": slug,
            "target_chapters": _safe_int(getattr(project, "target_chapters", 0)),
            "pending_autonomous_repair_tasks": pending,
            "queued_autonomous_repair_tasks": queued,
            "failed_autonomous_repair_tasks": failed,
            "rejected_autonomous_repair_candidates": rejected,
        },
        "error": error,
        "payload": {
            "slug": slug,
            "title": title,
            "include_pending_rewrite_tasks": True,
        },
        "repair_status": repair_status,
        "synthetic_db_repair_task": True,
    }


async def _load_db_repair_task_summaries(
    settings: AppSettings,
    existing_tasks: list[dict[str, object]],
) -> list[dict[str, object]]:
    active_slugs = {
        str(task.get("project_slug") or "")
        for task in existing_tasks
        if str(task.get("project_slug") or "")
        and str(task.get("status") or "") in {"running", "queued"}
    }
    async with session_scope(settings) as session:
        rows = await list_projects(session)
        project_ids = [row.id for row in rows]
        repair_counts = await _load_project_chapter_state_counts(session, project_ids)
        autonomous_repair_counts = await _load_project_autonomous_repair_task_counts(
            session,
            project_ids,
        )
        repair_heal_owned_slugs = _fetch_heal_owned_slugs_by_kind(
            settings.redis.url,
            "repair",
        )
        summaries: list[dict[str, object]] = []
        for row in rows:
            if row.slug in active_slugs:
                continue
            repair_status = _build_project_repair_status_payload(
                row,
                repair_counts.get(row.id, []),
                autonomous_repair_counts.get(row.id, {}),
            )
            summary = _build_db_repair_task_summary(row, repair_status)
            if summary is not None:
                if row.slug in repair_heal_owned_slugs:
                    job_id = f"repair:heal:{row.slug}"
                    summary["worker_job_id"] = job_id
                    worker_progress = _load_worker_heal_progress_snapshot(
                        settings.redis.url,
                        job_id,
                    )
                    if worker_progress:
                        _merge_worker_progress_into_db_repair_summary(
                            summary,
                            worker_progress,
                        )
                    else:
                        summary["status"] = "queued"
                        summary["current_stage"] = "delegated_to_worker_self_heal"
                summaries.append(summary)
        return summaries


async def _load_db_repair_task_summary(
    settings: AppSettings,
    task_id: str,
) -> dict[str, object] | None:
    if not task_id.startswith("db-repair:"):
        return None
    summaries = await _load_db_repair_task_summaries(settings, [])
    for summary in summaries:
        if summary.get("task_id") == task_id:
            return summary
    return None


def _safe_int(value: object, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


_STRUCTURAL_REPAIR_BLOCK_FLAGS: frozenset[str] = frozenset(
    {
        "generation_resume_blocked_until_repair_audit",
        "production_paused",
        "structural_repair_required",
    }
)
_GENERATION_GATE_BLOCK_FLAGS: frozenset[str] = frozenset(
    {
        "generation_resume_blocked_by_planning_gate",
        "generation_auto_repair_exhausted",
    }
)


def _format_project_repair_pause_error(
    project_slug: str,
    reason: object | None = None,
    *,
    raw_detail: str | None = None,
) -> str:
    reason_text = str(reason or "structural repair is required")
    message = (
        f"项目 '{project_slug}' 当前处于结构修复暂停状态，不能继续自动写作。"
        f"原因：{reason_text}。请先运行修复流程并通过修复审计；审计通过后系统会解除 "
        "generation_resume_blocked_until_repair_audit 门控。"
    )
    if raw_detail:
        message = f"{message} 原始错误：{raw_detail}"
    return message


def _format_generation_gate_pause_error(
    project_slug: str,
    reason: object | None = None,
    *,
    raw_detail: str | None = None,
) -> str:
    reason_text = str(reason or "planning gate auto-repair exhausted")
    message = (
        f"项目 '{project_slug}' 当前被规划/世界观门禁暂停。"
        f"原因：{reason_text}。系统已完成自动回炉尝试但仍未闭合，"
        "后续恢复会携带该诊断重新进入自动修复链路。"
    )
    if raw_detail:
        message = f"{message} 原始错误：{raw_detail}"
    return message


def _project_autowrite_block_payload(project: Any) -> dict[str, object] | None:
    metadata = getattr(project, "metadata_json", None) or {}
    if not isinstance(metadata, dict):
        metadata = {}
    structural_blocked = any(bool(metadata.get(flag)) for flag in _STRUCTURAL_REPAIR_BLOCK_FLAGS)
    generation_gate_blocked = any(
        bool(metadata.get(flag)) for flag in _GENERATION_GATE_BLOCK_FLAGS
    )
    if not structural_blocked and not generation_gate_blocked:
        return None

    slug = str(getattr(project, "slug", "") or "")
    reason = (
        metadata.get("production_pause_reason")
        or metadata.get("repair_pause_reason")
        or metadata.get("rescue_status")
        or "structural_repair_required"
    )
    if generation_gate_blocked:
        detail = metadata.get("last_generation_gate_error")
        return {
            "ok": False,
            "blocked_generation_gate": True,
            "project_slug": slug,
            "current_stage": "blocked_generation_gate",
            "reason": reason,
            "error": _format_generation_gate_pause_error(
                slug,
                reason,
                raw_detail=detail if isinstance(detail, str) else None,
            ),
            "next_action": "resume_with_generation_gate_diagnostics",
        }
    return {
        "ok": False,
        "blocked_structural_repair": True,
        "project_slug": slug,
        "current_stage": "blocked_structural_repair",
        "reason": reason,
        "error": _format_project_repair_pause_error(slug, reason),
        "next_action": "run_repair_workflow_and_wait_for_repair_audit",
    }


def _build_project_repair_status_payload(
    project: Any,
    chapter_state_counts: list[dict[str, object]],
    autonomous_repair_task_counts: dict[str, int] | None = None,
) -> dict[str, object]:
    metadata = getattr(project, "metadata_json", None) or {}
    if not isinstance(metadata, dict):
        metadata = {}

    total_chapters = sum(_safe_int(item.get("count")) for item in chapter_state_counts)
    blocked_chapters = sum(
        _safe_int(item.get("count"))
        for item in chapter_state_counts
        if str(item.get("production_state") or "") == "blocked"
    )
    complete_ok_chapters = sum(
        _safe_int(item.get("count"))
        for item in chapter_state_counts
        if str(item.get("status") or "") == "complete"
        and str(item.get("production_state") or "") == "ok"
    )
    revision_chapters = sum(
        _safe_int(item.get("count"))
        for item in chapter_state_counts
        if str(item.get("status") or "") == "revision"
    )
    pending_chapters = sum(
        _safe_int(item.get("count"))
        for item in chapter_state_counts
        if str(item.get("production_state") or "") == "pending"
        or str(item.get("status") or "") in {"drafting", "planned", "review"}
    )
    task_counts = {
        str(status): _safe_int(count)
        for status, count in (autonomous_repair_task_counts or {}).items()
    }
    autonomous_total = sum(task_counts.values())
    autonomous_pending = _safe_int(task_counts.get("pending")) + _safe_int(
        task_counts.get("queued")
    )
    autonomous_failed = _safe_int(task_counts.get("failed"))
    autonomous_rejected = _safe_int(task_counts.get("rejected_candidate"))

    initial_repair_scope = _safe_int(
        metadata.get("repair_audit_out_of_range_chapters")
        or metadata.get("repair_audit_blocked_chapters")
        or metadata.get("repair_scope_total")
    )
    repair_scope_total = initial_repair_scope if initial_repair_scope > 0 else blocked_chapters
    repair_remaining = (
        min(blocked_chapters, repair_scope_total) if repair_scope_total > 0 else blocked_chapters
    )
    repair_completed = max(repair_scope_total - repair_remaining, 0)
    progress_percent = (
        round((repair_completed / repair_scope_total) * 100, 1) if repair_scope_total > 0 else None
    )

    production_paused = bool(metadata.get("production_paused"))
    resume_blocked = bool(metadata.get("generation_resume_blocked_until_repair_audit"))
    generation_gate_blocked = any(
        bool(metadata.get(flag)) for flag in _GENERATION_GATE_BLOCK_FLAGS
    )
    structural_repair_required = bool(metadata.get("structural_repair_required"))
    project_status = str(getattr(project, "status", "") or "")

    if generation_gate_blocked:
        phase = "planning_gate"
        label = "规划自动修复耗尽"
        detail = "规划/世界观门禁未闭合，恢复时会携带诊断重新进入自动修复链路。"
    elif production_paused or resume_blocked:
        phase = "repair_gate"
        label = "修复门控中"
        detail = "项目已暂停继续生产，需先完成阻塞章节修复并通过审计。"
    elif structural_repair_required:
        phase = "repair_required"
        label = "需要结构修复"
        detail = "项目被标记为需要结构修复，继续写作前应先处理阻塞项。"
    elif blocked_chapters > 0:
        phase = "needs_attention"
        label = "存在阻塞章节"
        detail = "仍有章节没有通过生产门禁。"
    elif autonomous_pending > 0:
        phase = "repair_gate"
        label = "闭环修复待执行"
        detail = "已有章节存在新闭环修复任务，需先执行修复并复评。"
    elif autonomous_failed > 0:
        phase = "needs_attention"
        label = "闭环修复需复查"
        detail = "部分闭环修复任务未通过，需要携带失败反馈进入下一轮修复。"
    else:
        phase = "normal"
        label = "正常"
        detail = "当前没有检测到修复门控。"

    is_repairing = phase != "normal" or project_status == "paused"
    return {
        "phase": phase,
        "label": label,
        "detail": detail,
        "is_repairing": is_repairing,
        "production_paused": production_paused,
        "resume_blocked_until_repair_audit": resume_blocked,
        "structural_repair_required": structural_repair_required,
        "total_chapters": total_chapters,
        "repair_scope_total": repair_scope_total,
        "repair_completed": repair_completed,
        "repair_remaining": repair_remaining,
        "progress_percent": progress_percent,
        "blocked_chapters": blocked_chapters,
        "complete_ok_chapters": complete_ok_chapters,
        "revision_chapters": revision_chapters,
        "pending_chapters": pending_chapters,
        "initial_out_of_range_chapters": initial_repair_scope,
        "chapter_state_counts": chapter_state_counts,
        "autonomous_repair_tasks": {
            **task_counts,
            "total": autonomous_total,
            "pending_or_queued": autonomous_pending,
        },
        "pending_autonomous_repair_tasks": autonomous_pending,
        "failed_autonomous_repair_tasks": autonomous_failed,
        "rejected_autonomous_repair_candidates": autonomous_rejected,
    }


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
        repair_counts = await _load_project_chapter_state_counts(session, [project.id])
        autonomous_repair_counts = await _load_project_autonomous_repair_task_counts(
            session,
            [project.id],
        )
        repair_status = _build_project_repair_status_payload(
            project,
            repair_counts.get(project.id, []),
            autonomous_repair_counts.get(project.id, {}),
        )
    outputs = collect_project_artifact_entries(settings, project_slug)
    markdown_entries = [item for item in outputs if str(item["suffix"]) == ".md"]
    default_preview_entry = next(
        (item for item in markdown_entries if item["name"] == "project.md"), None
    ) or (markdown_entries[0] if markdown_entries else None)
    project_markdown_entry = next(
        (item for item in markdown_entries if item["name"] == "project.md"), None
    )
    chapter_markdown_entries = [
        item for item in markdown_entries if str(item["name"]).startswith("chapter-")
    ]
    world_expansion = _resolve_story_bible_progress(
        story_bible,
        current_chapter_number=int(project.current_chapter_number or 0),
    )
    project_meta = project.metadata_json or {}
    listing_profile = build_book_listing_profile(
        project=project,
        writing_profile=writing_profile,
        story_bible=story_bible,
        output_base_dir=settings.output.base_dir,
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
            "synopsis": project_meta.get("synopsis", ""),
            "tags": project_meta.get("tags", []),
            "premise": project_meta.get("premise", ""),
        },
        "listing_profile": listing_profile,
        "repair_status": repair_status,
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
            "project_word_count": int(project_markdown_entry["word_count"])
            if project_markdown_entry
            else 0,
            "chapter_word_count_total": sum(
                int(item.get("word_count") or 0) for item in chapter_markdown_entries
            ),
            "default_preview_name": str(default_preview_entry["name"])
            if default_preview_entry
            else None,
            "default_preview_word_count": int(default_preview_entry["word_count"])
            if default_preview_entry
            else 0,
            "default_preview_estimated_read_minutes": int(
                default_preview_entry["estimated_read_minutes"]
            )
            if default_preview_entry
            else 0,
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


async def _load_project_listing_payload(
    settings: AppSettings,
    project_slug: str,
) -> dict[str, object]:
    async with session_scope(settings) as session:
        project = await get_project_by_slug(session, project_slug)
        if project is None:
            raise ValueError(f"Project '{project_slug}' was not found.")
        style_guide = await session.get(StyleGuideModel, project.id)
        writing_profile = get_project_writing_profile(project, style_guide).model_dump(mode="json")
        story_bible = await build_story_bible_overview(session, project_slug)
    listing = build_book_listing_profile(
        project=project,
        writing_profile=writing_profile,
        story_bible=story_bible,
        output_base_dir=settings.output.base_dir,
    )
    return {
        "listing_profile": listing,
        "project": {
            "slug": project.slug,
            "title": project.title,
            "genre": project.genre,
            "sub_genre": project.sub_genre,
            "audience": project.audience,
            "status": project.status,
            "target_word_count": project.target_word_count,
            "target_chapters": project.target_chapters,
            "tags": (project.metadata_json or {}).get("tags", []),
        },
    }


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


_LISTING_REGENERATE_MODULES: dict[str, dict[str, object]] = {
    "logline": {
        "label": "一句话钩子",
        "kind": "text",
        "max_chars": 80,
        "system": (
            "你是网文编辑，擅长写一句话钩子。给出一句不超过 60 字的中文钩子，"
            "强调主角的反差、矛盾或代价感。只输出钩子本身，不要任何说明。"
        ),
    },
    "short_intro": {
        "label": "短简介",
        "kind": "text",
        "max_chars": 220,
        "system": (
            "你是网文上架编辑。请用 150-200 字写一段短简介，覆盖：主角身份、"
            "金手指、初始冲突、读者钩子。只输出简介正文，不要标题或解释。"
        ),
    },
    "shelf_intro": {
        "label": "上架简介（500 字）",
        "kind": "text",
        "max_chars": 520,
        "system": (
            "你是网文上架编辑。请用 380-480 字写一段上架简介。要求：开头给冲突、"
            "中段铺设定、结尾给追读钩子。语言克制不堆砌形容词。只输出简介正文。"
        ),
    },
    "promo_copy": {
        "label": "宣传文案 × 3",
        "kind": "json_list",
        "system": (
            "你是网文广告文案。请输出三条宣传文案：① 钩子型（适合首发广告）"
            "② 人物型（适合书友社区）③ 爽点型（适合短视频）。"
            '返回 JSON 数组，每个元素是一条不超过 120 字的中文文案。例：["...", "...", "..."]。'
            "只输出 JSON，不要其他解释。"
        ),
    },
    "tags": {
        "label": "标签",
        "kind": "json_list",
        "system": (
            "你是网文上架编辑。请输出 12-18 个适合的标签词（每个 2-6 字，"
            '覆盖题材、人设、爽点、调性）。返回 JSON 数组，例：["末日","重生"]。'
            "只输出 JSON，不要其他解释。"
        ),
    },
    "title_candidates": {
        "label": "候选书名",
        "kind": "json_titles",
        "system": (
            "候选书名由平台化书名工作流确定性生成。"
            "此模块不会调用大模型生成覆盖标题。"
        ),
    },
    "reader_promise": {
        "label": "读者承诺",
        "kind": "json_list",
        "system": (
            "你是网文上架编辑。请输出 5 条读者承诺，每条说明一个核心爽点或定位。"
            "返回 JSON 数组，每个元素是一句不超过 50 字的中文。只输出 JSON。"
        ),
    },
    "recommended_subtitle": {
        "label": "推荐副标题",
        "kind": "text",
        "max_chars": 40,
        "system": ("你是网文编辑。给出一个 8-22 字的副标题，强化主推卖点。只输出副标题本身。"),
    },
}


def _build_listing_regenerate_user_prompt(
    *,
    module: str,
    project: Any,
    listing: dict[str, Any],
) -> str:
    title = listing.get("primary_title") or getattr(project, "title", None) or "未命名作品"
    genre = listing.get("primary_category") or getattr(project, "genre", None) or "—"
    sub_genre = listing.get("secondary_category") or getattr(project, "sub_genre", None) or ""
    logline = listing.get("logline") or ""
    short_intro = listing.get("short_intro") or ""
    tags = listing.get("tags") or []
    characters = listing.get("main_characters") or []
    char_summary = (
        "、".join(
            f"{c.get('name', '')}（{c.get('role') or '主要角色'}）"
            for c in characters[:4]
            if isinstance(c, dict) and c.get("name")
        )
        or "—"
    )
    return (
        f"书名：《{title}》\n"
        f"题材：{genre}{(' / ' + sub_genre) if sub_genre else ''}\n"
        f"现有钩子：{logline}\n"
        f"现有简介摘要：{(short_intro or '—')[:240]}\n"
        f"主要标签：{('、'.join(tags[:8])) if tags else '—'}\n"
        f"主要角色：{char_summary}\n\n"
        f"请基于以上信息，重新生成「{_LISTING_REGENERATE_MODULES[module]['label']}」。"
    )


async def _regenerate_listing_module(
    settings: AppSettings,
    project_slug: str,
    module: str,
) -> dict[str, object]:
    """Regenerate a single listing module via LLM, persist as override.

    Strategy:
      1. Build the *current* listing profile (so the prompt has full context).
      2. Call ``complete_text`` (logical_role='editor') with module-specific
         system + user prompts.
      3. Parse the response per ``kind`` (text / json_list / json_titles).
      4. Merge into ``output/<slug>/listing/book-listing-metadata.json`` so the
         next ``build_book_listing_profile`` call uses the new value as an
         override.  If the user never clicks regenerate, this file isn't
         touched and the original generated content keeps showing.
    """
    if module not in _LISTING_REGENERATE_MODULES:
        raise ValueError(f"unknown listing module: {module}")
    spec = _LISTING_REGENERATE_MODULES[module]

    if module == "title_candidates":
        from bestseller.services.book_listing import write_platform_title_workflow_artifacts

        async with session_scope(settings) as session:
            project = await get_project_by_slug(session, project_slug)
            if project is None:
                raise ValueError(f"project '{project_slug}' not found")
            style_guide = await session.get(StyleGuideModel, project.id)
            writing_profile = get_project_writing_profile(project, style_guide).model_dump(
                mode="json"
            )
            story_bible = await build_story_bible_overview(session, project_slug)
            listing_profile = build_book_listing_profile(
                project=project,
                writing_profile=writing_profile,
                story_bible=story_bible,
                output_base_dir=settings.output.base_dir,
            )
        listing_dir = Path(settings.output.base_dir).resolve() / project_slug / "listing"
        write_platform_title_workflow_artifacts(listing_profile, listing_dir)
        return {
            "module": module,
            "label": spec["label"],
            "value": listing_profile.get("title_candidates", []),
            "model": "platform-title-workflow",
            "input_tokens": 0,
            "output_tokens": 0,
            "listing_profile": listing_profile,
        }

    # Helper to import only when called (keeps import graph minimal).
    from bestseller.services.llm import (
        LLMCompletionRequest,
        complete_text,
    )

    async with session_scope(settings) as session:
        project = await get_project_by_slug(session, project_slug)
        if project is None:
            raise ValueError(f"project '{project_slug}' not found")
        style_guide = await session.get(StyleGuideModel, project.id)
        writing_profile = get_project_writing_profile(project, style_guide).model_dump(mode="json")
        story_bible = await build_story_bible_overview(session, project_slug)
        listing_before = build_book_listing_profile(
            project=project,
            writing_profile=writing_profile,
            story_bible=story_bible,
            output_base_dir=settings.output.base_dir,
        )

        user_prompt = _build_listing_regenerate_user_prompt(
            module=module,
            project=project,
            listing=listing_before,
        )
        request = LLMCompletionRequest(
            logical_role="editor",
            system_prompt=str(spec["system"]),
            user_prompt=user_prompt,
            fallback_response="REGEN_FAILED",
            prompt_template=f"listing.regenerate.{module}",
            prompt_version="v1",
            project_id=project.id,
            metadata={"listing_module": module, "project_slug": project_slug},
            max_tokens_override=900,
        )
        result = await complete_text(session, settings, request)

    raw = (result.content or "").strip()
    if not raw or raw == "REGEN_FAILED":
        raise RuntimeError("LLM returned empty content")

    # ── Parse per module kind ───────────────────────────────────────────
    kind = str(spec["kind"])

    def _parse_listing_module(raw_value: str) -> object:
        if kind == "text":
            text = raw_value.strip().strip("\"'")
            max_chars = int(spec.get("max_chars") or 0)
            if max_chars and len(text) > max_chars:
                text = text[: max_chars - 1] + "…"
            return text
        if kind == "json_list":
            return _parse_json_list_response(raw_value)
        if kind == "json_titles":
            return _parse_json_titles_response(raw_value)
        raise RuntimeError(f"unknown module kind: {kind}")

    try:
        parsed_value = _parse_listing_module(raw)
    except Exception as exc:
        from bestseller.services.llm_closed_loop import build_repair_user_prompt, findings_from_exception

        findings = findings_from_exception(exc, default_path=f"listing.{module}")
        repair_request = request.model_copy(
            update={
                "user_prompt": build_repair_user_prompt(
                    original_user_prompt=user_prompt,
                    findings=findings,
                    language=getattr(project, "language", None),
                ),
                "prompt_template": f"listing.regenerate.{module}.repair",
                "metadata": {
                    "listing_module": module,
                    "project_slug": project_slug,
                    "semantic_repair_of": str(result.llm_run_id) if result.llm_run_id else None,
                    "repair_findings": [finding.to_dict() for finding in findings],
                },
            }
        )
        async with session_scope(settings) as repair_session:
            repair_result = await complete_text(repair_session, settings, repair_request)
        repair_raw = (repair_result.content or "").strip()
        if not repair_raw or repair_raw == "REGEN_FAILED":
            raise RuntimeError("LLM listing repair returned empty content") from exc
        try:
            parsed_value = _parse_listing_module(repair_raw)
            raw = repair_raw
        except Exception as repair_exc:
            raise RuntimeError(
                f"LLM listing repair failed validation for module '{module}'"
            ) from repair_exc

    # ── Persist as file override ────────────────────────────────────────
    listing_dir = Path(settings.output.base_dir).resolve() / project_slug / "listing"
    listing_dir.mkdir(parents=True, exist_ok=True)
    metadata_path = listing_dir / "book-listing-metadata.json"
    existing: dict[str, Any] = {}
    if metadata_path.exists():
        try:
            existing = json.loads(metadata_path.read_text(encoding="utf-8")) or {}
        except (OSError, ValueError):
            existing = {}
    if not isinstance(existing, dict):
        existing = {}
    existing[module] = parsed_value
    history = existing.setdefault("_regenerate_history", {})
    if isinstance(history, dict):
        history[module] = {
            "regenerated_at": datetime.now(tz=UTC).isoformat(),
            "model": result.model_name,
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
        }
    metadata_path.write_text(
        json.dumps(existing, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # ── Return fresh listing for the UI to swap in ──────────────────────
    async with session_scope(settings) as session:
        project = await get_project_by_slug(session, project_slug)
        if project is None:
            raise ValueError(f"project '{project_slug}' not found after regen")
        style_guide = await session.get(StyleGuideModel, project.id)
        writing_profile = get_project_writing_profile(project, style_guide).model_dump(mode="json")
        story_bible = await build_story_bible_overview(session, project_slug)
    listing_after = build_book_listing_profile(
        project=project,
        writing_profile=writing_profile,
        story_bible=story_bible,
        output_base_dir=settings.output.base_dir,
    )
    return {
        "module": module,
        "label": spec["label"],
        "value": parsed_value,
        "model": result.model_name,
        "input_tokens": result.input_tokens,
        "output_tokens": result.output_tokens,
        "listing_profile": listing_after,
    }


def _parse_json_list_response(raw: str) -> list[str]:
    text = _strip_code_fences(raw)
    try:
        data = json.loads(text)
    except ValueError:
        # Fall back to line-split if model returned plain lines.
        lines = [_strip_bullet(line).strip() for line in raw.splitlines()]
        return [line for line in lines if line][:20]
    if isinstance(data, list):
        return [str(item).strip() for item in data if item]
    if isinstance(data, dict):
        for key in ("items", "values", "tags", "list"):
            if isinstance(data.get(key), list):
                return [str(item).strip() for item in data[key] if item]
    return []


def _parse_json_titles_response(raw: str) -> list[dict[str, str]]:
    text = _strip_code_fences(raw)
    try:
        data = json.loads(text)
    except ValueError:
        return []
    if not isinstance(data, list):
        return []
    out: list[dict[str, str]] = []
    for index, item in enumerate(data, start=1):
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        out.append(
            {
                "id": int(item.get("id") or index),
                "title": title,
                "subtitle": str(item.get("subtitle") or "").strip(),
                "angle": str(item.get("angle") or "").strip(),
                "recommendation": str(item.get("recommendation") or "").strip(),
            }
        )
    return out[:20]


def _strip_code_fences(text: str) -> str:
    s = text.strip()
    if s.startswith("```"):
        # Drop opening fence (with optional language) and closing fence.
        s = s.split("\n", 1)[1] if "\n" in s else s
        if s.endswith("```"):
            s = s[:-3]
    return s.strip()


def _strip_bullet(line: str) -> str:
    s = line.strip()
    for prefix in ("- ", "• ", "* "):
        if s.startswith(prefix):
            return s[len(prefix) :]
    # Drop "1. " / "①" / etc.
    if len(s) > 2 and s[0].isdigit() and s[1] in {".", "、", ")", "."}:
        return s[2:].strip()
    return s


_BOOK_SCHEDULE_CHANNEL = "bestseller:book_schedule:events"


def _serialize_schedule_row(row: object) -> dict[str, object]:
    """Convert a ``BookGenerationScheduleModel`` to a JSON-friendly dict."""
    scheduled_at = getattr(row, "scheduled_at", None)
    fired_at = getattr(row, "fired_at", None)
    created_at = getattr(row, "created_at", None)
    return {
        "id": str(getattr(row, "id", "")),
        "task_type": getattr(row, "task_type", None),
        "project_slug": getattr(row, "project_slug", None),
        "title": getattr(row, "title", None),
        "scheduled_at": scheduled_at.isoformat() if scheduled_at else None,
        "timezone": getattr(row, "timezone", None),
        "status": getattr(row, "status", None),
        "task_id": getattr(row, "task_id", None),
        "fired_at": fired_at.isoformat() if fired_at else None,
        "error_message": getattr(row, "error_message", None),
        "requested_by": getattr(row, "requested_by", None),
        "created_at": created_at.isoformat() if created_at else None,
        "payload": getattr(row, "payload", None),
    }


def _parse_scheduled_at(raw: object, schedule_timezone: str) -> datetime:
    """Parse a user-supplied scheduled_at value into a timezone-aware UTC datetime.

    Accepted forms:
      - ISO 8601 string with offset, e.g. ``2026-04-30T22:00:00+08:00``.
      - ISO 8601 string without offset (interpreted in ``schedule_timezone``).
    """
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("scheduled_at must be a non-empty ISO 8601 string")
    text = raw.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"scheduled_at is not a valid ISO 8601 datetime: {exc}") from exc
    if parsed.tzinfo is None:
        try:
            from zoneinfo import ZoneInfo

            tz = ZoneInfo(schedule_timezone)
        except Exception as exc:
            raise ValueError(f"Unknown timezone {schedule_timezone!r}: {exc}") from exc
        parsed = parsed.replace(tzinfo=tz)
    return parsed.astimezone(UTC)


def _publish_book_schedule_event(settings: AppSettings, event: dict[str, object]) -> None:
    """Best-effort pubsub notification to the scheduler container.

    Failures are logged but never raised — the scheduler also polls
    pending rows on startup and periodically, so a missed pubsub event
    only delays job registration until the next poll.
    """
    try:
        import redis as _redis

        client = _redis.from_url(settings.redis.url, decode_responses=True, socket_timeout=2)
        client.publish(_BOOK_SCHEDULE_CHANNEL, json.dumps(event))
    except Exception:
        logging.getLogger(__name__).warning(
            "Failed to publish book schedule event %s",
            event,
            exc_info=True,
        )


def _create_book_generation_schedule(
    settings: AppSettings,
    *,
    task_type: str,
    payload: dict[str, object],
) -> dict[str, object]:
    from bestseller.services.book_generation_schedules import (
        create_schedule,
    )

    if not isinstance(payload, dict):
        raise ValueError("Request body must be a JSON object")
    raw_scheduled_at = payload.get("scheduled_at")
    if not raw_scheduled_at:
        raise ValueError("Field 'scheduled_at' is required for scheduled tasks")
    schedule_timezone = str(payload.get("timezone") or "Asia/Shanghai")
    scheduled_at = _parse_scheduled_at(raw_scheduled_at, schedule_timezone)

    inner_payload: dict[str, object] = {
        k: v for k, v in payload.items() if k not in {"scheduled_at", "timezone", "requested_by"}
    }

    if task_type == "autowrite":
        required = ["slug", "title", "genre", "target_words", "target_chapters", "premise"]
        missing = [k for k in required if not inner_payload.get(k)]
        if missing:
            raise ValueError(f"Missing required fields: {', '.join(missing)}")
        validate_longform_scope(
            int(inner_payload["target_words"]),
            int(inner_payload["target_chapters"]),
            language=inner_payload.get("language"),
        )
    elif task_type == "quickstart":
        if not inner_payload.get("genre_key"):
            raise ValueError("Field 'genre_key' is required.")
    else:
        raise ValueError(f"Unsupported task_type {task_type!r}")

    project_slug = (
        (str(inner_payload.get("slug") or "") or None) if task_type == "autowrite" else None
    )
    title = (
        (str(inner_payload.get("title") or "") or None)
        if task_type == "autowrite"
        else (str(inner_payload.get("genre_key") or "") or None)
    )
    requested_by = str(payload.get("requested_by") or "").strip() or None

    async def _do_create() -> dict[str, object]:
        async with session_scope(settings) as session:
            row = await create_schedule(
                session,
                task_type=task_type,
                scheduled_at=scheduled_at,
                payload=inner_payload,
                project_slug=project_slug,
                title=title,
                requested_by=requested_by,
                schedule_timezone=schedule_timezone,
            )
            return _serialize_schedule_row(row)

    result = asyncio.run(_do_create())
    _publish_book_schedule_event(
        settings,
        {"action": "create", "schedule_id": result["id"]},
    )
    return result


def _list_book_generation_schedules(
    settings: AppSettings,
    *,
    status_filter: str | None = None,
) -> list[dict[str, object]]:
    from bestseller.services.book_generation_schedules import (
        list_schedules,
    )

    async def _do_list() -> list[dict[str, object]]:
        async with session_scope(settings) as session:
            rows = await list_schedules(session, status_filter=status_filter)
            return [_serialize_schedule_row(r) for r in rows]

    return asyncio.run(_do_list())


def _get_book_generation_schedule(
    settings: AppSettings, schedule_id: str
) -> dict[str, object] | None:
    from bestseller.services.book_generation_schedules import (
        get_schedule,
    )

    try:
        sid = UUID(schedule_id)
    except ValueError:
        return None

    async def _do_get() -> dict[str, object] | None:
        async with session_scope(settings) as session:
            row = await get_schedule(session, sid)
            return _serialize_schedule_row(row) if row is not None else None

    return asyncio.run(_do_get())


def _cancel_book_generation_schedule(
    settings: AppSettings, schedule_id: str
) -> str | dict[str, object]:
    """Returns "not_found", "invalid_state: <msg>", or the serialized row."""
    from bestseller.services.book_generation_schedules import (
        cancel_schedule,
    )

    try:
        sid = UUID(schedule_id)
    except ValueError:
        return "not_found"

    async def _do_cancel() -> str | dict[str, object]:
        async with session_scope(settings) as session:
            try:
                row = await cancel_schedule(session, sid)
            except ValueError as exc:
                return f"invalid_state: {exc}"
            if row is None:
                return "not_found"
            return _serialize_schedule_row(row)

    outcome = asyncio.run(_do_cancel())
    if isinstance(outcome, dict):
        _publish_book_schedule_event(
            settings,
            {"action": "cancel", "schedule_id": schedule_id},
        )
    return outcome


def _read_reader_html() -> str:
    if _READER_HTML_PATH.exists():
        return _READER_HTML_PATH.read_text(encoding="utf-8")
    return "<!DOCTYPE html><html><body><h1>Reader not found.</h1></body></html>"


def _read_if_reader_html() -> str:
    if _IF_READER_HTML_PATH.exists():
        return _IF_READER_HTML_PATH.read_text(encoding="utf-8")
    return "<!DOCTYPE html><html><body><h1>IF Reader not found.</h1></body></html>"


def _read_quickstart_html() -> str:
    if _QUICKSTART_HTML_PATH.exists():
        return _QUICKSTART_HTML_PATH.read_text(encoding="utf-8")
    return "<!DOCTYPE html><html><body><h1>Quickstart page not found.</h1></body></html>"


def _read_library_html() -> str:
    if _LIBRARY_HTML_PATH.exists():
        return _LIBRARY_HTML_PATH.read_text(encoding="utf-8")
    return "<!DOCTYPE html><html><body><h1>Library page not found.</h1></body></html>"


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
            results.append(
                {
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
                }
            )
        except (OSError, json.JSONDecodeError):
            continue
    return results


def _build_chapter_toc(output_dir: Path) -> list[dict[str, object]]:
    """Scan chapter-NNN.md files and extract titles for TOC."""
    import re as _re

    entries: list[dict[str, object]] = []
    for p in sorted(output_dir.glob("chapter-*.md")):
        content_md = ""
        first_line = ""
        try:
            with p.open(encoding="utf-8") as f:
                for line in f:
                    content_md += line
                    stripped = line.strip()
                    if stripped:
                        first_line = stripped
                        break
                remainder = f.read()
                if remainder:
                    content_md += remainder
        except OSError:
            continue
        # Strip H1 marker, then remove leading duplicate "第N章 " prefix
        raw = first_line.lstrip("# ").strip() if first_line.startswith("#") else p.stem
        # "第1章 第1章：追线" → "第1章：追线"
        title = _re.sub(r"^第\d+章(?:[：:\s]+)?", "", raw).strip() or raw
        stats = build_markdown_reading_stats(content_md)
        try:
            num = int(p.stem.split("-")[1])
        except (IndexError, ValueError):
            num = len(entries) + 1
        entries.append(
            {
                "number": num,
                "title": title,
                "filename": p.name,
                "word_count": stats["word_count"],
                "estimated_read_minutes": stats["estimated_read_minutes"],
            }
        )
    return entries


def _reader_chapter_availability(production_state: str | None, word_count: int) -> str:
    if (production_state or "").lower() == "ok" and word_count > 0:
        return "available"
    if word_count > 0:
        return "repair_in_progress"
    return "planned"


def _load_project_chapter_index(
    settings: AppSettings,
    project_slug: str,
) -> tuple[
    list[dict[str, object]],  # chapter rows from DB (authoritative list)
    dict[int, dict[str, object]],  # chapter_number → volume summary
    list[dict[str, object]],  # ordered volume list
]:
    """Return the DB-authoritative chapter list plus volume metadata.

    Using DB as source of truth solves two problems in one shot:

    1. **Volume naming** — ``chapters.volume_id`` + ``volumes.title`` tell
       us which book each chapter belongs to; the filesystem scan in
       ``_build_chapter_toc`` has no way to know this.
    2. **Real-time sync** — a chapter draft is persisted to the DB the
       moment ``assemble_chapter_draft`` completes, well before the markdown
       export writes ``chapter-NNN.md`` to disk.  Scanning the filesystem
       under-reports the live chapter count (user reported "84 in task
       list, 50 in preview").  Reading from ``chapters`` + checking for
       the current ``chapter_draft_versions`` row keeps the reader in sync.

    Any DB error is swallowed and empty results returned — callers then
    fall back to the filesystem-only path.
    """
    try:
        from sqlalchemy import select

        from bestseller.infra.db.models import (
            ChapterDraftVersionModel,
            ChapterModel,
            VolumeModel,
        )

        async def _fetch() -> tuple[
            list[dict[str, object]],
            dict[int, dict[str, object]],
            list[dict[str, object]],
        ]:
            async with session_scope(settings) as sess:
                proj = await get_project_by_slug(sess, project_slug)
                if proj is None:
                    return [], {}, []
                volume_rows = list(
                    await sess.scalars(
                        select(VolumeModel)
                        .where(VolumeModel.project_id == proj.id)
                        .order_by(VolumeModel.volume_number)
                    )
                )
                volume_by_id = {v.id: v for v in volume_rows}

                chapter_rows = list(
                    await sess.scalars(
                        select(ChapterModel)
                        .where(ChapterModel.project_id == proj.id)
                        .order_by(ChapterModel.chapter_number)
                    )
                )

                # Look up which chapters have a current draft; we only
                # surface *draftable* chapters (a placeholder row with no
                # draft yet would confuse the reader).  Prefer the chapter
                # row's recalibrated current_word_count, with the current
                # draft row as a legacy fallback.
                draft_rows = list(
                    await sess.execute(
                        select(
                            ChapterDraftVersionModel.chapter_id,
                            ChapterDraftVersionModel.word_count,
                        ).where(
                            ChapterDraftVersionModel.project_id == proj.id,
                            ChapterDraftVersionModel.is_current.is_(True),
                        )
                    )
                )
                draft_word_count_by_chapter_id = {
                    chapter_id: int(word_count or 0) for chapter_id, word_count in draft_rows
                }

                chapters_out: list[dict[str, object]] = []
                volume_map: dict[int, dict[str, object]] = {}
                for ch in chapter_rows:
                    word_count = int(ch.current_word_count or 0)
                    if word_count <= 0:
                        word_count = draft_word_count_by_chapter_id.get(ch.id, 0)
                    has_draft = ch.id in draft_word_count_by_chapter_id
                    if not has_draft and word_count <= 0:
                        # Planned but not written yet — skip.  The user
                        # only wants readable chapters in the reader TOC.
                        continue
                    vol = volume_by_id.get(ch.volume_id) if ch.volume_id else None
                    vol_summary: dict[str, object] | None = None
                    if vol is not None:
                        vol_summary = {
                            "volume_number": vol.volume_number,
                            "volume_title": vol.title,
                        }
                        volume_map[ch.chapter_number] = vol_summary

                    chapter_status = str(ch.status or "").lower()
                    production_state = str(ch.production_state or "").lower()
                    revision_count = int(ch.revision_count or 0)

                    # The reader intentionally exposes only two user-facing
                    # states: production_state=ok means "正式版"; any other
                    # readable draft is still "修复中".  A chapter can remain
                    # status=revision after a repair pass, so production_state
                    # is the authoritative gate result here.
                    availability = _reader_chapter_availability(
                        production_state,
                        word_count,
                    )

                    chapters_out.append(
                        {
                            "number": ch.chapter_number,
                            "title": ch.title or f"第{ch.chapter_number}章",
                            "word_count": word_count,
                            "estimated_read_minutes": (
                                (word_count + 499) // 500 if word_count > 0 else 0
                            ),
                            "volume_number": vol_summary["volume_number"] if vol_summary else None,
                            "volume_title": vol_summary["volume_title"] if vol_summary else None,
                            "status": chapter_status,
                            "production_state": production_state,
                            "revision_count": revision_count,
                            "has_draft": has_draft,
                            "availability": availability,
                        }
                    )

                volumes_out: list[dict[str, object]] = [
                    {
                        "volume_number": v.volume_number,
                        "title": v.title,
                        "theme": v.theme,
                        "target_chapter_count": v.target_chapter_count,
                    }
                    for v in volume_rows
                ]
                return chapters_out, volume_map, volumes_out

        return asyncio.run(_fetch())
    except Exception:
        return [], {}, []


def _load_task_chapter_word_stats(
    settings: AppSettings,
    project_slugs: list[str],
) -> dict[str, dict[str, object]]:
    """Return DB-authoritative chapter counts for quickstart task cards."""
    slugs = sorted({slug for slug in project_slugs if slug})
    if not slugs:
        return {}

    try:
        from sqlalchemy import select

        from bestseller.infra.db.models import (
            ChapterDraftVersionModel,
            ChapterModel,
            ProjectModel,
        )

        async def _fetch() -> dict[str, dict[str, object]]:
            async with session_scope(settings) as sess:
                project_rows = list(
                    await sess.scalars(select(ProjectModel).where(ProjectModel.slug.in_(slugs)))
                )
                if not project_rows:
                    return {}

                project_by_id = {project.id: project for project in project_rows}
                project_ids = list(project_by_id)
                stats_by_slug: dict[str, dict[str, object]] = {
                    project.slug: {
                        "source": "db",
                        "project_slug": project.slug,
                        "project_title": project.title,
                        "target_chapters": int(project.target_chapters or 0),
                        "target_word_count": int(project.target_word_count or 0),
                        "completed_chapters": 0,
                        "word_count_total": 0,
                        "chapters": [],
                    }
                    for project in project_rows
                }

                draft_rows = list(
                    await sess.execute(
                        select(
                            ChapterDraftVersionModel.chapter_id,
                            ChapterDraftVersionModel.word_count,
                        ).where(
                            ChapterDraftVersionModel.project_id.in_(project_ids),
                            ChapterDraftVersionModel.is_current.is_(True),
                        )
                    )
                )
                draft_word_count_by_chapter_id = {
                    chapter_id: int(word_count or 0) for chapter_id, word_count in draft_rows
                }

                chapter_rows = list(
                    await sess.scalars(
                        select(ChapterModel)
                        .where(ChapterModel.project_id.in_(project_ids))
                        .order_by(ChapterModel.project_id, ChapterModel.chapter_number)
                    )
                )
                for chapter in chapter_rows:
                    project = project_by_id.get(chapter.project_id)
                    if project is None:
                        continue
                    word_count = int(chapter.current_word_count or 0)
                    if word_count <= 0:
                        word_count = draft_word_count_by_chapter_id.get(chapter.id, 0)
                    if chapter.id not in draft_word_count_by_chapter_id and word_count <= 0:
                        continue

                    stats = stats_by_slug[project.slug]
                    chapter_payload = {
                        "number": int(chapter.chapter_number),
                        "title": chapter.title or f"第{chapter.chapter_number}章",
                        "word_count": word_count,
                        "estimated_read_minutes": (
                            (word_count + 499) // 500 if word_count > 0 else 0
                        ),
                        "target_word_count": int(chapter.target_word_count or 0),
                        "status": chapter.status,
                    }
                    stats["chapters"].append(chapter_payload)  # type: ignore[index]
                    stats["word_count_total"] = int(stats["word_count_total"]) + word_count

                for stats in stats_by_slug.values():
                    chapters = list(stats["chapters"])  # type: ignore[arg-type]
                    chapters.sort(key=lambda item: int(item.get("number") or 0))
                    stats["chapters"] = chapters
                    stats["completed_chapters"] = len(chapters)

                return stats_by_slug

        return asyncio.run(_fetch())
    except Exception:
        logger.warning("Failed to load quickstart chapter word stats", exc_info=True)
        return {}


def _attach_task_chapter_word_stats(
    settings: AppSettings,
    tasks: list[dict[str, object]],
) -> list[dict[str, object]]:
    stats_by_slug = _load_task_chapter_word_stats(
        settings,
        [str(task.get("project_slug") or "") for task in tasks],
    )
    if not stats_by_slug:
        return tasks
    for task in tasks:
        slug = str(task.get("project_slug") or "")
        if slug in stats_by_slug:
            stats = stats_by_slug[slug]
            task["chapter_word_stats"] = stats
            project_title = str(stats.get("project_title") or "").strip()
            task_title = str(task.get("title") or "").strip()
            if project_title and (
                task.get("task_type") == "repair"
                or not task_title
                or task_title.lower().startswith("repair ")
            ):
                task["title"] = project_title
                task["project_title"] = project_title
    return tasks


def serve_web_app(
    host: str = "127.0.0.1",
    port: int = 8787,
    *,
    open_browser: bool = False,
) -> None:
    settings = load_settings()
    tasks_persist_path = Path(settings.output.base_dir) / ".web_tasks.json"
    task_manager = WebTaskManager(persist_path=tasks_persist_path)

    # Run BOTH recovery passes inside a single ``asyncio.run()`` so they
    # share one event loop. Splitting them across two ``asyncio.run()``
    # calls used to crash the second one with ``Future attached to a
    # different loop`` because the legacy ``_shared_engine`` cache held
    # asyncpg connections from the first (now-dead) loop. ``session_scope``
    # is now one-shot per call, but consolidating here also avoids paying
    # the engine-creation cost twice.
    async def _run_startup_recovery() -> None:
        try:
            recovered = await _recover_projects_from_output(settings)
            if recovered:
                print(
                    f"Auto-recovered {len(recovered)} projects from output directory"
                )
        except Exception:
            logger.exception("Project recovery from output directory failed")

        # Recover task history from output directories (for quickstart
        # dashboard). Must run AFTER ``_recover_projects_from_output`` so
        # DB records exist and this pass can derive ``target_chapters`` and
        # a resumable payload from them.
        try:
            recovered_tasks = await task_manager._recover_tasks_from_output(settings)
            if recovered_tasks:
                print(
                    f"Auto-recovered {recovered_tasks} task(s) from output directory"
                )
        except Exception:
            logger.exception("Task recovery from output directory failed")

    try:
        asyncio.run(_run_startup_recovery())
    except Exception:
        logger.exception("Startup recovery sequence failed")

    # Auto-resume tasks that were running/queued when the server last shut
    # down.  ``_load_from_disk`` (run inside the constructor above) marked
    # every zombie with a rebuildable payload as ``queued`` and recorded its
    # ID; we now re-spawn workers for them so users do not lose a multi-hour
    # novel generation just because Docker restarted.  Gated by an env var in
    # case an operator wants to debug a hung task without it re-launching.
    _auto_resume_enabled = os.environ.get(
        "BESTSELLER_AUTO_RESUME_ON_STARTUP", "true"
    ).lower() not in ("0", "false", "no", "off")
    if _auto_resume_enabled:
        try:
            resumed_ids = task_manager.auto_resume_zombies(
                redis_url=settings.redis.url,
            )
            if resumed_ids:
                print(
                    f"Auto-resumed {len(resumed_ids)} zombie task(s) after "
                    f"server restart: {', '.join(tid[:8] for tid in resumed_ids)}"
                )
        except Exception:
            logger.exception("Zombie auto-resume failed")
    else:
        logger.info("Zombie auto-resume disabled via BESTSELLER_AUTO_RESUME_ON_STARTUP")

    # Background watchdog: detect hung tasks (no progress for >stale_seconds)
    stale_seconds = int(os.environ.get("BESTSELLER_TASK_STALE_SECONDS", "2700"))
    sweep_interval = int(os.environ.get("BESTSELLER_WATCHDOG_INTERVAL", "60"))

    # Auto-evict completed/failed tasks older than this (seconds).
    _TASK_RETENTION_SECONDS = int(os.environ.get("BESTSELLER_TASK_RETENTION_SECONDS", "86400"))

    def _watchdog_loop() -> None:
        import gc as _gc
        import time as _time

        sweep_count = 0
        while True:
            try:
                _time.sleep(sweep_interval)
                sweep_count += 1
                marked = task_manager.watchdog_sweep(stale_after_seconds=stale_seconds)
                if marked:
                    logger.warning("Watchdog marked %d stale task(s) as failed", marked)

                # Every 10 sweeps (~10 min): evict old finished tasks to cap memory
                if sweep_count % 10 == 0:
                    evicted = task_manager.evict_old_tasks(max_age_seconds=_TASK_RETENTION_SECONDS)
                    if evicted:
                        logger.info("Watchdog evicted %d old task record(s)", evicted)

                # Every 10 sweeps: clean up orphaned litellm httpx clients
                if sweep_count % 10 == 0:
                    try:
                        from bestseller.services.llm import (
                            _cleanup_stale_litellm_clients,
                        )

                        _cleanup_stale_litellm_clients()
                    except Exception:
                        pass

                # Every 5 sweeps (~5 min): run a full GC cycle then ask glibc
                # to return free arenas to the OS.  Python's allocator holds
                # freed memory in internal arenas indefinitely; after large LLM
                # response allocations the process RSS can grow by hundreds of
                # MB and never shrink on its own.  malloc_trim(0) triggers an
                # immediate trim — RSS drops back close to live-object size.
                # This only works when PYTHONMALLOC=malloc is set (configured
                # in docker-compose.yml); otherwise it's a safe no-op.
                if sweep_count % 5 == 0:
                    try:
                        collected = _gc.collect()
                        import ctypes as _ctypes

                        try:
                            _ctypes.CDLL("libc.so.6").malloc_trim(0)
                            logger.debug(
                                "Watchdog GC: collected %d objects, malloc_trim done",
                                collected,
                            )
                        except OSError:
                            # Not on glibc (e.g. musl/Alpine) — GC still ran.
                            logger.debug("Watchdog GC: collected %d objects", collected)
                    except Exception:
                        pass
            except Exception:
                logger.exception("Watchdog sweep failed")

    threading.Thread(target=_watchdog_loop, daemon=True, name="task-watchdog").start()

    # Background progress bridge: arq-worker containers emit progress events
    # into Redis, but the web process owns the ``task.current_stage`` that the
    # UI renders. Without this bridge, every self-healed / resumed task looked
    # frozen at whatever stage the web process last saw — most commonly
    # ``volume_planning_started``. Sync every few seconds so the dashboard
    # reflects real worker progress.
    _progress_sync_interval = int(os.environ.get("BESTSELLER_PROGRESS_SYNC_INTERVAL", "5"))

    def _progress_sync_loop() -> None:
        import time as _time

        redis_url = settings.redis.url
        while True:
            try:
                _time.sleep(_progress_sync_interval)
                task_manager.sync_progress_from_worker_redis(redis_url)
            except Exception:
                logger.exception("Worker-progress sync iteration failed")

    threading.Thread(
        target=_progress_sync_loop,
        daemon=True,
        name="worker-progress-sync",
    ).start()

    reader_html = _read_reader_html()
    if_reader_html = _read_if_reader_html()

    class RequestHandler(BaseHTTPRequestHandler):
        server_version = "BestSellerWeb/0.1"

        def log_message(self, format: str, *args: object) -> None:
            return

        def _send_json(self, payload: object, status: HTTPStatus = HTTPStatus.OK) -> None:
            body = json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default).encode(
                "utf-8"
            )
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
            if isinstance(exc, (ValueError, FileNotFoundError)):
                error_msg = str(exc)
            else:
                logging.getLogger(__name__).exception("Unhandled error in request handler")
                error_msg = "Internal server error."
            self._send_json(
                {
                    "error": error_msg,
                },
                status=HTTPStatus.BAD_REQUEST
                if isinstance(exc, ValueError)
                else HTTPStatus.INTERNAL_SERVER_ERROR,
            )

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            path = unquote(parsed.path)
            query = parse_qs(parsed.query)
            try:
                if path == "/" or path == "/quickstart":
                    # Legacy `/` (novel_studio) was retired — quickstart is the
                    # only first-party UI now. Serve quickstart at both paths
                    # so existing bookmarks and reader "返回工作台" links keep
                    # working without redirect chains.
                    self._send_text(
                        _read_quickstart_html(), content_type="text/html; charset=utf-8"
                    )
                    return
                if path == "/library":
                    self._send_text(_read_library_html(), content_type="text/html; charset=utf-8")
                    return
                if path == "/api/library":
                    self._send_json(asyncio.run(_load_library_payload(settings)))
                    return
                if path == "/api/status":
                    self._send_json(
                        {
                            "app": "BestSeller Web Studio",
                            "database_connected": bool(settings.database.url),
                            "llm_mock": settings.llm.mock,
                            "planner_model": settings.llm.planner.model,
                            "writer_model": settings.llm.writer.model,
                            "output_base_dir": str(Path(settings.output.base_dir).resolve()),
                            "max_concurrent_tasks": task_manager._max_concurrent_tasks,
                        }
                    )
                    return
                if path == "/api/projects":
                    self._send_json(asyncio.run(_load_projects_payload(settings)))
                    return
                if path == "/api/writing-presets":
                    self._send_json(_public_writing_preset_catalog_payload())
                    return
                if path == "/api/prompt-packs":
                    from bestseller.services.prompt_packs import list_prompt_packs

                    packs = list_prompt_packs()
                    self._send_json([p.model_dump(mode="json") for p in packs])
                    return
                if path == "/api/budget/estimate":
                    from bestseller.services.budget import estimate_project_cost

                    ch_count = int(query.get("chapters", ["30"])[0])
                    self._send_json(estimate_project_cost(ch_count, settings))
                    return
                if path == "/api/tasks":
                    tasks = task_manager.list_tasks()
                    db_repair_tasks = asyncio.run(
                        _load_db_repair_task_summaries(settings, tasks)
                    )
                    db_repair_slugs = {
                        str(task.get("project_slug") or "")
                        for task in db_repair_tasks
                        if task.get("synthetic_db_repair_task")
                    }
                    if db_repair_slugs:
                        tasks = [
                            task
                            for task in tasks
                            if not (
                                str(task.get("task_id") or "").startswith("recovered-")
                                and str(task.get("project_slug") or "") in db_repair_slugs
                                and str(task.get("status") or "")
                                in {"failed", "incomplete"}
                            )
                        ]
                    tasks.extend(db_repair_tasks)
                    self._send_json(
                        _attach_task_chapter_word_stats(settings, tasks)
                    )
                    return
                if path == "/api/schedules":
                    qs = parse_qs(parsed.query)
                    status_filter = (qs.get("status") or [None])[0]
                    self._send_json(
                        _list_book_generation_schedules(settings, status_filter=status_filter)
                    )
                    return
                if path.startswith("/api/schedules/"):
                    schedule_id = path.removeprefix("/api/schedules/").strip("/")
                    if not schedule_id:
                        raise ValueError("Missing schedule_id in path")
                    item = _get_book_generation_schedule(settings, schedule_id)
                    if item is None:
                        self._route_not_found()
                        return
                    self._send_json(item)
                    return
                if path.startswith("/api/tasks/"):
                    progress_detail = path.endswith("/progress")
                    task_id = path.removeprefix("/api/tasks/")
                    if progress_detail:
                        task_id = task_id.removesuffix("/progress")
                    task_id = task_id.strip("/")
                    task = task_manager.get_task(task_id)
                    if task is None:
                        task = _load_worker_task_summary(settings, task_id)
                    if task is None:
                        task = asyncio.run(
                            _load_db_repair_task_summary(settings, task_id)
                        )
                    if task is None:
                        self._route_not_found()
                        return
                    enriched_tasks = _attach_task_chapter_word_stats(settings, [task])
                    enriched_task = enriched_tasks[0] if enriched_tasks else task
                    if progress_detail:
                        self._send_json(_task_progress_payload(enriched_task))
                    else:
                        self._send_json(enriched_task)
                    return
                project_slug = _match_project_route(path, "summary")
                if project_slug is not None:
                    self._send_json(
                        asyncio.run(_load_project_summary_payload(settings, project_slug))
                    )
                    return
                project_slug = _match_project_route(path, "listing")
                if project_slug is not None:
                    self._send_json(
                        asyncio.run(_load_project_listing_payload(settings, project_slug))
                    )
                    return
                project_slug = _match_project_route(path, "structure")
                if project_slug is not None:
                    self._send_json(
                        asyncio.run(_load_project_structure_payload(settings, project_slug))
                    )
                    return
                project_slug = _match_project_route(path, "story-bible")
                if project_slug is not None:
                    self._send_json(asyncio.run(_load_story_bible_payload(settings, project_slug)))
                    return
                project_slug = _match_project_route(path, "narrative")
                if project_slug is not None:
                    self._send_json(asyncio.run(_load_narrative_payload(settings, project_slug)))
                    return
                project_slug = _match_project_route(path, "workflow")
                if project_slug is not None:
                    self._send_json(asyncio.run(_load_workflow_payload(settings, project_slug)))
                    return
                project_slug = _match_project_route(path, "preview")
                if project_slug is not None:
                    artifact_name = (query.get("name") or ["project.md"])[0]
                    artifact_path = resolve_project_artifact_path(
                        settings, project_slug, artifact_name
                    )
                    content_md = artifact_path.read_text(encoding="utf-8")
                    self._send_text(
                        _render_preview_html(project_slug, artifact_path.name, content_md),
                        content_type="text/html; charset=utf-8",
                    )
                    return
                project_slug = _match_project_route(path, "preview-data")
                if project_slug is not None:
                    artifact_name = (query.get("name") or ["project.md"])[0]
                    artifact_path = resolve_project_artifact_path(
                        settings, project_slug, artifact_name
                    )
                    content_md = artifact_path.read_text(encoding="utf-8")
                    payload = build_preview_payload(project_slug, artifact_path.name, content_md)
                    payload.update(
                        {
                            "path": str(artifact_path.resolve()),
                            "size_bytes": artifact_path.stat().st_size,
                            "modified_at": datetime.fromtimestamp(
                                artifact_path.stat().st_mtime, UTC
                            ).isoformat(),
                        }
                    )
                    self._send_json(payload)
                    return
                project_slug = _match_project_route(path, "artifact")
                if project_slug is not None:
                    artifact_name = (query.get("name") or [None])[0]
                    if artifact_name is None:
                        raise ValueError("Query parameter 'name' is required.")
                    artifact_path = resolve_project_artifact_path(
                        settings, project_slug, artifact_name
                    )
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
                        chapters_done = len(
                            progress_data.get("chapters", [])
                            or progress_data.get("chapters_mainline", [])
                        )
                    compiled_files = (
                        sorted(f.name for f in build_dir.iterdir()) if build_dir.exists() else []
                    )
                    self._send_json(
                        {
                            "project_slug": project_slug,
                            "has_progress": progress_path.exists(),
                            "has_bible": "bible" in progress_data,
                            "has_arc_plans": "arc_plans" in progress_data
                            or "arc_plans_mainline" in progress_data,
                            "chapters_done": chapters_done,
                            "has_walkthrough": "walkthrough" in progress_data,
                            "compiled_files": compiled_files,
                        }
                    )
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
                        chapters_dir = (
                            Path(settings.output.base_dir)
                            / slug
                            / "translations"
                            / lang_param
                            / "chapters"
                        )
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
                        if (trans_base / lc / "chapters").exists() and any(
                            (trans_base / lc / "chapters").glob("ch*.json")
                        ):
                            avail.append(lc)
                    pkg["available_langs"] = avail
                    self._send_json(pkg)
                    return
                if path.startswith("/api/if-novels/") and "/branches/" in path:
                    parts = path.split("/")
                    # /api/if-novels/{slug}/branches/{route_id}?lang=xx
                    if len(parts) >= 6:
                        slug = parts[3]
                        route_id = parts[5]
                        lang_param = (query.get("lang") or ["zh"])[0].lower()
                        if lang_param not in {"zh", "en", "ja", "ko"}:
                            lang_param = "zh"
                        if lang_param == "zh":
                            branch_dir = (
                                Path(settings.output.base_dir) / slug / "if" / "branches" / route_id
                            )
                        else:
                            branch_dir = (
                                Path(settings.output.base_dir)
                                / slug
                                / "translations"
                                / lang_param
                                / "branches"
                                / route_id
                            )
                            # Fall back to default Chinese branch if translated version missing
                            if not branch_dir.exists():
                                branch_dir = (
                                    Path(settings.output.base_dir)
                                    / slug
                                    / "if"
                                    / "branches"
                                    / route_id
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
                    # DB-authoritative chapter list with volume info.  This
                    # stays in sync with ``assemble_chapter_draft`` — user
                    # previously saw "84 in task list, 50 in preview" because
                    # the filesystem scan only saw exported markdown files.
                    db_chapters, _vol_map, volumes = _load_project_chapter_index(
                        settings,
                        project_slug,
                    )
                    fs_toc = _build_chapter_toc(output_dir)
                    # Merge: DB list is authoritative for membership; fs
                    # provides word_count / read-minutes when the export has
                    # landed.  Chapters not yet exported still show with
                    # word_count=0 so the reader knows they exist.
                    fs_by_number: dict[int, dict[str, object]] = {
                        int(entry["number"]): entry for entry in fs_toc
                    }
                    if db_chapters:
                        merged_toc: list[dict[str, object]] = []
                        for ch in db_chapters:
                            n = int(ch["number"])
                            fs_entry = fs_by_number.get(n) or {}
                            db_word_count = int(ch.get("word_count") or 0)
                            fs_word_count = int(fs_entry.get("word_count") or 0)
                            word_count = db_word_count or fs_word_count
                            estimated_read_minutes = int(
                                ch.get("estimated_read_minutes") or 0
                            ) or int(fs_entry.get("estimated_read_minutes") or 0)
                            availability = str(ch.get("availability") or "available")
                            # If the file is missing on disk and the chapter
                            # isn't already flagged as repair/draft, surface it
                            # as 'unavailable' so the reader doesn't link to a
                            # 404.
                            exported = n in fs_by_number
                            if (
                                not exported
                                and availability == "available"
                                and not bool(ch.get("has_draft"))
                            ):
                                availability = "drafting"
                            merged_toc.append(
                                {
                                    "number": n,
                                    "title": fs_entry.get("title") or ch.get("title") or f"第{n}章",
                                    "filename": fs_entry.get("filename") or f"chapter-{n:03d}.md",
                                    "word_count": word_count,
                                    "estimated_read_minutes": estimated_read_minutes,
                                    "volume_number": ch.get("volume_number"),
                                    "volume_title": ch.get("volume_title"),
                                    "exported": exported,
                                    "status": ch.get("status"),
                                    "production_state": ch.get("production_state"),
                                    "revision_count": ch.get("revision_count"),
                                    "has_draft": ch.get("has_draft"),
                                    "availability": availability,
                                }
                            )
                        toc = merged_toc
                    else:
                        # DB path failed (or project is filesystem-only);
                        # degrade to the legacy filesystem TOC.
                        toc = fs_toc
                    # Resolve human-readable project title from DB.
                    project_title = project_slug
                    try:

                        async def _fetch_title() -> str:
                            async with session_scope(settings) as sess:
                                proj = await get_project_by_slug(sess, project_slug)
                                return proj.title if proj and proj.title else project_slug

                        project_title = asyncio.run(_fetch_title())
                    except Exception:
                        pass
                    self._send_json(
                        {
                            "project_slug": project_slug,
                            "title": project_title,
                            "chapters": toc,
                            "volumes": volumes,
                        }
                    )
                    return
                if path.startswith("/api/reader/") and "/chapter/" in path:
                    parts = path.split("/")
                    project_slug = parts[3]
                    chapter_n = int(parts[5])
                    output_dir = _project_output_dir(settings, project_slug)
                    chapter_file = output_dir / f"chapter-{chapter_n:03d}.md"
                    if not chapter_file.exists():
                        # Try 4-digit format for chapters >= 1000
                        chapter_file_4d = output_dir / f"chapter-{chapter_n:04d}.md"
                        if chapter_file_4d.exists():
                            chapter_file = chapter_file_4d
                        else:
                            # DB fallback: write chapter draft to disk on-the-fly
                            content = _try_load_chapter_draft_from_db(
                                settings,
                                project_slug,
                                f"chapter-{chapter_n:03d}.md",
                            )
                            if content is None and chapter_n >= 1000:
                                content = _try_load_chapter_draft_from_db(
                                    settings,
                                    project_slug,
                                    f"chapter-{chapter_n:04d}.md",
                                )
                            if content is None:
                                self._route_not_found()
                                return
                            output_dir.mkdir(parents=True, exist_ok=True)
                            chapter_file.write_text(content, encoding="utf-8")
                    md = chapter_file.read_text(encoding="utf-8")
                    # Resolve project language for accurate sanitization
                    _reader_lang: str | None = (query.get("lang") or [None])[0]
                    if _reader_lang is None:
                        try:

                            async def _fetch_lang() -> str | None:
                                async with session_scope(settings) as sess:
                                    proj = await get_project_by_slug(sess, project_slug)
                                    return getattr(proj, "language", None) if proj else None

                            _reader_lang = asyncio.run(_fetch_lang())
                        except Exception:
                            pass
                    html_content = markdown_to_html(md, language=_reader_lang)
                    stats = build_markdown_reading_stats(md)
                    # Resolve volume info for this chapter so the reader can
                    # render "第N卷 卷名 · 第M章 章名".  Best-effort lookup;
                    # we never fail the chapter response just because the
                    # volume query errored.
                    volume_number: int | None = None
                    volume_title: str | None = None
                    chapter_title: str | None = None
                    try:
                        _, vol_map, _ = _load_project_chapter_index(settings, project_slug)
                        vol_info = vol_map.get(chapter_n) if vol_map else None
                        if vol_info:
                            volume_number = vol_info.get("volume_number")  # type: ignore[assignment]
                            volume_title = vol_info.get("volume_title")  # type: ignore[assignment]
                    except Exception:
                        pass
                    self._send_json(
                        {
                            "number": chapter_n,
                            "filename": chapter_file.name,
                            "html": html_content,
                            "word_count": stats["word_count"],
                            "estimated_read_minutes": stats["estimated_read_minutes"],
                            "volume_number": volume_number,
                            "volume_title": volume_title,
                            "chapter_title": chapter_title,
                        }
                    )
                    return
                self._route_not_found()
            except BrokenPipeError:
                return
            except FileNotFoundError:
                self._route_not_found()
            except Exception as exc:
                self._route_error(exc)

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            path = unquote(parsed.path)
            try:
                if path == "/api/tasks/autowrite":
                    payload = self._read_json_body()
                    required = [
                        "slug",
                        "title",
                        "genre",
                        "target_words",
                        "target_chapters",
                        "premise",
                    ]
                    missing = [key for key in required if not payload.get(key)]
                    if missing:
                        raise ValueError(f"Missing required fields: {', '.join(missing)}")
                    validate_longform_scope(
                        int(payload["target_words"]),
                        int(payload["target_chapters"]),
                        language=payload.get("language"),
                    )
                    block_payload = asyncio.run(
                        _load_project_autowrite_block_payload(
                            settings, str(payload.get("slug") or "")
                        ),
                    )
                    if block_payload:
                        self._send_json(block_payload, status=HTTPStatus.CONFLICT)
                        return
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
                if path.startswith("/api/projects/") and path.endswith("/listing/regenerate"):
                    slug = (
                        path.removeprefix("/api/projects/")
                        .removesuffix("/listing/regenerate")
                        .strip("/")
                    )
                    if not slug:
                        raise ValueError("Project slug is required.")
                    payload = self._read_json_body()
                    module = str(payload.get("module") or "").strip()
                    if module not in _LISTING_REGENERATE_MODULES:
                        raise ValueError(
                            f"Unknown module '{module}'. "
                            f"Allowed: {', '.join(_LISTING_REGENERATE_MODULES)}"
                        )
                    try:
                        result = asyncio.run(_regenerate_listing_module(settings, slug, module))
                    except ValueError as exc:
                        self._send_json(
                            {"ok": False, "error": str(exc)},
                            status=HTTPStatus.NOT_FOUND,
                        )
                        return
                    except Exception as exc:
                        logger.exception("Listing regenerate failed for %s/%s", slug, module)
                        self._send_json(
                            {"ok": False, "error": f"重新生成失败: {exc}"},
                            status=HTTPStatus.INTERNAL_SERVER_ERROR,
                        )
                        return
                    self._send_json({"ok": True, **result})
                    return
                if path == "/api/tasks/quickstart":
                    payload = self._read_json_body()
                    if not payload.get("genre_key"):
                        raise ValueError("Field 'genre_key' is required.")
                    resume_slug = str(payload.get("project_slug") or "")
                    if resume_slug:
                        block_payload = asyncio.run(
                            _load_project_autowrite_block_payload(settings, resume_slug),
                        )
                        if block_payload:
                            self._send_json(block_payload, status=HTTPStatus.CONFLICT)
                            return
                    task = task_manager.create_quickstart_task(payload)
                    self._send_json(task, status=HTTPStatus.ACCEPTED)
                    return
                if path == "/api/tasks/autowrite/schedule":
                    payload = self._read_json_body()
                    schedule_dict = _create_book_generation_schedule(
                        settings,
                        task_type="autowrite",
                        payload=payload,
                    )
                    self._send_json(schedule_dict, status=HTTPStatus.ACCEPTED)
                    return
                if path == "/api/tasks/quickstart/schedule":
                    payload = self._read_json_body()
                    schedule_dict = _create_book_generation_schedule(
                        settings,
                        task_type="quickstart",
                        payload=payload,
                    )
                    self._send_json(schedule_dict, status=HTTPStatus.ACCEPTED)
                    return
                if path == "/api/tasks/if-generate":
                    payload = self._read_json_body()
                    if not payload.get("project_slug"):
                        raise ValueError("Field 'project_slug' is required.")
                    task = task_manager.create_if_task(payload)
                    self._send_json(task, status=HTTPStatus.ACCEPTED)
                    return
                if path.startswith("/api/tasks/") and path.endswith("/cancel"):
                    task_id = path.removeprefix("/api/tasks/").removesuffix("/cancel")
                    ok = task_manager.request_cancel(task_id)
                    if not ok:
                        self._send_json(
                            {"ok": False, "error": "Task not found or not running"},
                            status=HTTPStatus.NOT_FOUND,
                        )
                        return
                    self._send_json({"ok": True, "task_id": task_id})
                    return
                if path.startswith("/api/tasks/") and path.endswith("/resume"):
                    task_id = path.removeprefix("/api/tasks/").removesuffix("/resume")
                    old = task_manager.get_task(task_id)
                    if old is None:
                        if task_id.startswith("db-repair:"):
                            slug = task_id.removeprefix("db-repair:").strip()
                            job_id = _enqueue_repair_heal_job(settings.redis.url, slug)
                            if job_id:
                                self._send_json(
                                    {
                                        "ok": True,
                                        "task_id": task_id,
                                        "project_slug": slug,
                                        "status": "queued",
                                        "current_stage": "delegated_to_worker_self_heal",
                                        "worker_job_id": job_id,
                                    },
                                    status=HTTPStatus.ACCEPTED,
                                )
                                return
                            self._send_json(
                                {
                                    "ok": False,
                                    "error": "Repair task was visible in DB but could not be enqueued",
                                },
                                status=HTTPStatus.CONFLICT,
                            )
                            return
                        self._send_json(
                            {"ok": False, "error": "Task not found"},
                            status=HTTPStatus.NOT_FOUND,
                        )
                        return
                    saved_payload = old.get("payload") or {}
                    # Fallback: rebuild payload from the stored ProjectModel.
                    # Enables resume for historical / recovered tasks that
                    # pre-date payload persistence.
                    if not saved_payload:
                        slug = old.get("project_slug") or ""
                        if slug:
                            rebuilt = asyncio.run(
                                _rebuild_payload_from_db(settings, str(slug)),
                            )
                            if rebuilt:
                                saved_payload = rebuilt
                    if not saved_payload:
                        self._send_json(
                            {
                                "ok": False,
                                "error": "No saved payload to resume from and no matching project in DB",
                            },
                            status=HTTPStatus.BAD_REQUEST,
                        )
                        return
                    # Force-disable conception so resume doesn't recreate from scratch
                    saved_payload = dict(saved_payload)
                    saved_payload["_run_conception"] = False
                    slug = str(saved_payload.get("slug") or old.get("project_slug") or "")
                    block_payload = asyncio.run(
                        _load_project_autowrite_block_payload(settings, slug),
                    )
                    if block_payload:
                        stage = str(block_payload.get("current_stage") or "blocked_structural_repair")
                        error = str(block_payload.get("error") or "")
                        if block_payload.get("blocked_generation_gate"):
                            task_manager.mark_task_blocked_by_project_gate(
                                task_id,
                                stage=stage,
                                error=error,
                            )
                        else:
                            task_manager.mark_task_blocked_by_structural_repair(
                                task_id,
                                error,
                            )
                        self._send_json(block_payload, status=HTTPStatus.CONFLICT)
                        return
                    heal_owned = bool(
                        slug
                        and settings.redis.url
                        and slug
                        in _fetch_heal_owned_slugs_by_kind(
                            settings.redis.url,
                            "autowrite",
                        )
                    )
                    resumed = task_manager.resume_autowrite_task(
                        task_id,
                        saved_payload,
                        delegate_to_self_heal=heal_owned,
                        heal_owned=heal_owned,
                    )
                    if resumed is None:
                        self._send_json(
                            {"ok": False, "error": "Task not found"},
                            status=HTTPStatus.NOT_FOUND,
                        )
                        return
                    if resumed == "busy":
                        self._send_json(
                            {
                                "ok": False,
                                "error": "Task is already running or queued",
                            },
                            status=HTTPStatus.CONFLICT,
                        )
                        return
                    self._send_json(resumed, status=HTTPStatus.ACCEPTED)
                    return
                if path == "/api/projects/batch-delete":
                    body = self._read_json_body()
                    raw_slugs = body.get("slugs") or []
                    if not isinstance(raw_slugs, list) or not raw_slugs:
                        raise ValueError("Field 'slugs' must be a non-empty list.")
                    # Dedupe while preserving order
                    seen: set[str] = set()
                    slugs: list[str] = []
                    for raw in raw_slugs:
                        s = str(raw).strip()
                        if s and s not in seen:
                            seen.add(s)
                            slugs.append(s)
                    results = [_delete_project_full(slug, task_manager, settings) for slug in slugs]
                    summary = {
                        "total": len(results),
                        "success": sum(1 for r in results if r.get("ok")),
                        "failed": sum(1 for r in results if not r.get("ok")),
                    }
                    self._send_json({"results": results, "summary": summary})
                    return
                if path == "/api/tasks/cleanup":
                    body = self._read_json_body()
                    raw_statuses = body.get("statuses") or []
                    if not isinstance(raw_statuses, list) or not raw_statuses:
                        raise ValueError("Field 'statuses' must be a non-empty list.")
                    statuses = {str(s) for s in raw_statuses}
                    removed = task_manager.cleanup_tasks(statuses)
                    self._send_json({"removed": removed, "statuses": sorted(statuses)})
                    return
                if path == "/api/projects/recover":
                    recovered = asyncio.run(_recover_projects_from_output(settings))
                    self._send_json(
                        {
                            "recovered": len(recovered),
                            "projects": recovered,
                        }
                    )
                    return
                self._route_not_found()
            except Exception as exc:
                self._route_error(exc)

        def do_DELETE(self) -> None:
            parsed = urlparse(self.path)
            path = unquote(parsed.path)
            try:
                if path.startswith("/api/schedules/"):
                    schedule_id = path.removeprefix("/api/schedules/").strip("/")
                    if not schedule_id:
                        raise ValueError("Missing schedule_id in path")
                    outcome = _cancel_book_generation_schedule(settings, schedule_id)
                    if outcome == "not_found":
                        self._send_json(
                            {"ok": False, "error": "Schedule not found"},
                            status=HTTPStatus.NOT_FOUND,
                        )
                        return
                    if isinstance(outcome, str) and outcome.startswith("invalid_state:"):
                        self._send_json(
                            {"ok": False, "error": outcome.removeprefix("invalid_state:").strip()},
                            status=HTTPStatus.CONFLICT,
                        )
                        return
                    self._send_json({"ok": True, "schedule_id": schedule_id})
                    return
                if path.startswith("/api/tasks/"):
                    task_id = path.removeprefix("/api/tasks/").strip("/")
                    if not task_id:
                        raise ValueError("Missing task_id in path")
                    result = task_manager.delete_task(task_id)
                    if result == "not_found":
                        self._send_json(
                            {"ok": False, "error": "Task not found"},
                            status=HTTPStatus.NOT_FOUND,
                        )
                        return
                    if result == "running":
                        self._send_json(
                            {
                                "ok": False,
                                "error": "Task is still running — stop it before deleting.",
                            },
                            status=HTTPStatus.CONFLICT,
                        )
                        return
                    self._send_json({"ok": True, "task_id": task_id})
                    return
                if path.startswith("/api/projects/"):
                    slug = path.removeprefix("/api/projects/").strip("/")
                    if not slug:
                        raise ValueError("Missing project slug in path")
                    outcome = _delete_project_full(slug, task_manager, settings)
                    status_code = HTTPStatus.OK if outcome.get("ok") else HTTPStatus.CONFLICT
                    self._send_json(outcome, status=status_code)
                    return
                self._route_not_found()
            except Exception as exc:
                self._route_error(exc)

    httpd = _FastThreadingHTTPServer((host, port), RequestHandler)
    url = f"http://{host}:{port}"
    print(f"BestSeller Web Studio running at {url}")
    if open_browser:
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
