"""Behavior-preserving split of test_minimal_b.py."""

from support_minimal_b import *


class MinimalBTestsPart2(MinimalBTests):
    def test_loose_extraction_parsing_and_stage_cache_preserve_grounded_facts(self):
        raw = self.extraction(payoff={"type": self.bible["canon"]["payoff_menu"][0], "fulfilled": True, "pid": 2})
        raw["state"]["characters"]["阿宁"]["present"] = "true"
        serialized = json.dumps(raw, ensure_ascii=False)
        for reply in ("```json\n" + serialized + "\n```", serialized[:-1] + ',"unfinished":"'):
            with self.subTest(reply=reply[-20:]), tempfile.TemporaryDirectory() as directory:
                self.out = Path(directory)
                client = CaptureClient(self.out, [], {"r2-ch2-finish": reply})
                args = (client, "r2-ch2-finish", body(60), {}, self.bible, self.outline, [])
                value, failed, retries = q1.extract_state(*args)
                self.assertEqual((failed, retries), (False, 0))
                self.assertIs(value["state"]["characters"]["阿宁"]["present"], True)
                self.assertEqual(value["payoff"]["quote"], body(60).split("\n\n")[1])
                self.assertEqual(load(self.out / "steps/r2-ch2-finish.json"), value)
                self.assertEqual(q1.extract_state(*args), (value, False, 0))
                self.assert_calls(client, ["r2-ch2-finish"])

    def test_nested_extract_fields_and_string_ids_preserve_root_truth_and_real_quotes(self):
        first = "她锁上院门。她收起旧钥匙。"
        second = "她一掌震开石闸。守卫退到了阶下。"
        text = first + "\n\n" + second
        nested = dict(
            opens_new_mainline=False, ending_reason="旧谜已收束", realm_displayed=True, realm_display_pid="P2"
        )
        raw = dict(state=deepcopy(nested), payoff={"pid": "P2"}, gains=[{"sid": "S3", "content": "震开石闸"}])
        normalized = q1.normalize_extract(deepcopy(raw))
        self.assertIs(normalized["opens_new_mainline"], False)
        self.assertEqual(normalized["ending_reason"], "旧谜已收束")
        self.assertIs(normalized["realm_displayed"], True)
        grounded = q1.ground(deepcopy(raw), text)
        self.assertEqual(grounded["realm_display_pid"], 2)
        self.assertEqual(grounded["realm_display_quote"], second)
        self.assertEqual(grounded["payoff"]["pid"], 2)
        self.assertEqual(grounded["payoff"]["quote"], second)
        self.assertEqual(grounded["gains"][0]["sid"], 3)
        self.assertEqual(grounded["gains"][0]["quote"], "她一掌震开石闸。")
        for root_verdict in (False, True):
            with self.subTest(root_verdict=root_verdict):
                root = dict(
                    state=dict(nested, opens_new_mainline=not root_verdict),
                    opens_new_mainline=root_verdict,
                    ending_reason="根层结论",
                    realm_displayed=False,
                    realm_display_pid="P1",
                )
                value = q1.ground(root, text)
                self.assertIs(value["opens_new_mainline"], root_verdict)
                self.assertEqual(value["ending_reason"], "根层结论")
                self.assertIs(value["realm_displayed"], False)
                self.assertEqual(value["realm_display_pid"], 1)
                self.assertEqual(value["realm_display_quote"], first)
        for unknown in ("P42", "P0", "Punknown"):
            with self.subTest(unknown=unknown):
                value = q1.ground(
                    dict(state={}, realm_display_pid=unknown, payoff={"pid": unknown}, gains=[{"sid": "S99"}]), text
                )
                self.assertNotIn("realm_display_quote", value)
                self.assertNotIn("quote", value["payoff"])
                self.assertNotIn("quote", value["gains"][0])

    def test_acceptance_counts_new_dialogue_without_rewriting_history(self):
        client = CaptureClient(self.out, [body(60)])
        row = self.run_chapter(client)
        old = dict(row, number=1, style=dict(row["style"], dialogue_ratio=0.05))
        before = deepcopy(old)
        result = q1.acceptance([old, row], [], client, self.bible, self.threads, {}, base=1)
        self.assertEqual((result["new_dialogue20_pass"], result["new_chapters"]), (1, 1))
        self.assertEqual(result["generated"], 2)
        self.assertEqual(q1.acceptance([old, row], [], client, self.bible, self.threads, {}, base=0)["new_chapters"], 2)
        self.assertEqual(old, before)
        self.assertNotIn("criteria", result)
        self.assertEqual(q1.ground({"state": {}, "type": "解谜"}, "正文。")["type"], "揭秘")
        self.assertEqual(q1.ground({"state": {}, "type": "未知分类"}, "正文。")["type"], "未知分类")
        self.assertEqual(q1.ground({"state": {}, "type": {}}, "正文。")["type"], {})
        ranged = q1.ground({"state": {}, "payoff": {"pid": "P2-P3"}}, "首段。\n\n第二段。\n\n第三段。")
        self.assertEqual(ranged["payoff"]["quote"], "第二段。\n\n第三段。")
        invalid = q1.ground({"state": {}, "payoff": {"pid": "P2-P9"}}, "首段。\n\n第二段。")
        self.assertNotIn("quote", invalid["payoff"])
        final = {
            "state": {},
            "type": "日常",
            "hook_type": None,
            "payoff": {"fulfilled": False, "type": "无兑现"},
            "opens_new_mainline": False,
        }
        self.assertTrue(q1.valid_extract(final, "归家。", [], [], ending=True))
        self.assertFalse(q1.valid_extract(final, "归家。", [], [], ending=False))
