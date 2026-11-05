import copy
import unittest

from bookengine.polish_checks import scan


class PolishChecksTests(unittest.TestCase):
    def kinds(self, text, **kwargs):
        return {kind for hit in scan(text, **kwargs) for kind in hit["categories"]}

    def test_similar_actions_and_nearby_sentence_offsets(self):
        for gap in ("", "灯光暗了。"):
            text = "门开了。\n\n玻璃后的女人慢慢抬起了右手。" + gap + "玻璃后的女人慢慢抬起了右手食指。"
            hits = scan(text)
            self.assertIn("similarity_candidate", {c for h in hits for c in h["categories"]})
            for hit in hits:
                self.assertEqual(set(hit), {"id", "start", "end", "text", "categories"})
                self.assertEqual(text[hit["start"] : hit["end"]], hit["text"])
            self.assertEqual(hits[-1]["id"], 3 + bool(gap))

    def test_short_dialogue_and_separate_paragraphs(self):
        self.assertEqual(scan("“走吗？”“走吧。”“走吗？”"), [])
        self.assertEqual(scan("玻璃后的女人慢慢抬起了右手。\n\n玻璃后的女人慢慢抬起了右手。"), [])
        self.assertEqual(scan("“今天再等等吧。”“今天再等等吧。”"), [])

    def test_quotes_localize_only_unmatched_edges(self):
        for text, expected in (
            ("门开了。她说：“走吧。风停了。", [2]),
            ("门开了。走吧。”风停了。", [2]),
            ("门开了。「走吧。风停了。", [2]),
            ('门开了。"走吧。风停了。', [2]),
            ("“坏引语。她说：“好引语。”尾句。", [1]),
            ("“坏引语。\n\n「好引语。」尾句。", [1]),
        ):
            with self.subTest(text=text):
                self.assertEqual([h["id"] for h in scan(text) if "quote_candidate" in h["categories"]], expected)

    def test_legal_long_quotes_and_repeated_paragraph_openers(self):
        for text in (
            "“先走。\n\n等雨停了再回来。”",
            "「先走。\n\n等雨停了再回来。」",
            '"先走。\n\n等雨停了再回来。"',
            "“先走。\n\n“等雨停了再回来。”",
            "“先走。\n“等雨停了再回来。”",
            "“她告诉我「先走」。我没动。”",
        ):
            self.assertNotIn("quote_candidate", self.kinds(text), text)

    def test_speaker_repetition_is_candidate_only(self):
        cards = [{"name": "林宁"}, {"name": "周青"}]
        self.assertIn("speaker_candidate", self.kinds("林宁说：“走。”林宁答道：“等。”", cards=cards))
        self.assertIn("speaker_candidate", self.kinds("林宁说：“走。”林宁突然开口：“等。”"))
        self.assertNotIn("speaker_candidate", self.kinds("林宁说：“走。”周青答道：“等。”林宁说：“好。”", cards=cards))
        for verb in ("接话", "回答", "反问"):
            self.assertNotIn("speaker_candidate", self.kinds(f"林宁说：“走。”周青{verb}：“等。”林宁答道：“好。”"))
        self.assertNotIn("speaker_candidate", self.kinds("林宁说：“走。”\n\n林宁说：“好。”", cards=cards))

    def test_explicit_blocked_power_and_disabled_limb(self):
        state = {
            "protagonist": {"name": "林宁"},
            "continuity": {"blocked": ["天眼"], "limbs": {"左手": "已废", "右手": "正常"}},
        }
        original = copy.deepcopy(state)
        self.assertIn("continuity_candidate", self.kinds("林宁施展了天眼。", state=state))
        self.assertIn("continuity_candidate", self.kinds("主角用左手握住刀柄。", state=state))
        self.assertIn("continuity_candidate", self.kinds("我抬起左手。", state=state))
        for text in (
            "林宁没有施展天眼。",
            "林宁无法使用天眼。",
            "林宁回忆当年施展天眼。",
            "周青施展天眼。",
            "周青用左手推开林宁。",
            "林宁看着周青施展天眼。",
            "林宁说：“我施展了天眼。”",
            "如果林宁施展天眼就能逃脱。",
            "林宁试图施展天眼。",
            "林宁施展天眼失败。",
            "林宁使用天眼的记忆浮现。",
            "林宁用右手握住刀柄。",
        ):
            self.assertNotIn("continuity_candidate", self.kinds(text, state=state), text)
        self.assertEqual(state, original)

    def test_explicit_protagonist_card(self):
        self.assertIn(
            "continuity_candidate",
            self.kinds(
                "周青使用天眼。",
                cards=[{"name": "周青", "role": "protagonist"}],
                state={"continuity": {"blocked": ["天眼"]}},
            ),
        )

    def test_balances_and_arithmetic_do_not_guess(self):
        state = {"continuity": {"balances": {"寿命": {"amount": "65", "unit": "天"}}}}
        for text in (
            "寿命剩余70天。",
            "寿命扣减10天，剩余60天。",
            "寿命从65天扣减10天，剩余60天。",
            "寿命从70天扣减10天，剩余60天。",
            "寿命从65天，扣减了10天，剩余60天。",
        ):
            self.assertIn("balance_candidate", self.kinds(text, state=state), text)
        for text in (
            "寿命剩余65天。",
            "寿命扣减10天，剩余55天。",
            "周青的寿命剩余70天。",
            "林宁猜测寿命剩余70天。",
            "寿命大概还剩70天。",
            "寿命剩余70年。",
        ):
            self.assertNotIn("balance_candidate", self.kinds(text, state=state), text)
        self.assertNotIn("balance_candidate", self.kinds("寿命剩余70天。"))
        unknown = {"continuity": {"balances": {"寿命": {"amount": None, "unit": "天"}}}}
        self.assertNotIn("balance_candidate", self.kinds("寿命剩余70天。", state=unknown))
        self.assertIn("balance_candidate", self.kinds("寿命从65天扣减10天，剩余60天。", state=unknown))
        self.assertNotIn("balance_candidate", self.kinds("寿命扣减10天，剩余55天。寿命剩余55天。", state=state))
        decimal = {"continuity": {"balances": {"寿命": {"amount": 0.3, "unit": "天"}}}}
        self.assertNotIn("balance_candidate", self.kinds("寿命扣减0.1天，剩余0.2天。", state=decimal))
        self.assertNotIn("balance_candidate", self.kinds("主角付出十天寿命。寿命剩余55天。", state=state))
        self.assertEqual(state["continuity"]["balances"]["寿命"]["amount"], "65")

    def test_common_chinese_tens_and_hundreds(self):
        for amount, spent, correct, wrong in (
            (65, "十", "五十五", "六十"),
            ("一百一十五", "二十", "九十五", "一百零五"),
            ("两百", "九十五", "一百零五", "一百零六"),
        ):
            state = {"continuity": {"balances": {"寿命": {"amount": amount, "unit": "天"}}}}
            self.assertNotIn("balance_candidate", self.kinds(f"寿命扣减{spent}天，剩余{correct}天。", state=state))
            self.assertIn("balance_candidate", self.kinds(f"寿命扣减{spent}天，剩余{wrong}天。", state=state))
        self.assertNotIn(
            "balance_candidate",
            self.kinds(
                "寿命剩余一百二天。", state={"continuity": {"balances": {"寿命": {"amount": 120, "unit": "天"}}}}
            ),
        )

    def test_surface_hits_and_malformed_optional_state(self):
        self.assertIn("english", self.kinds("他停下 like 一尊雕像。"))
        for state in (
            None,
            [],
            3,
            {"continuity": None},
            {
                "protagonist": 3,
                "continuity": {"blocked": 2, "limbs": [], "balances": {"寿命": {"amount": [], "unit": 3}}},
            },
        ):
            self.assertEqual(scan("主角推开门。", cards=[None, {"name": 3}], state=state), [])
        self.assertEqual(scan(None), [])
        self.assertEqual(scan(""), [])
