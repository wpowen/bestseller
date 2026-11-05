"""HTTP regression over disposable real-library snapshots; no model calls."""

import argparse
from contextlib import contextmanager
import html
import io
import json
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET
import zipfile

ENGINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ENGINE))
sys.path.insert(0, str(ENGINE / "tools/webui"))
from books_guard import manifest
from library import Library, bible_data, read, save
from jobs import Queue
from server import Handler, ThreadingHTTPServer, config
from tools.chapter_outputs import finalized


class OfflineQueue(Queue):
    """Exercise management and recovery, fail if scheduling tries to call a model."""

    models_started = 0

    def launch(self, *args, **kwargs):
        self.models_started += 1
        raise AssertionError("smoke must never launch a model")

    def topic(self, *args, **kwargs):
        self.models_started += 1
        raise AssertionError("smoke must never generate topics")


@contextmanager
def no_children(metrics):
    active = True

    def audit(event, args):
        if active and event == "subprocess.Popen":
            metrics["models_started"] += 1
            raise AssertionError("smoke forbids child processes, including model CLIs")

    sys.addaudithook(audit)
    try:
        yield
    finally:
        active = False


@contextmanager
def serving(library, env):
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.origin = f"http://127.0.0.1:{server.server_port}"
    server.library, server.queue, server.configuration = library, None, config(env)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        if server.queue:
            server.queue.close()
            server.queue.exclusive.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def export_text(body, fmt):
    if fmt in ("md", "txt"):
        return body.decode("utf-8")
    with zipfile.ZipFile(io.BytesIO(body)) as archive:
        assert archive.testzip() is None
        names = archive.namelist()
        if fmt == "fanqie":
            return "\n".join(archive.read(n).decode("utf-8-sig") for n in names if n.endswith(".txt"))
        names = ["word/document.xml"] if fmt == "docx" else [n for n in names if n.endswith(".xhtml")]
        return "\n".join("".join(ET.fromstring(archive.read(n)).itertext()) for n in names)


def smoke(fixtures):
    fixtures = Path(fixtures).resolve()
    before = manifest(fixtures)
    metrics = dict(books=0, requests=0, max_books_ms=0, exports=0, reader_chapters=0, models_started=0)
    with no_children(metrics), tempfile.TemporaryDirectory(prefix="bookweb-real-smoke-") as temporary:
        root = Path(temporary)
        books = root / "library"
        shutil.copytree(fixtures / "webui", books / "webui", copy_function=shutil.copyfile)
        for path in books.rglob("*"):
            path.chmod(0o700 if path.is_dir() else 0o600)
        env = root / ".env"
        env.write_text("# no credentials\n")
        lib = Library(books, read_only=True)
        with serving(lib, env) as server:

            def request(path, data=None, binary=False):
                headers = {"Origin": server.origin, "Content-Type": "application/json"}
                body = json.dumps(data).encode() if data is not None else None
                start = time.perf_counter()
                with urlopen(Request(server.origin + path, data=body, headers=headers), timeout=15) as response:
                    assert response.status == 200, path
                    result = response.read()
                    if path == "/api/books":
                        elapsed = (time.perf_counter() - start) * 1000
                        metrics["max_books_ms"] = max(metrics["max_books_ms"], elapsed)
                        assert elapsed < 500, elapsed
                metrics["requests"] += 1
                return result if binary else json.loads(result)

            for asset in ("/", "/library.js", "/book.js", "/reader.js", "/presentation.js", "/shared.js"):
                assert request(asset, binary=True)
            listed = request("/api/books")["books"]
            metrics["books"] = len(listed)
            assert metrics["books"] == 4
            for row in listed:
                detail = request("/api/book?" + urlencode(dict(id=row["id"])))
                folder = lib.folder(row["id"])
                web = read(folder / "web.json", {})
                bible = bible_data(folder / "story_bible.yaml")
                expected = (
                    web.get("display_title")
                    or bible.get("title")
                    or web.get("selected_card", {}).get("title")
                    or web.get("title")
                )
                assert row["title"] == detail["meta"]["title"] == expected
                offline = request("/LIBRARY.html", binary=True).decode("utf-8")
                for chapter in detail["chapters"]:
                    # Hash-route entry + its actual data source, once per retained chapter.
                    assert request("/#" + "/read/" + row["id"] + "/" + str(chapter["number"]), binary=True)
                    reread = request("/api/book?" + urlencode(dict(id=row["id"])))
                    found = next(c for c in reread["chapters"] if c["number"] == chapter["number"])
                    source = folder / f"final/chapter-{chapter['number']:03d}.md"
                    expected_final = (
                        source.read_text("utf-8") if source.exists() and finalized(chapter["report"]) else ""
                    )
                    assert found["final"] == expected_final
                    if expected_final:
                        assert "这个版本尚未生成" not in found["final"]
                        assert "正在去 AI" not in found["final"]
                        assert html.escape(expected_final) in offline
                    metrics["reader_chapters"] += 1
                if not any(c["final"] for c in detail["chapters"]):
                    continue  # No-final books have no download control.
                for fmt in ("md", "txt", "docx", "epub", "fanqie"):
                    body = request(
                        "/download?" + urlencode(dict(id=row["id"], format=fmt, version="draft")), binary=True
                    )
                    content = export_text(body, fmt)
                    from exporter import plain

                    compact = lambda value: "".join(value.split())
                    for chapter in detail["chapters"]:
                        if chapter["final"]:
                            assert compact(plain(chapter["final"]) if fmt == "fanqie" else chapter["final"]) in compact(
                                content
                            ), (row["id"], fmt, chapter["number"])
                        elif chapter["draft"]:
                            assert compact(
                                plain(chapter["draft"]) if fmt == "fanqie" else chapter["draft"]
                            ) not in compact(content)
                    metrics["exports"] += 1

            # Retained chapters are intentionally sparse. Pause copies before enabling
            # the real scheduler so missing chapters can never trigger paid recovery.
            for folder in lib.folders():
                web = read(folder / "web.json", {})
                web["status"] = "paused"
                web.pop("pid", None)
                for stage in web.get("pipeline", {}).values():
                    stage.pop("pid", None)
                save(folder / "web.json", web)
            lib.read_only = False
            server.queue = OfflineQueue(lib, env, ENGINE)
            for row in listed:
                archived = request("/api/action", dict(id=row["id"], action="archive"))["id"]
                assert any(r["id"] == archived for r in request("/api/archive")["books"])
                restored = request("/api/action", dict(id=archived, action="restore"))["id"]
                assert restored == row["id"]
                deleted = request("/api/action", dict(id=restored, action="delete"))["id"]
                assert any(r["id"] == deleted for r in request("/api/trash")["books"])
                assert all(r["id"] != restored for r in request("/api/books")["books"])
                assert request("/api/action", dict(id=deleted, action="restore"))["id"] == restored
            # Derived state variants are explicitly created only on temporary copies.
            key = listed[0]["id"]
            folder = lib.folder(key)
            web = read(folder / "web.json")
            save(folder / "web.json", dict(web, display_title="回归重命名"))
            assert request("/api/book?" + urlencode(dict(id=key)))["meta"]["title"] == "回归重命名"
            original_bible = (folder / "story_bible.yaml").read_text("utf-8")
            (folder / "story_bible.yaml").unlink()
            web.pop("display_title", None)
            web.update(title="回归选题卡", polish=True, auto_deai=False)
            web.pop("pipeline_version", None)
            web.pop("pipeline", None)
            save(folder / "web.json", web)
            assert request("/api/book?" + urlencode(dict(id=key)))["meta"]["title"] == "回归选题卡"
            (folder / "story_bible.yaml").write_text(original_bible, encoding="utf-8")
            for path in (folder / "final").glob("chapter-*.md"):
                path.unlink()
            empty = request("/api/book?" + urlencode(dict(id=key)))
            assert not any(c["final"] for c in empty["chapters"])
            assert all(c["status"] == "draft" for c in empty["chapters"])
            assert b"\xe6\xad\xa3\xe5\x9c\xa8\xe5\x8e\xbb AI" in request("/LIBRARY.html", binary=True)
            metrics["models_started"] += server.queue.models_started
            assert metrics["models_started"] == 0
    metrics["fixture_unchanged"] = before == manifest(fixtures)
    assert metrics["fixture_unchanged"]
    metrics["max_books_ms"] = round(metrics["max_books_ms"], 2)
    return metrics


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixtures", type=Path, default=ENGINE / "tests/fixtures/real_library")
    args = parser.parse_args()
    print(json.dumps(smoke(args.fixtures), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
