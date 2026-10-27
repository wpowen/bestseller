"""Real SQLite task producers; a technical plan marker is not a quality grade."""

from copy import deepcopy

import pytest
from sqlalchemy import func, select, text, update
from test_rewrite_outline_binding import db as db
from test_rewrite_outline_binding import (
    forbid_external_execution as forbid_external_execution,
)
from test_rewrite_outline_binding import marker, seed

from bestseller.infra.db.models import ChapterDraftVersionModel, ChapterModel, RewriteTaskModel
from bestseller.services import fanqie_short_pipeline as pipeline
from bestseller.services import reviews
from bestseller.services.fanqie_short_ranking_gate import (
    FanqieRankingFinding,
    FanqieRankingGateReport,
)
from bestseller.services.rewrite_patch import text_hash

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]
PRODUCERS = ("_persist_fanqie_ranking_gate_failure", "_persist_fanqie_short_v2_gate_failure")


def report(*, passed=False, severity="critical", whole=False):
    return FanqieRankingGateReport(
        passed=passed,
        phase="whole_story",
        findings=(
            FanqieRankingFinding(
                code="first_screen_pressure_missing",
                severity=severity,
                message="Review the supplied opening without adding invented facts.",
                evidence="retained",
                phase="payoff" if whole else "opening",
                target="whole_story" if whole else "opening",
            ),
        ),
    )


async def produce(name, case, **kwargs):
    return await getattr(pipeline, name)(
        case.session,
        case.project,
        report=report(**kwargs),
        unlock_segment=2,
    )


async def tasks(session):
    return list(
        await session.scalars(select(RewriteTaskModel).order_by(RewriteTaskModel.created_at))
    )


@pytest.mark.parametrize("producer", PRODUCERS)
async def test_new_marked_task_binds_exact_body_and_plan_without_rebinding_old_task(db, producer):
    async with db() as session:
        case = await seed(session)
        old = deepcopy(
            (case.task.metadata_json, case.task.instructions, case.task.status, case.task.attempts)
        )
        assert await produce(producer, case) == 1
        await session.commit()
        fresh = next(t for t in await tasks(session) if t.id != case.task.id)
        assert fresh.metadata_json["draft_id"] == str(case.draft.id)
        assert fresh.metadata_json["parent_draft_id"] == str(case.draft.id)
        assert fresh.metadata_json["parent_draft_hash"] == text_hash(case.draft.content_md)
        assert fresh.metadata_json["source_plan_binding"] == case.current
        assert fresh.status == "pending" and fresh.attempts == 0
        assert fresh.trigger_source_id == case.chapter.id and fresh.project_id == case.project.id
        if producer.endswith("v2_gate_failure"):
            assert fresh.metadata_json["worker_route"]
            assert "fanqie_short_worker_route" in fresh.context_required
        await reviews._validate_rewrite_task_parent(session, fresh, case.draft)
        await session.refresh(case.task)
        assert (
            case.task.metadata_json,
            case.task.instructions,
            case.task.status,
            case.task.attempts,
        ) == old


@pytest.mark.parametrize("producer", PRODUCERS)
async def test_unmarked_legacy_chapter_keeps_unbound_creation_even_without_current_draft(
    db, producer
):
    async with db() as session:
        case = await seed(session, marked=False)
        case.draft.is_current = False
        await session.commit()
        assert await produce(producer, case) == 1
        fresh = next(t for t in await tasks(session) if t.id != case.task.id)
        assert all(
            k not in fresh.metadata_json
            for k in (
                "draft_id",
                "parent_draft_id",
                "parent_draft_hash",
                "source_plan_binding",
            )
        )
        assert case.chapter.status == "revision" and case.chapter.production_state == "blocked"


@pytest.mark.parametrize("producer", PRODUCERS)
@pytest.mark.parametrize("fault", ["missing", "duplicate", "foreign_owner", "empty"])
async def test_marked_source_must_be_unique_current_owned_nonempty_before_new_tasks(
    db, producer, fault
):
    async with db() as session:
        case = await seed(session)
        if fault == "missing":
            case.draft.is_current = False
        elif fault == "duplicate":
            # Remove this fixture-only index to probe a corrupt historical state.
            await session.execute(text("DROP INDEX stage_test_current"))
            session.add(
                ChapterDraftVersionModel(
                    project_id=case.project.id,
                    chapter_id=case.chapter.id,
                    version_no=2,
                    content_md="A second conflicting current body.",
                    is_current=True,
                )
            )
        elif fault == "foreign_owner":
            from uuid import uuid4

            case.draft.project_id = uuid4()
        else:
            case.draft.content_md = " \n "
        await session.commit()
        old = deepcopy(case.task.metadata_json)
        with pytest.raises(ValueError):
            await produce(producer, case)
        await session.flush()
        assert len(await tasks(session)) == 1 and case.task.metadata_json == old


@pytest.mark.parametrize("producer", PRODUCERS)
@pytest.mark.parametrize("invalid", [None, {}, {"artifact_id": "not proof"}])
async def test_present_invalid_marker_is_not_legacy_fallback(db, producer, invalid):
    async with db() as session:
        case = await seed(session)
        case.chapter.metadata_json = {"outline_source_admission": invalid, "raw": None}
        await session.commit()
        with pytest.raises(ValueError, match="rewrite_outline_marker_invalid"):
            await produce(producer, case)
        await session.flush()
        assert len(await tasks(session)) == 1


@pytest.mark.parametrize("producer", PRODUCERS)
@pytest.mark.parametrize("was_marked", [False, True])
async def test_fresh_columns_bind_changed_body_and_marker_despite_stale_identity_map(
    db, producer, was_marked
):
    async with db() as session:
        case = await seed(session, marked=was_marked)
        current = marker()
        body = "The current revised body retains its actual measurements."
        await session.execute(
            update(ChapterModel)
            .where(ChapterModel.id == case.chapter.id)
            .values(
                metadata_json={"outline_source_admission": current, "raw": {"unknown": None}},
            )
            .execution_options(synchronize_session=False)
        )
        await session.execute(
            update(ChapterDraftVersionModel)
            .where(ChapterDraftVersionModel.id == case.draft.id)
            .values(
                content_md=body,
            )
            .execution_options(synchronize_session=False)
        )
        await session.commit()
        assert case.draft.content_md != body
        assert await produce(producer, case) == 1
        fresh = next(t for t in await tasks(session) if t.id != case.task.id)
        assert fresh.metadata_json["source_plan_binding"] == current
        assert fresh.metadata_json["parent_draft_hash"] == text_hash(body)
        await session.flush()
        await session.refresh(case.chapter)
        assert case.chapter.metadata_json["outline_source_admission"] == current
        assert case.chapter.metadata_json["raw"] == {"unknown": None}
        await session.refresh(case.draft)
        await reviews._validate_rewrite_task_parent(session, fresh, case.draft)


@pytest.mark.parametrize("producer", PRODUCERS)
async def test_late_bad_marked_source_leaves_no_partial_new_tasks(db, producer):
    async with db() as session:
        case = await seed(session)
        case.chapter.status = "drafted"
        case.chapter.production_state = "awaiting_review"
        second = ChapterModel(
            project_id=case.project.id,
            chapter_number=2,
            chapter_goal="Unchanged",
            metadata_json={"outline_source_admission": marker()},
        )
        session.add(second)
        await session.commit()
        # Both routing functions visit every existing chapter for this finding.
        finding_report = report(whole=True)
        if producer.endswith("v2_gate_failure"):
            # Use a real non-opening code to retain the production routing rules.
            finding_report = FanqieRankingGateReport(
                False,
                "whole_story",
                (
                    FanqieRankingFinding(
                        "payoff_density_too_low",
                        "critical",
                        "Inspect the whole piece.",
                        "retained",
                        "payoff",
                        "whole_story",
                    ),
                ),
            )
        before = deepcopy((case.task.metadata_json, case.chapter.metadata_json))
        with pytest.raises(ValueError):
            await getattr(pipeline, producer)(
                session, case.project, report=finding_report, unlock_segment=2
            )
        await session.flush()
        assert len(await tasks(session)) == 1
        assert (case.task.metadata_json, case.chapter.metadata_json) == before
        assert (
            case.chapter.status == "drafted" and case.chapter.production_state == "awaiting_review"
        )


@pytest.mark.parametrize("producer", PRODUCERS)
async def test_dirty_marked_source_is_not_overwritten_or_bound_before_flush(db, producer):
    async with db() as session:
        case = await seed(session)
        case.draft.content_md = "A caller's unflushed replacement."
        with pytest.raises(ValueError, match="unflushed"):
            await produce(producer, case)
        assert case.draft.content_md == "A caller's unflushed replacement."
        with session.no_autoflush:
            assert await session.scalar(select(func.count()).select_from(RewriteTaskModel)) == 1


@pytest.mark.parametrize("producer", PRODUCERS)
@pytest.mark.parametrize("mode", ["passed", "audit"])
async def test_no_task_required_does_not_start_body_or_plan_binding(db, producer, mode):
    async with db() as session:
        case = await seed(session)
        case.chapter.metadata_json = {"outline_source_admission": None}
        case.draft.is_current = False
        await session.commit()
        assert (
            await produce(
                producer,
                case,
                passed=mode == "passed",
                severity="critical" if mode == "passed" else "medium",
            )
            == 0
        )
        assert len(await tasks(session)) == 1


@pytest.mark.parametrize("producer", PRODUCERS)
async def test_stale_marked_cache_cannot_restore_removed_database_marker(db, producer):
    async with db() as session:
        case = await seed(session)
        await session.execute(
            update(ChapterModel)
            .where(ChapterModel.id == case.chapter.id)
            .values(
                metadata_json={"raw": {"current_legacy_value": None}},
            )
            .execution_options(synchronize_session=False)
        )
        await session.commit()
        assert "outline_source_admission" in case.chapter.metadata_json
        assert await produce(producer, case) == 1
        await session.commit()
        await session.refresh(case.chapter)
        assert "outline_source_admission" not in case.chapter.metadata_json
        assert case.chapter.metadata_json["raw"] == {"current_legacy_value": None}
        fresh = next(t for t in await tasks(session) if t.id != case.task.id)
        assert "source_plan_binding" not in fresh.metadata_json


@pytest.mark.parametrize("producer", PRODUCERS)
async def test_unflushed_new_marked_chapter_cannot_enter_after_preflight(db, producer):
    async with db() as session:
        case = await seed(session)
        case.project.target_chapters = 2
        await session.commit()
        session.add(
            ChapterModel(
                project_id=case.project.id,
                chapter_number=2,
                chapter_goal="Pending new source",
                metadata_json={"outline_source_admission": marker()},
            )
        )
        whole_report = FanqieRankingGateReport(
            False,
            "whole_story",
            (
                FanqieRankingFinding(
                    "payoff_density_too_low",
                    "critical",
                    "Inspect all chapters.",
                    "retained",
                    "payoff",
                    "whole_story",
                ),
            ),
        )
        with pytest.raises(ValueError, match="unflushed"):
            await getattr(pipeline, producer)(
                session, case.project, report=whole_report, unlock_segment=2
            )
        with session.no_autoflush:
            assert await session.scalar(select(func.count()).select_from(RewriteTaskModel)) == 1
