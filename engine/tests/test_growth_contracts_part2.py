"""Behavior-preserving split of test_growth_contracts.py."""

from support_growth_contracts import *


class ContractsTestsPart2(ContractsTests):
    def test_legacy_unknown_conservatively_reserves_group_budget_without_rewriting_bible(self):
        data = yaml.safe_load((Path(__file__).resolve().parents[1] / "data/growth_payoff.yaml").read_text())
        bible = dict(canon=dict(data["packs"]["suspense-mystery"], solution_menu=["集体认同", "资源重配"]))
        canon = dict(
            bible["canon"],
            solution_menu=data["packs"]["suspense-mystery"]["solution_menu"],
            solution_aliases=data["solution_aliases"],
            final_volume_start=50,
        )
        legacy = {"solutions": [dict(chapter=n, type="集体认同", content="多人共同认可，主机理待核") for n in (1, 50)]}
        before = deepcopy((bible, legacy, canon))
        result = choose_contract(canon, [], 90, state=legacy)
        self.assertEqual(result["group_history_unknown"], 2)
        self.assertFalse(result["group_allowed"])
        self.assertIn("群体表态", result["solution_avoid"])
        self.assertEqual((bible, legacy, canon), before)
        text = "众人齐声声明拒绝代签。"
        raw = dict(state={}, solutions=[dict(type="众人作证", content="共同拒绝使代签失效", quote=text)])
        state, warnings = reduce_growth(legacy, raw, {"canon": canon}, text, 90)
        self.assertEqual(state["solutions"][:2], legacy["solutions"])
        self.assertIn("group_solution_book_cap", warnings)
        self.assertIn("group_solution_final_cap", warnings)
        for content in ("众人齐声喊名解除控制", "众人作证使伪证失效", "众人共同拒绝代签"):
            records = {"solutions": [dict(chapter=1, type="集体认同", content=content), legacy["solutions"][1]]}
            plan = choose_contract(canon, [], 90, state=records)
            self.assertEqual(plan["group_history_unknown"], 1)
            self.assertFalse(plan["group_allowed"])
            normalized, _ = reduce_growth(records, dict(state={}), {"canon": canon}, "", 90)
            self.assertEqual(normalized["solutions"][0]["type"], "群体表态")
            self.assertEqual(normalized["solutions"][0]["classification_basis"], "legacy_explicit_group_content")
            self.assertEqual(normalized["solution_history"][0], records["solutions"][0])
        for rows in (
            [],
            [dict(number=n, solutions=[dict(chapter=n, type="资源重配", content="重配物资")]) for n in range(1, 21)],
        ):
            self.assertEqual(choose_contract(canon, rows, 21)["group_history_unknown"], 0)

    def test_unresolved_levels_do_not_create_upgrade_obligations(self):
        from bookengine.contracts import growth_obligations

        for level in (None, "旧未知等级"):
            old = dict(
                dashboard={"strength": level}, realm_progress={"level": "通脉", "upgrade_at": 2, "displayed_at": None}
            )
            before = deepcopy(old)
            state, _ = reduce_growth(old, dict(state={}), {"canon": PACK}, "她仍未确认境界。", 5)
            self.assertEqual(state["dashboard"]["strength"], level)
            self.assertEqual(old, before)
            self.assertIsNone(state["realm_progress"]["upgrade_at"])
            self.assertFalse(growth_obligations(state, 6, PACK)["display_required"])

    def test_observations_plural_solutions_and_growth_merge_do_not_store_quotes_or_duplicate(self):
        text = "他抢回灵石。她诱敌离位。"
        raw = dict(
            state={"dashboard": {"strength": "通脉"}, "power_status": {"realm": "通脉"}},
            gains=[dict(type="收获", content="取回灵石", quote="他抢回灵石。")],
            costs=[dict(content="已经付出", quote="他抢回灵石。"), dict(content="虚构付出", quote="未发生")],
            solutions=[dict(category="诱敌离位", quote="她诱敌离位。")],
        )
        old = dict(
            custom={"unknown": True},
            dashboard={"strength": "入门"},
            solutions=[dict(chapter=1, type="诱敌离位", content="旧解法")],
        )
        snap = deepcopy((old, raw))
        literal = observations(raw, text, 30)
        self.assertEqual(literal["costs"], [dict(chapter=30, type="代价", content="已经付出")])
        self.assertEqual(literal["solutions"], [dict(chapter=30, type="诱敌离位", content="诱敌离位")])
        self.assertNotIn("quote", json.dumps(literal))
        state, warnings = reduce_growth(old, raw, {"canon": PACK}, text, 30)
        self.assertNotIn("solution_repeat_30", warnings)
        self.assertEqual(state["dashboard"]["strength"], "通脉")
        self.assertEqual(state["power_status"]["境界"]["值"], "通脉")
        self.assertEqual(state["custom"], old["custom"])
        self.assertEqual(reduce_growth(state, raw, {"canon": PACK}, text, 30)[0], state)
        self.assertNotIn("solution_repeat_30", reduce_growth(old, raw, {"canon": PACK}, text, 31)[1])
        alias_raw = dict(raw, solutions=[dict(category="诱开守卫", quote="她诱敌离位。")])
        alias_canon = dict(PACK, solution_aliases={"诱开守卫": "诱敌离位"})
        alias_state, alias_warn = reduce_growth(old, alias_raw, {"canon": alias_canon}, text, 30)
        self.assertEqual(alias_state["solutions"][-1]["type"], "诱敌离位")
        self.assertNotIn("solution_repeat_30", alias_warn)
        self.assertEqual((old, raw), snap)
        self.assertEqual(observations({"solutions": None}, text, 30).get("solutions", []), [])
        self.assertIsInstance(reduce_growth(None, None, {"canon": PACK}, text, 1)[0], dict)

    def test_growth_warnings_use_real_volume_ledger_and_ordered_dashboard(self):
        before = {"strength": "入门", "wealth": "1灵石", "status": "无名"}
        state = {"gains": [dict(chapter=2), dict(chapter=25)], "costs": [dict(chapter=3)] * 3}
        self.assertEqual(growth_warnings(state, before, dict(before, strength="通脉"), PACK, 1, 25), [])
        state["costs"].append(dict(chapter=20))
        warnings = growth_warnings(state, before, before, PACK, 1, 25)
        self.assertIn("dashboard_no_growth", warnings)
        self.assertIn("cost_gain_ratio", warnings)
        state["gains"] = [dict(chapter=2), dict(chapter=26)]
        self.assertIn("gains_below_two", growth_warnings(state, before, before, PACK, 1, 25))
        self.assertEqual(growth_warnings(state, before, before, dict(PACK, genre_kind="悬疑"), 1, 25), [])

    def test_daily_payoff_pending_and_suspense_frequency(self):
        rows = [
            dict(number=n, hook_type=HOOKS[(n - 1) % 5], payoff=dict(type=MENU[(n - 1) % 5], fulfilled=True))
            for n in range(1, 41)
        ]
        plan = choose_contract(PACK, rows, 41)
        self.assertTrue(plan["payoff_due"])
        self.assertIn(plan["payoff_type"], MENU)
        self.assertLessEqual(sum(r["hook_type"] == plan["hook_type"] for r in rows[-29:]) + 1, 30 * 0.25)
        self.assertTrue(choose_contract(PACK, rows + [plan], 42)["payoff_due"])
        pending = choose_contract(PACK, rows, 41, "升级")
        self.assertEqual(pending["payoff_type"], "升级")
        self.assertNotIn("pending_contract_blocked", pending["warnings"])
        repeated = [dict(number=n, payoff=dict(type="升级", fulfilled=True)) for n in (1, 2)]
        blocked = choose_contract(PACK, repeated, 3, "升级")
        self.assertTrue(blocked["payoff_due"])
        self.assertNotEqual(blocked["payoff_type"], "升级")
        self.assertIn("pending_contract_blocked", blocked["warnings"])
        self.assertEqual(choose_contract(PACK, [dict(solution_type="抢先夺物")], 3)["solution_type"], "抢先夺物")
        suspense = dict(PACK, genre_kind="悬疑")
        self.assertFalse(choose_contract(suspense, [], 2)["payoff_due"])
        self.assertTrue(choose_contract(suspense, [], 1)["payoff_due"])
        self.assertNotEqual(choose_contract(PACK, [dict(hook_type="危机")] * 3, 4)["hook_type"], "危机")
        with self.assertRaises(ValueError):
            choose_contract(dict(PACK, payoff_menu=["升级之子型"]), [], 1)

    def test_jump_rejected_without_manufacturing_intermediate_upgrade(self):
        old = {"strength": "入门"}
        before = deepcopy(old)
        result, warnings = dashboard_guard(old, {"strength": "化境"}, PACK, "他自称化境。")
        self.assertEqual(result, old)
        self.assertEqual(old, before)
        self.assertIn("unsupported_upgrade", warnings)
        state, warnings = reduce_growth(
            dict(dashboard=old, power_status={"限制": "右臂不可用"}),
            dict(state={"dashboard": {"strength": "化境"}, "power_status": {"境界": "化境"}}),
            {"canon": PACK},
            "他自称化境。",
            7,
        )
        self.assertEqual(state["dashboard"], old)
        self.assertEqual(state["power_status"]["境界"]["值"], "入门")
        self.assertEqual(state["power_status"]["限制"]["值"], "右臂不可用")

    def test_realm_obligation_only_literal_true_and_body_quote_discharge_it(self):
        from bookengine.contracts import growth_obligations

        old = {"dashboard": {"strength": "入门"}}
        text = "他踏入通脉，隔空震碎石门。"
        raw = {"state": {"dashboard": {"strength": "通脉"}}}
        state, _ = reduce_growth(old, raw, {"canon": PACK}, text, 10)
        before = deepcopy(state)
        obligation = growth_obligations(state, 11, PACK)
        self.assertEqual(state, before)
        self.assertEqual(
            {k: obligation[k] for k in ("level", "upgrade_at", "display_by", "display_required")},
            dict(level="通脉", upgrade_at=10, display_by=13, display_required=True),
        )
        plan = choose_contract(PACK, [], 11, pending="升级", state=state)
        self.assertEqual(plan["growth_obligation"], obligation)
        self.assertEqual(plan["payoff_type"], "升级")
        self.assertNotIn("pending_contract_blocked", plan["warnings"])
        self.assertTrue(obligation["display_before_upgrade"])
        self.assertIn("先展示当前阶", obligation["required_action"])
        for displayed, quote in ((True, "不存在"), ("true", text), (1, text), (False, text)):
            value, _ = reduce_growth(
                state, dict(state={}, realm_displayed=displayed, realm_display_quote=quote), {"canon": PACK}, text, 11
            )
            self.assertTrue(growth_obligations(value, 14, PACK)["display_required"])
        shown, _ = reduce_growth(
            state, dict(state={}, realm_displayed=True, realm_display_quote="隔空震碎石门"), {"canon": PACK}, text, 12
        )
        self.assertFalse(growth_obligations(shown, 13, PACK)["display_required"])
        self.assertFalse(growth_obligations(shown, 13, PACK)["display_before_upgrade"])
        self.assertEqual(shown["realm_progress"]["displayed_at"], 12)
        again, _ = reduce_growth(shown, dict(state={}), {"canon": PACK}, text, 13)
        self.assertEqual(again["realm_progress"], shown["realm_progress"])
        self.assertEqual(reduce_growth(old, raw, {"canon": PACK}, text, 10)[0], state)
        next_level, _ = reduce_growth(shown, dict(state={"dashboard": {"strength": "凝元"}}), {"canon": PACK}, text, 13)
        self.assertEqual(growth_obligations(next_level, 14, PACK)["display_by"], 16)
        self.assertTrue(growth_obligations(next_level, 14, PACK)["display_required"])

    def test_solution_windows_primary_history_and_group_lifetime_caps(self):
        data = yaml.safe_load((Path(__file__).resolve().parents[1] / "data/growth_payoff.yaml").read_text())
        canon = dict(data["packs"]["suspense-mystery"], solution_aliases=data["solution_aliases"])
        old = {
            "solutions": [
                dict(chapter=1, type="实力压制", content="正面制服"),
                dict(chapter=1, type="信息欺骗", content="诱敌造成错误判断", primary=True),
                dict(chapter=10, type="信息欺骗", content="再度用诱饵使守卫离开"),
            ]
        }
        before = deepcopy(old)
        text = "她用假信引开了守卫。"
        raw = {
            "state": {},
            "solutions": [
                dict(type="信息欺骗", content="以假信使守卫离开", quote=text),
                dict(type="实力压制", content="制服", quote=text),
            ],
        }
        state, warnings = reduce_growth(old, raw, {"canon": canon}, text, 20)
        self.assertEqual(old, before)
        self.assertEqual(len(state["solutions"]), 3)
        self.assertEqual(state["solutions"][0]["type"], "信息欺骗")
        self.assertTrue(state["solutions"][0]["primary_basis"])
        self.assertEqual(state["solution_history"][:2], old["solutions"][:2])
        self.assertEqual(len(state["solution_history"]), 5)
        self.assertIn("solution_repeat_30", warnings)
        self.assertEqual(state["solutions"][-1]["type"], "信息欺骗")
        recent = {"solutions": [dict(chapter=19, type="信息欺骗", content="假信")]}
        self.assertIn("solution_repeat_5", reduce_growth(recent, raw, {"canon": canon}, text, 20)[1])

        def planned(chapters, idx):
            rows = [
                dict(number=n, solutions=[dict(chapter=n, type="信息欺骗", content="假信使对方离开")]) for n in chapters
            ]
            return choose_contract(canon, rows, idx)

        self.assertNotIn("信息欺骗", planned([1], 20)["solution_avoid"])
        self.assertIn("信息欺骗", planned([1, 10], 20)["solution_avoid"])
        self.assertNotIn("信息欺骗", planned([1, 10], 31)["solution_avoid"])
        self.assertIn("信息欺骗", planned([10], 14)["solution_avoid"])
        self.assertNotIn("信息欺骗", planned([10], 15)["solution_avoid"])
        groups = [dict(number=n, solutions=[dict(chapter=n, type="共同喊名", content="齐声")]) for n in (1, 50)]
        contract = choose_contract(canon, groups, 90, final_volume_start=76)
        self.assertFalse(contract["group_allowed"])
        self.assertIn("群体表态", contract["solution_avoid"])
        contract = choose_contract(canon, groups[1:], 90, final_volume_start=50)
        self.assertFalse(contract["group_allowed"])
        self.assertTrue(choose_contract(canon, groups[:1], 50, final_volume_start=50)["group_allowed"])
        self.assertEqual(canon["solution_aliases"]["众人作证"], "群体表态")
        current = dict(state={}, solutions=[dict(type="众人作证", content="共同作证解除控制", quote=text)])
        group_state = {"solutions": [x for row in groups for x in row["solutions"]]}
        self.assertIn("group_solution_book_cap", reduce_growth(group_state, current, {"canon": canon}, text, 90)[1])
        last_canon = dict(canon, final_volume_start=50)
        self.assertIn(
            "group_solution_final_cap", reduce_growth(group_state, current, {"canon": last_canon}, text, 90)[1]
        )

    def test_one_primary_new_injury_preserves_extra_body_facts_and_old_limits(self):
        text = "他右臂骨折，左眼失明，付出三天寿命。"
        old = {"power_status": {"限制": "旧伤不能奔跑"}}
        raw = dict(
            state={"power_status": {"当前状态": {"右臂": "骨折", "左眼": "失明"}}},
            injuries=[dict(content="右臂骨折", quote="右臂骨折"), dict(content="左眼失明", quote="左眼失明")],
            costs=[dict(type="身体代价", content="付出三天寿命", quote="付出三天寿命")],
        )
        before = deepcopy((old, raw))
        state, warnings = reduce_growth(old, raw, {"canon": PACK}, text, 3)
        self.assertEqual(state["injuries_count"], 3)
        self.assertEqual(len(state["injuries"]), 1)
        self.assertEqual(state["injuries"][0]["content"], "右臂骨折")
        self.assertIn("injuries_over_one", warnings)
        self.assertEqual(state["power_status"]["限制"]["值"], "旧伤不能奔跑")
        self.assertEqual(state["power_status"]["当前状态"]["值"], {"右臂": "骨折", "左眼": "失明"})
        self.assertEqual([x["content"] for x in state["injury_facts"]], ["右臂骨折", "左眼失明", "付出三天寿命"])
        self.assertEqual((old, raw), before)
        self.assertEqual(reduce_growth(state, raw, {"canon": PACK}, text, 3)[0], state)
        self.assertEqual(
            reduce_growth(
                old, dict(state={}, injuries=[dict(content="新伤", quote="未出现")]), {"canon": PACK}, text, 3
            )[0]["injuries_count"],
            0,
        )
        same = dict(
            state={},
            injuries=[dict(content="右臂骨折", quote=text)],
            costs=[dict(type="身体代价", content="右臂折断的代价", quote=text)],
        )
        self.assertEqual(reduce_growth(old, same, {"canon": PACK}, text, 3)[0]["injuries_count"], 1)
        same["injuries"].append(dict(content="左眼失明", quote=text))
        self.assertEqual(reduce_growth(old, same, {"canon": PACK}, text, 3)[0]["injuries_count"], 2)

    def test_old_injury_markers_exclude_only_new_counts_and_preserve_raw_and_mixed_new_injuries(self):
        text = "左肩旧伤裂开，旧伤未愈又添右腕骨折，旧伤旁新添刀口。"
        old = {"power_status": {"限制": "左肩旧伤仍影响发力"}}
        for marker in ({"type": "旧伤复发"}, {"type": "旧伤加重"}, {"type": "身体伤残", "new": False}):
            with self.subTest(marker=marker):
                recurring = dict(marker, content="左肩旧伤裂开", quote="左肩旧伤裂开")
                raw = dict(state={}, injuries=[recurring], costs=[dict(recurring, physical=True)])
                before = deepcopy((old, raw))
                state, warnings = reduce_growth(old, raw, {"canon": PACK}, text, 8)
                self.assertEqual(state["injuries_count"], 0)
                self.assertEqual(state["injuries"], [])
                self.assertNotIn("injuries_over_one", warnings)
                self.assertEqual(state["power_status"]["限制"]["值"], "左肩旧伤仍影响发力")
                self.assertEqual((old, raw), before)
                raw["injuries"] += [
                    dict(type="新伤", new=True, content="旧伤未愈又添右腕骨折", quote="旧伤未愈又添右腕骨折")
                ]
                raw["costs"] += [dict(type="身体代价", content="旧伤旁新添刀口", quote="旧伤旁新添刀口")]
                before = deepcopy(raw)
                mixed, warnings = reduce_growth(old, raw, {"canon": PACK}, text, 8)
                self.assertEqual(mixed["injuries_count"], 2)
                self.assertEqual(len(mixed["injuries"]), 1)
                self.assertEqual(
                    [x["content"] for x in mixed["injury_facts"]], ["旧伤未愈又添右腕骨折", "旧伤旁新添刀口"]
                )
                self.assertIn("injuries_over_one", warnings)
                self.assertEqual(raw, before)
