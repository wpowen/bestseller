"""L1 tests for story-bible mermaid diagram export (`_render_diagrams`).

The renderer is duck-typed over the overview object, so a SimpleNamespace
stands in for StoryBibleOverview. Pins: fence pairing, node/edge emission,
label sanitization (no broken mermaid from hostile names), and placeholder
degradation when data is missing.
"""

from __future__ import annotations

# ruff: noqa: RUF001 — Chinese punctuation is intentional.
from types import SimpleNamespace

from bestseller.services.story_bible_export import (
    _mermaid_label,
    _render_diagrams,
)


def _full_overview() -> SimpleNamespace:
    return SimpleNamespace(
        characters=[
            SimpleNamespace(name="林晚", role="protagonist"),
            SimpleNamespace(name="赵天南", role="antagonist"),
        ],
        relationships=[
            SimpleNamespace(
                character_a="林晚",
                character_b="赵天南",
                relationship_type="宿敌",
                strength=-0.8,
            ),
        ],
        factions=[
            SimpleNamespace(name="铁衣卫", relationship_to_protagonist="敌对"),
        ],
        volume_frontiers=[
            SimpleNamespace(
                volume_number=1, title="出城", start_chapter_number=1,
                end_chapter_number=30,
            ),
            SimpleNamespace(
                volume_number=2, title="入局", start_chapter_number=31,
                end_chapter_number=None,
            ),
        ],
        deferred_reveals=[
            SimpleNamespace(
                reveal_code="R-01", label="身世", reveal_volume_number=2,
                reveal_chapter_number=45,
            ),
        ],
    )


def _empty_overview() -> SimpleNamespace:
    return SimpleNamespace(
        characters=[], relationships=[], factions=[],
        volume_frontiers=[], deferred_reveals=[],
    )


def test_full_data_renders_four_mermaid_blocks() -> None:
    out = _render_diagrams(
        "测试书",
        _full_overview(),
        {"conflict_map": [
            {"character_a": "林晚", "character_b": "赵天南", "conflict_type": "夺位"},
        ]},
        {"power_system": {"tiers": ["炼气", "筑基", "金丹"],
                          "protagonist_starting_tier": "炼气"}},
        None,
    )
    assert out.count("```mermaid") == 4
    # Every fence opened is closed.
    assert out.count("```") == 8
    # Relationship edge + conflict edge both present.
    assert "-->|宿敌 -0.8|" in out
    assert "-.->|冲突:夺位|" in out
    # Tier chain and highlight of starting tier.
    assert "T0 --> T1" in out
    assert "style T0" in out
    # Timeline: volumes chained, reveal attached to its volume.
    assert "V1 --> V2" in out
    assert "R0 -.-> V2" in out


def test_missing_data_degrades_to_placeholder_not_broken_mermaid() -> None:
    out = _render_diagrams("空书", _empty_overview(), None, None, None)
    assert "```mermaid" not in out
    assert out.count("_(尚未生成)_") == 4


def test_volume_plan_fallback_when_no_frontiers() -> None:
    out = _render_diagrams(
        "书", _empty_overview(), None, None,
        {"volumes": [
            {"volume_number": 1, "volume_title": "卷一"},
            {"volume_number": 2, "volume_title": "卷二"},
        ]},
    )
    assert "V1 --> V2" in out


def test_hostile_names_are_sanitized() -> None:
    overview = SimpleNamespace(
        characters=[
            SimpleNamespace(name='坏"名|字[x]', role="protagonist"),
        ],
        relationships=[], factions=[], volume_frontiers=[], deferred_reveals=[],
    )
    out = _render_diagrams("书", overview, None, None, None)
    # The label content must have every mermaid-breaking char translated
    # (node syntax itself legitimately uses [" and "]).
    block = out.split("```mermaid", 1)[1].split("```", 1)[0]
    assert "坏'名/字（x）" in block


def test_mermaid_label_truncates_and_translates() -> None:
    assert _mermaid_label('a"b|c[d]') == "a'b/c（d）"
    long = "很" * 40
    assert len(_mermaid_label(long)) <= 24
    assert _mermaid_label(long).endswith("…")
