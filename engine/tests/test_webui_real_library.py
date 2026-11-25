"""Default real-library regression; all mutations happen in disposable copies."""

import json
from pathlib import Path

from tools.real_library_smoke import smoke

FIXTURES = Path(__file__).parent / "fixtures/real_library"


def test_real_library_smoke():
    result = smoke(FIXTURES)
    assert result["books"] == 4
    assert result["max_books_ms"] < 500
    assert result["requests"] > 100
    assert result["models_started"] == 0
    assert result["fixture_unchanged"]


def test_smoke_accepts_new_book_without_bible_or_chapters(tmp_path):
    key = "webui/new-book"
    folder = tmp_path / key
    folder.mkdir(parents=True)
    (folder / "web.json").write_text(json.dumps({"title": "新书", "status": "queued"}), encoding="utf-8")
    (tmp_path / "manifest.json").write_text(json.dumps({"books": [{"id": key}]}), encoding="utf-8")
    result = smoke(tmp_path)
    assert result["books"] == 1 and result["reader_chapters"] == 0
    assert result["models_started"] == 0 and result["fixture_unchanged"]
    assert not (folder / "story_bible.yaml").exists()


def test_real_snapshots_are_bounded_and_exclude_raw_calls():
    paths = [p for p in FIXTURES.rglob("*") if p.is_file()]
    assert sum(p.stat().st_size for p in paths) <= 3 * 1024 * 1024
    assert not any(set(p.relative_to(FIXTURES).parts) & {"responses", "http"} for p in paths)
    manifest = json.loads((FIXTURES / "manifest.json").read_text())
    assert len(manifest["books"]) == 4
    assert any("retained" in b["report_statuses"] for b in manifest["books"])
