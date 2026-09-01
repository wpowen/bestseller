from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from copy import deepcopy
import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest

from bestseller.domain.story_engine import canonical_json_hash
from bestseller.services import story_engine_review as review_service
from bestseller.services.continuity import _merge_snapshot_storage
from bestseller.services.story_engine_review import (
    STORY_TRANSITION_RECEIPT_ARTIFACT_TYPE,
    StoryEngineReceiptRejected,
    StoryEngineReceiptVerdict,
    create_story_transition_receipt_artifact,
    fold_story_engine_receipt_into_snapshot,
    promote_chapter_draft_with_story_engine_receipt,
    review_story_engine_transition,
)

BODY = (
    "林澈当众把原始档案投上大屏,公开了管理层删改证据的记录。"
    "审计主管立刻冻结他的权限,并派人追查给他档案的证人。"
)


def _creative_core() -> dict[str, Any]:
    pre_state = {"exposure": {"category": "exposure", "value": 0}}
    post_state = {"exposure": {"category": "exposure", "value": 1}}
    post_hash = canonical_json_hash(post_state)
    return {
        "engine_artifact_id": str(uuid4()),
        "engine_version": 2,
        "window_artifact_id": str(uuid4()),
        "chapter_number": 7,
        "choice_id": "publish",
        "pre_state": pre_state,
        "pre_state_hash": canonical_json_hash(pre_state),
        "known_facts": ["档案将在午夜被销毁"],
        "pressure": "管理层正在销毁原始档案",
        "options": [
            {
                "choice_id": "publish",
                "label": "公开证据",
                "reachable_state_hash": post_hash,
            },
            {
                "choice_id": "hide",
                "label": "隐藏证据",
                "reachable_state_hash": "different-reachable-state",
            },
        ],
        "chosen_path": "当众公开原始档案,并承担暴露证人的代价",
        "alternative_costs": ["隐藏档案会错过最后公开窗口"],
        "opponent_strategy": "冻结权限并追查证人",
        "due_obligations": ["保护证人"],
        "required_state_changes": [
            {
                "key": "exposure",
                "category": "exposure",
                "before": 0,
                "operator": "set",
                "after": 1,
                "evidence": "公开原始档案",
                "monotonic": "non_decreasing",
            }
        ],
        "expected_post_state_hash": post_hash,
        "projection_hash": "projection-hash-7",
        "can_drive_generation": True,
    }


def _observation() -> dict[str, Any]:
    return {
        "choice_id": "publish",
        "pre_state_hash": _creative_core()["pre_state_hash"],
        "observed_action": "林澈当众公开原始档案",
        "observed_transitions": [
            {
                "key": "exposure",
                "category": "exposure",
                "before": 0,
                "operator": "set",
                "after": 1,
                "evidence": "公开了管理层删改证据的记录",
                "monotonic": "non_decreasing",
            }
        ],
        "opponent_counteraction": "审计主管冻结权限并追查证人",
        "new_obligations": ["林澈必须在追查到来前保护证人"],
        "evidence_quotes": [
            "林澈当众把原始档案投上大屏",
            "审计主管立刻冻结他的权限",
        ],
    }


def _review(
    *,
    core: dict[str, Any] | None = None,
    observation: dict[str, Any] | None = None,
    body: str = BODY,
) -> review_service.StoryEngineReceiptReview:
    return review_story_engine_transition(
        creative_core=core or _creative_core(),
        observation=observation or _observation(),
        draft_content_md=body,
        project_id=uuid4(),
        chapter_id=uuid4(),
        draft_version_id=uuid4(),
        workflow_run_id=uuid4(),
    )


def test_matched_receipt_replays_and_keeps_exact_evidence_lineage() -> None:
    review = _review()

    assert review.verdict is StoryEngineReceiptVerdict.MATCHED
    assert review.blocking_codes == ()
    assert review.replay_passed is True
    assert review.content["artifact_type"] == STORY_TRANSITION_RECEIPT_ARTIFACT_TYPE
    assert review.content["post_state_hash"] == _creative_core()["expected_post_state_hash"]
    assert review.content["receipt"]["evidence_quotes"][0] in BODY
    assert review.content["_meta"]["workflow_run_id"]
    assert review.content["_meta"]["receipt_hash"]


def test_shadow_projection_may_be_reviewed_without_gaining_writer_authority() -> None:
    core = _creative_core()
    core["can_drive_generation"] = False

    review = review_story_engine_transition(
        creative_core=core,
        observation=_observation(),
        draft_content_md=BODY,
        project_id=uuid4(),
        chapter_id=uuid4(),
        draft_version_id=uuid4(),
        workflow_run_id=uuid4(),
        allow_non_authoritative=True,
    )

    assert review.verdict is StoryEngineReceiptVerdict.MATCHED
    assert core["can_drive_generation"] is False


def test_invalid_creative_core_returns_a_fail_closed_diagnostic() -> None:
    core = _creative_core()
    core["options"] = []

    review = _review(core=core)

    assert review.verdict is StoryEngineReceiptVerdict.INVALID
    assert review.replay_passed is False
    assert review.blocking_codes == (
        "STORY_ENGINE_RECEIPT_CREATIVE_CORE_INVALID",
    )
    assert review.content["_meta"]["validation_error"]


@pytest.mark.asyncio
async def test_observation_extractor_returns_the_model_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observation = _observation()
    complete = AsyncMock(
        return_value=SimpleNamespace(content=json.dumps(observation, ensure_ascii=False))
    )
    monkeypatch.setattr(review_service, "complete_text", complete)

    result = await review_service.extract_story_engine_receipt_observation(
        AsyncMock(),
        SimpleNamespace(),  # type: ignore[arg-type]
        creative_core=_creative_core(),
        draft_content_md=BODY,
        project_id=uuid4(),
        chapter_id=uuid4(),
        draft_version_id=uuid4(),
        workflow_run_id=uuid4(),
    )

    assert result == observation
    request = complete.await_args.args[2]
    assert request.prompt_template == "story_engine_transition_receipt"
    assert "CHAPTER_DRAFT" in request.user_prompt


def test_observation_json_parser_handles_fences_and_rejects_non_json() -> None:
    assert review_service._extract_json_object("not json") == {}
    assert review_service._extract_json_object("```json\n{\"choice_id\":\"publish\"}\n```") == {
        "choice_id": "publish"
    }


def test_receipt_rejects_evidence_quote_not_present_in_draft() -> None:
    observation = _observation()
    observation["evidence_quotes"] = ["这句话正文里从未出现"]

    review = _review(observation=observation)

    assert review.verdict is StoryEngineReceiptVerdict.INVALID
    assert "STORY_ENGINE_RECEIPT_QUOTE_NOT_FOUND" in review.blocking_codes


def test_receipt_detects_abstract_result_only_action() -> None:
    observation = _observation()
    observation["observed_action"] = "局势发生了变化"

    review = _review(observation=observation)

    assert review.verdict is StoryEngineReceiptVerdict.INVALID
    assert "STORY_ENGINE_RECEIPT_ACTION_ABSTRACT" in review.blocking_codes


def test_receipt_requires_an_observed_opponent_response() -> None:
    observation = _observation()
    observation["opponent_counteraction"] = ""

    review = _review(observation=observation)

    assert review.verdict is StoryEngineReceiptVerdict.INVALID
    assert "STORY_ENGINE_RECEIPT_OPPONENT_RESPONSE_MISSING" in review.blocking_codes


def test_receipt_detects_chapter_restart_from_stale_pre_state() -> None:
    observation = _observation()
    observation["pre_state_hash"] = "stale-chapter-start"

    review = _review(observation=observation)

    assert review.verdict is StoryEngineReceiptVerdict.DIVERGED
    assert "STORY_ENGINE_RECEIPT_PRE_STATE_MISMATCH" in review.blocking_codes


def test_receipt_detects_expected_observed_transition_divergence() -> None:
    observation = _observation()
    observation["observed_transitions"][0]["after"] = 0

    review = _review(observation=observation)

    assert review.verdict is StoryEngineReceiptVerdict.DIVERGED
    assert "STORY_ENGINE_RECEIPT_TRANSITION_DIVERGED" in review.blocking_codes


def test_continuity_refresh_preserves_folded_story_engine_state() -> None:
    engine_state = {
        "receipt_artifact_id": str(uuid4()),
        "post_state_hash": "canonical-post-state",
        "replay_passed": True,
    }

    merged = _merge_snapshot_storage(
        existing_facts={"facts": [], "story_engine": engine_state},
        extracted_facts={"facts": [{"name": "location", "value": "档案室"}]},
        snapshot_contract={"is_usable": True},
    )

    assert merged["story_engine"] == engine_state
    assert merged["facts"][0]["name"] == "location"


@pytest.mark.asyncio
async def test_invalid_review_never_enters_canonical_transaction() -> None:
    session = _AtomicSession()
    observation = _observation()
    observation["evidence_quotes"] = ["正文没有这句话"]
    review = _review(observation=observation)
    draft = SimpleNamespace(
        id=UUID(review.content["draft_version_id"]),
        promotion_state="candidate",
        promotion_metadata={},
    )

    with pytest.raises(StoryEngineReceiptRejected, match="did not match"):
        await promote_chapter_draft_with_story_engine_receipt(
            session,  # type: ignore[arg-type]
            project_id=UUID(review.content["project_id"]),
            chapter_id=UUID(review.content["chapter_id"]),
            chapter_number=7,
            draft=draft,
            quality_score_id=uuid4(),
            judge_key="chapter-quality-v2",
            workflow_run_id=UUID(review.content["_meta"]["workflow_run_id"]),
            review=review,
        )

    assert session.canonical_writes == []


@pytest.mark.asyncio
async def test_identical_receipt_retry_reuses_existing_artifact() -> None:
    review = _review()
    project_id = UUID(review.content["project_id"])
    chapter_id = UUID(review.content["chapter_id"])
    workflow_run_id = UUID(review.content["_meta"]["workflow_run_id"])
    existing = review_service.PlanningArtifactVersionModel(
        id=uuid4(),
        project_id=project_id,
        artifact_type=STORY_TRANSITION_RECEIPT_ARTIFACT_TYPE,
        scope_ref_id=chapter_id,
        version_no=1,
        status="matched",
        schema_version="1.0",
        content=deepcopy(review.content),
        source_run_id=workflow_run_id,
        idempotency_key="receipt-attempt-1",
    )
    session = AsyncMock()
    session.scalar = AsyncMock(return_value=existing)

    result = await create_story_transition_receipt_artifact(
        session,
        project_id=project_id,
        chapter_id=chapter_id,
        content=review.content,
        idempotency_key="receipt-attempt-1",
        source_run_id=workflow_run_id,
    )

    assert result is existing
    session.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_same_receipt_key_rejects_changed_content() -> None:
    review = _review()
    project_id = UUID(review.content["project_id"])
    chapter_id = UUID(review.content["chapter_id"])
    workflow_run_id = UUID(review.content["_meta"]["workflow_run_id"])
    existing = review_service.PlanningArtifactVersionModel(
        id=uuid4(),
        project_id=project_id,
        artifact_type=STORY_TRANSITION_RECEIPT_ARTIFACT_TYPE,
        scope_ref_id=chapter_id,
        version_no=1,
        status="matched",
        schema_version="1.0",
        content=deepcopy(review.content),
        source_run_id=workflow_run_id,
        idempotency_key="receipt-attempt-1",
    )
    changed = deepcopy(review.content)
    changed["receipt"]["observed_action"] = "篡改后的行动"
    changed["_meta"]["receipt_hash"] = canonical_json_hash(changed["receipt"])
    session = AsyncMock()
    session.scalar = AsyncMock(return_value=existing)

    with pytest.raises(ValueError, match="different story transition receipt content"):
        await create_story_transition_receipt_artifact(
            session,
            project_id=project_id,
            chapter_id=chapter_id,
            content=changed,
            idempotency_key="receipt-attempt-1",
            source_run_id=workflow_run_id,
        )


@pytest.mark.asyncio
async def test_snapshot_fold_preserves_hard_facts_and_stores_replay_state() -> None:
    review = _review()
    project_id = UUID(review.content["project_id"])
    chapter_id = UUID(review.content["chapter_id"])
    workflow_run_id = UUID(review.content["_meta"]["workflow_run_id"])
    receipt = review_service.PlanningArtifactVersionModel(
        id=uuid4(),
        project_id=project_id,
        artifact_type=STORY_TRANSITION_RECEIPT_ARTIFACT_TYPE,
        scope_ref_id=chapter_id,
        version_no=1,
        status="matched",
        schema_version="1.0",
        content=deepcopy(review.content),
        source_run_id=workflow_run_id,
        idempotency_key="receipt-attempt-1",
    )
    snapshot = review_service.ChapterStateSnapshotModel(
        id=uuid4(),
        project_id=project_id,
        chapter_id=chapter_id,
        chapter_number=7,
        facts={"facts": [{"name": "location", "value": "档案室"}]},
        extraction_status="legacy_unverified",
    )
    session = AsyncMock()
    session.scalar = AsyncMock(return_value=snapshot)

    result = await fold_story_engine_receipt_into_snapshot(
        session,
        project_id=project_id,
        chapter_id=chapter_id,
        chapter_number=7,
        draft_version_id=UUID(review.content["draft_version_id"]),
        receipt_artifact=receipt,
        workflow_run_id=workflow_run_id,
    )

    assert result is snapshot
    assert snapshot.facts["facts"][0]["name"] == "location"
    assert snapshot.facts["story_engine"]["receipt_artifact_id"] == str(receipt.id)
    assert snapshot.facts["story_engine"]["state"] == review.content["post_state"]
    assert snapshot.facts["snapshot_contract"]["is_usable"] is True
    assert snapshot.extraction_status == "ok_promoted"


@pytest.mark.asyncio
async def test_snapshot_fold_creates_a_compatible_row_when_none_exists() -> None:
    review = _review()
    project_id = UUID(review.content["project_id"])
    chapter_id = UUID(review.content["chapter_id"])
    workflow_run_id = UUID(review.content["_meta"]["workflow_run_id"])
    receipt = review_service.PlanningArtifactVersionModel(
        id=uuid4(),
        project_id=project_id,
        artifact_type=STORY_TRANSITION_RECEIPT_ARTIFACT_TYPE,
        scope_ref_id=chapter_id,
        version_no=1,
        status="matched",
        schema_version="1.0",
        content=deepcopy(review.content),
        source_run_id=workflow_run_id,
        idempotency_key="receipt-attempt-1",
    )
    session = _WriteReceiptSession()
    session.scalar_results = [None]
    session.flush = AsyncMock(side_effect=session._flush)

    result = await fold_story_engine_receipt_into_snapshot(
        session,  # type: ignore[arg-type]
        project_id=project_id,
        chapter_id=chapter_id,
        chapter_number=7,
        draft_version_id=UUID(review.content["draft_version_id"]),
        receipt_artifact=receipt,
        workflow_run_id=workflow_run_id,
    )

    assert result in session.added
    assert result.extraction_status == "ok_promoted"
    assert result.extraction_model == "story_engine_v2"
    assert result.facts["story_engine"]["replay_passed"] is True


class _Savepoint(AbstractAsyncContextManager[None]):
    def __init__(self, session: _AtomicSession) -> None:
        self.session = session
        self.before: list[str] = []

    async def __aenter__(self) -> None:
        self.before = deepcopy(self.session.canonical_writes)
        return None

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        if exc_type is not None:
            self.session.canonical_writes[:] = self.before
        return False


class _AtomicSession:
    def __init__(self) -> None:
        self.canonical_writes: list[str] = []
        self.flush = AsyncMock()

    def begin_nested(self) -> _Savepoint:
        return _Savepoint(self)


class _WriteReceiptSession(_AtomicSession):
    def __init__(self) -> None:
        super().__init__()
        self.scalar_results: list[object | None] = [None, 2]
        self.added: list[object] = []

    async def scalar(self, statement: object) -> object | None:
        return self.scalar_results.pop(0)

    def add(self, value: object) -> None:
        self.added.append(value)

    async def _flush(self) -> None:
        for value in self.added:
            if getattr(value, "id", None) is None:
                value.id = uuid4()


@pytest.mark.asyncio
async def test_failed_promotion_rolls_back_receipt_and_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _AtomicSession()
    review = _review()
    draft_id = UUID(review.content["draft_version_id"])
    project_id = UUID(review.content["project_id"])
    chapter_id = UUID(review.content["chapter_id"])
    draft = SimpleNamespace(
        id=draft_id,
        promotion_state="candidate",
        promotion_metadata={},
    )

    async def fake_create(*args: Any, **kwargs: Any) -> SimpleNamespace:
        session.canonical_writes.append("receipt")
        return SimpleNamespace(id=uuid4(), content=review.content)

    async def fake_fold(*args: Any, **kwargs: Any) -> SimpleNamespace:
        session.canonical_writes.append("snapshot")
        return SimpleNamespace(id=uuid4())

    monkeypatch.setattr(
        review_service,
        "create_story_transition_receipt_artifact",
        fake_create,
    )
    monkeypatch.setattr(
        review_service,
        "fold_story_engine_receipt_into_snapshot",
        fake_fold,
    )
    monkeypatch.setattr(review_service, "mark_candidate_under_review", AsyncMock())
    monkeypatch.setattr(review_service, "mark_draft_eligible", AsyncMock())
    monkeypatch.setattr(
        review_service,
        "promote_chapter_draft",
        AsyncMock(
            return_value=SimpleNamespace(
                promoted_draft_id=uuid4(),
                changed=False,
                reason="another_candidate_won",
            )
        ),
    )

    with pytest.raises(StoryEngineReceiptRejected, match="exact reviewed draft"):
        await promote_chapter_draft_with_story_engine_receipt(
            session,  # type: ignore[arg-type]
            project_id=project_id,
            chapter_id=chapter_id,
            chapter_number=7,
            draft=draft,
            quality_score_id=uuid4(),
            judge_key="chapter-quality-v2",
            workflow_run_id=UUID(review.content["_meta"]["workflow_run_id"]),
            review=review,
        )

    assert session.canonical_writes == []


@pytest.mark.asyncio
async def test_new_receipt_artifact_uses_next_chapter_scoped_version() -> None:
    review = _review()
    session = _WriteReceiptSession()
    session.flush = AsyncMock(side_effect=session._flush)
    project_id = UUID(review.content["project_id"])
    chapter_id = UUID(review.content["chapter_id"])
    workflow_run_id = UUID(review.content["_meta"]["workflow_run_id"])

    result = await create_story_transition_receipt_artifact(
        session,  # type: ignore[arg-type]
        project_id=project_id,
        chapter_id=chapter_id,
        content=review.content,
        idempotency_key="receipt-attempt-3",
        source_run_id=workflow_run_id,
    )

    assert result in session.added
    assert result.id is not None
    assert result.scope_ref_id == chapter_id
    assert result.version_no == 3
    assert result.source_run_id == workflow_run_id


@pytest.mark.asyncio
async def test_successful_promotion_keeps_one_workflow_lineage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _AtomicSession()
    review = _review()
    workflow_run_id = UUID(review.content["_meta"]["workflow_run_id"])
    project_id = UUID(review.content["project_id"])
    chapter_id = UUID(review.content["chapter_id"])
    draft = SimpleNamespace(
        id=UUID(review.content["draft_version_id"]),
        promotion_state="candidate",
        promotion_metadata={},
    )
    artifact = SimpleNamespace(id=uuid4(), content=review.content, source_run_id=workflow_run_id)
    snapshot = SimpleNamespace(id=uuid4(), facts={})

    async def fake_create(*args: Any, **kwargs: Any) -> SimpleNamespace:
        session.canonical_writes.append("receipt")
        assert kwargs["source_run_id"] == workflow_run_id
        return artifact

    async def fake_fold(*args: Any, **kwargs: Any) -> SimpleNamespace:
        session.canonical_writes.append("snapshot")
        assert kwargs["workflow_run_id"] == workflow_run_id
        return snapshot

    monkeypatch.setattr(
        review_service,
        "create_story_transition_receipt_artifact",
        fake_create,
    )
    monkeypatch.setattr(
        review_service,
        "fold_story_engine_receipt_into_snapshot",
        fake_fold,
    )
    monkeypatch.setattr(review_service, "mark_candidate_under_review", AsyncMock())
    monkeypatch.setattr(review_service, "mark_draft_eligible", AsyncMock())
    monkeypatch.setattr(
        review_service,
        "promote_chapter_draft",
        AsyncMock(
            return_value=SimpleNamespace(
                promoted_draft_id=draft.id,
                changed=True,
                reason="promoted",
            )
        ),
    )

    result = await promote_chapter_draft_with_story_engine_receipt(
        session,  # type: ignore[arg-type]
        project_id=project_id,
        chapter_id=chapter_id,
        chapter_number=7,
        draft=draft,
        quality_score_id=uuid4(),
        judge_key="chapter-quality-v2",
        workflow_run_id=workflow_run_id,
        review=review,
    )

    assert session.canonical_writes == ["receipt", "snapshot"]
    assert result.receipt_artifact is artifact
    assert result.snapshot is snapshot
    assert draft.promotion_metadata["story_engine_receipt_id"] == str(artifact.id)
    assert draft.promotion_metadata["story_engine_workflow_run_id"] == str(workflow_run_id)
