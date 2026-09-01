from __future__ import annotations

from contextlib import asynccontextmanager
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from bestseller.domain.story_engine import canonical_json_hash
from bestseller.infra.db.models import PlanningArtifactVersionModel
from bestseller.services.story_design_kernel import story_design_kernel_from_dict
from bestseller.services.story_engine import (
    STORY_ENGINE_ARTIFACT_TYPE,
    STORY_ENGINE_WINDOW_ARTIFACT_TYPE,
    LegacyProjectionStatus,
    build_story_engine_artifact_content,
    build_story_engine_window_artifact_content,
    create_story_engine_artifact,
    create_story_engine_window_artifact,
    persist_legacy_story_engine_shadow,
    project_legacy_story_engine,
    resolve_latest_story_engine_artifact,
    resolve_latest_story_engine_window_artifact,
)


def test_story_engine_artifacts_share_the_snapshot_idempotency_index() -> None:
    index = next(
        candidate
        for candidate in PlanningArtifactVersionModel.__table__.indexes
        if candidate.name == "uq_planning_snapshot_idempotency"
    )

    assert index.unique is True
    assert "story_engine_v2" in str(index.dialect_options["postgresql"]["where"])
    assert "story_engine_window_v2" in str(
        index.dialect_options["postgresql"]["where"]
    )
    assert "story_engine_v2" in str(index.dialect_options["sqlite"]["where"])
    assert "story_engine_window_v2" in str(index.dialect_options["sqlite"]["where"])
    assert "story_transition_receipt_v1" in str(
        index.dialect_options["postgresql"]["where"]
    )
    assert "story_transition_receipt_v1" in str(
        index.dialect_options["sqlite"]["where"]
    )


def _kernel() -> object:
    return story_design_kernel_from_dict(
        {
            "version": 1,
            "shape": {
                "length_class": "long",
                "publication_mode": "web_serial",
                "outline_depth": "chapter",
                "primary_duties": ["forward_pull"],
                "ending_contract": "close current loop",
            },
            "reader_promise": "每章让一个主动选择改变局势。",
            "premise_contract": {
                "unique_hook": "旧档案决定晋升资格。",
                "core_question": "主角能否公开档案?",
                "commercial_pull": "调查与反制同步升级。",
            },
            "character_conflict_contracts": [
                {
                    "character_key": "protagonist",
                    "external_goal": "取得原始档案",
                    "internal_need": "学会信任证人",
                    "pressure_source": "管理层销毁证据",
                    "choice_axis": "保护证人或抢先公开",
                    "change_vector": "从控制到协作",
                }
            ],
            "structure_strategy": {
                "macro_strategy": "证据链递进",
                "chapter_engine": "选择改变证据或关系",
                "pacing_rule": "短兑现后产生新代价",
                "freshness_rule": "连续章节不得复用同一行动",
            },
            "plot_tree": [
                {
                    "key": "mainline",
                    "line_type": "main",
                    "label": "档案调查",
                    "role": "主线",
                    "current_state": "只有传闻",
                    "target_state": "取得证据链",
                    "failure_if_removed": "失去行动目标",
                }
            ],
            "beat_schedule": [
                {
                    "chapter_range": "1-10",
                    "duty": "闭合证据链",
                    "state_change": "资源、信任和暴露变化",
                    "payoff": "取得原始档案",
                    "hook_or_aftereffect": "对手追查证人",
                }
            ],
            "change_vectors": ["资源变化", "信任变化"],
            "uniqueness_constraints": ["不得重复同一种调查行动"],
        }
    )


def _snapshot() -> dict[str, object]:
    return {
        "passed": True,
        "resource_balances": {"protagonist": {"access": 1}},
        "rule_state": {},
        "relationship_state": {},
        "open_agency_debts": [],
        "faction_pressure_queue": [],
    }


def _content() -> dict[str, object]:
    projection = project_legacy_story_engine(
        engine_id="engine-1",
        kernel=_kernel(),
        premium_state_snapshot=_snapshot(),
    )
    return build_story_engine_artifact_content(
        projection,
        source_snapshot_hash="snapshot-hash",
    )


def _window_content() -> dict[str, object]:
    pre_state = {"pressure": {"category": "exposure", "value": 0}}
    post_state = {"pressure": {"category": "exposure", "value": 1}}
    post_hash = canonical_json_hash(post_state)
    return build_story_engine_window_artifact_content(
        {
            "window_id": "window-1",
            "engine_id": "engine-1",
            "engine_version": 1,
            "engine_artifact_id": str(uuid4()),
            "source_engine_hash": "engine-hash",
            "projections": [
                {
                    "chapter_number": 1,
                    "choice_id": "publish",
                    "pre_state": pre_state,
                    "pressure": "对手正在销毁证据",
                    "known_facts": ["档案室今晚封存"],
                    "options": [
                        {
                            "choice_id": "publish",
                            "label": "公开证据",
                            "reachable_state_hash": post_hash,
                        },
                        {
                            "choice_id": "hide",
                            "label": "隐藏证据",
                            "reachable_state_hash": "alternative-hash",
                        },
                    ],
                    "chosen_option_id": "publish",
                    "chosen_path": "公开证据并保护证人",
                    "alternative_costs": ["隐藏会失去最后窗口"],
                    "opponent_strategy": "冻结权限并追查证人",
                    "due_obligations": ["保护证人"],
                    "required_state_changes": [
                        {
                            "key": "pressure",
                            "category": "exposure",
                            "before": 0,
                            "operator": "set",
                            "after": 1,
                            "evidence": "主角公开了证据",
                        }
                    ],
                    "expected_post_state_hash": post_hash,
                    "fingerprint": "publish|evidence|pressure",
                }
            ],
        },
        maturity="canary_validated",
        can_drive_generation=True,
    )
def test_artifact_content_keeps_lineage_and_cannot_claim_generation_authority() -> None:
    content = _content()

    assert content["artifact_type"] == STORY_ENGINE_ARTIFACT_TYPE
    assert content["projection_status"] == LegacyProjectionStatus.STRUCTURE_ONLY
    assert content["can_drive_generation"] is False
    assert content["maturity"] == "structure_only"
    assert content["engine"]
    meta = content["_meta"]
    assert isinstance(meta, dict)
    assert meta["source_snapshot_hash"] == "snapshot-hash"
    assert meta["legacy_kernel_hash"]
    assert meta["engine_hash"] == canonical_json_hash(content["engine"])
    assert meta["fallback_source"] == "legacy_projection"


@pytest.mark.asyncio
async def test_same_idempotency_key_reuses_identical_story_engine_artifact() -> None:
    content = _content()
    existing = PlanningArtifactVersionModel(
        project_id=uuid4(),
        artifact_type=STORY_ENGINE_ARTIFACT_TYPE,
        version_no=1,
        status="structure_only",
        schema_version="2.0",
        content=content,
        idempotency_key="engine-attempt-1",
    )
    session = AsyncMock()
    session.scalar = AsyncMock(side_effect=[existing])

    result = await create_story_engine_artifact(
        session,
        project_id=existing.project_id,
        content=deepcopy(content),
        idempotency_key="engine-attempt-1",
    )

    assert result is existing
    session.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_same_idempotency_key_rejects_different_story_engine_content() -> None:
    content = _content()
    existing = PlanningArtifactVersionModel(
        project_id=uuid4(),
        artifact_type=STORY_ENGINE_ARTIFACT_TYPE,
        version_no=1,
        status="structure_only",
        schema_version="2.0",
        content=content,
        idempotency_key="engine-attempt-1",
    )
    session = AsyncMock()
    session.scalar = AsyncMock(return_value=existing)
    changed = deepcopy(content)
    changed["blocking_codes"] = ["DIFFERENT"]

    with pytest.raises(ValueError, match="different story engine content"):
        await create_story_engine_artifact(
            session,
            project_id=existing.project_id,
            content=changed,
            idempotency_key="engine-attempt-1",
        )


@pytest.mark.asyncio
async def test_resolver_rejects_stale_or_corrupted_engine_lineage() -> None:
    content = _content()
    artifact = PlanningArtifactVersionModel(
        project_id=uuid4(),
        artifact_type=STORY_ENGINE_ARTIFACT_TYPE,
        version_no=1,
        status="structure_only",
        schema_version="2.0",
        content=content,
        idempotency_key="engine-attempt-1",
    )
    session = AsyncMock()
    session.scalar = AsyncMock(return_value=artifact)

    stale = await resolve_latest_story_engine_artifact(
        session,
        project_id=artifact.project_id,
        source_snapshot_hash="new-snapshot",
    )
    assert stale is None

    broken = deepcopy(content)
    meta = broken["_meta"]
    assert isinstance(meta, dict)
    meta["engine_hash"] = "corrupted"
    artifact.content = broken
    session.scalar = AsyncMock(return_value=artifact)
    corrupted = await resolve_latest_story_engine_artifact(
        session,
        project_id=artifact.project_id,
        source_snapshot_hash="snapshot-hash",
    )
    assert corrupted is None


class _WriteSession:
    def __init__(self) -> None:
        self.scalar_results: list[object | None] = [None, 0]
        self.added: list[object] = []

    async def scalar(self, statement: object) -> object | None:
        return self.scalar_results.pop(0)

    def add(self, value: object) -> None:
        self.added.append(value)

    async def flush(self) -> None:
        for value in self.added:
            if getattr(value, "id", None) is None:
                value.id = uuid4()

    @asynccontextmanager
    async def begin_nested(self):
        yield


class _ConcurrentWriteSession(_WriteSession):
    def __init__(self, existing: PlanningArtifactVersionModel) -> None:
        super().__init__()
        self.scalar_results = [None, 0, existing]

    @asynccontextmanager
    async def begin_nested(self):
        raise IntegrityError("concurrent story engine insert", {}, RuntimeError())
        yield


@pytest.mark.asyncio
async def test_concurrent_identical_shadow_insert_reuses_winning_artifact() -> None:
    content = _content()
    project_id = uuid4()
    existing = PlanningArtifactVersionModel(
        id=uuid4(),
        project_id=project_id,
        artifact_type=STORY_ENGINE_ARTIFACT_TYPE,
        version_no=1,
        status="structure_only",
        schema_version="2.0",
        content=deepcopy(content),
        idempotency_key="engine-attempt-1",
    )
    session = _ConcurrentWriteSession(existing)

    result = await create_story_engine_artifact(
        session,
        project_id=project_id,
        content=content,
        idempotency_key="engine-attempt-1",
    )

    assert result is existing


@pytest.mark.asyncio
async def test_same_idempotency_key_reuses_identical_window_artifact() -> None:
    content = _window_content()
    existing = PlanningArtifactVersionModel(
        project_id=uuid4(),
        artifact_type=STORY_ENGINE_WINDOW_ARTIFACT_TYPE,
        version_no=1,
        status="canary_validated",
        schema_version="2.0",
        content=content,
        idempotency_key="window-attempt-1",
    )
    session = AsyncMock()
    session.scalar = AsyncMock(return_value=existing)

    result = await create_story_engine_window_artifact(
        session,
        project_id=existing.project_id,
        content=deepcopy(content),
        idempotency_key="window-attempt-1",
    )

    assert result is existing
    session.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_new_window_artifact_uses_next_project_version() -> None:
    content = _window_content()
    session = _WriteSession()
    session.scalar_results = [None, 3]
    project_id = uuid4()

    result = await create_story_engine_window_artifact(
        session,
        project_id=project_id,
        content=content,
        idempotency_key="window-attempt-4",
        source_run_id=uuid4(),
        notes="rolling canary window",
    )

    assert result in session.added
    assert result.id is not None
    assert result.project_id == project_id
    assert result.version_no == 4
    assert result.status == "canary_validated"
    assert result.idempotency_key == "window-attempt-4"


@pytest.mark.asyncio
async def test_same_idempotency_key_rejects_different_window_content() -> None:
    content = _window_content()
    existing = PlanningArtifactVersionModel(
        project_id=uuid4(),
        artifact_type=STORY_ENGINE_WINDOW_ARTIFACT_TYPE,
        version_no=1,
        status="canary_validated",
        schema_version="2.0",
        content=content,
        idempotency_key="window-attempt-1",
    )
    session = AsyncMock()
    session.scalar = AsyncMock(return_value=existing)
    changed = deepcopy(content)
    changed["diagnostic_note"] = "different valid envelope"

    with pytest.raises(ValueError, match="different story engine window content"):
        await create_story_engine_window_artifact(
            session,
            project_id=existing.project_id,
            content=changed,
            idempotency_key="window-attempt-1",
        )


@pytest.mark.asyncio
async def test_concurrent_identical_window_insert_reuses_winning_artifact() -> None:
    content = _window_content()
    project_id = uuid4()
    existing = PlanningArtifactVersionModel(
        id=uuid4(),
        project_id=project_id,
        artifact_type=STORY_ENGINE_WINDOW_ARTIFACT_TYPE,
        version_no=1,
        status="canary_validated",
        schema_version="2.0",
        content=deepcopy(content),
        idempotency_key="window-attempt-1",
    )
    session = _ConcurrentWriteSession(existing)

    result = await create_story_engine_window_artifact(
        session,
        project_id=project_id,
        content=content,
        idempotency_key="window-attempt-1",
    )

    assert result is existing


@pytest.mark.asyncio
async def test_window_resolver_requires_intact_canary_artifact() -> None:
    content = _window_content()
    artifact = PlanningArtifactVersionModel(
        project_id=uuid4(),
        artifact_type=STORY_ENGINE_WINDOW_ARTIFACT_TYPE,
        version_no=1,
        status="canary_validated",
        schema_version="2.0",
        content=content,
        idempotency_key="window-attempt-1",
    )
    session = AsyncMock()
    session.scalar = AsyncMock(return_value=artifact)

    assert await resolve_latest_story_engine_window_artifact(
        session,
        project_id=artifact.project_id,
    ) is artifact

    broken = deepcopy(content)
    broken["_meta"]["window_hash"] = "corrupted"
    artifact.content = broken
    assert await resolve_latest_story_engine_window_artifact(
        session,
        project_id=artifact.project_id,
    ) is None


@pytest.mark.asyncio
async def test_window_resolver_can_read_intact_shadow_for_observation_only() -> None:
    content = _window_content()
    content["status"] = "shadow_validated"
    content["maturity"] = "shadow_validated"
    content["can_drive_generation"] = False
    artifact = PlanningArtifactVersionModel(
        project_id=uuid4(),
        artifact_type=STORY_ENGINE_WINDOW_ARTIFACT_TYPE,
        version_no=1,
        status="shadow_validated",
        schema_version="2.0",
        content=content,
        idempotency_key="shadow-window-1",
    )
    session = AsyncMock()
    session.scalar = AsyncMock(return_value=artifact)

    assert await resolve_latest_story_engine_window_artifact(
        session,
        project_id=artifact.project_id,
        require_generation_authority=False,
    ) is artifact
    assert await resolve_latest_story_engine_window_artifact(
        session,
        project_id=artifact.project_id,
    ) is None


@pytest.mark.asyncio
async def test_shadow_persistence_writes_needs_replan_without_blocking_generation() -> None:
    session = _WriteSession()
    project = SimpleNamespace(
        id=uuid4(),
        metadata_json={"book_design_snapshot_hash": "book-snapshot"},
    )

    artifact = await persist_legacy_story_engine_shadow(
        session,
        project=project,
        kernel=_kernel(),
        source_run_id=uuid4(),
    )

    assert artifact.status == "needs_replan"
    assert artifact.content["can_drive_generation"] is False
    assert artifact.content["projection_status"] == "needs_replan"
    assert artifact.content["_meta"]["source_snapshot_hash"] == "book-snapshot"
