"""Four-digit chapter numbers survive diagnostics, reading and exports offline."""

import io
import json
from pathlib import Path
import sys
import zipfile

import pytest
import yaml

from bookengine.repair_ledger import repair_ledger
from bookengine.rhythm_diagnostics import _plan_content
from bookengine.solution_sequences import writer_solution_sequence_context
from exporter import read_book

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools/webui"))
from library import Library
from server import export_bytes

NUMBERS = [999, 1000, 1001, 2000]


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


@pytest.fixture
def long_book(tmp_path):
    book = tmp_path / "webui/long"
    save(book / "web.json", dict(title="四位章号回归", chapters=2000, status="complete"))
    save(book / "run.json", dict(status="complete", book=dict(chapters=2000)))
    volumes = [dict(number=1, start=1, end=999), dict(number=2, start=1000, end=2000)]
    (book / "story_bible.yaml").write_text(yaml.safe_dump(dict(volumes=volumes)), encoding="utf-8")
    for n in reversed(NUMBERS):
        body = f"本章识别句{n}。\n\n" + (
            "沈青梧推开门，檐下的雨已经停了。他沿着石阶走到院中，将手里的信递给等候的老人。\n\n" * 70
        )
        save(
            book / f"chapters/{n:03d}.json",
            dict(
                number=n,
                title=f"到达{n}",
                status="complete",
                cleaning=dict(text_hygiene=dict(status="repaired")),
                solution_sequence=dict(signature=["直接观察", "比较证据", "联合他人", "揭开事实"]),
            ),
        )
        save(book / f"writing_outlines/{n:03d}.json", dict(number=n, type="日常", title=f"到达{n}"))
        (book / f"chapter-{n:03d}.body.md").write_text(body, encoding="utf-8")
        (book / f"chapter-{n:03d}.r2.draft.md").write_text(body, encoding="utf-8")
        save(book / f"final/chapter-{n:03d}.deai.json", dict(complete=True, status="unchanged"))
        (book / f"final/chapter-{n:03d}.md").write_text(body, encoding="utf-8")
    return book


def test_repair_ledger_counts_all_four_digit_records_and_bodies(long_book):
    result = repair_ledger(long_book)
    assert result["chapters_compared"] == 4
    assert result["outcomes"]["text_hygiene"] == {"repaired": 4}


def test_rhythm_reads_four_digit_outlines_in_numeric_order(long_book, monkeypatch):
    seen = []
    monkeypatch.setattr("bookengine.rhythm_diagnostics.rhythm_issues", lambda b, h, rows: seen.extend(rows) or [])
    assert _plan_content(long_book)["checked"] == 4
    assert [row["number"] for row in seen] == NUMBERS


def test_solution_history_includes_1000_before_1001_and_all_before_2001(long_book):
    bible = {"volumes": [dict(number=1, start=990, end=2010)]}
    rows = writer_solution_sequence_context(long_book, 1001, bible)[0][1]
    assert [row["chapter"] for row in rows] == [999, 1000]
    rows = writer_solution_sequence_context(long_book, 2001, bible)[0][1]
    assert [row["chapter"] for row in rows] == NUMBERS


def test_web_chapters_and_outlines_use_numeric_order_and_real_volume_bounds(long_book):
    detail = Library(long_book.parents[1], read_only=True).detail("webui/long")
    assert [row["number"] for row in detail["chapters"]] == NUMBERS
    assert [row["number"] for row in detail["outline_view"]] == NUMBERS
    assert [row["volume"] for row in detail["chapters"]] == [1, 2, 2, 2]
    assert detail["meta"]["chapters"] == 4


def test_export_keeps_body_order_and_zip_member_order(long_book):
    _, rows = read_book(long_book)
    assert [row[0] for row in rows] == NUMBERS
    detail = Library(long_book.parents[1], read_only=True).detail("webui/long")
    with zipfile.ZipFile(io.BytesIO(export_bytes(detail, "final", "fanqie"))) as archive:
        assert [int(Path(name).name.split("-", 1)[0]) for name in archive.namelist()] == [*NUMBERS, 2001]
        for n, name in zip(NUMBERS, archive.namelist()):
            assert f"本章识别句{n}。" in archive.read(name).decode("utf-8")


def test_shared_chapter_files_ignore_non_chapters_and_sort_numbers(tmp_path):
    from tools.chapter_files import chapter_files

    for name in ("999.json", "1000.json", "1001.json", "2000.json", "notes.json", "000.json", "1000.tmp.json"):
        (tmp_path / name).write_text("{}", encoding="utf-8")
    (tmp_path / "2001.json").mkdir()
    assert [int(p.stem) for p in chapter_files(tmp_path, "*.json")] == NUMBERS
    assert chapter_files(tmp_path / "absent", "chapter-*.body.md") == []
