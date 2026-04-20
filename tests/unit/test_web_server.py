from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from bestseller.web import server as web_server


pytestmark = pytest.mark.unit


def _settings(tmp_path: Path) -> SimpleNamespace:
    return SimpleNamespace(output=SimpleNamespace(base_dir=str(tmp_path)))


def test_collect_project_artifact_entries_lists_files(tmp_path: Path) -> None:
    output_dir = tmp_path / "demo-story"
    output_dir.mkdir(parents=True)
    (output_dir / "project.md").write_text("# Demo", encoding="utf-8")
    (output_dir / "chapter-001.md").write_text("# Chapter", encoding="utf-8")

    entries = web_server.collect_project_artifact_entries(_settings(tmp_path), "demo-story")

    assert [item["name"] for item in entries] == ["chapter-001.md", "project.md"]
    assert entries[0]["word_count"] >= 1
    assert entries[0]["estimated_read_minutes"] == 1
    assert entries[0]["is_previewable"] is True


def test_resolve_project_artifact_path_blocks_path_escape(tmp_path: Path) -> None:
    output_dir = tmp_path / "demo-story"
    output_dir.mkdir(parents=True)
    (output_dir / "project.md").write_text("# Demo", encoding="utf-8")

    path = web_server.resolve_project_artifact_path(_settings(tmp_path), "demo-story", "../project.md")

    assert path.name == "project.md"
    assert path.parent == output_dir.resolve()


def test_render_preview_html_wraps_markdown_content() -> None:
    html = web_server._render_preview_html("demo-story", "project.md", "# 标题\n\n正文")  # noqa: SLF001

    assert "<title>demo-story / project.md</title>" in html
    assert "<h1>标题</h1>" in html
    assert "<p>正文</p>" in html
    assert "正文总字数" in html


def test_build_preview_payload_includes_html_and_stats() -> None:
    payload = web_server.build_preview_payload("demo-story", "project.md", "# 标题\n\n正文 world")  # noqa: SLF001

    assert payload["project_slug"] == "demo-story"
    assert payload["artifact_name"] == "project.md"
    assert payload["word_count"] >= 4
    assert payload["estimated_read_minutes"] == 1
    assert "<h1>标题</h1>" in str(payload["html"])


def test_build_chapter_toc_includes_reading_stats() -> None:
    output_dir = Path("/tmp") / f"demo-story-{uuid4()}"
    output_dir.mkdir(parents=True)
    chapter_path = output_dir / "chapter-001.md"
    chapter_path.write_text("# 第1章：暗潮入局\n\n正文内容一二三四五六七八九十。", encoding="utf-8")

    try:
        entries = web_server._build_chapter_toc(output_dir)  # noqa: SLF001
    finally:
        chapter_path.unlink(missing_ok=True)
        output_dir.rmdir()

    assert entries == [
        {
            "number": 1,
            "title": "暗潮入局",
            "filename": "chapter-001.md",
            "word_count": entries[0]["word_count"],
            "estimated_read_minutes": 1,
        }
    ]
    assert entries[0]["word_count"] >= 10


def test_quickstart_new_creation_buttons_reset_wizard_flow() -> None:
    html = web_server._QUICKSTART_HTML_PATH.read_text(encoding="utf-8")  # noqa: SLF001

    assert "window.startNewCreationFlow = function()" in html
    assert "function resetWizardState()" in html
    assert "onclick=\"switchView('wizard')\"" not in html
    assert html.count('onclick="startNewCreationFlow()"') >= 4


def test_public_writing_preset_catalog_payload_sanitizes_story_specific_overrides() -> None:
    payload = web_server._public_writing_preset_catalog_payload()  # noqa: SLF001

    platform_market = payload["platform_presets"][0]["writing_profile_overrides"].get("market", {})
    genre_market = next(
        item["writing_profile_overrides"].get("market", {})
        for item in payload["genre_presets"]
        if item["key"] == "apocalypse-supply"
    )
    genre_character = next(
        item["writing_profile_overrides"].get("character", {})
        for item in payload["genre_presets"]
        if item["key"] == "apocalypse-supply"
    )

    assert platform_market.get("platform_target") == "番茄小说"
    assert "reader_promise" not in platform_market
    assert "selling_points" not in genre_market
    assert "trope_keywords" not in genre_market
    assert genre_character == {}


def test_quickstart_task_uses_sanitized_genre_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = web_server.WebTaskManager()
    captured: dict[str, object] = {}

    def fake_create_autowrite_task(self: object, payload: dict[str, object]) -> dict[str, object]:
        captured["payload"] = payload
        return {"task_id": "demo-task"}

    monkeypatch.setattr(web_server.WebTaskManager, "create_autowrite_task", fake_create_autowrite_task)

    task = manager.create_quickstart_task({"genre_key": "apocalypse-supply", "chapter_count": 12})

    profile = captured["payload"]["writing_profile"]
    assert task["task_id"] == "demo-task"
    assert profile["market"]["pacing_profile"] == "fast"
    assert "reader_promise" not in profile["market"]
    assert "selling_points" not in profile["market"]
    assert "trope_keywords" not in profile["market"]
    assert profile.get("character", {}) == {}
    assert captured["payload"]["target_words"] == (
        12 * web_server.load_settings().generation.words_per_chapter.target
    )


def test_novel_studio_defaults_do_not_seed_apocalypse_story_template() -> None:
    html = web_server._UI_HTML_PATH.read_text(encoding="utf-8")  # noqa: SLF001

    assert '<input id="genre" list="genre-options" value=""' in html
    assert '<input id="sub-genre" list="sub-genre-options" value=""' in html
    assert 'option value="apocalypse-supply-chain" selected' not in html
    assert "末日零点降临前三天" not in html
    assert 'input id="protagonist-archetype" value="先知型求生者"' not in html
    assert 'input id="golden-finger" value="来自未来的购物入口，可低价购买末日关键物资"' not in html
    assert 'const defaultGenrePreset = genrePresets.find((item) => item.key === "apocalypse-supply");' not in html


def test_resolve_story_bible_progress_returns_current_frontier_and_next_gate() -> None:
    story_bible = SimpleNamespace(
        world_backbone=SimpleNamespace(title="全书世界主干"),
        volume_frontiers=[
            SimpleNamespace(
                volume_number=1,
                title="失准航线",
                frontier_summary="第一卷边界",
                expansion_focus="边境封锁",
                start_chapter_number=1,
                end_chapter_number=20,
                active_locations=["碎潮星港"],
                active_factions=["帝国航道署"],
            ),
            SimpleNamespace(
                volume_number=2,
                title="静默航道",
                frontier_summary="第二卷边界",
                expansion_focus="幕后层级",
                start_chapter_number=21,
                end_chapter_number=40,
                active_locations=["静默航道"],
                active_factions=["监察署"],
            ),
        ],
        expansion_gates=[
            SimpleNamespace(
                id=uuid4(),
                label="第2卷世界扩张闸门",
                condition_summary="拿到第一份铁证",
                unlocks_summary="展开第2卷",
                unlock_volume_number=2,
                unlock_chapter_number=21,
                status="unlocked",
            ),
            SimpleNamespace(
                id=uuid4(),
                label="第3卷世界扩张闸门",
                condition_summary="进入第二层势力",
                unlocks_summary="展开第3卷",
                unlock_volume_number=3,
                unlock_chapter_number=41,
                status="active",
            ),
        ],
    )

    payload = web_server._resolve_story_bible_progress(story_bible, current_chapter_number=24)  # noqa: SLF001

    assert payload["has_backbone"] is True
    assert payload["current_frontier"]["volume_number"] == 2
    assert payload["next_gate"]["unlock_volume_number"] == 3
    assert payload["unlocked_gate_count"] == 1


# ── Zombie auto-resume ───────────────────────────────────────────────────────


def _write_persisted_tasks(tmp_path: Path, tasks: list[dict[str, object]]) -> Path:
    persist_path = tmp_path / ".web_tasks.json"
    import json as _json

    persist_path.write_text(
        _json.dumps(tasks, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return persist_path


def test_load_from_disk_flags_resumable_zombies_as_queued(tmp_path: Path) -> None:
    persist_path = _write_persisted_tasks(
        tmp_path,
        [
            {
                "task_id": "z1",
                "task_type": "autowrite",
                "status": "running",
                "created_at": "2026-01-01T00:00:00+00:00",
                "updated_at": "2026-01-01T00:05:00+00:00",
                "project_slug": "demo",
                "title": "Demo",
                "current_stage": "chapter_pipeline_started",
                "progress_events": [],
                "payload": {"slug": "demo", "title": "Demo"},
            },
        ],
    )

    manager = web_server.WebTaskManager(persist_path=persist_path)

    task = manager.get_task("z1")
    assert task is not None
    assert task["status"] == "queued"
    assert task["current_stage"] == "auto_resume_pending"
    # The zombie ID was captured for the startup auto-resume sweep
    assert manager._pending_auto_resume_ids == ["z1"]  # noqa: SLF001
    # The auto_resume_queued marker event was appended for UI visibility
    stages = [e["stage"] for e in task["progress_events"]]
    assert "auto_resume_queued" in stages


def test_load_from_disk_fails_zombies_without_payload(tmp_path: Path) -> None:
    persist_path = _write_persisted_tasks(
        tmp_path,
        [
            {
                "task_id": "z-nopayload",
                "task_type": "autowrite",
                "status": "running",
                "created_at": "2026-01-01T00:00:00+00:00",
                "updated_at": "2026-01-01T00:05:00+00:00",
                "project_slug": "demo",
                "title": "Demo",
                "current_stage": "running",
                "progress_events": [],
                # No payload → cannot resume
            },
            {
                "task_id": "z-repair",
                "task_type": "repair",
                "status": "running",
                "created_at": "2026-01-01T00:00:00+00:00",
                "updated_at": "2026-01-01T00:05:00+00:00",
                "payload": {"project_slug": "demo"},
            },
        ],
    )

    manager = web_server.WebTaskManager(persist_path=persist_path)

    # No payload → failed
    no_payload = manager.get_task("z-nopayload")
    assert no_payload is not None
    assert no_payload["status"] == "failed"
    # Non-autowrite task with payload → still failed (only autowrite is
    # safely resumable today; repair workers expect operator involvement).
    repair = manager.get_task("z-repair")
    assert repair is not None
    assert repair["status"] == "failed"
    assert manager._pending_auto_resume_ids == []  # noqa: SLF001


def test_auto_resume_zombies_delegates_without_redis(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without a Redis URL (test / single-node env) autowrite zombies are
    still delegated — never spawned as a competing thread.
    """
    persist_path = _write_persisted_tasks(
        tmp_path,
        [
            {
                "task_id": "z-run",
                "task_type": "autowrite",
                "status": "running",
                "created_at": "2026-01-01T00:00:00+00:00",
                "updated_at": "2026-01-01T00:05:00+00:00",
                "project_slug": "demo",
                "title": "Demo",
                "current_stage": "running",
                "progress_events": [],
                "payload": {"slug": "demo", "title": "Demo"},
            },
        ],
    )

    manager = web_server.WebTaskManager(persist_path=persist_path)

    invocations: list[tuple[str, dict[str, object]]] = []

    def fake_worker(self: object, task_id: str, payload: dict[str, object]) -> None:
        invocations.append((task_id, dict(payload)))

    monkeypatch.setattr(web_server.WebTaskManager, "_run_autowrite_worker", fake_worker)

    delegated = manager.auto_resume_zombies()

    assert delegated == ["z-run"]
    # The pending list is cleared after a successful call (idempotent).
    assert manager._pending_auto_resume_ids == []  # noqa: SLF001
    # No thread should be spawned anymore — worker self-heal owns resume.
    import time as _time

    _time.sleep(0.1)
    assert invocations == []

    task = manager.get_task("z-run")
    assert task is not None
    assert task["status"] == "running"
    assert task["current_stage"] == "delegated_to_worker_self_heal"


def test_auto_resume_zombies_idempotent_when_nothing_pending(tmp_path: Path) -> None:
    persist_path = _write_persisted_tasks(tmp_path, [])
    manager = web_server.WebTaskManager(persist_path=persist_path)

    assert manager.auto_resume_zombies() == []
    assert manager.auto_resume_zombies() == []


def test_auto_resume_zombies_delegates_both_heal_owned_and_orphan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """All autowrite zombies get delegated — no thread spawn for either the
    heal-owned slug OR the orphan slug. Worker self-heal is authoritative;
    if it didn't pick up the orphan, the project is already terminal in DB
    (completed / awaiting human / active elsewhere) and spawning a web
    thread would only cause row-lock collisions.
    """
    persist_path = _write_persisted_tasks(
        tmp_path,
        [
            {
                "task_id": "z-heal-owned",
                "task_type": "autowrite",
                "status": "running",
                "created_at": "2026-01-01T00:00:00+00:00",
                "updated_at": "2026-01-01T00:05:00+00:00",
                "project_slug": "novel-a",
                "title": "A",
                "current_stage": "chapter_pipeline_started",
                "progress_events": [],
                "payload": {"slug": "novel-a", "title": "A"},
            },
            {
                "task_id": "z-orphan",
                "task_type": "autowrite",
                "status": "running",
                "created_at": "2026-01-01T00:00:00+00:00",
                "updated_at": "2026-01-01T00:05:00+00:00",
                "project_slug": "novel-b",
                "title": "B",
                "current_stage": "chapter_pipeline_started",
                "progress_events": [],
                "payload": {"slug": "novel-b", "title": "B"},
            },
        ],
    )

    manager = web_server.WebTaskManager(persist_path=persist_path)

    # Stub the scan-wait to return instantly, and the key scan to report
    # only novel-a as heal-owned.
    monkeypatch.setattr(web_server, "_wait_for_self_heal_scan", lambda _url, **_kw: True)
    monkeypatch.setattr(
        web_server,
        "_fetch_heal_owned_slugs",
        lambda _url: {"novel-a"},
    )

    invocations: list[str] = []

    def fake_worker(self: object, task_id: str, payload: dict[str, object]) -> None:
        invocations.append(task_id)

    monkeypatch.setattr(web_server.WebTaskManager, "_run_autowrite_worker", fake_worker)

    delegated = manager.auto_resume_zombies(redis_url="redis://stub")

    # Both tasks delegated — neither spawns a thread.
    assert sorted(delegated) == ["z-heal-owned", "z-orphan"]
    import time as _time

    _time.sleep(0.1)
    assert invocations == []

    for tid, expected_heal_owned in (("z-heal-owned", True), ("z-orphan", False)):
        task = manager.get_task(tid)
        assert task is not None
        assert task["status"] == "running"
        assert task["current_stage"] == "delegated_to_worker_self_heal"
        # Last progress event's payload records whether worker owns the slug.
        last_event = next(
            e for e in reversed(task["progress_events"])
            if e.get("stage") == "delegated_to_worker_self_heal"
        )
        assert last_event["payload"]["heal_owned"] is expected_heal_owned


def test_auto_resume_zombies_delegates_when_redis_unreachable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If Redis can't be scanned (e.g. startup race, network blip), web
    still delegates — NEVER spawns threads. Trust worker's self-heal to
    pick the slug up when Redis comes back; the old fail-open path was
    the direct cause of the LockNotAvailableError collisions we saw on
    every container restart.
    """
    persist_path = _write_persisted_tasks(
        tmp_path,
        [
            {
                "task_id": "z-fallback",
                "task_type": "autowrite",
                "status": "running",
                "created_at": "2026-01-01T00:00:00+00:00",
                "updated_at": "2026-01-01T00:05:00+00:00",
                "project_slug": "novel-c",
                "title": "C",
                "current_stage": "chapter_pipeline_started",
                "progress_events": [],
                "payload": {"slug": "novel-c", "title": "C"},
            },
        ],
    )

    manager = web_server.WebTaskManager(persist_path=persist_path)

    # Simulate a Redis outage: marker wait times out, scan returns empty.
    monkeypatch.setattr(web_server, "_wait_for_self_heal_scan", lambda _url, **_kw: False)
    monkeypatch.setattr(web_server, "_fetch_heal_owned_slugs", lambda _url: set())

    invocations: list[str] = []

    def fake_worker(self: object, task_id: str, payload: dict[str, object]) -> None:
        invocations.append(task_id)

    monkeypatch.setattr(web_server.WebTaskManager, "_run_autowrite_worker", fake_worker)

    delegated = manager.auto_resume_zombies(redis_url="redis://stub")
    assert delegated == ["z-fallback"]
    import time as _time

    _time.sleep(0.1)
    # Critical: NO thread should be spawned even when we can't confirm
    # worker ownership. Delegation wins every time.
    assert invocations == []

    task = manager.get_task("z-fallback")
    assert task is not None
    assert task["status"] == "running"
    assert task["current_stage"] == "delegated_to_worker_self_heal"


def test_fetch_heal_owned_slugs_parses_arq_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    """The helper must scan all three ARQ heal-job key prefixes
    (``arq:job:``, ``arq:in-progress:``, ``arq:retry:``) and collect the
    slug suffix from each. Missing any prefix would let a retrying heal
    race with the web auto-resume after the retry timer expires.
    """

    class _FakeRedis:
        def __init__(self) -> None:
            self._keys = [
                "arq:job:autowrite:heal:novel-a",
                "arq:in-progress:autowrite:heal:novel-b",
                "arq:retry:autowrite:heal:novel-c",
                "arq:queue",  # noise
                "arq:result:autowrite:heal:novel-d",  # different prefix, ignored
            ]

        def scan_iter(self, match: str, count: int = 200):  # noqa: ARG002
            prefix = match.rstrip("*")
            return (k for k in self._keys if k.startswith(prefix))

    fake_client = _FakeRedis()

    class _FakeRedisModule:
        @staticmethod
        def from_url(_url: str, **_kwargs: object) -> _FakeRedis:
            return fake_client

    import sys as _sys

    monkeypatch.setitem(_sys.modules, "redis", _FakeRedisModule)

    slugs = web_server._fetch_heal_owned_slugs("redis://stub")  # noqa: SLF001
    assert slugs == {"novel-a", "novel-b", "novel-c"}


def test_fetch_heal_owned_slugs_returns_empty_on_redis_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Connection errors must degrade to an empty set so auto-resume
    continues rather than silently hanging all recovered tasks.
    """

    class _FakeRedisModule:
        @staticmethod
        def from_url(_url: str, **_kwargs: object) -> object:
            raise RuntimeError("connection refused")

    import sys as _sys

    monkeypatch.setitem(_sys.modules, "redis", _FakeRedisModule)

    slugs = web_server._fetch_heal_owned_slugs("redis://stub")  # noqa: SLF001
    assert slugs == set()
