"""Unit tests for ``character_identity_resolver``.

Covers the three public helpers that prevent the ``王守真 / 王守真（三叔） /
三叔`` duplicate-character failure mode:

* ``canonical_character_key`` — strip trailing parens/slashes
* ``collect_entry_aliases`` — normalize alias lists
* ``resolve_character_match`` — 4-rule match order
* ``merge_character_with_aliases`` — fold incoming into existing
"""

from __future__ import annotations

import pytest

from bestseller.services.character_identity_resolver import (
    canonical_character_key,
    collect_entry_aliases,
    merge_character_with_aliases,
    resolve_character_match,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# canonical_character_key
# ---------------------------------------------------------------------------


class TestCanonicalCharacterKey:
    def test_plain_name_unchanged(self) -> None:
        assert canonical_character_key("王守真") == "王守真"

    def test_strips_surrounding_whitespace(self) -> None:
        assert canonical_character_key("  王守真  ") == "王守真"

    def test_strips_cjk_fullwidth_parenthetical(self) -> None:
        assert canonical_character_key("王守真（三叔）") == "王守真"

    def test_strips_ascii_parenthetical(self) -> None:
        assert canonical_character_key("王守真(三叔)") == "王守真"

    def test_strips_trailing_mixed_paren_then_whitespace(self) -> None:
        assert canonical_character_key("王守真（三叔）  ") == "王守真"

    def test_strips_slash_suffix(self) -> None:
        assert canonical_character_key("王守真/三叔") == "王守真"

    def test_strips_cjk_fullwidth_slash_suffix(self) -> None:
        assert canonical_character_key("王守真／三叔") == "王守真"

    def test_strips_pipe_suffix(self) -> None:
        assert canonical_character_key("王守真|三叔") == "王守真"
        assert canonical_character_key("王守真｜三叔") == "王守真"

    def test_strips_middot_suffix(self) -> None:
        assert canonical_character_key("王守真·三叔") == "王守真"
        assert canonical_character_key("王守真・三叔") == "王守真"

    def test_does_not_fuzzy_match_prefix(self) -> None:
        # "张三" vs "张三丰" must stay distinct — no prefix stripping.
        assert canonical_character_key("张三") != canonical_character_key("张三丰")
        assert canonical_character_key("张三丰") == "张三丰"

    def test_non_string_returns_empty(self) -> None:
        assert canonical_character_key(None) == ""
        assert canonical_character_key(42) == ""
        assert canonical_character_key(["王守真"]) == ""

    def test_empty_string_returns_empty(self) -> None:
        assert canonical_character_key("") == ""
        assert canonical_character_key("   ") == ""

    def test_handles_nested_parens(self) -> None:
        # Loop peels each trailing paren group until stable.
        assert canonical_character_key("王守真（三叔）（小）") == "王守真"


# ---------------------------------------------------------------------------
# collect_entry_aliases
# ---------------------------------------------------------------------------


class TestCollectEntryAliases:
    def test_list_of_strings(self) -> None:
        assert collect_entry_aliases({"aliases": ["三叔", "守真叔"]}) == ["三叔", "守真叔"]

    def test_single_string_wrapped(self) -> None:
        assert collect_entry_aliases({"aliases": "三叔"}) == ["三叔"]

    def test_deduplicates_preserving_order(self) -> None:
        assert collect_entry_aliases({"aliases": ["三叔", "三叔", "守真叔"]}) == [
            "三叔",
            "守真叔",
        ]

    def test_strips_whitespace(self) -> None:
        assert collect_entry_aliases({"aliases": ["  三叔  ", "守真叔"]}) == [
            "三叔",
            "守真叔",
        ]

    def test_missing_field_returns_empty(self) -> None:
        assert collect_entry_aliases({"name": "王守真"}) == []

    def test_non_mapping_returns_empty(self) -> None:
        assert collect_entry_aliases(None) == []
        assert collect_entry_aliases("王守真") == []

    def test_ignores_non_string_items(self) -> None:
        assert collect_entry_aliases({"aliases": ["三叔", 42, None, "守真叔"]}) == [
            "三叔",
            "守真叔",
        ]

    def test_ignores_empty_strings(self) -> None:
        assert collect_entry_aliases({"aliases": ["三叔", "", "   "]}) == ["三叔"]


# ---------------------------------------------------------------------------
# resolve_character_match — 4-rule order
# ---------------------------------------------------------------------------


class TestResolveCharacterMatch:
    def test_rule_1_exact_name_match(self) -> None:
        registry = {"王守真": {"name": "王守真", "role": "supporting"}}
        assert resolve_character_match({"name": "王守真"}, registry) == "王守真"

    def test_rule_2_candidate_name_in_existing_aliases(self) -> None:
        # Existing has aliases=["三叔"]; incoming "三叔" should resolve to "王守真".
        registry = {"王守真": {"name": "王守真", "aliases": ["三叔"]}}
        assert resolve_character_match({"name": "三叔"}, registry) == "王守真"

    def test_rule_3_existing_name_in_candidate_aliases(self) -> None:
        # Registry keyed by raw "三叔"; incoming is the canonical name with
        # aliases=["三叔"], should find the existing row.
        registry = {"三叔": {"name": "三叔"}}
        assert (
            resolve_character_match({"name": "王守真", "aliases": ["三叔"]}, registry)
            == "三叔"
        )

    def test_rule_4_canonical_keys_match(self) -> None:
        # "王守真（三叔）" canonical is "王守真"; matches existing "王守真".
        registry = {"王守真": {"name": "王守真"}}
        assert resolve_character_match({"name": "王守真（三叔）"}, registry) == "王守真"

    def test_rule_4_via_existing_alias_canonical(self) -> None:
        # Existing has alias "王守真（三叔）"; incoming canonical is "王守真".
        registry = {"老王": {"name": "老王", "aliases": ["王守真（三叔）"]}}
        assert resolve_character_match({"name": "王守真"}, registry) == "老王"

    def test_no_match_returns_none(self) -> None:
        registry = {"王守真": {"name": "王守真"}}
        assert resolve_character_match({"name": "宁尘"}, registry) is None

    def test_does_not_cross_distinct_names(self) -> None:
        # "张三" and "张三丰" must NOT resolve to each other.
        registry = {"张三丰": {"name": "张三丰"}}
        assert resolve_character_match({"name": "张三"}, registry) is None

    def test_empty_registry_returns_none(self) -> None:
        assert resolve_character_match({"name": "王守真"}, {}) is None

    def test_missing_candidate_name_returns_none(self) -> None:
        assert resolve_character_match({"role": "supporting"}, {"王守真": {}}) is None

    def test_invalid_inputs_return_none(self) -> None:
        assert resolve_character_match(None, {"王守真": {}}) is None  # type: ignore[arg-type]
        assert resolve_character_match({"name": "王守真"}, None) is None  # type: ignore[arg-type]

    def test_exact_match_beats_alias(self) -> None:
        # If candidate name exactly matches a registry key, that wins even if
        # another entry has this as an alias.
        registry = {
            "王守真": {"name": "王守真"},
            "老王": {"name": "老王", "aliases": ["王守真"]},
        }
        assert resolve_character_match({"name": "王守真"}, registry) == "王守真"


# ---------------------------------------------------------------------------
# merge_character_with_aliases
# ---------------------------------------------------------------------------


class TestMergeCharacterWithAliases:
    def test_preserves_existing_name_and_role(self) -> None:
        existing = {"name": "王守真", "role": "protagonist"}
        incoming = {"name": "三叔", "role": "supporting"}
        merged = merge_character_with_aliases(existing, incoming)
        assert merged["name"] == "王守真"
        assert merged["role"] == "protagonist"

    def test_folds_incoming_name_into_aliases(self) -> None:
        existing = {"name": "王守真", "aliases": []}
        incoming = {"name": "三叔"}
        merged = merge_character_with_aliases(existing, incoming)
        assert "三叔" in merged["aliases"]

    def test_folds_incoming_aliases(self) -> None:
        existing = {"name": "王守真", "aliases": ["三叔"]}
        incoming = {"name": "王守真", "aliases": ["守真叔", "小王"]}
        merged = merge_character_with_aliases(existing, incoming)
        assert merged["aliases"] == ["三叔", "守真叔", "小王"]

    def test_deduplicates_aliases(self) -> None:
        existing = {"name": "王守真", "aliases": ["三叔"]}
        incoming = {"name": "三叔", "aliases": ["三叔", "三叔"]}
        merged = merge_character_with_aliases(existing, incoming)
        assert merged["aliases"] == ["三叔"]

    def test_does_not_add_existing_name_to_aliases(self) -> None:
        existing = {"name": "王守真"}
        incoming = {"name": "王守真"}
        merged = merge_character_with_aliases(existing, incoming)
        assert merged.get("aliases", []) == []

    def test_fills_empty_scalar_fields(self) -> None:
        existing = {"name": "王守真", "role": "protagonist", "description": ""}
        incoming = {"name": "三叔", "description": "中年男子"}
        merged = merge_character_with_aliases(existing, incoming)
        assert merged["description"] == "中年男子"

    def test_preserves_existing_non_empty_scalar(self) -> None:
        existing = {"name": "王守真", "description": "原有描述"}
        incoming = {"name": "三叔", "description": "新描述"}
        merged = merge_character_with_aliases(existing, incoming)
        assert merged["description"] == "原有描述"

    def test_deep_merge_nested_dict_hole_fill(self) -> None:
        existing = {
            "name": "王守真",
            "metadata": {"age": 45, "hometown": ""},
        }
        incoming = {
            "name": "三叔",
            "metadata": {"age": 50, "hometown": "云河镇"},
        }
        merged = merge_character_with_aliases(existing, incoming)
        # Preserves existing age, fills empty hometown
        assert merged["metadata"]["age"] == 45
        assert merged["metadata"]["hometown"] == "云河镇"

    def test_does_not_mutate_inputs(self) -> None:
        existing = {"name": "王守真", "aliases": ["三叔"]}
        incoming = {"name": "守真叔"}
        existing_snapshot = dict(existing)
        existing_aliases_snapshot = list(existing["aliases"])
        merge_character_with_aliases(existing, incoming)
        assert existing == existing_snapshot
        assert existing["aliases"] == existing_aliases_snapshot

    def test_non_mapping_incoming_returns_existing_copy(self) -> None:
        existing = {"name": "王守真", "role": "protagonist"}
        merged = merge_character_with_aliases(existing, None)  # type: ignore[arg-type]
        assert merged == existing
        assert merged is not existing  # deep-copied

    def test_non_mapping_existing_raises(self) -> None:
        with pytest.raises(TypeError):
            merge_character_with_aliases(None, {"name": "王守真"})  # type: ignore[arg-type]

    def test_skips_empty_incoming_values(self) -> None:
        existing = {"name": "王守真"}
        incoming = {"name": "三叔", "description": "", "notes": []}
        merged = merge_character_with_aliases(existing, incoming)
        assert "description" not in merged or merged["description"] == ""
        assert "notes" not in merged or merged["notes"] == []
