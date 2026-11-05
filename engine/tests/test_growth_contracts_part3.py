"""Behavior-preserving split of test_growth_contracts.py."""

from support_growth_contracts import *


class SchedulingTestsPart1(SchedulingTests):
    def test_seeded_book_plans_differ_without_fixed_five_chapter_cycle(self):
        first = self.plan(self.canon("山门外的雷声"))
        second = self.plan(self.canon("斩断天命以后"))
        types = [r["payoff_type"] for r in first]
        self.assertNotEqual(types, [r["payoff_type"] for r in second])
        self.assertNotEqual([r["upgrade_due"] for r in first], [r["upgrade_due"] for r in second])
        self.assertTrue(any(a != b for a, b in zip(types, types[5:])))
        self.assertEqual(first, self.plan(self.canon("山门外的雷声")))

    def test_upgrade_gaps_setup_and_canonical_single_step_guard(self):
        canon = self.canon("浮动升级的测试书")
        rows = self.plan(canon, 240)
        upgrades = [r["number"] for r in rows if r["upgrade_due"]]
        gaps = [b - a for a, b in zip([0] + upgrades, upgrades)]
        self.assertGreater(len(set(gaps)), 1)
        self.assertTrue(all(4 <= gap <= 8 for gap in gaps), gaps)
        for row in rows:
            self.assertEqual(row["upgrade_due"], row["payoff_type"] == "升级")
            self.assertEqual(row["growth_setup"], row["number"] + 1 in upgrades)
            if row["upgrade_due"]:
                self.assertEqual(rows[row["number"] - 2]["next_upgrade"], row["number"])
        self.assertEqual(
            dashboard_guard({"strength": "第0阶"}, {"strength": "第2阶"}, canon, "")[0]["strength"], "第0阶"
        )

    def test_every_chapter_due_and_sliding_payoff_constraints(self):
        for seed in ("甲书", "乙书", "第三本", 42):
            rows = self.plan(self.canon(seed), 180)
            for i, row in enumerate(rows):
                self.assertTrue(row["payoff_due"])
                self.assertIn(row["payoff_type"], MENU)
                if i >= 2:
                    self.assertGreater(len({r["payoff_type"] for r in rows[i - 2 : i + 1]}), 1)
                if i >= 9:
                    self.assertGreaterEqual(len({r["payoff_type"] for r in rows[i - 9 : i + 1]}), 3)

    def test_future_reservations_execution_and_checkpoint_roundtrip_agree(self):
        canon = self.canon("断点续写的一本书")
        planned = self.plan(canon)
        actual = self.plan(canon, actual=True)
        keys = ("payoff_type", "payoff_due", "growth_setup", "upgrade_due", "next_upgrade", "hook_type")
        self.assertEqual([{k: r[k] for k in keys} for r in planned], [{k: r[k] for k in keys} for r in actual])
        for cut in (1, 9, 10, 19, 25, 49, 99):
            resumed = self.plan(canon, start=json.loads(json.dumps(planned[:cut], ensure_ascii=False)))
            self.assertEqual(planned, resumed)
        before = deepcopy((canon, planned))
        choose_contract(canon, planned[:30], 31)
        self.assertEqual((canon, planned), before)

    def test_pending_upgrade_precedes_calendar_and_blocked_debt_remains_explicit(self):
        canon = self.canon("升级债务")
        plan = choose_contract(canon, [], 2, pending="升级")
        self.assertEqual(plan["payoff_type"], "升级")
        self.assertTrue(plan["upgrade_due"])
        self.assertEqual(plan["next_upgrade"], 2)
        self.assertIsNone(plan["deferred_payoff"])
        self.assertNotIn("pending_contract_blocked", plan["warnings"])
        self.assertNotIn("growth_target", plan)
        for rows in (
            [dict(number=n, reserved_payoff_type="升级") for n in (1, 2)],
            [dict(number=n, reserved_payoff_type="升级" if n % 2 else "打脸") for n in range(1, 10)],
        ):
            result = choose_contract(canon, rows, len(rows) + 1, pending="升级")
            self.assertNotEqual(result["payoff_type"], "升级")
            self.assertIn("pending_contract_blocked", result["warnings"])
            self.assertEqual(result["deferred_payoff"], "升级")
        calendar = self.plan(canon, 20)
        n = next(r["number"] for r in calendar if r["upgrade_due"])
        result = choose_contract(canon, [], n, pending="打脸")
        self.assertEqual(result["payoff_type"], "打脸")
        self.assertFalse(result["upgrade_due"])

    def test_schedule_configuration_is_data_and_top_grade_has_no_extra_upgrade(self):
        canon = self.canon("测试配置")
        self.assertEqual(canon["scheduling"]["upgrade_gap"], [4, 8])
        self.assertEqual(canon["scheduling"]["setup_lead_chapters"], 1)
        canon["ladder"]["strength"] = ["初阶", "终阶"]
        rows = self.plan(canon, 30)
        self.assertEqual(sum(r["upgrade_due"] for r in rows), 1)
        self.assertIsNone(rows[-1]["next_upgrade"])
        canon = self.canon("配置覆盖")
        canon["scheduling"]["upgrade_gap"] = [4, 4]
        self.assertEqual([r["number"] for r in self.plan(canon, 20) if r["upgrade_due"]], [4, 8, 12, 16, 20])
