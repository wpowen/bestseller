from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import json
from pathlib import Path
import re
from types import SimpleNamespace
from uuid import uuid4

import pytest

from bestseller.services.pipelines import ProjectRepairPauseError
from bestseller.web import server as web_server

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"genre_key": "apocalypse-supply"}, True),  # 热门开局模板 (legacy)
        # 自由组合 free picker: empty genre_key + structured selection
        (
            {
                "genre_key": "",
                "selection": {
                    "channel": "male",
                    "genre": "apocalypse",
                    "sub_genre": "disaster-hoarding",
                    "tags": ["囤货", "升级流"],
                },
            },
            True,
        ),
        ({"selection": {"genre": "xuanhuan"}}, True),  # selection only
        ({"genre_key": ""}, False),  # neither → reject
        ({"genre_key": "", "selection": {}}, False),  # empty selection
        ({"selection": None}, False),
        ({}, False),
    ],
)
def test_quickstart_payload_has_genre_accepts_genre_key_or_selection(
    payload: dict, expected: bool
) -> None:
    # Regression: the /api/tasks/quickstart gate must accept the new
    # 频道·题材·子题材·标签 selection, not only the legacy genre_key — otherwise
    # the free picker fails with "Field 'genre_key' is required.".
    assert web_server._quickstart_payload_has_genre(payload) is expected


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

    with pytest.raises(ValueError):
        web_server.resolve_project_artifact_path(_settings(tmp_path), "demo-story", "../project.md")


def test_resolve_project_artifact_path_allows_safe_nested_exports(tmp_path: Path) -> None:
    export_dir = tmp_path / "demo-story" / "exports"
    export_dir.mkdir(parents=True)
    export_file = export_dir / "fanqie-short.md"
    export_file.write_text("# Demo\n\n正文", encoding="utf-8")

    path = web_server.resolve_project_artifact_path(
        _settings(tmp_path), "demo-story", "exports/fanqie-short.md"
    )

    assert path == export_file.resolve()


def test_render_preview_html_wraps_markdown_content() -> None:
    html = web_server._render_preview_html("demo-story", "project.md", "# 标题\n\n正文")

    assert "<title>demo-story / project.md</title>" in html
    assert "<h1>标题</h1>" in html
    assert "<p>正文</p>" in html
    assert "正文总字数" in html


def test_build_preview_payload_includes_html_and_stats() -> None:
    payload = web_server.build_preview_payload("demo-story", "project.md", "# 标题\n\n正文 world")

    assert payload["project_slug"] == "demo-story"
    assert payload["artifact_name"] == "project.md"
    assert payload["word_count"] >= 4
    assert payload["estimated_read_minutes"] == 1
    assert "<h1>标题</h1>" in str(payload["html"])


def test_methodology_course_document_has_24_complete_lessons() -> None:
    doc_path = (
        Path(__file__).resolve().parents[2]
        / "docs"
        / "novel-writing-methodology-course.md"
    )
    content = doc_path.read_text(encoding="utf-8")

    lesson_matches = list(
        re.finditer(r"^## 第\s*\d{2}\s*章[：:].+$", content, re.MULTILINE)
    )

    assert len(lesson_matches) == 24
    required_parts = [
        "### 问题定义",
        "### 核心概念",
        "### 原理模型",
        "### 方法论展开",
        "### 操作流程",
        "### 文本示例与拆解",
        "### 经典参照",
        "### 常见误区",
        "### 实作模板",
        "### 诊断清单",
        "### 应用边界与进阶用法",
        "### 与其他章节的联动",
        "### 练习产物",
        "### 案例改写步骤",
        "### 评估标准",
        "### 教材提示",
        "### 编辑验收问题",
        "### 来源与框架映射",
    ]
    for idx, match in enumerate(lesson_matches):
        end = lesson_matches[idx + 1].start() if idx + 1 < len(lesson_matches) else len(
            content
        )
        lesson_body = content[match.start() : end]
        assert len(lesson_body) >= 3000, f"{match.group(0)} too short"
        for required in required_parts:
            assert required in lesson_body, f"{match.group(0)} missing {required}"
        assert "讲师" not in lesson_body
        assert "口播" not in lesson_body


def test_build_methodology_course_payload_extracts_lessons() -> None:
    payload = web_server._build_methodology_course_payload()

    assert payload["status"] == "ready"
    assert payload["title"] == "写小说的方法论：长篇小说创作体系教材"
    assert payload["lesson_count"] == 24
    assert payload["lessons"][0]["label"] == "第 01 章"
    assert payload["lessons"][0]["path"] == "/methodology-course/01"
    assert payload["lessons"][-1]["anchor"] == "lesson-24"
    assert "<h1>写小说的方法论" in str(payload["overview_html"])


def test_build_methodology_lesson_payload_returns_single_detail_page() -> None:
    payload = web_server._build_methodology_lesson_payload(1)

    assert payload is not None
    lesson = payload["lesson"]
    assert lesson["number"] == 1
    assert lesson["path"] == "/methodology-course/01"
    assert "### 问题定义" in str(lesson["markdown"])
    assert "### 原理模型" in str(lesson["markdown"])
    assert "### 来源与框架映射" in str(lesson["markdown"])
    assert payload["previous"] is None
    assert payload["next"]["path"] == "/methodology-course/02"
    assert web_server._build_methodology_lesson_payload(99) is None


def test_build_methodology_course_payload_handles_missing_doc(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    missing_path = tmp_path / "missing-course.md"
    monkeypatch.setattr(web_server, "_METHODOLOGY_COURSE_MD_PATH", missing_path)

    payload = web_server._build_methodology_course_payload()

    assert payload["status"] == "missing"
    assert payload["lesson_count"] == 0
    assert "教材文档尚未生成" in str(payload["markdown"])
    assert "missing-course.md" in str(payload["source_path"])


def test_read_methodology_course_html() -> None:
    html = web_server._read_methodology_course_html()

    assert "写小说的方法论" in html
    assert "/api/methodology-course" in html
    assert "/api/methodology-course/lessons/" in html
    assert r"match(/\/methodology-course\/(\d{1,2})\/?$/)" in html
    assert r"match(/\\/methodology-course\\/" not in html
    assert "lessonGrid" in html
    assert "detailView" in html


def test_default_preview_prefers_db_current_chapter_over_readme() -> None:
    readme = {
        "name": "README.md",
        "suffix": ".md",
        "modified_at": "2026-05-01T00:00:00+00:00",
    }
    stale_chapter = {
        "name": "chapter-001.md",
        "suffix": ".md",
        "modified_at": "2026-05-02T00:00:00+00:00",
    }
    current_chapter = {
        "name": "chapter-001.md",
        "suffix": ".md",
        "modified_at": "2026-05-25T14:30:48+00:00",
        "source": "db_current_draft",
    }

    selected = web_server._select_default_preview_entry(
        [readme, stale_chapter],
        latest_current_chapter_entry=current_chapter,
    )

    assert selected == current_chapter


def test_default_preview_falls_back_to_latest_chapter_not_readme() -> None:
    entries = [
        {
            "name": "README.md",
            "suffix": ".md",
            "modified_at": "2026-05-25T00:00:00+00:00",
        },
        {
            "name": "chapter-001.md",
            "suffix": ".md",
            "modified_at": "2026-05-24T00:00:00+00:00",
        },
        {
            "name": "chapter-002.md",
            "suffix": ".md",
            "modified_at": "2026-05-24T01:00:00+00:00",
        },
    ]

    selected = web_server._select_default_preview_entry(entries)

    assert selected is not None
    assert selected["name"] == "chapter-002.md"


def test_clear_repair_resume_focus_pause_releases_paused_project() -> None:
    project = SimpleNamespace(
        status="paused",
        metadata_json={
            "production_paused": True,
            "production_pause_reason": "focus_latest_book_validation",
            "focus_pause": {
                "reason": "focus_manual_resume_autowrite",
                "set_by": "codex",
            },
            "generation_resume_blocked_until_repair_audit": True,
        },
    )

    changed = web_server._clear_repair_resume_focus_pause_on_project(project)

    assert changed is True
    assert project.status == "revising"
    assert "focus_pause" not in project.metadata_json
    assert "production_pause_reason" not in project.metadata_json
    assert project.metadata_json["generation_resume_blocked_until_repair_audit"] is True
    assert (
        project.metadata_json["last_repair_resume_focus_pause_reason"]
        == "focus_manual_resume_autowrite"
    )


def test_clear_repair_resume_focus_pause_preserves_structural_pause() -> None:
    project = SimpleNamespace(
        status="paused",
        metadata_json={
            "production_paused": True,
            "production_pause_reason": "structural_repair_before_continuation",
            "generation_resume_blocked_until_repair_audit": True,
        },
    )

    changed = web_server._clear_repair_resume_focus_pause_on_project(project)

    assert changed is False
    assert project.status == "paused"
    assert project.metadata_json["production_pause_reason"] == (
        "structural_repair_before_continuation"
    )


def test_attach_repair_heal_owner_preserves_running_db_workflow() -> None:
    summary = {
        "status": "running",
        "current_stage": "repair_chapter_87",
    }

    web_server._attach_repair_heal_owner_to_db_summary(
        summary,
        "repair:heal:novel-a",
        None,
    )

    assert summary["worker_job_id"] == "repair:heal:novel-a"
    assert summary["status"] == "running"
    assert summary["current_stage"] == "repair_chapter_87"


def test_attach_repair_heal_owner_marks_nonrunning_summary_queued() -> None:
    summary = {
        "status": "failed",
        "current_stage": "old_failure",
    }

    web_server._attach_repair_heal_owner_to_db_summary(
        summary,
        "repair:heal:novel-a",
        None,
    )

    assert summary["worker_job_id"] == "repair:heal:novel-a"
    assert summary["status"] == "queued"
    assert summary["current_stage"] == "delegated_to_worker_self_heal"


def test_upsert_artifact_entry_replaces_stale_file_metadata() -> None:
    stale = {
        "name": "chapter-001.md",
        "suffix": ".md",
        "word_count": 100,
        "source": "disk",
    }
    fresh = {
        "name": "chapter-001.md",
        "suffix": ".md",
        "word_count": 2400,
        "source": "db_current_draft",
    }

    merged = web_server._upsert_artifact_entry([stale], fresh)

    assert len(merged) == 1
    assert merged[0]["word_count"] == 2400
    assert merged[0]["source"] == "db_current_draft"


def test_build_chapter_toc_includes_reading_stats() -> None:
    output_dir = Path("/tmp") / f"demo-story-{uuid4()}"
    output_dir.mkdir(parents=True)
    chapter_path = output_dir / "chapter-001.md"
    chapter_path.write_text("# 第1章：暗潮入局\n\n正文内容一二三四五六七八九十。", encoding="utf-8")

    try:
        entries = web_server._build_chapter_toc(output_dir)
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


def test_try_load_chapter_draft_from_db_returns_markdown_string(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = "# 第75章：幕后黑手\n\n正文"

    def fake_run(coro: object) -> str:
        close = getattr(coro, "close", None)
        if callable(close):
            close()
        return expected

    monkeypatch.setattr(web_server.asyncio, "run", fake_run)

    assert (
        web_server._try_load_chapter_draft_from_db(
            _settings(Path("/tmp")),
            "demo-story",
            "chapter-075.md",
        )
        == expected
    )


def test_apply_project_titles_to_tasks_uses_database_title(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FakeSession:
        async def execute(self, _stmt: object) -> list[tuple[str, str]]:
            return [("book-a", "青囊不语问阴阳")]

    class _SessionScope:
        async def __aenter__(self) -> _FakeSession:
            return _FakeSession()

        async def __aexit__(self, *_args: object) -> None:
            return None

    monkeypatch.setattr(web_server, "session_scope", lambda _settings: _SessionScope())

    tasks = [
        {
            "task_id": "task-a",
            "project_slug": "book-a",
            "title": "南茅北马驱魔断案流·构思中 05-21 14:53",
            "payload": {
                "slug": "book-a",
                "title": "南茅北马驱魔断案流·构思中 05-21 14:53",
            },
        }
    ]

    titled = asyncio.run(web_server._apply_project_titles_to_tasks(SimpleNamespace(), tasks))

    assert titled[0]["title"] == "青囊不语问阴阳"
    assert titled[0]["project_title"] == "青囊不语问阴阳"
    assert titled[0]["payload"]["title"] == "青囊不语问阴阳"


def test_fanqie_short_toc_entry_uses_single_story_export(tmp_path: Path) -> None:
    output_dir = tmp_path / "fanqie-short"
    export_dir = output_dir / "exports"
    export_dir.mkdir(parents=True)
    (export_dir / "fanqie-short.md").write_text(
        "# 情绪爆改器\n\n第一段正文。\n\n---\n"
        "<!-- UNLOCK_LINE: 30% · 番茄短故事免费段截止 -->\n"
        "---\n\n第二段正文。",
        encoding="utf-8",
    )

    entry = web_server._fanqie_short_toc_entry(output_dir)

    assert entry is not None
    assert entry["number"] == 1
    assert entry["title"] == "全文"
    assert entry["filename"] == "exports/fanqie-short.md"
    assert entry["single_piece"] is True
    assert entry["content_mode"] == "fanqie_short_story"
    assert entry["word_count"] >= 6


def test_fanqie_short_reader_markers_are_hidden() -> None:
    content = (
        "# 标题\n\n> 类型：都市异能 · 番茄短故事 · 单篇完结\n\n开篇。\n\n---\n"
        "<!-- UNLOCK_LINE: 30% · 番茄短故事免费段截止 -->\n"
        "---\n\n后文。"
    )

    cleaned = web_server._strip_fanqie_short_reader_markers(content)

    assert "标题" not in cleaned
    assert "类型" not in cleaned
    assert "番茄短故事" not in cleaned
    assert "单篇完结" not in cleaned
    assert "UNLOCK_LINE" not in cleaned
    assert "---" not in cleaned
    assert "开篇" in cleaned
    assert "后文" in cleaned


def test_fanqie_short_export_task_stats_uses_current_full_export(tmp_path: Path) -> None:
    output_dir = tmp_path / "urban-power-reversal-1779201033" / "exports"
    output_dir.mkdir(parents=True)
    (output_dir / "fanqie-short.md").write_text(
        "# 情绪爆改器\n\n> 类型：都市异能 · 番茄短故事 · 单篇完结\n\n"
        "陆渊点下名字，群里造谣的人当场改口。\n\n第二段正文继续推进。",
        encoding="utf-8",
    )
    project = SimpleNamespace(
        slug="urban-power-reversal-1779201033",
        title="情绪爆改器",
        project_type="fanqie_short",
        target_word_count=15000,
        metadata_json={
            "content_mode": "fanqie_short_story",
            "length_key": "fanqie-short-15k",
            "platform_key": "fanqie_short",
        },
    )

    stats = web_server._build_fanqie_short_export_task_stats(
        _settings(tmp_path),
        project,
        include_chapters=True,
    )

    assert stats is not None
    assert stats["source"] == "fanqie_short_export"
    assert stats["project_title"] == "情绪爆改器"
    assert stats["unit_kind"] == "single_story"
    assert stats["unit_label"] == "全文"
    assert stats["target_segments"] == 1
    assert stats["completed_segments"] == 1
    assert stats["word_count_total"] >= 20
    assert stats["current_export_filename"] == "exports/fanqie-short.md"
    assert stats["chapters"] == [
        {
            "number": 1,
            "title": "全文",
            "unit_kind": "single_story",
            "unit_label": "全文",
            "filename": "exports/fanqie-short.md",
            "word_count": stats["word_count_total"],
            "estimated_read_minutes": stats["estimated_read_minutes"],
            "target_word_count": 15000,
            "status": "complete",
            "single_piece": True,
        }
    ]


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _stuck_autowrite_task(slug: str, *, stage: str = "machine_repair_required") -> web_server.WebTaskState:
    return web_server.WebTaskState(
        task_id=str(uuid4()),
        task_type="autowrite",
        status="incomplete",
        created_at=_now_iso(),
        updated_at=_now_iso(),
        project_slug=slug,
        title=slug,
        current_stage=stage,
        error="Task is waiting for machine repair or attention-gate repair.",
    )


def test_create_autowrite_reuses_stuck_card_instead_of_duplicating(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A new autowrite for a blocked slug must reuse its stuck card in place.

    Regression guard: a structurally blocked book used to mint a fresh uuid card
    on every attempt, piling up identical ``machine_repair_required`` zombies.
    """
    started: list[str] = []

    class _NoopThread:
        def __init__(self, *args: object, **kwargs: object) -> None:
            task_id = kwargs.get("args", (None,))[0]
            started.append(str(task_id))

        def start(self) -> None:  # pragma: no cover - trivial
            pass

    monkeypatch.setattr(web_server.threading, "Thread", _NoopThread)

    manager = web_server.WebTaskManager(persist_path=tmp_path / "tasks.json")
    stuck = _stuck_autowrite_task("exorcist-detective-1778051012")
    manager._tasks[stuck.task_id] = stuck

    result = manager.create_autowrite_task(
        {"slug": "exorcist-detective-1778051012", "title": "驱魔侦探"}
    )

    # Same card reused — no duplicate, id preserved, status reset to queued.
    assert result["task_id"] == stuck.task_id
    assert len(manager._tasks) == 1
    assert manager._tasks[stuck.task_id].status == "queued"
    assert started == [stuck.task_id]


def test_create_autowrite_returns_active_card_without_second_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A queued/running card for the slug short-circuits — no competing thread."""
    started: list[str] = []

    class _NoopThread:
        def __init__(self, *args: object, **kwargs: object) -> None:
            task_id = kwargs.get("args", (None,))[0]
            started.append(str(task_id))

        def start(self) -> None:  # pragma: no cover - trivial
            pass

    monkeypatch.setattr(web_server.threading, "Thread", _NoopThread)

    manager = web_server.WebTaskManager(persist_path=tmp_path / "tasks.json")
    active = _stuck_autowrite_task("busy-book")
    active.status = "running"
    active.current_stage = "drafting"
    manager._tasks[active.task_id] = active

    result = manager.create_autowrite_task({"slug": "busy-book", "title": "Busy"})

    assert result["task_id"] == active.task_id
    assert len(manager._tasks) == 1
    # No thread spawned — the live run keeps ownership.
    assert started == []


def test_create_autowrite_mints_new_card_for_fresh_slug(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A slug with no prior card still gets a brand-new task."""
    monkeypatch.setattr(
        web_server.threading,
        "Thread",
        lambda *a, **k: type("T", (), {"start": lambda self: None})(),
    )

    manager = web_server.WebTaskManager(persist_path=tmp_path / "tasks.json")
    result = manager.create_autowrite_task({"slug": "brand-new", "title": "New"})

    assert result["status"] == "queued"
    assert len(manager._tasks) == 1
    assert result["task_id"] in manager._tasks


def test_dashboard_task_filter_keeps_only_executing_tasks() -> None:
    tasks = [
        {"task_id": "queued", "status": "queued"},
        {"task_id": "running", "status": "running"},
        {"task_id": "completed", "status": "completed"},
        {"task_id": "failed", "status": "failed"},
        {"task_id": "incomplete", "status": "incomplete"},
    ]

    visible = web_server._filter_dashboard_visible_tasks(tasks)

    assert [task["task_id"] for task in visible] == ["queued", "running"]


def test_library_book_state_marks_finished_content_as_closed() -> None:
    state = web_server._library_book_state(
        status="revising",
        completed_units=1,
        target_units=1,
        has_content=True,
    )

    assert state == "closed_complete"
    assert web_server._library_book_state_label(state) == "已闭环"


def test_library_book_state_manual_completed_overrides_repair_attention() -> None:
    state = web_server._library_book_state(
        status="completed",
        completed_units=0,
        target_units=10,
        has_content=True,
        repair_status={"is_repairing": True},
        has_active_workflow=True,
    )

    assert state == "closed_complete"


def test_library_book_state_archive_overrides_shelf_state() -> None:
    state = web_server._library_book_state(
        status="completed",
        completed_units=1,
        target_units=1,
        has_content=True,
        archived=True,
    )

    assert state == "archived"
    assert web_server._library_book_state_label(state) == "已归档"
    assert web_server._project_library_archived({"library_archived": True}) is True


def test_project_row_to_dashboard_task_preserves_closed_book_details() -> None:
    task = web_server._project_row_to_dashboard_task(
        {
            "slug": "urban-power-reversal-1779201033",
            "title": "全员群把我挂成贪污犯后，我让老板当众自爆",
            "book_state": "closed_complete",
            "book_state_label": "已闭环",
            "project_type": "fanqie_short",
            "content_mode": "fanqie_short_story",
            "unit_kind": "single_story",
            "unit_label": "全文",
            "completed_chapters": 1,
            "target_chapters": 1,
            "target_word_count": 8000,
            "words_on_disk": 6027,
            "last_updated": "2026-05-20T00:00:00+00:00",
        }
    )

    assert task["task_id"] == "project:urban-power-reversal-1779201033"
    assert task["status"] == "completed"
    assert task["title"] == "全员群把我挂成贪污犯后，我让老板当众自爆"
    assert task["synthetic_project"] is True
    assert task["chapter_word_stats"]["unit_label"] == "全文"
    assert task["chapter_word_stats"]["completed_chapters"] == 1
    assert task["chapter_word_stats"]["target_chapters"] == 1


def test_payload_from_project_model_preserves_fanqie_short_contract() -> None:
    project = SimpleNamespace(
        slug="urban-power-reversal-1779201033",
        title="全员群把我挂成贪污犯后，我让老板当众自爆",
        genre="都市异能",
        sub_genre="职场打脸",
        target_word_count=8000,
        target_chapters=4,
        project_type="fanqie_short",
        metadata_json={
            "content_mode": "fanqie_short_story",
            "platform_key": "tomato_short",
            "length_key": "fanqie-short-8k",
            "pov": "first_person",
            "premise": "继续修订这个番茄短故事。",
            "writing_profile": {"market": {"platform_target": "番茄小说·短故事"}},
        },
    )

    payload = web_server._payload_from_project_model(project)

    assert payload["project_type"] == "fanqie_short"
    assert payload["creation_mode"] == "fanqie_short"
    assert payload["metadata"]["content_mode"] == "fanqie_short_story"
    assert payload["length_key"] == "fanqie-short-8k"
    assert payload["pov"] == "first_person"
    assert payload["_run_conception"] is False


def test_attach_task_stats_maps_fanqie_export_to_current_project_title(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def fake_stats(
        settings: object,
        project_slugs: list[str],
        *,
        summary_only: bool = False,
    ) -> dict[str, dict[str, object]]:
        return {
            "urban-power-reversal-1779201033": {
                "source": "fanqie_short_export",
                "project_title": "情绪爆改器",
                "project_type": "fanqie_short",
                "content_mode": "fanqie_short_story",
                "length_key": "fanqie-short-15k",
                "unit_kind": "single_story",
                "unit_label": "全文",
                "target_segments": 1,
                "completed_segments": 1,
                "word_count_total": 5460,
                "current_export_filename": "exports/fanqie-short.md",
            }
        }

    monkeypatch.setattr(web_server, "_load_task_chapter_word_stats", fake_stats)
    tasks = [
        {
            "project_slug": "urban-power-reversal-1779201033",
            "title": "开局现实逆袭，我用系统流证道",
            "task_type": "autowrite",
        }
    ]

    enriched = web_server._attach_task_chapter_word_stats(_settings(tmp_path), tasks)

    assert enriched[0]["title"] == "情绪爆改器"
    assert enriched[0]["project_title"] == "情绪爆改器"
    assert enriched[0]["project_type"] == "fanqie_short"
    assert enriched[0]["content_mode"] == "fanqie_short_story"
    assert enriched[0]["current_export_filename"] == "exports/fanqie-short.md"
    assert enriched[0]["chapter_word_stats"]["unit_kind"] == "single_story"


def test_fanqie_short_listing_profile_maps_to_current_export(tmp_path: Path) -> None:
    output_dir = tmp_path / "urban-power-reversal-1779201033" / "exports"
    output_dir.mkdir(parents=True)
    (output_dir / "fanqie-short.md").write_text(
        "# 情绪爆改器\n\n"
        "全员群刚把陆渊挂成贪污犯，手机就黑了：【目标曹敏，恐惧可放大十秒。】\n\n"
        "周庭轩把《离职交接确认书》推来，逼他承认四十七万是他挪的。\n\n"
        "陆渊点下名字，曹敏当场撤回公告。代价是丢失一段温暖记忆。\n\n"
        "周庭轩冲上来抢手机，曹敏退到摄像头下。\n\n"
        "裴铮想用父亲手术押金逼他闭嘴，陆渊把发布会变成公开审判。\n\n"
        "裴铮终于承认，真正怕的不是陆渊，而是公开记录。",
        encoding="utf-8",
    )
    project = SimpleNamespace(
        slug="urban-power-reversal-1779201033",
        title="旧标题",
        genre="都市异能",
        sub_genre="身份反转",
        audience="番茄短故事读者",
        status="revising",
        language="zh-CN",
        metadata_json={
            "author_display_name": "测试作者",
            "tags": ["旧系统流"],
            "synopsis": "陆寻被催债围堵，女警苏棠追查，赤蛇帮追杀。",
            "book_spec": {"protagonist": {"name": "陆渊"}},
        },
    )

    profile = web_server._build_fanqie_short_current_listing_profile(
        _settings(tmp_path),
        project,
    )

    assert profile is not None
    serialized = json.dumps(profile, ensure_ascii=False)
    assert profile["source"] == "fanqie_short_export"
    assert profile["primary_title"] == "情绪爆改器"
    assert profile["author_display_name"] == "测试作者"
    assert profile["length_type"] == "番茄短故事 · 单篇完结"
    assert profile["copy_pack"]["author"] == "测试作者"
    assert len(profile["title_candidates"]) >= 40
    assert "陆渊" in profile["character_names"]
    assert "周庭轩" in profile["character_names"]
    assert "裴铮" in profile["character_names"]
    assert not any(str(name).startswith("解") for name in profile["character_names"])
    assert "陆寻" not in serialized
    assert "苏棠" not in serialized
    assert "赤蛇帮" not in serialized


def test_project_identity_payload_overrides_fanqie_short_stale_chapter_fields(
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "urban-power-reversal-1779201033" / "exports"
    output_dir.mkdir(parents=True)
    (output_dir / "fanqie-short.md").write_text(
        "# 全员群把我挂成贪污犯后，我让老板当众自爆\n\n"
        "全员群刚把陆渊挂成贪污犯，手机就黑了：【目标曹敏，恐惧可放大十秒。】\n\n"
        "陆渊点下名字，曹敏当场撤回公告。代价是丢失一段温暖记忆。",
        encoding="utf-8",
    )
    project = SimpleNamespace(
        slug="urban-power-reversal-1779201033",
        title="情绪爆改器",
        genre="都市异能",
        sub_genre="身份反转",
        audience="番茄短故事读者",
        status="revising",
        target_word_count=15000,
        target_chapters=6,
        current_volume_number=1,
        current_chapter_number=0,
        project_type="fanqie_short",
        language="zh-CN",
        metadata_json={
            "book_state": "closed_complete",
            "content_mode": "fanqie_short_story",
            "tags": ["旧系统流"],
            "synopsis": "陆寻被催债围堵，女警苏棠追查，赤蛇帮追杀。",
            "premise": "旧长篇简介",
        },
    )
    profile = web_server._build_fanqie_short_current_listing_profile(
        _settings(tmp_path),
        project,
    )
    stats = web_server._build_fanqie_short_export_task_stats(
        _settings(tmp_path),
        project,
        include_chapters=True,
    )

    payload = web_server._build_project_identity_payload(
        project,
        profile or {},
        fanqie_stats=stats,
    )

    assert payload["title"] == "全员群把我挂成贪污犯后，我让老板当众自爆"
    assert payload["status"] == "completed"
    assert payload["target_chapters"] == 1
    assert payload["unit_label"] == "全文"
    assert payload["book_state"] == "closed_complete"
    assert "陆寻" not in str(payload["synopsis"])
    assert "旧系统流" not in payload["tags"]


def test_reader_html_supports_fanqie_single_story_mode() -> None:
    html = web_server._READER_HTML_PATH.read_text(encoding="utf-8")

    assert 'content_mode === "fanqie_short_story"' in html
    assert "正在加载全文" in html
    assert "单篇全文" in html


def test_quickstart_new_creation_buttons_reset_wizard_flow() -> None:
    html = web_server._QUICKSTART_HTML_PATH.read_text(encoding="utf-8")

    assert "window.startNewCreationFlow = function()" in html
    assert "function resetWizardState()" in html
    assert "onclick=\"switchView('wizard')\"" not in html
    assert html.count('onclick="startNewCreationFlow()"') >= 4


def test_quickstart_wizard_view_stays_hidden_until_new_creation() -> None:
    html = web_server._QUICKSTART_HTML_PATH.read_text(encoding="utf-8")

    wizard_base_block = html.split("  #viewWizard {", 1)[1].split("}", 1)[0]

    assert "display:" not in wizard_base_block
    assert "#viewWizard.view.active { display: flex; }" in html


def test_quickstart_fanqie_length_default_matches_selected_button() -> None:
    html = web_server._QUICKSTART_HTML_PATH.read_text(encoding="utf-8")

    assert "const DEFAULT_FANQIE_LENGTH_KEY = 'fanqie-short-15k';" in html
    assert "let fanqieLengthKey = DEFAULT_FANQIE_LENGTH_KEY;" in html
    assert 'class="length-btn fanqie-len-btn selected" data-length-key="fanqie-short-15k"' in html
    assert (
        'class="length-btn fanqie-len-btn selected" data-length-key="fanqie-short-8k"' not in html
    )


def test_quickstart_fanqie_task_progress_uses_single_story_language() -> None:
    html = web_server._QUICKSTART_HTML_PATH.read_text(encoding="utf-8")

    assert "function isFanqieShortTask(task)" in html
    assert "const isFanqieShort = isFanqieShortTask(data);" in html
    assert "const isFanqieShortTask = isFanqieShortTask(data);" not in html
    assert "短故事单篇已完成" in html
    assert "全文进度" in html
    assert "全文统计" in html
    assert "unitKind: 'single_story'" in html
    assert "作者" in html


def test_quickstart_listing_profile_content_is_copyable() -> None:
    html = web_server._QUICKSTART_HTML_PATH.read_text(encoding="utf-8")

    assert ".listing-panel {" in html
    assert "user-select: text;" in html
    assert "function copyableSpan(text, fallback = '')" in html
    assert "function buildTitleCandidatesCopyText(candidates)" in html
    assert "复制候选列表" in html
    assert "copyButton('复制', registerListingCopyText(item.title))" in html
    assert "await navigator.clipboard.writeText(text);" in html
    assert "area.focus();" in html


def test_quickstart_progress_panels_skip_unchanged_dom_rebuilds() -> None:
    html = web_server._QUICKSTART_HTML_PATH.read_text(encoding="utf-8")

    assert "let renderedChapterListSignature = null;" in html
    assert (
        "function renderChapterGridIfChanged("
        "taskId, signature, headerHtml, readLabel, readHref, html)"
    ) in html
    assert (
        "if (renderedChapterListTaskId === taskId "
        "&& renderedChapterListSignature === signature) return;"
    ) in html
    assert "renderChapterGridIfChanged(" in html
    assert "先重置章节区" not in html

    assert "let listingRenderedSignature = null;" in html
    assert "let listingLoadInFlight = null;" in html
    assert "function listingPayloadSignature(listing, project)" in html
    assert "listingLoadInFlight && listingLoadInFlight.slug === slug" in html
    assert "if (listingRenderedSignature !== signature)" in html


def test_quickstart_incomplete_tasks_are_not_labeled_stopped() -> None:
    html = web_server._QUICKSTART_HTML_PATH.read_text(encoding="utf-8")

    label_pos = html.index("label: '未完成'")
    start = html.rfind("if (status === 'incomplete')", 0, label_pos)
    end = html.index("if (status === 'completed')", start)
    incomplete_branch = html[start:end]

    assert "label: '未完成'" in incomplete_branch
    assert "自动恢复未接管" in incomplete_branch
    assert "已停止" not in incomplete_branch


def test_quickstart_exposes_runtime_llm_profile_switcher() -> None:
    html = web_server._QUICKSTART_HTML_PATH.read_text(encoding="utf-8")

    assert 'id="llmProfileSelect"' in html
    assert "function switchLlmProfile(profileKey)" in html
    assert "fetch('/api/llm-profile'" in html
    assert "updateLlmProfileUi(d.llm_profile)" in html


def test_quickstart_exposes_batch_concept_lab_picker() -> None:
    html = web_server._QUICKSTART_HTML_PATH.read_text(encoding="utf-8")

    assert 'id="conceptLab"' in html
    assert "脑洞组合 · 批量候选" in html
    assert "const CONCEPT_LAB_BATCH_COUNT = 12;" in html
    assert "count: CONCEPT_LAB_BATCH_COUNT" in html
    assert "脑洞候选 ${bundles.length} 组" in html
    assert "concept_lab_bundle_id" in html
    assert "concept_lab_bundle" in html


def test_quickstart_exposes_optional_audience_orientation_switch() -> None:
    html = web_server._QUICKSTART_HTML_PATH.read_text(encoding="utf-8")

    assert 'id="audienceOrientationRow"' in html
    assert 'data-audience="male"' in html
    assert 'data-audience="female"' in html
    assert "智能判定" in html
    # Default is empty ('') so the heat-search agent decides; the explicit pick
    # is sent as audience_orientation in the quickstart payload.
    assert "audience_orientation: selectedAudience || undefined" in html


def test_quickstart_creative_hook_concept_are_optional_not_auto_selected() -> None:
    """After picking a genre, 题材脑洞发散 / 反常识爽点 / 脑洞组合 must default to
    UNSELECTED (the framework grows them itself). Guards against the regression
    where the UI auto-selected a default and leaked it into the submission."""
    html = web_server._QUICKSTART_HTML_PATH.read_text(encoding="utf-8")

    # No implicit default creative direction.
    assert "pack.default_key || pack.directions[0].key" not in html
    # No implicit default hook (index 0) or default concept bundle.
    assert "selectedHookIndexByGenre[g.key] || 0" not in html
    assert "conceptLabCatalog.default_bundle_id || bundles[0]?.bundle_id" not in html
    # Optional hints are shown so the user knows skipping is intentional.
    assert "可选 · 不选则由AI按题材+市场热度自动决定" in html
    assert "AI自动决定（未选）" in html


def test_public_writing_preset_catalog_payload_sanitizes_story_specific_overrides() -> None:
    payload = web_server._public_writing_preset_catalog_payload()

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


def test_public_writing_preset_catalog_exposes_genre_dimensions() -> None:
    payload = web_server._public_writing_preset_catalog_payload()

    dimensions = payload["genre_dimensions"]
    presets = payload["genre_presets"]
    romantasy = next(item for item in presets if item["key"] == "cn-romantasy-court")
    reader_reward_options = [item["value"] for item in dimensions["reader_rewards"]["options"]]

    assert "reader_rewards" in dimensions
    assert "关系回报" in reader_reward_options
    assert "幻想言情" == romantasy["genre"]
    assert "言情/女性向" in romantasy["heat_domains"]
    assert "关系回报" in romantasy["reader_rewards"]
    assert "情绪关系" in romantasy["narrative_drives"]


def test_public_writing_preset_catalog_exposes_genre_creativity() -> None:
    payload = web_server._public_writing_preset_catalog_payload()

    creativity = payload["genre_creativity"]
    creativity_json = json.dumps(creativity, ensure_ascii=False)
    pack = creativity["cn-romantasy-court"]
    direction = next(
        item for item in pack["directions"] if item["key"] == "distilled-mechanism-remix"
    )

    assert pack["default_key"] == "genre-synthesis"
    assert len(pack["directions"]) >= 4
    assert "题材库" in direction["source_mix"]
    assert any(item.startswith("蒸馏库:") for item in direction["source_mix"])
    assert "premise_seed" in direction["prompt_hints"]
    assert direction["prompt_hints"]["usage_rule"]
    assert "父母失踪" not in creativity_json
    assert "父亲失踪" not in creativity_json
    assert "母亲失踪" not in creativity_json
    assert "必须根据所选类型动态创造主角目标" in creativity_json


def test_public_writing_preset_catalog_exposes_hook_candidates() -> None:
    payload = web_server._public_writing_preset_catalog_payload()

    hooks = payload["hook_candidates"]
    pack = hooks["apocalypse-supply"]
    first = pack[0]

    assert len(pack) >= 12
    assert len({item["spec"]["mechanism_key"] for item in pack}) >= 6
    assert first["spec"]["one_liner"]
    assert first["spec"]["core_rule"]
    assert first["spec"]["llm_design_brief"]
    assert first["score"]["h_norm"] >= 0


def test_quickstart_task_uses_sanitized_genre_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = web_server.WebTaskManager()
    captured: dict[str, object] = {}

    def fake_create_autowrite_task(self: object, payload: dict[str, object]) -> dict[str, object]:
        captured["payload"] = payload
        return {"task_id": "demo-task"}

    monkeypatch.setattr(
        web_server.WebTaskManager, "create_autowrite_task", fake_create_autowrite_task
    )

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


def test_quickstart_task_passes_selected_hook_spec(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = web_server.WebTaskManager()
    captured: dict[str, object] = {}
    catalog = web_server._public_writing_preset_catalog_payload()
    hook_spec = catalog["hook_candidates"]["apocalypse-supply"][0]["spec"]

    def fake_create_autowrite_task(self: object, payload: dict[str, object]) -> dict[str, object]:
        captured["payload"] = payload
        return {"task_id": "demo-task"}

    monkeypatch.setattr(
        web_server.WebTaskManager, "create_autowrite_task", fake_create_autowrite_task
    )

    task = manager.create_quickstart_task(
        {
            "genre_key": "apocalypse-supply",
            "chapter_count": 12,
            "hook_spec": hook_spec,
        }
    )

    payload = captured["payload"]
    assert payload["hook_spec"] == hook_spec
    assert payload["user_hints"]["hook_spec"] == hook_spec
    assert task["quickstart_meta"]["hook_spec"] == hook_spec


def test_quickstart_task_title_uses_local_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = web_server.WebTaskManager()
    captured: dict[str, object] = {}

    def fake_create_autowrite_task(self: object, payload: dict[str, object]) -> dict[str, object]:
        captured["payload"] = payload
        return {"task_id": "demo-task"}

    monkeypatch.setattr(
        web_server.WebTaskManager, "create_autowrite_task", fake_create_autowrite_task
    )
    monkeypatch.setattr(web_server, "_local_now", lambda: datetime(2026, 6, 2, 10, 27))

    manager.create_quickstart_task({"genre_key": "urban-blacktech", "chapter_count": 12})

    assert captured["payload"]["title"].endswith("06-02 10:27")


def test_quickstart_task_passes_selected_creative_direction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = web_server.WebTaskManager()
    captured: dict[str, object] = {}

    def fake_create_autowrite_task(self: object, payload: dict[str, object]) -> dict[str, object]:
        captured["payload"] = payload
        return {"task_id": "demo-task"}

    monkeypatch.setattr(
        web_server.WebTaskManager, "create_autowrite_task", fake_create_autowrite_task
    )

    task = manager.create_quickstart_task(
        {
            "genre_key": "cn-romantasy-court",
            "creative_key": "cross-genre-friction",
            "chapter_count": 12,
        }
    )

    payload = captured["payload"]
    hints = payload["user_hints"]
    assert task["quickstart_meta"]["creative_key"] == "cross-genre-friction"
    assert task["quickstart_meta"]["creative_title"] == "奇幻/玄幻/异世界 × 言情/女性向"
    assert payload["creative_key"] == "cross-genre-friction"
    assert payload["creative_brief"]["key"] == "cross-genre-friction"
    assert hints["creative_direction"] == "奇幻/玄幻/异世界 × 言情/女性向"
    assert "固定套路" in hints["usage_rule"]


def test_quickstart_task_without_explicit_selection_skips_autobake(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Genre-only quickstart must NOT auto-bake a default creative direction,
    concept bundle, or bundle-derived hook_spec. The framework should grow
    genre-fitting concepts itself instead of being pinned to a preset."""
    manager = web_server.WebTaskManager()
    captured: dict[str, object] = {}

    def fake_create_autowrite_task(self: object, payload: dict[str, object]) -> dict[str, object]:
        captured["payload"] = payload
        return {"task_id": "demo-task"}

    monkeypatch.setattr(
        web_server.WebTaskManager, "create_autowrite_task", fake_create_autowrite_task
    )

    manager.create_quickstart_task({"genre_key": "apocalypse-supply", "chapter_count": 12})

    payload = captured["payload"]
    assert payload["creative_key"] == ""
    assert payload["creative_brief"] == {}
    assert payload["concept_lab_bundle"] == {}
    assert payload["hook_spec"] == {}
    assert "hook_spec" not in payload["user_hints"]
    assert "audience" not in payload


def test_quickstart_task_threads_explicit_audience_orientation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = web_server.WebTaskManager()
    captured: dict[str, object] = {}

    def fake_create_autowrite_task(self: object, payload: dict[str, object]) -> dict[str, object]:
        captured["payload"] = payload
        return {"task_id": "demo-task"}

    monkeypatch.setattr(
        web_server.WebTaskManager, "create_autowrite_task", fake_create_autowrite_task
    )

    manager.create_quickstart_task(
        {
            "genre_key": "apocalypse-supply",
            "chapter_count": 12,
            "audience_orientation": "female",
        }
    )

    payload = captured["payload"]
    assert payload["audience"] == "女频"
    assert payload["audience_orientation"] == "female"
    assert payload["user_hints"]["audience_orientation"] == "女频"


def test_quickstart_task_passes_selected_concept_lab_bundle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = web_server.WebTaskManager()
    captured: dict[str, object] = {}
    catalog = web_server.build_concept_lab_catalog("apocalypse-supply", count=1)
    bundle = catalog.bundles[0].model_dump(mode="json")
    stale_top_level_hook = {**bundle["hook_spec"], "one_liner": "stale top-level hook"}

    def fake_create_autowrite_task(self: object, payload: dict[str, object]) -> dict[str, object]:
        captured["payload"] = payload
        return {"task_id": "demo-task"}

    monkeypatch.setattr(
        web_server.WebTaskManager, "create_autowrite_task", fake_create_autowrite_task
    )

    task = manager.create_quickstart_task(
        {
            "genre_key": "apocalypse-supply",
            "chapter_count": 12,
            "creative_key": bundle["creative_key"],
            "hook_spec": stale_top_level_hook,
            "concept_lab_bundle_id": bundle["bundle_id"],
            "concept_lab_bundle": bundle,
        }
    )

    payload = captured["payload"]
    hints = payload["user_hints"]
    assert payload["concept_lab_bundle"]["bundle_id"] == bundle["bundle_id"]
    assert payload["hook_spec"] == bundle["hook_spec"]
    assert hints["hook_spec"] == bundle["hook_spec"]
    assert hints["concept_lab"]["bundle_id"] == bundle["bundle_id"]
    assert hints["material_brief"]["query_terms"]
    assert hints["story_loop"]["per_chapter_contract"]
    assert task["quickstart_meta"]["hook_spec"] == bundle["hook_spec"]
    assert task["quickstart_meta"]["concept_lab_summary"]["bundle_id"] == bundle["bundle_id"]


def test_project_repair_status_payload_marks_repair_gate() -> None:
    project = SimpleNamespace(
        status="paused",
        metadata_json={
            "production_paused": True,
            "generation_resume_blocked_until_repair_audit": True,
            "repair_audit_out_of_range_chapters": 470,
        },
    )
    payload = web_server._build_project_repair_status_payload(
        project,
        [
            {"status": "complete", "production_state": "ok", "count": 27},
            {"status": "revision", "production_state": "blocked", "count": 470},
            {"status": "revision", "production_state": "ok", "count": 3},
        ],
    )

    assert payload["phase"] == "repair_gate"
    assert payload["is_repairing"] is True
    assert payload["repair_scope_total"] == 470
    assert payload["repair_remaining"] == 470
    assert payload["repair_completed"] == 0
    assert payload["progress_percent"] == 0
    assert payload["complete_ok_chapters"] == 27


def test_project_repair_status_payload_archived_is_not_repairing() -> None:
    project = SimpleNamespace(
        status="revising",
        metadata_json={"library_archived": True},
    )
    payload = web_server._build_project_repair_status_payload(
        project,
        [{"status": "revision", "production_state": "blocked", "count": 1}],
    )

    assert payload["phase"] == "archived"
    assert payload["label"] == "已归档"
    assert payload["is_repairing"] is False
    assert web_server._build_db_repair_task_summary(project, payload) is None


def test_project_repair_status_payload_completed_is_not_repairing() -> None:
    project = SimpleNamespace(
        slug="xianxia-upgrade-1776137730",
        title="道种破虚",
        status="completed",
        metadata_json={"manually_marked_completed": True},
        target_chapters=551,
    )
    payload = web_server._build_project_repair_status_payload(
        project,
        [{"status": "revision", "production_state": "blocked", "count": 31}],
    )

    assert payload["phase"] == "completed"
    assert payload["label"] == "已完成"
    assert payload["is_repairing"] is False
    assert web_server._build_db_repair_task_summary(project, payload) is None


def test_library_book_state_active_workflow_overrides_repair_attention() -> None:
    state = web_server._library_book_state(
        status="revising",
        completed_units=12,
        target_units=100,
        has_content=True,
        repair_status={"is_repairing": True},
        has_active_workflow=True,
    )

    assert state == "in_progress"


def test_project_repair_status_payload_tracks_progress_after_unblocking() -> None:
    project = SimpleNamespace(
        status="paused",
        metadata_json={
            "production_paused": True,
            "repair_audit_out_of_range_chapters": 470,
        },
    )
    payload = web_server._build_project_repair_status_payload(
        project,
        [
            {"status": "complete", "production_state": "ok", "count": 127},
            {"status": "revision", "production_state": "blocked", "count": 370},
        ],
    )

    assert payload["repair_scope_total"] == 470
    assert payload["repair_remaining"] == 370
    assert payload["repair_completed"] == 100
    assert payload["progress_percent"] == 21.3


def test_project_repair_status_payload_exposes_autonomous_repair_queue() -> None:
    project = SimpleNamespace(status="revising", metadata_json={})

    payload = web_server._build_project_repair_status_payload(
        project,
        [{"status": "complete", "production_state": "ok", "count": 57}],
        {"pending": 246, "failed": 0, "completed": 12},
    )

    assert payload["phase"] == "repair_gate"
    assert payload["pending_autonomous_repair_tasks"] == 246
    assert payload["failed_autonomous_repair_tasks"] == 0
    assert payload["autonomous_repair_tasks"]["total"] == 258


def test_reader_chapter_state_separates_blocked_queued_and_active_repair() -> None:
    assert web_server._reader_chapter_state(
        chapter_status="revision",
        production_state="ok",
        availability="available",
    ) == ("formal", "正式版")
    assert web_server._reader_chapter_state(
        chapter_status="revision",
        production_state="blocked",
        availability="repair_in_progress",
        pending_rewrite_task_count=2,
    ) == ("queued_repair", "排队修复")
    assert web_server._reader_chapter_state(
        chapter_status="revision",
        production_state="blocked",
        availability="repair_in_progress",
        auto_repair_in_progress=True,
    ) == ("active_repair", "正在修复")
    assert web_server._reader_chapter_state(
        chapter_status="revision",
        production_state="blocked",
        availability="repair_in_progress",
    ) == ("blocked", "阻塞待修")


def test_nonblocking_rejected_candidate_not_counted_as_failed_repair() -> None:
    assert web_server._is_nonblocking_rejected_candidate(
        error_log="chapter rewrite rejected by quality gate; current draft preserved",
        metadata={"preserved_current_quality_gate_outcome": "ok"},
        chapter_production_state="blocked",
    )
    assert web_server._is_nonblocking_rejected_candidate(
        error_log="chapter rewrite rejected by quality gate; current draft preserved",
        metadata={},
        chapter_production_state="ok",
    )
    assert not web_server._is_nonblocking_rejected_candidate(
        error_log="TimeoutError: rewrite task exceeded 30.0s",
        metadata={"preserved_current_quality_gate_outcome": "ok"},
        chapter_production_state="ok",
    )


def test_build_db_repair_task_summary_surfaces_project_queue() -> None:
    project = SimpleNamespace(
        slug="xianxia-upgrade-1776137730",
        title="道种破虚",
        target_chapters=1200,
        created_at=datetime(2026, 5, 18, 1, 0, tzinfo=UTC),
        updated_at=datetime(2026, 5, 18, 2, 0, tzinfo=UTC),
    )
    repair_status = {
        "phase": "repair_gate",
        "blocked_chapters": 7,
        "autonomous_repair_tasks": {"pending": 246, "total": 246},
    }

    task = web_server._build_db_repair_task_summary(project, repair_status)

    assert task is not None
    assert task["task_id"] == "db-repair:xianxia-upgrade-1776137730"
    assert task["task_type"] == "repair"
    assert task["status"] == "incomplete"
    assert task["title"] == "道种破虚"
    assert task["result"]["pending_autonomous_repair_tasks"] == 246
    assert task["synthetic_db_repair_task"] is True


def test_build_db_repair_task_summary_keeps_failed_rewrite_feedback_retryable() -> None:
    project = SimpleNamespace(
        slug="romantasy-1776330993",
        title="Shadowbound",
        status="revising",
        target_chapters=800,
        created_at=datetime(2026, 6, 4, 13, 0, tzinfo=UTC),
        updated_at=datetime(2026, 6, 4, 13, 10, tzinfo=UTC),
    )
    repair_status = {
        "phase": "needs_attention",
        "blocked_chapters": 0,
        "autonomous_repair_tasks": {"failed": 1, "total": 1},
        "detail": "部分闭环修复任务未通过，需要携带失败反馈进入下一轮修复。",
    }

    task = web_server._build_db_repair_task_summary(project, repair_status)

    assert task is not None
    assert task["status"] == "incomplete"
    assert task["current_stage"] == "legacy_quality_closure_repair_retry_pending"
    assert task["error"] is None


def test_db_repair_summary_rehydrates_latest_workflow_progress() -> None:
    project = SimpleNamespace(
        slug="exorcist-detective-1778051012",
        title="青囊不语问阴阳",
        status="paused",
        target_chapters=200,
        created_at=datetime(2026, 6, 2, 17, 0, tzinfo=UTC),
        updated_at=datetime(2026, 6, 2, 17, 45, tzinfo=UTC),
        metadata_json={"production_pause_reason": "focus_latest_book_validation"},
    )
    run_id = uuid4()
    run = SimpleNamespace(
        id=run_id,
        status="cancelled",
        current_step="focus_latest_book_validation_cancelled",
        error_message="Cancelled to focus validation on latest project apocalypse-rule-1780385156.",
        requested_by="worker_self_heal",
        created_at=datetime(2026, 6, 2, 9, 36, tzinfo=UTC),
        updated_at=datetime(2026, 6, 2, 9, 45, tzinfo=UTC),
        metadata_json={
            "project_slug": project.slug,
            "target_chapter_numbers": list(range(51, 101)),
            "focus_cancelled": True,
            "focus_primary_slug": "apocalypse-rule-1780385156",
        },
    )
    steps = [
        SimpleNamespace(
            workflow_run_id=run_id,
            step_order=2,
            step_name="collect_pending_rewrite_tasks",
            created_at=datetime(2026, 6, 2, 9, 37, tzinfo=UTC),
            output_ref={
                "target_chapter_numbers": list(range(86, 101)),
                "repair_gate_chapter_numbers": [86, 87, 88],
            },
        ),
        SimpleNamespace(
            workflow_run_id=run_id,
            step_order=4,
            step_name="repair_chapter_86",
            created_at=datetime(2026, 6, 2, 9, 38, tzinfo=UTC),
            output_ref={
                "chapter_number": 86,
                "chapter_status": "revision",
                "production_state": "blocked",
                "requires_human_review": True,
                "chapter_workflow_run_id": str(uuid4()),
            },
        ),
    ]

    latest = web_server._project_repair_workflow_snapshot(run, steps)
    repair_status = web_server._build_project_repair_status_payload(
        project,
        [{"status": "revision", "production_state": "blocked", "count": 2}],
        {},
        latest,
    )
    task = web_server._build_db_repair_task_summary(project, repair_status)

    assert repair_status["label"] == "修复已中断"
    assert repair_status["production_pause_reason"] == "focus_latest_book_validation"
    assert task is not None
    assert task["status"] == "incomplete"
    assert task["current_stage"] == "focus_latest_book_validation_cancelled"
    assert task["error"] == run.error_message
    assert task["result"]["target_chapter_numbers"] == list(range(86, 101))
    assert task["result"]["repair_gate_chapter_numbers"] == [86, 87, 88]
    assert task["result"]["processed_chapter_numbers"] == [86]
    stages = [event["stage"] for event in task["progress_events"]]
    assert "project_repair_targets_collected" in stages
    assert "project_repair_chapter_started" in stages
    assert "project_repair_chapter_completed" in stages


def test_db_repair_summary_keeps_generation_gate_retry_out_of_failed_state() -> None:
    project = SimpleNamespace(
        slug="oracle-pilot-dianshen",
        title="借运成神",
        status="writing",
        target_chapters=12,
        created_at=datetime(2026, 6, 4, 13, 0, tzinfo=UTC),
        updated_at=datetime(2026, 6, 4, 13, 3, tzinfo=UTC),
        metadata_json={
            "generation_gate_auto_retry_needed": True,
            "last_generation_gate_reason": "scene_plan_richness_gate_failed:no_state_delta",
        },
    )
    latest = {
        "status": "failed",
        "current_step": "repair_chapter_1",
        "error_message": "Scene 1.2 blocked by plan-richness gate",
        "created_at": "2026-06-04T13:00:54+00:00",
        "updated_at": "2026-06-04T13:03:28+00:00",
        "progress_events": [],
    }

    repair_status = web_server._build_project_repair_status_payload(
        project,
        [{"status": "revision", "production_state": "pending", "count": 1}],
        {},
        latest,
    )
    task = web_server._build_db_repair_task_summary(project, repair_status)

    assert repair_status["phase"] == "planning_gate"
    assert task is not None
    assert task["status"] == "queued"
    assert task["current_stage"] == "planning_gate_auto_retry_pending"
    assert task["error"] is None


def test_stale_autowrite_repair_block_task_can_be_hidden_by_db_repair() -> None:
    task = {
        "task_id": "old-autowrite",
        "task_type": "autowrite",
        "status": "cancelled",
        "current_stage": "blocked_structural_repair",
        "project_slug": "exorcist-detective-1778051012",
        "error": "项目 'exorcist-detective-1778051012' 当前处于结构修复暂停状态。",
    }

    assert web_server._is_stale_autowrite_repair_block_task(
        task,
        {"exorcist-detective-1778051012"},
    )
    assert not web_server._is_stale_autowrite_repair_block_task(task, set())


def test_repair_attention_task_is_dashboard_visible() -> None:
    task = {
        "task_id": "db-repair:exorcist-detective-1778051012",
        "task_type": "repair",
        "status": "incomplete",
        "repair_status": {"is_repairing": True, "label": "修复已中断"},
    }

    assert web_server._is_dashboard_visible_task(task)


def test_request_visible_task_cancel_routes_db_repair_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = web_server.WebTaskManager()
    settings = SimpleNamespace(redis=SimpleNamespace(url="redis://stub"))
    called: dict[str, object] = {}

    def fake_cancel_db_repair_task(settings_arg: object, task_id: str) -> str:
        called["settings"] = settings_arg
        called["task_id"] = task_id
        return "cancel_requested"

    monkeypatch.setattr(
        web_server,
        "_cancel_db_repair_task",
        fake_cancel_db_repair_task,
    )

    outcome = web_server._request_visible_task_cancel(
        manager,
        settings,
        "db-repair:xianxia-upgrade-1776137730",
    )

    assert outcome == "cancel_requested"
    assert called == {
        "settings": settings,
        "task_id": "db-repair:xianxia-upgrade-1776137730",
    }


def test_cancel_db_repair_task_flips_running_workflow_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A crashed repair can leave its workflow row stuck ``running`` with zero
    pending rewrite tasks and no ARQ job. Stop must still report
    ``cancel_requested`` by flipping that row — otherwise the synthetic
    ``db-repair:`` card stays "running" and Stop/Delete deadlock.
    """

    class _Result:
        def __init__(self, rowcount: int) -> None:
            self.rowcount = rowcount

    class _FakeSession:
        def __init__(self) -> None:
            self.executed: list[object] = []

        async def execute(self, stmt: object) -> _Result:
            self.executed.append(stmt)
            # 1st execute = RewriteTask update (none pending) -> 0 rows.
            # 2nd execute = WorkflowRun update (the stuck zombie) -> 1 row.
            return _Result(0 if len(self.executed) == 1 else 1)

    fake_session = _FakeSession()

    class _SessionScope:
        async def __aenter__(self) -> _FakeSession:
            return fake_session

        async def __aexit__(self, *_args: object) -> None:
            return None

    async def fake_get_project_by_slug(_session: object, _slug: str) -> object:
        return SimpleNamespace(id="proj-id")

    async def fake_abort(_redis_url: str, _job_id: str) -> bool:
        return False

    monkeypatch.setattr(web_server, "session_scope", lambda _settings: _SessionScope())
    monkeypatch.setattr(web_server, "get_project_by_slug", fake_get_project_by_slug)
    monkeypatch.setattr(web_server, "_abort_worker_heal_job_async", fake_abort)

    settings = SimpleNamespace(redis=SimpleNamespace(url="redis://stub"))
    outcome = asyncio.run(
        web_server._cancel_db_repair_task_async(
            settings,
            "db-repair:xianxia-upgrade-1781106694",
        )
    )

    assert outcome == "cancel_requested"
    # Both the rewrite-task sweep and the workflow-row flip must be attempted.
    assert len(fake_session.executed) == 2


def test_cancel_db_repair_task_unknown_project_is_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FakeSession:
        async def execute(self, _stmt: object) -> object:  # pragma: no cover
            raise AssertionError("must not query when project is missing")

    class _SessionScope:
        async def __aenter__(self) -> _FakeSession:
            return _FakeSession()

        async def __aexit__(self, *_args: object) -> None:
            return None

    async def fake_get_project_by_slug(_session: object, _slug: str) -> None:
        return None

    monkeypatch.setattr(web_server, "session_scope", lambda _settings: _SessionScope())
    monkeypatch.setattr(web_server, "get_project_by_slug", fake_get_project_by_slug)

    settings = SimpleNamespace(redis=SimpleNamespace(url="redis://stub"))
    outcome = asyncio.run(
        web_server._cancel_db_repair_task_async(settings, "db-repair:ghost-book")
    )

    assert outcome == "not_found"


def test_request_visible_task_cancel_routes_worker_heal_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = web_server.WebTaskManager()
    settings = SimpleNamespace(redis=SimpleNamespace(url="redis://stub"))
    called: dict[str, object] = {}

    def fake_abort_worker_heal_job(redis_url: str, job_id: str) -> bool:
        called["redis_url"] = redis_url
        called["job_id"] = job_id
        return True

    monkeypatch.setattr(
        web_server,
        "_abort_worker_heal_job",
        fake_abort_worker_heal_job,
    )

    outcome = web_server._request_visible_task_cancel(
        manager,
        settings,
        "repair:heal:xianxia-upgrade-1776137730",
    )

    assert outcome == "cancel_requested"
    assert called == {
        "redis_url": "redis://stub",
        "job_id": "repair:heal:xianxia-upgrade-1776137730",
    }


def test_request_visible_task_cancel_routes_project_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = web_server.WebTaskManager()
    settings = SimpleNamespace(redis=SimpleNamespace(url="redis://stub"))
    called: dict[str, object] = {}

    def fake_cancel_project_task(settings_arg: object, task_id: str) -> str:
        called["settings"] = settings_arg
        called["task_id"] = task_id
        return "cancel_requested"

    monkeypatch.setattr(
        web_server,
        "_cancel_project_task",
        fake_cancel_project_task,
    )

    outcome = web_server._request_visible_task_cancel(
        manager,
        settings,
        "project:xianxia-upgrade-1776137730",
    )

    assert outcome == "cancel_requested"
    assert called == {
        "settings": settings,
        "task_id": "project:xianxia-upgrade-1776137730",
    }


def test_request_visible_task_cancel_reports_existing_finished_task_not_running() -> None:
    manager = web_server.WebTaskManager()
    settings = SimpleNamespace(redis=SimpleNamespace(url="redis://stub"))
    with manager._lock:
        manager._tasks["done-task"] = web_server.WebTaskState(
            task_id="done-task",
            task_type="autowrite",
            status="completed",
            created_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-01T00:00:00+00:00",
            project_slug="done-book",
            current_stage="completed",
        )

    outcome = web_server._request_visible_task_cancel(manager, settings, "done-task")

    assert outcome == "not_running"


def test_worker_heal_progress_snapshot_reads_redis_progress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FakeRedis:
        def exists(self, *keys: str) -> int:
            return int("arq:in-progress:repair:heal:novel-a" in keys)

        def zscore(self, _key: str, _member: str) -> None:
            return None

        def lrange(self, key: str, *_args: object) -> list[str]:
            assert key == "task:repair:heal:novel-a:progress"
            return [
                '{"ts": 1779083333.4, "message": "project_repair_chapter_started", '
                '"data": {"project_slug": "novel-a", "chapter_number": 22}}'
            ]

        def close(self) -> None:
            return None

    class _FakeRedisModule:
        @staticmethod
        def from_url(_url: str, **_kwargs: object) -> _FakeRedis:
            return _FakeRedis()

    import sys as _sys

    monkeypatch.setitem(_sys.modules, "redis", _FakeRedisModule)

    snapshot = web_server._load_worker_heal_progress_snapshot(
        "redis://stub",
        "repair:heal:novel-a",
    )

    assert snapshot is not None
    assert snapshot["status"] == "running"
    assert snapshot["current_stage"] == "project_repair_chapter_started"
    assert snapshot["progress_events"] == [
        {
            "timestamp": 1779083333.4,
            "stage": "project_repair_chapter_started",
            "payload": {"project_slug": "novel-a", "chapter_number": 22},
        }
    ]


def test_db_repair_summary_merges_worker_progress_details() -> None:
    summary = {
        "task_id": "db-repair:novel-a",
        "task_type": "repair",
        "status": "queued",
        "updated_at": "2026-05-18T01:00:00+00:00",
        "current_stage": "delegated_to_worker_self_heal",
        "progress_events": [
            {
                "timestamp": "2026-05-18T01:00:00+00:00",
                "stage": "legacy_quality_closure_repair_pending",
                "payload": {"project_slug": "novel-a"},
            }
        ],
        "error": "old error",
    }
    worker_progress = {
        "status": "running",
        "current_stage": "project_repair_chapter_started",
        "progress_events": [
            {
                "timestamp": 1779083333.4,
                "stage": "project_repair_chapter_started",
                "payload": {"project_slug": "novel-a", "chapter_number": 22},
            }
        ],
        "latest_payload": {"project_slug": "novel-a", "chapter_number": 22},
    }

    merged = web_server._merge_worker_progress_into_db_repair_summary(
        summary,
        worker_progress,
    )

    assert merged["status"] == "running"
    assert merged["current_stage"] == "project_repair_chapter_started"
    assert merged["error"] is None
    assert merged["updated_at"] == "2026-05-18T05:48:53.400000+00:00"
    assert len(merged["progress_events"]) == 2
    assert merged["progress_events"][-1]["payload"]["chapter_number"] == 22


def test_task_progress_payload_surfaces_latest_event_and_repair_status() -> None:
    task = {
        "task_id": "autowrite:heal:novel-a",
        "task_type": "autowrite",
        "status": "running",
        "title": "长夜巡航",
        "project_slug": "novel-a",
        "current_stage": "project_repair_chapter_started",
        "progress_events": [
            {"stage": "legacy_quality_closure_started", "payload": {"chapter": 1}},
            {"stage": "project_repair_chapter_started", "payload": {"chapter": 13}},
            {
                "stage": "delegated_to_worker_self_heal",
                "payload": {"reason": "ARQ heal job already active"},
            },
        ],
        "repair_status": {"phase": "repairing", "blocked_chapters": 0},
    }

    payload = web_server._task_progress_payload(task)

    assert payload["task_id"] == "autowrite:heal:novel-a"
    assert payload["status"] == "running"
    assert payload["current_stage"] == "project_repair_chapter_started"
    assert payload["latest_event"] == {
        "stage": "project_repair_chapter_started",
        "payload": {"chapter": 13},
    }
    assert payload["repair_status"] == {"phase": "repairing", "blocked_chapters": 0}


def test_load_worker_task_summary_surfaces_redis_owned_autowrite_job(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        web_server,
        "_load_worker_heal_progress_snapshot",
        lambda redis_url, job_id: {
            "status": "running",
            "current_stage": "project_repair_chapter_started",
            "progress_events": [
                {
                    "stage": "project_repair_chapter_started",
                    "payload": {"chapter_number": 14},
                }
            ],
            "latest_payload": {"chapter_number": 14},
        },
    )

    summary = web_server._load_worker_task_summary(
        SimpleNamespace(redis=SimpleNamespace(url="redis://stub")),
        "autowrite:heal:novel-a",
    )

    assert summary is not None
    assert summary["task_id"] == "autowrite:heal:novel-a"
    assert summary["task_type"] == "autowrite"
    assert summary["project_slug"] == "novel-a"
    assert summary["current_stage"] == "project_repair_chapter_started"
    assert summary["synthetic_worker_task"] is True


def test_reader_chapter_availability_uses_production_gate() -> None:
    assert web_server._reader_chapter_availability("ok", 2100) == "available"
    assert web_server._reader_chapter_availability("blocked", 2100) == "repair_in_progress"
    assert web_server._reader_chapter_availability("pending", 2100) == "repair_in_progress"
    assert web_server._reader_chapter_availability("ok", 0) == "planned"


def test_project_autowrite_block_payload_ignores_machine_repair_pause() -> None:
    project = SimpleNamespace(
        slug="demo-paused",
        title="Demo",
        metadata_json={
            "generation_resume_blocked_until_repair_audit": True,
            "production_pause_reason": "structural_repair_before_continuation",
        },
    )

    payload = web_server._project_autowrite_block_payload(project)

    assert payload is None


def test_autowrite_worker_marks_structural_repair_pause_without_traceback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = web_server.WebTaskManager(persist_path=tmp_path / ".web_tasks.json")
    task_id = "paused-task"
    with manager._lock:
        manager._tasks[task_id] = web_server.WebTaskState(
            task_id=task_id,
            task_type="autowrite",
            status="queued",
            created_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-01T00:00:00+00:00",
            project_slug="demo-paused",
            title="Demo",
            current_stage="queued",
        )

    class _SessionScope:
        async def __aenter__(self) -> object:
            return object()

        async def __aexit__(self, *_args: object) -> None:
            return None

    monkeypatch.setattr(web_server, "session_scope", lambda _settings: _SessionScope())
    monkeypatch.setattr(
        web_server,
        "load_settings",
        lambda: SimpleNamespace(quality=SimpleNamespace(draft_mode=False)),
    )

    async def _raise_repair_pause(**_kwargs: object) -> object:
        raise ProjectRepairPauseError("Project 'demo-paused' is paused for structural repair.")

    monkeypatch.setattr(web_server, "run_autowrite_pipeline", _raise_repair_pause)

    manager._run_autowrite_worker(
        task_id,
        {
            "slug": "demo-paused",
            "title": "Demo",
            "genre": "玄幻",
            "target_words": 6000,
            "target_chapters": 3,
            "premise": "继续创作。",
        },
    )

    task = manager.get_task(task_id)
    assert task is not None
    assert task["status"] == "cancelled"
    assert task["current_stage"] == "blocked_structural_repair"
    assert "paused for structural repair" in str(task["error"])
    assert "Traceback" not in str(task["error"])


def test_quickstart_concept_lab_reaches_worker_project_payload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = web_server.WebTaskManager(persist_path=tmp_path / ".web_tasks.json")
    task_id = "concept-worker-task"
    bundle = web_server.build_concept_lab_catalog("apocalypse-supply", count=1).bundles[0]
    captured: dict[str, object] = {}

    def _capture_autowrite_task(
        self: web_server.WebTaskManager,
        payload: dict[str, object],
    ) -> dict[str, object]:
        captured["autowrite_payload"] = payload
        return {
            "task_id": task_id,
            "task_type": "autowrite",
            "status": "queued",
            "project_slug": payload["slug"],
            "title": payload["title"],
        }

    monkeypatch.setattr(
        web_server.WebTaskManager,
        "create_autowrite_task",
        _capture_autowrite_task,
    )

    quickstart_task = manager.create_quickstart_task(
        {
            "genre_key": "apocalypse-supply",
            "chapter_count": 12,
            "creative_key": bundle.creative_key,
            "hook_spec": bundle.hook_spec,
            "concept_lab_bundle_id": bundle.bundle_id,
            "concept_lab_bundle": bundle.model_dump(mode="json"),
        }
    )
    autowrite_payload = captured["autowrite_payload"]
    assert (
        quickstart_task["quickstart_meta"]["concept_lab_summary"]["bundle_id"]
        == bundle.bundle_id
    )
    assert autowrite_payload["user_hints"]["concept_lab"]["bundle_id"] == bundle.bundle_id

    with manager._lock:
        manager._tasks[task_id] = web_server.WebTaskState(
            task_id=task_id,
            task_type="autowrite",
            status="queued",
            created_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-01T00:00:00+00:00",
            project_slug=str(autowrite_payload["slug"]),
            title=str(autowrite_payload["title"]),
            current_stage="queued",
        )

    class _SessionScope:
        async def __aenter__(self) -> object:
            return object()

        async def __aexit__(self, *_args: object) -> None:
            return None

    class _PipelineResult:
        def model_dump(self, *, mode: str = "json") -> dict[str, object]:
            return {"project_slug": "concept-worker", "mode": mode}

    async def _fake_architect_story_facets(
        _session: object,
        _settings: object,
        **kwargs: object,
    ) -> object:
        captured["architect_user_hints"] = kwargs.get("user_hints")
        return SimpleNamespace(
            tone="高压",
            narrative_drive="生存升级",
            trope_tags=("末日",),
            setting="倒计时城市",
            generation_source="unit-test",
            model_dump=lambda mode="json": {"tone": "高压", "mode": mode},
        )

    async def _fake_run_conception_pipeline(
        _session: object,
        _settings: object,
        **kwargs: object,
    ) -> object:
        captured["conception_user_hints"] = kwargs.get("user_hints")
        return SimpleNamespace(
            premise="脑洞合同驱动的末日故事。",
            title="脑洞合同标题",
            writing_profile={"market": {"reader_promise": bundle.reader_promise}},
            commercial_brief={"benchmark_works": [], "target_audiences": ["番茄读者"]},
            conception_log=[{"stage": "concept_lab"}],
            hook_spec=bundle.hook_spec,
            synopsis="用脑洞合同推进故事循环。",
            tags=["末日", "脑洞"],
        )

    async def _fake_run_autowrite_pipeline(**kwargs: object) -> object:
        captured["pipeline_kwargs"] = kwargs
        return _PipelineResult()

    from bestseller.services import conception as conception_services
    from bestseller.services import story_architect as story_architect_services

    monkeypatch.setattr(web_server, "session_scope", lambda _settings: _SessionScope())
    monkeypatch.setattr(
        web_server,
        "load_settings",
        lambda: SimpleNamespace(quality=SimpleNamespace(draft_mode=False)),
    )
    monkeypatch.setattr(
        story_architect_services,
        "architect_story_facets",
        _fake_architect_story_facets,
    )
    monkeypatch.setattr(
        conception_services,
        "run_conception_pipeline",
        _fake_run_conception_pipeline,
    )
    monkeypatch.setattr(web_server, "run_autowrite_pipeline", _fake_run_autowrite_pipeline)

    manager._run_autowrite_worker(task_id, autowrite_payload)

    task = manager.get_task(task_id)
    assert task is not None
    assert task["status"] == "completed"
    assert captured["architect_user_hints"]["concept_lab"]["bundle_id"] == bundle.bundle_id
    assert captured["conception_user_hints"]["concept_lab"]["bundle_id"] == bundle.bundle_id
    project_payload = captured["pipeline_kwargs"]["project_payload"]
    assert project_payload.metadata["concept_lab"]["bundle_id"] == bundle.bundle_id
    assert project_payload.metadata["hook_spec"]["one_liner"] == bundle.hook_spec["one_liner"]
    assert project_payload.writing_profile.market.reader_promise == bundle.reader_promise


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

    payload = web_server._resolve_story_bible_progress(story_bible, current_chapter_number=24)

    assert payload["has_backbone"] is True
    assert payload["current_frontier"]["volume_number"] == 2
    assert payload["next_gate"]["unlock_volume_number"] == 3
    assert payload["unlocked_gate_count"] == 1


def test_design_dossier_readiness_flags_missing_design_surfaces() -> None:
    payload = web_server._build_design_dossier_readiness(
        planning_documents=[
            {
                "artifact_type": "book_spec",
                "version_no": 1,
                "content": {"title": "Demo"},
            }
        ],
        structure={"total_chapters": 12, "total_scenes": 36},
        story_bible={
            "world_backbone": {"title": "世界主干"},
            "world_rules": [{"rule_code": "R1"}],
            "locations": [],
            "factions": [],
            "characters": [{"name": "陆渊"}],
            "relationships": [],
        },
        narrative={
            "plot_arcs": [{"arc_code": "main"}],
            "chapter_contracts": [{"chapter_number": 1}],
            "scene_contracts": [],
        },
    )

    missing_labels = {item["label"] for item in payload["blocking_gaps"]}

    assert payload["status"] == "incomplete"
    assert "关系图" in missing_labels
    assert "场景合约" in missing_labels
    assert "人物信息" not in missing_labels


def test_pipeline_flow_html_contains_expected_mount_points() -> None:
    html = web_server._read_pipeline_flow_html()

    assert "流水线数据流" in html
    assert "/api/projects/${encodeURIComponent(slug)}/pipeline-flow" in html
    assert "phaseContainer" in html
    assert "issuesPanel" in html


def test_design_dossier_html_contains_expected_mount_points() -> None:
    html = web_server._read_design_dossier_html()

    assert "设计审查包" in html
    assert "/api/projects/${encodeURIComponent(slug)}/design-dossier" in html
    assert "/api/projects/${encodeURIComponent(slug)}/design-artifact" in html
    assert 'data-tab="relations"' in html
    assert "展开后会自动加载这份规划产物的原始内容" in html
    assert "侦查式关系图谱" in html
    assert "relationship-map" in html


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
    assert manager._pending_auto_resume_ids == ["z1"]
    # The auto_resume_queued marker event was appended for UI visibility
    stages = [e["stage"] for e in task["progress_events"]]
    assert "auto_resume_queued" in stages


def test_load_from_disk_dedupes_active_same_slug_zombies(tmp_path: Path) -> None:
    persist_path = _write_persisted_tasks(
        tmp_path,
        [
            {
                "task_id": "z-old",
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
            {
                "task_id": "z-new",
                "task_type": "autowrite",
                "status": "queued",
                "created_at": "2026-01-01T00:10:00+00:00",
                "updated_at": "2026-01-01T00:15:00+00:00",
                "project_slug": "demo",
                "title": "Demo",
                "current_stage": "queued",
                "progress_events": [],
                "payload": {"slug": "demo", "title": "Demo"},
            },
        ],
    )

    manager = web_server.WebTaskManager(persist_path=persist_path)

    assert manager.get_task("z-old") is None
    task = manager.get_task("z-new")
    assert task is not None
    assert task["status"] == "queued"
    assert task["current_stage"] == "auto_resume_pending"
    assert manager._pending_auto_resume_ids == ["z-new"]
    persisted = json.loads(persist_path.read_text(encoding="utf-8"))
    assert [item["task_id"] for item in persisted] == ["z-new"]


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
    # Repair task with payload → queued for startup resume.
    repair = manager.get_task("z-repair")
    assert repair is not None
    assert repair["status"] == "queued"
    assert repair["current_stage"] == "auto_resume_pending"
    assert manager._pending_auto_resume_ids == ["z-repair"]


def test_delete_tasks_by_project_can_include_active_archived_records(
    tmp_path: Path,
) -> None:
    persist_path = _write_persisted_tasks(
        tmp_path,
        [
            {
                "task_id": "archived-running",
                "task_type": "autowrite",
                "status": "running",
                "created_at": "2026-01-01T00:00:00+00:00",
                "updated_at": "2026-01-01T00:05:00+00:00",
                "project_slug": "archived-book",
                "payload": {"slug": "archived-book"},
            },
            {
                "task_id": "archived-incomplete",
                "task_type": "autowrite",
                "status": "incomplete",
                "created_at": "2026-01-01T00:00:00+00:00",
                "updated_at": "2026-01-01T00:05:00+00:00",
                "project_slug": "archived-book",
            },
            {
                "task_id": "active-other",
                "task_type": "autowrite",
                "status": "running",
                "created_at": "2026-01-01T00:00:00+00:00",
                "updated_at": "2026-01-01T00:05:00+00:00",
                "project_slug": "live-book",
                "payload": {"slug": "live-book"},
            },
        ],
    )
    manager = web_server.WebTaskManager(persist_path=persist_path)

    removed = manager.delete_tasks_by_project(
        "archived-book",
        include_active=True,
    )

    assert removed == 2
    assert manager.get_task("archived-running") is None
    assert manager.get_task("archived-incomplete") is None
    assert manager.get_task("active-other") is not None


def test_create_repair_task_defaults_to_pending_rewrite_takeover(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    persist_path = _write_persisted_tasks(tmp_path, [])
    monkeypatch.setattr(web_server.WebTaskManager, "_run_with_slot", lambda *args: None)
    manager = web_server.WebTaskManager(persist_path=persist_path)

    task = manager.create_repair_task({"project_slug": "needs-repair"})

    assert task["payload"]["include_pending_rewrite_tasks"] is True


def test_load_from_disk_normalizes_watchdog_failed_machine_repair_task(
    tmp_path: Path,
) -> None:
    persist_path = _write_persisted_tasks(
        tmp_path,
        [
            {
                "task_id": "waiting-review",
                "task_type": "autowrite",
                "status": "failed",
                "created_at": "2026-01-01T00:00:00+00:00",
                "updated_at": "2026-01-01T00:45:00+00:00",
                "project_slug": "demo",
                "title": "Demo",
                "current_stage": "failed",
                "error": "Task watchdog: no progress for >2700s, marking as failed",
                "progress_events": [
                    {
                        "timestamp": 1778662128.667118,
                        "stage": "chapter_pipeline_machine_repair_required",
                        "payload": {"chapter_number": 491},
                    },
                    {
                        "timestamp": 1778662154.0976653,
                        "stage": "project_pipeline_completed",
                        "payload": {"final_verdict": "attention"},
                    },
                ],
                "payload": {"slug": "demo", "title": "Demo"},
            },
        ],
    )

    manager = web_server.WebTaskManager(persist_path=persist_path)

    task = manager.get_task("waiting-review")
    assert task is not None
    assert task["status"] == "incomplete"
    assert task["current_stage"] == "machine_repair_required"
    assert "stale-watchdog" in str(task["error"])
    assert task["progress_events"][-1]["stage"] == "watchdog_failure_normalized"


def test_load_from_disk_normalizes_legacy_manual_gate_task(
    tmp_path: Path,
) -> None:
    legacy_stage = "waiting" + "_human"
    legacy_reason = "project_repair_requires_attention"
    persist_path = _write_persisted_tasks(
        tmp_path,
        [
            {
                "task_id": "legacy-gate",
                "task_type": "autowrite",
                "status": "incomplete",
                "created_at": "2026-01-01T00:00:00+00:00",
                "updated_at": "2026-01-01T00:45:00+00:00",
                "project_slug": "demo",
                "title": "Demo",
                "current_stage": legacy_stage,
                "error": legacy_reason,
                "progress_events": [
                    {
                        "timestamp": 1778662128.667118,
                        "stage": legacy_stage,
                        "payload": {"reason": legacy_reason},
                    },
                ],
                "payload": {"slug": "demo", "title": "Demo"},
            },
        ],
    )

    manager = web_server.WebTaskManager(persist_path=persist_path)

    task = manager.get_task("legacy-gate")
    assert task is not None
    assert task["status"] == "incomplete"
    assert task["current_stage"] == "repairable_auto_continue_pending"
    assert task["error"] == "project_repair_requires_machine_repair"
    assert task["progress_events"][-1]["stage"] == "repairable_auto_continue_pending"
    assert task["progress_events"][-1]["payload"]["reason"] == (
        "project_repair_requires_machine_repair"
    )


def test_auto_resume_zombies_restarts_repair_tasks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    persist_path = _write_persisted_tasks(
        tmp_path,
        [
            {
                "task_id": "z-repair",
                "task_type": "repair",
                "status": "running",
                "created_at": "2026-01-01T00:00:00+00:00",
                "updated_at": "2026-01-01T00:05:00+00:00",
                "project_slug": "demo",
                "title": "Repair demo",
                "current_stage": "repair_chapter_30",
                "progress_events": [],
                "payload": {"project_slug": "demo", "export_markdown": False},
            },
        ],
    )
    manager = web_server.WebTaskManager(persist_path=persist_path)
    invocations: list[tuple[str, dict[str, object]]] = []

    def fake_run_with_slot(
        self: object,
        task_id: str,
        worker: object,
        payload: dict[str, object],
    ) -> None:
        invocations.append((task_id, dict(payload)))

    monkeypatch.setattr(web_server.WebTaskManager, "_run_with_slot", fake_run_with_slot)

    resumed = manager.auto_resume_zombies()

    import time as _time

    _time.sleep(0.1)
    assert resumed == ["z-repair"]
    assert invocations == [("z-repair", {"project_slug": "demo", "export_markdown": False})]


def test_auto_resume_zombies_marks_unclaimed_without_redis(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without a Redis URL (test / single-node env) autowrite zombies are
    not claimed by worker self-heal, so they become manually resumable instead
    of fake-running until the watchdog fails them.
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

    assert delegated == []
    # The pending list is cleared after a successful call (idempotent).
    assert manager._pending_auto_resume_ids == []
    # No thread should be spawned automatically; manual resume remains available.
    import time as _time

    _time.sleep(0.1)
    assert invocations == []

    task = manager.get_task("z-run")
    assert task is not None
    assert task["status"] == "incomplete"
    assert task["current_stage"] == "auto_resume_not_claimed"
    assert "Auto-resume was not claimed" in str(task["error"])
    assert manager.watchdog_sweep(stale_after_seconds=1) == 0


def test_auto_resume_zombies_idempotent_when_nothing_pending(tmp_path: Path) -> None:
    persist_path = _write_persisted_tasks(tmp_path, [])
    manager = web_server.WebTaskManager(persist_path=persist_path)

    assert manager.auto_resume_zombies() == []
    assert manager.auto_resume_zombies() == []


def test_auto_resume_zombies_delegates_heal_owned_and_enqueues_orphan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Known worker-owned zombies stay running; unclaimed autowrite zombies
    should be pushed into the same deterministic worker heal queue instead
    of requiring a manual resume click.
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
            {
                "task_id": "z-repair-owned",
                "task_type": "autowrite",
                "status": "running",
                "created_at": "2026-01-01T00:00:00+00:00",
                "updated_at": "2026-01-01T00:05:00+00:00",
                "project_slug": "novel-r",
                "title": "R",
                "current_stage": "chapter_pipeline_machine_repair_required",
                "progress_events": [],
                "payload": {"slug": "novel-r", "title": "R"},
            },
        ],
    )

    manager = web_server.WebTaskManager(persist_path=persist_path)

    # Stub the scan-wait to return instantly, and the key scan to report
    # only novel-a as heal-owned.
    monkeypatch.setattr(web_server, "_wait_for_self_heal_scan", lambda _url, **_kw: True)
    monkeypatch.setattr(
        web_server,
        "_fetch_heal_owned_slugs_by_kind",
        lambda _url, heal_kind: {"novel-a"} if heal_kind == "autowrite" else {"novel-r"},
    )
    enqueued_slugs: list[str] = []

    def fake_enqueue(redis_url: str, slug: str) -> str | None:
        enqueued_slugs.append(slug)
        return f"autowrite:heal:{slug}"

    monkeypatch.setattr(web_server, "_enqueue_autowrite_heal_job", fake_enqueue)

    invocations: list[str] = []

    def fake_worker(self: object, task_id: str, payload: dict[str, object]) -> None:
        invocations.append(task_id)

    monkeypatch.setattr(web_server.WebTaskManager, "_run_autowrite_worker", fake_worker)

    delegated = manager.auto_resume_zombies(redis_url="redis://stub")

    assert delegated == ["z-heal-owned", "z-orphan", "z-repair-owned"]
    assert enqueued_slugs == ["novel-b"]
    import time as _time

    _time.sleep(0.1)
    assert invocations == []

    heal_owned_task = manager.get_task("z-heal-owned")
    assert heal_owned_task is not None
    assert heal_owned_task["status"] == "running"
    assert heal_owned_task["current_stage"] == "delegated_to_worker_self_heal"
    delegated_event = next(
        e
        for e in reversed(heal_owned_task["progress_events"])
        if e.get("stage") == "delegated_to_worker_self_heal"
    )
    assert delegated_event["payload"]["heal_owned"] is True

    orphan_task = manager.get_task("z-orphan")
    assert orphan_task is not None
    assert orphan_task["status"] == "running"
    assert orphan_task["current_stage"] == "delegated_to_worker_self_heal"
    enqueued_event = next(
        e
        for e in reversed(orphan_task["progress_events"])
        if e.get("stage") == "delegated_to_worker_self_heal"
    )
    assert enqueued_event["payload"]["heal_owned"] is True
    assert enqueued_event["payload"]["enqueued_by_web"] is True

    repair_owned_task = manager.get_task("z-repair-owned")
    assert repair_owned_task is not None
    assert repair_owned_task["status"] == "running"
    assert repair_owned_task["current_stage"] == "delegated_to_worker_self_heal"
    repair_delegated_event = next(
        e
        for e in reversed(repair_owned_task["progress_events"])
        if e.get("stage") == "delegated_to_worker_self_heal"
    )
    assert repair_delegated_event["payload"]["heal_owned"] is True
    assert repair_delegated_event["payload"].get("enqueued_by_web") is not True


def test_auto_resume_zombies_marks_unclaimed_when_redis_unreachable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If Redis can't be scanned (e.g. startup race, network blip), web
    should not spawn a competing web thread, but it also must not show a
    fake-running task that later fails by watchdog.
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
    monkeypatch.setattr(
        web_server,
        "_fetch_heal_owned_slugs_by_kind",
        lambda _url, _heal_kind: set(),
    )

    invocations: list[str] = []

    def fake_worker(self: object, task_id: str, payload: dict[str, object]) -> None:
        invocations.append(task_id)

    monkeypatch.setattr(web_server.WebTaskManager, "_run_autowrite_worker", fake_worker)

    delegated = manager.auto_resume_zombies(redis_url="redis://stub")
    assert delegated == []
    import time as _time

    _time.sleep(0.1)
    # Critical: NO thread should be spawned even when we can't confirm
    # worker ownership.
    assert invocations == []

    task = manager.get_task("z-fallback")
    assert task is not None
    assert task["status"] == "incomplete"
    assert task["current_stage"] == "auto_resume_not_claimed"


def test_manual_resume_delegates_when_worker_heal_owns_slug(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Manual resume must not spawn a web thread for a slug already owned
    by worker self-heal. Otherwise the two paths race on the same project row.
    """
    persist_path = _write_persisted_tasks(
        tmp_path,
        [
            {
                "task_id": "failed-heal-owned",
                "task_type": "autowrite",
                "status": "failed",
                "created_at": "2026-01-01T00:00:00+00:00",
                "updated_at": "2026-01-01T00:05:00+00:00",
                "project_slug": "novel-a",
                "title": "A",
                "current_stage": "failed",
                "progress_events": [],
                "payload": {"slug": "novel-a", "title": "A"},
            },
        ],
    )
    manager = web_server.WebTaskManager(persist_path=persist_path)
    invocations: list[str] = []

    def fake_worker(self: object, task_id: str, payload: dict[str, object]) -> None:
        invocations.append(task_id)

    monkeypatch.setattr(web_server.WebTaskManager, "_run_autowrite_worker", fake_worker)

    resumed = manager.resume_autowrite_task(
        "failed-heal-owned",
        {"slug": "novel-a", "title": "A"},
        delegate_to_self_heal=True,
        heal_owned=True,
    )

    assert isinstance(resumed, dict)
    assert resumed["status"] == "running"
    assert resumed["current_stage"] == "delegated_to_worker_self_heal"
    assert invocations == []
    task = manager.get_task("failed-heal-owned")
    assert task is not None
    assert task["progress_events"][-1]["stage"] == "delegated_to_worker_self_heal"
    assert task["progress_events"][-1]["payload"]["heal_owned"] is True


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
                "arq:job:repair:heal:novel-r",
                "arq:queue",  # noise
                "arq:result:autowrite:heal:novel-d",  # different prefix, ignored
            ]

        def scan_iter(self, match: str, count: int = 200):
            prefix = match.rstrip("*")
            return (k for k in self._keys if k.startswith(prefix))

    fake_client = _FakeRedis()

    class _FakeRedisModule:
        @staticmethod
        def from_url(_url: str, **_kwargs: object) -> _FakeRedis:
            return fake_client

    import sys as _sys

    monkeypatch.setitem(_sys.modules, "redis", _FakeRedisModule)

    slugs = web_server._fetch_heal_owned_slugs("redis://stub")
    assert slugs == {"novel-a", "novel-b", "novel-c", "novel-r"}
    autowrite_slugs = web_server._fetch_heal_owned_slugs_by_kind(
        "redis://stub",
        "autowrite",
    )
    repair_slugs = web_server._fetch_heal_owned_slugs_by_kind(
        "redis://stub",
        "repair",
    )
    assert autowrite_slugs == {"novel-a", "novel-b", "novel-c"}
    assert repair_slugs == {"novel-r"}


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

    slugs = web_server._fetch_heal_owned_slugs("redis://stub")
    assert slugs == set()


def test_sync_progress_ignores_stale_redis_progress_without_active_arq_job(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = web_server.WebTaskManager()
    task = web_server.WebTaskState(
        task_id="task-a",
        task_type="autowrite",
        status="running",
        created_at="2026-05-13T00:00:00+00:00",
        updated_at="2026-05-13T01:00:00+00:00",
        project_slug="novel-a",
        title="Novel A",
        current_stage="delegated_to_worker_self_heal",
        progress_events=[],
    )
    with manager._lock:
        manager._tasks[task.task_id] = task

    class _FakeRedis:
        lrange_called = False

        def exists(self, *_keys: str) -> int:
            return 0

        def zscore(self, _key: str, _member: str) -> None:
            return None

        def lrange(self, *_args: object) -> list[str]:
            self.lrange_called = True
            return ['{"ts": 1778648419.8, "message": "story_bible_refresh_started", "data": {}}']

        def close(self) -> None:
            return None

    fake_client = _FakeRedis()

    class _FakeRedisModule:
        @staticmethod
        def from_url(_url: str, **_kwargs: object) -> _FakeRedis:
            return fake_client

    import sys as _sys

    monkeypatch.setitem(_sys.modules, "redis", _FakeRedisModule)

    updated = manager.sync_progress_from_worker_redis("redis://stub")

    assert updated == 0
    assert fake_client.lrange_called is False
    assert manager.get_task("task-a")["current_stage"] == "delegated_to_worker_self_heal"


def test_sync_progress_merges_when_arq_job_is_active(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = web_server.WebTaskManager()
    task = web_server.WebTaskState(
        task_id="task-a",
        task_type="autowrite",
        status="running",
        created_at="2026-05-13T00:00:00+00:00",
        updated_at="2026-05-13T01:00:00+00:00",
        project_slug="novel-a",
        title="Novel A",
        current_stage="delegated_to_worker_self_heal",
        progress_events=[],
    )
    with manager._lock:
        manager._tasks[task.task_id] = task

    class _FakeRedis:
        def exists(self, *_keys: str) -> int:
            return 1

        def zscore(self, _key: str, _member: str) -> None:
            return None

        def lrange(self, *_args: object) -> list[str]:
            return ['{"ts": 1778648419.8, "message": "story_bible_refresh_started", "data": {}}']

        def close(self) -> None:
            return None

    class _FakeRedisModule:
        @staticmethod
        def from_url(_url: str, **_kwargs: object) -> _FakeRedis:
            return _FakeRedis()

    import sys as _sys

    monkeypatch.setitem(_sys.modules, "redis", _FakeRedisModule)

    updated = manager.sync_progress_from_worker_redis("redis://stub")

    assert updated == 1
    assert manager.get_task("task-a")["current_stage"] == "story_bible_refresh_started"


def test_sync_progress_merges_repair_heal_into_autowrite_card(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = web_server.WebTaskManager()
    task = web_server.WebTaskState(
        task_id="task-book",
        task_type="autowrite",
        status="incomplete",
        created_at="2026-05-13T00:00:00+00:00",
        updated_at="2026-05-13T01:00:00+00:00",
        project_slug="novel-repair",
        title="Novel Repair",
        current_stage="machine_repair_required",
        error="old gate",
        progress_events=[],
    )
    with manager._lock:
        manager._tasks[task.task_id] = task

    class _FakeRedis:
        lrange_key: str | None = None

        def exists(self, *keys: str) -> int:
            return int(any(key == "arq:in-progress:repair:heal:novel-repair" for key in keys))

        def zscore(self, _key: str, member: str) -> float | None:
            return 1.0 if member == "repair:heal:novel-repair" else None

        def lrange(self, key: str, *_args: object) -> list[str]:
            self.lrange_key = key
            return [
                '{"ts": 1778648419.8, "message": "repair_chapter_74", '
                '"data": {"chapter_number": 74}}'
            ]

        def close(self) -> None:
            return None

    fake_client = _FakeRedis()

    class _FakeRedisModule:
        @staticmethod
        def from_url(_url: str, **_kwargs: object) -> _FakeRedis:
            return fake_client

    import sys as _sys

    monkeypatch.setitem(_sys.modules, "redis", _FakeRedisModule)

    updated = manager.sync_progress_from_worker_redis("redis://stub")

    synced = manager.get_task("task-book")
    assert updated == 1
    assert synced is not None
    assert synced["status"] == "running"
    assert synced["current_stage"] == "repair_chapter_74"
    assert synced["error"] is None
    assert fake_client.lrange_key == "task:repair:heal:novel-repair:progress"


def test_sync_progress_marks_worker_generation_gate_block_auto_continue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = web_server.WebTaskManager()
    task = web_server.WebTaskState(
        task_id="task-gate",
        task_type="autowrite",
        status="running",
        created_at="2026-05-13T00:00:00+00:00",
        updated_at="2026-05-13T01:00:00+00:00",
        project_slug="novel-gate",
        title="Novel Gate",
        current_stage="delegated_to_worker_self_heal",
        progress_events=[],
    )
    with manager._lock:
        manager._tasks[task.task_id] = task

    class _FakeRedis:
        def exists(self, *_keys: str) -> int:
            return 1

        def zscore(self, _key: str, _member: str) -> None:
            return None

        def lrange(self, *_args: object) -> list[str]:
            return [
                '{"ts": 1778648419.8, "message": "blocked_generation_gate", '
                '"data": {"reason": "story_bible_gate_failed", "error": "L2 bible gate failed"}}'
            ]

        def close(self) -> None:
            return None

    class _FakeRedisModule:
        @staticmethod
        def from_url(_url: str, **_kwargs: object) -> _FakeRedis:
            return _FakeRedis()

    import sys as _sys

    monkeypatch.setitem(_sys.modules, "redis", _FakeRedisModule)

    updated = manager.sync_progress_from_worker_redis("redis://stub")

    synced = manager.get_task("task-gate")
    assert updated == 1
    assert synced is not None
    assert synced["status"] == "incomplete"
    assert synced["current_stage"] == "repairable_auto_continue_pending"
    assert synced["error"] == "L2 bible gate failed"


def test_sync_progress_normalizes_legacy_manual_gate_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = web_server.WebTaskManager()
    task = web_server.WebTaskState(
        task_id="task-legacy-gate",
        task_type="autowrite",
        status="running",
        created_at="2026-05-13T00:00:00+00:00",
        updated_at="2026-05-13T01:00:00+00:00",
        project_slug="novel-legacy-gate",
        title="Novel Legacy Gate",
        current_stage="delegated_to_worker_self_heal",
        progress_events=[],
    )
    with manager._lock:
        manager._tasks[task.task_id] = task

    legacy_stage = "waiting" + "_human"
    legacy_reason = "project_repair_requires_attention"

    class _FakeRedis:
        def exists(self, *_keys: str) -> int:
            return 1

        def zscore(self, _key: str, _member: str) -> None:
            return None

        def lrange(self, *_args: object) -> list[str]:
            return [
                json.dumps(
                    {
                        "ts": 1778648419.8,
                        "message": legacy_stage,
                        "data": {"reason": legacy_reason},
                    }
                )
            ]

        def close(self) -> None:
            return None

    class _FakeRedisModule:
        @staticmethod
        def from_url(_url: str, **_kwargs: object) -> _FakeRedis:
            return _FakeRedis()

    import sys as _sys

    monkeypatch.setitem(_sys.modules, "redis", _FakeRedisModule)

    updated = manager.sync_progress_from_worker_redis("redis://stub")

    synced = manager.get_task("task-legacy-gate")
    assert updated == 1
    assert synced is not None
    assert synced["status"] == "incomplete"
    assert synced["current_stage"] == "repairable_auto_continue_pending"
    assert synced["progress_events"][-1]["stage"] == "repairable_auto_continue_pending"
    assert synced["progress_events"][-1]["payload"]["reason"] == (
        "project_repair_requires_machine_repair"
    )


def test_sync_progress_marks_repair_completed_with_attention_incomplete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = web_server.WebTaskManager()
    task = web_server.WebTaskState(
        task_id="task-repair-attention",
        task_type="autowrite",
        status="running",
        created_at="2026-05-13T00:00:00+00:00",
        updated_at="2026-05-13T01:00:00+00:00",
        project_slug="novel-attention",
        title="Novel Attention",
        current_stage="delegated_to_worker_self_heal",
        progress_events=[],
    )
    with manager._lock:
        manager._tasks[task.task_id] = task

    class _FakeRedis:
        def exists(self, *_keys: str) -> int:
            return 1

        def zscore(self, _key: str, _member: str) -> None:
            return None

        def lrange(self, *_args: object) -> list[str]:
            return [
                '{"ts": 1778648419.8, "message": "project_repair_completed", '
                '"data": {"requires_human_review": true}}'
            ]

        def close(self) -> None:
            return None

    class _FakeRedisModule:
        @staticmethod
        def from_url(_url: str, **_kwargs: object) -> _FakeRedis:
            return _FakeRedis()

    import sys as _sys

    monkeypatch.setitem(_sys.modules, "redis", _FakeRedisModule)

    updated = manager.sync_progress_from_worker_redis("redis://stub")

    synced = manager.get_task("task-repair-attention")
    assert updated == 1
    assert synced is not None
    assert synced["status"] == "incomplete"
    assert synced["current_stage"] == "machine_repair_required"
    assert synced["error"] == "Task reached a machine-repair or attention gate."


def test_sync_progress_marks_autowrite_completed_with_attention_incomplete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = web_server.WebTaskManager()
    task = web_server.WebTaskState(
        task_id="task-autowrite-attention",
        task_type="autowrite",
        status="running",
        created_at="2026-05-13T00:00:00+00:00",
        updated_at="2026-05-13T01:00:00+00:00",
        project_slug="novel-attention",
        title="Novel Attention",
        current_stage="delegated_to_worker_self_heal",
        progress_events=[],
    )
    with manager._lock:
        manager._tasks[task.task_id] = task

    class _FakeRedis:
        def exists(self, *_keys: str) -> int:
            return 1

        def zscore(self, _key: str, _member: str) -> None:
            return None

        def lrange(self, *_args: object) -> list[str]:
            return [
                '{"ts": 1778648419.8, "message": "autowrite_completed", '
                '"data": {"requires_human_review": true, "final_verdict": "attention"}}'
            ]

        def close(self) -> None:
            return None

    class _FakeRedisModule:
        @staticmethod
        def from_url(_url: str, **_kwargs: object) -> _FakeRedis:
            return _FakeRedis()

    import sys as _sys

    monkeypatch.setitem(_sys.modules, "redis", _FakeRedisModule)

    updated = manager.sync_progress_from_worker_redis("redis://stub")

    synced = manager.get_task("task-autowrite-attention")
    assert updated == 1
    assert synced is not None
    assert synced["status"] == "incomplete"
    assert synced["current_stage"] == "machine_repair_required"
    assert synced["error"] == "Task reached a machine-repair or attention gate."


def test_mark_completed_keeps_attention_result_incomplete() -> None:
    manager = web_server.WebTaskManager()
    task = web_server.WebTaskState(
        task_id="task-direct-attention",
        task_type="autowrite",
        status="running",
        created_at="2026-05-13T00:00:00+00:00",
        updated_at="2026-05-13T01:00:00+00:00",
        project_slug="novel-attention",
        title="Novel Attention",
        current_stage="running",
        progress_events=[],
    )
    with manager._lock:
        manager._tasks[task.task_id] = task

    manager._mark_completed(
        "task-direct-attention",
        {"requires_human_review": True, "final_verdict": "attention"},
    )

    synced = manager.get_task("task-direct-attention")
    assert synced is not None
    assert synced["status"] == "incomplete"
    assert synced["current_stage"] == "machine_repair_required"


def test_sync_progress_keeps_intermediate_attention_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = web_server.WebTaskManager()
    task = web_server.WebTaskState(
        task_id="task-repair-running",
        task_type="autowrite",
        status="incomplete",
        created_at="2026-05-13T00:00:00+00:00",
        updated_at="2026-05-13T01:00:00+00:00",
        project_slug="novel-repair-running",
        title="Novel Repair Running",
        current_stage="machine_repair_required",
        error="old gate",
        progress_events=[],
    )
    with manager._lock:
        manager._tasks[task.task_id] = task

    class _FakeRedis:
        def exists(self, *_keys: str) -> int:
            return 1

        def zscore(self, _key: str, _member: str) -> None:
            return None

        def lrange(self, *_args: object) -> list[str]:
            return [
                '{"ts": 1778648419.8, "message": "project_repair_review_completed", '
                '"data": {"verdict": "attention"}}'
            ]

        def close(self) -> None:
            return None

    class _FakeRedisModule:
        @staticmethod
        def from_url(_url: str, **_kwargs: object) -> _FakeRedis:
            return _FakeRedis()

    import sys as _sys

    monkeypatch.setitem(_sys.modules, "redis", _FakeRedisModule)

    updated = manager.sync_progress_from_worker_redis("redis://stub")

    synced = manager.get_task("task-repair-running")
    assert updated == 1
    assert synced is not None
    assert synced["status"] == "running"
    assert synced["current_stage"] == "project_repair_review_completed"
    assert synced["error"] is None


def test_sync_progress_prefers_active_autowrite_over_finished_repair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = web_server.WebTaskManager()
    task = web_server.WebTaskState(
        task_id="task-active-autowrite",
        task_type="autowrite",
        status="incomplete",
        created_at="2026-05-13T00:00:00+00:00",
        updated_at="2026-05-13T01:00:00+00:00",
        project_slug="novel-active",
        title="Novel Active",
        current_stage="machine_repair_required",
        error="old repair result",
        progress_events=[],
    )
    with manager._lock:
        manager._tasks[task.task_id] = task

    class _FakeRedis:
        def exists(self, *keys: str) -> int:
            active_keys = {
                "arq:in-progress:autowrite:heal:novel-active",
                "arq:result:repair:heal:novel-active",
            }
            return int(any(key in active_keys for key in keys))

        def zscore(self, _key: str, _member: str) -> None:
            return None

        def lrange(self, key: str, *_args: object) -> list[str]:
            if key == "task:autowrite:heal:novel-active:progress":
                return [
                    '{"ts": 1778648428.1, "message": "chapter_pipeline_started", '
                    '"data": {"chapter_number": 371}}'
                ]
            if key == "task:repair:heal:novel-active:progress":
                return [
                    '{"ts": 1778648433.5, "message": "machine_blocked", '
                    '"data": {"reason": "old repair result"}}'
                ]
            return []

        def close(self) -> None:
            return None

    class _FakeRedisModule:
        @staticmethod
        def from_url(_url: str, **_kwargs: object) -> _FakeRedis:
            return _FakeRedis()

    import sys as _sys

    monkeypatch.setitem(_sys.modules, "redis", _FakeRedisModule)

    updated = manager.sync_progress_from_worker_redis("redis://stub")

    synced = manager.get_task("task-active-autowrite")
    assert updated == 1
    assert synced is not None
    assert synced["status"] == "running"
    assert synced["current_stage"] == "chapter_pipeline_started"
    assert synced["error"] is None


def test_sync_progress_ignores_stale_result_after_resume(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = web_server.WebTaskManager()
    task = web_server.WebTaskState(
        task_id="task-resumed",
        task_type="autowrite",
        status="running",
        created_at="2026-05-18T09:00:00+00:00",
        updated_at="2026-05-18T10:00:00+00:00",
        project_slug="novel-resumed",
        title="Novel Resumed",
        current_stage="chapter_pipeline_started",
        error=None,
        progress_events=[
            {
                "timestamp": "2026-05-18T10:00:00+00:00",
                "stage": "resume_requested",
                "payload": {},
            }
        ],
    )
    with manager._lock:
        manager._tasks[task.task_id] = task

    class _FakeRedis:
        def exists(self, *keys: str) -> int:
            if "arq:result:repair:heal:novel-resumed" in keys:
                return 1
            return 0

        def zscore(self, _key: str, _member: str) -> None:
            return None

        def lrange(self, *_args: object) -> list[str]:
            return [
                '{"ts": 1779097514.6, "message": "failed", "data": {"error": "old repair failure"}}'
            ]

        def close(self) -> None:
            return None

    class _FakeRedisModule:
        @staticmethod
        def from_url(_url: str, **_kwargs: object) -> _FakeRedis:
            return _FakeRedis()

    import sys as _sys

    monkeypatch.setitem(_sys.modules, "redis", _FakeRedisModule)

    updated = manager.sync_progress_from_worker_redis("redis://stub")

    synced = manager.get_task("task-resumed")
    assert updated == 0
    assert synced is not None
    assert synced["status"] == "running"
    assert synced["current_stage"] == "chapter_pipeline_started"
    assert synced["error"] is None


def test_push_progress_resurrects_failed_task_on_new_nonterminal_progress() -> None:
    manager = web_server.WebTaskManager()
    task = web_server.WebTaskState(
        task_id="task-progress",
        task_type="autowrite",
        status="failed",
        created_at="2026-05-18T09:00:00+00:00",
        updated_at="2026-05-18T09:30:00+00:00",
        project_slug="novel-progress",
        title="Novel Progress",
        current_stage="failed",
        error="old failure",
        progress_events=[],
    )
    with manager._lock:
        manager._tasks[task.task_id] = task

    manager._push_progress(
        "task-progress",
        "chapter_pipeline_started",
        {"chapter_number": 2},
    )

    synced = manager.get_task("task-progress")
    assert synced is not None
    assert synced["status"] == "running"
    assert synced["current_stage"] == "chapter_pipeline_started"
    assert synced["error"] is None


def test_sync_progress_resurrects_failed_task_when_worker_heal_is_active(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = web_server.WebTaskManager()
    task = web_server.WebTaskState(
        task_id="task-resume",
        task_type="autowrite",
        status="failed",
        created_at="2026-05-13T00:00:00+00:00",
        updated_at="2026-05-13T01:00:00+00:00",
        project_slug="novel-resume",
        title="Novel Resume",
        current_stage="failed",
        error="old failure",
        progress_events=[],
    )
    with manager._lock:
        manager._tasks[task.task_id] = task

    class _FakeRedis:
        def exists(self, *_keys: str) -> int:
            return 1

        def zscore(self, _key: str, _member: str) -> None:
            return None

        def lrange(self, *_args: object) -> list[str]:
            return [
                '{"ts": 1778648421.1, "message": "volume_planning_started", '
                '"data": {"volume_number": 2}}'
            ]

        def close(self) -> None:
            return None

    class _FakeRedisModule:
        @staticmethod
        def from_url(_url: str, **_kwargs: object) -> _FakeRedis:
            return _FakeRedis()

    import sys as _sys

    monkeypatch.setitem(_sys.modules, "redis", _FakeRedisModule)

    updated = manager.sync_progress_from_worker_redis("redis://stub")

    synced = manager.get_task("task-resume")
    assert updated == 1
    assert synced is not None
    assert synced["status"] == "running"
    assert synced["current_stage"] == "volume_planning_started"
    assert synced["error"] is None


def test_watchdog_rescues_delegated_task_when_worker_job_active(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    persist_path = tmp_path / ".web_tasks.json"
    manager = web_server.WebTaskManager(persist_path=persist_path)
    task = web_server.WebTaskState(
        task_id="task-a",
        task_type="autowrite",
        status="running",
        created_at="2026-05-13T00:00:00+00:00",
        updated_at="2026-05-13T01:00:00+00:00",
        project_slug="novel-a",
        title="Novel A",
        current_stage="delegated_to_worker_self_heal",
        progress_events=[],
    )
    with manager._lock:
        manager._tasks[task.task_id] = task
        manager._save_to_disk()

    class _Settings:
        class redis:
            url = "redis://stub"

    class _FakeRedis:
        def exists(self, *_keys: str) -> int:
            return 1

        def zscore(self, _key: str, _member: str) -> None:
            return None

        def close(self) -> None:
            return None

    class _FakeRedisModule:
        @staticmethod
        def from_url(_url: str, **_kwargs: object) -> _FakeRedis:
            return _FakeRedis()

    import sys as _sys

    monkeypatch.setitem(_sys.modules, "redis", _FakeRedisModule)
    monkeypatch.setattr(web_server, "load_settings", lambda: _Settings())

    assert manager.watchdog_sweep(stale_after_seconds=1) == 0
    rescued = manager.get_task("task-a")
    assert rescued is not None
    assert rescued["status"] == "running"
    assert rescued["current_stage"] == "delegated_to_worker_self_heal"


def test_watchdog_marks_unowned_delegated_task_incomplete(tmp_path: Path) -> None:
    manager = web_server.WebTaskManager(persist_path=tmp_path / ".web_tasks.json")
    task = web_server.WebTaskState(
        task_id="task-a",
        task_type="autowrite",
        status="running",
        created_at="2026-05-13T00:00:00+00:00",
        updated_at="2026-05-13T01:00:00+00:00",
        project_slug="novel-a",
        title="Novel A",
        current_stage="delegated_to_worker_self_heal",
        progress_events=[],
    )
    with manager._lock:
        manager._tasks[task.task_id] = task

    assert manager.watchdog_sweep(stale_after_seconds=1) == 0
    incomplete = manager.get_task("task-a")
    assert incomplete is not None
    assert incomplete["status"] == "incomplete"
    assert incomplete["current_stage"] == "auto_resume_not_claimed"


def test_watchdog_preserves_machine_repair_gate_as_incomplete(tmp_path: Path) -> None:
    manager = web_server.WebTaskManager(persist_path=tmp_path / ".web_tasks.json")
    task = web_server.WebTaskState(
        task_id="task-machine-repair",
        task_type="autowrite",
        status="running",
        created_at="2026-05-13T00:00:00+00:00",
        updated_at="2026-05-13T01:00:00+00:00",
        project_slug="novel-a",
        title="Novel A",
        current_stage="volume_planning_started",
        progress_events=[
            {
                "timestamp": 1778662128.667118,
                "stage": "chapter_pipeline_machine_repair_required",
                "payload": {"chapter_number": 491},
            },
        ],
    )
    with manager._lock:
        manager._tasks[task.task_id] = task

    assert manager.watchdog_sweep(stale_after_seconds=1) == 0
    incomplete = manager.get_task("task-machine-repair")
    assert incomplete is not None
    assert incomplete["status"] == "incomplete"
    assert incomplete["current_stage"] == "machine_repair_required"
    assert incomplete["progress_events"][-1]["stage"] == "machine_repair_required"


def test_query_bool_parses_truthy_values() -> None:
    assert web_server._query_bool("1") is True
    assert web_server._query_bool("true") is True
    assert web_server._query_bool("yes") is True
    assert web_server._query_bool("0") is False
    assert web_server._query_bool(None) is False


def test_compact_task_for_dashboard_trims_progress_events() -> None:
    events = [{"stage": f"stage-{index}", "payload": {}} for index in range(120)]
    task = {"task_id": "t1", "progress_events": events}
    compacted = web_server._compact_task_for_dashboard(task)
    assert len(compacted["progress_events"]) <= web_server._DASHBOARD_PROGRESS_EVENT_LIMIT
    assert compacted.get("progress_events_truncated") is True
    assert compacted["progress_events"][0]["stage"] == "stage-0"
    assert compacted["progress_events"][-1]["stage"] == "stage-119"


def test_web_task_state_record_event_splits_tiers() -> None:
    """Milestones survive the activity ring buffer; tier routing works."""
    task = web_server.WebTaskState(
        task_id="t1",
        task_type="autowrite",
        status="running",
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
    )

    # Flood the activity ring buffer well past its cap.
    for i in range(web_server._ACTIVITY_EVENT_CAP + 120):
        task.record_event("scene_activity", {"i": i})
    assert len(task.progress_events) == web_server._ACTIVITY_EVENT_CAP
    assert task.milestone_events == []

    # A stage in _MILESTONE_STAGES is mirrored to the durable axis.
    assert "methodology_selected" in web_server._MILESTONE_STAGES
    task.record_event("methodology_selected", {"framework": "F"})
    # An arbitrary stage can be forced onto the milestone axis.
    task.record_event("custom_event", {"reason": "x"}, milestone=True)
    assert [e["stage"] for e in task.milestone_events] == [
        "methodology_selected",
        "custom_event",
    ]

    # More activity must NOT evict the milestones.
    for i in range(web_server._ACTIVITY_EVENT_CAP + 50):
        task.record_event("noise", {"i": i})
    assert len(task.milestone_events) == 2
    assert len(task.progress_events) == web_server._ACTIVITY_EVENT_CAP


def test_web_task_state_to_dict_includes_milestones() -> None:
    task = web_server.WebTaskState(
        task_id="t2",
        task_type="autowrite",
        status="running",
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
    )
    task.record_event("methodology_selected", {"framework": "F"})
    payload = task.to_dict()
    assert "milestone_events" in payload
    assert payload["milestone_events"][0]["stage"] == "methodology_selected"


def test_push_progress_routes_tier_payload_hint() -> None:
    """A `_tier=milestone` payload hint promotes an otherwise-activity stage."""
    from bestseller.services.progress_context import TIER_KEY, TIER_MILESTONE

    manager = web_server.WebTaskManager(persist_path=None)
    with manager._lock:
        manager._tasks["task"] = web_server.WebTaskState(
            task_id="task",
            task_type="autowrite",
            status="running",
            created_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-01T00:00:00+00:00",
        )
    # Stage not in _MILESTONE_STAGES, but the deep emitter tagged it milestone.
    manager._push_progress("task", "anti_meta_gate_blocked", {TIER_KEY: TIER_MILESTONE})
    task = manager._tasks["task"]
    assert task.milestone_events[-1]["stage"] == "anti_meta_gate_blocked"

    # An ordinary activity event stays off the milestone axis.
    manager._push_progress("task", "scene_draft_review_evaluated", {"score": 7})
    assert all(e["stage"] != "scene_draft_review_evaluated" for e in task.milestone_events)
