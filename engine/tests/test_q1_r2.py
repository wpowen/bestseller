"""Pure regressions: PYTHONPATH=engine python3 -m unittest discover -s engine/tests -p test_q1_r2.py."""

from copy import deepcopy
import json
import unittest
from unittest.mock import patch

from bookengine import q1, q1_plan
from bookengine.q1_quality import metrics, normalize_scenes, progress


class Q1R2Tests(unittest.TestCase):
    def test_scene_strings_aliases_and_ranges(self):
        cases = {
            "P1-P3:meeting/P4-P11 daily": ["meeting", "daily"],
            "P1-P11 daily": ["daily"],
            "P1–P3：会议；P4-P6 情感，P7-P11 mystery": ["meeting", "emotion", "reveal"],
            "战斗/行动/日常/揭秘/文书": ["action", "action", "daily", "reveal", "meeting"],
            "MYSTERY / paperwork / romance": ["reveal", "meeting", "emotion"],
            "inaction/unknown/玄学": ["unknown"] * 3,
            "": [],
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(normalize_scenes(raw), expected)

    def test_scene_objects_tables_and_invalid_values_preserve_inputs(self):
        raw = {
            "P1-P3": {"type": "审查"},
            "P4-P6": {"scene_type": "mystery"},
            "P7-P11": "daily",
            "P12": {"kind": "动作"},
        }
        saved = deepcopy(raw)
        self.assertEqual(normalize_scenes(raw), ["meeting", "reveal", "daily", "action"])
        self.assertEqual(raw, saved)
        for value in (None, [], {}, "  "):
            self.assertEqual(normalize_scenes(value), [])
        for value in (9, False, {"where": "客栈"}, ["不存在"]):
            self.assertEqual(normalize_scenes(value), ["unknown"])
        self.assertEqual(normalize_scenes({"range": "P1-P3", "type": "meeting"}), ["meeting"])
        self.assertEqual(normalize_scenes({"paragraphs": "P1-P11", "scene_type": "daily"}), ["daily"])

    def test_environment_counts_sentences_not_verbs(self):
        for subject in ("风", "寒风", "雪", "飞雪", "水", "雨水", "光", "月光"):
            for verb in ("挤进", "咬住", "舔过", "爬上"):
                with self.subTest(subject=subject, verb=verb):
                    self.assertEqual(metrics(f"{subject}悄悄{verb}门缝。")["environment_personification"], 1)
        self.assertEqual(metrics("寒风挤进衣领，雨水爬上鞋面。")["environment_personification"], 1)
        self.assertEqual(
            metrics("风停了，他挤进人群。月光下她咬住布条。雨水落在地上。")["environment_personification"], 0
        )
        self.assertEqual(metrics(None)["environment_personification"], 0)

    def test_state_advances_only_on_actual_events_and_does_not_mutate(self):
        threads = [{"id": "hidden", "kind": "hidden", "reveal_volume": 2, "beats": [{"id": "truth", "volume": 2}]}]
        old = {"ledger": {"hidden": {"last": 1, "done": [], "max_gap": 0}}}
        planned = {"advances": [{"thread": "hidden", "beat": "truth", "done": True, "event": "  "}]}
        saved = deepcopy((old, planned, threads))
        state, _ = progress(old, planned, 2, {"chapters": 3}, threads, {})
        self.assertEqual(state["ledger"]["hidden"]["last"], 1)
        self.assertEqual(state["ledger"]["hidden"]["done"], [])
        self.assertEqual((old, planned, threads), saved)
        actual = {"advances": [dict(planned["advances"][0], event="当场揭开旧案真相", reveal=True)]}
        state, issues = progress(state, actual, 2, {"chapters": 3}, threads, {})
        self.assertEqual(state["ledger"]["hidden"]["last"], 2)
        self.assertEqual(state["ledger"]["hidden"]["done"], ["truth"])
        self.assertTrue(any("提前reveal" in s for s in issues))


class Q1R2ContinuationTests(unittest.TestCase):
    def test_eight_chapter_attendance_survives_done_beats_without_taking_future_beat(self):
        for kind in ("dark", "hidden", "romance", "support"):
            with self.subTest(kind=kind):
                threads = {
                    "threads": [
                        dict(
                            id=kind,
                            kind=kind,
                            beats=[
                                dict(id="current-done", volume=2, description="已经兑现的旧事"),
                                dict(
                                    id="future-secret", volume=3, description="后卷才揭开的真相", reveal_not_before=60
                                ),
                            ],
                        )
                    ]
                }
                state = {"ledger": {kind: {"last": 25, "done": ["current-done"]}}}
                volume = {"number": 2, "start": 26, "end": 50}
                chapter = {"number": 33, "threads": ["future-secret"]}
                saved = deepcopy((threads, state, volume, chapter))

                self.assertEqual(q1.beat_slice(threads, volume, dict(chapter, number=32), state), [])
                targets = q1.beat_slice(threads, volume, chapter, state)

                self.assertEqual(len(targets), 1)
                self.assertEqual(targets[0]["thread"], kind)
                self.assertIs(targets[0]["must_appear"], True)
                self.assertEqual(targets[0]["beat"], "")
                self.assertTrue(targets[0]["event"].strip())
                self.assertNotIn("后卷才揭开的真相", targets[0]["event"])
                self.assertEqual((threads, state, volume, chapter), saved)

    def test_context_keeps_all_present_voice_cards_in_complete_1500_character_json(self):
        names = ["周屿", "阿宁", "许砚", "沈棠", "陆舟", "齐川"]
        people = [
            dict(
                name=name,
                role="protagonist" if i == 0 else "support",
                taboos=[f"{name}不以大道理掩饰自己的恐惧", f"{name}不用整齐排比向同伴讲原则"],
                habits=[f"{name}迟疑时把反问停在半句话", f"{name}生气时会故意省掉对方称呼"],
                voice=f"{name}受质疑时先反问，再压低声音说自己的打算。" * (20 if i % 2 else 1),
                desire="赢回同伴的信任。" * 80,
                fear="害怕再次失去家人。" * 80,
                flaw="不肯承认错误。" * 80,
                growth_end="接受别人的帮助。" * 80,
            )
            for i, name in enumerate(names)
        ]
        absent = dict(name="未出场者", role="support", taboos=["离场禁区"], habits=["离场习惯"])
        bible = {"characters": [*people, absent]}
        # Protagonist must survive selection even when omitted from scene names.
        chapter = {"number": 33, "scenes": [{"characters": names[1:]}]}
        state = {
            "characters": {name: {"location": "很长的状态记录" * 200} for name in names},
            "objects": {"杂物": "无关详情" * 500},
        }
        args = (bible, {"threads": []}, {"number": 2}, chapter, state, [], [], "")
        saved = deepcopy(args)

        context = q1.context(*args)
        self.assertEqual(len(context), 4)
        sections = dict(context)
        self.assertEqual(set(sections), {"本章章纲", "出场人物档案、口吻卡与当前状态", "上章结尾", "本章推进线"})
        raw = sections["出场人物档案、口吻卡与当前状态"]
        self.assertLessEqual(len(raw), 1500)
        dossier = json.loads(raw)
        self.assertEqual(len(dossier["characters"]), len(names))
        cards = {person["name"]: person for person in dossier["characters"]}
        self.assertEqual(set(cards), set(names))
        for person in people[1:]:
            card = cards[person["name"]]
            self.assertEqual(card["voice"], person["voice"][:100])
            self.assertLessEqual(len(card["voice"]), 100)
            self.assertNotIn("taboos", card)
            self.assertNotIn("habits", card)
        self.assertEqual(cards[people[0]["name"]], {"name": people[0]["name"]})
        self.assertEqual(args, saved)

    def _deduplicated_outline(self, candidate, recent):
        source = {"chapters": [candidate]}
        args = (
            object(),
            {},
            {"threads": []},
            deepcopy(q1_plan.DATA["profile"]),
            {"number": 2, "start": 26, "end": 50},
            27,
            27,
            {},
            recent,
            [],
            [],
        )
        saved = deepcopy((source, args[1:]))
        # No Client is instantiated; the only planning entry point is an in-memory stub.
        with patch.object(q1_plan, "_ask", return_value=deepcopy(source)) as ask:
            result = q1_plan.outlines(*args)
        ask.assert_called_once()
        self.assertEqual((source, args[1:]), saved)
        self.assertEqual(len(result), 1)
        return result[0]

    def _assert_removed_or_explicitly_overridden(self, result, marker, subject, label):
        if marker not in json.dumps(result, ensure_ascii=False):
            return
        # Allow retained logistical scene information only with an explicit instruction
        # that the new constraint supersedes the conflicting concrete model plan.
        explanation = json.dumps(
            {k: v for k, v in result.items() if k not in ("structure", "hook_type", "scenes", "hook")},
            ensure_ascii=False,
        )
        self.assertRegex(explanation, r"覆盖|忽略|优先|重排|重构|重新|不得|不沿用")
        self.assertRegex(explanation, subject)
        self.assertTrue(
            label in explanation or "structure" in explanation or "hook_type" in explanation,
            "覆盖说明必须关联新的结构或钩子约束",
        )

    def test_structure_dedup_does_not_leave_an_authoritative_conflicting_scene(self):
        marker = "继续沿街追赶逃走的敌人"
        candidate = dict(
            number=27,
            structure="追逐",
            hook_type="关系破裂",
            hook="同伴当场拒绝与他同行",
            scenes=[
                dict(
                    where="街头",
                    characters=["周屿"],
                    goal=marker,
                    obstacle="敌人越跑越远",
                    choice="继续追赶",
                    kind="action",
                )
            ],
        )
        result = self._deduplicated_outline(candidate, [dict(number=26, structure="追逐")])
        self.assertIn(result["structure"], q1_plan.DATA["structures"])
        self.assertNotEqual(result["structure"], "追逐")
        self._assert_removed_or_explicitly_overridden(result, marker, r"scenes|场景|章纲|结构", result["structure"])

    def test_clue_dedup_does_not_leave_an_authoritative_conflicting_hook(self):
        marker = "墙角浮出一枚全新的脚印作为章末悬念"
        candidate = dict(number=27, structure="潜入", hook_type="clue", hook=marker, scenes=[])
        result = self._deduplicated_outline(candidate, [dict(number=26, hook_type="clue")])
        self.assertIn(result["hook_type"], q1_plan.DATA["hook_types"])
        self.assertNotEqual(result["hook_type"], "clue")
        self._assert_removed_or_explicitly_overridden(result, marker, r"hook|钩子|结尾|章末", result["hook_type"])


if __name__ == "__main__":
    unittest.main()
