"""Regression tests for writer-prompt context budget enforcement.

Root cause (2026-06-02 quality regression): ``_budget_context_sections`` only
recognised ~39 of the ~76 section keys actually passed in by
``build_scene_draft_prompts``. The other ~37 — including the heavy "advanced
garnish" blocks (voice_dna, diversity, l3_prompt, signature_scene, ...) —
bypassed the token budget entirely (kept full, never counted), so the writer
prompt ballooned to ~17.7k tokens regardless of ``context_budget_tokens``.

These tests lock in: (1) advanced/uncovered sections ARE subject to the budget,
and (2) integrity guardrails are protected.

F1+F2 (2026-06-03): regression tests for the "Tier-1 starvation" fix.
  - F2: a single block is hard-capped at ``per_block_token_cap`` (default
    600 tokens) before budgeting, so one bloated canon block can no longer
    eat the entire budget on its own.
  - F1: Tier 1 is also subject to a soft cap (80% of budget). If Tier 1
    alone would exceed that share, the LARGEST Tier 1 items are dropped
    first so Tier 2 (which holds continuity sections like
    ``recent_scene_section``, ``emotion_track_section``, ``clue_section``)
    can still get budget.
"""

from __future__ import annotations

import pytest

from bestseller.services.drafts import (
    _budget_context_sections,
    _estimate_tokens,
    _truncate_section_to_tokens,
)


def _big(token_target: int) -> str:
    """Return CJK text whose estimated tokens ~= token_target."""
    return "字" * token_target


@pytest.mark.unit
def test_advanced_garnish_sections_are_trimmed_when_over_budget() -> None:
    """Uncovered 'advanced' sections must not bypass the budget."""
    sections = {
        # Tier-1 essential contract — small, must survive.
        "contract_section": _big(200),
        # Advanced garnish blocks (previously uncovered → bypassed budget).
        "voice_dna_line": _big(4000),
        "five_layer_line": _big(4000),
        "l3_prompt_line": _big(4000),
        "signature_scene_line": _big(4000),
    }
    result = _budget_context_sections(sections, budget_tokens=1000)

    # Essential contract is always kept.
    assert result["contract_section"] == sections["contract_section"]

    # At least some of the oversized advanced blocks must be blanked so the
    # total respects the budget (previously NONE were blanked).
    kept_advanced = [
        k
        for k in ("voice_dna_line", "five_layer_line", "l3_prompt_line", "signature_scene_line")
        if result[k]
    ]
    assert len(kept_advanced) < 4, "advanced garnish bypassed the budget"

    total = sum(_estimate_tokens(v) for v in result.values())
    # contract(200) fits; the rest should be trimmed to keep us near budget.
    assert total <= 1000 + 4000, f"budget not enforced on advanced blocks: {total}"


@pytest.mark.unit
def test_integrity_guardrails_are_protected() -> None:
    """Coherence guardrails (canon/timeline/character_role/length) survive."""
    sections = {
        "canon_guardrails_line": _big(300),
        "timeline_canon_line": _big(300),
        "character_role_line": _big(300),
        "chapter_length_line": _big(120),
        "current_scene_contract_line": _big(200),
        # Advanced garnish that should yield budget to the guardrails.
        "voice_dna_line": _big(5000),
        "budget_diversity_line": _big(5000),
    }
    result = _budget_context_sections(sections, budget_tokens=2000)

    for key in (
        "canon_guardrails_line",
        "timeline_canon_line",
        "character_role_line",
        "chapter_length_line",
        "current_scene_contract_line",
    ):
        assert result[key], f"integrity guardrail dropped: {key}"


@pytest.mark.unit
def test_unknown_future_section_does_not_bypass_budget() -> None:
    """Any section not explicitly tiered defaults to trimmable, not free."""
    sections = {
        "contract_section": _big(100),
        "some_future_block_line": _big(9000),
    }
    result = _budget_context_sections(sections, budget_tokens=500)
    assert result["contract_section"]
    assert not result["some_future_block_line"], "untiered section bypassed budget"


@pytest.mark.unit
def test_story_engine_creative_core_is_tier_zero_and_never_trimmed() -> None:
    sections = {
        "creative_core_line": "CURRENT-CHAPTER-ENGINE-CONTEXT",
        "contract_section": _big(200),
        "some_future_block_line": _big(9000),
    }

    result = _budget_context_sections(sections, budget_tokens=1)

    assert result["creative_core_line"] == "CURRENT-CHAPTER-ENGINE-CONTEXT"
    assert not result["some_future_block_line"]


# ---------------------------------------------------------------------------
# F2: per-block hard cap.  A single bloated block must not eat the entire
# budget on its own — it gets head+tail truncated to the configured cap.
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_per_block_cap_hard_limits_single_block() -> None:
    """One huge block → truncated to ≤ cap, never blanked."""
    big = _big(5000)
    result = _truncate_section_to_tokens(big, max_tokens=600)
    assert result != big, "oversized block should be truncated"
    assert _estimate_tokens(result) <= 800, (
        f"truncated block should be near the cap; got {_estimate_tokens(result)} tokens"
    )
    # Truncation must keep both head and tail — opening rule + latest state.
    assert "…[truncated;" in result
    assert result.startswith("字"), "head lost during truncation"
    assert result.rstrip().endswith("字"), "tail lost during truncation"


@pytest.mark.unit
def test_per_block_cap_no_op_for_small_blocks() -> None:
    """Blocks under the cap must pass through unchanged."""
    text = _big(100)
    assert _truncate_section_to_tokens(text, max_tokens=600) == text


@pytest.mark.unit
def test_per_block_cap_zero_means_unlimited() -> None:
    """Cap = 0 means "no cap" — keep the full block (back-compat for callers
    that explicitly want to disable F2)."""
    big = _big(5000)
    assert _truncate_section_to_tokens(big, max_tokens=0) == big


# ---------------------------------------------------------------------------
# F1: Tier 1 soft cap.  When Tier 1 alone is bigger than 80% of the budget,
# the LARGEST Tier 1 items get dropped first so Tier 2 (which holds the
# continuity section) can still get budget.  This is the regression fix for
# the "丢连续性" starvation observed in the WS-B post-mortem.
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_tier1_soft_cap_drops_largest_items_preserving_continuity() -> None:
    """If Tier 1 alone would consume >80% of budget, drop the biggest first.

    The historical regression: a content-dense chapter had Tier 1 (canon +
    timeline + character_role + length + contract + qimao opening + reader
    contract) summing to ~12.4k tokens against an 8k budget, which silently
    starved ``recent_scene_section`` (continuity) and forced
    ``emotion_track_section`` / ``clue_section`` out of the prompt — the
    writer then lost the cross-scene context the user complained about
    ("读不通").  After F1, the largest Tier 1 items get dropped, freeing
    budget for the Tier 2 continuity sections.

    Test arithmetic (after F2 per-block cap of 600):
      * 3 large Tier 1 → 3×600 = 1800 tokens; 80% of 2000 = 1600; F1 drops
        1 (1800→1200) so the remaining Tier 1 fits the 80% ceiling.
      * Pass 2 then has 2000−1200 = 800 tokens of headroom, enough for
        recent_scene(200) + emotion(200) + clue(200) = 600.
    """
    sections = {
        # Tier 1 — three big reference blocks.  Each truncates to 600
        # under F2, summing to 1800 — over 80% of the 2000 budget.
        "canon_guardrails_line": _big(2000),
        "timeline_canon_line": _big(2000),
        "character_role_line": _big(2000),
        # Tier 2 — the continuity sections we MUST protect.
        "recent_scene_section": _big(200),
        "emotion_track_section": _big(200),
        "clue_section": _big(200),
    }
    result = _budget_context_sections(sections, budget_tokens=2000)

    # Continuity must NOT be blanked (this is the whole point of F1).
    assert result["recent_scene_section"], "continuity section was starved"
    assert result["emotion_track_section"], "emotion track was starved"
    assert result["clue_section"], "clue section was starved"

    # Total stays at-or-under the budget (no longer balloons past it).
    total = sum(_estimate_tokens(v) for v in result.values())
    assert total <= 2000, (
        f"budget not enforced: total={total} > 2000 — Tier 1 cap didn't help"
    )

    # At least one of the largest Tier 1 items must have been dropped to make
    # room for Tier 2. (We don't pin *which* one — depends on sort order —
    # but at least one must be blanked for the assertion above to hold.)
    dropped_tier1 = [
        k
        for k in (
            "canon_guardrails_line",
            "timeline_canon_line",
            "character_role_line",
        )
        if not result[k]
    ]
    assert dropped_tier1, (
        "Tier 1 soft cap did not drop any item — continuity must have won "
        "budget from somewhere"
    )


@pytest.mark.unit
def test_tier1_soft_cap_no_op_when_tier1_fits() -> None:
    """If Tier 1 fits in 80% of budget, no Tier 1 items are dropped."""
    sections = {
        "canon_guardrails_line": _big(200),
        "timeline_canon_line": _big(200),
        "character_role_line": _big(200),
        "chapter_length_line": _big(120),
        "current_scene_contract_line": _big(200),
    }
    result = _budget_context_sections(sections, budget_tokens=2000)

    # Everything still kept (Tier 1 sum ~920 < 2000*0.8 = 1600).
    for key in sections:
        assert result[key], f"Tier 1 item dropped unnecessarily: {key}"


@pytest.mark.unit
def test_tier1_soft_cap_never_drops_binding_blocks() -> None:
    """CD5 regression: F1 must drop redundant guardrails, NEVER binding blocks.

    Before this fix, F1 dropped the *largest* Tier-1 item by size. After F2
    caps every block to ~600, the binding contract / canon-fact blocks tie
    with the integrity guardrails, so the size-only sort could drop
    ``contract_section`` or ``hard_fact_line`` — losing the writer's binding
    contract or the canon facts the prose must not contradict. Those must be
    protected; only the redundant guardrails (canon/timeline/scene_coherence/
    character_role) may be dropped to free budget.
    """
    sections = {
        # Binding — MUST survive even under heavy pressure.
        "contract_section": _big(2000),
        "current_scene_contract_line": _big(2000),
        "hard_fact_line": _big(2000),
        "identity_line": _big(2000),
        # Redundant integrity guardrails — droppable under pressure.
        "canon_guardrails_line": _big(2000),
        "timeline_canon_line": _big(2000),
        "scene_coherence_line": _big(2000),
        "character_role_line": _big(2000),
    }
    result = _budget_context_sections(sections, budget_tokens=2000)

    for key in (
        "contract_section",
        "current_scene_contract_line",
        "hard_fact_line",
        "identity_line",
    ):
        assert result[key], f"binding block was dropped by F1: {key}"
