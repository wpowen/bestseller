"""Refresh stays independent of raw response history and sees legacy costs."""

import os

from test_books_cache import book, get_json, http_library, ledger_row, put
from pathlib import Path
from book_cache import BookCache


def test_refresh_does_not_scan_raw_http_history(tmp_path, monkeypatch):
    root = tmp_path / "books"
    folder = book(root)
    history = folder / "final/.deai/001/http/stages"
    put(history / "large-history/response.json", {"text": "原始调用"})
    original = os.scandir

    def scan(path):
        if str(path).startswith(str(history.parent)):
            raise AssertionError("书库刷新不应遍历 HTTP 原始响应目录")
        return original(path)

    monkeypatch.setattr(os, "scandir", scan)
    with http_library(root) as server:
        payload = get_json(server)
        assert len(payload["books"]) == 1
        assert payload["books"][0]["calls"] == 3
        assert get_json(server) == payload


def test_legacy_final_ledger_update_refreshes_costs(tmp_path):
    root = tmp_path / "books"
    folder = book(root)
    ledger = folder / "final/http/ledger.json"
    put(ledger, [ledger_row("legacy")])
    with http_library(root) as server:
        before = get_json(server)
        put(ledger, [ledger_row("legacy"), ledger_row("new-call")])
        after = get_json(server)
        assert after["books"][0]["calls"] == before["books"][0]["calls"] + 1
        assert after["spending"] > before["spending"]


def test_listing_skips_draft_text_and_reader_still_loads_it(tmp_path, monkeypatch):
    root = tmp_path / "books"
    folder = book(root)
    original = Path.read_text
    reads = []

    def read(path, *args, **kwargs):
        if path.name.endswith(".body.md"):
            reads.append(path)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read)
    with http_library(root) as server:
        assert get_json(server)["books"][0]["written_chapters"] == 2
        assert reads == []
        detail = get_json(server, "/api/book?id=webui/story")
        assert detail["chapters"][0]["draft"] == original(folder / "chapter-001.body.md")
        assert reads


def test_single_file_deai_ledger_is_counted_and_refreshed(tmp_path):
    root = tmp_path / "books"
    folder = book(root)
    ledger = folder / "final/.deai/single/chapter-001/final/.deai/001/http/ledger.json"
    put(ledger, [ledger_row("single-file")])
    with http_library(root) as server:
        before = get_json(server)
        assert before["books"][0]["calls"] == 4
        put(ledger, [ledger_row("single-file"), ledger_row("single-file-recheck")])
        after = get_json(server)
        assert after["books"][0]["calls"] == 5
        assert after["spending"] > before["spending"]


def test_refresh_handles_entry_removed_after_directory_scan(tmp_path, monkeypatch):
    folder = book(tmp_path / "books")
    removed = folder / "chapter-999.body.md"
    put(removed, "等待移除的正文")
    original = os.scandir

    class Scan:
        def __enter__(self):
            self.source = original(folder)
            return self.entries()

        def entries(self):
            for entry in self.source:
                if entry.name == removed.name:
                    removed.unlink(missing_ok=True)
                yield entry

        def __exit__(self, *args):
            self.source.close()

    monkeypatch.setattr(os, "scandir", lambda path: Scan() if Path(path) == folder else original(path))
    cache = BookCache(lambda path: path)
    assert cache.get(folder, "value", lambda: "ok") == "ok"
