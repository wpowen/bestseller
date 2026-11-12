"""Topic contracts: prompt isolation, deterministic filtering and crash recovery."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from collections import Counter

from ideation import axes_for, generation_prompt, read, run, save, step
from ideation.filters import grams, rotate, screen, similarity, validate


def card(n=0, **extra):
    value = dict(
        title=f"山河第{n}次回声",
        hook=f"风起时第{n}个孩子回家",
        tags=["成长", "反差", "守护"],
        premise="陈禾想救回姐姐，却遭同门拦住去路。",
        golden_finger_rule="无",
        opening="陈禾在山门前拦住送葬队。",
        engine="从村镇到王朝再到异界，依次争取粮食、地位与自由。",
        axes=dict(shell=str(n), variable=chr(0x4E00 + n % 2000) + chr(0x5E00 + n % 2000)),
    )
    value.update(extra)
    return value


class IdeationContracts(unittest.TestCase):
    def test_prompt_allowlist(self):
        market = dict(
            anchors=[dict(title="唯一真实榜单名字")], books=[dict(title="另一真实名")], tags=["成长"], rules=["返还"]
        )
        data = dict(genre="suspense-mystery", chapters=100, idea="失踪的家人", seed=53)
        prompt = generation_prompt(data, [dict(shell="民俗")], market)
        self.assertNotIn("唯一真实", prompt)
        self.assertNotIn("另一真实", prompt)
        self.assertNotIn("返还", prompt)
        self.assertNotIn("废柴", prompt)
        self.assertIn("失踪的家人", prompt)

    def test_model_authors_the_idea_and_fixed_axis_values_win(self):
        from ideation import bind_axes, used_variables

        free = bind_axes(
            dict(card(), variable="会说谎的界碑", desire="带妹妹出山", payoff="守护成功"),
            dict(shell="边关", title_form="短名"),
        )
        self.assertEqual(
            free["axes"],
            dict(
                shell="边关",
                title_form="短名",
                variable="会说谎的界碑",
                desire="带妹妹出山",
                payoff="守护成功",
                variable_type="构思",
                authored=True,
            ),
        )
        self.assertTrue(all(key not in free for key in ("variable", "desire", "payoff")))
        fixed = bind_axes(
            dict(card(), variable="模型想换掉"),
            dict(shell="宗门", variable="纸扎", variable_type="职业", avoid_variable="不该外露"),
        )
        self.assertEqual((fixed["axes"]["variable"], fixed["axes"]["variable_type"]), ("纸扎", "职业"))
        self.assertNotIn("avoid_variable", fixed["axes"])
        self.assertFalse(fixed["axes"]["authored"])
        silent = bind_axes(card(hook="她在雨里等回一封家书"), dict(shell="民俗"))
        self.assertEqual(silent["axes"]["variable"], "她在雨里等回一封家书")
        self.assertEqual(bind_axes("broken", dict(shell="民俗")), "broken")
        history = [free, fixed, free, dict(title="旧卡没有轴")]
        self.assertEqual(used_variables(history), ["会说谎的界碑", "纸扎"])

    def test_prompt_has_no_fixed_idea_pools_and_lists_used_ideas(self):
        data = dict(
            genre="xuanhuan-power-fantasy",
            chapters=50,
            idea="",
            seed=87,
            history=[dict(axes=dict(variable="会说谎的界碑"))],
        )
        axes = axes_for(data, {})[:6]
        prompt = generation_prompt(data, axes, dict(tags=["成长"], rules=["倍率结算", "资源返还"]))
        for polluted in ("倍率结算", "资源返还", "偿清债务", "修伞匠", "木制算盘", "进度条", "类别特征"):
            self.assertNotIn(polluted, prompt)
        self.assertIn("会说谎的界碑", prompt)
        self.assertIn("不要为了填这一栏硬造能力", prompt)

    def test_same_new_idea_is_one_direction(self):
        def idea(shell, variable):
            return dict(shell=shell, variable=variable, authored=True)

        first = card(1, axes=idea("宗门", "会说谎的界碑"))
        second = card(2, title="另一个书名完全不同", hook="钩子也完全是别的内容", axes=idea("边关", "会说谎的界碑"))
        third = card(3, title="第三个书名并不相同", hook="第三个钩子说的是别的", axes=idea("边关", "倒着走的年历"))
        good, bad = screen([first, second, third], 100, "xuanhuan-power-fantasy", [], [])
        self.assertEqual([c["title"] for c in good], [first["title"], third["title"]])
        self.assertEqual(bad[0]["reasons"], ["新变量重复"])
        kept = dict(second, axes=dict(second["axes"], authored=False))
        self.assertEqual(len(screen([first, kept], 100, "xuanhuan-power-fantasy", [], [])[0]), 2)

    def test_axes_reproducible_and_max_two(self):
        data = dict(genre="xuanhuan-power-fantasy", seed=87)
        axes = axes_for(data, {})
        self.assertEqual(axes, axes_for(data, {}))
        self.assertEqual(len(axes), 24)
        self.assertLessEqual(max(Counter(a["shell"] for a in axes).values()), 2)
        self.assertTrue(all(set(a) == {"shell", "title_form"} for a in axes))

    def test_direction_both(self):
        source = dict(shell="宗门", variable="纸扎", variable_type="职业")
        data = dict(genre="xuanhuan-power-fantasy", seed=87, direction=dict(mode="both", candidate=dict(axes=source)))
        axes = axes_for(data, {})[:12]
        self.assertEqual(sum(a["shell"] == source["shell"] for a in axes), 6)
        self.assertEqual(sum(a.get("variable") == source["variable"] for a in axes), 6)
        kept_shell = [a for a in axes if a["variant"] == "shell"]
        self.assertTrue(all("variable" not in a and a["avoid_variable"] == "纸扎" for a in kept_shell))

    def test_fields_language_and_length(self):
        self.assertEqual(validate(card(), 100), [])
        for change in [
            dict(hook="字" * 31),
            dict(tags=["一"]),
            dict(premise="hello"),
            dict(premise="ＮＯ"),
            dict(premise="caf é"),
            dict(engine=""),
            dict(opening="\ufffd"),
        ]:
            self.assertTrue(validate(card(**change), 100), change)
        self.assertEqual(validate(card(engine=""), 5), [])
        self.assertTrue(validate(card(premise="苏小满想逃走。"), 100))

    def test_jaccard_and_collision(self):
        self.assertEqual(similarity("山，河！故人", "山河故人"), 1)
        raw = [card(title="山河故人", hook="山河绕城九日不见故人")]
        good, bad = screen(raw, 100, "suspense-mystery", [], [dict(title="山河故人")])
        self.assertEqual(good, [])
        self.assertIn("撞车:", bad[0]["reasons"][0])
        good, bad = screen(raw, 100, "suspense-mystery", raw, [])
        self.assertEqual(good, [])
        self.assertIn("历史", bad[0]["reasons"][0])

    def test_cliche_is_not_deleted(self):
        from ideation.filters import load

        pairs = load("cliche")
        pair = pairs.get("xuanhuan-power-fantasy", pairs.get("default"))[0]
        good, bad = screen([card(premise="，".join(pair))], 100, "xuanhuan-power-fantasy", [], [])
        self.assertEqual(len(good), 1)
        self.assertTrue(good[0]["cliche"])

    def test_diversity_round_robin(self):
        cards = [
            card(
                n,
                title=("山河，归客" if n % 3 == 0 else "月夜？" if n % 3 == 1 else "竹林") + str(n),
                axes=dict(shell=str(n % 6)),
            )
            for n in range(18)
        ]
        chosen = rotate(cards)
        self.assertEqual(len({c["axes"]["shell"] for c in chosen}), 6)

    def test_steps_replay_completed_and_retry_uncertain_without_erasing_accounting(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            calls = []

            def client(*args, **opts):
                calls.append(opts["stage"])
                return "[1]"

            self.assertEqual(step(root, "gen", "prompt", client, 10), [1])
            self.assertEqual(step(root, "gen", "prompt", client, 10), [1])
            self.assertEqual(calls, ["gen"])
            save(root / "http/ledger.json", [dict(stage="unknown", status="inflight")])
            before = (root / "http/ledger.json").read_bytes()
            self.assertEqual(step(root, "unknown", "prompt", client, 10), [1])
            self.assertEqual(calls, ["gen", "unknown-retry-1"])
            self.assertEqual((root / "http/ledger.json").read_bytes(), before)

    def test_real_process_kill_reuses_completed_step(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = """
from pathlib import Path
import os,sys,time
from ideation import step
root=Path(sys.argv[1])
def client(*args,**kwargs):
 with (root/'charged').open('a') as f:f.write('paid\\n')
 return '[1]'
step(root,'first','same',client,10)
if sys.argv[2]=='kill':os.kill(os.getpid(),9)
step(root,'second','same',client,10)
"""
            env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]))
            first = subprocess.run([sys.executable, "-c", source, str(root), "kill"], env=env, capture_output=True)
            self.assertLess(first.returncode, 0)
            second = subprocess.run([sys.executable, "-c", source, str(root), "resume"], env=env, capture_output=True)
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual((root / "charged").read_text().splitlines(), ["paid", "paid"])

    def test_complete_run_needs_no_client(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            save(root / "topic.json", dict(status="complete", candidates=[card()]))
            self.assertEqual(run(root, None, root)["status"], "complete")

    def test_pipeline_supplements_once_and_judge_failure_keeps_candidates(self):
        titles = [
            "荒村夜渡",
            "雪原孤灯",
            "山海余烬",
            "春潮旧债",
            "古井新声",
            "断桥归客",
            "竹林迷踪",
            "草原长风",
            "深宫暗火",
            "海港残星",
            "荒城旧约",
            "小镇余音",
            "秋江暮雨",
            "西岭孤舟",
            "长街过客",
            "边关故梦",
            "石窟惊雷",
            "南岛归帆",
            "幽谷残月",
            "北原流萤",
            "东巷炊烟",
            "云外故人",
            "红墙秘语",
            "沙洲落雁",
        ]

        class Client:
            def __init__(self):
                self.calls = []

            def __call__(self, messages, **opts):
                self.calls.append(opts["stage"])
                if opts["stage"].startswith("judge"):
                    return "truncated ["
                n = int(opts["stage"][-1])
                values = [
                    card(title=t + ("？" if i % 3 == 0 else "，归来" if i % 3 == 1 else ""), hook=t)
                    for i, t in enumerate(titles[n * 6 : n * 6 + 6])
                ]
                if n == 0:
                    values = [{} for _ in values]
                if n == 1:
                    values[0] = {}
                return json.dumps(values, ensure_ascii=False)

            def estimate(self):
                return 0.003

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = root / "topics" / "one"
            save(folder / "topic.json", dict(status="running", genre="xuanhuan-power-fantasy", chapters=100, idea=""))
            client = Client()
            market = dict(status="missing", date="2026-11-04", books=[])
            with patch("ideation.snapshot", return_value=market), patch("ideation.features", return_value={}):
                result = run(folder, client, root)
                self.assertEqual(len(result["candidates"]), 12)
                self.assertEqual(
                    set(client.calls),
                    {
                        "generate-0",
                        "generate-1",
                        "generate-2",
                        "generate-3",
                        "judge",
                        *(f"judge-retry-{n}" for n in range(1, 6)),
                    },
                )
                self.assertIn("判官格式失败", result["judge_status"])
                self.assertIn("无市场数据", result["notice"])
                run(folder, client, root)
                self.assertEqual(len(client.calls), 10)

    def test_shape_cap_and_direction_groups_survive_ranking(self):
        from ideation import select

        cards = [
            card(n, title=("山河，归客" if n < 10 else "月夜？" if n < 14 else "竹林") + str(n)) for n in range(18)
        ]
        chosen = rotate(cards)
        self.assertEqual(len(chosen), 12)
        from ideation.filters import shape

        self.assertLessEqual(max(Counter(shape(c["title"]) for c in chosen).values()), 7)
        for n, c in enumerate(cards):
            c["axes"]["variant"] = "shell" if n % 2 else "variable"
        chosen = select(cards, dict(direction=dict(mode="both")), True)
        self.assertEqual(Counter(c["axes"]["variant"] for c in chosen), {"shell": 6, "variable": 6})

    def test_direction_shape_cap_applies_to_whole_twelve_not_each_six(self):
        from ideation import select

        cards = []
        for group, counts in [("shell", (5, 2, 1)), ("variable", (9, 2, 0))]:
            for form, count in enumerate(counts):
                for n in range(count):
                    cards.append(
                        card(
                            title="山河" + ("，归来" if form == 1 else "？" if form == 2 else ""),
                            axes=dict(shell=group, variant=group),
                        )
                    )
        chosen = select(cards, dict(direction=dict(mode="both")))
        self.assertEqual(len(chosen), 12)
        self.assertEqual(Counter(c["axes"]["variant"] for c in chosen), {"shell": 6, "variable": 6})

    def test_older_success_cannot_hide_latest_unknown_payload(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            calls = []

            def client(*args, **kwargs):
                calls.append(kwargs)
                return "[1]"

            save(
                root / "http/old.request.json",
                dict(
                    model="MiniMax-M3.1-Flash",
                    messages=[dict(role="user", content="old input")],
                    max_completion_tokens=10,
                ),
            )
            save(
                root / "http/ledger.json",
                [
                    dict(stage="gen", status="ok", request="old.request.json"),
                    dict(stage="gen", status="inflight", request="old.request.json"),
                ],
            )
            with self.assertRaisesRegex(RuntimeError, "输入变化"):
                step(root, "gen", "new input", client, 10)
            self.assertEqual(calls, [])
            save(
                root / "http/old.request.json",
                dict(
                    model="MiniMax-M3.1-Flash",
                    messages=[dict(role="user", content="old input")],
                    max_completion_tokens=10,
                ),
            )
            save(root / "http/ledger.json", [dict(stage="gen", status="ok", request="old.request.json")])
            with self.assertRaisesRegex(RuntimeError, "输入变化"):
                step(root, "gen", "new input", client, 10)
            self.assertEqual(calls, [])

    def test_matching_completed_transport_is_reused_when_step_file_is_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            calls = []

            def cached_transport(*args, **kwargs):
                calls.append(kwargs["stage"])
                return "[1]"

            save(
                root / "http/old.request.json",
                dict(
                    model="MiniMax-M3.1-Flash", messages=[dict(role="user", content="same")], max_completion_tokens=10
                ),
            )
            save(root / "http/ledger.json", [dict(stage="gen", status="ok", request="old.request.json")])
            self.assertEqual(step(root, "gen", "same", cached_transport, 10), [1])
            self.assertEqual(step(root, "gen", "same", cached_transport, 10), [1])
            self.assertEqual(calls, ["gen"])
