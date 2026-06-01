from __future__ import annotations

import pytest

from bestseller.services.quality_repair_playbooks import (
    get_quality_repair_playbook,
    render_quality_repair_playbooks,
)

pytestmark = pytest.mark.unit


def test_repair_playbook_renders_actionable_opening_repetition_guidance() -> None:
    playbook = get_quality_repair_playbook("CHAPTER_OPENING_REPETITION")

    assert playbook is not None
    rendered = playbook.render()
    assert "前300字" in rendered
    assert "最近12章" in rendered
    assert "验收" in rendered


def test_render_repair_playbooks_dedupes_and_skips_unknown_codes() -> None:
    rendered = render_quality_repair_playbooks(
        ["CHAPTER_TOO_SHORT", "UNKNOWN", "CHAPTER_TOO_SHORT", "ANTI_META_LEAK"]
    )

    assert rendered.count("[CHAPTER_TOO_SHORT]") == 1
    assert "[ANTI_META_LEAK]" in rendered
    assert "UNKNOWN" not in rendered


def test_render_repair_playbooks_can_append_book_methodology(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "bestseller.services.quality_repair_playbooks.render_book_methodology_block",
        lambda **kwargs: f"methodology stage={kwargs['stage']} scope={kwargs['scope']}",
    )

    rendered = render_quality_repair_playbooks(["ENDING_SENTENCE_WEAK"])

    assert "methodology stage=repair scope=scene" in rendered


@pytest.mark.parametrize(
    "code",
    [
        "WORD_COUNT_METADATA_MISMATCH",
        "PAYOFF_LEDGER_LOW",
        "PAYOFF_HOOK_ONLY",
        "PERSONA_ABANDON_RATE_HIGH",
        "PERSONA_WEIGHTED_SCORE_LOW",
        "PERSONA_PAYOFF_DENSITY_LOW",
        "SIGNATURE_IMAGE_MISSING",
        "OPENING_PRESSURE_THIN",
        "ENDING_HOOK_MISSING",
        "PARAGRAPH_DUPLICATE_PARAPHRASE",
        "CALLBACK_OBLIGATION_MISSING",
        "LENGTH_OUT_OF_BAND",
        "GOLDEN_THREE_WEAK",
        "NAMING_OUT_OF_POOL",
        "CLIFFHANGER_REPEAT",
    ],
)
def test_new_quality_codes_have_actionable_playbooks(code: str) -> None:
    playbook = get_quality_repair_playbook(code)

    assert playbook is not None, f"missing playbook for {code}"
    rendered = playbook.render()
    assert code in rendered
    assert "验收" in rendered


def test_persona_codes_are_in_auto_repair_allowlist() -> None:
    from bestseller.settings import load_settings
    from bestseller.services.retention_safety_gate import AUTO_REPAIR_RETENTION_CODES

    repairable_codes = set(load_settings().pipeline.chapter_auto_repair_repairable_codes)

    for code in (
        "PERSONA_ABANDON_RATE_HIGH",
        "PERSONA_WEIGHTED_SCORE_LOW",
        "PERSONA_PAYOFF_DENSITY_LOW",
    ):
        assert code in AUTO_REPAIR_RETENTION_CODES
    for code in (
        "SIGNATURE_IMAGE_MISSING",
        "ENDING_HOOK_MISSING",
        "GOLDEN_THREE_WEAK",
        "NAMING_OUT_OF_POOL",
        "CLIFFHANGER_REPEAT",
    ):
        assert code in repairable_codes


def test_duplicate_gate_emits_canonical_repair_codes() -> None:
    from bestseller.services.chapter_duplicate_gate import (
        CHAPTER_BODY_TEMPLATE_REPEAT,
        CHAPTER_OPENING_DUPLICATE,
    )

    assert CHAPTER_OPENING_DUPLICATE == "CHAPTER_OPENING_REPETITION"
    assert CHAPTER_BODY_TEMPLATE_REPEAT == "CROSS_CHAPTER_REPETITION"
    # Canonical codes must have playbooks so the rewrite is targeted.
    assert get_quality_repair_playbook(CHAPTER_OPENING_DUPLICATE) is not None
    assert get_quality_repair_playbook(CHAPTER_BODY_TEMPLATE_REPEAT) is not None
