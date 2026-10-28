from __future__ import annotations

import asyncio
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
import hashlib
import hmac
import html
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
import mimetypes
import os
from pathlib import Path
import re
import socketserver
import threading
import traceback
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse
from uuid import UUID, uuid4
import webbrowser
from zoneinfo import ZoneInfo

QUICKSTART_CLIENT_SCHEMA_VERSION = "quickstart-client.v2"


def _quickstart_seed_hash(seed: str) -> str | None:
    value = str(seed or "").strip()
    return hashlib.sha256(value.encode("utf-8")).hexdigest() if value else None


def _quickstart_seed_length(seed: str) -> int:
    """Return the manifest length in UTF-8 bytes, matching TextEncoder in JS."""

    return len(str(seed or "").strip().encode("utf-8"))


def _validate_quickstart_client_schema(payload: Mapping[str, object]) -> None:
    """Fail closed for first-party quickstart requests from stale pages."""
    version = str(payload.get("client_schema_version") or "").strip()
    if version != QUICKSTART_CLIENT_SCHEMA_VERSION:
        raise ValueError("快速建书页面版本已过期，请刷新页面后重新提交。")
    manifest = payload.get("client_intent_manifest")
    if not isinstance(manifest, Mapping):
        raise ValueError("快速建书输入校验缺失，请刷新页面后重新提交。")
    seed = str(payload.get("concept_seed") or "").strip()[:500]
    expected = {
        "schema_version": QUICKSTART_CLIENT_SCHEMA_VERSION,
        "concept_seed_present": bool(seed),
        "concept_seed_length": _quickstart_seed_length(seed),
        "concept_seed_hash": _quickstart_seed_hash(seed),
    }
    for key, value in expected.items():
        if manifest.get(key) != value:
            raise ValueError("快速建书输入校验不一致，请刷新页面后重新提交。")


from bestseller.domain.enums import ProjectStatus
from bestseller.api.routers.tasks import _project_task_events, _task_event_payload
from bestseller.domain.fanqie_short import is_fanqie_short_project
from bestseller.domain.project import InteractiveFictionConfig, ProjectCreate
from bestseller.infra.db.models import (
    PlanningArtifactVersionModel,
    RewriteTaskModel,
    StyleGuideModel,
    WorkflowRunModel,
    WorkflowStepRunModel,
)
from bestseller.infra.db.session import session_scope
from bestseller.services.anti_commonsense_hook import generate_hook_candidates
from bestseller.services.anti_commonsense_mechanisms import get_mechanism
from bestseller.services.book_listing import _limit_chars as _listing_limit_chars
from bestseller.services.book_listing import (
    build_book_listing_profile,
    validate_book_listing_profile,
)
from bestseller.services.chapter_revision import (
    apply_chapter_revision_task,
    create_chapter_revision_task,
)
from bestseller.services.concept_contract import ConceptContractError
from bestseller.services.conception_approval_bridge import (
    approve_pending_conception,
    assert_conception_approved,
    build_planning_handoff_payload,
    build_conception_options,
    canonical_snapshot_content_hash,
    canonical_snapshot_premise,
)
from bestseller.services.concept_lab import (
    build_concept_lab_catalog,
    concept_lab_to_user_hints,
    select_concept_lab_bundle,
)
from bestseller.services.exports import build_markdown_reading_stats, markdown_to_html
from bestseller.services.genre_creativity import (
    creative_direction_to_user_hints,
    get_genre_creative_direction_for_preset,
    get_genre_creativity_catalog_payload,
)
from bestseller.services.hook_propagation import coerce_hook_spec
from bestseller.services.if_generation import run_if_pipeline_integrated
from bestseller.services.inspection import (
    build_project_structure,
    build_project_workflow_overview,
    build_story_bible_overview,
)
from bestseller.services.narrative import build_narrative_overview
from bestseller.services.pipelines import (
    ProjectRepairPauseError,
    run_autowrite_pipeline,
    run_chapter_pipeline,
)
from bestseller.services.planner import PlannerFallbackError
from bestseller.services.production_control import ProductionControlHaltedError
from bestseller.services.prewrite_review import (
    approve_prewrite_materials,
    assert_prewrite_review_approved,
    create_prewrite_issue_task,
    edit_prewrite_material,
    load_prewrite_review_payload,
    repair_prewrite_material_from_task,
)
from bestseller.services.progress_context import (
    TIER_KEY,
    TIER_MILESTONE,
    TaskCancelledError,
    bind_progress,
    set_cancellation_checker,
)
from bestseller.services.budget import (
    ExecutionBudgetError, bind_execution_scope, current_execution_scope,
    raise_if_execution_stopped, ExecutionLedger, ExecutionReconciliation,
)
from bestseller.services.retry_ledger import (
    execution_terminal_code,
    assert_execution_scope_open, project_execution_scope,
)
from bestseller.services.projects import (
    delete_project_completely,
    get_project_by_slug,
    is_project_delete_tombstoned,
    list_projects,
)
from bestseller.services.repair import run_project_repair
from bestseller.services.runtime_provenance import (
    disk_runtime_provenance,
    initialize_process_runtime,
    task_runtime_provenance,
)
from bestseller.services.story_appeal import AppealBarNotMetError
from bestseller.services.story_enhancers import STORY_ENHANCERS_METADATA_KEY
from bestseller.services.writing_presets import (
    get_genre_preset_dimensions,
    load_writing_preset_catalog,
    validate_longform_scope,
)
from bestseller.services.writing_profile import (
    get_project_writing_profile,
    sanitize_genre_story_overrides,
)
from bestseller.settings import (
    AppSettings,
    apply_runtime_llm_profile,
    load_settings,
    runtime_llm_profile_payload,
    set_runtime_llm_profile,
)

logger = logging.getLogger(__name__)

_READER_HTML_PATH = Path(__file__).with_name("novel_reader.html")
_IF_READER_HTML_PATH = Path(__file__).with_name("novel_if_reader.html")
_QUICKSTART_HTML_PATH = Path(__file__).with_name("novel_quickstart.html")
_LIBRARY_HTML_PATH = Path(__file__).with_name("novel_library.html")
_DESIGN_DOSSIER_HTML_PATH = Path(__file__).with_name("novel_design_dossier.html")
_PIPELINE_FLOW_HTML_PATH = Path(__file__).with_name("novel_pipeline_flow.html")
_CHARACTER_NETWORK_HTML_PATH = Path(__file__).with_name("novel_character_network.html")
_READER_REVIEW_HTML_PATH = Path(__file__).with_name("novel_reader_review.html")
_METHODOLOGY_COURSE_HTML_PATH = Path(__file__).with_name("novel_methodology_course.html")
_ARCHITECTURE_COURSE_HTML_PATH = Path(__file__).with_name("novel_architecture_course.html")
_METHODOLOGY_COURSE_MD_PATH = (
    Path(__file__).resolve().parents[3] / "docs" / "novel-writing-methodology-course.md"
)
_METHODOLOGY_COURSE_EN_MD_PATH = (
    Path(__file__).resolve().parents[3] / "docs" / "novel-writing-methodology-course.en.md"
)
_METHODOLOGY_BROADCAST_MD_PATH = (
    Path(__file__).resolve().parents[3] / "docs" / "口播稿-写作方法论-20260623.md"
)
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


def _local_now() -> datetime:
    tz_name = os.getenv("TZ", "Asia/Shanghai")
    try:
        return datetime.now(ZoneInfo(tz_name))
    except Exception:
        logger.warning("Invalid TZ=%s; falling back to system local time", tz_name)
        return datetime.now()


def _timestamp_seconds(value: object) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _normalise_event_timestamp(value: object) -> object:
    """Expose progress timestamps in one browser-safe ISO representation."""
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), UTC).isoformat()
    if isinstance(value, str):
        # Numeric strings can be emitted by a Redis client configured without
        # response decoding; ISO strings are already the public contract.
        numeric = value.strip().replace(".", "", 1)
        if numeric.isdigit():
            return datetime.fromtimestamp(float(value), UTC).isoformat()
    return value


def _json_default(value: object) -> object:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


_SELF_HEAL_SCAN_DONE_KEY = "bestseller:self_heal:scan_done"
_HEAL_KEY_PREFIXES: dict[str, tuple[str, ...]] = {
    "outline_replan": (
        "arq:job:outline-replan:heal:",
        "arq:in-progress:outline-replan:heal:",
        "arq:retry:outline-replan:heal:",
    ),
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
_HEAL_RESULT_KEY_PREFIXES: dict[str, str] = {
    "outline_replan": "arq:result:outline-replan:heal:",
    "autowrite": "arq:result:autowrite:heal:",
    "repair": "arq:result:repair:heal:",
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


def _fetch_heal_result_slugs_by_kind(redis_url: str, heal_kind: str) -> set[str]:
    """Return deterministic heal jobs whose terminal ARQ result is retained.

    ``_fetch_heal_owned_slugs_by_kind`` intentionally only reports live queue
    ownership.  The dashboard also needs the most recent completed/failed
    worker receipt, however, because the in-process web task may be an older
    conception failure for the same project.  Result keys are evidence of a
    worker receipt, not evidence that a job is still running.
    """
    import redis as _redis

    prefix = _HEAL_RESULT_KEY_PREFIXES.get(heal_kind)
    if prefix is None:
        return set()
    try:
        client = _redis.from_url(redis_url, decode_responses=True, socket_timeout=2)
    except Exception:
        return set()
    slugs: set[str] = set()
    try:
        for key in client.scan_iter(match=prefix + "*", count=200):
            slug = key[len(prefix) :]
            if slug:
                slugs.add(slug)
    except Exception:
        logger.exception("task list: Redis result scan failed")
        return set()
    finally:
        try:
            client.close()
        except Exception:
            pass
    return slugs


def _worker_heal_job_id(task_type: str, slug: str) -> str | None:
    slug = (slug or "").strip()
    if not slug:
        return None
    kind = {
        "repair": "repair",
        "outline_replan": "outline-replan",
    }.get(task_type, "autowrite")
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


async def _abort_worker_heal_job_async(redis_url: str, job_id: str) -> bool:
    """Ask ARQ to abort a deterministic self-heal job.

    Synthetic task cards such as ``db-repair:<slug>`` can be owned by ARQ
    worker jobs instead of ``WebTaskManager``.  The web UI still needs a stop
    action for those visible tasks, so this performs a best-effort abort and
    removes queued/retry ownership keys.
    """
    if not redis_url or not job_id:
        return False

    from arq.connections import create_pool
    from arq.jobs import Job

    pool = await create_pool(_arq_redis_settings_from_url(redis_url))
    try:
        has_job = await pool.exists(
            f"arq:job:{job_id}",
            f"arq:in-progress:{job_id}",
            f"arq:retry:{job_id}",
        )
        if not has_job and await pool.zscore("arq:queue", job_id) is None:
            return False

        job = Job(job_id, pool)
        abort_requested = False
        try:
            abort_requested = await job.abort(timeout=0.1)
        except TimeoutError:
            abort_requested = True
        except Exception:
            logger.exception("Failed to request ARQ abort for job %s", job_id)

        removed = await pool.delete(
            f"arq:job:{job_id}",
            f"arq:retry:{job_id}",
        )
        try:
            removed += await pool.zrem("arq:queue", job_id)
        except Exception:
            logger.debug("Failed to remove %s from ARQ queue", job_id, exc_info=True)
        return bool(abort_requested or removed)
    finally:
        closer = getattr(pool, "aclose", None) or getattr(pool, "close", None)
        if closer is not None:
            result = closer()
            if asyncio.iscoroutine(result):
                await result


def _abort_worker_heal_job(redis_url: str, job_id: str) -> bool:
    """Synchronously abort an ARQ self-heal job from request-handler code."""
    if not redis_url or not job_id:
        return False
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        logger.warning("task cancel: cannot abort ARQ job from an active event loop")
        return False
    try:
        return asyncio.run(_abort_worker_heal_job_async(redis_url, job_id))
    except Exception:
        logger.exception("task cancel: failed to abort ARQ job %s", job_id)
        return False


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
        ts = _normalise_event_timestamp(evt.get("ts", evt.get("timestamp")))
        if not isinstance(ts, (int, float, str)):
            continue
        stage = evt.get("message") or evt.get("stage")
        if not isinstance(stage, str) or not stage:
            continue
        stage = _normalize_machine_repair_stage(stage)
        payload = evt.get("data") if isinstance(evt.get("data"), dict) else evt.get("payload")
        payload_dict = payload if isinstance(payload, dict) else {}
        payload_dict = _normalize_machine_repair_payload(payload_dict)
        events.append(
            {
                "timestamp": ts,
                "stage": stage,
                "payload": payload_dict,
            }
        )
        if isinstance(evt.get("event_type"), str):
            events[-1]["event_type"] = evt["event_type"]
        for identity_key in ("job_id", "task_id", "attempt_id", "task_attempt_id", "workflow_run_id",
                             "project_id", "project_slug", "execution_scope_id", "scope_id"):
            if evt.get(identity_key) is not None:
                events[-1][identity_key] = evt[identity_key]
        latest_stage = stage
        latest_payload = payload_dict
    return events, latest_stage, latest_payload


def _normalize_machine_repair_stage(stage: str) -> str:
    legacy_blocked = "waiting" + "_human"
    legacy_review = legacy_blocked + "_review"
    legacy_pause_suffix = "paused_for_" + "human_review"
    if stage in {"machine_blocked", "blocked_generation_gate"}:
        return "repairable_auto_continue_pending"
    if stage == legacy_blocked:
        return "repairable_auto_continue_pending"
    if stage == legacy_review:
        return "machine_repair_required"
    if stage.endswith(legacy_pause_suffix):
        return stage[: -len(legacy_pause_suffix)] + "machine_repair_required"
    return stage


def _normalize_machine_repair_payload(payload: dict[str, object]) -> dict[str, object]:
    if not payload:
        return payload
    normalized = dict(payload)
    if normalized.get("reason") == "project_repair_requires_attention":
        normalized["reason"] = "project_repair_requires_machine_repair"
    return normalized


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
        # Milestones live in a separate, untrimmed list so the durable axis
        # survives the activity list's tail window on reconnect. This read is a
        # best-effort enhancement: a failure must not sink the whole snapshot.
        try:
            raw_milestones = client.lrange(f"task:{job_id}:milestones", 0, -1)
        except Exception:
            raw_milestones = []
    except Exception:
        return None
    finally:
        try:
            client.close()
        except Exception:
            pass

    events, latest_stage, latest_payload = _normalise_worker_progress_events(raw_events or [])
    milestone_events, _ms_stage, _ms_payload = _normalise_worker_progress_events(
        raw_milestones or []
    )
    evidence = sorted(milestone_events + events,
                      key=lambda event: _timestamp_seconds(event.get("timestamp")) or 0)
    projection = _project_task_events(evidence, task_id=job_id)
    decisive = projection["event"]
    latest_stage = decisive.get("stage") or latest_stage
    latest_payload = _task_event_payload(decisive) or latest_payload
    status = projection["status"]
    if not projection["terminal"]:
        status = "incomplete" if job_state == "result" else job_state
    elif status == "completed" and job_state != "result":
        status = job_state
    return {
        "job_state": job_state,
        "status": status,
        "current_stage": latest_stage or "delegated_to_worker_self_heal",
        "progress_events": events,
        "milestone_events": milestone_events,
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
    summary["current_stage"] = worker_progress.get("current_stage") or summary.get("current_stage")
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
    worker_milestones = worker_progress.get("milestone_events")
    if isinstance(worker_milestones, list) and worker_milestones:
        existing_ms = summary.get("milestone_events")
        base_ms = existing_ms if isinstance(existing_ms, list) else []
        summary["milestone_events"] = base_ms + worker_milestones
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


def _attach_repair_heal_owner_to_db_summary(
    summary: dict[str, object],
    job_id: str,
    worker_progress: dict[str, object] | None,
) -> dict[str, object]:
    summary["worker_job_id"] = job_id
    summary["activity_status"] = "active"
    summary["execution_active"] = True
    if worker_progress:
        return _merge_worker_progress_into_db_repair_summary(summary, worker_progress)
    if str(summary.get("status") or "").strip().lower() != "running":
        summary["status"] = "queued"
        summary["current_stage"] = "delegated_to_worker_self_heal"
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
            if isinstance(event, dict) and current_stage and event.get("stage") == current_stage
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
        "updated_at": task.get("updated_at"),
        "current_workflow": task.get("current_workflow"),
        "workflow_run_id": task.get("workflow_run_id"),
        "activity_status": task.get("activity_status"),
        "execution_active": task.get("execution_active"),
        "projection_authority": task.get("projection_authority"),
        "projection_migration": task.get("projection_migration"),
        "task_history": task.get("task_history"),
        "delivery_closure": task.get("delivery_closure"),
        "book_state": task.get("book_state"),
    }


def _is_stale_autowrite_repair_block_task(
    task: Mapping[str, object],
    db_repair_slugs: set[str],
) -> bool:
    slug = _task_project_slug(task)
    if not slug or slug not in db_repair_slugs:
        return False
    task_type = str(task.get("task_type") or "")
    if task_type != "autowrite":
        return False
    status = str(task.get("status") or "")
    if status not in {"failed", "cancelled", "incomplete"}:
        return False
    stage = str(task.get("current_stage") or "")
    error = str(task.get("error") or "")
    return (
        stage == "blocked_structural_repair"
        or "paused for structural repair" in error
        or "当前处于结构修复暂停状态" in error
    )


def _load_worker_task_summary(
    settings: AppSettings,
    task_id: str,
) -> dict[str, object] | None:
    if task_id.startswith("outline-replan:heal:"):
        task_type = "outline_replan"
        slug = task_id.removeprefix("outline-replan:heal:")
    elif task_id.startswith("autowrite:heal:"):
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
    latest_payload = worker_progress.get("latest_payload")
    latest_payload = latest_payload if isinstance(latest_payload, dict) else {}
    status = str(worker_progress.get("status") or "running")
    # A terminal ARQ result can represent an incomplete hand-off.  Preserve
    # its worker-provided reason at the top level so the dashboard does not
    # render a misleading blank error beside a pending/failed card.
    error = None
    if status in {"failed", "incomplete", "cancelled"} and latest_payload.get("error"):
        error = str(latest_payload["error"])
    events = worker_progress.get("progress_events")
    events = events if isinstance(events, list) else []
    updated_at = None
    if events and isinstance(events[-1], dict):
        updated_at = events[-1].get("timestamp")
        if isinstance(updated_at, (int, float)):
            updated_at = datetime.fromtimestamp(float(updated_at), UTC).isoformat()
    return {
        "task_id": task_id,
        "worker_job_id": task_id,
        "task_type": task_type,
        "status": status,
        "updated_at": updated_at,
        "title": slug,
        "project_title": slug,
        "project_slug": slug,
        "current_stage": worker_progress.get("current_stage") or "delegated_to_worker_self_heal",
        "progress_events": worker_progress.get("progress_events") or [],
        "milestone_events": worker_progress.get("milestone_events") or [],
        "result": latest_payload,
        "error": error,
        "synthetic_worker_task": True,
    }


def _resolve_worker_task_resume_target(
    settings: AppSettings,
    task_id: str,
) -> tuple[str, str] | None:
    """Resolve a resumable worker card only from a matching Redis receipt."""
    prefixes = {
        "outline-replan:heal:": "outline_replan",
        "autowrite:heal:": "autowrite",
        "repair:heal:": "repair",
    }
    task_type = next((kind for prefix, kind in prefixes.items() if task_id.startswith(prefix)), None)
    if task_type is None:
        return None
    prefix = next(prefix for prefix, kind in prefixes.items() if kind == task_type)
    requested_slug = task_id.removeprefix(prefix).strip()
    if not requested_slug:
        return None
    receipt = _load_worker_task_summary(settings, task_id)
    if receipt is None or _task_project_slug(receipt) != requested_slug:
        return None
    return task_type, requested_slug


def _worker_resume_enqueue_kind(task_type: str, *, outline_replan_available: bool) -> str:
    """Choose the route from current project truth, not the historical card."""
    if task_type == "outline_replan" and not outline_replan_available:
        # Once prose exists, worker/tasks.py intentionally skips outline
        # regeneration and requires suffix-preserving ordinary continuation.
        return "autowrite"
    return task_type


def _worker_resume_requires_autowrite_block(
    task_type: str,
    *,
    outline_replan_available: bool,
) -> bool:
    """Apply prose block gates only to ordinary continuation routes."""
    return task_type != "outline_replan" or not outline_replan_available


def _worker_summary_timestamp(summary: Mapping[str, object]) -> float:
    value = summary.get("updated_at")
    parsed = _timestamp_seconds(value)
    return parsed if parsed is not None else float("-inf")


def _load_worker_task_summaries(settings: AppSettings) -> list[dict[str, object]]:
    """Load the newest retained worker receipt for each project slug.

    This is deliberately separate from DB repair summaries: a worker result
    is a receipt of a real delegated run and must not be synthesized into a
    running/completed state when Redis has no corresponding evidence.
    """
    candidates: list[dict[str, object]] = []
    for kind in ("outline_replan", "autowrite", "repair"):
        slugs = _fetch_heal_owned_slugs_by_kind(settings.redis.url, kind)
        slugs.update(_fetch_heal_result_slugs_by_kind(settings.redis.url, kind))
        task_type = kind
        for slug in slugs:
            job_id = _worker_heal_job_id(task_type, slug)
            if job_id is None:
                continue
            summary = _load_worker_task_summary(settings, job_id)
            if summary is not None:
                candidates.append(summary)

    newest_by_slug: dict[str, dict[str, object]] = {}
    for summary in candidates:
        slug = _task_project_slug(summary)
        if not slug:
            continue
        previous = newest_by_slug.get(slug)
        if previous is None or _worker_summary_timestamp(summary) >= _worker_summary_timestamp(
            previous
        ):
            newest_by_slug[slug] = summary
    return list(newest_by_slug.values())


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


async def _enqueue_outline_replan_heal_job_async(
    settings: AppSettings, redis_url: str, slug: str
) -> str | None:
    """Push a zero-draft planning recovery into the dedicated worker lane."""
    from arq.connections import create_pool

    from bestseller.worker.self_heal import (
        StuckProject,
        _outline_replan_heal_job_id,
        _outline_replan_progress_fingerprint,
        _requeue_outline_replan,
    )

    pool = await create_pool(_arq_redis_settings_from_url(redis_url))
    try:
        async with session_scope(settings) as session:
            project = await get_project_by_slug(session, slug)
            if project is None:
                return None
            fingerprint = _outline_replan_progress_fingerprint(project)
        stuck = StuckProject(
            project_id=None,
            slug=slug,
            reason="web_manual_outline_replan_resume",
            stuck_at_chapter=None,
            chapters_total=0,
            chapters_with_draft=0,
            heal_kind="outline_replan",
            progress_fingerprint=fingerprint,
        )
        job_id = await _requeue_outline_replan(pool, stuck)
        if job_id is None and slug in _fetch_heal_owned_slugs_by_kind(redis_url, "outline_replan"):
            return _outline_replan_heal_job_id(slug)
        return job_id
    finally:
        closer = getattr(pool, "aclose", None) or getattr(pool, "close", None)
        if closer is not None:
            result = closer()
            if asyncio.iscoroutine(result):
                await result


def _enqueue_outline_replan_heal_job(
    settings: AppSettings, redis_url: str, slug: str
) -> str | None:
    if not redis_url or not slug:
        return None
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        logger.warning("outline replan resume: cannot enqueue ARQ job from an active event loop")
        return None
    try:
        return asyncio.run(_enqueue_outline_replan_heal_job_async(settings, redis_url, slug))
    except Exception:
        logger.exception("outline replan resume: failed to enqueue worker job for slug=%s", slug)
        return None


async def _clear_outline_replan_resume_abandonment(settings: AppSettings, slug: str) -> bool:
    """Reset only stale self-heal abandonment budget for a zero-draft replan."""
    from sqlalchemy import func, select

    from bestseller.infra.db.models import ChapterDraftVersionModel, ChapterModel

    async with session_scope(settings) as session:
        project = await get_project_by_slug(session, slug)
        if project is None:
            return False
        metadata = dict(getattr(project, "metadata_json", None) or {})
        if execution_terminal_code(metadata):
            return False
        needs_replan = (
            str(getattr(project, "status", "") or "").lower() == "needs_replan"
            or str(metadata.get("planning_status") or "").lower() == "needs_replan"
            or str(metadata.get("outline_semantic_gate_status") or "").lower() == "needs_replan"
            or bool(metadata.get("outline_replan_required"))
        )
        if not needs_replan:
            return False
        draft_count = int(
            await session.scalar(
                select(func.count(func.distinct(ChapterDraftVersionModel.chapter_id)))
                .select_from(ChapterDraftVersionModel)
                .join(ChapterModel, ChapterModel.id == ChapterDraftVersionModel.chapter_id)
                .where(
                    ChapterModel.project_id == project.id,
                    ChapterDraftVersionModel.is_current.is_(True),
                )
            )
            or 0
        )
        if draft_count:
            return False
        prefixes = ("self_heal_abandoned", "self_heal_no_progress")
        explicit = {
            "self_heal_strategy",
            "self_heal_next_retry_at",
            "outline_replan_next_retry_at",
        }
        updated = {
            key: value
            for key, value in metadata.items()
            if not key.startswith(prefixes) and key not in explicit
        }
        if updated == metadata:
            return True
        project.metadata_json = updated
        await session.flush()
        return True


def _clear_outline_replan_resume_abandonment_sync(settings: AppSettings, slug: str) -> bool:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        return False
    try:
        return asyncio.run(_clear_outline_replan_resume_abandonment(settings, slug))
    except Exception:
        logger.exception("outline replan resume: failed to clear abandonment for slug=%s", slug)
        return False


def _clear_repair_resume_focus_pause_on_project(project: Any) -> bool:
    """Clear stale focus-pause metadata when a human explicitly resumes repair."""

    metadata = getattr(project, "metadata_json", None)
    if not isinstance(metadata, dict):
        return False

    if execution_terminal_code(metadata):
        return False
    focus_pause = metadata.get("focus_pause")
    focus_reason = ""
    if isinstance(focus_pause, dict):
        focus_reason = str(focus_pause.get("reason") or "").strip()
    production_reason = str(metadata.get("production_pause_reason") or "").strip()
    gate_reason = str(metadata.get("last_generation_gate_reason") or "").strip()
    reason = next(
        (
            value
            for value in (focus_reason, production_reason, gate_reason)
            if value.startswith("focus_")
        ),
        "",
    )
    if not reason and "focus_pause" not in metadata:
        return False

    updated = dict(metadata)
    updated["last_repair_resume_focus_pause_cleared_at"] = _utc_now()
    updated["last_repair_resume_focus_pause_reason"] = reason or focus_reason or "focus_pause"
    updated.pop("focus_pause", None)
    updated.pop("focus_primary_slug", None)
    for key in ("production_pause_reason", "last_generation_gate_reason"):
        if str(updated.get(key) or "").strip().startswith("focus_"):
            updated.pop(key, None)
    if production_reason.startswith("focus_") or focus_reason.startswith("focus_"):
        updated.pop("production_paused", None)
        updated.pop("paused_at", None)

    project.metadata_json = updated
    if (getattr(project, "status", None) or "").lower() == ProjectStatus.PAUSED.value:
        project.status = ProjectStatus.REVISING.value
    return True


async def _clear_repair_resume_focus_pause(
    settings: AppSettings,
    slug: str,
) -> bool:
    """Release stale focus pause before enqueueing an explicit repair resume."""

    if not slug:
        return False
    async with session_scope(settings) as session:
        project = await get_project_by_slug(session, slug)
        if project is None:
            return False
        changed = _clear_repair_resume_focus_pause_on_project(project)
        if changed:
            await session.flush()
        return changed


def _clear_repair_resume_focus_pause_sync(
    settings: AppSettings,
    slug: str,
) -> bool:
    if not slug:
        return False
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        logger.warning(
            "repair resume: cannot clear focus pause from an active event loop",
        )
        return False
    try:
        return asyncio.run(_clear_repair_resume_focus_pause(settings, slug))
    except Exception:
        logger.exception(
            "repair resume: failed to clear focus pause for slug=%s",
            slug,
        )
        return False


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
    settings = load_settings()
    catalog["genre_dimensions"] = get_genre_preset_dimensions()
    catalog["genre_creativity"] = get_genre_creativity_catalog_payload()
    hook_candidates: dict[str, object] = {}
    for item in catalog.get("genre_presets") or []:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "")
        genre = str(item.get("genre") or key)
        language = str(item.get("language") or "zh-CN")
        try:
            candidates = generate_hook_candidates(
                genre=genre,
                locale=language,
                count=max(1, int(getattr(settings.hook_engine, "quickstart_candidate_count", 4))),
                seed=int(hashlib.sha256(key.encode("utf-8")).hexdigest()[:8], 16),
                min_h_norm=float(getattr(settings.hook_engine, "min_h_norm", 30.0)),
            )
            serialized: list[dict[str, object]] = []
            for candidate in candidates:
                payload = candidate.model_dump(mode="json")
                # Attach the Chinese label so the UI never falls back to the
                # raw mechanism_key when the JS label map is missing an entry.
                try:
                    mech = get_mechanism(candidate.spec.mechanism_key)
                    payload["mechanism_label"] = mech.label
                except Exception:
                    payload["mechanism_label"] = candidate.spec.mechanism_key
                serialized.append(payload)
            hook_candidates[key] = serialized
        except Exception:
            logger.debug("Failed to generate quickstart hook candidates for %s", key, exc_info=True)
    catalog["hook_candidates"] = hook_candidates
    catalog["platform_presets"] = [
        _sanitize_preset_payload(item) if isinstance(item, dict) else item
        for item in (catalog.get("platform_presets") or [])
    ]
    catalog["genre_presets"] = [
        _sanitize_preset_payload(item) if isinstance(item, dict) else item
        for item in (catalog.get("genre_presets") or [])
    ]
    return catalog


def _genre_taxonomy_payload() -> dict[str, object]:
    """Canonical genre tree for the create wizard (channel→genre→sub→tags).

    The 62 legacy preset cards are returned as ``templates`` (each tagged with
    its canonical genre) so the front-end can render them as one-click "热门开局
    模板" that pre-fill the four axes.
    """
    from bestseller.services import genre_taxonomy as gt
    from bestseller.services.writing_presets import list_genre_presets

    tax = gt.load_genre_taxonomy()
    genres = [
        {
            "key": g.key,
            "label": g.label,
            "channel": list(g.channel),
            "heat": g.heat,
            "category_default": g.category_default,
            "pack_default": g.pack_default,
            "aliases": list(g.aliases),
            "sub_genres": [
                {
                    "key": s.key,
                    "label": s.label,
                    "category": s.category or g.category_default,
                    "pack": s.pack or g.pack_default,
                    "power_system": s.power_system,
                    "default_tags": list(s.default_tags),
                }
                for s in g.sub_genres
            ],
        }
        for g in tax.genres
    ]
    templates = [
        {
            "key": p.key,
            "name": p.name,
            "genre": p.genre,
            "sub_genre": p.sub_genre,
            "language": p.language,
            "suitable_for_short_story": p.suitable_for_short_story,
            "canonical_genre": gt.canonicalize(p.genre, p.sub_genre),
        }
        for p in list_genre_presets()
    ]
    return {
        "version": tax.version,
        "channels": [{"key": c.key, "label": c.label} for c in tax.channels],
        "genres": genres,
        "tags": sorted(gt.tag_pool()),
        "templates": templates,
    }


def _channel_of_genre(genre: str | None) -> str | None:
    """Derive the reader channel from the taxonomy for a free-form genre string.

    Every taxonomy genre carries exactly one channel, so the channel is a pure
    function of the genre. The legacy preset path used to hardcode ``None``
    here, which left ``contract.channel_key`` empty for every preset-created
    book. Best-effort: returns None when the genre does not resolve.
    """

    try:
        from bestseller.services.genre_taxonomy import _resolve_genre

        resolved = _resolve_genre(genre)
        if resolved is not None and resolved.channel:
            return str(resolved.channel[0])
    except Exception:  # pragma: no cover - never block creation on this
        logger.debug("channel derivation failed for genre=%r", genre, exc_info=True)
    return None


def _genre_preset_from_selection(selection: dict) -> object:
    """Build an ephemeral GenrePreset from a canonical taxonomy selection.

    Lets the quickstart handler accept a structured ``(channel, genre,
    sub_genre, tags)`` selection without rewiring everything downstream — the
    composed genre/sub-genre strings carry the choice and route correctly via
    the existing prompt-pack / category inference (+ canonicalize).
    """
    from bestseller.services.genre_taxonomy import resolve_selection
    from bestseller.services.writing_presets import GenrePreset

    channel = selection.get("channel")
    genre = selection.get("genre")
    sub = selection.get("sub_genre")
    tags = [t for t in (selection.get("tags") or []) if isinstance(t, str) and t.strip()]
    resolved = resolve_selection(channel, genre, sub, tags)

    genre_str = (resolved.genre_str or (genre or "自定义")).strip() or "自定义"
    sub_str = (resolved.sub_genre_str or genre_str).strip() or genre_str
    tag_str = "、".join(resolved.tags)
    description = f"{genre_str}题材" + (f"，主打{tag_str}" if tag_str else "") + "。"

    return GenrePreset(
        key=f"custom-{resolved.genre_key or 'genre'}",
        name=sub_str,
        genre=genre_str,
        sub_genre=sub_str,
        description=description,
        language="zh-CN",
        prompt_pack_key=resolved.pack,
        target_word_options=[],
        target_chapter_options=[120, 300, 600],
        suitable_for_short_story=False,
    )


def _quickstart_payload_has_genre(payload: dict) -> bool:
    """A quickstart request is valid if it carries a genre source.

    Either a legacy ``genre_key`` (热门开局模板) OR a structured taxonomy
    ``selection`` (频道·题材·子题材·标签 free picker). The task handler
    synthesises a preset from ``selection`` when ``genre_key`` is empty, so the
    HTTP gate must accept either — not just ``genre_key``.
    """
    if not isinstance(payload, dict):
        return False
    if payload.get("genre_key"):
        return True
    selection = payload.get("selection")
    return isinstance(selection, dict) and bool(selection)


def _persist_conception_log(
    settings: AppSettings,
    task_id: str,
    title: str | None,
    conception_log: object,
    hook_candidates: object = None,
    degradation_events: object = None,
) -> str | None:
    """构思中间产物落盘（成功与**失败**路径共用）。

    2026-08-18 定罪：首版只挂在成功路径上，而被榜单达标线拦截的构思——
    恰恰最需要审计的那种——什么都没留下。构思失败时这份 log 是唯一能回答
    「淘汰赛选了什么、为什么没达标、池子里有没有更好的」的证据。
    """

    if not conception_log:
        return None
    try:
        log_dir = Path(settings.output.base_dir) / "conception-logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / f"{task_id}.json"
        log_file.write_text(
            json.dumps(
                {
                    "task_id": task_id,
                    "title": title,
                    "conception_log": conception_log,
                    "hook_candidates": hook_candidates,
                    "degradation_events": degradation_events,
                },
                ensure_ascii=False,
                indent=1,
                default=str,
            ),
            encoding="utf-8",
        )
        return str(log_file)
    except Exception:
        logger.warning(
            "conception_log persistence failed for task %s (non-fatal)",
            task_id,
            exc_info=True,
        )
        return None


def _project_output_dir(settings: AppSettings, project_slug: str) -> Path:
    return (Path(settings.output.base_dir) / project_slug).resolve()


async def _load_chapter_prompt_manifest_payload(
    settings: AppSettings, project_slug: str, chapter_number: int
) -> dict[str, object]:
    """B3: writer Prompt Compiler kept/dropped blocks for one chapter."""
    from bestseller.services.prompt_manifest import load_chapter_prompt_manifest

    async with session_scope(settings) as session:
        return await load_chapter_prompt_manifest(
            session,
            project_slug=project_slug,
            chapter_number=chapter_number,
        )


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


def _model_catalog_payload(settings: AppSettings, slug: str | None = None) -> dict[str, object]:
    """Return the switchable model catalog plus the project's current selection."""
    from bestseller.services.model_catalog import load_model_catalog, selected_model_id, default_new_project_model_id

    selected: str | None = None
    if slug:

        async def _load_selected() -> str | None:
            async with session_scope(settings) as session:
                project = await get_project_by_slug(session, slug)
                if project is None:
                    return None
                return selected_model_id(project.metadata_json)

        selected = asyncio.run(_load_selected())
    return {
        "models": [entry.model_dump(mode="json") for entry in load_model_catalog()],
        "selected": selected,
        "default_model_id": default_new_project_model_id(settings),
    }


def _set_project_model_payload(
    settings: AppSettings,
    slug: str,
    model_id: str | None,
    task_manager: WebTaskManager | None = None,
) -> dict[str, object]:
    """Set (or clear when falsy) the project's selected catalog model id.

    When the DB project row does not exist yet — the book is still in its
    in-process conception phase, before materialization — the choice is stashed
    on the active task so the conception runner can fold it into the project
    metadata at creation time. This keeps the dashboard model picker usable from
    the moment the book card appears, rather than erroring until conception ends.
    """
    from bestseller.services.model_catalog import (
        PROJECT_MODEL_ID_KEY,
        get_model_catalog_entry,
    )

    normalized = (model_id or "").strip() or None
    if normalized is not None:
        entry = get_model_catalog_entry(normalized)
        if entry is None:
            raise ValueError(f"Unknown model id: {normalized}")
        if not entry.available:
            reason = entry.unavailable_reason or (f"set its API key ({entry.api_key_env}).")
            raise ValueError(f"Model '{entry.display_name}' is not available — {reason}")

    async def _apply() -> bool:
        async with session_scope(settings) as session:
            project = await get_project_by_slug(session, slug)
            if project is None:
                return False
            metadata = dict(project.metadata_json or {})
            if normalized is None:
                metadata.pop(PROJECT_MODEL_ID_KEY, None)
            else:
                metadata[PROJECT_MODEL_ID_KEY] = normalized
            project.metadata_json = metadata
            await session.commit()
            return True

    applied = asyncio.run(_apply())
    if applied:
        return {"slug": slug, "selected": normalized, "ok": True}
    # No DB project row yet — defer to the in-flight conception task if any.
    if task_manager is not None and task_manager.set_pending_project_model(slug, normalized):
        return {"slug": slug, "selected": normalized, "ok": True, "deferred": True}
    raise ValueError(f"Project '{slug}' was not found.")


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
        # 卡片不是事实源：委托给 worker 的卡可能是僵尸（job 被重启杀掉、
        # 重试超限后永不回写）。删除前按 DB + ARQ 队列对账一次，僵尸卡
        # 判死后重查；任何一侧探测失败都按"活着"处理，绝不误杀活任务。
        try:
            heal_job_active = any(
                slug in _fetch_heal_owned_slugs_by_kind(settings.redis.url, kind)
                for kind in ("autowrite", "repair", "outline_replan")
            )
        except Exception:
            heal_job_active = True

        async def _db_workflow_active() -> bool:
            from sqlalchemy import select

            from bestseller.infra.db.models import ProjectModel, WorkflowRunModel

            async with session_scope(settings) as session:
                project = (
                    await session.execute(select(ProjectModel.id).where(ProjectModel.slug == slug))
                ).scalar_one_or_none()
                if project is None:
                    return False
                running = (
                    await session.execute(
                        select(WorkflowRunModel.id)
                        .where(
                            WorkflowRunModel.project_id == project,
                            WorkflowRunModel.status.in_(
                                ("pending", "queued", "running", "machine_blocked")
                            ),
                        )
                        .limit(1)
                    )
                ).scalar_one_or_none()
                return running is not None

        try:
            db_workflow_active = asyncio.run(_db_workflow_active())
        except Exception:
            db_workflow_active = True
        reconciled = task_manager.reconcile_delegated_zombie_tasks(
            slug,
            db_workflow_active=db_workflow_active,
            heal_job_active=heal_job_active,
        )
        if reconciled:
            logger.warning(
                "project delete: reconciled %d zombie delegated task card(s) for %s",
                reconciled,
                slug,
            )
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


def _chapter_export_number(name: object) -> int | None:
    match = re.fullmatch(r"chapter-(\d{3,4})\.md", str(name or ""))
    if match is None:
        return None
    return int(match.group(1))


def _select_default_preview_entry(
    markdown_entries: list[dict[str, object]],
    *,
    latest_current_chapter_entry: dict[str, object] | None = None,
) -> dict[str, object] | None:
    """Choose the preview users expect to see after chapter generation.

    Project summaries historically picked the first markdown file alphabetically,
    which is often ``README.md``.  That made the UI look stale even when a new
    current chapter draft existed in the DB.  Prefer the DB-current chapter, then
    fall back to real chapter files, then project-level markdown.
    """
    if latest_current_chapter_entry is not None:
        return latest_current_chapter_entry
    chapter_entries = [
        item for item in markdown_entries if _chapter_export_number(item.get("name")) is not None
    ]
    if chapter_entries:
        return max(
            chapter_entries,
            key=lambda item: (
                _timestamp_seconds(item.get("modified_at")) or 0.0,
                _chapter_export_number(item.get("name")) or 0,
            ),
        )
    return next((item for item in markdown_entries if item["name"] == "project.md"), None) or (
        markdown_entries[0] if markdown_entries else None
    )


def _upsert_artifact_entry(
    entries: list[dict[str, object]],
    replacement: dict[str, object] | None,
) -> list[dict[str, object]]:
    if replacement is None:
        return entries
    replacement_name = str(replacement.get("name") or "")
    if not replacement_name:
        return entries
    merged: list[dict[str, object]] = []
    replaced = False
    for item in entries:
        if str(item.get("name") or "") == replacement_name:
            merged.append({**item, **replacement})
            replaced = True
        else:
            merged.append(item)
    if not replaced:
        merged.append(replacement)
    return sorted(merged, key=lambda item: str(item.get("name") or ""))


def resolve_project_artifact_path(
    settings: AppSettings,
    project_slug: str,
    artifact_name: str,
) -> Path:
    output_dir = _project_output_dir(settings, project_slug)
    requested = Path(str(artifact_name or "").strip())
    if requested.is_absolute() or any(part in {"", ".", ".."} for part in requested.parts):
        raise ValueError("Artifact path escapes the project output directory.")
    artifact_path = (output_dir / requested).resolve()
    if output_dir not in artifact_path.parents:
        raise ValueError("Artifact path escapes the project output directory.")
    # For chapter markdown files, always sync from DB so edits (dedup fixes,
    # quality rewrites) are reflected immediately without a full export run.
    fresh_content = _try_load_chapter_draft_from_db(settings, project_slug, requested.name)
    if fresh_content is not None:
        output_dir.mkdir(parents=True, exist_ok=True)
        # Only write if content changed to avoid unnecessary disk writes
        if not artifact_path.exists() or artifact_path.read_text(encoding="utf-8") != fresh_content:
            artifact_path.write_text(fresh_content, encoding="utf-8")
        return artifact_path
    if not artifact_path.exists() or not artifact_path.is_file():
        raise FileNotFoundError(
            f"Artifact '{requested.as_posix()}' was not found for '{project_slug}'."
        )
    return artifact_path


def _try_load_chapter_draft_from_db(
    settings: AppSettings,
    project_slug: str,
    artifact_name: str,
) -> str | None:
    m = re.match(r"chapter-(\d{3,4})\.md$", artifact_name)
    if m is None:
        return None

    async def _fetch_content() -> str | None:
        payload = await _async_load_chapter_draft_payload(settings, project_slug, artifact_name)
        if payload is None:
            return None
        return str(payload.get("content") or "")

    try:
        return asyncio.run(_fetch_content())
    except Exception:
        logger.warning(
            "Failed to load chapter draft from DB for %s/%s",
            project_slug,
            artifact_name,
            exc_info=True,
        )
        return None


async def _async_load_chapter_draft_payload(
    settings: AppSettings,
    project_slug: str,
    artifact_name: str,
) -> dict[str, object] | None:
    """Async core: fetch chapter draft payload dict from the DB.

    Returns *None* if the artifact name doesn't look like a chapter export or
    no draft is found.
    """
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

    def _content_has_chapter_heading(content_md: str, ch_number: int) -> bool:
        return bool(
            re.match(
                rf"^\s*#\s*第\s*{ch_number}\s*章(?:[：:].*)?$",
                content_md or "",
                flags=re.MULTILINE,
            )
        )

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
                select(ChapterDraftVersionModel)
                .where(ChapterDraftVersionModel.chapter_id == chapter.id)
                .order_by(
                    ChapterDraftVersionModel.is_current.desc(),
                    ChapterDraftVersionModel.version_no.desc(),
                    ChapterDraftVersionModel.created_at.desc(),
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        if draft is None:
            return None
        content_md = draft.content_md or ""
        if not _content_has_chapter_heading(content_md, chapter.chapter_number):
            heading = format_chapter_heading(
                chapter.chapter_number,
                chapter.title,
                language=proj.language,
            )
            content_md = f"{heading}\n\n{content_md}"
        return {
            "content": content_md,
            "version_no": int(draft.version_no or 0),
            "updated_at": draft.created_at.isoformat()
            if isinstance(draft.created_at, datetime)
            else None,
            "word_count": int(draft.word_count or 0),
            "chapter_title": chapter.title,
        }


def _try_load_chapter_draft_payload_from_db(
    settings: AppSettings,
    project_slug: str,
    artifact_name: str,
) -> dict[str, object] | None:
    """Return current chapter draft content and version metadata from the DB.

    Matches filenames like ``chapter-001.md``.  Returns *None* if the file
    name doesn't look like a chapter export or no draft is found.
    """
    m = re.match(r"chapter-(\d{3,4})\.md$", artifact_name)
    if m is None:
        return None
    chapter_number = int(m.group(1))
    try:
        return asyncio.run(_async_load_chapter_draft_payload(settings, project_slug, artifact_name))
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
    # Milestone events are the durable "where is this book in the pipeline"
    # axis. Unlike ``progress_events`` (a ring buffer capped at
    # ``_ACTIVITY_EVENT_CAP``), milestones are NOT evicted by the high-volume
    # chapter/scene/gate activity stream, so the front-end can always show the
    # full stage history even on a 1000-chapter run.
    milestone_events: list[dict[str, object]] = field(default_factory=list)
    result: dict[str, object] | None = None
    error: str | None = None
    cancel_requested: bool = False
    payload: dict[str, object] | None = None  # original payload for resume
    runtime_provenance: dict[str, object] | None = None

    def to_dict(self) -> dict[str, object]:
        title = self.title
        if self.status == "failed" and title and "·构思中" in title:
            title = title.replace("·构思中", "·构思失败", 1)
        return {
            "task_id": self.task_id,
            "task_type": self.task_type,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "project_slug": self.project_slug,
            "title": title,
            "current_stage": self.current_stage,
            "progress_events": list(self.progress_events),
            "milestone_events": list(self.milestone_events),
            "result": self.result,
            "error": self.error,
            "cancel_requested": self.cancel_requested,
            "payload": self.payload,
            "runtime_provenance": self.runtime_provenance,
        }

    def record_event(
        self,
        stage: str,
        payload: dict[str, object] | None = None,
        *,
        timestamp: str | None = None,
        milestone: bool = False,
    ) -> dict[str, object]:
        """Append a progress event, mirroring milestones to the durable axis.

        Every event lands in ``progress_events`` (capped ring buffer). When the
        event is a milestone — either ``milestone=True`` or its ``stage`` is in
        :data:`_MILESTONE_STAGES` — it is also appended to ``milestone_events``,
        which is only trimmed at a much larger defensive cap. The same event
        dict object is shared between both lists.
        """

        event: dict[str, object] = {
            "timestamp": timestamp or _utc_now(),
            "stage": stage,
            "payload": payload or {},
        }
        self.progress_events.append(event)
        if len(self.progress_events) > _ACTIVITY_EVENT_CAP:
            self.progress_events = self.progress_events[-_ACTIVITY_EVENT_CAP:]
        if milestone or stage in _MILESTONE_STAGES:
            self.milestone_events.append(event)
            if len(self.milestone_events) > _MILESTONE_EVENT_CAP:
                self.milestone_events = self.milestone_events[-_MILESTONE_EVENT_CAP:]
        return event


# Ring-buffer cap for the high-frequency activity stream (unchanged behaviour).
_ACTIVITY_EVENT_CAP = 300
# Defensive cap for the durable milestone axis; milestones are low-cardinality
# (a few hundred per book at most), this only guards against pathological runs.
_MILESTONE_EVENT_CAP = 2000

# Stages that belong on the durable milestone axis: pipeline phase boundaries,
# state transitions, and per-chapter completion. High-frequency, paired, or
# fine-grained events (chapter_pipeline_started, *_gate_evaluated, scene drafts)
# stay activity-only so they never evict milestones. Deep code can still force a
# milestone via the ``_tier`` payload hint regardless of this set.
_MILESTONE_STAGES: frozenset[str] = frozenset(
    {
        # terminal / state transitions
        "queued",
        "running",
        "completed",
        "failed",
        "incomplete",
        "cancelled",
        "cancel_requested",
        "resume_requested",
        "auto_resume_pending",
        "auto_resume_queued",
        "auto_resume_not_claimed",
        "delegated_to_worker_self_heal",
        "watchdog_failure_normalized",
        # blocked / machine-repair gates
        "blocked_structural_repair",
        "blocked_generation_gate",
        "machine_blocked",
        "machine_repair_required",
        # conception phase
        "conception_complete",
        "story_architect_complete",
        "methodology_selected",
        "heat_search_completed",
        "hook_candidates_generated",
        # planning phase
        "planning_started",
        "planning_completed",
        "foundation_planning_started",
        "foundation_planning_completed",
        "story_bible_materialization_started",
        "story_bible_materialization_completed",
        "outline_materialization_started",
        "outline_materialization_completed",
        "narrative_lines_repair_completed",
        "world_richness_repair_completed",
        # multi-volume + writing phase
        "progressive_autowrite_started",
        "volume_planning_started",
        "volume_planning_completed",
        "volume_writing_started",
        "volume_writing_completed",
        "volume_complete",
        "project_pipeline_started",
        "project_pipeline_completed",
        "chapter_pipeline_completed",
        "project_export_completed",
        # repair phase
        "project_repair_started",
        "project_repair_completed",
    }
)

_WATCHDOG_STALE_PREFIX = "Task watchdog: no progress for >"
_MACHINE_REPAIR_STAGES = frozenset(
    {
        "chapter_pipeline_machine_repair_required",
        "scene_pipeline_machine_repair_required",
        "machine_blocked",
        "machine_repair_required",
        "blocked_generation_gate",
        "repairable_auto_continue_pending",
        "exported_requires_machine_repair",
        "skipped_requires_machine_repair",
        "exported_requires_human_review",
        "skipped_requires_human_review",
        "draft_exported_requires_human_review",
    }
)
_ATTENTION_VERDICTS = frozenset(
    {
        "attention",
        "needs_attention",
        "machine_blocked",
        "machine_repair_required",
        "requires_machine_repair",
        "machine_repair_required",
        "requires_human_review",
        "exported_requires_machine_repair",
        "skipped_requires_machine_repair",
        "exported_requires_human_review",
        "skipped_requires_human_review",
        "draft_exported_requires_human_review",
    }
)
# Per-call wall-clock cap for conception LLM calls. Conception's calls are small
# structured proposals that normally finish in well under a minute; the generous
# ``planner`` role timeout (900s) only matters for large planning batches that
# run later. Capping conception keeps the worst-case stall (3 retries) at
# ~3×305s ≈ 915s — comfortably under the 2700s no-progress watchdog — so a
# stalled provider recovers or fails fast instead of stranding the book for 45m.
_CONCEPTION_CALL_TIMEOUT_SECONDS = 300.0
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

# A slug that already owns a queued/running card must not spawn a second one —
# returning the live card avoids a concurrent double-run on the same project.
_ACTIVE_TASK_STATUSES = frozenset({"queued", "running"})
# Stuck, non-terminal cards (a run that hit the machine-repair / attention gate,
# failed, or was cancelled by a structural-repair block). A fresh autowrite for
# the same slug reuses one of these in place instead of minting a new task_id,
# which is what previously let zombie cards pile up — e.g. 9 identical
# ``machine_repair_required`` cards for a single blocked book.
_REUSABLE_TASK_STATUSES = frozenset({"incomplete", "failed", "cancelled"})


def _task_has_machine_repair_gate(task: WebTaskState) -> bool:
    for event in reversed(task.progress_events or []):
        if not isinstance(event, dict):
            continue
        stage = str(event.get("stage") or "")
        if stage in _MACHINE_REPAIR_STAGES or "machine_repair" in stage:
            return True
        payload = event.get("payload")
        if not isinstance(payload, dict):
            continue
        if (
            payload.get("requires_machine_repair") is True
            or payload.get("requires_human_review") is True
        ):
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


def _payload_requires_machine_attention(payload: dict[str, Any]) -> bool:
    if (
        payload.get("requires_machine_repair") is True
        or payload.get("requires_human_review") is True
    ):
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


def _is_output_recovery_cache_record(item: Mapping[str, object]) -> bool:
    """Recognize this producer, never infer provenance from a prefix alone."""
    slug = str(item.get("project_slug") or "")
    result = item.get("result")
    if (not slug or item.get("task_id") != f"recovered-{slug}"
        or item.get("task_type") not in {"autowrite", "export_history"}
        or item.get("runtime_provenance") or not isinstance(result, Mapping)
        or result.get("project_slug") != slug):
        return False
    if result.get("recovered_from_output") is True:
        return True
    # Baseline producer v0 had exactly these three result keys. Its events
    # were bare file-derived started/completed records, without executor IDs.
    if set(result) == {"project_slug", "chapters_written", "target_chapters"}:
        events = item.get("progress_events")
        allowed_item_keys = {"task_id", "task_type", "status", "created_at", "updated_at", "project_slug",
                             "title", "current_stage", "progress_events", "milestone_events", "result",
                             "error", "cancel_requested", "payload", "runtime_provenance"}
        if (set(item) - allowed_item_keys or type(result.get("chapters_written")) is not int
            or result["chapters_written"] <= 0 or type(result.get("target_chapters")) is not int
            or result["target_chapters"] <= 0 or not isinstance(events, list)
            or len(events) != result["chapters_written"] + 1
            or not all(isinstance(e, Mapping) and set(e) == {"timestamp", "stage", "payload"} for e in events)):
            return False
        return (events[0].get("stage") == "project_pipeline_started"
                and events[0].get("payload") == {"chapter_count": result["target_chapters"]}
                and all(e.get("stage") == "chapter_pipeline_completed"
                        and isinstance(e.get("payload"), Mapping) and set(e["payload"]) == {"chapter_number"}
                        and type(e["payload"]["chapter_number"]) is int and e["payload"]["chapter_number"] > 0
                        for e in events[1:]))
    # Exact legacy output-scanner result shape, with no execution provenance.
    if (set(result) != {"project_slug", "chapters_written", "chapters_on_disk", "adopted", "project_exists", "target_chapters"}
        or result.get("adopted") is not False
        or type(result.get("project_exists")) is not bool
        or type(result.get("chapters_on_disk")) is not int
        or result["chapters_on_disk"] <= 0
        or type(result.get("target_chapters")) is not int
        or result.get("chapters_written") != (result["chapters_on_disk"] if result["project_exists"] else 0)):
        return False
    events = item.get("progress_events")
    if not isinstance(events, list) or not events or not all(isinstance(e, Mapping) for e in events):
        return False
    first = events[0]
    if first.get("stage") not in {"project_pipeline_started", "export_history_found"}:
        return False
    if first.get("payload") != {"chapter_count": result["target_chapters"]}:
        return False
    allowed = {"project_pipeline_started", "chapter_pipeline_completed", "export_history_found",
               "chapter_export_found", "blocked_structural_repair", "auto_resume_queued"}
    chapters = [e for e in events if e.get("stage") in {"chapter_pipeline_completed", "chapter_export_found"}]
    return (all(e.get("stage") in allowed for e in events)
            and len(chapters) == result["chapters_on_disk"]
            and all(set(e) == {"timestamp", "stage", "payload"} and isinstance(e.get("payload"), Mapping)
                    and set(e["payload"]) == {"chapter_number"} for e in chapters))


def _output_history_record(item: Mapping[str, object], *, archive_legacy: bool = False) -> dict[str, object]:
    """An export observation has no execution actor, business clock or resume payload."""
    result = dict(item.get("result") or {})
    if archive_legacy:
        result["legacy_cache_entry"] = json.loads(json.dumps(dict(item)))
    result.setdefault("chapters_on_disk", sum(e.get("stage") in {"chapter_pipeline_completed", "chapter_export_found"}
                                             for e in item.get("progress_events") or []))
    result.update(recovered_from_output=True, adopted=False, chapters_written=0,
                  export_recovery={"schema": "export-history.v2",
                                   "producer": "WebTaskManager._recover_tasks_from_output",
                                   "execution_evidence": False, "business_timestamps": "unknown"})
    events = [{"timestamp": None, "stage": "export_history_found",
               "payload": {"source": "output_export", "execution_evidence": False,
                           "chapters_on_disk": result.get("chapters_on_disk", 0)}}]
    for event in item.get("progress_events") or []:
        if event.get("stage") not in {"chapter_pipeline_completed", "chapter_export_found"}:
            continue
        source = dict(event.get("payload") or {})
        source.update(source="output_export", execution_evidence=False)
        if event.get("timestamp"):
            source["export_mtime_at"] = event["timestamp"]
        events.append({"timestamp": None, "stage": "chapter_export_found", "payload": source})
    return {**dict(item), "task_type": "export_history", "status": "history",
            "created_at": "", "updated_at": "", "payload": None, "cancel_requested": False,
            "current_stage": "export_history_found" if result.get("project_exists") else "export_history_unverified",
            "progress_events": events, "milestone_events": [], "result": result,
            "runtime_provenance": None, "error": None}


class WebTaskManager:
    def __init__(self, persist_path: Path | None = None) -> None:
        self._lock = threading.Lock()
        self._tasks: dict[str, WebTaskState] = {}
        self._executing_task_ids: set[str] = set()
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

    @staticmethod
    def _task_runtime_provenance(task_id: str, started_at: str) -> dict[str, object]:
        return task_runtime_provenance(task_id, started_at)

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
            with self._lock:
                task = self._tasks.get(task_id)
                if task is not None and execution_terminal_code(task.result or {}):
                    return
                self._executing_task_ids.add(task_id)
            self._check_cancelled(task_id)
            async def check_task_cancelled() -> None:
                self._check_cancelled(task_id)
            # asyncio.run copies this thread's context. Cancellation is checked
            # at each actual LLM attempt/backoff, not just progress callbacks.
            set_cancellation_checker(check_task_cancelled)
            scope = current_execution_scope() or asyncio.run(self._task_execution_scope(task_id))
            with bind_execution_scope(scope):
                worker(task_id, payload)
                raise_if_execution_stopped()
            # Budget errors can be swallowed by old advisory/static fallbacks.
            # Their persisted ledger state still wins over a completed card.
            asyncio.run(self._assert_task_execution_open(task_id))
        except TaskCancelledError as error:
            self._stop_execution_task(task_id, error)
        except ExecutionBudgetError as error:
            self._stop_execution_task(task_id, error)
        finally:
            with self._lock:
                self._executing_task_ids.discard(task_id)
            set_cancellation_checker(None)
            self._task_slots.release()

    # ── Disk persistence ──────────────────────────────────────────────────

    def _load_from_disk(self) -> None:
        """Restore tasks from a JSON file on disk."""
        if self._persist_path is None or not self._persist_path.exists():
            return
        try:
            original_bytes = self._persist_path.read_bytes()
            data = json.loads(original_bytes)
            changed = False
            legacy_records = [item for item in data if _is_output_recovery_cache_record(item)
                              and (item.get("result", {}).get("export_recovery") or {}).get("schema") != "export-history.v2"]
            if legacy_records:
                digest = hashlib.sha256(original_bytes).hexdigest()
                backup = self._persist_path.with_name(f"{self._persist_path.name}.before-export-recovery-{digest}.json")
                try:
                    try:
                        descriptor = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                        with os.fdopen(descriptor, "wb") as stream:
                            stream.write(original_bytes)
                    except FileExistsError:
                        if backup.read_bytes() != original_bytes:
                            raise OSError("export-recovery backup differs from original cache")
                        backup.chmod(0o600)
                except OSError as exc:
                    # Never overwrite the only original or auto-resume its synthetic payload.
                    raise RuntimeError("Cannot preserve original export-recovery task cache") from exc
            for item in data:
                if item in legacy_records:
                    item = _output_history_record(item, archive_legacy=True)
                    changed = True
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
                    milestone_events=item.get("milestone_events") or [],
                    result=item.get("result"),
                    error=item.get("error"),
                    cancel_requested=bool(item.get("cancel_requested", False)),
                    payload=item.get("payload"),
                    runtime_provenance=item.get("runtime_provenance"),
                )
                normalized_stage = _normalize_machine_repair_stage(str(task.current_stage or ""))
                if normalized_stage != task.current_stage:
                    task.current_stage = normalized_stage
                    changed = True
                if task.error == "project_repair_requires_attention":
                    task.error = "project_repair_requires_machine_repair"
                    changed = True
                normalized_events: list[dict[str, object]] = []
                events_changed = False
                for event in task.progress_events:
                    if not isinstance(event, dict):
                        normalized_events.append(event)
                        continue
                    normalized_event = dict(event)
                    stage = normalized_event.get("stage")
                    if isinstance(stage, str):
                        new_stage = _normalize_machine_repair_stage(stage)
                        if new_stage != stage:
                            normalized_event["stage"] = new_stage
                            events_changed = True
                    payload = normalized_event.get("payload")
                    if isinstance(payload, dict):
                        new_payload = _normalize_machine_repair_payload(payload)
                        if new_payload != payload:
                            normalized_event["payload"] = new_payload
                            events_changed = True
                    normalized_events.append(normalized_event)
                if events_changed:
                    task.progress_events = normalized_events[-300:]
                    changed = True
                if isinstance(task.payload, dict):
                    normalized_payload = _normalize_machine_repair_payload(task.payload)
                    if normalized_payload != task.payload:
                        task.payload = normalized_payload
                        changed = True
                terminal_code = execution_terminal_code(task.result or {})
                if terminal_code:
                    task.status = "cancelled" if terminal_code == "execution_cancelled" else "failed"
                    task.current_stage = terminal_code
                    changed = True
                if (
                    task.status == "failed"
                    and str(task.error or "").startswith(_WATCHDOG_STALE_PREFIX)
                    and _task_has_machine_repair_gate(task)
                ):
                    task.status = "incomplete"
                    task.current_stage = "machine_repair_required"
                    task.error = (
                        "Task reached a machine-repair or attention gate; "
                        "normalized from an old stale-watchdog failure."
                    )
                    task.record_event(
                        "watchdog_failure_normalized",
                        {"reason": "machine_repair_gate"},
                    )
                    changed = True
                # Running/queued tasks from a previous session need recovery.
                # If the task has a rebuildable payload, move it to queued and
                # stage it for auto-resume by ``serve_web`` once the event
                # loop is up; otherwise we have no way to continue, so mark
                # it failed and let the user decide.
                if task.status in ("running", "queued"):
                    if task.cancel_requested:
                        # A persisted cancellation is user intent, not a
                        # recoverable zombie.  Clearing this flag used to
                        # resurrect the pre-restart payload and could replay
                        # obsolete conception prompts into an already-cleaned
                        # project.
                        task.status = "cancelled"
                        task.current_stage = "cancelled"
                        task.error = task.error or "Task cancelled before server restart"
                        task.cancel_requested = False
                        task.record_event(
                            "cancelled_after_restart",
                            {"reason": "persisted_cancel_request"},
                        )
                    elif task.payload and task.task_type in ("autowrite", "repair"):
                        task.status = "queued"
                        task.current_stage = "auto_resume_pending"
                        task.error = None
                        task.cancel_requested = False
                        task.record_event(
                            "auto_resume_queued",
                            {"reason": "server restart"},
                        )
                        self._pending_auto_resume_ids.append(task.task_id)
                    else:
                        task.status = "failed"
                        task.current_stage = "failed"
                        task.error = task.error or (
                            "Server restarted while task was running (no payload to resume)"
                        )
                    changed = True
                self._tasks[task.task_id] = task
            if self._dedupe_loaded_active_tasks():
                changed = True
            # Persist the recovered state so the file on disk matches memory.
            # Without this, a crash before the next _save_to_disk would leave
            # orphaned "running" entries that never get cleaned up.
            if changed:
                self._save_to_disk()
        except (OSError, json.JSONDecodeError, KeyError):
            pass  # corrupt file — start fresh

    def _dedupe_loaded_active_tasks(self) -> bool:
        """Collapse duplicate recovered active cards for the same book.

        Disk persistence can contain several pre-crash cards for one slug. All
        would be normalized to ``queued`` on boot and compete for the same
        concurrency slots, producing a fake backlog. Keep the newest active
        card per task type + slug and drop the older zombies.
        """
        latest_by_owner: dict[tuple[str, str], WebTaskState] = {}
        duplicate_ids: set[str] = set()

        def rank(task: WebTaskState) -> tuple[float, str]:
            updated = _timestamp_seconds(task.updated_at)
            created = _timestamp_seconds(task.created_at)
            return (updated if updated is not None else created or 0.0, task.task_id)

        for task in self._tasks.values():
            if task.status not in _ACTIVE_TASK_STATUSES or not task.project_slug:
                continue
            key = (task.task_type, task.project_slug)
            incumbent = latest_by_owner.get(key)
            if incumbent is None:
                latest_by_owner[key] = task
                continue
            if rank(task) >= rank(incumbent):
                duplicate_ids.add(incumbent.task_id)
                latest_by_owner[key] = task
            else:
                duplicate_ids.add(task.task_id)

        if not duplicate_ids:
            return False
        for task_id in duplicate_ids:
            self._tasks.pop(task_id, None)
        pending: list[str] = []
        seen: set[str] = set()
        for task_id in self._pending_auto_resume_ids:
            if task_id in duplicate_ids or task_id in seen or task_id not in self._tasks:
                continue
            pending.append(task_id)
            seen.add(task_id)
        self._pending_auto_resume_ids = pending
        return True

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
        """Inventory export history, never infer execution/adoption from files.

        PostgreSQL supplies project identity only. Export count and mtime
        cannot establish completion, a business timestamp, or resume authority.
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
            if is_project_delete_tombstoned(settings, slug_dir.name):
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
                if project is not None and _project_library_archived(project.metadata_json):
                    continue

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

                status = "history"
                current_stage = "export_history_found" if project is not None else "export_history_unverified"

                # Directory mtime → timestamps
                try:
                    dir_mtime = slug_dir.stat().st_mtime
                except OSError:
                    dir_mtime = 0.0
                dir_ts = datetime.fromtimestamp(dir_mtime, UTC).isoformat()

                # These are file observations, not writing progress events.
                events: list[dict[str, object]] = [
                    {
                        "timestamp": None,
                        "stage": "export_history_found",
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
                            "stage": "chapter_export_found",
                            "payload": {"chapter_number": ch_num},
                        }
                    )
                task = WebTaskState(
                    task_id=f"recovered-{slug}",
                    task_type="export_history",
                    status=status,
                    created_at="",
                    updated_at="",
                    project_slug=slug,
                    title=title,
                    current_stage=current_stage,
                    progress_events=events,
                    result={
                        "project_slug": slug,
                        "chapters_written": 0,
                        "chapters_on_disk": num_chapters,
                        # A synthetic history card is never an adoption receipt,
                        # even when a corresponding project already exists.
                        "adopted": False,
                        "project_exists": project is not None,
                        "target_chapters": target_chapters,
                    },
                    payload=None,
                )
                task = WebTaskState(**_output_history_record(task.to_dict()))

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

    def _latest_task_for_slug(
        self,
        slug: str,
        statuses: frozenset[str],
    ) -> WebTaskState | None:
        """Most-recently-updated task for *slug* whose status is in *statuses*.

        Caller must hold ``self._lock``. Returns the live ``WebTaskState`` (not a
        dict) so callers can mutate it in place.
        """
        matches = [
            task
            for task in self._tasks.values()
            if task.project_slug == slug and task.status in statuses
        ]
        if not matches:
            return None
        matches.sort(key=lambda item: item.updated_at or item.created_at, reverse=True)
        return matches[0]

    def find_active_task_by_project_slug(self, slug: str) -> dict[str, object] | None:
        slug = str(slug or "").strip()
        if not slug:
            return None
        with self._lock:
            task = self._latest_task_for_slug(slug, _ACTIVE_TASK_STATUSES)
            return task.to_dict() if task is not None else None

    def set_pending_project_model(self, slug: str, model_id: str | None) -> bool:
        """Stash a per-book model choice on the active (conception) task.

        Used when the dashboard model picker is changed before the DB project
        row exists. The conception runner reads this via
        ``_pending_project_model`` at materialization time and folds it into the
        new project's metadata. Returns ``True`` when an active task was found
        and updated, ``False`` otherwise.
        """
        slug = str(slug or "").strip()
        if not slug:
            return False
        normalized = (model_id or "").strip() or None
        with self._lock:
            task = self._latest_task_for_slug(slug, _ACTIVE_TASK_STATUSES)
            if task is None:
                return False
            payload = dict(task.payload or {})
            if normalized is None:
                payload.pop("llm_model_id", None)
            else:
                payload["llm_model_id"] = normalized
            task.payload = payload
            self._save_to_disk()
            return True

    def _pending_project_model(self, task_id: str) -> str | None:
        """Read a per-book model choice stashed on the task payload (or None)."""
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None or not isinstance(task.payload, dict):
                return None
            value = task.payload.get("llm_model_id")
            return str(value).strip() or None if value else None

    def is_task_executing(self, task_id: str) -> bool:
        # Current-process ownership only: never serialized or inferred from a
        # cached running flag, so a restart cannot revive fake execution.
        with self._lock:
            return task_id in self._executing_task_ids

    def get_task(self, task_id: str) -> dict[str, object] | None:
        with self._lock:
            task = self._tasks.get(task_id)
            return task.to_dict() if task is not None else None

    def create_autowrite_task(self, payload: dict[str, object]) -> dict[str, object]:
        slug = str(payload.get("slug") or "").strip()
        title = str(payload.get("title") or "")
        serialized_payload = json.loads(json.dumps(payload, default=_json_default))
        with self._lock:
            # An autowrite already queued/running for this slug owns the project;
            # return it instead of spawning a competing second run.
            if slug:
                active = self._latest_task_for_slug(slug, _ACTIVE_TASK_STATUSES)
                if active is not None:
                    return active.to_dict()
            # Reuse a stuck card for the same slug in place (same task_id) rather
            # than minting a new one, so blocked books don't accumulate duplicate
            # zombie cards in the dashboard.
            reusable = self._latest_task_for_slug(slug, _REUSABLE_TASK_STATUSES) if slug else None
            now = _utc_now()
            if reusable is not None:
                task_id = reusable.task_id
                task = reusable
                task.status = "queued"
                task.current_stage = "queued"
                task.error = None
                task.result = None
                # A reusable card may contain an old machine-repair / gate
                # event. Keeping that history makes the self-heal scanner
                # classify the fresh run as incomplete immediately, so the
                # user can never actually restart writing. A new run gets a
                # fresh event stream; the prior task state remains in the DB
                # workflow/audit tables for diagnosis.
                task.progress_events = []
                task.cancel_requested = False
                task.updated_at = now
                task.runtime_provenance = self._task_runtime_provenance(task_id, now)
                if title:
                    task.title = title
                task.record_event("queued", {"reason": "reused_stuck_card"}, timestamp=now)
            else:
                armed_task_id = None
                if os.environ.get("BESTSELLER_ARMED_UI_ACCEPTANCE") == "1":
                    async def consume_acceptance():
                        from bestseller.services.independent_acceptance import consume_ui
                        async with session_scope(load_settings()) as acceptance_session:
                            return await consume_ui(acceptance_session.bind, serialized_payload)
                    armed_task_id = asyncio.run(consume_acceptance())
                task_id = armed_task_id or str(uuid4())
                task = WebTaskState(
                    task_id=task_id,
                    task_type="autowrite",
                    status="queued",
                    created_at=now,
                    updated_at=now,
                    project_slug=slug,
                    title=title,
                    current_stage="queued",
                    runtime_provenance=self._task_runtime_provenance(task_id, now),
                )
                self._tasks[task_id] = task
            # Persist the exact creation envelope before returning or starting
            # the worker.  A refresh/restart between enqueue and worker startup
            # must not turn a seeded book into an unseeded resume.
            task.payload = serialized_payload
            task.runtime_provenance = self._task_runtime_provenance(task_id, now)
            self._save_to_disk()
            snapshot = task.to_dict()
        thread = threading.Thread(
            target=self._run_with_slot,
            args=(task_id, self._run_autowrite_worker, serialized_payload),
            daemon=True,
        )
        thread.start()
        return snapshot

    async def rearm_authorized_budget_resume(self, task_id: str) -> bool:
        """Journal native authorization before rearming a stopped creation task."""
        from copy import deepcopy
        from bestseller.services.execution_budget_resume import (
            prepare_authorized_budget_resume,
        )

        with self._lock:
            task = self._tasks.get(task_id)
            if task is None or task_id in self._executing_task_ids:
                return False
            previous = deepcopy(task.to_dict())
        async with session_scope(load_settings()) as session:
            receipt = await prepare_authorized_budget_resume(
                session, task_id=task_id, task_snapshot=previous,
            )
        with self._lock:
            task = self._tasks.get(task_id)
            if (task is None or task.to_dict() != previous
                    or task_id in self._executing_task_ids):
                raise ValueError("task changed during budget authorization verification")
            result = deepcopy(task.result or {})
            result.pop("execution_terminal", None)
            if execution_terminal_code(result):
                raise ValueError("another execution stop cannot be superseded")
            task.record_event("execution_budget_authorization_applied", {
                "previous_terminal": deepcopy(previous.get("result")),
                "previous_error": previous.get("error"),
                "previous_stage": previous.get("current_stage"),
                "receipt": receipt,
            }, milestone=True)
            result["last_execution_budget_authorization"] = receipt
            task.result = result
            task.error = None
            task.current_stage = "execution_budget_authorized_manual_resume_required"
            task.updated_at = _utc_now()
            self._save_to_disk()
        return True

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
            if execution_terminal_code(task.result or {}):
                return task.to_dict()
            if task.status in ("running", "queued"):
                return "busy"
            # A resume reuses the original semantic inputs and spend history.
            # A new UI default must never silently reroll old paid candidates.
            from bestseller.services.production_strategy import freeze_resume_profile
            serialized_payload = freeze_resume_profile(task.payload, serialized_payload)
            if serialized_payload.get("_run_conception") and not delegate_to_self_heal:
                # A fresh conception retry must not append new provider receipts
                # to the terminal workflow returned by an old idempotency key.
                serialized_payload["_conception_attempt_id"] = f"{task_id}:retry:{uuid4()}"
            now = _utc_now()
            task.runtime_provenance = self._task_runtime_provenance(task_id, now)
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
                task.record_event(
                    "delegated_to_worker_self_heal",
                    {"reason": reason, "heal_owned": heal_owned},
                    timestamp=now,
                )
                self._save_to_disk()
                return task.to_dict()
            task.status = "queued"
            task.current_stage = "queued"
            task.error = None
            task.result = None
            task.cancel_requested = False
            task.updated_at = now
            task.payload = serialized_payload
            task.record_event("resume_requested", {}, timestamp=now)
            self._save_to_disk()
            task_snapshot = task.to_dict()
        thread = threading.Thread(
            target=self._run_with_slot,
            args=(task_id, self._run_autowrite_worker, serialized_payload),
            daemon=True,
        )
        thread.start()
        return task_snapshot

    async def approved_project_resume_payload(self, task_id: str, *, original=None, slug=None):
        if original is None:
            with self._lock:
                task = self._tasks.get(task_id)
                if task is None or task.status != "failed" or task.cancel_requested:
                    raise ValueError("stopped original task required")
                original = json.loads(json.dumps(task.payload, default=_json_default))
                slug = task.project_slug
        rebuilt = await _rebuild_payload_from_db(load_settings(), slug)
        if not rebuilt:
            raise ValueError("approved project payload unavailable")
        payload = _merge_resume_payload_with_project_truth(original, rebuilt)
        payload["slug"] = slug
        payload["_run_conception"] = False
        return payload

    def resume_approved_project(self, task_id: str) -> dict:
        from bestseller.services.execution_phase_handoff import create
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None or task.status != "failed" or task.cancel_requested:
                raise ValueError("stopped original task required")
            previous = {"result": task.result, "error": task.error, "updated_at": task.updated_at}
            async def prepare():
                payload = await self.approved_project_resume_payload(task_id,
                    original=json.loads(json.dumps(task.payload, default=_json_default)), slug=task.project_slug)
                async with session_scope(load_settings()) as session:
                    receipt = await create(session, task_id=task_id, payload=payload)
                    await session.commit()
                payload["_execution_phase_handoff"] = receipt["receipt_key"]
                return payload, receipt
            payload, receipt = asyncio.run(prepare())
            history = list(payload.get("_phase_handoff_control_history") or [])
            if not history or history[-1].get("receipt_key") != receipt["receipt_key"]:
                history.append({"receipt_key": receipt["receipt_key"], "previous_task": previous})
            payload["_phase_handoff_control_history"] = history
            task.payload = payload
            task.result = None
            task.error = None
            task.cancel_requested = False
            task.status = "queued"
            task.current_stage = "approved_project_phase_handoff"
            task.updated_at = _utc_now()
            task.record_event("approved_project_phase_handoff", {"receipt_key": receipt["receipt_key"],
                "canonical_id": receipt["binding"]["canonical_id"], "original_binding_preserved": True}, timestamp=task.updated_at)
            self._save_to_disk()
            result = task.to_dict()
        threading.Thread(target=self._run_with_slot,
            args=(task_id,self._run_autowrite_worker,payload),daemon=True).start()
        return result

    def resume_reviewed_conception(self, task_id: str) -> dict:
        from bestseller.services.conception_continuation import consume, pending, resume_allowed, resumable_control_error
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None or task.status not in {"failed", "cancelled"}:
                raise ValueError("stopped original task required")
            if not execution_terminal_code(task.result or {}) and not (
                task.payload.get("_active_continuation_scope")
                and resumable_control_error(task.error)
                and execution_terminal_code((task.payload.get("_prior_terminal_receipt") or {}).get("result") or {})):
                raise ValueError("original unknown terminal receipt required")
            payload = json.loads(json.dumps(task.payload, default=_json_default))
            old_terminal = {"result": task.result, "error": task.error,
                            "updated_at": task.updated_at}
            async def activate():
                async with session_scope(load_settings()) as session:
                    # A separately audited repair allocation freezes its predecessor;
                    # consuming it never reopens an unknown scope or slides time.
                    if (not payload.get("_active_continuation_scope")
                        or not resumable_control_error(task.error)):
                        if await pending(session.bind, task_id):
                            return await consume(session.bind, task_id, payload)
                    if payload.get("_active_continuation_scope"):
                        if not resumable_control_error(task.error):
                            raise ValueError("this terminal outcome needs separate reconciliation")
                        scope = await resume_allowed(session.bind, task_id, payload)
                        if "CONCEPT_RECONCILIATION_UNRESOLVED" in str(task.error):
                            from bestseller.services.conception_continuation import assert_reconciliation_recovery_source
                            await assert_reconciliation_recovery_source(session.bind, scope, payload)
                        if task.error == "execution_operation_identity_required":
                            from bestseller.services.conception_continuation import recover_completed_market_identity_stop
                            await recover_completed_market_identity_stop(session, task_id=task_id, scope=scope, payload=payload)
                            await session.commit()
                        return scope
                    return await consume(session.bind, task_id, payload)
            scope = asyncio.run(activate())
            payload["_active_continuation_scope"] = scope
            if "_prior_terminal_receipt" not in payload:
                payload["_prior_terminal_receipt"] = old_terminal
            else:
                payload["_continuation_control_failures"] = [
                    *(payload.get("_continuation_control_failures") or []), old_terminal]
            task.payload = payload
            task.result = None
            task.error = None
            task.cancel_requested = False
            task.status = "queued"
            task.current_stage = "reviewed_conception_continuation"
            task.updated_at = _utc_now()
            task.record_event("reviewed_conception_continuation", {"scope_id": scope,
                "unknown_preserved": True, "original_request_replayed": False}, timestamp=task.updated_at)
            self._save_to_disk()
            result = task.to_dict()
        threading.Thread(target=self._run_with_slot,
            args=(task_id,self._run_autowrite_worker,payload),daemon=True).start()
        return result

    def create_repair_task(self, payload: dict[str, object]) -> dict[str, object]:
        payload = dict(payload)
        payload.setdefault("include_pending_rewrite_tasks", True)
        task_id = str(uuid4())
        now = _utc_now()
        task = WebTaskState(
            task_id=task_id,
            task_type="repair",
            status="queued",
            created_at=now,
            updated_at=now,
            project_slug=str(payload.get("project_slug") or ""),
            title=f"Repair {payload.get('project_slug') or ''}",
            current_stage="queued",
            payload=json.loads(json.dumps(payload, default=_json_default)),
            runtime_provenance=self._task_runtime_provenance(task_id, now),
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

    def create_prewrite_repair_task(self, payload: dict[str, object]) -> dict[str, object]:
        task_id = str(uuid4())
        now = _utc_now()
        task = WebTaskState(
            task_id=task_id,
            task_type="prewrite_repair",
            status="queued",
            created_at=now,
            updated_at=now,
            project_slug=str(payload.get("project_slug") or ""),
            title=f"Prewrite repair {payload.get('project_slug') or ''}",
            current_stage="queued",
            payload=json.loads(json.dumps(payload, default=_json_default)),
            runtime_provenance=self._task_runtime_provenance(task_id, now),
        )
        with self._lock:
            self._tasks[task_id] = task
            self._save_to_disk()
        thread = threading.Thread(
            target=self._run_with_slot,
            args=(task_id, self._run_prewrite_repair_worker, payload),
            daemon=True,
        )
        thread.start()
        return task.to_dict()

    def create_chapter_revision_task(self, payload: dict[str, object]) -> dict[str, object]:
        task_id = str(uuid4())
        now = _utc_now()
        operation = str(payload.get("operation") or "")
        task = WebTaskState(
            task_id=task_id,
            task_type="chapter_revision",
            status="queued",
            created_at=now,
            updated_at=now,
            project_slug=str(payload.get("project_slug") or ""),
            title=(
                f"Chapter {payload.get('chapter_number') or ''} {operation or 'revision'}"
            ).strip(),
            current_stage="queued",
            payload=json.loads(json.dumps(payload, default=_json_default)),
            runtime_provenance=self._task_runtime_provenance(task_id, now),
        )
        with self._lock:
            self._tasks[task_id] = task
            self._save_to_disk()
        thread = threading.Thread(
            target=self._run_with_slot,
            args=(task_id, self._run_chapter_revision_worker, payload),
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
            "machine_blocked",
            "machine_repair_required",
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
            if execution_terminal_code(task.result or {}):
                # Retain late events for diagnosis, without reopening the task.
                task.record_event(stage, payload)
                self._save_to_disk()
                return
            task.updated_at = _utc_now()
            task.current_stage = stage
            normalized_stage = stage.lower()
            if task.status in {"failed", "incomplete", "queued"} and normalized_stage not in {
                "failed",
                "error",
                "cancelled",
                "machine_blocked",
                "machine_repair_required",
                "blocked_generation_gate",
            }:
                task.status = "running"
                task.error = None
            tier = payload.get(TIER_KEY) if isinstance(payload, dict) else None
            is_milestone = tier == TIER_MILESTONE or stage in _MILESTONE_STAGES
            task.record_event(stage, payload, timestamp=task.updated_at, milestone=is_milestone)
            # Persist immediately for key milestones; batch minor updates
            # to avoid excessive disk I/O during large pipelines.
            if (
                is_milestone
                or stage in self._PERSIST_STAGES
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
            if execution_terminal_code(task.result or {}):
                return
            task.status = "running"
            task.updated_at = _utc_now()
            task.current_stage = "running"
            task.error = None
            self._save_to_disk()

    def _mark_completed(self, task_id: str, result: dict[str, object]) -> None:
        with self._lock:
            task = self._tasks[task_id]
            if execution_terminal_code(task.result or {}):
                return
            task.updated_at = _utc_now()
            task.result = result
            terminal_code = execution_terminal_code(result)
            if terminal_code:
                task.status = "cancelled" if terminal_code == "execution_cancelled" else "failed"
                task.current_stage = terminal_code
                task.error = terminal_code
                self._save_to_disk()
                return
            from bestseller.services.autonomous_continuation import is_partial_window_result
            if is_partial_window_result(result) and not _payload_requires_machine_attention(result):
                task.status = "incomplete"
                task.current_stage = "continuation_required"
                task.error = None
            elif _payload_requires_machine_attention(result):
                task.status = "incomplete"
                task.current_stage = "machine_repair_required"
                task.error = str(
                    result.get("error")
                    or result.get("message")
                    or result.get("reason")
                    or "Task reached a machine-repair or attention gate."
                )
            else:
                task.status = "completed"
                task.current_stage = "completed"
                task.error = None
            self._save_to_disk()

    def _mark_failed(self, task_id: str, error: str) -> None:
        with self._lock:
            task = self._tasks[task_id]
            if execution_terminal_code(task.result or {}):
                return
            task.status = "failed"
            task.updated_at = _utc_now()
            task.current_stage = "failed"
            task.error = error
            # The placeholder is persisted before conception starts.  Leaving
            # “构思中” in a terminal failed card makes a durable failure look
            # like a job that survived a reboot and is still consuming time.
            if task.title and "·构思中" in task.title:
                task.title = task.title.replace("·构思中", "·构思失败", 1)
            self._save_to_disk()
        try:
            logger.error("Task %s failed: %s", task_id, error.splitlines()[-1] if error else "")
        except Exception:
            pass

    def _mark_execution_terminal(self, task_id: str, error: BaseException) -> None:
        """Durable receipt consumed by resume, restart and late-event paths."""
        code = execution_terminal_code(error)
        if code is None:
            raise ValueError("An execution terminal error is required")
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return
            task.result = {**(task.result or {}),
                           "execution_terminal": {"terminal": True, "code": code}}
            if isinstance(getattr(error, "diagnostic", None), dict):
                task.result["continuation_diagnostic"] = error.diagnostic
            task.status = "cancelled" if code == "execution_cancelled" else "failed"
            task.current_stage = code
            task.error = code
            task.updated_at = _utc_now()
            task.record_event(code, {"terminal": True, "code": code}, milestone=True)
            self._save_to_disk()

    def _stop_execution_task(self, task_id: str, error: BaseException) -> None:
        if getattr(error, "terminal", True) is False and execution_terminal_code(error) is None:
            with self._lock:
                task = self._tasks.get(task_id)
                if task is None or execution_terminal_code(task.result or {}):
                    return  # A late unsent error cannot supersede an earlier stop.
            # A definite unsent/busy admission is recoverable only while the
            # authoritative scope is still open; never clear a money fence.
            try:
                asyncio.run(self._assert_task_execution_open(task_id))
            except ExecutionBudgetError as current_error:
                error = current_error
            else:
                self._mark_failed(task_id, "请求未发出；排除本地故障后可恢复，已消耗次数保留。")
                with self._lock:
                    task = self._tasks.get(task_id)
                    if task is not None:
                        task.result = {**(task.result or {}), "execution_recoverable": {
                            "terminal": False, "code": getattr(error, "code", None)}}
                        task.record_event("provider_admission_failed", {"terminal": False,
                            "code": getattr(error, "code", None)})
                        self._save_to_disk()
                return
        self._mark_execution_terminal(task_id, error)
        with self._lock:
            task = self._tasks.get(task_id)
            slug = str(task.project_slug or "") if task is not None else ""
        if slug:
            try:
                from bestseller.worker.tasks import _persist_execution_terminal
                asyncio.run(_persist_execution_terminal(
                    slug, error, session_context=lambda: session_scope(load_settings())))
            except Exception:
                # Task receipt is already durable; never retry this operation
                # because reporting its terminal state failed.
                logger.exception("Could not persist project execution stop for %s", slug)

    async def _assert_task_execution_open(self, task_id: str) -> None:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None or execution_terminal_code(task.result or {}):
                return
            slug = str(task.project_slug or "")
        try:
            async with session_scope(load_settings()) as session:
                await assert_execution_scope_open(session, await self._task_execution_scope(task_id))
                if slug:
                    project = await get_project_by_slug(session, slug)
                    if project is not None:
                        await assert_execution_scope_open(session, project_execution_scope(project))
        except ExecutionBudgetError:
            raise
        except Exception as error:
            raise ExecutionBudgetError("execution_ledger_unavailable") from error

    async def _task_execution_scope(self, task_id: str, *, audit_only: bool = False) -> str:
        with self._lock:
            task = self._tasks.get(task_id)
            payload = dict(task.payload) if task else {}
            slug = str(task.project_slug or "") if task is not None else ""
        if payload.get("_active_continuation_scope"):
            from bestseller.services.conception_continuation import active_scope
            async with session_scope(load_settings()) as session:
                if audit_only:
                    return await active_scope(session.bind, task_id, payload, audit_only=True)
                return await active_scope(session.bind, task_id, payload)
        if slug:
            try:
                async with session_scope(load_settings()) as session:
                    project = await get_project_by_slug(session, slug)
                    if project is not None:
                        return project_execution_scope(project)
            except Exception as error:
                raise ExecutionBudgetError("execution_ledger_unavailable") from error
        return f"conception:{task_id}"

    async def execution_reconciliation(self, task_id: str, payload: dict | None = None,
                                       *, settings: AppSettings | None = None) -> dict:
        """Inspect/resolve paid unknown outcomes; no automatic task dispatch."""
        from copy import deepcopy
        from bestseller.services import historical_binding_unknown_recovery as historical
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                raise ValueError("task not found")
            if payload is not None and task.status in _ACTIVE_TASK_STATUSES:
                raise ValueError("stop the task before reconciling its remote outcome")
            slug = str(task.project_slug or "")
            previous_task = historical.task_snapshot(task)
            previous_full_task = deepcopy(task.to_dict())
        scope = await self._task_execution_scope(task_id, audit_only=True)
        effective_settings = settings or load_settings()
        evidence = ExecutionReconciliation.model_validate(payload) if payload is not None else None
        async with session_scope(effective_settings) as session:
            ledger = ExecutionLedger(session.bind, scope, effective_settings.budget)
            if evidence is None:
                return await ledger.reconciliation_status()
            receipt = await ledger.reconcile(evidence)
            # A replayed receipt must not clear a newly re-frozen scope. Query
            # the current authoritative status after the idempotent operation.
            current = await ledger.reconciliation_status()
            ready = (receipt["remaining_unknown"] == 0 and current["status"] == "active"
                     and current["execution_open"])
            project_ready = not slug
            ledger_stream_quarantine = False
            binding_reclassification = None
            projectless_quarantine_proof = None
            if ready and slug:
                from sqlalchemy import select
                from bestseller.infra.db.models import ProjectModel
                from bestseller.worker.tasks import clear_reconciled_execution_terminal
                project = await session.scalar(select(ProjectModel).where(ProjectModel.slug == slug).with_for_update())
                if project is not None:
                    binding_reclassification = await historical.derive_reclassification(
                        session, project=project, scope_id=scope, task_id=task_id,
                        task=previous_task, receipt=receipt, evidence=evidence)
                    previous_metadata = deepcopy(project.metadata_json)
                    if binding_reclassification:
                        with self._lock:
                            current_task = self._tasks.get(task_id)
                            if current_task is None or historical.task_snapshot(current_task) != previous_task:
                                binding_reclassification = None
                        if binding_reclassification:
                            historical.project_unknown(project, binding_reclassification)
                    project_ready = await clear_reconciled_execution_terminal(session, project, scope_id=scope, receipt=receipt)
                    if binding_reclassification and not project_ready:
                        project.metadata_json = previous_metadata
                        binding_reclassification = None
                    if project_ready:
                        from bestseller.services.failed_stream_recovery import verified_quarantine
                        ledger_stream_quarantine = await verified_quarantine(session,project=project,scope_id=scope,receipt=receipt)
                else:
                    from bestseller.services.conception_logline_recovery import verified_projectless_quarantine
                    from bestseller.services.prompt_source_policy import content_hash
                    projectless_quarantine_proof = await verified_projectless_quarantine(
                        session, effective_settings, task_id=task_id,
                        task_snapshot=previous_full_task, scope_id=scope, receipt=receipt,
                    )
                    project_ready = bool(
                        projectless_quarantine_proof
                        and projectless_quarantine_proof.get("task_id") == task_id
                        and projectless_quarantine_proof.get("scope_id") == scope
                        and projectless_quarantine_proof.get("task_snapshot_sha256") == content_hash(previous_full_task)
                    )
                ready = ready and project_ready
        if ready:
            with self._lock:
                task = self._tasks.get(task_id)
                task_code = execution_terminal_code(task.result or {}) if task is not None else None
                if projectless_quarantine_proof:
                    # The source reader is SELECT-only. Task ownership and the
                    # COMPLETE persisted projection must still match afterward.
                    if (task is None or task.to_dict() != previous_full_task
                            or task_id in self._executing_task_ids
                            or task_code != "execution_result_unknown"
                            or task.status != "failed" or task.cancel_requested):
                        ready = False
                legacy_proven = bool(binding_reclassification and task is not None
                    and task_code == historical.LEGACY_CODE
                    and historical.task_snapshot(task) == binding_reclassification["previous_task"])
                if task_code not in {None, "execution_result_unknown"} and not (
                    task_code == "execution_ledger_unavailable" and ledger_stream_quarantine) and not legacy_proven:
                    ready = False
                if ready and task is not None and (legacy_proven or task_code in {"execution_result_unknown", "execution_ledger_unavailable"}):
                    old_result = deepcopy(task.result or {})
                    event = {"previous_terminal": old_result.get("execution_terminal"), "receipt": receipt}
                    if projectless_quarantine_proof:
                        event["projectless_quarantine_proof"] = deepcopy(projectless_quarantine_proof)
                        event["previous_projection"] = {
                            key: deepcopy(previous_full_task.get(key)) for key in
                            ("status", "result", "error", "current_stage", "updated_at", "cancel_requested")
                        }
                        old_result["last_projectless_quarantine_proof"] = deepcopy(projectless_quarantine_proof)
                    if legacy_proven:
                        old_result[historical.HISTORY] = [*old_result.get(historical.HISTORY, []), binding_reclassification]
                        event["historical_reclassification"] = binding_reclassification
                    # Retain the original task terminal and controls before clearing.
                    task.record_event("execution_reconciled", event,
                                      milestone=legacy_proven or bool(projectless_quarantine_proof))
                    old_result.pop("execution_terminal", None)
                    # execution_terminal_code also recognises a top-level code.
                    old_result.pop("code", None)
                    old_result.pop("terminal", None)
                    if str(old_result.get("status") or "").startswith("execution_"):
                        old_result.pop("status", None)
                    task.result = {**old_result, "last_execution_reconciliation": receipt}
                    task.status = "failed"
                    task.current_stage = "execution_reconciled_manual_resume_required"
                    task.error = None
                    task.updated_at = _utc_now()
                    self._save_to_disk()
        return {**receipt, "ready_for_manual_resume": ready, "automatic_resume": False,
                **({"projectless_quarantine_proof": projectless_quarantine_proof}
                   if projectless_quarantine_proof else {})}

    async def recover_unsent_admission(self, task_id: str, payload: dict,
                                     *, settings: AppSettings | None = None) -> dict:
        """Explicit local migration; never launch or extend the original task."""
        from bestseller.services.unsent_admission_recovery import reconcile_confirmed_unsent_admission
        if set(payload) - {"expected_attempt_id", "dry_run"}:
            raise ValueError("unsupported unsent recovery field")
        if not isinstance(payload.get("expected_attempt_id"), str) or not payload["expected_attempt_id"]:
            raise ValueError("expected_attempt_id is required")
        dry_run = payload.get("dry_run", True)
        if type(dry_run) is not bool:
            raise ValueError("dry_run must be boolean")
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None or task.status != "failed" or task.cancel_requested:
                raise ValueError("only a stopped pre-project task can be migrated")
            before = task.to_dict()
            allocated_slug = str(task.project_slug or "")
            prior = (task.result or {}).get("last_unsent_admission_reconciliation")
            projection = (prior.get("previous_projection") if isinstance(prior, dict) else None)
            if projection is not None and execution_terminal_code(task.result or {}) not in {
                None, (prior.get("budget_at_recovery") or {}).get("block_code")
            }:
                raise ValueError("a later task stop cannot be superseded by an old migration")
            if projection is None:
                result = dict(task.result or {})
                terminal = result.pop("execution_terminal", None)
                if execution_terminal_code(result):
                    raise ValueError("another task stop cannot be superseded")
                projection = {"terminal": terminal, "current_stage": task.current_stage, "error": task.error}
            workflow_id = str((task.payload or {}).get("_conception_workflow_run_id") or "")
        async with session_scope(settings or load_settings()) as session:
            if allocated_slug and await get_project_by_slug(session, allocated_slug) is not None:
                raise ValueError("a materialized project requires project-level recovery")
            outcome = await reconcile_confirmed_unsent_admission(session, task_id=task_id,
                expected_attempt_id=payload["expected_attempt_id"], workflow_id=workflow_id,
                previous_projection=projection, dry_run=dry_run)
        block = outcome["current_budget"]["block_code"]
        if not dry_run:
            with self._lock:
                task = self._tasks.get(task_id)
                if task is None or task.to_dict() != before:
                    raise ValueError("task changed during recovery; receipt retained, inspect before retrying")
                renewed = (outcome.get("time_renewal_receipt") is not None
                    and (task.result or {}).get("last_execution_time_renewal") != outcome["time_renewal_receipt"])
                if (task.result or {}).get("last_unsent_admission_reconciliation") != outcome["receipt"] or renewed:
                    result = dict(task.result or {})
                    result.pop("execution_terminal", None)
                    result.pop("execution_recoverable", None)
                    result["last_unsent_admission_reconciliation"] = outcome["receipt"]
                    if renewed:
                        result["last_execution_time_renewal"] = outcome["time_renewal_receipt"]
                    if block:
                        result["execution_terminal"] = {"terminal": True, "code": block}
                    else:
                        result["execution_recoverable"] = {"terminal": False, "code": "execution_provider_not_sent"}
                    task.result = result
                    task.current_stage = block or "provider_admission_reconciled_manual_resume_required"
                    task.error = block
                    task.updated_at = _utc_now()
                    task.record_event("execution_time_renewal_applied" if renewed else "unsent_admission_reconciled", {
                        "receipt_key": outcome["receipt_key"], "previous_projection": projection,
                        "previous_task_terminal": (before.get("result") or {}).get("execution_terminal"),
                        "current_block_code": block, "automatic_resume": False}, milestone=True)
                    self._save_to_disk()
        with self._lock:
            task = self._tasks.get(task_id)
            projected_block = execution_terminal_code(task.result or {}) if task is not None else "task_missing"
        return {**outcome, "task_projection_block_code": projected_block,
            "ready_for_manual_resume": block is None and (dry_run or projected_block is None)}

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
            task.record_event(
                "blocked_structural_repair",
                {"reason": error},
                timestamp=task.updated_at,
            )
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
            task.record_event(stage, {"reason": error}, timestamp=task.updated_at, milestone=True)
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
            # Deliberately not gated on the task looking live. A card reads
            # ``incomplete`` once its ARQ job is gone, but the book itself is
            # usually still being driven by self-heal at exactly that point —
            # which is when the user most wants Stop to work. Refusing there
            # made the button do nothing on the runs it was needed for.
            if task.status in ("completed", "failed", "cancelled"):
                return False
            force = bool(task.cancel_requested)
            task.cancel_requested = True
            task.updated_at = _utc_now()
            task.record_event("cancel_requested", {"force": force}, timestamp=task.updated_at)
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
            task.result = {**(task.result or {}), "execution_terminal": {
                "terminal": True, "code": "execution_cancelled"}}
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

    def delete_tasks_by_project(
        self,
        project_slug: str,
        *,
        include_active: bool = False,
    ) -> int:
        """Delete all task records associated with *project_slug*.

        Running / queued tasks are kept by default — the caller is expected to
        verify that no active tasks exist for the project before invoking this.
        Set ``include_active`` only when the project is being archived or
        deleted and the task card should disappear regardless of its last
        recovered status.
        Returns the number of task records removed.
        """
        if not project_slug:
            return 0
        removed = 0
        with self._lock:
            survivors: dict[str, WebTaskState] = {}
            for tid, t in self._tasks.items():
                if t.project_slug == project_slug and (
                    include_active or t.status not in ("running", "queued")
                ):
                    removed += 1
                    continue
                survivors[tid] = t
            if removed:
                self._tasks = survivors
                self._save_to_disk()
        if removed:
            logger.info("Deleted %d task record(s) for project %s", removed, project_slug)
        return removed

    def delete_tasks_by_projects(
        self,
        project_slugs: set[str],
        *,
        include_active: bool = False,
    ) -> int:
        removed = 0
        for slug in sorted(project_slugs):
            removed += self.delete_tasks_by_project(slug, include_active=include_active)
        return removed

    def has_active_task_for_project(self, project_slug: str) -> bool:
        if not project_slug:
            return False
        with self._lock:
            return any(
                t.project_slug == project_slug and t.status in ("running", "queued")
                for t in self._tasks.values()
            )

    def reconcile_delegated_zombie_tasks(
        self,
        project_slug: str,
        *,
        db_workflow_active: bool,
        heal_job_active: bool,
    ) -> int:
        """Fail delegated task cards whose worker-side execution no longer exists.

        2026-08-18 定罪（custom-xuanhuan-1787015909）：重启把委托给 worker 的
        自愈 job 杀了（``max retries exceeded``），它再也不会回来更新卡片——
        卡片永远停在 running，``has_active_task_for_project`` 因此永远拒绝删书。
        卡片不是事实源；执行活不活要问 DB（running workflow）和 ARQ 队列
        （heal job key）。两边都说死了，卡片才许被判死——任何一边活着都不动
        （fail-safe：探测失败的一侧由调用方按 active=True 传入）。

        只动**委托型**卡片（progress 里出现过 ``delegated_to_worker_self_heal``）：
        in-process 跑者（如构思期任务）的执行体是 web 自己的线程，DB/队列探测
        看不见它，误判会把活任务的卡标死而线程还在写。
        """

        if not project_slug or db_workflow_active or heal_job_active:
            return 0
        changed = 0
        with self._lock:
            for task in self._tasks.values():
                if task.project_slug != project_slug:
                    continue
                if task.status not in ("running", "queued"):
                    continue
                delegated = any(
                    isinstance(event, dict)
                    and event.get("stage") == "delegated_to_worker_self_heal"
                    for event in task.progress_events
                )
                if not delegated:
                    continue
                task.status = "failed"
                task.current_stage = "delegated_job_lost"
                task.error = (
                    "Delegated worker self-heal job is gone (no running DB "
                    "workflow, no queued ARQ job) — card reconciled from zombie."
                )
                task.cancel_requested = False
                task.record_event(
                    "delegated_job_lost_reconciled",
                    {"db_workflow_active": False, "heal_job_active": False},
                )
                changed += 1
            if changed:
                self._save_to_disk()
        return changed

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

        Active tasks and recoverable original autowrite envelopes are kept.
        A nonempty autowrite payload is retained until the result explicitly
        proves whole-book completion, even while book identity is unresolved.
        Other tasks retain the existing age-based eviction policy.

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
                if (
                    t.task_type == "autowrite"
                    and isinstance(t.payload, dict)
                    and t.payload
                    and not (
                        isinstance(t.result, dict)
                        and t.result.get("whole_book_complete") is True
                    )
                ):
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

        Export mtimes are diagnostic only: a reader GET can rebuild those
        files. Only independent worker ownership can rescue a stale task.

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
            if self._mark_machine_repair_gate_incomplete(tid):
                continue
            self._mark_failed(
                tid,
                f"Task watchdog: no progress for >{stale_after_seconds}s, marking as failed",
            )
            stale_count += 1
        return stale_count

    def _mark_machine_repair_gate_incomplete(self, task_id: str) -> bool:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None or not _task_has_machine_repair_gate(task):
                return False
            task.status = "incomplete"
            task.current_stage = "machine_repair_required"
            task.error = "Task is waiting for machine repair or attention-gate repair."
            task.updated_at = _utc_now()
            task.record_event(
                "machine_repair_required",
                {"reason": "watchdog_preserved_attention_gate"},
                timestamp=task.updated_at,
            )
            self._save_to_disk()
        logger.info("Task %s is waiting for machine repair; marked incomplete", task_id)
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
            task.record_event(
                "auto_resume_not_claimed",
                {"reason": "worker self-heal job no longer active"},
                timestamp=now,
            )
            self._save_to_disk()
        logger.info("Task %s no longer has a worker self-heal owner; marked incomplete", task_id)
        return True

    def _rescue_from_disk_heartbeat(
        self,
        task_id: str,
        stale_after_seconds: int,
    ) -> bool:
        """Observe recent exports for diagnosis; they never prove worker liveness."""
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
            logger.info(
                "Task %s has a recent export (%.0fs ago); file mtime is not execution heartbeat",
                task_id,
                now_ts - latest,
            )
            return False
        except Exception:
            logger.exception("export diagnostic scan failed for %s", task_id)
            return False

    def auto_resume_zombies(
        self,
        redis_url: str | None = None,
        settings: AppSettings | None = None,
    ) -> list[str]:
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
        if not pending:
            return []

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
        with self._lock:
            pending_slugs = {
                str(self._tasks[task_id].project_slug or "").strip()
                for task_id in pending
                if task_id in self._tasks and str(self._tasks[task_id].project_slug or "").strip()
            }
        active_workflow_slugs: set[str] = set()
        existing_project_slugs: set[str] = set()
        if settings is not None and pending_slugs:
            try:
                active_workflow_slugs = asyncio.run(
                    _load_active_workflow_slugs_by_slug(settings, pending_slugs)
                )
            except RuntimeError:
                logger.warning(
                    "Zombie auto-resume could not inspect active DB workflows "
                    "from an active event loop"
                )
            except Exception:
                logger.exception("Zombie auto-resume failed to inspect active DB workflows")
            # Which pending slugs already have a project row? A conception-phase
            # zombie has none — worker self-heal (keyed on an existing project)
            # can never recover it, so it must be re-run in-process instead.
            try:
                existing_project_slugs = asyncio.run(
                    _load_existing_project_slugs(settings, pending_slugs)
                )
            except RuntimeError:
                logger.warning(
                    "Zombie auto-resume could not inspect existing projects "
                    "from an active event loop"
                )
            except Exception:
                logger.exception("Zombie auto-resume failed to inspect existing projects")

        delegated: list[str] = []
        for task_id in pending:
            repair_payload: dict[str, object] | None = None
            enqueue_autowrite_slug: str | None = None
            conception_resume_payload: dict[str, object] | None = None
            with self._lock:
                task = self._tasks.get(task_id)
                if task is None or not task.payload:
                    continue
                if execution_terminal_code(task.result or {}):
                    continue
                if task.status != "queued":
                    # Already transitioned (e.g. user cancelled between
                    # startup and this call) — skip.
                    continue
                if task.cancel_requested:
                    # Cancellation can arrive after disk recovery queued the
                    # task but before the auto-resume sweep claims it.  Never
                    # turn that race into a resumed writer.
                    task.status = "cancelled"
                    task.current_stage = "cancelled"
                    task.error = task.error or "Task cancelled before auto-resume"
                    task.cancel_requested = False
                    task.record_event(
                        "cancelled_before_auto_resume",
                        {"reason": "cancel_request_won_startup_race"},
                    )
                    self._save_to_disk()
                    continue
                slug = (task.project_slug or "").strip()
                if task.task_type == "repair":
                    heal_owned = bool(slug and slug in repair_heal_owned_slugs)
                else:
                    heal_owned = bool(
                        slug
                        and (slug in autowrite_heal_owned_slugs or slug in repair_heal_owned_slugs)
                    )
                db_workflow_active = bool(slug and slug in active_workflow_slugs)
                if task.task_type == "repair" and not heal_owned:
                    task.current_stage = "queued"
                    task.error = None
                    task.cancel_requested = False
                    task.record_event("resume_requested", {"reason": "server restart"})
                    repair_payload = dict(task.payload)
                    self._save_to_disk()
                    delegated.append(task_id)
                    logger.info("Resuming repair zombie task %s via web worker", task_id)
                elif (
                    task.task_type == "autowrite"
                    and settings is not None
                    and slug
                    and slug not in existing_project_slugs
                    and bool(task.payload.get("_run_conception"))
                ):
                    # Conception-phase zombie: no project row exists yet, so
                    # worker self-heal (which resumes a stored project keyed on
                    # slug) has nothing to recover. The conception inputs
                    # (premise / hooks / concept bundle) live ONLY in this web
                    # task payload, so re-run conception in-process — this is
                    # what extends the closed loop to the pre-project phase.
                    conception_resume_payload = dict(task.payload)
                    conception_resume_payload["_run_conception"] = True
                    task.status = "queued"
                    task.current_stage = "queued"
                    task.error = None
                    task.cancel_requested = False
                    task.record_event(
                        "resume_requested",
                        {"reason": "conception-phase auto-resume", "phase": "conception"},
                    )
                    self._save_to_disk()
                    logger.info(
                        "Resuming conception-phase zombie task %s (slug=%s) in-process",
                        task_id,
                        slug,
                    )
                elif heal_owned or db_workflow_active:
                    reason = (
                        "DB workflow already active"
                        if db_workflow_active and not heal_owned
                        else "ARQ heal job already active"
                    )
                    task.status = "running"
                    task.current_stage = "delegated_to_worker_self_heal"
                    task.error = None
                    task.cancel_requested = False
                    task.record_event(
                        "delegated_to_worker_self_heal",
                        {
                            "reason": reason,
                            "heal_owned": heal_owned,
                            "db_workflow_active": db_workflow_active,
                        },
                    )
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
                    task.record_event(
                        "auto_resume_not_claimed",
                        {"reason": reason, "heal_owned": False},
                    )
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
                        task.record_event(
                            "delegated_to_worker_self_heal",
                            {
                                "reason": "web auto-resume enqueued worker self-heal job",
                                "heal_owned": True,
                                "enqueued_by_web": True,
                                "job_id": job_id,
                            },
                            timestamp=now,
                        )
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
                        task.record_event(
                            "auto_resume_not_claimed",
                            {
                                "reason": reason,
                                "heal_owned": False,
                                "enqueue_failed": True,
                            },
                        )
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
            if conception_resume_payload is not None:
                thread = threading.Thread(
                    target=self._run_with_slot,
                    args=(task_id, self._run_autowrite_worker, conception_resume_payload),
                    daemon=True,
                )
                thread.start()
                delegated.append(task_id)

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
                payload.get("user_hints") if isinstance(payload.get("user_hints"), dict) else None
            )
            conception_brief: dict[str, object] | None = None
            conception_log: list[dict[str, object]] | None = None
            conception_hook_spec: dict[str, object] | None = None
            conception_contract: dict[str, object] | None = None
            conception_methodology: dict[str, object] | None = None
            conception_hook_candidates: list[dict[str, object]] | None = None
            effective_synopsis = ""
            effective_tags: list[str] = []
            conception_result = None
            # Must be initialised here (not only inside ``if run_conception``):
            # on RESUME the project already exists so ``_run_conception`` is
            # False, the conception block is skipped, and the later
            # ``if conception_story_appeal`` reference would raise
            # UnboundLocalError and fail the whole resumed task.
            conception_story_appeal: dict[str, object] | None = None
            conception_motif_amplification: dict[str, object] | None = None
            conception_title_tournament: dict[str, object] | None = None
            conception_default_family: dict[str, object] | None = None
            conception_degradation: list[dict[str, object]] = []
            story_facets_obj = None
            from bestseller.services.genre_intent_contract import contract_from_payload

            genre_intent_contract = contract_from_payload(payload)
            # Fail closed (P11): the quickstart boundary always persists a
            # validated genre_intent_contract on the task envelope. If that
            # envelope re-reads as invalid here, it is corruption, not absence —
            # silently continuing would drop EVERY user creation option with no
            # recoverable path. Absence (legacy/resume rebuilds) stays valid.
            if genre_intent_contract is None and isinstance(
                payload.get("genre_intent_contract"), dict
            ):
                raise ValueError(
                    "genre_intent_contract in the task envelope is present but invalid; "
                    "refusing to continue without the user's creation intent. "
                    "Recreate the book (or fix the persisted contract) before retrying."
                )
            creation_intent_contract = None
            conception_attempt = None
            if run_conception and isinstance(payload.get("creation_intent_contract"), dict):
                from bestseller.services.creation_intent_contract import (
                    ConceptionAttemptInput,
                )
                from bestseller.services.creation_intent_contract import (
                    contract_from_payload as load_creation_intent,
                )

                creation_intent_contract = load_creation_intent(payload)
                source_conception_workflow_run_id = payload.get("_conception_workflow_run_id")
                attempt_id = (
                    f"{task_id}:resume:{uuid4().hex}"
                    if source_conception_workflow_run_id
                    else str(payload.get("_conception_attempt_id") or f"{task_id}:initial")
                )
                conception_attempt = ConceptionAttemptInput(
                    contract=creation_intent_contract,
                    input_payload={
                        "premise": payload.get("premise"),
                        "user_hints": payload.get("user_hints") or {},
                        "genre": payload.get("genre"),
                        "sub_genre": payload.get("sub_genre"),
                        "target_chapters": payload.get("target_chapters"),
                    },
                    attempt_id=attempt_id,
                    idempotency_key=attempt_id,
                )

            # Use a single session scope for both conception and autowrite
            # to ensure transactional consistency.
            async with session_scope(settings) as session:
                conception_workflow_run = None
                conception_checkpoint = None
                conception_replay_context = None
                conception_input_hash = None
                if run_conception:
                    from bestseller.domain.enums import WorkflowStatus

                    if creation_intent_contract is not None and hasattr(session, "scalar"):
                        from bestseller.services.workflows import create_workflow_run
                        from bestseller.services.conception_checkpoints import (
                            load_conception_checkpoint,
                            load_replay_context, save_replay_context,
                            stable_conception_input_hash,
                        )

                        conception_input_hash = stable_conception_input_hash(conception_attempt)
                        source_run_id = payload.get("_conception_workflow_run_id")
                        if source_run_id:
                            resume_project = await get_project_by_slug(session, str(payload.get("slug") or ""))
                            checkpoint_read_binding = {
                                "expected_project_id": resume_project.id if resume_project else None,
                                "expected_intent_hash": creation_intent_contract.contract_hash(),
                            }
                            conception_checkpoint = await load_conception_checkpoint(
                                session,
                                workflow_run_id=UUID(str(source_run_id)),
                                input_hash=conception_input_hash,
                                **checkpoint_read_binding,
                            )

                            conception_replay_context = await load_replay_context(
                                session, workflow_run_id=UUID(str(source_run_id)), input_hash=conception_input_hash,
                                **checkpoint_read_binding)

                        attempt_id = conception_attempt.attempt_id
                        conception_workflow_run = await create_workflow_run(
                            session,
                            project_id=None,
                            workflow_type="conception_initial",
                            status=WorkflowStatus.RUNNING,
                            scope_type="creation",
                            scope_id=None,
                            requested_by="web-ui",
                            current_step="conception",
                            idempotency_key=attempt_id,
                            metadata={
                                "execution_scope_id": current_execution_scope(),
                                "attempt_id": attempt_id,
                                "creation_intent_hash": creation_intent_contract.contract_hash(),
                                "creation_intent_schema": creation_intent_contract.schema_version,
                                "input_hash": conception_attempt.input_hash(),
                                "conception_checkpoint_input_hash": conception_input_hash,
                                "source_conception_workflow_run_id": str(source_run_id)
                                if source_run_id
                                else None,
                            },
                        )
                        if conception_checkpoint is not None:
                            from bestseller.services.conception_checkpoints import (
                                save_conception_checkpoint,
                            )

                            await save_conception_checkpoint(
                                session,
                                workflow_run_id=conception_workflow_run.id,
                                checkpoint={
                                    **conception_checkpoint,
                                    "source_workflow_run_id": str(source_run_id),
                                },
                            )
                        if conception_replay_context is not None:
                            await save_replay_context(session, workflow_run_id=conception_workflow_run.id,
                                                      record=conception_replay_context)
                        # LLM receipts are written by separate provider/session
                        # paths and reference this workflow row.  Commit the
                        # run before the first conception call so its FK is
                        # visible to those paths.
                        raise_if_execution_stopped()
                        await session.commit()
                        payload["_conception_workflow_run_id"] = str(conception_workflow_run.id)
                        with self._lock:
                            task = self._tasks.get(task_id)
                            if task is not None:
                                task.payload = json.loads(json.dumps(payload, default=_json_default))
                                self._save_to_disk()
                    from bestseller.services.conception import (
                        run_conception_pipeline,
                    )
                    from bestseller.services.llm import bind_conception_model

                    # Per-book model chosen at creation/conception time. Bound
                    # for the conception phase so its project-less LLM calls use
                    # the selected model too (planning/writing already resolve it
                    # from project metadata). Reset automatically on scope exit.
                    _conception_model = self._pending_project_model(task_id)
                    genre_key = str(payload.get("_genre_key", ""))
                    if genre_key:
                        # ── Story Architect: generate StoryFacets before conception ──
                        story_facets_obj = None
                        try:
                            from bestseller.services.production_strategy import (
                                protect_architect_headroom,
                            )
                            from bestseller.services.story_architect import (
                                architect_story_facets,
                            )

                            # The validated pre-tournament replay context already
                            # freezes facets. Regenerating them would spend on an
                            # unused proposal before restoring that context.
                            if conception_checkpoint is None and conception_replay_context is None:
                                with protect_architect_headroom(
                                    (user_hints or {}).get("production_profile"),
                                    int(payload["target_chapters"]),
                                    stop_after_conception=bool(
                                        payload.get("stop_after_conception", False)
                                    ),
                                ), bind_conception_model(
                                    _conception_model,
                                    call_timeout_seconds=_CONCEPTION_CALL_TIMEOUT_SECONDS,
                                    task_id=task_id,
                                    workflow_run_id=(
                                        getattr(conception_workflow_run, "id", None)
                                    ),
                                ):
                                    story_facets_obj = await architect_story_facets(
                                        session,
                                        settings,
                                        primary_genre=genre_key,
                                        language=str(payload.get("language", "zh-CN")),
                                        genre_key=genre_key,
                                        user_hints=user_hints,
                                        genre_intent=genre_intent_contract,
                                    )
                            else:
                                progress("story_architect_skipped_checkpoint", {
                                    "source_conception_workflow_run_id": source_conception_workflow_run_id,
                                })
                            if conception_checkpoint is None and conception_replay_context is None:
                                progress(
                                    "story_architect_complete",
                                    {
                                        "genre_intent_contract_hash": (
                                            genre_intent_contract.contract_hash()
                                            if genre_intent_contract is not None
                                            else None
                                        ),
                                        "genre_key": (
                                            genre_intent_contract.genre_key
                                            if genre_intent_contract is not None
                                            else genre_key
                                        ),
                                        "sub_genre": (
                                            genre_intent_contract.sub_genre_label
                                            if genre_intent_contract is not None
                                            else str(payload.get("sub_genre") or "")
                                        ),
                                        "prompt_pack_key": (
                                            genre_intent_contract.prompt_pack_key
                                            if genre_intent_contract is not None
                                            else None
                                        ),
                                        "tone": story_facets_obj.tone,
                                        "narrative_drive": story_facets_obj.narrative_drive,
                                        "trope_tags": list(story_facets_obj.trope_tags),
                                        "setting": story_facets_obj.setting,
                                        "source": story_facets_obj.generation_source,
                                    },
                                )
                        except (TaskCancelledError, ExecutionBudgetError):
                            raise
                        except Exception:
                            logger.warning(
                                "Story Architect failed; proceeding without facets", exc_info=True
                            )

                        try:
                            with bind_conception_model(
                                _conception_model,
                                call_timeout_seconds=_CONCEPTION_CALL_TIMEOUT_SECONDS,
                                task_id=task_id,
                                workflow_run_id=(
                                    getattr(conception_workflow_run, "id", None)
                                ),
                            ):
                                async def _save_conception_replay_context(record):
                                    from bestseller.services.conception_checkpoints import save_replay_context
                                    async with session_scope(settings) as checkpoint_session:
                                        await save_replay_context(checkpoint_session,
                                            workflow_run_id=conception_workflow_run.id, record=record)

                                async def _save_conception_checkpoint(
                                    checkpoint: dict[str, object],
                                ) -> None:
                                    from bestseller.services.conception_checkpoints import (
                                        save_conception_checkpoint,
                                    )

                                    async with session_scope(settings) as checkpoint_session:
                                        await save_conception_checkpoint(
                                            checkpoint_session,
                                            workflow_run_id=conception_workflow_run.id,
                                            checkpoint={
                                                **checkpoint,
                                                "input_hash": conception_input_hash,
                                                "source_workflow_run_id": str(
                                                    source_conception_workflow_run_id
                                                )
                                                if source_conception_workflow_run_id
                                                else None,
                                            },
                                        )

                                _conception_pipeline_options: dict[str, object] = {
                                    "resume_checkpoint": conception_checkpoint,
                                }
                                if conception_workflow_run is not None:
                                    _conception_pipeline_options.update(
                                        {
                                            "checkpoint_callback": _save_conception_checkpoint,
                                            "checkpoint_input_hash": conception_input_hash,
                                            "resume_replay_context": conception_replay_context,
                                            "replay_context_callback": _save_conception_replay_context,
                                        }
                                    )
                                conception_result = await run_conception_pipeline(
                                    session,
                                    settings,
                                    genre_key=genre_key,
                                    genre=str(payload.get("genre") or ""),
                                    sub_genre=str(payload.get("sub_genre") or ""),
                                    chapter_count=int(payload["target_chapters"]),
                                    user_hints=user_hints,
                                    story_facets=story_facets_obj,
                                    genre_intent_contract=genre_intent_contract,
                                    creation_intent_contract=(
                                        creation_intent_contract.model_dump(mode="json")
                                        if creation_intent_contract is not None else None
                                    ),
                                    progress=progress,
                                    **_conception_pipeline_options,
                                )
                                raise_if_execution_stopped()
                        except Exception as exc:
                            # The success path below closes the conception row,
                            # but *any* exception unwinds the outer session before
                            # that assignment.  Quality blocks were handled here
                            # first; user cancellation and unexpected conception
                            # errors were not, leaving project-less rows stuck at
                            # ``running`` even after the visible task had reached
                            # ``cancelled`` or ``failed``.  Close every ordinary
                            # exception in a fresh transaction, preserve cancelled
                            # as its own terminal state, then re-raise unchanged.
                            _blocked_wf_id = getattr(conception_workflow_run, "id", None)
                            if _blocked_wf_id is not None:
                                try:
                                    from sqlalchemy import (
                                        update as _sql_update,
                                    )

                                    async with session_scope(settings) as _close_session:
                                        await _close_session.execute(
                                            _sql_update(WorkflowRunModel)
                                            .where(
                                                WorkflowRunModel.id == _blocked_wf_id,
                                                WorkflowRunModel.status.in_(
                                                    [
                                                        WorkflowStatus.PENDING.value,
                                                        WorkflowStatus.QUEUED.value,
                                                        WorkflowStatus.RUNNING.value,
                                                    ]
                                                ),
                                            )
                                            .values(
                                                status=(
                                                    WorkflowStatus.CANCELLED.value
                                                    if isinstance(exc, TaskCancelledError)
                                                    else WorkflowStatus.FAILED.value
                                                ),
                                                current_step=(
                                                    "cancelled"
                                                    if isinstance(exc, TaskCancelledError)
                                                    else "failed"
                                                ),
                                                error_message=str(exc)[:4000],
                                            )
                                        )
                                except Exception:
                                    logger.debug(
                                        "closing terminal conception workflow row "
                                        "failed (non-fatal); the periodic reaper "
                                        "will catch it",
                                        exc_info=True,
                                    )
                            raise
                        effective_premise = conception_result.premise
                        effective_title = conception_result.title
                        self._update_task_title(task_id, effective_title)
                        effective_writing_profile = conception_result.writing_profile
                        conception_brief = conception_result.commercial_brief
                        conception_log = conception_result.conception_log
                        conception_hook_spec = (
                            conception_result.hook_spec
                            if isinstance(conception_result.hook_spec, dict)
                            else None
                        )
                        conception_contract = (
                            getattr(conception_result, "concept_contract", None)
                            if isinstance(
                                getattr(conception_result, "concept_contract", None),
                                dict,
                            )
                            else None
                        )
                        effective_synopsis = conception_result.synopsis
                        effective_tags = conception_result.tags
                        conception_methodology = getattr(
                            conception_result, "concept_methodology", None
                        )
                        conception_hook_candidates = getattr(
                            conception_result, "hook_candidates", None
                        )
                        conception_story_appeal = getattr(conception_result, "story_appeal", None)
                        conception_motif_amplification = getattr(
                            conception_result, "motif_amplification", None
                        )
                        conception_title_tournament = getattr(
                            conception_result, "title_tournament", None
                        )
                        conception_default_family = getattr(
                            conception_result, "default_family", None
                        )
                        conception_degradation = [
                            {
                                "stage": event.stage,
                                "component": event.component,
                                "reason": event.reason,
                                "severity": event.severity,
                                "fallback": event.fallback,
                                "model": event.model,
                                "metadata": event.metadata,
                            }
                            for event in (
                                getattr(conception_result, "degradation_events", None) or ()
                            )
                        ]
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
                                "degraded": bool(
                                    getattr(
                                        conception_result,
                                        "degraded",
                                        bool(conception_degradation),
                                    )
                                ),
                                "degradation_count": len(conception_degradation),
                                "hook_h_norm": (
                                    conception_hook_spec.get("score", {}).get("h_norm")
                                    if isinstance(conception_hook_spec, dict)
                                    else None
                                ),
                            },
                        )
                        # ── conception_log 落盘 ──────────────────────────────
                        # 2026-08-17 定罪：conception_log（淘汰赛点子池全文、每轮
                        # 评分、淘汰理由）在此被取出后曾无任何下游写入——91 次
                        # LLM 调用的中间产物全部丢弃，只有胜出者落库。后果：
                        # ①构思平庸时无法事后审计哪一环丢了质量（本次调研只能从
                        # 最终产物反推）②前端「看不到构思了什么」的根子不是没
                        # 展示，是没存 ③池子里是否有更好的点子被埋没，永远无人知。
                        # 落成文件（可能上百 KB，不塞 JSONB），路径记进 workflow_run
                        # 供前端与审计取用。失败不阻断建书。
                        conception_log_path = _persist_conception_log(
                            settings,
                            task_id,
                            effective_title,
                            conception_log,
                            hook_candidates=conception_hook_candidates,
                            degradation_events=conception_degradation,
                        )

                        if conception_workflow_run is not None:
                            conception_workflow_run.status = WorkflowStatus.COMPLETED.value
                            conception_workflow_run.current_step = "completed"
                            conception_workflow_run.metadata_json = {
                                **(conception_workflow_run.metadata_json or {}),
                                "result_title": effective_title,
                                "conception_log_path": conception_log_path,
                                "result_hash": hashlib.sha256(
                                    json.dumps(
                                        {
                                            "title": effective_title,
                                            "premise": effective_premise,
                                            "synopsis": effective_synopsis,
                                        },
                                        ensure_ascii=False,
                                        sort_keys=True,
                                        separators=(",", ":"),
                                    ).encode("utf-8")
                                ).hexdigest(),
                            }

                if run_conception:
                    # Revalidate before materialisation: a missing result must
                    # not silently turn a newly conceived book into a legacy one.
                    from bestseller.services.concept_contract import (
                        require_conception_contract_for_target,
                    )

                    conception_contract = require_conception_contract_for_target(
                        conception_contract,
                        target_chapters=int(payload["target_chapters"]),
                    )

                project_metadata: dict[str, object] = {"premise": effective_premise}
                if isinstance(payload.get("genre_intent_contract"), dict):
                    project_metadata["genre_intent_contract"] = json.loads(
                        json.dumps(payload["genre_intent_contract"])
                    )
                # Story-enhancer checkboxes (脑洞/反常识/18 故事 skill) from the
                # create form → hard outline contracts. Validated so unknown
                # skill keys are dropped; empty selection adds nothing.
                from bestseller.services.story_enhancers import (
                    STORY_ENHANCERS_METADATA_KEY,
                    resolve_story_enhancers,
                )

                _enhancers = resolve_story_enhancers(
                    {STORY_ENHANCERS_METADATA_KEY: payload.get("story_enhancers")}
                    if isinstance(payload.get("story_enhancers"), dict)
                    else {}
                )
                if not _enhancers.is_default():
                    project_metadata[STORY_ENHANCERS_METADATA_KEY] = _enhancers.model_dump(
                        mode="json"
                    )
                payload_hook_spec = coerce_hook_spec(payload.get("hook_spec"))
                if payload_hook_spec is not None:
                    project_metadata["hook_spec"] = payload_hook_spec.model_dump(mode="json")
                if conception_hook_spec:
                    project_metadata["hook_spec"] = conception_hook_spec
                if conception_contract:
                    project_metadata["concept_contract_version"] = "2"
                    project_metadata["concept_contract"] = conception_contract
                    project_metadata["hook_card"] = conception_contract.get("hook_card", {})
                    project_metadata["seriality_proof"] = conception_contract.get(
                        "seriality_proof", {}
                    )
                if run_conception:
                    project_metadata["synopsis"] = effective_synopsis
                    project_metadata["tags"] = effective_tags
                    # 故事脊柱(2026-07-08 框架层):全管线传导的故事核。
                    _spine = getattr(conception_result, "story_spine", None)
                    if isinstance(_spine, dict) and _spine:
                        project_metadata["story_spine"] = _spine
                if conception_brief:
                    project_metadata["commercial_brief"] = conception_brief
                    project_metadata["benchmark_works"] = conception_brief.get(
                        "benchmark_works", []
                    )
                    project_metadata["target_audiences"] = conception_brief.get(
                        "target_audiences", []
                    )
                if conception_degradation:
                    project_metadata["conception_degraded"] = True
                    project_metadata["conception_degradation_events"] = conception_degradation
                # Persist conception artifacts so they are inspectable in the
                # book document view (/design/{slug}) alongside planning output.
                conception_artifacts: dict[str, object] = {}
                if story_facets_obj is not None:
                    # Story Architect runs before the conception tournament.  Its
                    # facets are an exploration proposal, not the final project
                    # truth: the tournament may (and often should) select a
                    # different setting/mechanism.  Persisting the proposal at
                    # top level made planning_kernel and later outline prompts
                    # treat it as authoritative, so a winning concept such as a
                    # herb-garden story could retain an unrelated bloodline-island
                    # setting.  Keep it only as an auditable conception artifact.
                    conception_artifacts["story_architect_proposal"] = story_facets_obj.model_dump(
                        mode="json"
                    )
                if conception_methodology:
                    conception_artifacts["concept_methodology"] = conception_methodology
                if conception_hook_candidates:
                    conception_artifacts["hook_candidates"] = conception_hook_candidates
                if conception_story_appeal:
                    conception_artifacts["story_appeal"] = conception_story_appeal
                if conception_motif_amplification:
                    conception_artifacts["motif_amplification"] = conception_motif_amplification
                if conception_title_tournament:
                    conception_artifacts["title_tournament"] = conception_title_tournament
                if conception_default_family:
                    conception_artifacts["default_family"] = conception_default_family
                if conception_brief:
                    conception_artifacts["commercial_brief"] = conception_brief
                creative_brief = payload.get("creative_brief")
                if isinstance(creative_brief, dict) and creative_brief:
                    project_metadata["creative_brief"] = creative_brief
                concept_lab_bundle = payload.get("concept_lab_bundle")
                if isinstance(concept_lab_bundle, dict) and concept_lab_bundle:
                    project_metadata["concept_lab"] = concept_lab_bundle
                extra_meta = payload.get("metadata")
                if isinstance(extra_meta, dict):
                    project_metadata.update(extra_meta)
                if run_conception:
                    # The direct autowrite API accepts caller metadata for
                    # compatibility, but it must not replace the canonical
                    # result produced by this conception run.  Re-assert only
                    # these generated story fields here; resume/rebuild paths
                    # keep their existing metadata because run_conception is
                    # false there.
                    project_metadata["premise"] = effective_premise
                    project_metadata["synopsis"] = effective_synopsis
                    project_metadata["tags"] = list(effective_tags)
                    _canonical_spine = getattr(conception_result, "story_spine", None)
                    if isinstance(_canonical_spine, dict) and _canonical_spine:
                        project_metadata["story_spine"] = _canonical_spine
                    # A request may carry a stale preview copy in extra metadata.
                    # Once conception has produced the validated winner, no
                    # preliminary facets may shadow that winner downstream.
                    project_metadata.pop("story_facets", None)
                    # create_project() consumes the immutable packaging receipt
                    # synchronously. Keep the verified conception artifact on
                    # the payload after the untrusted metadata merge.
                    if conception_artifacts:
                        project_metadata["conception_artifacts"] = conception_artifacts
                # Request metadata may contain stale preview values. Re-assert
                # the validated creation-time genre contract after the merge.
                if genre_intent_contract is not None:
                    project_metadata["genre_intent_contract"] = genre_intent_contract.model_dump(
                        mode="json"
                    )
                # Persist the full creation-intent contract. It previously
                # lived only on the task payload, so every downstream reader
                # keyed on metadata["creation_intent_contract"] (e.g. the
                # writing-profile pov/tone bridge in writing_profile.py) read
                # None for every book, and options that rode ONLY this
                # contract were silently dropped after conception. Placed
                # after the extra_meta merge so a stale preview copy cannot
                # shadow the validated creation-time contract.
                if isinstance(payload.get("creation_intent_contract"), dict):
                    project_metadata["creation_intent_contract"] = json.loads(
                        json.dumps(payload["creation_intent_contract"])
                    )
                if isinstance(user_hints, dict) and user_hints.get("production_profile"):
                    project_metadata["production_profile"] = user_hints["production_profile"]
                    project_metadata["user_hints"] = dict(user_hints)
                if creation_intent_contract is not None:
                    # Keep an audit-friendly projection alongside the frozen
                    # contract. Older downstream readers and support queries
                    # still inspect these top-level keys; without the projection
                    # a correctly transported seed/tone looked absent after
                    # project materialisation even though it was nested in V1.
                    project_metadata["concept_seed"] = creation_intent_contract.concept_seed
                    project_metadata["tone_preference"] = creation_intent_contract.tone_preference
                    project_metadata["audience_orientation"] = (
                        creation_intent_contract.audience_orientation
                    )
                    project_metadata["creation_input_receipt"] = {
                        "schema_version": creation_intent_contract.schema_version,
                        "contract_hash": creation_intent_contract.contract_hash(),
                        "field_sources": {
                            key: (value.value if hasattr(value, "value") else str(value))
                            for key, value in creation_intent_contract.field_sources.items()
                        },
                        "concept_seed_present": bool(creation_intent_contract.concept_seed),
                    }
                # Request metadata is user-controlled and may contain stale
                # preview values.  Re-assert the verified concept contract
                # after the merge so it cannot be replaced by an unvalidated
                # HookCard/Spine/SerialityProof.
                if conception_contract:
                    project_metadata["concept_contract_version"] = "2"
                    project_metadata["concept_contract"] = conception_contract
                    project_metadata["hook_card"] = conception_contract.get("hook_card", {})
                    project_metadata["seriality_proof"] = conception_contract.get(
                        "seriality_proof", {}
                    )
                    project_metadata["story_spine"] = conception_contract.get("story_spine", {})
                    project_metadata.pop("hook_spec", None)
                # A per-book model picked from the dashboard *during* conception
                # (before this project row existed) was stashed on the task
                # payload by _set_project_model_payload. Fold it in so the
                # writing phase honours the choice. See set_pending_project_model.
                pending_model = self._pending_project_model(task_id)
                if pending_model:
                    project_metadata["llm_model_id"] = pending_model
                # Stamp the shared new-project policy before row creation. The
                # central project service applies the same contract for CLI/API
                # callers, so entrypoints cannot silently select different modes.
                from bestseller.services.generation_policy import (
                    apply_new_project_generation_policy,
                )

                project_metadata = apply_new_project_generation_policy(
                    project_metadata,
                    generation_unit_mode=payload.get("generation_unit_mode"),
                    prose_prompt_profile=payload.get("prose_prompt_profile"),
                )
                # ── Advisory market validation (opt-in, never gates) ──
                # Runs against the conception outputs on the REAL web creation
                # path (the pipelines.py hook only covers the CLI
                # use_conception path). Summary lands in project metadata; the
                # full report is persisted as an artifact by
                # persist_creation_snapshots. Any failure degrades silently.
                market_validation_report_obj = None
                # Read the flag defensively: an advisory add-on must never be
                # able to crash creation, and ``settings`` is a stub in some
                # callers/tests where ``.pipeline`` is absent entirely.
                _mv_enabled = bool(
                    getattr(
                        getattr(settings, "pipeline", None),
                        "enable_market_validation",
                        False,
                    )
                )
                if run_conception and _mv_enabled:
                    try:
                        from bestseller.services.market_validation.request_builder import (
                            build_creation_request,
                        )
                        from bestseller.services.market_validation.service import (
                            run_market_validation,
                        )

                        market_validation_report_obj = await run_market_validation(
                            build_creation_request(
                                metadata=project_metadata,
                                genre_label=str(payload.get("genre") or ""),
                                sub_genre_label=str(payload.get("sub_genre") or ""),
                                title=effective_title,
                                concept=effective_premise,
                                blurb=effective_synopsis,
                                fallback_genre_key=genre_key,
                                project_slug=str(payload.get("slug") or ""),
                            ),
                            settings=settings,
                            session=session,
                        )
                        _mv_summary = market_validation_report_obj.summary()
                        project_metadata["market_validation_summary"] = _mv_summary
                        progress(
                            "market_validation_completed",
                            {
                                "score": _mv_summary["verdict"]["score"],
                                "band": _mv_summary["verdict"]["band"],
                                "title_verdicts": _mv_summary["title_check"]["verdicts"],
                                "platforms": _mv_summary["platforms_used"],
                            },
                        )
                    except (TaskCancelledError, ExecutionBudgetError):
                        raise
                    except Exception:
                        market_validation_report_obj = None
                        logger.warning(
                            "Advisory market validation failed (fail-open)",
                            exc_info=True,
                        )
                if payload.get("draft_mode"):
                    settings.quality.draft_mode = True
                stop_after_conception = bool(payload.get("stop_after_conception", False))

                async def persist_creation_snapshots(project: object) -> None:
                    """Persist the immutable creation/conception handoff after row creation."""
                    if not run_conception or creation_intent_contract is None:
                        return
                    from bestseller.services.conception_snapshots import (
                        create_conception_snapshot_artifact,
                        snapshot_hash,
                    )

                    project_id = project.id
                    attempt_id = conception_attempt.attempt_id if conception_attempt is not None else str(payload.get("_conception_attempt_id") or f"{task_id}:initial")
                    source_run_id = (
                        conception_workflow_run.id if conception_workflow_run is not None else None
                    )
                    intent_content = creation_intent_contract.model_dump(mode="json")
                    intent_artifact = await create_conception_snapshot_artifact(
                        session,
                        project_id=project_id,
                        artifact_type="creation_intent",
                        content=intent_content,
                        status="validated",
                        idempotency_key=f"{attempt_id}:intent",
                        schema_version=creation_intent_contract.schema_version,
                        source_run_id=source_run_id,
                        created_by="web-ui",
                    )
                    conception_content = {
                        "schema_version": "conception-snapshot.v1",
                        "attempt_id": attempt_id,
                        "input_hash": conception_attempt.input_hash()
                        if conception_attempt
                        else None,
                        "conception_mode": "initial",
                        "creation_intent_hash": creation_intent_contract.contract_hash(),
                        "title": effective_title,
                        "premise": effective_premise,
                        "synopsis": effective_synopsis,
                        "tags": effective_tags,
                        "writing_profile": effective_writing_profile or {},
                        "world_model": getattr(conception_result, "world_model", {}) or {},
                        # Keep the generated conception hook when the user did
                        # not provide a legacy HookSpec.  Parenthesising this
                        # precedence-sensitive choice avoids the conditional
                        # expression swallowing the generated value.
                        "hook_spec": (
                            conception_hook_spec
                            if conception_hook_spec
                            else (
                                payload_hook_spec.model_dump(mode="json")
                                if payload_hook_spec is not None
                                else {}
                            )
                        ),
                        "concept_contract": conception_contract or {},
                        "story_spine": getattr(conception_result, "story_spine", {}) or {},
                        "commercial_brief": conception_brief or {},
                        "conception_artifacts": conception_artifacts,
                    }
                    snapshot_artifact = await create_conception_snapshot_artifact(
                        session,
                        project_id=project_id,
                        artifact_type="conception_snapshot",
                        content=conception_content,
                        status=("pending_user_approval" if stop_after_conception else "validated"),
                        idempotency_key=f"{attempt_id}:snapshot",
                        schema_version="conception-snapshot.v1",
                        source_run_id=source_run_id,
                        created_by="web-ui",
                    )
                    metadata = dict(project.metadata_json or {})
                    if current_execution_scope():
                        metadata["execution_scope_id"] = current_execution_scope()
                    metadata.update(
                        {
                            "creation_intent_snapshot_id": str(intent_artifact.id),
                            "creation_intent_contract_hash": creation_intent_contract.contract_hash(),
                            "conception_snapshot_id": str(snapshot_artifact.id),
                            "conception_snapshot_status": snapshot_artifact.status,
                            "conception_workflow_run_id": str(source_run_id)
                            if source_run_id
                            else None,
                        }
                    )
                    project.metadata_json = metadata
                    if not stop_after_conception:
                        from bestseller.services.reviewed_conception_selection import select_reviewed_conception_snapshot
                        selection = await select_reviewed_conception_snapshot(
                            session, settings, project_id=project_id, snapshot_id=snapshot_artifact.id,
                            expected_hash=snapshot_hash(snapshot_artifact.content), actor="web-ui complete-book selection",
                            reason="Select the same successfully reviewed source version for the requested complete book")
                        progress("conception_canonical_selected", {"project_id":str(project_id), **selection})
                    if conception_workflow_run is not None:
                        conception_workflow_run.project_id = project_id
                        conception_workflow_run.metadata_json = {
                            **(conception_workflow_run.metadata_json or {}),
                            "execution_scope_id": current_execution_scope(),
                            "project_id": str(project_id),
                            "creation_intent_snapshot_id": str(intent_artifact.id),
                            "conception_snapshot_id": str(snapshot_artifact.id),
                            "snapshot_hash": snapshot_hash(snapshot_artifact.content),
                        }
                    await session.flush()
                    # Full market-validation report as a versioned artifact
                    # (summary already rode in via project metadata). Advisory:
                    # persistence failure must never fail creation.
                    if market_validation_report_obj is not None:
                        try:
                            from bestseller.services.market_validation.repository import (
                                persist_market_validation_report,
                            )

                            await persist_market_validation_report(
                                session,
                                str(project.slug),
                                market_validation_report_obj,
                            )
                        except Exception:
                            logger.debug(
                                "market validation artifact persist failed (non-fatal)",
                                exc_info=True,
                            )

                if stop_after_conception and run_conception:
                    if not conception_contract:
                        raise ValueError("构思未形成通过门禁的创意合同，未创建书籍、未进入规划。")
                    project_metadata["planning_status"] = "awaiting_concept_approval"
                    project_metadata["conception_only"] = True
                target_chapters = int(payload["target_chapters"])
                from bestseller.domain.enums import ProjectType as DomainProjectType

                project_type_raw = str(
                    payload.get("project_type") or DomainProjectType.LINEAR.value
                )
                try:
                    resolved_project_type = DomainProjectType(project_type_raw)
                except ValueError:
                    resolved_project_type = DomainProjectType.LINEAR
                if current_execution_scope():
                    project_metadata["execution_scope_id"] = current_execution_scope()
                project_create = ProjectCreate(
                    slug=str(payload["slug"]),
                    title=effective_title,
                    genre=str(payload["genre"]),
                    sub_genre=(str(payload["sub_genre"]) if payload.get("sub_genre") else None),
                    audience=(str(payload["audience"]) if payload.get("audience") else None),
                    language=str(payload.get("language") or "zh-CN"),
                    target_word_count=int(payload["target_words"]),
                    target_chapters=target_chapters,
                    project_type=resolved_project_type,
                    metadata=project_metadata,
                    writing_profile=effective_writing_profile,
                )
                if stop_after_conception and run_conception:
                    from bestseller.services.projects import create_project

                    project = await create_project(session, project_create, settings)
                    await persist_creation_snapshots(project)
                    hook_card = conception_contract.get("hook_card", {})
                    progress(
                        "conception_only_complete",
                        {
                            "project_slug": project.slug,
                            "planning_started": False,
                            "concept_contract_version": "2",
                            "hook": (
                                str(hook_card.get("one_liner") or "")
                                if isinstance(hook_card, dict)
                                else ""
                            ),
                        },
                    )
                    return {
                        "project_slug": project.slug,
                        "project_id": str(project.id),
                        "mode": "conception_only",
                        "planning_started": False,
                        "concept_contract": conception_contract,
                    }
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
                # Materialise the project and commit the creation handoff
                # before planning starts.  This makes the intent/conception
                # snapshots durable even when planning fails part-way through.
                # Resume payloads have ``run_conception=False`` and therefore
                # deliberately bypass this block and never mint a new initial
                # snapshot.
                if (
                    run_conception
                    and creation_intent_contract is not None
                    and hasattr(session, "scalar")
                ):
                    from bestseller.services.projects import create_project

                    project = await get_project_by_slug(session, str(payload["slug"]))
                    if project is None:
                        project = await create_project(session, project_create, settings)
                    await persist_creation_snapshots(project)
                    raise_if_execution_stopped()
                    await session.commit()
                if payload.get('autonomous_book') is True:
                    from bestseller.services.draft_continuity import draft_mode_enabled
                    existing_book=await get_project_by_slug(session,str(payload['slug']))
                    if existing_book is not None and await draft_mode_enabled(session,project_id=existing_book.id,project=existing_book):
                        from bestseller.services.book_run import submit
                        from arq.connections import create_pool,RedisSettings
                        pool=await create_pool(RedisSettings.from_dsn(settings.redis.url))
                        try:
                            goal=await submit(session,settings,pool,project=existing_book,requested_model=payload.get('requested_model'))
                        finally:await pool.aclose()
                        from bestseller.services.creation_configuration import read_window
                        window=read_window(existing_book)
                        return {**goal,'execution_window':window.model_dump(mode='json') if window else {},'final_verdict':'book_objective_recorded'}
                # Progressive routing (large novels → per-volume plan/write
                # feedback loop) lives inside run_autowrite_pipeline so the
                # web-ui path and worker self-heal path pick the same pipeline
                # for the same target_chapters. See PROGRESSIVE_CHAPTER_THRESHOLD
                # in services.pipelines.
                # Conception evidence must survive a pipeline failure. Until
                # 2026-08-10 the snapshot was written only after the pipeline
                # RETURNED, so any book that died in planning lost its
                # story_appeal / motif_amplification / hook_candidates /
                # market_validation report entirely — precisely the books whose
                # concept you most need to inspect. Live case: 《废脉炉子天天骂我》
                # died in foundation and left zero conception artifacts behind.
                from bestseller.services.chapter_review_reconciliation import prepare_native_review_resume
                resume_project = await get_project_by_slug(session, str(payload["slug"]))
                if resume_project is not None:
                    from bestseller.services.project_style_invariant_boundary import prepare_creation_style_invariant_boundary
                    style_boundary = await prepare_creation_style_invariant_boundary(
                        session, project_id=resume_project.id, requested_by="web-ui",
                    )
                    if style_boundary["changed"]:
                        await session.commit()
                    resume = await prepare_native_review_resume(
                        session, settings, project_id=resume_project.id, requested_by="web-ui",
                    )
                    if resume["status"] == "prepared":
                        await session.commit()
                result = await run_autowrite_pipeline(**common_kwargs)
                raise_if_execution_stopped()
                from bestseller.services.autonomous_continuation import record_window_continuation
                continuation = await record_window_continuation(session, project_id=result.project_id,
                    result=result.model_dump(mode="json"), dispatch_authorized=payload.get("autonomous_book") is True, requested_model=payload.get("requested_model"))
                if continuation is not None:
                    await session.commit()
                    if payload.get("autonomous_book") is True:
                        from bestseller.services.autonomous_dispatch import enqueue_checkpoint
                        from arq.connections import create_pool, RedisSettings
                        queue = await create_pool(RedisSettings.from_dsn(settings.redis.url))
                        try:
                            await enqueue_checkpoint(session, queue, continuation['continuation_workflow_run_id'])
                        finally:
                            await queue.aclose()
                    return {**result.model_dump(mode="json"), **continuation}
            return json.loads(json.dumps(result.model_dump(mode="json"), default=_json_default))

        # Overall pipeline cap: 24h. Long enough for 100+ chapters.
        pipeline_timeout = float(os.environ.get("BESTSELLER_PIPELINE_TIMEOUT", "86400"))

        async def guarded_runner() -> dict[str, object]:
            from bestseller.services.model_context_capacity import bind_selected_model_capacity
            from bestseller.services.web_pipeline_owner import web_pipeline_owner
            with bind_selected_model_capacity(payload):
                async with web_pipeline_owner(load_settings(), str(payload["slug"])):
                    return await asyncio.wait_for(runner(), timeout=pipeline_timeout)

        try:
            # Bind the ambient progress emitter so deep pipeline actions
            # (concept agents, quality gates, repair sub-steps) can surface
            # status without threading ``progress`` through every signature.
            with bind_progress(progress):
                result = asyncio.run(guarded_runner())
            self._mark_completed(task_id, result)
        except ExecutionBudgetError as exc:
            _close_creation_workflow_after_task_exit(
                load_settings(), task_id=task_id, payload=payload, status="failed",
                error_message=exc.code,
            )
            self._stop_execution_task(task_id, exc)
        except TaskCancelledError as exc:
            _close_creation_workflow_after_task_exit(
                load_settings(),
                task_id=task_id,
                payload=payload,
                status="cancelled",
                error_message=str(exc) or "Task cancelled by user",
            )
            self._mark_cancelled(task_id)
            logger.info("Task %s exited via cancellation: %s", task_id, exc)
        except TimeoutError:
            _close_creation_workflow_after_task_exit(
                load_settings(),
                task_id=task_id,
                payload=payload,
                status="failed",
                error_message=f"Pipeline exceeded timeout of {pipeline_timeout:.0f}s",
            )
            self._mark_failed(
                task_id,
                f"Pipeline exceeded timeout of {pipeline_timeout:.0f}s",
            )
        except ProductionControlHaltedError as exc:
            _close_creation_workflow_after_task_exit(
                load_settings(), task_id=task_id, payload=payload,
                status="cancelled", error_message=str(exc),
            )
            self._push_progress(task_id, "fanqie_halted_by_operator", exc.control)
            self.mark_task_blocked_by_project_gate(
                task_id, stage=f"production_{exc.intent}_by_operator", error=str(exc),
            )
        except ProjectRepairPauseError as exc:
            _close_creation_workflow_after_task_exit(
                load_settings(),
                task_id=task_id,
                payload=payload,
                status="failed",
                error_message=str(exc),
            )
            project_slug = str(payload.get("slug") or "")
            self.mark_task_blocked_by_structural_repair(
                task_id,
                _format_project_repair_pause_error(
                    project_slug,
                    raw_detail=str(exc),
                ),
            )
            logger.info("Autowrite task %s blocked by structural repair pause: %s", task_id, exc)
        except PlannerFallbackError as exc:
            _close_creation_workflow_after_task_exit(
                load_settings(),
                task_id=task_id,
                payload=payload,
                status="failed",
                error_message=str(exc),
            )
            # A deliberate planning block, not a crash. Without this handler it
            # fell through to the generic ``except Exception`` and the user saw
            # a raw Python traceback (2026-07-26,
            # urban-power-reversal-1785026717: "PlannerFallbackError:
            # Whole-book outline semantic gate rejected promotion") — the same
            # UX defect its sibling deliberate-block exceptions already avoid.
            detail = str(exc)
            try:
                progress(
                    "planning_blocked",
                    {"reason": detail, "planning_completed": False},
                )
            except Exception:
                logger.debug("planning_blocked progress emit failed", exc_info=True)
            logger.info("Autowrite task %s blocked during planning: %s", task_id, detail)
            self._mark_failed(
                task_id,
                "规划阶段已停止；已有成果保留，正文进度以正式采用内容为准。\n\n"
                f"{detail}\n\n"
                "恢复方向：查看具体诊断，区分格式、来源或基础设施故障与内容审校问题；"
                "修复对应原因后，从原任务的失败步骤恢复。",
            )
        except ConceptContractError as exc:
            _close_creation_workflow_after_task_exit(
                load_settings(),
                task_id=task_id,
                payload=payload,
                status="failed",
                error_message=str(exc),
            )
            reasons = list(exc.violations)
            try:
                progress(
                    "one_sentence_outline_blocked",
                    {
                        "action": "reject",
                        "reasons": reasons,
                        "planning_started": False,
                        "project_created": False,
                    },
                )
            except Exception:
                logger.debug("one_sentence_outline_blocked progress emit failed", exc_info=True)
            logger.info(
                "Autowrite task %s blocked by concept contract before planning: %s",
                task_id,
                "; ".join(reasons),
            )
            # 被门拦下的构思同样要留档（2026-08-19 污染门毙书零档案）：
            # 日志由 raise 点挂在异常上带出 async 帧，settings 现场加载。
            _cc_log_path = _persist_conception_log(
                load_settings(),
                task_id,
                None,
                getattr(exc, "conception_log", None),
            )
            self._mark_failed(
                task_id,
                "一句话创意未通过长篇门禁，已停止；未创建书籍、未进入规划。\n\n"
                + "\n".join(f"- {reason}" for reason in reasons)
                + (f"\n\n（构思全过程已存档：{_cc_log_path}）" if _cc_log_path else ""),
            )
        except AppealBarNotMetError as exc:
            _close_creation_workflow_after_task_exit(
                load_settings(),
                task_id=task_id,
                payload=payload,
                status="failed",
                error_message=str(exc),
            )
            # 这是【刻意拦截】不是崩溃：一句话故事大纲或上架文案未过硬门，
            # 必须在 create_project/run_autowrite_pipeline 之前终止，不留僵尸项目。
            # NOTE: the conception workflow row is closed inside ``runner()``
            # (see the try/except around run_conception_pipeline) — not here —
            # because ``conception_workflow_run``/``settings``/``session`` are
            # locals of the async runner frame, not of this sync handler.
            report = exc.report or {}
            logline_gate = report.get("logline_gate") or {}
            blurb_total = (report.get("blurb") or {}).get("total")
            title_total = (report.get("title") or {}).get("total")
            # Attribute the block to the bar that ACTUALLY raised it. The
            # logline gate has been advisory since 2026-07-25
            # (``block_expansion: false`` — it vetoed 3/3 real bestsellers), so
            # a non-expand verdict from it is a note, not a cause. Reporting it
            # as the cause sent this session chasing the wrong gate: live 2026-08-10
            # 《搓背》 was killed by the BLURB bar (persona 0/3 on a one-sentence
            # v0 fallback) and the failure read "一句话故事大纲不成立" with blurb
            # feedback pasted underneath.
            logline_gate_blocked = bool(
                logline_gate
                and logline_gate.get("blocking")
                and str(logline_gate.get("action") or "") != "expand"
            )
            if logline_gate_blocked:
                try:
                    progress(
                        "one_sentence_outline_blocked",
                        {
                            "action": logline_gate.get("action"),
                            "overall": logline_gate.get("overall"),
                            "weakest_axis": logline_gate.get("weakest_axis"),
                            "reasons": logline_gate.get("reasons") or [],
                            "fix_directives": logline_gate.get("fix_directives") or [],
                            "planning_started": False,
                        },
                    )
                except Exception:
                    logger.debug("one_sentence_outline_blocked progress emit failed", exc_info=True)
                msg = (
                    "一句话故事大纲不成立，已停止，未创建书籍、未进入规划。\n"
                    f"裁决：{logline_gate.get('action')}　"
                    f"总分：{logline_gate.get('overall')}　"
                    f"最弱维度：{logline_gate.get('weakest_axis')}\n\n"
                    f"{exc.feedback}"
                )
                logger.info(
                    "Autowrite task %s blocked before planning: one-sentence outline %s",
                    task_id,
                    logline_gate.get("action"),
                )
                self._mark_failed(task_id, msg)
                return
            try:
                progress(
                    "appeal_blocked",
                    {
                        "blurb_total": blurb_total,
                        "title_total": title_total,
                        "meets_bar": False,
                        "feedback": exc.feedback,
                    },
                )
            except Exception:
                logger.debug("appeal_blocked progress emit failed", exc_info=True)
            # 被拦截的构思恰恰最需要审计——中间产物照样落盘（2026-08-18）。
            # 本 handler 是 sync 帧：``settings``/``conception_log`` 都是 async
            # runner 的 locals，在这里引用是 NameError（首版真踩了——拦截时
            # 炸 NameError，用户看到的是崩溃栈而不是拦截报告，档案也没落）。
            # 日志由 raise 点挂在异常上带出，settings 现场加载。
            _failed_log_path = _persist_conception_log(
                load_settings(),
                task_id,
                None,
                getattr(exc, "conception_log", None),
            )
            # 阈值必须从配置读，不能写死在文案里：这句原本硬编码「80 分」，
            # 而简介达标线其实是 68（80 是书名的线）。2026-08-23 真机拦截时
            # 它报「未达 80 分」而简介 65.0、书名 94.8，排查的人（包括我）
            # 会以为卡在书名上。报错文案说错阈值，等于把人引向错误的现场。
            try:
                from bestseller.services.story_appeal import load_story_appeal_config

                _bars = (load_story_appeal_config() or {}).get("meets_bar", {}) or {}
                _blurb_min = float(_bars.get("blurb_min", 68))
                _title_min = float(_bars.get("title_min", 80))
            except Exception:
                _blurb_min, _title_min = 68.0, 80.0
            _unmet = []
            if isinstance(blurb_total, (int, float)) and blurb_total < _blurb_min:
                _unmet.append(f"简介 {blurb_total:.1f} < {_blurb_min:.0f}")
            if isinstance(title_total, (int, float)) and title_total < _title_min:
                _unmet.append(f"书名 {title_total:.1f} < {_title_min:.0f}")
            msg = (
                "未达内部吸引力评审门槛（"
                + ("；".join(_unmet) if _unmet else "综合未达标")
                + "），已拦截，未进入规划。\n"
                f"简介点击力：{blurb_total}（线 {_blurb_min:.0f}）　"
                f"书名点击力：{title_total}（线 {_title_min:.0f}）\n\n"
                + (f"（构思全过程已存档：{_failed_log_path}）\n" if _failed_log_path else "")
                + "（已自动多轮重生仍未达标，需调整设定/方向后重建。整改建议：）\n"
                f"{exc.feedback}"
            )
            logger.info(
                "Autowrite task %s blocked: appeal bar not met (blurb=%s title=%s)",
                task_id,
                blurb_total,
                title_total,
            )
            self._mark_failed(task_id, msg)
        except Exception:
            tb = traceback.format_exc()
            _close_creation_workflow_after_task_exit(
                load_settings(),
                task_id=task_id,
                payload=payload,
                status="failed",
                error_message=tb,
            )
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

        creation_mode = str(payload.get("creation_mode") or "long_serial").strip()
        if creation_mode not in {"long_serial", "fanqie_short"}:
            raise ValueError(
                f"Unsupported creation_mode {creation_mode!r}; expected long_serial or fanqie_short"
            )
        is_fanqie_short = creation_mode == "fanqie_short"

        genre_key = str(payload.get("genre_key") or "")
        selection = (
            payload.get("selection")
            if isinstance(payload.get("selection"), dict) and payload.get("selection")
            else None
        )
        genre_presets = {p.key: p for p in list_genre_presets()}
        genre_preset = genre_presets.get(genre_key)
        if genre_preset is None:
            if selection is not None:
                # New canonical-taxonomy path: synthesise a preset from the
                # user's (channel, genre, sub_genre, tags) selection so the rest
                # of the handler is unchanged. Legacy genre_key path still works.
                genre_preset = _genre_preset_from_selection(selection)
                if not genre_key:
                    genre_key = genre_preset.key
            elif str(genre_key).startswith("custom-"):
                # Resume / re-run of a custom-taxonomy book (slug-derived key like
                # ``custom-xuanhuan``, no selection payload): rebuild the ephemeral
                # preset from the synthetic key. The project's real genre/sub_genre
                # are read from the DB downstream.
                from bestseller.services.writing_presets import synthesize_genre_preset

                genre_preset = synthesize_genre_preset(genre_key)
            else:
                raise ValueError(
                    f"Unknown genre_key: {genre_key}. Provide a valid genre_key "
                    f"or a 'selection'. Available: {list(genre_presets.keys())}"
                )

        if is_fanqie_short and not genre_preset.suitable_for_short_story:
            from bestseller.domain.fanqie_short import ensure_fanqie_short_genre_compatible

            ensure_fanqie_short_genre_compatible(
                genre_key,
                suitable=genre_preset.suitable_for_short_story,
            )

        # Freeze the user's taxonomy choice before any AI agent runs.  The
        # contract is the authority for genre/sub-genre/prompt-pack routing;
        # Story Architect may only add surface suggestions later.
        from bestseller.services.genre_intent_contract import (
            build_genre_intent_contract,
            contract_from_selection,
        )
        from bestseller.services.genre_taxonomy import resolve_selection
        from bestseller.services.story_enhancers import capture_story_enhancer_input

        raw_enhancers = payload.get("story_enhancers")
        selected_enhancers, enhancer_input_sources = capture_story_enhancer_input(
            raw_enhancers if isinstance(raw_enhancers, dict) else None
        )
        audience_orientation = (
            str(payload.get("audience_orientation") or "").strip().lower() or None
        )
        _narrative_scale = str(payload.get("narrative_scale") or "").strip() or None
        _tone_preference = str(payload.get("tone_preference") or "").strip() or None
        if audience_orientation not in {None, "male", "female"}:
            raise ValueError("audience_orientation must be male, female, or omitted")
        if _narrative_scale not in {None, "serial", "epic"}:
            raise ValueError("narrative_scale must be serial, epic, or omitted")
        if _tone_preference not in {None, "epic", "light", "dark", "hot"}:
            raise ValueError("tone_preference must be epic, light, dark, hot, or omitted")
        for flag in ("draft_mode", "stop_after_conception", "autonomous_book"):
            if flag in payload and not isinstance(payload[flag], bool):
                raise ValueError(f"{flag} must be a boolean when provided")
        try:
            contract = (
                contract_from_selection(
                    selection,
                    audience_orientation=audience_orientation,
                    narrative_scale=_narrative_scale,
                    tone_preference=_tone_preference,
                    enhancers=selected_enhancers,
                )
                if selection is not None
                else build_genre_intent_contract(
                    resolve_selection(
                        _channel_of_genre(genre_preset.genre),
                        genre_preset.genre,
                        genre_preset.sub_genre,
                        [],
                    ),
                    source="legacy_inferred",
                    audience_orientation=audience_orientation,
                    narrative_scale=_narrative_scale,
                    tone_preference=_tone_preference,
                    enhancers=selected_enhancers,
                )
            )
        except ValueError as exc:
            raise ValueError(
                "Unable to resolve the selected genre intent contract; "
                "creation was rejected to avoid silently dropping user constraints"
            ) from exc

        # Resolve chapter count
        raw_chapter_count = payload.get("chapter_count")
        if raw_chapter_count not in (None, ""):
            try:
                chapter_count = int(raw_chapter_count)
            except (TypeError, ValueError) as exc:
                raise ValueError("chapter_count must be an integer") from exc
            if not is_fanqie_short and not 1 <= chapter_count <= 2000:
                raise ValueError("chapter_count must be between 1 and 2000 for long serials")
        else:
            chapter_count = 0
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

        configuration_submission = None
        supplied_configuration = payload.get("configuration_submission")
        prior_contract = payload.get("creation_intent_contract") or {}
        if supplied_configuration is None and isinstance(prior_contract, dict) and prior_contract.get("configuration"):
            prior_window = payload.get("execution_window") or (payload.get("metadata") or {}).get("execution_window")
            if not prior_window:
                raise ValueError("configuration_resume_window_missing")
            supplied_configuration = {"configuration": prior_contract["configuration"], "execution_window": prior_window}
        if supplied_configuration is not None:
            from bestseller.services.creation_configuration import parse_configuration_submission
            configuration_submission = parse_configuration_submission(supplied_configuration)
            if is_fanqie_short or not genre_preset.language.startswith("zh"):
                raise ValueError("creation configuration currently targets Chinese chaptered fiction")
            if payload.get("chapter_count") is not None and (
                type(payload["chapter_count"]) is not int or payload["chapter_count"] != configuration_submission.configuration.book.total_chapters
            ):
                raise ValueError("chapter_count must equal configuration whole-book count")
            chapter_count = configuration_submission.configuration.book.total_chapters
        words_per_chapter = (configuration_submission.configuration.book.chapter_target
            if configuration_submission is not None else load_settings().generation.words_per_chapter.target)
        target_words = chapter_count * words_per_chapter
        fanqie_meta: dict[str, object] | None = None
        fanqie_pov = "first_person"
        fanqie_length_key = ""
        project_type_value = "linear"
        if is_fanqie_short:
            from bestseller.domain.enums import ProjectType
            from bestseller.domain.fanqie_short import (
                DEFAULT_LENGTH_KEY,
                apply_fanqie_short_writing_profile,
                build_fanqie_short_metadata,
                resolve_length_preset,
            )

            fanqie_length_key = str(payload.get("length_key") or DEFAULT_LENGTH_KEY)
            spec = resolve_length_preset(fanqie_length_key)
            chapter_count = int(spec["segment_count"])
            target_words = int(spec["target_words"])
            fanqie_pov = str(payload.get("pov") or "first_person")
            if fanqie_pov not in {"first_person", "third_limited"}:
                raise ValueError("pov must be first_person or third_limited for fanqie_short")
            fanqie_meta = build_fanqie_short_metadata(
                length_key=fanqie_length_key,
                pov=fanqie_pov,
                segment_count=chapter_count,
                target_words=target_words,
            )
            project_type_value = ProjectType.FANQIE_SHORT.value

        # Resume: reuse existing project slug if provided
        resume_slug = str(payload.get("project_slug") or "")
        is_en = genre_preset.language.startswith("en")
        placeholder = (
            f"{genre_preset.name} - Drafting {_local_now().strftime('%m-%d %H:%M')}"
            if is_en
            else f"{genre_preset.name}·构思中 {_local_now().strftime('%m-%d %H:%M')}"
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
        # Stop auto-baking concrete creative results (2026-06-09). Previously the
        # framework silently injected a *default* creative direction + an
        # auto-generated concept bundle (catalog.bundles[0]) + a bundle-derived
        # hook_spec as (semi-)hard contracts, even when the user picked nothing.
        # Those baked presets over-constrained planning and frequently mismatched
        # the genre. Now we only carry forward what the user *explicitly* selected;
        # otherwise the heat-search/methodology agents let the model grow
        # genre-fitting concepts naturally. See memory: shuangwen-fusion-switch.
        explicit_creative_key = str(payload.get("creative_key") or "").strip()
        if not explicit_creative_key:
            # The quickstart UI never sends `creative_key` — it sends the very same
            # value under `story_enhancers.creativity_direction` (identical key
            # space: genre-synthesis / cross-genre-friction /
            # distilled-mechanism-remix / anti-cliche-opening). Without this bridge
            # `creative_direction` was always None, so the real GenreCreativeDirection
            # engine — stance / conflict_engine / opening_hook / novelty_pressure /
            # anti_cliche_guardrails, all derived from the genre's OWN pack — was
            # unreachable, and the user's 创意方向 pick degraded into a bare label
            # string echoed in six prompts with nothing behind it.
            explicit_creative_key = str(
                getattr(selected_enhancers, "creativity_direction", "") or ""
            ).strip()
        creative_direction = (
            get_genre_creative_direction_for_preset(genre_preset, explicit_creative_key)
            if (is_new_project and explicit_creative_key)
            else None
        )
        creative_hints = creative_direction_to_user_hints(creative_direction)
        explicit_concept_seed = str(payload.get("concept_seed") or "").strip()[:500]
        if explicit_concept_seed:
            creative_hints["concept_seed"] = explicit_concept_seed
        selected_hook_spec = coerce_hook_spec(payload.get("hook_spec"))
        if selected_hook_spec is not None:
            creative_hints["hook_spec"] = selected_hook_spec.model_dump(mode="json")
        explicit_bundle_id = str(payload.get("concept_lab_bundle_id") or "").strip()
        explicit_bundle_payload = (
            payload.get("concept_lab_bundle")
            if isinstance(payload.get("concept_lab_bundle"), dict)
            and payload.get("concept_lab_bundle")
            else None
        )
        concept_bundle = (
            select_concept_lab_bundle(
                genre_key=genre_key,
                bundle_id=explicit_bundle_id,
                creative_key=creative_direction.key if creative_direction else "",
                hook_spec=selected_hook_spec,
                bundle_payload=explicit_bundle_payload,
            )
            if (is_new_project and (explicit_bundle_id or explicit_bundle_payload))
            else None
        )
        if concept_bundle is not None:
            concept_hints = concept_lab_to_user_hints(concept_bundle)
            previous_usage_rule = str(creative_hints.get("usage_rule") or "")
            creative_hints.update(concept_hints)
            bundle_hook_spec = coerce_hook_spec(concept_bundle.hook_spec)
            if bundle_hook_spec is not None:
                selected_hook_spec = bundle_hook_spec
                creative_hints["hook_spec"] = bundle_hook_spec.model_dump(mode="json")
            if previous_usage_rule and concept_hints.get("usage_rule"):
                creative_hints["usage_rule"] = (
                    f"{previous_usage_rule}\n{concept_hints['usage_rule']}"
                )
        premise = (
            f"A {genre_preset.genre} ({genre_preset.sub_genre}) novel: {genre_preset.description}"
            if is_en
            else (
                f"基于{genre_preset.genre}（{genre_preset.sub_genre}）题材，"
                f"{genre_preset.description}"
            )
        )

        writing_profile = sanitize_genre_story_overrides(genre_preset.writing_profile_overrides)
        if is_fanqie_short:
            from bestseller.domain.fanqie_short import apply_fanqie_short_writing_profile

            writing_profile = apply_fanqie_short_writing_profile(
                writing_profile if isinstance(writing_profile, dict) else {},
                pov=fanqie_pov,
            )

        from bestseller.services.production_strategy import normalize_profile
        production_profile = normalize_profile(payload.get("production_profile"))
        creative_hints = {**(creative_hints or {}), "production_profile": production_profile}
        autowrite_payload: dict[str, object] = {
            "production_profile": production_profile,
            "slug": slug,
            "title": title,
            "genre": genre_preset.genre,
            "sub_genre": genre_preset.sub_genre,
            "target_words": target_words,
            "target_chapters": chapter_count,
            "project_type": project_type_value,
            "premise": premise,
            "export_markdown": True,
            "auto_repair": True,
            "draft_mode": bool(payload.get("draft_mode", False)),
            "stop_after_conception": bool(payload.get("stop_after_conception", False)),
            "story_enhancers": (
                payload.get("story_enhancers")
                if isinstance(payload.get("story_enhancers"), dict)
                else None
            ),
            "language": genre_preset.language,
            "writing_profile": writing_profile,
            "creative_key": creative_direction.key if creative_direction else "",
            "creative_brief": (
                creative_direction.model_dump(mode="json") if creative_direction else {}
            ),
            "hook_spec": selected_hook_spec.model_dump(mode="json") if selected_hook_spec else {},
            "concept_lab_bundle": (
                concept_bundle.model_dump(mode="json") if concept_bundle is not None else {}
            ),
            "user_hints": creative_hints,
            # The contract is resolved and validated at the creation boundary;
            # an unresolved selection is rejected before this payload is built.
            "genre_intent_contract": (
                contract.model_dump(mode="json") if contract is not None else {}
            ),
            # Enable AI conception for new projects (not resume)
            "_run_conception": is_new_project,
            "_genre_key": genre_key,
        }
        # Per-book writing model chosen in the creation form (Step 3). Validated
        # against the catalog. An explicit unknown/unavailable pick is rejected;
        # omission freezes the current global default for a NEW book only. Rides payload → task.payload → project metadata
        # (llm_model_id) via _pending_project_model at materialization.
        _model_id = str(payload.get("llm_model_id") or "").strip()
        _model_explicit = bool(_model_id)
        if not _model_id and is_new_project:
            from bestseller.services.model_catalog import default_new_project_model_id
            _model_id = default_new_project_model_id(load_settings()) or ""
        if _model_id:
            from bestseller.services.model_catalog import get_model_catalog_entry

            _entry = get_model_catalog_entry(_model_id)
            if _entry is None:
                raise ValueError(f"Unknown llm_model_id: {_model_id}")
            if not _entry.available:
                raise ValueError(
                    f"Unavailable llm_model_id: {_model_id} — "
                    f"{_entry.unavailable_reason or 'unavailable'}"
                )
            autowrite_payload["llm_model_id"] = _model_id

        # Freeze the complete creation request once, at the UI boundary.  The
        # downstream conception/planning/writing stages consume this envelope;
        # they must not reconstruct a partial intent from ad-hoc prompt hints.
        from bestseller.services.creation_intent_contract import (
            build_creation_intent_contract,
        )

        creation_intent = build_creation_intent_contract(
            contract,
            schema_version="creation-intent.v2" if configuration_submission else "creation-intent.v1",
            configuration=configuration_submission.configuration if configuration_submission else None,
            field_sources={
                **({"configuration": "explicit"} if configuration_submission else {}),
                "audience_orientation": "explicit" if audience_orientation else "default",
                "narrative_scale": "explicit" if _narrative_scale == "epic" else "default",
                "tone_preference": "explicit" if _tone_preference else "default",
                "chapter_count": "explicit"
                if payload.get("chapter_count") not in (None, "")
                else "default",
                "length_key": "explicit" if length_key else "default",
                "pov": "explicit" if payload.get("pov") else "default",
                "draft_mode": "explicit" if bool(payload.get("draft_mode")) else "default",
                "stop_after_conception": "explicit"
                if bool(payload.get("stop_after_conception"))
                else "default",
                "llm_model_id": "explicit" if _model_explicit else "default",
                "story_enhancers": "explicit" if (
                    not selected_enhancers.is_default()
                    or enhancer_input_sources["cost_style"] == "explicit"
                ) else "default",
                "creative_direction": "explicit" if creative_direction else "default",
                "concept_seed": "explicit" if explicit_concept_seed else "default",
                "hook_spec": "explicit" if selected_hook_spec else "default",
                "concept_lab": "explicit" if concept_bundle is not None else "default",
            },
            audience_orientation=audience_orientation,
            narrative_scale=_narrative_scale,
            tone_preference=_tone_preference,
            chapter_count=chapter_count,
            length_key=length_key or (fanqie_length_key or "long"),
            # ``fanqie_pov`` defaults to first-person for the short-story
            # product.  It must not leak into long-serial books whose form has
            # no POV selector; those retain the framework's third-limited
            # default unless a caller explicitly supplies a POV.
            pov=(fanqie_pov if is_fanqie_short else str(payload.get("pov") or "third-limited")),
            draft_mode=bool(payload.get("draft_mode", False)),
            stop_after_conception=bool(payload.get("stop_after_conception", False)),
            llm_model_id=_model_id or None,
            story_enhancers=selected_enhancers,
            concept_lab=(
                concept_bundle.model_dump(mode="json") if concept_bundle is not None else None
            ),
            creative_direction=(creative_direction.key if creative_direction else None),
            concept_seed=explicit_concept_seed or None,
            hook_spec={
                **(selected_hook_spec.model_dump(mode="json") if selected_hook_spec else {}),
                "_story_enhancer_input_sources": enhancer_input_sources,
            },
            language=genre_preset.language,
            project_type=project_type_value,
            creation_mode=creation_mode,
        )
        autowrite_payload["creation_intent_contract"] = creation_intent.model_dump(mode="json")
        if configuration_submission is not None:
            from bestseller.services.creation_configuration import configuration_metadata
            autowrite_payload["metadata"] = configuration_metadata({
                **(autowrite_payload.get("metadata") or {}),
                "creation_intent_contract": creation_intent.model_dump(mode="json")}, configuration_submission)
            autowrite_payload["configuration_submission"] = configuration_submission.model_dump(mode="json")
            if configuration_submission.configuration.readers.primary:
                autowrite_payload["audience"] = configuration_submission.configuration.readers.primary
        autowrite_payload["_conception_attempt_id"] = f"{slug}:initial"
        autowrite_payload["autonomous_book"] = payload.get("autonomous_book") is True
        autowrite_payload["requested_model"] = payload.get("requested_model")
        # Optional reader-orientation override (男频/女频). Empty/None lets the
        # heat-search agent + genre preset decide; an explicit pick is threaded
        # through as the project audience and a conception hint.
        _orientation = str(payload.get("audience_orientation") or "").strip().lower()
        _explicit_audience = {"male": "男频", "female": "女频"}.get(_orientation)
        if _explicit_audience:
            if not (configuration_submission and configuration_submission.configuration.readers.primary):
                autowrite_payload["audience"] = _explicit_audience
            autowrite_payload["audience_orientation"] = _orientation
            if isinstance(autowrite_payload.get("user_hints"), dict):
                autowrite_payload["user_hints"]["audience_orientation"] = _explicit_audience
        if fanqie_meta is not None:
            autowrite_payload["metadata"] = fanqie_meta
            autowrite_payload["creation_mode"] = "fanqie_short"
            autowrite_payload["length_key"] = fanqie_length_key
            autowrite_payload["pov"] = fanqie_pov
        creation_input_receipt = {
            "schema_version": QUICKSTART_CLIENT_SCHEMA_VERSION,
            "concept_seed_present": bool(explicit_concept_seed),
            "concept_seed_length": _quickstart_seed_length(explicit_concept_seed),
            "concept_seed_hash": _quickstart_seed_hash(explicit_concept_seed),
            "tone_preference": creation_intent.tone_preference,
            "effect_skills": list(creation_intent.story_enhancers.effect_skills),
            "contract_hash": creation_intent.contract_hash(),
            "production_profile": production_profile,
        }
        # Store the browser-visible controls and receipt in the durable task
        # envelope, not only in the one-shot HTTP response.  Failed conception
        # may have no Project row yet, so this payload is the sole resume source.
        autowrite_payload.update(
            {
                "concept_seed": explicit_concept_seed,
                "tone_preference": _tone_preference or None,
                "audience_orientation": _orientation or None,
                "narrative_scale": _narrative_scale or None,
                "client_schema_version": str(payload.get("client_schema_version") or ""),
                "client_intent_manifest": json.loads(
                    json.dumps(payload.get("client_intent_manifest") or {})
                ),
                "creation_input_receipt": creation_input_receipt,
            }
        )
        if resume_slug:
            # Re-entry is not a second creation request. Rebase the transport
            # envelope on the materialized project and its latest accepted
            # premise so generic preset text or refreshed form defaults cannot
            # become a competing story source.
            rebuilt_payload = asyncio.run(_rebuild_payload_from_db(load_settings(), resume_slug))
            if not rebuilt_payload:
                raise ValueError(f"Project '{resume_slug}' cannot be rebuilt for resume")
            autowrite_payload = _merge_resume_payload_with_project_truth(
                autowrite_payload,
                rebuilt_payload,
            )
            autowrite_payload["_run_conception"] = False
        task = self.create_autowrite_task(autowrite_payload)
        task["creation_input_receipt"] = creation_input_receipt
        # Enrich response with quickstart metadata
        task["quickstart_meta"] = {
            "production_profile": production_profile,
            **({"configuration_submission": configuration_submission.model_dump(mode="json")} if configuration_submission else {}),
            "creation_mode": creation_mode,
            "genre_key": genre_key,
            "genre_intent_contract": (
                contract.model_dump(mode="json") if contract is not None else {}
            ),
            "genre_name": genre_preset.name,
            "heat_domains": genre_preset.heat_domains,
            "reader_rewards": genre_preset.reader_rewards,
            "narrative_drives": genre_preset.narrative_drives,
            "creative_key": creative_direction.key if creative_direction else "",
            "creative_title": creative_direction.title if creative_direction else "",
            "stop_after_conception": bool(payload.get("stop_after_conception", False)),
            "concept_seed": explicit_concept_seed,
            "hook_spec": selected_hook_spec.model_dump(mode="json") if selected_hook_spec else {},
            "concept_lab_bundle": (
                concept_bundle.model_dump(mode="json") if concept_bundle is not None else {}
            ),
            "concept_lab_summary": (
                {
                    "bundle_id": concept_bundle.bundle_id,
                    "reader_promise": concept_bundle.reader_promise,
                    "one_liner": concept_bundle.one_liner,
                    "title_seed": (
                        concept_bundle.title_seeds[0].text if concept_bundle.title_seeds else ""
                    ),
                }
                if concept_bundle is not None
                else {}
            ),
            "chapter_count": chapter_count,
            "target_words": target_words,
            "length_key": fanqie_length_key if is_fanqie_short else length_key,
            "pov": fanqie_pov if is_fanqie_short else None,
            "creation_input_receipt": creation_input_receipt,
        }
        return task

    def create_if_task(self, payload: dict[str, object]) -> dict[str, object]:
        task_id = str(uuid4())
        now = _utc_now()
        task = WebTaskState(
            task_id=task_id,
            task_type="if_generation",
            status="queued",
            created_at=now,
            updated_at=now,
            project_slug=str(payload.get("project_slug") or ""),
            title=f"IF Generation: {payload.get('project_slug') or ''}",
            current_stage="queued",
            payload=json.loads(json.dumps(payload, default=_json_default)),
            runtime_provenance=self._task_runtime_provenance(task_id, now),
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
                    genre = if_cfg_for_profile.if_genre
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
                            metadata={"execution_scope_id": current_execution_scope()},
                            writing_profile=wp_override,
                        ),
                        settings,
                    )
                    raise_if_execution_stopped()
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
                raise_if_execution_stopped()
                return str(out_path)

            with bind_progress(progress):
                out_path = asyncio.run(_run_if())
            self._mark_completed(task_id, {"story_package": out_path})
        except ExecutionBudgetError as exc:
            self._stop_execution_task(task_id, exc)
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
            from bestseller.services.web_pipeline_owner import web_pipeline_owner
            async with web_pipeline_owner(settings, project_slug), session_scope(settings) as session:
                from bestseller.services.chapter_review_reconciliation import prepare_native_review_resume
                resume_project = await get_project_by_slug(session, project_slug)
                if resume_project is not None:
                    resume = await prepare_native_review_resume(
                        session, settings, project_id=resume_project.id, requested_by="web-ui-repair",
                    )
                    if resume["status"] == "prepared":
                        await session.commit()
                result = await run_project_repair(
                    session,
                    settings,
                    project_slug,
                    requested_by="web-ui",
                    refresh_impacts=bool(payload.get("refresh_impacts", True)),
                    export_markdown=bool(payload.get("export_markdown", True)),
                    include_pending_rewrite_tasks=bool(
                        payload.get("include_pending_rewrite_tasks", True)
                    ),
                    scan_publication_gate_candidates=bool(
                        payload.get("scan_publication_gate_candidates", False)
                    ),
                    progress=progress,
                )
                raise_if_execution_stopped()
            return json.loads(json.dumps(result.model_dump(mode="json"), default=_json_default))

        try:
            with bind_progress(progress):
                result = asyncio.run(runner())
            self._mark_completed(task_id, result)
        except ExecutionBudgetError as exc:
            self._stop_execution_task(task_id, exc)
        except TaskCancelledError:
            self._mark_cancelled(task_id)
        except Exception:
            tb = traceback.format_exc()
            logger.exception("Repair task %s crashed", task_id)
            self._mark_failed(task_id, tb)

    def _run_prewrite_repair_worker(self, task_id: str, payload: dict[str, object]) -> None:
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
            rewrite_task_id = UUID(str(payload["rewrite_task_id"]))
            progress(
                "prewrite_material_repair_started",
                {
                    "project_slug": project_slug,
                    "rewrite_task_id": str(rewrite_task_id),
                },
            )
            from bestseller.services.web_pipeline_owner import web_pipeline_owner
            async with web_pipeline_owner(settings, project_slug), session_scope(settings) as session:
                result = await repair_prewrite_material_from_task(
                    session,
                    settings,
                    project_slug,
                    rewrite_task_id=rewrite_task_id,
                )
                raise_if_execution_stopped()
            progress("prewrite_material_repair_completed", result)
            return json.loads(json.dumps(result, default=_json_default))

        try:
            with bind_progress(progress):
                result = asyncio.run(runner())
            self._mark_completed(task_id, result)
        except ExecutionBudgetError as exc:
            self._stop_execution_task(task_id, exc)
        except TaskCancelledError:
            self._mark_cancelled(task_id)
        except Exception:
            tb = traceback.format_exc()
            logger.exception("Prewrite repair task %s crashed", task_id)
            self._mark_failed(task_id, tb)

    def _run_chapter_revision_worker(self, task_id: str, payload: dict[str, object]) -> None:
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
            chapter_number = int(payload["chapter_number"])
            operation = str(payload["operation"])
            rewrite_task_id = UUID(str(payload["rewrite_task_id"]))
            progress(
                "chapter_revision_started",
                {
                    "project_slug": project_slug,
                    "chapter_number": chapter_number,
                    "operation": operation,
                    "rewrite_task_id": str(rewrite_task_id),
                },
            )
            from bestseller.services.web_pipeline_owner import web_pipeline_owner
            async with web_pipeline_owner(settings, project_slug), session_scope(settings) as session:
                if operation == "regenerate":
                    from bestseller.services.review_source_ordering import CODE, archived_rewrite_task_ids
                    original_task = await session.get(RewriteTaskModel, rewrite_task_id)
                    if original_task is not None and rewrite_task_id in await archived_rewrite_task_ids(session, project_id=original_task.project_id, settings=settings):
                        raise ValueError(CODE)
                    chapter_result = await run_chapter_pipeline(
                        session,
                        settings,
                        project_slug,
                        chapter_number,
                        requested_by="reader-ui",
                        export_markdown=True,
                        supersede_pending_rewrites=True,
                        progress=progress,
                    )
                    raise_if_execution_stopped()
                    task = await session.get(RewriteTaskModel, rewrite_task_id)
                    if task is not None and rewrite_task_id not in await archived_rewrite_task_ids(session, project_id=task.project_id, settings=settings):
                        task.status = "completed"
                        task.attempts = int(task.attempts or 0) + 1
                        task.metadata_json = {
                            **(task.metadata_json or {}),
                            "completed_at": _utc_now(),
                            "chapter_workflow_run_id": str(chapter_result.workflow_run_id),
                            "result_chapter_draft_version_no": (
                                chapter_result.chapter_draft_version_no
                            ),
                            "export_artifact_id": str(chapter_result.export_artifact_id)
                            if chapter_result.export_artifact_id
                            else None,
                        }
                    result: dict[str, object] = {
                        "ok": True,
                        "operation": operation,
                        "chapter_number": chapter_number,
                        "rewrite_task_id": str(rewrite_task_id),
                        "chapter_workflow_run_id": str(chapter_result.workflow_run_id),
                        "result_version_no": chapter_result.chapter_draft_version_no,
                    }
                else:
                    if operation not in {"polish", "humanize", "issue"}:
                        raise ValueError("Unsupported chapter revision operation.")
                    result = await apply_chapter_revision_task(
                        session,
                        settings,
                        project_slug,
                        chapter_number,
                        rewrite_task_id=rewrite_task_id,
                        operation=operation,  # type: ignore[arg-type]
                    )
                    raise_if_execution_stopped()
            progress(
                "chapter_revision_rejected" if result.get("ok") is False
                else "chapter_revision_completed",
                result,
            )
            return json.loads(json.dumps(result, default=_json_default))

        try:
            with bind_progress(progress):
                result = asyncio.run(runner())
            if result.get("ok") is False:
                # A rejected edit is a finished attempt, not a successful edit.
                # Persist the full service receipt for UI requirement feedback.
                with self._lock:
                    task = self._tasks[task_id]
                    task.result = result
                    task.status = "failed"
                    task.current_stage = "chapter_revision_rejected"
                    task.updated_at = _utc_now()
                    task.error = str(
                        result.get("rejected_reason") or result.get("error")
                        or "Chapter revision did not satisfy the edit requirements."
                    )
                    self._save_to_disk()
            else:
                self._mark_completed(task_id, result)
        except ExecutionBudgetError as exc:
            self._stop_execution_task(task_id, exc)
        except TaskCancelledError:
            self._mark_cancelled(task_id)
        except Exception:
            tb = traceback.format_exc()
            logger.exception("Chapter revision task %s crashed", task_id)
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
            snapshot: list[tuple[str, str, str, set[float], float, str | None]] = []
            for tid, task in self._tasks.items():
                cached_projection = (task.result or {}).get("_worker_event_projection")
                if task.status not in ("running", "queued", "failed", "incomplete") and not (
                    task.status in {"completed", "cancelled"} and isinstance(cached_projection, dict)
                ):
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
                        cached_projection.get("job_id")
                        if isinstance(cached_projection, dict) else None,
                    )
                )

        updated = 0
        for tid, slug, task_type, known_ts, task_updated_ts, projected_job_id in snapshot:
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
                    and projected_job_id != job_id
                    and candidate_latest_ts > 0
                    and candidate_latest_ts <= task_updated_ts
                ):
                    continue
                if state_rank > selected_state_rank or (
                    state_rank == selected_state_rank and candidate_latest_ts > selected_latest_ts
                ):
                    selected = (job_id, redis_key)
                    raw_events = candidate_events
                    selected_latest_ts = candidate_latest_ts
                    selected_state_rank = state_rank
            if selected is None or not raw_events:
                continue

            normalized, _, _ = _normalise_worker_progress_events(raw_events)
            # Keep numeric timestamps on the existing card history for deduplication.
            for event in normalized:
                event["timestamp"] = _timestamp_seconds(event["timestamp"])
            fresh = [event for event in normalized if event["timestamp"] not in known_ts]
            if not normalized:
                continue

            with self._lock:
                task = self._tasks.get(tid)
                if task is None:
                    continue
                if fresh:
                    task.progress_events = (task.progress_events + fresh)[-300:]
                result = dict(task.result or {})
                cached = result.get("_worker_event_projection") or {}
                cached = cached if isinstance(cached, dict) else {}
                # Retain root + decisive receipt across Redis tail trimming. This
                # is display evidence only, never an execution or adoption write.
                prior = cached.get("events", []) if cached.get("job_id") == selected[0] else []
                evidence = sorted(prior + normalized,
                                  key=lambda event: _timestamp_seconds(event.get("timestamp")) or 0)
                projection = _project_task_events(evidence, task_id=selected[0])
                decisive = projection["event"]
                current_events = projection["events"]
                retained = current_events[:1]
                if decisive and decisive not in retained:
                    retained.append(decisive)
                result["_worker_event_projection"] = {"job_id": selected[0], "events": retained}
                terminal_code = projection["code"]
                # An unattributed pre-existing execution stop cannot be retired
                # by heartbeat/progress. Only our own superseded projection is
                # replaceable, and its original receipt remains in the history.
                existing_code = execution_terminal_code(task.result or {})
                prior_code = _project_task_events(prior, task_id=selected[0])["code"]
                if existing_code and existing_code != prior_code:
                    terminal_code = existing_code
                elif prior_code and not terminal_code:
                    result.pop("execution_terminal", None)
                if terminal_code:
                    result["execution_terminal"] = {"terminal": True, "code": terminal_code}
                    task.status = "cancelled" if terminal_code == "execution_cancelled" else "failed"
                    task.current_stage = terminal_code
                    task.error = terminal_code
                else:
                    payload = _task_event_payload(decisive)
                    stage = str(decisive.get("stage") or "delegated_to_worker_self_heal")
                    task.current_stage = stage
                    projected_status = projection["status"]
                    if not projection["terminal"]:
                        projected_status = "running" if selected_state_rank == 1 else "incomplete"
                    task.status = projected_status
                    if projected_status in {"failed", "incomplete", "cancelled"}:
                        reason = payload.get("reason")
                        task.error = str(payload.get("error") or payload.get("message") or reason or {
                            "failed": "Worker self-heal task failed",
                            "incomplete": "Task reached a machine-repair or attention gate.",
                            "cancelled": "Stopped by operator",
                        }[projected_status])
                        if stage in _TERMINAL_ATTENTION_STAGES and projected_status == "incomplete":
                            task.current_stage = "machine_repair_required"
                        if (
                            stage in {"machine_blocked", "machine_repair_required",
                                      "blocked_generation_gate"}
                            and reason and ":" in str(reason)
                        ):
                            task.current_stage = f"{stage}:{str(reason).split(':', 1)[1].strip()}"
                        if projected_status == "cancelled":
                            task.cancel_requested = False
                    else:
                        task.error = None
                    if projection["terminal"]:
                        result.update(payload)
                task.result = result
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
    payload: dict[str, object] = {
        "slug": project.slug,
        "title": project.title,
        "genre": project.genre,
        "sub_genre": project.sub_genre,
        "target_words": project.target_word_count,
        "target_chapters": project.target_chapters,
        "project_type": getattr(project, "project_type", None),
        "premise": premise,
        "export_markdown": True,
        "auto_repair": True,
        "metadata": metadata,
        "writing_profile": metadata.get("writing_profile"),
        "_run_conception": False,
        "_genre_key": genre_key,
    }

    # Carry forward core creative controls for legacy recovery resume paths.
    # Without this, some historical/recovered tasks only containing a
    # persisted project row lose user-authored intent and drift into generic
    # defaults.
    creative_transfer_fields = {
        "configuration_submission": ({"configuration": metadata["creation_configuration"],
            "execution_window": metadata["execution_window"]} if metadata.get("creation_configuration") else None),
        "creation_intent_contract": metadata.get("creation_intent_contract"),
        "genre_intent_contract": metadata.get("genre_intent_contract"),
        "creative_key": metadata.get("creative_key"),
        "concept_seed": metadata.get("concept_seed"),
        "tone_preference": metadata.get("tone_preference"),
        "audience_orientation": metadata.get("audience_orientation"),
        "story_enhancers": metadata.get(STORY_ENHANCERS_METADATA_KEY),
        "hook_spec": metadata.get("hook_spec"),
        "concept_lab_bundle": metadata.get("concept_lab") or metadata.get("concept_lab_bundle"),
        "length_key": metadata.get("length_key"),
        "pov": metadata.get("pov"),
    }
    for key, value in creative_transfer_fields.items():
        if value not in (None, ""):
            payload[key] = value

    # Restore user_hints + direct user-facing intent markers so planning layers
    # can rebuild prompt blocks exactly as the original creation flow.
    user_hints = metadata.get("user_hints")
    if isinstance(user_hints, dict) and user_hints:
        payload["user_hints"] = user_hints

    if is_fanqie_short_project(project):
        payload["creation_mode"] = "fanqie_short"
        payload["length_key"] = metadata.get("length_key")
        payload["pov"] = metadata.get("pov")
    return payload


def _merge_resume_payload_with_project_truth(
    saved_payload: dict[str, object],
    rebuilt_payload: dict[str, object] | None,
) -> dict[str, object]:
    """Rebase a resume request on the materialized project truth.

    ``saved_payload`` is the pre-materialization request envelope persisted by
    the web task manager.  It is valuable for operational switches and for
    legacy fields that were never copied into the project row, but it must not
    outrank the project once that row exists.  In particular, conception may
    replace the generic form premise with the approved concept, while the old
    task envelope still carries the generic premise and exploratory metadata.

    Keep saved-only fields, then let every field rebuilt from PostgreSQL win.
    This makes the project row the sole resume truth without dropping legacy
    request controls that the DB projection does not know about.
    """

    merged = dict(saved_payload)
    if rebuilt_payload:
        merged.update(rebuilt_payload)
    return merged


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
        payload = _payload_from_project_model(project)
        # Resume from the accepted, versioned premise. The original web
        # envelope may contain a generic pre-conception sentence, while a
        # title-derived fallback is not story truth at all.
        from bestseller.services.story_source import load_accepted_project_premise

        payload["premise"] = await load_accepted_project_premise(session, project)
        canonical_id = (project.metadata_json or {}).get("creation_canonical_snapshot_id")
        if canonical_id:
            canonical = await session.get(PlanningArtifactVersionModel, UUID(str(canonical_id)))
            if (
                canonical is None
                or canonical.project_id != project.id
                or canonical.artifact_type != "conception_snapshot"
            ):
                raise ValueError("approved conception snapshot is missing")
            payload["premise"] = canonical_snapshot_premise(
                {"status": canonical.status, "content": canonical.content}
            )
            metadata = project.metadata_json or {}
            if canonical_snapshot_content_hash(
                {"status": canonical.status, "content": canonical.content}
            ) != str(metadata.get("creation_canonical_snapshot_hash") or ""):
                raise ValueError("approved conception snapshot hash is inconsistent")
            payload["approved_conception_snapshot_id"] = str(canonical.id)
            payload["approved_conception_snapshot_hash"] = (
                metadata.get("creation_canonical_snapshot_hash")
            )
            intent_id = metadata.get("creation_intent_snapshot_id")
            if intent_id:
                intent = await session.get(PlanningArtifactVersionModel, UUID(str(intent_id)))
                if (
                    intent is None
                    or intent.project_id != project.id
                    or intent.artifact_type != "creation_intent"
                ):
                    raise ValueError("creation intent snapshot is missing")
                intent_content = intent.content if isinstance(intent.content, Mapping) else {}
                authoritative_seed = str(intent_content.get("concept_seed") or "").strip()
                if authoritative_seed:
                    payload["approved_authoritative_seed"] = authoritative_seed
            if (
                metadata.get("conception_approved") is True
                and str(metadata.get("planning_status") or "").lower()
                == "conception_approved"
            ):
                payload = build_planning_handoff_payload(
                    payload,
                    project=project,
                    canonical_snapshot={
                        "id": str(canonical.id),
                        "status": canonical.status,
                        "content": canonical.content,
                    },
                )
        return payload


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


async def _recover_projects_from_output(
    settings: AppSettings,
    *,
    operator_opt_in: bool = False,
) -> list[dict[str, object]]:
    """Explicitly import legacy exports into DB records, preserving the files.

    PostgreSQL is authoritative. Merely finding exports or starting the Web
    server must never adopt them. Imported records retain unverified-export
    provenance; this recovery does not create promoted drafts or review proof.
    """
    if operator_opt_in is not True:
        logger.info("Export-to-database recovery skipped: explicit operator opt-in required")
        return []

    import re as _re

    output_base = Path(settings.output.base_dir)
    if not output_base.exists():
        return []

    recovered: list[dict[str, object]] = []

    async with session_scope(settings) as session:
        existing_projects = await list_projects(session)
        existing_slugs = {p.slug for p in existing_projects}

        for slug_dir in sorted(output_base.iterdir()):
            # Skip hidden AND "_"-prefixed dirs: the latter are internal scratch
            # dirs (e.g. ``_compare-ohstory`` holds chapter-001-freehand.md +
            # chapter-001-ohstory.md — two files → same chapter_number → a
            # uq_chapter_number crash that previously failed the WHOLE startup
            # recovery and left new books stuck at ``auto_resume_pending``).
            if not slug_dir.is_dir() or slug_dir.name.startswith((".", "_")):
                continue
            slug = slug_dir.name
            if slug in existing_slugs:
                continue
            if is_project_delete_tombstoned(settings, slug):
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
                    "recovery_authority": "operator_opt_in_unverified_export",
                },
            )
            session.add(project)
            # Flush to allocate project.id so we can link chapter rows.
            await session.flush()

            # Materialize ChapterModel rows for every chapter-*.md on disk
            # marked as COMPLETE, so ``run_autowrite_pipeline``'s resume
            # filter (which checks ``ChapterStatus.COMPLETE`` in DB, not disk)
            # correctly skips them and continues from the next chapter.
            _seen_ch_nums: set[int] = set()
            for ch_file in chapter_files:
                ch_num_match = _re.search(r"chapter-(\d+)", ch_file.name)
                if not ch_num_match:
                    continue
                ch_num = int(ch_num_match.group(1))
                # Dedupe: variant files (chapter-001.md + chapter-001-ohstory.md)
                # map to the same chapter_number → would violate uq_chapter_number.
                if ch_num in _seen_ch_nums:
                    continue
                _seen_ch_nums.add(ch_num)
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
                        metadata_json={
                            "recovered": True,
                            "source_file": ch_file.name,
                            "recovery_authority": "operator_opt_in_unverified_export",
                        },
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


async def _load_library_archived_project_slugs(settings: AppSettings) -> set[str]:
    async with session_scope(settings) as session:
        projects = await list_projects(session)
    archived: set[str] = set()
    for project in projects:
        slug = str(getattr(project, "slug", "") or "").strip()
        if slug and _project_library_archived(getattr(project, "metadata_json", None)):
            archived.add(slug)
    return archived


async def _load_projects_payload(
    settings: AppSettings,
    *,
    light: bool = False,
    project_slug: str | None = None,
    progress_readback: bool = False,
) -> list[dict[str, object]]:
    async with session_scope(settings) as session:
        if project_slug is None:
            rows = await list_projects(session)
        else:
            project = await get_project_by_slug(session, project_slug)
            rows = [project] if project is not None else []
        content_stats = await _load_project_content_stats(
            session,
            rows,
            settings=settings,
            include_filesystem=not light,
        )
        project_activity = await _load_workflow_activity_by_project(
            session,
            [row.id for row in rows],
        )
        active_project_slugs = {slug for slug, value in project_activity.items() if value["execution_active"]}
        redis_url = getattr(getattr(settings, "redis", None), "url", None)
        if redis_url:
            active_project_slugs.update(_fetch_heal_owned_slugs(str(redis_url)))
        if light:
            project_ids = [row.id for row in rows]
            repair_counts = await _load_project_chapter_state_counts(session, project_ids)
            repair_tasks = await _load_project_autonomous_repair_task_counts(session, project_ids)
            payload_rows: list[dict[str, object]] = []
            for row in rows:
                meta = row.metadata_json if isinstance(row.metadata_json, dict) else {}
                is_short = is_fanqie_short_project(row)
                stats = content_stats.get(row.id, {})
                completed_units = _safe_int(stats.get("completed_chapters"))
                word_total = _safe_int(stats.get("word_count_total"))
                repair_status = _build_project_repair_status_payload(
                    row, repair_counts.get(row.id, []), repair_tasks.get(row.id, {}),
                )
                delivery = await _read_project_delivery_status(
                    session, row, **({"progress_only": True} if progress_readback else {}),
                )
                is_closed = delivery["is_complete"] is True
                has_active_workflow = (
                    row.slug in active_project_slugs
                    and not is_closed
                    and not _project_library_archived(meta)
                )
                book_state = _library_book_state(
                    status=row.status,
                    completed_units=completed_units,
                    target_units=int(row.target_chapters or 0),
                    has_content=(
                        word_total > 0 or completed_units > 0
                        or _safe_int(stats.get("candidate_chapters")) > 0
                    ),
                    archived=_project_library_archived(meta),
                    repair_status=repair_status,
                    has_active_workflow=has_active_workflow,
                    delivery_closure=delivery,
                )
                if (not has_active_workflow and project_activity.get(row.slug, {}).get("status") == "stale"
                    and book_state not in {"closed_complete", "archived", "delivery_pending"}):
                    book_state = "needs_attention"
                payload_rows.append(
                    {
                        "id": str(row.id),
                        "slug": row.slug,
                        "title": row.title,
                        "genre": row.genre,
                        "status": row.status,
                        "book_state": book_state,
                        "delivery_closure": delivery,
                        "book_state_label": _library_book_state_label(book_state),
                        "library_archived": _project_library_archived(meta),
                        "archived_at": meta.get("library_archived_at"),
                        "task_visibility": (
                            "library_archive" if _project_library_archived(meta) else "library"
                        ),
                        "can_trigger_revision": bool(row.slug),
                        "llm_model_id": meta.get("llm_model_id"),
                        "repair_status": repair_status,
                        "project_type": row.project_type,
                        "content_mode": meta.get("content_mode"),
                        "length_key": meta.get("length_key"),
                        "unit_kind": "single_story" if is_short else "chapter",
                        "unit_label": "全文" if is_short else "章节",
                        "target_word_count": row.target_word_count,
                        "target_chapters": row.target_chapters,
                        "segment_count": row.target_chapters if is_short else None,
                        "completed_chapters": completed_units,
                        **_adoption_projection_fields(stats),
                        "completed_segments": completed_units if is_short else 0,
                        "words_on_disk": word_total,
                        "word_count_total": word_total,
                        "current_chapter_number": row.current_chapter_number,
                        "has_active_workflow": has_active_workflow,
                        **_project_activity_fields(project_activity.get(row.slug), has_active_workflow),
                        "created_at": row.created_at.isoformat() if row.created_at else None,
                        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
                    }
                )
            if progress_readback:
                for row, payload in zip(rows, payload_rows, strict=True):
                    if is_fanqie_short_project(row):
                        exported = _build_fanqie_short_export_task_stats(settings, row, include_chapters=False)
                        if exported:
                            payload.update({key: exported.get(key) for key in (
                                "current_export_filename", "current_export_modified_at")})
                            payload["export_word_count_total"] = exported.get("word_count_total", 0)
            return payload_rows
        repair_counts = await _load_project_chapter_state_counts(
            session,
            [row.id for row in rows],
        )
        autonomous_repair_counts = await _load_project_autonomous_repair_task_counts(
            session,
            [row.id for row in rows],
        )
        latest_repair_workflows = await _load_latest_project_repair_workflow_snapshots(
            session,
            [row.id for row in rows],
        )
        payload_rows: list[dict[str, object]] = []
        for row in rows:
            meta = row.metadata_json if isinstance(row.metadata_json, dict) else {}
            is_short = is_fanqie_short_project(row)
            stats = content_stats.get(row.id, {})
            completed_units = _safe_int(stats.get("completed_chapters"))
            word_total = _safe_int(stats.get("word_count_total"))
            delivery = await _read_project_delivery_status(session, row)
            is_closed = delivery["is_complete"] is True
            has_active_workflow = (
                row.slug in active_project_slugs
                and not is_closed
                and not _project_library_archived(meta)
            )
            repair_status = _build_project_repair_status_payload(
                row,
                repair_counts.get(row.id, []),
                autonomous_repair_counts.get(row.id, {}),
                latest_repair_workflows.get(row.id),
            )
            book_state = _library_book_state(
                status=row.status,
                completed_units=completed_units,
                target_units=int(row.target_chapters or 0),
                has_content=word_total > 0 or completed_units > 0,
                archived=_project_library_archived(meta),
                repair_status=repair_status,
                has_active_workflow=has_active_workflow,
                delivery_closure=delivery,
            )
            if (not has_active_workflow and project_activity.get(row.slug, {}).get("status") == "stale"
                and book_state not in {"closed_complete", "archived", "delivery_pending"}):
                book_state = "needs_attention"
            payload_rows.append(
                {
                    "id": str(row.id),
                    "slug": row.slug,
                    "title": row.title,
                    "genre": row.genre,
                    "status": row.status,
                    "book_state": book_state,
                    "delivery_closure": delivery,
                    "book_state_label": _library_book_state_label(book_state),
                    "project_type": row.project_type,
                    "content_mode": meta.get("content_mode"),
                    "length_key": meta.get("length_key"),
                    "unit_kind": "single_story" if is_short else "chapter",
                    "unit_label": "全文" if is_short else "章节",
                    "target_word_count": row.target_word_count,
                    "target_chapters": row.target_chapters,
                    "segment_count": row.target_chapters if is_short else None,
                    "completed_chapters": completed_units,
                    **_adoption_projection_fields(stats),
                    "completed_segments": completed_units if is_short else 0,
                    "words_on_disk": word_total,
                    "word_count_total": word_total,
                    "current_chapter_number": row.current_chapter_number,
                    "has_active_workflow": has_active_workflow,
                    **_project_activity_fields(project_activity.get(row.slug), has_active_workflow),
                    "created_at": row.created_at.isoformat() if row.created_at else None,
                    "updated_at": row.updated_at.isoformat() if row.updated_at else None,
                    "repair_status": repair_status,
                }
            )
        return payload_rows


def _adopted_knowledge_evidence(chapter: Any, draft: Any, snapshot: Any, mode: str) -> tuple[bool, bool, str]:
    """Project exact applied receipts only; never extract, repair or adopt."""
    from bestseller.services.feedback import KNOWLEDGE_LINEAGE_SCHEMA

    source = {
        "schema": KNOWLEDGE_LINEAGE_SCHEMA,
        "project_id": str(chapter.project_id), "chapter_id": str(chapter.id),
        "source_chapter_draft_version_id": str(draft.id),
        "source_version_no": draft.version_no,
        "source_body_sha256": hashlib.sha256(draft.content_md.encode("utf-8")).hexdigest(),
    }

    def matches(value: Any) -> bool:
        return isinstance(value, Mapping) and all(value.get(k) == v for k, v in source.items())

    feedback_ok = False
    receipts = (chapter.metadata_json or {}).get("knowledge_feedback_receipts", [])
    for receipt in reversed(receipts if isinstance(receipts, list) else []):
        if isinstance(receipt, Mapping) and receipt.get("status") in {"ok", "empty", "already_applied"}:
            # The latest applied source wins; an older matching receipt is history.
            from bestseller.services.knowledge_evidence_reconciliation import feedback_evidence_complete
            feedback_ok = matches(receipt.get("source_lineage")) and feedback_evidence_complete(
                chapter, receipt, draft.content_md,
            )
            break
    facts = snapshot.facts if snapshot is not None else None
    contract = facts.get("snapshot_contract") if isinstance(facts, Mapping) else None
    snapshot_ok = bool(
        snapshot is not None and snapshot.project_id == chapter.project_id
        and snapshot.chapter_id == chapter.id and snapshot.chapter_number == chapter.chapter_number
        and snapshot.extraction_status == f"ok_{mode}"
        and matches(contract) and contract.get("is_usable") is True
    )
    bible_receipts = (chapter.metadata_json or {}).get("story_bible_update_receipts", [])
    bible_state = "unknown"  # historical debt paths never required this optional extraction
    if bible_receipts:
        latest = next((r for r in reversed(bible_receipts) if isinstance(r, Mapping)), None) if isinstance(bible_receipts, list) else None
        if latest is None:
            bible_state = "pending"
        elif latest.get("status") in {"llm_error", "rejected_source"}:
            bible_state = "error"
        elif latest.get("status") in {"ok", "empty", "already_applied"} and matches(latest.get("source_lineage")):
            bible_state = "closed"
        else:
            # Includes explicit reconciliation and an applied projection of a
            # different draft/body. Older successes cannot erase this state.
            bible_state = "pending"
    return feedback_ok, snapshot_ok, bible_state


async def _load_adopted_chapter_progress(
    session: Any,
    project_ids: list[UUID],
    *,
    project_languages: Mapping[UUID, str] | None = None,
) -> dict[UUID, dict[str, object]]:
    """Read adoption evidence; candidate text and export files are separate work.

    A promoted incumbent can differ from the current editing candidate.
    Numeric debt counts only after the production service revalidates its exact
    current draft, score and review. This projection never promotes or repairs.
    """
    from sqlalchemy import and_, or_, select
    from bestseller.infra.db.models import ChapterDraftVersionModel, ChapterModel, ChapterStateSnapshotModel
    from bestseller.services.adopted_chapter_source import (
        choose_usable_promoted_chapter_source,
        select_adopted_chapter_source,
    )
    from bestseller.services.chapter_word_count_truth import authoritative_zh_word_count
    from bestseller.services.continuity import snapshot_evidence_for_sources

    stats = {
        project_id: {
            "source": "db", "adoption_evidence": "verified",
            "completed_chapters": 0, "adopted_chapters": 0,
            "promoted_chapters": 0, "accepted_with_debt_chapters": 0,
            "candidate_chapters": 0, "word_count_total": 0,
            "candidate_word_count_total": 0, "adopted_chapter_numbers": [], "chapters": [],
            "knowledge": {"status": "pending", "label": "未开始", "adopted_chapters": 0,
                          "closed_chapters": 0, "feedback_chapters": 0, "snapshot_chapters": 0,
                          "pending_chapters": 0, "closed_chapter_numbers": [],
                          "bible": {"status": "unknown", "closed_chapters": 0,
                                    "pending_chapters": 0, "error_chapters": 0, "unknown_chapters": 0}},
        }
        for project_id in project_ids
    }
    if not project_ids:
        return stats
    rows = await session.execute(
        select(ChapterModel, ChapterDraftVersionModel)
        .outerjoin(ChapterDraftVersionModel, and_(
            ChapterDraftVersionModel.chapter_id == ChapterModel.id,
            or_(ChapterDraftVersionModel.promotion_state == "promoted",
                ChapterDraftVersionModel.is_current.is_(True)),
        ))
        .where(ChapterModel.project_id.in_(project_ids))
        .order_by(ChapterModel.chapter_number, ChapterDraftVersionModel.version_no)
        .execution_options(populate_existing=True, autoflush=False)
    )
    by_chapter: dict[UUID, tuple[Any, list[Any]]] = {}
    for chapter, draft in rows:
        if chapter.project_id not in stats:
            continue
        entry = by_chapter.setdefault(chapter.id, (chapter, []))
        if (draft is not None and draft.project_id == chapter.project_id
            and draft.chapter_id == chapter.id and str(draft.content_md or "").strip()):
            entry[1].append(draft)
    snapshots = {}
    if by_chapter:
        for snapshot, in await session.execute(
            select(ChapterStateSnapshotModel).where(ChapterStateSnapshotModel.project_id.in_(project_ids))
            .execution_options(populate_existing=True, autoflush=False)
        ):
            snapshots[(snapshot.project_id, snapshot.chapter_id)] = snapshot
    knowledge_sources = {project_id: {} for project_id in project_ids}
    knowledge_source_bodies = {project_id: {} for project_id in project_ids}
    bible_sources = {project_id: {} for project_id in project_ids}
    for chapter, drafts in by_chapter.values():
        snapshot = snapshots.get((chapter.project_id, chapter.id))
        knowledge_sources[chapter.project_id][chapter.chapter_number] = (chapter.id, None, snapshot)
        target = stats[chapter.project_id]
        language = (project_languages or {}).get(chapter.project_id, "zh-CN")
        promoted = choose_usable_promoted_chapter_source(chapter=chapter, drafts=drafts)
        current = next((d for d in drafts if d.is_current), None)
        accepted = promoted
        mode = "promoted"
        metadata = chapter.metadata_json or {}
        debt = metadata.get("numeric_promotion_debt")
        if (current is not None and isinstance(debt, Mapping)
            and debt.get("draft_id") == str(current.id)
            and metadata.get("chapter_quality_debt") and chapter.production_state == "quality_debt"
            and current.promotion_state not in {"promoted", "superseded", "rejected", "quarantined"}):
            numeric_source, numeric_receipt = await select_adopted_chapter_source(
                session, chapter=chapter, lock=False,
            )
            if numeric_source is current and numeric_receipt is not None:
                accepted, mode = current, "accepted_with_debt"
        if current is not None and (accepted is None or current.id != accepted.id):
            target["candidate_chapters"] += 1
            target["candidate_word_count_total"] += authoritative_zh_word_count(
                current.content_md, language=language,
            )
        if accepted is None:
            continue
        knowledge_sources[chapter.project_id][chapter.chapter_number] = (chapter.id, {
            "schema": "adopted-knowledge.v1", "project_id": str(chapter.project_id),
            "chapter_id": str(chapter.id), "source_chapter_draft_version_id": str(accepted.id),
            "source_version_no": accepted.version_no,
            "source_body_sha256": hashlib.sha256(accepted.content_md.encode("utf-8")).hexdigest(),
            "adoption_state": mode,
        }, snapshot)
        knowledge_source_bodies[chapter.project_id][chapter.chapter_number] = accepted.content_md
        words = authoritative_zh_word_count(accepted.content_md, language=language)
        target["adopted_chapters"] += 1
        target["completed_chapters"] += 1  # compatibility alias, evidence-based
        target["promoted_chapters" if mode == "promoted" else "accepted_with_debt_chapters"] += 1
        target["word_count_total"] += words
        target["adopted_chapter_numbers"].append(int(chapter.chapter_number))
        target["chapters"].append({
            "number": int(chapter.chapter_number), "title": chapter.title,
            "word_count": words, "estimated_read_minutes": (words + 499) // 500,
            "target_word_count": int(chapter.target_word_count or 0),
            "status": chapter.status, "acceptance": mode, "draft_id": str(accepted.id),
        })
        feedback_ok, snapshot_ok, bible_state = _adopted_knowledge_evidence(
            chapter, accepted, snapshots.get((chapter.project_id, chapter.id)), mode,
        )
        if bible_state == 'closed':
            bible_sources[chapter.project_id][chapter.id] = {
                **knowledge_sources[chapter.project_id][chapter.chapter_number][1],
                'chapter_number': chapter.chapter_number,
            }
        knowledge = target["knowledge"]
        knowledge["adopted_chapters"] += 1
        knowledge["feedback_chapters"] += int(feedback_ok)
        knowledge["snapshot_chapters"] += int(snapshot_ok)
        knowledge["bible"][f"{bible_state}_chapters"] += 1
        if feedback_ok and snapshot_ok and bible_state == "closed":
            knowledge["closed_chapters"] += 1
            knowledge["closed_chapter_numbers"].append(int(chapter.chapter_number))
    for project_id, target in stats.items():
        # Revalidate the predecessor chain once over this same SQL readback.
        # A legacy/changed dependency cannot remain a green closed progress row.
        dependency_status = snapshot_evidence_for_sources(
            knowledge_sources[project_id], source_bodies=knowledge_source_bodies[project_id],
        )
        target["adopted_chapter_numbers"].sort()
        target["chapters"].sort(key=lambda item: item["number"])
        knowledge = target["knowledge"]
        pending_bible_numbers = set()
        if bible_sources[project_id]:
            from bestseller.services.story_bible_projection import adopted_bible_projection_statuses

            projections = await adopted_bible_projection_statuses(
                session, project_id=project_id, source_lineages=bible_sources[project_id],
            )
            pending_bible_numbers = {
                source['chapter_number'] for chapter_id, source in bible_sources[project_id].items()
                if projections[chapter_id] not in {'current', 'legacy_current'}
            }
            knowledge['bible']['closed_chapters'] -= len(pending_bible_numbers)
            knowledge['bible']['pending_chapters'] += len(pending_bible_numbers)
        knowledge["snapshot_chapters"] = sum(dependency_status.get(number, False)
            for number in target["adopted_chapter_numbers"])
        knowledge["closed_chapter_numbers"] = [number for number in knowledge["closed_chapter_numbers"]
            if dependency_status.get(number, False) and number not in pending_bible_numbers]
        knowledge["closed_chapters"] = len(knowledge["closed_chapter_numbers"])
        knowledge["closed_chapter_numbers"].sort()
        knowledge["pending_chapters"] = knowledge["adopted_chapters"] - knowledge["closed_chapters"]
        bible = knowledge["bible"]
        bible["status"] = ("error" if bible["error_chapters"] else "pending" if bible["pending_chapters"]
                           else "closed" if bible["closed_chapters"] and not bible["unknown_chapters"] else "unknown")
        if knowledge["adopted_chapters"]:
            closed = knowledge["pending_chapters"] == 0
            knowledge.update(status="closed" if closed else "pending", label="已闭合" if closed else "待闭合")
    return stats


def _adoption_projection_fields(stats: Mapping[str, object]) -> dict[str, object]:
    """Small, shared evidence surface for library and task projections."""
    return {key: stats.get(key) for key in (
        "adoption_evidence", "adopted_chapters", "promoted_chapters",
        "accepted_with_debt_chapters", "candidate_chapters",
        "candidate_word_count_total", "adopted_chapter_numbers",
        "knowledge",
    )}


async def _load_project_content_stats(
    session: Any,
    rows: list[Any],
    *,
    settings: AppSettings | None = None,
    include_filesystem: bool = False,
) -> dict[UUID, dict[str, object]]:
    if not rows:
        return {}

    project_ids = [row.id for row in rows]
    stats = await _load_adopted_chapter_progress(
        session, project_ids,
        project_languages={row.id: getattr(row, "language", "zh-CN") for row in rows},
    )
    for current in stats.values():
        current.update(chapters_on_disk=0, project_md_exists=False, export_word_count_total=0)

    if settings is not None and include_filesystem:
        output_base = Path(settings.output.base_dir)
        for row in rows:
            slug = str(row.slug or "")
            slug_dir = output_base / slug if slug else None
            if not slug_dir or not slug_dir.exists():
                continue
            chapter_files = sorted(slug_dir.glob("chapter-*.md"))
            words_on_disk = 0
            for cf in chapter_files:
                try:
                    words_on_disk += len(cf.read_text(encoding="utf-8"))
                except OSError:
                    continue
            current = stats[row.id]
            current["chapters_on_disk"] = len(chapter_files)
            current["export_word_count_total"] = words_on_disk
            current["project_md_exists"] = (slug_dir / "project.md").exists()

    return stats


def _workflow_activity_evidence(
    run: Any, *, latest_step_at: Any = None, now: datetime | None = None, owner_active: bool = False,
) -> dict[str, object]:
    """Read liveness separately from durable workflow status; never rewrite it."""
    from bestseller.services.planning_concurrency import PLANNING_STALE_AFTER_SECONDS

    def field(obj: Any, key: str, default: Any = None) -> Any:
        return obj.get(key, default) if isinstance(obj, Mapping) else getattr(obj, key, default)

    def timestamp(value: Any) -> datetime | None:
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
            if not isinstance(value, datetime):
                return None
            return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
        except (ValueError, TypeError, OverflowError):
            return None

    current = now or datetime.now(UTC)
    status = str(field(run, "status", "") or "").lower()
    if status not in {"pending", "queued", "running"}:
        return {"status": "inactive", "execution_active": False, "evidence": []}
    metadata = field(run, "metadata_json", {}) or field(run, "metadata", {}) or {}
    metadata = metadata if isinstance(metadata, Mapping) else {}
    candidates = [("workflow_updated", field(run, "updated_at")),
                  ("workflow_created", field(run, "created_at")),
                  ("workflow_step", latest_step_at),
                  ("planner_heartbeat", metadata.get("planner_last_heartbeat_at"))]
    for step in field(run, "steps", []) or []:
        candidates.append(("workflow_step", field(step, "created_at")))
    valid = [(kind, stamp) for kind, raw in candidates if (stamp := timestamp(raw)) is not None
             and (current - stamp).total_seconds() >= -300]
    fresh = sorted({kind for kind, stamp in valid
                    if (current - stamp).total_seconds() <= PLANNING_STALE_AFTER_SECONDS})
    if owner_active:
        fresh.append("worker_owner")
    return {"status": "active" if fresh else "stale" if valid else "unknown",
            "execution_active": bool(fresh), "evidence": fresh,
            "last_evidence_at": max((stamp for _, stamp in valid), default=None).isoformat() if valid else None,
            "stale_after_seconds": PLANNING_STALE_AFTER_SECONDS}


def _aggregate_workflow_activity(states: list[dict[str, object]]) -> dict[str, object]:
    active = any(s["execution_active"] for s in states)
    stale = sum(s["status"] == "stale" for s in states)
    unknown = sum(s["status"] == "unknown" for s in states)
    return {"status": "active" if active else "stale" if stale else "unknown" if unknown else "inactive",
            "execution_active": active, "stale_workflow_count": stale, "unknown_workflow_count": unknown,
            "evidence": sorted({str(e) for s in states for e in s.get("evidence", [])})}


def _project_activity_fields(activity: Mapping[str, object] | None, active: bool) -> dict[str, object]:
    value = dict(activity or _aggregate_workflow_activity([]))
    if active and not value.get("execution_active"):
        value.update(status="active", execution_active=True, evidence=["worker_owner"])
    elif not active and value.get("execution_active"):
        value.update(status="inactive", execution_active=False)
    return {"activity_status": value["status"], "execution_active": active, "workflow_activity": value,
            "current_workflow": value.get("current_workflow")}


async def _load_workflow_activity_by_project(
    session: Any,
    project_ids: list[UUID],
) -> dict[str, dict[str, object]]:
    if not project_ids:
        return {}

    from sqlalchemy import func, select

    from bestseller.infra.db.models import ProjectModel, WorkflowRunModel, WorkflowStepRunModel

    latest_step = select(func.max(WorkflowStepRunModel.created_at)).where(
        WorkflowStepRunModel.workflow_run_id == WorkflowRunModel.id,
    ).correlate(WorkflowRunModel).scalar_subquery()
    result = await session.execute(
        select(ProjectModel.slug, WorkflowRunModel.id, WorkflowRunModel.workflow_type,
            WorkflowRunModel.status, WorkflowRunModel.current_step,
            WorkflowRunModel.created_at, WorkflowRunModel.updated_at,
            WorkflowRunModel.metadata_json["planner_last_heartbeat_at"].as_string().label("planner_heartbeat_at"),
            latest_step.label("latest_step_at"))
        .join(WorkflowRunModel, WorkflowRunModel.project_id == ProjectModel.id)
        .where(
            ProjectModel.id.in_(project_ids),
        )
        .execution_options(autoflush=False)
    )
    grouped: dict[str, list[tuple[Any, dict[str, object]]]] = {}
    from types import SimpleNamespace
    for item in result.mappings():
        # Liveness needs only this one metadata scalar, not old prompt/result
        # documents or implicit ORM relationship loading on every UI poll.
        run = SimpleNamespace(**dict(item), metadata_json={
            "planner_last_heartbeat_at":item["planner_heartbeat_at"]})
        slug = item["slug"]
        state = _workflow_activity_evidence(run, latest_step_at=item["latest_step_at"])
        snapshot = {
            "workflow_run_id": str(getattr(run, "id", "") or ""),
            "workflow_type": getattr(run, "workflow_type", None),
            "status": getattr(run, "status", None),
            "current_step": getattr(run, "current_step", None),
            "created_at": getattr(run, "created_at", None).isoformat() if getattr(run, "created_at", None) else None,
            "updated_at": getattr(run, "updated_at", None).isoformat() if getattr(run, "updated_at", None) else None,
            "last_evidence_at": state.get("last_evidence_at"),
        }
        grouped.setdefault(str(slug), []).append((snapshot, state))
    payload = {}
    for slug, entries in grouped.items():
        # A currently evidenced run wins over old repair history. Persisted
        # status alone never establishes a live executor. No events are made up.
        snapshot, current_state = max(entries, key=lambda pair: (
            bool(pair[1]["execution_active"]),
            pair[0].get("last_evidence_at") or pair[0].get("updated_at") or pair[0].get("created_at") or "",
            pair[0].get("created_at") or "",
        ))
        aggregate = _aggregate_workflow_activity([state for _, state in entries])
        if not aggregate["execution_active"]:
            aggregate["status"] = current_state["status"]
        payload[slug] = {**aggregate, "current_workflow": snapshot}
    return payload


async def _load_active_workflow_project_slugs(session: Any, project_ids: list[UUID]) -> set[str]:
    activity = await _load_workflow_activity_by_project(session, project_ids)
    return {slug for slug, value in activity.items() if value["execution_active"]}


async def _load_active_workflow_slugs_by_slug(
    settings: AppSettings,
    slugs: set[str],
) -> set[str]:
    if not slugs:
        return set()

    from sqlalchemy import select

    from bestseller.infra.db.models import ProjectModel, WorkflowRunModel

    async with session_scope(settings) as session:
        result = await session.execute(
            select(ProjectModel.slug)
            .join(WorkflowRunModel, WorkflowRunModel.project_id == ProjectModel.id)
            .where(
                ProjectModel.slug.in_(slugs),
                WorkflowRunModel.status.in_(("pending", "queued", "running")),
            )
            .distinct()
        )
        return {str(slug) for slug in result.scalars() if slug}


async def _load_existing_project_slugs(
    settings: AppSettings,
    slugs: set[str],
) -> set[str]:
    """Return the subset of ``slugs`` that already have a ``ProjectModel`` row.

    Used by zombie auto-resume to distinguish a *conception-phase* zombie
    (no project row yet — its only state is the in-memory web payload) from a
    *writing-phase* zombie (project exists, can be resumed by worker self-heal
    keyed on slug). The two need different recovery paths.
    """
    if not slugs:
        return set()

    from sqlalchemy import select

    from bestseller.infra.db.models import ProjectModel

    async with session_scope(settings) as session:
        result = await session.execute(
            select(ProjectModel.slug).where(ProjectModel.slug.in_(slugs)).distinct()
        )
        return {str(slug) for slug in result.scalars() if slug}


async def _load_library_payload(settings: AppSettings) -> list[dict[str, object]]:
    """Return a richer per-project payload for the library page.

    Combines DB fields with filesystem chapter stats so the library can show
    "all in-progress + completed books" with click-through to the reader.
    """
    output_base = Path(settings.output.base_dir)
    async with session_scope(settings) as session:
        rows = await list_projects(session)
        adopted_progress = await _load_adopted_chapter_progress(
            session, [row.id for row in rows],
            project_languages={row.id: row.language for row in rows},
        )
        repair_counts = await _load_project_chapter_state_counts(
            session,
            [row.id for row in rows],
        )
        autonomous_repair_counts = await _load_project_autonomous_repair_task_counts(
            session,
            [row.id for row in rows],
        )
        project_activity = await _load_workflow_activity_by_project(
            session,
            [row.id for row in rows],
        )
        active_project_slugs = {slug for slug, value in project_activity.items() if value["execution_active"]}
        redis_url = getattr(getattr(settings, "redis", None), "url", None)
        if redis_url:
            active_project_slugs.update(_fetch_heal_owned_slugs(str(redis_url)))
        results: list[dict[str, object]] = []
        for row in rows:
            slug = row.slug
            is_short = is_fanqie_short_project(row)
            is_archived = _project_library_archived(row.metadata_json)
            delivery = await _read_project_delivery_status(session, row)
            is_closed = delivery["is_complete"] is True
            has_active_workflow = slug in active_project_slugs and not is_closed and not is_archived
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
            completed_chapters = int(adopted_progress[row.id]["adopted_chapters"])

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

            target_chapters = int(row.target_chapters or 0)
            display_title = row.title
            unit_kind = "single_story" if is_short else "chapter"
            unit_label = "全文" if is_short else "章节"
            current_export_filename = None
            current_export_modified_at = None
            if is_short:
                fanqie_stats = _build_fanqie_short_export_task_stats(
                    settings,
                    row,
                    include_chapters=False,
                )
                if fanqie_stats:
                    display_title = str(fanqie_stats.get("project_title") or display_title)
                    target_chapters = int(fanqie_stats.get("target_segments") or 1)
                    completed_chapters = int(fanqie_stats.get("completed_segments") or 0)
                    chapters_on_disk = completed_chapters
                    words_on_disk = int(fanqie_stats.get("word_count_total") or 0)
                    last_updated_iso = (
                        str(
                            fanqie_stats.get("current_export_modified_at") or last_updated_iso or ""
                        )
                        or None
                    )
                    current_export_filename = fanqie_stats.get("current_export_filename")
                    current_export_modified_at = fanqie_stats.get("current_export_modified_at")

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

            repair_status = _build_project_repair_status_payload(
                row,
                db_counters,
                autonomous_repair_counts.get(row.id, {}),
            )
            # Export files and lifecycle COMPLETE cannot manufacture adoption.
            completed_chapters = int(adopted_progress[row.id]["adopted_chapters"])
            book_state = _library_book_state(
                status=row.status,
                completed_units=completed_chapters,
                target_units=target_chapters,
                has_content=completed_chapters > 0 or words_on_disk > 0 or project_md_exists,
                archived=_project_library_archived(row.metadata_json),
                repair_status=repair_status,
                has_active_workflow=has_active_workflow,
                delivery_closure=delivery,
            )
            if (not has_active_workflow and project_activity.get(row.slug, {}).get("status") == "stale"
                and book_state not in {"closed_complete", "archived", "delivery_pending"}):
                book_state = "needs_attention"
            results.append(
                {
                    "id": str(row.id),
                    "slug": slug,
                    "title": display_title,
                    "genre": row.genre,
                    "primary_category": primary_category,
                    "llm_model_id": (row.metadata_json or {}).get("llm_model_id"),
                    "status": row.status,
                    "book_state": book_state,
                    "delivery_closure": delivery,
                    "book_state_label": _library_book_state_label(book_state),
                    "library_archived": is_archived,
                    "archived_at": (
                        (row.metadata_json or {}).get("library_archived_at")
                        if isinstance(row.metadata_json, dict)
                        else None
                    ),
                    "task_visibility": "library_archive" if is_archived else "library",
                    "can_trigger_revision": bool(slug),
                    "project_type": row.project_type,
                    "content_mode": (
                        (row.metadata_json or {}).get("content_mode")
                        if isinstance(row.metadata_json, dict)
                        else None
                    ),
                    "unit_kind": unit_kind,
                    "unit_label": unit_label,
                    "target_chapters": target_chapters,
                    "target_word_count": row.target_word_count,
                    "completed_chapters": completed_chapters,
                    **_adoption_projection_fields(adopted_progress[row.id]),
                    "adopted_word_count_total": adopted_progress[row.id]["word_count_total"],
                    "word_count_total": adopted_progress[row.id]["word_count_total"],
                    "chapters_on_disk": chapters_on_disk,
                    "db_chapter_total": db_total,
                    "words_on_disk": words_on_disk,
                    "last_updated": last_updated_iso,
                    "current_export_filename": current_export_filename,
                    "current_export_modified_at": current_export_modified_at,
                    "has_project_md": project_md_exists,
                    "has_active_workflow": has_active_workflow,
                    **_project_activity_fields(project_activity.get(row.slug), has_active_workflow),
                    "tags": tags,
                    "tagline": tagline,
                    "repair_status": repair_status,
                }
            )
        # Sort: active / attention states first, then closed books by freshness.
        results.sort(
            key=lambda r: (
                0
                if str(r.get("book_state") or "").lower()
                in {"in_progress", "needs_attention", "draft"}
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


async def _set_project_library_archived(
    settings: AppSettings,
    slug: str,
    *,
    archived: bool,
) -> dict[str, object] | None:
    from sqlalchemy import select, update

    from bestseller.infra.db.models import ProjectModel, WorkflowRunModel

    slug = str(slug or "").strip()
    if not slug:
        return None
    async with session_scope(settings) as session:
        project = (
            await session.execute(select(ProjectModel).where(ProjectModel.slug == slug))
        ).scalar_one_or_none()
        if project is None:
            return None
        meta = dict(project.metadata_json or {})
        if archived:
            previous_status = str(getattr(project, "status", "") or "").strip()
            if previous_status and previous_status != ProjectStatus.ARCHIVED.value:
                meta.setdefault("library_archived_previous_status", previous_status)
            meta["library_archived"] = True
            meta["library_archived_at"] = _utc_now()
            project.status = ProjectStatus.ARCHIVED.value
        else:
            meta.pop("library_archived", None)
            meta.pop("library_archived_at", None)
            previous_status = str(meta.pop("library_archived_previous_status", "") or "").strip()
            if getattr(project, "status", None) == ProjectStatus.ARCHIVED.value:
                project.status = previous_status or ProjectStatus.PLANNING.value
        project.metadata_json = meta
        cancelled_workflows = 0
        if archived:
            cancel_result = await session.execute(
                update(WorkflowRunModel)
                .where(
                    WorkflowRunModel.project_id == project.id,
                    WorkflowRunModel.status.in_(
                        ("pending", "queued", "running", "machine_blocked", "awaiting_authority", "awaiting_reference")
                    ),
                )
                .values(
                    status="cancelled",
                    current_step="skipped_archived",
                    error_message="skipped because project is library_archived",
                    updated_at=datetime.now(UTC),
                )
                .execution_options(synchronize_session=False)
            )
            cancelled_workflows = int(cancel_result.rowcount or 0)
        return {
            "ok": True,
            "project_slug": slug,
            "library_archived": archived,
            "archived_at": meta.get("library_archived_at"),
            "cancelled_workflows": cancelled_workflows,
        }


async def _set_project_completed(
    settings: AppSettings,
    slug: str,
) -> dict[str, object] | None:
    from sqlalchemy import select, update

    from bestseller.infra.db.models import ProjectModel, WorkflowRunModel

    slug = str(slug or "").strip()
    if not slug:
        return None
    async with session_scope(settings) as session:
        project = (
            await session.execute(select(ProjectModel).where(ProjectModel.slug == slug))
        ).scalar_one_or_none()
        if project is None:
            return None
        meta = dict(project.metadata_json or {})
        meta["manually_marked_completed"] = True
        meta["completed_at"] = _utc_now()
        meta.pop("library_archived", None)
        meta.pop("library_archived_at", None)
        meta.pop("library_archived_previous_status", None)
        project.metadata_json = meta
        project.status = ProjectStatus.COMPLETED.value
        cancel_result = await session.execute(
            update(WorkflowRunModel)
            .where(
                WorkflowRunModel.project_id == project.id,
                WorkflowRunModel.status.in_(("pending", "queued", "running", "machine_blocked")),
            )
            .values(
                status="cancelled",
                current_step="skipped_project_completed",
                error_message="skipped because project was manually marked completed",
                updated_at=datetime.now(UTC),
            )
            .execution_options(synchronize_session=False)
        )
        redis_url = str(getattr(getattr(settings, "redis", None), "url", "") or "")
        cancelled_jobs = 0
        for job_id in (
            f"autowrite:heal:{slug}",
            f"project-pipeline:heal:{slug}",
            f"repair:heal:{slug}",
        ):
            try:
                if await _abort_worker_heal_job_async(redis_url, job_id):
                    cancelled_jobs += 1
            except Exception:
                logger.exception("project complete: failed to abort heal job %s", job_id)

        return {
            "ok": True,
            "project_slug": slug,
            "status": ProjectStatus.COMPLETED.value,
            "book_state": "closed_complete",
            "cancelled_workflows": int(cancel_result.rowcount or 0),
            "cancelled_jobs": cancelled_jobs,
        }


async def _cancel_project_task_async(settings: AppSettings, task_id: str) -> str:
    """Cancel active work for a synthetic ``project:<slug>`` task card."""
    if not task_id.startswith("project:"):
        return "not_found"
    slug = task_id.removeprefix("project:").strip()
    if not slug:
        return "not_found"

    from sqlalchemy import select, update

    from bestseller.infra.db.models import ProjectModel, WorkflowRunModel
    from bestseller.services.production_control import request_pause

    async with session_scope(settings) as session:
        project = (
            await session.execute(select(ProjectModel).where(ProjectModel.slug == slug))
        ).scalar_one_or_none()
        if project is None:
            return "not_found"
        # Record the operator's intent where the pipeline cannot overwrite it.
        # ``project.status`` and ``projects.metadata`` are both rewritten by the
        # running pipeline from its own in-memory copy, which is why a stopped
        # book used to be requeued by self-heal a minute later.
        await request_pause(
            session,
            project.id,
            actor="web_dashboard",
            reason="cancelled_from_project_task",
        )
        result = await session.execute(
            update(WorkflowRunModel)
            .where(
                WorkflowRunModel.project_id == project.id,
                WorkflowRunModel.status.in_(("pending", "queued", "running", "machine_blocked")),
            )
            .values(
                status="cancelled",
                current_step="cancelled_from_project_task",
                error_message="Project task cancelled from web dashboard.",
                updated_at=datetime.now(UTC),
            )
            .execution_options(synchronize_session=False)
        )
        cancelled_rows = int(result.rowcount or 0)
        status_changed = project.status not in (
            ProjectStatus.COMPLETED.value,
            ProjectStatus.ARCHIVED.value,
            ProjectStatus.PAUSED.value,
        )
        if status_changed:
            project.status = ProjectStatus.PAUSED.value

    redis_url = str(getattr(getattr(settings, "redis", None), "url", "") or "")
    job_cancelled = False
    for job_id in (
        f"autowrite:heal:{slug}",
        f"project-pipeline:heal:{slug}",
        f"repair:heal:{slug}",
    ):
        try:
            job_cancelled = await _abort_worker_heal_job_async(redis_url, job_id) or job_cancelled
        except Exception:
            logger.exception("project task cancel: failed to abort heal job %s", job_id)
    if cancelled_rows or job_cancelled or status_changed:
        return "cancel_requested"
    return "not_running"


async def _close_creation_workflow_after_task_exit_async(
    settings: AppSettings,
    *,
    attempt_id: str,
    status: str,
    error_message: str,
) -> int:
    """Close a project-less conception row after its web task exits.

    The conception row is created before a project exists, so the synthetic
    ``project:<slug>`` cancellation path cannot find it. This task-scoped
    fallback uses the persisted attempt id and only updates active
    ``conception_initial`` rows; a later workflow or a different book cannot be
    cancelled accidentally.
    """
    attempt = str(attempt_id or "").strip()
    if not attempt or status not in {"failed", "cancelled"}:
        return 0
    from sqlalchemy import select

    async with session_scope(settings) as session:
        result = await session.execute(
            select(WorkflowRunModel).where(
                WorkflowRunModel.workflow_type == "conception_initial",
                WorkflowRunModel.status.in_(("pending", "queued", "running")),
            )
        )
        rows = list(result.scalars().all())
        closed = 0
        for row in rows:
            metadata = row.metadata_json if isinstance(row.metadata_json, dict) else {}
            if str(metadata.get("attempt_id") or "") != attempt:
                continue
            row.status = status
            row.current_step = "cancelled" if status == "cancelled" else "failed"
            row.error_message = str(error_message or "")[:4000] or None
            row.metadata_json = {
                **metadata,
                "terminal_status_source": "web_task_exit",
            }
            closed += 1
        return closed


def _close_creation_workflow_after_task_exit(
    settings: AppSettings,
    *,
    task_id: str,
    payload: Mapping[str, object],
    status: str,
    error_message: str,
) -> int:
    """Best-effort synchronous bridge for the web worker's terminal handlers."""
    if not bool(payload.get("_run_conception")):
        return 0
    attempt_id = str(payload.get("_conception_attempt_id") or f"{task_id}:initial")
    try:
        return asyncio.run(
            _close_creation_workflow_after_task_exit_async(
                settings,
                attempt_id=attempt_id,
                status=status,
                error_message=error_message,
            )
        )
    except Exception:
        logger.warning(
            "failed to close conception workflow after task %s exited (%s)",
            task_id,
            status,
            exc_info=True,
        )
        return 0


async def _has_live_pipeline_run_async(settings: AppSettings, slug: str) -> bool:
    """DB 里这本书还有正在跑的流水线吗？

    2026-08-22 真机：书停在 `machine_repair_required`（任务卡片显示
    incomplete），我点了 resume，结果**两个 project_pipeline + 两个
    chapter_pipeline 同时 running**，几分钟后

        asyncpg.exceptions.DeadlockDetectedError: deadlock detected

    整本书失败在第 22 章。根因是同一事实住两地：web 的任务卡片说
    incomplete，`workflow_runs` 表说 running，而 resume 的忙碌判据只看
    前者。恢复前必须按**事实源**对账。

    ⚠️ 查不到就返回 False（放行）——探针本身不该变成新的卡死来源。
    """

    slug = (slug or "").strip()
    if not slug:
        return False

    from sqlalchemy import select

    from bestseller.infra.db.models import ProjectModel, WorkflowRunModel

    try:
        async with session_scope(settings) as session:
            project = (
                await session.execute(select(ProjectModel).where(ProjectModel.slug == slug))
            ).scalar_one_or_none()
            if project is None:
                return False
            row = (
                await session.execute(
                    select(WorkflowRunModel.id)
                    .where(
                        WorkflowRunModel.project_id == project.id,
                        WorkflowRunModel.status == "running",
                        WorkflowRunModel.workflow_type.in_(
                            ("project_pipeline", "chapter_pipeline", "scene_pipeline")
                        ),
                    )
                    .limit(1)
                )
            ).scalar_one_or_none()
            return row is not None
    except Exception:
        logger.exception("live-pipeline probe failed for %s", slug)
        return False


async def _prepare_failed_fanqie_beat_resume_async(settings: AppSettings, slug: str) -> dict:
    """Reconcile a proven failed paid Beat before a new native owner is acquired."""
    from sqlalchemy import and_, or_, select
    from bestseller.infra.db.models import ChapterModel, ProjectModel, WorkflowRunModel
    from bestseller.services.fanqie_beat_recovery import prepare_failed_beat_projection_recovery

    async with session_scope(settings) as session:
        project = await session.scalar(select(ProjectModel).where(ProjectModel.slug == slug))
        if project is None or project.project_type != "fanqie_short":
            return {"status": "unchanged", "provider_calls": 0}
        chapter = await session.scalar(
            select(ChapterModel.id).where(ChapterModel.project_id == project.id).limit(1)
        )
        producer = await session.scalar(select(WorkflowRunModel.id).where(
            WorkflowRunModel.project_id == project.id,
            WorkflowRunModel.workflow_type == "generate_fanqie_beat_sheet",
            or_(WorkflowRunModel.status == "running", and_(
                WorkflowRunModel.status == "failed",
                WorkflowRunModel.metadata_json["beat_projection_failure_receipt"]["schema"]
                .as_string().is_not(None),
            )),
        ).limit(1))
        if chapter is not None or producer is None:
            return {"status": "unchanged", "provider_calls": 0}
        return await prepare_failed_beat_projection_recovery(
            session, settings, project_id=project.id, requested_by="web_dashboard",
        )


async def _clear_production_halt_async(
    settings: AppSettings,
    slug: str,
    *,
    strict: bool = False,
) -> bool:
    """Apply an explicit dashboard RUN and synchronize its paused projection.

    Starting a book from the dashboard *is* an instruction to run, so it must
    lift a previous stop. Without this a stopped book would be permanently
    unstartable — trading the zombie-resume bug for a stuck-forever one.
    A RUN row can also coexist with a stale paused status; the trusted helper
    repairs that projection without changing automatic recovery guards.
    """

    slug = (slug or "").strip()
    if not slug:
        return False

    from bestseller.services.budget import ExecutionBudgetError
    from bestseller.services.web_project_resume import (
        WebProjectResumeBlockedError,
        request_explicit_web_resume,
    )

    try:
        async with session_scope(settings) as session:
            return await request_explicit_web_resume(
                session,
                slug=slug,
                actor="web_dashboard",
                reason="operator started writing",
            )
    except (WebProjectResumeBlockedError, ExecutionBudgetError):
        raise
    except Exception:
        logger.exception("failed to clear production halt for %s", slug)
        if strict:
            raise
        return False


def _cancel_project_task(settings: AppSettings, task_id: str) -> str:
    if not task_id.startswith("project:"):
        return "not_found"
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        logger.warning("task cancel: cannot cancel project task from an active event loop")
        return "not_found"
    try:
        return asyncio.run(_cancel_project_task_async(settings, task_id))
    except Exception:
        logger.exception("task cancel: failed to cancel project task %s", task_id)
        return "not_found"


def _project_row_to_dashboard_task(row: dict[str, object]) -> dict[str, object]:
    slug = str(row.get("slug") or "").strip()
    title = str(row.get("title") or slug or "未命名").strip()
    book_state = str(row.get("book_state") or "").lower()
    if book_state == "closed_complete" and not (
        (row.get("delivery_closure") or {}).get("status") == "verified"
        and (row.get("delivery_closure") or {}).get("is_complete") is True
    ):
        book_state = ("delivery_pending" if int(row.get("target_chapters") or 0) > 0
                      and int(row.get("completed_chapters") or 0) >= int(row.get("target_chapters") or 0)
                      else "needs_attention")
    status = (
        "completed"
        if (book_state == "closed_complete" and (row.get("delivery_closure") or {}).get("status") == "verified"
            and (row.get("delivery_closure") or {}).get("is_complete") is True)
        else ("archived" if book_state == "archived" else "project")
    )
    historical_delivery_pending = (
        (row.get("delivery_closure") or {}).get("status") == "not_checked"
        and str(row.get("status") or "").lower() in _BOOK_CLOSED_STATUSES
        and row.get("adoption_evidence") == "verified"
        and int(row.get("target_chapters") or 0) > 0
        and int(row.get("completed_chapters") or 0) >= int(row.get("target_chapters") or 0)
        and int(row.get("candidate_chapters") or 0) == 0
    )
    stale_activity = row.get("activity_status") == "stale" and not historical_delivery_pending
    verified_activity = (row.get("has_active_workflow") is True
                         and status == "project"
                         and row.get("activity_status") not in {"stale", "unknown", "inactive"})
    if status == "project" and stale_activity:
        status = "incomplete"
    elif status == "project" and verified_activity:
        status = "running"
    elif (status == "project" and row.get("adoption_evidence") == "verified"
          and int(row.get("candidate_chapters") or 0) > 0):
        status = "incomplete"
    workflow = row.get("current_workflow") or (row.get("workflow_activity") or {}).get("current_workflow") or {}
    updated_at = (workflow.get("last_evidence_at") or workflow.get("updated_at")
                  or row.get("updated_at") or row.get("last_updated") or row.get("created_at") or "")
    target_units = int(row.get("target_chapters") or 0)
    completed_units = int(row.get("completed_chapters") or 0)
    word_count = int(row.get("adopted_word_count_total", row.get("word_count_total")) or 0) if (
        row.get("adoption_evidence") == "verified"
    ) else int(row.get("words_on_disk") or 0)
    unit_kind = str(row.get("unit_kind") or "chapter")
    unit_label = str(row.get("unit_label") or ("全文" if unit_kind == "single_story" else "章节"))
    return {
        "task_id": f"project:{slug}",
        "task_type": "project",
        "has_active_workflow": verified_activity,
        "execution_active": verified_activity,
        "activity_status": row.get("activity_status"),
        "workflow_activity": row.get("workflow_activity"),
        "current_workflow": workflow or None,
        "workflow_run_id": workflow.get("workflow_run_id"),
        "projection_authority": "postgresql",
        "evidence_status": "stale_workflow" if stale_activity else "workflow_record" if row.get("has_active_workflow") is True else "no_workflow",
        "status": status,
        "created_at": row.get("created_at") or updated_at,
        "updated_at": updated_at,
        "project_slug": slug,
        "title": title,
        "project_title": title,
        "project_status": row.get("status") or "",
        "book_state": book_state,
        "delivery_closure": row.get("delivery_closure"),
        "book_state_label": row.get("book_state_label") or "",
        "project_type": row.get("project_type") or "",
        "content_mode": row.get("content_mode") or "",
        "current_stage": "project_closed" if status == "completed" else "delivery_pending" if book_state == "delivery_pending" else "workflow_activity_stale" if stale_activity else "project_candidate_review" if status == "incomplete" and int(row.get("candidate_chapters") or 0) > 0 else "project_registered",
        "progress_events": [],  # a library projection cannot invent execution events
        "result": {
            "project_slug": slug,
            "project_type": row.get("project_type") or "",
            "content_mode": row.get("content_mode") or "",
            "chapters_written": completed_units,
            "target_chapters": target_units,
            "target_words": row.get("target_word_count") or 0,
        },
        "payload": {
            "slug": slug,
            "title": title,
            "genre": row.get("genre") or "",
            "project_type": row.get("project_type") or "",
            "content_mode": row.get("content_mode") or "",
            "target_chapters": target_units,
            "target_words": row.get("target_word_count") or 0,
        },
        "chapter_word_stats": {
            "source": "library_project",
            **_adoption_projection_fields(row),
            "project_slug": slug,
            "project_title": title,
            "project_type": row.get("project_type") or "",
            "content_mode": row.get("content_mode") or "",
            "unit_kind": unit_kind,
            "unit_label": unit_label,
            "target_chapters": target_units,
            "target_segments": target_units if unit_kind == "single_story" else 0,
            "completed_chapters": completed_units,
            "completed_segments": completed_units if unit_kind == "single_story" else 0,
            "target_word_count": int(row.get("target_word_count") or 0),
            "word_count_total": word_count,
            "current_export_filename": row.get("current_export_filename"),
            "current_export_modified_at": row.get("current_export_modified_at"),
        },
        "synthetic_project": True,
        "library_archived": bool(row.get("library_archived")),
    } | ({"current_stage": workflow.get("current_step") or workflow.get("workflow_type") or "project_pipeline_started"}
         if status == "running" and workflow else {})


def _reconcile_dashboard_tasks_with_projects(
    tasks: list[dict[str, object]], projects: list[dict[str, object]],
) -> list[dict[str, object]]:
    """Read-through migration: PG owns the card; cached history stays intact.

    Never modifies task-manager records, cache files, workflows or draft state.
    Keep superseded display evidence separately; never replay it as live events.
    """
    result = list(tasks)
    for row in projects:
        slug = str(row.get("slug") or "").strip()
        delivered = ((row.get("delivery_closure") or {}).get("status") == "verified"
                     and (row.get("delivery_closure") or {}).get("is_complete") is True)
        authoritative = (delivered or row.get("current_workflow") or (row.get("workflow_activity") or {}).get("current_workflow")
                         or row.get("has_active_workflow") is True
                         or (row.get("adoption_evidence") == "verified" and int(row.get("candidate_chapters") or 0) > 0))
        if not slug or not authoritative:
            continue
        previous = [task for task in result if _task_project_slug(task) == slug]
        # A completed creation workflow proves registration, not that a later
        # planning call succeeded. Retain the newer real failure until a live
        # PG workflow or a completed book supersedes that evidence.
        inactive = row.get("has_active_workflow") is not True
        closed = delivered or str(row.get("book_state") or "") == "archived"
        pg_time = _timestamp_seconds(str((row.get("current_workflow") or {}).get("updated_at") or row.get("updated_at") or ""))
        failures = [task for task in previous if task.get("status") in {"failed", "cancelled"}
                    and not task.get("synthetic_project") and pg_time is not None
                    and (_timestamp_seconds(task.get("updated_at")) or 0) >= pg_time]
        owners = [task for task in previous if task.get("execution_owner_verified") is True
                  and task.get("status") in {"running", "queued"}]
        if inactive and not closed and (failures or owners):
            chosen = max(owners or failures, key=lambda task:str(task.get("updated_at") or ""))
            enriched = {**_project_row_to_dashboard_task(row), **chosen,
                "synthetic_project": False, "projection_authority": "task_execution",
                "project_status":row.get("status"), "execution_active":bool(owners),
                "activity_status":"active" if owners else "inactive"}
            result = [task for task in result if _task_project_slug(task) != slug]
            result.append(enriched)
            continue
        history = []
        for task in previous:
            history.extend(task.get("task_history") or [])
            if task.get("synthetic_project"):
                continue
            history.append({key: task.get(key) for key in (
                "task_id", "task_type", "status", "current_stage", "created_at", "updated_at",
                "progress_events", "error",
            )})
        unique = {json.dumps(item, sort_keys=True, default=str): item for item in history}
        card = _project_row_to_dashboard_task(row)
        card["task_history"] = list(unique.values())
        card["projection_migration"] = {"schema": "pg-task-projection.v1", "cache_preserved": True,
                                         "superseded_task_ids": [t.get("task_id") for t in card["task_history"]]}
        result = [task for task in result if _task_project_slug(task) != slug]
        result.append(card)
    return result


async def _load_current_dashboard_tasks(
    settings: AppSettings, tasks: list[dict[str, object]], *, slug: str | None = None,
    delivery_statuses: dict[str, dict[str, object]] | None = None,
    progress_readback: bool = False,
    same_request_stats: dict[str, dict[str, object]] | None = None,
) -> list[dict[str, object]]:
    try:
        projects = await _load_projects_payload(
            settings, light=True, **({"project_slug": slug} if slug is not None else {}),
            **({"progress_readback": True} if progress_readback else {}),
        )
    except Exception:
        logger.warning("Current project task projection unavailable", exc_info=True)
        # Cache is retained as history, but an unavailable readback cannot
        # certify that a historical RUNNING record is executing now.
        return [{**task, "activity_status": "unknown", "execution_active": False,
                 "delivery_closure": {"status": "unverified", "is_complete": False},
                 "evidence_status": "project_readback_unavailable"} if _task_project_slug(task) else task
                for task in tasks]
    if slug is not None:
        projects = [row for row in projects if str(row.get("slug") or "") == slug]
    if progress_readback and same_request_stats is not None:
        for row in projects:
            stats = deepcopy(_project_row_to_dashboard_task(row)["chapter_word_stats"])
            is_short = row.get("project_type") == "fanqie_short"
            stats.update(source="db", chapters=[],
                         unit_kind="segment" if is_short else "chapter",
                         unit_label="段" if is_short else "章")
            for key in ("current_export_filename", "current_export_modified_at", "export_word_count_total"):
                if row.get(key) is not None:
                    stats[key] = row[key]
            same_request_stats[str(row["slug"])] = stats
    delivery_by_slug = {str(row.get("slug")): row.get("delivery_closure") for row in projects}
    if delivery_statuses is not None:
        delivery_statuses.update({key: value for key, value in delivery_by_slug.items() if value})
    tasks = [{**task, "delivery_closure": delivery_by_slug.get(_task_project_slug(task))
              or {"status": "unverified", "is_complete": False}} for task in tasks]
    return _reconcile_dashboard_tasks_with_projects(tasks, projects)


async def _load_project_dashboard_task_payload(
    settings: AppSettings,
    slug: str,
) -> dict[str, object] | None:
    slug = str(slug or "").strip()
    if not slug:
        return None
    rows = await _load_projects_payload(settings, light=True, project_slug=slug)
    for row in rows:
        if str(row.get("slug") or "") == slug:
            return _project_row_to_dashboard_task(row)
    return None


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
    from sqlalchemy import select

    from bestseller.infra.db.models import ChapterModel, RewriteTaskModel
    from bestseller.services.autonomous_book_repair import AUTONOMOUS_REPAIR_TRIGGER

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


def _coerce_int_list(value: object) -> list[int]:
    if not isinstance(value, list | tuple | set):
        return []
    numbers: set[int] = set()
    for item in value:
        try:
            number = int(item)
        except (TypeError, ValueError):
            continue
        if number > 0:
            numbers.add(number)
    return sorted(numbers)


def _workflow_dt(value: object) -> datetime | None:
    return value if isinstance(value, datetime) else None


def _workflow_dt_text(value: object, fallback: str | None = None) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return fallback or _utc_now()


def _repair_workflow_status_stage(status: str, current_step: object) -> str:
    normalized = status.strip().lower()
    step = str(current_step or "").strip()
    if normalized in {"pending", "queued", "running"}:
        return step or "project_repair_running"
    if normalized == "cancelled":
        return step or "project_repair_cancelled"
    if normalized == "failed":
        return step or "project_repair_failed"
    if normalized == "machine_blocked":
        return step or "project_repair_requires_attention"
    if normalized == "completed":
        return step or "project_repair_completed"
    return step or f"project_repair_{normalized or 'latest'}"


def _project_repair_workflow_snapshot(
    run: WorkflowRunModel,
    steps: list[WorkflowStepRunModel],
) -> dict[str, object]:
    metadata = run.metadata_json if isinstance(run.metadata_json, dict) else {}
    created_text = _workflow_dt_text(getattr(run, "created_at", None))
    updated_text = _workflow_dt_text(getattr(run, "updated_at", None), created_text)
    status = str(getattr(run, "status", "") or "")
    current_step = str(getattr(run, "current_step", "") or "")

    target_chapter_numbers = _coerce_int_list(metadata.get("target_chapter_numbers"))
    repair_gate_chapter_numbers: list[int] = []
    processed_chapter_numbers: list[int] = []
    passed_chapter_numbers: list[int] = []
    review_chapter_numbers: list[int] = []
    progress_events: list[dict[str, object]] = []

    for step in sorted(steps, key=lambda item: int(getattr(item, "step_order", 0) or 0)):
        output = step.output_ref if isinstance(step.output_ref, dict) else {}
        step_text = _workflow_dt_text(getattr(step, "created_at", None), updated_text)
        step_name = str(getattr(step, "step_name", "") or "")
        if step_name == "collect_pending_rewrite_tasks":
            collected_targets = _coerce_int_list(output.get("target_chapter_numbers"))
            collected_gate = _coerce_int_list(output.get("repair_gate_chapter_numbers"))
            if collected_targets:
                target_chapter_numbers = collected_targets
            if collected_gate:
                repair_gate_chapter_numbers = collected_gate
            progress_events.append(
                {
                    "timestamp": step_text,
                    "stage": "project_repair_targets_collected",
                    "payload": {
                        "project_slug": metadata.get("project_slug"),
                        "target_chapter_numbers": target_chapter_numbers,
                        "repair_gate_chapter_numbers": repair_gate_chapter_numbers,
                        "scope_total": len(target_chapter_numbers),
                    },
                }
            )
            continue

        chapter_number = None
        if step_name.startswith("repair_chapter_"):
            chapter_number = _safe_int(output.get("chapter_number"))
            if chapter_number <= 0:
                match = re.search(r"(\d+)$", step_name)
                chapter_number = int(match.group(1)) if match else 0
        if not chapter_number:
            continue

        if chapter_number not in processed_chapter_numbers:
            processed_chapter_numbers.append(chapter_number)
        production_state = str(output.get("production_state") or "").lower()
        requires_review = bool(output.get("requires_human_review"))
        if production_state == "ok" and not requires_review:
            passed_chapter_numbers.append(chapter_number)
        else:
            review_chapter_numbers.append(chapter_number)
        started_payload = {
            "project_slug": metadata.get("project_slug"),
            "chapter_number": chapter_number,
        }
        completed_payload = {
            **started_payload,
            "workflow_run_id": output.get("chapter_workflow_run_id"),
            "requires_human_review": requires_review,
            "chapter_status": output.get("chapter_status"),
            "production_state": output.get("production_state"),
        }
        progress_events.append(
            {
                "timestamp": step_text,
                "stage": "project_repair_chapter_started",
                "payload": started_payload,
            }
        )
        progress_events.append(
            {
                "timestamp": step_text,
                "stage": "project_repair_chapter_completed",
                "payload": completed_payload,
            }
        )

    current_chapter_number = 0
    match = re.search(r"repair_chapter_(\d+)", current_step)
    if match:
        current_chapter_number = int(match.group(1))
        if (
            status in {"pending", "queued", "running"}
            and current_chapter_number not in processed_chapter_numbers
        ):
            progress_events.append(
                {
                    "timestamp": updated_text,
                    "stage": "project_repair_chapter_started",
                    "payload": {
                        "project_slug": metadata.get("project_slug"),
                        "chapter_number": current_chapter_number,
                    },
                }
            )

    progress_events.append(
        {
            "timestamp": updated_text,
            "stage": _repair_workflow_status_stage(status, current_step),
            "payload": {
                "project_slug": metadata.get("project_slug"),
                "workflow_run_id": str(getattr(run, "id", "")),
                "workflow_status": status,
                "current_step": current_step,
                "error": getattr(run, "error_message", None),
            },
        }
    )

    return {
        "workflow_run_id": str(getattr(run, "id", "")),
        "status": status,
        "workflow_activity": _workflow_activity_evidence(
            run, latest_step_at=max((step.created_at for step in steps if getattr(step, "created_at", None)), default=None),
        ),
        "current_step": current_step,
        "error_message": getattr(run, "error_message", None),
        "requested_by": getattr(run, "requested_by", None),
        "created_at": created_text,
        "updated_at": updated_text,
        "target_chapter_numbers": target_chapter_numbers,
        "repair_gate_chapter_numbers": repair_gate_chapter_numbers,
        "processed_chapter_numbers": sorted(set(processed_chapter_numbers)),
        "passed_chapter_numbers": sorted(set(passed_chapter_numbers)),
        "review_chapter_numbers": sorted(set(review_chapter_numbers)),
        "current_chapter_number": current_chapter_number or None,
        "focus_cancelled": bool(metadata.get("focus_cancelled")),
        "focus_primary_slug": metadata.get("focus_primary_slug"),
        "progress_events": progress_events,
    }


async def _load_latest_project_repair_workflow_snapshots(
    session: Any,
    project_ids: list[UUID],
) -> dict[UUID, dict[str, object]]:
    if not project_ids:
        return {}
    from sqlalchemy import select

    rows = await session.scalars(
        select(WorkflowRunModel)
        .where(
            WorkflowRunModel.project_id.in_(project_ids),
            WorkflowRunModel.workflow_type == "project_repair",
        )
        .order_by(
            WorkflowRunModel.project_id.asc(),
            WorkflowRunModel.created_at.desc(),
            WorkflowRunModel.updated_at.desc(),
        )
    )
    latest_runs: dict[UUID, WorkflowRunModel] = {}
    for run in rows:
        if run.project_id is not None and run.project_id not in latest_runs:
            latest_runs[run.project_id] = run
    if not latest_runs:
        return {}

    step_rows = await session.scalars(
        select(WorkflowStepRunModel)
        .where(WorkflowStepRunModel.workflow_run_id.in_([run.id for run in latest_runs.values()]))
        .order_by(WorkflowStepRunModel.workflow_run_id.asc(), WorkflowStepRunModel.step_order.asc())
    )
    steps_by_run: dict[UUID, list[WorkflowStepRunModel]] = {}
    for step in step_rows:
        steps_by_run.setdefault(step.workflow_run_id, []).append(step)

    return {
        project_id: _project_repair_workflow_snapshot(run, steps_by_run.get(run.id, []))
        for project_id, run in latest_runs.items()
    }


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
    preserved = str(metadata_dict.get("preserved_current_quality_gate_outcome") or "").lower()
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
    latest_workflow = repair_status.get("latest_repair_workflow")
    latest_workflow = latest_workflow if isinstance(latest_workflow, dict) else {}
    if active_total <= 0 and phase in {"normal", "archived"}:
        return None

    slug = str(getattr(project, "slug", "") or "")
    if not slug:
        return None
    project_status = str(getattr(project, "status", "") or "").strip().lower()
    if project_status in _BOOK_CLOSED_STATUSES or phase in {"completed", "closed_complete"}:
        return None
    title = str(getattr(project, "title", "") or slug)
    latest_status = str(latest_workflow.get("status") or "").strip().lower()
    latest_stage = _repair_workflow_status_stage(
        latest_status,
        latest_workflow.get("current_step"),
    )
    latest_error = str(latest_workflow.get("error_message") or "").strip()
    latest_events = latest_workflow.get("progress_events")
    workflow_events = latest_events if isinstance(latest_events, list) else []
    latest_activity = latest_workflow.get("workflow_activity") or _workflow_activity_evidence(latest_workflow)
    if latest_status in {"pending", "queued", "running"} and not latest_activity["execution_active"]:
        status = "incomplete"
        stage = "workflow_activity_stale" if latest_activity["status"] == "stale" else "workflow_activity_unknown"
        error = "历史运行状态缺少当前执行证据，等待核验或恢复。"
    elif latest_status in {"pending", "queued"}:
        status = "queued"
        stage = latest_stage
        error = None
    elif latest_status == "running":
        status = "running"
        stage = latest_stage
        error = None
    elif latest_status == "failed" and phase == "planning_gate":
        status = "queued"
        stage = "planning_gate_auto_retry_pending"
        error = None
    elif latest_status == "failed":
        status = "failed"
        stage = latest_stage
        error = latest_error or "project repair workflow failed"
    elif latest_status == "cancelled":
        status = "incomplete"
        stage = latest_stage
        error = latest_error or "project repair workflow was cancelled"
    elif queued > 0:
        status = "queued"
        stage = "legacy_quality_closure_repair_queued"
        error = None
    elif pending > 0:
        status = "incomplete"
        stage = "legacy_quality_closure_repair_pending"
        error = None
    elif failed > 0:
        status = "incomplete"
        stage = "legacy_quality_closure_repair_retry_pending"
        error = None
    else:
        status = "incomplete"
        stage = f"legacy_quality_closure_{phase or 'repair'}"
        error = str(repair_status.get("detail") or "") or None

    updated_at = getattr(project, "updated_at", None)
    created_at = getattr(project, "created_at", None) or updated_at
    updated_text = str(latest_workflow.get("updated_at") or "") or (
        updated_at.isoformat() if isinstance(updated_at, datetime) else _utc_now()
    )
    created_text = str(latest_workflow.get("created_at") or "") or (
        created_at.isoformat() if isinstance(created_at, datetime) else updated_text
    )
    generic_event = {
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
            "latest_workflow_status": latest_status or None,
        },
    }
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
        "progress_events": [generic_event, *workflow_events],
        "result": {
            "project_slug": slug,
            "target_chapters": _safe_int(getattr(project, "target_chapters", 0)),
            "pending_autonomous_repair_tasks": pending,
            "queued_autonomous_repair_tasks": queued,
            "failed_autonomous_repair_tasks": failed,
            "rejected_autonomous_repair_candidates": rejected,
            "latest_repair_workflow_id": latest_workflow.get("workflow_run_id"),
            "target_chapter_numbers": latest_workflow.get("target_chapter_numbers"),
            "repair_gate_chapter_numbers": latest_workflow.get("repair_gate_chapter_numbers"),
            "processed_chapter_numbers": latest_workflow.get("processed_chapter_numbers"),
        },
        "error": error,
        "payload": {
            "slug": slug,
            "title": title,
            "include_pending_rewrite_tasks": True,
            "target_chapter_numbers": latest_workflow.get("target_chapter_numbers"),
            "repair_gate_chapter_numbers": latest_workflow.get("repair_gate_chapter_numbers"),
        },
        "repair_status": repair_status,
        "synthetic_db_repair_task": True,
        "activity_status": latest_activity["status"],
        "execution_active": bool(latest_activity["execution_active"]),
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
        latest_repair_workflows = await _load_latest_project_repair_workflow_snapshots(
            session,
            project_ids,
        )
        repair_heal_owned_slugs = _fetch_heal_owned_slugs_by_kind(
            settings.redis.url,
            "repair",
        )
        outline_replan_heal_owned_slugs = _fetch_heal_owned_slugs_by_kind(
            settings.redis.url,
            "outline_replan",
        )
        summaries: list[dict[str, object]] = []
        for row in rows:
            if row.slug in active_slugs:
                continue
            repair_status = _build_project_repair_status_payload(
                row,
                repair_counts.get(row.id, []),
                autonomous_repair_counts.get(row.id, {}),
                latest_repair_workflows.get(row.id),
                repair_owner_active=row.slug in repair_heal_owned_slugs,
                outline_replan_owner_active=row.slug in outline_replan_heal_owned_slugs,
            )
            summary = _build_db_repair_task_summary(row, repair_status)
            if summary is not None:
                if row.slug in repair_heal_owned_slugs:
                    job_id = f"repair:heal:{row.slug}"
                    worker_progress = _load_worker_heal_progress_snapshot(
                        settings.redis.url,
                        job_id,
                    )
                    _attach_repair_heal_owner_to_db_summary(
                        summary,
                        job_id,
                        worker_progress,
                    )
                elif row.slug in outline_replan_heal_owned_slugs:
                    job_id = f"outline-replan:heal:{row.slug}"
                    worker_progress = _load_worker_heal_progress_snapshot(
                        settings.redis.url,
                        job_id,
                    )
                    summary["worker_job_id"] = job_id
                    summary["repair_status"]["is_repairing"] = True
                    _merge_worker_progress_into_db_repair_summary(summary, worker_progress)
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


def _find_original_autowrite_task(
    task_manager: "WebTaskManager",
    slug: str,
) -> dict[str, object] | None:
    """Find a persisted autowrite envelope for a project slug.

    Completion is deliberately checked against current DB/filesystem dashboard
    stats by the async wrapper below; the task manager result is often ``None``
    after cancellation. A historical false-completed envelope is recoverable
    here too, but only when the wrapper confirms the manuscript is incomplete.
    """

    slug = str(slug or "").strip()
    if not slug:
        return None
    for task in task_manager.list_tasks():
        if (
            str(task.get("task_type") or "") != "autowrite"
            or str(task.get("project_slug") or "") != slug
            or str(task.get("status") or "") not in (_REUSABLE_TASK_STATUSES | {"completed"})
        ):
            continue
        payload = task.get("payload")
        if not isinstance(payload, dict):
            continue
        return task
    return None


async def _load_incomplete_original_autowrite_task(
    settings: AppSettings,
    task_manager: "WebTaskManager",
    slug: str,
) -> dict[str, object] | None:
    task = _find_original_autowrite_task(task_manager, slug)
    if task is None:
        return None
    dashboard = await _load_project_dashboard_task_payload(settings, slug)
    stats = dashboard.get("chapter_word_stats") if isinstance(dashboard, dict) else None
    if not isinstance(stats, dict):
        return None
    if "target_chapters" not in stats or "completed_chapters" not in stats:
        return None
    try:
        target = int(stats.get("target_chapters") or 0)
        completed = int(stats.get("completed_chapters") or 0)
    except (TypeError, ValueError):
        return None
    return task if target > 0 and 0 <= completed < target else None


def _resume_original_autowrite_task(
    task_manager: "WebTaskManager",
    original: dict[str, object],
    saved_payload: dict[str, object],
    *,
    heal_owned: bool,
) -> dict[str, object] | str | None:
    task_id = str(original.get("task_id") or "")
    if not task_id:
        return None
    return task_manager.resume_autowrite_task(
        task_id,
        saved_payload,
        delegate_to_self_heal=heal_owned,
        heal_owned=heal_owned,
    )


def _mark_db_repair_resume_block(
    task_manager: "WebTaskManager",
    task_id: str,
    block_payload: dict[str, object],
) -> None:
    stage = str(block_payload.get("current_stage", "blocked_structural_repair"))
    error = str(block_payload.get("error") or "")
    if block_payload.get("blocked_generation_gate"):
        task_manager.mark_task_blocked_by_project_gate(task_id, stage=stage, error=error)
    else:
        task_manager.mark_task_blocked_by_structural_repair(task_id, error)


async def _cancel_db_repair_task_async(settings: AppSettings, task_id: str) -> str:
    """Cancel the DB-backed repair work represented by ``db-repair:<slug>``."""
    if not task_id.startswith("db-repair:"):
        return "not_found"
    slug = task_id.removeprefix("db-repair:").strip()
    if not slug:
        return "not_found"

    from sqlalchemy import update

    from bestseller.domain.enums import WorkflowStatus
    from bestseller.infra.db.models import RewriteTaskModel, WorkflowRunModel
    from bestseller.services.autonomous_book_repair import AUTONOMOUS_REPAIR_TRIGGER
    from bestseller.services.repair import WORKFLOW_TYPE_PROJECT_REPAIR

    async with session_scope(settings) as session:
        project = await get_project_by_slug(session, slug)
        if project is None:
            return "not_found"
        result = await session.execute(
            update(RewriteTaskModel)
            .where(
                RewriteTaskModel.project_id == project.id,
                RewriteTaskModel.trigger_type == AUTONOMOUS_REPAIR_TRIGGER,
                RewriteTaskModel.status.in_(("pending", "queued", "paused")),
            )
            .values(
                status="cancelled",
                error_log="Repair task cancelled from web dashboard.",
            )
        )
        cancelled_rows = int(result.rowcount or 0)

        # The synthetic ``db-repair:<slug>`` card reports the project_repair
        # workflow row's status. A crashed/abandoned repair can leave that row
        # stuck in ``running`` (e.g. a flush-time lock timeout the worker could
        # not record), so Stop must flip the row itself — otherwise the card
        # stays "running", the cancel reports ``not_running``, and the dashboard
        # Stop/Delete buttons deadlock against their own visibility model.
        workflow_result = await session.execute(
            update(WorkflowRunModel)
            .where(
                WorkflowRunModel.project_id == project.id,
                WorkflowRunModel.workflow_type == WORKFLOW_TYPE_PROJECT_REPAIR,
                WorkflowRunModel.status.in_(("pending", "queued", "running")),
            )
            .values(
                status=WorkflowStatus.CANCELLED.value,
                current_step="cancelled_from_web_dashboard",
                error_message="Repair workflow cancelled from web dashboard.",
            )
        )
        cancelled_workflow_rows = int(workflow_result.rowcount or 0)

    job_cancelled = await _abort_worker_heal_job_async(
        settings.redis.url,
        f"repair:heal:{slug}",
    )
    return (
        "cancel_requested"
        if cancelled_rows or cancelled_workflow_rows or job_cancelled
        else "not_running"
    )


def _cancel_db_repair_task(settings: AppSettings, task_id: str) -> str:
    if not task_id.startswith("db-repair:"):
        return "not_found"
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        logger.warning("task cancel: cannot cancel DB repair task from an active event loop")
        return "not_found"
    try:
        return asyncio.run(_cancel_db_repair_task_async(settings, task_id))
    except Exception:
        logger.exception("task cancel: failed to cancel DB repair task %s", task_id)
        return "not_found"


def _request_visible_task_cancel(
    task_manager: WebTaskManager,
    settings: AppSettings,
    task_id: str,
) -> str:
    """Cancel any task visible through the dashboard task API.

    The dashboard merges in-memory web tasks with synthetic worker/DB repair
    task cards.  Cancellation must follow the same visibility model; otherwise
    clicking Stop on a synthetic card reports "Task not found" even though the
    task was just listed.
    """
    normalized_task_id = task_id.strip("/")
    if not normalized_task_id:
        return "not_found"

    task = task_manager.get_task(normalized_task_id)
    if task is not None:
        marked = task_manager.request_cancel(normalized_task_id)
        # Marking the task is not enough. That flag lives in this process's
        # memory, while the thing that keeps the book running — self-heal —
        # only reads the database. A live run showed the cost: the repair the
        # user stopped went to `cancelled`, and self-heal had queued a fresh
        # chapter pipeline three minutes later, with no cancel/pause marker
        # anywhere on the project. Stop is a decision about the *book*, so it
        # has to land where self-heal will see it.
        if not marked:
            # Already finished — nothing to stop, and pausing its project here
            # would strand a book that legitimately completed.
            return "not_running"
        slug = str(task.get("project_slug") or "").strip()
        if slug:
            try:
                _cancel_project_task(settings, f"project:{slug}")
            except Exception:
                logger.exception("task cancel: failed to pause project %s", slug)
        return "cancel_requested"

    if normalized_task_id.startswith("db-repair:"):
        return _cancel_db_repair_task(settings, normalized_task_id)

    if normalized_task_id.startswith("project:"):
        return _cancel_project_task(settings, normalized_task_id)

    if normalized_task_id.startswith(("autowrite:heal:", "repair:heal:")):
        return (
            "cancel_requested"
            if _abort_worker_heal_job(settings.redis.url, normalized_task_id)
            else "not_found"
        )

    return "not_found"


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
        "generation_gate_auto_retry_needed",
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
    generation_gate_blocked = any(bool(metadata.get(flag)) for flag in _GENERATION_GATE_BLOCK_FLAGS)
    # These flags represent machine-repair work, not a human handoff. Older
    # deployments used them to return 409 and freeze resume attempts; keep the
    # metadata visible elsewhere, but never block autowrite/resume on it.
    if structural_blocked or generation_gate_blocked:
        logger.info(
            "Ignoring machine-repair autowrite block flags for slug=%s",
            getattr(project, "slug", ""),
        )
    return None


def _build_project_repair_status_payload(
    project: Any,
    chapter_state_counts: list[dict[str, object]],
    autonomous_repair_task_counts: dict[str, int] | None = None,
    latest_repair_workflow: dict[str, object] | None = None,
    *,
    repair_owner_active: bool = False,
    outline_replan_owner_active: bool = False,
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
    production_pause_reason = str(metadata.get("production_pause_reason") or "").strip()
    resume_blocked = bool(metadata.get("generation_resume_blocked_until_repair_audit"))
    generation_gate_blocked = (
        any(bool(metadata.get(flag)) for flag in _GENERATION_GATE_BLOCK_FLAGS)
        or any(
            str(metadata.get(flag) or "").strip().lower() == "needs_replan"
            for flag in (
                "planning_status",
                "outline_semantic_gate_status",
                "rolling_outline_status",
            )
        )
        or bool(metadata.get("outline_replan_required"))
    )
    structural_repair_required = bool(metadata.get("structural_repair_required"))
    latest_workflow = latest_repair_workflow if isinstance(latest_repair_workflow, dict) else {}
    latest_workflow_status = str(latest_workflow.get("status") or "").strip().lower()
    latest_workflow_error = str(latest_workflow.get("error_message") or "").strip()
    latest_activity = latest_workflow.get("workflow_activity") or _workflow_activity_evidence(latest_workflow)
    latest_workflow_active = bool(latest_activity.get("execution_active"))
    latest_workflow_interrupted = latest_workflow_status in {"cancelled", "failed"}
    project_status = str(getattr(project, "status", "") or "")
    library_archived = bool(metadata.get("library_archived")) or project_status == "archived"
    project_closed = project_status.lower() in _BOOK_CLOSED_STATUSES

    if library_archived:
        phase = "archived"
        label = "已归档"
        detail = "项目已归档，不会进入自动写作或自动修复。"
    elif project_closed:
        phase = "completed"
        label = "已完成"
        detail = "项目已标记完成，不会进入自动写作或自动修复。"
    elif generation_gate_blocked and (
        repair_owner_active or outline_replan_owner_active or latest_workflow_active
    ):
        phase = "planning_gate"
        label = "规划门禁续跑中"
        detail = "规划/世界观门禁未闭合，系统会携带诊断重新进入自动修复链路。"
    elif generation_gate_blocked:
        phase = "needs_attention"
        label = "需要重新规划"
        detail = "规划/世界观门禁未闭合，自愈巡检会携带诊断重新接管这本书。"
    elif latest_workflow_active:
        phase = "repair_gate"
        label = "结构修复执行中"
        detail = "后台正在执行项目结构修复，页面会按 workflow 记录回填章节进度。"
    elif latest_activity.get("status") == "stale":
        phase = "needs_attention"
        label = "历史任务待恢复"
        detail = "持久化运行状态已过期，缺少近期步骤、心跳或执行者证据；原记录保留。"
    elif latest_workflow_interrupted and (blocked_chapters > 0 or project_status == "paused"):
        phase = "needs_attention"
        label = "修复已中断"
        detail = latest_workflow_error or "最近一次项目结构修复未完成，需要重新运行修复流程。"
    elif production_paused or resume_blocked:
        phase = "repair_gate"
        label = "修复续跑中"
        detail = (
            f"项目暂停原因：{production_pause_reason}。"
            if production_pause_reason
            else "项目存在阻塞章节，后台会继续执行修复并通过审计。"
        )
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

    is_repairing = (
        False
        if library_archived or project_closed
        else bool(latest_workflow_active or repair_owner_active or outline_replan_owner_active)
    )
    return {
        "phase": phase,
        "label": label,
        "detail": detail,
        "is_repairing": is_repairing,
        "production_paused": production_paused,
        "production_pause_reason": production_pause_reason or None,
        "resume_blocked_until_repair_audit": resume_blocked,
        "structural_repair_required": structural_repair_required,
        "latest_repair_workflow": latest_workflow or None,
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


def _is_fanqie_short_export_listing(listing_profile: Mapping[str, object] | None) -> bool:
    return (
        bool(listing_profile) and str(listing_profile.get("source") or "") == "fanqie_short_export"
    )


def _build_project_identity_payload(
    project: object,
    listing_profile: Mapping[str, object],
    *,
    fanqie_stats: Mapping[str, object] | None = None,
    include_id: bool = False,
    delivery_closure: Mapping[str, object] | None = None,
) -> dict[str, object]:
    meta = (
        getattr(project, "metadata_json", {})
        if isinstance(getattr(project, "metadata_json", {}), dict)
        else {}
    )
    display_title = str(
        listing_profile.get("primary_title")
        or listing_profile.get("current_content_title")
        or getattr(project, "title", None)
        or getattr(project, "slug", "")
        or "未命名作品"
    )
    payload: dict[str, object] = {
        "slug": getattr(project, "slug", ""),
        "title": display_title,
        "stored_title": getattr(project, "title", ""),
        "genre": getattr(project, "genre", None),
        "sub_genre": getattr(project, "sub_genre", None),
        "audience": getattr(project, "audience", None),
        "status": getattr(project, "status", None),
        "target_word_count": getattr(project, "target_word_count", 0),
        "target_chapters": getattr(project, "target_chapters", 0),
        "author_display_name": _project_author_display_name(project),
        "current_volume_number": getattr(project, "current_volume_number", None),
        "current_chapter_number": getattr(project, "current_chapter_number", None),
        "synopsis": meta.get("synopsis", ""),
        "tags": meta.get("tags", []),
        "premise": meta.get("premise", ""),
    }
    if include_id:
        project_id = getattr(project, "id", None)
        payload["id"] = str(project_id) if project_id is not None else None

    if _is_fanqie_short_export_listing(listing_profile):
        stats = fanqie_stats or {}
        current_export = listing_profile.get("current_export")
        current_export = current_export if isinstance(current_export, Mapping) else {}
        book_state = _library_book_state(
            status=getattr(project, "status", None),
            completed_units=int(stats.get("completed_chapters") or 0),
            target_units=int(stats.get("target_chapters") or 1),
            has_content=bool(stats.get("word_count_total") or current_export.get("word_count")),
            archived=_project_library_archived(meta), delivery_closure=delivery_closure,
        )
        payload.update(
            {
                "status": "completed"
                if book_state == "closed_complete"
                else getattr(project, "status", None),
                "book_state": book_state or None,
                "library_archived": _project_library_archived(meta),
                "project_type": getattr(project, "project_type", None),
                "content_mode": meta.get("content_mode") or "fanqie_short_story",
                "unit_kind": stats.get("unit_kind") or "single_story",
                "unit_label": stats.get("unit_label") or "全文",
                "target_chapters": int(stats.get("target_chapters") or 1),
                "target_segments": int(stats.get("target_segments") or 1),
                "completed_chapters": int(stats.get("completed_chapters") or 0),
                "completed_segments": int(stats.get("completed_segments") or 0),
                "current_chapter_number": int(stats.get("completed_chapters") or 0),
                "words_on_disk": int(
                    stats.get("word_count_total") or current_export.get("word_count") or 0
                ),
                "synopsis": listing_profile.get("short_intro")
                or listing_profile.get("shelf_intro")
                or meta.get("synopsis", ""),
                "tags": listing_profile.get("tags") or meta.get("tags", []),
                "premise": listing_profile.get("logline") or meta.get("premise", ""),
            }
        )
    payload["delivery_closure"] = delivery_closure
    return payload


_DESIGN_ARTIFACT_LABELS: dict[str, str] = {
    "heat_search": "热度搜索",
    "concept_methodology": "脑洞/爽点方法论",
    "concept_fusion": "脑洞融合",
    "hook_candidates": "钩子候选",
    "commercial_brief": "商业定位简报",
    "premise": "命题与核心承诺",
    "book_spec": "全书设定",
    "world_spec": "世界观设定",
    "cast_spec": "人物设定",
    "story_design_kernel": "故事设计内核",
    "entry_system_kernel": "入口系统内核",
    "emotion_driven_kernel": "情绪驱动内核",
    "public_emotion_kernel": "公共情绪内核",
    "compliance_boundary_kernel": "合规边界内核",
    "act_plan": "幕结构",
    "volume_plan": "卷规划",
    "volume_chapter_outline": "卷章大纲",
    "chapter_outline_batch": "章节大纲批次",
    "volume_cast_expansion": "分卷人物扩展",
    "volume_world_disclosure": "分卷世界揭示",
    "plan_validation": "规划校验",
    "prewrite_readiness": "开写前就绪度",
    "promotional_brief": "上架/宣传资料",
    "fanqie_beat_sheet": "番茄短故事节拍表",
}

_DESIGN_ARTIFACT_PHASES: dict[str, str] = {
    "heat_search": "conception",
    "concept_methodology": "conception",
    "concept_fusion": "conception",
    "hook_candidates": "conception",
    "commercial_brief": "conception",
    "premise": "foundation",
    "book_spec": "foundation",
    "world_spec": "foundation",
    "cast_spec": "foundation",
    "story_design_kernel": "foundation",
    "entry_system_kernel": "foundation",
    "emotion_driven_kernel": "foundation",
    "public_emotion_kernel": "foundation",
    "compliance_boundary_kernel": "foundation",
    "act_plan": "macro_plan",
    "volume_plan": "macro_plan",
    "volume_chapter_outline": "outline",
    "chapter_outline_batch": "outline",
    "volume_cast_expansion": "outline",
    "volume_world_disclosure": "outline",
    "plan_validation": "quality_gate",
    "prewrite_readiness": "quality_gate",
    "promotional_brief": "publishing",
    "fanqie_beat_sheet": "outline",
}

_DESIGN_REQUIRED_SURFACES: tuple[tuple[str, str, str], ...] = (
    ("planning_artifacts", "规划产物", "缺少命题、设定或大纲等 PlanningArtifactVersion。"),
    ("structure", "章节大纲", "缺少可审查的卷/章/场结构。"),
    ("characters", "人物信息", "缺少人物卡，无法提前审查人物目标、秘密、弧光和状态。"),
    ("relationships", "关系图", "缺少人物关系边，无法提前审查关系张力和变化。"),
    ("world", "世界观", "缺少世界主干、规则、地点或势力信息。"),
    ("plot_arcs", "叙事线", "缺少主线/副线/主题线，无法提前判断跨章推进。"),
    ("chapter_contracts", "章节合约", "缺少章节合约，无法在写正文前审查每章任务。"),
    ("scene_contracts", "场景合约", "缺少场景合约，无法审查每场的冲突、信息和尾钩。"),
)


def _design_artifact_phase(artifact_type: str) -> str:
    return _DESIGN_ARTIFACT_PHASES.get(artifact_type, "other")


def _design_artifact_label(artifact_type: str) -> str:
    return _DESIGN_ARTIFACT_LABELS.get(artifact_type, artifact_type.replace("_", " "))


def _count_design_surface(
    *,
    surface: str,
    planning_documents: list[dict[str, object]],
    structure: Mapping[str, object],
    story_bible: Mapping[str, object],
    narrative: Mapping[str, object],
) -> int:
    if surface == "planning_artifacts":
        return len(planning_documents)
    if surface == "structure":
        return int(structure.get("total_chapters") or 0) + int(structure.get("total_scenes") or 0)
    if surface == "characters":
        return len(story_bible.get("characters") or [])
    if surface == "relationships":
        return len(story_bible.get("relationships") or [])
    if surface == "world":
        return (
            int(bool(story_bible.get("world_backbone")))
            + len(story_bible.get("world_rules") or [])
            + len(story_bible.get("locations") or [])
            + len(story_bible.get("factions") or [])
        )
    if surface == "plot_arcs":
        return len(narrative.get("plot_arcs") or [])
    if surface == "chapter_contracts":
        return len(narrative.get("chapter_contracts") or [])
    if surface == "scene_contracts":
        return len(narrative.get("scene_contracts") or [])
    return 0


def _build_design_dossier_readiness(
    *,
    planning_documents: list[dict[str, object]],
    structure: Mapping[str, object],
    story_bible: Mapping[str, object],
    narrative: Mapping[str, object],
) -> dict[str, object]:
    checks: list[dict[str, object]] = []
    for surface, label, missing_message in _DESIGN_REQUIRED_SURFACES:
        count = _count_design_surface(
            surface=surface,
            planning_documents=planning_documents,
            structure=structure,
            story_bible=story_bible,
            narrative=narrative,
        )
        checks.append(
            {
                "surface": surface,
                "label": label,
                "count": count,
                "status": "present" if count > 0 else "missing",
                "message": "可审查" if count > 0 else missing_message,
            }
        )
    missing = [item for item in checks if item["status"] == "missing"]
    return {
        "status": "ready" if not missing else "incomplete",
        "present_count": len(checks) - len(missing),
        "missing_count": len(missing),
        "checks": checks,
        "blocking_gaps": missing,
    }


def _planning_artifact_stats(
    documents: list[dict[str, object]],
) -> dict[str, object]:
    by_type: dict[str, int] = {}
    latest_by_type: dict[str, dict[str, object]] = {}
    for doc in documents:
        artifact_type = str(doc.get("artifact_type") or "")
        by_type[artifact_type] = by_type.get(artifact_type, 0) + 1
        current = latest_by_type.get(artifact_type)
        version = int(doc.get("version_no") or 0)
        current_version = int(current.get("version_no") or 0) if current else -1
        if current is None or version > current_version:
            latest_by_type[artifact_type] = doc
    return {
        "document_count": len(documents),
        "type_count": len(by_type),
        "by_type": by_type,
        "latest_by_type": latest_by_type,
    }


def _fanqie_short_export_artifact_entry(
    settings: AppSettings,
    project_slug: str,
) -> dict[str, object] | None:
    output_dir = _project_output_dir(settings, project_slug)
    export_path = _fanqie_short_export_file(output_dir)
    if export_path is None:
        return None
    stat = export_path.stat()
    entry: dict[str, object] = {
        "name": str(_FANQIE_SHORT_EXPORT_RELATIVE_PATH),
        "path": str(export_path.resolve()),
        "size_bytes": stat.st_size,
        "suffix": export_path.suffix.lower(),
        "modified_at": datetime.fromtimestamp(stat.st_mtime, UTC).isoformat(),
        "is_previewable": export_path.suffix.lower() == ".md",
    }
    if export_path.suffix.lower() == ".md":
        entry.update(_read_markdown_artifact_metadata(export_path))
    return entry


async def _load_latest_current_chapter_preview_entry(
    session: Any,
    settings: AppSettings,
    project: Any,
) -> dict[str, object] | None:
    """Return a DB-authoritative artifact entry for the newest current chapter."""
    from sqlalchemy import select

    from bestseller.infra.db.models import ChapterDraftVersionModel, ChapterModel

    row = (
        await session.execute(
            select(
                ChapterModel.chapter_number,
                ChapterDraftVersionModel.content_md,
                ChapterDraftVersionModel.word_count,
                ChapterDraftVersionModel.created_at,
            )
            .join(
                ChapterDraftVersionModel,
                ChapterDraftVersionModel.chapter_id == ChapterModel.id,
            )
            .where(
                ChapterModel.project_id == project.id,
                ChapterDraftVersionModel.is_current.is_(True),
            )
            .order_by(
                ChapterDraftVersionModel.created_at.desc().nullslast(),
                ChapterModel.chapter_number.desc(),
            )
            .limit(1)
        )
    ).first()
    if row is None:
        return None
    chapter_number, content_md, draft_word_count, created_at = row
    content = str(content_md or "")
    if not content.strip():
        return None
    stats = build_markdown_reading_stats(content)
    output_path = (
        Path(settings.output.base_dir) / str(project.slug) / f"chapter-{int(chapter_number):03d}.md"
    )
    modified_at = created_at
    return {
        "name": f"chapter-{int(chapter_number):03d}.md",
        "path": str(output_path.resolve()),
        "size_bytes": len(content.encode("utf-8")),
        "suffix": ".md",
        "modified_at": modified_at.isoformat()
        if isinstance(modified_at, datetime)
        else datetime.now(UTC).isoformat(),
        "is_previewable": True,
        "word_count": int(draft_word_count or stats["word_count"] or 0),
        "character_count": stats["character_count"],
        "paragraph_count": stats["paragraph_count"],
        "estimated_read_minutes": stats["estimated_read_minutes"],
        "source": "db_current_draft",
    }


async def _load_project_summary_payload(
    settings: AppSettings,
    project_slug: str,
) -> dict[str, object]:
    async with session_scope(settings) as session:
        project = await get_project_by_slug(session, project_slug)
        if project is None:
            raise ValueError(f"Project '{project_slug}' was not found.")
        delivery = await _read_project_delivery_status(session, project)
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
        latest_repair_workflows = await _load_latest_project_repair_workflow_snapshots(
            session,
            [project.id],
        )
        latest_current_chapter_entry = await _load_latest_current_chapter_preview_entry(
            session,
            settings,
            project,
        )
        repair_status = _build_project_repair_status_payload(
            project,
            repair_counts.get(project.id, []),
            autonomous_repair_counts.get(project.id, {}),
            latest_repair_workflows.get(project.id),
        )
    outputs = collect_project_artifact_entries(settings, project_slug)
    outputs = _upsert_artifact_entry(outputs, latest_current_chapter_entry)
    markdown_entries = [item for item in outputs if str(item["suffix"]) == ".md"]
    default_preview_entry = _select_default_preview_entry(
        markdown_entries,
        latest_current_chapter_entry=latest_current_chapter_entry,
    )
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
    listing_error = None
    try:
        listing_profile = _build_fanqie_short_current_listing_profile(
            settings, project
        ) or build_book_listing_profile(
            project=project,
            writing_profile=writing_profile,
            story_bible=story_bible,
            output_base_dir=settings.output.base_dir,
        )
    except ValueError as exc:
        from bestseller.services.listing_errors import listing_conflict_detail

        listing_error = listing_conflict_detail(exc)
        if listing_error is None:
            raise
        # Keep chapter/workflow inspection available, without serving stale copy.
        listing_profile = {}
    display_title = str(
        listing_profile.get("primary_title")
        or listing_profile.get("current_content_title")
        or project.title
        or project_slug
    )
    fanqie_stats = (
        _build_fanqie_short_export_task_stats(settings, project, include_chapters=True)
        if _is_fanqie_short_export_listing(listing_profile)
        else None
    )
    project_payload = _build_project_identity_payload(
        project,
        listing_profile,
        fanqie_stats=fanqie_stats,
        include_id=True,
        delivery_closure=delivery,
    )
    output_stats = {
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
    }
    if fanqie_stats is not None:
        export_entry = _fanqie_short_export_artifact_entry(settings, project_slug)
        if export_entry is not None:
            outputs = [export_entry]
        default_preview_entry = export_entry
        output_stats = {
            "markdown_output_count": 1 if export_entry is not None else 0,
            "project_word_count": 0,
            "chapter_word_count_total": int(fanqie_stats.get("word_count_total") or 0),
            "default_preview_name": str(fanqie_stats.get("current_export_filename") or ""),
            "default_preview_word_count": int(fanqie_stats.get("word_count_total") or 0),
            "default_preview_estimated_read_minutes": int(
                fanqie_stats.get("estimated_read_minutes") or 0
            ),
        }
    structure_summary = {
        "total_chapters": structure.total_chapters,
        "total_scenes": structure.total_scenes,
        "volume_count": len(structure.volumes),
    }
    if fanqie_stats is not None:
        structure_summary.update(
            {
                "total_chapters": int(fanqie_stats.get("target_chapters") or 1),
                "total_scenes": 1,
                "volume_count": 1,
            }
        )
    return {
        "title": display_title,
        "project": project_payload,
        "listing_profile": listing_profile,
        "listing_error": listing_error,
        "repair_status": repair_status,
        "writing_profile": writing_profile,
        "structure_summary": structure_summary,
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
        "output_stats": output_stats,
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
        delivery = await _read_project_delivery_status(session, project)
        style_guide = await session.get(StyleGuideModel, project.id)
        writing_profile = get_project_writing_profile(project, style_guide).model_dump(mode="json")
        story_bible = await build_story_bible_overview(session, project_slug)
    listing = _build_fanqie_short_current_listing_profile(
        settings, project
    ) or build_book_listing_profile(
        project=project,
        writing_profile=writing_profile,
        story_bible=story_bible,
        output_base_dir=settings.output.base_dir,
    )
    fanqie_stats = (
        _build_fanqie_short_export_task_stats(settings, project, include_chapters=False)
        if _is_fanqie_short_export_listing(listing)
        else None
    )
    return {
        "listing_profile": listing,
        "project": _build_project_identity_payload(
            project,
            listing,
            fanqie_stats=fanqie_stats,
            include_id=False,
            delivery_closure=delivery,
        ),
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


# 执行进度面板:把"规划→写作"全过程拍平成人类可读的阶段/产物/实时步骤,消除黑盒。
_EXECUTION_PHASES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("premise", "立意 · Premise", ("premise",)),
    ("book_spec", "作品契约 · BookSpec", ("book_spec",)),
    ("world_spec", "世界观 · WorldSpec", ("world_spec",)),
    ("cast_spec", "角色阵容 · CastSpec", ("cast_spec",)),
    ("story_design_kernel", "故事内核 + AI 推演", ("story_design_kernel",)),
    ("volume_plan", "卷规划 · VolumePlan", ("volume_plan",)),
    ("outline", "章节细纲 · Outline", ("volume_chapter_outline", "chapter_outline_batch")),
    ("materialize", "章节物化 · Materialize", ()),
    ("writing", "正文写作 · Writing", ()),
    ("export", "导出 · Export", ()),
)

_ARTIFACT_LABELS: dict[str, str] = {
    "premise": "立意",
    "book_spec": "作品契约",
    "world_spec": "世界观",
    "cast_spec": "角色阵容",
    "story_design_kernel": "故事内核",
    "emotion_driven_kernel": "情绪内核",
    "entry_system_kernel": "开篇系统",
    "public_emotion_kernel": "大众情绪内核",
    "compliance_boundary_kernel": "合规边界",
    "volume_plan": "卷规划",
    "volume_chapter_outline": "分卷细纲",
    "chapter_outline_batch": "章节大纲批次",
    "plan_validation": "规划校验",
    "prewrite_readiness": "开写就绪检查",
    "promotional_brief": "营销文案",
    "volume_world_disclosure": "世界揭示节奏",
}


async def _load_project_reviews_payload(
    settings: AppSettings,
    project_slug: str,
    *,
    limit: int = 200,
) -> dict[str, object]:
    """聚合一本书的判官评审:verdict + 维度评分 + 证据理由,按时间倒序。"""

    from sqlalchemy import desc, select

    from bestseller.infra.db.models import (
        ChapterModel,
        QualityScoreModel,
        ReviewReportModel,
    )

    async with session_scope(settings) as session:
        project = await get_project_by_slug(session, project_slug)
        if project is None:
            raise ValueError(f"Project '{project_slug}' was not found.")
        quality_rows = list(
            (
                await session.execute(
                    select(QualityScoreModel).where(QualityScoreModel.project_id == project.id)
                )
            ).scalars()
        )
        quality_by_report = {
            q.review_report_id: q for q in quality_rows if q.review_report_id is not None
        }
        chapter_rows = list(
            await session.execute(
                select(ChapterModel.id, ChapterModel.chapter_number).where(
                    ChapterModel.project_id == project.id
                )
            )
        )
        chapter_number_by_id = {cid: num for cid, num in chapter_rows}
        report_rows = list(
            (
                await session.execute(
                    select(ReviewReportModel)
                    .where(ReviewReportModel.project_id == project.id)
                    .order_by(desc(ReviewReportModel.created_at))
                    .limit(limit)
                )
            ).scalars()
        )

    reviews: list[dict[str, object]] = []
    for report in report_rows:
        quality = quality_by_report.get(report.id)
        scores = None
        if quality is not None:
            scores = {
                "overall": quality.score_overall,
                "goal": quality.score_goal,
                "conflict": quality.score_conflict,
                "emotion": quality.score_emotion,
                "dialogue": quality.score_dialogue,
                "style": quality.score_style,
                "hook": quality.score_hook,
                "evidence_summary": quality.evidence_summary,
            }
        reviews.append(
            {
                "id": str(report.id),
                "target_type": report.target_type,
                "target_id": str(report.target_id) if report.target_id else None,
                "chapter_number": chapter_number_by_id.get(report.target_id),
                "reviewer_type": report.reviewer_type,
                "verdict": report.verdict,
                "severity_max": report.severity_max,
                "created_at": report.created_at.isoformat() if report.created_at else None,
                "structured_output": report.structured_output
                if isinstance(report.structured_output, dict)
                else {},
                "scores": scores,
            }
        )

    return {
        "generated_at": _utc_now(),
        "project_slug": project_slug,
        "count": len(reviews),
        "reviews": reviews,
    }


def _export_artifact_bytes_verified(artifact: Any, settings: Any, project_slug: str) -> bool:
    """Resolve immutable bytes across host/container mounts without trusting a mirror."""
    checksum = str(artifact.checksum or "")
    if len(checksum) != 64 or any(c not in "0123456789abcdef" for c in checksum):
        return False
    stored = Path(artifact.storage_uri)
    portable = Path(settings.output.base_dir) / project_slug / ".export-artifacts" / checksum / stored.name
    for path in (stored, portable):
        try:
            if hashlib.sha256(path.read_bytes()).hexdigest() == checksum:
                return True
        except (OSError, ValueError):
            pass
    return False


async def _load_execution_progress_payload(
    settings: AppSettings,
    project_slug: str,
) -> dict[str, object]:
    """聚合一本书的执行进度:阶段状态 + 已产出规划 + 当前活动 + 实时步骤流 + 章节进度。"""

    from sqlalchemy import case, func, select

    from bestseller.infra.db.models import (
        ChapterDraftVersionModel,
        ChapterModel,
        ExportArtifactModel,
        PlanningArtifactVersionModel,
    )
    from bestseller.services.web_progress_queries import load_execution_workflow_summary

    async with session_scope(settings) as session:
        project = await get_project_by_slug(session, project_slug)
        if project is None:
            raise ValueError(f"Project '{project_slug}' was not found.")
        overview = await load_execution_workflow_summary(session, project.id)

        # 最新版本的各类规划产物
        rows = list(
            await session.execute(
                select(
                    PlanningArtifactVersionModel.artifact_type,
                    PlanningArtifactVersionModel.version_no,
                    case(
                        (PlanningArtifactVersionModel.artifact_type == "story_design_kernel",
                         PlanningArtifactVersionModel.content),
                        else_=None,
                    ),
                )
                .where(PlanningArtifactVersionModel.project_id == project.id)
                .order_by(
                    PlanningArtifactVersionModel.artifact_type.asc(),
                    PlanningArtifactVersionModel.version_no.desc(),
                )
            )
        )
        latest_by_type: dict[str, tuple[int, dict]] = {}
        count_by_type: dict[str, int] = {}
        for atype, vno, content in rows:
            count_by_type[atype] = count_by_type.get(atype, 0) + 1
            if atype not in latest_by_type:
                latest_by_type[atype] = (vno, content if isinstance(content, dict) else {})

        chapters_total = int(
            await session.scalar(
                select(func.count(ChapterModel.id)).where(ChapterModel.project_id == project.id)
            )
            or 0
        )
        adoption = (await _load_adopted_chapter_progress(
            session, [project.id],
            project_languages={project.id: getattr(project, "language", "zh-CN")},
        ))[project.id]
        chapters_written = int(adoption["adopted_chapters"])
        exported_numbers: set[int] = set()
        adopted_sources = {row["draft_id"]: row for row in adoption["chapters"]}
        for artifact, in await session.execute(select(ExportArtifactModel).where(
            ExportArtifactModel.project_id == project.id,
        )):
            if not adopted_sources:
                break
            data = artifact.metadata_json or {}
            if not data.get("immutable_bytes"):
                continue
            if not _export_artifact_bytes_verified(artifact, settings, project.slug):
                continue
            for source in data.get("source_drafts", []):
                if not isinstance(source, Mapping):
                    continue
                adopted = adopted_sources.get(source.get("draft_id"))
                if not adopted:
                    continue
                draft = await session.get(ChapterDraftVersionModel, UUID(source["draft_id"]))
                if (draft is not None and draft.project_id == project.id
                    and str(draft.chapter_id) == source.get("chapter_id")
                    and source.get("chapter_number") == adopted["number"]
                    and source.get("version_no") == draft.version_no
                    and source.get("promotion_state") == draft.promotion_state
                    and source.get("adoption_state") == adopted["acceptance"]
                    and hashlib.sha256((draft.content_md or "").encode()).hexdigest()
                    == source.get("body_sha256")):
                    exported_numbers.add(adopted["number"])
        target_chapters = int(getattr(project, "target_chapters", 0) or 0)
        # Canonical adoption supplies premise only. A design lock or an in-memory
        # compilation is not evidence that foundation artifacts were persisted.
        canonical_premise_verified = False
        metadata = getattr(project, "metadata_json", None) or {}
        canonical_id = metadata.get("creation_canonical_snapshot_id")
        if metadata.get("conception_approved") is True and canonical_id:
            try:
                source = await session.get(PlanningArtifactVersionModel, UUID(str(canonical_id)))
                if (source is not None and source.project_id == project.id
                    and str(source.id) == str(canonical_id)
                    and source.artifact_type == "conception_snapshot"):
                    saved = {"status": source.status, "content": source.content}
                    canonical_premise_verified = bool(
                        canonical_snapshot_premise(saved)
                        and canonical_snapshot_content_hash(saved)
                        == metadata.get("creation_canonical_snapshot_hash")
                        and int(source.content["concept_contract"]["target_chapters"]) == target_chapters
                    )
            except (ValueError, TypeError, KeyError):
                canonical_premise_verified = False

    # ---- 阶段状态 ----
    runs = overview.get("runs", [])
    run_activity = [
        _workflow_activity_evidence(r, latest_step_at=r.get("latest_step_at")) for r in runs
    ]
    activity = _aggregate_workflow_activity(run_activity)
    active_runs = [r for r, state in zip(runs, run_activity) if state["execution_active"]]
    latest_run = active_runs[0] if active_runs else (runs[0] if runs else None)
    active = bool(active_runs)
    failed_run = bool(latest_run and latest_run.get("status") == "failed")

    phases: list[dict[str, object]] = []
    current_marked = False
    for key, label, atypes in _EXECUTION_PHASES:
        if key == "premise":
            done = canonical_premise_verified or "premise" in count_by_type
        elif key == "materialize":
            done = chapters_total > 0
        elif key == "writing":
            done = chapters_written > 0 and chapters_written >= target_chapters > 0
        elif key == "export":
            done = chapters_written > 0 and len(exported_numbers) == chapters_written
        else:
            done = any(t in count_by_type for t in atypes)
        detail = ""
        if key == "premise" and canonical_premise_verified:
            detail = "canonical 构思已采用；基础规划按各自落库产物判定"
        if key == "story_design_kernel" and "story_design_kernel" in latest_by_type:
            content = latest_by_type["story_design_kernel"][1]
            meta = content.get("oracle_meta") if isinstance(content, dict) else None
            beats = len(content.get("beat_schedule") or []) if isinstance(content, dict) else 0
            plots = len(content.get("plot_tree") or []) if isinstance(content, dict) else 0
            if isinstance(meta, dict):
                src = meta.get("source", "?")
                detail = f"AI推演({src}):{beats} beat / {plots} 线"
                if meta.get("needs_enrichment"):
                    detail += "(草稿待升级)"
            else:
                detail = f"{beats} beat / {plots} 线"
        if key == "writing":
            detail = f"已写 {chapters_written}/{target_chapters or '—'} 章"
        elif key == "materialize" and chapters_total:
            detail = f"已建 {chapters_total} 章"
        elif key == "export":
            detail = f"已采用正文导出 {len(exported_numbers)}/{chapters_written} 章"

        status = "done" if done else "pending"
        if not done and not current_marked:
            if failed_run:
                status = "failed"
            elif active:
                status = "running"
            current_marked = True
        phases.append({"key": key, "label": label, "status": status, "detail": detail})

    knowledge = adoption["knowledge"]
    phases.append({
        "key": "knowledge", "label": "知识入库",
        "status": "done" if knowledge["status"] == "closed" else "pending",
        "detail": f"知识入库{knowledge['closed_chapters']}/{knowledge['adopted_chapters']}（{knowledge['label']}）",
    })

    # ---- 已产出规划产物清单 ----
    artifacts = [
        {
            "type": atype,
            "label": _ARTIFACT_LABELS.get(atype, atype),
            "count": count_by_type[atype],
            "version": latest_by_type[atype][0],
        }
        for atype in sorted(count_by_type)
    ]

    # ---- 最近步骤流水(跨所有 run,按时间倒序)----
    recent_steps = overview["recent_steps"]

    # ---- 当前活动 + 健康 ----
    health = "unknown"  # absence of failures is not positive health evidence
    if failed_run:
        health = "failed"
    elif any(int(r.get("failed_step_count") or 0) > 0 for r in runs):
        health = "warning"
    elif activity["status"] == "stale":
        health = "warning"
    current = {
        "workflow_type": latest_run.get("workflow_type") if latest_run else None,
        "status": latest_run.get("status") if latest_run else None,
        "activity_status": _workflow_activity_evidence(
            latest_run, latest_step_at=latest_run.get("latest_step_at"),
        )["status"] if latest_run else "inactive",
        "execution_active": active,
        "current_step": latest_run.get("current_step") if latest_run else None,
        "updated_at": latest_run.get("updated_at") if latest_run else None,
        "error": latest_run.get("error_message") if latest_run else None,
    }

    return {
        "project": {
            "slug": project_slug,
            "title": getattr(project, "title", project_slug),
            "status": getattr(project, "status", None),
            "target_chapters": target_chapters,
            "chapters_total": chapters_total,
            "chapters_written": chapters_written,
            **_adoption_projection_fields(adoption),
        },
        "phases": phases,
        "knowledge": knowledge,
        "artifacts": artifacts,
        "current": current,
        "recent_steps": recent_steps,
        "health": health,
        "execution_active": active,
        "activity_status": activity["status"],
        "workflow_activity": activity,
        "evidence_status": "stale_workflow" if activity["status"] == "stale" else "workflow_record" if runs else "no_workflow",
        "run_count": overview.get("run_count", 0),
    }


async def _load_pipeline_flow_payload(
    settings: AppSettings,
    project_slug: str,
    task_manager: WebTaskManager,
) -> dict[str, object]:
    from bestseller.services.pipeline_flow_overview import build_pipeline_flow_overview

    task_timeline: list[dict[str, object]] = []
    for task in task_manager.list_tasks():
        if str(task.get("project_slug") or "") != project_slug:
            continue
        events = task.get("progress_events") or []
        if isinstance(events, list):
            for event in events[-40:]:
                if isinstance(event, dict):
                    task_timeline.append(
                        {
                            "task_id": task.get("task_id"),
                            "ts": event.get("ts"),
                            "stage": event.get("stage"),
                            "message": event.get("message"),
                            "data": event.get("data"),
                        }
                    )
        break

    async with session_scope(settings) as session:
        project = await get_project_by_slug(session, project_slug)
        if project is None:
            raise ValueError(f"Project '{project_slug}' was not found.")
        adoption = await _load_adopted_chapter_progress(
            session, [project.id], project_languages={project.id: project.language},
        )
        overview = await build_pipeline_flow_overview(
            session,
            project_slug,
            settings=settings,
            task_timeline=task_timeline,
            adoption_progress=adoption[project.id],
        )
    return overview.model_dump(mode="json")


async def _load_project_conception_options_payload(
    settings: AppSettings,
    project_slug: str,
) -> dict[str, object]:
    """Expose only the current project-bound conception snapshot."""
    from sqlalchemy import select

    async with session_scope(settings) as session:
        project = await get_project_by_slug(session, project_slug)
        if project is None:
            raise ValueError(f"Project '{project_slug}' was not found.")
        current_snapshot_id = str(
            (project.metadata_json or {}).get("conception_snapshot_id") or ""
        )
        if not current_snapshot_id:
            raise ValueError("Project has no current conception snapshot")
        snapshot = await session.scalar(
            select(PlanningArtifactVersionModel)
            .where(
                PlanningArtifactVersionModel.id == UUID(current_snapshot_id),
                PlanningArtifactVersionModel.project_id == project.id,
                PlanningArtifactVersionModel.artifact_type == "conception_snapshot",
            )
            .with_for_update()
        )
        if snapshot is None:
            raise ValueError("Current conception snapshot is missing for this project")
        authoritative_seed = None
        intent_id = str((project.metadata_json or {}).get("creation_intent_snapshot_id") or "")
        intent_hash = str((project.metadata_json or {}).get("creation_intent_contract_hash") or "")
        if intent_id and intent_hash:
            intent = await session.scalar(
                select(PlanningArtifactVersionModel).where(
                    PlanningArtifactVersionModel.id == UUID(intent_id),
                    PlanningArtifactVersionModel.project_id == project.id,
                    PlanningArtifactVersionModel.artifact_type == "creation_intent",
                )
            )
            from bestseller.services.conception_snapshots import snapshot_hash

            snapshot_intent_hash = str((snapshot.content or {}).get("creation_intent_hash") or "")
            if (
                intent is not None
                and intent.status == "validated"
                and intent.schema_version == "creation-intent.v1"
                and snapshot_intent_hash == intent_hash == snapshot_hash(intent.content)
            ):
                intent_content = intent.content if isinstance(intent.content, Mapping) else {}
                authoritative_seed = str(intent_content.get("concept_seed") or "").strip() or None
        conception_log = None
        run = (
            await session.scalar(
                select(WorkflowRunModel).where(WorkflowRunModel.id == snapshot.source_run_id)
            )
            if snapshot.source_run_id
            else None
        )
        log_path_value = None
        if run is not None and isinstance(run.metadata_json, Mapping):
            log_path_value = run.metadata_json.get("conception_log_path")
        if log_path_value:
            base = Path(settings.output.base_dir).resolve() / "conception-logs"
            candidate_path = Path(str(log_path_value)).resolve()
            if candidate_path.is_relative_to(base) and candidate_path.is_file():
                try:
                    parsed = json.loads(candidate_path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    parsed = None
                if isinstance(parsed, Mapping):
                    conception_log = parsed
        return build_conception_options(
            project={
                "slug": project.slug,
                "lock_version": project.lock_version,
                "target_chapters": project.target_chapters,
            },
            snapshot={
                "id": str(snapshot.id),
                "status": snapshot.status,
                "version_no": snapshot.version_no,
                "source_run_id": str(snapshot.source_run_id) if snapshot.source_run_id else None,
                "content": snapshot.content,
            },
            conception_log=conception_log,
            authoritative_seed=authoritative_seed,
        )


async def _load_project_design_dossier_payload(
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
        prewrite_review = await load_prewrite_review_payload(session, project_slug)
        style_guide = await session.get(StyleGuideModel, project.id)
        writing_profile = get_project_writing_profile(project, style_guide).model_dump(mode="json")
        from sqlalchemy import select

        artifact_rows = await session.execute(
            select(
                PlanningArtifactVersionModel.id,
                PlanningArtifactVersionModel.artifact_type,
                PlanningArtifactVersionModel.scope_ref_id,
                PlanningArtifactVersionModel.version_no,
                PlanningArtifactVersionModel.status,
                PlanningArtifactVersionModel.schema_version,
                PlanningArtifactVersionModel.created_at,
                PlanningArtifactVersionModel.notes,
            )
            .where(PlanningArtifactVersionModel.project_id == project.id)
            .order_by(
                PlanningArtifactVersionModel.artifact_type.asc(),
                PlanningArtifactVersionModel.version_no.desc(),
                PlanningArtifactVersionModel.created_at.desc(),
            )
        )
        workflow_rows = await session.execute(
            select(
                WorkflowRunModel.id,
                WorkflowRunModel.status,
                WorkflowRunModel.created_at,
            )
            .where(WorkflowRunModel.project_id == project.id)
            .order_by(WorkflowRunModel.created_at.desc())
        )
        artifact_documents: list[dict[str, object]] = []
        for row in artifact_rows:
            artifact_type = str(row.artifact_type)
            artifact_documents.append(
                {
                    "artifact_id": str(row.id),
                    "artifact_type": artifact_type,
                    "label": _design_artifact_label(artifact_type),
                    "phase": _design_artifact_phase(artifact_type),
                    "version_no": row.version_no,
                    "scope_ref_id": str(row.scope_ref_id) if row.scope_ref_id else None,
                    "status": row.status,
                    "schema_version": row.schema_version,
                    "created_at": row.created_at.isoformat(),
                    "notes": row.notes,
                }
            )
        workflow_documents = list(workflow_rows)
        latest_workflow = workflow_documents[0] if workflow_documents else None

    structure_payload = structure.model_dump(mode="json")
    story_bible_payload = story_bible.model_dump(mode="json")
    narrative_payload = narrative.model_dump(mode="json")
    readiness = _build_design_dossier_readiness(
        planning_documents=artifact_documents,
        structure=structure_payload,
        story_bible=story_bible_payload,
        narrative=narrative_payload,
    )
    conception_artifacts = (project.metadata_json or {}).get("conception_artifacts") or {}
    # Planning-stage quality signals (judge verdicts / gate reports) flattened
    # for the 规划产物 tab so质量 can be assessed without digging through JSON.
    from bestseller.services.design_quality import build_design_quality_reports

    quality_reports = build_design_quality_reports(project.metadata_json)
    # Curated views for conception-phase artifacts (story_appeal / hook_candidates
    # / commercial_brief / concept_methodology) so they render readably like the
    # planning artifacts instead of as a raw nested JSON dump.
    from bestseller.services.artifact_view import build_artifact_view

    conception_views = {
        key: build_artifact_view(key, value) for key, value in conception_artifacts.items()
    }
    # 榜单对标回归（P4.2 产物，advisory）：最近一次 vs 真书 Arena win-rate。
    try:
        from bestseller.services.benchmark_regression import load_benchmark_report

        benchmark_report = load_benchmark_report(
            project.slug, output_base_dir=settings.output.base_dir
        )
    except Exception:
        benchmark_report = None
    return {
        "generated_at": _utc_now(),
        "benchmark_report": benchmark_report,
        # Conception-phase artifacts (heat search / methodology / hook candidates
        # / commercial brief) persisted in project metadata. Surfaced inline so
        # the book document view can render them as a "构思产物" group.
        "conception_artifacts": conception_artifacts,
        "conception_views": conception_views,
        "quality_reports": quality_reports,
        "project": {
            "id": str(project.id),
            "slug": project.slug,
            "title": project.title,
            "stored_title": project.title,
            "genre": project.genre,
            "sub_genre": project.sub_genre,
            "audience": project.audience,
            "status": project.status,
            "target_word_count": project.target_word_count,
            "target_chapters": project.target_chapters,
            "author_display_name": _project_author_display_name(project),
            "current_volume_number": project.current_volume_number,
            "current_chapter_number": project.current_chapter_number,
            "synopsis": (project.metadata_json or {}).get("synopsis", ""),
            "tags": (project.metadata_json or {}).get("tags", []),
            "premise": (project.metadata_json or {}).get("premise", ""),
        },
        "summary": {
            "structure_summary": {
                "total_chapters": structure.total_chapters,
                "total_scenes": structure.total_scenes,
                "volume_count": len(structure.volumes),
            },
            "story_bible_counts": {
                "has_world_backbone": story_bible.world_backbone is not None,
                "world_rule_count": len(story_bible.world_rules),
                "location_count": len(story_bible.locations),
                "faction_count": len(story_bible.factions),
                "character_count": len(story_bible.characters),
                "relationship_count": len(story_bible.relationships),
                "volume_frontier_count": len(story_bible.volume_frontiers),
                "deferred_reveal_count": len(story_bible.deferred_reveals),
                "expansion_gate_count": len(story_bible.expansion_gates),
            },
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
                "run_count": len(workflow_documents),
                "completed_run_count": sum(
                    1 for row in workflow_documents if row.status == "completed"
                ),
                "failed_run_count": sum(1 for row in workflow_documents if row.status == "failed"),
                "latest_run_id": str(latest_workflow.id) if latest_workflow else None,
                "latest_run_status": latest_workflow.status if latest_workflow else None,
            },
            "world_expansion": _resolve_story_bible_progress(
                story_bible,
                current_chapter_number=int(project.current_chapter_number or 0),
            ),
            "repair_status": {},
        },
        "readiness": readiness,
        "prewrite_review": prewrite_review,
        "planning_artifacts": {
            **_planning_artifact_stats(artifact_documents),
            "documents": artifact_documents,
        },
        "writing_profile": writing_profile,
        "listing_profile": {},
        "structure": structure_payload,
        "story_bible": story_bible_payload,
        "narrative": narrative_payload,
        "workflow": {
            "run_count": len(workflow_documents),
            "latest_run_id": str(latest_workflow.id) if latest_workflow else None,
            "latest_run_status": latest_workflow.status if latest_workflow else None,
        },
    }


async def _load_project_design_artifact_payload(
    settings: AppSettings,
    project_slug: str,
    artifact_id: str,
) -> dict[str, object]:
    try:
        artifact_uuid = UUID(str(artifact_id))
    except ValueError as exc:
        raise ValueError("Invalid planning artifact id.") from exc
    async with session_scope(settings) as session:
        project = await get_project_by_slug(session, project_slug)
        if project is None:
            raise ValueError(f"Project '{project_slug}' was not found.")
        from sqlalchemy import select

        artifact = await session.scalar(
            select(PlanningArtifactVersionModel).where(
                PlanningArtifactVersionModel.project_id == project.id,
                PlanningArtifactVersionModel.id == artifact_uuid,
            )
        )
        if artifact is None:
            raise ValueError(
                f"Planning artifact '{artifact_id}' was not found for '{project_slug}'."
            )
        from bestseller.services.artifact_view import build_artifact_view

        return {
            "artifact_id": str(artifact.id),
            "artifact_type": artifact.artifact_type,
            "label": _design_artifact_label(artifact.artifact_type),
            "phase": _design_artifact_phase(artifact.artifact_type),
            "version_no": artifact.version_no,
            "scope_ref_id": str(artifact.scope_ref_id) if artifact.scope_ref_id else None,
            "status": artifact.status,
            "schema_version": artifact.schema_version,
            "source_run_id": str(artifact.source_run_id) if artifact.source_run_id else None,
            "notes": artifact.notes,
            "created_at": artifact.created_at.isoformat(),
            "created_by": artifact.created_by,
            "content": artifact.content,
            # Curated, methodology-grounded view-model rendered readably by the
            # design dossier (raw ``content`` stays available behind a fold).
            "view": build_artifact_view(artifact.artifact_type, artifact.content),
        }


def _reader_review_golden_finger(cast_content: object) -> str:
    """Pull the protagonist golden finger out of a cast_spec artifact, defensively."""

    if not isinstance(cast_content, Mapping):
        return ""
    for path in (("golden_finger",), ("protagonist", "golden_finger")):
        node: object = cast_content
        for key in path:
            node = node.get(key) if isinstance(node, Mapping) else None
        if isinstance(node, str) and node.strip():
            return node.strip()
    return ""


def _reader_review_protagonist_archetype(cast_content: object) -> str:
    if not isinstance(cast_content, Mapping):
        return ""
    for path in (("protagonist_archetype",), ("protagonist", "archetype")):
        node: object = cast_content
        for key in path:
            node = node.get(key) if isinstance(node, Mapping) else None
        if isinstance(node, str) and node.strip():
            return node.strip()
    return ""


def _reader_review_power_signature(world_content: object) -> str:
    """First few power-system tiers as a one-line signature for the sameness table."""

    if not isinstance(world_content, Mapping):
        return ""
    power = world_content.get("power_system")
    tiers: list[str] = []
    if isinstance(power, Mapping):
        raw = power.get("tiers")
        if isinstance(raw, list):
            tiers = [str(t).strip() for t in raw if str(t).strip()]
    return "·".join(tiers[:3])


async def _reader_review_book_skeleton(
    session: object,
    project: object,
) -> dict[str, object]:
    """Per-book 套路骨架 for the cross-book sameness table (screen ⑤)."""

    from sqlalchemy import select

    from bestseller.domain.enums import ArtifactType
    from bestseller.infra.db.models import ChapterModel
    from bestseller.services.reader_review import classify_golden_finger_type
    from bestseller.services.workflows import get_latest_planning_artifact

    cast = await get_latest_planning_artifact(
        session, project_id=project.id, artifact_type=ArtifactType.CAST_SPEC
    )
    world = await get_latest_planning_artifact(
        session, project_id=project.id, artifact_type=ArtifactType.WORLD_SPEC
    )
    cast_content = cast.content if cast is not None else None
    world_content = world.content if world is not None else None
    meta = project.metadata_json or {}
    chapter_one = await session.scalar(
        select(ChapterModel).where(
            ChapterModel.project_id == project.id,
            ChapterModel.chapter_number == 1,
        )
    )
    gf_text = _reader_review_golden_finger(cast_content)
    if not gf_text:
        meta_gf = meta.get("golden_finger")
        gf_text = meta_gf.strip() if isinstance(meta_gf, str) else ""
    if not gf_text:
        gf_text = str(meta.get("premise") or meta.get("logline") or "").strip()
    return {
        "slug": project.slug,
        "title": project.title,
        "protagonist_archetype": _reader_review_protagonist_archetype(cast_content),
        "golden_finger_type": classify_golden_finger_type(gf_text),
        "opening_archetype": (chapter_one.opening_archetype if chapter_one else "") or "",
        "power_system_signature": _reader_review_power_signature(world_content),
        "ch1_hook_type": (chapter_one.hook_type if chapter_one else "") or "",
    }


def _reader_review_chapter_dict(chapter: object) -> dict[str, object]:
    return {
        "chapter_number": chapter.chapter_number,
        "title": chapter.title,
        "chapter_goal": chapter.chapter_goal,
        "opening_situation": chapter.opening_situation,
        "main_conflict": chapter.main_conflict,
        "opening_archetype": chapter.opening_archetype,
        "ending_cliff_type": chapter.ending_cliff_type,
        "hook_description": chapter.hook_description,
        "primary_emotion": chapter.primary_emotion,
        "hype_type": chapter.hype_type,
    }


def _reader_review_scene_dict(scene: object) -> dict[str, object]:
    return {
        "scene_number": scene.scene_number,
        "title": scene.title,
        "scene_type": scene.scene_type,
        "time_label": scene.time_label,
        "participants": list(scene.participants or []),
        "purpose": dict(scene.purpose or {}),
        "entry_state": dict(scene.entry_state or {}),
        "exit_state": dict(scene.exit_state or {}),
        "key_dialogue_beats": list(scene.key_dialogue_beats or []),
        "sensory_anchors": dict(scene.sensory_anchors or {}),
        "hook_requirement": scene.hook_requirement,
    }


async def _load_reader_review_payload(
    settings: AppSettings,
    project_slug: str,
    *,
    recent_books: int = 8,
) -> dict[str, object]:
    """Assemble the 5-screen pre-write reader-review payload (DB → reader_review).

    Pure read layer: loads existing artifacts and renders them as readable story.
    Does not run the LLM (arena win-rate is read from the stored benchmark report
    if present); generation logic is never touched.
    """

    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    from bestseller.domain.enums import ArtifactType
    from bestseller.infra.db.models import ChapterModel, ProjectModel
    from bestseller.services.premise_appeal_arena import resolve_reference_set
    from bestseller.services.reader_review import build_reader_review
    from bestseller.services.workflows import get_latest_planning_artifact

    async with session_scope(settings) as session:
        project = await get_project_by_slug(session, project_slug)
        if project is None:
            raise ValueError(f"Project '{project_slug}' was not found.")
        meta = project.metadata_json or {}

        cast = await get_latest_planning_artifact(
            session, project_id=project.id, artifact_type=ArtifactType.CAST_SPEC
        )
        world = await get_latest_planning_artifact(
            session, project_id=project.id, artifact_type=ArtifactType.WORLD_SPEC
        )
        # Golden finger: structured cast field first, then the project-metadata
        # top-level field (real books often leave the cast field None and keep
        # the engine described in metadata / the logline).
        golden_finger = _reader_review_golden_finger(cast.content if cast else None)
        if not golden_finger:
            meta_gf = meta.get("golden_finger")
            golden_finger = meta_gf.strip() if isinstance(meta_gf, str) else ""
        concept_text = str(meta.get("premise") or meta.get("logline") or "").strip()
        # Power system: metadata copy first (richer tier list), then world_spec.
        power_system = meta.get("power_system")
        if not isinstance(power_system, Mapping):
            power_system = None
        if power_system is None and world is not None and isinstance(world.content, Mapping):
            power_system = world.content.get("power_system")

        golden_chapters = (
            await session.scalars(
                select(ChapterModel)
                .where(
                    ChapterModel.project_id == project.id,
                    ChapterModel.chapter_number <= 3,
                )
                .options(selectinload(ChapterModel.scenes))
                .order_by(ChapterModel.chapter_number.asc())
            )
        ).all()
        golden_three = [
            (
                _reader_review_chapter_dict(ch),
                [
                    _reader_review_scene_dict(s)
                    for s in sorted(ch.scenes, key=lambda sc: sc.scene_number)
                ],
            )
            for ch in golden_chapters
        ]

        rhythm_rows = await session.execute(
            select(
                ChapterModel.chapter_number,
                ChapterModel.hype_type,
                ChapterModel.hype_intensity,
                ChapterModel.primary_emotion,
            )
            .where(ChapterModel.project_id == project.id)
            .order_by(ChapterModel.chapter_number.asc())
        )
        rhythm_chapters = [
            {
                "chapter_number": row.chapter_number,
                "hype_type": row.hype_type,
                "hype_intensity": row.hype_intensity,
                "primary_emotion": row.primary_emotion,
            }
            for row in rhythm_rows
        ]

        recent_projects = (
            await session.scalars(
                select(ProjectModel)
                .order_by(ProjectModel.created_at.desc())
                .limit(max(1, recent_books))
            )
        ).all()
        if all(p.id != project.id for p in recent_projects):
            recent_projects = [project, *recent_projects][: max(1, recent_books)]
        skeletons = [await _reader_review_book_skeleton(session, p) for p in recent_projects]

    refs = resolve_reference_set(project.genre, project.sub_genre, min_refs=3)
    arena_win_rate: float | None = None
    try:
        from bestseller.services.benchmark_regression import load_benchmark_report

        report = load_benchmark_report(project.slug, output_base_dir=settings.output.base_dir)
        if isinstance(report, Mapping):
            raw_rate = report.get("win_rate")
            if raw_rate is not None:
                arena_win_rate = float(raw_rate)
    except Exception:
        arena_win_rate = None

    return build_reader_review(
        project={
            "slug": project.slug,
            "title": project.title,
            "genre": project.genre,
            "sub_genre": project.sub_genre,
            "premise": meta.get("premise") or meta.get("logline") or "",
            "synopsis": meta.get("synopsis", ""),
        },
        golden_finger=golden_finger,
        concept_text=concept_text,
        power_system=power_system,
        golden_three_chapters=golden_three,
        rhythm_chapters=rhythm_chapters,
        reference_blurbs=refs,
        arena_win_rate=arena_win_rate,
        cross_book_skeletons=skeletons,
    )


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
        "min_chars": 80,
        "system": (
            "你是资深网文上架编辑。请用 100-200 字写一段短简介：先落到主角当下的"
            "具体困境，再用一句白话讲清能力与代价，最后留下必须做出的选择。因果必须"
            "连贯，不罗列设定，不使用策划术语、空泛宣传语或低俗称呼。只输出正文。"
        ),
    },
    "shelf_intro": {
        "label": "上架简介（500 字）",
        "kind": "text",
        "max_chars": 500,
        "min_chars": 120,
        "system": (
            "你是资深网文上架编辑。请写 160-300 字的中文上架简介。第一段直接呈现"
            "主角遭遇的具体冲突；第二段只用白话解释一次核心能力、限制和代价；结尾落到"
            "一个迫在眉睫且不可回避的选择。每句都要推动因果，不罗列世界观，不复述策划"
            "说明，不堆专有名词，不用低俗称呼、猎奇生理化表达或泛泛反问。只输出正文。"
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
        "system": ("候选书名由平台化书名工作流确定性生成。此模块不会调用大模型生成覆盖标题。"),
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
    metadata = getattr(project, "metadata_json", None)
    if not isinstance(metadata, dict):
        metadata = {}
    premise = str(metadata.get("premise") or "").strip()
    spine = metadata.get("story_spine")
    if not isinstance(spine, dict):
        spine = {}
    spine_summary = "\n".join(
        f"- {label}：{str(spine.get(key) or '').strip()}"
        for key, label in (
            ("who", "主角"),
            ("wants", "目标"),
            ("why_now", "为什么是现在"),
            ("against", "阻力"),
            ("stakes", "失败代价"),
            ("question", "核心悬念"),
        )
        if str(spine.get(key) or "").strip()
    )
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
        f"故事前提：{(premise or '—')[:500]}\n"
        f"故事脊柱：\n{spine_summary or '- 暂无'}\n"
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
            # Normalise whitespace/truncation the same way build_book_listing_profile
            # does downstream (via _limit_chars), so the post-save equality check
            # below compares like-for-like instead of raw-vs-normalised text.
            text = _listing_limit_chars(text, max_chars=max_chars or 1_000_000)
            min_chars = int(spec.get("min_chars") or 1)
            if len(text) < min_chars:
                raise ValueError(f"listing.{module} is too short: {len(text)} < {min_chars}")
            return text
        if kind == "json_list":
            return _parse_json_list_response(raw_value)
        if kind == "json_titles":
            return _parse_json_titles_response(raw_value)
        raise RuntimeError(f"unknown module kind: {kind}")

    try:
        parsed_value = _parse_listing_module(raw)
    except Exception as exc:
        from bestseller.services.llm_closed_loop import (
            build_repair_user_prompt,
            findings_from_exception,
        )

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
    reflected_value = listing_after.get(module)
    changed = reflected_value != listing_before.get(module)
    if module == "shelf_intro" and reflected_value != parsed_value:
        raise RuntimeError("regenerated shelf intro was saved but not reflected by listing profile")
    if not changed:
        raise RuntimeError(f"regenerated listing module '{module}' did not change")
    return {
        "module": module,
        "label": spec["label"],
        "value": parsed_value,
        "model": result.model_name,
        "input_tokens": result.input_tokens,
        "output_tokens": result.output_tokens,
        "changed": changed,
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
        if not _quickstart_payload_has_genre(inner_payload):
            raise ValueError("请先选择题材(选一个频道·题材·子题材,或一张热门开局模板)。")
    else:
        raise ValueError(f"Unsupported task_type {task_type!r}")

    project_slug = (
        (str(inner_payload.get("slug") or "") or None) if task_type == "autowrite" else None
    )
    title = (
        (str(inner_payload.get("title") or "") or None)
        if task_type == "autowrite"
        else (
            str(inner_payload.get("genre_key") or "")
            or str((inner_payload.get("selection") or {}).get("genre") or "")
            or None
        )
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


def _read_design_dossier_html() -> str:
    if _DESIGN_DOSSIER_HTML_PATH.exists():
        return _DESIGN_DOSSIER_HTML_PATH.read_text(encoding="utf-8")
    return "<!DOCTYPE html><html><body><h1>Design dossier page not found.</h1></body></html>"


def _read_pipeline_flow_html() -> str:
    if _PIPELINE_FLOW_HTML_PATH.exists():
        return _PIPELINE_FLOW_HTML_PATH.read_text(encoding="utf-8")
    return "<!DOCTYPE html><html><body><h1>Pipeline flow page not found.</h1></body></html>"


def _read_character_network_html() -> str:
    if _CHARACTER_NETWORK_HTML_PATH.exists():
        return _CHARACTER_NETWORK_HTML_PATH.read_text(encoding="utf-8")
    return "<!DOCTYPE html><html><body><h1>Character network page not found.</h1></body></html>"


def _read_reader_review_html() -> str:
    if _READER_REVIEW_HTML_PATH.exists():
        return _READER_REVIEW_HTML_PATH.read_text(encoding="utf-8")
    return "<!DOCTYPE html><html><body><h1>Reader review page not found.</h1></body></html>"


def _read_methodology_course_html() -> str:
    if _METHODOLOGY_COURSE_HTML_PATH.exists():
        return _METHODOLOGY_COURSE_HTML_PATH.read_text(encoding="utf-8")
    return "<!DOCTYPE html><html><body><h1>Methodology course page not found.</h1></body></html>"


def _read_architecture_course_html() -> str:
    if _ARCHITECTURE_COURSE_HTML_PATH.exists():
        return _ARCHITECTURE_COURSE_HTML_PATH.read_text(encoding="utf-8")
    return "<!DOCTYPE html><html><body><h1>Architecture course page not found.</h1></body></html>"


def _methodology_course_config(language: str = "zh") -> dict[str, str | Path]:
    if language == "en":
        return {
            "language": "en",
            "path": _METHODOLOGY_COURSE_EN_MD_PATH,
            "fallback_title": "The Story Engine Method",
            "missing_markdown": (
                "# The Story Engine Method\n\n"
                "The English methodology document has not been generated yet. "
                "Create `docs/novel-writing-methodology-course.en.md` first."
            ),
            "base_path": "/methodology-course-en",
        }
    return {
        "language": "zh",
        "path": _METHODOLOGY_COURSE_MD_PATH,
        "fallback_title": "写小说的方法论",
        "missing_markdown": (
            "# 写小说的方法论\n\n"
            "教材文档尚未生成。请先创建 `docs/novel-writing-methodology-course.md`。"
        ),
        "base_path": "/methodology-course",
    }


def _load_methodology_course_markdown(language: str = "zh") -> tuple[str, str, str | None]:
    config = _methodology_course_config(language)
    doc_path = Path(config["path"])
    if doc_path.exists():
        content_md = doc_path.read_text(encoding="utf-8")
        status = "ready"
        updated_at = datetime.fromtimestamp(doc_path.stat().st_mtime, tz=UTC).isoformat()
    else:
        content_md = str(config["missing_markdown"])
        status = "missing"
        updated_at = None
    return content_md, status, updated_at


def _split_methodology_course_lessons(
    content_md: str,
    language: str = "zh",
) -> tuple[str, str, list[dict[str, object]]]:
    config = _methodology_course_config(language)
    title = str(config["fallback_title"])
    for line in content_md.splitlines():
        if line.startswith("# "):
            title = line.removeprefix("# ").strip() or title
            break

    if language == "en":
        heading_re = re.compile(r"^##\s*Lesson\s*([0-9]{1,2})\s*[：:](.+)$", re.MULTILINE)
    else:
        heading_re = re.compile(
            r"^## 第\s*([0-9０-９]{1,2})\s*[章节期講讲][：:](.+)$",
            re.MULTILINE,
        )
    matches = list(heading_re.finditer(content_md))
    overview_md = content_md[: matches[0].start()].rstrip() if matches else content_md
    lessons: list[dict[str, object]] = []
    base_path = str(config["base_path"])
    for idx, match in enumerate(matches):
        raw_number, lesson_title = match.groups()
        normalized_number = int(
            raw_number.translate(str.maketrans("０１２３４５６７８９", "0123456789"))
        )
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(content_md)
        markdown = content_md[match.start() : end].strip()
        excerpt = ""
        for paragraph in markdown.split("\n\n"):
            text = paragraph.strip()
            if text and not text.startswith("#"):
                excerpt = re.sub(r"\s+", " ", text)
                break
        lessons.append(
            {
                "number": normalized_number,
                "label": f"Lesson {normalized_number:02d}"
                if language == "en"
                else f"第 {normalized_number:02d} 章",
                "title": lesson_title.strip(),
                "anchor": f"lesson-{normalized_number:02d}",
                "path": f"{base_path}/{normalized_number:02d}",
                "markdown": markdown,
                "html": markdown_to_html(markdown, language=language),
                "stats": build_markdown_reading_stats(markdown),
                "excerpt": excerpt[:180],
            }
        )

    return title, overview_md, lessons


def _build_methodology_course_payload(language: str = "zh") -> dict[str, object]:
    config = _methodology_course_config(language)
    doc_path = Path(config["path"])
    content_md, status, updated_at = _load_methodology_course_markdown(language)
    title, overview_md, lessons = _split_methodology_course_lessons(content_md, language)
    return {
        "status": status,
        "language": language,
        "title": title,
        "updated_at": updated_at,
        "lesson_count": len(lessons),
        "lessons": lessons,
        "overview_markdown": overview_md,
        "overview_html": markdown_to_html(overview_md, language=language),
        "markdown": content_md,
        "html": markdown_to_html(content_md, language=language),
        "stats": build_markdown_reading_stats(content_md),
        "source_path": str(doc_path),
    }


def _build_methodology_lesson_payload(
    lesson_number: int, language: str = "zh"
) -> dict[str, object] | None:
    course = _build_methodology_course_payload(language)
    for lesson in course["lessons"]:
        if isinstance(lesson, dict) and lesson.get("number") == lesson_number:
            return {
                "status": course["status"],
                "course_title": course["title"],
                "updated_at": course["updated_at"],
                "lesson_count": course["lesson_count"],
                "lesson": lesson,
                "previous": next(
                    (
                        item
                        for item in course["lessons"]
                        if isinstance(item, dict) and item.get("number") == lesson_number - 1
                    ),
                    None,
                ),
                "next": next(
                    (
                        item
                        for item in course["lessons"]
                        if isinstance(item, dict) and item.get("number") == lesson_number + 1
                    ),
                    None,
                ),
                "source_path": course["source_path"],
            }
    return None


def _build_methodology_broadcast_payload() -> dict[str, object]:
    """Split the broadcast-script doc (`## NN · 标题`) into copyable segments."""
    if _METHODOLOGY_BROADCAST_MD_PATH.exists():
        content_md = _METHODOLOGY_BROADCAST_MD_PATH.read_text(encoding="utf-8")
        status = "ready"
        updated_at = datetime.fromtimestamp(
            _METHODOLOGY_BROADCAST_MD_PATH.stat().st_mtime, tz=UTC
        ).isoformat()
    else:
        content_md = (
            "# 写作方法论 · 口播稿\n\n"
            "口播稿文档尚未生成。请先创建 `docs/口播稿-写作方法论-20260623.md`。"
        )
        status = "missing"
        updated_at = None

    title = "写作方法论 · 口播稿"
    for line in content_md.splitlines():
        if line.startswith("# "):
            title = line.removeprefix("# ").strip() or title
            break

    heading_re = re.compile(r"^##\s*([0-9０-９]{1,2})\s*·\s*(.+)$", re.MULTILINE)
    matches = list(heading_re.finditer(content_md))
    overview_md = content_md[: matches[0].start()].rstrip() if matches else content_md
    segments: list[dict[str, object]] = []
    for idx, match in enumerate(matches):
        raw_number, seg_title = match.groups()
        normalized_number = int(
            raw_number.translate(str.maketrans("０１２３４５６７８９", "0123456789"))
        )
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(content_md)
        markdown = content_md[match.start() : end].strip()
        excerpt = ""
        for paragraph in markdown.split("\n\n"):
            text = paragraph.strip()
            if text and not text.startswith("#"):
                excerpt = re.sub(r"\s+", " ", text)
                break
        segments.append(
            {
                "number": normalized_number,
                "label": f"第 {normalized_number:02d} 集",
                "title": seg_title.strip(),
                "markdown": markdown,
                "html": markdown_to_html(markdown, language="zh"),
                "stats": build_markdown_reading_stats(markdown),
                "excerpt": excerpt[:180],
            }
        )

    return {
        "status": status,
        "title": title,
        "updated_at": updated_at,
        "segment_count": len(segments),
        "segments": segments,
        "overview_markdown": overview_md,
        "overview_html": markdown_to_html(overview_md, language="zh"),
        "markdown": content_md,
        "stats": build_markdown_reading_stats(content_md),
        "source_path": str(_METHODOLOGY_BROADCAST_MD_PATH),
    }


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


_FANQIE_SHORT_EXPORT_RELATIVE_PATH = Path("exports") / "fanqie-short.md"


def _fanqie_short_export_file(output_dir: Path) -> Path | None:
    export_path = output_dir / _FANQIE_SHORT_EXPORT_RELATIVE_PATH
    if export_path.exists() and export_path.is_file():
        return export_path
    return None


def _strip_fanqie_short_reader_markers(content_md: str) -> str:
    """Remove export-only scaffolding from the reader full-text view."""
    import re as _re

    cleaned = _re.sub(
        r"\n{0,2}---\s*\n<!--\s*UNLOCK_LINE:.*?-->\s*\n---\n{0,2}",
        "\n\n",
        content_md,
        flags=_re.DOTALL,
    )
    cleaned = _re.sub(r"(?m)^\s*---+\s*$\n?", "\n", cleaned)
    cleaned = _re.sub(r"\n{3,}", "\n\n", cleaned).strip()

    lines = cleaned.splitlines()
    while lines:
        first = lines[0].strip()
        if not first:
            lines.pop(0)
            continue
        if first.startswith("#"):
            lines.pop(0)
            continue
        unquoted = first.lstrip("> ").strip()
        if (
            "番茄短故事" in unquoted
            or "单篇完结" in unquoted
            or _re.match(r"^(类型|Genre)\s*[:：]", unquoted)
        ):
            lines.pop(0)
            continue
        break
    return _re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def _fanqie_short_toc_entry_from_markdown(content_md: str) -> dict[str, object]:
    stats = build_markdown_reading_stats(content_md)
    return {
        "number": 1,
        "title": "全文",
        "filename": str(_FANQIE_SHORT_EXPORT_RELATIVE_PATH),
        "word_count": stats["word_count"],
        "estimated_read_minutes": stats["estimated_read_minutes"],
        "volume_number": None,
        "volume_title": None,
        "exported": True,
        "status": "complete",
        "production_state": "ok",
        "revision_count": 0,
        "has_draft": True,
        "availability": "available",
        "single_piece": True,
        "content_mode": "fanqie_short_story",
    }


def _fanqie_short_toc_entry(output_dir: Path) -> dict[str, object] | None:
    export_path = _fanqie_short_export_file(output_dir)
    if export_path is None:
        return None
    raw_md = export_path.read_text(encoding="utf-8")
    content_md = _strip_fanqie_short_reader_markers(raw_md)
    return _fanqie_short_toc_entry_from_markdown(content_md)


def _remove_leading_markdown_heading(content_md: str) -> str:
    lines = content_md.strip().splitlines()
    if lines and lines[0].lstrip().startswith("#"):
        return "\n".join(lines[1:]).strip()
    return content_md.strip()


def _load_fanqie_short_draft_text_from_db(
    settings: AppSettings,
    project_slug: str,
) -> str | None:
    """Assemble current short-story segment drafts into one reader body."""
    try:
        from sqlalchemy import select

        from bestseller.infra.db.models import ChapterDraftVersionModel, ChapterModel
        from bestseller.services.drafts import sanitize_novel_markdown_content

        async def _fetch() -> str | None:
            async with session_scope(settings) as sess:
                proj = await get_project_by_slug(sess, project_slug)
                if proj is None or not is_fanqie_short_project(proj):
                    return None
                rows = list(
                    await sess.execute(
                        select(
                            ChapterModel.chapter_number,
                            ChapterDraftVersionModel.content_md,
                        )
                        .join(
                            ChapterDraftVersionModel,
                            ChapterDraftVersionModel.chapter_id == ChapterModel.id,
                        )
                        .where(
                            ChapterModel.project_id == proj.id,
                            ChapterDraftVersionModel.is_current.is_(True),
                        )
                        .order_by(ChapterModel.chapter_number.asc())
                    )
                )
                parts: list[str] = []
                for _chapter_number, content_md in rows:
                    body = sanitize_novel_markdown_content(content_md or "")
                    body = _remove_leading_markdown_heading(body)
                    if body:
                        parts.append(body)
                return "\n\n".join(parts).strip() or None

        return asyncio.run(_fetch())
    except Exception:
        return None


def _load_reader_project_summary(
    settings: AppSettings,
    project_slug: str,
) -> dict[str, object]:
    try:

        async def _fetch() -> dict[str, object]:
            async with session_scope(settings) as sess:
                proj = await get_project_by_slug(sess, project_slug)
                if proj is None:
                    return {
                        "title": project_slug,
                        "genre": "",
                        "language": None,
                        "is_fanqie_short": False,
                        "project_exists": False,
                    }
                return {
                    "title": proj.title or project_slug,
                    "genre": getattr(proj, "genre", "") or "",
                    "language": getattr(proj, "language", None),
                    "is_fanqie_short": is_fanqie_short_project(proj),
                    "project_exists": True,
                }

        return asyncio.run(_fetch())
    except Exception:
        return {
            "title": project_slug,
            "genre": "",
            "language": None,
            "is_fanqie_short": False,
            "project_exists": None,
        }


def _load_fanqie_short_reader_markdown(
    settings: AppSettings,
    project_slug: str,
    output_dir: Path,
    *,
    title: str,
    genre: str,
) -> str | None:
    export_path = _fanqie_short_export_file(output_dir)
    if export_path is not None:
        return _strip_fanqie_short_reader_markers(export_path.read_text(encoding="utf-8"))

    draft_text = _load_fanqie_short_draft_text_from_db(settings, project_slug)
    if not draft_text:
        return None
    return draft_text.strip()


def _fanqie_short_export_title(raw_md: str, fallback: str) -> str:
    for line in raw_md.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            title = stripped.lstrip("#").strip()
            if title:
                return title
        if stripped:
            break
    return fallback


def _plain_text_from_markdown(content_md: str) -> str:
    import re as _re

    text = _re.sub(r"<!--.*?-->", "", content_md, flags=_re.DOTALL)
    text = _re.sub(r"(?m)^\s{0,3}#{1,6}\s*", "", text)
    text = _re.sub(r"[*_`>#\[\]]", "", text)
    text = _re.sub(r"\s+", " ", text)
    return text.strip()


def _limit_text_chars(text: str, limit: int) -> str:
    value = " ".join(str(text or "").split()).strip()
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 1)].rstrip("，。；、 ") + "…"


def _first_sentence_text(text: str, limit: int) -> str:
    import re as _re

    value = " ".join(str(text or "").split()).strip()
    close_bracket = value.find("】")
    if 0 <= close_bracket <= limit + 20:
        return _limit_text_chars(value[: close_bracket + 1], limit)
    match = _re.search(r"[。！？】]", value)
    if match:
        value = value[: match.end()]
    return _limit_text_chars(value, limit)


def _nested_text(data: dict[str, object], *path: str) -> str:
    cur: object = data
    for key in path:
        if not isinstance(cur, dict):
            return ""
        cur = cur.get(key)
    return str(cur or "").strip()


def _project_author_display_name(project: object) -> str:
    meta = (
        getattr(project, "metadata_json", {})
        if isinstance(getattr(project, "metadata_json", {}), dict)
        else {}
    )
    for path in (
        ("author_display_name",),
        ("author_name",),
        ("author",),
        ("pen_name",),
        ("publishing", "amazon_kdp", "author_display_name"),
        ("amazon_kdp", "author_display_name"),
        ("publication", "author_display_name"),
        ("listing_profile", "author_display_name"),
    ):
        value = _nested_text(meta, *path)
        if value:
            return value
    return "未填写"


def _extract_fanqie_short_character_names(
    plain_text: str,
    project: object,
    *,
    limit: int = 8,
) -> list[str]:
    import re as _re

    meta = (
        getattr(project, "metadata_json", {})
        if isinstance(getattr(project, "metadata_json", {}), dict)
        else {}
    )
    protagonist = _nested_text(meta, "book_spec", "protagonist", "name")
    surnames = (
        "赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦尤许何吕施张孔曹严华金魏陶姜"
        "谢邹喻柏窦章云苏潘葛范彭鲁韦马苗方俞任袁柳史唐薛雷贺倪汤滕"
        "罗毕郝安常乐于傅齐康伍余顾孟黄穆萧尹姚邵汪毛米贝明臧成戴"
        "宋庞熊纪舒屈项祝董梁杜阮蓝席季麻强贾路江童颜郭梅盛林钟"
        "徐邱骆高夏蔡田胡霍万卢莫房裘解宗丁宣邓杭洪左石崔龚程邢"
        "裴陆荣翁荀羊惠曲封靳松段侯全秋伊宫宁仇甘厉祖武刘景龙叶"
        "谭劳姬申冉雍桑桂牛边燕尚温庄晏柴阎慕连习艾鱼向古易廖"
    )
    action_suffix = (
        "说|问|看|把|在|是|的|站|笑|脸|拿|冲|发|走|点|握|盯|闭|睁|递|往|推|"
        "打开|没有|已经|终于|猛|坐|扶|赶|转身|忽然|正|要|刚|还|就|又|却|给|"
        "靠|伸|低|抬|接|听|签|扣|追|等|想|当|：|，|。|、|“|”|\\s"
    )
    pattern = _re.compile(rf"([{surnames}][\u4e00-\u9fff]{{1,2}})(?=(?:{action_suffix}))")
    blocked_suffixes = ("公司", "集团", "项目", "系统", "模型", "医院", "公告", "手机", "群")
    blocked_names = {
        "解释",
        "全员群",
        "流程",
        "项目款",
        "发布会",
        "手术",
        "记忆",
        "温暖",
        "恐惧",
        "得意",
        "控制欲",
    }
    verb_suffixes = "站坐看拿说问走笑想把在给让推递握盯闭睁扶赶签扣追正"
    counts: dict[str, int] = {}
    first_pos: dict[str, int] = {}
    if protagonist and protagonist in plain_text:
        counts[protagonist] = plain_text.count(protagonist) + 1000
        first_pos[protagonist] = plain_text.find(protagonist)
    for match in pattern.finditer(plain_text):
        name = match.group(1)
        if len(name) == 3 and name[-1] in verb_suffixes:
            name = name[:2]
        if len(name) < 2 or len(name) > 3:
            continue
        if name in blocked_names or name.startswith(("解", "于")) or name.endswith("总"):
            continue
        if any(name.endswith(item) for item in blocked_suffixes):
            continue
        counts[name] = counts.get(name, 0) + 1
        first_pos.setdefault(name, match.start())
    ordered = [
        item
        for item in sorted(
            counts, key=lambda item: (-counts[item], first_pos.get(item, 10**9), item)
        )
        if counts[item] >= 2 or item == protagonist
    ]
    return ordered[:limit]


def _fanqie_short_tags_from_text(project: object, plain_text: str) -> list[str]:
    meta = (
        getattr(project, "metadata_json", {})
        if isinstance(getattr(project, "metadata_json", {}), dict)
        else {}
    )
    tags: list[str] = []
    for item in (getattr(project, "genre", None), getattr(project, "sub_genre", None)):
        value = str(item or "").strip()
        if value and value not in tags:
            tags.append(value)
    for item in meta.get("tags") if isinstance(meta.get("tags"), list) else []:
        value = str(item or "").strip()
        if value and value in plain_text and value not in tags:
            tags.append(value)
    detectors = [
        ("公司", "职场反击"),
        ("离职", "职场反击"),
        ("公告", "舆论反转"),
        ("全员群", "群聊审判"),
        ("情绪", "情绪操控"),
        ("恐惧", "情绪操控"),
        ("得意", "情绪操控"),
        ("代价", "能力代价"),
        ("记忆", "记忆代价"),
        ("父亲", "亲情牵引"),
        ("医院", "亲情牵引"),
        ("发布会", "发布会翻盘"),
        ("项目款", "公司黑账"),
        ("录音", "证据反杀"),
    ]
    for needle, tag in detectors:
        if needle in plain_text and tag not in tags:
            tags.append(tag)
    for tag in ("番茄短故事", "单篇完结", "快节奏", "打脸", "爽文"):
        if tag not in tags:
            tags.append(tag)
    return tags[:16]


def _build_fanqie_short_title_candidates(
    *,
    title: str,
    protagonist: str,
    tags: list[str],
) -> list[dict[str, object]]:
    hooks = [
        "十秒情绪",
        "记忆代价",
        "上司自爆",
        "公司群审判",
        "公开录音",
        "离职反击",
        "发布会翻盘",
        "黑账自证",
        "父亲手术",
        "情绪爆改",
    ]
    raw_titles = [
        title,
        "全员群把我挂成贪污犯后，我让老板当众自爆",
        "他们逼我背锅，我让整场发布会变成自爆现场",
        "离职当天，全公司听见老板亲口认罪",
        "我被挂上全员群那天，反派开始替我说真话",
        f"{protagonist}的情绪爆改局",
        "十秒情绪爆改，我让上司当场自爆",
        "被挂贪污犯后，我点开了情绪爆改器",
        "公司群审判我，我让反派自己开口",
        "每次打脸，都要忘掉一段亲情",
        "离职当天，我放大了老板的得意",
        "我用十秒恐惧撤回全员公告",
        "发布会前，我让幕后老板自证黑账",
        "父亲手术被卡后，我把公司送上审判台",
    ]
    patterns = [
        "{p}用{h}翻盘",
        "开局被栽赃，我靠{h}反杀",
        "{h}一开，上司自己认账",
        "我把{h}用在公司群",
        "{h}之后，全公司沉默了",
    ]
    for hook in hooks:
        for pattern in patterns:
            raw_titles.append(pattern.format(p=protagonist, h=hook))
    seen: set[str] = set()
    candidates: list[dict[str, object]] = []
    subtitle = "开局即反击，金手指有代价，职场黑账当场反转。"
    for value in raw_titles:
        clean = _limit_text_chars(value, 28)
        if not clean or clean in seen:
            continue
        seen.add(clean)
        idx = len(candidates) + 1
        candidates.append(
            {
                "id": idx,
                "title": clean,
                "subtitle": subtitle,
                "angle": "番茄短故事｜当前全文映射",
                "recommendation": "主推" if idx == 1 else ("广告测试" if idx <= 6 else "备选"),
                "platform": "fanqie_short",
                "platform_label": "番茄短故事",
                "platform_scope": "target_platform",
                "scope_label": "平台专项",
                "platform_tag": "番茄短故事",
                "display_label": "番茄短故事",
                "pattern": "当前正文提取",
                "score": max(72, 98 - idx),
                "fit_notes": [
                    "基于当前导出全文生成。",
                    f"核心标签：{'、'.join(tags[:3])}" if tags else "核心标签来自当前正文。",
                ],
                "risk_flags": [],
                "risk_blocked": False,
            }
        )
        if len(candidates) >= 40:
            break
    return candidates


def _build_fanqie_short_current_listing_profile(
    settings: AppSettings,
    project: object,
) -> dict[str, object] | None:
    output_dir = _project_output_dir(settings, str(getattr(project, "slug", "") or ""))
    export_path = _fanqie_short_export_file(output_dir)
    if export_path is None:
        return None

    raw_md = export_path.read_text(encoding="utf-8")
    title = _fanqie_short_export_title(
        raw_md,
        str(getattr(project, "title", "") or "未命名作品"),
    )
    content_md = _strip_fanqie_short_reader_markers(raw_md)
    plain = _plain_text_from_markdown(content_md)
    reading_stats = build_markdown_reading_stats(content_md)
    names = _extract_fanqie_short_character_names(plain, project)
    protagonist = names[0] if names else "主角"
    other_names = [name for name in names[1:5] if name != protagonist]
    opponents = "、".join(other_names[:3]) or "对手"
    genre = str(getattr(project, "genre", "") or "都市异能").strip()
    sub_genre = str(getattr(project, "sub_genre", "") or "番茄短故事").strip()
    tags = _fanqie_short_tags_from_text(project, plain)
    author = _project_author_display_name(project)
    opening = _first_sentence_text(plain, 86)
    logline = _limit_text_chars(
        f"{protagonist}被公开栽赃后，获得能点选目标并放大情绪的金手指；"
        "每一次让反派自爆，都要付出一段温暖记忆。",
        120,
    )
    shelf_intro = _limit_text_chars(
        f"{protagonist}开局就被推上公开处刑台：{opening} "
        "他获得的金手指不是无代价外挂，而是点选目标、放大情绪，让对方在十几秒内露出破绽；"
        "代价是被抹去一段温暖记忆。"
        f"从公司公告、大堂录音到发布会现场，{protagonist}一边守住父亲手术，一边逼{opponents}在恐惧、得意和控制欲里自证。"
        f"《{title}》主打{genre}短故事的快打脸、连环反转和有代价爽感，爽点来得快，代价也扎得疼。",
        500,
    )
    characters: list[dict[str, object]] = []
    for idx, name in enumerate(names[:8]):
        if idx == 0:
            characters.append(
                {
                    "name": name,
                    "role": "主角",
                    "identity": "被公司公开栽赃的前员工",
                    "appeal": "用有代价的情绪放大能力，把现实羞辱变成公开反击。",
                    "goal": "洗清栽赃、守住父亲手术、逼公司黑账曝光。",
                    "arc_state": "从被动背锅转为主动设局。",
                    "is_pov_character": True,
                }
            )
        else:
            role = "关键对手" if name[0] in "周裴" else "关键角色"
            characters.append(
                {
                    "name": name,
                    "role": role,
                    "identity": "",
                    "appeal": "推动主角反击链条和公开反转。",
                    "goal": "",
                    "arc_state": "当前全文角色",
                    "is_pov_character": False,
                }
            )

    title_candidates = _build_fanqie_short_title_candidates(
        title=title,
        protagonist=protagonist,
        tags=tags,
    )
    profile: dict[str, object] = {
        "schema_version": "fanqie-short-current-1.0",
        "source": "fanqie_short_export",
        "source_files": [str(_FANQIE_SHORT_EXPORT_RELATIVE_PATH)],
        "book_id": str(getattr(project, "slug", "") or ""),
        "target_platform": "番茄短故事",
        "primary_title": title,
        "recommended_subtitle": "十秒情绪爆改，反派当场自证。",
        "author_display_name": author,
        "logline": logline,
        "channel": str(getattr(project, "audience", "") or "番茄短故事读者"),
        "length_type": "番茄短故事 · 单篇完结",
        "serialization_status": str(getattr(project, "status", "") or "draft"),
        "language": str(getattr(project, "language", "") or "zh-CN"),
        "primary_category": genre,
        "secondary_category": sub_genre,
        "tertiary_categories": tags[:6],
        "platform_category_suggestions": {
            "target_platform": [f"番茄短故事/{genre}", f"番茄短故事/{sub_genre}"],
            "general": tags[:8],
        },
        "tags": tags,
        "short_intro": shelf_intro,
        "long_intro": "",
        "shelf_intro": shelf_intro,
        "promo_copy": [
            "开篇50字金手指生效，十秒情绪放大让反派当场露馅。",
            "每一次打脸都要支付记忆代价，爽感快，疼感也真。",
            "公司公告、大堂录音、发布会现场一路翻盘，现实压迫直接变公开审判。",
        ],
        "main_characters": characters,
        "character_names": [item["name"] for item in characters],
        "reader_promise": [
            "开局即冲突，前200字内完成第一次能力反击。",
            "金手指每次使用都有记忆代价，冲突不会变成无脑平推。",
            "职场栽赃、医院压力、发布会翻盘连续推进，爽点密度高。",
            "反派不是被作者按头认输，而是在被放大的情绪里自己露出破绽。",
            "单篇完结，主线收束，不拆成连载章节阅读。",
        ],
        "not_recommended_categories": [],
        "title_candidates": title_candidates,
        "title_workflow": {
            "candidate_source": "fanqie_short_current_export",
            "candidate_policy": "current_story_only",
            "candidate_count": len(title_candidates),
            "platform_label": "番茄短故事",
            "candidates": title_candidates,
        },
        "copy_pack": {
            "title": title,
            "subtitle": "十秒情绪爆改，反派当场自证。",
            "author": author,
            "book_id": str(getattr(project, "slug", "") or ""),
            "category": " / ".join(item for item in [genre, sub_genre] if item),
            "tags": "、".join(tags),
            "character_names": "、".join(item["name"] for item in characters),
            "shelf_intro": shelf_intro,
        },
        "marketing_assets": {
            "short_video_scripts": [
                {
                    "duration_seconds": 15,
                    "angle": "goldfinger_hook",
                    "script": f"{protagonist}被挂成贪污犯，手机弹出情绪爆改器：点一下，反派十秒内自己露馅。",
                },
                {
                    "duration_seconds": 45,
                    "angle": "cost_and_payoff",
                    "script": "他能放大恐惧、得意和控制欲，但每次反击都会丢掉一段温暖记忆。爽点越狠，代价越疼。",
                },
                {
                    "duration_seconds": 90,
                    "angle": "conflict_chain",
                    "script": f"从全员群公告到发布会现场，{protagonist}把职场栽赃、医院押金和公司黑账连成反击链。",
                },
            ],
            "material_slots": ["开篇反击", "大堂录音", "记忆代价", "发布会翻盘", "结尾收束"],
            "recommended_cadence": "single-story launch",
        },
        "ip_readiness": {
            "score": 100,
            "status": "ready",
            "checks": [],
            "visual_motifs": [
                "黑屏提示",
                "全员群公告",
                "录音红点",
                "发布会大屏",
                "被抹掉的温暖记忆",
            ],
            "character_tags": characters[:5],
        },
        "current_export": {
            "filename": str(_FANQIE_SHORT_EXPORT_RELATIVE_PATH),
            "word_count": int(reading_stats["word_count"] or 0),
            "estimated_read_minutes": int(reading_stats["estimated_read_minutes"] or 0),
            "modified_at": datetime.fromtimestamp(export_path.stat().st_mtime, UTC).isoformat(),
        },
    }
    profile["compliance"] = validate_book_listing_profile(profile)  # type: ignore[arg-type]
    return profile


def _build_fanqie_short_export_task_stats(
    settings: AppSettings,
    project: object,
    *,
    include_chapters: bool,
) -> dict[str, object] | None:
    project_slug = str(getattr(project, "slug", "") or "").strip()
    if not project_slug:
        return None

    output_dir = _project_output_dir(settings, project_slug)
    export_path = _fanqie_short_export_file(output_dir)
    if export_path is None:
        return None

    raw_md = export_path.read_text(encoding="utf-8")
    export_title = _fanqie_short_export_title(
        raw_md,
        str(getattr(project, "title", "") or project_slug),
    )
    content_md = _strip_fanqie_short_reader_markers(raw_md)
    reading_stats = build_markdown_reading_stats(content_md)
    word_count = int(reading_stats["word_count"] or 0)
    completed = 1 if word_count > 0 else 0
    meta = (
        getattr(project, "metadata_json", {})
        if isinstance(getattr(project, "metadata_json", {}), dict)
        else {}
    )
    stat = export_path.stat()
    entry = {
        "number": 1,
        "title": "全文",
        "unit_kind": "single_story",
        "unit_label": "全文",
        "filename": str(_FANQIE_SHORT_EXPORT_RELATIVE_PATH),
        "word_count": word_count,
        "estimated_read_minutes": int(reading_stats["estimated_read_minutes"] or 0),
        "target_word_count": int(getattr(project, "target_word_count", 0) or 0),
        "status": "complete" if word_count > 0 else "empty",
        "single_piece": True,
    }
    return {
        "source": "fanqie_short_export",
        "project_slug": project_slug,
        "project_title": export_title,
        "current_content_title": export_title,
        "author_display_name": _project_author_display_name(project),
        "project_type": getattr(project, "project_type", None),
        "content_mode": meta.get("content_mode") or "fanqie_short_story",
        "platform_key": meta.get("platform_key"),
        "length_key": meta.get("length_key"),
        "unit_kind": "single_story",
        "unit_label": "全文",
        "target_chapters": 1,
        "target_segments": 1,
        "target_word_count": int(getattr(project, "target_word_count", 0) or 0),
        "completed_chapters": completed,
        "completed_segments": completed,
        "word_count_total": word_count,
        "estimated_read_minutes": int(reading_stats["estimated_read_minutes"] or 0),
        "current_export_filename": str(_FANQIE_SHORT_EXPORT_RELATIVE_PATH),
        "current_export_modified_at": datetime.fromtimestamp(stat.st_mtime, UTC).isoformat(),
        "chapters": [entry] if include_chapters else [],
    }


def _reader_chapter_availability(production_state: str | None, word_count: int) -> str:
    if (production_state or "").lower() == "ok" and word_count > 0:
        return "available"
    if word_count > 0:
        return "repair_in_progress"
    return "planned"


_READER_STATE_LABELS: dict[str, str] = {
    "adopted": "已采用",
    "accepted_with_debt": "已采用（含质量债）",
    "formal": "已保存稿",
    "paused": "待编辑",
    "stopped": "已停止",
    "active_repair": "正在修复",
    "queued_repair": "排队修复",
    "blocked": "阻塞待修",
    "quality_debt": "待编辑",
    "drafting": "生成中",
    "planned": "待生成",
}


def _reader_chapter_state(
    *,
    chapter_status: str | None,
    production_state: str | None,
    availability: str | None,
    pending_rewrite_task_count: int = 0,
    auto_repair_in_progress: bool = False,
    production_intent: str | None = None,
    adoption_state: str | None = None,
) -> tuple[str, str]:
    """Return the reader-facing state, separating queued work from live repair.

    The legacy reader collapsed every readable non-``ok`` chapter into
    ``repair``. That was truthful for availability but misleading for progress:
    a blocked chapter, a queued rewrite, and an actively repairing chapter are
    different operational states.
    """

    status = str(chapter_status or "").lower()
    production = str(production_state or "").lower()
    avail = str(availability or "").lower()
    if adoption_state == "accepted_with_debt":
        state = "accepted_with_debt"
    elif adoption_state == "promoted":
        state = "adopted"
    elif production == "ok" or avail == "available":
        state = "formal"
    elif avail == "planned":
        state = "planned"
    elif production_intent == "pause":
        state = "paused"
    elif production_intent == "stop":
        state = "stopped"
    elif auto_repair_in_progress:
        state = "active_repair"
    elif production == "blocked":
        # Pending rewrite records can outlive a failed workflow, without a worker.
        state = "blocked"
    elif pending_rewrite_task_count > 0:
        state = "queued_repair"
    elif production == "quality_debt":
        state = "quality_debt"
    elif status in {"drafting", "review"} or production == "pending":
        state = "drafting"
    else:
        state = "blocked"
    return state, _READER_STATE_LABELS[state]


def _reader_state_payload(chapter: dict[str, object]) -> dict[str, object]:
    """Share automatic-production state between TOC and chapter responses."""
    payload = {
        key: chapter.get(key)
        for key in (
            "reader_state", "reader_label", "pending_rewrite_task_count", "production_intent"
        )
    }
    payload.update({key: chapter[key] for key in (
        "adoption_state", "source_draft_id", "source_body_sha256"
    ) if key in chapter})
    return payload


async def _reader_source_adoption(session, project_id, chapter_id, draft_id, content_md):
    """Classify only the exact displayed draft using read-only database evidence."""
    from bestseller.services.feedback import validate_adopted_knowledge_source

    lineage, rejection = await validate_adopted_knowledge_source(
        session, project_id=project_id, chapter_id=chapter_id,
        source_chapter_draft_version_id=draft_id, chapter_md=content_md, lock=False,
    )
    return {
        "adoption_state": lineage.get("adoption_state") if rejection is None else "not_adopted",
        "source_draft_id": str(draft_id),
        "source_body_sha256": lineage["source_body_sha256"],
    }


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
            RewriteTaskModel,
            SceneCardModel,
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
                from bestseller.services.production_control import load_control_state

                control = await load_control_state(sess, proj.id)
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
                chapter_number_by_id = {str(ch.id): int(ch.chapter_number) for ch in chapter_rows}
                chapter_id_by_number = {int(ch.chapter_number): str(ch.id) for ch in chapter_rows}

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
                            ChapterDraftVersionModel.version_no,
                            ChapterDraftVersionModel.created_at,
                            ChapterDraftVersionModel.id,
                            ChapterDraftVersionModel.content_md,
                        )
                        .where(ChapterDraftVersionModel.project_id == proj.id)
                        .order_by(
                            ChapterDraftVersionModel.chapter_id,
                            ChapterDraftVersionModel.is_current.desc(),
                            ChapterDraftVersionModel.version_no.desc(),
                            ChapterDraftVersionModel.created_at.desc(),
                        )
                    )
                )
                draft_meta_by_chapter_id: dict[object, dict[str, object]] = {}
                for chapter_id, word_count, version_no, created_at, draft_id, content_md in draft_rows:
                    if chapter_id in draft_meta_by_chapter_id:
                        continue
                    adoption = await _reader_source_adoption(
                        sess, proj.id, chapter_id, draft_id, content_md or "",
                    )
                    draft_meta_by_chapter_id.setdefault(
                        chapter_id,
                        {
                            **adoption,
                            "word_count": int(word_count or 0),
                            "version_no": int(version_no or 0),
                            "updated_at": created_at.isoformat()
                            if isinstance(created_at, datetime)
                            else None,
                        },
                    )

                pending_rewrite_count_by_chapter: dict[int, int] = {}
                pending_rewrite_rows = list(
                    await sess.scalars(
                        select(RewriteTaskModel).where(
                            RewriteTaskModel.project_id == proj.id,
                            RewriteTaskModel.status.in_(["pending", "queued"]),
                        )
                    )
                )
                scene_source_ids = [
                    task.trigger_source_id
                    for task in pending_rewrite_rows
                    if task.trigger_source_id
                    and str(task.trigger_source_id) not in chapter_number_by_id
                ]
                scene_chapter_by_id: dict[str, int] = {}
                if scene_source_ids:
                    scene_rows = list(
                        await sess.scalars(
                            select(SceneCardModel).where(SceneCardModel.id.in_(scene_source_ids))
                        )
                    )
                    for scene in scene_rows:
                        chapter_number = chapter_number_by_id.get(str(scene.chapter_id))
                        if chapter_number is not None:
                            scene_chapter_by_id[str(scene.id)] = chapter_number

                def _task_chapter_number(task: RewriteTaskModel) -> int | None:
                    source_id = str(task.trigger_source_id or "")
                    if source_id in chapter_number_by_id:
                        return chapter_number_by_id[source_id]
                    if source_id in scene_chapter_by_id:
                        return scene_chapter_by_id[source_id]
                    metadata = task.metadata_json if isinstance(task.metadata_json, dict) else {}
                    raw_number = metadata.get("chapter_number") or metadata.get(
                        "target_chapter_number"
                    )
                    try:
                        number = int(raw_number)
                    except (TypeError, ValueError):
                        number = None
                    if number in chapter_id_by_number:
                        return number
                    raw_chapter_id = str(metadata.get("chapter_id") or "")
                    if raw_chapter_id in chapter_number_by_id:
                        return chapter_number_by_id[raw_chapter_id]
                    return None

                for task in pending_rewrite_rows:
                    chapter_number = _task_chapter_number(task)
                    if chapter_number is not None:
                        pending_rewrite_count_by_chapter[chapter_number] = (
                            pending_rewrite_count_by_chapter.get(chapter_number, 0) + 1
                        )

                chapters_out: list[dict[str, object]] = []
                volume_map: dict[int, dict[str, object]] = {}
                for ch in chapter_rows:
                    draft_meta = draft_meta_by_chapter_id.get(ch.id) or {}
                    word_count = int(ch.current_word_count or 0)
                    if word_count <= 0:
                        word_count = int(draft_meta.get("word_count") or 0)
                    has_draft = ch.id in draft_meta_by_chapter_id
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
                    chapter_metadata = (
                        ch.metadata_json if isinstance(ch.metadata_json, dict) else {}
                    )

                    # production_state=ok is readable, not proof of review approval; other drafts
                    # are split into blocked/queued/active states so the reader
                    # does not imply every blocked chapter is being edited live.
                    availability = _reader_chapter_availability(
                        production_state,
                        word_count,
                    )
                    pending_rewrite_task_count = pending_rewrite_count_by_chapter.get(
                        int(ch.chapter_number),
                        0,
                    )
                    reader_state, reader_label = _reader_chapter_state(
                        chapter_status=chapter_status,
                        production_state=production_state,
                        availability=availability,
                        pending_rewrite_task_count=pending_rewrite_task_count,
                        production_intent=control.intent.value,
                        auto_repair_in_progress=bool(
                            chapter_metadata.get("auto_repair_in_progress")
                        ),
                        adoption_state=draft_meta.get("adoption_state"),
                    )

                    chapters_out.append(
                        {
                            **{key: draft_meta.get(key) for key in (
                                "adoption_state", "source_draft_id", "source_body_sha256"
                            )},
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
                            "production_intent": control.intent.value,
                            "reader_state": reader_state,
                            "reader_label": reader_label,
                            "pending_rewrite_task_count": pending_rewrite_task_count,
                            "version_no": draft_meta.get("version_no"),
                            "updated_at": draft_meta.get("updated_at")
                            or (
                                ch.updated_at.isoformat()
                                if isinstance(ch.updated_at, datetime)
                                else None
                            ),
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


_DASHBOARD_PROGRESS_EVENT_LIMIT = 48
_DASHBOARD_VISIBLE_TASK_STATUSES = {"queued", "running"}
_BOOK_RUNNING_STATUSES = {"queued", "running", "in_progress", "planning", "revising"}
_BOOK_CLOSED_STATUSES = {"completed", "complete", "published", "closed", "closed_complete"}
_BOOK_ATTENTION_STATUSES = {"failed", "error", "cancelled", "incomplete", "paused"}


def _query_bool(value: object) -> bool:
    return str(value or "").lower() in {"1", "true", "yes", "on"}


def _is_dashboard_visible_task(task: dict[str, object]) -> bool:
    status = str(task.get("status") or "").lower()
    if status in _DASHBOARD_VISIBLE_TASK_STATUSES:
        return True
    repair_status = task.get("repair_status")
    return (
        str(task.get("task_type") or "") == "repair"
        and isinstance(repair_status, dict)
        and bool(repair_status.get("is_repairing"))
    )


def _filter_dashboard_visible_tasks(
    tasks: list[dict[str, object]],
) -> list[dict[str, object]]:
    return [task for task in tasks if _is_dashboard_visible_task(task)]


def _project_library_archived(metadata: object) -> bool:
    return isinstance(metadata, dict) and bool(metadata.get("library_archived"))


def _compact_task_for_dashboard(task: dict[str, object]) -> dict[str, object]:
    """Trim heavy task payloads for dashboard list responses."""
    compacted = dict(task)
    events = task.get("progress_events")
    if not isinstance(events, list) or len(events) <= _DASHBOARD_PROGRESS_EVENT_LIMIT:
        return compacted
    head = events[:8]
    tail = events[-(_DASHBOARD_PROGRESS_EVENT_LIMIT - len(head)) :]
    compacted["progress_events"] = head + tail
    compacted["progress_events_truncated"] = True
    return compacted


def _task_project_slug(task: Mapping[str, object]) -> str:
    slug = str(task.get("project_slug") or "").strip()
    if slug:
        return slug
    payload = task.get("payload")
    if isinstance(payload, Mapping):
        return str(payload.get("slug") or payload.get("project_slug") or "").strip()
    return ""


def _settle_task_cards_from_project_status(
    tasks: list[dict[str, object]],
    project_statuses: dict[str, str],
    delivery_statuses: dict[str, dict[str, object]] | None = None,
) -> None:
    """Project fresh native delivery onto response copies, retaining run history.

    Lifecycle status alone grants no completion. Callers supply results of the
    read-only closure reader, never project metadata or cached task receipts.
    """

    for task in tasks:
        slug = _task_project_slug(task)
        if not slug:
            continue
        delivery = (delivery_statuses or {}).get(slug)
        if not delivery:
            continue
        previous_delivery = task.get("delivery_closure")
        task["delivery_closure"] = delivery
        if delivery.get("status") == "not_checked":
            # Deferred verification is neither a new success nor a failed proof.
            # Preserve the historical lifecycle in this response copy.
            task["delivery_verification_pending"] = True
            continue
        if delivery.get("status") != "verified" or delivery.get("is_complete") is not True:
            if task.get("current_stage") == "project_closed" or task.get("book_state") == "closed_complete":
                history = list(task.get("task_history") or [])
                previous = {key: task.get(key) for key in (
                    "task_id", "status", "current_stage", "current_stage_label", "book_state", "error",
                )}
                previous["delivery_closure"] = previous_delivery
                if previous not in history:
                    history.append(previous)
                task["task_history"] = history
                task["status"] = "incomplete"
                task["current_stage"] = "delivery_pending" if delivery.get("body_complete") is True else "delivery_unverified"
                task["current_stage_label"] = "正文齐备，待交付" if delivery.get("body_complete") is True else "交付待核验"
                task["book_state"] = "delivery_pending" if delivery.get("body_complete") is True else "needs_attention"
            continue
        if task.get("status") != "completed" or task.get("current_stage") != "project_closed" or task.get("error"):
            history = list(task.get("task_history") or [])
            previous = {key: task.get(key) for key in (
                "task_id", "task_type", "status", "current_stage", "created_at", "updated_at", "error",
            )}
            if previous not in history:
                history.append(previous)
            task["task_history"] = history
        task["status"] = "completed"
        task["current_stage"] = "project_closed"
        task["book_state"] = "closed_complete"
        task["current_stage_label"] = "正式交付已核验"
        task["error"] = None


async def _read_project_delivery_status(
    session: Any, project: Any, *, progress_only: bool = False,
) -> dict[str, object]:
    """Fresh readonly authority; exceptions never fall back to status/counts."""
    from bestseller.services.book_delivery_readiness import read_book_delivery_closure

    if progress_only:
        # A frequent progress projection never issues a delivery certificate.
        # Full/default reads, the reader and export retain native verification.
        return {"status": "not_checked", "is_complete": False, "body_complete": False,
                "reason": "delivery_verification_not_requested",
                "reader": "read_book_delivery_closure"}
    try:
        result = await read_book_delivery_closure(session, project, lock=False)
        verdict = result.verdict
        return {
            "status": "verified" if verdict.is_complete is True else "pending",
            "is_complete": verdict.is_complete is True,
            "body_complete": verdict.body_complete is True,
            "reason": verdict.reason,
            "reader": "read_book_delivery_closure",
        }
    except Exception:
        logger.warning("Native delivery readback unavailable for %s", project.slug, exc_info=True)
        return {"status": "unverified", "is_complete": False, "body_complete": False,
                "reason": "delivery_readback_unavailable", "reader": "read_book_delivery_closure"}


async def _apply_project_titles_to_tasks(
    settings: AppSettings,
    tasks: list[dict[str, object]],
    *,
    delivery_statuses: dict[str, dict[str, object]] | None = None,
    progress_readback: bool = False,
) -> list[dict[str, object]]:
    """Use DB project titles as the authoritative task-card display names."""
    slugs = sorted({_task_project_slug(task) for task in tasks if _task_project_slug(task)})
    if not slugs:
        return tasks
    try:
        from sqlalchemy import select

        from bestseller.infra.db.models import ProjectModel

        async with session_scope(settings) as sess:
            rows = list(await sess.scalars(select(ProjectModel).where(ProjectModel.slug.in_(slugs))))
            titles = {
                str(row.slug): str(row.title) for row in rows
                if row.slug and str(row.title or "").strip()
            }
            project_statuses = {
                str(row.slug): str(row.status or "").strip().lower() for row in rows if row.slug
            }
            # Only the handler's current-request reader results may be reused.
            # Never read this cache from task JSON or retain it across GETs.
            fresh_deliveries = {slug: {"status": "unverified", "is_complete": False} for slug in slugs}
            for row in rows:
                if row.slug:
                    fresh_deliveries[str(row.slug)] = ((delivery_statuses or {}).get(str(row.slug))
                        or await _read_project_delivery_status(sess, row,
                            **({"progress_only": True} if progress_readback else {})))
    except Exception:
        logger.warning("Failed to apply DB project titles to task cards", exc_info=True)
        _settle_task_cards_from_project_status(tasks, {}, {
            slug: {"status": "unverified", "is_complete": False} for slug in slugs
        })
        return tasks

    # Runs before the title early-return: a completed book must close its card
    # even if its title happens to be blank.
    _settle_task_cards_from_project_status(tasks, project_statuses, fresh_deliveries)
    if not titles:
        return tasks

    for task in tasks:
        slug = _task_project_slug(task)
        title = titles.get(slug)
        if not title:
            continue
        task["project_slug"] = slug
        task["project_title"] = title
        task["title"] = title
        payload = task.get("payload")
        if isinstance(payload, dict):
            payload["slug"] = slug
            payload["project_slug"] = slug
            payload["title"] = title
    return tasks


def _library_book_state(
    *,
    status: object,
    completed_units: int,
    target_units: int,
    has_content: bool,
    archived: bool = False,
    repair_status: dict[str, object] | None = None,
    has_active_workflow: bool = False,
    delivery_closure: Mapping[str, object] | None = None,
) -> str:
    if archived:
        return "archived"
    normalized = str(status or "").lower()
    if (delivery_closure and delivery_closure.get("status") == "verified"
        and delivery_closure.get("is_complete") is True):
        return "closed_complete"
    if target_units > 0 and completed_units >= target_units and has_content:
        return "delivery_pending"
    if has_active_workflow:
        return "in_progress"
    if normalized in _BOOK_ATTENTION_STATUSES:
        return "needs_attention"
    if repair_status and repair_status.get("is_repairing"):
        return "needs_attention"
    if normalized in _BOOK_RUNNING_STATUSES:
        return "in_progress"
    if has_content:
        return "in_progress"
    return "draft"


def _library_book_state_label(state: str) -> str:
    return {
        "archived": "已归档",
        "closed_complete": "已闭环",
        "delivery_pending": "正文齐备，待交付",
        "in_progress": "编写中",
        "needs_attention": "需处理",
        "draft": "草稿",
    }.get(state, state)


def _load_task_chapter_word_stats_summary(
    settings: AppSettings,
    project_slugs: list[str],
) -> dict[str, dict[str, object]]:
    """Use the same adoption evidence as detail; omit the per-chapter rows."""
    stats = _load_task_chapter_word_stats(settings, project_slugs, summary_only=False)
    return {slug: {**value, "chapters": []} for slug, value in stats.items()}


def _load_task_chapter_word_stats(
    settings: AppSettings,
    project_slugs: list[str],
    *,
    summary_only: bool = False,
) -> dict[str, dict[str, object]]:
    """Return adopted progress, separately from candidates and export previews."""
    if summary_only:
        return _load_task_chapter_word_stats_summary(settings, project_slugs)
    slugs = sorted({slug for slug in project_slugs if slug})
    if not slugs:
        return {}
    try:
        from sqlalchemy import select
        from bestseller.infra.db.models import ProjectModel

        async def _fetch() -> dict[str, dict[str, object]]:
            async with session_scope(settings) as session:
                projects = list(await session.scalars(
                    select(ProjectModel).where(ProjectModel.slug.in_(slugs))
                ))
                progress = await _load_adopted_chapter_progress(
                    session, [p.id for p in projects],
                    project_languages={p.id: p.language for p in projects},
                )
                result = {}
                for project in projects:
                    meta = project.metadata_json if isinstance(project.metadata_json, dict) else {}
                    is_short = is_fanqie_short_project(project)
                    value = {
                        **progress[project.id], "project_slug": project.slug,
                        "project_title": project.title, "project_type": project.project_type,
                        "content_mode": meta.get("content_mode"), "length_key": meta.get("length_key"),
                        "unit_kind": "segment" if is_short else "chapter",
                        "unit_label": "段" if is_short else "章",
                        "target_chapters": int(project.target_chapters or 0),
                        "target_segments": int(project.target_chapters or 0) if is_short else 0,
                        "completed_segments": progress[project.id]["adopted_chapters"] if is_short else 0,
                        "target_word_count": int(project.target_word_count or 0),
                    }
                    if is_short:
                        exported = _build_fanqie_short_export_task_stats(
                            settings, project, include_chapters=False,
                        )
                        if exported:
                            # Exported preview bytes are not adoption evidence.
                            for key in ("current_export_filename", "current_export_modified_at"):
                                value[key] = exported.get(key)
                            value["export_word_count_total"] = exported.get("word_count_total", 0)
                    result[project.slug] = value
                return result
        return asyncio.run(_fetch())
    except Exception:
        logger.warning("Failed to load adopted chapter progress", exc_info=True)
        return {}  # missing stats remain unknown; never synthesize completion


def _attach_task_chapter_word_stats(
    settings: AppSettings,
    tasks: list[dict[str, object]],
    *,
    summary_only: bool = False,
    same_request_stats: dict[str, dict[str, object]] | None = None,
) -> list[dict[str, object]]:
    stats_by_slug = (deepcopy(same_request_stats) if same_request_stats is not None
                     else _load_task_chapter_word_stats(
                         settings, [str(task.get("project_slug") or "") for task in tasks],
                         summary_only=summary_only,
                     ))
    if not stats_by_slug and same_request_stats is None:
        return tasks
    for task in tasks:
        slug = str(task.get("project_slug") or "")
        if same_request_stats is not None and slug not in stats_by_slug:
            previous = task.get("chapter_word_stats")
            if isinstance(previous, dict):
                task["chapter_word_stats"] = {
                    "source": "unverified", "adoption_evidence": "unverified",
                    "previous_snapshot": deepcopy(previous),
                }
            continue
        if slug in stats_by_slug:
            stats = stats_by_slug[slug]
            task["chapter_word_stats"] = stats
            project_title = str(stats.get("project_title") or "").strip()
            task_title = str(task.get("title") or "").strip()
            is_current_fanqie_export = (
                stats.get("source") == "fanqie_short_export"
                or stats.get("unit_kind") == "single_story"
            )
            if project_title and (
                is_current_fanqie_export
                or task.get("task_type") == "repair"
                or not task_title
                or task_title.lower().startswith("repair ")
            ):
                task["title"] = project_title
            if project_title:
                task["project_title"] = project_title
            for key in (
                "project_type",
                "content_mode",
                "platform_key",
                "length_key",
                "author_display_name",
                "current_content_title",
                "current_export_filename",
                "current_export_modified_at",
            ):
                if stats.get(key):
                    task[key] = stats[key]
    return tasks


def _provided_web_token(get_header: Any) -> str:
    """Extract the client-supplied web token from request headers."""
    token = get_header("X-Web-Token") or ""
    if not token:
        auth = get_header("Authorization") or ""
        if auth.startswith("Bearer "):
            token = auth[len("Bearer ") :]
    return token.strip()


def _web_auth_ok(expected_token: str, provided_token: str) -> bool:
    """True if auth is disabled (no token configured) or the token matches."""
    if not expected_token:
        return True
    return bool(provided_token) and hmac.compare_digest(provided_token, expected_token)



async def _configuration_project_choices(settings):
    from sqlalchemy import select
    from bestseller.infra.db.models import ProjectModel
    async with session_scope(settings) as session:
        rows = await session.scalars(select(ProjectModel).order_by(ProjectModel.title))
        return [{"slug": row.slug, "title": row.title} for row in rows]


async def _project_creation_configuration(settings, slug, payload=None):
    from bestseller.services.creation_configuration import configuration_view, save_project_configuration
    async with session_scope(settings) as session:
        project = await get_project_by_slug(session, slug)
        if project is None:
            raise ValueError("configuration_project_not_found")
        if payload is None:
            from sqlalchemy import select
            from bestseller.infra.db.models import WorkflowRunModel
            value = configuration_view(project)
            pending = list(await session.scalars(select(WorkflowRunModel).where(
                WorkflowRunModel.project_id == project.id, WorkflowRunModel.workflow_type == "creation_configuration_change",
                WorkflowRunModel.current_step == "proposal_requires_replan").order_by(WorkflowRunModel.created_at.desc()).limit(10)))
            if pending:
                value["pending_changes"] = [{"receipt_id": str(row.id), **row.metadata_json} for row in pending]
            return value
        if "expected_hash" not in payload or "expected_window_hash" not in payload:
            raise ValueError("configuration_expected_versions_required")
        result = await save_project_configuration(session, project, payload["submission"],
            expected_hash=payload["expected_hash"], expected_window_hash=payload["expected_window_hash"])
        await session.commit()
        return result

def _web_status_payload(settings, task_manager, *, light: bool = False) -> dict[str, object]:
    """Cheap UI status; full process/disk provenance remains the default audit API."""
    effective_settings = apply_runtime_llm_profile(settings)
    payload = {
        "app": "BestSeller Web Studio",
        "database_connected": bool(effective_settings.database.url),
        "llm_mock": effective_settings.llm.mock,
        "planner_model": effective_settings.llm.planner.model,
        "writer_model": effective_settings.llm.writer.model,
        "llm_profile": runtime_llm_profile_payload(settings),
        "output_base_dir": str(Path(settings.output.base_dir).resolve()),
        "max_concurrent_tasks": task_manager._max_concurrent_tasks,
    }
    if light:
        payload["runtime_provenance_available"] = True
    else:
        payload["runtime_provenance"] = {
            "process_runtime": initialize_process_runtime(settings),
            "disk_runtime": disk_runtime_provenance(settings),
        }
    return payload


def serve_web_app(
    host: str = "127.0.0.1",
    port: int = 8787,
    *,
    open_browser: bool = False,
) -> None:
    settings = load_settings()
    initialize_process_runtime(settings)
    tasks_persist_path = Path(settings.output.base_dir) / ".web_tasks.json"
    task_manager = WebTaskManager(persist_path=tasks_persist_path)

    # Keep recovery passes inside a single ``asyncio.run()`` so they
    # share one event loop. Splitting them across two ``asyncio.run()``
    # calls used to crash the second one with ``Future attached to a
    # different loop`` because the legacy ``_shared_engine`` cache held
    # asyncpg connections from the first (now-dead) loop. ``session_scope``
    # is now one-shot per call, but consolidating here also avoids paying
    # the engine-creation cost twice.
    async def _run_startup_recovery() -> None:
        try:
            # Independent of AUTO_RESUME: task history is not authorization
            # to adopt old exports as PostgreSQL truth during a migration.
            recover_exports = os.environ.get(
                "BESTSELLER_RECOVER_EXPORTS_ON_STARTUP", "false"
            ).strip().lower() in {"1", "true", "yes", "on"}
            recovered = await _recover_projects_from_output(
                settings, operator_opt_in=recover_exports,
            )
            if recovered:
                print(f"Auto-recovered {len(recovered)} projects from output directory")
        except Exception:
            logger.exception("Project recovery from output directory failed")

        # Recover task history from output directories (for quickstart
        # dashboard). File observations never get executable payloads, even
        # for existing projects; project progress remains a PostgreSQL projection.
        try:
            recover_task_history = os.environ.get(
                "BESTSELLER_RECOVER_TASK_HISTORY_ON_STARTUP", "true"
            ).strip().lower() in {"1", "true", "yes", "on"}
            recovered_tasks = (
                await task_manager._recover_tasks_from_output(settings)
                if recover_task_history else 0
            )
            if recovered_tasks:
                print(f"Auto-recovered {recovered_tasks} task(s) from output directory")
        except Exception:
            logger.exception("Task recovery from output directory failed")

        try:
            archived_slugs = await _load_library_archived_project_slugs(settings)
            removed = task_manager.delete_tasks_by_projects(
                archived_slugs,
                include_active=True,
            )
            if removed:
                logger.info(
                    "Removed %d web task record(s) for archived projects during startup",
                    removed,
                )
        except Exception:
            logger.exception("Archived task cleanup failed")

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
                settings=settings,
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

    # Optional shared-secret auth for mutating endpoints. When unset (default),
    # the server stays open for local single-user use; set BESTSELLER_WEB_TOKEN
    # to require a token whenever the UI is exposed beyond localhost.
    web_auth_token = os.environ.get("BESTSELLER_WEB_TOKEN", "").strip()

    class RequestHandler(BaseHTTPRequestHandler):
        server_version = "BestSellerWeb/0.1"

        def log_message(self, format: str, *args: object) -> None:
            return

        def _require_auth(self) -> bool:
            """Return True if the request may mutate state.

            No token configured -> open (local default). Otherwise the request
            must present a matching token (constant-time compare).
            """
            provided = _provided_web_token(self.headers.get)
            if _web_auth_ok(web_auth_token, provided):
                return True
            self._send_json({"error": "Unauthorized."}, status=HTTPStatus.UNAUTHORIZED)
            return False

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
            # Single-file apps inline their JS, so a stale-cached HTML silently
            # serves old JS after a rebuild ("rebuilt but nothing changed").
            # Force the browser to revalidate the HTML shell on every load.
            if "text/html" in content_type:
                self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
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
            if isinstance(exc, ExecutionBudgetError):
                self._send_json({"error": exc.code, "scope_id": exc.scope_id}, status=HTTPStatus.CONFLICT)
                return
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
            # Defense-in-depth (P17): when BESTSELLER_WEB_TOKEN is configured,
            # GET /api/* must present the token too, not only POST/DELETE. With
            # no token configured this is a no-op (local default stays open).
            if path.startswith("/api/") and not self._require_auth():
                return
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
                if path == "/architecture-course":
                    self._send_text(
                        _read_architecture_course_html(),
                        content_type="text/html; charset=utf-8",
                    )
                    return
                if path == "/methodology-course":
                    self._send_text(
                        _read_methodology_course_html(),
                        content_type="text/html; charset=utf-8",
                    )
                    return
                if path.startswith("/methodology-course/"):
                    lesson_slug = path.removeprefix("/methodology-course/").strip("/")
                    if re.fullmatch(r"\d{1,2}", lesson_slug or ""):
                        self._send_text(
                            _read_methodology_course_html(),
                            content_type="text/html; charset=utf-8",
                        )
                        return
                    self._route_not_found()
                    return
                if path == "/methodology-course-en":
                    self._send_text(
                        _read_methodology_course_html(),
                        content_type="text/html; charset=utf-8",
                    )
                    return
                if path.startswith("/methodology-course-en/"):
                    lesson_slug = path.removeprefix("/methodology-course-en/").strip("/")
                    if re.fullmatch(r"\d{1,2}", lesson_slug or ""):
                        self._send_text(
                            _read_methodology_course_html(),
                            content_type="text/html; charset=utf-8",
                        )
                        return
                    self._route_not_found()
                    return
                if path.startswith("/design/"):
                    project_slug = path.removeprefix("/design/").strip("/")
                    if not project_slug:
                        self._route_not_found()
                        return
                    self._send_text(
                        _read_design_dossier_html(),
                        content_type="text/html; charset=utf-8",
                    )
                    return
                if path.startswith("/flow/"):
                    project_slug = path.removeprefix("/flow/").strip("/")
                    if not project_slug:
                        self._route_not_found()
                        return
                    self._send_text(
                        _read_pipeline_flow_html(),
                        content_type="text/html; charset=utf-8",
                    )
                    return
                if path.startswith("/characters/"):
                    project_slug = path.removeprefix("/characters/").strip("/")
                    if not project_slug:
                        self._route_not_found()
                        return
                    self._send_text(
                        _read_character_network_html(),
                        content_type="text/html; charset=utf-8",
                    )
                    return
                if path.startswith("/review/"):
                    project_slug = path.removeprefix("/review/").strip("/")
                    if not project_slug:
                        self._route_not_found()
                        return
                    self._send_text(
                        _read_reader_review_html(),
                        content_type="text/html; charset=utf-8",
                    )
                    return
                if path == "/api/library":
                    self._send_json(asyncio.run(_load_library_payload(settings)))
                    return
                if path == "/api/methodology-course":
                    self._send_json(_build_methodology_course_payload())
                    return
                if path == "/api/methodology-course-en":
                    self._send_json(_build_methodology_course_payload("en"))
                    return
                if path == "/api/methodology-broadcast":
                    self._send_json(_build_methodology_broadcast_payload())
                    return
                if path.startswith("/api/methodology-course/lessons/"):
                    lesson_slug = path.removeprefix("/api/methodology-course/lessons/").strip("/")
                    if not re.fullmatch(r"\d{1,2}", lesson_slug or ""):
                        self._route_not_found()
                        return
                    payload = _build_methodology_lesson_payload(int(lesson_slug))
                    if payload is None:
                        self._route_not_found()
                        return
                    self._send_json(payload)
                    return
                if path.startswith("/api/methodology-course-en/lessons/"):
                    lesson_slug = path.removeprefix("/api/methodology-course-en/lessons/").strip(
                        "/"
                    )
                    if not re.fullmatch(r"\d{1,2}", lesson_slug or ""):
                        self._route_not_found()
                        return
                    payload = _build_methodology_lesson_payload(int(lesson_slug), "en")
                    if payload is None:
                        self._route_not_found()
                        return
                    self._send_json(payload)
                    return
                if path == "/api/status":
                    light_mode = _query_bool((query.get("light") or ["0"])[0])
                    self._send_json(_web_status_payload(settings, task_manager, light=light_mode))
                    return
                if path == "/api/llm-profile":
                    self._send_json(runtime_llm_profile_payload(settings))
                    return
                if path == "/api/projects":
                    light_mode = _query_bool((query.get("light") or ["0"])[0])
                    if light_mode:
                        progress_readback = _query_bool((query.get("progress_readback") or ["0"])[0])
                        self._send_json(asyncio.run(_load_projects_payload(settings, light=True,
                            **({"progress_readback": True} if progress_readback else {}))))
                        return
                    self._send_json(asyncio.run(_load_projects_payload(settings, light=False)))
                    return
                if path == "/api/writing-presets":
                    self._send_json(_public_writing_preset_catalog_payload())
                    return
                if path == "/api/genre-taxonomy":
                    self._send_json(_genre_taxonomy_payload())
                    return
                if path == "/api/concept-lab":
                    genre_key = str((query.get("genre_key") or [""])[0] or "")
                    if not genre_key:
                        raise ValueError("Field 'genre_key' is required.")
                    creative_key = str((query.get("creative_key") or [""])[0] or "")
                    count_raw = str((query.get("count") or ["12"])[0] or "12")
                    try:
                        count = max(1, min(12, int(count_raw)))
                    except ValueError:
                        count = 12
                    catalog = build_concept_lab_catalog(
                        genre_key,
                        creative_key=creative_key,
                        count=count,
                    )
                    self._send_json(catalog.model_dump(mode="json"))
                    return
                if path == "/api/prompt-packs":
                    from bestseller.services.prompt_packs import list_prompt_packs

                    packs = list_prompt_packs()
                    self._send_json([p.model_dump(mode="json") for p in packs])
                    return
                if path == "/api/creation-configuration":
                    from bestseller.services.writing_presets import list_platform_presets
                    self._send_json({"platforms": [{"key": p.key, "name": p.name} for p in list_platform_presets()],
                        "chapter_presets": [20, 500, 1000, 2000], "chapter_target_default": 2000,
                        "unit": "zh_body_chars", "dispatch_started": False})
                    return
                if path == "/api/creation-configuration/projects":
                    self._send_json(asyncio.run(_configuration_project_choices(settings)))
                    return
                configuration_slug = _match_project_route(path, "creation-configuration")
                if configuration_slug is not None:
                    self._send_json(asyncio.run(_project_creation_configuration(settings, configuration_slug)))
                    return
                if path == "/api/model-catalog":
                    slug = (query.get("slug") or [None])[0] or None
                    self._send_json(_model_catalog_payload(settings, slug))
                    return
                model_route_slug = _match_project_route(path, "model")
                if model_route_slug is not None:
                    self._send_json(_model_catalog_payload(settings, model_route_slug))
                    return
                if path == "/api/budget/estimate":
                    from bestseller.services.budget import estimate_project_cost

                    ch_count = int(query.get("chapters", ["30"])[0])
                    self._send_json(estimate_project_cost(ch_count, settings))
                    return
                if path == "/api/tasks":
                    summary_mode = _query_bool((query.get("summary") or ["0"])[0])
                    include_inactive = _query_bool((query.get("include_inactive") or ["0"])[0])
                    progress_readback = summary_mode and _query_bool((query.get("progress_readback") or ["0"])[0])
                    # Export observations are retained in the cache/audit, not
                    # execution cards. Existing books are projected from PG.
                    tasks = [task for task in task_manager.list_tasks()
                             if task.get("task_type") != "export_history"]
                    if not include_inactive:
                        tasks = _filter_dashboard_visible_tasks(tasks)
                    if summary_mode:
                        tasks = [_compact_task_for_dashboard(task) for task in tasks]
                    worker_tasks = _load_worker_task_summaries(settings)
                    if summary_mode:
                        worker_tasks = [_compact_task_for_dashboard(task) for task in worker_tasks]
                    tasks.extend(worker_tasks)
                    db_repair_tasks = asyncio.run(_load_db_repair_task_summaries(settings, tasks))
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
                                (
                                    str(task.get("task_id") or "").startswith("recovered-")
                                    and str(task.get("project_slug") or "") in db_repair_slugs
                                    and str(task.get("status") or "")
                                    in {
                                        "failed",
                                        "incomplete",
                                    }
                                )
                                or _is_stale_autowrite_repair_block_task(
                                    task,
                                    db_repair_slugs,
                                )
                            )
                        ]
                    tasks.extend(db_repair_tasks)
                    tasks = [{**task, "execution_owner_verified":task_manager.is_task_executing(str(task.get("task_id") or ""))} for task in tasks]
                    delivery_statuses = {}
                    same_request_stats = {} if progress_readback else None
                    tasks = asyncio.run(_load_current_dashboard_tasks(settings, tasks, delivery_statuses=delivery_statuses,
                        **({"progress_readback": True, "same_request_stats": same_request_stats} if progress_readback else {})))
                    if not include_inactive:
                        tasks = _filter_dashboard_visible_tasks(tasks)
                    tasks = asyncio.run(_apply_project_titles_to_tasks(settings, tasks, delivery_statuses=delivery_statuses,
                        **({"progress_readback": True} if progress_readback else {})))
                    self._send_json(
                        _attach_task_chapter_word_stats(
                            settings,
                            tasks,
                            summary_only=summary_mode,
                            **({"same_request_stats": same_request_stats} if same_request_stats is not None else {}),
                        )
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
                if path.startswith("/api/tasks/") and path.endswith("/phase-handoff-authorization"):
                    if not self._require_auth(): return
                    task_id = path.removeprefix("/api/tasks/").removesuffix("/phase-handoff-authorization")
                    async def inspect_phase():
                        from bestseller.services.execution_phase_handoff import proof
                        payload = await task_manager.approved_project_resume_payload(task_id)
                        async with session_scope(settings) as session:
                            binding, _, _ = await proof(session, task_id=task_id, payload=payload)
                        return {"available": True, "canonical_id": binding["canonical_id"]}
                    try:
                        self._send_json(asyncio.run(inspect_phase()))
                    except (ValueError, TypeError, KeyError):
                        self._send_json({"available": False})
                    return
                if path.startswith("/api/tasks/") and path.endswith("/continuation-authorization"):
                    if not self._require_auth(): return
                    task_id = path.removeprefix("/api/tasks/").removesuffix("/continuation-authorization")
                    from bestseller.services.conception_continuation import pending, resume_allowed, resumable_control_error
                    async def inspect_continuation():
                        async with session_scope(settings) as session:
                            current = task_manager.get_task(task_id) or {}
                            if (resumable_control_error(current.get("error"))
                                and current.get("payload", {}).get("_active_continuation_scope")):
                                return await resume_allowed(session.bind, task_id, current["payload"]), True
                            return await pending(session.bind, task_id), False
                    scope, existing_window = asyncio.run(inspect_continuation())
                    self._send_json({"available": bool(scope), "scope_id": scope, "existing_window": existing_window})
                    return
                if path.startswith("/api/tasks/") and path.endswith("/execution"):
                    if not self._require_auth():
                        return
                    task_id = path.removeprefix("/api/tasks/").removesuffix("/execution")
                    self._send_json(asyncio.run(task_manager.execution_reconciliation(task_id, settings=settings)))
                    return
                if path.startswith("/api/tasks/"):
                    progress_detail = path.endswith("/progress")
                    task_id = path.removeprefix("/api/tasks/")
                    if progress_detail:
                        task_id = task_id.removesuffix("/progress")
                    task_id = task_id.strip("/")
                    task = task_manager.get_task(task_id)
                    if task is not None and _is_stale_autowrite_repair_block_task(
                        task,
                        {_task_project_slug(task)},
                    ):
                        repair_slug = _task_project_slug(task)
                        repair_task = asyncio.run(
                            _load_db_repair_task_summary(
                                settings,
                                f"db-repair:{repair_slug}",
                            )
                        )
                        if repair_task is not None:
                            task = repair_task
                    if task is None:
                        task = _load_worker_task_summary(settings, task_id)
                    if task is None:
                        task = asyncio.run(_load_db_repair_task_summary(settings, task_id))
                    if task is None and task_id.startswith("project:"):
                        task = asyncio.run(
                            _load_project_dashboard_task_payload(
                                settings,
                                task_id.removeprefix("project:"),
                            )
                        )
                    if task is None:
                        self._route_not_found()
                        return
                    task = {**task, "execution_owner_verified":task_manager.is_task_executing(str(task.get("task_id") or ""))}
                    delivery_statuses = {}
                    current_tasks = asyncio.run(_load_current_dashboard_tasks(
                        settings, [task], slug=_task_project_slug(task),
                        delivery_statuses=delivery_statuses,
                    ))
                    projected = current_tasks[0] if current_tasks else task
                    if task.get("task_id") == task_id and not task.get("synthetic_project") and projected.get("task_id") != task_id:
                        task = {**projected, **task, "synthetic_project":False,
                                "project_projection": {key:projected.get(key) for key in ("task_id","status","current_stage","project_status","execution_active")}}
                    else:
                        task = projected
                    titled_tasks = asyncio.run(_apply_project_titles_to_tasks(settings, [task], delivery_statuses=delivery_statuses))
                    task = titled_tasks[0] if titled_tasks else task
                    enriched_tasks = _attach_task_chapter_word_stats(settings, [task])
                    enriched_task = enriched_tasks[0] if enriched_tasks else task
                    if progress_detail:
                        self._send_json(_task_progress_payload(enriched_task))
                    else:
                        self._send_json(enriched_task)
                    return
                # B3: Prompt Compiler「生效片段」manifest for a chapter.
                if (
                    path.startswith("/api/projects/")
                    and "/chapters/" in path
                    and path.endswith("/prompt-manifest")
                ):
                    parts = [p for p in path.split("/") if p]
                    # ["api","projects",slug,"chapters",n,"prompt-manifest"]
                    if len(parts) >= 6 and parts[0] == "api" and parts[1] == "projects":
                        project_slug = parts[2]
                        try:
                            chapter_number = int(parts[4])
                        except (TypeError, ValueError):
                            raise ValueError("chapter_number must be an integer")
                        try:
                            self._send_json(
                                asyncio.run(
                                    _load_chapter_prompt_manifest_payload(
                                        settings, project_slug, chapter_number
                                    )
                                )
                            )
                        except ValueError as exc:
                            self._send_json(
                                {"ok": False, "error": str(exc)},
                                status=HTTPStatus.NOT_FOUND,
                            )
                        return
                project_slug = _match_project_route(path, "summary")
                if project_slug is not None:
                    self._send_json(
                        asyncio.run(_load_project_summary_payload(settings, project_slug))
                    )
                    return
                project_slug = _match_project_route(path, "listing")
                if project_slug is not None:
                    from bestseller.services.listing_errors import listing_conflict_detail

                    try:
                        payload = asyncio.run(_load_project_listing_payload(settings, project_slug))
                    except ValueError as exc:
                        detail = listing_conflict_detail(exc)
                        if detail is None:
                            raise
                        self._send_json({"detail": detail}, status=HTTPStatus.CONFLICT)
                        return
                    self._send_json(payload)
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
                project_slug = _match_project_route(path, "execution-progress")
                if project_slug is not None:
                    try:
                        self._send_json(
                            asyncio.run(_load_execution_progress_payload(settings, project_slug))
                        )
                    except ValueError as exc:
                        self._send_json({"error": str(exc)}, status=HTTPStatus.NOT_FOUND)
                    return
                project_slug = _match_project_route(path, "pipeline-flow")
                if project_slug is not None:
                    try:
                        self._send_json(
                            asyncio.run(
                                _load_pipeline_flow_payload(settings, project_slug, task_manager)
                            )
                        )
                    except ValueError as exc:
                        self._send_json({"error": str(exc)}, status=HTTPStatus.NOT_FOUND)
                    return
                project_slug = _match_project_route(path, "reviews")
                if project_slug is not None:
                    try:
                        self._send_json(
                            asyncio.run(_load_project_reviews_payload(settings, project_slug))
                        )
                    except ValueError as exc:
                        self._send_json({"error": str(exc)}, status=HTTPStatus.NOT_FOUND)
                    return
                project_slug = _match_project_route(path, "design-dossier")
                if project_slug is not None:
                    self._send_json(
                        asyncio.run(_load_project_design_dossier_payload(settings, project_slug))
                    )
                    return
                project_slug = _match_project_route(path, "conception/options")
                if project_slug is not None:
                    try:
                        self._send_json(
                            asyncio.run(
                                _load_project_conception_options_payload(settings, project_slug)
                            )
                        )
                    except ValueError as exc:
                        self._send_json({"error": str(exc)}, status=HTTPStatus.NOT_FOUND)
                    return
                project_slug = _match_project_route(path, "reader-review")
                if project_slug is not None:
                    try:
                        self._send_json(
                            asyncio.run(_load_reader_review_payload(settings, project_slug))
                        )
                    except ValueError as exc:
                        self._send_json({"error": str(exc)}, status=HTTPStatus.NOT_FOUND)
                    return
                project_slug = _match_project_route(path, "prewrite-review")
                if project_slug is not None:

                    async def _load_prewrite() -> dict[str, object]:
                        async with session_scope(settings) as session:
                            return await load_prewrite_review_payload(session, project_slug)

                    self._send_json(asyncio.run(_load_prewrite()))
                    return
                project_slug = _match_project_route(path, "design-artifact")
                if project_slug is not None:
                    artifact_id = (query.get("artifact_id") or [""])[0]
                    if not artifact_id:
                        raise ValueError("Missing artifact_id query parameter.")
                    self._send_json(
                        asyncio.run(
                            _load_project_design_artifact_payload(
                                settings,
                                project_slug,
                                artifact_id,
                            )
                        )
                    )
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
                    project_summary = _load_reader_project_summary(settings, project_slug)
                    project_title = str(project_summary.get("title") or project_slug)
                    is_fanqie_short = bool(project_summary.get("is_fanqie_short")) or (
                        _fanqie_short_export_file(output_dir) is not None
                    )
                    if is_fanqie_short:
                        short_md = _load_fanqie_short_reader_markdown(
                            settings,
                            project_slug,
                            output_dir,
                            title=project_title,
                            genre=str(project_summary.get("genre") or ""),
                        )
                        toc = [_fanqie_short_toc_entry_from_markdown(short_md)] if short_md else []
                        self._send_json(
                            {
                                "project_slug": project_slug,
                                "title": project_title,
                                "chapters": toc,
                                "volumes": [],
                                "single_piece": True,
                                "content_mode": "fanqie_short_story",
                                "project_exists": project_summary.get("project_exists"),
                            }
                        )
                        return
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
                                    **_reader_state_payload(ch),
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
                                    "version_no": ch.get("version_no"),
                                    "updated_at": ch.get("updated_at"),
                                }
                            )
                        toc = merged_toc
                    else:
                        # DB path failed (or project is filesystem-only);
                        # degrade to the legacy filesystem TOC.
                        toc = fs_toc
                    self._send_json(
                        {
                            "project_slug": project_slug,
                            "title": project_title,
                            "chapters": toc,
                            "volumes": volumes,
                            "project_exists": project_summary.get("project_exists"),
                        }
                    )
                    return
                if path.startswith("/api/reader/") and "/chapter/" in path:
                    parts = path.split("/")
                    project_slug = parts[3]
                    chapter_n = int(parts[5])
                    output_dir = _project_output_dir(settings, project_slug)
                    project_summary = _load_reader_project_summary(settings, project_slug)
                    project_title = str(project_summary.get("title") or project_slug)
                    is_fanqie_short = bool(project_summary.get("is_fanqie_short")) or (
                        _fanqie_short_export_file(output_dir) is not None
                    )
                    if is_fanqie_short:
                        if chapter_n != 1:
                            self._route_not_found()
                            return
                        short_md = _load_fanqie_short_reader_markdown(
                            settings,
                            project_slug,
                            output_dir,
                            title=project_title,
                            genre=str(project_summary.get("genre") or ""),
                        )
                        if not short_md:
                            self._route_not_found()
                            return
                        _reader_lang = str(project_summary.get("language") or "") or None
                        html_content = markdown_to_html(short_md, language=_reader_lang)
                        stats = build_markdown_reading_stats(short_md)
                        self._send_json(
                            {
                                "number": 1,
                                "filename": str(_FANQIE_SHORT_EXPORT_RELATIVE_PATH),
                                "html": html_content,
                                "word_count": stats["word_count"],
                                "estimated_read_minutes": stats["estimated_read_minutes"],
                                "volume_number": None,
                                "volume_title": None,
                                "chapter_title": "全文",
                                "single_piece": True,
                                "content_mode": "fanqie_short_story",
                            }
                        )
                        return
                    chapter_file = output_dir / f"chapter-{chapter_n:03d}.md"
                    # SSOT: always check DB first so the reader reflects the
                    # current chapter_draft_versions.is_current row even when a
                    # stale file is cached on disk. Falls through to file-only
                    # behavior for Mode B projects that never touch the DB.
                    fresh_payload = _try_load_chapter_draft_payload_from_db(
                        settings,
                        project_slug,
                        f"chapter-{chapter_n:03d}.md",
                    )
                    fresh_content = (
                        str(fresh_payload.get("content") or "") if fresh_payload else None
                    )
                    if fresh_content is None and chapter_n >= 1000:
                        fresh_payload = _try_load_chapter_draft_payload_from_db(
                            settings,
                            project_slug,
                            f"chapter-{chapter_n:04d}.md",
                        )
                        fresh_content = (
                            str(fresh_payload.get("content") or "") if fresh_payload else None
                        )
                        if fresh_content is not None:
                            chapter_file = output_dir / f"chapter-{chapter_n:04d}.md"
                    if fresh_content is not None:
                        output_dir.mkdir(parents=True, exist_ok=True)
                        if (
                            not chapter_file.exists()
                            or chapter_file.read_text(encoding="utf-8") != fresh_content
                        ):
                            chapter_file.write_text(fresh_content, encoding="utf-8")
                    elif not chapter_file.exists():
                        chapter_file_4d = output_dir / f"chapter-{chapter_n:04d}.md"
                        if chapter_file_4d.exists():
                            chapter_file = chapter_file_4d
                        else:
                            self._route_not_found()
                            return
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
                    stat_updated_at = None
                    try:
                        stat_updated_at = datetime.fromtimestamp(
                            chapter_file.stat().st_mtime,
                            UTC,
                        ).isoformat()
                    except OSError:
                        pass
                    # Resolve volume info for this chapter so the reader can
                    # render "第N卷 卷名 · 第M章 章名".  Best-effort lookup;
                    # we never fail the chapter response just because the
                    # volume query errored.
                    volume_number: int | None = None
                    volume_title: str | None = None
                    chapter_title: str | None = None
                    reader_state_payload: dict[str, object] = {}
                    try:
                        chapter_index, vol_map, _ = _load_project_chapter_index(settings, project_slug)
                        chapter_entry = next(
                            (entry for entry in chapter_index if entry.get("number") == chapter_n), {}
                        )
                        reader_state_payload = _reader_state_payload(chapter_entry)
                        vol_info = vol_map.get(chapter_n) if vol_map else None
                        if vol_info:
                            volume_number = vol_info.get("volume_number")  # type: ignore[assignment]
                            volume_title = vol_info.get("volume_title")  # type: ignore[assignment]
                    except Exception:
                        pass
                    self._send_json(
                        {
                            **reader_state_payload,
                            "number": chapter_n,
                            "filename": chapter_file.name,
                            "html": html_content,
                            "word_count": int(
                                fresh_payload.get("word_count") or stats["word_count"]
                            )
                            if fresh_payload
                            else stats["word_count"],
                            "estimated_read_minutes": stats["estimated_read_minutes"],
                            "volume_number": volume_number,
                            "volume_title": volume_title,
                            "chapter_title": (
                                fresh_payload.get("chapter_title")
                                if fresh_payload
                                else chapter_title
                            ),
                            "version_no": fresh_payload.get("version_no")
                            if fresh_payload
                            else None,
                            "updated_at": (
                                fresh_payload.get("updated_at")
                                if fresh_payload
                                else stat_updated_at
                            ),
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
            if not self._require_auth():
                return
            parsed = urlparse(self.path)
            path = unquote(parsed.path)
            try:
                if path.startswith("/api/tasks/") and path.endswith("/continue-approved-project"):
                    task_id = path.removeprefix("/api/tasks/").removesuffix("/continue-approved-project")
                    self._send_json(task_manager.resume_approved_project(task_id), status=HTTPStatus.ACCEPTED)
                    return
                if path.startswith("/api/tasks/") and path.endswith("/continue-reviewed-conception"):
                    task_id = path.removeprefix("/api/tasks/").removesuffix("/continue-reviewed-conception")
                    if int(self.headers.get("Content-Length") or "0") > 4096:
                        raise ValueError("continuation payload exceeds bound")
                    self._send_json(task_manager.resume_reviewed_conception(task_id), status=HTTPStatus.ACCEPTED)
                    return
                if path.startswith("/api/tasks/") and path.endswith("/execution/reconcile"):
                    if int(self.headers.get("Content-Length") or "0") > 2_100_000:
                        raise ValueError("reconciliation body exceeds bound")
                    task_id = path.removeprefix("/api/tasks/").removesuffix("/execution/reconcile")
                    payload = self._read_json_body()
                    self._send_json(asyncio.run(task_manager.execution_reconciliation(task_id, payload, settings=settings)))
                    return
                if path.startswith("/api/tasks/") and path.endswith("/execution/recover-unsent"):
                    if int(self.headers.get("Content-Length", "0")) > 4096:
                        raise ValueError("unsent recovery body exceeds bound")
                    task_id = path.removeprefix("/api/tasks/").removesuffix("/execution/recover-unsent")
                    self._send_json(asyncio.run(task_manager.recover_unsent_admission(
                        task_id, self._read_json_body(), settings=settings)))
                    return
                if path == "/api/creation-configuration/preview":
                    from bestseller.services.creation_configuration import preview_submission
                    self._send_json(preview_submission(self._read_json_body()))
                    return
                configuration_slug = _match_project_route(path, "creation-configuration")
                if configuration_slug is not None:
                    result = asyncio.run(_project_creation_configuration(settings, configuration_slug, self._read_json_body()))
                    self._send_json(result)
                    return
                model_post_slug = _match_project_route(path, "model")
                if model_post_slug is not None:
                    payload = self._read_json_body()
                    self._send_json(
                        _set_project_model_payload(
                            settings,
                            model_post_slug,
                            str(payload.get("model_id") or ""),
                            task_manager=task_manager,
                        )
                    )
                    return
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
                    # An explicit start is an operator instruction to run, so it
                    # lifts any earlier stop/pause.
                    asyncio.run(
                        _clear_production_halt_async(settings, str(payload.get("slug") or ""))
                    )
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
                approve_slug = _match_project_route(path, "prewrite/approve")
                if approve_slug is not None:
                    payload = self._read_json_body()

                    async def _approve() -> dict[str, object]:
                        async with session_scope(settings) as session:
                            return await approve_prewrite_materials(
                                session,
                                approve_slug,
                                approved_by=str(payload.get("approved_by") or "web-ui"),
                                notes=str(payload.get("notes") or ""),
                            )

                    self._send_json({"ok": True, "prewrite_review": asyncio.run(_approve())})
                    return
                conception_approve_slug = _match_project_route(path, "conception/approve")
                if conception_approve_slug is not None:
                    payload = self._read_json_body()

                    async def _approve_conception() -> dict[str, object]:
                        async with session_scope(settings) as session:
                            project = await get_project_by_slug(session, conception_approve_slug)
                            if project is None:
                                raise ValueError("Project not found")
                            return await approve_pending_conception(
                                session,
                                project_id=project.id,
                                snapshot_id=UUID(str(payload.get("snapshot_id") or "")),
                                expected_lock_version=int(payload.get("expected_lock_version")),
                                source_hash=str(payload.get("source_hash") or ""),
                                option_id=str(payload.get("option_id") or ""),
                                idempotency_key=str(payload.get("idempotency_key") or ""),
                                actor=str(payload.get("actor") or "web-ui"),
                                reason=str(payload.get("reason") or "conception approved from design dossier"),
                            )

                    try:
                        self._send_json({"ok": True, "conception": asyncio.run(_approve_conception())})
                    except (ValueError, TypeError) as exc:
                        self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.CONFLICT)
                    return
                enter_writing_slug = _match_project_route(path, "enter-writing")
                if enter_writing_slug is not None:
                    active = task_manager.find_active_task_by_project_slug(enter_writing_slug)
                    if active is not None:
                        self._send_json({"ok": True, "already_running": True, **active})
                        return

                    async def _assert_and_payload() -> dict[str, object] | None:
                        async with session_scope(settings) as session:
                            project = await get_project_by_slug(session, enter_writing_slug)
                            if project is None:
                                return None
                            assert_conception_approved(project)
                            metadata = project.metadata_json if isinstance(project.metadata_json, Mapping) else {}
                            planning_handoff = (
                                metadata.get("conception_approved") is True
                                and str(metadata.get("planning_status") or "").lower()
                                == "conception_approved"
                            )
                            if not planning_handoff:
                                await assert_prewrite_review_approved(session, enter_writing_slug)
                        return await _rebuild_payload_from_db(settings, enter_writing_slug)

                    try:
                        payload = asyncio.run(_assert_and_payload())
                    except PermissionError as exc:

                        async def _load_block() -> dict[str, object]:
                            async with session_scope(settings) as session:
                                return await load_prewrite_review_payload(
                                    session,
                                    enter_writing_slug,
                                )

                        self._send_json(
                            {
                                "ok": False,
                                "error": str(exc),
                                "prewrite_review": asyncio.run(_load_block()),
                            },
                            status=HTTPStatus.CONFLICT,
                        )
                        return
                    if not payload:
                        self._send_json(
                            {"ok": False, "error": "Project not found or cannot be rebuilt"},
                            status=HTTPStatus.NOT_FOUND,
                        )
                        return
                    payload = dict(payload)
                    payload["_run_conception"] = False
                    project_metadata = payload.get("metadata")
                    planning_handoff = isinstance(project_metadata, Mapping) and (
                        project_metadata.get("conception_approved") is True
                        and str(project_metadata.get("planning_status") or "").lower()
                        == "conception_approved"
                    )
                    if planning_handoff:
                        payload["planning_handoff"] = True
                        payload.pop("prewrite_review_approved", None)
                    else:
                        payload["revision_mode"] = True
                        payload["prewrite_review_approved"] = True
                    task = task_manager.create_autowrite_task(payload)
                    self._send_json({"ok": True, **task}, status=HTTPStatus.ACCEPTED)
                    return
                if (
                    path.startswith("/api/projects/")
                    and "/prewrite/materials/" in path
                    and (path.endswith("/edit") or path.endswith("/issue"))
                ):
                    parts = path.split("/")
                    if len(parts) < 8:
                        raise ValueError("Invalid prewrite material route.")
                    project_slug = parts[3]
                    artifact_id = UUID(parts[6])
                    action = parts[7]
                    payload = self._read_json_body()
                    if action == "edit":
                        raw_content = payload.get("content")
                        if isinstance(raw_content, str):
                            raw_content = json.loads(raw_content)
                        if not isinstance(raw_content, dict):
                            raise ValueError("Field 'content' must be a JSON object.")

                        async def _edit_material() -> dict[str, object]:
                            async with session_scope(settings) as session:
                                return await edit_prewrite_material(
                                    session,
                                    project_slug,
                                    artifact_id=artifact_id,
                                    content=raw_content,
                                    notes=str(payload.get("notes") or ""),
                                    edited_by=str(payload.get("edited_by") or "web-ui"),
                                )

                        self._send_json(asyncio.run(_edit_material()))
                        return
                    instructions = str(payload.get("instructions") or "").strip()
                    if not instructions:
                        raise ValueError("Field 'instructions' is required.")

                    async def _create_prewrite_issue() -> dict[str, object]:
                        async with session_scope(settings) as session:
                            rewrite_task = await create_prewrite_issue_task(
                                session,
                                project_slug,
                                artifact_id=artifact_id,
                                instructions=instructions,
                                requested_by=str(payload.get("requested_by") or "web-ui"),
                            )
                            return {
                                "rewrite_task_id": str(rewrite_task.id),
                                "project_slug": project_slug,
                                "artifact_id": str(artifact_id),
                            }

                    issue = asyncio.run(_create_prewrite_issue())
                    task = task_manager.create_prewrite_repair_task(issue)
                    self._send_json({"ok": True, "rewrite_task": issue, "task": task})
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
                archive_slug = _match_project_route(path, "archive")
                if archive_slug is not None:
                    result = asyncio.run(
                        _set_project_library_archived(
                            settings,
                            archive_slug,
                            archived=True,
                        )
                    )
                    if result is None:
                        self._send_json(
                            {"ok": False, "error": "Project not found"},
                            status=HTTPStatus.NOT_FOUND,
                        )
                        return
                    result["tasks_deleted"] = task_manager.delete_tasks_by_project(
                        archive_slug,
                        include_active=True,
                    )
                    self._send_json(result)
                    return
                complete_slug = _match_project_route(path, "complete")
                if complete_slug is not None:
                    result = asyncio.run(_set_project_completed(settings, complete_slug))
                    if result is None:
                        self._send_json(
                            {"ok": False, "error": "Project not found"},
                            status=HTTPStatus.NOT_FOUND,
                        )
                        return
                    result["tasks_deleted"] = task_manager.delete_tasks_by_project(
                        complete_slug,
                        include_active=True,
                    )
                    self._send_json(result)
                    return
                unarchive_slug = _match_project_route(path, "unarchive")
                if unarchive_slug is not None:
                    result = asyncio.run(
                        _set_project_library_archived(
                            settings,
                            unarchive_slug,
                            archived=False,
                        )
                    )
                    if result is None:
                        self._send_json(
                            {"ok": False, "error": "Project not found"},
                            status=HTTPStatus.NOT_FOUND,
                        )
                        return
                    self._send_json(result)
                    return
                revision_slug = _match_project_route(path, "revision-task")
                if revision_slug is not None:
                    active = task_manager.find_active_task_by_project_slug(revision_slug)
                    if active is not None:
                        self._send_json(
                            {
                                "ok": True,
                                "already_running": True,
                                **active,
                            },
                            status=HTTPStatus.ACCEPTED,
                        )
                        return
                    try:

                        async def _assert_revision_prewrite() -> None:
                            async with session_scope(settings) as session:
                                await assert_prewrite_review_approved(session, revision_slug)

                        asyncio.run(_assert_revision_prewrite())
                    except PermissionError as exc:

                        async def _load_revision_block() -> dict[str, object]:
                            async with session_scope(settings) as session:
                                return await load_prewrite_review_payload(session, revision_slug)

                        self._send_json(
                            {
                                "ok": False,
                                "error": str(exc),
                                "prewrite_review": asyncio.run(_load_revision_block()),
                            },
                            status=HTTPStatus.CONFLICT,
                        )
                        return
                    payload = asyncio.run(_rebuild_payload_from_db(settings, revision_slug))
                    if not payload:
                        self._send_json(
                            {"ok": False, "error": "Project not found or cannot be rebuilt"},
                            status=HTTPStatus.NOT_FOUND,
                        )
                        return
                    block_payload = asyncio.run(
                        _load_project_autowrite_block_payload(settings, revision_slug),
                    )
                    if block_payload:
                        self._send_json(block_payload, status=HTTPStatus.CONFLICT)
                        return
                    payload = dict(payload)
                    payload["_run_conception"] = False
                    payload["revision_mode"] = True
                    task = task_manager.create_autowrite_task(payload)
                    self._send_json({"ok": True, **task}, status=HTTPStatus.ACCEPTED)
                    return
                if (
                    path.startswith("/api/reader/")
                    and "/chapter/" in path
                    and path.endswith("/revision")
                ):
                    parts = path.split("/")
                    project_slug = parts[3]
                    chapter_number = int(parts[5])
                    payload = self._read_json_body()
                    operation = str(payload.get("operation") or "").strip()
                    if operation not in {"regenerate", "polish", "humanize", "issue"}:
                        raise ValueError("Unsupported chapter revision operation.")
                    instructions = str(payload.get("instructions") or "").strip()
                    if operation == "issue" and not instructions:
                        raise ValueError("Field 'instructions' is required for issue edits.")

                    async def _create_chapter_revision() -> dict[str, object]:
                        async with session_scope(settings) as session:
                            rewrite_task = await create_chapter_revision_task(
                                session,
                                project_slug,
                                chapter_number,
                                operation=operation,
                                instructions=instructions,
                                requested_by=str(payload.get("requested_by") or "reader-ui"),
                            )
                            return {
                                "project_slug": project_slug,
                                "chapter_number": chapter_number,
                                "operation": operation,
                                "rewrite_task_id": str(rewrite_task.id),
                            }

                    revision = asyncio.run(_create_chapter_revision())
                    task = task_manager.create_chapter_revision_task(revision)
                    self._send_json(
                        {"ok": True, "rewrite_task": revision, "task": task},
                        status=HTTPStatus.ACCEPTED,
                    )
                    return
                if path == "/api/tasks/quickstart":
                    payload = self._read_json_body()
                    _validate_quickstart_client_schema(payload)
                    # Accept either a legacy genre_key (热门开局模板) OR a structured
                    # taxonomy selection (频道·题材·子题材·标签 free picker).
                    if not _quickstart_payload_has_genre(payload):
                        raise ValueError(
                            "请先选择题材(选一个频道·题材·子题材,或一张热门开局模板)。"
                        )
                    resume_slug = str(payload.get("project_slug") or "")
                    if resume_slug:
                        try:

                            async def _assert_quickstart_resume_prewrite() -> None:
                                async with session_scope(settings) as session:
                                    await assert_prewrite_review_approved(session, resume_slug)

                            asyncio.run(_assert_quickstart_resume_prewrite())
                        except PermissionError as exc:

                            async def _load_quickstart_block() -> dict[str, object]:
                                async with session_scope(settings) as session:
                                    return await load_prewrite_review_payload(session, resume_slug)

                            self._send_json(
                                {
                                    "ok": False,
                                    "error": str(exc),
                                    "prewrite_review": asyncio.run(_load_quickstart_block()),
                                },
                                status=HTTPStatus.CONFLICT,
                            )
                            return
                        block_payload = asyncio.run(
                            _load_project_autowrite_block_payload(settings, resume_slug),
                        )
                        if block_payload:
                            self._send_json(block_payload, status=HTTPStatus.CONFLICT)
                            return
                    task = task_manager.create_quickstart_task(payload)
                    self._send_json(task, status=HTTPStatus.ACCEPTED)
                    return
                if path == "/api/concept-lab/preview":
                    payload = self._read_json_body()
                    genre_key = str(payload.get("genre_key") or "")
                    if not genre_key:
                        raise ValueError("Field 'genre_key' is required.")
                    seed_raw = payload.get("seed")
                    try:
                        seed_value = int(seed_raw) if seed_raw is not None else None
                    except (TypeError, ValueError):
                        seed_value = None
                    catalog = build_concept_lab_catalog(
                        genre_key,
                        creative_key=str(payload.get("creative_key") or ""),
                        hook_spec=(
                            payload.get("hook_spec")
                            if isinstance(payload.get("hook_spec"), dict)
                            else None
                        ),
                        count=max(1, min(12, int(payload.get("count") or 12))),
                        seed=seed_value,
                    )
                    self._send_json(catalog.model_dump(mode="json"))
                    return
                if path == "/api/hook-candidates":
                    payload = self._read_json_body()
                    genre_key = str(payload.get("genre_key") or payload.get("genre") or "")
                    if not genre_key:
                        raise ValueError("Field 'genre_key' is required.")
                    seed_raw = payload.get("seed")
                    try:
                        seed_value = int(seed_raw) if seed_raw is not None else None
                    except (TypeError, ValueError):
                        seed_value = None
                    count_value = max(1, min(12, int(payload.get("count") or 6)))
                    candidates = generate_hook_candidates(
                        genre=genre_key,
                        locale=str(payload.get("locale") or "zh-CN"),
                        count=count_value,
                        seed=seed_value,
                        min_h_norm=float(getattr(settings.hook_engine, "min_h_norm", 30.0)),
                    )
                    serialized: list[dict[str, object]] = []
                    for candidate in candidates:
                        cand_payload = candidate.model_dump(mode="json")
                        try:
                            mech = get_mechanism(candidate.spec.mechanism_key)
                            cand_payload["mechanism_label"] = mech.label
                        except Exception:
                            cand_payload["mechanism_label"] = candidate.spec.mechanism_key
                        serialized.append(cand_payload)
                    self._send_json(
                        {
                            "genre_key": genre_key,
                            "seed": seed_value,
                            "candidates": serialized,
                        }
                    )
                    return
                if path == "/api/llm-profile":
                    payload = self._read_json_body()
                    profile_key = str(payload.get("profile") or payload.get("active_key") or "")
                    if not profile_key:
                        raise ValueError("Field 'profile' is required.")
                    self._send_json(set_runtime_llm_profile(settings, profile_key))
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
                    _validate_quickstart_client_schema(payload)
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
                    outcome = _request_visible_task_cancel(task_manager, settings, task_id)
                    if outcome == "not_found":
                        self._send_json(
                            {"ok": False, "error": "Task not found"},
                            status=HTTPStatus.NOT_FOUND,
                        )
                        return
                    if outcome == "not_running":
                        self._send_json(
                            {"ok": False, "error": "Task is not running or queued"},
                            status=HTTPStatus.CONFLICT,
                        )
                        return
                    self._send_json({"ok": True, "task_id": task_id})
                    return
                if path.startswith("/api/tasks/") and path.endswith("/resume"):
                    task_id = path.removeprefix("/api/tasks/").removesuffix("/resume")
                    old = task_manager.get_task(task_id)
                    if old is not None and execution_terminal_code(old.get("result") or {}) in {
                        "execution_budget_exhausted", "execution_deadline_exhausted",
                        "execution_downstream_budget_reserved",
                        "execution_authorization_paused",
                    }:
                        try:
                            if not asyncio.run(task_manager.rearm_authorized_budget_resume(task_id)):
                                raise ValueError("task is busy or missing")
                        except ValueError:
                            self._send_json({
                                "ok": False,
                                "error": "当前预算停止未通过新授权复验，原始回执已保留。",
                            }, status=HTTPStatus.CONFLICT)
                            return
                        old = task_manager.get_task(task_id)
                    if old is None:
                        worker_resume = _resolve_worker_task_resume_target(settings, task_id)
                        if worker_resume is not None:
                            task_type, slug = worker_resume
                            if _worker_heal_job_is_active(settings.redis.url, task_id):
                                self._send_json(
                                    {
                                        "ok": False,
                                        "error": "该 worker 任务仍在运行或排队中，不能重复恢复。",
                                    },
                                    status=HTTPStatus.CONFLICT,
                                )
                                return
                            if asyncio.run(_has_live_pipeline_run_async(settings, slug)):
                                self._send_json(
                                    {
                                        "ok": False,
                                        "error": "这本书还有正在运行的流水线，不能重复恢复。",
                                    },
                                    status=HTTPStatus.CONFLICT,
                                )
                                return
                            outline_resume_available = (
                                task_type == "outline_replan"
                                and _clear_outline_replan_resume_abandonment_sync(settings, slug)
                            )
                            if _worker_resume_requires_autowrite_block(
                                task_type,
                                outline_replan_available=outline_resume_available,
                            ):
                                block_payload = asyncio.run(
                                    _load_project_autowrite_block_payload(settings, slug)
                                )
                                if block_payload:
                                    self._send_json(block_payload, status=HTTPStatus.CONFLICT)
                                    return
                            asyncio.run(_clear_production_halt_async(settings, slug, strict=True))
                            _clear_repair_resume_focus_pause_sync(settings, slug)
                            task_type = _worker_resume_enqueue_kind(
                                task_type,
                                outline_replan_available=outline_resume_available,
                            )
                            outline_resume = task_type == "outline_replan"
                            if outline_resume:
                                job_id = _enqueue_outline_replan_heal_job(
                                    settings,
                                    settings.redis.url,
                                    slug,
                                )
                            elif task_type == "repair":
                                job_id = _enqueue_repair_heal_job(settings.redis.url, slug)
                            else:
                                job_id = _enqueue_autowrite_heal_job(settings.redis.url, slug)
                            if job_id:
                                self._send_json(
                                    {
                                        "ok": True,
                                        "task_id": task_id,
                                        "project_slug": slug,
                                        "status": "queued",
                                        "current_stage": (
                                            "delegated_to_worker_outline_replan"
                                            if outline_resume
                                            else "delegated_to_worker_self_heal"
                                        ),
                                        "worker_job_id": job_id,
                                    },
                                    status=HTTPStatus.ACCEPTED,
                                )
                                return
                            self._send_json(
                                {
                                    "ok": False,
                                    "error": "Worker task receipt is valid but could not be re-enqueued",
                                },
                                status=HTTPStatus.CONFLICT,
                            )
                            return
                        if task_id.startswith("db-repair:"):
                            slug = task_id.removeprefix("db-repair:").strip()
                            # A synthetic repair card still resumes the same
                            # production project.  Its worker must not inherit
                            # the durable operator halt written by Stop.
                            if slug:
                                asyncio.run(
                                    _clear_production_halt_async(settings, slug, strict=True)
                                )
                            _clear_repair_resume_focus_pause_sync(settings, slug)
                            outline_resume = _clear_outline_replan_resume_abandonment_sync(
                                settings,
                                slug,
                            )
                            original = asyncio.run(
                                _load_incomplete_original_autowrite_task(
                                    settings,
                                    task_manager,
                                    slug,
                                )
                            )
                            if original is not None and not outline_resume:
                                if asyncio.run(_has_live_pipeline_run_async(settings, slug)):
                                    self._send_json(
                                        {
                                            "ok": False,
                                            "error": "这本书还有正在运行的流水线，不能重复恢复。",
                                        },
                                        status=HTTPStatus.CONFLICT,
                                    )
                                    return
                                original_task_id = str(original.get("task_id") or "")
                                original_payload = original.get("payload")
                                if original_task_id and isinstance(original_payload, dict):
                                    saved_payload = dict(original_payload)
                                    rebuilt = asyncio.run(
                                        _rebuild_payload_from_db(settings, slug)
                                    )
                                    saved_payload = _merge_resume_payload_with_project_truth(
                                        saved_payload,
                                        rebuilt,
                                    )
                                    saved_payload["slug"] = slug
                                    saved_payload["project_slug"] = slug
                                    saved_payload["_run_conception"] = False
                                    block_payload = asyncio.run(
                                        _load_project_autowrite_block_payload(settings, slug)
                                    )
                                    if block_payload:
                                        _mark_db_repair_resume_block(
                                            task_manager,
                                            original_task_id,
                                            block_payload,
                                        )
                                        self._send_json(block_payload, status=HTTPStatus.CONFLICT)
                                        return
                                    heal_owned = bool(
                                        settings.redis.url
                                        and slug
                                        in _fetch_heal_owned_slugs_by_kind(
                                            settings.redis.url,
                                            "autowrite",
                                        )
                                    )
                                    resumed = _resume_original_autowrite_task(
                                        task_manager,
                                        original,
                                        saved_payload,
                                        heal_owned=heal_owned,
                                    )
                                    if isinstance(resumed, dict):
                                        self._send_json(
                                            {
                                                "ok": True,
                                                "resume_mode": "full_autowrite",
                                                **resumed,
                                            },
                                            status=HTTPStatus.ACCEPTED,
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
                            job_id = (
                                _enqueue_outline_replan_heal_job(
                                    settings,
                                    settings.redis.url,
                                    slug,
                                )
                                if outline_resume
                                else _enqueue_repair_heal_job(settings.redis.url, slug)
                            )
                            if job_id:
                                self._send_json(
                                    {
                                        "ok": True,
                                        "task_id": task_id,
                                        "project_slug": slug,
                                        "status": "queued",
                                        "current_stage": (
                                            "delegated_to_worker_outline_replan"
                                            if outline_resume
                                            else "delegated_to_worker_self_heal"
                                        ),
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
                    slug = str(saved_payload.get("slug") or old.get("project_slug") or "")
                    if not saved_payload:
                        if slug:
                            rebuilt = asyncio.run(
                                _rebuild_payload_from_db(settings, str(slug)),
                            )
                            if rebuilt:
                                saved_payload = rebuilt
                    else:
                        # Once a project exists, PostgreSQL is the canonical
                        # resume truth.  The persisted task payload is the
                        # pre-conception request envelope and may still carry a
                        # generic premise or exploratory world/material data.
                        # Preserve only saved-only controls; every field that
                        # can be rebuilt from the project must win.
                        rebuilt = (
                            asyncio.run(
                                _rebuild_payload_from_db(settings, slug),
                            )
                            if slug
                            else None
                        )
                        saved_payload = _merge_resume_payload_with_project_truth(
                            dict(saved_payload),
                            rebuilt,
                        )
                    if not saved_payload:
                        self._send_json(
                            {
                                "ok": False,
                                "error": "No saved payload to resume from and no matching project in DB",
                            },
                            status=HTTPStatus.BAD_REQUEST,
                        )
                        return
                    saved_payload = dict(saved_payload)
                    slug = str(saved_payload.get("slug") or old.get("project_slug") or "")
                    # Only disable conception when a project already exists
                    # (writing-phase resume must NOT recreate from scratch). For
                    # a conception-phase zombie — no project row yet — keep
                    # conception ON so resume re-runs it; forcing it off would
                    # try to write chapters for a project that never got built.
                    project_exists = bool(
                        slug and asyncio.run(_load_existing_project_slugs(settings, {slug}))
                    )
                    if project_exists:
                        saved_payload["_run_conception"] = False
                    else:
                        saved_payload["_run_conception"] = True
                    block_payload = asyncio.run(
                        _load_project_autowrite_block_payload(settings, slug),
                    )
                    if block_payload:
                        stage = str(
                            block_payload.get("current_stage") or "blocked_structural_repair"
                        )
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
                    # DB 里还有活跃流水线时不许再起一个执行器。
                    #
                    # 2026-08-22 真机：卡片显示 incomplete、DB 里
                    # workflow_runs 仍是 running，resume 放行后两个
                    # project_pipeline 并发写同一本书，几分钟后 Postgres
                    # 报 DeadlockDetectedError，整本书失败在第 22 章。
                    if slug and asyncio.run(_has_live_pipeline_run_async(settings, slug)):
                        self._send_json(
                            {
                                "ok": False,
                                "error": (
                                    "这本书还有正在运行的流水线，不能重复恢复"
                                    "（会造成并发写入与数据库死锁）。请等它结束，"
                                    "或先点停止再恢复。"
                                ),
                            },
                            status=HTTPStatus.CONFLICT,
                        )
                        return

                    # 恢复同样是「操作者要求运行」，必须解掉之前那次 Stop
                    # 写下的暂停指令——它被刻意写在流水线覆盖不到的
                    # book_production_control 里，不会自己消失。
                    #
                    # 2026-08-22 真机：点 Stop 再点 Resume，书立刻走
                    # progressive_autowrite_started → autowrite_halted_by_operator
                    # → autowrite_completed，0 章却显示「已完成」。
                    # 显式启动那条路径（/api/tasks/autowrite）早就在做这件事，
                    # 注释写着「An explicit start is an operator instruction to
                    # run, so it lifts any earlier stop/pause」——恢复这条只是
                    # 漏了，不是设计如此。
                    if slug:
                        asyncio.run(_clear_production_halt_async(settings, slug))
                        if project_exists and str(old.get("error") or "").rstrip().endswith((
                            "ValueError: fanqie_projection_scene_field_type:entry_state",
                            "ValueError: fanqie_projection_scene_field_type:exit_state",
                        )):
                            asyncio.run(_prepare_failed_fanqie_beat_resume_async(settings, slug))
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
                    # This authenticated, explicit operator action is separate
                    # from displaying task history or starting the server.
                    recovered = asyncio.run(
                        _recover_projects_from_output(settings, operator_opt_in=True)
                    )
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
            if not self._require_auth():
                return
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
