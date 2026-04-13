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
