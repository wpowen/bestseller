"""Behavior-preserving split of test_webui_contracts.py."""

from support_webui_contracts import *


class LibraryTestsPart1(LibraryTests):
    def test_listing_excludes_sensitive_directories_and_symlinks(self):
        self.book("ordinary")
        self.book("nested/visible")
        for key in [
            "Private/book",
            "apiKEY/book",
            "d407-copy/book",
            ".hidden/book",
            "responses/book",
            "steps/book",
            "http/book",
            "final/book",
        ]:
            self.book(key)
        (self.lib.root / "linked").symlink_to(self.lib.root / "ordinary", target_is_directory=True)
        self.assertEqual({row["id"] for row in self.lib.listing()}, {"ordinary", "nested/visible"})

    def test_folder_rejects_traversal_absolute_sensitive_and_symlink_keys(self):
        target = self.book("ordinary")
        (self.lib.root / "alias").symlink_to(target, target_is_directory=True)
        for key in [
            "../outside",
            str(target),
            "",
            ".",
            "PRIVATE/book",
            "some/key-backup",
            "archive/D407/book",
            "alias",
            "alias/child",
        ]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.lib.folder(key)

    def test_detail_keeps_draft_final_titles_and_reports_separate(self):
        folder = self.book()
        before = snapshot(folder)
        detail = self.lib.detail("webui/story")
        self.assertEqual([r["number"] for r in detail["chapters"]], [1, 2])
        self.assertEqual(detail["chapters"][0]["title"], "潮声1")
        self.assertIn("草稿1", detail["chapters"][0]["draft"])
        self.assertIn("定稿1", detail["chapters"][0]["final"])
        self.assertTrue(detail["chapters"][0]["report"]["complete"])
        self.assertEqual(snapshot(folder), before)

    def test_legacy_chapters_remain_readable(self):
        folder = self.book(total=0)
        put(folder / "chapter-003.md", "旧版第三章正文")
        (row,) = self.lib.detail("webui/story")["chapters"]
        self.assertEqual((row["number"], row["draft"]), (3, "旧版第三章正文"))

    def test_offline_library_escapes_final_text_without_draft_fallback(self):
        folder = self.book()
        (folder / "final/chapter-002.md").unlink()
        before = snapshot(folder)
        page = self.lib.static()
        self.assertIn("定稿1", page)
        self.assertNotIn("草稿2", page)
        self.assertIn("本章正在去 AI，完成后显示", page)
        self.assertNotIn("草稿1", page)
        self.assertNotIn("<script>", page)
        self.assertIn("&lt;灯&gt;", page)
        self.assertEqual((self.lib.own / "LIBRARY.html").read_text("utf-8"), page)
        self.assertEqual(snapshot(folder), before)
