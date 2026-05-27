"""Canonical pipeline topology schema for Web Studio data-flow visualization."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from bestseller.domain.enums import ArtifactType, ProjectType
from bestseller.infra.db.models import ProjectModel
from bestseller.services.gate_registry import _GATES, registered_gate_names
from bestseller.services.pipelines import PROGRESSIVE_CHAPTER_THRESHOLD
from bestseller.settings import AppSettings

PIPELINE_FLOW_SCHEMA_VERSION = "pipeline-flow-v1"

PipelinePathId = Literal["standard", "progressive", "fanqie_short"]
NodeStatus = Literal[
    "pending",
    "running",
    "completed",
    "skipped",
    "failed",
    "blocked",
    "not_applicable",
]
IssueSeverity = Literal["critical", "warning", "info"]


class FlowIORef(BaseModel):
    kind: str
    label: str
    source_node_id: str | None = None
    table: str | None = None
    artifact_type: str | None = None
    file_pattern: str | None = None


class FlowGateRef(BaseModel):
    name: str
    metadata_keys: list[str] = Field(default_factory=list)
    repair_strategy: str = "rewrite_task"


class PipelineFlowNodeDef(BaseModel):
    id: str
    phase: str
    label_zh: str
    code_ref: str
    paths: list[PipelinePathId | Literal["all"]]
    inputs: list[FlowIORef] = Field(default_factory=list)
    outputs: list[FlowIORef] = Field(default_factory=list)
    gates: list[FlowGateRef] = Field(default_factory=list)
    optional: bool = False
    children: list[str] = Field(default_factory=list)
    workflow_types: list[str] = Field(default_factory=list)
    step_names: list[str] = Field(default_factory=list)
    artifact_types: list[str] = Field(default_factory=list)
    progress_events: list[str] = Field(default_factory=list)


class PipelineFlowEdgeDef(BaseModel):
    from_id: str
    to_id: str
    label: str = ""
    data_kind: str = "data"


class PipelineFlowNodeRuntime(BaseModel):
    id: str
    status: NodeStatus = "pending"
    metrics: dict[str, Any] = Field(default_factory=dict)
    workflow_refs: list[dict[str, Any]] = Field(default_factory=list)
    node_issues: list[dict[str, Any]] = Field(default_factory=list)


class PipelineFlowChapterRuntime(BaseModel):
    chapter_number: int
    title: str | None = None
    status: str
    production_state: str | None = None
    scene_count: int = 0
    word_count: int = 0
    gates: list[str] = Field(default_factory=list)
    node_statuses: dict[str, NodeStatus] = Field(default_factory=dict)


class PipelineFlowIssue(BaseModel):
    severity: IssueSeverity
    node_id: str
    code: str
    message: str
    evidence: dict[str, Any] = Field(default_factory=dict)
    suggested_action: str = ""


class PipelineFlowOverview(BaseModel):
    generated_at: str
    schema_version: str = PIPELINE_FLOW_SCHEMA_VERSION
    active_path: PipelinePathId
    project: dict[str, Any]
    node_defs: list[PipelineFlowNodeDef]
    edges: list[PipelineFlowEdgeDef]
    nodes: list[PipelineFlowNodeRuntime]
    chapters: list[PipelineFlowChapterRuntime] = Field(default_factory=list)
    issues: list[PipelineFlowIssue] = Field(default_factory=list)
    task_timeline: list[dict[str, Any]] = Field(default_factory=list)


def _io(
    kind: str,
    label: str,
    *,
    source: str | None = None,
    table: str | None = None,
    artifact: str | None = None,
    file_pattern: str | None = None,
) -> FlowIORef:
    return FlowIORef(
        kind=kind,
        label=label,
        source_node_id=source,
        table=table,
        artifact_type=artifact,
        file_pattern=file_pattern,
    )


def _gate_node(gate_name: str, metadata_keys: tuple[str, ...], repair: str) -> PipelineFlowNodeDef:
    return PipelineFlowNodeDef(
        id=gate_name,
        phase="gate",
        label_zh=gate_name.replace("_", " "),
        code_ref=f"gate_registry.{gate_name}",
        paths=["all"],
        gates=[
            FlowGateRef(
                name=gate_name,
                metadata_keys=list(metadata_keys),
                repair_strategy=repair,
            )
        ],
        step_names=[gate_name],
    )


def _build_gate_nodes() -> list[PipelineFlowNodeDef]:
    return [
        _gate_node(g.name, g.metadata_keys, g.repair_strategy)
        for g in _GATES
    ]


def build_pipeline_flow_schema() -> tuple[list[PipelineFlowNodeDef], list[PipelineFlowEdgeDef]]:
    """Return canonical pipeline topology derived from current code paths."""
    nodes: list[PipelineFlowNodeDef] = [
        PipelineFlowNodeDef(
            id="genre_selection",
            phase="config",
            label_zh="书籍种类 / 题材",
            code_ref="novel_categories.resolve_category",
            paths=["all"],
            outputs=[_io("metadata", "genre / sub_genre", table="projects")],
        ),
        PipelineFlowNodeDef(
            id="prompt_pack_resolution",
            phase="config",
            label_zh="Prompt Pack 解析",
            code_ref="prompt_packs.resolve_prompt_pack",
            paths=["all"],
            inputs=[_io("metadata", "genre", source="genre_selection")],
            outputs=[_io("metadata", "prompt_pack_key", table="projects")],
        ),
        PipelineFlowNodeDef(
            id="writing_profile",
            phase="config",
            label_zh="写作配置",
            code_ref="writing_profile.get_project_writing_profile",
            paths=["all"],
            inputs=[_io("metadata", "prompt_pack", source="prompt_pack_resolution")],
            outputs=[_io("db", "style_guides", table="style_guides")],
        ),
        PipelineFlowNodeDef(
            id="methodology_bridge",
            phase="config",
            label_zh="方法论回落链",
            code_ref="methodology_bridge.get_fragment",
            paths=["all"],
            inputs=[
                _io("yaml", "writing_methodology.yaml"),
                _io("yaml", "prompt_pack fragments", source="prompt_pack_resolution"),
            ],
            outputs=[_io("text", "planner/scene/prewrite/judge fragments")],
        ),
        PipelineFlowNodeDef(
            id="project_create",
            phase="config",
            label_zh="创建项目",
            code_ref="projects.create_project",
            paths=["all"],
            outputs=[_io("db", "projects", table="projects")],
            progress_events=["project_creation_started", "project_creation_completed"],
        ),
        PipelineFlowNodeDef(
            id="generate_foundation_plan",
            phase="planning",
            label_zh="故事基石（渐进式）",
            code_ref="planner.generate_foundation_plan",
            paths=["progressive"],
            workflow_types=["generate_foundation_plan"],
            progress_events=["foundation_planning_started", "foundation_planning_completed"],
            artifact_types=[ArtifactType.BOOK_SPEC.value, ArtifactType.WORLD_SPEC.value],
        ),
        PipelineFlowNodeDef(
            id="generate_novel_plan",
            phase="planning",
            label_zh="全书企划",
            code_ref="planner.generate_novel_plan",
            paths=["standard", "progressive"],
            workflow_types=["generate_novel_plan"],
            progress_events=["planning_started", "planning_completed", "planning_skipped_resume"],
            artifact_types=[
                ArtifactType.PREMISE.value,
                ArtifactType.BOOK_SPEC.value,
                ArtifactType.WORLD_SPEC.value,
                ArtifactType.CAST_SPEC.value,
                ArtifactType.VOLUME_PLAN.value,
                ArtifactType.CHAPTER_OUTLINE_BATCH.value,
            ],
            step_names=[
                "store_premise",
                "generate_book_spec",
                "generate_world_spec",
                "generate_cast_spec",
                "generate_volume_plan",
            ],
            children=["generate_public_emotion_kernel", "generate_compliance_boundary_kernel"],
        ),
        PipelineFlowNodeDef(
            id="generate_volume_plan",
            phase="planning",
            label_zh="分卷大纲",
            code_ref="planner.generate_volume_plan",
            paths=["progressive"],
            workflow_types=["generate_volume_plan"],
            progress_events=["volume_planning_started", "volume_planning_completed"],
            artifact_types=[ArtifactType.VOLUME_CHAPTER_OUTLINE.value],
        ),
        PipelineFlowNodeDef(
            id="volume_planning_loop",
            phase="planning",
            label_zh="多卷规划循环",
            code_ref="pipelines.run_progressive_autowrite_pipeline",
            paths=["progressive"],
            progress_events=["progressive_autowrite_started"],
            children=["generate_volume_plan"],
        ),
        PipelineFlowNodeDef(
            id="materialize_story_bible",
            phase="materialize",
            label_zh="世界观物化",
            code_ref="workflows.materialize_latest_story_bible",
            paths=["standard", "progressive", "fanqie_short"],
            workflow_types=["materialize_story_bible"],
            progress_events=[
                "story_bible_materialization_started",
                "story_bible_materialization_completed",
            ],
            outputs=[
                _io("db", "world_rules", table="world_rules"),
                _io("db", "characters", table="characters"),
                _io("db", "locations", table="locations"),
            ],
        ),
        PipelineFlowNodeDef(
            id="materialize_chapter_outline_batch",
            phase="materialize",
            label_zh="章纲物化",
            code_ref="workflows.materialize_latest_chapter_outline_batch",
            paths=["standard", "progressive", "fanqie_short"],
            workflow_types=["materialize_chapter_outline_batch"],
            progress_events=[
                "outline_materialization_started",
                "outline_materialization_completed",
            ],
            inputs=[_io("artifact", "CHAPTER_OUTLINE_BATCH", artifact=ArtifactType.CHAPTER_OUTLINE_BATCH.value)],
            outputs=[
                _io("db", "volumes", table="volumes"),
                _io("db", "chapters", table="chapters"),
                _io("db", "scene_cards", table="scene_cards"),
            ],
        ),
        PipelineFlowNodeDef(
            id="materialize_narrative_graph",
            phase="materialize",
            label_zh="叙事图谱物化",
            code_ref="workflows.materialize_latest_narrative_graph",
            paths=["standard", "progressive", "fanqie_short"],
            workflow_types=["materialize_narrative_graph"],
            progress_events=[
                "narrative_graph_materialization_started",
                "narrative_graph_materialization_completed",
            ],
            outputs=[
                _io("db", "plot_arcs", table="plot_arcs"),
                _io("db", "clues", table="clues"),
            ],
        ),
        PipelineFlowNodeDef(
            id="materialize_narrative_tree",
            phase="materialize",
            label_zh="叙事树物化",
            code_ref="workflows.materialize_latest_narrative_tree",
            paths=["standard", "progressive"],
            workflow_types=["materialize_narrative_tree"],
            progress_events=[
                "narrative_tree_materialization_started",
                "narrative_tree_materialization_completed",
            ],
        ),
        PipelineFlowNodeDef(
            id="fanqie_foundation_plan",
            phase="planning",
            label_zh="番茄短篇基石",
            code_ref="fanqie_short_pipeline.run_fanqie_short_pipeline",
            paths=["fanqie_short"],
            progress_events=["fanqie_foundation_plan_started", "fanqie_foundation_plan_completed"],
        ),
        PipelineFlowNodeDef(
            id="fanqie_beat_sheet",
            phase="planning",
            label_zh="BeatSheet",
            code_ref="fanqie_short_pipeline",
            paths=["fanqie_short"],
            progress_events=["fanqie_beat_sheet_started", "fanqie_beat_sheet_completed"],
            artifact_types=[ArtifactType.FANQIE_BEAT_SHEET.value],
        ),
        PipelineFlowNodeDef(
            id="fanqie_materialization",
            phase="materialize",
            label_zh="短篇大纲落地",
            code_ref="fanqie_short_pipeline",
            paths=["fanqie_short"],
            progress_events=["fanqie_materialization_started", "fanqie_materialization_completed"],
        ),
        PipelineFlowNodeDef(
            id="fanqie_segment_writing",
            phase="writing",
            label_zh="短篇分段写作",
            code_ref="fanqie_short_pipeline",
            paths=["fanqie_short"],
            progress_events=["fanqie_segment_writing_started", "fanqie_segment_writing_completed"],
        ),
        PipelineFlowNodeDef(
            id="fanqie_whole_review",
            phase="writing",
            label_zh="短篇全文审校",
            code_ref="fanqie_short_pipeline",
            paths=["fanqie_short"],
            progress_events=["fanqie_whole_review_started", "fanqie_whole_review_completed"],
        ),
        PipelineFlowNodeDef(
            id="fanqie_export",
            phase="export",
            label_zh="短篇导出",
            code_ref="fanqie_short_pipeline",
            paths=["fanqie_short"],
            progress_events=["fanqie_export_completed", "fanqie_short_pipeline_completed"],
            outputs=[_io("file", "exports/fanqie-short.md", file_pattern="exports/fanqie-short.md")],
        ),
        PipelineFlowNodeDef(
            id="run_project_pipeline",
            phase="writing",
            label_zh="项目写作循环",
            code_ref="pipelines.run_project_pipeline:7393",
            paths=["standard", "progressive"],
            workflow_types=["project_pipeline"],
            progress_events=["project_pipeline_started", "project_pipeline_completed"],
            children=["material_forge", "chapter_loop"],
        ),
        PipelineFlowNodeDef(
            id="material_forge",
            phase="writing",
            label_zh="素材锻造（可选）",
            code_ref="material_forge.forge_all_materials",
            paths=["standard", "progressive"],
            optional=True,
            progress_events=["material_forge_started", "material_forge_completed"],
            outputs=[_io("db", "project_materials", table="project_materials")],
        ),
        PipelineFlowNodeDef(
            id="commercial_planning_readiness_gate",
            phase="gate",
            label_zh="商业规划就绪门禁",
            code_ref="pipelines.commercial_planning_readiness_gate",
            paths=["standard", "progressive"],
            step_names=["commercial_planning_readiness_gate"],
        ),
        PipelineFlowNodeDef(
            id="chapter_loop",
            phase="writing",
            label_zh="逐章流水线",
            code_ref="pipelines.run_chapter_pipeline",
            paths=["standard", "progressive"],
            workflow_types=["chapter_pipeline"],
            progress_events=["chapter_pipeline_started", "chapter_pipeline_completed"],
            children=[
                "load_chapter_context",
                "chapter_scene_contract_materializer",
                "chapter_outline_readiness_gate",
                "chapter_predraft_quality_gate",
                "scene_by_scene",
                "chapter_first",
                "assemble_chapter_draft",
                "review_chapter_draft",
                "extract_chapter_state_snapshot",
                "export_chapter_markdown",
            ],
        ),
        PipelineFlowNodeDef(
            id="load_chapter_context",
            phase="chapter",
            label_zh="加载章上下文",
            code_ref="pipelines.run_chapter_pipeline:4956",
            paths=["standard", "progressive"],
            step_names=["load_chapter_context"],
        ),
        PipelineFlowNodeDef(
            id="chapter_scene_contract_materializer",
            phase="chapter",
            label_zh="场景合约物化",
            code_ref="chapter_scene_contract_materializer",
            paths=["standard", "progressive"],
            optional=True,
            step_names=["chapter_scene_contract_materializer"],
        ),
        PipelineFlowNodeDef(
            id="scene_by_scene",
            phase="chapter",
            label_zh="逐场景写作",
            code_ref="pipelines.run_scene_pipeline",
            paths=["standard", "progressive"],
            workflow_types=["scene_pipeline"],
            children=[
                "load_context",
                "generate_scene_draft",
                "review_scene_draft",
                "refresh_scene_knowledge",
            ],
        ),
        PipelineFlowNodeDef(
            id="chapter_first",
            phase="chapter",
            label_zh="整章生成",
            code_ref="drafts.generate_chapter_draft_once",
            paths=["standard", "progressive"],
            optional=True,
            step_names=["generate_chapter_draft_once"],
        ),
        PipelineFlowNodeDef(
            id="assemble_chapter_draft",
            phase="chapter",
            label_zh="组装章草稿",
            code_ref="drafts.assemble_chapter_draft",
            paths=["standard", "progressive"],
            step_names=["assemble_chapter_draft"],
            outputs=[_io("db", "chapter_draft_versions", table="chapter_draft_versions")],
        ),
        PipelineFlowNodeDef(
            id="review_chapter_draft",
            phase="chapter",
            label_zh="章级审校",
            code_ref="reviews.review_chapter_draft",
            paths=["standard", "progressive"],
            step_names=["review_chapter_draft"],
            outputs=[_io("db", "review_reports", table="review_reports")],
        ),
        PipelineFlowNodeDef(
            id="extract_chapter_state_snapshot",
            phase="chapter",
            label_zh="章状态抽取",
            code_ref="knowledge.extract_chapter_state_snapshot",
            paths=["standard", "progressive"],
            outputs=[
                _io("db", "canon_facts", table="canon_facts"),
                _io("db", "timeline_events", table="timeline_events"),
            ],
        ),
        PipelineFlowNodeDef(
            id="export_chapter_markdown",
            phase="export",
            label_zh="导出章节 Markdown",
            code_ref="exports.export_chapter_markdown",
            paths=["standard", "progressive", "fanqie_short"],
            outputs=[_io("file", "chapter-NNN.md", file_pattern="chapter-{n:03d}.md")],
        ),
        PipelineFlowNodeDef(
            id="load_context",
            phase="scene",
            label_zh="场景上下文",
            code_ref="context.build_scene_writer_context_from_models",
            paths=["standard", "progressive"],
            step_names=["load_context"],
        ),
        PipelineFlowNodeDef(
            id="generate_scene_draft",
            phase="scene",
            label_zh="场景草稿生成",
            code_ref="drafts.generate_scene_draft",
            paths=["standard", "progressive"],
            step_names=["generate_scene_draft"],
            outputs=[_io("db", "scene_draft_versions", table="scene_draft_versions")],
        ),
        PipelineFlowNodeDef(
            id="review_scene_draft",
            phase="scene",
            label_zh="场景审校",
            code_ref="reviews.review_scene_draft",
            paths=["standard", "progressive"],
            step_names=["review_scene_draft"],
        ),
        PipelineFlowNodeDef(
            id="refresh_scene_knowledge",
            phase="scene",
            label_zh="场景知识刷新",
            code_ref="knowledge.refresh_scene_knowledge",
            paths=["standard", "progressive"],
            step_names=["refresh_scene_knowledge"],
        ),
        PipelineFlowNodeDef(
            id="volume_writing_loop",
            phase="writing",
            label_zh="分卷写作循环",
            code_ref="pipelines.run_progressive_autowrite_pipeline",
            paths=["progressive"],
            progress_events=["volume_writing_started", "volume_writing_completed"],
            children=["run_project_pipeline"],
        ),
        PipelineFlowNodeDef(
            id="volume_feedback",
            phase="writing",
            label_zh="卷末反馈吸收",
            code_ref="pipelines.run_progressive_autowrite_pipeline",
            paths=["progressive"],
            progress_events=["volume_feedback_collection_started", "volume_feedback_collected"],
            artifact_types=[ArtifactType.VOLUME_WRITING_FEEDBACK.value],
        ),
        PipelineFlowNodeDef(
            id="periodic_consistency_check",
            phase="export",
            label_zh="周期性一致性检查",
            code_ref="consistency.review_project_consistency",
            paths=["standard", "progressive"],
            progress_events=[
                "periodic_consistency_check_started",
                "periodic_consistency_check_completed",
            ],
        ),
        PipelineFlowNodeDef(
            id="rolling_summary",
            phase="export",
            label_zh="知识压缩",
            code_ref="knowledge.compress_knowledge_window",
            paths=["standard", "progressive"],
            progress_events=["rolling_summary_started", "rolling_summary_completed"],
        ),
        PipelineFlowNodeDef(
            id="export_project_markdown",
            phase="export",
            label_zh="全书导出",
            code_ref="exports.export_project_markdown",
            paths=["standard", "progressive"],
            progress_events=["project_export_started", "project_export_completed"],
            outputs=[_io("file", "project.md", file_pattern="project.md")],
        ),
        PipelineFlowNodeDef(
            id="auto_repair",
            phase="export",
            label_zh="自动修复",
            code_ref="pipelines.run_project_repair",
            paths=["standard", "progressive"],
            progress_events=["auto_repair_started", "auto_repair_completed"],
        ),
    ]
    nodes.extend(_build_gate_nodes())

    edges: list[PipelineFlowEdgeDef] = [
        PipelineFlowEdgeDef(from_id="genre_selection", to_id="prompt_pack_resolution", label="genre"),
        PipelineFlowEdgeDef(from_id="prompt_pack_resolution", to_id="writing_profile", label="pack"),
        PipelineFlowEdgeDef(from_id="writing_profile", to_id="methodology_bridge", label="profile"),
        PipelineFlowEdgeDef(from_id="methodology_bridge", to_id="project_create", label="fragments"),
        PipelineFlowEdgeDef(from_id="project_create", to_id="generate_novel_plan", label="premise"),
        PipelineFlowEdgeDef(from_id="project_create", to_id="generate_foundation_plan", label="premise"),
        PipelineFlowEdgeDef(from_id="generate_foundation_plan", to_id="volume_planning_loop", label="foundation"),
        PipelineFlowEdgeDef(from_id="generate_novel_plan", to_id="materialize_story_bible", label="artifacts"),
        PipelineFlowEdgeDef(from_id="materialize_story_bible", to_id="materialize_chapter_outline_batch", label="bible"),
        PipelineFlowEdgeDef(
            from_id="materialize_chapter_outline_batch",
            to_id="materialize_narrative_graph",
            label="outline",
        ),
        PipelineFlowEdgeDef(
            from_id="materialize_narrative_graph",
            to_id="materialize_narrative_tree",
            label="graph",
        ),
        PipelineFlowEdgeDef(from_id="materialize_narrative_tree", to_id="run_project_pipeline", label="structure"),
        PipelineFlowEdgeDef(from_id="run_project_pipeline", to_id="chapter_loop", label="chapters"),
        PipelineFlowEdgeDef(from_id="chapter_loop", to_id="load_chapter_context", label="per chapter"),
        PipelineFlowEdgeDef(from_id="load_chapter_context", to_id="scene_by_scene", label="scenes"),
        PipelineFlowEdgeDef(from_id="scene_by_scene", to_id="load_context", label="per scene"),
        PipelineFlowEdgeDef(from_id="load_context", to_id="generate_scene_draft", label="context"),
        PipelineFlowEdgeDef(from_id="generate_scene_draft", to_id="review_scene_draft", label="draft"),
        PipelineFlowEdgeDef(from_id="review_scene_draft", to_id="refresh_scene_knowledge", label="approved"),
        PipelineFlowEdgeDef(from_id="scene_by_scene", to_id="assemble_chapter_draft", label="assembled"),
        PipelineFlowEdgeDef(from_id="assemble_chapter_draft", to_id="review_chapter_draft", label="chapter draft"),
        PipelineFlowEdgeDef(
            from_id="review_chapter_draft",
            to_id="extract_chapter_state_snapshot",
            label="passed",
        ),
        PipelineFlowEdgeDef(from_id="extract_chapter_state_snapshot", to_id="export_chapter_markdown", label="snapshot"),
        PipelineFlowEdgeDef(from_id="run_project_pipeline", to_id="export_project_markdown", label="done"),
        PipelineFlowEdgeDef(from_id="project_create", to_id="fanqie_foundation_plan", label="fanqie"),
        PipelineFlowEdgeDef(from_id="fanqie_foundation_plan", to_id="fanqie_beat_sheet", label="foundation"),
        PipelineFlowEdgeDef(from_id="fanqie_beat_sheet", to_id="fanqie_materialization", label="beats"),
        PipelineFlowEdgeDef(from_id="fanqie_materialization", to_id="fanqie_segment_writing", label="outline"),
        PipelineFlowEdgeDef(from_id="fanqie_segment_writing", to_id="fanqie_whole_review", label="segments"),
        PipelineFlowEdgeDef(from_id="fanqie_whole_review", to_id="fanqie_export", label="reviewed"),
        PipelineFlowEdgeDef(from_id="volume_planning_loop", to_id="generate_volume_plan", label="volume"),
        PipelineFlowEdgeDef(from_id="generate_volume_plan", to_id="volume_writing_loop", label="plan"),
        PipelineFlowEdgeDef(from_id="volume_writing_loop", to_id="volume_feedback", label="written"),
    ]
    return nodes, edges


def resolve_pipeline_path(
    project: ProjectModel,
    *,
    settings: AppSettings | None = None,
) -> PipelinePathId:
    project_type = getattr(project, "project_type", None) or ProjectType.LINEAR.value
    if project_type == ProjectType.FANQIE_SHORT.value:
        return "fanqie_short"
    target_chapters = int(getattr(project, "target_chapters", 0) or 0)
    if settings is not None and settings.pipeline.progressive_planning:
        return "progressive"
    if target_chapters > PROGRESSIVE_CHAPTER_THRESHOLD:
        return "progressive"
    return "standard"


def node_applies(defn: PipelineFlowNodeDef, active_path: PipelinePathId) -> bool:
    if "all" in defn.paths:
        return True
    return active_path in defn.paths


def schema_gate_node_ids() -> frozenset[str]:
    return registered_gate_names()


def schema_step_names_for_drift_check() -> tuple[str, ...]:
    """Step names that must exist as substrings in pipelines.py or planner.py."""
    defs, _edges = build_pipeline_flow_schema()
    names: list[str] = []
    for defn in defs:
        names.extend(defn.step_names)
    return tuple(dict.fromkeys(names))
