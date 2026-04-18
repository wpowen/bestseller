"""Regression tests for ``parse_world_spec_input`` sanitizer.

Background
----------
``WorldRuleInput.description`` is a required field (``min_length=1``).  LLMs
occasionally omit it — emitting ``{"name": ..., "trigger": ...}`` or similar
variants — and the resulting ``ValidationError`` propagates up through
``upsert_world_spec`` and crashes the whole autowrite task.  A single sloppy
model response should never take down a 7x24 pipeline, so the service layer
coalesces common alias fields into ``description`` and falls back to ``name``
as a last resort.

These tests pin down that behaviour.
"""

from __future__ import annotations

import pytest

from bestseller.services.story_bible import parse_world_spec_input

pytestmark = pytest.mark.unit


def test_parses_rules_with_canonical_description() -> None:
    spec = parse_world_spec_input(
        {
            "world_name": "Arcadia",
            "rules": [
                {"name": "Gravity", "description": "Things fall down."},
            ],
        }
    )
    assert len(spec.rules) == 1
    assert spec.rules[0].description == "Things fall down."


def test_coalesces_trigger_into_description_when_description_missing() -> None:
    spec = parse_world_spec_input(
        {
            "rules": [
                {"name": "Mana Surge", "trigger": "Emotional resonance activates latent flows."},
            ],
        }
    )
    assert spec.rules[0].description == "Emotional resonance activates latent flows."


def test_coalesces_effect_when_neither_description_nor_trigger_present() -> None:
    spec = parse_world_spec_input(
        {
            "rules": [
                {"name": "Soul Weight", "effect": "Lies increase the soul's karmic burden."},
            ],
        }
    )
    assert spec.rules[0].description == "Lies increase the soul's karmic burden."


def test_falls_back_to_name_when_no_alias_field_present() -> None:
    """Pipeline must not crash even if the LLM emits a near-empty rule."""
    spec = parse_world_spec_input(
        {
            "rules": [
                {"name": "代价递进律"},  # only name
            ],
        }
    )
    # Name is echoed into description; the row is still persisted.
    assert spec.rules[0].description == "代价递进律"


def test_preserves_empty_whitespace_description_by_falling_back() -> None:
    """A whitespace-only description is treated the same as missing."""
    spec = parse_world_spec_input(
        {
            "rules": [
                {"name": "Echo Law", "description": "   ", "trigger": "Repeat a truth three times."},
            ],
        }
    )
    assert spec.rules[0].description == "Repeat a truth three times."


def test_reproduces_production_failure_payload() -> None:
    """Exact shape observed in production logs — 4 rules all missing description,
    each carrying only ``name`` and ``trigger``.  Before the fix this raised
    ``4 validation errors for WorldSpecInput`` and failed the autowrite task."""
    payload = {
        "rules": [
            {"name": "势力边界律", "trigger": "第一次正式谈判"},
            {"name": "声望清算机", "trigger": "声望崩塌事件"},
            {"name": "代价递进律", "trigger": "陷入困境的真相"},
            {"name": "力量跃迁代", "trigger": "力量退化事件"},
        ]
    }
    spec = parse_world_spec_input(payload)
    assert len(spec.rules) == 4
    assert all(rule.description for rule in spec.rules)
