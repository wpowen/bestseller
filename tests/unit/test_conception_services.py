from __future__ import annotations

import json

import pytest

from bestseller.services import conception as conception_services


pytestmark = pytest.mark.unit


def test_ensure_complete_profile_uses_english_defaults_for_english_projects() -> None:
    profile = conception_services._ensure_complete_profile(
        {},
        {
            "genre": "Fantasy",
            "sub_genre": "Epic Fantasy",
            "language": "en-US",
            "existing_overrides": {},
        },
        {},
        {},
        {},
    )

    assert profile["serialization"]["opening_mandate"].startswith("Reveal the protagonist edge")
    assert profile["serialization"]["chapter_ending_rule"].startswith("Every chapter ends")
    assert "前3章" not in profile["serialization"]["opening_mandate"]


def test_build_fallback_final_uses_english_premise_and_profile_defaults() -> None:
    payload = json.loads(
        conception_services._build_fallback_final(
            {
                "genre": "Fantasy",
                "sub_genre": "Epic Fantasy",
                "description": "A hunted archivist steals the ledger that can expose a dead dynasty.",
                "language": "en-US",
            },
            {},
            {},
            {},
        )
    )

    assert payload["premise"].startswith("A Fantasy (Epic Fantasy) novel:")
    assert payload["writing_profile"]["serialization"]["chapter_ending_rule"].startswith(
        "Every chapter"
    )
    assert "基于" not in payload["premise"]
