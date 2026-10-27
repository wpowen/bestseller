"""Offline SQLite regressions through the production exact promotion API.

The shared fixture supplies real rows, real continuity receipt production and
real validation; only the external extraction response is synthetic.
"""

from copy import deepcopy
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified
from sqlalchemy.schema import CreateIndex, CreateTable
from test_exact_chapter_adoption import (
    admit,
    promote,
)
from test_exact_chapter_adoption import (
    candidates as candidates,
)
from test_exact_chapter_adoption import (
    configured as configured,
)

from bestseller.infra.db.models import (
    ChapterDraftVersionModel,
    DraftPromotionDecisionModel,
    QualityScoreModel,
    ReviewReportModel,
    SceneCardModel,
    SceneDraftVersionModel,
)
from bestseller.services.adopted_chapter_source import (
    choose_usable_promoted_chapter_source,
    continuity_source_key,
)
from bestseller.services.draft_promotion import promote_best_draft
from bestseller.services.reviews import chapter_review_body_fingerprint

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.fixture(autouse=True)
async def promotion_index(candidates):
    connection = await candidates[0].connection()
    index = next(
        index
        for index in ChapterDraftVersionModel.__table__.indexes
        if index.name == "uq_chapter_draft_promoted"
    )
    await connection.execute(CreateIndex(index))


def receipt(draft, passed=False):
    return {
        "draft_id": str(draft.id),
        "body_sha256": chapter_review_body_fingerprint(draft.content_md)["digest"],
        "passed": passed,
        "reason": "source_correction",
        "reports": [{"code": "COUNTDOWN_REGRESSION", "passed": passed}],
        "retained_detail": {"evidence": ["old exact source rejected"]},
    }


def bind(chapter, value, *, key=None):
    metadata = deepcopy(chapter.metadata_json)
    ledger = metadata.setdefault("continuity_admission_by_source", {})
    ledger[key or continuity_source_key(value)] = deepcopy(value)
    chapter.metadata_json = metadata


async def setup_incumbent(candidates, monkeypatch):
    await admit(candidates, monkeypatch)
    session, _, _, chapter, old, new = candidates
    old.promotion_state = "promoted"
    old.promotion_metadata = {"preserved": {"audit": ["original-generation"]}}
    old.promotion_reason_codes = ["origin:fixture", "quality_eligible"]
    score = await session.scalar(
        select(QualityScoreModel).where(
            QualityScoreModel.chapter_draft_version_id == new.id,
        )
    )
    score.score_goal, score.score_conflict = 1, 0.90
    score.score_emotion, score.score_style = 0.83, 0.85
    rejected = receipt(old)
    bind(chapter, rejected)
    await session.commit()
    return rejected


def columns(row):
    return {column.key: deepcopy(getattr(row, column.key)) for column in row.__table__.columns}


@pytest.mark.parametrize("title", ["Harbor Atlas", "山城夜班手记"])
@pytest.mark.parametrize("exact", [True, False])
@pytest.mark.parametrize("ranking", ["higher_score", "earlier_version_tie"])
async def test_exact_rejected_higher_incumbent_is_superseded_with_truthful_audit(
    candidates,
    monkeypatch,
    title,
    exact,
    ranking,
):
    rejected = await setup_incumbent(candidates, monkeypatch)
    session, _, project, chapter, old, new = candidates
    project.title = title
    # Normalized receipt hashes and raw exact promotion hashes remain distinct.
    old.content_md += "\r\nThe recorded interval was contradicted."
    rejected = receipt(old)
    bind(chapter, rejected)
    old_score = await session.scalar(
        select(QualityScoreModel).where(
            QualityScoreModel.chapter_draft_version_id == old.id,
        )
    )
    if ranking == "earlier_version_tie":
        old_score.score_overall = 0.58
        old_score.score_goal, old_score.score_conflict = 1, 0.90
        old_score.score_emotion, old_score.score_style = 0.83, 0.85
        new_score = await session.scalar(
            select(QualityScoreModel).where(
                QualityScoreModel.chapter_draft_version_id == new.id,
            )
        )
        new_score.score_overall = 0.58
    review = ReviewReportModel(
        project_id=project.id,
        target_type="chapter",
        target_id=chapter.id,
        reviewer_type="critic",
        verdict="pass",
        structured_output={"draft_id": str(old.id), "preserved": ["original review"]},
    )
    session.add(review)
    await session.flush()
    old_score.review_report_id = review.id
    await session.commit()
    await session.refresh(old_score)
    before_old, before_score, before_review = columns(old), columns(old_score), columns(review)
    before_chapter = deepcopy(chapter.metadata_json)
    assert choose_usable_promoted_chapter_source(chapter=chapter, drafts=[old, new]) is None

    outcome = await promote(candidates, exact=exact, entry="best")
    assert outcome.changed and outcome.promoted_draft_id == new.id, outcome
    assert outcome.incumbent_draft_id == old.id
    await session.commit()
    await session.refresh(old)
    await session.refresh(new)
    await session.refresh(old_score)
    await session.refresh(review)
    assert old.promotion_state == "superseded" and new.promotion_state == "promoted"
    assert columns(old) == {**before_old, "promotion_state": "superseded"}
    assert columns(old_score) == before_score and columns(review) == before_review
    assert chapter.metadata_json == before_chapter
    assert choose_usable_promoted_chapter_source(chapter=chapter, drafts=[old, new]).id == new.id
    decisions = list(await session.scalars(select(DraftPromotionDecisionModel)))
    replacement = next(row for row in decisions if row.chapter_draft_version_id == old.id)
    assert (replacement.from_state, replacement.to_state, replacement.decision_source) == (
        "promoted",
        "superseded",
        "replacement",
    )
    assert replacement.reason_codes == ["incumbent_source_rejected"]
    assert replacement.quality_score_id == old_score.id
    assert replacement.evidence_json == {
        "replacement_draft_id": str(new.id),
        "incumbent_source_rejected": {
            "project_id": str(project.id),
            "chapter_id": str(chapter.id),
            "draft_id": str(old.id),
            "body_sha256": rejected["body_sha256"],
            "source_key": continuity_source_key(rejected),
            "receipt": rejected,
        },
    }
    assert all(
        row.reason_codes == ["quality_eligible"] for row in decisions if row is not replacement
    )
    assert len(list(await session.scalars(select(ChapterDraftVersionModel)))) == 2


@pytest.mark.parametrize(
    "fault",
    [
        "missing",
        "none",
        "missing_passed",
        "zero",
        "string_false",
        "malformed_receipt",
        "malformed_ledger",
        "wrong_draft",
        "wrong_body",
        "wrong_key",
        "valid",
    ],
)
async def test_only_exact_boolean_negative_can_waive_incumbent_veto(candidates, monkeypatch, fault):
    rejected = await setup_incumbent(candidates, monkeypatch)
    session, _, _, chapter, old, new = candidates
    metadata = deepcopy(chapter.metadata_json)
    key = continuity_source_key(rejected)
    ledger = metadata["continuity_admission_by_source"]
    if fault == "missing":
        ledger.pop(key)
    elif fault == "none":
        ledger[key]["passed"] = None
    elif fault == "missing_passed":
        ledger[key].pop("passed")
    elif fault == "zero":
        ledger[key]["passed"] = 0
    elif fault == "string_false":
        ledger[key]["passed"] = "false"
    elif fault == "malformed_receipt":
        ledger[key] = [rejected]
    elif fault == "malformed_ledger":
        metadata["continuity_admission_by_source"] = [rejected]
    elif fault == "wrong_draft":
        ledger[key]["draft_id"] = str(uuid4())
    elif fault == "wrong_body":
        ledger[key]["body_sha256"] = "0" * 64
    elif fault == "wrong_key":
        ledger["unbound-key"] = ledger.pop(key)
    else:
        ledger[key]["passed"] = True
    chapter.metadata_json = metadata
    # Python considers False == 0; ensure the JSON type change reaches SQLite.
    flag_modified(chapter, "metadata_json")
    await session.commit()
    outcome = await promote(candidates, entry="best")
    assert not outcome.changed and outcome.reason == "incumbent_is_better_or_unscored"
    assert outcome.promoted_draft_id == old.id
    assert old.promotion_state == "promoted" and new.promotion_state == "under_review"
    assert not list(await session.scalars(select(DraftPromotionDecisionModel)))


async def test_later_valid_source_receipt_supersedes_historical_failure(candidates, monkeypatch):
    rejected = await setup_incumbent(candidates, monkeypatch)
    session, _, _, chapter, old, _ = candidates
    metadata = deepcopy(chapter.metadata_json)
    metadata["continuity_admission_history"] = [rejected]
    metadata["continuity_adoption_block"] = rejected
    chapter.metadata_json = metadata
    bind(chapter, receipt(old, True))
    await session.commit()
    assert choose_usable_promoted_chapter_source(chapter=chapter, drafts=[old]).id == old.id
    outcome = await promote(candidates, entry="best")
    assert not outcome.changed and outcome.reason == "incumbent_is_better_or_unscored"
    # A normal better candidate still replaces it, with the normal reason.
    score = await session.scalar(
        select(QualityScoreModel).where(
            QualityScoreModel.chapter_draft_version_id == old.id,
        )
    )
    score.score_overall = 0.86
    await session.commit()
    outcome = await promote(candidates, entry="best")
    assert outcome.changed
    replacement = await session.scalar(
        select(DraftPromotionDecisionModel).where(
            DraftPromotionDecisionModel.chapter_draft_version_id == old.id,
        )
    )
    assert replacement.reason_codes == ["better_candidate"]
    assert "incumbent_source_rejected" not in replacement.evidence_json


@pytest.mark.parametrize("passed", [False, None, 0, "false"])
async def test_canonical_blocked_candidate_is_excluded_from_best_of_n(
    candidates, monkeypatch, passed
):
    await admit(candidates, monkeypatch)
    session, _, _, chapter, old, new = candidates
    bind(chapter, receipt(old, passed))
    await session.commit()
    outcome = await promote(candidates, exact=False, entry="best")
    assert outcome.changed and outcome.promoted_draft_id == new.id
    assert old.promotion_state == "under_review"


@pytest.mark.parametrize("exact", [True, False])
@pytest.mark.parametrize("block", ["quality", "source", "absent_candidate"])
async def test_rejected_incumbent_stays_physical_until_eligible_candidate_succeeds(
    candidates,
    monkeypatch,
    exact,
    block,
):
    await setup_incumbent(candidates, monkeypatch)
    session, _, _, chapter, old, new = candidates
    if block == "quality":
        score = await session.scalar(
            select(QualityScoreModel).where(
                QualityScoreModel.chapter_draft_version_id == new.id,
            )
        )
        score.evidence_summary = {"hard_gates_passed": False, "blocking_codes": ["HARD_BLOCK"]}
    elif block == "source":
        bind(chapter, receipt(new))
    else:
        new.promotion_state = "quarantined"
    await session.commit()
    if exact and block == "source":
        with pytest.raises(ValueError, match="chapter_promotion_continuity_receipt_invalid"):
            await promote(candidates, exact=exact, entry="best")
    else:
        outcome = await promote(candidates, exact=exact, entry="best")
        assert not outcome.changed and outcome.reason == "no_eligible_candidate"
    assert old.promotion_state == "promoted"
    assert not list(await session.scalars(select(DraftPromotionDecisionModel)))


@pytest.mark.parametrize("quality", ["missing", "ineligible", "other_judge"])
@pytest.mark.parametrize("admission", ["rejected", "valid", "missing", "none", "malformed"])
async def test_unrankable_incumbent_veto_requires_no_exact_rejection(
    candidates,
    monkeypatch,
    quality,
    admission,
):
    rejected = await setup_incumbent(candidates, monkeypatch)
    session, _, _, chapter, old, new = candidates
    if admission == "valid":
        bind(chapter, receipt(old, True))
    elif admission == "none":
        bind(chapter, receipt(old, None))
    elif admission in {"missing", "malformed"}:
        metadata = deepcopy(chapter.metadata_json)
        key = continuity_source_key(rejected)
        if admission == "missing":
            metadata["continuity_admission_by_source"].pop(key)
        else:
            metadata["continuity_admission_by_source"][key] = [rejected]
        chapter.metadata_json = metadata
    score = await session.scalar(
        select(QualityScoreModel).where(
            QualityScoreModel.chapter_draft_version_id == old.id,
        )
    )
    if quality == "missing":
        await session.delete(score)
    elif quality == "ineligible":
        score.evidence_summary = {"hard_gates_passed": False, "blocking_codes": ["HARD_BLOCK"]}
    else:
        score.judge_key = "another-ruler"
    await session.commit()
    outcome = await promote(candidates, entry="best")
    if admission == "rejected":
        assert outcome.changed and outcome.promoted_draft_id == new.id, outcome
        assert outcome.incumbent_draft_id == old.id
        await session.commit()
        await session.refresh(old)
        await session.refresh(new)
        assert old.promotion_state == "superseded" and new.promotion_state == "promoted"
        replacement = await session.scalar(
            select(DraftPromotionDecisionModel).where(
                DraftPromotionDecisionModel.chapter_draft_version_id == old.id,
            )
        )
        assert replacement.reason_codes == ["incumbent_source_rejected"]
        assert replacement.evidence_json["incumbent_source_rejected"]["receipt"] == rejected
        assert replacement.quality_score_id is None
    else:
        assert not outcome.changed and outcome.reason == "incumbent_is_better_or_unscored"
        assert old.promotion_state == "promoted" and new.promotion_state == "under_review"
        assert not list(await session.scalars(select(DraftPromotionDecisionModel)))


@pytest.mark.parametrize("fault", ["body", "current", "preview"])
async def test_rejected_incumbent_cannot_waive_exact_candidate_guards(
    candidates,
    monkeypatch,
    fault,
):
    await setup_incumbent(candidates, monkeypatch)
    session, _, _, _, old, new = candidates
    overrides = {}
    if fault == "body":
        overrides["expected_body_sha256"] = "0" * 64
        error = "exact_promotion_body_changed"
    elif fault == "current":
        new.is_current = False
        error = "exact_promotion_source_changed"
    else:
        new.promotion_metadata = {}
        error = "chapter_promotion_continuity_receipt_stale"
    await session.commit()
    with pytest.raises(ValueError, match=error):
        await promote(candidates, entry="best", **overrides)
    assert old.promotion_state == "promoted" and new.promotion_state == "under_review"
    assert not list(await session.scalars(select(DraftPromotionDecisionModel)))


@pytest.mark.parametrize("incumbent_score", [0.99, 0.86])
async def test_scene_ranking_and_replacement_remain_unchanged(candidates, incumbent_score):
    session, _, project, chapter, _, _ = candidates
    connection = await session.connection()
    for model in (SceneCardModel, SceneDraftVersionModel):
        ddl = str(CreateTable(model.__table__).compile(dialect=connection.dialect))
        await connection.exec_driver_sql(ddl.replace("::jsonb", ""))
    index = next(
        index
        for index in SceneDraftVersionModel.__table__.indexes
        if index.name == "uq_scene_draft_promoted"
    )
    await connection.execute(CreateIndex(index))
    scene = SceneCardModel(
        project_id=project.id,
        chapter_id=chapter.id,
        scene_number=1,
        scene_type="investigation",
        purpose={},
        entry_state={},
        exit_state={},
        metadata_json={},
    )
    session.add(scene)
    await session.flush()
    old, new = [
        SceneDraftVersionModel(
            project_id=project.id,
            scene_card_id=scene.id,
            version_no=i,
            content_md=f"Scene body {i}",
            is_current=i == 2,
            promotion_state="promoted" if i == 1 else "under_review",
        )
        for i in (1, 2)
    ]
    session.add_all([old, new])
    await session.flush()
    scene.metadata_json = {
        "continuity_admission_by_source": {
            continuity_source_key(receipt(old)): receipt(old),
        }
    }
    for draft, value in ((old, incumbent_score), (new, 0.90)):
        session.add(
            QualityScoreModel(
                project_id=project.id,
                target_type="scene",
                target_id=scene.id,
                scene_draft_version_id=draft.id,
                judge_key="scene-ruler",
                score_overall=value,
                score_goal=value,
                score_conflict=value,
                score_emotion=value,
                score_style=value,
                is_current=draft is new,
                evidence_summary={"hard_gates_passed": True},
            )
        )
    await session.commit()
    outcome = await promote_best_draft(
        session,
        project_id=project.id,
        draft_kind="scene",
        parent_id=scene.id,
        judge_key="scene-ruler",
    )
    assert outcome.changed is (incumbent_score < 0.90)
    assert outcome.promoted_draft_id == (new.id if outcome.changed else old.id)
    if outcome.changed:
        decision = await session.scalar(
            select(DraftPromotionDecisionModel).where(
                DraftPromotionDecisionModel.scene_draft_version_id == old.id,
            )
        )
        assert decision.reason_codes == ["better_candidate"]
