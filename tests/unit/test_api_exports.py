from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from bestseller.api.routers.exports import export_novel
from bestseller.infra.db.models import ExportArtifactModel
from bestseller.services import exports as export_services
from bestseller.settings import load_settings

pytestmark = pytest.mark.unit


class _Result:
    def __init__(self, value: object) -> None:
        self._value = value

    def scalar_one_or_none(self) -> object:
        return self._value


class _Session:
    def __init__(self, project: object) -> None:
        self.project = project

    async def execute(self, stmt: object) -> _Result:
        return _Result(self.project)


@pytest.mark.asyncio
async def test_export_novel_unpacks_service_tuple_and_surfaces_warnings(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    project = SimpleNamespace(id=uuid4(), slug="tuple-export")
    artifact = ExportArtifactModel(
        project_id=project.id,
        export_type="markdown",
        source_scope="project",
        source_id=project.id,
        storage_uri=str(tmp_path / "project.md"),
        checksum="checksum",
        version_label="project-current",
        created_by_run_id=None,
        metadata_json={
            "warnings": ["chapter 2 skipped because it has no current draft"],
            "skipped_chapters": [2],
            "word_count": 321,
        },
    )
    output_path = tmp_path / "project.md"

    async def fake_export(**kwargs: object) -> tuple[object, Path]:
        return artifact, output_path

    monkeypatch.setattr(export_services, "export_project_markdown", fake_export)

    response = await export_novel(
        slug=project.slug,
        fmt="markdown",
        session=_Session(project),  # type: ignore[arg-type]
        settings=load_settings(env={}),
        _key=None,  # type: ignore[arg-type]
    )

    assert response.file_path == str(output_path)
    assert response.word_count == 321
    assert response.warnings == ["chapter 2 skipped because it has no current draft"]
    assert response.skipped_chapters == [2]


@pytest.mark.asyncio
async def test_export_novel_maps_incomplete_publication_to_conflict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = SimpleNamespace(id=uuid4(), slug="incomplete-export")

    async def fake_export(**kwargs: object) -> tuple[object, Path]:
        raise export_services.ProjectExportIncompleteError(project.slug, [2])

    monkeypatch.setattr(export_services, "export_project_markdown", fake_export)

    with pytest.raises(HTTPException) as exc_info:
        await export_novel(
            slug=project.slug,
            fmt="markdown",
            session=_Session(project),  # type: ignore[arg-type]
            settings=load_settings(env={}),
            _key=None,  # type: ignore[arg-type]
        )

    assert exc_info.value.status_code == 409
