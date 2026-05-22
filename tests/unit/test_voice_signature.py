from __future__ import annotations

import pytest

from bestseller.domain.voice_dna import VoiceDNA
from bestseller.services.voice_signature import (
    blend_voice_dna,
    compute_voice_dna_diff,
    extract_voice_dna_from_text,
    render_voice_dna_block,
)

pytestmark = pytest.mark.unit


_SAMPLE_TEXT_A = (
    "夜色如墨，山风扑过，火光在崖边一闪而灭。\n"
    "他握紧剑柄，心中暗想：今夜若不退，便是死路一条。\n"
    "“你当真敢杀我？”那人冷冷一笑，"
    "“便是死，我也要拖你下水。”\n"
    "他不答，只是出剑。\n"
    "剑光如电，劈开夜色，劈开风声，劈开沉沉的死寂。\n"
    "他想起十年前的那个雨夜，想起那把烧成灰的伞，"
    "想起那个没有说出口的名字。\n"
    "天地之间，唯有一柄剑，一具影，一颗未死的心。\n"
) * 30


def test_extract_voice_dna_produces_signature_for_chinese_text() -> None:
    dna = extract_voice_dna_from_text(
        _SAMPLE_TEXT_A, source_id="sample-a", source_label="样本A"
    )

    assert dna.source_id == "sample-a"
    assert dna.sample_chars > 0
    assert dna.sentence_length.p50 > 0
    assert 0 <= dna.pacing.dialogue_ratio <= 1
    assert 0 <= dna.pacing.action_ratio <= 1
    assert 0 <= dna.pacing.interior_ratio <= 1
    assert 0 <= dna.pacing.description_ratio <= 1
    assert dna.confidence > 0


def test_extract_voice_dna_handles_empty_text() -> None:
    dna = extract_voice_dna_from_text("", source_id="empty")

    assert dna.sample_chars == 0
    assert dna.confidence == 0.0
    assert dna.sentence_length.p50 == 0


def test_extract_voice_dna_is_deterministic() -> None:
    dna1 = extract_voice_dna_from_text(_SAMPLE_TEXT_A, source_id="x")
    dna2 = extract_voice_dna_from_text(_SAMPLE_TEXT_A, source_id="x")

    assert dna1.model_dump() == dna2.model_dump()


def test_extract_detects_dialogue_in_pacing_signature() -> None:
    dna = extract_voice_dna_from_text(_SAMPLE_TEXT_A, source_id="dialogue-test")

    assert dna.pacing.dialogue_ratio > 0


def test_extract_catchphrases_appear_when_repeated() -> None:
    repetitive = "山河无恙。山河无恙。山河无恙。\n他想起山河无恙四个字。\n" * 40
    dna = extract_voice_dna_from_text(repetitive, source_id="catch")

    assert any("山河" in p for p in dna.catchphrases)


def test_blend_voice_dna_weights_two_samples() -> None:
    dna_short = extract_voice_dna_from_text(
        "好。\n来了。\n走了。\n" * 200, source_id="short"
    )
    dna_long = extract_voice_dna_from_text(
        ("天地玄黄，宇宙洪荒，日月盈昃，辰宿列张，寒来暑往，秋收冬藏。" * 5 + "\n") * 60,
        source_id="long",
    )

    blended = blend_voice_dna([dna_short, dna_long], weights=[1.0, 1.0])

    assert blended.source_label.startswith("blend")
    assert dna_short.sentence_length.p50 <= blended.sentence_length.p50 <= dna_long.sentence_length.p50
    assert blended.confidence > 0


def test_blend_voice_dna_validates_weights() -> None:
    dna = extract_voice_dna_from_text(_SAMPLE_TEXT_A, source_id="x")
    with pytest.raises(ValueError):
        blend_voice_dna([dna], weights=[1.0, 2.0])
    with pytest.raises(ValueError):
        blend_voice_dna([dna], weights=[0.0])
    with pytest.raises(ValueError):
        blend_voice_dna([])


def test_compute_voice_dna_diff_zero_when_identical() -> None:
    dna = extract_voice_dna_from_text(_SAMPLE_TEXT_A, source_id="self")

    diff = compute_voice_dna_diff(dna, dna)

    assert diff.overall_drift < 0.05
    assert not diff.needs_correction


def test_compute_voice_dna_diff_detects_drift() -> None:
    target = extract_voice_dna_from_text(_SAMPLE_TEXT_A, source_id="target")
    observed = extract_voice_dna_from_text(
        "他说好。她说不好。他说走。她说留。\n" * 200, source_id="observed"
    )

    diff = compute_voice_dna_diff(target, observed)

    assert diff.overall_drift > 0.1
    assert diff.sentence_length_drift > 0 or diff.pacing_drift > 0


def test_render_voice_dna_block_zh_includes_targets() -> None:
    dna = extract_voice_dna_from_text(
        _SAMPLE_TEXT_A, source_id="render", source_label="测试声纹"
    )

    block = render_voice_dna_block(dna)

    assert "作者声纹" in block
    assert "测试声纹" in block
    assert "中位数" in block


def test_render_voice_dna_block_handles_none() -> None:
    assert render_voice_dna_block(None) == ""


def test_render_voice_dna_block_supports_english() -> None:
    dna = extract_voice_dna_from_text(_SAMPLE_TEXT_A, source_id="en-test")

    block = render_voice_dna_block(dna, language="en")

    assert "Voice DNA" in block


def test_render_voice_dna_block_accepts_mapping_payload() -> None:
    payload = {
        "source_label": "字典样本",
        "sentence_length": {"p50": 18, "short_ratio": 0.3, "long_ratio": 0.1},
        "pacing": {"dialogue_ratio": 0.4, "action_ratio": 0.2, "interior_ratio": 0.2, "description_ratio": 0.2},
        "catchphrases": ["心头一沉"],
        "taboo_phrases": ["XX 现象"],
    }

    block = render_voice_dna_block(payload)

    assert "字典样本" in block
    assert "心头一沉" in block
    assert "XX 现象" in block
