"""A failed chapter gets a smaller, source-preserving attempt before fallback."""

import json
from types import SimpleNamespace

import pytest

from deai.client import ModelResponseError, STOP
from deai.flow import Result, Session, polish
from deai.io import process, read_json
from tools.runtime_support import ProjectError, QuotaExhausted


TEXT = (
    "她沉声道：“快走。”院里的雨水顺着台阶流进水沟，送信的人还在院墙外等她回话。"
    "她把信封收进衣袋，推开了门。\n\n"
    "他站在门边等着，手里抱着刚从河边拾回的木箱。雨打在箱盖上，门外的脚印已经被水冲淡了。"
)


class ScopedClient:
    def __init__(self, work, env=None, model=None, *, fail_stage="recall22-initial-character_label-0", always=False):
        self.calls = []
        self.work = work
        self.fail_stage = fail_stage
        self.always = always

    def estimate(self):
        return 0

    def __call__(self, messages, *, stage, **kwargs):
        data = json.loads(messages[-1]["content"].split("\n")[-1])
        self.calls.append(dict(stage=stage, data=data, usage=None))
        if stage.startswith(self.fail_stage) and (self.always or "-narrow" not in stage):
            raise ModelResponseError(stage, "JSON incomplete paragraph coverage")
        pids = [p["pid"] for p in data.get("paragraphs", [])]
        if "examples" in data:
            return dict(checked_pids=pids, issues=[])
        if "targets" in data:
            return dict(candidates=[dict(sid=t["sid"], choices=[]) for t in data["targets"]])
        if "items" in data:
            return dict(checks=[dict(id=t["id"], ok=True, reason="fake-only") for t in data["items"]])
        return dict(checked_pids=pids, facts=[], chapter={})


def run(root, client):
    source = root / "chapter-001.body.md"
    source.write_text(TEXT)
    status = process(root, "001", source, root / "absent.env", "local", polish, lambda *a, **k: client)
    return status, read_json(root / "final/chapter-001.deai.json")


@pytest.mark.parametrize("phase", ["initial", "recheck"])
def test_failed_detection_retries_smaller_scope_and_keeps_history(tmp_path, phase):
    client = ScopedClient(tmp_path, fail_stage=f"recall22-{phase}-character_label-0")
    status, report = run(tmp_path, client)
    assert status in ("polished", "unchanged")
    recovery = report["checks"]["recovery"]
    assert recovery["strategy"] == "single_item"
    assert recovery["attempts"][0]["failure"]["reason"] == "JSON incomplete paragraph coverage"
    assert recovery["attempts"][1]["status"] == status
    smaller = [c for c in client.calls if "-narrow" in c["stage"] and "examples" in c["data"]]
    assert smaller and all(len(c["data"]["paragraphs"]) == 1 for c in smaller)
    assert all(c["stage"].startswith(client.fail_stage) for c in smaller)
    assert (tmp_path / "chapter-001.body.md").read_text() == TEXT
    assert report["cumulative"]["requests"] == len(client.calls)
    assert report["cumulative"]["unknown_usage"] == len(client.calls)
    assert report["checks"]["unknown_usage"] == len(client.calls)
    assert report["calls"] == len(client.calls)


def test_failed_smaller_attempt_falls_back_with_both_reasons_and_is_resumable(tmp_path):
    client = ScopedClient(tmp_path, always=True)
    status, report = run(tmp_path, client)
    assert status == "retained" and report["text"] == TEXT
    attempts = report["checks"]["recovery"]["attempts"]
    assert len(attempts) == 2 and all(a["failure"]["reason"] for a in attempts)
    assert "-narrow" in attempts[1]["failure"]["stage"]
    before = (tmp_path / "final/chapter-001.md").read_bytes()
    assert before == TEXT.encode()

    def forbidden(*a, **k):
        raise AssertionError("final fallback is already processed")

    assert process(tmp_path, "001", tmp_path / "chapter-001.body.md", "", "local", forbidden, forbidden) == "cached"
    assert (tmp_path / "final/chapter-001.md").read_bytes() == before


def test_recovery_record_is_on_disk_before_retry_and_survives_terminal_error(tmp_path):
    calls = []

    def fail(text, ctx, llm):
        calls.append(ctx)
        if len(calls) == 1:
            return Result(text, status="retained", checks={"failure": {"stage": "fake", "reason": "bad coverage"}})
        saved = read_json(tmp_path / "final/.deai/001/recovery.json")
        assert saved["attempts"][0]["failure"]["reason"] == "bad coverage"
        raise QuotaExhausted("fake quota")

    source = tmp_path / "chapter-001.body.md"
    source.write_text(TEXT)
    with pytest.raises(QuotaExhausted):
        process(
            tmp_path, "001", source, "", "local", fail, lambda *a, **k: SimpleNamespace(calls=[], estimate=lambda: 0)
        )
    assert len(calls) == 2 and calls[1]["_narrow_retry"]
    report = read_json(tmp_path / "final/chapter-001.deai.json")
    assert report["checks"]["recovery"]["attempts"][0]["failure"]["reason"] == "bad coverage"
    assert source.read_text() == TEXT and not (tmp_path / "final/chapter-001.md").exists()


@pytest.mark.parametrize(
    "error", [ProjectError("fake project"), QuotaExhausted("fake quota"), InterruptedError("stop")]
)
def test_terminal_first_attempt_never_retries(tmp_path, error):
    seen = []

    def fail(*args):
        seen.append(True)
        raise error

    source = tmp_path / "chapter-001.body.md"
    source.write_text(TEXT)
    with pytest.raises(type(error)):
        process(tmp_path, "001", source, "", "local", fail, lambda *a, **k: SimpleNamespace())
    assert len(seen) == 1
    STOP.clear()


@pytest.mark.parametrize("kind", ["targets", "items", "paragraphs"])
def test_narrow_retry_still_covers_every_requested_identifier(tmp_path, kind):
    client = ScopedClient(tmp_path, fail_stage="never")
    session = Session(client, {"_narrow_retry": True})
    if kind == "targets":
        data = dict(targets=[dict(sid=f"S{i}") for i in range(3)])
        value = session.ask("candidates", "test", data)
        assert {r["sid"] for r in value["candidates"]} == {"S0", "S1", "S2"}
    elif kind == "items":
        data = dict(items=[dict(id=f"P{i}") for i in range(3)])
        value = session.ask("facts", "test", data)
        assert {r["id"] for r in value["checks"]} == {"P0", "P1", "P2"}
    else:
        data = dict(paragraphs=[dict(pid=f"P{i}", text="一段原文。") for i in range(3)])
        value = session.ask("diagnostics", "test", data)
        assert set(value["checked_pids"]) == {"P0", "P1", "P2"}
    assert len(client.calls) == 3
    assert all(len(c["data"][kind]) == 1 for c in client.calls)


def test_word_band_rejection_also_gets_a_smaller_attempt(tmp_path):
    source = tmp_path / "chapter-001.body.md"
    source.write_text(TEXT)
    seen = []

    def overshoot(text, ctx, llm):
        seen.append(ctx)
        return Result(text + text, status="polished")

    status = process(
        tmp_path, "001", source, "", "local", overshoot, lambda *a, **k: SimpleNamespace(calls=[], estimate=lambda: 0)
    )
    report = read_json(tmp_path / "final/chapter-001.deai.json")
    assert status == "retained" and len(seen) == 2
    assert report["checks"]["recovery"]["attempts"][0]["failure"]["reason"] == "word_band"
    assert report["text"] == TEXT


@pytest.mark.parametrize("status", ["retained", "partial", "unchanged"])
def test_retained_without_failure_is_not_a_retry_request(tmp_path, status):
    seen = []
    source = tmp_path / "chapter-001.body.md"
    source.write_text(TEXT)

    def keep(text, *args):
        seen.append(text)
        return Result(text, status=status, warnings=[dict(note="参考提示，不是失败")])

    assert (
        process(
            tmp_path, "001", source, "", "local", keep, lambda *a, **k: SimpleNamespace(calls=[], estimate=lambda: 0)
        )
        == status
    )
    assert seen == [TEXT]
    assert not (tmp_path / "final/.deai/001/recovery.json").exists()


def test_failed_retry_preserves_previously_accepted_final_and_report(tmp_path):
    from deai.io import save

    previous = dict(status="polished", complete=True, text=TEXT)
    save(tmp_path, "final/chapter-001.md", TEXT)
    save(tmp_path, "final/chapter-001.deai.json", previous)
    save(tmp_path, "final/.deai/001/input.md", TEXT)
    old_report = (tmp_path / "final/chapter-001.deai.json").read_bytes()
    status, report = run(tmp_path, ScopedClient(tmp_path, always=True))
    assert status == "retained" and report == previous
    assert (tmp_path / "final/chapter-001.deai.json").read_bytes() == old_report
    assert (tmp_path / "final/chapter-001.md").read_text() == TEXT
    attempt = read_json(tmp_path / "final/.deai/001/attempt-last.json")
    assert len(attempt["checks"]["recovery"]["attempts"]) == 2


def test_scene_retry_uses_single_slots_and_reassembles_complete_variants():
    requests = []

    def client(messages, **kwargs):
        data = json.loads(messages[-1]["content"].split("\n")[-1])
        requests.append(data)
        return dict(variants=[dict(lines=[dict(id=s["id"], text="留在这里。") for s in data["slots"]])])

    data = dict(slots=[dict(id="D1"), dict(id="D2")], characters=[dict(name="孟岚")], facts={"must_keep": "门已关"})
    result = Session(client, {"_narrow_retry": True}).ask("dialogue-scene", "test", data)
    assert len(requests) == 2 and all(len(r["slots"]) == 1 for r in requests)
    assert all(r["characters"] == data["characters"] and r["facts"] == data["facts"] for r in requests)
    assert {line["id"] for line in result["variants"][0]["lines"]} == {"D1", "D2"}
