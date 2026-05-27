from __future__ import annotations

import pytest

from bestseller.services.quality_contract_registry import (
    UNKNOWN_COMMERCIAL_BLOCK_CONTRACT,
    contract_for_code,
    is_registered_quality_code,
)
from bestseller.settings import load_settings

pytestmark = pytest.mark.unit


def test_every_auto_repair_code_has_quality_contract() -> None:
    settings = load_settings(env={})

    missing = [
        code
        for code in settings.pipeline.chapter_auto_repair_repairable_codes
        if not is_registered_quality_code(code)
    ]

    assert missing == []


def test_unknown_code_fails_closed_in_commercial_strict_mode() -> None:
    contract = contract_for_code("NEW_UNMAPPED_CODE", commercial_strict=True)

    assert contract == UNKNOWN_COMMERCIAL_BLOCK_CONTRACT
    assert contract.repairable is False
