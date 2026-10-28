"""Independent byte/hash oracles for the source-archive contract tests."""

import hashlib
import json
import os
from pathlib import Path

RELATIVE_MODULE = "src/bestseller/services/review_issuer_archive.py"
RELATIVE_PIPELINE = "src/bestseller/services/pipelines.py"


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


def aggregate(values: dict[str, str]) -> str:
    digest = hashlib.sha256()
    for relative, value in sorted(values.items()):
        digest.update(relative.encode() + b"\0")
        digest.update(bytes.fromhex(value))
    return digest.hexdigest()


def files_below(root: Path) -> dict[str, bytes]:
    if not root.exists():
        return {}
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file() and not path.is_symlink()
    }


def tree_state(root: Path) -> dict:
    """Include empty directories, links and same-content replacement metadata."""
    if not root.exists():
        return {}
    result = {}
    for path in [root, *sorted(root.rglob("*"))]:
        info = path.lstat()
        payload = (
            os.readlink(path)
            if path.is_symlink()
            else sha(path.read_bytes())
            if path.is_file()
            else None
        )
        result[path.relative_to(root).as_posix()] = (
            info.st_mode,
            info.st_ino,
            info.st_size,
            info.st_mtime_ns,
            info.st_ctime_ns,
            payload,
        )
    return result
