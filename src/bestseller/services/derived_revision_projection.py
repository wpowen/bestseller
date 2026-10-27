"""Refresh downstream planning projections after a derived input revision.

The apply step intentionally changes only the canonical BookSpec/WorldSpec.
This module is the bounded, no-LLM bridge that rebuilds CAST_SPEC and
VOLUME_PLAN from those new inputs before an outline attempt.  It never resumes
production and never clears the structural ``needs_replan`` gate.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any
from uuid import UUID

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.domain.enums import ArtifactType, ProjectStatus, WorkflowStatus
from bestseller.domain.planning import PlanningArtifactCreate
from bestseller.infra.db.models import (
    BookProductionControlModel,
    ChapterDraftVersionModel,
    ChapterModel,
    PlanningArtifactVersionModel,
    ProjectModel,
    ReviewReportModel,
    WorkflowRunModel,
)
from bestseller.services.planner import (
    _compile_source_bound_cast_spec,
    _compile_source_bound_volume_plan,
    _enforce_premise_locked_cast,
    _enforce_seriality_volume_mapping,
)
from bestseller.services.projects import import_planning_artifact
from bestseller.services.story_rebuild_workspace import source_content_hash
from bestseller.services.workflows import materialize_story_bible

_ACTIVE_WORKFLOWS = frozenset(
    status.value
    for status in (
        WorkflowStatus.PENDING,
        WorkflowStatus.QUEUED,
        WorkflowStatus.RUNNING,
        WorkflowStatus.MACHINE_BLOCKED,
    )
)


class DerivedProjectionError(RuntimeError):
    """Projection cannot be refreshed without weakening a source contract."""

    def __init__(self, message: str, *, details: Mapping[str, Any] | None = None) -> None:
        super().__init__(message)
        self.details = dict(details or {})


def _text(value: object) -> str:
    return str(value or "").strip()


def _mapping(value: Any) -> Mapping[str, Any]:  # noqa: ANN401
    return value if isinstance(value, Mapping) else {}


def _marker(metadata: Mapping[str, Any]) -> Mapping[str, Any]:
    value = metadata.get("derived_input_revision")
    return value if isinstance(value, Mapping) else {}


def _identity_names(cast: Any) -> set[str]:  # noqa: ANN401
    names: set[str] = set()
    if not isinstance(cast, Mapping):
        return names
    for key in ("protagonist", "antagonist_forces", "supporting_cast", "characters"):
        value = cast.get(key)
        values = value if isinstance(value, list) else [value]
        for item in values:
            if isinstance(item, Mapping):
                for name_key in ("name", "canonical_name"):
                    name = _text(item.get(name_key))
                    if name:
                        names.add(name)
    return names


def _character_identity_entries(cast: Any) -> list[dict[str, Any]]:  # noqa: ANN401
    """Return person entries with stable paths, keeping force names separate."""

    if not isinstance(cast, Mapping):
        return []
    entries: list[dict[str, Any]] = []
    for key in ("protagonist", "antagonist"):
        value = cast.get(key)
        if isinstance(value, Mapping):
            entries.append(
                {
                    "path": key,
                    "name": _text(value.get("name") or value.get("canonical_name")),
                    "role": _text(value.get("role")) or key,
                    "payload": value,
                }
            )
    for index, value in enumerate(cast.get("supporting_cast") or []):
        if isinstance(value, Mapping):
            entries.append(
                {
                    "path": f"supporting_cast[{index}]",
                    "name": _text(value.get("name") or value.get("canonical_name")),
                    "role": _text(value.get("role")) or "supporting",
                    "payload": value,
                }
            )
    for index, value in enumerate(cast.get("characters") or []):
        if isinstance(value, Mapping):
            entries.append(
                {
                    "path": f"characters[{index}]",
                    "name": _text(value.get("name") or value.get("canonical_name")),
                    "role": _text(value.get("role")) or "character",
                    "payload": value,
                }
            )
    return [entry for entry in entries if entry["name"]]


def _validate_projection_cast_identity(
    cast: object, *, expected_protagonist: str = ""
) -> None:
    """Fail closed on role collisions before a derived cast can be approved."""

    if not isinstance(cast, Mapping):
        raise DerivedProjectionError("DERIVED_PROJECTION_CAST_IDENTITY_INVALID")
    entries = _character_identity_entries(cast)
    seen: dict[str, dict[str, Any]] = {}
    for entry in entries:
        key = entry["name"].casefold()
        prior = seen.get(key)
        if prior is not None and prior["path"] != entry["path"]:
            raise DerivedProjectionError(
                "DERIVED_PROJECTION_CAST_IDENTITY_COLLISION: "
                f"{prior['name']}@{prior['path']}={entry['name']}@{entry['path']}"
            )
        seen[key] = entry
    protagonist = next(
        (entry["name"] for entry in entries if entry["path"] == "protagonist"),
        "",
    )
    if expected_protagonist and protagonist != expected_protagonist:
        raise DerivedProjectionError(
            "DERIVED_PROJECTION_CAST_PROTAGONIST_DRIFT: "
            f"expected={expected_protagonist!r}, actual={protagonist!r}"
        )


def _source_name_evidence(
    name: str,
    *,
    premise: str,
    book: Mapping[str, Any],
    world: Mapping[str, Any],
) -> list[dict[str, str]]:
    """Locate exact approved-source references for one legacy character name."""

    evidence: list[dict[str, str]] = []
    ignored_keys = frozenset({"_meta", "metadata", "control", "audit", "provenance"})

    def walk(value: Any, path: str, source: str) -> None:  # noqa: ANN401
        if len(evidence) >= 12:
            return
        if isinstance(value, str):
            if name and name in value:
                compact = value.replace("\n", " ").strip()
                start = max(0, compact.find(name) - 50)
                evidence.append(
                    {
                        "source": source,
                        "path": path,
                        "quote": compact[start : start + 180],
                    }
                )
            return
        if isinstance(value, Mapping):
            for key, child in value.items():
                if str(key).lower() in ignored_keys:
                    continue
                walk(child, f"{path}.{key}", source)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                walk(child, f"{path}[{index}]", source)

    walk(premise, "premise", "premise")
    walk(book, "book_spec", "book_spec")
    walk(world, "world_spec", "world_spec")
    return evidence


_RECOVERY_IDENTITY_FIELDS = frozenset(
    {
        "name",
        "canonical_name",
        "aliases",
        "name_reasoning",
        "role",
        "gender",
        "pronoun_set_zh",
        "pronoun_set_en",
        "metadata",
    }
)


def _recover_cast_from_approved_sources(
    compiled_cast: Mapping[str, Any],
    legacy_cast: Mapping[str, Any],
    *,
    premise: str,
    book: Mapping[str, Any],
    world: Mapping[str, Any],
    legacy_artifact_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build a diagnostic candidate without importing legacy descriptions.

    The compiled candidate owns names, roles, and identity locks.  Legacy
    entries are retained only in the audit as field-diff observations.  A
    name appearing in an approved prose source is not enough to add a slot or
    copy its description; the typed source-review bridge is the only path that
    may later apply an explicitly evidenced field patch.
    """

    recovered = deepcopy(dict(compiled_cast))
    entries = _character_identity_entries(recovered)
    by_name = {entry["name"].casefold(): entry for entry in entries}
    audit: dict[str, Any] = {
        "mode": "approved_source_identity_recovery.v1",
        "promotion_status": "needs_independent_source_review",
        "legacy_cast_artifact_id": legacy_artifact_id,
        "legacy_cast_hash": source_content_hash(dict(legacy_cast)),
        "approved_sources": {
            "premise": {"content_hash": source_content_hash(premise)},
            "book_spec": {"content_hash": source_content_hash(dict(book))},
            "world_spec": {"content_hash": source_content_hash(dict(world))},
        },
        "entries": [],
    }

    for legacy in _character_identity_entries(legacy_cast):
        name = legacy["name"]
        evidence = _source_name_evidence(
            name,
            premise=premise,
            book=book,
            world=world,
        )
        row: dict[str, Any] = {
            "legacy_path": legacy["path"],
            "name": name,
            "evidence": evidence,
            "candidate_fields": [],
            "decision": "omitted",
        }
        if not evidence:
            row["reason"] = "name_absent_from_approved_sources"
            audit["entries"].append(row)
            continue

        target = by_name.get(name.casefold())
        if target is None:
            row["reason"] = "legacy_slot_preserved_unverified"
            row["decision"] = "legacy_preserved_unverified"
            row["field_diff"] = {
                "proposed_description_fields": [
                    key for key in legacy["payload"] if key not in _RECOVERY_IDENTITY_FIELDS
                ],
                "applied": False,
            }
            audit["entries"].append(row)
            continue

        target_path = str(target.get("path") or "")
        legacy_path = str(legacy["path"])
        same_role = (
            (legacy_path == "protagonist" and target_path == "protagonist")
            or (legacy_path == "antagonist" and target_path == "antagonist")
            or (
                legacy_path.startswith("supporting_cast")
                and target_path.startswith("supporting_cast")
            )
            or (
                legacy_path.startswith("characters")
                and target_path.startswith("characters")
            )
        )
        if not same_role:
            row["reason"] = "legacy_role_conflicts_with_authoritative_role"
            audit["entries"].append(row)
            continue

        target_payload = target.get("payload")
        if not isinstance(target_payload, dict):
            for candidate in _character_identity_entries(recovered):
                if candidate["path"] == target.get("path"):
                    target_payload = candidate["payload"]
                    break
        if isinstance(target_payload, dict):
            for key in legacy["payload"]:
                if key in _RECOVERY_IDENTITY_FIELDS or key not in target_payload:
                    continue
                row["candidate_fields"].append(key)
            row["decision"] = "candidate_only_requires_source_review"
            row["field_diff"] = {
                "proposed_description_fields": list(row["candidate_fields"]),
                "applied": False,
            }
        audit["entries"].append(row)

    _validate_projection_cast_identity(
        recovered,
        expected_protagonist=_text(
            _mapping(compiled_cast.get("protagonist")).get("name")
        ),
    )
    audit["field_diff"] = {
        "legacy_identity_count": len(_character_identity_entries(legacy_cast)),
        "recovered_identity_count": len(_character_identity_entries(recovered)),
    }
    audit["candidate_cast_hash"] = source_content_hash(recovered)
    return recovered, audit


def _with_projection_meta(
    payload: dict[str, Any],
    *,
    report_id: str,
    parent_book_id: UUID,
    parent_world_id: UUID,
) -> dict[str, Any]:
    result = deepcopy(payload)
    raw_meta = result.get("_meta")
    meta: Mapping[str, Any] = raw_meta if isinstance(raw_meta, Mapping) else {}
    result["_meta"] = {
        **dict(meta),
        "derived_revision_projection": True,
        "revision_report_id": report_id,
        "parent_book_artifact_id": str(parent_book_id),
        "parent_world_artifact_id": str(parent_world_id),
    }
    return result


async def refresh_derived_revision_projection(
    session: AsyncSession,
    settings: Any,  # noqa: ANN401 - compiler settings are intentionally opaque
    project_slug: str,
    report_id: str | UUID,
    *,
    cast_report_id: UUID | None = None,
    cast_source_review_id: UUID | None = None,
) -> dict[str, Any]:
    """Compile and persist fresh CAST/VOLUME projections without committing.

    The caller owns the transaction boundary.  Any exception leaves the
    transaction for the caller to roll back; no partial projection is
    advertised as current.
    """
    if cast_report_id is not None and cast_source_review_id is not None:
        raise DerivedProjectionError(
            "DERIVED_PROJECTION_CAST_REVIEW_IDS_MUTUALLY_EXCLUSIVE"
        )

    del settings  # source-bound compilers are deterministic and do not use it
    project = await session.scalar(
        select(ProjectModel).where(ProjectModel.slug == project_slug).with_for_update()
    )
    if project is None:
        raise DerivedProjectionError("DERIVED_PROJECTION_PROJECT_NOT_FOUND")
    if _text(project.status).lower() != ProjectStatus.PAUSED.value:
        raise DerivedProjectionError("DERIVED_PROJECTION_PAUSE_REQUIRED")

    control = await session.scalar(
        select(BookProductionControlModel)
        .where(BookProductionControlModel.project_id == project.id)
        .with_for_update()
    )
    if control is None or _text(control.desired_state).lower() != "pause":
        raise DerivedProjectionError("DERIVED_PROJECTION_AUTHORITATIVE_PAUSE_REQUIRED")

    workflow = await session.scalar(
        select(WorkflowRunModel.id)
        .where(
            and_(
                WorkflowRunModel.project_id == project.id,
                WorkflowRunModel.status.in_(_ACTIVE_WORKFLOWS),
            )
        )
        .limit(1)
    )
    if workflow is not None:
        raise DerivedProjectionError("DERIVED_PROJECTION_ACTIVE_WORKFLOW")
    draft = await session.scalar(
        select(ChapterDraftVersionModel.id)
        .join(ChapterModel, ChapterModel.id == ChapterDraftVersionModel.chapter_id)
        .where(ChapterModel.project_id == project.id)
        .limit(1)
    )
    if draft is not None:
        raise DerivedProjectionError("DERIVED_PROJECTION_DRAFTS_ALREADY_EXIST")

    metadata = deepcopy(dict(project.metadata_json or {}))
    applied = _marker(metadata)
    if _text(applied.get("status")) != "applied" or _text(applied.get("report_id")) != _text(
        report_id
    ):
        raise DerivedProjectionError("DERIVED_PROJECTION_APPLIED_MARKER_MISMATCH")
    existing = metadata.get("derived_revision_projection")

    report = await session.scalar(
        select(ReviewReportModel).where(
            and_(
                ReviewReportModel.id == report_id,
                ReviewReportModel.project_id == project.id,
                ReviewReportModel.verdict == "pass",
                ReviewReportModel.reviewer_type == "derived_input_revision",
            )
        )
    )
    if report is None:
        raise DerivedProjectionError("DERIVED_PROJECTION_REPORT_NOT_APPROVED")
    from bestseller.services.derived_revision import verify_derived_revision_report

    try:
        verify_derived_revision_report(report.structured_output)
    except (TypeError, ValueError) as exc:
        raise DerivedProjectionError("DERIVED_PROJECTION_REPORT_INVALID") from exc

    book_id = _text(applied.get("book_artifact_id"))
    world_id = _text(applied.get("world_artifact_id"))
    book = await session.scalar(
        select(PlanningArtifactVersionModel).where(
            PlanningArtifactVersionModel.id == book_id,
            PlanningArtifactVersionModel.project_id == project.id,
            PlanningArtifactVersionModel.artifact_type == ArtifactType.BOOK_SPEC.value,
            PlanningArtifactVersionModel.status == "approved",
        )
    )
    world = await session.scalar(
        select(PlanningArtifactVersionModel).where(
            PlanningArtifactVersionModel.id == world_id,
            PlanningArtifactVersionModel.project_id == project.id,
            PlanningArtifactVersionModel.artifact_type == ArtifactType.WORLD_SPEC.value,
            PlanningArtifactVersionModel.status == "approved",
        )
    )
    if book is None or world is None:
        raise DerivedProjectionError("DERIVED_PROJECTION_CURRENT_INPUTS_MISSING")
    latest_book = await session.scalar(
        select(PlanningArtifactVersionModel)
        .where(
            PlanningArtifactVersionModel.project_id == project.id,
            PlanningArtifactVersionModel.artifact_type == ArtifactType.BOOK_SPEC.value,
            PlanningArtifactVersionModel.status == "approved",
        )
        .order_by(PlanningArtifactVersionModel.version_no.desc())
        .limit(1)
    )
    latest_world = await session.scalar(
        select(PlanningArtifactVersionModel)
        .where(
            PlanningArtifactVersionModel.project_id == project.id,
            PlanningArtifactVersionModel.artifact_type == ArtifactType.WORLD_SPEC.value,
            PlanningArtifactVersionModel.status == "approved",
        )
        .order_by(PlanningArtifactVersionModel.version_no.desc())
        .limit(1)
    )
    if (
        latest_book is None
        or latest_world is None
        or latest_book.id != book.id
        or latest_world.id != world.id
    ):
        raise DerivedProjectionError("DERIVED_PROJECTION_INPUT_POINTER_STALE")

    premise = await session.scalar(
        select(PlanningArtifactVersionModel)
        .where(
            PlanningArtifactVersionModel.project_id == project.id,
            PlanningArtifactVersionModel.artifact_type == ArtifactType.PREMISE.value,
            PlanningArtifactVersionModel.status == "approved",
        )
        .order_by(PlanningArtifactVersionModel.version_no.desc())
        .limit(1)
    )
    if premise is None or not isinstance(premise.content, Mapping):
        raise DerivedProjectionError("DERIVED_PROJECTION_APPROVED_PREMISE_MISSING")
    premise_text = _text(premise.content.get("premise") or premise.content.get("text"))
    if not premise_text:
        raise DerivedProjectionError("DERIVED_PROJECTION_APPROVED_PREMISE_EMPTY")
    from bestseller.services.world_recompile_candidate import build_world_recompile_candidate

    current_candidate = build_world_recompile_candidate(
        project,
        premise=premise_text,
        premise_artifact_id=str(premise.id),
        source_artifact=world,
    )
    if current_candidate["input_hash"] != report.structured_output["candidate"]["input_hash"]:
        raise DerivedProjectionError("DERIVED_PROJECTION_SOURCE_INPUTS_STALE")
    if isinstance(existing, Mapping) and _text(existing.get("report_id")) == _text(report_id):
        for key, kind in (("cast_artifact_id", "cast_spec"), ("volume_artifact_id", "volume_plan")):
            latest = await session.scalar(
                select(PlanningArtifactVersionModel)
                .where(
                    PlanningArtifactVersionModel.project_id == project.id,
                    PlanningArtifactVersionModel.artifact_type == kind,
                )
                .order_by(PlanningArtifactVersionModel.version_no.desc())
                .limit(1)
            )
            if latest is None or str(latest.id) != existing.get(key) or latest.status != "approved":
                raise DerivedProjectionError("DERIVED_PROJECTION_STALE_COMPLETED_POINTER")
        return {**dict(existing), "status": "already_projected"}
    from bestseller.services.derived_cast_source_review import normalize_cast_recovery_sources

    book_payload, world_payload = normalize_cast_recovery_sources(book.content, world.content)

    old_cast = await session.scalar(
        select(PlanningArtifactVersionModel)
        .where(
            PlanningArtifactVersionModel.project_id == project.id,
            PlanningArtifactVersionModel.artifact_type == ArtifactType.CAST_SPEC.value,
            PlanningArtifactVersionModel.status == "approved",
        )
        .order_by(PlanningArtifactVersionModel.version_no.desc())
        .limit(1)
    )
    old_cast_identity_valid = True
    old_cast_identity_error = ""
    if old_cast is not None:
        try:
            _validate_projection_cast_identity(old_cast.content)
        except DerivedProjectionError as exc:
            # A historical cast with a role collision is evidence of the
            # corruption this recovery is meant to remove.  It must not become
            # the identity authority through the subset check below.
            old_cast_identity_valid = False
            old_cast_identity_error = str(exc)
    cast_recovery_audit: dict[str, Any] | None = None
    if cast_report_id is not None:
        from bestseller.services.derived_cast_repair import verify_cast_dependency_report

        cast_review = await session.scalar(
            select(ReviewReportModel).where(
                ReviewReportModel.id == cast_report_id,
                ReviewReportModel.project_id == project.id,
                ReviewReportModel.reviewer_type == "derived_cast_dependency",
                ReviewReportModel.verdict == "pass",
            )
        )
        if (
            old_cast is None
            or cast_review is None
            or cast_review.target_id != old_cast.id
            or cast_review.structured_output.get("derived_revision_report_id") != str(report_id)
            or cast_review.structured_output.get("source_world_id") != str(world.id)
        ):
            raise DerivedProjectionError("DERIVED_PROJECTION_CAST_REVIEW_NOT_BOUND")
        verify_cast_dependency_report(
            cast_review.structured_output,
            cast_content=old_cast.content,
            world_content=world_payload,
        )
        cast_payload = deepcopy(cast_review.structured_output["candidate_cast"])
        _validate_projection_cast_identity(
            cast_payload,
            expected_protagonist=_text(
                _mapping(book.content).get("protagonist", {}).get("name")
                if isinstance(_mapping(book.content).get("protagonist"), Mapping)
                else ""
            ),
        )
    elif old_cast is not None and not old_cast_identity_valid:
        from bestseller.services.derived_cast_source_review import (
            build_cast_recovery_candidate,
            build_historical_cast_evidence,
            collect_approved_historical_casts,
        )

        compiled = _compile_source_bound_cast_spec(
            project, premise_text, book_payload, world_payload
        )
        compiled = _enforce_premise_locked_cast(
            compiled, premise_text, language=project.language
        )
        historical_casts = await collect_approved_historical_casts(
            session, project_id=project.id, current_version=old_cast.version_no
        )
        historical_evidence = build_historical_cast_evidence(old_cast, historical_casts)
        candidate_cast, candidate_audit = build_cast_recovery_candidate(
            compiled,
            old_cast.content,
            approved_sources={
                "premise": premise_text,
                "book_spec": book_payload,
                "world_spec": world_payload,
            },
            parent_ids={
                "premise": str(premise.id),
                "book": str(book.id),
                "world": str(world.id),
            },
            revision_report_id=_text(report_id),
            legacy_artifact_id=str(old_cast.id),
            legacy_artifact_status=old_cast.status,
            legacy_artifact_version=old_cast.version_no,
            historical_cast_evidence=historical_evidence,
        )
        if cast_source_review_id is None:
            raise DerivedProjectionError(
                "DERIVED_PROJECTION_CAST_SOURCE_REVIEW_REQUIRED",
                details={
                    "candidate_cast": candidate_cast,
                    "cast_identity_recovery": candidate_audit,
                    "legacy_cast_identity_error": old_cast_identity_error,
                },
            )
        source_review = await session.scalar(
            select(ReviewReportModel).where(
                ReviewReportModel.id == cast_source_review_id,
                ReviewReportModel.project_id == project.id,
                ReviewReportModel.target_id == old_cast.id,
                ReviewReportModel.reviewer_type == "derived_cast_source",
                ReviewReportModel.verdict == "pass",
            )
        )
        if source_review is None:
            raise DerivedProjectionError("DERIVED_PROJECTION_CAST_SOURCE_REVIEW_NOT_BOUND")
        source_payload = source_review.structured_output
        if (
            source_payload.get("derived_revision_report_id") != str(report_id)
            or source_payload.get("source_world_id") != str(world.id)
            or source_payload.get("source_book_id") != str(book.id)
            or source_payload.get("source_premise_id") != str(premise.id)
        ):
            raise DerivedProjectionError("DERIVED_PROJECTION_CAST_SOURCE_REVIEW_NOT_BOUND")
        from bestseller.services.derived_cast_source_review import verify_prepared_cast_source_review
        try:
            cast_payload, cast_recovery_audit = verify_prepared_cast_source_review(
                source_payload,
                base_candidate=candidate_cast,
                base_audit=candidate_audit,
                historical_cast_evidence=historical_evidence,
            )
        except (TypeError, ValueError) as exc:
            raise DerivedProjectionError(
                "DERIVED_PROJECTION_CAST_SOURCE_REPLAY_INVALID", details={"reason": str(exc)}
            ) from exc
    else:
        cast_payload = _compile_source_bound_cast_spec(
            project, premise_text, book_payload, world_payload
        )
        cast_payload = _enforce_premise_locked_cast(
            cast_payload, premise_text, language=project.language
        )
        if old_cast is not None and not old_cast_identity_valid:
            cast_payload, cast_recovery_audit = _recover_cast_from_approved_sources(
                cast_payload,
                old_cast.content,
                premise=premise_text,
                book=book_payload,
                world=world_payload,
                legacy_artifact_id=str(old_cast.id),
            )
            raise DerivedProjectionError(
                "DERIVED_PROJECTION_CAST_SOURCE_REVIEW_REQUIRED",
                details={
                    "candidate_cast": cast_payload,
                    "cast_identity_recovery": cast_recovery_audit,
                    "legacy_cast_identity_error": old_cast_identity_error,
                },
            )

    expected_protagonist = _text(
        _mapping(book_payload.get("protagonist")).get("name")
    )
    _validate_projection_cast_identity(
        cast_payload,
        expected_protagonist=expected_protagonist,
    )
    if old_cast is not None and old_cast_identity_valid:
        old_names = _identity_names(old_cast.content)
        new_names = _identity_names(cast_payload)
        if not old_names.issubset(new_names):
            raise DerivedProjectionError(
                f"DERIVED_PROJECTION_IDENTITY_DRIFT: missing={sorted(old_names - new_names)!r}; "
                f"compiled={sorted(new_names)!r}"
            )

    volume_payload = _compile_source_bound_volume_plan(
        project, premise_text, book_payload, world_payload, cast_payload
    )
    _enforce_seriality_volume_mapping(project, volume_payload)
    cast_payload = _with_projection_meta(
        cast_payload,
        report_id=_text(report_id),
        parent_book_id=book.id,
        parent_world_id=world.id,
    )
    if cast_recovery_audit is not None:
        cast_payload["_meta"] = {
            **_mapping(cast_payload.get("_meta")),
            "cast_identity_recovery": cast_recovery_audit,
            "legacy_cast_identity_error": old_cast_identity_error,
        }
    volume_projection_payload = _with_projection_meta(
        {"volumes": volume_payload},
        report_id=_text(report_id),
        parent_book_id=book.id,
        parent_world_id=world.id,
    )
    cast_artifact = await import_planning_artifact(
        session,
        project_slug,
        PlanningArtifactCreate(
            artifact_type=ArtifactType.CAST_SPEC,
            content=cast_payload,
            notes=f"derived revision projection report={report_id} parent_world={world.id}",
        ),
    )
    volume_artifact = await import_planning_artifact(
        session,
        project_slug,
        PlanningArtifactCreate(
            artifact_type=ArtifactType.VOLUME_PLAN,
            content=volume_projection_payload["volumes"],
            notes=f"derived revision projection report={report_id} parent_world={world.id}",
        ),
    )
    story_bible = await materialize_story_bible(
        session,
        project_slug,
        requested_by="derived_revision_projection",
        book_spec_content=book_payload,
        world_spec_content=world_payload,
        cast_spec_content=cast_payload,
        volume_plan_content=volume_payload,
        source_artifact_ids={
            "book_spec": book.id,
            "world_spec": world.id,
            "cast_spec": cast_artifact.id,
            "volume_plan": volume_artifact.id,
        },
    )
    projection = {
        "report_id": _text(report_id),
        "parent_book_artifact_id": str(book.id),
        "parent_world_artifact_id": str(world.id),
        "cast_artifact_id": str(cast_artifact.id),
        "volume_artifact_id": str(volume_artifact.id),
        "story_bible_workflow_id": str(story_bible.workflow_run_id),
        "cast_dependency_report_id": str(cast_report_id) if cast_report_id else None,
        "cast_identity_recovery": cast_recovery_audit,
        "projection_status": "completed",
        "status": "projected",
    }
    applied = dict(applied)
    applied.update(
        {
            "book_artifact_id": str(book.id),
            "world_artifact_id": str(world.id),
            "cast_artifact_id": str(cast_artifact.id),
            "volume_artifact_id": str(volume_artifact.id),
            "story_bible_workflow_id": str(story_bible.workflow_run_id),
            "projection_pending": False,
            "projection_status": "completed",
        }
    )
    # materialize_story_bible may enrich metadata (identity manifest, retrieval
    # pointers). Merge into its post-materialization state instead of writing
    # the pre-materialization snapshot back over those updates.
    fresh_metadata = deepcopy(dict(project.metadata_json or {}))
    fresh_metadata["derived_input_revision"] = applied
    fresh_metadata["derived_revision_projection"] = projection
    fresh_metadata["derived_revision_projection_pending"] = False
    fresh_metadata["planning_status"] = "needs_replan"
    fresh_metadata["outline_semantic_gate_status"] = "needs_replan"
    project.metadata_json = fresh_metadata
    return {"status": "projected", **projection}


__all__ = ["DerivedProjectionError", "refresh_derived_revision_projection"]
