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
