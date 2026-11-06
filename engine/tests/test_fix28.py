"""INBOX 28: malformed model replies roll back one chapter; legacy jobs gain a watcher."""

import json
from pathlib import Path
from unittest import mock

import pytest

from deai.client import STOP
from deai.flow import Session, polish
from deai.io import read_json
from test_run_finish_deai import http, completion
from test_webui_contracts import ENGINE, WebFixture, jobs, library, put, snapshot


class ReplyClient:
    instances = []
    bad_stage = ""
    bad_inventory = None

    def __init__(self, out, *args, **kwargs):
        self.number = Path(out).name
        self.calls = []
        self.__class__.instances.append(self)

    def estimate(self):
        return 0

    def __call__(self, messages, *, stage, **kwargs):
        self.calls.append(dict(stage=stage, usage=None))
        data = json.loads(messages[-1]["content"].split("\n")[-1])
        if self.number == "001" and stage.startswith(self.bad_stage):
            return "{}"
        if "examples" in data:
            return dict(checked_pids=[p["pid"] for p in data["paragraphs"]], issues=[])
        if "targets" in data:
            return dict(candidates=[dict(sid=t["sid"], choices=[]) for t in data["targets"]])
        if "items" in data:
            return dict(checks=[dict(id=t["id"], ok=True) for t in data["items"]])
        if stage.startswith("diagnostics"):
            return dict(checked_pids=[p["pid"] for p in data["paragraphs"]], facts=[], chapter={})
        if stage.startswith("inventory"):
            if self.number == "001" and self.bad_inventory is not None:
                return dict(entities=[], paragraphs=[], scenes=[self.bad_inventory])
            return dict(entities=[], paragraphs=[], scenes=[dict(id="scene", pids=["P1"])])
        if stage.startswith("dialogue-cast"):
            return dict(slots=[dict(id=s["id"], speaker="她") for s in data["slots"]])
        if stage.startswith("dialogue-scene"):
            return dict(variants=[])
        raise AssertionError(stage)


@pytest.mark.parametrize(
    "bad_stage",
    [
        "recall22-recheck-verb_mismatch-0",
        "recall22-candidates-0",
        "diagnostics",
        "dialogue-cast",
        "inventory",
    ],
)
def test_bad_substep_rolls_back_whole_chapter_and_watcher_finishes(tmp_path, monkeypatch, bad_stage):
    from deai.__main__ import main

    ReplyClient.instances = []
    monkeypatch.setattr(ReplyClient, "bad_stage", bad_stage)
    monkeypatch.setattr("deai.__main__.MiniMax", ReplyClient)
    monkeypatch.setattr("urllib.request.urlopen", mock.Mock(side_effect=AssertionError("no network")))
    put(tmp_path / "deai-context.json", dict(force_dialogue=True))
    put(tmp_path / "run.json", dict(status="complete", book=dict(chapters=2)))
    source = "她沉声道：“快走。”“好。”她站在门边，等着院里的脚步声远去，又把没收完的衣服抱回屋里。"
    for n in (1, 2):
        put(tmp_path / f"chapter-{n:03d}.body.md", source + "\r\n\r\n灯还亮着，她走过去关上了窗。")
        put(tmp_path / f"chapters/{n:03d}.json", dict(status="complete"))
    before = snapshot(tmp_path)
    try:
        assert main(["run", str(tmp_path), "--watch", "--pipeline", "--dialogue-mode", "scene"]) == 0
        assert not STOP.is_set()
        report = read_json(tmp_path / "final/chapter-001.deai.json")
        assert report["status"] == "retained"
        assert report["processing_complete"] is True and report["complete"] is False
        assert report["edits"] == []
        assert report["checks"]["failure"]["stage"] == bad_stage
        assert report["checks"]["failure"]["reason"]
        assert (tmp_path / "final/chapter-001.md").read_bytes() == (tmp_path / "chapter-001.body.md").read_bytes()
        assert (tmp_path / "final/chapter-002.md").exists()
        failed_calls = [c for c in ReplyClient.instances[0].calls if c["stage"].startswith(bad_stage)]
        assert len([c for c in failed_calls if "-narrow" not in c["stage"]]) == 6
        assert len([c for c in failed_calls if "-narrow" in c["stage"]]) == 6
        recovery = report["checks"]["recovery"]
        assert recovery["strategy"] == "single_item" and recovery["status"] == "retained"
        assert [attempt["status"] for attempt in recovery["attempts"]] == ["retained", "retained"]
        for attempt in recovery["attempts"]:
            assert attempt["failure"]["stage"] == bad_stage
            assert attempt["failure"]["reason"]
        work = tmp_path / "final/.deai/001"
        assert read_json(work / "attempt-initial.json")["checks"]["failure"] == recovery["attempts"][0]["failure"]
        assert report["checks"]["failure"] == recovery["attempts"][1]["failure"]
        assert read_json(work / "recovery.json") == recovery
        for name, content in before.items():
            assert (tmp_path / name).read_bytes() == content
        finals = snapshot(tmp_path / "final")
        count = len(ReplyClient.instances)
        assert main(["run", str(tmp_path), "--watch", "--pipeline"]) == 0
        assert len(ReplyClient.instances) == count
        assert snapshot(tmp_path / "final") == finals
    finally:
        STOP.clear()


class LegacyWatcherTests(WebFixture):
    def legacy(self, **fields):
        folder = self.book(status="running", final=False)
        put(folder / "web.json", dict(library.read(folder / "web.json"), polish=True, pid=880001, **fields))
        return folder

    def restart(self):
        self.queue.exclusive.close()
        self.queue = jobs.Queue(self.lib, self.env, ENGINE)
        self.addCleanup(self.queue.exclusive.close)

    def test_startup_adds_watcher_to_surviving_legacy_writer_without_restarting_writer(self):
        folder = self.legacy()
        put(folder / "runner.lock", "active writer lock")
        before = snapshot(folder)
        self.restart()
        self.queue.tick()
        self.spawn.assert_called_once()
        self.assertEqual(self.spawn.call_args.args[0][2], "deai")
        self.assertIn("--watch", self.spawn.call_args.args[0])
        data = library.read(folder / "web.json")
        self.assertEqual(data["pipeline_version"], 1)
        self.assertEqual(data["pipeline"]["book"]["pid"], 880001)
        self.assertEqual(data["pipeline"]["polish"]["status"], "running")
        self.assertEqual(data["status"], "running")
        self.queue.tick()
        self.assertEqual(self.spawn.call_count, 1)
        for name, content in before.items():
            if name != "web.json":
                self.assertEqual((folder / name).read_bytes(), content)

    def test_tick_migrates_owned_live_writer_and_resume_does_not_duplicate_it(self):
        folder = self.legacy()
        self.queue.processes["webui/story"] = dict(p=self.child, kind="book", folder=folder, paused=False)
        self.queue.action("webui/story", "resume")
        self.queue.tick()
        self.spawn.assert_called_once()
        self.assertEqual(self.spawn.call_args.args[0][2], "deai")
        self.assertIs(self.queue.processes["webui/story"]["p"], self.child)
        self.assertTrue(self.queue.processes["webui/story"]["pipeline"])

    def test_live_legacy_writer_is_repaired_by_tick_without_clicking_continue(self):
        folder = self.legacy()
        self.queue.processes["webui/story"] = dict(p=self.child, kind="book", folder=folder, paused=False)
        self.queue.tick()
        self.spawn.assert_called_once()
        self.assertEqual(self.spawn.call_args.args[0][2], "deai")

    def test_dead_legacy_writer_resumes_both_stages(self):
        folder = self.legacy()
        self.kill.side_effect = ProcessLookupError
        self.restart()
        self.queue.tick()
        self.assertEqual([c.args[0][2] for c in self.spawn.call_args_list], ["bookengine", "deai"])

    def test_rolled_back_chapter_has_plain_status_in_http_detail(self):
        folder = self.legacy()
        put(folder / "final/chapter-001.md", "原稿。")
        put(folder / "final/chapter-001.deai.json", dict(status="retained", complete=False, processing_complete=True))
        status, _, body = self.request("/api/book?id=webui/story")
        self.assertEqual(status, 200)
        row = json.loads(body)["chapters"][0]
        self.assertEqual(row["deai_notice"], "去 AI 未完成，使用原稿")

    def test_completed_legacy_book_only_starts_missing_final_watcher(self):
        folder = self.legacy()
        put(folder / "run.json", dict(status="complete", book=dict(chapters=2)))
        put(folder / "web.json", dict(library.read(folder / "web.json"), status="complete", pid=None))
        self.kill.side_effect = ProcessLookupError
        self.restart()
        self.queue.tick()
        self.spawn.assert_called_once()
        self.assertEqual(self.spawn.call_args.args[0][2], "deai")

    def test_all_existing_finals_are_preserved_without_spawning_any_process(self):
        folder = self.book(status="complete")
        put(folder / "run.json", dict(status="complete", book=dict(chapters=2)))
        put(folder / "web.json", dict(library.read(folder / "web.json"), polish=True))
        before = snapshot(folder / "final")
        self.queue.action("webui/story", "resume")
        self.queue.tick()
        self.spawn.assert_not_called()
        self.assertEqual(snapshot(folder / "final"), before)
        self.assertEqual(library.read(folder / "web.json")["status"], "complete")

    def test_migration_retains_legacy_polisher_terminal_state_until_explicit_retry(self):
        for kind in ("quota_exhausted", "error"):
            with self.subTest(kind=kind):
                folder = self.book("webui/" + kind, status=kind, final=False)
                put(folder / "web.json", dict(library.read(folder / "web.json"), polish=True))
                put(folder / "run.json", dict(status="complete", book=dict(chapters=2)))
                put(folder / "runtime.json", dict(kind=kind, stage="recall22-facts", error="fixture failure"))
        self.kill.side_effect = ProcessLookupError
        self.restart()
        self.queue.tick()
        self.spawn.assert_not_called()
        for kind in ("quota_exhausted", "error"):
            data = library.read(self.lib.own / kind / "web.json")
            self.assertEqual(data["pipeline"]["polish"]["status"], kind)
            self.assertIn("fixture failure", data["reason"])
        self.queue.action("webui/error", "retry")
        self.queue.tick()
        self.spawn.assert_called_once()
        self.assertEqual(self.spawn.call_args.args[0][2], "deai")

    def test_surviving_concurrency_paused_writer_continues_original_pid_after_restart(self):
        import signal

        folder = self.legacy()
        put(folder / "web.json", dict(library.read(folder / "web.json"), status="concurrency_paused"))
        self.restart()
        self.queue.tick()
        self.killpg.assert_called_once_with(880001, signal.SIGCONT)
        self.spawn.assert_called_once()
        self.assertEqual(self.spawn.call_args.args[0][2], "deai")
        self.assertEqual(self.queue.processes["webui/story"]["p"].pid, 880001)

    def test_version_marker_without_pipeline_uses_surviving_run_pid(self):
        folder = self.legacy(pipeline_version=1)
        data = library.read(folder / "web.json")
        data.pop("pid")
        put(folder / "web.json", data)
        put(folder / "run.json", dict(status="running", pid=880001, book=dict(chapters=2)))
        self.restart()
        self.queue.tick()
        self.spawn.assert_called_once()
        self.assertEqual(self.spawn.call_args.args[0][2], "deai")
        self.assertEqual(self.queue.processes["webui/story"]["p"].pid, 880001)

    def test_legacy_budget_paused_writer_waits_for_continue_then_resumes_with_watcher(self):
        import signal

        folder = self.legacy()
        put(folder / "web.json", dict(library.read(folder / "web.json"), status="budget_paused"))
        self.restart()
        self.queue.tick()
        self.spawn.assert_not_called()
        self.killpg.assert_not_called()
        self.queue.action("webui/story", "resume")
        self.queue.tick()
        self.killpg.assert_called_once_with(880001, signal.SIGCONT)
        self.spawn.assert_called_once()
        self.assertEqual(self.spawn.call_args.args[0][2], "deai")
        self.assertFalse(self.queue.processes["webui/story"]["paused"])


@pytest.mark.parametrize("scene", [dict(pids=3), dict(pids=[{}]), "invalid"])
def test_malformed_inventory_is_retried_then_retains_chapter(tmp_path, monkeypatch, scene):
    monkeypatch.setattr(ReplyClient, "bad_stage", "never")
    monkeypatch.setattr(ReplyClient, "bad_inventory", scene)
    client = ReplyClient(tmp_path / "001")
    result = polish(
        "她沉声道：“快走。”“好。”她还在门边等着他，屋里的人一直没出来。",
        dict(force_dialogue=True, dialogue_mode="scene"),
        client,
    )
    assert result.status == "retained"
    assert result.checks["failure"]["stage"] == "inventory"
    assert len([c for c in client.calls if c["stage"].startswith("inventory")]) == 6
    assert not STOP.is_set()


@pytest.mark.parametrize("bug_index", [0, 1])
def test_parallel_candidate_bug_is_never_hidden_by_format_error(tmp_path, monkeypatch, bug_index):
    from tools.runtime_support import ProjectError
    from deai.client import ModelResponseError
    from deai.rules import RECALL

    size = RECALL["repair"]["packet_size"]
    original_ask = Session.ask
    text = "\n\n".join(["她沉声道：“快走。”她还在门边等着他，屋里的人一直没出来。"] * (size + 1))

    def ask(self, stage, *args, **kwargs):
        if stage == f"recall22-candidates-{bug_index}":
            raise RuntimeError("fixture candidate programming bug")
        if stage.startswith("recall22-candidates-"):
            raise ModelResponseError(stage, "fixture format failure")
        return original_ask(self, stage, *args, **kwargs)

    monkeypatch.setattr(Session, "ask", ask)
    monkeypatch.setattr(ReplyClient, "bad_stage", "never")
    try:
        with pytest.raises(ProjectError, match="fixture candidate programming bug"):
            polish(text, {}, ReplyClient(tmp_path / "001"))
    finally:
        STOP.clear()


@pytest.mark.parametrize("bad", [None, "", "{invalid"])
def test_transport_format_exhaustion_rolls_back_in_real_flow(http, bad):
    from deai.client import MiniMax

    http.replies.extend(completion(bad) for _ in range(108))
    result = polish("她推开门。", {}, MiniMax(http.work, http.env))
    assert result.status == "retained" and result.text == "她推开门。"
    assert result.checks["failure"]["stage"].startswith("recall22-initial-")
    assert result.checks["failure"]["reason"]
    assert len(http.requests) >= 6
    assert not STOP.is_set()


def test_parallel_programming_bug_wins_over_bad_model_response(tmp_path, monkeypatch):
    from tools.runtime_support import ProjectError

    original_call = ReplyClient.__call__
    monkeypatch.setattr(ReplyClient, "bad_stage", "recall22-recheck-verb_mismatch-0")

    def reply(self, messages, *, stage, **kwargs):
        if stage == "diagnostics":
            raise TypeError("fixture programming bug")
        return original_call(self, messages, stage=stage, **kwargs)

    monkeypatch.setattr(ReplyClient, "__call__", reply)
    try:
        with pytest.raises(ProjectError, match="fixture programming bug"):
            polish("她沉声道：“快走。”她还在门边等着他，屋里的人一直没出来。", {}, ReplyClient(tmp_path / "001"))
    finally:
        STOP.clear()
