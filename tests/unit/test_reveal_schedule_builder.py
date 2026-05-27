from __future__ import annotations

from bestseller.services.reveal_schedule_builder import (
    build_reveal_schedule,
    write_reveal_schedule_for_book,
)


def test_build_reveal_schedule_extracts_known_reveal_floors() -> None:
    payload = build_reveal_schedule(
        series_bible_text="扣账人和三代为一户都不能在前三章揭开。",
        total_chapters=80,
    )

    ids = {item["id"] for item in payload["reveals"]}
    assert "kou_zhang_ren" in ids
    assert "san_dai_wei_yi_hu" in ids


def test_write_reveal_schedule_for_book(tmp_path) -> None:
    story_bible = tmp_path / "story-bible"
    story_bible.mkdir()
    (story_bible / "series-bible.md").write_text("镜影林渊只在中段揭示。", encoding="utf-8")

    path = write_reveal_schedule_for_book(tmp_path)

    assert path.exists()
    assert "jing_ying_lin_yuan_self_aware" in path.read_text(encoding="utf-8")
