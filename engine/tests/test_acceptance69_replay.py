"""Offline acceptance of saved bad candidates, not a live-model quality test.

Run with the real library available; missing historical evidence is an explicit skip.
Only temporary copies are writable. Downstream model verdicts deliberately approve
edits so they cannot conceal a broken spill guard; classification uses saved hits.
"""

import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time

import pytest

from deai import flow
from deai.adoption import spill_reasons
from deai.text import replace, sentences


ENGINE = Path(__file__).resolve().parents[1]
SOURCE = ENGINE.parent / "books/webui/6c18dfd5-ad35-4347-8271-f1d4ac48238b"
BAD_CHAPTERS = [14, 20, 22, 33, 45, 49]


def load(path):
    return json.loads(path.read_text("utf-8"))


def put(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def inventory(folder):
    return {
        str(p.relative_to(folder)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(folder.rglob("*"))
        if p.is_file()
    }


@pytest.fixture(scope="module")
def saved_book(tmp_path_factory):
    if not SOURCE.is_dir():
        pytest.skip("acceptance69 requires the original saved book (no fabricated fallback)")
    root = tmp_path_factory.mktemp("acceptance69-source")
    before = inventory(SOURCE)
    copy = root / "saved-book"
    shutil.copytree(SOURCE, copy)
    assert inventory(copy) == before
    put(root / "source-before.json", before)
    yield copy
    after = inventory(SOURCE)
    put(root / "source-after.json", after)
    assert after == before, "real historical source changed during acceptance"
    assert inventory(copy) == before, "read-only acceptance source copy changed"


@pytest.fixture(autouse=True)
def no_model_network(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("acceptance69 must never call a model/network")

    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(flow, "STOP", threading.Event())


class SavedCandidates:
    """Replay saved candidate bytes; permissive offline verdicts isolate adoption.

    Initial semantic hits come from the historical report, deterministic detection,
    validation, ranking, preservation checks and final adoption remain production code.
    Other targets get no candidate. No API client is instantiated.
    """

    def __init__(self, hits, selected, choices):
        self.hits, self.selected, self.choices = hits, selected, choices
        self.stages = []
        self.offered = []

    def __call__(self, messages, *, stage, **kwargs):
        self.stages.append(stage)
        data = json.loads(messages[-1]["content"].rsplit("\n", 1)[-1])
        if "targets" in data:
            targets = data["targets"]
            self.offered.extend(t["sid"] for t in targets)
            return dict(
                candidates=[
                    dict(sid=t["sid"], choices=self.choices if t["sid"] == self.selected else []) for t in targets
                ]
            )
        if "requested_type" in data:
            allowed = {s["sid"] for p in data["paragraphs"] for s in p["sentences"]}
            return dict(
                checked_pids=[p["pid"] for p in data["paragraphs"]],
                issues=[
                    dict(sid=h["sid"], type=h["type"], severity=h["severity"])
                    for h in self.hits
                    if stage.startswith("recall22-initial")
                    and h.get("source") == "model"
                    and h["sid"] in allowed
                    and h["type"] == data["requested_type"]
                ],
            )
        if "items" in data:
            return dict(checks=[dict(id=item["id"], ok=True) for item in data["items"]])
        assert stage == "diagnostics", stage
        return dict(checked_pids=[p["pid"] for p in data["paragraphs"]], facts=[], chapter={})


def historical_case(book, number):
    work = book / f"final/.deai/{number:03d}"
    original = (work / "input.md").read_text("utf-8")
    assert original == (book / f"chapter-{number:03d}.body.md").read_text("utf-8")
    assert (book / f"steps/r2-ch{number}-finish.json").is_file()
    report = load(book / f"final/chapter-{number:03d}.deai.json")
    bad = [
        edit
        for edit in report["edits"]
        if spill_reasons(original, dict(edit, text=edit["before"]), edit["replacement"])
    ]
    assert len(bad) == 1
    edit = bad[0]
    hit = next(row for row in sentences(original) if row["sid"] == edit["sid"])
    assert hit["text"] == edit["before"]
    assert hit["start"] == edit["start"] and hit["end"] == edit["end"]
    records = [r for r in load(work / "http/ledger.json") if r["stage"].startswith("recall22-candidates-")]
    candidates = [
        dict(row, path=str((work / "http" / record["output"]).relative_to(book)))
        for record in records
        for row in load(work / "http" / record["output"])["candidates"]
        if row["sid"] == edit["sid"]
    ]
    assert len(candidates) == 1
    assert edit["replacement"] in candidates[0]["choices"]
    # Root responses/steps are the writing chain; de-AI responses live under http/.
    calls = [r for r in load(book / "calls.json") if r["stage"] == f"r2-ch{number}-finish" and r["status"] == "ok"]
    assert calls
    response = book / f"responses/{calls[-1]['seq']:03d}.json"
    assert response.is_file()
    return original, report, hit, edit, candidates[0], str(response.relative_to(book))


@pytest.mark.parametrize("number", BAD_CHAPTERS)
def test_saved_bad_candidate_is_blocked_by_real_adoption(saved_book, tmp_path, monkeypatch, number):
    original, report, hit, bad, candidate, response = historical_case(saved_book, number)
    replay = SavedCandidates(report["hits"], hit["sid"], [bad["replacement"]])
    guarded = flow.polish(original, {}, replay)
    assert hit["sid"] in replay.offered
    assert guarded.status in ("unchanged", "polished")
    assert not any(edit["sid"] == hit["sid"] for edit in guarded.edits)
    assert hit["text"] in guarded.text
    assert all(not spill_reasons(original, dict(e, text=e["before"]), e["replacement"]) for e in guarded.edits)
    assert guarded.text == replace(original, guarded.edits)
    assert guarded.text.count("“") == guarded.text.count("”")
    reasons = spill_reasons(original, hit, bad["replacement"])
    assert "quote_balance" in reasons
    if number in (33, 45):
        assert "repeats_following_text" in reasons
        # Quote-neutralized guard still rejects the authentic spill via neighbour protection.
        with monkeypatch.context() as patch:
            patch.setattr(flow, "spill_reasons", lambda *a: [r for r in spill_reasons(*a) if r != "quote_balance"])
            neighbour_only = flow.polish(
                original, {}, SavedCandidates(report["hits"], hit["sid"], [bad["replacement"]])
            )
        assert not any(edit["sid"] == hit["sid"] for edit in neighbour_only.edits)
        assert all(
            not spill_reasons(original, dict(e, text=e["before"]), e["replacement"]) for e in neighbour_only.edits
        )
    with monkeypatch.context() as patch:
        patch.setattr(flow, "spill_reasons", lambda *a: [])
        unsafe = flow.polish(original, {}, SavedCandidates(report["hits"], hit["sid"], [bad["replacement"]]))
    assert unsafe.status == "polished"
    assert any(e["replacement"] == bad["replacement"] for e in unsafe.edits)
    assert unsafe.text == replace(original, unsafe.edits)
    assert unsafe.text.count("“") != unsafe.text.count("”")
    put(
        tmp_path / "replay-evidence.json",
        dict(
            chapter=number,
            sid=hit["sid"],
            candidate_response=candidate["path"],
            writer_response=response,
            writer_step=f"steps/r2-ch{number}-finish.json",
            reasons=reasons,
            stages=replay.stages,
            guarded=guarded.status,
            guard_disabled=unsafe.status,
        ),
    )


@pytest.mark.parametrize("number", [14, 33])
def test_safe_scoped_replacement_is_still_adopted(saved_book, number):
    original, report, hit, bad, _, _ = historical_case(saved_book, number)
    choice = bad["replacement"] + "”" if number == 14 else hit["text"].replace("故留了", "才留了")
    assert spill_reasons(original, hit, choice) == []
    result = flow.polish(original, {}, SavedCandidates(report["hits"], hit["sid"], [choice]))
    assert result.status == "polished"
    assert any(edit["sid"] == hit["sid"] and edit["replacement"] == choice for edit in result.edits)
    assert all(not spill_reasons(original, dict(e, text=e["before"]), e["replacement"]) for e in result.edits)
    assert result.text == replace(original, result.edits)
    assert result.text.count("“") == result.text.count("”")


def test_preceding_neighbor_guard_in_real_adoption(saved_book, monkeypatch):
    original, report, hit, bad, _, _ = historical_case(saved_book, 14)
    # Derived boundary counterexample: repeat exactly four preceding source chars,
    # while preserving quote balance so only the preceding-text guard can reject it.
    safe = bad["replacement"] + "”"
    choice = original[: hit["start"]][-4:] + safe
    assert spill_reasons(original, hit, choice) == ["repeats_preceding_text"]
    guarded = flow.polish(original, {}, SavedCandidates(report["hits"], hit["sid"], [choice]))
    assert not any(edit["sid"] == hit["sid"] for edit in guarded.edits)
    with monkeypatch.context() as patch:
        patch.setattr(flow, "spill_reasons", lambda *a: [r for r in spill_reasons(*a) if r != "repeats_preceding_text"])
        unsafe = flow.polish(original, {}, SavedCandidates(report["hits"], hit["sid"], [choice]))
    assert any(edit["sid"] == hit["sid"] and edit["replacement"] == choice for edit in unsafe.edits)


def cli_environment(root):
    """Subprocesses have no inherited credentials and cannot open network sockets."""
    site = root / "offline-python"
    site.mkdir(exist_ok=True)
    (site / "sitecustomize.py").write_text(
        "import socket\n"
        "def denied(*a, **k): raise AssertionError('acceptance69 child network forbidden')\n"
        "socket.socket.connect = denied\n"
        "socket.create_connection = denied\n",
        encoding="utf-8",
    )
    return dict(
        PATH=os.defpath,
        HOME=str(root),
        PYTHONPATH=os.pathsep.join([str(site), str(ENGINE)]),
        PYTHONDONTWRITEBYTECODE="1",
        PYTHONUNBUFFERED="1",
        BOOKWEB_TEST="1",
    )


def assert_final_report(book):
    report = load(book / "book_checkup.json")
    assert report["source_preference"] == "final"
    assert report["chapter_count"] == 50 and report["reference_only"] is True
    assert set(report["sources"]) == {str(n) for n in range(1, 51)}
    assert all(report["sources"][str(n)] == f"final/chapter-{n:03d}.md" for n in range(1, 51))
    assert report["unclosed_quotes"]["chapters"] == BAD_CHAPTERS
    assert report["unclosed_quotes"]["count"] == 6
    return report


def changed(before, after):
    return {key for key in before.keys() | after.keys() if before.get(key) != after.get(key)}


def test_public_cli_replaces_stale_draft_report_without_touching_book(saved_book, tmp_path):
    book = tmp_path / "book"
    shutil.copytree(saved_book, book)
    stale = load(book / "book_checkup.json")
    assert stale["source_preference"] == "draft" and stale["unclosed_quotes"]["count"] == 0
    before = inventory(book)
    command = [sys.executable, "-m", "tools.book_checkup", str(book), "--source", "final"]
    started = time.monotonic()
    result = subprocess.run(
        command, cwd=ENGINE.parent, env=cli_environment(tmp_path), capture_output=True, text=True, timeout=120
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["chapters"] == 50
    assert_final_report(book)
    assert changed(before, inventory(book)) == {"book_checkup.json"}
    put(
        tmp_path / "cli-evidence.json",
        dict(command=command, exit=result.returncode, seconds=time.monotonic() - started, stdout=result.stdout),
    )


@pytest.mark.parametrize(
    "route",
    [
        "pipeline",
        "legacy",
        "queued",
        "queued_idle",
        "schedule_resume",
    ],
)
def test_queue_completion_really_spawns_final_cli(saved_book, tmp_path, monkeypatch, request, route):
    sys.path.insert(0, str(ENGINE / "tools/webui"))
    import final_checkup
    import jobs
    import library
    import pipeline

    lib = library.Library(tmp_path / "books")
    book = lib.own / "saved"
    shutil.copytree(saved_book, book)
    data = load(book / "web.json")
    data.update(status="polishing", auto_deai=False, polish=False)
    if route != "legacy":
        data.update(pipeline_version=1, pipeline=dict(book=dict(status="complete"), polish=dict(status="running")))
        if route in ("queued", "queued_idle"):
            data["status"] = "queued"
        elif route == "schedule_resume":
            data["status"] = "concurrency_paused"
    else:
        data.pop("pipeline_version", None)
        data.pop("pipeline", None)
    data.pop("pid", None)
    put(book / "web.json", data)
    env_file = tmp_path / "empty.env"
    env_file.write_text("# no credentials\n", encoding="utf-8")
    child_env = cli_environment(tmp_path)
    queue = jobs.Queue.__new__(jobs.Queue)
    queue.library, queue.env, queue.engine, queue.jobs = lib, env_file, ENGINE, 1
    # Public deai CLI exits from its finished-book fast path without a model.
    # The checkup below is also a real public CLI with the production log drainer.
    worker_command = [
        sys.executable,
        "-m",
        "deai",
        "run",
        str(book),
        "--watch",
        "--pipeline",
        "--original",
        "--env",
        str(env_file),
    ]
    worker_before = inventory(book)
    actual_worker_command = worker_command
    if route == "schedule_resume":
        ready, release = tmp_path / "worker-ready", tmp_path / "worker-release"
        wrapper = (
            "import pathlib,runpy,sys,time\n"
            "ready,release=map(pathlib.Path,sys.argv[1:3])\n"
            "args=sys.argv[3:]\nready.write_text('ready')\n"
            "while not release.exists(): time.sleep(.01)\n"
            "sys.argv=['deai',*args]\nrunpy.run_module('deai',run_name='__main__')\n"
        )
        actual_worker_command = [sys.executable, "-c", wrapper, str(ready), str(release), *worker_command[3:]]
    worker = subprocess.Popen(actual_worker_command, cwd=ENGINE.parent, env=child_env, start_new_session=True)

    def cleanup_worker():
        if worker.poll() is None:
            worker.kill()
        worker.wait(timeout=10)

    request.addfinalizer(cleanup_worker)
    if route == "schedule_resume":
        deadline = time.monotonic() + 10
        while not ready.exists() and worker.poll() is None and time.monotonic() < deadline:
            time.sleep(0.01)
        assert ready.exists() and worker.poll() is None
        os.killpg(worker.pid, signal.SIGSTOP)
        _, stopped = os.waitpid(worker.pid, os.WUNTRACED)
        assert os.WIFSTOPPED(stopped)
        release.touch()
    else:
        assert worker.wait(timeout=10) == 0
    assert inventory(book) == worker_before
    key = "webui/saved" + ("/polish" if route != "legacy" else "")
    queue.processes = {
        key: dict(p=worker, folder=book, kind="polish", paused=route == "schedule_resume", pipeline=route != "legacy")
    }
    if route == "queued_idle":
        queue.processes.clear()
    monkeypatch.setattr(queue, "child_env", lambda: child_env)
    spawned = []
    real_spawn = final_checkup.spawn

    def observe_spawn(*args, **kwargs):
        child = real_spawn(*args, **kwargs)
        spawned.append(child)
        return child

    monkeypatch.setattr(final_checkup, "spawn", observe_spawn)
    if route == "schedule_resume":
        data["status"] = "paused"
        put(book / "web.json", data)
        queue.tick()
        assert load(book / "web.json")["status"] == "paused" and spawned == []
        assert queue.processes[key]["paused"] is True
        data["status"] = "concurrency_paused"
        put(book / "web.json", data)
        pipeline.tick(queue)
        assert load(book / "web.json")["status"] == "concurrency_paused" and spawned == []
        assert load(book / "book_checkup.json")["source_preference"] == "draft"
    before = inventory(book)
    started = time.monotonic()
    queue.tick()
    assert load(book / "web.json")["status"] == "complete"
    if route == "queued":
        put(
            tmp_path / "queue-gap-evidence.json",
            dict(
                route=route,
                worker_command=worker_command,
                worker_exit=worker.returncode,
                final_status=load(book / "web.json")["status"],
                checkup_spawns=len(spawned),
                report_source=load(book / "book_checkup.json")["source_preference"],
                report_quotes=load(book / "book_checkup.json")["unclosed_quotes"]["count"],
                changed=sorted(changed(before, inventory(book))),
            ),
        )
    put(
        tmp_path / "completion-before-wait.json",
        dict(
            route=route,
            checkup_spawns=len(spawned),
            final_status=load(book / "web.json")["status"],
            worker_registered=key in queue.processes,
        ),
    )
    assert len(spawned) == 1
    child = spawned[0]
    assert child.args == final_checkup.checkup_command(book)
    assert child.pid != os.getpid()
    try:
        assert child.wait(timeout=120) == 0
        child.log_thread.join(timeout=10)
        assert not child.log_thread.is_alive()
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=10)
    assert_final_report(book)
    assert worker.wait(timeout=10) == 0
    if route == "schedule_resume":
        assert queue.processes[key]["paused"] is False
    log = (book / "web-process.log").read_text("utf-8")
    assert json.loads(log.splitlines()[-1])["report"] == str(book / "book_checkup.json")
    allowed = {"web.json", "runtime.json", ".pipeline/polish/runtime.json", "book_checkup.json", "web-process.log"}
    differences = changed(before, inventory(book))
    assert {"book_checkup.json", "web-process.log"} <= differences <= allowed
    queue.tick()
    assert len(spawned) == 1, "completed book must not spawn another checkup each tick"
    assert lib.detail("webui/saved")["checkup"]["source_preference"] == "final"
    put(
        tmp_path / "queue-evidence.json",
        dict(
            route=route,
            worker_command=worker_command,
            actual_worker_command=actual_worker_command,
            worker_pid=worker.pid,
            checkup_pid=child.pid,
            exit=child.returncode,
            command=child.args,
            seconds=time.monotonic() - started,
            changed=sorted(differences),
            report=str(book / "book_checkup.json"),
            log=str(book / "web-process.log"),
        ),
    )
