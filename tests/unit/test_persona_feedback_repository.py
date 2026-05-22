from __future__ import annotations

from pathlib import Path

import pytest

from bestseller.services.persona_feedback_repository import (
    list_feedback_positions,
    load_chapter_feedback,
    load_chapter_feedback_file,
    load_latest_feedback,
    resolve_persona_feedback_dir,
    resolve_persona_feedback_path,
    save_chapter_feedback,
)
from bestseller.services.reader_persona_simulator import (
    ChapterSignalPack,
    simulate_readers,
)

pytestmark = pytest.mark.unit


def _signal(position: int = 1) -> ChapterSignalPack:
    return ChapterSignalPack(
        chapter_position=position,
        chapter_text_chars=2500,
        hook_count=4,
        payoff_count=2,
        cliffhanger_strength=0.7,
        voice_dna_drift=0.1,
        market_hooks_hit=3,
        market_hooks_required=3,
        novelty_score=0.6,
        consistency_score=0.85,
        emotional_beat_count=2,
        saturated_trope_hits=0,
        target_length_min=2200,
        target_length_max=3000,
        dialogue_ratio=0.3,
        action_ratio=0.25,
        interior_ratio=0.2,
        prose_quality_score=0.7,
    )


def test_resolve_dir_mode_a_and_b(tmp_path: Path) -> None:
    dir_a = resolve_persona_feedback_dir("book", output_base_dir=tmp_path)
    dir_b = resolve_persona_feedback_dir(
        "book", output_base_dir=tmp_path, mode_b=True
    )

    assert dir_a == tmp_path / "book" / "knowledge" / "persona-feedback"
    assert (
        dir_b
        == tmp_path / "ai-generated" / "book" / "knowledge" / "persona-feedback"
    )


def test_resolve_path_filename_format(tmp_path: Path) -> None:
    path = resolve_persona_feedback_path("book", 7, output_base_dir=tmp_path)

    assert path.name == "after-ch-007.json"


def test_resolve_path_rejects_invalid_position(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        resolve_persona_feedback_path("book", 0, output_base_dir=tmp_path)


def test_save_and_load_round_trip(tmp_path: Path) -> None:
    result = simulate_readers(_signal(position=3))

    save_chapter_feedback(result, "demo", output_base_dir=tmp_path)
    loaded = load_chapter_feedback("demo", 3, output_base_dir=tmp_path)

    assert loaded is not None
    assert loaded.chapter_position == 3
    assert loaded.weighted_score == result.weighted_score
    assert loaded.abandon_rate == result.abandon_rate
    assert len(loaded.per_persona) == len(result.per_persona)


def test_load_returns_none_when_missing(tmp_path: Path) -> None:
    assert load_chapter_feedback("demo", 1, output_base_dir=tmp_path) is None


def test_load_latest_returns_highest_position(tmp_path: Path) -> None:
    for position in (1, 3, 2):
        save_chapter_feedback(
            simulate_readers(_signal(position=position)),
            "many",
            output_base_dir=tmp_path,
        )

    latest = load_latest_feedback("many", output_base_dir=tmp_path)
    assert latest is not None
    assert latest.chapter_position == 3


def test_load_latest_before_chapter(tmp_path: Path) -> None:
    for position in (1, 2, 5, 7):
        save_chapter_feedback(
            simulate_readers(_signal(position=position)),
            "many",
            output_base_dir=tmp_path,
        )

    latest = load_latest_feedback(
        "many", before_chapter=5, output_base_dir=tmp_path
    )
    assert latest is not None
    assert latest.chapter_position == 2


def test_load_latest_returns_none_for_empty_dir(tmp_path: Path) -> None:
    assert load_latest_feedback("nothing", output_base_dir=tmp_path) is None


def test_list_feedback_positions(tmp_path: Path) -> None:
    for position in (2, 1, 5):
        save_chapter_feedback(
            simulate_readers(_signal(position=position)),
            "list",
            output_base_dir=tmp_path,
        )

    assert list_feedback_positions("list", output_base_dir=tmp_path) == [1, 2, 5]


def test_load_file_handles_garbage(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("not json at all", encoding="utf-8")
    assert load_chapter_feedback_file(bad) is None


def test_load_file_handles_invalid_payload(tmp_path: Path) -> None:
    bad = tmp_path / "shape.json"
    bad.write_text('{"chapter_position": -1}', encoding="utf-8")
    assert load_chapter_feedback_file(bad) is None


def test_save_creates_parent_dirs(tmp_path: Path) -> None:
    deeply = tmp_path / "deep" / "deeper"
    save_chapter_feedback(
        simulate_readers(_signal(position=4)),
        "mk",
        output_base_dir=deeply,
    )
    assert (
        deeply / "mk" / "knowledge" / "persona-feedback" / "after-ch-004.json"
    ).exists()


def test_save_atomic_no_temp_leftover(tmp_path: Path) -> None:
    save_chapter_feedback(
        simulate_readers(_signal(position=1)),
        "atomic",
        output_base_dir=tmp_path,
    )
    feedback_dir = (
        tmp_path / "atomic" / "knowledge" / "persona-feedback"
    )
    leftover = [p for p in feedback_dir.iterdir() if p.suffix == ".tmp"]
    assert leftover == []
