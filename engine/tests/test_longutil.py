import unittest

from bookengine.longutil import loose_object, memory_sections, paragraph_edits
from bookengine.longmemory import long_context


class LongUtilTests(unittest.TestCase):
    def test_yaml_and_json_numeric_aliases_without_changing_prose(self):
        value = loose_object(
            '```yaml\nsummary: 回到矿洞\nclock: {day: "2", minute: "540"}\ncharacters: {甲: {present: "false", location: 东矿}}\n```'
        )
        self.assertEqual(value["clock"], {"day": 2, "minute": 540})
        self.assertFalse(value["characters"]["甲"]["present"])
        self.assertEqual(loose_object('{"number":"10","title":"001"}')["title"], "001")

    def test_scalar_is_rejected_but_valid_list_is_usable(self):
        self.assertEqual(loose_object("- one\n- two"), ["one", "two"])
        for raw in ("只有正文", ""):
            with self.assertRaises(ValueError):
                loose_object(raw)

    def test_edits_ignore_duplicate_invalid_ids_but_preserve_valid_text(self):
        value = paragraph_edits(
            {
                "patches": [
                    {"paragraph_id": "2", "replacement": "走进矿洞。"},
                    {"paragraph": 2, "text": "重复"},
                    {"paragraph": 9, "text": "越界"},
                ]
            },
            "在门口。\n\n出发。",
        )
        self.assertEqual(value["edits"], [{"paragraph": 2, "text": "走进矿洞。"}])

    def test_oversized_late_state_cannot_displace_current_chapter(self):
        sections = memory_sections(
            {"number": 100, "goal": "完成选择"},
            "上章结尾",
            {"characters": {}, "objects": {"大": "字" * 50000}},
            [],
            {},
            {},
            "",
        )
        text = long_context(sections, 1000)
        self.assertLessEqual(len(text), 1000)
        self.assertIn("完成选择", text)
        self.assertIn("上章结尾", text)


if __name__ == "__main__":
    unittest.main()
