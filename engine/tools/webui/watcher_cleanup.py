"""Stop only a book's own pipeline watcher before an authorized deletion."""

import os
from pathlib import Path
import signal
import subprocess
import time

from library import save
from run_status import alive
from tools.chapter_outputs import finished


def is_watcher(pid, folder):
    """Verify a surviving Docker worker's command before signalling its PID."""
    if type(pid) is not int or pid <= 0:
        return False
    try:
        args = (Path("/proc") / str(pid) / "cmdline").read_bytes().decode().strip("\0").split("\0")
    except (OSError, UnicodeError):
        return False
    return args[1:5] == ["-m", "deai", "run", str(folder)] and "--watch" in args[5:] and "--pipeline" in args[5:]


def stop_survivor(pid, folder):
    for sig in (signal.SIGINT, signal.SIGTERM):
        if not is_watcher(pid, folder):
            return
        try:
            os.kill(pid, sig)
            os.kill(pid, signal.SIGCONT)
        except ProcessLookupError:
            return
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if not is_watcher(pid, folder):
                return
            time.sleep(0.05)
    raise ValueError("本书去 AI 进程尚未退出，请稍后再删除")


def stop_for_delete(queue, folder, data):
    complete = finished(folder)
    if not complete and data.get("status") not in ("complete", "paused", "budget_paused"):
        return
    live = [i for i in queue.processes.values() if i["folder"] == folder and i["p"].poll() is None]
    if any(i["kind"] != "polish" or not i.get("pipeline") for i in live):
        return
    stages = data.get("pipeline", {})
    if alive(stages.get("book", {}).get("pid")):
        return
    watchers = {i["p"].pid: i["p"] for i in live}
    for pid in (data.get("pid"), stages.get("polish", {}).get("pid")):
        if alive(pid) and pid not in watchers:
            if not is_watcher(pid, folder):
                return
            watchers[pid] = None
    for pid, child in watchers.items():
        if child is None:
            stop_survivor(pid, folder)
            continue
        try:
            child.send_signal(signal.SIGINT)
            child.send_signal(signal.SIGCONT)
            try:
                child.wait(timeout=3)
            except subprocess.TimeoutExpired:
                child.terminate()
                child.wait(timeout=3)
        except ProcessLookupError:
            child.wait(timeout=3)
        except subprocess.TimeoutExpired:
            raise ValueError("本书去 AI 进程尚未退出，请稍后再删除") from None
    if not watchers:
        return
    if data.get("pid") in watchers:
        data.pop("pid")
    if "polish" in stages:
        stages["polish"] = dict(status="complete" if complete else "paused")
    if complete:
        data.update(status="complete", reason="")
    save(folder / "web.json", data)
