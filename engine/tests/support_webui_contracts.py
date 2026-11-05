"""Offline web UI contracts. No DB, paid model calls, or real process signals.

Run from the worktree with:
PYTHONDONTWRITEBYTECODE=1 ../../.venv/bin/python -B -m unittest discover \
    -s engine/tests -p 'test_webui*.py' -v
"""

import ast


from email.parser import BytesParser


import io


import json


import math


import os


from pathlib import Path


import subprocess


import sys


import tempfile


from types import SimpleNamespace


import unittest


from unittest import mock


from urllib.parse import urlencode


import uuid


import xml.etree.ElementTree as ET


import zipfile


ENGINE = Path(__file__).resolve().parents[1]


WEBUI = ENGINE / "tools" / "webui"


sys.path.insert(0, str(WEBUI))


import library


import jobs


import server


def put(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    if not isinstance(value, (str, bytes)):
        value = json.dumps(value, ensure_ascii=False)
    if isinstance(value, bytes):
        path.write_bytes(value)
    else:
        path.write_text(value, encoding="utf-8")


def snapshot(folder):
    return {str(p.relative_to(folder)): p.read_bytes() for p in folder.rglob("*") if p.is_file() and not p.is_symlink()}


class MemorySocket:
    """Feed actual HTTP bytes through BaseHTTPRequestHandler, without binding a port."""

    def __init__(self, request):
        self.input = io.BytesIO(request)
        self.output = bytearray()

    def makefile(self, *args, **kwargs):
        return self.input

    def sendall(self, data):
        self.output.extend(data)


class WebFixture(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="webui-contract-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        environment = mock.patch.dict(
            os.environ,
            {
                "PATH": os.defpath,
                "PYTHONDONTWRITEBYTECODE": "1",
            },
            clear=True,
        )
        environment.start()
        self.addCleanup(environment.stop)
        self.lib = library.Library(self.root / "books")
        self.env = self.root / "test.env"
        put(self.env, "# deliberately no credentials\n")
        # No background scheduler, paid subprocess, or process signal may escape.
        patches = [
            mock.patch.object(jobs.threading.Thread, "start"),
            mock.patch.object(jobs.subprocess, "Popen"),
            mock.patch.object(jobs.os, "killpg"),
            mock.patch.object(jobs.os, "kill"),
        ]
        self.thread, self.spawn, self.killpg, self.kill = [p.start() for p in patches]
        for patch in patches:
            self.addCleanup(patch.stop)
        self.child = mock.Mock(pid=812345, returncode=None)
        self.child.poll.return_value = None
        self.spawn.return_value = self.child
        self.queue = jobs.Queue(self.lib, self.env, ENGINE)
        self.addCleanup(self.queue.exclusive.close)
        self.origin = "http://127.0.0.1:18765"
        self.http = SimpleNamespace(library=self.lib, queue=self.queue, origin=self.origin, configuration={})

    def book(self, key="webui/story", *, status="queued", budget=0.2, total=2, final=True):
        folder = self.lib.root / key
        put(folder / "run.json", {"status": "running", "book": {"chapters": total}})
        put(
            folder / "web.json",
            {"title": "潮汐 <归途>", "status": status, "budget": budget, "chapters": total, "polish": False},
        )
        put(folder / "book.yaml", {"chapters": total})
        for n in range(1, total + 1):
            put(folder / f"chapter-{n:03d}.body.md", f"草稿{n}：她推开门。<script>危险</script> & 灯光。")
            put(folder / f"chapters/{n:03d}.json", {"title": f"潮声{n}", "status": "complete"})
            if final:
                put(folder / f"final/chapter-{n:03d}.md", f"定稿{n}：她听见潮声。<灯> & 船。")
                put(folder / f"final/chapter-{n:03d}.deai.json", {"status": "polished", "complete": True})
        put(folder / "book.md", "# CLI原样导出\n\n草稿总集\n")
        put(folder / "book.docx", server.docx("CLI草稿总集\n第一章 & <船>"))
        return folder

    def topic(self):
        key = str(uuid.uuid4())
        put(
            self.lib.own / ".topics" / key / "topic.json",
            {
                "id": key,
                "status": "complete",
                "chapters": 2,
                "genre": "cozy-mystery",
                "candidates": [{"title": "潮声", "premise": "摆渡人要在涨潮前找回失踪乘客。"}],
            },
        )
        return {
            "topic_id": key,
            "candidate": 0,
            "genre": "cozy-mystery",
            "chapters": 2,
            "model": "flash",
            "budget": 0.2,
            "jobs": 2,
        }

    def request(self, path, *, method="GET", data=None, headers=None, raw=None):
        body = raw if raw is not None else (json.dumps(data).encode() if data is not None else b"")
        fields = {"Host": self.origin.removeprefix("http://"), "Connection": "close"}
        if method == "POST":
            fields.update(Origin=self.origin, **{"Content-Type": "application/json", "Content-Length": str(len(body))})
        for name, value in (headers or {}).items():
            if value is None:
                fields.pop(name, None)
            else:
                fields[name] = value
        wire = (
            f"{method} {path} HTTP/1.1\r\n" + "".join(f"{k}: {v}\r\n" for k, v in fields.items()) + "\r\n"
        ).encode() + body
        sock = MemorySocket(wire)
        server.Handler(sock, ("127.0.0.1", 12345), self.http)
        head, payload = bytes(sock.output).split(b"\r\n\r\n", 1)
        status, header_block = head.split(b"\r\n", 1)
        return int(status.split()[1]), BytesParser().parsebytes(header_block), payload

    def download(self, key="webui/story", fmt="txt", version="draft", **extra):
        return self.request("/download?" + urlencode(dict(id=key, format=fmt, version=version, **extra)))


class BoundaryTests(unittest.TestCase):
    pass


class LibraryTests(WebFixture):
    pass


class CostTests(WebFixture):
    pass


class HttpTests(WebFixture):
    pass
