"""Finished watchers must exit before scanning drafts or constructing workers."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from unittest import mock

import pytest

from deai import __main__ as cli
from tools.chapter_outputs import finished


def put(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value) if isinstance(value, dict) else value, encoding="utf-8")


def snapshot(folder):
    return {
        str(path.relative_to(folder)): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in folder.rglob("*")
        if path.is_file()
    }


def finished_book(folder):
    put(folder / "run.json", dict(status="complete", book=dict(chapters=5)))
    put(folder / "web.json", dict(status="complete", polish=True, chapters=5))
    for n, status in enumerate(("polished", "unchanged", "retained", "partial", "original"), 1):
        put(folder / f"chapter-{n:03d}.body.md", f"synthetic draft {n}")
        put(folder / f"chapters/{n:03d}.json", dict(status="complete"))
        put(folder / f"final/chapter-{n:03d}.md", f"synthetic final {n}")
        put(
            folder / f"final/chapter-{n:03d}.deai.json",
            dict(status=status, complete=status in ("polished", "unchanged"), processing_complete=True),
        )
    put(folder / ".pipeline/polish/runtime.json", dict(kind="waiting", stage="old-synthetic-stage"))
    put(folder / "calls.json", "[]")
    assert finished(folder)


@pytest.mark.parametrize("flags", [("--watch", "--pipeline"), ("--watch",), ("--pipeline",)])
@pytest.mark.parametrize("extra_draft", [False, True])
def test_finished_clone_exits_before_scan_pool_or_model(tmp_path, flags, extra_draft):
    source = tmp_path / "synthetic-source"
    finished_book(source)
    if extra_draft:
        # A draft outside the finished plan must never enter chapter processing.
        put(source / "chapter-006.body.md", "synthetic extra draft")
        put(source / "chapters/006.json", dict(status="complete"))
    clone = tmp_path / "synthetic-clone"
    shutil.copytree(source, clone)
    before = snapshot(clone)
    with (
        mock.patch.object(cli, "chapters", side_effect=AssertionError("must not scan drafts")) as scan,
        mock.patch.object(cli, "ThreadPoolExecutor", side_effect=AssertionError("must not create pool")) as pool,
        mock.patch.object(cli, "process", side_effect=AssertionError("must not process chapters")) as process,
        mock.patch.object(cli, "MiniMax", side_effect=AssertionError("must not construct client")) as client,
        mock.patch.object(cli.STOP, "wait", side_effect=AssertionError("must not wait")),
    ):
        assert cli.main(["run", str(clone), "--env", str(tmp_path / "absent.env"), *flags]) == 0
    for boundary in (scan, pool, process, client):
        boundary.assert_not_called()
    assert snapshot(clone) == before


def test_watcher_rechecks_finished_before_scanning_after_wait(tmp_path):
    finished_book(tmp_path)
    put(tmp_path / "run.json", dict(status="running", book=dict(chapters=5)))
    assert not finished(tmp_path)

    def writer_finished(_):
        put(tmp_path / "run.json", dict(status="complete", book=dict(chapters=5)))
        return False

    try:
        with (
            mock.patch.object(cli, "chapters", side_effect=[[], AssertionError("must not scan finished book")]) as scan,
            mock.patch.object(cli.STOP, "wait", side_effect=writer_finished) as wait,
            mock.patch.object(cli, "process", side_effect=AssertionError("must not process chapters")),
            mock.patch.object(cli, "MiniMax", side_effect=AssertionError("must not construct client")),
        ):
            assert cli.main(["run", str(tmp_path), "--watch", "--pipeline"]) == 0
        assert scan.call_count == 1
        wait.assert_called_once_with(2)
        assert finished(tmp_path)
    finally:
        cli.STOP.clear()


@pytest.mark.parametrize("flags", [("--watch", "--pipeline"), ("--watch",)])
def test_finished_cli_does_not_read_blocking_chapter_checkpoint(tmp_path, flags):
    finished_book(tmp_path)
    checkpoint = tmp_path / "chapters/001.json"
    checkpoint.unlink()
    # Reading this synthetic checkpoint would block indefinitely without a writer.
    os.mkfifo(checkpoint)
    assert finished(tmp_path)
    before = snapshot(tmp_path)
    result = subprocess.run(
        [sys.executable, "-B", "-m", "deai", "run", str(tmp_path), "--env", str(tmp_path / "absent.env"), *flags],
        cwd=tmp_path,
        env={
            "PATH": os.defpath,
            "PYTHONPATH": str(Path(cli.__file__).resolve().parents[1]),
            "PYTHONDONTWRITEBYTECODE": "1",
        },
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert result.returncode == 0, result.stderr
    assert snapshot(tmp_path) == before
    assert checkpoint.is_fifo()
