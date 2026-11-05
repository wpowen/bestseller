"""Production book paths are never writable from tests."""

import os
from pathlib import Path

import pytest

from tools import books_guard


def test_manifest_detects_added_removed_and_changed(tmp_path):
    root = tmp_path / "books"
    root.mkdir()
    (root / "old").write_text("old")
    (root / "changed").write_text("before")
    before = books_guard.manifest(root)
    (root / "old").unlink()
    (root / "changed").write_text("after")
    (root / "new").write_text("new")
    assert books_guard.compare(before, books_guard.manifest(root)) == {
        "added": ["new"],
        "deleted": ["old"],
        "changed": ["changed"],
    }


def test_test_startup_rejects_repository_books_and_aliases(tmp_path, monkeypatch):
    monkeypatch.setenv("BOOKWEB_TEST", "1")
    root = books_guard.repository_books()[0]
    alias = tmp_path / "alias"
    alias.symlink_to(root, target_is_directory=True)
    for path in (root, root / "nested", alias):
        with pytest.raises(ValueError, match="真实书库"):
            books_guard.check_test_root(path)
    books_guard.check_test_root(tmp_path / "safe")


def test_global_audit_blocks_mutations_before_io(tmp_path):
    root = books_guard.repository_books()[0]
    path = root / ".g34-must-never-exist"
    for action in (
        lambda: path.write_text("never"),
        lambda: os.open(path, os.O_CREAT | os.O_WRONLY, 0o600),
        lambda: path.mkdir(),
        lambda: path.unlink(),
        lambda: path.rename(tmp_path / "moved"),
        lambda: (tmp_path / "missing").rename(path),
    ):
        with pytest.raises(PermissionError, match="真实书库"):
            action()
    assert not path.exists()


def test_relative_descriptor_write_is_blocked_in_isolated_child(tmp_path):
    import subprocess
    import sys

    engine = Path(__file__).resolve().parents[1]
    script = """
import os, sys
from pathlib import Path
from tools import books_guard
root = Path(sys.argv[1])
books_guard.repository_books = lambda: (root,)
books_guard.install_write_guard()
fd = os.open(root, os.O_RDONLY)
try:
    try:
        child = os.open('sentinel', os.O_CREAT | os.O_WRONLY, 0o600, dir_fd=fd)
    except PermissionError:
        pass
    else:
        os.close(child)
        raise AssertionError('descriptor write bypassed guard')
finally:
    os.close(fd)
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path)], cwd=engine, capture_output=True, text=True, timeout=10
    )
    assert result.returncode == 0, result.stderr
