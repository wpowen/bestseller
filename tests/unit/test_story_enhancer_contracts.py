"""Book-level story-enhancer checkboxes → hard per-chapter outline contracts.

User feedback (zhaoshen-hr-v13, 26 chapters): the framework HAS 18 story-effect
skills + 脑洞/concept-lab + 反常识 creativity, but they were soft advisory and
never reached the outline, so the chapters came out logically rigorous yet bland
(the core 脑洞 "招神改写现实" appeared 0 times in 25 chapters). These tests pin
the engine layer that turns book-creation checkboxes into hard outline contracts.
"""

from __future__ import annotations

from bestseller.services.story_effect_skills import (
    ALL_STORY_EFFECT_SKILL_KEYS,
    _render_selected_story_effect_contract,
)


def test_all_18_skills_render_a_nonempty_contract() -> None:
    """Every one of the 18 skills must yield a real contract when selected —
    previously only 4 did, so checking the other 14 did nothing."""
    assert len(ALL_STORY_EFFECT_SKILL_KEYS) == 18
    for key in ALL_STORY_EFFECT_SKILL_KEYS:
        block = _render_selected_story_effect_contract(key, language="zh-CN")
        assert block and block.strip(), f"{key} rendered empty contract"
        # Hard contract must demand per-chapter cashing, not vague advice.
        assert ("每章" in block or "本章" in block or "必须" in block), key


def test_generic_contract_carries_skill_identity() -> None:
    """A skill without a dedicated renderer still gets a contract derived from
    its catalog entry (description / output_contract), not a blank placeholder."""
    block = _render_selected_story_effect_contract("moral_dilemma_engine", language="zh-CN")
    assert "moral_dilemma" in block or "道德" in block or "困境" in block


def test_unknown_skill_key_renders_empty() -> None:
    assert _render_selected_story_effect_contract("not_a_real_engine", language="zh-CN") == ""


from bestseller.services.story_enhancers import (
    STORY_ENHANCERS_METADATA_KEY,
    StoryEnhancerSelection,
    render_story_enhancer_contract_block,
    resolve_story_enhancers,
)


def test_resolve_story_enhancers_from_metadata() -> None:
    meta = {
        STORY_ENHANCERS_METADATA_KEY: {
            "brainhole": True,
            "concept_lab": True,
            "creativity_direction": "cross-genre-friction",
            "effect_skills": ["comedy_engine", "twist_reversal_engine", "not_real"],
        }
    }
    sel = resolve_story_enhancers(meta)
    assert sel.brainhole is True
    assert sel.concept_lab is True
    assert sel.creativity_direction == "cross-genre-friction"
    # Invalid skill keys are dropped; valid ones kept (order preserved).
    assert sel.effect_skills == ("comedy_engine", "twist_reversal_engine")


def test_resolve_empty_when_absent() -> None:
    sel = resolve_story_enhancers({})
    assert sel.is_empty()
    assert sel.effect_skills == ()


def test_render_book_contract_demands_every_chapter() -> None:
    sel = StoryEnhancerSelection(
        brainhole=True,
        effect_skills=("comedy_engine", "twist_reversal_engine"),
    )
    block = render_story_enhancer_contract_block(sel, language="zh-CN")
    assert block
    # Book-level header makes it apply to ALL chapters (not router-picks-one).
    assert "每章" in block or "每一章" in block
    # Each selected skill's contract is present.
    assert "comedy_engine" in block
    assert "twist_reversal_engine" in block


def test_comedy_anchor_is_genre_native_not_urban_locked() -> None:
    block = render_story_enhancer_contract_block(
        StoryEnhancerSelection(effect_skills=("comedy_engine",)), language="zh-CN"
    )
    assert "现代规则中报错" not in block
    assert "符合本题材世界规则" in block


def test_render_empty_selection_is_empty() -> None:
    assert render_story_enhancer_contract_block(StoryEnhancerSelection(), language="zh-CN") == ""


from bestseller.services.story_enhancers import (
    audit_story_enhancer_coverage,
    story_enhancer_repair_directives,
)


def _ch(n, text):
    return {"chapter_number": n, "main_conflict": text, "scenes": [{"purpose": {"story": text}}]}


def test_audit_flags_uncashed_effect() -> None:
    """The v13 failure: comedy/twist selected but chapters are all bland
    compliance procedural → audit must flag the uncashed effects."""
    sel = StoryEnhancerSelection(effect_skills=("comedy_engine", "twist_reversal_engine"))
    bland = [_ch(i, "陈屿必须在合规复核和保Offer之间二选一，红线评分逼近") for i in range(1, 11)]
    gaps = audit_story_enhancer_coverage(bland, sel)
    flagged = {g["effect"] for g in gaps}
    assert "comedy_engine" in flagged and "twist_reversal_engine" in flagged


def test_audit_passes_when_effects_present() -> None:
    sel = StoryEnhancerSelection(effect_skills=("comedy_engine",))
    funny = [_ch(i, "一个荒诞的反差笑点，主角吐槽到尴尬") for i in range(1, 11)]
    gaps = audit_story_enhancer_coverage(funny, sel)
    assert all(g["effect"] != "comedy_engine" for g in gaps)


def test_repair_directives_name_the_missing_effect() -> None:
    sel = StoryEnhancerSelection(effect_skills=("comedy_engine",))
    bland = [_ch(i, "程序合规审核签字") for i in range(1, 11)]
    directives = story_enhancer_repair_directives(bland, sel)
    assert directives
    assert any("comedy" in d or "喜剧" in d or "笑" in d for d in directives)


def test_no_directives_when_nothing_selected() -> None:
    assert story_enhancer_repair_directives([_ch(1, "x")], StoryEnhancerSelection()) == []
