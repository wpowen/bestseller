from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from bestseller.domain.enums import ProjectType
from bestseller.infra.db.models import ProjectModel
from bestseller.services.pipeline_flow_overview import (
    build_pipeline_flow_overview,
    resolve_pipeline_path,
)

pytestmark = pytest.mark.unit


def _project(
    *,
    slug: str = "demo",
    project_type: str = ProjectType.LINEAR.value,
    target_chapters: int = 30,
) -> ProjectModel:
    project = ProjectModel(
        slug=slug,
        title="演示书",
        genre="xuanhuan",
        target_word_count=180000,
        target_chapters=target_chapters,
        current_volume_number=1,
        current_chapter_number=0,
        status="planning",
        project_type=project_type,
        metadata_json={"prompt_pack_key": "xianxia"},
    )
    project.id = uuid4()
    project.created_at = datetime.now(timezone.utc)
    return project


def test_resolve_pipeline_path_fanqie_short() -> None:
    project = _project(project_type=ProjectType.FANQIE_SHORT.value)
    assert resolve_pipeline_path(project) == "fanqie_short"


def test_resolve_pipeline_path_progressive_by_target() -> None:
    project = _project(target_chapters=120)
    assert resolve_pipeline_path(project) == "progressive"


def test_resolve_pipeline_path_standard() -> None:
    project = _project(target_chapters=20)
    assert resolve_pipeline_path(project) == "standard"


@pytest.mark.asyncio
async def test_build_pipeline_flow_overview_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_get_project(_session, _slug: str):
        return None

    monkeypatch.setattr(
        "bestseller.services.pipeline_flow_overview.get_project_by_slug",
        fake_get_project,
    )

    class _Session:
        pass

    with pytest.raises(ValueError, match="not found"):
        await build_pipeline_flow_overview(_Session(), "missing")  # type: ignore[arg-type]
