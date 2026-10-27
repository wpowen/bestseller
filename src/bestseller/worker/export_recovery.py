"""Queue and execute only missing native exports; never restart prose pipelines."""

from datetime import timedelta
import hashlib
import logging
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from bestseller.infra.db.models import ChapterDraftVersionModel, ChapterModel, ProjectModel
from bestseller.infra.db.session import get_server_session
from bestseller.services.budget import bind_execution_scope
from bestseller.services.export_only_recovery import (
    ExportOnlyStatus,
    preflight_export_only_recovery,
    run_export_only_recovery,
)
from bestseller.services.production_control import halted_project_ids
from bestseller.services.retry_ledger import project_execution_scope
from bestseller.settings import get_settings

logger = logging.getLogger(__name__)
REQUESTED_BY = "worker-export-only"

# Existing generic ghost cleanup can remove job/retry debris. This compatibility
# path may remove ONLY an exact, identity-checked completed export result.
_RESULT_SNAPSHOT = """
if redis.call('exists', KEYS[2], KEYS[3], KEYS[4], KEYS[6]) > 0 or
   redis.call('zscore', KEYS[5], ARGV[1]) then return {} end
local value = redis.call('get', KEYS[1])
local ttl = redis.call('pttl', KEYS[1])
if not value or ttl <= 0 then return {} end
return {value, ttl}
"""
_RESULT_DELETE_CAS = """
if redis.call('exists', KEYS[2], KEYS[3], KEYS[4], KEYS[6]) > 0 or
   redis.call('zscore', KEYS[5], ARGV[1]) then return 0 end
local ttl = redis.call('pttl', KEYS[1])
if redis.call('get', KEYS[1]) ~= ARGV[2] or ttl <= 0 or ttl > tonumber(ARGV[3]) then return 0 end
return redis.call('del', KEYS[1])
"""


async def _clear_completed_export_result(redis, settings, project, job_id):
    """Recheck native eligibility; CAS only the same terminal result, no owner."""
    from arq.jobs import deserialize_result
    from bestseller.worker.tasks import _pipeline_live_key

    if job_id != "export-only:heal:" + project.slug:
        return False
    queue_name = getattr(redis, "default_queue_name", "arq:queue")
    keys = ["arq:result:" + job_id, "arq:job:" + job_id,
            "arq:in-progress:" + job_id, "arq:retry:" + job_id,
            queue_name, _pipeline_live_key(project.slug)]
    try:
        snapshot = await redis.eval(_RESULT_SNAPSHOT, len(keys), *keys, job_id)
        if not snapshot or len(snapshot) != 2:
            return False
        raw, ttl = snapshot
        if not isinstance(raw, bytes) or type(ttl) is not int or ttl <= 0:
            return False
        saved = deserialize_result(raw, deserializer=getattr(redis, "job_deserializer", None))
        expected = {"workflow_run_id": job_id,
                    "payload": {"project_id": str(project.id), "project_slug": project.slug}}
        value = saved.result
        if (saved.success is not True or saved.function != "run_export_only_recovery_task"
                or saved.job_id != job_id or saved.queue_name != queue_name
                or saved.args != () or saved.kwargs != expected or not isinstance(value, dict)):
            return False
        if (value.get("status") != "completed" or value.get("reason") != "native_delivery_verified"
                or value.get("project_id") != str(project.id) or value.get("workflow_run_id") != job_id
                or type(value.get("provider_calls")) is not int or value["provider_calls"] != 0):
            return False
        audit, receipt = value.get("audit"), value.get("source_receipt")
        if (not isinstance(audit, dict) or not isinstance(receipt, dict)
                or audit.get("status") != "completed" or audit.get("project_id") != str(project.id)
                or audit.get("requested_by") != REQUESTED_BY
                or type(audit.get("provider_calls")) is not int or audit["provider_calls"] != 0
                or audit.get("source_receipt") != receipt
                or receipt.get("manifest", {}).get("project_id") != str(project.id)
                or receipt.get("project", {}).get("project", {}).get("id") != str(project.id)
                or receipt.get("project", {}).get("project", {}).get("slug") != project.slug):
            return False
        async with get_server_session() as session:
            current = await session.get(ProjectModel, project.id)
            if current is None or current.slug != project.slug:
                return False
            checked = await preflight_export_only_recovery(
                session, settings, project_id=current.id, requested_by=REQUESTED_BY)
        if checked.status != ExportOnlyStatus.ELIGIBLE or checked.project_id != project.id:
            return False
        removed = await redis.eval(_RESULT_DELETE_CAS, len(keys), *keys, job_id, raw, ttl)
    except Exception as error:
        logger.warning("export-only legacy result retained (%s)", type(error).__name__)
        return False
    if removed:
        logger.info("export-only retired completed result %s sha256=%s", job_id, hashlib.sha256(raw).hexdigest())
    return bool(removed)


def _export_needs_probe(project, settings):
    """Cheap candidate hint only. Native admission is mandatory before enqueue/run."""
    meta = project.metadata_json or {}
    if meta.get("completion_export_error"):
        return True
    root = Path(settings.output.base_dir).resolve()
    output = (root / project.slug).resolve()
    if output == root or not output.is_relative_to(root):
        return False
    if project.project_type == "fanqie_short":
        receipt = meta.get("fanqie_whole_export_receipt") or {}
        files = (receipt.get("delivery_verification") or {}).get("files") or {}
        names = {
            "markdown_path": "fanqie-short.md",
            "readiness_path": "signing-readiness.json",
            "source_receipt_path": "adopted-source.json",
        }
        pairs = [(output / "exports" / name, (files.get(key) or {}).get("sha256"))
                 for key, name in names.items()]
    else:
        checksum = meta.get("completion_export_checksum")
        pairs = []
        for key in ("completion_export_path", "completion_export_storage_uri"):
            raw = meta.get(key)
            if not isinstance(raw, str) or not raw:
                return True
            path = Path(raw).resolve()
            if not path.is_relative_to(output):
                return True
            pairs.append((path, checksum))
    for path, expected in pairs:
        if not isinstance(expected, str) or len(expected) != 64:
            return True
        try:
            if not path.resolve().is_relative_to(output):
                return True
            if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                return True
        except OSError:
            return True
    return False


async def enqueue_export_only_recoveries(settings, redis):
    """Missing/corrupt mirror hints select candidates, never grant a saved pass."""
    from bestseller.worker.self_heal import _active_arq_project_slugs

    owners = await _active_arq_project_slugs(redis)
    if owners is None or redis is None:
        return []
    queued = []
    try:
        async with get_server_session() as session:
            halted = await halted_project_ids(session)
            census = (
                select(ChapterModel.project_id)
                .join(ProjectModel, ProjectModel.id == ChapterModel.project_id)
                .outerjoin(ChapterDraftVersionModel, and_(
                    ChapterDraftVersionModel.project_id == ChapterModel.project_id,
                    ChapterDraftVersionModel.chapter_id == ChapterModel.id,
                    ChapterDraftVersionModel.promotion_state == "promoted",
                ))
                .group_by(ChapterModel.project_id, ProjectModel.target_chapters)
                .having(
                    func.count(func.distinct(ChapterModel.id)) == ProjectModel.target_chapters,
                    func.count(func.distinct(ChapterDraftVersionModel.chapter_id)) == ProjectModel.target_chapters,
                )
            )
            candidates = list(await session.scalars(select(ProjectModel).where(
                ProjectModel.id.in_(census),
                ProjectModel.status.in_(("writing", "revising", "completed")),
                ProjectModel.target_chapters > 0,
            ).order_by(ProjectModel.id)))
        for project in candidates:
            if project.id in halted or project.slug in owners:
                continue
            if not _export_needs_probe(project, settings):
                continue
            async with get_server_session() as session:
                checked = await preflight_export_only_recovery(
                    session, settings, project_id=project.id, requested_by=REQUESTED_BY,
                )
            if checked.status != ExportOnlyStatus.ELIGIBLE:
                continue
            job_id = "export-only:heal:" + project.slug
            job = await redis.enqueue_job(
                "run_export_only_recovery_task", workflow_run_id=job_id,
                payload={"project_id": str(project.id), "project_slug": project.slug},
                _job_id=job_id, _expires=timedelta(days=1),
            )
            if job is None and await _clear_completed_export_result(redis, settings, project, job_id):
                job = await redis.enqueue_job(
                    "run_export_only_recovery_task", workflow_run_id=job_id,
                    payload={"project_id": str(project.id), "project_slug": project.slug},
                    _job_id=job_id, _expires=timedelta(days=1),
                )
            if job is not None:
                queued.append({"slug": project.slug, "task_id": job_id, "heal_kind": "export_only"})
    except Exception as error:  # No failed probe can fall back to a writing job.
        logger.warning("export-only scan refused (%s)", type(error).__name__)
    return queued


async def run_export_only_recovery_task(ctx, workflow_run_id, payload):
    """Recheck native controls, sources and finance under the acquired project lease."""
    from bestseller.worker.tasks import _acquire_pipeline_live, _pipeline_live_guard

    settings = get_settings()
    project_id = UUID(payload["project_id"])
    async with get_server_session() as session:
        project = await session.get(ProjectModel, project_id)
        if project is None or project.slug != payload["project_slug"]:
            return {"status": "blocked", "reason": "project_identity_mismatch", "provider_calls": 0}
        if not isinstance(session.bind, AsyncEngine):
            return {"status": "blocked", "reason": "independent_engine_required", "provider_calls": 0}
        factory = async_sessionmaker(session.bind, expire_on_commit=False)
        scope, slug = project_execution_scope(project), project.slug
    redis = ctx.get("redis")
    token = uuid4().hex
    state = await _acquire_pipeline_live(redis, slug, token)
    if state != "acquired":
        return {"status": "blocked", "reason": "project_owner_unavailable", "provider_calls": 0}
    with bind_execution_scope(scope):
        async with _pipeline_live_guard(redis, slug, token, state):
            result = await run_export_only_recovery(
                factory, settings, project_id=project_id, requested_by=REQUESTED_BY,
            )
    return {**result.to_dict(), "workflow_run_id": workflow_run_id, "provider_calls": 0}
