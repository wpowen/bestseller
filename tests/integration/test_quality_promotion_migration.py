from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
import importlib.util
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from bestseller.domain.enums import DraftPromotionState
from bestseller.infra.db.models import (
    ChapterDraftVersionModel,
    ChapterModel,
    DraftPromotionDecisionModel,
    ProjectModel,
    QualityScoreModel,
)
from bestseller.infra.db.session import create_engine, create_session_factory
from bestseller.services import draft_promotion as promotion_service
from bestseller.services.draft_promotion import (
    mark_candidate_under_review,
    mark_draft_eligible,
    promote_chapter_draft,
    transition_draft_state,
)

pytestmark = pytest.mark.integration


def test_sqlite_migration_roundtrip_preserves_legacy_current_flags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migration_path = (
        Path(__file__).parents[2]
        / "migrations/versions/0035_quality_promotion_contract.py"
    )
    spec = importlib.util.spec_from_file_location("migration_0035", migration_path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = sa.create_engine("sqlite://")
    metadata = sa.MetaData()
    sa.Table("projects", metadata, sa.Column("id", sa.Uuid(), primary_key=True))
    sa.Table("workflow_runs", metadata, sa.Column("id", sa.Uuid(), primary_key=True))
    sa.Table("scene_cards", metadata, sa.Column("id", sa.Uuid(), primary_key=True))
    sa.Table("chapters", metadata, sa.Column("id", sa.Uuid(), primary_key=True))
    scene_drafts = sa.Table(
        "scene_draft_versions",
        metadata,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("scene_card_id", sa.Uuid(), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("is_current", sa.Boolean(), nullable=False),
    )
    chapter_drafts = sa.Table(
        "chapter_draft_versions",
        metadata,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("chapter_id", sa.Uuid(), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("is_current", sa.Boolean(), nullable=False),
    )
    sa.Table(
        "quality_scores",
        metadata,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("target_type", sa.String(32), nullable=False),
        sa.Column("target_id", sa.Uuid(), nullable=False),
        sa.Column("is_current", sa.Boolean(), nullable=False),
        sa.Column("score_overall", sa.Numeric(4, 2), nullable=False),
    )
    project_id = uuid4()
    scene_parent_id = uuid4()
    chapter_parent_id = uuid4()
    with engine.begin() as connection:
        metadata.create_all(connection)
        connection.execute(
            scene_drafts.insert(),
            [
                {
                    "id": uuid4(),
                    "project_id": project_id,
                    "scene_card_id": scene_parent_id,
                    "version_no": 1,
                    "is_current": False,
                },
                {
                    "id": uuid4(),
                    "project_id": project_id,
                    "scene_card_id": scene_parent_id,
                    "version_no": 2,
                    "is_current": True,
                },
            ],
        )
        connection.execute(
            chapter_drafts.insert(),
            {
                "id": uuid4(),
                "project_id": project_id,
                "chapter_id": chapter_parent_id,
                "version_no": 1,
                "is_current": True,
            },
        )
        monkeypatch.setattr(
            migration,
            "op",
            Operations(MigrationContext.configure(connection)),
        )
        migration.upgrade()

        scene_rows = connection.execute(
            sa.text(
                "SELECT is_current, promotion_state FROM scene_draft_versions "
                "ORDER BY version_no"
            )
        ).all()
        chapter_rows = connection.execute(
            sa.text(
                "SELECT is_current, promotion_state FROM chapter_draft_versions "
                "ORDER BY version_no"
            )
        ).all()
        inspector = sa.inspect(connection)
        assert scene_rows == [(0, "legacy_unverified"), (1, "legacy_unverified")]
        assert chapter_rows == [(1, "legacy_unverified")]
        assert {
            index["name"] for index in inspector.get_indexes("scene_draft_versions")
        } >= {"uq_scene_draft_promoted"}
        assert {
            index["name"] for index in inspector.get_indexes("chapter_draft_versions")
        } >= {"uq_chapter_draft_promoted"}

        migration.downgrade()
        assert "promotion_state" not in {
            column["name"]
            for column in sa.inspect(connection).get_columns("scene_draft_versions")
        }


@pytest_asyncio.fixture
async def promotion_db() -> AsyncIterator[
    tuple[AsyncEngine, async_sessionmaker, list[UUID]]
]:
    engine = create_engine()
    if engine.dialect.name != "postgresql":
        await engine.dispose()
        pytest.skip("Promotion locking and migration tests require PostgreSQL")
    project_ids: list[UUID] = []
    try:
        async with engine.connect() as connection:
            version = await connection.scalar(text("SELECT version_num FROM alembic_version"))
        if version != "0035_quality_promotion_contract":
            pytest.fail(f"PostgreSQL schema must be at 0035, found {version!r}")
        yield engine, create_session_factory(engine=engine), project_ids
    finally:
        async with create_session_factory(engine=engine)() as session:
            if project_ids:
                await session.execute(delete(ProjectModel).where(ProjectModel.id.in_(project_ids)))
                await session.commit()
        await engine.dispose()


async def _seed_chapter(
    factory: async_sessionmaker,
    project_ids: list[UUID],
    *,
    promoted_first: bool = False,
    include_first_score: bool = True,
) -> tuple[UUID, UUID, UUID, UUID]:
    project_id = uuid4()
    chapter_id = uuid4()
    first_id = uuid4()
    second_id = uuid4()
    project_ids.append(project_id)
    async with factory() as session:
        session.add(
            ProjectModel(
                id=project_id,
                slug=f"promotion-{project_id.hex}",
                title="Promotion integration fixture",
                genre="test",
                target_word_count=1000,
                target_chapters=1,
                metadata_json={},
            )
        )
        await session.flush()
        session.add(
            ChapterModel(
                id=chapter_id,
                project_id=project_id,
                chapter_number=1,
                title="Chapter",
                chapter_goal="Exercise exact-version promotion.",
                information_revealed=[],
                information_withheld=[],
                foreshadowing_actions={},
                target_word_count=1000,
                metadata_json={},
            )
        )
        await session.flush()
        session.add_all(
            [
                ChapterDraftVersionModel(
                    id=first_id,
                    project_id=project_id,
                    chapter_id=chapter_id,
                    version_no=1,
                    content_md="first",
                    word_count=1,
                    assembled_from_scene_draft_ids=[],
                    is_current=False,
                    promotion_state="promoted" if promoted_first else "candidate",
                    promotion_reason_codes=[],
                    promotion_score=None,
                    promotion_metadata={},
                ),
                ChapterDraftVersionModel(
                    id=second_id,
                    project_id=project_id,
                    chapter_id=chapter_id,
                    version_no=2,
                    content_md="second",
                    word_count=1,
                    assembled_from_scene_draft_ids=[],
                    is_current=True,
                    promotion_state="candidate",
                    promotion_reason_codes=[],
                    promotion_metadata={},
                ),
            ]
        )
        await session.flush()
        if include_first_score:
            session.add(_quality_score(project_id, chapter_id, first_id, 0.90, 1))
        session.add(_quality_score(project_id, chapter_id, second_id, 0.88, 1))
        await session.commit()
    return project_id, chapter_id, first_id, second_id


def _quality_score(
    project_id: UUID,
    chapter_id: UUID,
    draft_id: UUID,
    overall: float,
    evaluation_round: int,
) -> QualityScoreModel:
    return QualityScoreModel(
        project_id=project_id,
        target_type="chapter_draft",
        target_id=chapter_id,
        chapter_draft_version_id=draft_id,
        evaluation_round=evaluation_round,
        judge_key="promotion-integration",
        is_current=False,
        score_overall=overall,
        score_goal=0.86,
        score_conflict=0.86,
        score_emotion=0.86,
        score_dialogue=0.86,
        score_style=0.86,
        score_hook=0.86,
        evidence_summary={"hard_gates_passed": True, "blocking_codes": []},
    )


async def test_promotion_uses_exact_scores_preserves_current_and_audits_replacement(
    promotion_db: tuple[AsyncEngine, async_sessionmaker, list[UUID]],
) -> None:
    _, factory, project_ids = promotion_db
    project_id, chapter_id, first_id, second_id = await _seed_chapter(
        factory, project_ids
    )

    async with factory() as session:
        first = await promote_chapter_draft(
            session,
            project_id=project_id,
            chapter_id=chapter_id,
            judge_key="promotion-integration",
        )
        await session.commit()
    assert first.changed is True
    assert first.promoted_draft_id == first_id

    async with factory() as session:
        audit_count_before = len(
            (
                await session.scalars(
                    select(DraftPromotionDecisionModel).where(
                        DraftPromotionDecisionModel.project_id == project_id
                    )
                )
            ).all()
        )
        unchanged = await promote_chapter_draft(
            session,
            project_id=project_id,
            chapter_id=chapter_id,
            judge_key="promotion-integration",
        )
        await session.commit()
    assert unchanged.changed is False
    assert unchanged.reason == "incumbent_is_better_or_unscored"

    async with factory() as session:
        session.add(_quality_score(project_id, chapter_id, second_id, 0.95, 2))
        await session.commit()
    async with factory() as session:
        replaced = await promote_chapter_draft(
            session,
            project_id=project_id,
            chapter_id=chapter_id,
            judge_key="promotion-integration",
        )
        await session.commit()
    assert replaced.changed is True
    assert replaced.promoted_draft_id == second_id
    assert replaced.incumbent_draft_id == first_id

    async with factory() as session:
        drafts = {
            draft.id: draft
            for draft in (
                await session.scalars(
                    select(ChapterDraftVersionModel).where(
                        ChapterDraftVersionModel.chapter_id == chapter_id
                    )
                )
            ).all()
        }
        decisions = (
            await session.scalars(
                select(DraftPromotionDecisionModel)
                .where(DraftPromotionDecisionModel.project_id == project_id)
                .order_by(DraftPromotionDecisionModel.created_at)
            )
        ).all()
    assert drafts[first_id].promotion_state == "superseded"
    assert drafts[second_id].promotion_state == "promoted"
    assert drafts[first_id].is_current is False
    assert drafts[second_id].is_current is True
    assert len(decisions) == audit_count_before + 4
    assert [
        (decision.from_state, decision.to_state)
        for decision in decisions
        if decision.chapter_draft_version_id == first_id
    ] == [
        ("candidate", "under_review"),
        ("under_review", "eligible"),
        ("eligible", "promoted"),
        ("promoted", "superseded"),
    ]


async def test_unscored_promoted_incumbent_is_preserved_without_audit(
    promotion_db: tuple[AsyncEngine, async_sessionmaker, list[UUID]],
) -> None:
    _, factory, project_ids = promotion_db
    project_id, chapter_id, first_id, _ = await _seed_chapter(
        factory,
        project_ids,
        promoted_first=True,
        include_first_score=False,
    )

    async with factory() as session:
        outcome = await promote_chapter_draft(
            session,
            project_id=project_id,
            chapter_id=chapter_id,
            judge_key="promotion-integration",
        )
        await session.commit()
    assert outcome.changed is False
    assert outcome.reason == "incumbent_is_better_or_unscored"
    assert outcome.promoted_draft_id == first_id

    async with factory() as session:
        promoted = (
            await session.scalars(
                select(ChapterDraftVersionModel).where(
                    ChapterDraftVersionModel.chapter_id == chapter_id,
                    ChapterDraftVersionModel.promotion_state == "promoted",
                )
            )
        ).one()
        decisions = (
            await session.scalars(
                select(DraftPromotionDecisionModel).where(
                    DraftPromotionDecisionModel.project_id == project_id
                )
            )
        ).all()
    assert promoted.id == first_id
    assert decisions == []


async def test_no_matching_judge_score_preserves_promoted_incumbent(
    promotion_db: tuple[AsyncEngine, async_sessionmaker, list[UUID]],
) -> None:
    _, factory, project_ids = promotion_db
    project_id, chapter_id, first_id, _ = await _seed_chapter(
        factory,
        project_ids,
        promoted_first=True,
        include_first_score=False,
    )

    async with factory() as session:
        outcome = await promote_chapter_draft(
            session,
            project_id=project_id,
            chapter_id=chapter_id,
            judge_key="judge-with-no-scores",
        )
        await session.commit()
    assert outcome.changed is False
    assert outcome.reason == "no_eligible_candidate"
    assert outcome.promoted_draft_id == first_id
    assert outcome.incumbent_draft_id == first_id


async def test_replacement_savepoint_rolls_back_incumbent_supersede(
    promotion_db: tuple[AsyncEngine, async_sessionmaker, list[UUID]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, factory, project_ids = promotion_db
    project_id, chapter_id, first_id, second_id = await _seed_chapter(
        factory, project_ids
    )
    async with factory() as session:
        initial = await promote_chapter_draft(
            session,
            project_id=project_id,
            chapter_id=chapter_id,
            judge_key="promotion-integration",
        )
        await session.commit()
    assert initial.promoted_draft_id == first_id

    async with factory() as session:
        session.add(_quality_score(project_id, chapter_id, second_id, 0.95, 2))
        await session.commit()

    async def fail_after_incumbent_flush(*args, **kwargs) -> None:
        raise RuntimeError("simulated candidate promotion failure")

    monkeypatch.setattr(
        promotion_service,
        "_advance_to_promoted",
        fail_after_incumbent_flush,
    )
    async with factory() as session:
        with pytest.raises(RuntimeError, match="simulated candidate promotion failure"):
            await promote_chapter_draft(
                session,
                project_id=project_id,
                chapter_id=chapter_id,
                judge_key="promotion-integration",
            )
        await session.commit()

    async with factory() as session:
        drafts = {
            draft.id: draft.promotion_state
            for draft in (
                await session.scalars(
                    select(ChapterDraftVersionModel).where(
                        ChapterDraftVersionModel.chapter_id == chapter_id
                    )
                )
            ).all()
        }
        decisions = (
            await session.scalars(
                select(DraftPromotionDecisionModel).where(
                    DraftPromotionDecisionModel.project_id == project_id
                )
            )
        ).all()
    assert drafts[first_id] == "promoted"
    assert drafts[second_id] == "candidate"
    assert len(decisions) == 3


async def test_service_rejects_blank_judge_and_mismatched_exact_score(
    promotion_db: tuple[AsyncEngine, async_sessionmaker, list[UUID]],
) -> None:
    _, factory, project_ids = promotion_db
    project_id, chapter_id, first_id, second_id = await _seed_chapter(
        factory, project_ids
    )
    async with factory() as session:
        with pytest.raises(ValueError, match="judge_key is required"):
            await promote_chapter_draft(
                session,
                project_id=project_id,
                chapter_id=chapter_id,
                judge_key=" ",
            )
        await mark_candidate_under_review(
            session,
            project_id=project_id,
            draft_kind="chapter",
            draft_id=first_id,
        )
        second_score_id = await session.scalar(
            select(QualityScoreModel.id).where(
                QualityScoreModel.chapter_draft_version_id == second_id
            )
        )
        assert second_score_id is not None
        with pytest.raises(ValueError, match="Exact draft quality score not found"):
            await mark_draft_eligible(
                session,
                project_id=project_id,
                draft_kind="chapter",
                draft_id=first_id,
                quality_score_id=second_score_id,
            )
        await session.rollback()


async def test_parent_lock_serializes_concurrent_promotions(
    promotion_db: tuple[AsyncEngine, async_sessionmaker, list[UUID]],
) -> None:
    _, factory, project_ids = promotion_db
    project_id, chapter_id, first_id, _ = await _seed_chapter(factory, project_ids)

    async def promote_once():
        async with factory() as session:
            outcome = await promote_chapter_draft(
                session,
                project_id=project_id,
                chapter_id=chapter_id,
                judge_key="promotion-integration",
            )
            await session.commit()
            return outcome

    outcomes = await asyncio.gather(promote_once(), promote_once())
    assert sum(outcome.changed for outcome in outcomes) == 1

    async with factory() as session:
        promoted_ids = (
            await session.scalars(
                select(ChapterDraftVersionModel.id).where(
                    ChapterDraftVersionModel.chapter_id == chapter_id,
                    ChapterDraftVersionModel.promotion_state == "promoted",
                )
            )
        ).all()
    assert promoted_ids == [first_id]


async def test_human_override_is_complete_audited_and_idempotent(
    promotion_db: tuple[AsyncEngine, async_sessionmaker, list[UUID]],
) -> None:
    _, factory, project_ids = promotion_db
    project_id, _, first_id, _ = await _seed_chapter(factory, project_ids)
    async with factory() as session:
        draft = await session.get(ChapterDraftVersionModel, first_id)
        assert draft is not None
        draft.promotion_state = DraftPromotionState.REJECTED.value
        await session.commit()

    async with factory() as session:
        changed = await transition_draft_state(
            session,
            project_id=project_id,
            draft_kind="chapter",
            draft_id=first_id,
            to_state=DraftPromotionState.UNDER_REVIEW,
            decision_source="human_override",
            actor="editor@example.com",
            reason="Manual review found the automated rejection was invalid.",
            evidence={"manual_review_id": "manual-1"},
            reason_codes=["editor_reopened"],
        )
        repeated = await transition_draft_state(
            session,
            project_id=project_id,
            draft_kind="chapter",
            draft_id=first_id,
            to_state=DraftPromotionState.UNDER_REVIEW,
            decision_source="human_override",
            actor="editor@example.com",
            reason="Manual review found the automated rejection was invalid.",
            evidence={"manual_review_id": "manual-1"},
            reason_codes=["editor_reopened"],
        )
        await session.commit()
    assert changed is True
    assert repeated is False

    async with factory() as session:
        decisions = (
            await session.scalars(
                select(DraftPromotionDecisionModel).where(
                    DraftPromotionDecisionModel.chapter_draft_version_id == first_id
                )
            )
        ).all()
    assert len(decisions) == 1
    assert decisions[0].decision_source == "human_override"
    assert decisions[0].actor == "editor@example.com"
    assert decisions[0].reason
    assert decisions[0].evidence_json == {"manual_review_id": "manual-1"}
