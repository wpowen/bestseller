from __future__ import annotations

import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

import bestseller.services.distillation_book_parser as book_parser
from bestseller.services.distillation_book_parser import (
    BookParseError,
    normalize_title_key,
    parse_source_book,
)
from bestseller.services.distillation_source_preparer import (
    DuplicateSourceTitleError,
    prepare_source,
)


def _chapter_body(label: str) -> str:
    return f"{label}推进主角判断局势, 形成行动目标, 并留下下一步悬念。" * 8


def test_parse_gbk_txt_splits_common_chinese_chapter_headings(tmp_path: Path) -> None:
    source = tmp_path / "同名书 精校版.txt"
    text = (
        "第一章 初入局面\n"
        f"{_chapter_body('第一段')}\n\n"
        "第二章 规则反转\n"
        f"{_chapter_body('第二段')}\n"
    )
    source.write_bytes(text.encode("gbk"))

    parsed = parse_source_book(source)

    assert parsed.source_format == "txt"
    assert parsed.encoding in {"gb18030", "gbk"}
    assert len(parsed.chapters) == 2
    assert parsed.chapters[0].boundary_type == "chinese_chapter_heading"
    assert parsed.metadata.title == "同名书 精校版"


def test_parse_epub_reads_metadata_and_spine_order(tmp_path: Path) -> None:
    epub = tmp_path / "ignored-filename.epub"
    with ZipFile(epub, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr(
            "META-INF/container.xml",
            """<?xml version="1.0"?>
            <container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
              <rootfiles>
                <rootfile full-path="OPS/content.opf" media-type="application/oebps-package+xml"/>
              </rootfiles>
            </container>
            """,
        )
        archive.writestr(
            "OPS/content.opf",
            """<?xml version="1.0" encoding="utf-8"?>
            <package xmlns="http://www.idpf.org/2007/opf" unique-identifier="bookid" version="2.0">
              <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
                <dc:title>测试书名</dc:title>
                <dc:creator>测试作者</dc:creator>
                <dc:language>zh-CN</dc:language>
              </metadata>
              <manifest>
                <item id="c1" href="chapter1.xhtml" media-type="application/xhtml+xml"/>
                <item id="c2" href="chapter2.xhtml" media-type="application/xhtml+xml"/>
              </manifest>
              <spine>
                <itemref idref="c1"/>
                <itemref idref="c2"/>
              </spine>
            </package>
            """,
        )
        archive.writestr(
            "OPS/chapter1.xhtml",
            f"<html><body><h1>第一章 开局</h1><p>{_chapter_body('开局')}</p></body></html>",
        )
        archive.writestr(
            "OPS/chapter2.xhtml",
            f"<html><body><h1>第二章 升级</h1><p>{_chapter_body('升级')}</p></body></html>",
        )

    parsed = parse_source_book(epub)

    assert parsed.source_format == "epub"
    assert parsed.metadata.title == "测试书名"
    assert parsed.metadata.author == "测试作者"
    assert parsed.metadata.language == "zh-CN"
    assert [chapter.title for chapter in parsed.chapters] == ["开局", "升级"]


def test_prepare_source_registers_private_title_key_and_skips_duplicate_title(
    tmp_path: Path,
) -> None:
    repo_root = tmp_path / "repo"
    private_root = tmp_path / "private"
    source_one = tmp_path / "同名书 精校版.txt"
    source_two = tmp_path / "同名书 完本.md"
    body = "第一章 开始\n" + _chapter_body("内容") + "\n"
    source_one.write_bytes(body.encode("utf-8"))
    source_two.write_text(body.replace("开始", "另一个开头"), encoding="utf-8")

    first = prepare_source(source_one, "source-0001", repo_root, private_root)
    second = prepare_source(source_two, "source-0002", repo_root, private_root)

    assert not first.skipped
    assert second.skipped
    assert second.duplicate_of == "source-0001"
    assert not (repo_root / "data" / "distillation" / "source-0002").exists()

    registry = json.loads(
        (repo_root / "data" / "distillation" / "source_registry.index.json").read_text(
            encoding="utf-8"
        )
    )
    assert registry["entries"][0]["canonical_source_id"] == "source-0001"
    assert "同名书" not in json.dumps(registry, ensure_ascii=False)

    manifest = json.loads(
        (repo_root / "data" / "distillation" / "source-0001" / "source_manifest.json").read_text(
            encoding="utf-8"
        )
    )
    assert manifest["source_format"] == "txt"
    assert manifest["redaction_policy"]["store_source_title_in_repo"] is False
    assert "title_key_hmac_sha256" in manifest


def test_prepare_source_duplicate_title_can_error(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    private_root = tmp_path / "private"
    source_one = tmp_path / "同名书.txt"
    source_two = tmp_path / "同名书.md"
    body = "第一章 开始\n" + _chapter_body("内容") + "\n"
    source_one.write_text(body, encoding="utf-8")
    source_two.write_text(body, encoding="utf-8")

    prepare_source(source_one, "source-0001", repo_root, private_root)

    with pytest.raises(DuplicateSourceTitleError):
        prepare_source(
            source_two,
            "source-0002",
            repo_root,
            private_root,
            dedupe_policy="error",
        )


def test_parse_mobi_without_calibre_falls_back_or_reports_actionable_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(book_parser, "resolve_calibre_executable", lambda *_args: None)
    source = tmp_path / "sample.mobi"
    source.write_bytes(b"not a real mobi file, but long enough " * 20)

    with pytest.raises(BookParseError) as excinfo:
        parse_source_book(source)
    message = str(excinfo.value).lower()
    assert "mobi" in message or "unpack" in message or "kindle" in message or "calibre" in message


def test_dedupe_corpus_prefers_txt_over_epub(tmp_path: Path) -> None:
    from bestseller.services.distillation_corpus import dedupe_corpus_paths_by_title

    epub = tmp_path / "演示书.epub"
    txt = tmp_path / "演示书.txt"
    epub.write_bytes(b"x" * 10)
    txt.write_bytes(b"y" * 10)
    canonical, siblings = dedupe_corpus_paths_by_title([epub, txt])
    assert len(canonical) == 1
    assert canonical[0] == txt
    assert len(siblings) == 1


def test_normalize_title_key_removes_common_edition_noise() -> None:
    assert normalize_title_key(" 示例书名\uff08精校版\uff09.epub ") == "示例书名"
