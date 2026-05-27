"""Per-project runtime overlay for pipeline data-flow visualization."""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from pydantic import Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.domain.enums import ArtifactType, ProjectType
from bestseller.domain.project import ProjectStructureRead
from bestseller.infra.db.models import ChapterModel, PlanningArtifactVersionModel, ProjectModel
from bestseller.services.gate_registry import registered_block_metadata_keys
from bestseller.services.inspection import (
    build_project_structure,
    build_project_workflow_overview,
    build_story_bible_overview,
)
from bestseller.services.narrative import build_narrative_overview
from bestseller.services.pipeline_flow_schema import (
    PIPELINE_FLOW_SCHEMA_VERSION,
    PipelineFlowChapterRuntime,
    PipelineFlowEdgeDef,
    PipelineFlowIssue,
    PipelineFlowNodeDef,
    PipelineFlowNodeRuntime,
    PipelineFlowOverview,
    PipelinePathId,
    NodeStatus,
    build_pipeline_flow_schema,
    resolve_pipeline_path,
    node_applies,
)
from bestseller.services.projects import get_project_by_slug
from bestseller.settings import AppSettings


def _collect_step_index(
    workflow_runs: list[Any],
) -> dict[str, list[dict[str, Any]]]:
    index: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for run in workflow_runs:
        for step in run.steps:
            index[step.step_name].append(
                {
                    "workflow_run_id": str(run.workflow_run_id),
                    "workflow_type": run.workflow_type,
                    "status": step.status,
                    "error_message": step.error_message,
                    "output_ref": step.output_ref,
                }
            )
    return index


def _artifact_presence(
    rows: list[PlanningArtifactVersionModel],
) -> dict[str, dict[str, Any]]:
    latest: dict[str, PlanningArtifactVersionModel] = {}
    for row in rows:
        key = str(row.artifact_type)
        if key not in latest or row.version_no > latest[key].version_no:
            latest[key] = row
    return {
        key: {
            "version_no": row.version_no,
            "status": row.status,
            "artifact_id": str(row.id),
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }
        for key, row in latest.items()
    }


def _infer_node_status(
    defn: PipelineFlowNodeDef,
    *,
    active_path: PipelinePathId,
    step_index: dict[str, list[dict[str, Any]]],
    workflow_by_type: dict[str, list[Any]],
    artifact_map: dict[str, dict[str, Any]],
    structure: ProjectStructureRead,
    story_counts: dict[str, int],
    narrative_counts: dict[str, int],
    project_metadata: dict[str, Any],
    blocked_chapters: int,
) -> tuple[NodeStatus, dict[str, Any], list[dict[str, Any]]]:
    if not node_applies(defn, active_path):
        return "not_applicable", {}, []

    metrics: dict[str, Any] = {}
    workflow_refs: list[dict[str, Any]] = []
    node_issues: list[dict[str, Any]] = []

    for wf_type in defn.workflow_types:
        for run in workflow_by_type.get(wf_type, []):
            workflow_refs.append(
                {
                    "workflow_run_id": str(run.workflow_run_id),
                    "workflow_type": run.workflow_type,
                    "status": run.status,
                    "current_step": run.current_step,
                    "error_message": run.error_message,
                }
            )

    for step_name in defn.step_names:
        workflow_refs.extend(step_index.get(step_name, []))

    for artifact_type in defn.artifact_types:
        if artifact_type in artifact_map:
            metrics[f"artifact_{artifact_type}"] = artifact_map[artifact_type]

    if defn.id == "materialize_chapter_outline_batch":
        metrics["total_chapters"] = structure.total_chapters
        metrics["total_scenes"] = structure.total_scenes
    if defn.id == "materialize_story_bible":
        metrics.update(story_counts)
    if defn.id == "materialize_narrative_graph":
        metrics.update(narrative_counts)

    # Gate nodes from project/chapter metadata
    for gate in defn.gates:
        for key in gate.metadata_keys:
            if project_metadata.get(key):
                metrics["blocked"] = True
                metrics["block_key"] = key
                node_issues.append(
                    {
                        "code": "GATE_BLOCKED",
                        "message": f"project metadata has {key}",
                    }
                )

    # Status resolution
    if workflow_refs:
        statuses = [str(ref.get("status") or "") for ref in workflow_refs]
        if any(s == "failed" for s in statuses):
            return "failed", metrics, node_issues
        if any(s in {"running", "in_progress"} for s in statuses):
            return "running", metrics, node_issues
        if metrics.get("blocked"):
            return "blocked", metrics, node_issues
        if all(s == "completed" for s in statuses if s):
            return "completed", metrics, node_issues
        if any(s == "completed" for s in statuses):
            return "running", metrics, node_issues

    if defn.artifact_types:
        if all(at in artifact_map for at in defn.artifact_types):
            if defn.id == "materialize_chapter_outline_batch" and structure.total_chapters == 0:
                node_issues.append(
                    {
                        "code": "ARTIFACT_NOT_MATERIALIZED",
                        "message": "outline artifact exists but chapters table is empty",
                    }
                )
                return "failed", metrics, node_issues
            return "completed", metrics, node_issues
        if any(at in artifact_map for at in defn.artifact_types):
            return "running", metrics, node_issues

    if defn.id == "project_create":
        return "completed", metrics, node_issues

    if defn.id == "chapter_loop" and structure.total_chapters > 0:
        metrics["blocked_chapters"] = blocked_chapters
        if blocked_chapters > 0:
            return "blocked", metrics, node_issues
        approved = sum(
            1
            for vol in structure.volumes
            for ch in vol.chapters
            if str(ch.status) in {"approved", "complete", "completed"}
        )
        metrics["chapters_with_draft"] = approved
        if approved >= structure.total_chapters:
            return "completed", metrics, node_issues
        if approved > 0:
            return "running", metrics, node_issues
        return "pending", metrics, node_issues

    if defn.phase == "config":
        return "completed", metrics, node_issues

    return "pending", metrics, node_issues


def _build_global_issues(
    *,
    structure: ProjectStructureRead,
    artifact_map: dict[str, dict[str, Any]],
    workflow_runs: list[Any],
    active_path: PipelinePathId,
    chapter_meta_by_number: dict[int, dict[str, Any]],
) -> list[PipelineFlowIssue]:
    issues: list[PipelineFlowIssue] = []

    outline_artifact = artifact_map.get(ArtifactType.CHAPTER_OUTLINE_BATCH.value)
    if outline_artifact and structure.total_chapters == 0 and active_path != "fanqie_short":
        issues.append(
            PipelineFlowIssue(
                severity="critical",
                node_id="materialize_chapter_outline_batch",
                code="MATERIALIZATION_GAP",
                message="规划产物 CHAPTER_OUTLINE_BATCH 已存在，但 chapters 表为空",
                evidence={"artifact": outline_artifact},
                suggested_action="重新执行 materialize_chapter_outline_batch 或检查物化日志",
            )
        )

    for run in workflow_runs:
        if run.status == "failed" and run.error_message:
            if "reaped by self-heal" in run.error_message:
                continue
            issues.append(
                PipelineFlowIssue(
                    severity="critical",
                    node_id="run_project_pipeline",
                    code="WORKFLOW_FAILED",
                    message=f"{run.workflow_type} failed: {run.error_message[:200]}",
                    evidence={
                        "workflow_run_id": str(run.workflow_run_id),
                        "workflow_type": run.workflow_type,
                    },
                    suggested_action="查看 workflow_step_runs 与对应 gate/repair 队列",
                )
            )
        for step in run.steps:
            if step.status == "failed" and step.error_message:
                issues.append(
                    PipelineFlowIssue(
                        severity="warning",
                        node_id=step.step_name,
                        code="STEP_FAILED",
                        message=step.error_message[:300],
                        evidence={
                            "workflow_run_id": str(run.workflow_run_id),
                            "step_name": step.step_name,
                        },
                        suggested_action="对照 pipelines.py 中该 step 的前置条件",
                    )
                )

    block_keys = registered_block_metadata_keys()
    for vol in structure.volumes:
        for ch in vol.chapters:
            meta = chapter_meta_by_number.get(ch.chapter_number, {})
            for key in block_keys:
                if meta.get(key):
                    issues.append(
                        PipelineFlowIssue(
                            severity="critical",
                            node_id=key.replace("blocked_by_", "").replace("_gate", "_gate"),
                            code="GATE_BLOCKED",
                            message=f"第 {ch.chapter_number} 章: {key}",
                            evidence={"chapter_number": ch.chapter_number, "key": key},
                            suggested_action="检查章级 rewrite_tasks 与 gate_registry 修复策略",
                        )
                    )
            if ch.production_state == "blocked":
                issues.append(
                    PipelineFlowIssue(
                        severity="critical",
                        node_id="chapter_loop",
                        code="CHAPTER_BLOCKED",
                        message=f"第 {ch.chapter_number} 章 production_state=blocked",
                        evidence={"chapter_number": ch.chapter_number},
                        suggested_action="运行 project repair 或单章 pipeline 续跑",
                    )
                )

    return issues


async def build_pipeline_flow_overview(
    session: AsyncSession,
    project_slug: str,
    *,
    settings: AppSettings | None = None,
    task_timeline: list[dict[str, Any]] | None = None,
) -> PipelineFlowOverview:
    project = await get_project_by_slug(session, project_slug)
    if project is None:
        raise ValueError(f"Project '{project_slug}' was not found.")

    active_path = resolve_pipeline_path(project, settings=settings)
    node_defs, edges = build_pipeline_flow_schema()

    structure = await build_project_structure(session, project_slug)
    workflow = await build_project_workflow_overview(session, project_slug)
    story_bible = await build_story_bible_overview(session, project_slug)
    narrative = await build_narrative_overview(session, project_slug)

    artifact_rows = list(
        await session.scalars(
            select(PlanningArtifactVersionModel)
            .where(PlanningArtifactVersionModel.project_id == project.id)
            .order_by(
                PlanningArtifactVersionModel.artifact_type.asc(),
                PlanningArtifactVersionModel.version_no.desc(),
            )
        )
    )
    artifact_map = _artifact_presence(artifact_rows)

    workflow_by_type: dict[str, list[Any]] = defaultdict(list)
    for run in workflow.runs:
        workflow_by_type[run.workflow_type].append(run)

    step_index = _collect_step_index(workflow.runs)
    project_metadata = dict(project.metadata_json or {})

    story_counts = {
        "world_rule_count": len(story_bible.world_rules),
        "character_count": len(story_bible.characters),
        "location_count": len(story_bible.locations),
        "relationship_count": len(story_bible.relationships),
    }
    narrative_counts = {
        "plot_arc_count": len(narrative.plot_arcs),
        "clue_count": len(narrative.clues),
        "chapter_contract_count": len(narrative.chapter_contracts),
        "scene_contract_count": len(narrative.scene_contracts),
    }

    blocked_chapters = sum(
        1
        for vol in structure.volumes
        for ch in vol.chapters
        if ch.production_state == "blocked"
    )

    runtime_nodes: list[PipelineFlowNodeRuntime] = []
    for defn in node_defs:
        status, metrics, node_issues = _infer_node_status(
            defn,
            active_path=active_path,
            step_index=step_index,
            workflow_by_type=workflow_by_type,
            artifact_map=artifact_map,
            structure=structure,
            story_counts=story_counts,
            narrative_counts=narrative_counts,
            project_metadata=project_metadata,
            blocked_chapters=blocked_chapters,
        )
        refs: list[dict[str, Any]] = []
        for wf_type in defn.workflow_types:
            for run in workflow_by_type.get(wf_type, []):
                refs.append(
                    {
                        "workflow_run_id": str(run.workflow_run_id),
                        "workflow_type": run.workflow_type,
                        "status": run.status,
                        "current_step": run.current_step,
                        "error_message": run.error_message,
                    }
                )
        for step_name in defn.step_names:
            refs.extend(step_index.get(step_name, []))
        # Limit workflow_refs to avoid huge API responses
        if len(refs) > 50:
            refs = refs[-50:]
        runtime_nodes.append(
            PipelineFlowNodeRuntime(
                id=defn.id,
                status=status,
                metrics=metrics,
                workflow_refs=refs,
                node_issues=node_issues,
            )
        )

    chapter_meta_result = await session.execute(
        select(ChapterModel.chapter_number, ChapterModel.metadata_json).where(
            ChapterModel.project_id == project.id
        )
    )
    chapter_meta_by_number: dict[int, dict[str, Any]] = {}
    for row in chapter_meta_result:
        ch_no, meta = row[0], row[1]
        chapter_meta_by_number[int(ch_no)] = dict(meta or {})

    chapters_runtime: list[PipelineFlowChapterRuntime] = []
    for vol in structure.volumes:
        for ch in vol.chapters:
            ch_meta = chapter_meta_by_number.get(ch.chapter_number, {})
            ch_gates = [key for key in registered_block_metadata_keys() if ch_meta.get(key)]
            chapters_runtime.append(
                PipelineFlowChapterRuntime(
                    chapter_number=ch.chapter_number,
                    title=ch.title,
                    status=str(ch.status),
                    production_state=ch.production_state,
                    scene_count=len(ch.scenes),
                    word_count=ch.current_word_count,
                    gates=ch_gates,
                )
            )

    issues = _build_global_issues(
        structure=structure,
        artifact_map=artifact_map,
        workflow_runs=workflow.runs,
        active_path=active_path,
        chapter_meta_by_number=chapter_meta_by_number,
    )

    metadata = project.metadata_json or {}
    return PipelineFlowOverview(
        generated_at=datetime.now(UTC).isoformat(),
        active_path=active_path,
        project={
            "slug": project.slug,
            "title": project.title,
            "genre": project.genre,
            "sub_genre": getattr(project, "sub_genre", None),
            "status": project.status,
            "project_type": getattr(project, "project_type", ProjectType.LINEAR.value),
            "target_chapters": project.target_chapters,
            "current_chapter_number": project.current_chapter_number,
            "prompt_pack_key": metadata.get("prompt_pack_key") or metadata.get("prompt_pack_name"),
            "premise": metadata.get("premise", ""),
        },
        node_defs=[d for d in node_defs if node_applies(d, active_path) or d.phase == "gate"],
        edges=[e for e in edges if any(
            n.id == e.from_id for n in node_defs if node_applies(n, active_path)
        ) and any(
            n.id == e.to_id for n in node_defs if node_applies(n, active_path)
        )],
        nodes=[n for n in runtime_nodes if any(
            d.id == n.id and (node_applies(d, active_path) or d.phase == "gate")
            for d in node_defs
        )],
        chapters=chapters_runtime,
        issues=issues,
        task_timeline=task_timeline or [],
    )

# Public re-exports (tests and callers import from pipeline_flow_overview).
from bestseller.services.pipeline_flow_schema import (  # noqa: E402
    PIPELINE_FLOW_SCHEMA_VERSION,
    build_pipeline_flow_schema,
    resolve_pipeline_path,
    schema_gate_node_ids,
    schema_step_names_for_drift_check,
)
