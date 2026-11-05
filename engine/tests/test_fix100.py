"""Pure bounded-planning/extension contracts; no clients, credentials or network."""

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import yaml

from bookengine import longutil, q1_plan, q1_prepare
from bookengine.config import Book
from bookengine.storage import save
from bookengine.text import parse_json


class Fix100Tests(unittest.TestCase):
    def setUp(self):
        for name in ("socket.create_connection", "socket.socket.connect", "urllib.request.urlopen"):
            guard = patch(name, side_effect=AssertionError("Network forbidden in pure tests"))
            guard.start()
            self.addCleanup(guard.stop)

    def lines(self):
        return {
            "threads": [
                dict(
                    id=kind,
                    kind=kind,
                    beats=[
                        dict(id=f"{kind}-now", volume=2, description=f"{kind}当场行动"),
                        dict(id=f"{kind}-future", volume=3, description=f"{kind}后卷事件"),
                    ],
                )
                for kind in ("main", "dark", "hidden", "romance", "growth", "support")
            ]
        }

    def test_consequence_and_aftermath_merge_into_existing_main_without_losing_new_beats(self):
        raw = {
            "threads": [
                dict(
                    id="main",
                    kind="main",
                    beats=[dict(id="old-main", volume=1, description="已经完成的主线胜利", reveal_not_before=0)],
                ),
                dict(
                    id="后果线",
                    kind="consequence",
                    beats=[
                        dict(id="new-consequence", volume=6, description="旧胜利引出的新后果", reveal_not_before=126)
                    ],
                ),
                dict(
                    id="余波线",
                    kind="aftermath",
                    beats=[dict(id="new-aftermath", volume=6, description="同一主线的后续选择", reveal_not_before=128)],
                ),
            ]
        }
        before = deepcopy(raw)
        result = q1_plan.normalize_threads(raw, 6, 130, deepcopy(q1_plan.DATA["profile"]))
        mains = [line for line in result["threads"] if line["kind"] == "main"]
        self.assertEqual(len(mains), 1)
        self.assertEqual(mains[0]["id"], "main")
        indexed = {beat["id"]: beat for beat in mains[0]["beats"]}
        for line in raw["threads"]:
            for beat in line["beats"]:
                self.assertEqual(indexed[beat["id"]], beat)
        self.assertFalse(any(t["kind"] in ("consequence", "aftermath") for t in result["threads"]))
        self.assertEqual(raw, before)

    def test_truncated_review_recovers_complete_tail_fields_without_fabrication(self):
        prefix = dict(edits=[], summary="甲收回旧灯。", state={"characters": {}}, advances=[])
        complete = dict(
            scene_types=["action", "reveal"],
            actual_type="揭秘",
            actual_payoff="收回旧灯",
            actual_hook_type="截止时刻将到",
            hook_type="截止时刻将到",
            double_or_reawakening=False,
            terms=[],
            actual_structure="潜入",
        )
        # The first fragment is independently parseable, as in the observed
        # truncated reply: parse_json accepts it and misses complete later fields.
        head = json.dumps(prefix, ensure_ascii=False) + "," + json.dumps(complete, ensure_ascii=False)[1:-1] + ","
        for unfinished, missing in (
            ('"reason":"尚未说完', "reason"),
            ('"opens_new_mainline":tru', "opens_new_mainline"),
            ('"voice_issues":[{"name":"甲","reason":"未完', "voice_issues"),
            ('"r2_events":{"event":"未完', "r2_events"),
        ):
            with self.subTest(missing=missing):
                raw = head + unfinished
                if missing in ("reason", "opens_new_mainline"):
                    self.assertEqual(parse_json(raw), prefix)
                result = longutil.loose_object(raw)
                for key, value in {**prefix, **complete}.items():
                    self.assertEqual(result[key], value)
                self.assertNotIn(missing, result)
                self.assertTrue(result.recovered_output)
        # A nonempty terms value is usable only if its entire list is complete.
        for tail, expected in (
            (
                '"terms":[{"term":"潮灯","definition":"旧灯的别称"}],"reason":"未完',
                [{"term": "潮灯", "definition": "旧灯的别称"}],
            ),
            ('"terms":[{"term":"潮灯","definition":"未完', None),
        ):
            result = longutil.loose_object(json.dumps(prefix, ensure_ascii=False) + "," + tail)
            if expected is None:
                self.assertNotIn("terms", result)
            else:
                self.assertEqual(result["terms"], expected)
            self.assertNotIn("reason", result)

    def test_complete_json_keeps_nested_fields_nested_without_recovery(self):
        original = dict(
            edits=[],
            summary="甲仍留在院里。",
            state={"characters": {"甲": {"location": "院"}}},
            metadata={
                "actual_hook_type": "只属于嵌套对象",
                "scene_types": ["daily"],
                "double_or_reawakening": True,
                "terms": [{"term": "旧灯", "definition": "照明用"}],
                "opens_new_mainline": True,
            },
        )
        for indent in (None, 2):
            with (
                self.subTest(indent=indent),
                patch.object(
                    longutil, "recover_fields", side_effect=AssertionError("完整 JSON 不应扫描并提升嵌套字段")
                ),
            ):
                result = longutil.loose_object(json.dumps(original, ensure_ascii=False, indent=indent) + "\n  ")
                self.assertEqual(result, original)
                self.assertEqual(set(result), set(original))
                self.assertFalse(getattr(result, "recovered_output", False))

    def test_beat_slice_never_expands_six_urgent_lines_beyond_main_and_one_side(self):
        threads = self.lines()
        vol = dict(number=2, start=26, end=50)
        state = {"ledger": {t["id"]: dict(last=1, done=[]) for t in threads["threads"]}}
        for selected in ([], ["romance"], ["dark", "hidden", "support"]):
            chapter = dict(number=33, threads=selected)
            before = deepcopy((threads, vol, chapter, state))
            with self.subTest(selected=selected):
                result = q1_plan.beat_slice(threads, vol, chapter, state)
                self.assertEqual(len(result), 2)
                self.assertEqual(result[0]["thread"], "main")
                self.assertEqual(len({r["thread"] for r in result}), 2)
                self.assertTrue(all(r["beat"].endswith("-now") for r in result))
                if selected:
                    self.assertIn(result[1]["thread"], selected)
                self.assertEqual((threads, vol, chapter, state), before)

    def test_batch_prompt_allocations_match_returned_outlines_and_runtime_selection(self):
        threads = self.lines()
        vol = dict(number=2, start=26, end=50)
        state = {"ledger": {t["id"]: dict(last=25, done=[]) for t in threads["threads"]}}
        before = deepcopy((threads, vol, state))
        hostile = {"chapters": [dict(number=n, threads=[t["id"] for t in threads["threads"]]) for n in range(26, 36)]}
        with patch.object(q1_plan, "_ask", return_value=hostile) as ask:
            result = q1_plan.outlines(
                object(), {}, threads, deepcopy(q1_plan.DATA["profile"]), vol, 26, 35, state, [], [], []
            )
        ask.assert_called_once()
        rhythm = dict(ask.call_args.args[3])["稳定节奏排期，每章只执行指定两条线"]
        self.assertEqual([r["number"] for r in rhythm], list(range(26, 36)))
        rolling = deepcopy(state)
        for slot, chapter in zip(rhythm, result):
            with self.subTest(chapter=chapter["number"]):
                self.assertLessEqual(len(slot["threads"]), 2)
                self.assertEqual(chapter["threads"], slot["threads"])
                # q1 exports this same selector for the chapter consumer.
                chosen = q1_plan.beat_slice(threads, vol, chapter, rolling)
                self.assertEqual([r["thread"] for r in chosen], slot["threads"])
                for target in chosen:
                    rolling["ledger"][target["thread"]]["last"] = chapter["number"]
        self.assertEqual((threads, vol, state), before)

    def test_extension_30_to_100_to_130_keeps_every_old_volume_beat_and_file(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / "book"
            # This completed unit has its own 30-chapter volume boundary.
            original_bible = dict(
                title="旧书",
                summary="已兑现旧结局",
                power={"cost": "旧代价"},
                characters=[
                    dict(name="林宁", role="protagonist", voice="短句直说", desire="救回姐姐"),
                    dict(name="周远舟", role="support", voice="爱反问", desire="保住药铺"),
                    dict(name="赵听禾", role="antagonist", voice="慢声威胁", desire="夺下城门"),
                ],
                volumes=[dict(number=1, start=1, end=30, goal="旧胜利")],
            )
            original_threads = {
                "threads": [
                    dict(
                        id="main",
                        kind="main",
                        max_gap=10,
                        beats=[dict(id="main-old", volume=1, description="已经完成的旧节拍", reveal_not_before=0)],
                    )
                ]
            }
            save(out / "story_bible.yaml", yaml.safe_dump(original_bible, allow_unicode=True))
            save(out / "threads.yaml", yaml.safe_dump(original_threads, allow_unicode=True))
            save(out / "state.json", {"ledger": {"main": {"done": ["main-old"], "last": 30}}})
            save(out / "chapter-030.body.md", "旧胜利已兑现，不能撤回。")
            paths = [out / name for name in ("story_bible.yaml", "threads.yaml", "chapter-030.body.md")]
            preserved = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in paths}
            patches = {
                "extend-100": dict(
                    bible={
                        "volumes": [
                            dict(number=n, start=start, end=end)
                            for n, start, end in ((2, 31, 60), (3, 61, 90), (4, 91, 100))
                        ]
                    },
                    threads={
                        "threads": [
                            dict(
                                id="main",
                                beats=[
                                    dict(id="main-old", volume=1, description="不得覆盖旧节拍"),
                                    dict(id="main-new100", volume=4, description="百章新后果", reveal_not_before=0),
                                ],
                            )
                        ]
                    },
                ),
                "extend-130": dict(
                    bible={
                        "volumes": [
                            dict(number=n, start=start, end=end) for n, start, end in ((5, 101, 125), (6, 126, 130))
                        ]
                    },
                    threads={
                        "threads": [
                            dict(
                                id="main",
                                beats=[dict(id="main-new130", volume=6, description="后续新选择", reveal_not_before=0)],
                            )
                        ]
                    },
                ),
            }
            calls = []

            def plan(client, kind, key, sections, **kwargs):
                self.assertEqual(kind, "continuation")
                calls.append((key, deepcopy(sections)))
                return deepcopy(patches[key])

            prior_volumes = deepcopy(original_bible["volumes"])
            prior_beats = deepcopy(original_threads["threads"][0]["beats"])
            with patch.object(q1_prepare, "_ask", side_effect=plan):
                for target, previous_end in ((100, 30), (130, 100)):
                    bible, threads, _ = q1_plan.prepare(
                        object(),
                        Book("suspense-mystery", target, 2200, "续写既有后果"),
                        out,
                        Path(directory) / "assets",
                    )
                    self.assertEqual(dict(calls[-1][1])["新目标及旧单元结束"]["previous_end"], previous_end)
                    self.assertEqual(max(v["end"] for v in bible["volumes"]), target)
                    for old in prior_volumes:
                        self.assertIn(old, bible["volumes"])
                    main = next(t for t in threads["threads"] if t["id"] == "main")
                    for old in prior_beats:
                        self.assertIn(old, main["beats"])
                    prior_volumes = deepcopy(bible["volumes"])
                    prior_beats = deepcopy(main["beats"])
                    for p, old in preserved.items():
                        self.assertEqual((p.read_bytes(), p.stat().st_mtime_ns), old)
                    continuation = out / f"continuation-{target}.yaml"
                    preserved[continuation] = (continuation.read_bytes(), continuation.stat().st_mtime_ns)
                # A cached replay reconstructs all extensions without another model request.
                again, again_threads, _ = q1_plan.prepare(
                    object(), Book("suspense-mystery", 130, 2200, "续写既有后果"), out, Path(directory) / "assets"
                )
            self.assertEqual([key for key, _ in calls], ["extend-100", "extend-130"])
            self.assertEqual(again["volumes"], prior_volumes)
            main = next(t for t in again_threads["threads"] if t["id"] == "main")
            self.assertEqual(main["beats"], prior_beats)
            for p, old in preserved.items():
                self.assertEqual((p.read_bytes(), p.stat().st_mtime_ns), old)


if __name__ == "__main__":
    unittest.main()
