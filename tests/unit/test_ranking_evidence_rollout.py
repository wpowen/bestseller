from __future__ import annotations

import pytest

from bestseller.services.ranking_evidence_rollout import resolve_ranking_evidence_rollout
from bestseller.settings import PipelineSettings

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("mode", ["off", "shadow", "advisory", "canary", "required"])
def test_no_rollout_mode_can_disable_commercial_claim_authority(mode: str) -> None:
    decision = resolve_ranking_evidence_rollout(
        PipelineSettings(ranking_evidence_mode=mode),
        project_id="book-1",
        genre="异世界",
    )

    assert decision.require_commercial_claim is True


def test_canary_only_blocks_matching_project_or_genre() -> None:
    settings = PipelineSettings(
        ranking_evidence_mode="canary",
        ranking_evidence_canary_project_ids=["book-1"],
        ranking_evidence_canary_genres=["异世界经营"],
    )

    matched = resolve_ranking_evidence_rollout(settings, project_id="book-1")
    outside = resolve_ranking_evidence_rollout(settings, project_id="book-2", genre="悬疑")

    assert matched.block_canonical_promotion is True
    assert outside.block_canonical_promotion is False
    assert outside.show_blockers is True


def test_required_mode_collects_displays_and_blocks_promotion() -> None:
    decision = resolve_ranking_evidence_rollout(PipelineSettings(ranking_evidence_mode="required"))

    assert decision.collect_evidence is True
    assert decision.show_blockers is True
    assert decision.block_canonical_promotion is True
