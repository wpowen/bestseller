from __future__ import annotations

from datetime import UTC, datetime

# ruff: noqa: ANN401, RUF001
import json
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.infra.db.models import (
    PlanningArtifactVersionModel,
    RewriteTaskModel,
)
from bestseller.services.llm import LLMCompletionRequest, complete_text
from bestseller.services.projects import get_project_by_slug
from bestseller.settings import AppSettings

PREWRITE_REVIEW_METADATA_KEY = "prewrite_review"
PREWRITE_REVIEW_SCHEMA_VERSION = "prewrite-review.v1"

# The planner only materializes an act-level plan for genuinely long serials
# (see ``settings.pipeline.act_plan_threshold``).  Keeping ``act_plan`` in the
# unconditional prewrite contract made every short book impossible to start:
# a 20-chapter project could never satisfy a material that the planner was
# explicitly designed not to emit.  The review surface therefore derives its
# required artifacts from the project's target size instead of treating the
# optional macro layer as universal.
PREWRITE_ACT_PLAN_MIN_CHAPTERS = 50

PREWRITE_REQUIRED_ARTIFACT_TYPES: tuple[str, ...] = (
    "premise",
    "book_spec",
    "world_spec",
    "cast_spec",
    "story_design_kernel",
    "volume_plan",
    "volume_chapter_outline",
    "chapter_outline_batch",
    "plan_validation",
    "prewrite_readiness",
)


def required_prewrite_artifact_types(*, target_chapters: int | None) -> tuple[str, ...]:
    """Return the write-start contract for a project of the given size.

    ``act_plan`` is a long-serial planning layer.  Short books still have
    volume/chapter outlines and all foundation artifacts, but must not be
    blocked waiting for an artifact the planner intentionally skipped.
    """

    required = list(PREWRITE_REQUIRED_ARTIFACT_TYPES)
    try:
        chapters = int(target_chapters or 0)
    except (TypeError, ValueError):
        chapters = 0
    if chapters >= PREWRITE_ACT_PLAN_MIN_CHAPTERS:
        # Keep the macro artifact adjacent to the foundation/macro-plan
        # surfaces when it is applicable; ordering is part of the UI contract.
        required.insert(5, "act_plan")
    return tuple(required)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _artifact_content_preview(content: Any, *, limit: int = 280) -> str:
    try:
        text = json.dumps(content, ensure_ascii=False, sort_keys=True)
    except TypeError:
        text = str(content)
    return text[:limit]


async def load_prewrite_review_payload(
    session: AsyncSession,
    project_slug: str,
) -> dict[str, Any]:
    project = await get_project_by_slug(session, project_slug)
    if project is None:
        raise ValueError(f"Project '{project_slug}' was not found.")

    rows = list(
        (
            await session.execute(
                select(PlanningArtifactVersionModel)
                .where(PlanningArtifactVersionModel.project_id == project.id)
                .order_by(
                    PlanningArtifactVersionModel.artifact_type.asc(),
                    PlanningArtifactVersionModel.version_no.desc(),
                    PlanningArtifactVersionModel.created_at.desc(),
                )
            )
        ).scalars()
    )
    latest_by_type: dict[str, PlanningArtifactVersionModel] = {}
    for row in rows:
        latest_by_type.setdefault(str(row.artifact_type), row)

    snapshot = {
        artifact_type: {
            "artifact_id": str(row.id),
            "version_no": int(row.version_no),
            "status": row.status,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }
        for artifact_type, row in latest_by_type.items()
    }
    review = dict((project.metadata_json or {}).get(PREWRITE_REVIEW_METADATA_KEY) or {})
    approved_snapshot = review.get("approved_snapshot")
    approved = review.get("status") == "approved" and approved_snapshot == snapshot
    stale_artifact_types: list[str] = []
    if review.get("status") == "approved" and approved_snapshot != snapshot:
        old = approved_snapshot if isinstance(approved_snapshot, dict) else {}
        for artifact_type, current in snapshot.items():
            if old.get(artifact_type) != current:
                stale_artifact_types.append(artifact_type)

    missing_required = [
        artifact_type
        for artifact_type in required_prewrite_artifact_types(
            target_chapters=getattr(project, "target_chapters", None),
        )
        if artifact_type not in latest_by_type
    ]
    issue_rows = list(
        (
            await session.execute(
                select(RewriteTaskModel)
                .where(
                    RewriteTaskModel.project_id == project.id,
                    RewriteTaskModel.trigger_type == "prewrite_material_issue",
                )
                .order_by(RewriteTaskModel.created_at.desc())
                .limit(30)
            )
        ).scalars()
    )
    issues = [
        {
            "task_id": str(task.id),
            "artifact_id": str(task.trigger_source_id) if task.trigger_source_id else None,
            "status": task.status,
            "strategy": task.rewrite_strategy,
            "instructions": task.instructions,
            "attempts": int(task.attempts or 0),
            "created_at": task.created_at.isoformat() if task.created_at else None,
            "updated_at": task.updated_at.isoformat() if task.updated_at else None,
            "metadata": task.metadata_json or {},
        }
        for task in issue_rows
    ]
    return {
        "schema_version": PREWRITE_REVIEW_SCHEMA_VERSION,
        "project_slug": project_slug,
        "status": "approved" if approved else review.get("status") or "needs_review",
        "is_approved": approved,
        "approved_at": review.get("approved_at"),
        "approved_by": review.get("approved_by"),
        "approved_snapshot": approved_snapshot if isinstance(approved_snapshot, dict) else {},
        "current_snapshot": snapshot,
        "missing_required_artifacts": missing_required,
        "stale_artifact_types": stale_artifact_types,
        "materials": [
            {
                "artifact_id": str(row.id),
                "artifact_type": row.artifact_type,
                "version_no": int(row.version_no),
                "status": row.status,
                "schema_version": row.schema_version,
                "scope_ref_id": str(row.scope_ref_id) if row.scope_ref_id else None,
                "created_at": row.created_at.isoformat() if row.created_at else None,
                "created_by": row.created_by,
                "notes": row.notes,
                "content_preview": _artifact_content_preview(row.content),
                "approved_in_snapshot": (
                    isinstance(approved_snapshot, dict)
                    and approved_snapshot.get(str(row.artifact_type))
                    == snapshot.get(str(row.artifact_type))
                ),
            }
            for row in latest_by_type.values()
        ],
        "issues": issues,
    }


async def assert_prewrite_review_approved(
    session: AsyncSession,
    project_slug: str,
) -> dict[str, Any]:
    payload = await load_prewrite_review_payload(session, project_slug)
    if not payload["is_approved"]:
        raise PermissionError(
            "正文生成已被写前物料审核门禁拦截。请在物料页审核通过当前最新物料后再进入正文。"
        )
    return payload


async def approve_prewrite_materials(
    session: AsyncSession,
    project_slug: str,
    *,
    approved_by: str = "web-ui",
    notes: str = "",
) -> dict[str, Any]:
    project = await get_project_by_slug(session, project_slug)
    if project is None:
        raise ValueError(f"Project '{project_slug}' was not found.")
    payload = await load_prewrite_review_payload(session, project_slug)
    artifact_ids = [
        UUID(str(meta["artifact_id"]))
        for meta in payload["current_snapshot"].values()
        if isinstance(meta, dict) and meta.get("artifact_id")
    ]
    if artifact_ids:
        artifact_rows = list(
            (
                await session.execute(
                    select(PlanningArtifactVersionModel).where(
                        PlanningArtifactVersionModel.id.in_(artifact_ids)
                    )
                )
            ).scalars()
        )
        for row in artifact_rows:
            row.status = "approved"
        await session.flush()
        payload = await load_prewrite_review_payload(session, project_slug)
    review = {
        "schema_version": PREWRITE_REVIEW_SCHEMA_VERSION,
        "status": "approved",
        "approved_at": _now_iso(),
        "approved_by": approved_by,
        "notes": notes,
        "approved_snapshot": payload["current_snapshot"],
    }
    project.metadata_json = {
        **(project.metadata_json or {}),
        PREWRITE_REVIEW_METADATA_KEY: review,
    }
    await session.flush()
    return await load_prewrite_review_payload(session, project_slug)


async def edit_prewrite_material(
    session: AsyncSession,
    project_slug: str,
    *,
    artifact_id: UUID,
    content: dict[str, Any],
    notes: str = "",
    edited_by: str = "web-ui",
) -> dict[str, Any]:
    project = await get_project_by_slug(session, project_slug)
    if project is None:
        raise ValueError(f"Project '{project_slug}' was not found.")
    artifact = await session.get(PlanningArtifactVersionModel, artifact_id)
    if artifact is None or artifact.project_id != project.id:
        raise ValueError("Planning artifact was not found for this project.")
    max_version = int(
        (
            await session.scalar(
                select(func.coalesce(func.max(PlanningArtifactVersionModel.version_no), 0)).where(
                    PlanningArtifactVersionModel.project_id == project.id,
                    PlanningArtifactVersionModel.artifact_type == artifact.artifact_type,
                    PlanningArtifactVersionModel.scope_ref_id == artifact.scope_ref_id,
                )
            )
        )
        or 0
    )
    new_artifact = PlanningArtifactVersionModel(
        project_id=project.id,
        artifact_type=artifact.artifact_type,
        scope_ref_id=artifact.scope_ref_id,
        version_no=max_version + 1,
        status="needs_review",
        schema_version=artifact.schema_version or "1.0",
        content=content,
        source_run_id=artifact.source_run_id,
        notes=notes or f"Manual edit from {edited_by}; previous v{artifact.version_no}",
        created_by=edited_by,
    )
    session.add(new_artifact)
    project.metadata_json = {
        **(project.metadata_json or {}),
        PREWRITE_REVIEW_METADATA_KEY: {
            **dict((project.metadata_json or {}).get(PREWRITE_REVIEW_METADATA_KEY) or {}),
            "status": "needs_review",
            "invalidated_at": _now_iso(),
            "invalidated_by": edited_by,
            "invalidated_reason": "material_edited",
        },
    }
    await session.flush()
    return {
        "ok": True,
        "artifact_id": str(new_artifact.id),
        "artifact_type": new_artifact.artifact_type,
        "version_no": new_artifact.version_no,
        "status": new_artifact.status,
    }


async def create_prewrite_issue_task(
    session: AsyncSession,
    project_slug: str,
    *,
    artifact_id: UUID,
    instructions: str,
    requested_by: str = "web-ui",
) -> RewriteTaskModel:
    project = await get_project_by_slug(session, project_slug)
    if project is None:
        raise ValueError(f"Project '{project_slug}' was not found.")
    artifact = await session.get(PlanningArtifactVersionModel, artifact_id)
    if artifact is None or artifact.project_id != project.id:
        raise ValueError("Planning artifact was not found for this project.")
    task = RewriteTaskModel(
        project_id=project.id,
        trigger_type="prewrite_material_issue",
        trigger_source_id=artifact.id,
        rewrite_strategy="prewrite_material_repair",
        priority=3,
        status="queued",
        instructions=instructions,
        context_required=["planning_artifact_content", "human_issue_description"],
        metadata_json={
            "requested_by": requested_by,
            "artifact_type": artifact.artifact_type,
            "artifact_version_no": artifact.version_no,
            "source_artifact_id": str(artifact.id),
        },
    )
    session.add(task)
    project.metadata_json = {
        **(project.metadata_json or {}),
        PREWRITE_REVIEW_METADATA_KEY: {
            **dict((project.metadata_json or {}).get(PREWRITE_REVIEW_METADATA_KEY) or {}),
            "status": "needs_review",
            "invalidated_at": _now_iso(),
            "invalidated_by": requested_by,
            "invalidated_reason": "material_issue_submitted",
        },
    }
    await session.flush()
    return task


async def repair_prewrite_material_from_task(
    session: AsyncSession,
    settings: AppSettings,
    project_slug: str,
    *,
    rewrite_task_id: UUID,
) -> dict[str, Any]:
    project = await get_project_by_slug(session, project_slug)
    if project is None:
        raise ValueError(f"Project '{project_slug}' was not found.")
    task = await session.get(RewriteTaskModel, rewrite_task_id)
    if task is None or task.project_id != project.id:
        raise ValueError("Rewrite task was not found for this project.")
    artifact_id = task.trigger_source_id
    if artifact_id is None:
        raise ValueError("Prewrite material task does not reference an artifact.")
    artifact = await session.get(PlanningArtifactVersionModel, artifact_id)
    if artifact is None or artifact.project_id != project.id:
        raise ValueError("Planning artifact was not found for this project.")

    original_json = json.dumps(artifact.content, ensure_ascii=False, indent=2, sort_keys=True)
    system_prompt = (
        "你是小说平台的写前物料修复编辑。只修复用户指出的问题，保持原有 JSON 结构、"
        "字段语义和正文生成契约。只输出合法 JSON，不要解释。"
    )
    user_prompt = (
        f"项目: {project.title} ({project.slug})\n"
        f"物料类型: {artifact.artifact_type} v{artifact.version_no}\n"
        f"用户问题描述:\n{task.instructions}\n\n"
        f"原始 JSON:\n{original_json}"
    )
    completion = await complete_text(
        session,
        settings,
        LLMCompletionRequest(
            logical_role="editor",
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            fallback_response=original_json,
            prompt_template="prewrite_material_repair",
            prompt_version="1.0",
            project_id=project.id,
            metadata={
                "project_slug": project.slug,
                "artifact_type": artifact.artifact_type,
                "rewrite_task_id": str(task.id),
            },
        ),
    )
    try:
        repaired_content = json.loads(completion.content)
        if not isinstance(repaired_content, dict):
            repaired_content = {"value": repaired_content}
    except json.JSONDecodeError:
        repaired_content = {
            **artifact.content,
            "_repair_note": completion.content,
        }
    max_version = int(
        (
            await session.scalar(
                select(func.coalesce(func.max(PlanningArtifactVersionModel.version_no), 0)).where(
                    PlanningArtifactVersionModel.project_id == project.id,
                    PlanningArtifactVersionModel.artifact_type == artifact.artifact_type,
                    PlanningArtifactVersionModel.scope_ref_id == artifact.scope_ref_id,
                )
            )
        )
        or 0
    )
    new_artifact = PlanningArtifactVersionModel(
        project_id=project.id,
        artifact_type=artifact.artifact_type,
        scope_ref_id=artifact.scope_ref_id,
        version_no=max_version + 1,
        status="needs_review",
        schema_version=artifact.schema_version or "1.0",
        content=repaired_content,
        source_run_id=artifact.source_run_id,
        notes=f"Repaired from issue task {task.id}; previous v{artifact.version_no}",
        created_by="prewrite-repair",
    )
    session.add(new_artifact)
    await session.flush()
    task.status = "completed"
    task.attempts = int(task.attempts or 0) + 1
    task.metadata_json = {
        **(task.metadata_json or {}),
        "model_name": completion.model_name,
        "generation_mode": completion.provider,
        "llm_run_id": str(completion.llm_run_id) if completion.llm_run_id else None,
        "result_artifact_id": str(new_artifact.id),
        "result_artifact_version_no": new_artifact.version_no,
    }
    project.metadata_json = {
        **(project.metadata_json or {}),
        PREWRITE_REVIEW_METADATA_KEY: {
            **dict((project.metadata_json or {}).get(PREWRITE_REVIEW_METADATA_KEY) or {}),
            "status": "needs_review",
            "invalidated_at": _now_iso(),
            "invalidated_by": "prewrite-repair",
            "invalidated_reason": "material_repaired",
        },
    }
    return {
        "ok": True,
        "rewrite_task_id": str(task.id),
        "artifact_id": str(new_artifact.id),
        "artifact_type": new_artifact.artifact_type,
        "version_no": new_artifact.version_no,
        "status": new_artifact.status,
    }
