"""Two independent public CLI processes per book, sharing only disk checkpoints."""

import os
import signal
import subprocess
import sys
import time

from library import costs, read, save
from tools.chapter_outputs import finished

# Chapters de-AI'd at once when finished drafts pile up; deai accepts 1-4.
POLISH_CHAPTER_JOBS = 3

TERMINAL = {"error", "quota_exhausted"}


def enabled(data):
    return data.get("pipeline_version") == 1 or data.get("auto_deai") is True or data.get("polish") is True


def migrate(folder, data):
    """Upgrade only Web metadata, preserving prose, checkpoints and failed stages."""
    if not enabled(data) or (data.get("pipeline_version") == 1 and data.get("pipeline")):
        return
    run = read(folder / "run.json", {})
    state = data.get("status")
    after_writing = run.get("status") == "complete" and state in TERMINAL
    stage = "polish" if state in ("polishing", "queued_polish") or after_writing else "book"
    states = data.setdefault("pipeline", {})
    pid = data.get("pid") or (run.get("pid") if stage == "book" else None)
    if pid is not None:
        states.setdefault(stage, dict(status="running", pid=pid))
    if run.get("status") == "complete":
        states["book"] = dict(status="complete")
    else:
        states.setdefault("book", dict(status="queued"))
    runtime = read(folder / "runtime.json", {})
    if runtime.get("kind") in TERMINAL and states.get(stage, {}).get("status") != "complete":
        states[stage] = dict(states.get(stage, {}), status=runtime["kind"], reason=runtime.get("error", ""))
    automatic = data.get("auto_deai") is True or data.get("polish") is True
    data.update(pipeline_version=1, auto_deai=automatic, polish=automatic)
    save(folder / "web.json", data)


class ExistingWriter:
    """Monitor a surviving writer without starting it again or inferring exit success."""

    def __init__(self, folder, pid):
        self.folder, self.pid, self.returncode = folder, pid, None

    def poll(self):
        from run_status import alive

        if self.returncode is None and not alive(self.pid):
            self.returncode = 0 if read(self.folder / "run.json", {}).get("status") == "complete" else 1
        return self.returncode


def reconcile(queue):
    """Enroll legacy owned writers and monitor surviving writers before scheduling."""
    from run_status import ACTIVE, alive

    for folder in queue.library.own.iterdir():
        if not folder.is_dir() or folder.is_symlink() or folder.name.startswith("."):
            continue
        data = read(folder / "web.json", {})
        if not enabled(data):
            continue
        migrate(folder, data)
        if data.get("status") == "complete" and not finished(folder):
            data["status"] = "queued"
            save(folder / "web.json", data)
        live = [i for i in queue.processes.values() if i["folder"] == folder and i["p"].poll() is None]
        states = data.setdefault("pipeline", {})
        for item in live:
            item["pipeline"] = True
            states[item["kind"]] = dict(status="running", pid=item["p"].pid)
        book = states.get("book", {})
        owned_writer = any(i["folder"] == folder and i["kind"] == "book" for i in queue.processes.values())
        if data.get("status") in ACTIVE | {"paused", "budget_paused"} and not owned_writer and alive(book.get("pid")):
            key = str(folder.relative_to(queue.library.root))
            queue.processes[key] = dict(
                p=ExistingWriter(folder, book["pid"]),
                kind="book",
                folder=folder,
                paused=data.get("status") in ("paused", "budget_paused", "concurrency_paused"),
                pipeline=True,
            )
        if live and read(folder / "web.json", {}) != data:
            save(folder / "web.json", data)


def runtime_folder(folder, stage):
    return folder if stage == "book" else folder / ".pipeline/polish"


def items(queue, folder):
    return [p for p in queue.processes.values() if p["folder"] == folder and p.get("pipeline")]


def spawn(queue, folder, data, stage):
    states = data.setdefault("pipeline", {})
    runtime = runtime_folder(folder, stage)
    save(runtime / "runtime.json", {})
    env = queue.child_env()
    if stage == "book":
        command = [
            sys.executable,
            "-m",
            "bookengine",
            str(folder / "book.yaml"),
            "--q1",
            "--style",
            "b",
            "--env",
            str(queue.env),
            "--out",
            str(folder),
        ]
    else:
        command = [
            sys.executable,
            "-m",
            "deai",
            "run",
            str(folder),
            "--env",
            str(queue.env),
            "--watch",
            "--pipeline",
            "--jobs",
            str(POLISH_CHAPTER_JOBS),
        ]
        env["DEAI_RUNTIME_DIR"] = str(runtime)
        if not (data.get("auto_deai") is True or data.get("polish") is True):
            command.append("--original")
    try:
        with (folder / f"web-{stage}.log").open("a") as log:
            child = subprocess.Popen(
                command, cwd=queue.engine.parent, env=env, stdout=log, stderr=log, start_new_session=True
            )
    except OSError:
        states[stage] = dict(status="error", reason="无法启动子进程，可从检查点重试")
        return
    key = str(folder.relative_to(queue.library.root)) + ("/polish" if stage == "polish" else "")
    queue.processes[key] = dict(p=child, kind=stage, folder=folder, paused=False, pipeline=True)
    states[stage] = dict(status="running", pid=child.pid)


def start(queue, folder, data):
    migrate(folder, data)
    data["pipeline_version"] = 1
    if data.get("auto_deai") is True:
        data["polish"] = True
    for field in ("budget", "deai_budget", "total_budget"):
        data.pop(field, None)
    states = data.setdefault("pipeline", {})
    if read(folder / "run.json", {}).get("status") == "complete":
        states["book"] = dict(status="complete")
    if finished(folder):
        states["polish"] = dict(status="complete")
    elif states.get("polish", {}).get("status") == "complete":
        states["polish"] = dict(status="queued")
    for stage in ("book", "polish"):
        if states.get(stage, {}).get("status") not in ("complete", *TERMINAL):
            if not any(i["kind"] == stage and i["p"].poll() is None for i in items(queue, folder)):
                spawn(queue, folder, data, stage)
    update(folder, data)


def update(folder, data):
    states = data.get("pipeline", {})
    failures = [(stage, state) for stage, state in states.items() if state.get("status") in TERMINAL]
    if failures:
        stage, state = failures[0]
        data.update(status=state["status"], reason=f"{stage}: " + state.get("reason", "可从检查点继续"))
    elif states.get("book", {}).get("status") == "complete" and states.get("polish", {}).get("status") == "complete":
        if finished(folder):
            data.update(status="complete", reason="")
        else:
            states["polish"] = dict(status="error", reason="去 AI 已退出但终稿缺章，可从检查点继续")
            data.update(status="error", reason=states["polish"]["reason"])
    else:
        data.update(status="polishing" if states.get("book", {}).get("status") == "complete" else "running", reason="")
    data.pop("pid", None)
    save(folder / "web.json", data)


def tick(queue):
    reconcile(queue)
    folders = {i["folder"] for i in queue.processes.values() if i.get("pipeline")}
    for folder in folders:
        data = read(folder / "web.json", {})
        for key, item in list(queue.processes.items()):
            if item["folder"] != folder or not item.get("pipeline") or item["p"].poll() is None:
                continue
            stage = item["kind"]
            state = read(runtime_folder(folder, stage) / "runtime.json", {})
            success = item["p"].returncode == 0 and (
                read(folder / "run.json", {}).get("status") == "complete" if stage == "book" else finished(folder)
            )
            if success:
                data.setdefault("pipeline", {})[stage] = dict(status="complete")
                save(runtime_folder(folder, stage) / "runtime.json", {})
            else:
                kind = state.get("kind") if state.get("kind") in TERMINAL else "error"
                reason = state.get("error") or "CLI退出；可从检查点重试"
                data.setdefault("pipeline", {})[stage] = dict(status=kind, reason=reason)
                save(
                    runtime_folder(folder, stage) / "runtime.json",
                    dict(state, kind=kind, error=reason, stage=state.get("stage", stage), updated=time.time()),
                )
            del queue.processes[key]
        if data.get("status") == "queued":
            if not any(i["paused"] for i in items(queue, folder)):
                start(queue, folder, data)
        elif data.get("status") not in ("paused", "budget_paused", "concurrency_paused", "orphaned"):
            previous = data.get("status")
            start(queue, folder, data)
            if previous != "complete" and data.get("status") == "complete":
                queue.library.static()


def action(queue, folder, data, name):
    reconcile(queue)
    data = read(folder / "web.json", data)
    live = [i for i in queue.processes.values() if i["folder"] == folder and i["p"].poll() is None]
    from run_status import alive

    owned_pids = {i["p"].pid for i in live}
    if any(s.get("pid") not in owned_pids and alive(s.get("pid")) for s in data.get("pipeline", {}).values()):
        raise ValueError("旧子进程仍在运行，不能重复启动或接管")
    if name == "pause":
        for item in live:
            os.killpg(item["p"].pid, signal.SIGSTOP)
            item["paused"] = True
        data.update(status="paused", reason="已暂停写作和去 AI；检查点保留")
    elif name in ("resume", "retry", "postprocess"):
        if data.get("status") == "orphaned":
            raise ValueError(data.get("reason", "旧进程仍在使用"))
        if name == "postprocess" and live:
            raise ValueError("本书已有运行进程，请勿重复启动")
        if not live and data.get("pipeline_version") != 1:
            if not data.get("polish"):
                data["external_polish_cost"] = max(
                    0, costs(folder)["cost"] - costs(folder, include_final=False)["cost"]
                )
            data.update(auto_deai=True, polish=True)
        if name == "postprocess":
            if read(folder / "run.json", {}).get("status") != "complete":
                raise ValueError("请先完成正文")
            data.update(auto_deai=True, polish=True)
        states = data.setdefault("pipeline", {})
        for stage in ("book", "polish"):
            if states.get(stage, {}).get("status") in TERMINAL:
                states[stage] = dict(status="queued")
                save(runtime_folder(folder, stage) / "runtime.json", {})
        data.update(pipeline_version=1, status="queued", reason="从各自检查点继续，已有终稿保留")
    else:
        raise ValueError("未知操作")
    save(folder / "web.json", data)
    return dict(ok=True)


def schedule(queue):
    """Concurrency is per book: stop/resume both stages as one slot."""
    groups = {}
    for item in queue.processes.values():
        if item["kind"] != "topic" and item["p"].poll() is None:
            groups.setdefault(item["folder"], []).append(item)
    active = [folder for folder, group in groups.items() if any(not i["paused"] for i in group)]
    for folder in active[queue.jobs :]:
        data = read(folder / "web.json", {})
        for item in groups[folder]:
            if not item["paused"]:
                os.killpg(item["p"].pid, signal.SIGSTOP)
                item["paused"] = True
        data.update(status="concurrency_paused", reason="并发上限降低，空出位置后自动继续")
        save(folder / "web.json", data)
    occupied = min(len(active), queue.jobs)
    for folder, group in groups.items():
        data = read(folder / "web.json", {})
        if data.get("status") not in ("concurrency_paused", "queued") or not all(i["paused"] for i in group):
            continue
        if occupied >= queue.jobs:
            continue
        for item in group:
            os.killpg(item["p"].pid, signal.SIGCONT)
            item["paused"] = False
        occupied += 1
        if enabled(data):
            start(queue, folder, data)
        else:
            data.update(status="polishing" if group[0]["kind"] == "polish" else "running", reason="")
            save(folder / "web.json", data)
