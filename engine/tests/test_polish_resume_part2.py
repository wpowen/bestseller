"""Behavior-preserving split of test_polish_resume.py."""

from support_polish_resume import *


class PolishResumeTestsPart2(PolishResumeTests):
    def test_growth_stage_names_reach_fifty_and_keep_time_without_speed_stop(self):
        for seconds, expected in ((0, "complete"), (6000, "complete")):
            with self.subTest(seconds=seconds):
                book = replace(self.book, chapters=50)
                self.bible["initial_dashboard"] = {"strength": "辨认异常", "wealth": "3200元"}
                for n in range(1, 50):
                    row = dict(self.cached_row(), number=n, run_elapsed=0, seconds=120 if seconds else 0.01)
                    self.put(self.out / f"chapters/{n:03d}.json", row)
                    self.put(self.out / f"chapter-{n:03d}.body.md", BODY)
                self.put(
                    self.out / "run.json",
                    dict(
                        mode="Q1",
                        book=book.record(),
                        style="b",
                        status="interrupted",
                        started=storage.now(),
                        completed_chapters=49,
                        execution_seconds=seconds,
                        revision=dict(
                            name=q1.REVISION,
                            started=storage.now(),
                            base_chapters=0,
                            base_seconds=seconds,
                            initial_seconds=0,
                        ),
                    ),
                )
                stale = dict(
                    number=1,
                    start=1,
                    end=25,
                    before_dashboard={"strength": "stale"},
                    after_dashboard={"strength": "stale"},
                    feedback=[],
                )
                self.put(self.out / "volumes/01-025.json", stale)
                client = LocalClient(self.out)
                final = dict(self.cached_row(), number=50)
                self.put(self.out / "chapter-050.body.md", BODY)
                with ExitStack() as stack, redirect_stdout(io.StringIO()):
                    for key, value in [
                        ("QClient", client),
                        ("prepare", (self.bible, {"threads": []}, {})),
                        ("volume", dict(number=2, start=26, end=50)),
                        ("outlines", [dict(number=50)]),
                        ("chapter", final),
                    ]:
                        stack.enter_context(patch.object(q1, key, return_value=value))
                    stack.enter_context(patch.object(q1, "export"))
                    result = q1._run(book, self.out, None, self.out.parent / "assets", 180, "b", lambda: False)
                self.assertEqual(result["status"], expected)
                self.assertEqual(result["completed_chapters"], 50)
                self.assertEqual(self.get(self.out / "volumes/01-025.json"), stale)
                memory = self.get(self.out / "volumes/02-050.json")
                self.assertEqual(memory["after_dashboard"], final["state"].get("dashboard", {}))
                self.assertEqual(memory["fact_source"], "final_body")
                self.assertNotIn("scores", memory)
                self.assertEqual(client.calls, [])
                self.assertGreaterEqual(result["execution_seconds"], seconds)
                if seconds:
                    self.assertGreater(result["warnings"][-1]["average_seconds"], 100)
                self.assertEqual(self.files[self.out / "chapter-001.body.md"], BODY)

    def test_unsettled_cached_last_chapter_repairs_missing_and_stale_accounting(self):
        for old_quality in (None, {"generated": 0, "stale": True}):
            with self.subTest(old_quality=old_quality):
                self.files.pop(self.out / "volumes/01-001.json", None)
                result, row, chapter = self.resume(old_quality=old_quality)
                self.assertEqual(result["status"], "complete")
                self.assertEqual(result["completed_chapters"], 1)
                self.assertEqual(result["quality"]["generated"], 1)
                self.assertEqual(result["acceptance"], result["quality"])
                self.assertEqual(result["dropped_fields"]["1"], row["dropped_fields"])
                self.assertNotIn("stale", result["quality"])
                self.assertEqual(self.get(self.out / "run.json"), result)
                self.assertEqual(self.get(self.out / "state.json"), row["state"])
                memory = self.get(self.out / "volumes/01-001.json")
                self.assertEqual((memory["fact_source"], memory["end"]), ("final_body", 1))
                self.assertNotIn("scores", memory)
                self.assertEqual(memory["summary"], row["summary"])
                self.assertEqual(chapter.call_count, 1)

    def test_already_settled_cached_chapter_is_not_reviewed_again(self):
        quality = {"generated": 1, "already_settled": True}
        result, row, chapter = self.resume(completed=1, old_quality=quality)
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["acceptance"]["generated"], quality["generated"])
        self.assertEqual(result["acceptance"]["chapter_chars"], [row["chars"]])
        self.assertEqual(result["acceptance"]["word_band_pass"], 1)
        self.assertEqual(self.get(self.out / "chapters/001.json"), row)
        self.assertEqual(chapter.call_count, 0)

    def test_run_carries_previous_delta_in_current_finish_preserving_old_body(self):
        for paid in (True, False):
            with self.subTest(recovered_paid=paid):
                self.files.pop(self.out / "chapters/001.recovery.json", None)
                book = replace(self.book, chapters=2)
                kind = self.bible["canon"]["payoff_menu"][0]
                old = self.cached_row()
                old.update(state_failed=True, payoff_due=True, payoff_type=kind, payoff_fulfilled=False)
                old["state"]["pending_payoff"] = kind
                old["state"]["continuity"]["balances"]["寿命"] = {"amount": 2, "unit": "天"}
                self.put(self.out / "chapters/001.json", old)
                self.put(self.out / "chapter-001.body.md", BODY)
                self.put(self.out / "chapter-001.draft.md", "不可重写的旧初稿。")
                self.put(self.out / "writing_outlines/001.json", dict(number=1, payoff_due=True, payoff_type=kind))
                self.put(self.out / "state.json", old["state"])
                self.put(
                    self.out / "run.json",
                    dict(
                        mode="Q1",
                        book=book.record(),
                        style="b",
                        status="interrupted",
                        started=storage.now(),
                        completed_chapters=1,
                        revision=dict(name=q1.REVISION, started=storage.now(), base_chapters=1, base_seconds=0),
                    ),
                )
                for path in list(self.files):
                    if (
                        path.name.startswith("r2-ch")
                        or path == self.out / "chapters/002.json"
                        or path.name.startswith("01-002")
                    ):
                        self.files.pop(path)
                recovery = dict(
                    type="日常",
                    hook_type=q1.PLAN_DATA["hook_types"][0],
                    payoff={"type": kind if paid else "无兑现", "fulfilled": paid, "quote": BODY if paid else ""},
                    state={"continuity": {"balances": {"寿命": {"amount": 1, "unit": "天"}}}},
                )
                current = dict(
                    type="日常",
                    hook_type=q1.PLAN_DATA["hook_types"][1],
                    state={},
                    payoff={"type": kind, "fulfilled": True, "quote": BODY},
                    previous_delta=recovery,
                )
                client = LocalClient(
                    self.out,
                    {"r2-ch2-draft": BODY, "r2-ch2-finish": current, "r2-ch2-boundary-reextract": UNKNOWN_BOUNDARY},
                )
                with ExitStack() as stack, redirect_stdout(io.StringIO()):
                    stack.enter_context(patch.object(q1, "QClient", return_value=client))
                    stack.enter_context(patch.object(q1, "prepare", return_value=(self.bible, {"threads": []}, {})))
                    stack.enter_context(patch.object(q1, "volume", return_value=dict(number=1, start=1, end=2)))
                    stack.enter_context(patch.object(q1, "outlines", return_value=[{"number": 2}]))
                    stack.enter_context(patch.object(q1, "export"))
                    writes = stack.enter_context(patch.object(q1, "save", wraps=self.put))
                    for module in (q1_chapter, q1_extract):
                        stack.enter_context(patch.object(module, "save", writes))
                    q1._run(book, self.out, None, self.out.parent / "assets", 180, "b", lambda: False)
                stages = [c["stage"] for c in client.calls]
                self.assertEqual(stages, ["r2-ch2-draft", "r2-ch2-finish", "r2-ch2-boundary-reextract"])
                self.assertIn("## 上章失败顺带补提\n", client.calls[1]["prompt"])
                self.assertIn(json.dumps(BODY, ensure_ascii=False), client.calls[1]["prompt"])
                recovered = self.get(self.out / "chapters/001.recovery.json")
                next_row = self.get(self.out / "chapters/002.json")
                self.assertTrue(recovered["state_recovered"])
                self.assertTrue(recovered["carry_attempted"])
                self.assertEqual(recovered["payoff_fulfilled"], paid)
                self.assertTrue(next_row["payoff_fulfilled"])
                self.assertIsNone(next_row["state"]["pending_payoff"])
                self.assertEqual((next_row["outside_calls"], next_row["state_retries"]), (2, 0))
                self.assert_unknown_handoff(next_row, previous_missing=True)
                self.assertEqual(next_row["state"]["continuity"]["balances"]["寿命"], {"amount": 1, "unit": "天"})
                self.assertEqual(self.files[self.out / "chapter-001.body.md"], BODY)
                self.assertEqual(self.files[self.out / "chapter-001.draft.md"], "不可重写的旧初稿。")
                self.assertNotIn(
                    self.out / "chapter-001.body.md", [Path(call.args[0]) for call in writes.call_args_list]
                )

    def test_successive_extensions_ignore_shared_legacy_files_and_stage_cache(self):
        original = self.seed_extension()
        legacy = self.continuation(50)
        self.put(self.out / "continuation.yaml", yaml.safe_dump(legacy))
        self.put(self.out / "steps/plan-q1-extend.json", legacy)
        client = LocalClient(self.out, {f"plan-q1-r2-extend-{n}": self.continuation(n) for n in (50, 75)})
        prior = {}
        for target in (50, 75):
            bible, _, _ = q1_plan.prepare(
                client, replace(self.book, chapters=target), self.out, self.out.parent / "assets", design=True
            )
            self.assertEqual(max(v["end"] for v in bible["volumes"]), target)
            self.assertIn(self.out / f"continuation-{target}.yaml", self.files.keys())
            self.assertIn(self.out / f"steps/plan-q1-r2-extend-{target}.json", self.files.keys())
            self.assertRegex(client.calls[-1]["prompt"], rf'"previous_end"\s*:\s*{25 if target == 50 else 50}\b')
            for path, text in prior.items():
                self.assertEqual(self.files[path], text, "旧 continuation/stage 不得重写")
            prior.update(
                {
                    self.out / f"continuation-{target}.yaml": self.files[self.out / f"continuation-{target}.yaml"],
                    self.out / f"steps/plan-q1-r2-extend-{target}.json": self.files[
                        self.out / f"steps/plan-q1-r2-extend-{target}.json"
                    ],
                }
            )
        self.assertEqual([c["stage"] for c in client.calls], ["plan-q1-r2-extend-50", "plan-q1-r2-extend-75"])
        self.assertEqual(self.files[self.out / "story_bible.yaml"], original)
        self.assertEqual(self.get(self.out / "steps/plan-q1-extend.json"), legacy)

    def test_extension_recovers_matching_stage_without_a_second_request(self):
        self.seed_extension()
        self.put(self.out / "continuation-50.yaml", yaml.safe_dump(self.continuation(50)))
        self.put(self.out / "steps/plan-q1-r2-extend-50.json", self.continuation(50))
        self.put(self.out / "steps/plan-q1-r2-extend-75.json", self.continuation(75))
        client = LocalClient(self.out)
        for attempt in range(2):
            with self.subTest(attempt=attempt), redirect_stdout(io.StringIO()):
                bible, _, _ = q1_plan.prepare(
                    client, replace(self.book, chapters=75), self.out, self.out.parent / "assets", design=True
                )
                self.assertEqual(max(v["end"] for v in bible["volumes"]), 75)
        self.assertIn(self.out / "continuation-75.yaml", self.files.keys())
        self.assertEqual(client.calls, [])
