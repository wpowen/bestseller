from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from bestseller.infra.db.models import (
    ChapterDraftVersionModel,
    ChapterModel,
    ProjectModel,
    QualityScoreModel,
)
from bestseller.infra.db.session import create_engine, create_session_factory
from bestseller.services.pipelines import _promote_reviewed_chapter_draft

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def effect_gate_db() -> AsyncIterator[tuple[async_sessionmaker, list[UUID]]]:
    engine: AsyncEngine = create_engine()
    if engine.dialect.name != "postgresql":
        await engine.dispose()
        pytest.skip("Story-effect promotion tests require PostgreSQL")
    project_ids: list[UUID] = []
    try:
        yield create_session_factory(engine=engine), project_ids
    finally:
        async with create_session_factory(engine=engine)() as session:
            if project_ids:
                await session.execute(delete(ProjectModel).where(ProjectModel.id.in_(project_ids)))
                await session.commit()
        await engine.dispose()


async def _seed_effect_candidate(
    factory: async_sessionmaker,
    project_ids: list[UUID],
    *,
    selected: bool,
) -> tuple[UUID, UUID, UUID]:
    project_id, chapter_id, draft_id = uuid4(), uuid4(), uuid4()
    project_ids.append(project_id)
    async with factory() as session:
        session.add(
            ProjectModel(
                id=project_id,
                slug=f"effect-gate-{project_id.hex}",
                title="Effect gate integration fixture",
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
                title="Effect gate",
                chapter_goal="Verify selected story effect promotion gating.",
                information_revealed=[],
                information_withheld=[],
                foreshadowing_actions={},
                target_word_count=1000,
                metadata_json=(
                    {"selected_effect_skills": {"primary": "comedy_engine"}}
                    if selected
                    else {}
                ),
            )
        )
        await session.flush()
        session.add(
            ChapterDraftVersionModel(
                id=draft_id,
                project_id=project_id,
                chapter_id=chapter_id,
                version_no=1,
                content_md="双方完成谈判, 局势保持原样。",
                word_count=14,
                assembled_from_scene_draft_ids=[],
                is_current=True,
                promotion_state="candidate",
                promotion_reason_codes=[],
                promotion_metadata={},
            )
        )
        await session.flush()
        session.add(
            QualityScoreModel(
                project_id=project_id,
                target_type="chapter_draft",
                target_id=chapter_id,
                chapter_draft_version_id=draft_id,
                evaluation_round=1,
                judge_key="effect-gate-integration",
                is_current=True,
                score_overall=0.95,
                score_goal=0.95,
                score_conflict=0.95,
                score_emotion=0.95,
                score_style=0.95,
                evidence_summary={"hard_gates_passed": True},
            )
        )
        await session.commit()
    return project_id, chapter_id, draft_id


async def test_unrealized_selected_effect_blocks_promotion_and_keeps_no_current(
    effect_gate_db: tuple[async_sessionmaker, list[UUID]],
) -> None:
    factory, project_ids = effect_gate_db
    project_id, chapter_id, draft_id = await _seed_effect_candidate(
        factory, project_ids, selected=True
    )

    async with factory() as session:
        project = await session.get(ProjectModel, project_id)
        chapter = await session.get(ChapterModel, chapter_id)
        draft = await session.get(ChapterDraftVersionModel, draft_id)
        quality = await session.scalar(
            select(QualityScoreModel).where(
                QualityScoreModel.chapter_draft_version_id == draft_id
            )
        )
        assert project is not None and chapter is not None and draft is not None
        assert quality is not None

        promoted, reason = await _promote_reviewed_chapter_draft(
            session,
            settings=None,  # effect gate returns before settings are consulted
            project=project,
            chapter=chapter,
            draft=draft,
            quality=quality,
            workflow_run_id=None,
        )
        await session.commit()

        assert promoted is False
        assert reason == "story_effect_realization_blocked"
        assert draft.is_current is False
        assert chapter.production_state == "blocked"
        assert chapter.metadata_json["effect_realization_blocked"] is True
        report = draft.promotion_metadata["effect_realization_gate"]
        assert report["selected_effects"] == ["comedy_engine"]
        assert report["passed"] is False


async def test_legacy_project_without_selected_effect_is_not_changed(
    effect_gate_db: tuple[async_sessionmaker, list[UUID]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory, project_ids = effect_gate_db
    project_id, chapter_id, draft_id = await _seed_effect_candidate(
        factory, project_ids, selected=False
    )

    async def source_ok(*args, **kwargs):
        return True, "legacy"

    monkeypatch.setattr(
        "bestseller.services.pipelines._chapter_source_mode_is_promotable",
        source_ok,
    )
    monkeypatch.setattr(
        "bestseller.services.pipelines.resolve_story_engine_rollout_decision_from_db",
        _legacy_rollout,
    )

    async with factory() as session:
        project = await session.get(ProjectModel, project_id)
        chapter = await session.get(ChapterModel, chapter_id)
        draft = await session.get(ChapterDraftVersionModel, draft_id)
        quality = await session.scalar(
            select(QualityScoreModel).where(
                QualityScoreModel.chapter_draft_version_id == draft_id
            )
        )
        assert project is not None and chapter is not None and draft is not None
        assert quality is not None

        promoted, reason = await _promote_reviewed_chapter_draft(
            session,
            settings=None,
            project=project,
            chapter=chapter,
            draft=draft,
            quality=quality,
            workflow_run_id=None,
        )

        assert promoted is True
        assert reason == "legacy"


async def _legacy_rollout(*args, **kwargs):
    return type("Decision", (), {"effective_mode": "legacy"})()


async def test_unrealized_effect_candidate_does_not_replace_incumbent_current(
    effect_gate_db: tuple[async_sessionmaker, list[UUID]],
) -> None:
    factory, project_ids = effect_gate_db
    project_id, chapter_id, draft_id = await _seed_effect_candidate(
        factory, project_ids, selected=True
    )

    async with factory() as session:
        candidate = await session.get(ChapterDraftVersionModel, draft_id)
        assert candidate is not None
        candidate.is_current = False
        candidate.version_no = 2
        await session.flush()
        incumbent = ChapterDraftVersionModel(
            project_id=project_id,
            chapter_id=chapter_id,
            version_no=1,
            content_md="旧稿已经完整兑现本章承诺。",
            word_count=13,
            assembled_from_scene_draft_ids=[],
            is_current=True,
            promotion_state="promoted",
            promotion_reason_codes=[],
            promotion_metadata={},
        )
        session.add(incumbent)
        await session.commit()
        incumbent_id = incumbent.id

    async with factory() as session:
        project = await session.get(ProjectModel, project_id)
        chapter = await session.get(ChapterModel, chapter_id)
        candidate = await session.get(ChapterDraftVersionModel, draft_id)
        quality = await session.scalar(
            select(QualityScoreModel).where(
                QualityScoreModel.chapter_draft_version_id == draft_id
            )
        )
        assert project is not None and chapter is not None and candidate is not None
        assert quality is not None

        promoted, reason = await _promote_reviewed_chapter_draft(
            session,
            settings=None,
            project=project,
            chapter=chapter,
            draft=candidate,
            quality=quality,
            workflow_run_id=None,
        )
        await session.commit()

    async with factory() as session:
        incumbent = await session.get(ChapterDraftVersionModel, incumbent_id)
        candidate = await session.get(ChapterDraftVersionModel, draft_id)
        assert promoted is False
        assert reason == "story_effect_realization_blocked"
        assert incumbent is not None and incumbent.is_current is True
        assert candidate is not None and candidate.is_current is False
