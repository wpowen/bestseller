import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

from bookengine import q1_quality as quality


class DashboardKeyTests(unittest.TestCase):
    def test_aliases_are_exact_and_canonical_chinese_wins_in_any_order(self):
        patch = {
            "strength": "旧",
            "实力等级": "二阶",
            "财富数值和单位": "0元",
            "地位名声等级": "队长",
            "势力规范名": "救援队",
            "舞台规范名": "山城",
            "主角实力等级": "三阶",
            "阿宁地位名声等级": "导师",
            "主角": {"strength": "四阶"},
            "阿宁": {"stage": "别城"},
        }
        expected = dict(strength="二阶", wealth="0元", status="队长", affiliation="救援队", stage="山城")
        snapshot = copy.deepcopy(patch)
        self.assertEqual(quality.normalize_dashboard(patch), expected)
        self.assertEqual(quality.normalize_dashboard(dict(reversed(list(patch.items())))), expected)
        self.assertEqual(patch, snapshot)

    def test_placeholders_do_not_hide_valid_aliases_or_zero(self):
        for placeholder in [
            None,
            "",
            "未知",
            "未明确",
            "未披露",
            "未标注",
            "未计量",
            "未变化",
            "无变化",
            "无明确变化",
            "不变",
            "Unknown",
            "unchanged",
            [],
            True,
        ]:
            with self.subTest(placeholder=placeholder):
                self.assertEqual(
                    quality.normalize_dashboard({"wealth": 0, "财富数值和单位": placeholder}), {"wealth": 0}
                )
        self.assertEqual(
            quality.normalize_dashboard({"财富数值及单位": "10金", "势力": "无所属", "舞台": "城"}),
            {"wealth": "10金", "affiliation": "无所属", "stage": "城"},
        )
        self.assertEqual(quality.normalize_dashboard({"财富": "无"}), {"wealth": "无"})
        self.assertEqual(quality.normalize_dashboard(None), {})

    def test_merge_migrates_old_and_applies_patch_without_touching_ledger(self):
        old = {
            "dashboard": {"wealth": "10金", "实力等级": "二阶", "舞台": "城"},
            "ledger": {"dark": {"last": 8, "done": ["a"], "max_gap": 5}},
        }
        raw = {
            "state": {
                "dashboard": {"strength": "旧", "实力等级": "三阶", "财富": "未变化", "stage": "Unknown"},
                "ledger": {"dark": {"done": ["伪造"]}},
            }
        }
        snapshot = copy.deepcopy((old, raw))
        result = quality.merge_state(old, raw, {"initial_dashboard": {"财富": "1金", "地位名声": "新人"}})
        self.assertEqual(result["dashboard"], dict(wealth="10金", strength="三阶", stage="城", status="新人"))
        self.assertEqual(result["ledger"], old["ledger"])
        self.assertEqual((old, raw), snapshot)

    def test_volume_reads_both_key_languages(self):
        self.assertEqual(quality.volume_issues({"实力等级": "一阶"}, {"strength": "一阶"}, 1, []), ["卷末仪表盘未变化"])
        self.assertEqual(quality.volume_issues({"strength": "一阶"}, {"实力等级": "二阶"}, 1, []), [])


class ReplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location(
            "reconcile_q1_state", Path(__file__).parents[1] / "tools/reconcile_q1_state.py"
        )
        cls.tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.tool)

    def test_stage_maps_only_explicit_canonical_names_not_rooms_or_volume_plan(self):
        bible = {"world": {"stages": [{"volume": 1, "stage": "山城与旧港"}, {"volume": 2, "stage": "海城"}]}}
        for value in ["山城·北街房间", "旧港仓库"]:
            self.assertEqual(self.tool.canonical_stage(value, bible), "山城与旧港")
        for value in ["街角房间", "楼上", "山城到海城", "Unknown"]:
            self.assertEqual(self.tool.canonical_stage(value, bible), "Unknown")
        self.assertEqual(self.tool.canonical_stage("山城与旧港", bible), "山城与旧港")
        dashboard = dict.fromkeys(self.tool.KEYS, "同值")
        a = self.tool.projected(dict(dashboard, stage="山城·街角房间"), bible)
        b = self.tool.projected(dict(dashboard, stage="旧港·楼上房间"), bible)
        self.assertFalse(self.tool.derive_volume(a, b, [])[0])

    def test_dry_run_replays_checkpoints_and_write_changes_only_derived_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for sub in ["steps", "chapters", "volumes"]:
                (root / sub).mkdir()
            initial = dict(strength="一阶", wealth="10金", status="新人", affiliation="队伍", stage="山城")
            (root / "story_bible.yaml").write_text(
                json.dumps({"initial_dashboard": initial, "world": {"stages": [{"stage": "山城"}, {"stage": "海城"}]}})
            )

            def save(path, value):
                (root / path).write_text(json.dumps(value, ensure_ascii=False))

            for n, patch in [
                (1, {"实力等级": "二阶", "舞台": "山城·屋内"}),
                (2, {"strength": "旧", "实力等级": "三阶", "舞台规范名": "海城"}),
            ]:
                save(
                    f"chapters/{n:03}.json",
                    {
                        "number": n,
                        "state": {"dashboard": initial, "ledger": {"keep": n}},
                        "style": {"score": 3},
                        "summary": "保留",
                    },
                )
                save(f"steps/q1-ch{n}-final-state.json", {"state": {"dashboard": patch}})
                save(
                    f"volumes/{n:02}.json",
                    {
                        "number": n,
                        "start": n,
                        "end": n,
                        "scores": {"AI味": 3},
                        "feedback": ["保留"],
                        "dashboard_changed": False,
                    },
                )
            save("state.json", {"dashboard": initial, "ledger": {"keep": 2}})
            save(
                "run.json",
                {
                    "quality": {"dashboard_changed": [False, False], "ledger": {"keep": 2}},
                    "acceptance": {"scores": [3]},
                    "usage": {"tokens": 123},
                    "previous_runs": [7],
                },
            )
            report = self.tool.reconcile_book(root, expected=2)
            self.assertEqual(report["replay"]["available"], 2)
            self.assertEqual(report["derived"]["dashboard_changed"], [True, True])
            self.assertEqual(report["derived"]["stage_changes"], 1)
            self.assertEqual(json.loads((root / "chapters/002.json").read_text())["state"]["dashboard"], initial)
            self.tool.apply_updates(root, report["updates"])
            row = json.loads((root / "chapters/002.json").read_text())
            self.assertEqual(row["state"]["dashboard"]["strength"], "三阶")
            self.assertEqual(row["state"]["ledger"], {"keep": 2})
            self.assertEqual(row["style"], {"score": 3})
            vol = json.loads((root / "volumes/02.json").read_text())
            self.assertEqual(vol["scores"], {"AI味": 3})
            run = json.loads((root / "run.json").read_text())
            self.assertEqual(run["usage"], {"tokens": 123})
            self.assertEqual(run["quality"]["ledger"], {"keep": 2})
            self.assertEqual(run["previous_runs"], [7])
            (root / "steps/q1-ch1-final-state.json").unlink()
            partial = self.tool.reconcile_book(root, expected=2)
            self.assertFalse(partial["replay"]["complete"])
            self.assertIsNone(partial["derived"]["dashboard_changed"][0])
            self.assertIsNone(partial["derived"]["stage_changes"])
            self.assertIn("Unknown", str(partial["derived"]["volume_issues"]))
            self.assertEqual(partial["chapters"][0]["dashboard"]["strength"], "Unknown")
            # The enclosing response is broken; the sole dashboard object is exact JSON.
            (root / "responses").mkdir()
            save("calls.json", [{"stage": "q1-ch1-final-state", "seq": 1}])
            save(
                "responses/001.json",
                {
                    "choices": [
                        {"message": {"content": 'broken outer JSON "state":{"dashboard":{"实力等级":"二阶"}} trailing'}}
                    ]
                },
            )
            recovered = self.tool.reconcile_book(root, expected=2)
            self.assertEqual(recovered["replay"]["available"], 2)
            self.assertEqual(recovered["chapters"][0]["source"], "responses/001.json#dashboard")
            self.assertEqual(recovered["chapters"][0]["dashboard"]["strength"], "二阶")


if __name__ == "__main__":
    unittest.main()
