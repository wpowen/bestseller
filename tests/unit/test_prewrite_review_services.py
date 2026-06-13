from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from bestseller.infra.db.models import (
    ChapterDraftVersionModel,
    ChapterModel,
    PlanningArtifactVersionModel,
    ProjectModel,
)
from bestseller.services import chapter_revision, prewrite_review

pytestmark = pytest.mark.unit


class _ScalarResult:
    def __init__(self, rows: list[object]) -> None:
        self._rows = rows

    def scalars(self) -> list[object]:
        return self._rows


class FakeSession:
    def __init__(
        self,
        *,
        execute_results: list[list[object]] | None = None,
        scalar_results: list[object | None] | None = None,
        get_results: dict[object, object] | None = None,
    ) -> None:
        self.execute_results = list(execute_results or [])
        self.scalar_results = list(scalar_results or [])
        self.get_results = dict(get_results or {})
        self.added: list[object] = []
        self.flushed = False

    async def execute(self, stmt: object) -> _ScalarResult:
        rows = self.execute_results.pop(0) if self.execute_results else []
        return _ScalarResult(rows)

    async def scalar(self, stmt: object) -> object | None:
        return self.scalar_results.pop(0) if self.scalar_results else None

    async def get(self, model: object, key: object) -> object | None:
        return self.get_results.get(key)

    def add(self, obj: object) -> None:
        if getattr(obj, "id", None) is None:
            obj.id = uuid4()
        self.added.append(obj)

    async def flush(self) -> None:
        self.flushed = True


def _project() -> ProjectModel:
    project = ProjectModel(
        slug="gate-book",
        title="门禁之书",
        genre="fantasy",
        target_word_count=10000,
        target_chapters=3,
        status="planning",
        metadata_json={},
    )
    project.id = uuid4()
    return project


@pytest.mark.asyncio
async def test_prewrite_review_requires_current_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    project = _project()
    artifact = PlanningArtifactVersionModel(
        project_id=project.id,
        artifact_type="book_spec",
        version_no=1,
        status="approved",
        schema_version="1.0",
        content={"title": "门禁之书"},
        created_by="system",
    )
    artifact.id = uuid4()
    artifact.created_at = datetime.now(UTC)
    approved_snapshot = {
        "book_spec": {
            "artifact_id": str(artifact.id),
            "version_no": 1,
            "status": "approved",
            "created_at": artifact.created_at.isoformat(),
        }
    }
    project.metadata_json = {
        "prewrite_review": {
            "status": "approved",
            "approved_snapshot": approved_snapshot,
            "approved_by": "tester",
            "approved_at": "2026-06-13T00:00:00+00:00",
        }
    }

    async def fake_get_project_by_slug(session: object, slug: str) -> ProjectModel:
        return project

    monkeypatch.setattr(prewrite_review, "get_project_by_slug", fake_get_project_by_slug)
    payload = await prewrite_review.load_prewrite_review_payload(
        FakeSession(execute_results=[[artifact], []]),
        "gate-book",
    )

    assert payload["is_approved"] is True
    assert payload["status"] == "approved"
    assert payload["current_snapshot"] == approved_snapshot


@pytest.mark.asyncio
async def test_create_chapter_revision_task_records_source_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _project()
    chapter = ChapterModel(
        project_id=project.id,
        chapter_number=1,
        title="第一章",
        chapter_goal="开场",
        status="review",
        target_word_count=2000,
        current_word_count=1200,
        information_revealed=[],
        information_withheld=[],
        foreshadowing_actions={},
        metadata_json={},
        production_state="ok",
    )
    chapter.id = uuid4()
    current = ChapterDraftVersionModel(
        project_id=project.id,
        chapter_id=chapter.id,
        version_no=2,
        content_md="当前正文",
        word_count=4,
        assembled_from_scene_draft_ids=[],
    )
    current.id = uuid4()

    async def fake_get_project_by_slug(session: object, slug: str) -> ProjectModel:
        return project

    monkeypatch.setattr(chapter_revision, "get_project_by_slug", fake_get_project_by_slug)
    session = FakeSession(scalar_results=[chapter, current])

    task = await chapter_revision.create_chapter_revision_task(
        session,
        "gate-book",
        1,
        operation="humanize",
        requested_by="reader-ui",
    )

    assert task in session.added
    assert task.trigger_type == "reader_chapter_revision"
    assert task.rewrite_strategy == "chapter_humanize"
    assert task.metadata_json["source_chapter_draft_id"] == str(current.id)
    assert task.metadata_json["source_chapter_draft_version_no"] == 2
