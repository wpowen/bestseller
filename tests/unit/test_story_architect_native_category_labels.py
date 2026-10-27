"""Category-native labels must not force realistic architects into fallback."""

import pytest

from bestseller.domain.facets import StoryFacets
from bestseller.services.genre_intent_contract import contract_from_selection
from bestseller.services.story_architect import (
    _story_facets_default_motif_findings,
    _story_facets_ontology_findings,
)


def _contract(genre="realistic", sub_genre="slice-realistic", channel="general"):
    return contract_from_selection(
        {"channel": channel, "genre": genre, "sub_genre": sub_genre, "tags": []}
    )


def _findings(contract, labels):
    return _story_facets_default_motif_findings(
        StoryFacets(primary_genre=contract.genre_key, sub_genres=labels),
        genre_intent=contract,
        user_hints={},
    )


def test_realistic_city_workplace_labels_are_native_without_user_seed():
    # Reproduces the two-label rejection observed in the real architect run.
    contract = _contract()
    assert contract.category_key == "urban-contemporary"
    assert contract.allowed_modernity == "modern"
    assert "都市" not in contract.tags and "职场" not in contract.tags
    assert _findings(contract, ("都市生活", "职场成长")) == []


@pytest.mark.parametrize("label", ["末世求生", "科幻", "悬疑推理", "都市犯罪"])
def test_native_city_label_does_not_authorize_external_genre(label):
    findings = _findings(_contract(), (label,))
    assert [item.code for item in findings] == ["STORY_FACETS_UNREQUESTED_GENRE_FUSION"]


def test_other_modern_category_does_not_inherit_workplace_authority():
    contract = _contract("realistic", "family-saga")
    assert contract.allowed_modernity == "modern"
    assert contract.category_key == "relationship-driven"
    assert _findings(contract, ("职场成长",))


def test_native_fantasy_still_rejects_city_labels_and_modern_ontology():
    contract = _contract("xuanhuan", "otherworld-continent", "male")
    assert _findings(contract, ("都市生活", "职场成长"))
    facets = StoryFacets(primary_genre="xuanhuan", setting="主角在写字楼用手机工作")
    assert [item.code for item in _story_facets_ontology_findings(facets, contract)] == [
        "STORY_FACETS_ONTOLOGY_MISMATCH"
    ]
