from __future__ import annotations

from bestseller.services.reveal_schedule_builder import (
    build_reveal_schedule,
    write_reveal_schedule_for_book,
)


def test_build_reveal_schedule_extracts_quoted_reveal_terms_generically() -> None:
    # De-hardcoded: floors come from each book's own bible (quoted reveal terms
    # on lines marked 真相/揭/reveal), not from baked pilot-book tokens.
    payload = build_reveal_schedule(
        series_bible_text="核心真相是「主角的真实身世」，要到中段才揭开。",
        total_chapters=80,
    )

    all_tokens = [token for item in payload["reveals"] for token in item["tokens"]]
    assert "主角的真实身世" in all_tokens


def test_write_reveal_schedule_for_book(tmp_path) -> None:
    story_bible = tmp_path / "story-bible"
    story_bible.mkdir()
    (story_bible / "series-bible.md").write_text("「关键反转」只在中段揭示。", encoding="utf-8")

    path = write_reveal_schedule_for_book(tmp_path)

    assert path.exists()
    assert "关键反转" in path.read_text(encoding="utf-8")
