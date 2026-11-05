"""Offline ideation UI integration; never launch a paid worker."""

import copy
import json
import sys
from types import ModuleType
from unittest import mock

from test_webui_contracts import WebFixture, jobs, library, server, put, snapshot


def card():
    return dict(
        title="渡口借命，归来的人少了一岁",
        hook="摆渡人用余生换回失踪乘客",
        tags=["民俗", "解谜", "逆袭"],
        premise="摆渡人要在涨潮前找回失踪乘客。",
        golden_finger_rule="每渡一人可以借回一天寿命",
        opening="退潮后船底传来敲门声。",
        engine="找回乘客、寻找失船、揭开渡口身世",
        axes=dict(shell="民俗悬疑", variable="渡船", desire="守护", emotion="解谜"),
    )


class IdeationWebTests(WebFixture):
    def rich_topic(self):
        data = self.topic()
        folder = self.lib.own / ".topics" / data["topic_id"]
        topic = library.read(folder / "topic.json")
        topic.update(chapters=50, channel="男频", candidates=[card()])
        put(folder / "topic.json", topic)
        return dict(data, chapters=50), folder

    def test_same_input_always_creates_new_batch_retaining_previous_folder(self):
        first = self.queue.topic(dict(genre="cozy-mystery", chapters=5, idea="渡口", channel="男频"))
        self.child.poll.return_value = 0
        second = self.queue.topic(dict(genre="cozy-mystery", chapters=5, idea="渡口", channel="男频"))
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(second["channel"], "男频")
        self.assertEqual(self.queue.get_topic(first["id"]), first)

    def test_topic_has_no_money_cap_and_preserves_old_cost_ledger(self):
        ledger = self.lib.own / ".topics/old/http/ledger.json"
        put(
            ledger,
            [dict(model="MiniMax-M3.1-Flash", status="inflight", usage=None, input_bytes=10**9, max_tokens=10**9)],
        )
        before = ledger.read_bytes()
        result = self.queue.topic(dict(genre="cozy-mystery", chapters=5))
        self.assertNotIn("budget", result)
        self.assertFalse(hasattr(self.queue, "limit"))
        self.assertEqual(ledger.read_bytes(), before)
        self.spawn.assert_called_once()

    def test_invalid_channel_cannot_launch_or_write_batch(self):
        for channel in (None, [], {}, "其他"):
            before = snapshot(self.lib.own)
            status, _, _ = self.request(
                "/api/topics", method="POST", data=dict(genre="cozy-mystery", chapters=5, channel=channel)
            )
            self.assertEqual(status, 400)
            self.assertEqual(snapshot(self.lib.own), before)
        self.spawn.assert_not_called()

    def test_direction_modes_preserve_full_candidate_and_validate_before_writes(self):
        _, folder = self.rich_topic()
        chosen = library.read(folder / "topic.json")["candidates"][0]
        for mode in ("both", "shell", "variable"):
            self.child.poll.return_value = 0
            result = self.queue.topic(
                dict(genre="cozy-mystery", chapters=50, direction=dict(mode=mode, candidate=chosen))
            )
            self.assertEqual(result["direction"], dict(mode=mode, candidate=chosen))
        for direction in (
            {"mode": "arbitrary", "candidate": chosen},
            {"mode": "both"},
            [],
            {"mode": "shell", "candidate": {"title": "缺字段"}},
        ):
            before = snapshot(self.lib.own)
            with self.assertRaises(ValueError):
                self.queue.topic(dict(genre="cozy-mystery", chapters=50, direction=direction))
            self.assertEqual(snapshot(self.lib.own), before)

    def test_complete_card_and_edits_reach_book_without_changing_original_candidates(self):
        data, topic_folder = self.rich_topic()
        original = (topic_folder / "topic.json").read_bytes()
        internal = json.loads(original)
        internal.update(history=[card()], market={"books": [{"title": "内部书目"}]}, axes=[card()["axes"]])
        put(topic_folder / "topic.json", internal)
        edited = card()
        edited["title"] = "退潮时，船底传来敲门声"
        data["candidate_edit"] = edited
        result = self.queue.create(data)
        settings = library.read(self.lib.folder(result["id"]) / "book.yaml")
        self.assertEqual(json.loads(settings["user_input"]), edited)
        topic = library.read(topic_folder / "topic.json")
        self.assertEqual(topic["candidates"], json.loads(original)["candidates"])
        self.assertEqual(topic["history"], internal["history"])
        self.assertEqual(topic["market"], internal["market"])
        self.assertEqual(topic["axes"], internal["axes"])

    def test_unedited_card_keeps_all_fields_including_reference_metadata(self):
        data, folder = self.rich_topic()
        original = library.read(folder / "topic.json")
        original["candidates"][0].update(scores={"点击欲": 8}, market_note="同壳新书：未知")
        put(folder / "topic.json", original)
        result = self.queue.create(data)
        settings = library.read(self.lib.folder(result["id"]) / "book.yaml")
        self.assertEqual(json.loads(settings["user_input"]), original["candidates"][0])

    def test_invalid_edits_rejected_over_http_without_side_effects(self):
        data, _ = self.rich_topic()
        for edit in (
            None,
            [],
            {"title": "缺字段"},
            dict(card(), hook="太长" * 31),
            dict(card(), tags="不是数组"),
            dict(card(), engine=None),
        ):
            before = snapshot(self.lib.own)
            status, _, _ = self.request("/api/books", method="POST", data=dict(data, candidate_edit=edit))
            self.assertEqual(status, 400, edit)
            self.assertEqual(snapshot(self.lib.own), before)

    def test_legacy_edits_remain_available_without_rich_fields(self):
        data = self.topic()
        data["candidate_edit"] = dict(title="渡口", premise="她赶在涨潮前找回失踪者。")
        result = self.queue.create(data)
        settings = library.read(self.lib.folder(result["id"]) / "book.yaml")
        self.assertEqual(json.loads(settings["user_input"]), data["candidate_edit"])

    def test_cannot_strip_rich_fields_or_forge_scoring_metadata(self):
        data, _ = self.rich_topic()
        for edit in ({"title": "渡口", "premise": "只剩简介"}, dict(card(), scores={"点击欲": 10})):
            before = snapshot(self.lib.own)
            with self.assertRaises(ValueError):
                self.queue.create(dict(data, candidate_edit=edit))
            self.assertEqual(snapshot(self.lib.own), before)

    def test_worker_calls_pipeline_and_preserves_partial_checkpoints_on_failure(self):
        _, folder = self.rich_topic()
        package = ModuleType("ideation")
        expected = library.read(folder / "topic.json")
        expected.update(seed=17, status="complete", cost=0.012)
        package.run = mock.Mock(return_value=expected)
        with mock.patch.dict(sys.modules, ideation=package), mock.patch("deai.client.MiniMax") as factory:
            jobs.topic_worker(folder, self.env)
            factory.assert_called_once_with(folder, self.env)
            package.run.assert_called_once_with(folder, factory.return_value, self.lib.root)
            self.assertEqual(library.read(folder / "topic.json"), expected)

            def fail(*_):
                partial = copy.deepcopy(expected)
                partial.update(seed=18, status="running", steps={"axes": "done"})
                put(folder / "topic.json", partial)
                raise RuntimeError("sensitive transport details")

            package.run.side_effect = fail
            jobs.topic_worker(folder, self.env)
        failed = library.read(folder / "topic.json")
        self.assertEqual(failed["status"], "error")
        self.assertEqual(failed["seed"], 18)
        self.assertEqual(failed["steps"], {"axes": "done"})
        self.assertNotIn("sensitive", failed["error"])

    def test_topic_readback_has_cost_and_no_cache(self):
        data, folder = self.rich_topic()
        put(
            folder / "http/ledger.json",
            [dict(model="MiniMax-M3.1-Flash", usage={"prompt_tokens": 1000, "completion_tokens": 1000})],
        )
        status, headers, payload = self.request("/api/topics?id=" + data["topic_id"])
        self.assertEqual(status, 200)
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertAlmostEqual(json.loads(payload)["usage"]["cost"], 0.00075)

    def test_readback_excludes_large_private_planning_fields_but_keeps_card_axes(self):
        data, folder = self.rich_topic()
        internal = library.read(folder / "topic.json")
        internal.update(
            history=[card()] * 60,
            axes=[card()["axes"]] * 24,
            features={"anchors": [{"title": "内部锚点"}]},
            market={"date": "2026-11-04", "status": "缓存", "books": [{"title": "内部书目"}]},
        )
        put(folder / "topic.json", internal)
        public = self.queue.get_topic(data["topic_id"])
        self.assertEqual(public["market"], {"date": "2026-11-04", "status": "缓存"})
        for key in ("history", "axes", "features"):
            self.assertNotIn(key, public)
        self.assertEqual(public["candidates"][0]["axes"], card()["axes"])
        self.assertEqual(library.read(folder / "topic.json"), internal)

    def test_server_configuration_exposes_channels_without_money_limits(self):
        config = server.config(self.env)
        self.assertEqual(config["channels"], ["男频", "女频"])
        self.assertNotIn("budgets", config)


class CollisionReadbackTests(WebFixture):
    def test_only_collision_rejections_are_exposed_without_raw_market_text(self):
        data = self.topic()
        folder = self.lib.own / ".topics" / data["topic_id"]
        topic = library.read(folder / "topic.json")
        topic["rejected"] = [
            dict(title="待筛标题", reasons=["撞车:榜单已出版标题"]),
            dict(title="坏字段", reasons=["字段"]),
        ]
        topic["market"] = dict(date="2026-11-04", status="fresh", books=[dict(hook="完整简介不得出现在浏览器")])
        put(folder / "topic.json", topic)
        result = self.queue.get_topic(data["topic_id"])
        self.assertEqual(result["collisions"], [topic["rejected"][0]])
        self.assertNotIn("rejected", result)
        self.assertNotIn("books", result["market"])
        self.assertNotIn("完整简介", json.dumps(result, ensure_ascii=False))
