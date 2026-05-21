from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from bestseller.services.pipelines import ProjectRepairPauseError
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

    with pytest.raises(ValueError):
        web_server.resolve_project_artifact_path(
            _settings(tmp_path), "demo-story", "../project.md"
        )


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

    assert "content_mode === \"fanqie_short_story\"" in html
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
    assert (
        'class="length-btn fanqie-len-btn selected" data-length-key="fanqie-short-15k"'
        in html
    )
    assert (
        'class="length-btn fanqie-len-btn selected" data-length-key="fanqie-short-8k"'
        not in html
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


def test_quickstart_incomplete_tasks_are_not_labeled_stopped() -> None:
    html = web_server._QUICKSTART_HTML_PATH.read_text(encoding="utf-8")

    label_pos = html.index("label: '未完成'")
    start = html.rfind("if (status === 'incomplete')", 0, label_pos)
    end = html.index("if (status === 'completed')", start)
    incomplete_branch = html[start:end]

    assert "label: '未完成'" in incomplete_branch
    assert "自动恢复未接管" in incomplete_branch
    assert "已停止" not in incomplete_branch


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


def test_quickstart_task_passes_selected_creative_direction(monkeypatch: pytest.MonkeyPatch) -> None:
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
    assert (
        web_server._reader_chapter_availability("blocked", 2100) == "repair_in_progress"
    )
    assert (
        web_server._reader_chapter_availability("pending", 2100) == "repair_in_progress"
    )
    assert web_server._reader_chapter_availability("ok", 0) == "planned"


def test_project_autowrite_block_payload_explains_structural_repair_pause() -> None:
    project = SimpleNamespace(
        slug="demo-paused",
        title="Demo",
        metadata_json={
            "generation_resume_blocked_until_repair_audit": True,
            "production_pause_reason": "structural_repair_before_continuation",
        },
    )

    payload = web_server._project_autowrite_block_payload(project)

    assert payload is not None
    assert payload["blocked_structural_repair"] is True
    assert payload["project_slug"] == "demo-paused"
    assert payload["current_stage"] == "blocked_structural_repair"
    assert "structural_repair_before_continuation" in str(payload["error"])


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


def test_sync_progress_marks_worker_generation_gate_block_incomplete(
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
    assert synced["current_stage"] == "blocked_generation_gate"
    assert synced["error"] == "L2 bible gate failed"


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
                '{"ts": 1779097514.6, "message": "failed", '
                '"data": {"error": "old repair failure"}}'
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
