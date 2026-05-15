"""Unit tests for ``quality_levers.detectors``."""

from __future__ import annotations

import pytest

from bestseller.services.quality_levers.detectors import (
    audit_chapter,
    compute_pulse_density,
    compute_sensory_coverage,
    count_cjk_chars,
    detect_psychological_dumping,
    evaluate_word_count,
    measure_signature_density,
    scan_abstract_sensory_terms,
    scan_banned_patterns,
    scan_forbidden_voice_words,
)

pytestmark = pytest.mark.unit


def test_count_cjk_chars_basic() -> None:
    assert count_cjk_chars("Hello 世界") == 2
    assert count_cjk_chars("") == 0
    # 8 CJK chars; Chinese full-stop U+3002 is outside [一-鿿]
    assert count_cjk_chars("沈青崖的指节绷紧。") == 8


def test_compute_pulse_density_passes_with_dense_pulse_words() -> None:
    text = (
        "他喉结动了一下。指节绷紧。"
        "心一沉。"
        "牙关咬紧。"
        "立刻摸进右袖。"
    )
    result = compute_pulse_density(text)
    assert result.pulse_count >= 3
    assert result.density_per_300_chars > 1.0
    assert result.passed is True


def test_compute_pulse_density_fails_on_cold_text() -> None:
    text = "这是" * 100  # 200 字符无任何心率词
    result = compute_pulse_density(text)
    assert result.pulse_count == 0
    assert result.passed is False


def test_scan_banned_patterns_detects_smooth_transition() -> None:
    text = "他握紧了拳头。可那不是最要命的。最要命的是有人在他身后。"
    result = scan_banned_patterns(text)
    assert result.total_hits >= 1
    pattern_ids = {hit.pattern_id for hit in result.hits}
    assert "smooth_transition" in pattern_ids
    assert result.passed is False


def test_scan_banned_patterns_detects_parallel_action() -> None:
    text = "他一边走路一边思考着昨晚的对话。"
    result = scan_banned_patterns(text)
    assert "parallel_action" in {hit.pattern_id for hit in result.hits}


def test_scan_banned_patterns_passes_clean_text() -> None:
    text = "他没有立刻抬头。门外周神算笑得很慢。"
    result = scan_banned_patterns(text)
    assert result.total_hits == 0
    assert result.passed is True


def test_scan_abstract_sensory_terms_detects_narration_only() -> None:
    text = "屋内一片阴森。墙角的影子动了。"
    result = scan_abstract_sensory_terms(text)
    assert result.total_hits >= 1
    hit_words = {word for word, _ in result.hits}
    assert "阴森" in hit_words


def test_scan_abstract_sensory_terms_ignores_dialogue_quotes() -> None:
    # Chinese dialogue uses 中文引号 ""
    text = '管家小声说："这里好阴森。" 沈青崖没有回应。'
    result = scan_abstract_sensory_terms(text)
    # "阴森" is inside dialogue → should NOT be flagged
    assert result.total_hits == 0
    assert result.passed is True


def test_detect_psychological_dumping_flags_long_background_paragraph() -> None:
    long_paragraph = (
        "他想起十五年前那场大火。师父曾经告诫过他，旧案的真相是不可触碰的。"
        "回忆涌上来，原来那一夜，井底的人并没有死。"
        "其实他从一开始就知道。这些年里，他不止一次回忆起当年的细节。"
    ) * 2  # make it long enough
    text = "前段动作。\n\n" + long_paragraph
    result = detect_psychological_dumping(text)
    assert result.total_hits >= 1
    assert result.passed is False


def test_detect_psychological_dumping_short_paragraphs_pass() -> None:
    text = "他抬头。\n\n门外周神算说话。\n\n两个时辰。"
    result = detect_psychological_dumping(text)
    assert result.total_hits == 0
    assert result.passed is True


def test_measure_signature_density() -> None:
    text = "按程序，沈青崖摊开掌心。现场的章只盖在一张纸上。"
    result = measure_signature_density(
        text,
        signature_words=("按程序", "现场", "章"),
        threshold=2,
    )
    assert result.total_hits >= 3
    assert result.passed is True


def test_measure_signature_density_fails_below_threshold() -> None:
    text = "他打开门，走出去。"
    result = measure_signature_density(
        text,
        signature_words=("按程序", "现场"),
        threshold=2,
    )
    assert result.total_hits == 0
    assert result.passed is False


def test_scan_forbidden_voice_words_flags_voice_dna_banned_terms() -> None:
    result = scan_forbidden_voice_words(
        "我觉得这件事肯定没问题。",
        forbidden_words=("我觉得", "肯定"),
    )

    assert result.total_hits == 2
    assert result.passed is False
    assert ("我觉得", 1) in result.hits


def test_evaluate_word_count_qimao_in_range() -> None:
    text = "啊" * 3000
    result = evaluate_word_count(text, platform="qimao")
    assert result.min_chars == 2500
    assert result.max_chars == 4000
    assert result.passed is True


def test_evaluate_word_count_qimao_underflow() -> None:
    text = "啊" * 1000
    result = evaluate_word_count(text, platform="qimao")
    assert result.passed is False
    assert "underflow" in result.reason


def test_evaluate_word_count_qimao_overflow() -> None:
    text = "啊" * 5000
    result = evaluate_word_count(text, platform="qimao")
    assert result.passed is False
    assert "overflow" in result.reason


def test_evaluate_word_count_falls_back_to_default_5000() -> None:
    text = "啊" * 3000
    result = evaluate_word_count(text, platform=None)
    assert result.min_chars == 5000
    assert result.passed is False  # 3000 < 5000 default


def test_compute_sensory_coverage_investigation_scene() -> None:
    text = (
        "他蹲下，指尖拂过湿润的灰浆——还潮。"
        "鼻间有焦纸味，夹一缕人皮油脂。"
        "门外脚步声停了三息。"
        "墙角的光线晃了一下。"
    )
    result = compute_sensory_coverage(text, scene_type="investigation_scene")
    assert result is not None
    # olfactory + visual + tactile + auditory all present
    assert "olfactory" in result.hit_axes
    assert "tactile" in result.hit_axes
    assert result.missing_must_include == ()
    assert result.passed is True


def test_compute_sensory_coverage_missing_must_include() -> None:
    text = "他看了一眼。光线很弱。"  # only visual
    result = compute_sensory_coverage(text, scene_type="investigation_scene")
    assert result is not None
    assert "olfactory" in result.missing_must_include
    assert result.passed is False


def test_compute_sensory_coverage_unknown_scene_returns_none() -> None:
    assert (
        compute_sensory_coverage("text", scene_type="nonexistent_scene_type")
        is None
    )


def test_audit_chapter_bundles_results() -> None:
    text = (
        "两个时辰。"
        "门外周神算说话。"
        "沈青崖喉结动了一下。"
        "他没有立刻抬头。"
    )
    audit = audit_chapter(text, platform="qimao")
    assert audit.word_count is not None
    assert audit.pulse.pulse_count >= 1
    # No banned patterns in this clean snippet
    assert audit.banned_patterns.total_hits == 0
    assert audit.abstract_sensory.total_hits == 0


def test_audit_chapter_can_include_character_voice_metrics() -> None:
    text = "先把账算清。林澈摸了摸裂纹账珠。我觉得这笔账不对。"
    audit = audit_chapter(
        text,
        platform="qimao",
        signature_words=("先把账算清", "裂纹账珠"),
        signature_threshold=2,
        forbidden_words=("我觉得",),
    )

    assert audit.signature_density is not None
    assert audit.signature_density.passed is True
    assert audit.signature_density.total_hits == 2
    assert audit.forbidden_voice is not None
    assert audit.forbidden_voice.passed is False
    assert audit.forbidden_voice.hits == (("我觉得", 1),)
