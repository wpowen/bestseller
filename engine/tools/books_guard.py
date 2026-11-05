"""Read-only book inventory and test-time write protection (standard library only)."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys


def repository_books():
    repo = Path(__file__).resolve().parents[2]
    roots = [repo / "books"]
    marker = repo / ".git"
    if marker.is_file():
        gitdir = (repo / marker.read_text().strip().removeprefix("gitdir: ")).resolve()
        common = gitdir / "commondir"
        if common.is_file():
            roots.append((gitdir / common.read_text().strip()).resolve().parent / "books")
    return tuple(dict.fromkeys(p.resolve() for p in roots))


def protected(path):
    path = Path(path).resolve()
    return any(path == root or root in path.parents for root in repository_books())


def check_test_root(root):
    if (
        os.environ.get("BOOKWEB_TEST") == "1" or "pytest" in sys.modules or os.environ.get("PYTEST_CURRENT_TEST")
    ) and protected(root):
        raise ValueError("测试实例禁止使用真实书库；请复制到系统临时目录")


def manifest(root):
    root = Path(root).resolve()
    files = {}
    for current, dirs, names in os.walk(root, followlinks=False):
        for name in sorted([*names, *(d for d in dirs if (Path(current) / d).is_symlink())]):
            path = Path(current) / name
            if path.is_symlink():
                files[str(path.relative_to(root))] = dict(link=os.readlink(path))
                continue
            digest = hashlib.sha256()
            with path.open("rb") as source:
                for block in iter(lambda: source.read(1024 * 1024), b""):
                    digest.update(block)
            files[str(path.relative_to(root))] = dict(size=path.stat().st_size, sha256=digest.hexdigest())
    return dict(root=str(root), files=files)


def compare(before, after):
    old, new = before["files"], after["files"]
    return dict(
        added=sorted(new.keys() - old.keys()),
        deleted=sorted(old.keys() - new.keys()),
        changed=sorted(k for k in old.keys() & new.keys() if old[k] != new[k]),
    )


def install_write_guard():
    """Audit Python filesystem calls, including os.open and dir_fd mutations."""
    roots = repository_books()

    def check(path, dir_fd=None):
        if isinstance(path, int) or path is None:
            return
        path = Path(os.fsdecode(path))
        if not path.is_absolute() and dir_fd not in (None, -1):
            # CPython/macOS exposes the descriptor path through F_GETPATH.
            if sys.platform == "darwin":
                import fcntl

                path = Path(os.fsdecode(fcntl.fcntl(dir_fd, 50, bytes(1024))).split("\0")[0]) / path
            else:
                path = Path(os.readlink(f"/proc/self/fd/{dir_fd}")) / path
        resolved = path.resolve()
        if any(resolved == root or root in resolved.parents for root in roots):
            raise PermissionError("测试禁止写入真实书库")

    def audit(event, args):
        if event == "open":
            path, mode, flags = args
            if (mode and any(c in mode for c in "wax+")) or (
                flags and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND)
            ):
                check(path)
        elif event in ("os.remove", "os.rmdir", "os.mkdir", "os.chmod", "os.chown", "os.utime"):
            check(args[0], args[-1] if isinstance(args[-1], int) else None)
        elif event in ("os.rename", "os.link"):
            check(args[0], args[2] if len(args) > 2 else None)
            check(args[1], args[3] if len(args) > 3 else None)
        elif event == "os.symlink":
            check(args[1], args[2])
        elif event == "os.truncate":
            check(args[0])

    sys.addaudithook(audit)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("snapshot", "verify"))
    parser.add_argument("output", type=Path)
    parser.add_argument("--books", type=Path, default=repository_books()[-1])
    args = parser.parse_args()
    if args.action == "snapshot":
        if protected(args.output):
            parser.error("清单必须写在真实书库外")
        args.output.write_text(json.dumps(manifest(args.books), ensure_ascii=False, indent=2) + "\n")
        print("snapshot saved")
        return 0
    before = json.loads(args.output.read_text())
    changes = compare(before, manifest(before["root"]))
    print(json.dumps(changes, ensure_ascii=False, indent=2))
    return int(any(changes.values()))


if __name__ == "__main__":
    sys.exit(main())
