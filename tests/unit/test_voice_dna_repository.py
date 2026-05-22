from __future__ import annotations

from pathlib import Path

import pytest

from bestseller.services.voice_dna_repository import (
    load_voice_dna,
    load_voice_dna_file,
    resolve_voice_dna_path,
    save_voice_dna,
)
from bestseller.services.voice_signature import extract_voice_dna_from_text

pytestmark = pytest.mark.unit


_SAMPLE = (
    "夜色如墨，山风扑过，火光在崖边一闪而灭。\n"
    "他握紧剑柄，心中暗想：今夜若不退，便是死路一条。\n"
    "“你当真敢杀我？”那人冷冷一笑。\n"
    "他不答，只是出剑。剑光如电。\n"
) * 30


def _make_dna():
    return extract_voice_dna_from_text(
        _SAMPLE, source_id="repo-test", source_label="repo测试"
    )


def test_resolve_path_mode_a(tmp_path: Path) -> None:
    path = resolve_voice_dna_path("my-book", output_base_dir=tmp_path)

    assert path == tmp_path / "my-book" / "story-bible" / "voice-dna.json"


def test_resolve_path_mode_b(tmp_path: Path) -> None:
    path = resolve_voice_dna_path("my-book", output_base_dir=tmp_path, mode_b=True)

    assert (
        path
        == tmp_path / "ai-generated" / "my-book" / "story-bible" / "voice-dna.json"
    )


def test_save_and_load_round_trip(tmp_path: Path) -> None:
    dna = _make_dna()

    saved_path = save_voice_dna(dna, "demo", output_base_dir=tmp_path)
    assert saved_path.exists()

    loaded = load_voice_dna("demo", output_base_dir=tmp_path)

    assert loaded is not None
    assert loaded.source_id == dna.source_id
    assert loaded.sample_chars == dna.sample_chars
    assert loaded.sentence_length.p50 == dna.sentence_length.p50
    assert list(loaded.catchphrases) == list(dna.catchphrases)


def test_load_returns_none_when_missing(tmp_path: Path) -> None:
    assert load_voice_dna("never-created", output_base_dir=tmp_path) is None


def test_load_file_returns_none_for_bad_json(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")

    assert load_voice_dna_file(bad) is None


def test_load_file_returns_none_for_invalid_payload(tmp_path: Path) -> None:
    bad = tmp_path / "invalid.json"
    bad.write_text('{"source_id": "x"}', encoding="utf-8")  # missing required fields

    assert load_voice_dna_file(bad) is None


def test_save_is_atomic_using_tmp_rename(tmp_path: Path) -> None:
    dna = _make_dna()
    save_voice_dna(dna, "atomic", output_base_dir=tmp_path)

    # No stray temp file should remain
    dna_dir = tmp_path / "atomic" / "story-bible"
    leftover = [p for p in dna_dir.iterdir() if p.suffix == ".tmp"]
    assert leftover == []


def test_save_overwrites_existing_file(tmp_path: Path) -> None:
    dna1 = _make_dna()
    dna2 = extract_voice_dna_from_text(
        "短句。来了。走了。" * 200, source_id="other"
    )

    save_voice_dna(dna1, "ow", output_base_dir=tmp_path)
    save_voice_dna(dna2, "ow", output_base_dir=tmp_path)

    loaded = load_voice_dna("ow", output_base_dir=tmp_path)
    assert loaded is not None
    assert loaded.source_id == dna2.source_id
