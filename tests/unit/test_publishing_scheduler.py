from __future__ import annotations

from uuid import uuid4

import pytest

from bestseller.infra.db.models import (
    ChapterDraftVersionModel,
    ChapterModel,
    ProjectModel,
    PublishingHistoryModel,
    PublishingPlatformModel,
    PublishingScheduleModel,
)
from bestseller.scheduler import jobs as scheduler_jobs
from bestseller.settings import load_settings

pytestmark = pytest.mark.unit


class _ScalarResult:
    def __init__(self, value: object | None) -> None:
        self._value = value

    def scalar_one_or_none(self) -> object | None:
        return self._value


class _FakeSession:
    def __init__(self, *results: object | None) -> None:
        self._results = list(results)
        self.added: list[object] = []

    async def execute(self, stmt: object) -> _ScalarResult:
        if not self._results:
            return _ScalarResult(None)
        return _ScalarResult(self._results.pop(0))

    def add(self, obj: object) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        for obj in self.added:
            if getattr(obj, "id", None) is None:
                obj.id = uuid4()


@pytest.mark.asyncio
async def test_publish_next_chapter_blocks_pending_chapter_before_adapter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id = uuid4()
    platform_id = uuid4()
    schedule = PublishingScheduleModel(
        project_id=project_id,
        platform_id=platform_id,
        cron_expression="0 8 * * *",
        timezone="Asia/Shanghai",
        start_chapter=1,
        current_chapter=29,
        chapters_per_release=1,
        status="active",
        metadata_json={},
    )
    schedule.id = uuid4()
    platform = PublishingPlatformModel(
        project_id=project_id,
        name="番茄",
        platform_type="fanqie",
        api_base_url="https://example.invalid",
        credentials_enc=None,
        metadata_json={},
    )
    platform.id = platform_id
    project = ProjectModel(
        slug="xianxia-upgrade-1776137730",
        title="道种破虚",
        genre="xianxia",
        target_word_count=1_500_000,
        target_chapters=550,
        metadata_json={},
    )
    project.id = project_id
    chapter = ChapterModel(
        project_id=project_id,
        chapter_number=30,
        title="沉渊绞杀",
        chapter_goal="推进主线",
        information_revealed=[],
        information_withheld=[],
        foreshadowing_actions={},
        target_word_count=3000,
        status="drafting",
        production_state="pending",
        metadata_json={},
    )
    chapter.id = uuid4()
    draft = ChapterDraftVersionModel(
        project_id=project_id,
        chapter_id=chapter.id,
        version_no=12,
        content_md="# 第30章 沉渊绞杀\n\n宁尘站在药圃边。",
        word_count=20,
        assembled_from_scene_draft_ids=[],
        is_current=True,
    )
    draft.id = uuid4()

    class _Adapter:
        called = False

        async def publish_chapter(self, *args: object, **kwargs: object) -> object:
            self.called = True
            raise AssertionError("adapter must not be called when publication gate blocks")

    adapter = _Adapter()
    monkeypatch.setattr(scheduler_jobs, "get_adapter", lambda **kwargs: adapter)

    async def fake_comparison_payloads(*args: object, **kwargs: object):
        return [(chapter, draft)]

    monkeypatch.setattr(
        scheduler_jobs,
        "load_publication_comparison_payloads",
        fake_comparison_payloads,
    )
    session = _FakeSession(schedule, platform, project, chapter, draft)

    published = await scheduler_jobs.publish_next_chapter(
        session=session,
        settings=load_settings(env={}),
        schedule_id=schedule.id,
    )

    assert published is False
    assert adapter.called is False
    history = next(obj for obj in session.added if isinstance(obj, PublishingHistoryModel))
    assert history.status == "failed"
    assert "不是可发布状态" in (history.error_message or "")
    assert "不是 ok" in (history.error_message or "")
