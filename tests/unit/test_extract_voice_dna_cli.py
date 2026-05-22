from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from bestseller.cli.voice_dna import voice_dna_app
from bestseller.services.voice_dna_repository import load_voice_dna

pytestmark = pytest.mark.unit


runner = CliRunner()


_SAMPLE_TEXT = (
    "他握紧剑柄，心中暗想：今夜若不退，便是死路一条。\n"
    "“你当真敢杀我？”那人冷冷一笑。\n"
    "他不答，只是出剑。剑光如电。\n"
) * 60


def _write_sample(tmp_path: Path, name: str = "sample.txt", content: str | None = None) -> Path:
    path = tmp_path / name
    path.write_text(content or _SAMPLE_TEXT, encoding="utf-8")
    return path


def test_extract_single_source_persists_dna(tmp_path: Path) -> None:
    src = _write_sample(tmp_path)

    result = runner.invoke(
        voice_dna_app,
        [
            "extract",
            "--slug",
            "test-book",
            "--source",
            str(src),
            "--label",
            "样本A",
            "--output-base-dir",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0, result.output

    dna = load_voice_dna("test-book", output_base_dir=tmp_path)
    assert dna is not None
    assert dna.sample_chars > 0


def test_extract_two_sources_blends(tmp_path: Path) -> None:
    a = _write_sample(tmp_path, "a.txt")
    b = _write_sample(tmp_path, "b.txt", content="短句。来了。走了。" * 200)

    result = runner.invoke(
        voice_dna_app,
        [
            "extract",
            "--slug",
            "blended",
            "--source",
            str(a),
            "--source",
            str(b),
            "--weight",
            "1.0",
            "--weight",
            "1.0",
            "--label",
            "A",
            "--label",
            "B",
            "--output-base-dir",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "blended 2 samples" in result.output

    dna = load_voice_dna("blended", output_base_dir=tmp_path)
    assert dna is not None
    assert dna.source_id.startswith("blend-")


def test_extract_refuses_overwrite_without_flag(tmp_path: Path) -> None:
    src = _write_sample(tmp_path)

    first = runner.invoke(
        voice_dna_app,
        [
            "extract",
            "--slug",
            "ow",
            "--source",
            str(src),
            "--output-base-dir",
            str(tmp_path),
        ],
    )
    assert first.exit_code == 0, first.output

    second = runner.invoke(
        voice_dna_app,
        [
            "extract",
            "--slug",
            "ow",
            "--source",
            str(src),
            "--output-base-dir",
            str(tmp_path),
        ],
    )
    assert second.exit_code == 1
    assert "already exists" in second.output


def test_extract_overwrite_flag_replaces(tmp_path: Path) -> None:
    src = _write_sample(tmp_path)

    runner.invoke(
        voice_dna_app,
        [
            "extract",
            "--slug",
            "ow2",
            "--source",
            str(src),
            "--output-base-dir",
            str(tmp_path),
        ],
    )
    second = runner.invoke(
        voice_dna_app,
        [
            "extract",
            "--slug",
            "ow2",
            "--source",
            str(src),
            "--overwrite",
            "--output-base-dir",
            str(tmp_path),
        ],
    )
    assert second.exit_code == 0, second.output


def test_extract_rejects_missing_source(tmp_path: Path) -> None:
    result = runner.invoke(
        voice_dna_app,
        [
            "extract",
            "--slug",
            "nope",
            "--source",
            str(tmp_path / "does-not-exist.txt"),
            "--output-base-dir",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 2
    assert "source not found" in result.output


def test_extract_rejects_mismatched_weight_count(tmp_path: Path) -> None:
    src = _write_sample(tmp_path)

    result = runner.invoke(
        voice_dna_app,
        [
            "extract",
            "--slug",
            "wc",
            "--source",
            str(src),
            "--weight",
            "1.0",
            "--weight",
            "2.0",
            "--output-base-dir",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 2
    assert "weight count" in result.output


def test_inspect_emits_voice_block(tmp_path: Path) -> None:
    src = _write_sample(tmp_path)

    runner.invoke(
        voice_dna_app,
        [
            "extract",
            "--slug",
            "insp",
            "--source",
            str(src),
            "--label",
            "巡查样本",
            "--output-base-dir",
            str(tmp_path),
        ],
    )

    result = runner.invoke(
        voice_dna_app,
        [
            "inspect",
            "--slug",
            "insp",
            "--output-base-dir",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "作者声纹" in result.output
    assert "巡查样本" in result.output


def test_inspect_returns_error_when_missing(tmp_path: Path) -> None:
    result = runner.invoke(
        voice_dna_app,
        [
            "inspect",
            "--slug",
            "never-extracted",
            "--output-base-dir",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 1
    assert "no voice DNA" in result.output
