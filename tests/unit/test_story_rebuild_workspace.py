from __future__ import annotations

import pytest

from bestseller.services.story_rebuild_workspace import (
    RebuildState,
    advance_workspace,
    assert_source_unchanged,
    create_shadow_workspace,
    cutover,
    rollback,
)

pytestmark = pytest.mark.unit


def _promotable_workspace():
    workspace = create_shadow_workspace("ws-1", "canonical-r1", {"title": "旧书"})
    for state in (
        RebuildState.ARCHITECTURE_VALIDATED,
        RebuildState.FOUNDATION_VALIDATED,
        RebuildState.OPENING_VALIDATED,
        RebuildState.PACKAGE_VALIDATED,
    ):
        workspace = advance_workspace(workspace, state)
    return advance_workspace(
        workspace,
        RebuildState.PROMOTABLE,
        candidate_revision="candidate-r2",
        candidate_hash="a" * 64,
    )


def test_failed_cutover_keeps_canonical_pointer_and_success_is_atomic() -> None:
    promotable = _promotable_workspace()

    failed = cutover(
        promotable,
        candidate_revision="candidate-r2",
        candidate_hash="a" * 64,
        checks_passed=False,
    )
    promoted = cutover(
        promotable,
        candidate_revision="candidate-r2",
        candidate_hash="a" * 64,
        checks_passed=True,
    )

    assert failed.state is RebuildState.PROMOTABLE
    assert failed.current_pointer == "canonical-r1"
    assert promoted.state is RebuildState.CANONICAL
    assert promoted.current_pointer == "candidate-r2"


def test_rollback_restores_source_pointer_and_source_mutation_is_rejected() -> None:
    promoted = cutover(
        _promotable_workspace(),
        candidate_revision="candidate-r2",
        candidate_hash="a" * 64,
        checks_passed=True,
    )

    restored = rollback(promoted, reason="canary_failed")

    assert restored.current_pointer == "canonical-r1"
    assert restored.state is RebuildState.ROLLED_BACK
    with pytest.raises(ValueError, match="SOURCE_REVISION_MUTATED"):
        assert_source_unchanged(restored, {"title": "被原地修改"})


def test_promotable_requires_a_frozen_candidate_revision_and_hash() -> None:
    workspace = create_shadow_workspace("ws-2", "canonical-r1", {})
    for state in (
        RebuildState.ARCHITECTURE_VALIDATED,
        RebuildState.FOUNDATION_VALIDATED,
        RebuildState.OPENING_VALIDATED,
        RebuildState.PACKAGE_VALIDATED,
    ):
        workspace = advance_workspace(workspace, state)

    with pytest.raises(ValueError, match="PROMOTION_REQUIRES_FROZEN_CANDIDATE"):
        advance_workspace(workspace, RebuildState.PROMOTABLE)
