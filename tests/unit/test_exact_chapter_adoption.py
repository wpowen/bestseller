"""Exact adoption uses real SQLite rows, paid-preview stubs and production gates."""

from copy import deepcopy
import hashlib
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.schema import CreateTable
from test_adopted_knowledge_resume_control_integrity import configured as configured
from test_chapter_continuity_admission import response
from test_fanqie_segment_resume import forbid

from bestseller.infra.db.models import (
    ChapterDraftVersionModel,
    ChapterModel,
    DraftPromotionDecisionModel,
    QualityScoreModel,
)
from bestseller.services import chapter_continuity_admission as admission
from bestseller.services import continuity, draft_promotion, pipelines, quality_gates_config
from bestseller.services.adopted_chapter_source import select_adopted_chapter_source
from bestseller.services.reviews import chapter_review_body_fingerprint

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


def body_hash(draft):
    return hashlib.sha256(draft.content_md.encode("utf-8")).hexdigest()


@pytest.fixture
async def candidates(configured, monkeypatch):
    session, settings, project, chapter, a = configured
    connection = await session.connection()
    ddl = str(CreateTable(DraftPromotionDecisionModel.__table__).compile(
        dialect=connection.dialect,
    ))
    await connection.exec_driver_sql(ddl.replace("::jsonb", ""))
    a.promotion_state = "under_review"
    a.is_current = False
    b = ChapterDraftVersionModel(
        project_id=project.id, chapter_id=chapter.id, version_no=2,
        content_md="阿青把新钥匙交给守门人。", promotion_state="under_review",
        is_current=True, promotion_metadata={},
    )
    session.add(b)
    await session.flush()
    for draft, score in ((a, .99), (b, .90)):
        session.add(QualityScoreModel(
            project_id=project.id, target_type="chapter", target_id=chapter.id,
            chapter_draft_version_id=draft.id, judge_key="offline-exact",
            score_overall=score, score_goal=score, score_conflict=score,
            score_emotion=score, score_style=score, is_current=draft is b,
            evidence_summary={"hard_gates_passed": True},
        ))
    await session.commit()
    config = SimpleNamespace(phase_d=SimpleNamespace(
        enabled=True, countdown_arithmetic_enabled=True, regression_check_enabled=True,
    ))
    monkeypatch.setattr(quality_gates_config, "get_quality_gates_config", lambda: config)
    monkeypatch.setattr(pipelines, "get_quality_gates_config", lambda: config)
    monkeypatch.setattr(admission, "complete_text", forbid)
    return session, settings, project, chapter, a, b


async def admit(candidates, monkeypatch):
    session, settings, project, chapter, _, b = candidates

    async def complete(*args, **kwargs):
        assert not session.in_nested_transaction()
        return response()

    # Exercise the actual receipt producer, including its per-source ledger.
    # Only the external completion is replaced; no receipt fields are fabricated.
    with monkeypatch.context() as patch:
        patch.setattr(admission, "complete_text", complete)
        await pipelines._ensure_chapter_continuity_before_adoption(
            session, settings, project_id=project.id, chapter=chapter, draft=b,
            workflow_run_id=None,
        )
    retained = b.promotion_metadata[admission.PREVIEW_KEY]
    receipt = chapter.metadata_json["continuity_admission"]
    assert receipt["passed"] is True
    assert receipt["draft_id"] == str(b.id)
    assert receipt["body_sha256"] == chapter_review_body_fingerprint(b.content_md)["digest"]
    assert receipt["snapshot_dependencies"] == retained["snapshot_dependencies"]
    assert receipt["preview_raw_sha256"] == retained["raw_sha256"]
    assert chapter.metadata_json["continuity_admission_by_source"][
        f"{b.id}:{receipt['body_sha256']}"
    ] == receipt
    await session.commit()


async def promote(candidates, *, exact=True, entry="chapter", **overrides):
    session, _, project, chapter, _, b = candidates
    kwargs = {"project_id": project.id, "judge_key": "offline-exact"}
    if exact:
        kwargs.update(expected_draft_id=b.id, expected_body_sha256=body_hash(b))
    kwargs.update(overrides)
    if entry == "best":
        return await draft_promotion.promote_best_draft(
            session, draft_kind="chapter", parent_id=chapter.id, **kwargs,
        )
    return await draft_promotion.promote_chapter_draft(session, chapter_id=chapter.id, **kwargs)


async def assert_no_promotion(candidates):
    session, _, _, _, a, b = candidates
    assert a.promotion_state == b.promotion_state == "under_review"
    assert not list(await session.scalars(select(DraftPromotionDecisionModel)))


@pytest.mark.parametrize("entry", ["chapter", "best"])
async def test_checked_b_wins_over_higher_scoring_unchecked_a(candidates, monkeypatch, entry):
    await admit(candidates, monkeypatch)
    session, _, project, chapter, a, b = candidates
    outcome = await promote(candidates, entry=entry)
    assert outcome.changed and outcome.promoted_draft_id == b.id
    assert a.promotion_state == "under_review"
    assert admission.PREVIEW_KEY not in a.promotion_metadata
    decisions = list(await session.scalars(select(DraftPromotionDecisionModel)))
    assert decisions and {row.chapter_draft_version_id for row in decisions} == {b.id}
    await session.commit()
    selected, _ = await select_adopted_chapter_source(session, chapter=chapter, lock=False)
    assert selected.id == b.id
    # Actual adopted knowledge consumes the checked bytes without a second call.
    snapshot = await continuity.extract_chapter_state_snapshot(
        session, candidates[1], project_id=project.id, chapter=chapter,
        chapter_md=b.content_md, source_chapter_draft_version_id=b.id,
    )
    assert snapshot.extraction_status == "ok_promoted"
    assert snapshot.facts["snapshot_contract"]["pre_adoption_preview_reused"]


async def test_original_api_still_chooses_higher_scoring_a_without_receipts(candidates):
    session, _, _, _, a, b = candidates
    outcome = await promote(candidates, exact=False)
    assert outcome.changed and outcome.promoted_draft_id == a.id
    assert a.promotion_state == "promoted" and b.promotion_state == "under_review"
    assert {row.chapter_draft_version_id for row in await session.scalars(
        select(DraftPromotionDecisionModel)
    )} == {a.id}


async def test_exact_body_changed_in_database_rejects_stale_identity_map(candidates, monkeypatch):
    await admit(candidates, monkeypatch)
    session, _, _, _, _, b = candidates
    expected_hash = body_hash(b)
    factory = async_sessionmaker(session.bind, expire_on_commit=False)
    async with factory() as operator:
        changed = await operator.get(ChapterDraftVersionModel, b.id)
        changed.content_md += "后来门被锁上。"
        await operator.commit()
    assert body_hash(b) == expected_hash
    with pytest.raises(ValueError, match="exact_promotion_body_changed"):
        await promote(candidates, expected_body_sha256=expected_hash)
    assert body_hash(b) != expected_hash
    await assert_no_promotion(candidates)


async def test_exact_no_longer_current_rejects_without_falling_back(candidates, monkeypatch):
    await admit(candidates, monkeypatch)
    session, _, _, _, a, b = candidates
    a.is_current = True
    b.is_current = False
    await session.commit()
    with pytest.raises(ValueError, match="exact_promotion_source_changed"):
        await promote(candidates)
    await assert_no_promotion(candidates)


@pytest.mark.parametrize("field", ["passed", "snapshot_dependencies", "preview_raw_sha256"])
async def test_missing_or_failed_gate_receipt_cannot_adopt(candidates, monkeypatch, field):
    await admit(candidates, monkeypatch)
    session, _, _, chapter, _, _ = candidates
    metadata = deepcopy(chapter.metadata_json)
    receipt = metadata["continuity_admission"]
    source_key = f"{receipt['draft_id']}:{receipt['body_sha256']}"
    receipt.pop(field)
    metadata["continuity_admission_by_source"][source_key].pop(field, None)
    chapter.metadata_json = metadata
    await session.commit()
    with pytest.raises(ValueError, match="chapter_promotion_continuity_receipt_"):
        await promote(candidates)
    await assert_no_promotion(candidates)


async def test_changed_predecessor_source_invalidates_preview_dependencies(candidates, monkeypatch):
    session, settings, project, chapter, _, _ = candidates
    chapter.chapter_number = 2
    previous = ChapterModel(
        project_id=project.id, chapter_number=1, chapter_goal="前章事件", metadata_json={},
    )
    session.add(previous)
    await session.flush()
    prior_draft = ChapterDraftVersionModel(
        project_id=project.id, chapter_id=previous.id, version_no=1,
        content_md="前章发生的事件。", promotion_state="promoted", is_current=True,
    )
    session.add(prior_draft)
    await session.commit()
    async def complete(*args, **kwargs):
        return response()
    with monkeypatch.context() as patch:
        patch.setattr(continuity, "complete_text", complete)
        await continuity.extract_chapter_state_snapshot(
            session, settings, project_id=project.id, chapter=previous,
            chapter_md=prior_draft.content_md, source_chapter_draft_version_id=prior_draft.id,
        )
    await session.commit()
    await admit(candidates, monkeypatch)
    prior_draft.content_md += "前章来源已改变。"
    await session.commit()
    with pytest.raises(ValueError, match="chapter_promotion_continuity_receipt_stale"):
        await promote(candidates)
    await assert_no_promotion(candidates)


async def test_exact_ineligible_b_never_promotes_eligible_a(candidates, monkeypatch):
    await admit(candidates, monkeypatch)
    session, _, _, _, _, b = candidates
    score = await session.scalar(select(QualityScoreModel).where(
        QualityScoreModel.chapter_draft_version_id == b.id,
    ))
    score.evidence_summary = {"hard_gates_passed": False}
    await session.commit()
    outcome = await promote(candidates)
    assert not outcome.changed and outcome.promoted_draft_id is None
    await assert_no_promotion(candidates)


async def test_exact_preserves_higher_scoring_incumbent(candidates, monkeypatch):
    await admit(candidates, monkeypatch)
    session, _, _, _, a, b = candidates
    a.promotion_state = "promoted"
    await session.commit()
    outcome = await promote(candidates)
    assert not outcome.changed and outcome.reason == "incumbent_is_better_or_unscored"
    assert outcome.promoted_draft_id == a.id
    assert a.promotion_state == "promoted" and b.promotion_state == "under_review"
    assert not list(await session.scalars(select(DraftPromotionDecisionModel)))


@pytest.mark.parametrize("field", ["expected_draft_id", "expected_body_sha256"])
async def test_partial_exact_binding_is_rejected(candidates, field):
    with pytest.raises(ValueError, match="exact_promotion_requires_draft_and_body"):
        await promote(candidates, **{field: None})
    await assert_no_promotion(candidates)


async def test_foreign_expected_id_cannot_select_local_candidate(candidates):
    with pytest.raises(ValueError, match="exact_promotion_source_changed"):
        await promote(candidates, expected_draft_id=uuid4())
    await assert_no_promotion(candidates)


async def test_disabled_phase_d_keeps_exact_body_binding(candidates, monkeypatch):
    monkeypatch.setattr(quality_gates_config, "get_quality_gates_config", lambda: SimpleNamespace(
        phase_d=SimpleNamespace(enabled=False),
    ))
    outcome = await promote(candidates)
    assert outcome.changed and outcome.promoted_draft_id == candidates[-1].id


@pytest.mark.parametrize("normalize_newlines", [False, True])
async def test_exact_hash_uses_raw_utf8_including_crlf(candidates, monkeypatch, normalize_newlines):
    session, _, _, _, _, b = candidates
    b.content_md += "\r\n门外传来脚步声。"
    await session.commit()
    await admit(candidates, monkeypatch)
    if normalize_newlines:
        with pytest.raises(ValueError, match="exact_promotion_body_changed"):
            await promote(candidates, expected_body_sha256=(
                chapter_review_body_fingerprint(b.content_md)["digest"]
            ))
        await assert_no_promotion(candidates)
    else:
        outcome = await promote(candidates)
        assert outcome.changed and outcome.promoted_draft_id == b.id


async def test_exact_replacement_works_inside_story_engine_savepoint(candidates, monkeypatch):
    await admit(candidates, monkeypatch)
    session, _, _, _, a, b = candidates
    a.promotion_state = "promoted"
    score = await session.scalar(select(QualityScoreModel).where(
        QualityScoreModel.chapter_draft_version_id == a.id,
    ))
    score.score_overall = .86
    score.score_goal = score.score_conflict = score.score_emotion = score.score_style = .86
    await session.commit()
    async with session.begin_nested():
        outcome = await promote(candidates)
        assert session.in_nested_transaction()
        assert outcome.changed and outcome.promoted_draft_id == b.id
        assert outcome.incumbent_draft_id == a.id
    assert a.promotion_state == "superseded" and b.promotion_state == "promoted"


async def test_exact_reads_its_source_receipt_not_latest_other_candidate(candidates, monkeypatch):
    await admit(candidates, monkeypatch)
    session, _, _, chapter, a, b = candidates
    receipt = deepcopy(chapter.metadata_json["continuity_admission"])
    chapter.metadata_json = {
        "continuity_admission_by_source": {
            f"{b.id}:{receipt['body_sha256']}": receipt,
        },
        "continuity_admission": {
            "draft_id": str(a.id),
            "body_sha256": chapter_review_body_fingerprint(a.content_md)["digest"],
            "passed": False,
        },
    }
    await session.commit()
    outcome = await promote(candidates)
    assert outcome.changed and outcome.promoted_draft_id == b.id
