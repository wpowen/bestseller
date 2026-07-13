"""Regression test for the stalled-scene-rewrite "ship the best attempt" fix.

Real production evidence (2026-07-13, book custom-xianxia-1783881021, chapter 8
scene 2): a scene ran 11 bounded rewrite attempts. Scores were
0.66/0.66/0.66/0.66/0.71/0.68/0.67/0.67/0.65/0.66/0.63 — the *best* attempt
(v5, 0.71) was neither first nor last, and the *shipped* draft (v11, is_current)
was the single worst-scoring attempt of the whole loop, and it contained a
self-contradicting duplicated beat. ``rewrite_scene_from_task`` always flips
``is_current`` to the newest attempt with no score comparison, and the old
quarantine path never corrected this before shipping the chapter. See
``_promote_best_scoring_scene_draft_on_stall`` in
``bestseller.services.pipelines``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from bestseller.infra.db.models import (
    ChapterModel,
    ProjectModel,
    QualityScoreModel,
    SceneCardModel,
    SceneDraftVersionModel,
)
from bestseller.infra.db.session import create_engine, create_session_factory
from bestseller.services.pipelines import _promote_best_scoring_scene_draft_on_stall

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def scene_promotion_db() -> AsyncIterator[tuple[async_sessionmaker, list[UUID]]]:
    engine: AsyncEngine = create_engine()
    if engine.dialect.name != "postgresql":
        await engine.dispose()
        pytest.skip("Scene stalled-rewrite promotion tests require PostgreSQL")
    project_ids: list[UUID] = []
    try:
        yield create_session_factory(engine=engine), project_ids
    finally:
        async with create_session_factory(engine=engine)() as session:
            if project_ids:
                await session.execute(delete(ProjectModel).where(ProjectModel.id.in_(project_ids)))
                await session.commit()
        await engine.dispose()


async def _seed_scene_with_attempts(
    factory: async_sessionmaker,
    project_ids: list[UUID],
    *,
    scores: list[float],
) -> tuple[SceneCardModel, list[SceneDraftVersionModel]]:
    """Seed one scene with N draft attempts (in order) scored per ``scores``.

    The LAST attempt is left as ``is_current`` and its score is written with
    ``QualityScoreModel.is_current=True`` (matching how the review loop's own
    quality score is flagged) while earlier attempts' scores are historical
    (``is_current=False``), matching production behaviour exactly.
    """

    project_id = uuid4()
    chapter_id = uuid4()
    scene_id = uuid4()
    project_ids.append(project_id)
    async with factory() as session:
        session.add(
            ProjectModel(
                id=project_id,
                slug=f"scene-stall-{project_id.hex}",
                title="Scene stalled-rewrite fixture",
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
                chapter_goal="Exercise stalled scene rewrite promotion.",
                information_revealed=[],
                information_withheld=[],
                foreshadowing_actions={},
                target_word_count=1000,
                metadata_json={},
            )
        )
        await session.flush()
        scene = SceneCardModel(
            id=scene_id,
            project_id=project_id,
            chapter_id=chapter_id,
            scene_number=1,
            scene_type="action",
            purpose={},
            entry_state={},
            exit_state={},
            metadata_json={},
        )
        session.add(scene)
        await session.flush()

        drafts: list[SceneDraftVersionModel] = []
        for index, score in enumerate(scores, start=1):
            is_last = index == len(scores)
            draft = SceneDraftVersionModel(
                project_id=project_id,
                scene_card_id=scene_id,
                version_no=index,
                content_md=f"attempt {index} content",
                word_count=100,
                is_current=is_last,
                generation_params={},
            )
            session.add(draft)
            await session.flush()
            session.add(
                QualityScoreModel(
                    project_id=project_id,
                    target_type="scene_draft",
                    target_id=scene_id,
                    scene_draft_version_id=draft.id,
                    evaluation_round=1,
                    judge_key="scene-stall-integration",
                    is_current=is_last,
                    score_overall=score,
                    evidence_summary={},
                )
            )
            drafts.append(draft)
        await session.commit()
        for draft in drafts:
            await session.refresh(draft)
    return scene, drafts


async def test_promotes_best_scoring_attempt_not_most_recent(
    scene_promotion_db: tuple[async_sessionmaker, list[UUID]],
) -> None:
    factory, project_ids = scene_promotion_db
    # Mirrors the real 11-attempt production loop: the best attempt (index 5,
    # score 0.71) is neither first nor last; the shipped/current attempt
    # (index 11, score 0.63) is the single worst score of the whole loop.
    scores = [0.66, 0.66, 0.66, 0.66, 0.71, 0.68, 0.67, 0.67, 0.65, 0.66, 0.63]
    scene, drafts = await _seed_scene_with_attempts(factory, project_ids, scores=scores)
    current_draft = drafts[-1]
    best_draft = drafts[4]  # index 5 (0-based 4), score 0.71

    async with factory() as session:
        scene = await session.get(SceneCardModel, scene.id)
        current_draft = await session.get(SceneDraftVersionModel, current_draft.id)
        current_quality = await session.scalar(
            select(QualityScoreModel).where(
                QualityScoreModel.scene_draft_version_id == current_draft.id
            )
        )
        assert current_quality is not None

        promoted_draft, promoted_quality = await _promote_best_scoring_scene_draft_on_stall(
            session,
            scene=scene,
            current_draft=current_draft,
            current_quality=current_quality,
        )
        await session.commit()

        assert promoted_draft.id == best_draft.id
        assert float(promoted_quality.score_overall) == pytest.approx(0.71)

    async with factory() as session:
        refreshed_best = await session.get(SceneDraftVersionModel, best_draft.id)
        refreshed_last = await session.get(SceneDraftVersionModel, current_draft.id)
        assert refreshed_best.is_current is True
        assert refreshed_last.is_current is False
        # Exactly one is_current draft remains for the scene.
        current_count = await session.scalar(
            select(func.count()).select_from(SceneDraftVersionModel).where(
                SceneDraftVersionModel.scene_card_id == scene.id,
                SceneDraftVersionModel.is_current.is_(True),
            )
        )
        assert current_count == 1


async def test_noop_when_current_attempt_is_already_the_best(
    scene_promotion_db: tuple[async_sessionmaker, list[UUID]],
) -> None:
    factory, project_ids = scene_promotion_db
    scores = [0.55, 0.60, 0.72]  # current (last) attempt is also the best
    scene, drafts = await _seed_scene_with_attempts(factory, project_ids, scores=scores)
    current_draft = drafts[-1]

    async with factory() as session:
        scene = await session.get(SceneCardModel, scene.id)
        current_draft = await session.get(SceneDraftVersionModel, current_draft.id)
        current_quality = await session.scalar(
            select(QualityScoreModel).where(
                QualityScoreModel.scene_draft_version_id == current_draft.id
            )
        )
        assert current_quality is not None

        promoted_draft, promoted_quality = await _promote_best_scoring_scene_draft_on_stall(
            session,
            scene=scene,
            current_draft=current_draft,
            current_quality=current_quality,
        )
        await session.commit()

        assert promoted_draft.id == current_draft.id
        assert promoted_quality.id == current_quality.id

    async with factory() as session:
        refreshed = await session.get(SceneDraftVersionModel, current_draft.id)
        assert refreshed.is_current is True
