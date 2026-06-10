from __future__ import annotations

import json
from pathlib import Path

from bestseller.services.methodology_patch_proposals import (
    ATTRIBUTION_REPORT_RELPATH,
    aggregate_methodology_proposals,
    render_proposals_markdown,
    write_proposal_batch,
)


def _write_report(book_root: Path, rows: list[dict]) -> None:
    path = book_root / ATTRIBUTION_REPORT_RELPATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8"
    )


def _record(issue: str, layer: str, missing: str, directive: str = "修复指令") -> dict:
    return {
        "issue_id": issue,
        "root_layer": layer,
        "artifact_path": "design/outline.json",
        "missing": missing,
        "repair_directive": directive,
    }


def test_aggregate_groups_recurring_patterns_across_books(tmp_path: Path) -> None:
    book_a = tmp_path / "book-a"
    book_b = tmp_path / "book-b"
    # Same failure mode with book-specific tokens — must land in ONE bucket.
    _write_report(
        book_a,
        [
            _record("i1", "outline", "第3章缺少「林昼」的动机链"),
            _record("i2", "outline", "第17章缺少「沈砚」的动机链"),
            _record("i3", "prose", "场景物料抽象"),
        ],
    )
    _write_report(
        book_b,
        [
            _record("i4", "outline", "第8章缺少「周燃」的动机链"),
            _record("i5", "prose", "场景物料抽象"),
        ],
    )
    batch = aggregate_methodology_proposals([book_a, book_b], min_occurrences=3)
    assert batch.books_scanned == 2
    assert batch.records_scanned == 5
    assert len(batch.proposals) == 1  # outline×3 qualifies; prose×2 below threshold
    proposal = batch.proposals[0]
    assert proposal.root_layer == "outline"
    assert proposal.occurrences == 3
    assert set(proposal.books) == {"book-a", "book-b"}
    assert "动机链" in proposal.pattern
    # Book-specific tokens must be normalized away
    assert "林昼" not in proposal.pattern


def test_books_without_reports_are_skipped_not_fatal(tmp_path: Path) -> None:
    empty = tmp_path / "no-report-book"
    empty.mkdir()
    batch = aggregate_methodology_proposals([empty])
    assert batch.books_scanned == 0
    assert batch.skipped_books == ("no-report-book",)
    assert batch.proposals == ()


def test_write_proposal_batch_renders_human_review_artifacts(tmp_path: Path) -> None:
    book = tmp_path / "book"
    _write_report(book, [_record(f"i{i}", "prose", "场景物料抽象") for i in range(4)])
    batch = aggregate_methodology_proposals([book], min_occurrences=3)
    md_path, json_path = write_proposal_batch(batch, tmp_path / "out")
    markdown = md_path.read_text(encoding="utf-8")
    assert "方法论补丁提案" in markdown
    assert "pending_review" in json_path.read_text(encoding="utf-8")
    assert "处置" in markdown  # human disposition checklist
    # Proposals never auto-apply: rendering is the end of the pipeline.
    rendered = render_proposals_markdown(batch)
    assert "采纳并落 config" in rendered
