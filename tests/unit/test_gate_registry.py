from __future__ import annotations

import pytest

from bestseller.services.gate_registry import (
    project_resume_is_terminally_blocked,
    registered_block_metadata_keys,
    registered_gate_names,
)

pytestmark = pytest.mark.unit


def test_gate_registry_exposes_non_quality_block_keys() -> None:
    keys = set(registered_block_metadata_keys())

    assert "blocked_by_write_safety_gate" in keys
    assert "blocked_by_chapter_predraft_quality_gate" in keys
    assert "qimao_opening_gate_blocked" in keys


def test_gate_registry_marks_qimao_exhaustion_terminal() -> None:
    assert "qimao_opening_gate" in registered_gate_names()
    assert project_resume_is_terminally_blocked(
        {"qimao_opening_gate_exhausted": True}
    )
