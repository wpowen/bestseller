from pathlib import Path

from bestseller.services.intent_alignment import (
    intent_tag_source_from_contract,
    intent_tags_from_contract,
)


def test_split_contract_with_empty_user_tags_does_not_promote_defaults():
    contract = {
        "genre_intent": {
            "tag_source": "user",
            "tags": ["废柴逆袭", "升级流", "血脉觉醒"],
            "default_tags": ["废柴逆袭", "升级流", "血脉觉醒"],
            "user_tags": [],
        }
    }

    assert intent_tags_from_contract(contract) == []
    assert intent_tag_source_from_contract(contract) == "none"


def test_split_contract_keeps_only_explicit_user_tags():
    contract = {
        "genre_intent": {
            "tag_source": "user",
            "tags": ["废柴逆袭", "升级流", "血脉觉醒", "复仇"],
            "default_tags": ["废柴逆袭", "升级流", "血脉觉醒"],
            "user_tags": ["复仇"],
        }
    }

    assert intent_tags_from_contract(contract) == ["复仇"]
    assert intent_tag_source_from_contract(contract) == "user"


def test_legacy_merged_tags_remain_compatible_but_are_inferred():
    contract = {
        "genre_intent": {
            "tag_source": "legacy_inferred",
            "tags": ["废柴逆袭", "升级流"],
        }
    }

    assert intent_tags_from_contract(contract) == ["废柴逆袭", "升级流"]
    assert intent_tag_source_from_contract(contract) == "legacy_inferred"


def test_legacy_merged_tags_without_source_are_still_legacy_shape():
    """A pre-split payload has no provenance fields and remains compatible."""
    contract = {"genre_intent": {"tags": ["废柴逆袭"]}}

    assert "user_tags" not in contract["genre_intent"]
    assert "default_tags" not in contract["genre_intent"]
    assert intent_tags_from_contract(contract) == ["废柴逆袭"]
    assert intent_tag_source_from_contract(contract) == "legacy_inferred"


def test_late_alignment_uses_provenance_aware_helper_and_receipt_source():
    source = Path(__file__).parents[2] / "src/bestseller/services/conception.py"
    text = source.read_text(encoding="utf-8")

    assert "intent_tags_from_contract" in text
    assert '"taxonomy_tags_source": _intent_tag_source' in text
    assert "intent_tag_source_from_contract" in text
