"""Real HTTP cache contracts; all books, ledgers and runtime files are offline fixtures."""

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import shutil
import sys
import threading
from types import MethodType, SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent / "fixtures"))
from books_benchmark import benchmark, get_json, http_library, ledger_row, put
import jobs


def book(root, key="webui/story"):
    folder = root / key
    put(folder / "run.json", dict(status="running", execution_seconds=60, book=dict(chapters=3)))
    put(
        folder / "web.json",
        dict(
            title="旧书名",
            status="running",
            polish=True,
            chapters=3,
            pipeline_version=1,
            pipeline=dict(book=dict(status="running"), polish=dict(status="running")),
        ),
    )
    put(folder / "story_bible.yaml", dict(title="渡口来信", premise="船工寻找父亲。"))
    put(folder / "calls.json", [ledger_row("draft")])
    put(folder / "http/ledger.json", [ledger_row("bible")])
    put(folder / "runtime.json", dict(kind="running", stage="draft"))
    put(folder / ".pipeline/polish/runtime.json", {})
    for n in (1, 2):
        put(folder / f"chapter-{n:03d}.body.md", f"林舟走到渡口，推开木门。第{n}天。")
        put(folder / f"chapters/{n:03d}.json", dict(status="complete", title=f"渡口{n}", within_band=True))
    put(folder / "final/chapter-001.md", "林舟回到渡口。")
    put(folder / "final/chapter-001.deai.json", dict(status="polished", complete=True, after_score=7.3))
    put(folder / "final/.deai/001/http/ledger.json", [ledger_row("repair")])
    return folder


@pytest.fixture
def http(tmp_path):
    root = tmp_path / "fixture-data"
    folder = book(root)
    with http_library(root) as server:
        yield server, folder


def meta(server):
    return get_json(server)["books"][0]


def update(path, **fields):
    value = json.loads(path.read_text())
    value.update(fields)
    put(path, value)


def actual_reads(monkeypatch, root):
    counts = Counter()
    lock = threading.Lock()
    original = Path.read_text

    def tracked(path, *args, **kwargs):
        if path.is_relative_to(root):
            with lock:
                counts[str(path.relative_to(root))] += 1
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", tracked)
    return counts


def expensive_reads(counts):
    return {
        p: count
        for p, count in counts.items()
        if p.endswith((".body.md", ".deai.json", "ledger.json", "calls.json", "story_bible.yaml"))
        or "/chapters/" in p
        or ("/final/" in p and p.endswith(".md"))
    }


def test_warm_http_reuses_meta_and_costs_without_rereading_content(http, monkeypatch):
    server, folder = http
    expected = get_json(server)
    counts = actual_reads(monkeypatch, server.library.root)
    assert get_json(server) == expected
    assert not expensive_reads(counts), "warm GET reread cached book content and ledgers"


def test_listing_and_spending_share_costs_on_cold_http(http, monkeypatch):
    server, folder = http
    counts = actual_reads(monkeypatch, server.library.root)
    payload = get_json(server)
    assert payload["spending"] == payload["books"][0]["cost"]
    assert counts["webui/story/final/.deai/001/http/ledger.json"] == 1
    assert counts["webui/story/calls.json"] <= 2  # total and writer-only views


@pytest.mark.parametrize(
    "source,changes,field,expected",
    [
        ("web.json", {"display_title": "用户改名"}, "title", "用户改名"),
        ("web.json", {"status": "paused"}, "status", "paused"),
        ("web.json", {"reason": "等待用户继续"}, "step", "等待用户继续"),
        ("web.json", {"created": "2026-11-05T12:00:00Z"}, "created", "2026-11-05T12:00:00Z"),
        ("web.json", {"polish": False}, "deai_cost", 0),
        ("run.json", {"book": {"chapters": 5}}, "total", 5),
        ("run.json", {"execution_seconds": 90}, "eta", 45),
        ("story_bible.yaml", {"title": "圣经新书名"}, "title", "圣经新书名"),
        ("story_bible.yaml", {"premise": "渡口的新秘密。"}, "synopsis", "渡口的新秘密。"),
        ("final/chapter-001.deai.json", {"after_score": 2.1}, "score", 2.1),
        ("final/chapter-001.deai.json", {"complete": False}, "chapters", 0),
        ("chapters/001.json", {"status": "draft"}, "written_chapters", 1),
    ],
)
def test_key_file_update_changes_real_http_metadata(http, source, changes, field, expected):
    server, folder = http
    assert meta(server)[field] != expected
    update(folder / source, **changes)
    assert meta(server)[field] == expected


def test_checkpoint_title_and_findings_are_refreshed(http):
    server, folder = http
    before = meta(server)
    assert before["quality_issues"] == []
    update(
        folder / "chapters/001.json",
        title="改过的章节名",
        within_band=False,
        warnings=["称谓不一致"],
        hard_conflicts=["时间矛盾"],
    )
    after = meta(server)
    assert after["chapter_info"][0]["title"] == "改过的章节名"
    assert after["quality_issues"][0]["warnings"] == ["称谓不一致"]
    assert after["quality_issues"][0]["hard_conflicts"] == ["时间矛盾"]


def test_final_and_draft_add_change_delete_and_report_completion(http):
    server, folder = http
    assert meta(server)["chapters"] == 1
    put(folder / "final/chapter-001.md", "新" * 12)
    assert meta(server)["words"] == 12
    put(folder / "chapter-001.body.md", "草稿修改后，她走回河边。")
    detail = get_json(server, "/api/book?id=webui/story")
    assert detail["chapters"][0]["draft"] == "草稿修改后，她走回河边。"
    assert detail["meta"]["words"] == 12
    put(folder / "final/chapter-002.md", "终" * 8)
    assert meta(server)["chapters"] == 1  # text alone is not a final receipt
    put(folder / "final/chapter-002.deai.json", dict(status="retained", processing_complete=True))
    assert meta(server)["chapters"] == 2
    assert meta(server)["words"] == 20
    (folder / "final/chapter-002.deai.json").unlink()
    assert meta(server)["chapters"] == 1
    (folder / "chapter-001.body.md").unlink()
    assert meta(server)["chapters"] == 0
    assert meta(server)["written_chapters"] == 1
    (folder / "chapter-002.body.md").unlink()
    put(folder / "chapter-003.md", "旧格式草稿。")
    assert meta(server)["chapter_info"][2]["status"] == "draft"
    put(folder / "chapter-003.md", "改过的旧格式草稿。")
    assert get_json(server, "/api/book?id=webui/story")["chapters"][0]["draft"] == "改过的旧格式草稿。"
    (folder / "chapter-003.md").unlink()
    assert meta(server)["chapter_info"][2]["status"] == "pending"
    put(folder / "chapter-003.body.md", "新写出的正文。")
    put(folder / "chapters/003.json", dict(status="complete", title="第三章"))
    assert meta(server)["chapter_info"][2]["title"] == "第三章"
    assert meta(server)["written_chapters"] == 1


@pytest.mark.parametrize("source", ["runtime.json", ".pipeline/polish/runtime.json"])
def test_each_pipeline_runtime_and_stage_completion_refresh_http(http, source):
    server, folder = http
    assert meta(server)["status"] == "running"
    put(folder / source, dict(kind="waiting", stage="recheck", retry=4, next_retry_at=1793836860, error="接口等待"))
    value = meta(server)
    assert (value["status"], value["stage"], value["retry"], value["next_retry_at"], value["error"]) == (
        "waiting",
        "recheck",
        4,
        1793836860,
        "接口等待",
    )
    web = json.loads((folder / "web.json").read_text())
    web["pipeline"]["book" if source == "runtime.json" else "polish"]["status"] = "complete"
    put(folder / "web.json", web)
    assert meta(server)["status"] == "running"


@pytest.mark.parametrize(
    "source",
    ["calls.json", "http/ledger.json", "final/.deai/001/http/ledger.json", "final/.deai/new-stage/http/ledger.json"],
)
def test_ledger_content_and_membership_change_costs_and_spending(http, source):
    server, folder = http
    before = get_json(server)
    path = folder / source
    rows = json.loads(path.read_text()) if path.exists() else []
    row = dict(model="MiniMax-M3.1-Flash", usage=dict(prompt_tokens=1000, completion_tokens=1000))
    put(path, rows + [row])
    after = get_json(server)
    assert after["books"][0]["calls"] == before["books"][0]["calls"] + 1
    assert after["books"][0]["tokens"] == before["books"][0]["tokens"] + 2000
    assert after["books"][0]["cost"] == pytest.approx(before["books"][0]["cost"] + 0.00075)
    assert after["spending"] == pytest.approx(before["spending"] + 0.00075)
    writer_delta = 0 if source.startswith("final/") else 0.00075
    assert after["books"][0]["writer_cost"] == pytest.approx(before["books"][0]["writer_cost"] + writer_delta)
    put(path, rows)
    assert get_json(server) == before
    if not rows:
        path.unlink()
        assert get_json(server) == before


def test_missing_usage_and_corrupt_ledger_never_become_zero(http):
    server, folder = http
    assert meta(server)["unknown"] == 0
    put(folder / "calls.json", [ledger_row("unknown", unknown=True)])
    value = meta(server)
    assert value["unknown"] == 1
    assert value["unknown_bound"] > 0
    put(folder / "calls.json", "{unfinished")
    value = meta(server)
    assert value["unknown"] == 1
    assert value["unknown_bound"] >= 1e6
    put(folder / "calls.json", [ledger_row("recovered")])
    assert meta(server)["unknown"] == 0


@pytest.mark.parametrize("source", ["web.json", "final/.deai/001/http/ledger.json"])
def test_atomic_replace_same_size_and_restored_mtime_refreshes_real_fields(http, source):
    server, folder = http
    if source == "web.json":
        update(folder / source, display_title="旧书名")
    before = get_json(server)
    path = folder / source
    stat = path.stat()
    old = path.read_text()
    new = old.replace("旧书名", "新书名") if source == "web.json" else old.replace("4200", "5200")
    assert new != old and len(new.encode()) == stat.st_size
    temporary = path.with_name(path.name + ".replacement")
    put(temporary, new)
    os.utime(temporary, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    os.replace(temporary, path)
    assert path.stat().st_mtime_ns == stat.st_mtime_ns and path.stat().st_ino != stat.st_ino
    after = get_json(server)
    if source == "web.json":
        assert after["books"][0]["title"] == "新书名"
    else:
        assert after["books"][0]["tokens"] == before["books"][0]["tokens"] + 1000
        assert after["spending"] == pytest.approx(before["spending"] + 0.00015)


def test_archive_restore_delete_spent_and_new_books(http):
    server, folder = http
    original = get_json(server)
    archive = folder.parent / ".archive" / folder.name
    archive.parent.mkdir()
    folder.rename(archive)
    assert get_json(server)["books"] == []
    assert get_json(server)["spending"] == original["spending"]
    assert get_json(server, "/api/archive")["books"][0]["id"] == "webui/.archive/story"
    update(archive / "web.json", display_title="归档改名")
    assert get_json(server, "/api/archive")["books"][0]["title"] == "归档改名"
    archive.rename(folder)
    assert meta(server)["title"] == "归档改名"
    put(folder.parent / ".spent.json", {"webui/story": dict(charged=original["spending"])})
    assert get_json(server)["spending"] == original["spending"]
    shutil.rmtree(folder)
    assert get_json(server)["books"] == []
    assert get_json(server)["spending"] == original["spending"]
    put(folder.parent / ".spent.json", {"webui/story": dict(charged=original["spending"] + 1)})
    assert get_json(server)["spending"] == original["spending"] + 1
    book(server.library.root)
    assert meta(server)["title"] == "渡口来信"
    put(folder.parent / ".spent.json", [])
    assert "error" in get_json(server, expected_status=400)


def test_topics_costs_and_runtime_use_real_http_without_queue_worker(http):
    server, folder = http
    topic_id = "00000000-0000-4000-8000-000000000001"
    topic = folder.parent / ".topics" / topic_id
    before = get_json(server)["spending"]
    put(topic / "topic.json", dict(id=topic_id, status="running", genre="cozy-mystery", chapters=30))
    put(topic / "calls.json", [ledger_row("topic")])
    put(topic / "http/ledger.json", [ledger_row("judge")])
    # Bind the actual read method; Queue.__init__ and its scheduler never run.
    server.queue = SimpleNamespace(library=server.library)
    server.queue.get_topic = MethodType(jobs.Queue.get_topic, server.queue)
    path = f"/api/topics?id={topic_id}"
    value = get_json(server, path)
    assert value["usage"]["calls"] == 2
    update(topic / "topic.json", notice="候选已更新")
    assert get_json(server, path)["notice"] == "候选已更新"
    assert get_json(server)["spending"] == pytest.approx(before + value["usage"]["cost"])
    put(topic / "runtime.json", dict(kind="waiting", retry=3, stage="judge", error="选题接口等待"))
    assert get_json(server, path)["error"] == "选题接口等待"
    put(topic / "http/ledger.json", [ledger_row("unknown", unknown=True)])
    assert get_json(server, path)["usage"]["unknown"] == 1
    assert get_json(server)["spending"] == pytest.approx(before + get_json(server, path)["usage"]["cost"])
    shutil.rmtree(topic)
    assert get_json(server)["spending"] == before


def test_unchanged_books_are_not_rebuilt_after_another_book_changes(http, monkeypatch):
    server, folder = http
    other = book(server.library.root, "webui/other")
    assert len(get_json(server)["books"]) == 2
    update(other / "web.json", display_title="另一本书改名")
    counts = actual_reads(monkeypatch, server.library.root)
    assert "另一本书改名" in [row["title"] for row in get_json(server)["books"]]
    assert not {p: n for p, n in expensive_reads(counts).items() if p.startswith("webui/story/")}


def test_cached_results_are_copied_and_request_mutation_cannot_poison_http(http):
    server, folder = http
    expected = get_json(server)
    local = server.library.listing()
    local[0]["chapter_info"][0]["title"] = "污染"
    local[0]["quality_issues"].append(dict(number=99))
    assert get_json(server) == expected


def test_http_retries_snapshot_when_files_change_during_metadata_read(http, monkeypatch):
    server, folder = http
    original = Path.read_text
    entered, release = threading.Event(), threading.Event()

    def paused(path, *args, **kwargs):
        value = original(path, *args, **kwargs)
        if path == folder / "web.json" and not entered.is_set():
            entered.set()
            assert release.wait(5), "writer was not released"
        return value

    monkeypatch.setattr(Path, "read_text", paused)
    with ThreadPoolExecutor(max_workers=1) as executor:
        request = executor.submit(get_json, server)
        try:
            assert entered.wait(5)
            data = json.loads(original(folder / "web.json"))
            data["display_title"] = "并发更新后的书名"
            put(folder / "web.json", data)
            put(folder / "final/chapter-001.md", "新" * 20)
        finally:
            release.set()
        value = request.result(timeout=10)["books"][0]
    assert value["title"] == "并发更新后的书名"
    assert value["words"] == 20
    assert meta(server) == value


def test_concurrent_cold_http_requests_build_one_book_once(http, monkeypatch):
    server, folder = http
    counts = actual_reads(monkeypatch, server.library.root)
    barrier = threading.Barrier(4)

    def request():
        barrier.wait(timeout=5)
        return get_json(server)

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(lambda _: request(), range(4)))
    assert all(row == results[0] for row in results)
    assert counts["webui/story/final/chapter-001.md"] == 1
    assert counts["webui/story/chapter-001.body.md"] == 0


def test_warm_cache_still_rejects_symlink_replacement(http, tmp_path):
    server, folder = http
    assert meta(server)["chapters"] == 1
    outside = tmp_path / "outside"
    put(outside / "chapter-001.md", "外部文件不得读取")
    shutil.rmtree(folder / "final")
    (folder / "final").symlink_to(outside, target_is_directory=True)
    assert "error" in get_json(server, expected_status=400)


def test_continuously_changing_snapshot_is_not_published(http, monkeypatch):
    server, folder = http
    original = Path.read_text
    version = 0

    def changing(path, *args, **kwargs):
        nonlocal version
        value = original(path, *args, **kwargs)
        if path == folder / "web.json":
            version += 1
            data = json.loads(value)
            data["display_title"] = f"正在更新{version}"
            put(path, data)
        return value

    with monkeypatch.context() as patch:
        patch.setattr(Path, "read_text", changing)
        assert "error" in get_json(server, expected_status=400)
    assert meta(server)["title"] == f"正在更新{version}"


def test_real_http_five_books_150_chapters_under_300ms(tmp_path):
    result = benchmark(tmp_path / "fixture-data", cold_count=1, warm_count=5)
    assert result["fixture"]["chapters"] == 150
    assert result["cold"]["max_ms"] < 300, result["cold"]
    assert result["warm"]["max_ms"] < 300, result["warm"]
