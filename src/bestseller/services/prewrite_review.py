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
from bestseller.services.planning_capability_selection import (
    CAPABILITIES,
    resolve_planning_capability,
)
from bestseller.services.projects import get_project_by_slug
from bestseller.settings import AppSettings

PREWRITE_REVIEW_METADATA_KEY = "prewrite_review"
PREWRITE_REVIEW_SCHEMA_VERSION = "prewrite-review.v1"

# evaluate_and_persist_fanqie_long_readiness consumes chapter prose. Its
# result is downstream evidence, not a changed input to write-start approval.
# Unknown artifact types remain reviewable sources; do not use a prefix rule.
_DOWNSTREAM_EVIDENCE_ARTIFACT_TYPES = frozenset({"fanqie_long_ranking_readiness"})

# Exact producer finding codes, not word/genre matching. Other planning,
# reference, Canon, Timeline and authored instructions retain their behavior.
_SCENE_METHOD_FINDINGS = {
    "story_design_kernel_missing": "story_design_kernel",
    "story_design_contract_invalid": "story_design_kernel",
    "story_design_contract_thin": "story_design_kernel",
    "distilled_worldview_not_bound": "story_design_kernel",
    "emotion_driven_kernel_missing": "emotion_driven_kernel",
    "empathy_contract_missing": "emotion_driven_kernel",
    "bomb_contract_not_consumed": "emotion_driven_kernel",
    "antagonist_moral_contract_thin": "emotion_driven_kernel",
    "ending_texture_missing": "emotion_driven_kernel",
    "public_emotion_kernel_missing": "public_emotion_kernel",
    "public_emotion_bridge_missing": "public_emotion_kernel",
    "entry_system_kernel_invalid": "entry_system_kernel",
}


def project_scene_prewrite_directives(
    project_or_metadata: Any,
    *,
    language: str | None = None,
) -> dict[str, Any]:
    """Reproject only exact framework-method directives against live selection.

    The reserved prewrite cache's exact producer templates identify method
    instructions; unknown/authored text is never inferred from words or an old
    receipt. Existing context warnings are not inputs to this projection.
    Original caches/reports remain untouched; this returns no grade or waiver.
    """
    from collections.abc import Mapping
    from copy import deepcopy

    from bestseller.services.planning_kernel import (
        _DIRECTIVE_TEMPLATES_EN,
        _DIRECTIVE_TEMPLATES_ZH,
        PrewriteReadinessReport,
        build_prewrite_repair_directives,
    )
    from bestseller.services.prompt_source_policy import content_hash

    metadata = (
        project_or_metadata
        if isinstance(project_or_metadata, Mapping)
        else getattr(project_or_metadata, "metadata_json", None)
    )
    metadata = metadata if isinstance(metadata, Mapping) else {}
    selections = {key: resolve_planning_capability(metadata, key) for key in CAPABILITIES}
    catalog: dict[str, list[str]] = {}
    for templates in (_DIRECTIVE_TEMPLATES_EN, _DIRECTIVE_TEMPLATES_ZH):
        for code, text in templates.items():
            if code in _SCENE_METHOD_FINDINGS:
                catalog.setdefault(text, []).append(code)
    excluded, retained = [], []
    cached = metadata.get("prewrite_repair_directives")
    report = metadata.get("prewrite_readiness_report")
    report_data = report.to_dict() if isinstance(report, PrewriteReadinessReport) else report
    directives = []
    if cached:
        origin = "prewrite_repair_directives"
        producer_cache = isinstance(cached, (list, tuple))
        items = (
            cached
            if isinstance(cached, (list, tuple, Mapping))
            else [cached]
            if isinstance(cached, str)
            else []
        )
        for index, item in enumerate(items):
            text = str(item).strip()
            if not text:
                continue
            codes = catalog.get(text, []) if producer_cache and isinstance(item, str) else []
            # A future producer reusing a template for multiple capabilities
            # cannot silently discard it if any owning capability is selected.
            inactive = codes and all(
                not selections[_SCENE_METHOD_FINDINGS[code]]["selected"] for code in codes
            )
            evidence = {"loc": [origin, index], "text_sha256": content_hash(text), "codes": codes}
            if inactive:
                excluded.append({**evidence, "reason": "current_method_not_selected"})
            else:
                directives.append(text)
                retained.append(evidence)
    elif isinstance(report_data, Mapping):
        origin = "prewrite_readiness_report"
        projected = deepcopy(dict(report_data))
        for field in ("blocking_findings", "warnings"):
            items = projected.get(field)
            if not isinstance(items, list):
                continue
            keep = []
            for index, item in enumerate(items):
                code = item.get("code") if isinstance(item, Mapping) else None
                capability = _SCENE_METHOD_FINDINGS.get(code) if isinstance(code, str) else None
                if capability and not selections[capability]["selected"]:
                    excluded.append(
                        {
                            "loc": [origin, field, index],
                            "code": code,
                            "reason": "current_method_not_selected",
                            "finding_sha256": content_hash(item),
                        }
                    )
                else:
                    keep.append(item)
            projected[field] = keep
        directives = build_prewrite_repair_directives(projected, language=language)
    else:
        origin = "none"
    return {
        "directives": directives,
        "receipt": {
            "schema": "scene-prewrite-directive-projection.v1",
            "origin": origin,
            "current_selections": selections,
            "excluded": excluded,
            "retained": retained,
            "cached_directives_sha256": content_hash(cached),
            "report_sha256": content_hash(report_data),
            "producer_contract_sha256": content_hash(
                {"finding_capabilities": _SCENE_METHOD_FINDINGS, "exact_templates": catalog}
            ),
            "projected_directives_sha256": content_hash(directives),
            "projected_count": len(directives),
            "raw_history_modified": False,
        },
    }


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


def required_prewrite_artifact_types(
    *,
    target_chapters: int | None,
    project_metadata: dict[str, Any] | None = None,
) -> tuple[str, ...]:
    """Return the write-start contract for a project of the given size.

    ``act_plan`` is a long-serial planning layer.  Short books still have
    volume/chapter outlines and all foundation artifacts, but must not be
    blocked waiting for an artifact the planner intentionally skipped.
    """

    selections = {key: resolve_planning_capability(project_metadata, key) for key in CAPABILITIES}
    required = [
        key
        for key in PREWRITE_REQUIRED_ARTIFACT_TYPES
        if key not in selections or selections[key]["selected"]
    ]
    for key, receipt in selections.items():
        if receipt["selected"] and key not in required:
            required.append(key)
    try:
        chapters = int(target_chapters or 0)
    except (TypeError, ValueError):
        chapters = 0
    if chapters >= PREWRITE_ACT_PLAN_MIN_CHAPTERS:
        # Keep the macro artifact adjacent to the foundation/macro-plan
        # surfaces when it is applicable; ordering is part of the UI contract.
        required.insert(required.index("volume_plan"), "act_plan")
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

    selections = {key: resolve_planning_capability(project, key) for key in CAPABILITIES}
    inactive = {key for key, receipt in selections.items() if not receipt["selected"]}
    snapshot = {
        artifact_type: {
            "artifact_id": str(row.id),
            "version_no": int(row.version_no),
            "status": row.status,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }
        for artifact_type, row in latest_by_type.items()
        if artifact_type not in inactive | _DOWNSTREAM_EVIDENCE_ARTIFACT_TYPES
    }
    review = dict((project.metadata_json or {}).get(PREWRITE_REVIEW_METADATA_KEY) or {})
    approved_snapshot = review.get("approved_snapshot")
    active_approved = (
        {
            key: value for key, value in approved_snapshot.items()
            if key not in inactive | _DOWNSTREAM_EVIDENCE_ARTIFACT_TYPES
        }
        if isinstance(approved_snapshot, dict)
        else approved_snapshot
    )
    approved = review.get("status") == "approved" and active_approved == snapshot
    stale_artifact_types: list[str] = []
    if review.get("status") == "approved" and active_approved != snapshot:
        old = active_approved if isinstance(active_approved, dict) else {}
        for artifact_type, current in snapshot.items():
            if old.get(artifact_type) != current:
                stale_artifact_types.append(artifact_type)

    missing_required = [
        artifact_type
        for artifact_type in required_prewrite_artifact_types(
            target_chapters=getattr(project, "target_chapters", None),
            project_metadata=project.metadata_json,
        )
        if artifact_type not in latest_by_type
    ]
    selected_material_missing = any(
        key in selections and selections[key]["selected"] for key in missing_required
    )
    if selected_material_missing:
        approved = False
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
        "status": "approved"
        if approved
        else (
            "needs_review" if selected_material_missing else review.get("status") or "needs_review"
        ),
        "is_approved": approved,
        "approved_at": review.get("approved_at"),
        "approved_by": review.get("approved_by"),
        "approved_snapshot": approved_snapshot if isinstance(approved_snapshot, dict) else {},
        "current_snapshot": snapshot,
        "snapshot_projection": {
            "policy": "prewrite-source-vs-downstream-evidence.v1",
            "historical_approval_unchanged": True,
            "excluded_downstream_current": {
                str(row.artifact_type): {
                    "artifact_id": str(row.id), "version_no": int(row.version_no),
                    "status": row.status,
                }
                for row in latest_by_type.values()
                if str(row.artifact_type) in _DOWNSTREAM_EVIDENCE_ARTIFACT_TYPES
            },
            "excluded_downstream_approved": {
                key: value for key, value in (approved_snapshot or {}).items()
                if key in _DOWNSTREAM_EVIDENCE_ARTIFACT_TYPES
            } if isinstance(approved_snapshot, dict) else {},
        },
        "missing_required_artifacts": missing_required,
        "stale_artifact_types": stale_artifact_types,
        "planning_capabilities": selections,
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
                "applicability": "downstream_evidence"
                if str(row.artifact_type) in _DOWNSTREAM_EVIDENCE_ARTIFACT_TYPES
                else selections[str(row.artifact_type)]["status"]
                if str(row.artifact_type) in selections
                else "required_or_reviewable",
                "approved_in_snapshot": (
                    str(row.artifact_type) in snapshot
                    and isinstance(approved_snapshot, dict)
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
            # Canonical is a stronger source-authority state, not a pending
            # editorial decision. Reviewing materials must not demote it and
            # invalidate an otherwise unchanged pinned foundation cohort.
            if row.status != "canonical":
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
