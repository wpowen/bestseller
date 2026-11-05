"""Recoverable book management and seven-day trash retention."""

import datetime as dt
import json
import os
from pathlib import Path
import re
import shutil
from library import costs, now, read, save
from tools.redact import Redactor

TRASH_NAME = re.compile(r"(\d{8}T\d{12}Z)--(.+)")


def trash_parts(name):
    match = TRASH_NAME.fullmatch(name)
    if not match or match[2].startswith("."):
        raise ValueError("无效回收站记录")
    return dt.datetime.strptime(match[1], "%Y%m%dT%H%M%S%fZ").replace(tzinfo=dt.timezone.utc), match[2]


def record(queue, action, folder, title, new_title=None, origin_info=None):
    entry = dict(
        at=now(),
        action=action,
        id=str(folder.relative_to(queue.library.root)),
        title=title,
        new_title=new_title,
        **(origin_info or dict(source="cli", user_agent="", client="127.0.0.1")),
    )
    log = queue.library.safe(queue.library.own / ".ops.log")
    with log.open("a", encoding="utf-8") as output:
        output.write(Redactor(queue.env)(json.dumps(entry, ensure_ascii=False)) + "\n")
        output.flush()
        os.fsync(output.fileno())


def cleanup_trash(queue):
    trash = queue.library.safe(queue.library.own / ".trash")
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=7)
    for folder in trash.glob("*"):
        if folder.is_symlink() or not folder.is_dir():
            continue
        try:
            deleted, _ = trash_parts(folder.name)
        except ValueError:
            continue
        if deleted < cutoff:
            queue.library.safe(folder)
            record(queue, "purge", folder, queue.library.meta(folder)["title"])
            shutil.rmtree(folder)


def manage(self, folder, data, action, title, origin_info=None):
    lib = self.library
    archive = lib.safe(lib.own / ".archive")
    trash = lib.safe(lib.own / ".trash")
    in_trash = folder.parent == trash
    ledger = lib.safe(lib.own / ".spent.json")
    if lib.read_only or folder.parent not in (lib.own, archive, trash) or folder.name.startswith(".") or not data:
        raise ValueError("仅可管理直属网页书籍")
    if in_trash and action != "restore":
        raise ValueError("回收站只允许恢复")
    original_name = trash_parts(folder.name)[1] if in_trash else folder.name
    for path in folder.rglob("*"):
        if path.is_symlink():
            raise ValueError("书目录含符号链接")
    if action == "delete":
        from watcher_cleanup import stop_for_delete

        stop_for_delete(self, folder, data)
    live = [i for i in self.processes.values() if i["folder"] == folder and i["p"].poll() is None]
    if data.get("status") in ("running", "polishing", "orphaned") or live:
        raise ValueError("运行中或孤立进程书籍不可操作")
    from run_status import alive

    if any(alive(stage.get("pid")) for stage in data.get("pipeline", {}).values()):
        raise ValueError("写作或去 AI 子进程仍存在，请先结束进程")
    if data.get("pid") is not None:
        try:
            os.kill(data["pid"], 0)
        except ProcessLookupError:
            pass
        else:
            raise ValueError("子进程仍存在，请先结束进程")
    current = lib.meta(folder)["title"]
    if action == "rename" and (
        not isinstance(title, str) or not 1 <= len(title.strip()) <= 80 or any(ord(c) < 32 for c in title)
    ):
        raise ValueError("书名须为1–80字的单行文字")
    target = folder
    if action in ("archive", "restore"):
        if (action == "archive") != (folder.parent == lib.own):
            raise ValueError("书籍已归档或未归档")
        target = lib.safe((archive if action == "archive" else lib.own) / original_name)
        if target.exists():
            raise ValueError("目标已存在，禁止覆盖")
    if action == "delete":
        stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        target = lib.safe(trash / (stamp + "--" + folder.name))
        if target.exists():
            raise ValueError("回收站目标已存在")
    record(self, action, folder, current, title if action == "rename" else None, origin_info)
    if action == "delete":
        paid = read(ledger, {})
        paid["webui/" + folder.name] = dict(costs(folder), charged=self.charge(folder))
        save(ledger, paid)
        target.parent.mkdir(exist_ok=True)
        folder.rename(target)
        for key, item in list(self.processes.items()):
            if item["folder"] == folder:
                del self.processes[key]
    elif action == "rename":
        save(folder / "web.json", dict(data, display_title=title.strip()))
    else:
        target.parent.mkdir(exist_ok=True)
        folder.rename(target)
    return dict(ok=True, id=str(target.relative_to(lib.root)))
