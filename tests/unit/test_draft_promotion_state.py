from __future__ import annotations

import inspect
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.schema import CreateIndex

from bestseller.domain.enums import DraftPromotionState
from bestseller.domain.promotion import (
    PromotionCandidate,
    PromotionEvidence,
    PromotionValidationError,
    is_promotion_eligible,
    promotion_rank_key,
    validate_promotion_transition,
)
from bestseller.infra.db.models import (
    ChapterDraftVersionModel,
    DraftPromotionDecisionModel,
    QualityScoreModel,
    SceneDraftVersionModel,
)
from bestseller.services.draft_promotion import (
    mark_candidate_under_review,
    mark_draft_eligible,
    promote_best_draft,
    promote_chapter_draft,
    promote_scene_draft,
    quarantine_draft,
    select_best_eligible_draft,
    transition_draft_state,
)

pytestmark = pytest.mark.unit


def test_promotion_state_vocabulary_is_formal_and_complete() -> None:
    assert {state.value for state in DraftPromotionState} == {
        "legacy_unverified",
        "candidate",
        "under_review",
        "eligible",
        "promoted",
        "superseded",
        "rejected",
        "quarantined",
    }


def test_eligibility_requires_exact_score_hard_gates_and_zero_blockers() -> None:
    draft_id = uuid4()
    eligible = PromotionEvidence(
        draft_id=draft_id,
        score_draft_id=draft_id,
        score_overall=0.90,
        core_scores=(0.82, 0.85, 0.88),
        hard_gates_passed=True,
        blocking_codes=(),
    )
    assert is_promotion_eligible(eligible, min_overall=0.85, min_core=0.80) is True

    assert is_promotion_eligible(
        eligible.__class__(**{**eligible.__dict__, "score_draft_id": uuid4()}),
        min_overall=0.85,
        min_core=0.80,
    ) is False
    assert is_promotion_eligible(
        eligible.__class__(**{**eligible.__dict__, "hard_gates_passed": False}),
        min_overall=0.85,
        min_core=0.80,
    ) is False
    assert is_promotion_eligible(
        eligible.__class__(**{**eligible.__dict__, "blocking_codes": ("HOOK_BLOCK",)}),
        min_overall=0.85,
        min_core=0.80,
    ) is False
    assert is_promotion_eligible(
        eligible.__class__(**{**eligible.__dict__, "core_scores": (0.79, 0.95)}),
        min_overall=0.85,
        min_core=0.80,
    ) is False


def test_candidate_order_is_overall_then_min_core_then_earlier_version() -> None:
    candidates = [
        PromotionCandidate(uuid4(), version_no=3, score_overall=0.91, core_scores=(0.88, 0.89)),
        PromotionCandidate(uuid4(), version_no=2, score_overall=0.92, core_scores=(0.80, 0.99)),
        PromotionCandidate(uuid4(), version_no=1, score_overall=0.92, core_scores=(0.80, 0.95)),
    ]

    ranked = sorted(candidates, key=promotion_rank_key, reverse=True)

    assert [candidate.version_no for candidate in ranked] == [1, 2, 3]


def test_state_transitions_and_human_override_require_complete_audit() -> None:
    validate_promotion_transition(
        DraftPromotionState.CANDIDATE,
        DraftPromotionState.UNDER_REVIEW,
        decision_source="quality_gate",
    )
    validate_promotion_transition(
        DraftPromotionState.REJECTED,
        DraftPromotionState.UNDER_REVIEW,
        decision_source="human_override",
        actor="editor@example.com",
        reason="Editorial exception after manual read.",
        evidence={"manual_review_id": "review-1"},
    )
    validate_promotion_transition(
        DraftPromotionState.PROMOTED,
        DraftPromotionState.SUPERSEDED,
        decision_source="replacement",
        evidence={"replacement_draft_id": str(uuid4())},
    )

    with pytest.raises(PromotionValidationError):
        validate_promotion_transition(
            DraftPromotionState.REJECTED,
            DraftPromotionState.PROMOTED,
            decision_source="human_override",
            actor="editor@example.com",
            reason="Skip review.",
            evidence={"manual_review_id": "review-1"},
        )
    with pytest.raises(PromotionValidationError):
        validate_promotion_transition(
            DraftPromotionState.REJECTED,
            DraftPromotionState.PROMOTED,
            decision_source="human_override",
            actor=None,
            reason="Manual decision.",
            evidence={"manual_review_id": "review-1"},
        )
    with pytest.raises(PromotionValidationError):
        validate_promotion_transition(
            DraftPromotionState.REJECTED,
            DraftPromotionState.PROMOTED,
            decision_source="human_override",
            actor="editor@example.com",
            reason=None,
            evidence={"manual_review_id": "review-1"},
        )
    with pytest.raises(PromotionValidationError):
        validate_promotion_transition(
            DraftPromotionState.QUARANTINED,
            DraftPromotionState.UNDER_REVIEW,
            decision_source="human_override",
            actor="editor@example.com",
            reason="Manual decision.",
            evidence={},
        )


@pytest.mark.parametrize(
    "model",
    [SceneDraftVersionModel, ChapterDraftVersionModel],
)
def test_draft_models_keep_current_orthogonal_to_promotion(model: type) -> None:
    columns = model.__table__.c
    assert columns.is_current is not None
    assert columns.promotion_state.server_default.arg.text == "'candidate'"
    assert columns.promotion_reason_codes is not None
    assert columns.promotion_score.type.precision == 5
    assert columns.promotion_score.type.scale == 4
    assert columns.promoted_at.type.timezone is True
    assert columns.quarantined_at.type.timezone is True
    assert columns.promotion_metadata is not None
    checks = " ".join(
        str(constraint.sqltext)
        for constraint in model.__table__.constraints
        if constraint.__class__.__name__ == "CheckConstraint"
    )
    assert "legacy_unverified" in checks
    assert "promotion_score >= 0 AND promotion_score <= 1" in checks


@pytest.mark.parametrize(
    ("model", "index_name"),
    [
        (SceneDraftVersionModel, "uq_scene_draft_promoted"),
        (ChapterDraftVersionModel, "uq_chapter_draft_promoted"),
    ],
)
def test_promoted_partial_unique_indexes_compile_for_postgres_and_sqlite(
    model: type,
    index_name: str,
) -> None:
    index = next(item for item in model.__table__.indexes if item.name == index_name)
    pg_sql = str(CreateIndex(index).compile(dialect=postgresql.dialect()))
    sqlite_sql = str(CreateIndex(index).compile(dialect=sqlite.dialect()))

    assert "UNIQUE INDEX" in pg_sql
    assert "WHERE promotion_state = 'promoted'" in pg_sql
    assert "UNIQUE INDEX" in sqlite_sql
    assert "WHERE promotion_state = 'promoted'" in sqlite_sql


def test_quality_score_exact_binding_and_evaluation_fields_are_constrained() -> None:
    columns = QualityScoreModel.__table__.c
    assert columns.scene_draft_version_id.foreign_keys
    assert columns.chapter_draft_version_id.foreign_keys
    assert columns.evaluation_round.server_default.arg.text == "1"
    assert columns.judge_key.type.length == 128
    assert columns.pairwise_group_id is not None

    check_sql = " ".join(
        str(constraint.sqltext)
        for constraint in QualityScoreModel.__table__.constraints
        if constraint.__class__.__name__ == "CheckConstraint"
    )
    assert "scene_draft_version_id IS NULL OR chapter_draft_version_id IS NULL" in check_sql

    index_names = {index.name for index in QualityScoreModel.__table__.indexes}
    assert {
        "idx_quality_scores_scene_draft_judge_round",
        "idx_quality_scores_chapter_draft_judge_round",
        "idx_quality_scores_pairwise_group",
    } <= index_names


def test_decision_audit_xor_binds_exactly_one_draft_version() -> None:
    columns = DraftPromotionDecisionModel.__table__.c
    assert columns.scene_draft_version_id.foreign_keys
    assert columns.chapter_draft_version_id.foreign_keys
    assert columns.quality_score_id.foreign_keys
    assert columns.workflow_run_id.foreign_keys
    assert columns.reason_codes is not None
    assert columns.evidence_json is not None
    assert DraftPromotionDecisionModel.metadata_json is not None
    assert columns.metadata is not None

    check_sql = " ".join(
        str(constraint.sqltext)
        for constraint in DraftPromotionDecisionModel.__table__.constraints
        if constraint.__class__.__name__ == "CheckConstraint"
    )
    assert "scene_draft_version_id IS NULL" in check_sql
    assert "chapter_draft_version_id IS NOT NULL" in check_sql
    assert "scene_draft_version_id IS NOT NULL" in check_sql
    assert "chapter_draft_version_id IS NULL" in check_sql
    assert "legacy_unverified" in check_sql
    assert "promotion_score >= 0 AND promotion_score <= 1" in check_sql


def test_promotion_service_uses_nested_transaction_and_never_commits() -> None:
    source = inspect.getsource(promote_best_draft)
    assert ".with_for_update()" in source
    assert "begin_nested()" in source
    assert "IntegrityError" in inspect.getsource(
        __import__("bestseller.services.draft_promotion", fromlist=["*"])
    )
    assert ".commit(" not in source
    assert "await session.flush()" in source


def test_promotion_service_exposes_complete_phase_two_api() -> None:
    assert all(
        callable(item)
        for item in (
            transition_draft_state,
            mark_candidate_under_review,
            mark_draft_eligible,
            quarantine_draft,
            select_best_eligible_draft,
            promote_scene_draft,
            promote_chapter_draft,
        )
    )
    assert "judge_key" in inspect.signature(promote_best_draft).parameters
