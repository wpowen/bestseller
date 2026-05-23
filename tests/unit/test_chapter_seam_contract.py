from __future__ import annotations

from pydantic import ValidationError
import pytest

from bestseller.domain.chapter_seam_contract import ChapterSeamContract


def test_chapter_seam_contract_requires_continuity_after_opening_chapter() -> None:
    with pytest.raises(ValidationError):
        ChapterSeamContract(chapter_no=52)


def test_chapter_seam_contract_accepts_inherited_state() -> None:
    contract = ChapterSeamContract(
        chapter_no=52,
        inherits_from_prev=["ch51 尾声中镜债未解"],
        required_callbacks=["回执镜片"],
        carry_forward_state={"林渊": "刚拿到回执"},
        forbidden_resets=["不要重新介绍困魂镜规则"],
    )

    assert contract.chapter_no == 52
    assert contract.required_callbacks == ["回执镜片"]
