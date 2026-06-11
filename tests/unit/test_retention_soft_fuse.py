from __future__ import annotations

import pytest

from bestseller.services.pipelines import _block_codes_are_retention_only
from bestseller.services.retention_safety_gate import AUTO_REPAIR_RETENTION_CODES

pytestmark = pytest.mark.unit


def test_retention_only_codes_qualify_for_soft_fuse() -> None:
    assert _block_codes_are_retention_only(
        ("PERSONA_WEIGHTED_SCORE_LOW", "SIGNATURE_SCENE_MISSING")
    )


def test_deterministic_audit_retention_codes_qualify() -> None:
    # The 500ch run dead-ended on exactly these (ch9 terminal blockers).
    assert _block_codes_are_retention_only(
        ("SIGNATURE_IMAGE_MISSING", "ENDING_HOOK_MISSING")
    )


def test_structural_code_disqualifies_soft_fuse() -> None:
    assert not _block_codes_are_retention_only(
        ("PERSONA_WEIGHTED_SCORE_LOW", "CHAPTER_SPLICE_TIME_JUMP")
    )


def test_empty_codes_do_not_qualify() -> None:
    assert not _block_codes_are_retention_only(())


def test_every_official_retention_code_qualifies() -> None:
    assert _block_codes_are_retention_only(tuple(AUTO_REPAIR_RETENTION_CODES))
