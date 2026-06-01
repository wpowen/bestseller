from __future__ import annotations

from types import SimpleNamespace

import pytest

from scripts.methodology_books import run_real_abc_book


class _FakeScalarResult:
    def __init__(self, rows: list[object]) -> None:
        self._rows = rows

    def all(self) -> list[object]:
        return self._rows


class _FakeExecuteResult:
    def __init__(self, rows: list[object]) -> None:
        self._rows = rows

    def scalars(self) -> _FakeScalarResult:
        return _FakeScalarResult(self._rows)


class _FakeSession:
    def __init__(self, runs: list[object]) -> None:
        self._runs = runs
        self.flushed = False

    async def execute(self, _stmt: object) -> _FakeExecuteResult:
        return _FakeExecuteResult(self._runs)

    async def flush(self) -> None:
        self.flushed = True


class _FakeSessionScope:
    def __init__(self, session: _FakeSession) -> None:
        self._session = session

    async def __aenter__(self) -> _FakeSession:
        return self._session

    async def __aexit__(self, *_exc: object) -> None:
        return None


@pytest.mark.asyncio
async def test_watchdog_timeout_marks_validation_project_paused(monkeypatch: pytest.MonkeyPatch) -> None:
    project = SimpleNamespace(
        id="project-id",
        metadata_json={"validation_run": "methodology_abc_real"},
        status="planning",
    )
    runs = [
        SimpleNamespace(status="running", error_message=None),
        SimpleNamespace(status="running", error_message=None),
    ]
    fake_session = _FakeSession(runs)

    monkeypatch.setattr(
        run_real_abc_book,
        "session_scope",
        lambda _settings: _FakeSessionScope(fake_session),
    )

    async def _fake_get_project_by_slug(_session: object, slug: str) -> object:
        assert slug == "watchdog-proof"
        return project

    monkeypatch.setattr(
        run_real_abc_book,
        "get_project_by_slug",
        _fake_get_project_by_slug,
    )

    await run_real_abc_book._mark_running_workflows_failed(
        object(),
        "watchdog-proof",
        message="TimeoutError: pipeline exceeded 1s watchdog",
    )

    assert fake_session.flushed is True
    assert project.status == "paused"
    assert project.metadata_json["validation_run"] == "methodology_abc_real"
    assert project.metadata_json["validation_run_timed_out"] is True
    assert project.metadata_json["production_paused"] is True
    assert project.metadata_json["production_pause_reason"] == "validation_watchdog_timeout"
    assert all(run.status == "failed" for run in runs)
    assert all(
        run.error_message == "TimeoutError: pipeline exceeded 1s watchdog"
        for run in runs
    )


def test_validation_target_word_count_uses_framework_chapter_budget() -> None:
    settings = SimpleNamespace(
        generation=SimpleNamespace(
            words_per_chapter=SimpleNamespace(min=5000, target=6400),
        ),
    )

    assert run_real_abc_book._target_word_count_for_validation(settings, 1) == 6400
    assert run_real_abc_book._target_word_count_for_validation(settings, 3) == 19200


def test_validation_target_word_count_never_under_minimum() -> None:
    settings = SimpleNamespace(
        generation=SimpleNamespace(
            words_per_chapter=SimpleNamespace(min=5000, target=2200),
        ),
    )

    assert run_real_abc_book._target_word_count_for_validation(settings, 1) == 5000
