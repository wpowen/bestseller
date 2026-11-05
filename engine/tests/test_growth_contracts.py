"""Behavior-preserving split of test_growth_contracts.py."""

from support_growth_contracts import *


class ContractsTestsPart1(ContractsTests):
    def test_malformed_extraction_keeps_finished_new_fields_but_not_incomplete_ledgers(self):
        from bookengine.longutil import loose_object

        fields = dict(
            summary="拿回旧物。",
            state={},
            type="收获",
            payoff={"type": "收获", "fulfilled": True, "quote": "她拿回旧物。"},
            gains=[{"type": "收获", "content": "取回旧物", "quote": "她拿回旧物。"}],
            costs=[{"type": "代价", "content": "用掉绳索", "quote": "绳索断了。"}],
            solutions=[{"type": "实力压制", "content": "当场制住对手", "quote": "她按住对手。"}],
            demotion_event={"quote": "他失去了一级修为。"},
            structure="潜入",
        )
        # A malformed unrelated container forces field-level recovery, while
        # every extraction field below is still a complete JSON literal.
        broken = '{"broken":[BROKEN},' + json.dumps(fields, ensure_ascii=False)[1:]
        recovered = loose_object(broken)
        for key, value in fields.items():
            with self.subTest(field=key):
                self.assertEqual(recovered.get(key), value)
        self.assertTrue(recovered.recovered_output)
        partial = {k: v for k, v in fields.items() if k != "gains"}
        truncated = (
            '{"broken":[BROKEN},'
            + json.dumps(partial, ensure_ascii=False)[1:-1]
            + ',"gains":[{"type":"收获","content":"未完成'
        )
        recovered = loose_object(truncated)
        self.assertNotIn("gains", recovered)
        for key, value in partial.items():
            self.assertEqual(recovered.get(key), value)

    def test_extract_first_request_contains_all_six_actual_types_and_original_quote(self):
        from bookengine import q1

        text = "她拿回了自己的灵石。"
        nested = dict(
            gains=[dict(type="收获", content="取回自己的灵石", quote=text)], costs=[], solutions=[], advances=[]
        )
        reply = dict(
            type="收获", hook_type="危机", state=deepcopy(nested), payoff=dict(type="收获", fulfilled=True, quote=text)
        )

        class CaptureStage:
            prefix = ""

            def __init__(self):
                self.calls = []

            def stage(self, name, prompt, schema=None, **kwargs):
                self.calls.append((name, prompt, schema, kwargs))
                return deepcopy(reply)

        with tempfile.TemporaryDirectory() as directory:
            client = CaptureStage()
            client.out = Path(directory)
            result, failed, retries = q1.extract_state(
                client,
                "type-input-regression",
                text,
                {},
                {"canon": deepcopy(PACK)},
                {"number": 1, "payoff_due": True, "payoff_type": "收获"},
                [],
            )
        self.assertEqual((failed, retries), (False, 0))
        for key, value in {**reply, **nested}.items():
            with self.subTest(field=key):
                self.assertEqual(result[key], value)
        self.assertEqual(
            q1.observations(result, text, 1)["gains"], [dict(chapter=1, type="收获", content="取回自己的灵石")]
        )
        self.assertEqual(len(client.calls), 1)
        prompt = client.calls[0][1]
        legal = json.loads(prompt.split("## 合法值\n", 1)[1].split("\n\n## ", 1)[0])
        self.assertEqual(legal.get("types"), ["对抗", "谋划", "情感", "日常", "揭秘", "收获"])
        self.assertEqual(legal["hook_types"], HOOKS)
        self.assertIn(text, prompt)
        self.assertEqual(result["payoff"]["quote"], text)
        self.assertNotIn("recovered_previous", json.loads(client.calls[0][2]))
        self.assertEqual(client.calls[0][3]["effort"], "low")

    def test_power_realm_tracks_guarded_dashboard_without_removing_injury_or_blocking_real_demotion(self):
        previous = dict(dashboard={"strength": "通脉"}, power_status={"境界": "通脉", "限制": "左臂不能发力"})
        raw = dict(state={"dashboard": {"strength": "入门"}, "power_status": {"境界": {"值": "入门", "最后更新": 3}}})
        result, warnings = reduce_growth(previous, raw, {"canon": PACK}, "他按住伤口。", 3)
        self.assertEqual(result["dashboard"]["strength"], "通脉")
        self.assertEqual(result["power_status"]["境界"]["值"], "通脉")
        self.assertEqual(result["power_status"]["限制"]["值"], "左臂不能发力")
        self.assertIn("unsupported_demotion", warnings)
        self.assertIn("power_realm_synced_to_dashboard", warnings)
        text = "他从通脉跌回入门。"
        raw["demotion_event"] = {"quote": text}
        result, warnings = reduce_growth(previous, raw, {"canon": PACK}, text, 3)
        self.assertEqual(result["dashboard"]["strength"], "入门")
        self.assertEqual(result["power_status"]["境界"]["值"], "入门")
        self.assertFalse(warnings)

    def test_deep_ledger_lift_is_not_a_substitute_for_missing_chapter_classification(self):
        from bookengine.q1 import normalize_extract

        text = "她拿回了自己的灵石。"
        ledger = dict(gains=[dict(type="收获", content="取回灵石", quote=text)], costs=[], solutions=[])
        raw = dict(
            state=dict(power_status={"当前状态": {"值": dict(ledger, 症状="仍然手痛"), "最后更新": 1}}),
            payoff=dict(type="收获", fulfilled=True, quote=text),
            hook_type="危机",
        )
        before = deepcopy(raw)
        result = normalize_extract(raw)
        self.assertNotIn("type", result)
        self.assertEqual(raw, before)
        self.assertEqual(result["gains"], ledger["gains"])
        self.assertEqual(observations(result, text, 1)["gains"], [dict(chapter=1, type="收获", content="取回灵石")])
        power = power_merge({"代价": "旧伤"}, raw["state"]["power_status"], 2)
        self.assertEqual(power["代价"]["值"], "旧伤")
        self.assertEqual(power["当前状态"]["值"], {"症状": "仍然手痛"})
        self.assertEqual(power_merge({}, {"costs": [{"value": "旧伤"}]}, 2)["代价"]["值"], [{"值": "旧伤"}])

    def test_canon_strength_requires_exact_level_and_real_demotion_quote_and_handles_null_patch(self):
        canon = deepcopy(PACK)
        old = {"strength": "通脉", "wealth": "3灵石", "unknown": "keep"}
        text = "毒针封住经脉，他从通脉跌回入门。"
        for value in ("普通", "入门", "通脉境", "通脉（受伤）", {"realm": "凝元"}):
            result, warnings = dashboard_guard(old, {"strength": value}, canon, text)
            self.assertEqual(result["strength"], "通脉")
            self.assertTrue(warnings)
            self.assertNotIn("unknown", result)
        for patch in (None, [], {"strength": None}):
            self.assertEqual(
                dashboard_guard(old, patch, canon, text), ({k: v for k, v in old.items() if k != "unknown"}, [])
            )
        self.assertEqual(dashboard_guard(None, None, canon, text), ({}, []))
        result, warnings = dashboard_guard(old, {"strength": "入门"}, canon, text, {"quote": "他从通脉跌回入门。"})
        self.assertEqual(result["strength"], "入门")
        self.assertFalse(warnings)
        self.assertEqual(
            dashboard_guard(old, {"strength": "入门"}, canon, text, {"quote": "编造失阶"})[0]["strength"], "通脉"
        )
        self.assertEqual(dashboard_guard(old, {"strength": "凝元"}, canon, text)[0]["strength"], "凝元")
        self.assertEqual(old, {"strength": "通脉", "wealth": "3灵石", "unknown": "keep"})

    def test_power_six_chinese_fields_unknown_english_names_are_values_and_lru_uses_update_time(self):
        old = {
            "cost": {"value": "折寿", "last_updated": 1},
            "limit": "每次一击",
            "blocked": ["剑意"],
            "balances": {"life": 2},
            "status": "行动受限",
            "realm": "通脉",
        }
        snap = deepcopy(old)
        result = power_merge(old, {"代价": "新代价", "foreign_rule": {"unknown_domain": 7}}, 8)
        self.assertEqual(set(result), {"代价", "限制", "封禁", "余额", "当前状态", "境界"})
        self.assertEqual(result["境界"]["值"], "通脉")
        self.assertEqual(result["余额"]["值"], {"寿命": 2})
        self.assertEqual(result["代价"], {"值": "新代价", "最后更新": 8})
        self.assertEqual(result["当前状态"]["值"]["状态"], "行动受限")
        self.assertEqual(result["当前状态"]["值"]["扩展"][0]["名称"], "foreign_rule")
        self.assertEqual(result["当前状态"]["值"]["扩展"][0]["值"]["扩展"][0], {"名称": "unknown_domain", "值": 7})

        def assert_chinese_keys(value):
            if isinstance(value, dict):
                for key, item in value.items():
                    self.assertFalse(any(c.isascii() and c.isalpha() for c in key), key)
                    assert_chinese_keys(item)
            elif isinstance(value, list):
                for item in value:
                    assert_chinese_keys(item)

        assert_chinese_keys(result)
        self.assertEqual(old, snap)
        self.assertEqual(power_merge(result, {"境界": {"值": None, "最后更新": None}}, 9), result)
        domains = [
            dict(名称="旧域", 值="old", 最后更新=1),
            dict(名称="常用域", 值="hot", 最后更新=7),
            dict(名称="次旧域", 值="warm", 最后更新=3),
        ]
        result = power_merge({"当前状态": {"值": {"扩展": domains}, "最后更新": 7}}, {"新域": "new"}, 9, limit=3)
        self.assertEqual({x["名称"] for x in result["当前状态"]["值"]["扩展"]}, {"常用域", "次旧域", "新域"})
        self.assertEqual(next(x for x in result["当前状态"]["值"]["扩展"] if x["名称"] == "常用域")["最后更新"], 7)

    def test_extract_never_fills_plan_values_and_false_needs_no_negative_evidence(self):
        text = "他抢回灵石，逼退了守门人。"
        raw = dict(
            type="对抗", hook_type="危机", state={}, payoff=dict(type="收获", fulfilled=True, quote="他抢回灵石")
        )
        snap = deepcopy(raw)
        self.assertTrue(valid_extract(raw, text, HOOKS, MENU))
        self.assertEqual(raw, snap)
        for bad in (
            {},
            {k: v for k, v in raw.items() if k != "state"},
            dict(raw, state=None),
            dict(raw, state=[]),
            dict(raw, state="partial"),
            dict(raw, type=""),
            dict(raw, hook_type="计划钩子"),
            dict(raw, payoff=dict(raw["payoff"], fulfilled="true")),
            dict(raw, payoff=dict(raw["payoff"], fulfilled=1)),
            dict(raw, payoff=dict(raw["payoff"], quote="")),
            dict(raw, payoff=dict(raw["payoff"], quote="没有发生的句子")),
            dict(raw, payoff=dict(raw["payoff"], type="收获子型")),
        ):
            with self.subTest(bad=bad):
                self.assertFalse(valid_extract(bad, text, HOOKS, MENU))
        for quote in ("", None, "他抢回灵石"):
            value = dict(raw, payoff=dict(type="无兑现", fulfilled=False, quote=quote))
            self.assertTrue(valid_extract(value, text, HOOKS, MENU))
            self.assertFalse(payoff_ok(value["payoff"], "无兑现", text, MENU))
        for quote in ("", None, text):
            self.assertFalse(
                valid_extract(dict(raw, payoff=dict(type="升级", fulfilled=False, quote=quote)), text, HOOKS, MENU)
            )
        self.assertFalse(
            valid_extract(dict(raw, payoff=dict(type="无兑现", fulfilled=True, quote=text)), text, HOOKS, MENU)
        )
        self.assertFalse(
            valid_extract(dict(raw, payoff=dict(type="无兑现", fulfilled=False, quote="伪造证据")), text, HOOKS, MENU)
        )
        self.assertTrue(payoff_ok(raw["payoff"], "收获", text, MENU))
        self.assertFalse(payoff_ok(raw["payoff"], "升级", text, MENU))

    def test_partial_power_update_keeps_unmentioned_action_limits_and_balance_units(self):
        old = {
            "当前状态": {
                "值": {"行动限制": {"右臂": "不可用", "左腿": "活动受限"}, "记忆": "缺失旧片段"},
                "最后更新": 3,
            },
            "封禁": {"值": {"剑意": "封禁"}, "最后更新": 2},
            "余额": {"值": {"寿命": {"数量": 2, "单位": "天"}, "灵石": {"数量": 3, "单位": "枚"}}, "最后更新": 3},
        }
        patch = {
            "当前状态": {"值": {"行动限制": {"左腿": "活动正常"}}, "最后更新": 4},
            "余额": {"值": {"寿命": {"数量": 1}}, "最后更新": 4},
        }
        before = deepcopy((old, patch))
        result = power_merge(old, patch, 4)
        self.assertEqual(
            result["当前状态"]["值"], {"行动限制": {"右臂": "不可用", "左腿": "活动正常"}, "记忆": "缺失旧片段"}
        )
        self.assertEqual(result["封禁"], old["封禁"])
        self.assertEqual(result["余额"]["值"]["寿命"], {"数量": 1, "单位": "天"})
        self.assertEqual(result["余额"]["值"]["灵石"], old["余额"]["值"]["灵石"])
        self.assertEqual(result["当前状态"]["最后更新"], 4)
        self.assertEqual((old, patch), before)

    def test_data_aliases_record_joint_naming_as_same_canonical_solution(self):
        data = yaml.safe_load((Path(__file__).resolve().parents[1] / "data/growth_payoff.yaml").read_text())
        canon = dict(deepcopy(data["packs"]["suspense-mystery"]), solution_aliases=data["solution_aliases"])
        self.assertEqual(canon["solution_aliases"]["共同喊名"], "群体表态")
        self.assertNotIn("拆册共管", canon["solution_aliases"])
        self.assertIn("群体表态", canon["solution_menu"])
        self.assertNotIn("集体认同", canon["solution_menu"])
        old = {"solutions": [dict(chapter=1, type="集体认同", content="曾共同见证身份")]}
        text = "众人共同喊出了真名。"
        raw = {"state": {}, "solutions": [dict(type="共同喊名", content="当场共同确认身份", quote=text)]}
        before = deepcopy((old, raw, canon))
        state, warnings = reduce_growth(old, raw, {"canon": canon}, text, 20)
        self.assertEqual(state["solutions"][-1], dict(chapter=20, type="群体表态", content="当场共同确认身份"))
        self.assertNotIn("solution_repeat_30", warnings)
        self.assertNotIn("quote", state["solutions"][-1])
        self.assertEqual((old, raw, canon), before)

    def test_legacy_recognition_and_shared_actions_are_not_oral_group_proxies(self):
        data = yaml.safe_load((Path(__file__).resolve().parents[1] / "data/growth_payoff.yaml").read_text())
        canon = dict(
            data["packs"]["suspense-mystery"], solution_aliases=data["solution_aliases"], final_volume_start=50
        )
        old = {
            "solutions": [
                dict(chapter=1, type="集体认同", content="旧记录机理未审"),
                dict(chapter=50, type="拆册共管", content="旧册分处保管"),
            ]
        }
        before = deepcopy(old)
        plan = choose_contract(canon, [], 90, state=old)
        self.assertTrue(plan["group_allowed"])
        self.assertNotIn("群体表态", plan["solution_avoid"])
        self.assertEqual(plan["group_history_unknown"], 1)
        self.assertIn("未审", data["solution_mechanisms"]["集体认同"])
        for name in ("拆册", "拆册共管", "分印共管", "共同见证"):
            self.assertNotEqual(canon["solution_aliases"].get(name), "群体表态")
        text = "众人齐声拒绝代签。她将照明储备改供救援设备。"
        raw = dict(
            state={}, solutions=[dict(type="众人作证", content="齐声拒绝解除代签控制", quote="众人齐声拒绝代签。")]
        )
        state, warnings = reduce_growth(old, raw, {"canon": canon}, text, 90)
        self.assertEqual(state["solutions"][:2], old["solutions"])
        self.assertEqual(old, before)
        self.assertEqual(state["solutions"][-1]["type"], "群体表态")
        self.assertNotIn("group_solution_book_cap", warnings)
        self.assertNotIn("group_solution_final_cap", warnings)
        action = dict(
            state={},
            solutions=[dict(type="资源重配", content="将照明储备改供救援设备", quote="她将照明储备改供救援设备。")],
        )
        state, warnings = reduce_growth(old, action, {"canon": canon}, text, 90)
        self.assertEqual(state["solutions"][-1]["type"], "资源重配")
        self.assertNotIn("group_solution_final_cap", warnings)
        self.assertTrue(choose_contract(canon, [], 91, state=state)["group_allowed"])
