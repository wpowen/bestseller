from __future__ import annotations

import pytest

from bestseller.services.story_design_grammars import (
    load_story_design_grammar_registry,
    render_story_design_grammar_prompt_block,
    resolve_story_design_grammar,
)

pytestmark = pytest.mark.unit


EXPECTED_GRAMMARS = {
    "default",
    "action-progression",
    "base-building",
    "eastern-aesthetic",
    "esports-competition",
    "female-growth-ncp",
    "otherworld-cross-system",
    "relationship-driven",
    "strategy-worldbuilding",
    "suspense-mystery",
}


def test_registry_loads_all_story_design_grammars() -> None:
    registry = load_story_design_grammar_registry()

    assert EXPECTED_GRAMMARS.issubset(registry.keys())


def test_each_grammar_has_enough_design_surface_area() -> None:
    registry = load_story_design_grammar_registry()

    for key in EXPECTED_GRAMMARS:
        grammar = registry[key]
        assert len(grammar.state_variables) >= 5, key
        assert len(grammar.chapter_change_vectors) >= 5, key
        assert len(grammar.reader_rewards) >= 5, key
        assert len(grammar.forbidden_defaults) >= 5, key


def test_resolve_story_design_grammar_by_key_and_genre_text() -> None:
    assert resolve_story_design_grammar(category_key="base-building").key == "base-building"
    assert resolve_story_design_grammar(genre="悬疑推理").key == "suspense-mystery"
    assert resolve_story_design_grammar(genre="异界穿越").key == "otherworld-cross-system"
    assert resolve_story_design_grammar(genre="仙侠升级").key == "action-progression"
    assert (
        resolve_story_design_grammar(genre="女频", sub_genre="言情成长").key
        == "relationship-driven"
    )


def test_render_grammar_prompt_block_surfaces_vectors_and_anti_defaults() -> None:
    grammar = resolve_story_design_grammar(genre="悬疑推理")

    block = render_story_design_grammar_prompt_block(grammar)

    assert "Story Design Grammar" in block
    assert "Chapter change vectors" in block
    assert "Forbidden defaults" in block
    assert grammar.chapter_change_vectors[0] in block
