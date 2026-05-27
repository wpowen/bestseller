"""Obsidian vault export for project planning and continuity material.

The database remains canonical.  This module writes a rebuildable Markdown
workspace that lets authors inspect, cross-link, and annotate the novel system
in Obsidian without making the vault a second source of truth.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import re
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.infra.db.models import (
    ChapterDraftVersionModel,
    ChapterModel,
    ExportArtifactModel,
    MaterialLibraryModel,
    PlanningArtifactVersionModel,
    ProjectMaterialModel,
    ProjectModel,
)
from bestseller.services.distillation_assets import (
    read_json,
    read_jsonl,
    validate_distillation_package,
)
from bestseller.services.drafts import sanitize_novel_markdown_content
from bestseller.services.exports import (
    _ensure_chapter_heading,
    create_export_artifact,
)
from bestseller.services.inspection import build_story_bible_overview
from bestseller.services.knowledge import list_canon_facts, list_timeline_events
from bestseller.services.material_density import (
    MaterialDensityReport,
    audit_project_material_density,
    material_density_report_to_dict,
)
from bestseller.services.methodology_cards import (
    load_methodology_cards,
    load_methodology_source_set,
    methodology_coverage_summary,
    validate_card_sources,
)
from bestseller.services.projects import get_project_by_slug
from bestseller.services.prompt_packs import list_prompt_packs
from bestseller.settings import AppSettings

_MANAGED_NOTICE = (
    "> Generated from BestSeller DB. Treat this vault as a readable workspace, "
    "not the source of truth. Put manual notes in [[Inbox/README|Inbox]]."
)
_PLACEHOLDER = "_(尚未生成)_"


@dataclass(frozen=True)
class ObsidianDocument:
    relative_path: Path
    content_md: str


@dataclass(frozen=True)
class MaterialDimensionSummary:
    dimension: str
    status: str
    genre: str | None
    count: int
    avg_confidence: float
    avg_coverage_score: float | None


@dataclass(frozen=True)
class DistillationPackageSummary:
    source_id: str
    relative_path: str
    ok: bool
    missing_files: tuple[str, ...] = ()
    material_rows: int = 0
    mechanism_rows: int = 0
    volume_rows: int = 0
    chapter_jobs: int = 0
    errors: tuple[str, ...] = ()


@dataclass(frozen=True)
class DistillationAggregateSummary:
    aggregate_key: str
    relative_path: str
    source_ids: tuple[str, ...] = ()
    maturity_status: str = "unknown"
    maturity_score: float | None = None
    material_rows: int = 0
    mechanism_rows: int = 0
    anti_copy_rules: int = 0
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class PromptPackSummary:
    key: str
    name: str
    version: str
    genres: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    fragment_count: int = 0
    obligatory_scene_count: int = 0
    anti_pattern_count: int = 0
    source_note_count: int = 0
    relative_path: str = ""
    errors: tuple[str, ...] = ()


@dataclass(frozen=True)
class MethodologyDeckSummary:
    source_set_id: str
    relative_path: str
    card_count: int
    verified_cards: int
    verified_sources: int
    uncovered_verified_sources: tuple[str, ...] = ()
    cards_missing_gate_binding: tuple[str, ...] = ()
    finding_count: int = 0
    errors: tuple[str, ...] = ()


@dataclass(frozen=True)
class ModelCallAsset:
    asset_id: str
    asset_type: str
    title: str
    status: str
    source_path: str
    use_for: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    notes: str = ""


@dataclass(frozen=True)
class ObsidianVaultPayload:
    project: ProjectModel
    story_bible: Any
    canon_facts: Sequence[Any] = field(default_factory=list)
    timeline_events: Sequence[Any] = field(default_factory=list)
    chapter_payloads: Sequence[tuple[ChapterModel, ChapterDraftVersionModel]] = field(
        default_factory=list
    )
    project_materials: Sequence[ProjectMaterialModel] = field(default_factory=list)
    global_material_dimensions: Sequence[MaterialDimensionSummary] = field(default_factory=list)
    material_density: MaterialDensityReport | None = None
    planning_artifacts: Sequence[PlanningArtifactVersionModel] = field(default_factory=list)
    distillation_packages: Sequence[DistillationPackageSummary] = field(default_factory=list)
    distillation_aggregates: Sequence[DistillationAggregateSummary] = field(default_factory=list)
    prompt_packs: Sequence[PromptPackSummary] = field(default_factory=list)
    methodology_decks: Sequence[MethodologyDeckSummary] = field(default_factory=list)
    model_call_assets: Sequence[ModelCallAsset] = field(default_factory=list)
    generated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    include_chapters: bool = True
    include_raw: bool = True
    include_system_assets: bool = True


@dataclass(frozen=True)
class ObsidianVaultExport:
    vault_path: Path
    file_count: int
    checksum: str
    manifest_path: Path
    artifact: ExportArtifactModel


async def export_obsidian_vault(
    session: AsyncSession,
    settings: AppSettings,
    project_slug: str,
    *,
    output_path: Path | None = None,
    include_chapters: bool = True,
    include_raw: bool = True,
    include_system_assets: bool = True,
    created_by_run_id: UUID | None = None,
) -> ObsidianVaultExport:
    """Export project knowledge into an Obsidian-compatible Markdown vault."""
    project = await get_project_by_slug(session, project_slug)
    if project is None:
        raise ValueError(f"Project '{project_slug}' was not found.")

    repo_root = Path(__file__).resolve().parents[3]
    global_material_dimensions = (
        await _load_global_material_dimensions(session) if include_system_assets else []
    )
    material_density = (
        await audit_project_material_density(
            session,
            project_id=str(project.id),
            genre=project.genre,
            sub_genre=getattr(project, "sub_genre", None),
        )
        if include_system_assets
        else None
    )
    distillation_packages = _load_distillation_packages(repo_root) if include_system_assets else []
    distillation_aggregates = (
        _load_distillation_aggregates(repo_root) if include_system_assets else []
    )
    prompt_packs = _load_prompt_pack_summaries(repo_root) if include_system_assets else []
    methodology_decks = _load_methodology_deck_summaries(repo_root) if include_system_assets else []

    payload = ObsidianVaultPayload(
        project=project,
        story_bible=await build_story_bible_overview(session, project_slug),
        canon_facts=await list_canon_facts(session, project_slug, current_only=False),
        timeline_events=await list_timeline_events(session, project_slug),
        chapter_payloads=await _load_current_chapter_payloads(session, project.id),
        project_materials=await _load_project_materials(session, project.id),
        global_material_dimensions=global_material_dimensions,
        material_density=material_density,
        planning_artifacts=await _load_latest_planning_artifacts(session, project.id),
        distillation_packages=distillation_packages,
        distillation_aggregates=distillation_aggregates,
        prompt_packs=prompt_packs,
        methodology_decks=methodology_decks,
        model_call_assets=_build_model_call_assets(
            global_material_dimensions=global_material_dimensions,
            distillation_packages=distillation_packages,
            distillation_aggregates=distillation_aggregates,
            prompt_packs=prompt_packs,
            methodology_decks=methodology_decks,
        ),
        include_chapters=include_chapters,
        include_raw=include_raw,
        include_system_assets=include_system_assets,
    )

    vault_path = output_path or Path(settings.output.base_dir) / project.slug / "obsidian-vault"
    documents = build_obsidian_documents(payload)
    manifest = write_obsidian_vault(vault_path, documents, payload=payload)
    checksum = _hash_manifest(manifest)

    artifact = create_export_artifact(
        project_id=project.id,
        export_type="obsidian",
        source_scope="project",
        source_id=project.id,
        storage_uri=str(vault_path.resolve()),
        checksum=checksum,
        version_label="obsidian-vault-current",
        created_by_run_id=created_by_run_id,
    )
    session.add(artifact)
    await session.flush()

    return ObsidianVaultExport(
        vault_path=vault_path,
        file_count=len(documents) + 1,
        checksum=checksum,
        manifest_path=vault_path / "_manifest.json",
        artifact=artifact,
    )


def build_obsidian_documents(payload: ObsidianVaultPayload) -> list[ObsidianDocument]:
    """Build all Markdown files that make up the managed vault snapshot."""
    docs: list[ObsidianDocument] = [
        ObsidianDocument(Path("00-主页.md"), _render_home(payload)),
        ObsidianDocument(Path("维护/维护看板.md"), _render_maintenance_board(payload)),
        ObsidianDocument(Path("Inbox/README.md"), _render_inbox(payload)),
        ObsidianDocument(Path("故事圣经/总览.md"), _render_story_bible_overview(payload)),
        ObsidianDocument(Path("世界观/规则.md"), _render_world_rules(payload)),
        ObsidianDocument(Path("世界观/地点.md"), _render_locations(payload)),
        ObsidianDocument(Path("世界观/势力.md"), _render_factions(payload)),
        ObsidianDocument(Path("人物/人物索引.md"), _render_character_index(payload)),
        ObsidianDocument(Path("关系/关系索引.md"), _render_relationships(payload)),
        ObsidianDocument(Path("卷纲/卷计划.md"), _render_volume_plan(payload)),
        ObsidianDocument(Path("伏笔与揭示/揭示计划.md"), _render_reveal_plan(payload)),
        ObsidianDocument(
            Path("Canon/当前事实.md"),
            _render_canon_facts(payload, current_only=True),
        ),
        ObsidianDocument(
            Path("Canon/事实履历.md"),
            _render_canon_facts(payload, current_only=False),
        ),
        ObsidianDocument(Path("时间线/时间线.md"), _render_timeline(payload)),
        ObsidianDocument(Path("素材/项目素材.md"), _render_project_materials(payload)),
        ObsidianDocument(Path("规划产物/规划产物索引.md"), _render_planning_artifacts(payload)),
    ]
    if payload.include_system_assets:
        docs.extend(
            [
                ObsidianDocument(Path("资料资产/总览.md"), _render_asset_workbench(payload)),
                ObsidianDocument(Path("资料资产/缺口看板.md"), _render_gap_dashboard(payload)),
                ObsidianDocument(Path("物料库/全局物料维度.md"), _render_global_materials(payload)),
                ObsidianDocument(
                    Path("蒸馏资料/蒸馏包索引.md"),
                    _render_distillation_packages(payload),
                ),
                ObsidianDocument(
                    Path("蒸馏资料/聚合资产索引.md"),
                    _render_distillation_aggregates(payload),
                ),
                ObsidianDocument(Path("提示词/Prompt Pack 索引.md"), _render_prompt_packs(payload)),
                ObsidianDocument(
                    Path("方法论/方法论卡片索引.md"),
                    _render_methodology_decks(payload),
                ),
                ObsidianDocument(Path("模型调用索引.md"), _render_model_call_index(payload)),
            ]
        )

    for character in _items(payload.story_bible, "characters"):
        name = _text_attr(character, "name", fallback="未命名人物")
        docs.append(
            ObsidianDocument(
                Path("人物") / f"{_safe_filename(name)}.md",
                _render_character_note(payload, character),
            )
        )

    if payload.include_chapters:
        for chapter, draft in payload.chapter_payloads:
            title = _chapter_title(chapter)
            docs.append(
                ObsidianDocument(
                    Path("正文") / f"{int(chapter.chapter_number):03d}-{_safe_filename(title)}.md",
                    _render_chapter_note(payload, chapter, draft),
                )
            )

    if payload.include_raw:
        docs.extend(_raw_artifact_documents(payload))

    return docs


def write_obsidian_vault(
    vault_path: Path,
    documents: Sequence[ObsidianDocument],
    *,
    payload: ObsidianVaultPayload,
) -> dict[str, Any]:
    """Write documents and a machine-readable manifest without deleting user notes."""
    vault_path.mkdir(parents=True, exist_ok=True)
    _write_obsidian_settings(vault_path)

    manifest_files: list[dict[str, str]] = []
    for document in documents:
        target = vault_path / document.relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        content = document.content_md.rstrip() + "\n"
        target.write_text(content, encoding="utf-8")
        manifest_files.append(
            {
                "path": document.relative_path.as_posix(),
                "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            }
        )

    manifest: dict[str, Any] = {
        "schema": "bestseller.obsidian.v1",
        "project_slug": payload.project.slug,
        "project_id": str(payload.project.id),
        "generated_at": payload.generated_at.isoformat(),
        "source_of_truth": "postgresql",
        "managed_file_count": len(documents),
        "files": sorted(manifest_files, key=lambda item: item["path"]),
    }
    manifest_path = vault_path / "_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


async def _load_current_chapter_payloads(
    session: AsyncSession,
    project_id: UUID,
) -> list[tuple[ChapterModel, ChapterDraftVersionModel]]:
    stmt = (
        select(ChapterModel, ChapterDraftVersionModel)
        .join(
            ChapterDraftVersionModel,
            ChapterDraftVersionModel.chapter_id == ChapterModel.id,
        )
        .where(
            ChapterModel.project_id == project_id,
            ChapterDraftVersionModel.is_current.is_(True),
        )
        .order_by(ChapterModel.chapter_number.asc())
    )
    result = await session.execute(stmt)
    return list(result.all())


async def _load_project_materials(
    session: AsyncSession,
    project_id: UUID,
) -> list[ProjectMaterialModel]:
    stmt = (
        select(ProjectMaterialModel)
        .where(ProjectMaterialModel.project_id == project_id)
        .order_by(ProjectMaterialModel.material_type.asc(), ProjectMaterialModel.slug.asc())
    )
    return list(await session.scalars(stmt))


async def _load_global_material_dimensions(
    session: AsyncSession,
) -> list[MaterialDimensionSummary]:
    stmt = (
        select(
            MaterialLibraryModel.dimension,
            MaterialLibraryModel.status,
            MaterialLibraryModel.genre,
            func.count(MaterialLibraryModel.id),
            func.avg(MaterialLibraryModel.confidence),
            func.avg(MaterialLibraryModel.coverage_score),
        )
        .group_by(
            MaterialLibraryModel.dimension,
            MaterialLibraryModel.status,
            MaterialLibraryModel.genre,
        )
        .order_by(
            MaterialLibraryModel.dimension.asc(),
            MaterialLibraryModel.status.asc(),
            MaterialLibraryModel.genre.asc(),
        )
    )
    rows = (await session.execute(stmt)).all()
    return [
        MaterialDimensionSummary(
            dimension=str(row[0] or ""),
            status=str(row[1] or ""),
            genre=str(row[2]) if row[2] is not None else None,
            count=int(row[3] or 0),
            avg_confidence=float(row[4] or 0.0),
            avg_coverage_score=float(row[5]) if row[5] is not None else None,
        )
        for row in rows
    ]


async def _load_latest_planning_artifacts(
    session: AsyncSession,
    project_id: UUID,
) -> list[PlanningArtifactVersionModel]:
    rows = list(
        await session.scalars(
            select(PlanningArtifactVersionModel)
            .where(PlanningArtifactVersionModel.project_id == project_id)
            .order_by(
                PlanningArtifactVersionModel.artifact_type.asc(),
                PlanningArtifactVersionModel.scope_ref_id.asc(),
                PlanningArtifactVersionModel.version_no.desc(),
                PlanningArtifactVersionModel.created_at.desc(),
            )
        )
    )
    seen: set[tuple[str, str]] = set()
    latest: list[PlanningArtifactVersionModel] = []
    for row in rows:
        key = (str(row.artifact_type), str(row.scope_ref_id or "project"))
        if key in seen:
            continue
        seen.add(key)
        latest.append(row)
    return latest


def _load_distillation_packages(repo_root: Path) -> list[DistillationPackageSummary]:
    root = repo_root / "data" / "distillation"
    if not root.exists():
        return []
    summaries: list[DistillationPackageSummary] = []
    for package_dir in sorted(root.glob("source-*")):
        if not package_dir.is_dir():
            continue
        report = validate_distillation_package(package_dir)
        summaries.append(
            DistillationPackageSummary(
                source_id=report.source_id or package_dir.name,
                relative_path=_relative_to_repo(package_dir, repo_root),
                ok=report.ok,
                missing_files=report.missing_files,
                material_rows=report.material_rows,
                mechanism_rows=report.mechanism_rows,
                volume_rows=report.volume_rows,
                chapter_jobs=report.chapter_jobs,
                errors=report.errors,
            )
        )
    return summaries


def _load_distillation_aggregates(repo_root: Path) -> list[DistillationAggregateSummary]:
    root = repo_root / "data" / "distillation" / "aggregates"
    if not root.exists():
        return []
    summaries: list[DistillationAggregateSummary] = []
    for aggregate_dir in sorted(root.iterdir()):
        if not aggregate_dir.is_dir():
            continue
        manifest_path = aggregate_dir / "aggregate_manifest.json"
        warnings: list[str] = []
        manifest: dict[str, object] = {}
        if manifest_path.exists():
            try:
                manifest = read_json(manifest_path)
            except Exception as exc:
                warnings.append(f"aggregate_manifest invalid: {exc}")
        else:
            warnings.append("missing aggregate_manifest.json")
        source_ids = _string_tuple(manifest.get("source_ids"))
        material_rows = _int_from_any(manifest.get("material_rows"))
        if material_rows == 0:
            material_rows = _jsonl_count(aggregate_dir / "material_entries.active.jsonl")
            material_rows += _jsonl_count(aggregate_dir / "material_entries.review.jsonl")
        mechanism_rows = _int_from_any(manifest.get("mechanism_rows")) or _jsonl_count(
            aggregate_dir / "mechanism_registry.jsonl"
        )
        anti_copy_rules = _int_from_any(manifest.get("anti_copy_blocked_combinations"))
        if anti_copy_rules == 0:
            anti_copy_rules = _anti_copy_rule_count(aggregate_dir / "anti_copy_rules.json")
        summaries.append(
            DistillationAggregateSummary(
                aggregate_key=str(manifest.get("aggregate_key") or aggregate_dir.name),
                relative_path=_relative_to_repo(aggregate_dir, repo_root),
                source_ids=source_ids,
                maturity_status=str(manifest.get("maturity_status") or "unknown"),
                maturity_score=_float_or_none(manifest.get("maturity_score")),
                material_rows=material_rows,
                mechanism_rows=mechanism_rows,
                anti_copy_rules=anti_copy_rules,
                warnings=tuple(warnings + list(_string_tuple(manifest.get("warnings")))),
            )
        )
    return summaries


def _load_prompt_pack_summaries(repo_root: Path) -> list[PromptPackSummary]:
    prompt_dir = repo_root / "config" / "prompt_packs"
    path_by_key = {
        path.stem: _relative_to_repo(path, repo_root)
        for path in sorted(prompt_dir.glob("*.yaml"))
        if path.is_file()
    }
    summaries: list[PromptPackSummary] = []
    try:
        packs = sorted(list_prompt_packs(), key=lambda item: item.key)
    except Exception as exc:
        return [
            PromptPackSummary(
                key="__load_error__",
                name="Prompt pack load error",
                version="unknown",
                tags=("error",),
                relative_path=_relative_to_repo(prompt_dir, repo_root),
                errors=(str(exc),),
            )
        ]
    for pack in packs:
        fragments = pack.fragments.model_dump(exclude_none=True)
        summaries.append(
            PromptPackSummary(
                key=pack.key,
                name=pack.name,
                version=pack.version,
                genres=tuple(pack.genres),
                tags=tuple(pack.tags),
                fragment_count=len(fragments),
                obligatory_scene_count=len(pack.obligatory_scenes),
                anti_pattern_count=len(pack.anti_patterns),
                source_note_count=len(pack.source_notes),
                relative_path=path_by_key.get(pack.key, f"config/prompt_packs/{pack.key}.yaml"),
            )
        )
    return summaries


def _load_methodology_deck_summaries(repo_root: Path) -> list[MethodologyDeckSummary]:
    root = repo_root / "data" / "methodology_sources"
    if not root.exists():
        return []
    summaries: list[MethodologyDeckSummary] = []
    for source_dir in sorted(root.iterdir()):
        if not source_dir.is_dir():
            continue
        manifest_path = source_dir / "manifest.yaml"
        cards_path = source_dir / "cards.yaml"
        errors: list[str] = []
        if not manifest_path.exists() or not cards_path.exists():
            summaries.append(
                MethodologyDeckSummary(
                    source_set_id=source_dir.name,
                    relative_path=_relative_to_repo(source_dir, repo_root),
                    card_count=0,
                    verified_cards=0,
                    verified_sources=0,
                    errors=("missing manifest.yaml or cards.yaml",),
                )
            )
            continue
        try:
            source_set = load_methodology_source_set(manifest_path)
            deck = load_methodology_cards(cards_path)
            coverage = methodology_coverage_summary(deck, source_set)
            findings = validate_card_sources(deck, source_set)
            summaries.append(
                MethodologyDeckSummary(
                    source_set_id=source_set.source_set_id,
                    relative_path=_relative_to_repo(source_dir, repo_root),
                    card_count=int(coverage.get("cards") or 0),
                    verified_cards=int(coverage.get("verified_cards") or 0),
                    verified_sources=int(coverage.get("verified_sources") or 0),
                    uncovered_verified_sources=_string_tuple(
                        coverage.get("uncovered_verified_source_ids")
                    ),
                    cards_missing_gate_binding=_string_tuple(
                        coverage.get("cards_missing_gate_binding")
                    ),
                    finding_count=len(findings),
                )
            )
        except Exception as exc:
            errors.append(str(exc))
            summaries.append(
                MethodologyDeckSummary(
                    source_set_id=source_dir.name,
                    relative_path=_relative_to_repo(source_dir, repo_root),
                    card_count=0,
                    verified_cards=0,
                    verified_sources=0,
                    errors=tuple(errors),
                )
            )
    return summaries


def _build_model_call_assets(
    *,
    global_material_dimensions: Sequence[MaterialDimensionSummary],
    distillation_packages: Sequence[DistillationPackageSummary],
    distillation_aggregates: Sequence[DistillationAggregateSummary],
    prompt_packs: Sequence[PromptPackSummary],
    methodology_decks: Sequence[MethodologyDeckSummary],
) -> list[ModelCallAsset]:
    assets: list[ModelCallAsset] = []
    for item in global_material_dimensions:
        if item.status != "active":
            continue
        assets.append(
            ModelCallAsset(
                asset_id=f"material-dimension:{item.dimension}:{item.genre or 'generic'}",
                asset_type="material_dimension",
                title=f"{item.dimension} / {item.genre or 'generic'}",
                status=item.status,
                source_path="material_library",
                use_for=("retrieval", "forge", "planner_context"),
                tags=tuple(filter(None, (item.dimension, item.genre))),
                notes=f"count={item.count}; avg_confidence={item.avg_confidence:.2f}",
            )
        )
    for item in distillation_aggregates:
        assets.append(
            ModelCallAsset(
                asset_id=f"distillation-aggregate:{item.aggregate_key}",
                asset_type="distillation_aggregate",
                title=item.aggregate_key,
                status=item.maturity_status,
                source_path=item.relative_path,
                use_for=("genre_design_reference", "anti_copy", "material_seed"),
                tags=item.source_ids[:8],
                notes=f"maturity={item.maturity_score}; material_rows={item.material_rows}",
            )
        )
    for item in distillation_packages:
        assets.append(
            ModelCallAsset(
                asset_id=f"distillation-package:{item.source_id}",
                asset_type="distillation_package",
                title=item.source_id,
                status="ready" if item.ok else "needs_repair",
                source_path=item.relative_path,
                use_for=("source_trace", "aggregate_input", "material_review"),
                tags=("distillation",),
                notes=f"materials={item.material_rows}; mechanisms={item.mechanism_rows}",
            )
        )
    for item in prompt_packs:
        assets.append(
            ModelCallAsset(
                asset_id=f"prompt-pack:{item.key}",
                asset_type="prompt_pack",
                title=item.name,
                status="ready" if item.fragment_count and not item.errors else "needs_review",
                source_path=item.relative_path,
                use_for=("writer_prompt", "review_prompt", "genre_route"),
                tags=item.tags,
                notes=(
                    f"version={item.version}; fragments={item.fragment_count}; "
                    f"errors={len(item.errors)}"
                ),
            )
        )
    for item in methodology_decks:
        assets.append(
            ModelCallAsset(
                asset_id=f"methodology-deck:{item.source_set_id}",
                asset_type="methodology_deck",
                title=item.source_set_id,
                status="ready" if not item.errors and item.finding_count == 0 else "needs_review",
                source_path=item.relative_path,
                use_for=("quality_gate", "health_report", "prompt_contract"),
                tags=("methodology",),
                notes=f"cards={item.card_count}; verified_sources={item.verified_sources}",
            )
        )
    return sorted(assets, key=lambda item: (item.asset_type, item.asset_id))


def _render_home(payload: ObsidianVaultPayload) -> str:
    project = payload.project
    story = payload.story_bible
    lines = _frontmatter(
        {
            "type": "obsidian-vault-home",
            "project": project.slug,
            "source": "bestseller-db",
            "generated_at": payload.generated_at.isoformat(),
            "tags": ["bestseller", "novel-system"],
        }
    )
    lines.extend(
        [
            f"# {project.title}",
            "",
            _MANAGED_NOTICE,
            "",
            "## 导航",
            "- [[故事圣经/总览|故事圣经总览]]",
            "- [[人物/人物索引|人物]]",
            "- [[关系/关系索引|关系]]",
            "- [[世界观/规则|世界规则]] · [[世界观/地点|地点]] · [[世界观/势力|势力]]",
            "- [[卷纲/卷计划|卷计划]] · [[伏笔与揭示/揭示计划|伏笔与揭示]]",
            "- [[Canon/当前事实|Canon 当前事实]] · [[时间线/时间线|时间线]]",
            "- [[素材/项目素材|项目素材]] · [[规划产物/规划产物索引|规划产物]]",
        ]
    )
    if payload.include_system_assets:
        lines.extend(
            [
                "- [[资料资产/总览|资料资产]] · [[资料资产/缺口看板|缺口看板]]",
                "- [[物料库/全局物料维度|全局物料]] · [[蒸馏资料/聚合资产索引|蒸馏聚合]]",
                "- [[提示词/Prompt Pack 索引|Prompt Pack]] · [[方法论/方法论卡片索引|方法论]]",
                "- [[模型调用索引|模型调用索引]]",
            ]
        )
    lines.extend(
        [
            "- [[维护/维护看板|维护看板]] · [[Inbox/README|人工修订 Inbox]]",
            "",
            "## 结构计数",
            f"- 人物: {len(_items(story, 'characters'))}",
            f"- 关系: {len(_items(story, 'relationships'))}",
            f"- 世界规则: {len(_items(story, 'world_rules'))}",
            f"- 地点: {len(_items(story, 'locations'))}",
            f"- 势力: {len(_items(story, 'factions'))}",
            f"- 卷前沿: {len(_items(story, 'volume_frontiers'))}",
            f"- Canon 事实: {len(payload.canon_facts)}",
            f"- 时间线事件: {len(payload.timeline_events)}",
            f"- 正文当前稿: {len(payload.chapter_payloads)}",
            f"- 项目素材: {len(payload.project_materials)}",
        ]
    )
    if payload.include_system_assets:
        lines.extend(
            [
                f"- 全局物料维度: {len(payload.global_material_dimensions)}",
                f"- 蒸馏包: {len(payload.distillation_packages)}",
                f"- 蒸馏聚合: {len(payload.distillation_aggregates)}",
                f"- Prompt Pack: {len(payload.prompt_packs)}",
                f"- 方法论 Deck: {len(payload.methodology_decks)}",
                f"- 模型可调用资产: {len(payload.model_call_assets)}",
            ]
        )
    lines.extend(
        [
            "",
            "## 项目元数据",
            "| 字段 | 值 |",
            "|---|---|",
            f"| slug | `{project.slug}` |",
            f"| 类型 | {_escape_table(project.genre or '')} |",
            f"| 子类型 | {_escape_table(getattr(project, 'sub_genre', '') or '')} |",
            f"| 目标章数 | {getattr(project, 'target_chapters', '') or ''} |",
            f"| 目标字数 | {getattr(project, 'target_word_count', '') or ''} |",
        ]
    )
    return "\n".join(lines)


def _render_story_bible_overview(payload: ObsidianVaultPayload) -> str:
    project = payload.project
    story = payload.story_bible
    backbone = _attr(story, "world_backbone")
    lines = _frontmatter({"type": "story-bible-overview", "project": project.slug})
    lines.extend(
        [
            f"# 故事圣经总览 — {project.title}",
            "",
            _MANAGED_NOTICE,
            "",
            "## 核心入口",
            f"- 世界规则: [[世界观/规则|{len(_items(story, 'world_rules'))} 条]]",
            f"- 人物: [[人物/人物索引|{len(_items(story, 'characters'))} 个]]",
            f"- 关系: [[关系/关系索引|{len(_items(story, 'relationships'))} 条]]",
            f"- 卷计划: [[卷纲/卷计划|{len(_items(story, 'volume_frontiers'))} 卷前沿]]",
            "",
            "## 世界骨架",
        ]
    )
    if backbone is None:
        lines.append(_PLACEHOLDER)
    else:
        rows = [
            ("世界名", _text_attr(backbone, "world_name")),
            ("世界前提", _text_attr(backbone, "world_premise")),
            ("力量体系", _text_attr(backbone, "power_system_name")),
            ("权力结构", _text_attr(backbone, "power_structure")),
            ("禁区", _text_attr(backbone, "forbidden_zones")),
        ]
        lines.extend(_table(["字段", "内容"], rows))
    return "\n".join(lines)


def _render_world_rules(payload: ObsidianVaultPayload) -> str:
    rows = []
    for rule in _items(payload.story_bible, "world_rules"):
        rows.append(
            (
                _text_attr(rule, "rule_code"),
                _text_attr(rule, "name"),
                _text_attr(rule, "description"),
                _text_attr(rule, "story_consequence"),
                _text_attr(rule, "exploitation_potential"),
            )
        )
    return _section_table(
        payload,
        "世界规则",
        "world-rules",
        ["Code", "规则", "描述", "剧情后果", "可利用点"],
        rows,
    )


def _render_locations(payload: ObsidianVaultPayload) -> str:
    rows = []
    for location in _items(payload.story_bible, "locations"):
        rows.append(
            (
                _text_attr(location, "name"),
                _text_attr(location, "location_type"),
                _text_attr(location, "atmosphere"),
                _join(_attr(location, "key_rule_codes")),
                _text_attr(location, "story_role"),
            )
        )
    return _section_table(
        payload,
        "地点",
        "locations",
        ["地点", "类型", "氛围", "规则", "剧情功能"],
        rows,
    )


def _render_factions(payload: ObsidianVaultPayload) -> str:
    rows = []
    for faction in _items(payload.story_bible, "factions"):
        rows.append(
            (
                _text_attr(faction, "name"),
                _text_attr(faction, "goal"),
                _text_attr(faction, "method"),
                _text_attr(faction, "relationship_to_protagonist"),
                _text_attr(faction, "internal_conflict"),
            )
        )
    return _section_table(
        payload,
        "势力",
        "factions",
        ["势力", "目标", "手段", "与主角", "内部矛盾"],
        rows,
    )


def _render_character_index(payload: ObsidianVaultPayload) -> str:
    lines = _frontmatter({"type": "character-index", "project": payload.project.slug})
    lines.extend([f"# 人物索引 — {payload.project.title}", "", _MANAGED_NOTICE, ""])
    rows = []
    for character in _items(payload.story_bible, "characters"):
        name = _text_attr(character, "name", fallback="未命名人物")
        rows.append(
            (
                f"[[人物/{_safe_filename(name)}|{name}]]",
                _text_attr(character, "role"),
                _text_attr(character, "arc_state"),
                _text_attr(character, "stance"),
                _text_attr(character, "alive_status"),
            )
        )
    lines.extend(_table(["人物", "功能", "弧光状态", "立场", "生死"], rows))
    return "\n".join(lines)


def _render_character_note(payload: ObsidianVaultPayload, character: object) -> str:
    name = _text_attr(character, "name", fallback="未命名人物")
    lines = _frontmatter(
        {
            "type": "character",
            "project": payload.project.slug,
            "character": name,
            "tags": ["bestseller/character"],
        }
    )
    lines.extend(
        [
            f"# {name}",
            "",
            _MANAGED_NOTICE,
            "",
            "## 身份",
        ]
    )
    rows = [
        ("角色功能", _text_attr(character, "role")),
        ("年龄", _text_attr(character, "age")),
        ("状态", _text_attr(character, "alive_status")),
        ("立场", _text_attr(character, "stance")),
        ("力量层级", _text_attr(character, "power_tier")),
        ("核心伤口", _text_attr(character, "core_wound")),
    ]
    lines.extend(_table(["字段", "内容"], rows))
    lines.extend(["", "## 欲望与阻力"])
    lines.extend(
        _table(
            ["字段", "内容"],
            [
                ("目标", _text_attr(character, "goal")),
                ("恐惧", _text_attr(character, "fear")),
                ("缺陷", _text_attr(character, "flaw")),
                ("优势", _text_attr(character, "strength")),
                ("秘密", _text_attr(character, "secret")),
                ("背景", _text_attr(character, "background")),
                ("弧光轨迹", _text_attr(character, "arc_trajectory")),
                ("弧光状态", _text_attr(character, "arc_state")),
            ],
        )
    )
    related = _relationships_for_character(payload, name)
    lines.extend(["", "## 关系"])
    if related:
        for relationship in related:
            other = _other_character_name(relationship, name)
            lines.append(
                f"- [[人物/{_safe_filename(other)}|{other}]]: "
                f"{_text_attr(relationship, 'relationship_type')}; "
                f"{_text_attr(relationship, 'tension_summary') or _PLACEHOLDER}"
            )
    else:
        lines.append(_PLACEHOLDER)
    return "\n".join(lines)


def _render_relationships(payload: ObsidianVaultPayload) -> str:
    lines = _frontmatter({"type": "relationship-index", "project": payload.project.slug})
    lines.extend([f"# 关系索引 — {payload.project.title}", "", _MANAGED_NOTICE, ""])
    rows = []
    for rel in _items(payload.story_bible, "relationships"):
        a = _text_attr(rel, "character_a", fallback="未知人物")
        b = _text_attr(rel, "character_b", fallback="未知人物")
        rows.append(
            (
                f"[[人物/{_safe_filename(a)}|{a}]]",
                f"[[人物/{_safe_filename(b)}|{b}]]",
                _text_attr(rel, "relationship_type"),
                _text_attr(rel, "strength"),
                _text_attr(rel, "tension_summary"),
            )
        )
    lines.extend(_table(["A", "B", "类型", "强度", "张力"], rows))
    return "\n".join(lines)


def _render_volume_plan(payload: ObsidianVaultPayload) -> str:
    rows = []
    for volume in _items(payload.story_bible, "volume_frontiers"):
        rows.append(
            (
                _text_attr(volume, "volume_number"),
                _text_attr(volume, "title"),
                _text_attr(volume, "start_chapter_number"),
                _text_attr(volume, "end_chapter_number"),
                _text_attr(volume, "frontier_summary"),
                _join(_attr(volume, "active_locations")),
                _join(_attr(volume, "active_factions")),
            )
        )
    return _section_table(
        payload,
        "卷计划",
        "volume-plan",
        ["卷", "标题", "起章", "止章", "前沿摘要", "地点", "势力"],
        rows,
    )


def _render_reveal_plan(payload: ObsidianVaultPayload) -> str:
    lines = _frontmatter({"type": "reveal-plan", "project": payload.project.slug})
    lines.extend([f"# 伏笔与揭示 — {payload.project.title}", "", _MANAGED_NOTICE, ""])
    reveal_rows = []
    for reveal in _items(payload.story_bible, "deferred_reveals"):
        reveal_rows.append(
            (
                _text_attr(reveal, "reveal_code"),
                _text_attr(reveal, "label"),
                _text_attr(reveal, "category"),
                _text_attr(reveal, "summary"),
                _text_attr(reveal, "reveal_volume_number"),
                _text_attr(reveal, "reveal_chapter_number"),
                _text_attr(reveal, "status"),
            )
        )
    lines.extend(["## 延迟揭示"])
    lines.extend(_table(["Code", "名称", "类别", "摘要", "卷", "章", "状态"], reveal_rows))
    gate_rows = []
    for gate in _items(payload.story_bible, "expansion_gates"):
        gate_rows.append(
            (
                _text_attr(gate, "gate_code"),
                _text_attr(gate, "label"),
                _text_attr(gate, "source_volume_number"),
                _text_attr(gate, "unlock_volume_number"),
                _text_attr(gate, "unlock_chapter_number"),
                _text_attr(gate, "status"),
            )
        )
    lines.extend(["", "## 展开门"])
    lines.extend(_table(["Code", "名称", "来源卷", "解锁卷", "解锁章", "状态"], gate_rows))
    return "\n".join(lines)


def _render_canon_facts(payload: ObsidianVaultPayload, *, current_only: bool) -> str:
    facts = [
        fact
        for fact in payload.canon_facts
        if not current_only or bool(_attr(fact, "is_current", default=False))
    ]
    title = "Canon 当前事实" if current_only else "Canon 事实履历"
    rows = []
    for fact in facts:
        rows.append(
            (
                _text_attr(fact, "subject_type"),
                _text_attr(fact, "subject_label"),
                _text_attr(fact, "predicate"),
                _json_inline(_attr(fact, "value_json")),
                _text_attr(fact, "valid_from_chapter_no"),
                _text_attr(fact, "valid_to_chapter_no"),
                _text_attr(fact, "is_current"),
            )
        )
    return _section_table(
        payload,
        title,
        "canon-current" if current_only else "canon-history",
        ["主体类型", "主体", "谓词", "值", "起章", "止章", "当前"],
        rows,
    )


def _render_timeline(payload: ObsidianVaultPayload) -> str:
    rows = []
    for event in payload.timeline_events:
        rows.append(
            (
                _text_attr(event, "story_order"),
                _text_attr(event, "story_time_label"),
                _text_attr(event, "event_type"),
                _text_attr(event, "event_name"),
                _join(_attr(event, "participant_ids")),
                _join(_attr(event, "consequences")),
            )
        )
    return _section_table(
        payload,
        "时间线",
        "timeline",
        ["顺序", "故事时间", "类型", "事件", "参与者", "后果"],
        rows,
    )


def _render_project_materials(payload: ObsidianVaultPayload) -> str:
    lines = _frontmatter({"type": "project-materials", "project": payload.project.slug})
    lines.extend([f"# 项目素材 — {payload.project.title}", "", _MANAGED_NOTICE, ""])
    if not payload.project_materials:
        lines.append(_PLACEHOLDER)
        return "\n".join(lines)

    for material in payload.project_materials:
        lines.extend(
            [
                f"## {material.material_type} / {material.name}",
                f"- slug: `{material.slug}`",
                f"- status: `{material.status}`",
                f"- source_library_ids: `{_json_inline(material.source_library_ids_json or [])}`",
                "",
                material.narrative_summary or _PLACEHOLDER,
                "",
            ]
        )
        if material.content_json:
            lines.extend(
                [
                    "```json",
                    json.dumps(material.content_json, ensure_ascii=False, indent=2, default=str),
                    "```",
                    "",
                ]
            )
    return "\n".join(lines)


def _render_asset_workbench(payload: ObsidianVaultPayload) -> str:
    lines = _frontmatter({"type": "asset-workbench", "project": payload.project.slug})
    lines.extend(
        [
            f"# 资料资产总览 — {payload.project.title}",
            "",
            _MANAGED_NOTICE,
            "",
            "## 维护入口",
            "- [[资料资产/缺口看板|缺口看板]]",
            "- [[物料库/全局物料维度|全局物料维度]]",
            "- [[素材/项目素材|项目物料]]",
            "- [[蒸馏资料/蒸馏包索引|蒸馏包索引]]",
            "- [[蒸馏资料/聚合资产索引|蒸馏聚合资产]]",
            "- [[提示词/Prompt Pack 索引|Prompt Pack]]",
            "- [[方法论/方法论卡片索引|方法论卡片]]",
            "- [[模型调用索引|模型调用索引]]",
            "",
            "## 资产计数",
        ]
    )
    rows = [
        ("project_materials", len(payload.project_materials), "DB / project_materials"),
        (
            "global_material_dimensions",
            len(payload.global_material_dimensions),
            "DB / material_library",
        ),
        ("distillation_packages", len(payload.distillation_packages), "data/distillation/source-*"),
        (
            "distillation_aggregates",
            len(payload.distillation_aggregates),
            "data/distillation/aggregates",
        ),
        ("prompt_packs", len(payload.prompt_packs), "config/prompt_packs"),
        ("methodology_decks", len(payload.methodology_decks), "data/methodology_sources"),
        ("model_call_assets", len(payload.model_call_assets), "raw/model-call-index.json"),
    ]
    lines.extend(_table(["资产层", "数量", "权威来源"], rows))
    return "\n".join(lines)


def _render_gap_dashboard(payload: ObsidianVaultPayload) -> str:
    lines = _frontmatter({"type": "asset-gap-dashboard", "project": payload.project.slug})
    lines.extend(
        [
            f"# 缺口看板 — {payload.project.title}",
            "",
            _MANAGED_NOTICE,
            "",
            "## 项目物料密度",
        ]
    )
    if payload.material_density is None:
        lines.append(_PLACEHOLDER)
    else:
        rows = [
            (
                item.dimension,
                item.active_count,
                item.target_count,
                item.gap,
                item.global_seed_count,
                "ok" if item.is_satisfied else "补充项目物料",
            )
            for item in payload.material_density.dimensions
        ]
        lines.extend(_table(["维度", "项目现有", "目标", "缺口", "全局种子", "动作"], rows))

    broken_packages = [item for item in payload.distillation_packages if not item.ok]
    weak_aggregates = [
        item
        for item in payload.distillation_aggregates
        if item.maturity_status not in {"production", "review"}
    ]
    prompt_gaps = [
        item
        for item in payload.prompt_packs
        if item.errors or item.fragment_count == 0 or item.source_note_count == 0
    ]
    methodology_gaps = [
        item
        for item in payload.methodology_decks
        if item.errors or item.finding_count or item.uncovered_verified_sources
    ]
    lines.extend(["", "## 系统资产缺口"])
    lines.extend(
        _table(
            ["类别", "数量", "建议动作"],
            [
                (
                    "distillation_package_needs_repair",
                    len(broken_packages),
                    "补齐缺失文件或修复 JSONL",
                ),
                (
                    "weak_distillation_aggregate",
                    len(weak_aggregates),
                    "补充同题材蒸馏并重新 aggregate",
                ),
                (
                    "prompt_pack_metadata_gap",
                    len(prompt_gaps),
                    "补 source_notes / fragments / 失败样例",
                ),
                (
                    "methodology_deck_gap",
                    len(methodology_gaps),
                    "补 OCR 覆盖、gate binding 或卡片来源",
                ),
            ],
        )
    )
    if broken_packages:
        lines.extend(["", "## 需要修复的蒸馏包"])
        lines.extend(
            _table(
                ["source", "路径", "缺失", "错误"],
                [
                    (
                        item.source_id,
                        item.relative_path,
                        _join(item.missing_files),
                        _join(item.errors[:3]),
                    )
                    for item in broken_packages[:50]
                ],
            )
        )
    return "\n".join(lines)


def _render_global_materials(payload: ObsidianVaultPayload) -> str:
    rows = [
        (
            item.dimension,
            item.status,
            item.genre or "generic",
            item.count,
            f"{item.avg_confidence:.2f}",
            "" if item.avg_coverage_score is None else f"{item.avg_coverage_score:.2f}",
        )
        for item in payload.global_material_dimensions
    ]
    return _section_table(
        payload,
        "全局物料维度",
        "global-material-dimensions",
        ["维度", "状态", "题材", "数量", "平均置信", "平均覆盖"],
        rows,
    )


def _render_distillation_packages(payload: ObsidianVaultPayload) -> str:
    rows = [
        (
            item.source_id,
            item.relative_path,
            "ok" if item.ok else "needs_repair",
            item.material_rows,
            item.mechanism_rows,
            item.volume_rows,
            item.chapter_jobs,
            _join(item.missing_files),
        )
        for item in payload.distillation_packages
    ]
    return _section_table(
        payload,
        "蒸馏包索引",
        "distillation-packages",
        ["source", "路径", "状态", "物料", "机制", "卷卡", "章节任务", "缺失"],
        rows,
    )


def _render_distillation_aggregates(payload: ObsidianVaultPayload) -> str:
    rows = [
        (
            item.aggregate_key,
            item.relative_path,
            item.maturity_status,
            "" if item.maturity_score is None else f"{item.maturity_score:.3f}",
            len(item.source_ids),
            item.material_rows,
            item.mechanism_rows,
            item.anti_copy_rules,
            _join(item.warnings[:3]),
        )
        for item in payload.distillation_aggregates
    ]
    return _section_table(
        payload,
        "蒸馏聚合资产",
        "distillation-aggregates",
        ["聚合", "路径", "成熟度", "分数", "来源数", "物料", "机制", "反抄袭", "警告"],
        rows,
    )


def _render_prompt_packs(payload: ObsidianVaultPayload) -> str:
    rows = [
        (
            item.key,
            item.name,
            item.version,
            _join(item.genres),
            _join(item.tags),
            item.fragment_count,
            item.obligatory_scene_count,
            item.anti_pattern_count,
            item.source_note_count,
            item.relative_path,
            _join(item.errors[:2]),
        )
        for item in payload.prompt_packs
    ]
    return _section_table(
        payload,
        "Prompt Pack 索引",
        "prompt-packs",
        [
            "key",
            "名称",
            "版本",
            "题材",
            "标签",
            "片段",
            "标志场景",
            "反模式",
            "来源",
            "路径",
            "错误",
        ],
        rows,
    )


def _render_methodology_decks(payload: ObsidianVaultPayload) -> str:
    rows = [
        (
            item.source_set_id,
            item.relative_path,
            item.card_count,
            item.verified_cards,
            item.verified_sources,
            len(item.uncovered_verified_sources),
            len(item.cards_missing_gate_binding),
            item.finding_count,
            _join(item.errors[:2]),
        )
        for item in payload.methodology_decks
    ]
    return _section_table(
        payload,
        "方法论卡片索引",
        "methodology-decks",
        ["来源集", "路径", "卡片", "已验证卡", "已验证源", "未覆盖源", "缺 gate", "发现", "错误"],
        rows,
    )


def _render_model_call_index(payload: ObsidianVaultPayload) -> str:
    lines = _frontmatter({"type": "model-call-index", "project": payload.project.slug})
    lines.extend(
        [
            f"# 模型调用索引 — {payload.project.title}",
            "",
            _MANAGED_NOTICE,
            "",
            "LLM 调用应优先读取 `raw/model-call-index.json`, 本页只提供人类可读视图。",
            "",
        ]
    )
    rows = [
        (
            item.asset_id,
            item.asset_type,
            item.title,
            item.status,
            item.source_path,
            _join(item.use_for),
            _join(item.tags),
            item.notes,
        )
        for item in payload.model_call_assets
    ]
    lines.extend(_table(["id", "类型", "标题", "状态", "来源", "用途", "标签", "备注"], rows))
    return "\n".join(lines)


def _render_planning_artifacts(payload: ObsidianVaultPayload) -> str:
    rows = []
    for artifact in payload.planning_artifacts:
        name = f"{_safe_filename(str(artifact.artifact_type))}-{artifact.version_no}.json"
        raw_path = f"raw/planning/{name}"
        rows.append(
            (
                _text_attr(artifact, "artifact_type"),
                _text_attr(artifact, "version_no"),
                _text_attr(artifact, "scope_ref_id"),
                _text_attr(artifact, "created_at"),
                f"[[{raw_path}|raw json]]",
            )
        )
    return _section_table(
        payload,
        "规划产物索引",
        "planning-artifacts",
        ["类型", "版本", "作用域", "创建时间", "原始内容"],
        rows,
    )


def _render_chapter_note(
    payload: ObsidianVaultPayload,
    chapter: ChapterModel,
    draft: ChapterDraftVersionModel,
) -> str:
    title = _chapter_title(chapter)
    content_md = _ensure_chapter_heading(
        chapter,
        sanitize_novel_markdown_content(draft.content_md or "", language=payload.project.language),
        language=payload.project.language,
    )
    lines = _frontmatter(
        {
            "type": "chapter-draft",
            "project": payload.project.slug,
            "chapter_number": int(chapter.chapter_number),
            "draft_version": int(draft.version_no),
            "word_count": int(draft.word_count or 0),
            "tags": ["bestseller/chapter"],
        }
    )
    lines.extend(
        [
            f"# 第{int(chapter.chapter_number)}章 {title}",
            "",
            _MANAGED_NOTICE,
            "",
            "## 章节约束",
            f"- 目标: {chapter.chapter_goal or _PLACEHOLDER}",
            f"- 字数: {int(draft.word_count or 0)}",
            (
                f"- 状态: {chapter.status or _PLACEHOLDER} / "
                f"{chapter.production_state or _PLACEHOLDER}"
            ),
            "",
            "## 正文快照",
            content_md,
        ]
    )
    return "\n".join(lines)


def _render_maintenance_board(payload: ObsidianVaultPayload) -> str:
    missing_current_drafts = max(
        int(payload.project.target_chapters or 0) - len(payload.chapter_payloads),
        0,
    )
    open_reveals = [
        item
        for item in _items(payload.story_bible, "deferred_reveals")
        if _text_attr(item, "status").lower()
        not in {"paid_off", "resolved", "complete", "completed"}
    ]
    lines = _frontmatter({"type": "maintenance-board", "project": payload.project.slug})
    lines.extend(
        [
            f"# 维护看板 — {payload.project.title}",
            "",
            _MANAGED_NOTICE,
            "",
            "## 使用规则",
            "- DB 是真值源; vault 可以随时由 `bestseller export obsidian` 重建。",
            "- 直接编辑生成文件会在下次同步时被覆盖; 人工想法写入 [[Inbox/README|Inbox]]。",
            "- 需要回写框架时, 把 Inbox 条目整理成 planning artifact / canon fact / rewrite task。",
            "",
            "## 待关注项",
            f"- 未完成揭示: {len(open_reveals)}",
            f"- 无当前正文稿章节: {missing_current_drafts}",
            f"- 项目素材条目: {len(payload.project_materials)}",
            f"- 规划产物快照: {len(payload.planning_artifacts)}",
            "",
            "## 未完成揭示",
        ]
    )
    if open_reveals:
        for item in open_reveals:
            lines.append(
                f"- `{_text_attr(item, 'reveal_code')}` {_text_attr(item, 'label')} "
                f"-> 第{_text_attr(item, 'reveal_chapter_number')}章"
            )
    else:
        lines.append(_PLACEHOLDER)
    return "\n".join(lines)


def _render_inbox(payload: ObsidianVaultPayload) -> str:
    lines = _frontmatter({"type": "human-inbox", "project": payload.project.slug})
    lines.extend(
        [
            f"# 人工修订 Inbox — {payload.project.title}",
            "",
            "这里保存作者在 Obsidian 中新增的想法、漏洞记录、人物补丁和世界观扩展。",
            "生成器不会依赖这里的内容; 需要纳入框架时, "
            "请转成数据库里的规划产物、Canon Fact、素材或重写任务。",
            "",
            "## 待整理",
            "- [ ] ",
        ]
    )
    return "\n".join(lines)


def _raw_artifact_documents(payload: ObsidianVaultPayload) -> list[ObsidianDocument]:
    docs: list[ObsidianDocument] = [
        ObsidianDocument(
            Path("raw/story_bible_overview.json"),
            json.dumps(_model_dump(payload.story_bible), ensure_ascii=False, indent=2),
        )
    ]
    if payload.include_system_assets:
        docs.extend(
            [
                ObsidianDocument(
                    Path("raw/model-call-index.json"),
                    json.dumps(_model_call_index_payload(payload), ensure_ascii=False, indent=2),
                ),
                ObsidianDocument(
                    Path("raw/material-coverage.json"),
                    json.dumps(_material_coverage_payload(payload), ensure_ascii=False, indent=2),
                ),
                ObsidianDocument(
                    Path("raw/asset-workbench.json"),
                    json.dumps(_asset_workbench_payload(payload), ensure_ascii=False, indent=2),
                ),
            ]
        )
    for artifact in payload.planning_artifacts:
        name = f"{_safe_filename(str(artifact.artifact_type))}-{artifact.version_no}.json"
        docs.append(
            ObsidianDocument(
                Path("raw/planning") / name,
                json.dumps(_attr(artifact, "content", default={}), ensure_ascii=False, indent=2),
            )
        )
    return docs


def _model_call_index_payload(payload: ObsidianVaultPayload) -> dict[str, object]:
    return {
        "schema": "bestseller.model_call_index.v1",
        "project_slug": payload.project.slug,
        "generated_at": payload.generated_at.isoformat(),
        "source_of_truth": "postgresql+repo-assets",
        "assets": [_dataclass_dict(item) for item in payload.model_call_assets],
    }


def _material_coverage_payload(payload: ObsidianVaultPayload) -> dict[str, object]:
    density = (
        material_density_report_to_dict(payload.material_density)
        if payload.material_density is not None
        else None
    )
    return {
        "schema": "bestseller.material_coverage.v1",
        "project_slug": payload.project.slug,
        "project_density": density,
        "global_dimensions": [_dataclass_dict(item) for item in payload.global_material_dimensions],
    }


def _asset_workbench_payload(payload: ObsidianVaultPayload) -> dict[str, object]:
    return {
        "schema": "bestseller.asset_workbench.v1",
        "project_slug": payload.project.slug,
        "distillation_packages": [_dataclass_dict(item) for item in payload.distillation_packages],
        "distillation_aggregates": [
            _dataclass_dict(item) for item in payload.distillation_aggregates
        ],
        "prompt_packs": [_dataclass_dict(item) for item in payload.prompt_packs],
        "methodology_decks": [_dataclass_dict(item) for item in payload.methodology_decks],
    }


def _section_table(
    payload: ObsidianVaultPayload,
    title: str,
    kind: str,
    headers: Sequence[str],
    rows: Sequence[Sequence[Any]],
) -> str:
    lines = _frontmatter({"type": kind, "project": payload.project.slug})
    lines.extend([f"# {title} — {payload.project.title}", "", _MANAGED_NOTICE, ""])
    lines.extend(_table(headers, rows))
    return "\n".join(lines)


def _frontmatter(values: Mapping[str, Any]) -> list[str]:
    lines = ["---"]
    for key, value in values.items():
        if isinstance(value, list):
            lines.append(f"{key}:")
            for item in value:
                lines.append(f"  - {_yaml_scalar(item)}")
        else:
            lines.append(f"{key}: {_yaml_scalar(value)}")
    lines.append("---")
    lines.append("")
    return lines


def _table(headers: Sequence[str], rows: Sequence[Sequence[Any]]) -> list[str]:
    if not rows:
        return [_PLACEHOLDER]
    lines = [
        "| " + " | ".join(_escape_table(header) for header in headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        cells = list(row)[: len(headers)]
        cells.extend("" for _ in range(len(headers) - len(cells)))
        lines.append("| " + " | ".join(_escape_table(_stringify(cell)) for cell in cells) + " |")
    return lines


def _write_obsidian_settings(vault_path: Path) -> None:
    obsidian_dir = vault_path / ".obsidian"
    obsidian_dir.mkdir(parents=True, exist_ok=True)
    app_json = obsidian_dir / "app.json"
    if not app_json.exists():
        app_json.write_text(
            json.dumps(
                {
                    "alwaysUpdateLinks": True,
                    "newFileLocation": "folder",
                    "newFileFolderPath": "Inbox",
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )


def _relative_to_repo(path: Path, repo_root: Path) -> str:
    try:
        return path.resolve().relative_to(repo_root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _jsonl_count(path: Path) -> int:
    if not path.exists():
        return 0
    try:
        return len(read_jsonl(path))
    except Exception:
        return 0


def _anti_copy_rule_count(path: Path) -> int:
    if not path.exists():
        return 0
    try:
        payload = read_json(path)
    except Exception:
        return 0
    for key in ("blocked_combinations", "rules", "anti_copy_rules"):
        value = payload.get(key)
        if isinstance(value, list):
            return len(value)
    return len(payload)


def _string_tuple(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,) if value else ()
    if isinstance(value, Iterable) and not isinstance(value, (bytes, Mapping)):
        return tuple(str(item) for item in value if str(item))
    return ()


def _int_from_any(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _float_or_none(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _dataclass_dict(value: object) -> dict[str, object]:
    data = asdict(value)
    return {key: _model_dump(item) for key, item in data.items()}


def _items(obj: object, attr_name: str) -> list[object]:
    value = _attr(obj, attr_name, default=[])
    if isinstance(value, Iterable) and not isinstance(value, (str, bytes, Mapping)):
        return list(value)
    return []


def _attr(obj: object, attr_name: str, default: object | None = None) -> object | None:
    if isinstance(obj, Mapping):
        return obj.get(attr_name, default)
    return getattr(obj, attr_name, default)


def _text_attr(obj: object, attr_name: str, *, fallback: str = "") -> str:
    return _stringify(_attr(obj, attr_name, default=fallback)) or fallback


def _stringify(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (str, int, float)):
        return str(value)
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Mapping) or isinstance(value, Sequence):
        return _json_inline(value)
    return str(value)


def _join(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping):
        return _json_inline(value)
    if isinstance(value, Iterable):
        return "; ".join(_stringify(item) for item in value if _stringify(item))
    return _stringify(value)


def _json_inline(value: object) -> str:
    if value is None:
        return ""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _safe_filename(value: str) -> str:
    cleaned = re.sub(r"[\\/:*?\"<>|#^\[\]]+", "-", value).strip().strip(".")
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned[:96] or "untitled"


def _escape_table(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", "<br>")


def _yaml_scalar(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value)
    return json.dumps(text, ensure_ascii=False)


def _chapter_title(chapter: ChapterModel) -> str:
    return (chapter.title or f"第{int(chapter.chapter_number)}章").strip()


def _character_name_from_id(payload: ObsidianVaultPayload, character_id: object) -> str:
    target = str(character_id or "")
    for character in _items(payload.story_bible, "characters"):
        if str(_attr(character, "id", default="")) == target:
            return _text_attr(character, "name", fallback="未命名人物")
    return target or "未知人物"


def _relationships_for_character(payload: ObsidianVaultPayload, name: str) -> list[Any]:
    out: list[Any] = []
    for relationship in _items(payload.story_bible, "relationships"):
        a = _text_attr(relationship, "character_a", fallback="未知人物")
        b = _text_attr(relationship, "character_b", fallback="未知人物")
        if name in {a, b}:
            out.append(relationship)
    return out


def _other_character_name(relationship: object, name: str) -> str:
    a = _text_attr(relationship, "character_a", fallback="未知人物")
    b = _text_attr(relationship, "character_b", fallback="未知人物")
    return b if a == name else a


def _model_dump(value: object) -> object:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, Mapping):
        return dict(value)
    if hasattr(value, "__dict__"):
        return {
            key: _model_dump(item)
            for key, item in vars(value).items()
            if not key.startswith("_")
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [_model_dump(item) for item in value]
    return value


def _hash_manifest(manifest: Mapping[str, Any]) -> str:
    content = json.dumps(manifest, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(content).hexdigest()
