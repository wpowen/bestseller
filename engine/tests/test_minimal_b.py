"""Behavior-preserving split of test_minimal_b.py."""

from support_minimal_b import *


class MinimalBTestsPart1(MinimalBTests):
    def test_noncombat_writer_gets_plain_language_chapter_type_and_no_fighting_instruction(self):
        outline = dict(
            self.outline,
            type="情感",
            rhythm_type="情感",
            rhythm_focus="说清隐瞒并重新约定分工",
            scenes=[
                {
                    "characters": ["阿宁"],
                    "goal": "坦白此前隐瞒",
                    "obstacle": "同伴不再信任她",
                    "choice": "交出路线决定权",
                }
            ],
        )
        client = CaptureClient(self.out, [body(60)])

        self.run_chapter(client, outline=outline)

        prompt = client.calls[0]["prompt"]
        assert "本章章型：情感" in prompt
        assert "本章不安排正面打斗" in prompt
        assert "对手只能作为消息或远处压力" in prompt
        assert all(term not in prompt for term in ("原章", "程序判定", "重规划"))

    def test_writer_has_four_blocks_without_style_or_prefix_leak(self):
        for dialogue in (30, 60):
            with self.subTest(dialogue=dialogue), tempfile.TemporaryDirectory() as directory:
                self.out = Path(directory)
                save(self.out / "chapter-001.body.md", self.previous)
                client = CaptureClient(self.out, [body(dialogue)])
                row = self.run_chapter(client)
                prompt = client.calls[0]["prompt"]
                self.assertEqual(
                    re.findall(r"^## (.+)$", prompt, re.M),
                    ["本章章纲", "出场人物档案、口吻卡与当前状态", "上章结尾", "本章推进线"],
                )
                self.assertIn("目标200汉字", prompt.split("## ", 1)[0])
                for required in ("只写两场戏", "说话直接，先问再答。", self.previous, "阿宁在院里等她，要求一起回家"):
                    self.assertIn(required, prompt)
                for forbidden in (
                    "PREFIX_SENTINEL",
                    "BIBLE_SENTINEL",
                    "BIBLE_SUMMARY_SENTINEL",
                    "PARAGRAPH_RULE_SENTINEL",
                    "TABOO_SENTINEL",
                    "HABIT_SENTINEL",
                    "OUTLINE_SUMMARY_SENTINEL",
                    "HISTORY_SUMMARY_SENTINEL",
                    "RECENT_SUMMARY_SENTINEL",
                    "USER_INPUT_SENTINEL",
                    "圣经",
                    "summary",
                    "taboos",
                    "habits",
                    "单句",
                    "段落",
                    "每段",
                    "3—6句",
                    "18—24段",
                ):
                    self.assertNotIn(forbidden, prompt)
                self.assert_calls(client, ["r2-ch2-draft", "r2-ch2-finish"])
                self.assertEqual(row["style"]["dialogue_ratio"], dialogue / 200)
                self.assertEqual(row["style"]["single_sentence_ratio"], 1)
                self.assertFalse(row["dialogue_rewrite"])
                self.assertEqual(row["state"]["characters"]["阿宁"]["location"], "院")
                self.assertEqual(row["summary"], "阿宁在院里等她。")
                self.assertTrue(row["within_band"])
                self.assert_saved(row, body(dialogue))

    def test_old_final_checkpoint_keeps_every_file_without_requests(self):
        row = dict(
            number=2,
            status="complete",
            fact_source="final_body",
            writer="q1-r2",
            summary="旧摘要",
            state={"characters": {}},
        )
        save(self.out / "chapters/002.json", row)
        save(self.out / "chapter-002.body.md", "旧正文只有短段。")
        save(self.out / "chapter-002.draft.md", "旧初稿。")

        def snapshot():
            return {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.out.rglob("*") if p.is_file()}

        before = snapshot()
        client = CaptureClient(self.out, [])
        self.assertEqual(self.run_chapter(client), row)
        self.assertEqual(client.calls, [])
        self.assertEqual(snapshot(), before)

    def test_context_trims_non_scene_hero_before_scene_character_fields(self):
        hero = dict(
            self.bible["characters"][0], desire="主动欲望" * 180, fear="具体恐惧" * 180, gender="女", address="阿宁"
        )
        support = [dict(name=f"配角{i}", role="support", voice="说话直接", desire="各自目标") for i in range(3)]
        bible = dict(self.bible, characters=[*support, hero])
        dashboard = dict(
            strength=self.bible["canon"]["ladder"]["strength"][0],
            wealth="2元",
            status=self.bible["canon"]["ladder"]["status"][0],
            stage="旧城",
            affiliation="无",
        )
        state = dict(
            dashboard=dashboard, characters={c["name"]: {"location": "长状态" * 250} for c in [hero, *support]}
        )
        chapter = dict(number=2, scenes=[{"characters": [c["name"] for c in support]}])
        before, dropped = deepcopy((bible, state, chapter)), []
        sections = dict(q1.context(bible, self.threads, {"number": 1}, chapter, state, [], [], "", dropped))
        cards = json.loads(sections["出场人物档案、口吻卡与当前状态"])["characters"]
        self.assertEqual(cards[0]["name"], "阿宁")
        self.assertEqual(cards[0], {"name": "阿宁"})
        self.assertEqual(dropped[0], "阿宁.dashboard:compressed")
        self.assertLess(dropped.index("阿宁.realm_ability"), dropped.index("配角2.current"))
        self.assertTrue(all(card["voice"] == "说话直接" for card in cards[1:]))
        self.assertEqual((bible, state, chapter), before)
        fallback = dict(
            q1.context(
                dict(bible, characters=[]),
                self.threads,
                {"number": 1},
                chapter,
                dict(dashboard=dashboard, characters={"阿宁": {"location": "院"}}),
                [],
                [],
                "",
            )
        )
        dossier = json.loads(fallback["出场人物档案、口吻卡与当前状态"])
        self.assertEqual(dossier["dashboard"], dashboard)
        self.assertEqual(dossier["characters"][0]["name"], "阿宁")

    def test_invalid_extraction_returns_raw_once_without_recovery_chain(self):
        raw = dict(type="日常", state={"characters": {"阿宁": {"location": "未证实地点"}}}, summary="保留失败内容")
        client = CaptureClient(self.out, [], {"r2-ch2-finish": raw})
        state = {"custom": "keep"}
        value, failed, retries = q1.extract_state(
            client, "r2-ch2-finish", body(60), state, self.bible, self.outline, []
        )
        self.assertEqual((failed, retries), (True, 0))
        self.assertEqual(value["summary"], raw["summary"])
        self.assertEqual(value["state"], raw["state"])
        self.assertEqual(load(self.out / "steps/r2-ch2-finish.json"), value)
        self.assertEqual(state, {"custom": "keep"})
        self.assert_calls(client, ["r2-ch2-finish"])

    def test_failed_finish_preserves_old_state_without_adopting_invalid_patch(self):
        old = dict(
            characters={"阿宁": {"location": "旧院", "present": True}},
            dashboard=dict(strength=self.bible["canon"]["ladder"]["strength"][0]),
            continuity={"balances": {"寿命": {"amount": 2, "unit": "天"}}},
            gains=[{"chapter": 1, "type": "旧得", "content": "旧物"}],
        )
        before = deepcopy(old)
        raw = dict(
            type="日常",
            state={
                "characters": {"阿宁": {"location": "未证实码头"}},
                "continuity": {"balances": {"寿命": {"amount": 99, "unit": "天"}}},
            },
        )
        client = CaptureClient(self.out, [body(60)], {"r2-ch2-finish": raw})
        row = self.run_chapter(client, state=old)
        self.assert_calls(client, ["r2-ch2-draft", "r2-ch2-finish"])
        self.assertTrue(row["state_failed"])
        self.assertTrue(row["state_initial_failed"])
        self.assertEqual(row["state_retries"], 0)
        for key in old:
            self.assertEqual(row["state"][key], old[key])
        self.assertEqual(old, before)
        self.assert_saved(row, body(60))

    def test_patch_requires_actual_adoption_and_final_body_evidence(self):
        draft, addition = body(60), "她拿回旧钥匙，当场打开门。"
        kind = self.bible["canon"]["payoff_menu"][0]
        for evidence in ("pid", "quote", "unfulfilled", "forged", "invalid_pid", "rejected", "unchanged"):
            with self.subTest(evidence=evidence), tempfile.TemporaryDirectory() as directory:
                self.out = Path(directory)
                patch_value = dict(after=1, text=addition)
                if evidence == "rejected":
                    patch_value["after"] = 0
                if evidence == "unchanged":
                    patch_value.update(text=draft.split("\n\n")[0], replace=True)
                first = self.extraction(patch=patch_value, state={"characters": {"阿宁": {"location": "初提地点"}}})
                payoff = (
                    dict(type=kind, fulfilled=True, pid=2)
                    if evidence == "pid"
                    else dict(type=kind, fulfilled=True, quote=addition)
                )
                if evidence == "invalid_pid":
                    payoff = dict(type=kind, fulfilled=True, pid=999)
                if evidence == "forged":
                    payoff["quote"] = "不存在于定稿的兑现"
                if evidence == "unfulfilled":
                    payoff = dict(type="无兑现", fulfilled=False, quote="")
                final = self.extraction(
                    payoff=payoff, patch={"after": 1, "text": "不得再次采用"}, edits=[{"id": 1, "text": "不得再次润色"}]
                )
                client = CaptureClient(self.out, [draft], {"r2-ch1-finish": first, "r2-ch1-final-state": final})
                row = self.run_chapter(client, rows=[], outline=dict(self.outline, number=1, payoff_type=kind))
                changed = evidence not in ("rejected", "unchanged")
                self.assert_calls(
                    client, ["r2-ch1-draft", "r2-ch1-finish"] + (["r2-ch1-final-state"] if changed else [])
                )
                expected = draft.replace("\n\n", "\n\n" + addition + "\n\n", 1) if changed else draft
                self.assert_saved(row, expected, 1)
                self.assertEqual((self.out / "chapter-001.r2.draft.md").read_text(), draft)
                self.assertEqual(row["outside_calls"], 2 + int(changed))
                self.assertEqual(row["payoff_fulfilled"], evidence in ("pid", "quote"))
                self.assertEqual(row["state_failed"], evidence in ("forged", "invalid_pid"))
                if evidence in ("pid", "quote"):
                    self.assertEqual(row["payoff"]["quote"], addition)
                    self.assertIsNone(row["state"]["pending_payoff"])
                else:
                    self.assertEqual(row["state"]["pending_payoff"], kind)
                if changed:
                    self.assertIn(
                        number_paragraphs(expected),
                        [c for c in client.calls if c["stage"].endswith("-final-state")][-1]["prompt"],
                    )
                    final_schema = [c for c in client.calls if c["stage"].endswith("-final-state")][-1]["prompt"].split(
                        "只返回完整JSON，结构："
                    )[-1]
                    for field in ("edits", "paragraph_edits", "patch", "tail", "previous_delta"):
                        self.assertNotIn('"' + field + '":', final_schema)
                    self.assertNotIn("不得再次采用", expected)
                    if row["state_failed"]:
                        self.assertEqual(row["state"], {"pending_payoff": kind, "handoff_unknown": True})
                    else:
                        self.assertEqual(row["state"]["characters"]["阿宁"]["location"], "院")

    def test_sentence_edit_changes_only_named_hit_and_uses_final_state(self):
        draft = "她明白这不是胜利，而是新的开始。\n\n“我陪你回家。”\n\n她将钥匙收进衣袋。"
        replacement = "她将院门推开。"
        first = self.extraction(
            edits=[dict(id=1, text=replacement, reason="命中套话")],
            state={"continuity": {"balances": {"寿命": {"amount": 99, "unit": "天"}}}},
        )
        final = self.extraction(
            state={"continuity": {"balances": {"寿命": {"amount": 1, "unit": "天"}}}},
            advances=[dict(thread="line", beat="wait", done=True, event="阿宁在院里等她")],
        )
        old = {"continuity": {"balances": {"寿命": {"amount": 2, "unit": "天"}}}}
        before = deepcopy(old)
        client = CaptureClient(self.out, [draft], {"r2-ch2-finish": first, "r2-ch2-final-state": final})
        row = self.run_chapter(client, state=old)
        self.assert_calls(client, ["r2-ch2-draft", "r2-ch2-finish", "r2-ch2-final-state"])
        expected = replacement + "\n\n" + draft.split("\n\n", 1)[1]
        self.assert_saved(row, expected)
        self.assertEqual(row["state"]["continuity"]["balances"]["寿命"], {"amount": 1, "unit": "天"})
        self.assertEqual(row["state"]["ledger"]["line"]["done"], ["wait"])
        self.assertEqual(row["state"]["ledger"]["line"]["last"], 2)
        self.assertEqual(old, before)

    def test_dialogue_and_length_are_local_finish_repairs_without_extra_calls(self):
        cases = [
            ("dialogue", body(10), dict(patch=dict(after=1, replace=True, text=body(35).split("\n\n")[0]))),
            ("long", body(60, 340), dict(body=body(60))),
            ("short", body(20, 100), dict(body=body(20))),
        ]
        for label, draft, proposal in cases:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                self.out = Path(directory)
                save(self.out / "chapter-001.body.md", self.previous)
                client = CaptureClient(self.out, [draft], {"r2-ch2-finish": self.extraction(**proposal)})
                row = self.run_chapter(client)
                self.assert_calls(client, ["r2-ch2-draft", "r2-ch2-finish", "r2-ch2-final-state"])
                final = (self.out / "chapter-002.body.md").read_text()
                self.assert_saved(row, final)
                self.assertTrue(row["within_band"])
                self.assertEqual(row["outside_calls"], 3)
                self.assertTrue(row["length_correction"])
                self.assertFalse(row["dialogue_rewrite"])
                self.assertEqual((self.out / "chapter-002.r2.draft.md").read_text(), draft)
                if label == "dialogue":
                    self.assertGreaterEqual(row["style"]["dialogue_ratio"], 0.15)
                    self.assertEqual(final.split("\n\n")[1], draft.split("\n\n")[1])
                else:
                    self.assertEqual(final, body(60 if label == "long" else 20))
                    self.assertEqual(final.split("\n\n")[0], draft.split("\n\n")[0])
                requirements = json.loads(client.calls[1]["prompt"].split("## 本章要求\n")[1].split("\n\n## ")[0])
                self.assertEqual(requirements["actual_chars"], q1.chinese_count(draft))
                self.assertEqual(requirements["word_range"], [170, 230])
                self.assertEqual(requirements["target_chars"], 200)

    def test_failed_previous_and_current_complete_bodies_share_one_finish_payload(self):
        def long_body(label, filler):
            fact = "阿宁在院里拿着旧钥匙。" if label == "上章" else "阿宁带着旧钥匙走进长廊。"
            head, tail = label + "开头独标。" + fact, label + "完整末尾证据。"
            return head + filler * (2200 - q1.chinese_count(head + tail)) + tail

        previous, current = long_body("上章", "甲"), long_body("眼前", "乙")
        self.book = Book("suspense-mystery", 25, 2200, "USER_INPUT_SENTINEL")
        carry = self.extraction(state={"objects": {"旧钥匙": {"owner": "阿宁", "location": "院"}}})
        current_raw = self.extraction(
            previous_delta=carry,
            summary="阿宁带着旧钥匙走进长廊。",
            state={"characters": {"阿宁": {"location": "廊", "present": True}}},
        )
        client = CaptureClient(
            self.out, [previous, current], {"r2-ch1-finish": {"type": "日常"}, "r2-ch2-finish": current_raw}
        )
        first = self.run_chapter(client, rows=[], outline=dict(self.outline, number=1))
        self.assertTrue(first["state_failed"])
        self.assertEqual((self.out / "chapter-001.body.md").read_text(), previous)
        rows = [first]
        second = self.run_chapter(client, state=first["state"], rows=rows)
        self.assertEqual(
            [c["stage"] for c in client.calls],
            [
                "r2-ch1-draft",
                "r2-ch1-finish",
                "r2-ch1-boundary-reextract",
                "r2-ch2-draft",
                "r2-ch2-finish",
                "r2-ch2-boundary-reextract",
            ],
        )
        finish = [c for c in client.calls if c["stage"].endswith("-finish")][-1]["prompt"]
        self.assertIn(number_paragraphs(current), finish)
        self.assertIn(previous, finish)
        self.assertIn("上章完整末尾证据。", finish)
        self.assertIn("眼前完整末尾证据。", finish)
        self.assertIn("## 上章失败顺带补提", finish)
        self.assertLess(len(finish), 10000)
        self.assertTrue(load(self.out / "steps/r2-ch2-finish.json")["_previous_complete"])
        recovered = load(self.out / "chapters/001.recovery.json")
        self.assertTrue(recovered["carry_attempted"])
        self.assertTrue(recovered["state_recovered"])
        self.assertTrue(recovered["state_failed"])  # Original failure remains visible.
        self.assertEqual(second["state"]["objects"]["旧钥匙"]["location"], "院")
        self.assertEqual(second["state"]["characters"]["阿宁"]["location"], "廊")
        self.assertFalse(second["state_failed"])
        self.assert_saved(second, current)
        self.assertEqual((self.out / "chapter-001.body.md").read_text(), previous)

    def test_quota_exceptions_do_not_become_format_retries(self):
        for stage in ("r2-ch2-draft", "r2-ch2-finish", "r2-ch2-final-state"):
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as directory:
                self.out = Path(directory)
                save(self.out / "chapter-001.body.md", self.previous)
                old = {"continuity": {"balances": {"寿命": {"amount": 2, "unit": "天"}}}}
                before = deepcopy(old)
                replies = {
                    "r2-ch2-finish": self.extraction(patch={"after": 1, "text": "她拿起钥匙。"}),
                    stage: QuotaExhausted("account quota exhausted"),
                }
                client = CaptureClient(self.out, [body(60)], replies)
                with self.assertRaisesRegex(QuotaExhausted, "quota"):
                    self.run_chapter(client, state=old)
                expected = ["r2-ch2-draft", "r2-ch2-finish", "r2-ch2-final-state"]
                self.assert_calls(client, expected[: expected.index(stage) + 1])
                self.assertEqual(old, before)
                self.assertFalse((self.out / "chapters/002.json").exists())
                self.assertEqual(list((self.out / "steps").glob("*.retry.json")), [])
