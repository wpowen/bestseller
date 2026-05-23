from __future__ import annotations

from uuid import uuid4

from bestseller.domain.chapter_seam_contract import ChapterSeamContract
from bestseller.services.diversity_budget import DiversityBudget
from bestseller.services.invariants import seed_invariants
from bestseller.services.prompt_constructor import build_chapter_l3_blocks, build_chapter_prompt
from bestseller.services.seam_prompt_composer import (
    render_seam_prompt_block,
    require_chapter_seam_contract,
)


def _contract() -> ChapterSeamContract:
    return ChapterSeamContract(
        chapter_no=52,
        inherits_from_prev=["ch51 章尾林渊已看到回执镜片"],
        required_callbacks=["回执镜片", "张家开门人"],
        opening_state="从镜片在掌心发烫直接开场。",
        carry_forward_state={"林渊": "刚确认父亲抵债线索"},
        forbidden_resets=["不要重新解释青囊来历"],
    )


def test_seam_prompt_composer_renders_required_callbacks() -> None:
    block = render_seam_prompt_block(_contract())

    assert "Chapter Seam Contract" in block
    assert "回执镜片" in block
    assert "不要重新解释青囊来历" in block


def test_require_chapter_seam_contract_rejects_missing_payload() -> None:
    try:
        require_chapter_seam_contract(None)
    except ValueError as exc:
        assert "seam_contract is required" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected ValueError")


def test_prompt_constructor_places_seam_block_in_system_prefix() -> None:
    invariants = seed_invariants(
        project_id=uuid4(),
        language="zh-CN",
        words_per_chapter={"min": 1000, "target": 1500, "max": 2000},
        pov="close_third",
    )

    plan = build_chapter_prompt(
        invariants,
        DiversityBudget(project_id=uuid4()),
        chapter_no=52,
        system="你是畅销小说作者。",
        seam_contract=_contract(),
        scene_spec="本章继续追查镜片。",
    )

    assert plan.render_system().startswith("你是畅销小说作者。")
    assert "Chapter Seam Contract" in plan.render_system()[:300]
    assert "回执镜片" in plan.render()


def test_l3_blocks_include_seam_contract() -> None:
    invariants = seed_invariants(
        project_id=uuid4(),
        language="zh-CN",
        words_per_chapter={"min": 1000, "target": 1500, "max": 2000},
        pov="close_third",
    )

    blocks = build_chapter_l3_blocks(
        invariants,
        DiversityBudget(project_id=uuid4()),
        chapter_no=52,
        seam_contract=_contract(),
    )

    assert "Chapter Seam Contract" in blocks.as_prompt_block()
