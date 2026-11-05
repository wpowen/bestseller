"""Public command, legacy adapter, and literal chapter round trips (no network)."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from zipfile import ZipFile, ZIP_STORED

from docx import Document
from bookengine.export import export

ENGINE = Path(__file__).resolve().parents[1]
FORMATS = "txt,md,docx,epub,fanqie"


def snapshot(root):
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in root.rglob("*")
        if p.is_file() and "exports" not in p.parts
    }


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.book = self.root / "source"
        self.book.mkdir()
        self.title = "盐井 & 火 <十章>"
        self.labels = [f"第{i}章 矿灯{i}" for i in range(1, 11)]
        self.bodies = [f"  第{i}回，灯未熄。\t\n\n“盐 & 火 <石>。”\n# 这是正文\n\n\n尾声。\n" for i in range(1, 11)]
        (self.book / "run.json").write_text(json.dumps({"status": "complete", "book": {"chapters": 10}}))
        (self.book / "book.md").write_text("# " + self.title + "\n\n")
        (self.book / "final").mkdir()
        for i, (label, body) in enumerate(zip(self.labels, self.bodies), 1):
            (self.book / f"chapter-{i:03d}.md").write_text("## " + label + "\n\n" + body + "\n\n")
            (self.book / f"chapter-{i:03d}.draft.md").write_text("初稿。\n")
            (self.book / f"final/chapter-{i:03d}.md").write_text(body)
            (self.book / f"final/chapter-{i:03d}.deai.json").write_text(
                json.dumps(dict(status="unchanged", complete=True))
            )

    def cli(self, *args, ok=True):
        result = subprocess.run(
            [sys.executable, "-m", "exporter", str(self.book), *args],
            env=dict(os.environ, PYTHONPATH=str(ENGINE)),
            capture_output=True,
            text=True,
        )
        if ok:
            self.assertEqual(result.returncode, 0, result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0)
        return result

    def test_public_ten_chapter_literal_roundtrip_and_toc(self):
        before = snapshot(self.book)
        result = self.cli("--formats", FORMATS)
        out = self.book / "exports" / "final"
        self.assertEqual(snapshot(self.book), before)
        self.assertEqual(Path(json.loads(result.stdout)["out"]), out.resolve())
        txt, md = [(out / ("book." + ext)).read_text() for ext in ("txt", "md")]
        for label in self.labels:
            self.assertIn(label, txt.split("\n\n")[1])
            self.assertIn(label, md.split("\n\n")[1])
        for i, (label, body) in enumerate(zip(self.labels, self.bodies), 1):
            self.assertEqual((out / f"chapter-{i:03d}.md").read_text(), "## " + label + "\n\n" + body + "\n\n")
            for text, prefix in ((txt, ""), (md, "## ")):
                tail = text.split("\n\n" + prefix + label + "\n\n", 1)[1]
                self.assertTrue(tail.startswith(body + "\n\n"))
        doc = Document(out / "book.docx")
        self.assertEqual(doc.paragraphs[0].text, self.title)
        starts = [i for i, p in enumerate(doc.paragraphs) if p.style.name == "Heading 1"]
        self.assertEqual([doc.paragraphs[i].text for i in starts], self.labels)
        for j, start in enumerate(starts):
            end = starts[j + 1] if j + 1 < len(starts) else len(doc.paragraphs)
            self.assertEqual("\n".join(p.text for p in doc.paragraphs[start + 1 : end]), self.bodies[j])
        self.assertEqual([p.text for p in doc.paragraphs[2 : starts[0]]], self.labels)
        with ZipFile(out / "book.epub") as z:
            self.assertEqual(z.namelist()[0], "mimetype")
            self.assertEqual(z.getinfo("mimetype").compress_type, ZIP_STORED)
            self.assertEqual(z.read("mimetype"), b"application/epub+zip")
            ns = {"x": "http://www.w3.org/1999/xhtml", "o": "http://www.idpf.org/2007/opf"}
            nav = ET.fromstring(z.read("OEBPS/nav.xhtml"))
            self.assertEqual([a.text for a in nav.findall(".//x:a", ns)], self.labels)
            for i, body in enumerate(self.bodies, 1):
                page = ET.fromstring(z.read(f"OEBPS/chapter-{i:03d}.xhtml"))
                self.assertEqual(page.find(".//x:h1", ns).text, self.labels[i - 1])
                self.assertEqual(page.find(".//x:p", ns).text, body)
            package = ET.fromstring(z.read("OEBPS/content.opf"))
            self.assertEqual(len(package.findall("o:spine/o:itemref", ns)), 10)
            paths = {x.get("id"): x.get("href") for x in package.findall("o:manifest/o:item", ns)}
            self.assertTrue(all("OEBPS/" + href in z.namelist() for href in paths.values()))

    def test_legacy_interface_and_artifact_names(self):
        out = self.root / "legacy"
        rows = [{"title": f"矿灯{i}"} for i in range(1, 11)]
        self.assertIsNone(export(out, self.title, rows, self.bodies))
        expected = "# " + self.title + "\n\n"
        for i, (label, body) in enumerate(zip(self.labels, self.bodies), 1):
            chapter = "## " + label + "\n\n" + body + "\n\n"
            expected += chapter
            self.assertEqual((out / f"chapter-{i:03d}.md").read_text(), chapter)
        self.assertEqual((out / "book.md").read_text(), expected)
        self.assertTrue((out / "book.docx").is_file())

    def test_draft_is_explicit_and_missing_final_never_falls_back(self):
        self.cli("--formats", "txt", "--version", "draft")
        self.assertEqual((self.book / "exports/draft/book.txt").read_text().count("初稿。"), 10)
        (self.book / "final/chapter-005.md").unlink()
        result = self.cli("--formats", "txt")
        self.assertEqual(json.loads(result.stdout)["missing_chapters"], [5])
        self.assertIn("缺少第 5 章", (self.book / "exports/final/book.txt").read_text())
        (self.book / "chapter-003.draft.md").unlink()
        self.cli("--formats", "txt", "--version", "draft", ok=False)

    def test_raw_final_bodies_and_checkpoint_titles(self):
        (self.book / "chapters").mkdir()
        for i, label in enumerate(self.labels, 1):
            (self.book / f"chapter-{i:03d}.md").unlink()
            (self.book / f"chapter-{i:03d}.body.md").write_text(self.bodies[i - 1])
            (self.book / f"chapters/{i:03d}.json").write_text(json.dumps({"number": i, "title": label}))
        self.cli("--formats", "txt", "--out", str(self.root / "download"))
        self.assertIn(self.labels[-1], (self.root / "download/book.txt").read_text())

    def test_fanqie_removes_markup_and_keeps_literal_punctuation(self):
        (self.book / "chapter-001.md").write_text(
            "## 第1章 灯/盐:*?\n\n# 灯\n**粗体**与_斜体_，`盐`。\n> 引用\n---\n[门](https://example.org)\n3 * 5 = 15，x_y，#号。\n\n"
        )
        (self.book / "final/chapter-001.md").write_text(
            (self.book / "chapter-001.md").read_text().split("\n\n", 1)[1][:-2]
        )
        self.cli("--formats", "fanqie")
        files = sorted((self.book / "exports/final/fanqie").glob("*.txt"))
        self.assertEqual(len(files), 10)
        self.assertIn("第1章", files[0].name)
        self.assertIn("灯_盐", files[0].name)
        self.assertEqual(
            files[0].read_text(), "第1章 灯/盐:*?\n\n灯\n粗体与斜体，盐。\n引用\n\n门\n3 * 5 = 15，x_y，#号。"
        )

    def test_output_cannot_overwrite_source_or_follow_symlinks(self):
        before = snapshot(self.book)
        for out in (self.book, self.book / "steps"):
            self.cli("--formats", "txt", "--out", str(out), ok=False)
        (self.book / "exports").symlink_to(self.book, target_is_directory=True)
        self.cli("--formats", "txt", ok=False)
        self.assertEqual(snapshot(self.book), before)

    def test_invalid_formats_and_partial_book_fail_before_output(self):
        self.cli("--formats", "pdf", ok=False)
        (self.book / "final/chapter-010.md").unlink()
        result = self.cli("--formats", "txt")
        self.assertEqual(json.loads(result.stdout)["missing_chapters"], [10])

    def test_polished_export_requires_every_completed_chapter(self):
        final = self.book / "final"
        for n in range(1, 11):
            (final / f"chapter-{n:03d}.md").write_text(f"润色{n}正文。")
            (final / f"chapter-{n:03d}.deai.json").write_text(json.dumps(dict(status="unchanged", complete=True)))
        self.cli("--version", "polished", "--formats", "txt")
        text = (self.book / "exports/polished/book.txt").read_text()
        self.assertIn("润色10正文。", text)
        self.assertNotIn("初稿", text)
        (final / "chapter-010.deai.json").write_text(json.dumps(dict(status="partial", complete=False)))
        result = self.cli("--version", "polished", "--formats", "epub")
        self.assertEqual(json.loads(result.stdout)["missing_chapters"], [10])

    def test_partial_then_complete_export_removes_obsolete_missing_notice(self):
        final = self.book / "final/chapter-010.md"
        body = final.read_bytes()
        final.unlink()
        self.cli("--formats", "md,fanqie")
        out = self.book / "exports/final"
        self.assertEqual(len(list((out / "fanqie").glob("*.txt"))), 10)
        self.assertTrue((out / "chapter-011.md").exists())
        final.write_bytes(body)
        self.cli("--formats", "md,fanqie")
        self.assertFalse((out / "chapter-011.md").exists())
        self.assertEqual(len(list((out / "fanqie").glob("*.txt"))), 10)
        self.assertFalse(any("导出说明" in p.name for p in (out / "fanqie").glob("*.txt")))


if __name__ == "__main__":
    unittest.main()
