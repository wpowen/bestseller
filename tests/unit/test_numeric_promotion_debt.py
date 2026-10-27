from types import SimpleNamespace
from uuid import uuid4

import pytest

from bestseller.services import draft_promotion as dp
from bestseller.services.reviews import chapter_review_body_fingerprint


def evidence():
    draft = SimpleNamespace(id=uuid4(), is_current=True, content_md="真实的当前正文。")
    score = SimpleNamespace(
        id=uuid4(),
        chapter_draft_version_id=draft.id,
        judge_key="chapter_quality_v1",
        is_current=True,
        review_report_id=uuid4(),
        score_overall=0.59,
        score_goal=0.77,
        score_conflict=0.90,
        score_emotion=0.83,
        score_style=0.80,
        evidence_summary={"hard_gates_passed": True, "blocking_codes": []},
    )
    report = SimpleNamespace(
        id=score.review_report_id,
        structured_output={
            "draft_id": str(draft.id),
            "reviewed_body_fingerprint": chapter_review_body_fingerprint(draft.content_md),
        },
    )
    return draft, score, report


def test_numeric_refusal_has_exact_deficits_without_promotion():
    draft, score, report = evidence()
    receipt = dp.numeric_promotion_debt_receipt(draft, score, report)
    assert receipt["draft_id"] == str(draft.id)
    assert receipt["quality_score_id"] == str(score.id)
    assert receipt["core_overall"] == pytest.approx(0.825)
    assert receipt["below_core_axes"] == ["score_goal"]
    assert receipt["reason_codes"] == ["CORE_OVERALL_BELOW_THRESHOLD", "CORE_AXIS_BELOW_THRESHOLD"]


@pytest.mark.parametrize(
    "failure",
    [
        "stale_draft",
        "stale_body",
        "stale_report",
        "no_judge",
        "hard_fail",
        "blockers",
        "missing_axis",
        "nan",
        "not_current",
        "empty_body",
        "eligible",
    ],
)
def test_numeric_receipt_rejects_every_other_refusal(failure):
    draft, score, report = evidence()
    if failure == "stale_draft":
        score.chapter_draft_version_id = uuid4()
    if failure == "stale_body":
        report.structured_output["reviewed_body_fingerprint"] = {}
    if failure == "stale_report":
        report.id = uuid4()
    if failure == "no_judge":
        score.judge_key = ""
    if failure == "hard_fail":
        score.evidence_summary["hard_gates_passed"] = False
    if failure == "blockers":
        score.evidence_summary["blocking_codes"] = ["CANON_CONFLICT"]
    if failure == "missing_axis":
        score.score_goal = None
    if failure == "nan":
        score.score_goal = float("nan")
    if failure == "not_current":
        draft.is_current = False
    if failure == "empty_body":
        draft.content_md = "  "
    if failure == "eligible":
        score.score_goal = 0.95
    assert dp.numeric_promotion_debt_receipt(draft, score, report) is None


@pytest.mark.parametrize("changed", ["none", "draft", "body", "quality", "report"])
async def test_durable_consumer_revalidates_all_bindings(changed):
    from unittest.mock import AsyncMock

    from bestseller.services import pipelines

    draft, score, report = evidence()
    draft.chapter_id = uuid4()
    receipt = dp.numeric_promotion_debt_receipt(draft, score, report)
    if changed == "draft":
        draft.is_current = False
    if changed == "body":
        draft.content_md += "正文后来改过。"
    if changed == "quality":
        score.is_current = False
    if changed == "report":
        report.structured_output["draft_id"] = str(uuid4())
    rows = {draft.id: draft, score.id: score, report.id: report}
    session = SimpleNamespace(get=AsyncMock(side_effect=lambda model, key: rows.get(key)))
    assert await pipelines._numeric_promotion_debt_binding_is_current(
        session, draft.chapter_id, {"numeric_promotion_debt": receipt}
    ) is (changed == "none")


@pytest.mark.parametrize(
    "active",
    [
        {},
        {"blocked_by_unknown_gate": True},
        {"blocked_by_material_referential_integrity_gate": True},
        {"production_block_code": "UNKNOWN"},
        {"blocked_by_chapter_outline_readiness_gate": True},
        {"blocked_by_write_safety_gate": True},
    ],
)
async def test_three_consumers_numeric_receipt_and_active_blockers(monkeypatch, active):
    from unittest.mock import AsyncMock

    from bestseller.services import pipelines
    from bestseller.services.gate_registry import chapter_local_debt_allows_continuation

    draft, score, report = evidence()
    receipt = dp.numeric_promotion_debt_receipt(draft, score, report)
    metadata = {
        "numeric_promotion_debt": receipt,
        "chapter_quality_debt": True,
        "chapter_quality_debt_reason": "chapter_promotion_ineligible",
        "retention_retry_last_block_codes": ["CHAPTER_TOO_SHORT"],
        "auto_repair_last_resolved_block_codes": ["LENGTH_OVER"],
        **active,
    }
    allowed = not active
    assert (
        chapter_local_debt_allows_continuation(
            metadata, has_current_draft=True, numeric_debt_binding_verified=True
        )
        is allowed
    )
    assert not chapter_local_debt_allows_continuation(metadata, has_current_draft=True)
    chapter = SimpleNamespace(
        id=uuid4(),
        project_id=uuid4(),
        chapter_number=1,
        status="revision",
        production_state="quality_debt",
        metadata_json=metadata,
    )
    monkeypatch.setattr(
        pipelines, "_numeric_promotion_debt_binding_is_current", AsyncMock(return_value=True)
    )
    from bestseller.services import adopted_chapter_source, adopted_knowledge_resume
    # These tests exercise dispatch of an already verified receipt. Exact source
    # and knowledge validation have real DB coverage in the lineage suite.
    monkeypatch.setattr(adopted_chapter_source, "select_adopted_chapter_source",
                        AsyncMock(return_value=(draft, receipt) if allowed else (None, None)))
    monkeypatch.setattr(adopted_knowledge_resume, "read_adopted_knowledge",
                        AsyncMock(return_value=({}, {"closed": True})))
    session = SimpleNamespace(
        scalars=AsyncMock(return_value=[chapter.id]),
        scalar=AsyncMock(return_value=chapter.id),
        execute=AsyncMock(side_effect=[
            [(chapter.id, draft.content_md)],
            SimpleNamespace(all=lambda: [(1, "revision", "quality_debt", metadata, 1)]),
        ]),
    )
    pending, _ = await pipelines._select_pending_chapters_for_resume(
        session, [chapter], resume_enabled=True, accept_on_stall=False
    )
    assert (pending == []) is allowed
    prior = await pipelines._load_prior_incomplete_chapter_numbers(
        session, project_id=uuid4(), before_chapter_number=2
    )
    assert (prior == []) is allowed
    result = SimpleNamespace(requires_human_review=True)
    assert (
        pipelines._chapter_requires_sequence_stop(
            chapter,
            result,
            SimpleNamespace(quality=SimpleNamespace(draft_mode=False)),
            has_current_draft=True,
            numeric_debt_binding_verified=True,
        )
        is not allowed
    )


async def test_mark_eligible_raises_bound_refusal_without_transition(monkeypatch):
    from unittest.mock import AsyncMock

    from bestseller.infra.db.models import ChapterDraftVersionModel, QualityScoreModel

    draft, score, _ = evidence()
    actual_draft = ChapterDraftVersionModel(id=draft.id, version_no=3)
    actual_score = QualityScoreModel(**vars(score))
    session = SimpleNamespace(
        execute=AsyncMock(
            return_value=SimpleNamespace(one_or_none=lambda: (actual_draft, actual_score))
        )
    )
    transition = AsyncMock()
    monkeypatch.setattr(dp, "transition_draft_state", transition)
    with pytest.raises(dp.DraftQualityIneligibleError) as caught:
        await dp.mark_draft_eligible(
            session,
            project_id=uuid4(),
            draft_kind="chapter",
            draft_id=draft.id,
            quality_score_id=score.id,
        )
    assert caught.value.draft_id == draft.id
    assert caught.value.quality_score_id == score.id
    transition.assert_not_called()


@pytest.mark.parametrize("changed", [False, True])
async def test_pipeline_catch_to_durable_closure_rejects_swapped_draft(changed, monkeypatch):
    import inspect
    import textwrap
    from unittest.mock import AsyncMock

    from bestseller.services import pipelines

    draft, score, report = evidence()
    workflow = SimpleNamespace(metadata_json={})
    chapter = SimpleNamespace(metadata_json={})
    source = inspect.getsource(pipelines.run_chapter_pipeline)
    start = source.index('                    chapter_source_mode = "chapter_promotion_ineligible"')
    end = source.index(
        '                    if _pipeline_quality_mode(settings) == "strict":', start
    )
    namespace = dict(vars(pipelines))
    namespace.update(
        chapter_draft=draft,
        chapter_quality=score,
        chapter_report=report,
        workflow_run=workflow,
        chapter=chapter,
        exc=dp.DraftQualityIneligibleError(draft.id, score.id),
    )
    exec(textwrap.dedent(source[start:end]), namespace)
    assert workflow.metadata_json["numeric_promotion_debt"]
    if changed:
        draft.id = uuid4()
    session = SimpleNamespace(flush=AsyncMock())
    workflow.id = uuid4()
    chapter.project_id = uuid4()
    admission = AsyncMock()
    monkeypatch.setattr(pipelines, "_ensure_chapter_continuity_before_adoption", admission)
    await pipelines._persist_chapter_numeric_adoption(
        session, chapter=chapter, draft=draft, quality=score, report=report, workflow=workflow,
        settings=SimpleNamespace(),
    )
    assert admission.await_count == (0 if changed else 1)
    session.flush.assert_awaited_once()
    assert bool(chapter.metadata_json["numeric_promotion_debt"]) is not changed
    assert chapter.metadata_json["chapter_quality_debt"] is True


@pytest.mark.parametrize("error", [ValueError("Draft not found"), RuntimeError("invalid state")])
def test_generic_promotion_failure_cannot_borrow_old_local_gate(error):
    import inspect
    import textwrap

    from bestseller.services import pipelines
    from bestseller.services.gate_registry import chapter_local_debt_allows_continuation

    draft, score, report = evidence()
    workflow = SimpleNamespace(metadata_json={})
    chapter = SimpleNamespace(metadata_json={"blocked_by_fanqie_long_ranking_gate": True})
    source = inspect.getsource(pipelines.run_chapter_pipeline)
    start = source.index('                    chapter_source_mode = "chapter_promotion_ineligible"')
    end = source.index(
        '                    if _pipeline_quality_mode(settings) == "strict":', start
    )
    namespace = dict(vars(pipelines))
    namespace.update(
        chapter_draft=draft,
        chapter_quality=score,
        chapter_report=report,
        workflow_run=workflow,
        chapter=chapter,
        exc=error,
        requires_human_review=False,
    )
    exec(textwrap.dedent(source[start:end]), namespace)
    assert namespace["requires_human_review"] is True
    assert chapter.metadata_json["blocked_by_chapter_promotion_evidence_gate"] is True
    assert not chapter_local_debt_allows_continuation(chapter.metadata_json, has_current_draft=True)
