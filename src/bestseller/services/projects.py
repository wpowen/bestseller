from __future__ import annotations

import asyncio
import copy
import json
import logging
import shutil
from pathlib import Path
from typing import Any

from sqlalchemy import func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.domain.enums import ProjectType
from bestseller.domain.planning import PlanningArtifactCreate
from bestseller.domain.project import ChapterCreate, ProjectCreate, SceneCardCreate, VolumeCreate
from bestseller.infra.db.models import (
    ChapterModel,
    PlanningArtifactVersionModel,
    ProjectModel,
    SceneCardModel,
    StyleGuideModel,
    VolumeModel,
)
from bestseller.services.generation_policy import apply_new_project_generation_policy
from bestseller.services.truth_version import maybe_bump_project_truth_version
from bestseller.services.writing_profile import (
    build_project_metadata,
    resolve_project_create_writing_profile,
)
from bestseller.settings import AppSettings


logger = logging.getLogger(__name__)
_DELETED_PROJECTS_REGISTRY = ".deleted-projects.json"

# DB delete can momentarily race a concurrent self-heal / repair run that holds
# row locks on the project. The tombstone written before the delete stops new
# heal bursts, so a short bounded retry lets the in-flight one drain and the
# delete succeed on the same user click instead of erroring out.
_DB_DELETE_MAX_ATTEMPTS = 3
_DB_DELETE_RETRY_DELAY_SECONDS = 1.0


def _deleted_projects_registry_path(settings: AppSettings) -> Path:
    return Path(settings.output.base_dir).resolve() / _DELETED_PROJECTS_REGISTRY


def _load_deleted_project_registry(settings: AppSettings) -> dict[str, Any]:
    path = _deleted_projects_registry_path(settings)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _write_deleted_project_registry(settings: AppSettings, payload: dict[str, Any]) -> None:
    path = _deleted_projects_registry_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def mark_project_delete_tombstone(settings: AppSettings, slug: str) -> None:
    registry = _load_deleted_project_registry(settings)
    deleted = registry.get("deleted")
    if not isinstance(deleted, dict):
        deleted = {}
    deleted[slug] = True
    registry["deleted"] = deleted
    _write_deleted_project_registry(settings, registry)


def clear_project_delete_tombstone(settings: AppSettings, slug: str) -> None:
    registry = _load_deleted_project_registry(settings)
    deleted = registry.get("deleted")
    if not isinstance(deleted, dict) or slug not in deleted:
        return
    deleted.pop(slug, None)
    registry["deleted"] = deleted
    _write_deleted_project_registry(settings, registry)


def is_project_delete_tombstoned(settings: AppSettings, slug: str) -> bool:
    deleted = _load_deleted_project_registry(settings).get("deleted")
    return isinstance(deleted, dict) and bool(deleted.get(slug))


async def get_project_by_slug(session: AsyncSession, slug: str) -> ProjectModel | None:
    return await session.scalar(select(ProjectModel).where(ProjectModel.slug == slug))


def _normalize_project_title(value: object) -> str:
    return " ".join(str(value or "").strip().casefold().split())


async def _find_duplicate_fanqie_short_project(
    session: AsyncSession,
    payload: ProjectCreate,
) -> ProjectModel | None:
    metadata = payload.metadata if isinstance(payload.metadata, dict) else {}
    if bool(metadata.get("allow_duplicate_project")):
        return None
    if payload.project_type != ProjectType.FANQIE_SHORT:
        return None

    normalized_title = _normalize_project_title(payload.title)
    if not normalized_title:
        return None

    return await session.scalar(
        select(ProjectModel)
        .where(
            ProjectModel.project_type == ProjectType.FANQIE_SHORT.value,
            ProjectModel.genre == payload.genre,
            ProjectModel.target_chapters == payload.target_chapters,
            func.lower(func.trim(ProjectModel.title)) == normalized_title,
        )
        .order_by(ProjectModel.updated_at.desc())
        .limit(1)
    )


async def initialize_project_genre_capabilities(
    session: AsyncSession,
    project: ProjectModel,
) -> dict[str, Any] | None:
    """Attach material-density and premium capability packs to a new project.

    Project creation should not fail just because the optional genre pack
    bootstrap cannot write in a test double or degraded environment.
    """

    try:
        from bestseller.services.material_density import hydrate_project_genre_pack

        return await hydrate_project_genre_pack(
            session,
            project_id=str(project.id),
            title=project.title,
            genre=project.genre,
            sub_genre=project.sub_genre,
            language=project.language,
            apply=True,
        )
    except Exception:
        logger.warning(
            "Failed to initialize genre capabilities for project %s",
            project.slug,
            exc_info=True,
        )
        return None


def _redis_key_belongs_to_slug(key: str, slug: str) -> bool:
    """True iff *slug* is a full colon-delimited segment of *key*.

    Self-heal Redis keys embed the slug as a segment:
    ``task:autowrite:heal:<slug>:progress`` / ``arq:job:repair:heal:<slug>``.
    Matching whole segments (not substrings) prevents ``book-v1`` from also
    sweeping ``book-v10``'s keys.
    """

    return bool(slug) and slug in key.split(":")


async def _purge_project_redis_keys(settings: AppSettings, slug: str) -> dict[str, Any]:
    """Delete a deleted project's Redis progress keys + scheduled self-heal jobs.

    ``delete_project_completely`` removes DB rows and disk artifacts but, without
    this, leaves ``task:*:heal:<slug>:*`` progress keys and
    ``arq:job:*:heal:<slug>`` scheduled self-heal jobs in Redis. The frontend
    task board reads those progress keys, so a deleted book keeps showing zombie
    tasks that survive page refreshes, and the self-heal scheduler keeps firing
    against the gone project. Scoped strictly to keys whose colon-delimited
    segments include the exact slug. Best-effort: a Redis outage is non-fatal —
    the DB/disk delete has already succeeded.
    """

    out: dict[str, Any] = {"deleted": 0, "error": None}
    url = getattr(getattr(settings, "redis", None), "url", None)
    if not url:
        # No Redis configured (or minimal settings) → nothing to purge.
        return out
    try:
        from redis.asyncio import from_url
    except Exception as exc:  # noqa: BLE001 — redis optional at import time
        out["error"] = f"redis_import_failed: {exc}"
        return out

    client = None
    try:
        client = from_url(url)
        to_delete: list[str] = []
        async for raw in client.scan_iter(match=f"*{slug}*"):
            key = raw.decode() if isinstance(raw, (bytes, bytearray)) else str(raw)
            if _redis_key_belongs_to_slug(key, slug):
                to_delete.append(key)
        if to_delete:
            out["deleted"] = int(await client.delete(*to_delete))
    except Exception as exc:  # noqa: BLE001
        out["error"] = f"redis_purge_failed: {exc}"
    finally:
        if client is not None:
            try:
                await client.aclose()
            except Exception:  # noqa: BLE001
                pass
    return out


async def delete_project_completely(
    session: AsyncSession,
    settings: AppSettings,
    slug: str,
) -> dict[str, Any]:
    """Fully remove a project: DB rows (cascades to all children), disk artifacts,
    and stale Redis progress keys / scheduled self-heal jobs.

    Returns a status dict. Raises ValueError if *slug* is empty or escapes
    the configured output base directory.
    """
    if not slug or "/" in slug or "\\" in slug or ".." in slug:
        raise ValueError(f"Invalid project slug: {slug!r}")

    result: dict[str, Any] = {
        "slug": slug,
        "db_deleted": False,
        "fs_deleted": False,
        "redis_keys_deleted": 0,
        "path": None,
        "errors": [],
    }

    # Step 0: Tombstone FIRST. A delete is an explicit "intent to remove"; we
    # record that intent before any fallible DB/disk work. If the DB delete
    # then races an in-flight self-heal run and fails, the project still cannot
    # be silently resurrected — self-heal and the project listing both honor
    # the tombstone — so the book stays gone from the user's view and a retry
    # finishes the cleanup, instead of looping forever as a half-dead project.
    try:
        mark_project_delete_tombstone(settings, slug)
    except OSError as exc:
        result["errors"].append(f"delete_tombstone_failed: {exc}")
        logger.exception("Failed to write delete tombstone for project %s", slug)

    # Step 0.6: 删前抢救转储 ─────────────────────────────────────────────────
    # 2026-08-17 真机事故：UI 批量删除 3 本书（209 章 / 964 草稿版本）时
    # backup sidecar 恰好没在运行，当日无任何转储——数据能救回纯属侥幸
    # （几小时前一次误打误撞的 up -d 让 sidecar 短暂跑过一次）。
    # DB 级「批量>5行」护栏也拦不住这条路：batch-delete 在循环里逐本删，
    # 每次都低于阈值。
    # 因此删除路径自带兜底：DB 级联删除**之前**，把项目行+全部章+当前稿
    # 正文导出到 output/backups/pre-delete/。不依赖 sidecar 是否在跑，
    # 导出失败只记 error 不阻断删除（删除是用户明确意图）。
    project = await get_project_by_slug(session, slug)
    # getattr 守卫：真实 ORM 行必有 id；测试假对象/异常行没有 id 时跳过转储
    # 而不是让 AttributeError 污染 errors 列表。
    if project is not None and getattr(project, "id", None) is not None:
        try:
            from datetime import datetime, timezone

            from sqlalchemy import select as _select

            from bestseller.infra.db.models import (
                ChapterDraftVersionModel as _CDV,
            )
            from bestseller.infra.db.models import ChapterModel as _CM

            _rows = (
                await session.execute(
                    _select(
                        _CM.chapter_number,
                        _CM.title,
                        _CM.hype_type,
                        _CDV.version_no,
                        _CDV.content_md,
                    )
                    .join(_CDV, _CDV.chapter_id == _CM.id)
                    .where(
                        _CM.project_id == project.id,
                        _CDV.is_current.is_(True),
                    )
                    .order_by(_CM.chapter_number)
                )
            ).all()
            _dump = {
                "slug": slug,
                "title": getattr(project, "title", None),
                "genre": getattr(project, "genre", None),
                "deleted_at": datetime.now(timezone.utc).isoformat(),
                "metadata": dict(getattr(project, "metadata_json", None) or {}),
                "chapters": [
                    {
                        "chapter_number": int(n),
                        "title": t,
                        "hype_type": h,
                        "version_no": int(v),
                        "content_md": c,
                    }
                    for n, t, h, v, c in _rows
                ],
            }
            _rescue_dir = Path(settings.output.base_dir) / "backups" / "pre-delete"
            _rescue_dir.mkdir(parents=True, exist_ok=True)
            _stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            _rescue_file = _rescue_dir / f"{slug}-{_stamp}.json"
            _rescue_file.write_text(
                json.dumps(_dump, ensure_ascii=False), encoding="utf-8"
            )
            result["pre_delete_dump"] = str(_rescue_file)
            logger.info(
                "Pre-delete rescue dump for %s: %d chapters → %s",
                slug,
                len(_dump["chapters"]),
                _rescue_file,
            )
        except Exception as exc:
            result["errors"].append(f"pre_delete_dump_failed: {exc}")
            logger.exception("Pre-delete rescue dump failed for project %s", slug)

    # Step 1: DB delete (cascades via ondelete="CASCADE"), with bounded retry on
    # transient lock contention from a concurrent self-heal / repair workflow.
    if project is None:
        result["errors"].append("project_not_found_in_db")
    else:
        last_exc: Exception | None = None
        for attempt in range(1, _DB_DELETE_MAX_ATTEMPTS + 1):
            try:
                await session.execute(text("SET LOCAL statement_timeout = '5min'"))
                await session.execute(text("SET LOCAL lock_timeout = '30s'"))
                await session.delete(project)
                await session.commit()
                result["db_deleted"] = True
                last_exc = None
                break
            except Exception as exc:  # noqa: BLE001
                await session.rollback()
                last_exc = exc
                logger.warning(
                    "Project %s DB delete attempt %d/%d failed: %s",
                    slug,
                    attempt,
                    _DB_DELETE_MAX_ATTEMPTS,
                    exc,
                )
                if attempt < _DB_DELETE_MAX_ATTEMPTS:
                    await asyncio.sleep(_DB_DELETE_RETRY_DELAY_SECONDS)
                    # Re-fetch in the fresh transaction; a concurrent actor may
                    # have finished the delete (or freed the lock) meanwhile.
                    project = await get_project_by_slug(session, slug)
                    if project is None:
                        result["db_deleted"] = True
                        last_exc = None
                        break
        if last_exc is not None:
            result["errors"].append(f"db_delete_failed: {last_exc}")
            logger.error(
                "Failed to delete project %s from DB after %d attempts",
                slug,
                _DB_DELETE_MAX_ATTEMPTS,
            )
            # Don't touch disk if DB failed; tombstone above keeps it suppressed.
            return result

    # Step 2: Disk cleanup — strict path validation
    base_dir = Path(settings.output.base_dir).resolve()
    target_dir = (base_dir / slug).resolve()
    result["path"] = str(target_dir)
    try:
        target_dir.relative_to(base_dir)  # Raises if target escapes base
    except ValueError:
        result["errors"].append(f"path_escape_rejected: {target_dir}")
        logger.error("Refusing to rmtree outside output base: %s", target_dir)
        return result

    if target_dir.exists() and target_dir.is_dir():
        try:
            shutil.rmtree(target_dir, ignore_errors=False)
            result["fs_deleted"] = True
        except Exception as exc:  # noqa: BLE001
            result["errors"].append(f"fs_delete_failed: {exc}")
            logger.exception("Failed to rmtree %s", target_dir)
    else:
        # Nothing on disk — treat as success (idempotent)
        result["fs_deleted"] = True

    # Step 3: Redis cleanup — clear the deleted project's progress keys and
    # scheduled self-heal jobs so the frontend task board stops showing zombie
    # tasks (which survive refreshes) and the self-heal scheduler stops firing
    # against the gone book. Best-effort; a Redis outage is non-fatal.
    redis_result = await _purge_project_redis_keys(settings, slug)
    result["redis_keys_deleted"] = redis_result["deleted"]
    if redis_result["error"]:
        result["errors"].append(redis_result["error"])

    return result


# 建书页「调性」→ 文风关键词。UI 的取值是 epic/light/dark/hot(标签:宏大/轻松/
# 暗黑/热血)。此前 tone_preference 全库只到达一个地方 —— 构思 prompt 里那坨 JSON
# blob —— 而写手真正用的是 style_guide.tone_keywords(来自 preset/模型),两者毫无
# 关系,所以用户选「轻松」或「暗黑」对正文零影响。
_TONE_PREFERENCE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "epic": ("宏大", "厚重", "史诗感"),
    "light": ("轻松", "幽默", "明快"),
    "dark": ("暗黑", "冷峻", "压抑"),
    "hot": ("热血", "燃", "爽快"),
}


def _tone_keywords_for(payload: ProjectCreate, writing_profile: WritingProfile) -> list[str]:
    """Lead the style guide with the user's 调性 pick, then the profile's own tone.

    The pick leads rather than replaces — the UI promises 「调性只能在所选题材边界
    内生效」, so the genre profile's own keywords stay behind it.
    """

    base = [str(k).strip() for k in (writing_profile.style.tone_keywords or []) if str(k).strip()]
    if not base:
        base = [payload.genre]
    meta = payload.metadata if isinstance(payload.metadata, dict) else {}
    contract = meta.get("genre_intent_contract")
    preference = ""
    if isinstance(contract, dict):
        preference = str(contract.get("tone_preference") or "").strip().lower()
    lead = _TONE_PREFERENCE_KEYWORDS.get(preference, ())
    if not lead:
        return base
    ordered: list[str] = []
    for keyword in [*lead, *base]:
        if keyword and keyword not in ordered:
            ordered.append(keyword)
    return ordered


async def create_project(
    session: AsyncSession,
    payload: ProjectCreate,
    settings: AppSettings,
) -> ProjectModel:
    existing = await get_project_by_slug(session, payload.slug)
    if existing is not None:
        raise ValueError(f"Project slug '{payload.slug}' already exists.")
    duplicate = await _find_duplicate_fanqie_short_project(session, payload)
    if duplicate is not None:
        archived = ""
        duplicate_meta = getattr(duplicate, "metadata_json", None) or {}
        if isinstance(duplicate_meta, dict) and duplicate_meta.get("library_archived"):
            archived = " (archived)"
        raise ValueError(
            "Duplicate fanqie_short project title "
            f"'{payload.title}' for genre '{payload.genre}' already exists as "
            f"'{duplicate.slug}'{archived}."
        )
    clear_project_delete_tombstone(settings, payload.slug)

    writing_profile = resolve_project_create_writing_profile(payload)
    project = ProjectModel(
        slug=payload.slug,
        title=payload.title,
        language=payload.language,
        genre=payload.genre,
        sub_genre=payload.sub_genre,
        target_word_count=payload.target_word_count,
        target_chapters=payload.target_chapters,
        audience=payload.audience,
        project_type=payload.project_type.value,
        metadata_json=apply_new_project_generation_policy(
            build_project_metadata(payload, writing_profile)
        ),
    )
    session.add(project)
    await session.flush()

    style = StyleGuideModel(
        project_id=project.id,
        pov_type=writing_profile.style.pov_type or settings.generation.pov,
        tense=writing_profile.style.tense,
        tone_keywords=_tone_keywords_for(payload, writing_profile),
        prose_style=writing_profile.style.prose_style,
        sentence_style=writing_profile.style.sentence_style,
        info_density=writing_profile.style.info_density,
        dialogue_ratio=writing_profile.style.dialogue_ratio,
        taboo_words=writing_profile.style.taboo_words,
        taboo_topics=writing_profile.style.taboo_topics,
        reference_works=writing_profile.style.reference_works,
        custom_rules=writing_profile.style.custom_rules,
    )
    session.add(style)
    await session.flush()
    await initialize_project_genre_capabilities(session, project)
    try:
        from bestseller.services.planning_kernel import persist_project_planning_kernel

        persist_project_planning_kernel(
            project,
            output_base_dir=settings.output.base_dir,
        )
    except Exception:
        logger.warning(
            "Failed to initialize planning kernel for project %s",
            project.slug,
            exc_info=True,
        )
    return project


async def list_projects(session: AsyncSession) -> list[ProjectModel]:
    result = await session.scalars(select(ProjectModel).order_by(ProjectModel.created_at.desc()))
    return list(result)


async def create_or_get_volume(
    session: AsyncSession,
    project_id: Any,
    payload: VolumeCreate,
) -> VolumeModel:
    existing = await session.scalar(
        select(VolumeModel).where(
            VolumeModel.project_id == project_id,
            VolumeModel.volume_number == payload.volume_number,
        )
    )
    if existing is not None:
        return existing

    volume = VolumeModel(
        project_id=project_id,
        volume_number=payload.volume_number,
        title=payload.title,
        theme=payload.theme,
        goal=payload.goal,
        obstacle=payload.obstacle,
        target_word_count=payload.target_word_count,
        target_chapter_count=payload.target_chapter_count,
        status=payload.status.value,
    )
    session.add(volume)
    await session.flush()
    return volume


async def create_chapter(
    session: AsyncSession,
    project_slug: str,
    payload: ChapterCreate,
) -> ChapterModel:
    project = await get_project_by_slug(session, project_slug)
    if project is None:
        raise ValueError(f"Project '{project_slug}' was not found.")

    existing = await session.scalar(
        select(ChapterModel).where(
            ChapterModel.project_id == project.id,
            ChapterModel.chapter_number == payload.chapter_number,
        )
    )
    if existing is not None:
        raise ValueError(f"Chapter {payload.chapter_number} already exists for '{project_slug}'.")

    volume = await create_or_get_volume(
        session,
        project.id,
        VolumeCreate(volume_number=payload.volume_number, title=f"Volume {payload.volume_number}"),
    )
    chapter = ChapterModel(
        project_id=project.id,
        volume_id=volume.id,
        chapter_number=payload.chapter_number,
        title=payload.title,
        chapter_goal=payload.chapter_goal,
        opening_situation=payload.opening_situation,
        main_conflict=payload.main_conflict,
        hook_type=payload.hook_type,
        hook_description=payload.hook_description,
        target_word_count=payload.target_word_count,
        status=payload.status.value,
        information_revealed=[],
        information_withheld=[],
        foreshadowing_actions={},
    )
    session.add(chapter)
    await session.flush()
    return chapter


async def create_scene_card(
    session: AsyncSession,
    project_slug: str,
    chapter_number: int,
    payload: SceneCardCreate,
) -> SceneCardModel:
    project = await get_project_by_slug(session, project_slug)
    if project is None:
        raise ValueError(f"Project '{project_slug}' was not found.")

    chapter = await session.scalar(
        select(ChapterModel).where(
            ChapterModel.project_id == project.id,
            ChapterModel.chapter_number == chapter_number,
        )
    )
    if chapter is None:
        raise ValueError(f"Chapter {chapter_number} was not found for '{project_slug}'.")

    existing = await session.scalar(
        select(SceneCardModel).where(
            SceneCardModel.chapter_id == chapter.id,
            SceneCardModel.scene_number == payload.scene_number,
        )
    )
    if existing is not None:
        raise ValueError(
            f"Scene {payload.scene_number} already exists in chapter {chapter_number}."
        )

    scene = SceneCardModel(
        project_id=project.id,
        chapter_id=chapter.id,
        scene_number=payload.scene_number,
        scene_type=payload.scene_type,
        title=payload.title,
        time_label=payload.time_label,
        participants=payload.participants,
        purpose=payload.purpose,
        entry_state=payload.entry_state,
        exit_state=payload.exit_state,
        key_dialogue_beats=payload.key_dialogue_beats,
        sensory_anchors=payload.sensory_anchors,
        forbidden_actions=payload.forbidden_actions,
        hook_requirement=payload.hook_requirement,
        metadata_json=payload.metadata,
        target_word_count=payload.target_word_count,
        status=payload.status.value,
    )
    session.add(scene)
    await session.flush()
    return scene


_FORCED_VERSION_NOTE = "input_hash matched but content differs — forced new version"


def _strip_meta(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: item for key, item in value.items() if key != "_meta"}
    return value


def _canonical_content_json(value: Any) -> str:
    return json.dumps(_strip_meta(value), ensure_ascii=False, sort_keys=True, default=str)


async def import_planning_artifact(
    session: AsyncSession,
    project_slug: str,
    payload: PlanningArtifactCreate,
) -> PlanningArtifactVersionModel:
    project = await get_project_by_slug(session, project_slug)
    if project is None:
        raise ValueError(f"Project '{project_slug}' was not found.")
    original_content = payload.content
    content = original_content
    if isinstance(content, dict):
        from bestseller.services.prewrite_quality_profile import planning_artifact_meta

        content = copy.deepcopy(content)
        meta = content.get("_meta") if isinstance(content.get("_meta"), dict) else {}
        content["_meta"] = {
            **planning_artifact_meta(project),
            **meta,
        }

    version_filters = [
        PlanningArtifactVersionModel.project_id == project.id,
        PlanningArtifactVersionModel.artifact_type == payload.artifact_type.value,
    ]
    if payload.scope_ref_id is None:
        version_filters.append(PlanningArtifactVersionModel.scope_ref_id.is_(None))
    else:
        version_filters.append(PlanningArtifactVersionModel.scope_ref_id == payload.scope_ref_id)

    input_hash = None
    if isinstance(content, dict):
        meta = content.get("_meta")
        if isinstance(meta, dict):
            raw_hash = meta.get("input_hash")
            if isinstance(raw_hash, str) and raw_hash.strip():
                input_hash = raw_hash.strip()

    forced_new_version_note: str | None = None
    if input_hash:
        reusable_stmt = (
            select(PlanningArtifactVersionModel)
            .where(
                *version_filters,
                PlanningArtifactVersionModel.status == "approved",
                PlanningArtifactVersionModel.content["_meta"]["input_hash"].astext == input_hash,
            )
            .order_by(
                PlanningArtifactVersionModel.version_no.desc(),
                PlanningArtifactVersionModel.created_at.desc(),
            )
            .limit(1)
        )
        reusable = await session.scalar(reusable_stmt)
        if reusable is not None:
            # R3 reuse trap: a stale ``_meta.input_hash`` on edited content
            # used to short-circuit the import and silently return the OLD
            # version. Reuse is only safe when the content (sans _meta) is
            # actually identical; otherwise force a new version and record
            # why in its notes.
            if _canonical_content_json(content) == _canonical_content_json(
                reusable.content
            ):
                return reusable
            forced_new_version_note = _FORCED_VERSION_NOTE
            logger.warning(
                "Planning artifact import for '%s' (%s): input_hash %s matched "
                "version %s but content differs — forcing a new version",
                project_slug,
                payload.artifact_type.value,
                input_hash,
                reusable.version_no,
            )

    exact_stmt = (
        select(PlanningArtifactVersionModel)
        .where(
            *version_filters,
            PlanningArtifactVersionModel.status == "approved",
            or_(
                PlanningArtifactVersionModel.content == content,
                PlanningArtifactVersionModel.content == original_content,
            ),
        )
        .order_by(
            PlanningArtifactVersionModel.version_no.desc(),
            PlanningArtifactVersionModel.created_at.desc(),
        )
        .limit(1)
    )
    exact = await session.scalar(exact_stmt)
    if exact is not None:
        return exact

    version_stmt = select(func.coalesce(func.max(PlanningArtifactVersionModel.version_no), 0)).where(
        *version_filters
    )
    next_version = int((await session.scalar(version_stmt)) or 0) + 1

    notes = "; ".join(
        part for part in (payload.notes, forced_new_version_note) if part
    ) or None
    artifact = PlanningArtifactVersionModel(
        project_id=project.id,
        artifact_type=payload.artifact_type.value,
        scope_ref_id=payload.scope_ref_id,
        version_no=next_version,
        status="approved",
        schema_version="1.0",
        content=content,
        source_run_id=payload.source_run_id,
        notes=notes,
    )
    session.add(artifact)
    maybe_bump_project_truth_version(
        project,
        artifact_type=payload.artifact_type,
        content=payload.content,
        scope_ref_id=payload.scope_ref_id,
    )
    await session.flush()
    return artifact


def load_json_file(path: Path) -> Any:
    import json

    return json.loads(path.read_text(encoding="utf-8"))
