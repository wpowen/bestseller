"""Pure ending candidates and bounded edits; no model, database or manuscript writes."""

import unittest

from bookengine.endings import apply_scene, apply_tail, ending_candidates, english_hits, scene_context
from bookengine.text import _paragraph_spans


REAL_TAIL = "是他自己的笔迹。\n\n而日期，是十二年前。"
# 修复前 S50 的冻结摘录：保留更早的同一日期及原最后几段。
# 原末两段与 phase.json 的 ending_regression.before_tail 一致；不读取可变书稿。
ORIGINAL_S50_EXCERPT = (
    "“第五个新郎已经死了。第十三位等着替你认下十二年前那笔拒誓。"
    "你若再走一步，我只能把你钉回旧照。”\n\n"
    "“沈亦衡——新郎。”\n\n"
    "落款不是岑家，也不是许家。\n\n" + REAL_TAIL
)


class EndingTests(unittest.TestCase):
    def test_real_chapter_last_two_paragraphs_even_when_time_is_already_known(self):
        text = ORIGINAL_S50_EXCERPT
        spans = _paragraph_spans(text)
        self.assertEqual("\n\n".join(text[a:b] for a, b in spans[-2:]), REAL_TAIL)
        hits = ending_candidates(text, {"canon": {"characters": [{"name": "沈亦衡"}]}})
        (time,) = [h for h in hits if h["kind"] == "time"]
        self.assertEqual(time["text"], "十二年前")
        self.assertTrue(time["known"])
        self.assertEqual(time["context"], REAL_TAIL)
        for hit in hits:
            self.assertGreaterEqual(hit["start"], spans[-2][0])
            self.assertEqual(text[hit["start"] : hit["end"]], hit["text"])
            self.assertNotIn("opens_new_mainline", hit)

    def test_new_name_identity_and_time_generalize(self):
        for name in ("温芷岚", "顾北川"):
            text = f"她合上旧册。\n\n纸上写着：“{name}——继承人。”\n\n日期是二十年前。他究竟是谁？"
            hits = ending_candidates(text, {})
            self.assertEqual({h["kind"] for h in hits}, {"name", "identity", "time"})
            self.assertFalse(any(h["known"] for h in hits))
        for role in ("守门人", "集团董事长"):
            hits = ending_candidates(f"温芷岚——{role}。\n\n他究竟是谁？", {})
            self.assertEqual({h["kind"] for h in hits}, {"name", "identity"})

    def test_bible_outline_and_prefix_are_context_not_verdicts(self):
        text = "许清禾早已在场。\n\n纸上写着：“许清禾——继承人。”\n\n原来是三十年前的真相。"
        bible = {"characters": [{"name": "许清禾"}]}
        outline = {"ending": "揭明继承人身份及三十年前旧案"}
        hits = ending_candidates(text, bible, outline)
        self.assertTrue(hits)
        self.assertTrue(all(h["known"] for h in hits))
        self.assertTrue(all("opens_new_mainline" not in h for h in hits))
        self.assertEqual(bible, {"characters": [{"name": "许清禾"}]})

    def test_plain_actions_and_time_recollections_do_not_trigger(self):
        for text in (
            "她关灯。\n\n他拿起外套。\n\n两人走回家。",
            "他想起十二年前同母亲吃饭。\n\n他把旧照片放回抽屉。",
            "日期是1998年。\n\n他收起结案书，回家吃饭。",
            "“回家吃饭。”她收起包。\n\n“带伞了吗？”他问。",
            "",
            None,
        ):
            self.assertEqual(ending_candidates(text, {}), [])

    def test_candidates_never_scan_earlier_paragraphs(self):
        text = "纸上写着：“温芷岚——继承人。”日期是二十年前。他究竟是谁？\n\n她收起纸。\n\n他回家。"
        self.assertEqual(ending_candidates(text, {}), [])

    def test_apply_preserves_prefix_separators_and_suffix_byte_for_byte(self):
        text = "  第一段\r\n保留软换行。\r\n \r\n\t第二段。\r\n\r\n旧尾一。\r\n \r\n旧尾二。\r\n"
        prefix = text[: text.index("旧尾一。")]
        result, adopted = apply_tail(text, ["她合上结案册。", "他推开家门。"])
        self.assertTrue(adopted)
        self.assertEqual(result.encode(), (prefix + "她合上结案册。\r\n \r\n他推开家门。\r\n").encode())

    def test_two_paragraph_string_is_accepted_and_old_truth_is_not_a_verdict(self):
        old = "不可改前文。\n\n旧尾一。\n\n旧尾二。"
        tail = "原来她就是二十年前的证人，证词与旧卷吻合。\n\n她在结案册上签了字。"
        self.assertTrue(ending_candidates(tail, {}))
        self.assertEqual(apply_tail(old, tail), ("不可改前文。\n\n" + tail, True))
        self.assertEqual(apply_tail(old, [tail]), ("不可改前文。\n\n" + tail, True))

    def test_invalid_and_three_paragraph_edits_are_rejected_atomically(self):
        text = "前文。\n\n旧尾一。\n\n旧尾二。"
        for value in (
            None,
            {},
            True,
            12,
            "",
            " \n\n ",
            "只有一段。",
            "一段。\n\n二段。\n\n三段。",
            [],
            ["一段。"],
            ["一段。", "二段。", "三段。"],
            ["一段。", ""],
            ["一段。", None],
            ["一段。\n\n夹带一段。", "末段。"],
            ["旧尾一。", "旧尾二。"],
        ):
            with self.subTest(value=value):
                self.assertEqual(apply_tail(text, value), (text, False))
        self.assertEqual(apply_tail("只有一段。", ["新一。", "新二。"]), ("只有一段。", False))

    def test_fake_closure_with_explicit_extra_mainline_is_rejected(self):
        text = "前文。\n\n旧尾一。\n\n旧尾二。"
        for promise in (
            "但真正的幕后主使仍未现身。",
            "另一个谜团才刚刚开始。",
            "新的敌人正在等着他们。",
            "可那人究竟是谁？",
        ):
            self.assertEqual(apply_tail(text, ["旧案终于结清。" + promise, "他们声称一切已经结束。"]), (text, False))


class SceneTests(unittest.TestCase):
    def test_last_delimiter_only_and_exact_prefix_preserved(self):
        for marker in ("---", "### 第二场"):
            text = f"第一场原文。\r\n\r\n---\r\n\r\n中场。\r\n\r\n{marker}\r\n\r\n旧场一句。\r\n\r\n旧场二句。"
            ctx = scene_context(text)
            self.assertTrue(ctx["boundary"])
            self.assertEqual([p["text"] for p in ctx["paragraphs"]], ["旧场一句。", "旧场二句。"])
            value = {"start": ctx["start"], "text": "“走吧。”她拉开门。\n\n他跟上她：“回家。”"}
            result, changed = apply_scene(text, value)
            self.assertTrue(changed)
            self.assertEqual(result.encode(), (text[: text.index("旧场一句。")] + value["text"]).encode())
            self.assertEqual(apply_scene(text, dict(value, start=ctx["start"] + 1)), (text, False))
            self.assertEqual(apply_scene(text, dict(value, start=2)), (text, False))

    def test_marker_without_blank_line_preserves_marker_and_earlier_bytes(self):
        text = "前文。\n---\n第一场。\n### 第二场\n旧末场。"
        ctx = scene_context(text)
        result, changed = apply_scene(text, {"start": ctx["start"], "text": "“吃饭去。”她笑了。"})
        self.assertTrue(changed)
        self.assertEqual(result, text[: text.index("旧末场。")] + "“吃饭去。”她笑了。")

    def test_no_marker_requires_latter_half_and_not_p1(self):
        text = "原文甲。\n\n原文乙。\n\n原文丙。\n\n原文丁。"
        ctx = scene_context(text)
        self.assertEqual(ctx["start"], 3)
        self.assertEqual([p["pid"] for p in ctx["paragraphs"]], [3, 4])
        for start in (1, 2, 0, -1, 5, True, 3.0, "3", None):
            self.assertEqual(apply_scene(text, {"start": start, "text": "“回去吧。”"}), (text, False))
        self.assertEqual(
            apply_scene(text, {"start": 3, "text": "“回去吧。”"}), (text[: text.index("原文丙。")] + "“回去吧。”", True)
        )
        self.assertEqual(scene_context("只有一段。")["paragraphs"], [])
        self.assertEqual(apply_scene("只有一段。", {"start": 1, "text": "整章替换。"}), ("只有一段。", False))

    def test_limits_600_scene_and_150_per_paragraph(self):
        text = "原文。\n\n---\n\n旧末场。"
        start = scene_context(text)["start"]
        limit = "\n\n".join(["“" + "话" * 150 + "”"] * 4)
        self.assertTrue(apply_scene(text, {"start": start, "text": limit})[1])
        for replacement in (limit + "\n\n多", "话" * 151, "话" * 601, "\n\n".join(["a" * 150] * 5)):
            self.assertEqual(apply_scene(text, {"start": start, "text": replacement}), (text, False))

    def test_invalid_shapes_and_explicit_new_main_mystery_are_atomic(self):
        text = "前文。\n\n---\n\n旧末场。"
        start = scene_context(text)["start"]
        for value in (
            None,
            [],
            {},
            {"start": start, "text": ""},
            {"start": start, "text": ["对白。"]},
            {"start": start, "text": "“意为两份婚约发生替换。”他解释机关。"},
            {"start": start, "text": "只说明机关，没有对白。"},
            {"start": start, "text": "“还有新的主线即将开启。”"},
            {"start": start, "text": "“真正的主谋仍未现身。”"},
            {"start": start, "text": "“走吧。”\n---\n“新的场面。”"},
        ):
            self.assertEqual(apply_scene(text, value), (text, False))

    def test_final_merged_english_is_located_without_silently_deleting_it(self):
        text = "他摸到knife，走向B3。\n\n---\n\n旧末场。"
        result, changed = apply_scene(text, {"start": scene_context(text)["start"], "text": "“门在A12。”她收起knife。"})
        self.assertTrue(changed)
        hits = english_hits(result)
        self.assertEqual([h["text"] for h in hits], ["knife", "knife"])
        for hit in hits:
            self.assertEqual(result[hit["start"] : hit["end"]], "knife")
        self.assertIn("B3", result)
        self.assertIn("A12", result)
        self.assertEqual(
            [h["text"] for h in english_hits("12knife，siblings，B3，A12，iPhone。")], ["12knife", "siblings", "iPhone"]
        )
        self.assertEqual(english_hits("“回家。”她说。B3。"), [])


if __name__ == "__main__":
    unittest.main()
