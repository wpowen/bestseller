"""Taxonomy describes a genre; payoff decks require explicit author selection.

Keep the original taxonomy-to-writer path covered, including the positive case:
when an author selects a curated effect deck, its type, recipe and intensity
must still reach the actual prompt builder. Missing genre/effect choices are a
valid no-op and must not receive a synthesized commercial story contract.
"""

from __future__ import annotations

from bestseller.services.hype_engine import hype_scheme_from_preset_overrides
from bestseller.services.writing_presets import get_genre_preset, synthesize_genre_preset


def _scheme_for(genre_key: str, genre: str, sub_genre: str | None):
    preset = synthesize_genre_preset(genre_key, genre=genre, sub_genre=sub_genre)
    return preset, hype_scheme_from_preset_overrides(
        getattr(preset, "writing_profile_overrides", {}) or {}
    )


def test_taxonomy_preset_leaves_unselected_hype_empty() -> None:
    """The same three genre routes must not silently adopt an effect deck."""

    cases = [
        ("custom-light-novel", "轻小说·二次元", "搞笑沙雕"),
        ("custom-xuanhuan", "东方玄幻", "符箓秘术"),
        ("custom-urban", "都市", "都市逆袭"),
    ]
    for key, genre, sub in cases:
        preset, scheme = _scheme_for(key, genre, sub)
        overrides = getattr(preset, "writing_profile_overrides", {}) or {}
        assert overrides.get("hype") == {}
        assert scheme.recipe_deck == ()
        assert scheme.is_empty


def test_unknown_genre_does_not_adopt_a_generic_deck() -> None:
    """Unrecognized genre labels must not invent payoff obligations."""

    _preset, scheme = _scheme_for("custom-unknown", "某个没见过的题材", None)
    assert scheme.recipe_deck == ()
    assert scheme.is_empty


def test_hype_constraints_actually_reach_the_prompt() -> None:
    """No implicit effects; explicitly chosen effects still reach the writer."""

    import inspect
    from uuid import uuid4

    from bestseller.services.diversity_budget import DiversityBudget
    from bestseller.services.invariants import LengthEnvelope, ProjectInvariants
    from bestseller.services.prompt_constructor import build_chapter_hype_blocks

    required = [
        name
        for name, param in inspect.signature(LengthEnvelope.__init__).parameters.items()
        if name != "self" and param.default is inspect.Parameter.empty
    ]
    envelope = LengthEnvelope(
        **{
            name: (1800 if "min" in name else 3600 if "max" in name else 2600)
            for name in required
        }
    )
    project_id = uuid4()
    _preset, scheme = _scheme_for("custom-light-novel", "轻小说·二次元", "搞笑沙雕")
    invariants = ProjectInvariants(
        project_id=project_id,
        language="zh-CN",
        length_envelope=envelope,
        hype_scheme=scheme,
    )
    blocks = build_chapter_hype_blocks(
        invariants,
        DiversityBudget(project_id=project_id),
        chapter_no=5,
        total_chapters=50,
    )
    assert blocks.hype_constraints_block == ""
    assert blocks.assigned_hype_type is None
    assert blocks.assigned_hype_recipe is None
    assert blocks.assigned_hype_intensity is None

    # Selecting a deck is separate from choosing the genre label above.
    chosen = get_genre_preset("xianxia-upgrade")
    assert chosen is not None
    invariants = ProjectInvariants(
        project_id=project_id,
        language="zh-CN",
        length_envelope=envelope,
        hype_scheme=hype_scheme_from_preset_overrides(chosen.writing_profile_overrides),
    )
    blocks = build_chapter_hype_blocks(
        invariants,
        DiversityBudget(project_id=project_id),
        chapter_no=5,
        total_chapters=50,
    )
    assert blocks.hype_constraints_block
    assert blocks.assigned_hype_type is not None
    assert blocks.assigned_hype_recipe is not None
    assert blocks.assigned_hype_intensity is not None
    assert "爽点" in blocks.hype_constraints_block
