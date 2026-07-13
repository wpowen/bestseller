from __future__ import annotations

import pytest

from bestseller.services.genre_intent_contract import (
    build_genre_intent_contract,
    contract_from_selection,
    detect_genre_native_ontology_violations,
)
from bestseller.services.genre_taxonomy import resolve_selection
from bestseller.services.story_enhancers import StoryEnhancerSelection

pytestmark = pytest.mark.unit


def test_plain_xianxia_contract_owns_native_prompt_pack() -> None:
    contract = contract_from_selection(
        {"channel": "male", "genre": "xianxia", "sub_genre": None, "tags": []}
    )

    assert contract.genre_key == "xianxia"
    assert contract.sub_genre_key is None
    assert contract.prompt_pack_key == "xianxia-upgrade-core"
    assert contract.allowed_modernity == "genre_native"
    assert contract.explicit_enhancers.is_default()


def test_urban_cultivation_contract_explicitly_allows_modern_elements() -> None:
    contract = contract_from_selection(
        {
            "channel": "male",
            "genre": "xianxia",
            "sub_genre": "urban-cultivation",
            "tags": [],
        }
    )

    assert contract.sub_genre_key == "urban-cultivation"
    assert contract.prompt_pack_key == "urban-cultivation-2.0"
    assert contract.allowed_modernity == "modern"


def test_contract_hash_is_stable_and_changes_when_user_intent_changes() -> None:
    plain = contract_from_selection({"genre": "xianxia"})
    urban = contract_from_selection(
        {"genre": "xianxia", "sub_genre": "urban-cultivation"}
    )

    assert plain.contract_hash() == plain.contract_hash()
    assert plain.contract_hash() != urban.contract_hash()


def test_native_contract_detects_high_signal_modern_ontology_leakage() -> None:
    contract = contract_from_selection({"genre": "xianxia", "sub_genre": "xianxia"})
    assert detect_genre_native_ontology_violations("他拿出手机，走进写字楼。", contract)
    assert detect_genre_native_ontology_violations("他把尸体送进殡仪馆，再安排入殓。", contract)
    assert not detect_genre_native_ontology_violations("他在宗门广场拔剑。", contract)


def test_explicit_enhancers_are_preserved_but_not_inferred() -> None:
    selection = StoryEnhancerSelection(brainhole=True, effect_skills=("twist_reversal_engine",))
    contract = build_genre_intent_contract(
        resolve_selection(None, "xianxia", None, []),
        enhancers=selection,
    )

    assert contract.explicit_enhancers.brainhole is True
    assert contract.explicit_enhancers.effect_skills == ("twist_reversal_engine",)
