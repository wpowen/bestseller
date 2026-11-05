import json
import unittest

from bookengine.text import (
    apply_paragraph_edits,
    apply_patches,
    chinese_count,
    number_paragraphs,
    pack,
    parse_json,
    prose,
)


class TextTests(unittest.TestCase):
    def test_json_tolerance(self):
        for raw in (
            '{"甲": [1,],}',
            '```json\n{"甲": [1]}\n```',
            "{'甲': [1],}",
            '<think>ignore</think>说明 {"甲": [1]} 完',
        ):
            with self.subTest(raw=raw):
                self.assertEqual(parse_json(raw), {"甲": [1]})
        self.assertEqual(parse_json('{"x": ",}", "y": true,}'), {"x": ",}", "y": True})
        self.assertEqual(parse_json('{"x":{"y":[1]}'), {"x": {"y": [1]}})
        self.assertEqual(parse_json('{"x": ["a", "b"},{"c": "}"}]}'), {"x": ["a", "b", {"c": "}"}]})
        with self.assertRaises(ValueError):
            parse_json('{"x": [1}')
        for raw in ("[]", "null", "{broken", "{'x': {1}}", None):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                parse_json(raw)

    def test_small_helpers(self):
        self.assertEqual(chinese_count("中文㐀，abc123🙂"), 3)
        self.assertEqual(prose(" \n## 第一章\n\n 正文 \n"), "正文")
        self.assertEqual(prose("正文\n## 场景"), "正文\n## 场景")
        self.assertEqual(json.loads(pack({"甲": ["乙"]})), {"甲": ["乙"]})
        self.assertIn("甲", pack({"甲": 1}))

    def test_patch_batch_uses_original_offsets(self):
        chapters = ["甲\n\n乙\n\n丙", "丁\n\n戊"]
        patches = [
            dict(chapter=1, original="甲", replacement="很长的新甲"),
            dict(chapter=1, original="丙", replacement="甲"),
            dict(chapter=2, original="丁", replacement="新丁"),
        ]
        self.assertEqual(apply_patches(chapters, patches), ["很长的新甲\n\n乙\n\n甲", "新丁\n\n戊"])
        self.assertEqual(chapters[0], "甲\n\n乙\n\n丙")
        self.assertEqual(apply_patches(chapters, []), chapters)
        deletion = dict(chapter=1, original="乙", replacement="")
        self.assertEqual(apply_patches(chapters, [deletion])[0], "甲\n\n\n\n丙")
        patch = dict(chapter=1, original="甲\n\n乙", replacement="新段")
        self.assertEqual(apply_patches(chapters, [patch])[0], "新段\n\n丙")

    def test_invalid_patches(self):
        base = dict(chapter=1, original="甲", replacement="新")
        for change in (
            {"chapter": 0},
            {"chapter": 2},
            {"chapter": True},
            {"chapter": "1"},
            {"original": ""},
            {"original": "不存在"},
            {"original": "甲\n\n乙\n\n丙"},
            {"replacement": " \n"},
            {"replacement": None},
        ):
            with self.subTest(change=change), self.assertRaises(ValueError):
                apply_patches(["甲\n\n乙\n\n丙"], [dict(base, **change)])
        for text, original in [
            ("甲甲\n\n乙", "甲"),
            ("甲\n\n甲", "甲"),
            ("甲乙\n\n丙", "甲"),
            ("甲\n乙\n\n丙", "乙"),
            ("甲\n", "甲"),
        ]:
            with self.subTest(text=text), self.assertRaises(ValueError):
                apply_patches([text], [dict(base, original=original)])
        for originals in (["甲", "甲"], ["甲\n\n乙", "乙\n\n丙"], ["甲", "新"]):
            with self.subTest(originals=originals), self.assertRaises(ValueError):
                apply_patches(["甲\n\n乙\n\n丙\n\n丁"], [dict(base, original=o) for o in originals])

    def test_numbered_patches_disambiguate_duplicates(self):
        chapters = ["甲\n\n乙\n\n甲"]
        patch = dict(chapter=1, paragraph=3, replacement="新甲")
        self.assertEqual(apply_patches(chapters, [patch]), ["甲\n\n乙\n\n新甲"])
        for number in (0, 4, True, "3"):
            with self.subTest(number=number), self.assertRaises(ValueError):
                apply_patches(chapters, [dict(patch, paragraph=number)])
        with self.assertRaises(ValueError):
            apply_patches(chapters, [patch, patch])

    def test_paragraph_edit_preserves_surroundings(self):
        text = "\r\n 甲 \r\n \t\r\n乙\r\n续行\r\n\r\n丙 \r\n"
        self.assertEqual(
            apply_paragraph_edits(text, [{"paragraph": 2, "text": "新"}], {2}), "\r\n 甲 \r\n \t\r\n新\r\n\r\n丙 \r\n"
        )
        self.assertEqual(apply_paragraph_edits(text, [], []), text)
        self.assertEqual(apply_paragraph_edits("", [], []), "")

    def test_invalid_edits(self):
        for edit in ({"paragraph": i, "text": "新"} for i in (0, -1, 3, True, "1", 1.0)):
            with self.subTest(edit=edit), self.assertRaises(ValueError):
                apply_paragraph_edits("甲\n\n乙", [edit], {1, 2})
        for edits in (
            [{"paragraph": 2, "text": "新"}],
            [{"paragraph": 1, "text": " "}],
            [{"paragraph": 1, "text": None}],
            [{"paragraph": 1, "text": "新"}] * 2,
            [{}],
            [None],
        ):
            with self.subTest(edits=edits), self.assertRaises(ValueError):
                apply_paragraph_edits("甲\n\n乙", edits, {1})

    def test_paragraph_numbering(self):
        self.assertEqual(number_paragraphs("\n甲\n续\n \n\n乙\n"), "\n[P1] 甲\n续\n \n\n[P2] 乙\n")
        self.assertEqual(number_paragraphs(" \n\n"), " \n\n")


if __name__ == "__main__":
    unittest.main()
