"""Behavior-preserving split of test_webui_contracts.py."""

from support_webui_contracts import *


class HttpTestsPart1(HttpTests):
    def test_same_origin_json_reaches_queue_once(self):
        data = self.topic()
        status, headers, body = self.request(
            "/api/books", method="POST", data=data, headers={"Content-Type": "application/json; charset=utf-8"}
        )
        self.assertEqual(status, 200, body)
        self.assertIn("id", json.loads(body))
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.spawn.assert_not_called()

    def test_mutations_reject_wrong_or_missing_origin_host_and_non_json(self):
        data = self.topic()
        before = snapshot(self.lib.own)
        for fields in [
            {"Origin": None},
            {"Origin": "http://evil.invalid"},
            {"Host": "evil.invalid"},
            {"Host": "localhost:18765"},
            {"Content-Type": "text/plain"},
            {"Content-Type": None},
        ]:
            with self.subTest(headers=fields):
                status, _, _ = self.request("/api/books", method="POST", data=data, headers=fields)
                self.assertEqual(status, 403)
                self.assertEqual(snapshot(self.lib.own), before)
        self.spawn.assert_not_called()

    def test_malformed_json_array_empty_and_oversized_requests_are_rejected(self):
        for raw in [b"{bad", b"[]", b"null", b"", b" " * 16385]:
            with self.subTest(size=len(raw)):
                status, _, _ = self.request("/api/books", method="POST", raw=raw)
                self.assertEqual(status, 400)
        self.spawn.assert_not_called()

    def test_obsolete_budget_input_is_ignored_through_http(self):
        for value in [float("nan"), float("inf"), -float("inf"), "NaN"]:
            with self.subTest(value=value):
                status, _, body = self.request("/api/books", method="POST", data=dict(self.topic(), budget=value))
                self.assertEqual(status, 200)
                folder = self.lib.folder(json.loads(body)["id"])
                self.assertNotIn("budget", library.read(folder / "web.json"))
        self.spawn.assert_not_called()

    def test_get_rejects_host_rebinding(self):
        status, _, _ = self.request("/api/books", headers={"Host": "attacker.invalid"})
        self.assertEqual(status, 403)

    def test_download_rejects_sensitive_symlink_and_traversal_folders(self):
        normal = self.book()
        for key in ["private/book", "KEY-backup/book", "archive/d407/book"]:
            self.book(key)
        (self.lib.root / "alias").symlink_to(normal, target_is_directory=True)
        outside = self.root / "outside"
        put(outside / "book.md", "OUTSIDE_SENTINEL")
        (self.lib.root / "external").symlink_to(outside, target_is_directory=True)
        for key in [
            "private/book",
            "KEY-backup/book",
            "archive/d407/book",
            "alias",
            "external",
            "../outside",
            str(outside),
        ]:
            for fmt in ["md", "txt", "docx"]:
                with self.subTest(key=key, fmt=fmt):
                    status, _, body = self.download(key, fmt)
                    self.assertEqual(status, 400)
                    self.assertNotIn(b"OUTSIDE_SENTINEL", body)

    def test_export_file_symlink_cannot_download_external_bytes(self):
        folder = self.book()
        secret = self.root / "secret.txt"
        put(secret, "OUTSIDE_SENTINEL")
        for fmt in ["md", "docx"]:
            (folder / ("book." + fmt)).unlink()
            (folder / ("book." + fmt)).symlink_to(secret)
            with self.subTest(fmt=fmt):
                status, _, body = self.download(fmt=fmt)
                self.assertEqual(status, 200)
                self.assertNotIn(b"OUTSIDE_SENTINEL", body)

    def test_chapter_symlink_and_parent_symlink_cannot_leak_text(self):
        folder = self.book()
        secret = self.root / "secret.txt"
        put(secret, "OUTSIDE_SENTINEL")
        (folder / "chapter-001.body.md").unlink()
        (folder / "chapter-001.body.md").symlink_to(secret)
        status, _, body = self.request("/api/book?" + urlencode({"id": "webui/story"}))
        self.assertEqual(status, 400)
        self.assertNotIn(b"OUTSIDE_SENTINEL", body)
        (folder / "chapter-001.body.md").unlink()
        put(folder / "chapter-001.body.md", "正常")
        external = self.root / "external-final"
        put(external / "chapter-001.md", "OUTSIDE_SENTINEL")
        for p in (folder / "final").iterdir():
            p.unlink()
        (folder / "final").rmdir()
        (folder / "final").symlink_to(external, target_is_directory=True)
        status, _, body = self.download(version="final")
        self.assertEqual(status, 400)
        self.assertNotIn(b"OUTSIDE_SENTINEL", body)

    def test_no_generic_file_download_or_format_path_injection(self):
        self.book()
        for path in [
            "/download?" + urlencode({"id": "webui/story", "format": "../../test.env"}),
            "/download?" + urlencode({"id": "webui/story", "format": "json"}),
            "/download?" + urlencode({"id": "webui/story", "format": "txt", "version": "../run.json"}),
            "/test.env",
            "/run.json",
            "/../../test.env",
        ]:
            with self.subTest(path=path):
                status, _, _ = self.request(path)
                self.assertIn(status, (400, 404))

    def test_legacy_draft_query_downloads_only_final_outputs(self):
        folder = self.book()
        for fmt in ["md", "docx"]:
            with self.subTest(fmt=fmt):
                status, headers, body = self.download(fmt=fmt)
                self.assertEqual(status, 200)
                self.assertNotEqual(body, (folder / ("book." + fmt)).read_bytes())
                if fmt == "md":
                    self.assertIn("定稿1", body.decode())
                self.assertIn("book-final." + fmt, headers["Content-Disposition"])
                self.assertEqual(int(headers["Content-Length"]), len(body))

    def test_draft_txt_and_final_exports_never_mix_versions(self):
        self.book()
        for version, expected, excluded in [("draft", "定稿", "草稿"), ("final", "定稿", "草稿")]:
            with self.subTest(version=version):
                status, _, body = self.download(version=version)
                self.assertEqual(status, 200)
                content = body.decode("utf-8")
                self.assertIn(expected + "1", content)
                self.assertIn(expected + "2", content)
                self.assertNotIn(excluded, content)
                self.assertLess(content.index(expected + "1"), content.index(expected + "2"))

    def test_missing_final_never_silently_downloads_draft(self):
        folder = self.book()
        (folder / "final/chapter-002.md").unlink()
        for fmt in ["md", "txt", "docx"]:
            with self.subTest(fmt=fmt):
                status, headers, _ = self.download(fmt=fmt, version="final")
                self.assertEqual(status, 200)
                self.assertEqual(headers["X-Missing-Chapters"], "2")

    def test_retained_or_partial_polish_is_not_published_as_final(self):
        folder = self.book()
        for report in [
            {"status": "retained", "complete": False},
            {"status": "partial", "complete": False},
            {"status": "error"},
        ]:
            put(folder / "final/chapter-001.deai.json", report)
            with self.subTest(report=report):
                status, headers, _ = self.download(version="final")
                self.assertEqual(status, 200)
                self.assertEqual(headers["X-Missing-Chapters"], "1")

    def test_empty_book_cannot_download_fake_complete_final(self):
        self.book(total=0)
        status, _, _ = self.download(version="final")
        self.assertEqual(status, 400)

    def test_final_docx_is_valid_package_with_escaped_chinese_paragraphs(self):
        self.book()
        status, headers, body = self.download(fmt="docx", version="final")
        self.assertEqual(status, 200)
        self.assertIn("wordprocessingml", headers["Content-Type"])
        with zipfile.ZipFile(io.BytesIO(body)) as archive:
            self.assertIsNone(archive.testzip())
            for name in ["[Content_Types].xml", "_rels/.rels", "word/document.xml"]:
                ET.fromstring(archive.read(name))
            doc = ET.fromstring(archive.read("word/document.xml"))
            paragraphs = [
                "".join(p.itertext())
                for p in doc.findall(".//{http://schemas.openxmlformats.org/wordprocessingml/2006/main}p")
            ]
        text = "\n".join(paragraphs)
        self.assertIn("潮汐 <归途>", text)
        self.assertIn("定稿1：她听见潮声。<灯> & 船。", text)
        self.assertNotIn("草稿", text)

    def test_http_legacy_stopped_resume_preserves_run_and_state(self):
        folder = self.book(status="budget_paused")
        put(folder / "run.json", {"status": "stopped", "stop_reason": "budget"})
        put(folder / "state.json", {"checkpoint": 1})
        before = snapshot(folder)
        status, _, _ = self.request("/api/action", method="POST", data={"id": "webui/story", "action": "resume"})
        self.assertEqual(status, 200)
        self.assertEqual(
            {k: v for k, v in snapshot(folder).items() if k != "web.json"},
            {k: v for k, v in before.items() if k != "web.json"},
        )
        self.assertEqual(library.read(folder / "web.json")["status"], "queued")
        self.spawn.assert_not_called()
