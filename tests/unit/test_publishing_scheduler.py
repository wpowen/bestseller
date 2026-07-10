from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

from bestseller.infra.db.models import (
    ChapterDraftVersionModel,
    ChapterModel,
    ProjectModel,
    PublishingHistoryModel,
    PublishingPlatformModel,
    PublishingScheduleModel,
)
from bestseller.scheduler import jobs as scheduler_jobs
from bestseller.scheduler import main as scheduler_main
from bestseller.services.publishing.base import PublishResult
from bestseller.settings import load_settings

pytestmark = pytest.mark.unit


class _ScalarResult:
    def __init__(self, value: object | None) -> None:
        self._value = value

    def scalar_one_or_none(self) -> object | None:
        return self._value


class _FakeSession:
    def __init__(
        self,
        *results: object | None,
        history_results: tuple[PublishingHistoryModel | None, ...] = (),
    ) -> None:
        self._results = list(results)
        self._history_results = list(history_results)
        self.added: list[object] = []
        self.statements: list[object] = []

    async def execute(self, stmt: object) -> _ScalarResult:
        self.statements.append(stmt)
        descriptions = getattr(stmt, "column_descriptions", ())
        entity = descriptions[0].get("entity") if descriptions else None
        if entity is PublishingHistoryModel:
            if not self._history_results:
                return _ScalarResult(None)
            return _ScalarResult(self._history_results.pop(0))
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

        async def authenticate(self) -> bool:
            return True

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
    assert not [obj for obj in session.added if isinstance(obj, PublishingHistoryModel)]


def test_consecutive_failure_metadata_accepts_mapping_values() -> None:
    assert hasattr(scheduler_jobs, "_consecutive_failures_from_metadata")

    assert scheduler_jobs._consecutive_failures_from_metadata(  # type: ignore[attr-defined]
        {"consecutive_failures": "4"}
    ) == 4


@pytest.mark.asyncio
async def test_transient_auth_error_does_not_pause_schedule(
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
        current_chapter=0,
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
        slug="transient-auth",
        title="短暂故障",
        genre="xianxia",
        target_word_count=100_000,
        target_chapters=100,
        metadata_json={},
    )
    project.id = project_id

    class _Adapter:
        async def authenticate(self) -> bool:
            raise TimeoutError("platform timed out")

    monkeypatch.setattr(scheduler_jobs, "get_adapter", lambda **kwargs: _Adapter())
    session = _FakeSession(schedule, platform, project)

    published = await scheduler_jobs.publish_next_chapter(
        session=session,
        settings=load_settings(env={}),
        schedule_id=schedule.id,
    )

    assert published is False
    assert schedule.status == "active"
    assert "platform timed out" in schedule.metadata_json["last_error"]


def test_review_status_polling_job_is_registered() -> None:
    assert hasattr(scheduler_main, "_register_review_polling_job")

    class _Scheduler:
        def __init__(self) -> None:
            self.calls: list[tuple[object, dict[str, object]]] = []

        def add_job(self, func: object, **kwargs: object) -> None:
            self.calls.append((func, kwargs))

    scheduler = _Scheduler()
    scheduler_main._register_review_polling_job(scheduler)  # type: ignore[attr-defined]

    assert len(scheduler.calls) == 1
    func, kwargs = scheduler.calls[0]
    assert func is scheduler_main._poll_publish_review_statuses_job  # type: ignore[attr-defined]
    assert kwargs["id"] == "publishing_review_status_poll"
    assert kwargs["trigger"] == "interval"
    assert kwargs["replace_existing"] is True


def _publish_fixture(
    *,
    chapters_per_release: int,
) -> tuple[
    PublishingScheduleModel,
    PublishingPlatformModel,
    ProjectModel,
    list[ChapterModel],
    list[ChapterDraftVersionModel],
]:
    project_id = uuid4()
    platform_id = uuid4()
    schedule = PublishingScheduleModel(
        project_id=project_id,
        platform_id=platform_id,
        cron_expression="0 8 * * *",
        timezone="Asia/Shanghai",
        start_chapter=30,
        current_chapter=29,
        chapters_per_release=chapters_per_release,
        status="active",
        metadata_json={},
    )
    schedule.id = uuid4()
    platform = PublishingPlatformModel(
        project_id=project_id,
        name="Fanqie",
        platform_type="fanqie",
        api_base_url="https://example.invalid",
        credentials_enc=None,
        metadata_json={},
    )
    platform.id = platform_id
    project = ProjectModel(
        slug="batch-publish",
        title="Batch Publish",
        genre="fantasy",
        language="en-US",
        target_word_count=100_000,
        target_chapters=100,
        metadata_json={},
    )
    project.id = project_id
    chapters: list[ChapterModel] = []
    drafts: list[ChapterDraftVersionModel] = []
    for chapter_number in range(30, 30 + chapters_per_release):
        chapter = ChapterModel(
            project_id=project_id,
            chapter_number=chapter_number,
            title=f"Chapter {chapter_number}",
            chapter_goal="advance",
            information_revealed=[],
            information_withheld=[],
            foreshadowing_actions={},
            target_word_count=100,
            status="complete",
            production_state="ok",
            metadata_json={},
        )
        chapter.id = uuid4()
        draft = ChapterDraftVersionModel(
            project_id=project_id,
            chapter_id=chapter.id,
            version_no=1,
            content_md=f"# Chapter {chapter_number}\n\nBody " * 50,
            word_count=200,
            assembled_from_scene_draft_ids=[],
            is_current=True,
        )
        draft.id = uuid4()
        chapters.append(chapter)
        drafts.append(draft)
    return schedule, platform, project, chapters, drafts


def _patch_publication_gate(
    monkeypatch: pytest.MonkeyPatch,
    adapter: object,
) -> None:
    monkeypatch.setattr(scheduler_jobs, "get_adapter", lambda **kwargs: adapter)

    async def fake_comparison_payloads(*args: object, **kwargs: object) -> list[object]:
        return []

    monkeypatch.setattr(
        scheduler_jobs,
        "load_publication_comparison_payloads",
        fake_comparison_payloads,
    )
    monkeypatch.setattr(scheduler_jobs, "collect_publication_blockers", lambda *args, **kwargs: [])


@pytest.mark.asyncio
async def test_batch_publish_advances_through_consecutive_chapters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    schedule, platform, project, chapters, drafts = _publish_fixture(chapters_per_release=2)
    schedule.metadata_json = {
        "consecutive_failures": 2,
        "last_error": "stale transient error",
        "pause_reason": "transient",
    }

    class _Adapter:
        def __init__(self) -> None:
            self.published_numbers: list[int] = []

        async def authenticate(self) -> bool:
            return True

        async def publish_chapter(self, content: str, meta: object) -> PublishResult:
            self.published_numbers.append(meta.chapter_number)
            return PublishResult(success=True, platform_chapter_id=f"remote-{meta.chapter_number}")

    adapter = _Adapter()
    _patch_publication_gate(monkeypatch, adapter)
    session = _FakeSession(
        schedule,
        platform,
        project,
        chapters[0],
        drafts[0],
        chapters[1],
        drafts[1],
    )

    published = await scheduler_jobs.publish_next_chapter(
        session,
        load_settings(env={}),
        schedule.id,
    )

    assert published is True
    assert adapter.published_numbers == [30, 31]
    assert schedule.current_chapter == 31
    assert schedule.metadata_json == {"consecutive_failures": 0}


@pytest.mark.asyncio
async def test_content_rejection_is_not_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    schedule, platform, project, chapters, drafts = _publish_fixture(chapters_per_release=1)

    class _Adapter:
        calls = 0

        async def authenticate(self) -> bool:
            return True

        async def publish_chapter(self, content: str, meta: object) -> PublishResult:
            self.calls += 1
            return PublishResult(
                success=False,
                error_message="content rejected",
                error_kind="content",
            )

    adapter = _Adapter()
    _patch_publication_gate(monkeypatch, adapter)
    session = _FakeSession(schedule, platform, project, chapters[0], drafts[0])

    await scheduler_jobs.publish_next_chapter(session, load_settings(env={}), schedule.id)

    assert adapter.calls == 1
    history = next(item for item in session.added if isinstance(item, PublishingHistoryModel))
    assert history.platform_response_json["delivery_state"] == "known_failed"
    assert schedule.status == "active"


@pytest.mark.asyncio
async def test_retryable_failure_uses_exponential_backoff_and_stable_idempotency_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    schedule, platform, project, chapters, drafts = _publish_fixture(chapters_per_release=1)
    sleeps: list[int] = []

    class _Adapter:
        def __init__(self) -> None:
            self.calls = 0
            self.keys: list[str | None] = []

        async def authenticate(self) -> bool:
            return True

        async def publish_chapter(self, content: str, meta: object) -> PublishResult:
            self.calls += 1
            self.keys.append(meta.idempotency_key)
            if self.calls < 3:
                return PublishResult(
                    success=False,
                    error_message="service unavailable",
                    retryable=True,
                    error_kind="transient",
                )
            return PublishResult(success=True, platform_chapter_id="remote-30")

    async def fake_sleep(delay: int) -> None:
        sleeps.append(delay)

    adapter = _Adapter()
    _patch_publication_gate(monkeypatch, adapter)
    monkeypatch.setattr(scheduler_jobs.asyncio, "sleep", fake_sleep)
    session = _FakeSession(schedule, platform, project, chapters[0], drafts[0])

    published = await scheduler_jobs.publish_next_chapter(
        session,
        load_settings(env={}),
        schedule.id,
    )

    assert published is True
    assert sleeps == [5, 10]
    assert len(set(adapter.keys)) == 1
    assert adapter.keys[0] == f"{schedule.id}:30"


def test_review_poll_query_rotates_least_recently_checked_records() -> None:
    assert hasattr(scheduler_main, "_build_review_poll_query")

    stmt = scheduler_main._build_review_poll_query(limit=20)  # type: ignore[attr-defined]
    compiled = str(stmt.compile(compile_kwargs={"literal_binds": True}))

    assert "review_checked_at" in compiled
    assert "NULLS FIRST" in compiled
    assert "created_at ASC" in compiled
    assert "LIMIT 20" in compiled


def test_review_poll_query_excludes_more_than_one_batch_of_terminal_rows_before_limit() -> None:
    terminal_rows = [
        {"review_status": ("published", "rejected", "failed")[index % 3]}
        for index in range(21)
    ]
    assert len(terminal_rows) > 20

    stmt = scheduler_main._build_review_poll_query(limit=20)  # type: ignore[attr-defined]
    compiled = str(
        stmt.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )

    assert "NOT IN ('published', 'rejected', 'failed')" in compiled
    assert compiled.index("NOT IN") < compiled.index("LIMIT 20")


def test_publishing_history_has_required_unique_idempotency_key() -> None:
    column = PublishingHistoryModel.__table__.c.idempotency_key
    unique_constraints = {
        tuple(constraint.columns.keys())
        for constraint in PublishingHistoryModel.__table__.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    }

    assert column.nullable is False
    assert ("idempotency_key",) in unique_constraints


@pytest.mark.asyncio
async def test_publish_locks_schedule_row_and_reuses_existing_success_record(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    schedule, platform, project, chapters, drafts = _publish_fixture(chapters_per_release=1)
    existing = PublishingHistoryModel(
        schedule_id=schedule.id,
        project_id=project.id,
        platform_id=platform.id,
        chapter_number=30,
        idempotency_key=f"{schedule.id}:30",
        status="success",
        platform_chapter_id="remote-30",
    )
    existing.id = uuid4()

    class _Adapter:
        async def authenticate(self) -> bool:
            return True

        async def publish_chapter(self, content: str, meta: object) -> PublishResult:
            raise AssertionError("an already successful idempotency record must not republish")

    _patch_publication_gate(monkeypatch, _Adapter())
    session = _FakeSession(
        schedule,
        platform,
        project,
        chapters[0],
        drafts[0],
        history_results=(existing,),
    )

    published = await scheduler_jobs.publish_next_chapter(
        session,
        load_settings(env={}),
        schedule.id,
    )

    first_query = str(
        session.statements[0].compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    assert "FOR UPDATE" in first_query
    assert published is True
    assert schedule.current_chapter == 30
    assert not [item for item in session.added if isinstance(item, PublishingHistoryModel)]


@pytest.mark.asyncio
async def test_publish_fails_closed_for_crash_window_pending_record_without_remote_idempotency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    schedule, platform, project, chapters, drafts = _publish_fixture(chapters_per_release=1)
    existing = PublishingHistoryModel(
        schedule_id=schedule.id,
        project_id=project.id,
        platform_id=platform.id,
        chapter_number=30,
        idempotency_key=f"{schedule.id}:30",
        status="pending",
        platform_response_json={"delivery_state": "uncertain"},
    )
    existing.id = uuid4()

    class _Adapter:
        supports_idempotency = False

        def __init__(self) -> None:
            self.keys: list[str | None] = []

        async def authenticate(self) -> bool:
            return True

        async def publish_chapter(self, content: str, meta: object) -> PublishResult:
            self.keys.append(meta.idempotency_key)
            return PublishResult(success=True, platform_chapter_id="remote-30")

    adapter = _Adapter()
    _patch_publication_gate(monkeypatch, adapter)
    session = _FakeSession(
        schedule,
        platform,
        project,
        chapters[0],
        drafts[0],
        history_results=(existing,),
    )

    published = await scheduler_jobs.publish_next_chapter(
        session,
        load_settings(env={}),
        schedule.id,
    )

    assert published is False
    assert adapter.keys == []
    assert schedule.current_chapter == 29
    assert schedule.status == "paused"
    assert existing.status == "pending"
    assert existing.platform_response_json["delivery_state"] == "reconcile_required"
    assert schedule.metadata_json["pause_reason"] == "reconcile_required"
    assert not [item for item in session.added if isinstance(item, PublishingHistoryModel)]


@pytest.mark.asyncio
async def test_publish_retries_existing_known_failed_delivery_without_remote_idempotency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    schedule, platform, project, chapters, drafts = _publish_fixture(chapters_per_release=1)
    existing = PublishingHistoryModel(
        schedule_id=schedule.id,
        project_id=project.id,
        platform_id=platform.id,
        chapter_number=30,
        idempotency_key=f"{schedule.id}:30",
        status="failed",
        platform_response_json={"delivery_state": "known_failed", "code": 503},
    )
    existing.id = uuid4()

    class _Adapter:
        supports_idempotency = False

        def __init__(self) -> None:
            self.calls = 0

        async def authenticate(self) -> bool:
            return True

        async def publish_chapter(self, content: str, meta: object) -> PublishResult:
            self.calls += 1
            return PublishResult(success=True, platform_chapter_id="remote-30")

    adapter = _Adapter()
    _patch_publication_gate(monkeypatch, adapter)
    session = _FakeSession(
        schedule,
        platform,
        project,
        chapters[0],
        drafts[0],
        history_results=(existing,),
    )

    published = await scheduler_jobs.publish_next_chapter(
        session,
        load_settings(env={}),
        schedule.id,
    )

    assert published is True
    assert adapter.calls == 1
    assert existing.platform_response_json["delivery_state"] == "success"
    assert schedule.current_chapter == 30


@pytest.mark.asyncio
async def test_success_without_remote_chapter_id_does_not_advance_schedule(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    schedule, platform, project, chapters, drafts = _publish_fixture(chapters_per_release=1)

    class _Adapter:
        async def authenticate(self) -> bool:
            return True

        async def publish_chapter(self, content: str, meta: object) -> PublishResult:
            return PublishResult(success=True, platform_chapter_id=None)

    _patch_publication_gate(monkeypatch, _Adapter())
    session = _FakeSession(schedule, platform, project, chapters[0], drafts[0])

    published = await scheduler_jobs.publish_next_chapter(
        session,
        load_settings(env={}),
        schedule.id,
    )

    history = next(item for item in session.added if isinstance(item, PublishingHistoryModel))
    assert published is False
    assert schedule.current_chapter == 29
    assert schedule.status == "paused"
    assert history.status == "failed"
    assert history.error_message == "Platform reported success without a remote chapter ID"
    assert history.platform_response_json["delivery_state"] == "uncertain"
