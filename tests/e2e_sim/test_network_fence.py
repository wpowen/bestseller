"""Run actual blocked socket operations in fresh processes (audit hooks persist)."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


class NetworkFenceTests(unittest.TestCase):
    def child(self, operation, *, http_endpoint=None):
        with tempfile.TemporaryDirectory(dir=ROOT / "reports/parallel-2026-10-28/P3") as tmp:
            sentinel = Path(tmp) / "network.jsonl"
            code = f"""
import asyncio, json, socket, sys
sys.path.insert(0, {str(ROOT / "tests")!r})
from e2e_sim.network_fence import install_fence
install_fence({{("127.0.0.1", 5432), ("127.0.0.2", 6379)}},
              {str(sentinel)!r}, http_endpoint={http_endpoint!r})
{operation}
"""
            result = subprocess.run(
                [sys.executable, "-B", "-c", code], capture_output=True, text=True
            )
            audit = sentinel.read_text() if sentinel.exists() else ""
            return result, audit

    def test_denied_dns_tcp_udp_unix_and_connect_ex_leave_persistent_sentinel(self):
        for operation in (
            "socket.getaddrinfo('SECRET.invalid', 443)",
            "socket.gethostbyname('SECRET.invalid')",
            "socket.gethostbyaddr('8.8.8.8')",
            "socket.socket().connect(('127.0.0.1', 9999))",
            "socket.socket().connect_ex(('127.0.0.2', 5432))",
            "socket.socket(socket.AF_UNIX).connect('/SECRET.sock')",
            "socket.socket(socket.AF_INET, socket.SOCK_DGRAM)"
            ".sendto(b'SECRET', ('127.0.0.1', 5432))",
            "socket.socket(socket.AF_INET, socket.SOCK_DGRAM).connect(('127.0.0.1', 5432))",
        ):
            with self.subTest(operation=operation):
                result, audit = self.child(operation)
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertIn("NetworkFenceError", result.stderr)
                self.assertTrue(audit)
                self.assertNotIn("SECRET", audit)
                self.assertTrue(json.loads(audit.splitlines()[0])["denied"])

    def test_allowed_numeric_resolution_pair_and_event_loop(self):
        result, audit = self.child("""
assert socket.getaddrinfo('127.0.0.1', 5432, type=socket.SOCK_STREAM)
a,b = socket.socketpair(); a.send(b'ok'); assert b.recv(2) == b'ok'; a.close(); b.close()
asyncio.run(asyncio.sleep(0))
try:
    socket.socket().connect(('127.0.0.1', 5432))
except OSError as exc:
    # The host sandbox may refuse even local TCP; the policy must allow it
    # through to that backend without producing a fence violation.
    from e2e_sim.network_fence import NetworkFenceError
    assert not isinstance(exc, NetworkFenceError)
""")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(audit, "")

    def test_caught_violation_still_leaves_evidence(self):
        result, audit = self.child("""
try:
    socket.create_connection(('127.0.0.1', 9999))
except PermissionError:
    pass
""")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(audit)

    def test_http_requires_explicit_exact_driver_endpoint(self):
        result, audit = self.child("socket.getaddrinfo('127.0.0.3', 8000)")
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(audit)
        result, audit = self.child(
            "socket.getaddrinfo('127.0.0.3', 8000)", http_endpoint=("127.0.0.3", 8000)
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(audit)
        for host, port in (("127.0.0.4", 8000), ("127.0.0.3", 8001)):
            result, audit = self.child(
                f"socket.getaddrinfo({host!r}, {port})", http_endpoint=("127.0.0.3", 8000)
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertTrue(audit)


if __name__ == "__main__":
    unittest.main()
