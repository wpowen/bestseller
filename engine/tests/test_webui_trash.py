"""Deletion is recoverable and records request provenance."""

import datetime as dt
import json
from unittest import mock

from test_webui_contracts import WebFixture, put, snapshot


class TrashTests(WebFixture):
    def test_delete_restore_preserves_every_byte_and_spending(self):
        folder = self.book(status="complete")
        put(folder / "calls.json", [{"usage": None}])
        before, cost = snapshot(folder), self.queue.spending()
        result = self.queue.action("webui/story", "delete")
        assert not folder.exists()
        assert result["id"].startswith("webui/.trash/")
        assert snapshot(self.lib.folder(result["id"])) == before
        assert not self.lib.listing()
        assert self.lib.trash()[0]["id"] == result["id"]
        self.queue.action(result["id"], "restore")
        assert snapshot(folder) == before
        assert self.queue.spending() == cost

    def test_http_logs_web_api_and_cli_sources(self):
        self.book(status="complete")
        for source, headers in [
            ("web", {"X-Bookweb-Source": "web", "User-Agent": "browser-test"}),
            ("api", {"User-Agent": "api-test"}),
        ]:
            code, _, _ = self.request(
                "/api/action",
                method="POST",
                data={"id": "webui/story", "action": "rename", "title": source},
                headers=headers,
            )
            assert code == 200
        self.queue.action("webui/story", "rename", title="cli")
        rows = [json.loads(line) for line in (self.lib.own / ".ops.log").read_text().splitlines()]
        assert [r["source"] for r in rows] == ["web", "api", "cli"]
        assert rows[0]["user_agent"] == "browser-test"
        assert rows[1]["client"] == "127.0.0.1"

    def test_expiry_only_purges_dated_trash_and_keeps_symlink_targets(self):
        import management

        self.book(status="complete")
        deleted = self.queue.action("webui/story", "delete")["id"]
        trash = self.lib.folder(deleted)
        old = trash.with_name("20000101T000000000000Z--story")
        trash.rename(old)
        outside = self.root / "outside"
        put(outside / "sentinel", "safe")
        (old.parent / "20000101T000000000000Z--link").symlink_to(outside, target_is_directory=True)
        management.cleanup_trash(self.queue)
        assert not old.exists()
        assert (outside / "sentinel").read_text() == "safe"
        assert self.lib.trash() == []

    def test_restore_conflict_is_lossless_and_trash_cannot_be_redeleted(self):
        folder = self.book(status="complete")
        key = self.queue.action("webui/story", "delete")["id"]
        self.book(status="complete")
        before = snapshot(self.lib.root)
        for action in ["restore", "archive", "delete", "rename"]:
            with self.assertRaises(ValueError):
                self.queue.action(key, action, title="x")
        assert snapshot(self.lib.root) == before
