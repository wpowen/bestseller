from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

from bestseller.domain.enums import ArtifactType
from bestseller.domain.planning import PlanningArtifactCreate
from bestseller.domain.project import ProjectCreate
from bestseller.infra.db.models import ProjectModel, StyleGuideModel
from bestseller.services import projects as project_services
from bestseller.settings import load_settings


pytestmark = pytest.mark.unit


class FakeSession:
    def __init__(self, scalar_results: list[object | None] | None = None) -> None:
        self.scalar_results = list(scalar_results or [])
        self.added: list[object] = []
        self.last_scalar_statement = None

    def add(self, obj: object) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        for obj in self.added:
            table = getattr(obj, "__table__", None)
            if table is None or "id" not in table.c:
                continue
            if getattr(obj, "id", None) is None:
                setattr(obj, "id", uuid4())

    async def scalar(self, stmt: object) -> object | None:
        self.last_scalar_statement = stmt
        if not self.scalar_results:
            return None
        return self.scalar_results.pop(0)


def build_settings() -> object:
    return load_settings(
        config_path=Path("config/default.yaml"),
        local_config_path=Path("config/does-not-exist.yaml"),
        env={},
    )


@pytest.mark.asyncio
async def test_create_project_creates_default_style_guide(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_get_project_by_slug(session: object, slug: str) -> None:
        return None

    monkeypatch.setattr(project_services, "get_project_by_slug", fake_get_project_by_slug)
    session = FakeSession()

    project = await project_services.create_project(
        session,
        ProjectCreate(
            slug="my-story",
            title="My Story",
            genre="fantasy",
            target_word_count=120000,
            target_chapters=60,
        ),
        build_settings(),
    )

    assert project.id is not None
    assert project.slug == "my-story"
    style_guides = [obj for obj in session.added if isinstance(obj, StyleGuideModel)]
    assert len(style_guides) == 1
    assert style_guides[0].project_id == project.id
    assert style_guides[0].tone_keywords == ["fantasy"]


@pytest.mark.asyncio
async def test_create_project_rejects_duplicate_slug(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_get_project_by_slug(session: object, slug: str) -> object:
        return object()

    monkeypatch.setattr(project_services, "get_project_by_slug", fake_get_project_by_slug)

    with pytest.raises(ValueError, match="already exists"):
        await project_services.create_project(
            FakeSession(),
            ProjectCreate(
                slug="my-story",
                title="My Story",
                genre="fantasy",
                target_word_count=120000,
                target_chapters=60,
            ),
            build_settings(),
        )


@pytest.mark.asyncio
async def test_import_planning_artifact_uses_null_scope_filter_and_increments_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = ProjectModel(
        slug="my-story",
        title="My Story",
        genre="fantasy",
        target_word_count=120000,
        target_chapters=60,
        metadata_json={},
    )
    project.id = uuid4()

    async def fake_get_project_by_slug(session: object, slug: str) -> ProjectModel:
        return project

    monkeypatch.setattr(project_services, "get_project_by_slug", fake_get_project_by_slug)
    session = FakeSession(scalar_results=[2])

    artifact = await project_services.import_planning_artifact(
        session,
        "my-story",
        PlanningArtifactCreate(
            artifact_type=ArtifactType.BOOK_SPEC,
            content={"logline": "A hero survives."},
        ),
    )

    assert artifact.version_no == 3
    assert artifact.project_id == project.id
    compiled_sql = str(
        session.last_scalar_statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    assert "scope_ref_id IS NULL" in compiled_sql


def test_load_json_file_reads_payload(tmp_path: Path) -> None:
    payload_path = tmp_path / "payload.json"
    payload_path.write_text(json.dumps({"chapter": 1, "title": "Opening"}), encoding="utf-8")

    payload = project_services.load_json_file(payload_path)

    assert payload["chapter"] == 1
    assert payload["title"] == "Opening"
