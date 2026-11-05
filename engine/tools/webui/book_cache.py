"""Per-book derived snapshots; disk files remain the only source of truth."""

from copy import deepcopy
from dataclasses import dataclass, field
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
        paths = {
            folder,
            *(
                folder / name
                for name in (
                    "web.json",
                    "run.json",
                    "runtime.json",
                    "story_bible.yaml",
                    "calls.json",
                    "http/ledger.json",
                    ".pipeline/polish/runtime.json",
                )
            ),
        }
        for pattern in (
            "chapter-*.md",
            "chapters/*.json",
            "final/chapter-*.md",
            "final/chapter-*.deai.json",
            "final/**/http/ledger.json",
        ):
            paths.update(folder.glob(pattern))
        # Check each ancestor once, including missing directories. lstat rejects
        # replaced symlinks without resolving every leaf's entire ancestry again.
        for path in tuple(paths):
            parent = path.parent
            while parent != folder and folder in parent.parents:
                paths.add(parent)
                parent = parent.parent
        result = []
        for path in sorted(paths):
            try:
                info = path.lstat()
            except FileNotFoundError:
                result.append((str(path.relative_to(folder)), None))
                continue
            if stat.S_ISLNK(info.st_mode):
                raise ValueError("不可读取此路径")
            result.append(
                (
                    str(path.relative_to(folder)),
                    info.st_dev,
                    info.st_ino,
                    info.st_mode,
                    info.st_size,
                    info.st_mtime_ns,
                    info.st_ctime_ns,
                )
            )
        return tuple(result)

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
