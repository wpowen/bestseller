"""Pure independent post-processing contracts; real models are tested separately."""

from collections import Counter
import unittest
from deai.text import paragraphs, sentences, replace, invariant, parse, count
from deai.rules import detect, merge_hits


class IndependentTests(unittest.TestCase):
    def test_parse_fences_literals_and_incomplete_errors(self):
        self.assertEqual(parse('```json\n{"a":1}\n```'), {"a": 1})
        self.assertEqual(parse("{'a': True}"), {"a": True})
        with self.assertRaises(ValueError):
            parse('{"a":')

    def test_offsets_preserve_crlf_and_surrounding_bytes(self):
        text = "  她开门。\r\n\r\n\t屋里阴森。她拿票。\r\n"
        hit = detect(text)[0]
        new = replace(text, [dict(start=hit["start"], end=hit["end"], replacement="屋里没有灯。")])
        self.assertEqual(new, text[: hit["start"]] + "屋里没有灯。" + text[hit["end"] :])
        self.assertEqual(invariant(text, new, {}), [])

    def test_paragraph_insertion_deletion_and_empty_replacement_rejected(self):
        old = "她开门。\n\n她坐下。"
        self.assertIn("paragraph_count", invariant(old, "她开门。", {}))
        self.assertIn("paragraph_structure", invariant(old, "她开门。\n她坐下。", {}))

    def test_numbers_and_entities_are_not_silently_changed(self):
        self.assertIn("numbers", invariant("许秋拿三张票。", "许秋拿两张票。", {}, ["许秋"]))
        self.assertIn("entity:许秋", invariant("许秋拿三张票。", "周秋拿三张票。", {}, ["许秋"]))

    def test_length_is_relative_to_original_and_explicit_range(self):
        self.assertIn("word_band", invariant("她" * 100, "她" * 89, {}, whole=True))
        self.assertIn("word_band", invariant("她" * 100, "她" * 111, {}, whole=True))
        self.assertEqual(invariant("她" * 100, "她" * 105, {}, whole=True), [])

    def test_fact_issues_never_add_to_style(self):
        hit = detect("屋里阴森。")[0]
        self.assertEqual(merge_hits([], [dict(hit, type="date_arithmetic")]), [])

    def test_uncertain_emotion_cannot_be_promoted_to_fact(self):
        self.assertIn("certainty:吓", invariant("她像被吓着一样松手。", "她吓得松手。", {}))
        self.assertEqual(invariant("她像被吓着一样松手。", "她松开了手。", {}), [])
        self.assertIn("body_side", invariant("他用左手开门。", "他用右手开门。", {}))

    def test_migration_rolls_back_only_violating_paragraph(self):
        from deai.text import rollback_invalid

        original = "她像被吓着一样松手。\n\n屋里阴森。"
        final = "她吓得松手。\n\n屋里没有灯。"
        edits = [
            dict(pid="P1", before="她像被吓着一样松手。", replacement="她吓得松手。"),
            dict(pid="P2", before="屋里阴森。", replacement="屋里没有灯。"),
        ]
        text, kept, pids = rollback_invalid(original, final, edits, {})
        self.assertEqual(text, "她像被吓着一样松手。\n\n屋里没有灯。")
        self.assertEqual(pids, ["P1"])
        self.assertEqual(len(kept), 1)

    def test_multiple_edits_retain_unselected_sentence_and_spacing(self):
        t = "她开门。 他起身。\n\n她坐下。"
        r = sentences(t)
        e = [
            dict(start=r[0]["start"], end=r[0]["end"], replacement="她推门。"),
            dict(start=r[2]["start"], end=r[2]["end"], replacement="她坐好。"),
        ]
        self.assertEqual(replace(t, e), "她推门。 他起身。\n\n她坐好。")

    def test_scene_selection_targets_hit_not_first_scene(self):
        from deai.dialogue import scene_for

        scenes = [dict(pids=["P1", "P2"]), dict(pids=["P3", "P4"])]
        slots = [dict(pid=f"P{i}") for i in range(1, 5)]
        self.assertEqual(scene_for(scenes, slots, ["P4"]), scenes[1])
        self.assertIsNone(scene_for(scenes, slots, ["P9"]))
        self.assertEqual(scene_for(scenes, slots, [], True), scenes[0])

    def test_resume_requires_matching_policy_source_and_result(self):
        import tempfile
        from pathlib import Path
        from deai.io import save, reusable

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            work = root / "final/.deai/001"
            config = dict(version="test", data="rules-1")
            save(work, "input.md", "旧稿。")
            save(work, "output.md", "终稿。")
            save(work, "policy.json", config)
            save(root, "final/chapter-001.md", "终稿。")
            save(root, "final/chapter-001.deai.json", dict(status="polished", complete=True))
            self.assertTrue(reusable(root, "001", "旧稿。", config))
            self.assertFalse(reusable(root, "001", "另稿。", config))
            self.assertFalse(reusable(root, "001", "旧稿。", dict(config, data="rules-2")))
            save(root, "final/chapter-001.md", "手改。")
            self.assertFalse(reusable(root, "001", "旧稿。", config))

    def test_chapter_selection_rejects_backwards_and_invalid_ranges(self):
        import argparse
        from deai.__main__ import chapter_range

        self.assertEqual(chapter_range("1-3,5"), {1, 2, 3, 5})
        for value in ("3-1", "0", "1-2-3", "x"):
            with self.assertRaises(argparse.ArgumentTypeError):
                chapter_range(value)

    def test_missing_coverage_is_not_a_clean_read(self):
        from deai.flow import missing_paragraphs

        ps = [dict(pid="P1"), dict(pid="P2")]
        self.assertEqual(missing_paragraphs(dict(checked_pids=["P1"]), ps), [ps[1]])
        self.assertEqual(missing_paragraphs(dict(checked_pids=["P1", "P2", "extra"]), ps), [])
        self.assertEqual(missing_paragraphs({}, ps), ps)

    def test_unchanged_confirmed_issue_cannot_disappear_on_recheck(self):
        from deai.rules import carry_hits

        text = "她开门。\n\n屋里阴森。"
        hit = detect(text)[0]
        self.assertEqual(len(carry_hits([hit], [], "她推开大门。\n\n屋里阴森。")), 1)
        self.assertEqual(carry_hits([hit], [], "她开门。\n\n屋里没人。"), [])
        self.assertEqual(carry_hits([], [hit], text, text), [])

    def test_final_recheck_reverts_unfixed_edit_and_keeps_other_paragraph(self):
        from deai.flow import final_adoption

        tail = "\n\n雨水顺着屋檐落在院子里，门边的人都没有说话。"
        old = "他缓缓抬眼。\n\n屋里阴森，没有灯。" + tail
        new = "他缓缓地抬起眼。\n\n屋里没有灯。" + tail
        rs = sentences(old)
        edits = [
            dict(
                start=rs[0]["start"], end=rs[0]["end"], before=rs[0]["text"], replacement="他缓缓地抬起眼。", pid="P1"
            ),
            dict(start=rs[1]["start"], end=rs[1]["end"], before=rs[1]["text"], replacement="屋里没有灯。", pid="P2"),
        ]
        before = [dict(rs[0], type="cool_micro_action")]
        after = [dict(sentences(new)[0], type="cool_micro_action")]
        text, kept, hits, rolled = final_adoption(old, new, edits, before, after, {})
        self.assertEqual(text, "他缓缓抬眼。\n\n屋里没有灯。" + tail)
        self.assertEqual(rolled, ["P1"])
        self.assertEqual(len(kept), 1)
        self.assertEqual(len(hits), 1)

    def test_chapters_sort_numerically_and_prefer_body_files(self):
        import tempfile
        from pathlib import Path
        from deai.io import chapters

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for n in ("001", "999", "1000"):
                (root / f"chapter-{n}.md").write_text("正文。")
            (root / "chapter-001.body.md").write_text("定稿。")
            found = chapters(root)
            self.assertEqual([n for n, p in found], ["001", "999", "1000"])
            self.assertEqual(found[0][1].name, "chapter-001.body.md")
