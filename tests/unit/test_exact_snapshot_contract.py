from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from bestseller.services import continuity

pytestmark = pytest.mark.unit


class _Session:
    def __init__(self, result: object | None) -> None:
        self.result = result
        self.statement: object | None = None

    async def scalar(self, statement: object) -> object | None:
        self.statement = statement
        return self.result


@pytest.mark.asyncio
async def test_previous_snapshot_requires_exact_promoted_predecessor() -> None:
    project_id = uuid4()
    session = _Session(None)

    snapshot = await continuity.load_previous_chapter_snapshot(
        session,  # type: ignore[arg-type]
        project_id=project_id,
        current_chapter_number=8,
    )

    assert snapshot is None
    where = str(session.statement).split("WHERE", maxsplit=1)[1]
    assert "chapter_number =" in where
    assert "chapter_number <" not in where
    assert "extraction_status" in where


@pytest.mark.asyncio
async def test_exact_promoted_snapshot_is_returned_as_continuity_authority() -> None:
    row = SimpleNamespace(
        chapter_number=7,
        facts={"facts": [{"name": "倒计时", "value": "20", "kind": "countdown"}]},
        time_anchor="第七天夜",
        chapter_time_span="两小时",
    )

    snapshot = await continuity.load_previous_chapter_snapshot(
        _Session(row),  # type: ignore[arg-type]
        project_id=uuid4(),
        current_chapter_number=8,
    )

    assert snapshot is not None
    assert snapshot.chapter_number == 7
    assert snapshot.facts[0].value == "20"
