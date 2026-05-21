"""Unit tests for source artifact audit."""

from __future__ import annotations

from pathlib import Path

import pytest

from bestseller.services.source_artifact_audit import (
    audit_source_artifacts,
    discover_source_artifacts,
    source_artifact_audit_report_to_dict,
)

pytestmark = pytest.mark.unit


def _book_dir(tmp_path: Path, slug: str = "book-a") -> Path:
    path = tmp_path / slug
    path.mkdir(parents=True)
    return path


def test_discover_source_artifacts_skips_chapter_files(tmp_path: Path) -> None:
    book = _book_dir(tmp_path)
    (book / "project.md").write_text("# Project", encoding="utf-8")
    (book / "chapter-001.md").write_text("chapter prose", encoding="utf-8")
    (book / "notes.txt").write_text("misc", encoding="utf-8")

    artifacts = discover_source_artifacts("book-a", output_dir=tmp_path)

    assert artifacts == (book / "project.md",)


def test_audit_source_artifacts_flags_forbidden_legacy_terms(tmp_path: Path) -> None:
    book = _book_dir(tmp_path)
    (book / "story-bible.md").write_text(
        "# Bible\n\n主角不是玩家，也没有副本机制。",
        encoding="utf-8",
    )

    report = audit_source_artifacts("book-a", output_dir=tmp_path)

    assert report.passed is False
    assert report.blocking_findings[0].code == "SOURCE_FORBIDDEN_TERM"
    assert report.blocking_findings[0].evidence["term_counts"]["玩家"] == 1
    assert report.blocking_findings[0].evidence["term_counts"]["副本"] == 1


def test_audit_source_artifacts_ignores_plain_copy_meaning(tmp_path: Path) -> None:
    book = _book_dir(tmp_path)
    (book / "project.md").write_text(
        "# Project\n\n"
        "父亲留下的证词被销毁了, 主角手上仍有一份副本。\n"
        "三叔把名单的副本交给了可信的人。\n",
        encoding="utf-8",
    )

    report = audit_source_artifacts("book-a", output_dir=tmp_path)

    assert report.passed is True
    assert report.findings == ()


def test_audit_source_artifacts_flags_language_mismatch(tmp_path: Path) -> None:
    book = _book_dir(tmp_path)
    (book / "project.md").write_text(
        "# 中文项目\n\n这是一个中文源资产。" * 80,
        encoding="utf-8",
    )

    report = audit_source_artifacts(
        "book-a",
        output_dir=tmp_path,
        expected_language="en-US",
    )

    assert report.passed is False
    assert report.blocking_findings[0].code == "SOURCE_LANGUAGE_MISMATCH"


def test_audit_source_artifacts_passes_clean_english_project(tmp_path: Path) -> None:
    book = _book_dir(tmp_path)
    (book / "project.md").write_text(
        "# The Witness Protocol\n\n"
        "> Genre: Science Fiction\n"
        "> Platform: tomato\n\n"
        + "Kade faces an immediate threat and a visible power cost. " * 120,
        encoding="utf-8",
    )

    report = audit_source_artifacts(
        "book-a",
        output_dir=tmp_path,
        expected_language="en-US",
        expected_platform="tomato",
        expected_category="Science Fiction",
    )

    assert report.passed is True
    assert report.findings == ()
    assert source_artifact_audit_report_to_dict(report)["artifact_count"] == 1


def test_audit_source_artifacts_missing_files_blocks_repair(tmp_path: Path) -> None:
    (tmp_path / "book-a").mkdir()

    report = audit_source_artifacts("book-a", output_dir=tmp_path)

    assert report.passed is False
    assert report.blocking_findings[0].code == "SOURCE_ARTIFACTS_MISSING"
