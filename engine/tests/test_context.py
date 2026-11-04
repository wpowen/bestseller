import unittest

from bookengine.context import limit_info, pack_context


class ContextTests(unittest.TestCase):
    def test_exact_budget_and_required_overflow(self):
        required = {"事实": "甲🙂"}
        self.assertEqual(pack_context(required, {}, 8), "## 事实\n甲🙂")
        with self.assertRaises(ValueError):
            pack_context(required, {}, 7)
        self.assertEqual(pack_context({}, {}, 0), "")

    def test_full_sections_and_insertion_priority(self):
        required, optional = {"R": "r"}, {"B": "bb", "A": "aa"}
        expected = "## R\nr\n\n## B\nbb"
        self.assertEqual(pack_context(required, optional, len(expected)), expected)
        self.assertEqual(pack_context(required, optional, 100), expected + "\n\n## A\naa")
        self.assertEqual(required, {"R": "r"})
        self.assertEqual(optional, {"B": "bb", "A": "aa"})

    def test_longest_paragraph_prefix_with_counted_marker(self):
        value = "甲🙂\n\n乙\n续行\n\n" + "丙" * 30
        expected = "## R\nr\n\n## O\n甲🙂\n\n乙\n续行\n\n[truncated]"
        actual = pack_context({"R": "r"}, {"O": value}, len(expected))
        self.assertEqual(actual, expected)
        shorter = pack_context({"R": "r"}, {"O": value}, len(expected) - 1)
        self.assertEqual(shorter, "## R\nr\n\n## O\n甲🙂\n\n[truncated]")

    def test_no_paragraph_fits_and_later_small_section(self):
        required = {"R": "r"}
        optional = {"long": "x" * 100, "S": "s"}
        self.assertEqual(pack_context(required, optional, 6), "## R\nr")
        self.assertEqual(pack_context(required, optional, 14), "## R\nr\n\n## S\ns")
        self.assertEqual(pack_context({}, {"O": "甲\n乙"}, 7), "")
        self.assertEqual(pack_context({}, {"O": "甲\n\n" + "乙" * 50}, 16), "")

    def test_whitespace_paragraph_break_preserves_text(self):
        value = " 甲\r\n续行\r\n \t\r\n" + "乙" * 50
        expected = "## O\n 甲\r\n续行\n\n[truncated]"
        self.assertEqual(pack_context({}, {"O": value}, len(expected)), expected)

    def test_unicode_cap_and_budget_report(self):
        required = {"界": "🙂" * 47_995}
        actual = pack_context(required, {"尾": "余"}, 48_001)
        self.assertEqual(len(actual), 48_000)
        self.assertEqual(actual, "## 界\n" + "🙂" * 47_995)
        with self.assertRaises(ValueError):
            pack_context({"界": "🙂" * 47_996}, {}, 48_001)
        self.assertEqual(limit_info("甲🙂", 2),
                         dict(chars=2, limit=2, remaining=0, fits=True))
        self.assertEqual(limit_info(actual + "界"),
                         dict(chars=48_001, limit=48_000, remaining=-1, fits=False))
        self.assertEqual(limit_info("", 50_000)["limit"], 48_000)

    def test_invalid_budgets(self):
        for limit in (-1, True, 1.5, "10"):
            with self.subTest(limit=limit):
                with self.assertRaises(ValueError):
                    pack_context({}, {}, limit)
                with self.assertRaises(ValueError):
                    limit_info("", limit)


if __name__ == "__main__":
    unittest.main()
