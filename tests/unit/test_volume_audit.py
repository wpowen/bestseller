from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import pytest

from bestseller.services.volume_audit import _chapter_length_stats, _volume_chapters


pytestmark = pytest.mark.unit


@dataclass
class _ScalarRows:
    values: list[Any]

    def __iter__(self):
        return iter(self.values)


@dataclass
class _FakeSession:
    volume_id: Any
    chapter_rows: list[Any]
    scalar_calls: list[Any] = field(default_factory=list)
    scalars_calls: list[Any] = field(default_factory=list)

    async def scalar(self, stmt: Any) -> Any:
        self.scalar_calls.append(stmt)
        return self.volume_id

    async def scalars(self, stmt: Any) -> _ScalarRows:
        self.scalars_calls.append(stmt)
        return _ScalarRows(self.chapter_rows)


@pytest.mark.asyncio
async def test_volume_chapters_uses_volume_id_without_chapter_relationship() -> None:
    rows = [
        SimpleNamespace(chapter_number=51, volume_id="volume-2"),
        SimpleNamespace(chapter_number=52, volume_id="volume-2"),
    ]
    session = _FakeSession(volume_id="volume-2", chapter_rows=rows)

    chapters = await _volume_chapters(session, project_id="project-1", volume_number=2)

    assert chapters == rows
    assert len(session.scalar_calls) == 1
    assert len(session.scalars_calls) == 1
    assert "ChapterModel.volume" not in str(session.scalars_calls[0])


@pytest.mark.asyncio
async def test_volume_chapters_falls_back_to_chapter_number_range_without_volume_row() -> None:
    rows = [
        SimpleNamespace(chapter_number=49, volume_id=None),
        SimpleNamespace(chapter_number=50, volume_id=None),
        SimpleNamespace(chapter_number=51, volume_id=None),
        SimpleNamespace(chapter_number=100, volume_id=None),
        SimpleNamespace(chapter_number=101, volume_id=None),
    ]
    session = _FakeSession(volume_id=None, chapter_rows=rows)

    chapters = await _volume_chapters(session, project_id="project-1", volume_number=2)

    assert [chapter.chapter_number for chapter in chapters] == [51, 100]


def test_chapter_length_stats_uses_current_word_count_not_missing_content_field() -> None:
    chapters = [
        SimpleNamespace(current_word_count=0),
        SimpleNamespace(current_word_count=1200),
        SimpleNamespace(current_word_count=1800),
    ]

    avg_len, min_len, max_len = _chapter_length_stats(chapters)

    assert avg_len == 1500
    assert min_len == 1200
    assert max_len == 1800
