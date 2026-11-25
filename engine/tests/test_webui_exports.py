"""Public HTTP downloads consume the independent exporter in both reading modes."""

import io
import xml.etree.ElementTree as ET
import zipfile
from test_webui_contracts import WebFixture, put


class WebExportTests(WebFixture):
    def test_blank_chapter_titles_download_by_number_and_keep_final_text(self):
        folder = self.book(total=4)
        for n, title in enumerate(("", "  ", None), 1):
            put(folder / f"chapters/{n:03d}.json", {"title": title, "status": "complete"})
        status, _, data = self.download(fmt="md")
        self.assertEqual(status, 200)
        content = data.decode()
        for n in (1, 2, 3):
            self.assertIn(f"## 第{n}章\n\n定稿{n}", content)
        self.assertIn("## 第4章 潮声4\n\n定稿4", content)
        self.assertNotIn("草稿", content)

    def test_epub_and_fanqie_preserve_selected_version(self):
        self.book()
        for version, word, excluded in [("draft", "定稿", "草稿"), ("final", "定稿", "草稿")]:
            for fmt in ("epub", "fanqie"):
                with self.subTest(version=version, fmt=fmt):
                    status, headers, body = self.download(fmt=fmt, version=version)
                    self.assertEqual(status, 200)
                    with zipfile.ZipFile(io.BytesIO(body)) as archive:
                        self.assertIsNone(archive.testzip())
                        if fmt == "epub":
                            self.assertEqual(archive.read("mimetype"), b"application/epub+zip")
                            content = "".join(ET.fromstring(archive.read("OEBPS/chapter-001.xhtml")).itertext())
                        else:
                            names = archive.namelist()
                            self.assertEqual(len(names), 2)
                            self.assertTrue(all(n.startswith("fanqie/") and n.endswith(".txt") for n in names))
                            content = archive.read(names[0]).decode("utf-8")
                        self.assertIn(word + "1", content)
                        self.assertNotIn(excluded + "1", content)

    def test_incomplete_final_never_exports_epub_or_fanqie(self):
        folder = self.book()
        (folder / "final/chapter-002.md").unlink()
        for fmt in ("epub", "fanqie"):
            code, headers, body = self.download(fmt=fmt, version="final")
            self.assertEqual(code, 200)
            self.assertEqual(headers["X-Missing-Chapters"], "2")
            with zipfile.ZipFile(io.BytesIO(body)) as archive:
                self.assertIsNone(archive.testzip())
