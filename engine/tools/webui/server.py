"""Loopback HTTP interface. Mutations require JSON and same Origin."""

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import signal
import threading
import tempfile
from urllib.parse import parse_qs, urlparse
import zipfile
from library import GENRES, Library, read, text, save
from job_inputs import whole
from jobs import Queue, topic_worker
from tools.version import __version__

ASSETS = Path(__file__).resolve().parent
ENGINE = ASSETS.parents[1]
REPO = ENGINE.parent
SHARED = Path(os.environ.get("BOOKS_DIR") or REPO / "books")


def config(env):
    values = {}
    for line in text(env).splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.removeprefix("export ").split("=", 1)
            values[k.strip()] = v.strip().strip("\"'")
    configured = bool(values.get(values.get("BESTSELLER__LLM__WRITER__API_KEY_ENV", "MINIMAX_API_KEY")))
    return dict(
        version=__version__,
        genres=[dict(id=k, name=v) for k, v in GENRES.items()],
        models=[
            dict(
                id="flash",
                name="MiniMax M3.1 Flash",
                enabled=configured,
                configured=configured,
                reason="公开CLI固定模型" if configured else "未配置",
            ),
            dict(
                id="m3",
                name="MiniMax M3",
                enabled=False,
                configured=configured,
                reason=("已配置密钥；" if configured else "未配置；") + "公开CLI无模型切换参数",
            ),
            dict(
                id="claude",
                name="Claude",
                enabled=False,
                reason=("已配置；" if values.get("ANTHROPIC_API_KEY") else "未配置；") + "公开CLI不支持",
            ),
        ],
        channels=["男频", "女频"],
        limitations=["单章重写暂无公开命令；重试从检查点续跑"],
    )


def docx(markdown):
    from exporter.documents import docx_bytes

    return docx_bytes("", [(1, "", markdown)])


def export_bytes(detail, version, fmt):
    from exporter import render, heading

    rows = [(r["number"], heading(r["number"], r["title"]), r["final"]) for r in detail["chapters"] if r["final"]]
    missing = sorted(set(range(1, detail["meta"]["total"] + 1)) - {r[0] for r in rows})
    if missing:
        title = "导出说明：仅含终稿；缺少第 " + "、".join(map(str, missing)) + " 章"
        rows.append((detail["meta"]["total"] + 1, "导出说明", title))
    with tempfile.TemporaryDirectory(prefix="bookweb-export-") as temporary:
        out = Path(temporary)
        render(out, detail["meta"]["title"], rows, (fmt,))
        if fmt != "fanqie":
            return (out / ("book." + fmt)).read_bytes()
        data = io.BytesIO()
        with zipfile.ZipFile(data, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in sorted((out / "fanqie").glob("*.txt")):
                archive.write(path, "fanqie/" + path.name)
        return data.getvalue()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def send(self, data, code=200, mime="application/json; charset=utf-8", filename=None, missing=None):
        if not isinstance(data, bytes):
            data = (json.dumps(data, ensure_ascii=False) if mime.startswith("application/json") else data).encode(
                "utf-8"
            )
        self.send_response(code)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; frame-ancestors 'none'",
        )
        if filename:
            self.send_header("Content-Disposition", 'attachment; filename="' + filename + '"')
        if missing is not None:
            self.send_header("X-Missing-Chapters", ",".join(map(str, missing)))
        self.end_headers()
        self.wfile.write(data)

    def hosts(self):
        return getattr(self.server, "hosts", None) or {self.server.origin.removeprefix("http://")}

    def valid_host(self):
        return self.headers.get("Host") in self.hosts()

    def do_GET(self):
        if not self.valid_host():
            self.send({"error": "Host不匹配"}, 403)
            return
        query = urlparse(self.path)
        args = parse_qs(query.query)
        get = lambda k, default="": args.get(k, [default])[0]
        try:
            lib = self.server.library
            path = query.path
            if path == "/api/config":
                self.send(self.server.configuration)
            elif path == "/favicon.ico":
                self.send(b"", code=204, mime="image/x-icon")
            elif path == "/api/settings":
                self.send(dict(jobs=self.server.queue.jobs if self.server.queue else None))
            elif path == "/api/books":
                self.send(dict(books=lib.listing(), spending=lib.spending(), read_only=lib.read_only))
            elif path == "/api/archive":
                self.send(dict(books=lib.archive()))
            elif path == "/api/topics":
                self.send(self.server.queue.get_topic(get("id")))
            elif path == "/api/book":
                self.send(lib.detail(get("id")))
            elif path == "/LIBRARY.html":
                self.send(lib.static(), mime="text/html; charset=utf-8")
            elif path == "/download":
                folder = lib.folder(get("id"))
                fmt = get("format")
                version = get("version", "final")
                if fmt not in ("md", "docx", "txt", "epub", "fanqie") or version not in ("draft", "final"):
                    raise ValueError("格式无效")
                detail = lib.detail(get("id"))
                available = {r["number"] for r in detail["chapters"] if r["final"]}
                if not available:
                    raise ValueError("尚无终稿可导出")
                missing = sorted(set(range(1, detail["meta"]["total"] + 1)) - available)
                body = export_bytes(detail, "final", fmt)
                self.send(
                    body,
                    mime={
                        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                        "md": "text/markdown; charset=utf-8",
                        "txt": "text/plain; charset=utf-8",
                        "epub": "application/epub+zip",
                        "fanqie": "application/zip",
                    }[fmt],
                    filename="book-final." + ("zip" if fmt == "fanqie" else fmt),
                    missing=missing,
                )
            elif path in (
                "/",
                "/index.html",
                "/app.js",
                "/shared.js",
                "/library.js",
                "/wizard.js",
                "/book.js",
                "/presentation.js",
                "/reader.js",
                "/overlays.js",
                "/style.css",
            ):
                name = "index.html" if path == "/" else path[1:]
                self.send(
                    (ASSETS / name).read_bytes(),
                    mime={
                        "html": "text/html; charset=utf-8",
                        "js": "text/javascript; charset=utf-8",
                        "css": "text/css; charset=utf-8",
                    }[name.rsplit(".", 1)[1]],
                )
            else:
                self.send({"error": "未找到"}, 404)
        except (ValueError, KeyError, OSError, TypeError):
            self.send({"error": "请求无效或产物尚未就绪"}, 400)

    def do_POST(self):
        if self.server.library.read_only:
            self.send({"error": "只读模式禁止操作"}, 403)
            return
        if (
            not self.valid_host()
            or self.headers.get("Origin") not in {"http://" + h for h in self.hosts()}
            or self.headers.get("Content-Type", "").split(";")[0] != "application/json"
        ):
            self.send({"error": "仅接受同源JSON操作"}, 403)
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 16384:
                raise ValueError("请求大小无效")
            data = json.loads(self.rfile.read(size))
            if not isinstance(data, dict):
                raise ValueError("需要JSON对象")
            if self.path == "/api/topics":
                result = self.server.queue.topic(data)
            elif self.path == "/api/settings":
                count = whole(data.get("jobs"), 1, 4, "同时写作的书数")
                queue = self.server.queue
                with queue.lock:
                    save(queue.library.own / ".queue.json", dict(jobs=count))
                    queue.jobs = count
                result = dict(jobs=count)
            elif self.path == "/api/books":
                result = self.server.queue.create(data)
            elif self.path == "/api/action":
                result = self.server.queue.action(data["id"], data["action"], title=data.get("title"))
            else:
                self.send({"error": "未找到"}, 404)
                return
            self.send(result)
        except ValueError as exc:
            self.send({"error": str(exc)[:240]}, 400)
        except (KeyError, TypeError, OSError):
            self.send({"error": "请求无效"}, 400)


def main():
    parser = argparse.ArgumentParser(description="127.0.0.1 local book UI")
    parser.add_argument("--env", type=Path, default=Path(os.environ.get("BOOKENGINE_ENV") or REPO / ".env"))
    parser.add_argument("--books", type=Path, default=SHARED)
    parser.add_argument("--read-only", action="store_true", help="仅浏览；不创建目录、队列或服务锁")
    parser.add_argument("--host", default=os.environ.get("BOOKWEB_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("BOOKWEB_PORT", 8787)))
    parser.add_argument(
        "--public-port",
        type=int,
        default=int(os.environ.get("BOOKWEB_PUBLIC_PORT", 0)),
        help="browser-facing port when behind Docker port mapping",
    )
    parser.add_argument("--topic-worker", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    os.umask(0o077)
    if args.topic_worker:
        topic_worker(args.topic_worker, args.env)
        return
    library = Library(args.books, read_only=args.read_only)
    queue = None if args.read_only else Queue(library, args.env.resolve(), ENGINE)
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    public = args.public_port or args.port
    server.origin = f"http://127.0.0.1:{public}"
    server.hosts = {f"127.0.0.1:{public}", f"localhost:{public}"}
    server.library = library
    server.queue = queue
    server.configuration = config(args.env.resolve())
    server.daemon_threads = True

    def stop(*_):
        if queue:
            queue.close()
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    print(f"webui pid={os.getpid()} {server.origin}", flush=True)
    try:
        server.serve_forever()
    finally:
        if queue:
            queue.close()
        server.server_close()
