from __future__ import annotations

from bestseller.services.concept_methodology_agent import (
    ConceptMethodology,
    sanitize_concept_methodology,
)


def _polluted_methodology() -> ConceptMethodology:
    return ConceptMethodology(
        audience_orientation="male",
        mindset="用寿命和人情账本把代价变成核心资源",
        mechanism_types=[
            "人情因果链：欠与还的人际账本折算成战力",
            "按次献祭寿命换取力量",
            "公开比试后的身份反差",
        ],
        reader_promise_axis="所有欠下的最终都要用阳寿偿还",
        shuangdian_cadence=[
            "每次反杀都扣除十年寿命",
            "主角用现场选择完成一次公开翻盘",
        ],
        design_axes=["人情账本轴", "行动选择逐级放大"],
        anti_patterns=["回报无代价"],
        market_signals=["公开翻盘"],
        rationale="人情账本与寿命献祭有差异化",
        source="llm",
    )


def test_methodology_keeps_this_books_own_wording() -> None:
    """2026-08-02: the motif filter was removed from the sanitiser.

    It deleted any field mentioning debt/death/lifespan and substituted
    framework filler, so every book that tripped it received identical
    replacement text — censorship and homogenisation in one pass. A
    methodology belongs to its own project.
    """
    sanitized = sanitize_concept_methodology(
        _polluted_methodology(),
        genre="东方玄幻",
        tone_preference="light",
        cost_style="minimal",
    )

    text = sanitized.model_dump_json()
    assert "账本" in text
    assert "公开比试后的身份反差" in text
    assert "主角用现场选择完成一次公开翻盘" in text


def test_explicit_debt_theme_is_not_silently_deleted() -> None:
    sanitized = sanitize_concept_methodology(
        _polluted_methodology(),
        genre="都市",
        tone_preference="",
        cost_style="standard",
    )

    assert "人情账本" in sanitized.model_dump_json()


def test_empty_fields_are_still_filled() -> None:
    """The sanitiser's remaining job: never hand a blank contract downstream."""
    bare = ConceptMethodology(audience_orientation="male", source="llm")

    filled = sanitize_concept_methodology(bare, genre="东方玄幻")

    assert filled.mindset
    assert filled.mechanism_types
    assert filled.reader_promise_axis
