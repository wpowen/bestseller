from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

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
