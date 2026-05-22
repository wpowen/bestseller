from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from typer.testing import CliRunner

from bestseller.cli.maintenance import (
    _stamp_retention_repair_request,
    maintenance_app,
)
from bestseller.domain.enums import ChapterStatus, SceneStatus

pytestmark = pytest.mark.unit


class FakeScalarResult:
    def __init__(self, rows: list[object]) -> None:
        self._rows = rows

    def __iter__(self):
        return iter(self._rows)


class FakeSession:
    def __init__(self, scenes: list[object]) -> None:
        self.scenes = scenes
        self.executed = 0

    async def scalars(self, _stmt: object) -> FakeScalarResult:
        return FakeScalarResult(self.scenes)

    async def execute(self, _stmt: object) -> None:
        self.executed += 1


def test_retention_repair_command_registered() -> None:
    result = CliRunner().invoke(maintenance_app, ["retention-repair", "--help"])

    assert result.exit_code == 0
    assert "--slug" in result.output
    assert "--chapter" in result.output


@pytest.mark.asyncio
async def test_stamp_retention_repair_request_resets_chapter_and_scenes() -> None:
    chapter = SimpleNamespace(
        id=uuid4(),
        metadata_json={},
        status="complete",
        production_state="ok",
    )
    scene = SimpleNamespace(
        id=uuid4(),
        scene_number=1,
        status=SceneStatus.APPROVED.value,
        metadata_json={},
    )
    session = FakeSession([scene])

    await _stamp_retention_repair_request(
        session,
        chapter=chapter,
        max_retries=3,
    )

    assert chapter.status == ChapterStatus.REVISION.value
    assert chapter.production_state == "blocked"
    assert chapter.metadata_json["retention_repair_requested"] is True
    assert chapter.metadata_json["retention_repair_max_retries"] == 3
    assert "HOOK_ECHO_MISSING" in chapter.metadata_json["auto_repair_last_block_codes"]
    assert scene.status == SceneStatus.NEEDS_REWRITE.value
    assert "留存自修复" in scene.metadata_json["auto_repair_hint"]
    assert session.executed == 1
