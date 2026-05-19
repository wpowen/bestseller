"""Legacy project backfill for public emotion and compliance kernels."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.domain.enums import ArtifactType
from bestseller.domain.planning import PlanningArtifactCreate
from bestseller.infra.db.models import ProjectModel
from bestseller.services.compliance_boundary_kernel import (
    build_compliance_boundary_kernel_seed,
    compliance_boundary_kernel_from_dict,
    compliance_boundary_kernel_to_dict,
)
from bestseller.services.projects import import_planning_artifact
from bestseller.services.public_emotion_kernel import (
    build_public_emotion_kernel_seed,
    public_emotion_kernel_from_dict,
    public_emotion_kernel_to_dict,
)
from bestseller.services.workflows import get_latest_planning_artifact


@dataclass(frozen=True)
class PublicEmotionBackfillResult:
    status: str
    public_emotion_kernel: dict[str, Any] | None
    compliance_boundary_kernel: dict[str, Any] | None
    source: str
    artifact_ids: dict[str, UUID] = field(default_factory=dict)
    version_nos: dict[str, int] = field(default_factory=dict)
    skipped_reason: str | None = None

    @property
    def changed(self) -> bool:
        return self.status in {"created", "repaired_invalid", "forced", "partial_created"}


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _text_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [item.strip() for item in value if isinstance(item, str) and item.strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _existing_public_kernel(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    try:
        return public_emotion_kernel_to_dict(public_emotion_kernel_from_dict(dict(value)))
    except Exception:
        return None


def _existing_compliance_kernel(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    try:
        return compliance_boundary_kernel_to_dict(
            compliance_boundary_kernel_from_dict(dict(value))
        )
    except Exception:
        return None


def _metadata_premise(metadata: Mapping[str, Any]) -> str | None:
    premise = metadata.get("premise")
    if isinstance(premise, Mapping):
        value = premise.get("premise")
        return _text(value) or None
    if isinstance(premise, str) and premise.strip():
        return premise.strip()
    for key in ("logline", "unique_hook", "dramatic_question", "theme_statement"):
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


async def _latest_artifact_content(
    session: AsyncSession,
    *,
    project_id: UUID | None,
    artifact_type: ArtifactType,
) -> dict[str, Any]:
    if project_id is None:
        return {}
    artifact = await get_latest_planning_artifact(
        session,
        project_id=project_id,
        artifact_type=artifact_type,
    )
    return _mapping(getattr(artifact, "content", None))


def _target_platform(project: ProjectModel, metadata: Mapping[str, Any]) -> str:
    writing_profile = _mapping(metadata.get("writing_profile"))
    market = _mapping(writing_profile.get("market"))
    for value in (
        metadata.get("target_platform"),
        metadata.get("platform_target"),
        market.get("platform_target"),
        getattr(project, "platform_target", None),
    ):
        text = _text(value)
        if text:
            return text
    return "general"


def _commercial_brief(
    project: ProjectModel,
    *,
    metadata: Mapping[str, Any],
    book_spec: Mapping[str, Any],
    premise: str,
) -> dict[str, Any]:
    commercial = dict(_mapping(metadata.get("commercial_brief")))
    audiences = (
        _text_list(metadata.get("target_audiences"))
        or _text_list(book_spec.get("target_audiences"))
        or _text_list(getattr(project, "audience", None))
    )
    if audiences and not commercial.get("target_audiences"):
        commercial["target_audiences"] = audiences
    if not commercial.get("reader_promise"):
        commercial["reader_promise"] = (
            book_spec.get("reader_promise")
            or book_spec.get("logline")
            or metadata.get("reader_promise")
        )
    if not commercial.get("unique_hook"):
        commercial["unique_hook"] = book_spec.get("unique_hook") or metadata.get("unique_hook")
    if not commercial.get("premise"):
        commercial["premise"] = premise
    return commercial


async def ensure_project_public_emotion_kernels(
    session: AsyncSession,
    project: ProjectModel,
    *,
    requested_by: str = "system",
    persist_artifact: bool = False,
    force: bool = False,
) -> PublicEmotionBackfillResult:
    """Ensure legacy projects have metadata kernels without touching prose.

    Existing valid kernels are preserved. Missing or invalid kernels are rebuilt
    from metadata and latest planning artifacts using deterministic seeds.
    """

    metadata = dict(project.metadata_json or {})
    existing_public = _existing_public_kernel(metadata.get("public_emotion_kernel"))
    existing_compliance = _existing_compliance_kernel(
        metadata.get("compliance_boundary_kernel")
    )
    if existing_public is not None and existing_compliance is not None and not force:
        if metadata.get("public_emotion_kernel") != existing_public:
            metadata["public_emotion_kernel"] = existing_public
        if metadata.get("compliance_boundary_kernel") != existing_compliance:
            metadata["compliance_boundary_kernel"] = existing_compliance
        project.metadata_json = metadata
        return PublicEmotionBackfillResult(
            status="existing",
            public_emotion_kernel=existing_public,
            compliance_boundary_kernel=existing_compliance,
            source="metadata",
        )

    project_id = getattr(project, "id", None)
    book_spec = _mapping(metadata.get("book_spec")) or await _latest_artifact_content(
        session,
        project_id=project_id,
        artifact_type=ArtifactType.BOOK_SPEC,
    )
    premise_artifact = await _latest_artifact_content(
        session,
        project_id=project_id,
        artifact_type=ArtifactType.PREMISE,
    )
    premise = _metadata_premise(metadata) or _text(premise_artifact.get("premise"))
    if not premise:
        premise = _text(book_spec.get("logline")) or getattr(project, "title", "")

    public_seed = build_public_emotion_kernel_seed(
        book_spec={
            **book_spec,
            "title": book_spec.get("title") or project.title,
            "genre": book_spec.get("genre") or project.genre,
            "premise": premise,
        },
        commercial_brief=_commercial_brief(
            project,
            metadata=metadata,
            book_spec=book_spec,
            premise=premise,
        ),
        project_metadata=metadata,
    )
    compliance_seed = build_compliance_boundary_kernel_seed(
        platform=_target_platform(project, metadata)
    )

    public_kernel = (
        public_emotion_kernel_to_dict(public_emotion_kernel_from_dict(public_seed))
        if existing_public is None or force
        else existing_public
    )
    compliance_kernel = (
        compliance_boundary_kernel_to_dict(
            compliance_boundary_kernel_from_dict(compliance_seed)
        )
        if existing_compliance is None or force
        else existing_compliance
    )

    status = "forced" if force else "created"
    if not force and (metadata.get("public_emotion_kernel") is not None or metadata.get("compliance_boundary_kernel") is not None):
        status = "repaired_invalid"
    elif existing_public is not None or existing_compliance is not None:
        status = "partial_created"

    backfill_meta: dict[str, Any] = {
        "status": status,
        "source": "legacy_backfill",
        "mode": "deterministic_seed",
        "requested_by": requested_by,
        "created_at": datetime.now(UTC).isoformat(),
        "used_metadata": {
            "book_spec": bool(_mapping(metadata.get("book_spec"))),
            "commercial_brief": bool(_mapping(metadata.get("commercial_brief"))),
            "target_audiences": bool(_text_list(metadata.get("target_audiences"))),
        },
        "used_artifact_fallback": {
            "book_spec": bool(book_spec) and not _mapping(metadata.get("book_spec")),
            "premise": bool(premise_artifact) and not _metadata_premise(metadata),
        },
    }
    metadata["public_emotion_kernel"] = public_kernel
    metadata["compliance_boundary_kernel"] = compliance_kernel
    metadata["public_emotion_kernel_backfill"] = backfill_meta
    project.metadata_json = metadata

    artifact_ids: dict[str, UUID] = {}
    version_nos: dict[str, int] = {}
    if persist_artifact:
        for artifact_type, key, payload in (
            (
                ArtifactType.PUBLIC_EMOTION_KERNEL,
                "public_emotion_kernel",
                public_kernel,
            ),
            (
                ArtifactType.COMPLIANCE_BOUNDARY_KERNEL,
                "compliance_boundary_kernel",
                compliance_kernel,
            ),
        ):
            artifact = await import_planning_artifact(
                session,
                project.slug,
                PlanningArtifactCreate(
                    artifact_type=artifact_type,
                    content=payload,
                    notes="Legacy public emotion/compliance kernel backfill",
                ),
            )
            artifact_ids[key] = artifact.id
            version_nos[key] = artifact.version_no

        project.metadata_json = {
            **(project.metadata_json or {}),
            "public_emotion_kernel_backfill": {
                **backfill_meta,
                "artifact_ids": {key: str(value) for key, value in artifact_ids.items()},
                "version_nos": version_nos,
            },
        }

    return PublicEmotionBackfillResult(
        status=status,
        public_emotion_kernel=public_kernel,
        compliance_boundary_kernel=compliance_kernel,
        source="legacy_backfill",
        artifact_ids=artifact_ids,
        version_nos=version_nos,
    )


__all__ = [
    "PublicEmotionBackfillResult",
    "ensure_project_public_emotion_kernels",
]
