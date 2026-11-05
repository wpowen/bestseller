"""Web-owned queue and public CLI subprocesses. No engine imports/state writes."""

import fcntl
import json
import math
import os
from pathlib import Path
import signal
import shutil
import subprocess
import sys
import threading
import time
import uuid
from library import GENRES, costs, now, read, save, stage_costs
from run_status import auto_deai, recover, runtime_view
import pipeline
from job_inputs import finite, whole, check_card


class Queue:
    def __init__(self, library, env, engine):
        if library.read_only:
            raise ValueError("只读模式禁止启动队列")
        self.library, self.env, self.engine = library, env, engine
        self.lock = threading.RLock()
        self.processes = {}
        self.closed = False
        self.jobs = int(finite(read(library.own / ".queue.json", {}).get("jobs", 3), 1, 4))
        self.exclusive = (library.own / ".service.lock").open("a")
        fcntl.flock(self.exclusive, fcntl.LOCK_EX | fcntl.LOCK_NB)
        recover(library)
        threading.Thread(target=self.loop, daemon=True).start()

    def charge(self, folder):
        return sum(stage_costs(folder, read(folder / "web.json", {})))

    def spending(self):
        return self.library.spending()

    def child_env(self):
        env = dict(os.environ)
        env["PYTHONPATH"] = str(self.engine)
        env["PYTHONUNBUFFERED"] = "1"
        for key in list(env):
            if key in (
                "BOOKENGINE_PHASE",
                "BOOKENGINE_RATE_FILE",
                "BOOKWEB_BUDGET_USD",
                "DEAI_RUNTIME_DIR",
            ) or key.startswith(("BOOKENGINE_BUDGET_", "DEAI_BUDGET_")):
                env.pop(key, None)
        return env

    def topic(self, data):
        genre = data.get("genre")
        chapters = whole(data.get("chapters", 50), 20, 2000, "章数")
        idea = data.get("idea", "")
        channel = data.get("channel", "")
        if genre not in GENRES or not isinstance(idea, str) or len(idea) > 3000:
            raise ValueError("题材或想法无效")
        if not isinstance(channel, str) or channel not in ("", "男频", "女频"):
            raise ValueError("频道无效")
        direction = data.get("direction")
        if "direction" in data:
            if not isinstance(direction, dict) or direction.get("mode") not in ("both", "shell", "variable"):
                raise ValueError("方向模式无效")
            direction = dict(mode=direction["mode"], candidate=check_card(direction.get("candidate"), chapters))
            axes = direction["candidate"].get("axes")
            if not isinstance(axes, dict) or any(
                not isinstance(axes.get(k), str) or not axes[k].strip() for k in ("shell", "variable")
            ):
                raise ValueError("此候选缺少方向轴")
        with self.lock:
            if any(p["kind"] == "topic" and p["p"].poll() is None for p in self.processes.values()):
                raise ValueError("选题正在生成，请稍候")
            key = str(uuid.uuid4())
            folder = self.library.own / ".topics" / key
            topic = dict(
                id=key, status="running", genre=genre, chapters=chapters, idea=idea, channel=channel, candidates=[]
            )
            if direction is not None:
                topic["direction"] = direction
            save(folder / "topic.json", topic)
            command = [sys.executable, "-m", "tools.webui", "--topic-worker", str(folder), "--env", str(self.env)]
            with (folder / "worker.log").open("a") as log:
                p = subprocess.Popen(
                    command,
                    cwd=self.engine.parent,
                    env=self.child_env(),
                    stdout=log,
                    stderr=log,
                    start_new_session=True,
                )
            self.processes["topic/" + key] = dict(p=p, kind="topic", folder=folder, paused=False)
            return self.get_topic(key)

    def get_topic(self, key, internal=False):
        uuid.UUID(key)
        folder = self.library.own / ".topics" / key
        data = read(folder / "topic.json", {})
        if not data or internal:
            return data
        public = {
            k: data[k]
            for k in (
                "id",
                "status",
                "genre",
                "chapters",
                "idea",
                "channel",
                "direction",
                "candidates",
                "notice",
                "judge_status",
                "cost_usd",
                "calls",
                "seed",
                "error",
                "book_id",
            )
            if k in data
        }
        if isinstance(data.get("market"), dict):
            public["market"] = {k: data["market"][k] for k in ("date", "status") if k in data["market"]}
        public["collisions"] = [
            dict(title=row["title"], reasons=[r for r in row["reasons"] if r.startswith("撞车:")])
            for row in data.get("rejected", [])
            if any(r.startswith("撞车:") for r in row["reasons"])
        ]
        public["usage"] = costs(folder)
        runtime = runtime_view(folder, data.get("status"))
        if runtime:
            public.update({k: runtime[k] for k in ("stage", "retry", "next_retry_at", "error") if k in runtime})
            if runtime.get("kind") != "running":
                public["status"] = runtime["kind"]
        return public

    def create(self, data):
        with self.lock:
            topic = self.get_topic(data["topic_id"], internal=True)
            index = data.get("candidate")
            if type(index) is not int or not 0 <= index < len(topic.get("candidates", [])):
                raise ValueError("请先点击选择一个候选题")
            if topic.get("book_id"):
                raise ValueError("此选题已开书，请到书库继续")
            if topic.get("status") != "complete":
                raise ValueError("请等待选题完成")
            if data.get("model") != "flash":
                raise ValueError("公开CLI仅支持 MiniMax Flash")
            chapters = int(finite(data.get("chapters", topic["chapters"]), 1, 2000))
            if chapters != topic["chapters"] or data.get("genre") != topic["genre"]:
                raise ValueError("请按新题材/章数重新选题")
            jobs = whole(data.get("jobs", self.jobs), 1, 4, "同时写作的书数")
            words = whole(data.get("words_per_chapter", 2300), 1500, 4000, "每章字数")
            candidate = topic["candidates"][index]
            if "candidate_edit" in data:
                edited = data["candidate_edit"]
                legacy = set(candidate) == {"title", "premise"}
                check_card(edited, chapters, legacy)
                editable = (
                    {"title", "premise"}
                    if legacy
                    else {"title", "hook", "tags", "premise", "golden_finger_rule", "opening", "engine"}
                )
                if set(edited) != set(candidate) or any(
                    edited[k] != candidate[k] for k in candidate if k not in editable
                ):
                    raise ValueError("仅可编辑卡片内容，不能修改参考分和方向轴")
                candidate = edited
            self.jobs = jobs
            save(self.library.own / ".queue.json", dict(jobs=jobs))
            key = str(uuid.uuid4())
            folder = self.library.own / key
            settings = dict(
                prompt_pack=topic["genre"],
                chapters=chapters,
                words_per_chapter=words,
                user_input=json.dumps(candidate, ensure_ascii=False),
            )
            save(folder / "book.yaml", settings)  # JSON is a YAML subset, no YAML dependency.
            save(
                folder / "web.json",
                dict(
                    title=candidate["title"],
                    genre=topic["genre"],
                    chapters=chapters,
                    polish=data.get("polish", True) is True,
                    auto_deai=data.get("polish", True) is True,
                    pipeline_version=1,
                    status="queued",
                    created=now(),
                    jobs=self.jobs,
                ),
            )
            topic["book_id"] = "webui/" + key
            save(self.library.own / ".topics" / topic["id"] / "topic.json", topic)
            return dict(id="webui/" + key)

    def launch(self, folder, data, kind="book"):
        if pipeline.enabled(data):
            return pipeline.start(self, folder, data)
        if kind == "polish":
            data = dict(data, polish=True)
        for field in ("budget", "deai_budget", "total_budget"):
            data.pop(field, None)
        command = [
            sys.executable,
            "-m",
            "bookengine",
            str(folder / "book.yaml"),
            "--q1",
            "--style",
            "b",
            "--env",
            str(self.env),
            "--out",
            str(folder),
        ]
        env = self.child_env()
        if kind == "polish":
            command = [sys.executable, "-m", "deai", "run", str(folder), "--env", str(self.env)]
        save(folder / "runtime.json", {})
        try:
            with (folder / "web-process.log").open("a") as log:
                p = subprocess.Popen(
                    command, cwd=self.engine.parent, env=env, stdout=log, stderr=log, start_new_session=True
                )
        except OSError as exc:
            reason = type(exc).__name__ + "：无法启动子进程，可从检查点重试"
            data.pop("pid", None)
            data.update(status="error", reason=reason)
            save(folder / "runtime.json", dict(kind="error", stage=kind, updated=time.time(), error=reason))
            save(folder / "web.json", data)
            return
        key = str(folder.relative_to(self.library.root))
        self.processes[key] = dict(p=p, kind=kind, folder=folder, paused=False)
        data.update(status="polishing" if kind == "polish" else "running", pid=p.pid, reason="")
        save(folder / "web.json", data)

    def action(self, key, action, title=None):
        with self.lock:
            folder = self.library.folder(key)
            data = read(folder / "web.json", {})
            if action in ("archive", "restore", "delete", "rename"):
                return self.manage(folder, data, action, title)
            if folder.parent != self.library.own or not data:
                raise ValueError("共享书籍只读")
            live = any(i["folder"] == folder and i["p"].poll() is None for i in self.processes.values())
            if pipeline.enabled(data) or (
                not live and action in ("resume", "retry") and read(folder / "run.json", {}).get("status") == "complete"
            ):
                return pipeline.action(self, folder, data, action)
            item = self.processes.get(key)
            if action == "postprocess":
                if item and item["p"].poll() is None:
                    raise ValueError("本书已有运行进程，请勿重复启动")
                if read(folder / "run.json", {}).get("status") != "complete":
                    raise ValueError("请先完成正文导出")
                base = costs(folder, include_final=False)["cost"]
                if not data.get("polish"):
                    data["external_polish_cost"] = max(0, costs(folder)["cost"] - base)
                data.update(polish=True, status="queued_polish", reason="独立deai CLI后处理；原始草稿不变")
            elif action == "pause":
                if item and item["p"].poll() is None:
                    os.killpg(item["p"].pid, signal.SIGSTOP)
                    item["paused"] = True
                data.update(status="paused", reason="已暂停；在途请求按未知上界计入")
            elif action in ("resume", "retry"):
                if data.get("status") == "orphaned":
                    raise ValueError(data["reason"])
                run = read(folder / "run.json", {})
                kind = (
                    item["kind"]
                    if item and item["p"].poll() is None
                    else (
                        "polish"
                        if run.get("status") == "complete" and (auto_deai(data) or data.get("polish"))
                        else "book"
                    )
                )
                if item and item["p"].poll() is None:
                    if sum(not x["paused"] for x in self.processes.values() if x["p"].poll() is None) >= self.jobs:
                        raise ValueError("并发已满，请稍后继续")
                    os.killpg(item["p"].pid, signal.SIGCONT)
                    item["paused"] = False
                    data.update(status="polishing" if item["kind"] == "polish" else "running", reason="")
                elif run.get("status") == "complete" and kind == "book":
                    raise ValueError("本书已完成，无需重跑")
                else:
                    data.update(
                        status="queued_polish" if kind == "polish" else "queued",
                        reason="从原检查点恢复，不重写已完成章节",
                    )
            else:
                raise ValueError("未知操作")
            save(folder / "web.json", data)
            return {"ok": True}

    def manage(self, folder, data, action, title):
        lib = self.library
        archive = lib.safe(lib.own / ".archive")
        log = lib.safe(lib.own / ".ops.log")
        ledger = lib.safe(lib.own / ".spent.json")
        if lib.read_only or folder.parent not in (lib.own, archive) or folder.name.startswith(".") or not data:
            raise ValueError("仅可管理直属网页书籍")
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
            target = lib.safe((archive if action == "archive" else lib.own) / folder.name)
            if target.exists():
                raise ValueError("目标已存在，禁止覆盖")
        with log.open("a", encoding="utf-8") as output:
            output.write(
                json.dumps(
                    dict(
                        at=now(),
                        action=action,
                        id=str(folder.relative_to(lib.root)),
                        title=current,
                        new_title=title if action == "rename" else None,
                    ),
                    ensure_ascii=False,
                )
                + "\n"
            )
            output.flush()
            os.fsync(output.fileno())
        if action == "delete":
            paid = read(ledger, {})
            paid["webui/" + folder.name] = dict(costs(folder), charged=self.charge(folder))
            save(ledger, paid)
            shutil.rmtree(folder)
            for key, item in list(self.processes.items()):
                if item["folder"] == folder:
                    del self.processes[key]
        elif action == "rename":
            save(folder / "web.json", dict(data, display_title=title.strip()))
        else:
            target.parent.mkdir(exist_ok=True)
            folder.rename(target)
        return dict(ok=True, id=str(target.relative_to(lib.root)))

    def loop(self):
        while not self.closed:
            try:
                with self.lock:
                    self.tick()
            except Exception:
                # A transient concurrent file replacement must not kill the scheduler.
                pass
            time.sleep(0.5)

    def tick(self):
        pipeline.tick(self)
        for key, item in list(self.processes.items()):
            if item.get("pipeline"):
                continue
            p, folder = item["p"], item["folder"]
            if item["kind"] == "topic":
                if p.poll() is not None:
                    topic = read(folder / "topic.json", {})
                    if topic.get("status") == "running":
                        runtime = runtime_view(folder, topic["status"])
                        kind = runtime.get("kind")
                        if kind not in ("quota_exhausted", "error"):
                            kind = "error"
                            runtime = dict(
                                kind=kind,
                                stage=runtime.get("stage", "ideation"),
                                updated=time.time(),
                                error="选题子进程退出，原始调用保留；可从检查点恢复",
                            )
                            save(folder / "runtime.json", runtime)
                        topic.update(status=kind, error=runtime.get("error", "选题未完成"))
                        save(folder / "topic.json", topic)
                    del self.processes[key]
                continue
            data = read(folder / "web.json", {})
            run = read(folder / "run.json", {})
            if p.poll() is not None:
                del self.processes[key]
                data.pop("pid", None)
                runtime = runtime_view(folder, data.get("status"))
                success = p.returncode == 0 and (item["kind"] == "polish" or run.get("status") == "complete")
                if success:
                    next_polish = item["kind"] == "book" and (auto_deai(data) or data.get("polish"))
                    data.update(status="queued_polish" if next_polish else "complete", reason="")
                    save(folder / "runtime.json", {})
                elif runtime.get("kind") in ("quota_exhausted", "error"):
                    data.update(status=runtime["kind"], reason=runtime.get("error") or "CLI退出；可从检查点继续")
                else:
                    reason = run.get("stop_reason") or "CLI退出；可从检查点重试"
                    data.update(status="error", reason=reason)
                    save(
                        folder / "runtime.json",
                        dict(kind="error", stage=runtime.get("stage", item["kind"]), updated=time.time(), error=reason),
                    )
                save(folder / "web.json", data)
                self.library.static()
        pipeline.schedule(self)
        running = len(
            {
                str(x["folder"])
                for x in self.processes.values()
                if x["kind"] != "topic" and not x["paused"] and x["p"].poll() is None
            }
        )
        for folder in sorted(self.library.own.iterdir()):
            if running >= self.jobs:
                break
            if not folder.is_dir() or folder.is_symlink():
                continue
            data = read(folder / "web.json", {})
            if any(i["folder"] == folder and i["p"].poll() is None for i in self.processes.values()):
                continue
            if data.get("status") in ("queued", "queued_polish"):
                self.launch(
                    folder,
                    data,
                    "polish"
                    if data["status"] == "queued_polish" or read(folder / "run.json", {}).get("status") == "complete"
                    else "book",
                )
                running += 1

    def close(self):
        self.closed = True
        with self.lock:
            for item in self.processes.values():
                if item["p"].poll() is None:
                    os.killpg(item["p"].pid, signal.SIGINT)
                    os.killpg(item["p"].pid, signal.SIGCONT)


def topic_worker(folder, env):
    from deai.client import MiniMax
    from tools.runtime_support import QuotaExhausted, write_runtime

    folder = Path(folder)
    data = read(folder / "topic.json")
    try:
        from ideation import run

        save(folder / "runtime.json", {})
        client = MiniMax(folder, env)
        data = run(folder, client, folder.parents[2])
    except Exception as exc:
        data = read(folder / "topic.json", data)
        runtime = read(folder / "runtime.json", {})
        kind = "quota_exhausted" if isinstance(exc, QuotaExhausted) else "error"
        if runtime.get("kind") not in ("quota_exhausted", "error"):
            message = type(exc).__name__ + "：选题未完成，保留原始调用与费用"
            write_runtime(folder, kind, runtime.get("stage") or "ideation", error=message)
            runtime = read(folder / "runtime.json", {})
        data.update(status=runtime["kind"], error=runtime.get("error", "选题未完成"))
    else:
        save(folder / "runtime.json", {})
    save(folder / "topic.json", data)
