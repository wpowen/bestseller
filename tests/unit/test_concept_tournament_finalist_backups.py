"""Regression tests for seriality finalist backup scheduling."""

from dataclasses import replace

import pytest

from bestseller.services.concept_tournament import (
    ConceptCandidate,
    _review_seriality_finalists_with_backups,
)

pytestmark = pytest.mark.unit


def _candidate(dimension: str, concept: str, composite: float) -> ConceptCandidate:
    return ConceptCandidate(dimension=dimension, concept=concept, composite=composite)


@pytest.mark.asyncio
async def test_failed_primary_can_promote_same_premise_backup() -> None:
    calls: list[str] = []
    contenders = [
        _candidate("A:primary", "A-primary", 10),
        _candidate("A:backup", "A-backup", 9),
    ]

    async def review(candidate: ConceptCandidate) -> ConceptCandidate:
        calls.append(candidate.concept)
        return (
            replace(candidate, rejected_reason="seriality failed")
            if candidate.concept == "A-primary"
            else candidate
        )

    reviewed = await _review_seriality_finalists_with_backups(
        contenders, finalist_count=2, review_candidate=review
    )

    assert calls == ["A-primary", "A-backup"]
    assert reviewed[-1].concept == "A-backup"
    assert reviewed[-1].rejected_reason is None


@pytest.mark.asyncio
async def test_passing_primary_does_not_consume_failed_premise_backup_budget() -> None:
    calls: list[str] = []
    contenders = [
        _candidate("A:primary", "A-primary", 10),
        _candidate("A:backup", "A-backup", 9),
        _candidate("B:primary", "B-primary", 8),
        _candidate("B:backup", "B-backup", 7),
    ]

    async def review(candidate: ConceptCandidate) -> ConceptCandidate:
        calls.append(candidate.concept)
        if candidate.concept == "B-primary":
            return replace(candidate, rejected_reason="seriality failed")
        return candidate

    reviewed = await _review_seriality_finalists_with_backups(
        contenders, finalist_count=3, review_candidate=review
    )

    assert calls == ["A-primary", "B-primary", "B-backup"]
    assert [candidate.concept for candidate in reviewed] == calls
    assert reviewed[-1].rejected_reason is None


@pytest.mark.asyncio
async def test_backup_reviews_never_exceed_finalist_budget() -> None:
    calls: list[str] = []
    contenders = [
        _candidate("A:primary", "A-primary", 10),
        _candidate("A:backup-1", "A-backup-1", 9),
        _candidate("A:backup-2", "A-backup-2", 8),
        _candidate("A:backup-3", "A-backup-3", 7),
    ]

    async def review(candidate: ConceptCandidate) -> ConceptCandidate:
        calls.append(candidate.concept)
        return replace(candidate, rejected_reason="seriality failed")

    reviewed = await _review_seriality_finalists_with_backups(
        contenders, finalist_count=3, review_candidate=review
    )

    assert len(calls) == 3
    assert len(reviewed) == 3
    assert calls == ["A-primary", "A-backup-1", "A-backup-2"]
