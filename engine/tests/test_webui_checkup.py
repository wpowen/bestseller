"""The web consumer reads the independent checkup without running diagnostics."""

import json
from test_webui_contracts import WebFixture, put, snapshot


class CheckupTests(WebFixture):
    def test_missing_then_saved_then_replaced_report_is_read_without_writes(self):
        folder = self.book(status="complete")
        assert self.lib.detail("webui/story").get("checkup") is None
        report = dict(
            schema_version=1,
            reference_only=True,
            chapter_count=2,
            stages={"count": 1, "locations": ["港口"]},
            antagonists=[{"name": "对手", "mention_count": 2, "first": 1, "last": 2, "span": 2}],
            title_keywords={"repeated": {"船": [1, 2, 3]}},
            location_breaks={"count": 0, "unknown": [1]},
            format_residue={"total": 0},
            introductions={"missing": []},
            aliases={"issues": []},
            unknown=[],
        )
        put(folder / "book_checkup.json", report)
        before = snapshot(folder)
        code, _, body = self.request("/api/book?id=webui/story")
        assert code == 200
        assert json.loads(body)["checkup"] == report
        assert snapshot(folder) == before
        report["format_residue"]["total"] = 3
        put(folder / "book_checkup.json", report)
        assert self.lib.detail("webui/story")["checkup"]["format_residue"]["total"] == 3

    def test_selected_card_title_is_fallback_and_bad_report_stays_unknown(self):
        folder = self.book(status="complete")
        web = json.loads((folder / "web.json").read_text())
        web.pop("title")
        web["selected_card"] = {"title": "锁定的选题名"}
        put(folder / "web.json", web)
        put(folder / "book_checkup.json", "broken")
        assert self.lib.detail("webui/story")["meta"]["title"] == "锁定的选题名"
        assert self.lib.detail("webui/story")["checkup"] is None
