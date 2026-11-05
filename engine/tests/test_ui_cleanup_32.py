"""Public form limits and global settings; offline HTTP, no model calls."""

import json

from test_webui_contracts import WebFixture, library, snapshot


class CleanupTests(WebFixture):
    def test_new_topics_default_to_fifty_and_reject_short_or_fractional_lengths(self):
        for chapters in (5, 10, 19, 20.5, True, 2001):
            with self.subTest(chapters=chapters):
                before = snapshot(self.lib.own)
                status, _, _ = self.request(
                    "/api/topics", method="POST", data=dict(genre="cozy-mystery", chapters=chapters)
                )
                self.assertEqual(status, 400)
                self.assertEqual(snapshot(self.lib.own), before)
        result = self.queue.topic(dict(genre="cozy-mystery"))
        self.assertEqual(result["chapters"], 50)

    def test_words_reach_book_config_and_creation_preserves_global_setting(self):
        status, _, _ = self.request("/api/settings", method="POST", data={"jobs": 4})
        self.assertEqual(status, 200)
        data = self.topic()
        data.pop("jobs")
        status, _, body = self.request("/api/books", method="POST", data=dict(data, words_per_chapter=3100))
        self.assertEqual(status, 200, body)
        folder = self.lib.folder(json.loads(body)["id"])
        self.assertEqual(library.read(folder / "book.yaml")["words_per_chapter"], 3100)
        self.assertEqual(self.queue.jobs, 4)
        self.assertEqual(library.read(self.lib.own / ".queue.json"), {"jobs": 4})

    def test_invalid_words_do_not_create_book_or_consume_topic(self):
        data = self.topic()
        before = snapshot(self.lib.own)
        for words in (1499, 4001, 2300.5, True, "oops"):
            status, _, _ = self.request("/api/books", method="POST", data=dict(data, words_per_chapter=words))
            self.assertEqual(status, 400, words)
            self.assertEqual(snapshot(self.lib.own), before)
        result = self.queue.create(data)
        self.assertEqual(library.read(self.lib.folder(result["id"]) / "book.yaml")["words_per_chapter"], 2300)

    def test_global_settings_defaults_validation_origin_and_persistence(self):
        self.assertEqual(json.loads(self.request("/api/settings")[2]), {"jobs": 3})
        for jobs in (0, 5, 2.5, True, "bad"):
            before = snapshot(self.lib.own)
            self.assertEqual(self.request("/api/settings", method="POST", data={"jobs": jobs})[0], 400)
            self.assertEqual(snapshot(self.lib.own), before)
        self.assertEqual(
            self.request("/api/settings", method="POST", data={"jobs": 1}, headers={"Origin": "http://evil.invalid"})[
                0
            ],
            403,
        )
        self.assertEqual(self.request("/api/settings", method="POST", data={"jobs": 1})[0], 200)
        self.assertEqual(self.queue.jobs, 1)
        self.assertEqual(library.read(self.lib.own / ".queue.json"), {"jobs": 1})
        self.lib.read_only = True
        self.assertEqual(self.request("/api/settings", method="POST", data={"jobs": 2})[0], 403)
        self.assertEqual(self.queue.jobs, 1)
        self.lib.read_only = False
        self.spawn.assert_not_called()
