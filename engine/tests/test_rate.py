"""Offline rolling-window tests, including independent processes on one inode."""
import fcntl
import json
import multiprocessing
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from bookengine import rate


class WouldWait(Exception):
    pass


def contend(path, barrier, results, rpm, tpm, tokens):
    """Use a fixed shared wall clock; report capacity without waiting a minute."""
    def stop(seconds):
        raise WouldWait

    count = 0
    try:
        with patch.object(rate, "time", SimpleNamespace(time=lambda: 1000, sleep=stop)):
            barrier.wait(timeout=15)
            for _ in range(4):
                try:
                    rate.acquire(path, tokens, 0, rpm=rpm, tpm=tpm)
                except WouldWait:
                    break
                count += 1
        results.put((count, None))
    except Exception as exc:
        results.put((count, type(exc).__name__))


class RateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "rate.json"
        self.now = 1000.0
        self.sleeps = []
        self.inode = None
        clock = SimpleNamespace(time=lambda: self.now, sleep=self.sleep)
        self.clock_patch = patch.object(rate, "time", clock)
        self.clock_patch.start()
        self.addCleanup(self.clock_patch.stop)

    def sleep(self, seconds):
        self.assertGreater(seconds, 0)
        self.assertLessEqual(seconds, 30)
        # A separate open description cannot acquire this if acquire still holds it.
        with self.path.open("r+") as state:
            fcntl.flock(state, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(state, fcntl.LOCK_UN)
        self.assertEqual(self.path.stat().st_ino, self.inode)
        self.sleeps.append(seconds)
        self.now += seconds
        if len(self.sleeps) > 10:
            self.fail("limiter did not make progress")

    def records(self):
        return json.loads(self.path.read_text())

    def test_reserves_input_plus_output_and_returns_none(self):
        self.assertIsNone(rate.acquire(self.path, 2, 3))
        self.assertEqual(self.records(), [[1000.0, 5]])
        self.assertEqual(self.sleeps, [])

    def test_rpm_counts_zero_token_requests_and_sleeps_outside_lock(self):
        rate.acquire(self.path, 0, 0, rpm=2)
        self.inode = self.path.stat().st_ino
        rate.acquire(self.path, 0, 0, rpm=2)
        self.now = 1010
        rate.acquire(self.path, 0, 0, rpm=2)
        self.assertEqual(self.sleeps, [30, 20])
        self.assertEqual(self.records(), [[1060, 0]])
        self.assertEqual(self.path.stat().st_ino, self.inode)

    def test_token_expiry_preserves_younger_reservations(self):
        rate.acquire(self.path, 1, 2, tpm=10)
        self.inode = self.path.stat().st_ino
        self.now = 1010
        rate.acquire(self.path, 2, 3, tpm=10)
        self.now = 1020
        rate.acquire(self.path, 1, 3, tpm=10)
        self.assertEqual(self.sleeps, [30, 10])
        self.assertEqual(self.records(), [[1010, 5], [1060, 4]])

    def test_both_limits_must_be_available(self):
        rate.acquire(self.path, 10, 0, rpm=2, tpm=10)
        self.now = 1010
        rate.acquire(self.path, 0, 0, rpm=2, tpm=10)
        self.inode = self.path.stat().st_ino
        self.now = 1020
        rate.acquire(self.path, 1, 0, rpm=1, tpm=10)
        self.assertEqual(self.sleeps, [30, 10, 10])
        self.assertEqual(self.records(), [[1070, 1]])

    def test_failed_request_is_not_refunded(self):
        with self.assertRaisesRegex(RuntimeError, "simulated"):
            rate.acquire(self.path, 2, 3, tpm=5)
            raise RuntimeError("simulated failed request")
        self.inode = self.path.stat().st_ino
        rate.acquire(self.path, 1, 0, tpm=5)
        self.assertEqual(self.sleeps, [30, 30])

    def test_zero_tpm_allows_zero_tokens(self):
        rate.acquire(self.path, 0, 0, tpm=0)
        self.assertEqual(self.records(), [[1000, 0]])

    def test_invalid_parameters_rejected_before_file_creation(self):
        defaults = dict(input_tokens=0, output_tokens=0, rpm=200, tpm=10000000)
        for key in defaults:
            for value in (True, False, 1.0, -1, None, "1"):
                with self.subTest(key=key, value=value):
                    params = dict(defaults, **{key: value})
                    with self.assertRaises(ValueError):
                        rate.acquire(self.path, **params)
                    self.assertFalse(self.path.exists())
        for params in (dict(rpm=0), dict(input_tokens=4, output_tokens=7, tpm=10)):
            with self.subTest(params=params), self.assertRaises(ValueError):
                rate.acquire(self.path, **dict(defaults, **params))
            self.assertFalse(self.path.exists())

    def test_corrupt_state_fails_closed_without_echoing_content(self):
        private = "private-payload-must-not-escape"
        cases = [private, "{", "null", "{}", '[[]]',
                 json.dumps([[private, 1]]), '[[true, 1]]', '[[1000, true]]',
                 '[[1000, -1]]', '[[1000, 1.5]]', '[[-1, 1]]',
                 '[[NaN, 1]]', '[[Infinity, 1]]', '[[1e999, 1]]',
                 '[[1000, 1, 2]]', b'\xff']
        for content in cases:
            raw = content if isinstance(content, bytes) else content.encode()
            self.path.write_bytes(raw)
            with self.subTest(raw=raw), self.assertRaises(RuntimeError) as caught:
                rate.acquire(self.path, 0, 0)
            self.assertEqual(str(caught.exception), "Invalid rate limiter state")
            self.assertTrue(caught.exception.__suppress_context__)
            self.assertEqual(self.path.read_bytes(), raw)
        self.assertEqual(self.sleeps, [])

    def test_backward_clock_keeps_future_reservations(self):
        rate.acquire(self.path, 1, 0, tpm=1)
        self.inode = self.path.stat().st_ino
        self.now = 990
        rate.acquire(self.path, 1, 0, tpm=1)
        self.assertEqual(self.sleeps, [30, 30, 10])
        self.assertEqual(self.records(), [[1060, 1]])

    def run_processes(self, workers, rpm, tpm, tokens):
        context = multiprocessing.get_context("spawn")
        barrier = context.Barrier(workers)
        results = context.Queue()
        processes = [context.Process(target=contend, args=(
            str(self.path), barrier, results, rpm, tpm, tokens)) for _ in range(workers)]
        try:
            for process in processes:
                process.start()
            reports = [results.get(timeout=25) for _ in processes]
            for process in processes:
                process.join(timeout=5)
                self.assertEqual(process.exitcode, 0)
            self.assertTrue(all(error is None for _, error in reports), reports)
            return sum(count for count, _ in reports)
        finally:
            for process in processes:
                if process.is_alive():
                    process.terminate()
                if process.pid is not None:
                    process.join(timeout=5)
            results.close()
            results.join_thread()

    def test_real_multiprocess_contention_enforces_rpm_and_tokens(self):
        for rpm, tpm, tokens in ((7, 10000000, 0), (200, 21, 3)):
            with self.subTest(rpm=rpm, tpm=tpm):
                self.path.unlink(missing_ok=True)
                self.assertEqual(self.run_processes(6, rpm, tpm, tokens), 7)
                self.assertEqual(len(self.records()), 7)
                self.assertEqual(sum(row[1] for row in self.records()), 7 * tokens)

    def test_restart_retains_reservations_from_exited_process(self):
        self.assertEqual(self.run_processes(1, 1, 5, 5), 1)
        self.inode = self.path.stat().st_ino
        rate.acquire(self.path, 1, 0, rpm=1, tpm=5)
        self.assertEqual(self.sleeps, [30, 30])
        self.assertEqual(self.records(), [[1060, 1]])


if __name__ == "__main__":
    unittest.main()
