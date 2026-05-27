from __future__ import annotations

from pathlib import Path

import pytest

from bestseller.services.forbidden_leaks_loader import load_forbidden_leaks_for_chapter

pytestmark = pytest.mark.unit


def test_qingnang_policy_allows_family_titles_but_blocks_core_terms() -> None:
    decision = load_forbidden_leaks_for_chapter(
        Path("output/exorcist-detective-1778051012"),
        chapter_number=1,
    )
    assert "困魂镜" in decision.forbidden_terms
    assert "林正淳" in decision.forbidden_terms
    assert "爷爷" not in decision.forbidden_terms
    assert "祖父" not in decision.forbidden_terms


def test_contextual_exception_removes_allowed_term() -> None:
    decision = load_forbidden_leaks_for_chapter(
        Path("output/exorcist-detective-1778051012"),
        chapter_number=1,
        context_tag="章末钩子最后一句",
    )
    assert "困魂镜" not in decision.forbidden_terms
    assert "困魂镜" in decision.excepted_terms
