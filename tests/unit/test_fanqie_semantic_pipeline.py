from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from bestseller.services import fanqie_long_semantic_review as semantic
from bestseller.services import pipelines


@pytest.mark.asyncio
@pytest.mark.parametrize("blocked", [False, True])
async def test_semantic_evidence_controls_keyword_veto_without_changing_report(
    monkeypatch, blocked
):
    finding = {
        "code": "first_100_pressure_missing",
        "severity": "critical",
        "target": "opening.first_100",
    }
    artifact = SimpleNamespace(id=uuid4(), content={"passed": False, "findings": [finding]})
    monkeypatch.setattr(
        pipelines, "load_current_chapter_texts_for_fanqie_gate", AsyncMock(return_value={})
    )
    monkeypatch.setattr(
        pipelines, "evaluate_and_persist_fanqie_long_readiness", AsyncMock(return_value=artifact)
    )
    review = semantic.FanqieLongSemanticReviewReport(
        blocking_findings=[
            semantic.FanqieSemanticFindingReview(code=finding["code"], verdict="uncertain")
        ]
        if blocked
        else [],
        called=True,
    )
    reviewer = AsyncMock(return_value=review)
    monkeypatch.setattr(semantic, "review_fanqie_long_semantic_findings", reviewer)
    draft = SimpleNamespace(id=uuid4(), content_md="还剩十五分钟。")
    chapter = SimpleNamespace(metadata_json={"fanqie_long_ranking_block_attempts": 99})
    settings = SimpleNamespace()
    payload = await pipelines._run_fanqie_long_gate_for_chapter(
        None,
        project=SimpleNamespace(metadata_json={}),
        project_slug="different-book",
        chapter_number=1,
        chapter_draft=draft,
        chapter=chapter,
        block_on_failure=True,
        settings=settings,
    )
    assert payload["passed"] is False
    assert payload["findings"] == [finding]
    assert payload["blocks_write"] is blocked
    assert payload["block_attempts_demoted"] is False
    assert payload["chapter_draft_id"] == str(draft.id)
    assert reviewer.call_args.kwargs["chapter_texts"] == {1: draft.content_md}
    assert chapter.metadata_json["fanqie_long_ranking_block_attempts"] == 99


@pytest.mark.parametrize(
    ("target", "chapter_number", "applies"),
    [
        ("opening.first_100", 2, False),
        ("chapters.1", 2, False),
        ("opening.first_100", 1, True),
        ("chapters.2", 2, True),
        ("chapters.exposition_streak", 2, True),
        ("unknown", 2, True),
        ("chapters.3", 2, True),
    ],
)
def test_prior_local_findings_do_not_veto_later_chapters(target, chapter_number, applies):
    assert (
        pipelines._fanqie_finding_applies_to_chapter({"target": target}, chapter_number) is applies
    )


@pytest.mark.asyncio
async def test_prior_opening_failure_stays_in_report_but_does_not_block_chapter_two(monkeypatch):
    finding = {
        "code": "first_100_pressure_missing",
        "severity": "critical",
        "target": "opening.first_100",
    }
    monkeypatch.setattr(
        pipelines,
        "load_current_chapter_texts_for_fanqie_gate",
        AsyncMock(return_value={1: "原首章"}),
    )
    monkeypatch.setattr(
        pipelines,
        "evaluate_and_persist_fanqie_long_readiness",
        AsyncMock(
            return_value=SimpleNamespace(
                id=uuid4(), content={"passed": False, "findings": [finding]}
            )
        ),
    )
    reviewer = AsyncMock(return_value=semantic.FanqieLongSemanticReviewReport(called=False))
    monkeypatch.setattr(semantic, "review_fanqie_long_semantic_findings", reviewer)
    payload = await pipelines._run_fanqie_long_gate_for_chapter(
        None,
        settings=SimpleNamespace(),
        project=SimpleNamespace(metadata_json={}),
        project_slug="another-book",
        chapter_number=2,
        chapter_draft=SimpleNamespace(id=uuid4(), content_md="第二章"),
        block_on_failure=True,
    )
    assert payload["passed"] is False
    assert payload["findings"] == [finding]
    assert payload["blocks_write"] is False
    assert reviewer.call_args.kwargs["findings"] == []
