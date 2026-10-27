"""Reader empty-state authority: actual lookup result vs absence vs failure."""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest

from bestseller.infra.db.models import ProjectModel
from bestseller.settings import load_settings
from bestseller.web import server

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "case,expected", [("project", True), ("missing", False), ("unknown", None)]
)
def test_reader_presence_is_never_inferred_from_slug_or_files(monkeypatch, case, expected):
    @asynccontextmanager
    async def session_scope(_settings):
        yield object()

    monkeypatch.setattr(server, "session_scope", session_scope)
    project = ProjectModel(
        slug="synthetic-reader-presence",
        title="Synthetic title",
        genre="现实",
        project_type="fanqie_short",
        language="zh-CN",
        target_chapters=4,
        target_word_count=8000,
        metadata_json={},
    )
    lookup = AsyncMock(
        side_effect=ValueError("unavailable") if case == "unknown" else None,
        return_value=project if case == "project" else None,
    )
    monkeypatch.setattr(server, "get_project_by_slug", lookup)
    result = server._load_reader_project_summary(load_settings(), project.slug)
    assert result["project_exists"] is expected
    assert result["title"] == ("Synthetic title" if case == "project" else project.slug)
    assert lookup.await_count == 1
