"""Behavior-preserving split of test_polish_resume.py."""

from support_polish_resume import *


class PolishResumeTestsPart1(PolishResumeTests):
    def test_empty_candidates_skip_model_and_non_candidate_sentence_is_preserved(self):
        client = LocalClient(self.out)
        text = "林宁留在院里。"
        body, detail = q1.amend(text, {"edits": []}, self.bible, self.old, {})
        self.assertEqual((body, client.calls, detail["hits_before"]), (text, [], 0))
        text = "他第一次清楚意识到她还在等。林宁留在院里。"
        from bookengine.precision import locate as actual_locate

        with patch.object(q1_extract, "locate", side_effect=actual_locate):
            body, detail = q1.amend(text, dict(edits=[dict(id=2, text="林宁走到码头。")]), self.bible, self.old, {})
        self.assertEqual(body, text)
        self.assertTrue(detail["adoption_failed"])
        with patch.object(q1_extract, "locate", side_effect=actual_locate):
            body, detail = q1.amend(text, dict(edits=[dict(id=1, text="他把伞递给她。")]), self.bible, self.old, {})
        self.assertEqual(body, "他把伞递给她。林宁留在院里。")
        self.assertFalse(detail["adoption_failed"])
        self.assertEqual(client.calls, [])

    def test_partial_invalid_edits_reject_body_and_all_proposed_facts(self):
        valid = dict(id=1, text="林宁走到桥上。", reason="地点修正")
        invalid = dict(id=99, text="林宁去了码头。", reason="不存在的句号")
        for edits in ([valid, invalid], [invalid, valid], [valid, dict(valid)]):
            with self.subTest(edits=edits):
                self.assert_rejected(self.proposed(edits))

    def test_nonlist_edits_do_not_adopt_state_or_summary(self):
        for edits in (None, {"id": 1, "text": "林宁去了码头。"}, "invalid", 7):
            with self.subTest(edits=edits):
                self.assert_rejected(self.proposed(edits))

    def test_valid_edits_never_adopt_model_review_state_or_summary(self):
        reply = dict(
            edits=[dict(id="1", text="林宁走到桥上。", reason="地点修正")],
            state={"characters": {"林宁": {"location": "桥"}}},
            summary="林宁到了桥上。",
        )
        body, detail, _ = self.amend(reply)
        self.assertEqual(body, "林宁走到桥上。")
        self.assertEqual(self.old["characters"]["林宁"]["location"], "院")
        self.assertEqual(len(detail["edits"]), 1)

    def test_valid_empty_replacement_deletes_only_its_sentence(self):
        body, detail, _ = self.amend(
            dict(edits=[dict(id=2, text="", reason="这句没有独立信息")], state={}), "林宁推开门。林宁再次推开门。"
        )
        self.assertEqual(body, "林宁推开门。")
        self.assertEqual(detail["edits"][0]["text"], "")

    def test_paragraph_markers_preserve_original_quotes_and_separate_candidate_sentence_ids(self):
        text = "“先别走。等雨停了再出发。”她握住门把。\r\n\r\n\t「我会等。你也别急。」他点头。"
        body, _, pure_client = self.amend({"edits": [], "state": {}}, text)
        self.assertEqual(pure_client.calls, [])
        client = LocalClient(self.out, {"r2-ch1-finish": self.extracted()})
        candidates = [dict(id=2, text="等雨停了再出发。”", categories=["quote-fixture"])]
        with patch.object(q1_extract, "locate", return_value=candidates):
            _, failed, retries = q1.extract_state(
                client, "r2-ch1-finish", text, self.old, self.bible, {"number": 1}, []
            )
        prompt = client.calls[0]["prompt"]
        numbered = prompt.split("## 编号正文\n", 1)[1].split("\n\n## ", 1)[0]
        self.assertEqual(re.sub(r"\[P\d+\] ", "", numbered), text)
        self.assertEqual(re.findall(r"\[P\d+\]", numbered), ["[P1]", "[P2]"])
        self.assertNotRegex(numbered, r"\[S\d+\]")
        candidate_section = prompt.split("## 候选句S编号\n", 1)[1].split("\n\n## ", 1)[0]
        self.assertEqual(json.loads(candidate_section.split("\n只返回完整JSON，结构：", 1)[0])[0]["id"], 2)
        self.assertEqual((failed, retries, len(client.calls)), (False, 0, 1))
        self.assertEqual(body, text)

    def test_cached_quote_worsening_rejects_whole_bundle_without_new_request(self):
        cases = [
            ("“先别走。雨停再出发。”\n\n林宁留在院里。", "“先别走。”"),
            ("「先别走。雨停再出发。」\n\n林宁留在院里。", "「先别走。」"),
            ("“先别走。林宁留在院里。", "““先别走。"),
        ]
        for index, (text, replacement) in enumerate(cases):
            with self.subTest(text=text):
                stage = f"cached-bad-quotes-{index}"
                cached = self.proposed(
                    [
                        dict(id=1, text=replacement, reason="错误地补引号"),
                        dict(id=len(q1._sentences(text)), text="林宁走到桥上。", reason="另一条修改"),
                    ]
                )
                path = self.out / "steps" / f"{stage}.json"
                self.put(path, cached)
                cache_bytes, old = self.files[path], deepcopy(self.old)
                client = LocalClient(self.out)
                client.glossary = {"旧灯": {"definition": "用来照明"}}
                body, detail, _ = self.amend(self.get(path), text)
                self.assertEqual((body, detail["edits"]), (text, []))
                self.assertEqual(self.old["continuity"], CONTINUITY)
                self.assertEqual(self.old["characters"]["林宁"]["location"], "院")
                self.assertTrue(detail["adoption_failed"])
                self.assertEqual(set(client.glossary), {"旧灯"})
                self.assertEqual((self.files[path], self.old, client.calls), (cache_bytes, old, []))

    def test_quote_guard_accepts_balanced_edits_repairs_and_nonworsening_old_damage(self):
        cases = [
            ("“先别走。雨停再出发。”", 2, "等雨停再走。”", "“先别走。等雨停再走。”"),
            ("“先别走。", 1, "“先别走。”", "“先别走。”"),
            ("「先别走。", 1, "「先别走。」", "「先别走。」"),
            ("“先别走。林宁守在院里。", 2, "林宁留在院里。", "“先别走。林宁留在院里。"),
        ]
        for text, number, replacement, expected in cases:
            with self.subTest(text=text):
                reply = dict(
                    edits=[dict(id=number, text=replacement, reason="局部修订")],
                    state={"characters": {"林宁": {"location": "院"}}},
                    summary="林宁在院里等候。",
                )
                body, detail, _ = self.amend(reply, text)
                self.assertEqual(body, expected)
                self.assertEqual(detail["edits"], [{k: e[k] for k in ("id", "text")} for e in reply["edits"]])

    def test_nested_limb_state_normalizes_and_injured_arm_blocks_hand_alias(self):
        description = "右臂折断，无法抬起"
        raw = {"continuity": {"limbs": {"林宁": {"右臂": description}}}}
        before = deepcopy((self.old, raw))
        state = merge_state(self.old, raw, self.bible)
        self.assertEqual(state["continuity"]["limbs"], {"左手": "已废", "右臂": description})
        for text in ("林宁抬起右手。", "林宁用右臂挡住门。"):
            with self.subTest(text=text):
                hits = scan_continuity(text, self.bible["characters"], state)
                self.assertTrue(any("continuity_candidate" in h["categories"] for h in hits))
        for text in ("林宁没有抬起右手。", "周青抬起右手。", "林宁抬起右脚。"):
            with self.subTest(text=text):
                self.assertFalse(
                    any(
                        "continuity_candidate" in h["categories"]
                        for h in scan_continuity(text, self.bible["characters"], state)
                    )
                )
        healthy = merge_state(self.old, {"continuity": {"limbs": {"林宁": {"右臂": "活动正常"}}}}, self.bible)
        self.assertFalse(
            any(
                "continuity_candidate" in h["categories"]
                for h in scan_continuity("林宁抬起右手。", self.bible["characters"], healthy)
            )
        )
        self.assertEqual((self.old, raw), before)

    def test_string_balance_normalizes_for_correct_and_incorrect_deductions(self):
        for source, amount in (("65天", "65"), ("六十五天", "六十五")):
            with self.subTest(source=source):
                raw = {"continuity": {"balances": {"寿命": source}}}
                before = deepcopy((self.old, raw))
                state = merge_state(self.old, raw, self.bible)
                self.assertEqual(state["continuity"]["balances"]["寿命"], {"amount": amount, "unit": "天"})
                state_before = deepcopy(state)
                correct = scan_continuity("寿命扣减10天，剩余55天。", state=state)
                incorrect = scan_continuity("寿命扣减10天，剩余60天。", state=state)
                self.assertFalse(any("balance_candidate" in h["categories"] for h in correct))
                self.assertTrue(any("balance_candidate" in h["categories"] for h in incorrect))
                self.assertEqual(state, state_before)
                self.assertEqual((self.old, raw), before)

    def test_empty_or_mistyped_continuity_preserves_existing_facts(self):
        for continuity in (
            None,
            [],
            "unknown",
            {},
            {"blocked": [], "limbs": {}, "balances": {}},
            {"blocked": {}, "limbs": [], "balances": None},
        ):
            with self.subTest(continuity=continuity):
                raw = {"continuity": continuity}
                before = deepcopy((self.old, raw))
                result = merge_state(self.old, raw, self.bible)
                self.assertEqual(result["continuity"], CONTINUITY)
                self.assertEqual((self.old, raw), before)

    def test_nonempty_changes_preserve_other_constraints_and_require_explicit_release(self):
        raw = {
            "continuity": {
                "blocked": ["天眼", "读心"],
                "limbs": {"右腿": "不可用"},
                "balances": {"灵石": {"amount": 3, "unit": "枚"}},
                "released": ["天眼"],
            }
        }
        before = deepcopy((self.old, raw))
        result = merge_state(self.old, raw, self.bible)["continuity"]
        self.assertEqual(set(result["blocked"]), {"遁术", "读心"})
        self.assertEqual(result["limbs"], {"左手": "已废", "右腿": "不可用"})
        self.assertEqual(result["balances"], {**CONTINUITY["balances"], "灵石": {"amount": 3, "unit": "枚"}})
        self.assertEqual((self.old, raw), before)

    def test_legacy_null_continuity_can_finish_chapter_without_losing_body(self):
        old = dict(deepcopy(self.old), continuity=None)
        extract = dict(
            state={"continuity": {"blocked": [], "limbs": {}, "balances": {}}},
            type="日常",
            hook_type=q1.PLAN_DATA["hook_types"][0],
            payoff={"type": self.bible["canon"]["payoff_menu"][0], "fulfilled": True, "quote": BODY},
        )
        client = LocalClient(self.out, {"r2-ch1-draft": BODY, "r2-ch1-finish": extract})
        row = q1.chapter(
            client,
            self.book,
            self.bible,
            {"threads": []},
            {},
            {"number": 1, "start": 1, "end": 1},
            {"number": 1},
            old,
            [],
            [],
            self.out,
            "b",
            {"b": ""},
        )
        self.assertEqual(row["status"], "complete")
        self.assertEqual(row["state"]["continuity"], {})
        self.assertEqual(self.files[self.out / "chapter-001.body.md"], BODY)
        self.assertEqual(self.get(self.out / "chapters/001.json"), row)
        self.assertIsNone(old["continuity"])
        self.assertEqual([c["stage"] for c in client.calls], ["r2-ch1-draft", "r2-ch1-finish"])
        self.assertEqual((row["outside_calls"], row["state_retries"]), (1, 0))

    def test_body_change_alone_triggers_one_final_state_from_adopted_text(self):
        raw = self.extracted(
            patch={"after": 1, "text": "她在门口停了一下。"}, state={"characters": {"林宁": {"location": "码头"}}}
        )
        final = self.extracted(state={"characters": {"林宁": {"location": "桥"}}}, summary="她在桥上停步。")
        client = LocalClient(self.out, {"r2-ch1-draft": BODY, "r2-ch1-finish": raw, "r2-ch1-final-state": final})
        row = q1.chapter(
            client,
            self.book,
            self.bible,
            {"threads": []},
            {},
            {"number": 1, "start": 1, "end": 1},
            {"number": 1},
            self.old,
            [],
            [],
            self.out,
            "b",
            {"b": ""},
        )
        expected = BODY.replace("\n\n", "\n\n她在门口停了一下。\n\n", 1)
        self.assertEqual(self.files[self.out / "chapter-001.body.md"], expected)
        self.assertEqual([c["stage"] for c in client.calls], ["r2-ch1-draft", "r2-ch1-finish", "r2-ch1-final-state"])
        self.assertIn("她在门口停了一下。", client.calls[-1]["prompt"])
        self.assertEqual(row["state"]["characters"]["林宁"]["location"], "桥")
        self.assertEqual((row["outside_calls"], row["state_retries"]), (2, 0))

    def test_rejected_bundle_keeps_body_and_does_not_extract_final_state(self):
        raw = self.extracted(edits=[dict(id=1, text="“我陪你回家。”"), dict(id=99, text="不存在的句子。")])
        client = LocalClient(self.out, {"r2-ch1-draft": BODY, "r2-ch1-finish": raw})
        with patch.object(q1_extract, "locate", return_value=[dict(id=1, categories=["quote-fixture"])]):
            row = q1.chapter(
                client,
                self.book,
                self.bible,
                {"threads": []},
                {},
                {"number": 1, "start": 1, "end": 1},
                {"number": 1},
                self.old,
                [],
                [],
                self.out,
                "b",
                {"b": ""},
            )
        self.assertEqual(self.files[self.out / "chapter-001.body.md"], BODY)
        self.assertTrue(row["polish"]["adoption_failed"])
        self.assertEqual([c["stage"] for c in client.calls], ["r2-ch1-draft", "r2-ch1-finish"])
        self.assertEqual(row["outside_calls"], 1)

    def test_failed_finish_carries_old_failure_once_without_recover_or_schema_retry(self):
        old = self.cached_row()
        old.update(state_failed=True)
        self.put(self.out / "chapters/001.json", old)
        self.put(self.out / "chapter-001.body.md", BODY)
        client = LocalClient(
            self.out, {"r2-ch2-draft": BODY, "r2-ch2-finish": {"state": {}}, "r2-ch3-finish": self.extracted()}
        )
        args = (
            client,
            replace(self.book, chapters=3),
            self.bible,
            {"threads": []},
            {},
            {"number": 1, "start": 1, "end": 3},
            {"number": 2},
            old["state"],
            [],
            [old],
            self.out,
            "b",
            {"b": ""},
        )
        row = q1.chapter(*args)
        self.assertTrue(row["state_failed"])
        self.assertEqual((row["state_retries"], row["outside_calls"]), (0, 1))
        self.assertTrue(old["carry_attempted"])
        self.assertFalse(old.get("state_recovered", False))
        self.assertEqual(row["state"]["continuity"], old["state"]["continuity"])
        self.assertEqual([c["stage"] for c in client.calls], ["r2-ch2-draft", "r2-ch2-finish"])
        self.assertIn("## 上章失败顺带补提\n", client.calls[-1]["prompt"])
        before = deepcopy((old, self.files))
        self.assertEqual(q1.chapter(*args), row)
        self.assertEqual((old, self.files), before)
        q1.extract_state(client, "r2-ch3-finish", BODY, old["state"], self.bible, {"number": 3}, [old])
        self.assertNotIn("## 上章失败顺带补提\n", client.calls[-1]["prompt"])
        self.assertEqual(self.files[self.out / "chapter-001.body.md"], BODY)
        self.assertEqual(old, before[0])
