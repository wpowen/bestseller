"""Per-book derived snapshots; disk files remain the only source of truth."""

from copy import deepcopy
from dataclasses import dataclass, field
import os
from pathlib import Path
import stat
import threading


@dataclass
class _Entry:
    lock: object = field(default_factory=threading.RLock)
    signature: object = None
    values: dict = field(default_factory=dict)


class BookCache:
    def __init__(self, safe):
        self.safe = safe
        self._entries = {}
        self._lock = threading.Lock()

    def _signature(self, folder):
        folder = self.safe(folder)
        result = {}

        def record(path, info=None):
            try:
                info = info or path.lstat()
            except FileNotFoundError:
                result[str(path.relative_to(folder))] = None
                return None
            if stat.S_ISLNK(info.st_mode):
                raise ValueError("不可读取此路径")
            result[str(path.relative_to(folder))] = (
                info.st_dev,
                info.st_ino,
                info.st_mode,
                info.st_size,
                info.st_mtime_ns,
                info.st_ctime_ns,
            )
            return info

        def scan(path, matches):
            info = record(path)
            if info is None or not stat.S_ISDIR(info.st_mode):
                return []
            selected = []
            try:
                with os.scandir(path) as entries:
                    for entry in entries:
                        if matches(entry.name):
                            child = path / entry.name
                            try:
                                info = entry.stat(follow_symlinks=False)
                            except FileNotFoundError:
                                record(child)
                                continue
                            record(child, info)
                            selected.append((child, info))
            except FileNotFoundError:
                record(path)
            return selected

        # List each chapter directory once; never enumerate raw HTTP responses.
        scan(folder, lambda n: n.startswith("chapter-") and n.endswith(".md"))
        for name in ("web.json", "run.json", "runtime.json", "story_bible.yaml", "calls.json"):
            record(folder / name)
        scan(folder / "chapters", lambda n: n.endswith(".json"))
        scan(folder / "final", lambda n: n.startswith("chapter-") and n.endswith((".md", ".deai.json")))
        for base in (folder / "http", folder / ".pipeline"):
            record(base)
        record(folder / ".pipeline/polish")
        record(folder / ".pipeline/polish/runtime.json")
        record(folder / "http/ledger.json")

        def final_ledgers(base, include_single=True):
            record(base / "final/http")
            record(base / "final/http/ledger.json")
            for stage, info in scan(base / "final/.deai", lambda n: True):
                if not stat.S_ISDIR(info.st_mode):
                    continue
                record(stage / "http")
                record(stage / "http/ledger.json")
                if include_single and stage.name == "single":
                    for single, info in scan(stage, lambda n: True):
                        if stat.S_ISDIR(info.st_mode):
                            record(single / "final")
                            final_ledgers(single, include_single=False)

        final_ledgers(folder)
        return tuple(sorted(result.items()))

    def get(self, folder, kind, build):
        folder = self.safe(Path(folder))
        with self._lock:
            entry = self._entries.setdefault(folder, _Entry())
        # RLock permits a metadata builder to reuse the same book's cost snapshot.
        with entry.lock:
            for _ in range(3):
                before = self._signature(folder)
                if entry.signature != before:
                    entry.signature = before
                    entry.values.clear()
                if kind in entry.values:
                    return deepcopy(entry.values[kind])
                try:
                    value = build()
                    after = self._signature(folder)
                except Exception:
                    entry.signature = None
                    entry.values.clear()
                    raise
                if before != after:
                    entry.signature = None
                    entry.values.clear()
                    continue
                entry.signature = after
                entry.values[kind] = deepcopy(value)
                return deepcopy(value)
            raise OSError("书籍产物持续更新，请重试")
