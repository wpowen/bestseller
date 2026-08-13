"""Persistence boundary for market validation reports.

The full report becomes a versioned planning artifact
(``market_validation_report``) and its digest is backfilled into
``project.metadata_json["market_validation_summary"]`` so prompt-layer
consumers can read it without joining artifact tables — the same contract the
Fanqie craft profile uses.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from bestseller.domain.enums import ArtifactType
from bestseller.domain.market_validation import MarketValidationReport
from bestseller.domain.planning import PlanningArtifactCreate

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

METADATA_SUMMARY_KEY = "market_validation_summary"


async def persist_market_validation_report(
    session: AsyncSession,
    project_slug: str,
    report: MarketValidationReport,
) -> dict[str, Any]:
    """Persist the report for a project. Returns a small receipt dict.

    Raises only when the project does not exist (caller error); storage
    hiccups inside the artifact layer propagate as-is because the caller
    decides whether persistence is best-effort.
    """

    from bestseller.services.projects import (
        get_project_by_slug,
        import_planning_artifact,
    )

    artifact = await import_planning_artifact(
        session,
        project_slug,
        PlanningArtifactCreate(
            artifact_type=ArtifactType.MARKET_VALIDATION_REPORT,
            content=report.model_dump(mode="json"),
            notes="Advisory market validation report (never gates generation).",
        ),
    )
    project = await get_project_by_slug(session, project_slug)
    if project is not None:
        metadata = dict(getattr(project, "metadata_json", None) or {})
        metadata[METADATA_SUMMARY_KEY] = report.summary()
        project.metadata_json = metadata
    return {
        "artifact_id": str(artifact.id),
        "artifact_type": ArtifactType.MARKET_VALIDATION_REPORT.value,
        "version_no": artifact.version_no,
        "metadata_key": METADATA_SUMMARY_KEY,
    }
