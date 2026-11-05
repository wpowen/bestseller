"""Pure sentence-rule fixtures, including the twelve supplied editorial examples."""

import unittest

from bookengine.precision import apply, locate


EXAMPLES = """他跪在祭台前，将残片抵住凹槽，低声道：“我记得离开矿镇，是为了给白团找一条能活下去的路。”"
四人沿风穴内壁下行，脚下的风声逐渐变成低沉兽吼。
他嘴上说得硬，心里却怕错过线索，更怕罗七急躁之下毁掉唯一证据。
他刚保住夜市，却已经有人在等下一次亮起；而那段再也想不起的摇篮曲，正从门后传出若有若无的哼唱。
江野站在锈死的后门外，听见风刮铁皮，像一段迟迟不肯开始的鼓点。
他终于有了岗位，也第一次清楚感觉到，这份工作不是靠一次危险回声换来的，而是要被七次复演、每一道安全边界和同伴是否肯把后背交给他反复证明。
他握紧护腕，第一次清楚意识到，他们面对的已经不是一只黑箱里的器材，而是一处正在城市里运行的破洞。
江野盯着它，明白自己方才指错了人，而真正的来源正在更深的采购链里醒来。
江野望着她的背影，手里的扳手沉得像块烫手的铁，脚下那条由三个支点撑起的路线，忽然被他看成了另一种形状。
他握紧那根通往钟楼出口的铜管，知道下一个要抢的已经不是声音，而是闸门开启的四十秒。
程雾从左侧带回获救者，现场只确认一处活人；那枚白线扣却把另一条尚在上游的时间，刻进了老街地下。
上方忽然传来钟楼墙体崩裂的闷响，碎石落进洪水，积水猛地抬高。""".splitlines()


class PrecisionRulesTests(unittest.TestCase):
    def test_all_twelve_editorial_examples(self):
        self.assertEqual(len(EXAMPLES), 12)
        for sentence in EXAMPLES:
            with self.subTest(sentence=sentence):
                hits = locate(sentence)
                self.assertEqual(len(hits), 1)
                self.assertTrue(hits[0]["categories"])
                self.assertEqual(hits[0]["text"], sentence)

    def test_rules_generalize_without_names_or_exact_sentences(self):
        for sentence in (
            "她嘴上答得轻松，心里却怕误点，更怕错过末班车。",
            "我记得走出荒原，是为了替弟弟找一条活下去的路。",
            "洞口的风声慢慢化作嘶吼。",
            "真正的时间正在旧账里苏醒。",
            "他把那段记忆，刻入了城墙。",
            "上方突然传来屋顶崩裂的闷响。",
            "这不是结束，而是开始。",
            "她终于明白了。",
            "他清楚意识到代价。",
            "他知道下一次必须先救人。",
        ):
            self.assertTrue(locate(sentence), sentence)

    def test_plain_actions_questions_and_concrete_similes_are_not_hits(self):
        for sentence in (
            "他第一次推开门，拿起杯子。",
            "车终于来了。",
            "“你知道吗？”她问。",
            "他知道车站在哪。",
            "这不是红灯，而是绿灯。",
            "雨点像豆子一样大。",
            "铁皮响了两声，他搬来梯子。",
            "楼上传来脚步声。",
        ):
            self.assertEqual(locate(sentence), [], sentence)

    def test_offsets_and_ids_preserve_whitespace_and_only_last_sentence_is_ending(self):
        text = "  他开门。\r\n\r\n\t他终于明白了。  他坐下。\r\n"
        (hit,) = locate(text)
        self.assertEqual(hit["id"], 2)
        self.assertEqual(text[hit["start"] : hit["end"]], hit["text"])
        self.assertFalse(hit["ending"])
        final = locate(text + "\r\n  这一切才刚刚开始")[-1]
        self.assertTrue(final["ending"])
        self.assertEqual(final["text"], "这一切才刚刚开始")

    def test_apply_replaces_only_the_target_sentence_and_preserves_other_bytes(self):
        text = "他开门。\r\n\r\n  他终于明白了。\t他坐下。"
        (hit,) = locate(text)
        edited, accepted = apply(text, [{"id": hit["id"], "text": "他捏紧了车票。"}])
        self.assertEqual(edited.encode(), (text[: hit["start"]] + "他捏紧了车票。" + text[hit["end"] :]).encode())
        self.assertEqual(accepted, [{"id": 2, "text": "他捏紧了车票。"}])

    def test_invalid_empty_duplicate_and_still_matching_edits_are_ignored(self):
        text = "他开门。他终于明白了。"
        bad = [
            None,
            {},
            {"id": True, "text": "甲"},
            {"id": "no", "text": "甲"},
            {"id": 1, "text": "别改动作。"},
            {"id": 9, "text": "甲"},
            {"id": 2, "text": " \n"},
            {"id": 2, "text": None},
            {"id": 2, "text": "他终于知道了。"},
        ]
        self.assertEqual(apply(text, bad), (text, []))
        good = {"id": 2, "text": "他收起车票。"}
        self.assertEqual(apply(text, bad + [good, {"id": 2, "text": "他离开了。"}])[1], [good])

    def test_numeric_ids_and_no_paragraph_insertion(self):
        text = "他开门。他终于明白了。"
        self.assertEqual(apply(text, [{"id": "2", "text": "他收起车票。"}])[0], "他开门。他收起车票。")
        self.assertEqual(apply(text, [{"id": 2, "text": "他收起车票。\n\n他起身。"}]), (text, []))

    def test_poetic_images_generalize_but_plain_comparisons_remain(self):
        for text in ("答案像一张无形的网罩住了他。", "阴影咬住了他的脚踝。"):
            self.assertTrue(locate(text))
        self.assertFalse(locate("雨点像豆子一样大。"))
        self.assertFalse(locate("白团忽然从阴影里窜出，咬住另一根细管。"))
        self.assertTrue(locate("他不是没有野心，只是更怕拿记忆证明自己。"))
        self.assertTrue(locate("这项目并非等待他们决定，而是替他们作出选择。"))

    def test_multiple_edits_use_original_offsets_and_final_paragraph_is_not_rewritten(self):
        text = "他终于明白了。\n\n他推门。她终于知道了"
        result, accepted = apply(text, [{"id": 3, "text": "她退后一步。"}, {"id": 1, "text": "他坐下。"}])
        self.assertEqual(result, "他坐下。\n\n他推门。她退后一步。")
        self.assertEqual(len(accepted), 2)
        self.assertTrue(locate(text)[-1]["ending"])

    def test_summary_inside_last_paragraph_targets_only_that_sentence(self):
        text = "他下楼。\n\n这一切才刚刚开始。他拎起包。"
        (hit,) = locate(text)
        self.assertFalse(hit["ending"])
        self.assertEqual(hit["text"], "这一切才刚刚开始。")
        result, _ = apply(text, [{"id": hit["id"], "text": "楼下又响起电话铃。"}])
        self.assertEqual(result, "他下楼。\n\n楼下又响起电话铃。他拎起包。")

    def test_blank_whitespace_has_no_hits(self):
        self.assertEqual(locate(" \r\n"), [])
