from __future__ import annotations

import pytest

from bestseller.services.narrative_contracts import validate_foundation_identity_contract

pytestmark = pytest.mark.unit


def _character(name: str, role: str) -> dict[str, str]:
    return {
        "name": name,
        "role": role,
        "gender": "male",
        "pronoun_set_zh": "他",
        "pronoun_set_en": "he/him",
    }


def test_duplicate_protagonist_and_antagonist_names_are_blocking() -> None:
    report = validate_foundation_identity_contract(
        {
            "protagonist": _character("纪年", "protagonist"),
            "antagonist": _character("纪年", "antagonist"),
            "supporting_cast": [],
        }
    )

    assert report.blocks
    assert any(
        violation.code == "FOUNDATION_IDENTITY_ALIAS_COLLISION"
        for violation in report.violations
    )
