"""Deletion and scheduling regressions use temporary books only."""

import os
import signal
import subprocess
import sys
import unittest
from unittest import mock

from test_webui_contracts import WebFixture, library, put
import pipeline

REAL_POPEN = subprocess.Popen
REAL_KILL = os.kill


class DeleteWatcherTests(WebFixture):
    def completed(self, status="complete"):
        folder = self.book(status=status)
        put(folder / "run.json", dict(status="complete", book=dict(chapters=2)))
        return folder

    def test_finals_arriving_during_scheduling_do_not_start_watcher(self):
        folder = self.completed("queued")
        data = library.read(folder / "web.json")
        data.update(polish=True, pipeline_version=1, pipeline=dict(book=dict(status="complete")))
        with mock.patch("pipeline.finished", side_effect=[False, True, True]):
            pipeline.start(self.queue, folder, data)
        self.spawn.assert_not_called()
        self.assertEqual(library.read(folder / "web.json")["status"], "complete")

    def test_delete_stops_only_own_watcher_and_does_not_resurrect_book(self):
        for status in ("complete", "paused"):
            with self.subTest(status=status):
                folder = self.completed(status)
                data = library.read(folder / "web.json")
                data.update(polish=True, pipeline_version=1, pipeline=dict(book=dict(status="complete")))
                put(folder / "web.json", data)
                watcher = mock.Mock(pid=812345)
                watcher.poll.side_effect = lambda: watcher.returncode
                watcher.returncode = None
                watcher.wait.side_effect = lambda timeout: setattr(watcher, "returncode", 0) or 0
                self.queue.processes["webui/story/polish"] = dict(
                    p=watcher, kind="polish", folder=folder, paused=status == "paused", pipeline=True
                )
                self.queue.action("webui/story", "delete")
                self.assertFalse(folder.exists())
                watcher.send_signal.assert_any_call(signal.SIGINT)
                self.queue.tick()
                self.queue.tick()
                self.assertFalse(folder.exists())
                self.spawn.assert_not_called()

    def test_live_writer_is_not_stopped_even_if_display_state_is_paused(self):
        folder = self.completed("paused")
        self.queue.processes["webui/story"] = dict(p=self.child, kind="book", folder=folder, paused=True)
        with self.assertRaises(ValueError):
            self.queue.action("webui/story", "delete")
        self.assertTrue(folder.exists())
        self.child.send_signal.assert_not_called()
        self.killpg.assert_not_called()

    def test_exited_records_are_removed_before_next_tick(self):
        folder = self.completed("paused")
        self.child.poll.return_value = 0
        self.child.returncode = 0
        self.queue.processes["webui/story"] = dict(p=self.child, kind="book", folder=folder, paused=False)
        self.queue.action("webui/story", "delete")
        self.queue.tick()
        self.assertFalse(folder.exists())
        self.spawn.assert_not_called()

    def test_finished_copy_is_not_enrolled_for_polishing(self):
        folder = self.completed()
        put(folder / "web.json", dict(library.read(folder / "web.json"), polish=True))
        for _ in range(3):
            self.queue.tick()
        self.assertEqual(self.lib.meta(folder)["status"], "complete")
        self.spawn.assert_not_called()

    def test_delete_waits_for_real_paused_watcher_to_exit(self):
        # The fixture disables process creation and signals globally; restore
        # them only for this disposable sleeping worker, never for model code.
        with mock.patch("subprocess.Popen", REAL_POPEN), mock.patch("os.kill", REAL_KILL):
            folder = self.completed("paused")
            child = REAL_POPEN(
                [sys.executable, "-c", "import signal; print('ready', flush=True); signal.pause()"],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
            )
            try:
                self.assertEqual(child.stdout.readline().strip(), "ready")
                os.kill(child.pid, signal.SIGSTOP)
                self.queue.processes["webui/story/polish"] = dict(
                    p=child, kind="polish", folder=folder, paused=True, pipeline=True
                )
                self.queue.action("webui/story", "delete")
                self.assertIsNotNone(child.poll())
                self.queue.tick()
                self.assertFalse(folder.exists())
            finally:
                if child.poll() is None:
                    child.kill()
                child.wait(timeout=5)
                child.stdout.close()

    def test_surviving_pid_must_match_exact_book_and_watcher_command(self):
        from watcher_cleanup import is_watcher

        folder = self.completed()
        for arguments, expected in (
            (["python", "-m", "deai", "run", str(folder), "--watch", "--pipeline"], True),
            (["python", "-m", "deai", "run", str(folder.parent / "other"), "--watch", "--pipeline"], False),
            (["python", "-m", "deai", "run", str(folder)], False),
            (["python", "-m", "bookengine", str(folder), "--watch", "--pipeline"], False),
        ):
            with (
                self.subTest(arguments=arguments),
                mock.patch("pathlib.Path.read_bytes", return_value=("\0".join(arguments) + "\0").encode()),
            ):
                self.assertEqual(is_watcher(812345, folder), expected)

    def test_delete_keeps_files_if_watcher_does_not_exit(self):
        folder = self.completed()
        self.queue.processes["webui/story/polish"] = dict(
            p=self.child, kind="polish", folder=folder, paused=False, pipeline=True
        )
        self.child.wait.side_effect = subprocess.TimeoutExpired("watcher", 3)
        with self.assertRaisesRegex(ValueError, "尚未退出"):
            self.queue.action("webui/story", "delete")
        self.assertTrue((folder / "chapter-001.body.md").exists())
        self.assertTrue((folder / "final/chapter-001.md").exists())
        self.child.terminate.assert_called_once()

    @unittest.skipUnless(sys.platform == "linux", "Docker survivor identity uses /proc")
    def test_linux_surviving_watcher_is_verified_and_stopped(self):
        folder = self.completed()
        stub = self.root / "stub"
        put(stub / "deai/__init__.py", "")
        put(stub / "deai/__main__.py", "import signal\nprint('ready', flush=True)\nsignal.pause()\n")
        with mock.patch("subprocess.Popen", REAL_POPEN), mock.patch("os.kill", REAL_KILL):
            child = REAL_POPEN(
                [sys.executable, "-m", "deai", "run", str(folder), "--watch", "--pipeline"],
                cwd=stub,
                env=dict(os.environ, PYTHONPATH=str(stub)),
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
            )
            try:
                self.assertEqual(child.stdout.readline().strip(), "ready")
                put(
                    folder / "web.json",
                    dict(
                        library.read(folder / "web.json"),
                        pipeline_version=1,
                        pipeline=dict(book=dict(status="complete"), polish=dict(status="running", pid=child.pid)),
                    ),
                )
                self.queue.action("webui/story", "delete")
                child.wait(timeout=5)
                self.queue.tick()
                self.assertFalse(folder.exists())
            finally:
                if child.poll() is None:
                    child.kill()
                child.wait(timeout=5)
                child.stdout.close()
