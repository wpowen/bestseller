"""Opt-in creation configuration and independent, bounded execution windows.

No defaults are installed on legacy projects. Saving never dispatches work or
rewrites an adopted body; changes to protected creative data become proposals.
"""
from collections.abc import Iterable, Mapping
from copy import deepcopy
from typing import Any, Literal, Self
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from bestseller.infra.db.models import ProjectModel
from bestseller.settings import AppSettings

from pydantic import BaseModel, ConfigDict, Field, StrictInt, ValidationError, model_validator
from sqlalchemy import select

from bestseller.services.prompt_source_policy import content_hash


class FrozenConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PlatformTargets(FrozenConfig):
    primary: str | None = Field(default=None, max_length=120)
    compatible: list[str] = Field(default_factory=list, max_length=12)

    @model_validator(mode="after")
    def valid_targets(self) -> Self:
        from bestseller.services.writing_presets import list_platform_presets
        allowed = {p.key for p in list_platform_presets()}
        values = ([self.primary] if self.primary else []) + self.compatible
        if len(values) != len(set(values)) or any(v not in allowed for v in values):
            raise ValueError("platform targets must be distinct registered platform IDs")
        if self.compatible and not self.primary:
            raise ValueError("compatible targets require a primary platform")
        return self


class ReaderTargets(FrozenConfig):
    primary: str | None = Field(default=None, max_length=240)
    secondary: list[str] = Field(default_factory=list, max_length=12)

    @model_validator(mode="after")
    def valid_readers(self) -> Self:
        values = ([self.primary] if self.primary else []) + self.secondary
        if any(not isinstance(v, str) or not v.strip() or len(v) > 240 for v in values):
            raise ValueError("reader profiles require nonempty bounded labels")
        if len(values) != len(set(values)):
            raise ValueError("primary and secondary readers must be distinct")
        if self.secondary and not self.primary:
            raise ValueError("secondary readers require a primary reader")
        return self


class BookExtent(FrozenConfig):
    total_chapters: StrictInt = Field(default=500, ge=1, le=2000)
    chapter_target: StrictInt = Field(default=2000, ge=1800, le=3500)
    unit: Literal["zh_body_chars"] = "zh_body_chars"

    @property
    def total_target(self) -> int:
        return self.total_chapters * self.chapter_target


class CreationConfiguration(FrozenConfig):
    schema_version: Literal["creation-configuration.v1"] = "creation-configuration.v1"
    platforms: PlatformTargets = Field(default_factory=PlatformTargets)
    readers: ReaderTargets = Field(default_factory=ReaderTargets)
    book: BookExtent = Field(default_factory=BookExtent)
    # Empty extension point only. Assets need a separate approved provenance
    # chain; no image observation is invented by this configuration slice.
    creative_asset_refs: list[str] = Field(default_factory=list, max_length=0)
    field_sources: dict[str, Literal["explicit", "default", "derived", "legacy"]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def source_fields(self) -> Self:
        if set(self.field_sources) - {"platforms", "readers", "book"}:
            raise ValueError("unknown configuration provenance field")
        return self

    def digest(self) -> str:
        return content_hash(self.model_dump(mode="json"))


class ExecutionWindow(FrozenConfig):
    schema_version: Literal["execution-window.v1"] = "execution-window.v1"
    purpose: Literal["complete_story", "opening_window", "continuation"] = "opening_window"
    start: StrictInt = Field(default=1, ge=1, le=2000)
    end: StrictInt = Field(default=20, ge=1, le=2000)

    @model_validator(mode="after")
    def bounds(self) -> Self:
        if self.end < self.start:
            raise ValueError("execution window end precedes start")
        if self.purpose == "opening_window" and self.start != 1:
            raise ValueError("opening window must start at chapter 1")
        return self

    def numbers(self) -> set[int]:
        return set(range(self.start, self.end + 1))


class ConfigurationSubmission(FrozenConfig):
    configuration: CreationConfiguration
    execution_window: ExecutionWindow

    @model_validator(mode="after")
    def inside_book(self) -> Self:
        if self.execution_window.end > self.configuration.book.total_chapters:
            raise ValueError("execution window exceeds whole-book target")
        return self


def parse_configuration_submission(submission: object) -> ConfigurationSubmission:
    """Keep incomplete drafts intact and explain dependent choices at submission."""
    try:
        return ConfigurationSubmission.model_validate(submission)
    except ValidationError as exc:
        messages = {
            ("configuration", "platforms"): (
                "compatible targets require a primary platform",
                "已选择兼容平台，请先选择主平台；如不指定平台，请取消所有兼容平台。",
            ),
            ("configuration", "readers"): (
                "secondary readers require a primary reader",
                "已填写次要读者，请先填写主要读者；如不指定读者，请清空次要读者。",
            ),
        }
        for issue in exc.errors(include_input=False):
            expected = messages.get(tuple(issue["loc"]))
            if expected and str((issue.get("ctx") or {}).get("error")) == expected[0]:
                raise ValueError(expected[1]) from exc
        raise


def read_configuration(project: ProjectModel) -> CreationConfiguration | None:
    meta = getattr(project, "metadata_json", None) or {}
    raw = meta.get("creation_configuration")
    if raw is None:
        return None  # Do not install new defaults on existing books.
    cfg = CreationConfiguration.model_validate(raw)
    if cfg.digest() != meta.get("creation_configuration_hash"):
        raise ValueError("creation_configuration_hash_mismatch")
    contract = meta.get("creation_intent_contract") or {}
    if isinstance(contract, Mapping) and contract.get("configuration") is not None:
        if contract["configuration"] != cfg.model_dump(mode="json"):
            raise ValueError("creation_configuration_contract_mismatch")
    if (int(project.target_chapters) != cfg.book.total_chapters
            or int(project.target_word_count) != cfg.book.total_target):
        raise ValueError("creation_configuration_project_projection_mismatch")
    return cfg


def read_window(project: ProjectModel) -> ExecutionWindow | None:
    cfg = read_configuration(project)
    if cfg is None:
        return None
    meta = project.metadata_json or {}
    raw = meta.get("execution_window")
    if raw is None:
        raise ValueError("configured_project_execution_window_missing")
    submission = ConfigurationSubmission(configuration=cfg, execution_window=ExecutionWindow.model_validate(raw))
    if meta.get("execution_window_hash") != content_hash(submission.execution_window.model_dump(mode="json")):
        raise ValueError("execution_window_hash_mismatch")
    return submission.execution_window


def configuration_metadata(metadata: Mapping[str, Any] | None, submission: object) -> dict[str, Any]:
    value = parse_configuration_submission(submission)
    cfg, window = value.configuration, value.execution_window
    result = deepcopy(dict(metadata or {}))
    result.update(creation_configuration=cfg.model_dump(mode="json"), creation_configuration_hash=cfg.digest(),
        execution_window=window.model_dump(mode="json"),
        execution_window_hash=content_hash(window.model_dump(mode="json")))
    contract = result.get("creation_intent_contract")
    if isinstance(contract, Mapping):
        from bestseller.services.creation_intent_contract import CreationIntentContract
        revised = {**contract, "schema_version": "creation-intent.v2", "configuration": cfg.model_dump(mode="json"),
            "chapter_count": cfg.book.total_chapters,
            "field_sources": {**contract.get("field_sources", {}), "configuration": "explicit",
                **({"chapter_count": "explicit"} if cfg.field_sources.get("book") == "explicit" else {})}}
        result["creation_intent_contract"] = CreationIntentContract.model_validate(revised).model_dump(mode="json")
        result["creation_intent_contract_hash"] = content_hash(result["creation_intent_contract"])
    return result


def creative_context(project: ProjectModel) -> dict[str, Any]:
    cfg = read_configuration(project)
    if cfg is None:
        return {}
    # Window cursor is operational. Advancing it cannot turn old paid reviews
    # into a different creative source, or silently change the whole-book ending.
    return {"schema_version": cfg.schema_version, "platforms": cfg.platforms.model_dump(),
        "readers": cfg.readers.model_dump(), "book": cfg.book.model_dump(),
        "field_sources": cfg.field_sources, "primary_target_rules": "primary governs; compatible targets are advisory",
        "submission_authorized": False}


def filter_window(project: ProjectModel, numbers: Iterable[int]) -> set[int]:
    window = read_window(project)
    values = set(numbers)
    return values & window.numbers() if window is not None else values


def configuration_view(project: ProjectModel) -> dict[str, Any]:
    cfg = read_configuration(project)
    window = read_window(project) if cfg else None
    return {"schema_version": "creation-configuration-view.v1", "project_id": str(project.id),
        "configuration": cfg.model_dump(mode="json") if cfg else None,
        "configuration_hash": cfg.digest() if cfg else None,
        "execution_window": window.model_dump(mode="json") if window else None,
        "execution_window_hash": (project.metadata_json or {}).get("execution_window_hash") if cfg else None,
        "legacy": cfg is None, "whole_book_chapters": project.target_chapters,
        "whole_book_target_chars": project.target_word_count,
        "counting_basis": "正文汉字；不计标点、数字、拉丁字母、frontmatter及生产注释",
        "publication_authorized": False}


async def save_project_configuration(
    session: AsyncSession, project: ProjectModel, submission: object, *,
    expected_hash: str | None, expected_window_hash: str | None,
) -> dict[str, Any]:
    """CAS local service. Protected creative edits are reviewable proposals only."""
    from bestseller.infra.db.models import ChapterModel, PlanningArtifactVersionModel, ProjectModel, WorkflowRunModel
    value = parse_configuration_submission(submission)
    locked_project = await session.get(ProjectModel, project.id, with_for_update=True, populate_existing=True)
    if locked_project is None:
        raise ValueError("creation_configuration_project_missing")
    project = locked_project
    before = configuration_view(project)
    if before["configuration_hash"] != expected_hash or before["execution_window_hash"] != expected_window_hash:
        raise ValueError("creation_configuration_concurrent_change")
    creative_changed = before["configuration"] != value.configuration.model_dump(mode="json")
    active = await session.scalar(select(WorkflowRunModel.id).where(WorkflowRunModel.project_id == project.id,
        WorkflowRunModel.status.in_(["running", "queued", "pending"])).limit(1))
    if active is not None:
        raise ValueError("creation_configuration_active_workflow_stop_before_edit")
    chapters = list(await session.scalars(select(ChapterModel).where(ChapterModel.project_id == project.id)))
    approved = await session.scalar(select(PlanningArtifactVersionModel.id).where(
        PlanningArtifactVersionModel.project_id == project.id, PlanningArtifactVersionModel.status == "approved").limit(1))
    # Any existing chapter is conservatively protected: an unfinished chapter
    # may already own paid evidence, even if it is not adopted yet.
    protected = bool(chapters or approved or (project.metadata_json or {}).get("book_design_snapshot_status") == "approved")
    changes = {key: {"before": (before["configuration"] or {}).get(key), "after": val}
               for key, val in value.configuration.model_dump(mode="json").items()
               if (before["configuration"] or {}).get(key) != val}
    if before["execution_window"] != value.execution_window.model_dump(mode="json"):
        changes["execution_window"] = {"before": before["execution_window"], "after": value.execution_window.model_dump(mode="json")}
    receipt = {"before": before, "requested": value.model_dump(mode="json"), "changes": changes,
        "affected_chapters": [c.chapter_number for c in chapters], "automatic_rewrite": False,
        "requires_replan": creative_changed and protected}
    status = "proposal_requires_replan" if creative_changed and protected else "saved"
    row = WorkflowRunModel(project_id=project.id, workflow_type="creation_configuration_change",
        scope_type="project", scope_id=project.id, status="completed", current_step=status, metadata_json=receipt)
    session.add(row)
    if status == "saved":
        project.metadata_json = configuration_metadata(project.metadata_json, value)
        project.target_chapters = value.configuration.book.total_chapters
        project.target_word_count = value.configuration.book.total_target
        project.audience = value.configuration.readers.primary
    await session.flush()
    return {"status": status, "receipt_id": str(row.id), "diff": receipt,
        "effective": configuration_view(project)}


def preview_submission(submission: object) -> dict[str, Any]:
    value = parse_configuration_submission(submission)
    cfg, window = value.configuration, value.execution_window
    return {"submission": value.model_dump(mode="json"), "configuration_hash": cfg.digest(),
        "whole_book_chapters": cfg.book.total_chapters, "whole_book_target_chars": cfg.book.total_target,
        "window_chapters": window.end-window.start+1, "window_range": [window.start, window.end],
        "counting_basis": "正文汉字；不计标点、数字、拉丁字母、frontmatter及生产注释",
        "conflict_receipt": {"primary": cfg.platforms.primary, "compatible": cfg.platforms.compatible,
            "policy": "primary_governs_secondary_advisory", "unresolved": [],
            "compatibility_not_verified": bool(cfg.platforms.compatible or cfg.readers.secondary)},
        "automatic_rewrite": False, "dispatch_started": False, "submission_authorized": False}


def project_settings(settings: AppSettings, project: ProjectModel) -> AppSettings:
    cfg = read_configuration(project)
    if cfg is None:
        return settings
    result = settings.model_copy(deep=True)
    result.generation.words_per_chapter.target = cfg.book.chapter_target
    scene = result.generation.words_per_scene
    scene.target = min(scene.max, max(scene.min, round(cfg.book.chapter_target / result.generation.scenes_per_chapter.target)))
    return result


def primary_platform_label(cfg: CreationConfiguration | None) -> str | None:
    if cfg is None or not cfg.platforms.primary:
        return None
    from bestseller.services.writing_presets import list_platform_presets
    return next(p.name for p in list_platform_presets() if p.key == cfg.platforms.primary)


def configuration_revision_fingerprint(project: ProjectModel) -> str:
    """CAS source for an explicit revision, including all current metadata."""
    return content_hash({"id": str(project.id), "title": project.title,
        "genre": project.genre, "language": project.language, "audience": project.audience,
        "target_chapters": project.target_chapters, "target_word_count": project.target_word_count,
        "status": str(project.status), "metadata": project.metadata_json})


async def apply_project_configuration_proposal(
    session: AsyncSession, *, project_id: UUID, proposal_id: UUID,
    expected_project_sha256: str, expected_proposal_sha256: str,
    actor: str, authorization_ref: str, explicit_user_approval: bool = False,
) -> dict[str, Any]:
    """Trusted operator apply: version contract, archive sources, require replan.

    Never registered as a model tool. No adopted bodies, planning rows, old
    reviews, controls, budget or spent counters are rewritten. Caller commits.
    """
    from bestseller.infra.db.models import PlanningArtifactVersionModel, WorkflowRunModel
    if explicit_user_approval is not True or not actor.strip() or not authorization_ref.strip():
        raise ValueError("configuration_apply_explicit_authorization_required")
    project = await session.get(ProjectModel, project_id, with_for_update=True, populate_existing=True)
    proposal = await session.get(WorkflowRunModel, proposal_id, with_for_update=True, populate_existing=True)
    if (project is None or proposal is None or proposal.project_id != project_id
            or proposal.workflow_type != "creation_configuration_change"):
        raise ValueError("configuration_apply_scope_mismatch")
    data = deepcopy(proposal.metadata_json or {})
    if content_hash(data.get("requested")) != expected_proposal_sha256:
        raise ValueError("configuration_apply_proposal_changed")
    prior = data.get("application")
    if prior is not None:
        if (prior["authorization_ref"] != authorization_ref or prior["actor"] != actor
                or prior["effective"] != configuration_view(project)):
            raise ValueError("configuration_apply_idempotence_conflict")
        return dict(prior)
    if (proposal.current_step != "proposal_requires_replan"
            or proposal.status != "completed"
            or configuration_revision_fingerprint(project) != expected_project_sha256
            or configuration_view(project) != data.get("before")):
        raise ValueError("configuration_apply_stale_source")
    active = await session.scalar(select(WorkflowRunModel.id).where(
        WorkflowRunModel.project_id == project_id,
        WorkflowRunModel.status.in_(["running", "queued", "pending"])).limit(1))
    if active is not None:
        raise ValueError("configuration_apply_active_workflow")
    requested = parse_configuration_submission(data["requested"])
    from bestseller.infra.db.models import ChapterModel
    outside = await session.scalar(select(ChapterModel.id).where(
        ChapterModel.project_id == project_id,
        ChapterModel.chapter_number > requested.configuration.book.total_chapters).limit(1))
    if outside is not None:
        raise ValueError("configuration_apply_existing_chapters_outside_new_book")
    assets = list(await session.scalars(select(PlanningArtifactVersionModel).where(
        PlanningArtifactVersionModel.project_id == project_id)))
    archive = WorkflowRunModel(project_id=project_id, workflow_type="creation_configuration_revision",
        scope_type="project", scope_id=project_id, status="completed",
        current_step="applied_requires_replan", requested_by=actor[:64], metadata_json={
            "schema": "creation-configuration-revision.v1", "proposal_id": str(proposal_id),
            "actor": actor, "authorization_ref": authorization_ref,
            "before_source_sha256": expected_project_sha256,
            "before": {"configuration_view": configuration_view(project),
                "metadata": deepcopy(project.metadata_json), "status": str(project.status),
                "audience": project.audience},
            "planning_sources_retained": [{"id": str(a.id), "type": a.artifact_type,
                "status": str(a.status), "content_sha256": content_hash(a.content)} for a in assets],
            "requested": requested.model_dump(mode="json"),
            "old_reviews_and_attempts_reusable_as_new_grade": False,
            "prose_generation_authorized": False})
    session.add(archive)
    await session.flush()
    metadata = configuration_metadata(project.metadata_json, requested)
    metadata["configuration_revision"] = {"id": str(archive.id), "proposal_id": str(proposal_id),
        "status": "requires_replan", "authorization_ref": authorization_ref,
        "new_configuration_hash": requested.configuration.digest()}
    metadata["planning_status"] = "needs_replan"
    project.metadata_json = metadata
    project.target_chapters = requested.configuration.book.total_chapters
    project.target_word_count = requested.configuration.book.total_target
    if requested.configuration.field_sources.get("readers") == "explicit":
        project.audience = requested.configuration.readers.primary
    if str(project.status) not in {"paused", "cancelled", "archived"}:
        project.status = "needs_replan"
    result = {"revision_id": str(archive.id), "actor": actor, "authorization_ref": authorization_ref,
        "effective": configuration_view(project), "requires_replan": True,
        "old_assets_unchanged": True, "old_spent_unchanged": True}
    proposal.metadata_json = {**data, "application": result}
    proposal.current_step = "applied_requires_replan"
    await session.flush()
    return result


async def complete_empty_configuration_revision_lineage(
    session: AsyncSession, project: ProjectModel,
) -> dict[str, Any] | None:
    """Reseal only version/extent lineage from the same adopted story, no LLM.

    Empty-book revision boundary. Old canonical row has its exact content
    archived before normal supersession; no old grade/attempt becomes new.
    """
    from bestseller.infra.db.models import ChapterModel, PlanningArtifactVersionModel, WorkflowRunModel
    from bestseller.services.concept_contract import reseal_concept_contract_lineage, validate_concept_contract
    from bestseller.services.conception_snapshots import create_conception_snapshot_artifact, promote_conception_snapshot, snapshot_hash
    from bestseller.services.book_design import ensure_project_book_design_snapshot
    locked = await session.get(ProjectModel, project.id, with_for_update=True, populate_existing=True)
    if locked is None:
        raise ValueError("configuration_revision_project_missing")
    project = locked
    metadata = deepcopy(project.metadata_json or {})
    revision = metadata.get("configuration_revision") or {}
    if not revision or not metadata.get("creation_canonical_snapshot_id") or not metadata.get("concept_contract"):
        return None
    if revision.get("lineage_receipt"):
        if validate_concept_contract(metadata["concept_contract"], target_chapters=project.target_chapters):
            raise ValueError("configuration_revision_lineage_changed")
        return dict(revision["lineage_receipt"])
    if revision.get("status") != "requires_replan":
        raise ValueError("configuration_revision_not_pending_replan")
    if await session.scalar(select(ChapterModel.id).where(ChapterModel.project_id == project.id).limit(1)):
        raise ValueError("configuration_lineage_requires_empty_book")
    archive = await session.get(WorkflowRunModel, UUID(revision["id"]), with_for_update=True)
    if (archive is None or archive.project_id != project.id
            or archive.workflow_type != "creation_configuration_revision"
            or archive.metadata_json.get("authorization_ref") != revision.get("authorization_ref")):
        raise ValueError("configuration_revision_authority_missing")
    old = await session.get(PlanningArtifactVersionModel, UUID(metadata["creation_canonical_snapshot_id"]), with_for_update=True)
    if (old is None or old.project_id != project.id or old.status != "canonical"
            or snapshot_hash(old.content) != metadata.get("creation_canonical_snapshot_hash")):
        raise ValueError("configuration_revision_canonical_source_changed")
    prior_contract = old.content.get("concept_contract") or {}
    old_target = int(prior_contract.get("target_chapters") or 0)
    if validate_concept_contract(prior_contract, target_chapters=old_target):
        raise ValueError("configuration_revision_old_contract_invalid")
    revised = reseal_concept_contract_lineage(prior_contract, target_chapters=project.target_chapters)
    authority = archive.metadata_json["actor"]
    intent = await create_conception_snapshot_artifact(session, project_id=project.id,
        artifact_type="creation_intent", content=deepcopy(metadata["creation_intent_contract"]),
        status="validated", schema_version="creation-intent.v2",
        idempotency_key="configuration-revision-intent:"+str(archive.id),
        source_run_id=archive.id, created_by=authority,
        notes="Explicit size revision; not a new model result")
    content = deepcopy(old.content)
    for key in ("hook_card", "seriality_proof", "story_spine"):
        content[key] = deepcopy(revised[key])
    content["concept_contract"] = revised
    content["creation_intent_hash"] = snapshot_hash(intent.content)
    content["configuration_revision_source"] = {"revision_id": str(archive.id),
        "original_snapshot_id": str(old.id), "original_content_hash": snapshot_hash(old.content),
        "kind": "creator_directed_extent_revision", "new_model_grade_claimed": False}
    archive.metadata_json = {**archive.metadata_json, "lineage_before": {
        "snapshot_id": str(old.id), "status": old.status, "content": deepcopy(old.content),
        "content_hash": snapshot_hash(old.content)}}
    derived = await create_conception_snapshot_artifact(session, project_id=project.id,
        artifact_type="conception_snapshot", content=content, status="validated",
        idempotency_key="configuration-revision-canonical:"+str(archive.id),
        source_run_id=archive.id, created_by=authority,
        notes="Same adopted story; explicit extent revision; old paid grade not reused")
    for key in ("concept_contract", "hook_card", "seriality_proof", "story_spine"):
        metadata[key] = deepcopy(revised if key == "concept_contract" else revised[key])
    metadata["creation_intent_snapshot_id"] = str(intent.id)
    project.metadata_json = metadata
    await promote_conception_snapshot(session, project_id=project.id, snapshot_id=derived.id,
        expected_lock_version=project.lock_version,
        actor=authority, reason="Explicit configuration revision; preserve original story, no reconception")
    ensure_project_book_design_snapshot(project, protagonist_name=metadata.get("creation_protagonist_name"), force_rebuild=True)
    receipt = {"original_snapshot_id": str(old.id), "snapshot_id": str(derived.id),
        "old_target_chapters": old_target, "new_target_chapters": project.target_chapters,
        "new_model_grade_claimed": False, "old_attempts_reset": False}
    project.metadata_json = {**project.metadata_json,
        "book_design_adoption_source": {"kind": "adopted_conception", "snapshot_id": str(derived.id), "content_hash": snapshot_hash(derived.content)},
        "configuration_revision": {**revision, "lineage_receipt": receipt}}
    await session.flush()
    return receipt
