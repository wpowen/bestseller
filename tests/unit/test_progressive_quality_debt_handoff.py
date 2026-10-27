from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from bestseller.services import pipelines


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return list(self._rows)


def _settings(mode: str = "closure"):
    return SimpleNamespace(
        pipeline=SimpleNamespace(
            quality_mode=mode,
            accept_on_stall=None,
            chapter_review_block_on_failure=False,
        )
    )


def _project(metadata=None):
    return SimpleNamespace(metadata_json=metadata or {})


class _Session:
    def __init__(self, rows, workflow_metadata=None):
        self.rows = rows
        self.workflow_metadata = workflow_metadata or {}

    async def execute(self, _statement):
        return _Result(self.rows)

    async def get(self, _model, _run_id):
        return SimpleNamespace(metadata_json=self.workflow_metadata)


@pytest.mark.asyncio
async def test_full_ten_chapter_quality_debt_window_can_handoff_to_next_window() -> None:
    rows = [
        (number, "quality_debt", {"chapter_quality_debt": True})
        for number in range(1, 11)
    ]
    allowed = await pipelines._progressive_volume_has_only_conceded_quality_debt(
        _Session(rows),
        settings=_settings(),
        project=_project(),
        project_id=uuid4(),
        volume_number=1,
        expected_chapter_numbers=set(range(1, 11)),
        workflow_run_id=uuid4(),
    )
    assert allowed is True


@pytest.mark.asyncio
async def test_missing_or_structural_chapter_still_blocks_handoff() -> None:
    missing = [
        (number, "quality_debt", {"chapter_quality_debt": True})
        for number in range(1, 10)
    ]
    assert await pipelines._progressive_volume_has_only_conceded_quality_debt(
        _Session(missing),
        settings=_settings(),
        project=_project(),
        project_id=uuid4(),
        volume_number=1,
        expected_chapter_numbers=set(range(1, 11)),
        workflow_run_id=uuid4(),
    ) is False

    blocked = [
        (number, "quality_debt", {"chapter_quality_debt": True})
        for number in range(1, 10)
    ] + [(10, "blocked", {"chapter_quality_debt": False})]
    assert await pipelines._progressive_volume_has_only_conceded_quality_debt(
        _Session(blocked),
        settings=_settings(),
        project=_project(),
        project_id=uuid4(),
        volume_number=1,
        expected_chapter_numbers=set(range(1, 11)),
        workflow_run_id=uuid4(),
    ) is False


@pytest.mark.asyncio
async def test_project_consistency_block_is_not_downgraded_to_advisory() -> None:
    rows = [
        (number, "quality_debt", {"chapter_quality_debt": True})
        for number in range(1, 11)
    ]
    assert await pipelines._progressive_volume_has_only_conceded_quality_debt(
        _Session(rows, {"blocked_by_project_consistency": True}),
        settings=_settings(),
        project=_project(),
        project_id=uuid4(),
        volume_number=1,
        expected_chapter_numbers=set(range(1, 11)),
        workflow_run_id=uuid4(),
    ) is False

@pytest.mark.asyncio
async def test_settled_approved_chapters_may_mix_with_quality_debt() -> None:
    rows = [
        (1, "quality_debt", {"chapter_quality_debt": True}),
        (2, "ok", {}),
        *[(number, "quality_reviewed", {}) for number in range(3, 11)],
    ]
    assert await pipelines._progressive_volume_has_only_conceded_quality_debt(
        _Session(rows),
        settings=_settings(),
        project=_project(),
        project_id=uuid4(),
        volume_number=1,
        expected_chapter_numbers=set(range(1, 11)),
        workflow_run_id=uuid4(),
    ) is True


@pytest.mark.asyncio
async def test_all_settled_approved_without_debt_does_not_use_exception() -> None:
    rows = [(number, "ok", {}) for number in range(1, 11)]
    assert await pipelines._progressive_volume_has_only_conceded_quality_debt(
        _Session(rows),
        settings=_settings(),
        project=_project(),
        project_id=uuid4(),
        volume_number=1,
        expected_chapter_numbers=set(range(1, 11)),
        workflow_run_id=uuid4(),
    ) is False


@pytest.mark.asyncio
async def test_strict_or_explicit_enhancer_does_not_use_exception() -> None:
    rows = [(1, "quality_debt", {"chapter_quality_debt": True})] + [
        (number, "ok", {}) for number in range(2, 11)
    ]
    for settings, project in (
        (_settings("strict"), _project()),
        (_settings(), _project({"story_enhancers": {"brainhole": True}})),
    ):
        assert await pipelines._progressive_volume_has_only_conceded_quality_debt(
            _Session(rows),
            settings=settings,
            project=project,
            project_id=uuid4(),
            volume_number=1,
            expected_chapter_numbers=set(range(1, 11)),
            workflow_run_id=uuid4(),
        ) is False
