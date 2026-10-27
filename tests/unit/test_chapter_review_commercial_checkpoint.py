"""Review orchestration releases its writes before independent commercial sessions."""

import asyncio
from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.schema import CreateTable
from test_adopted_knowledge_lineage import _jsonb  # noqa: F401

from bestseller.domain.context import ChapterWriterContextPacket
from bestseller.domain.llm_quality_judge import LLMQualityJudgeResult
from bestseller.infra.db.models import (
    ChapterDraftVersionModel,
    ChapterModel,
    ProjectModel,
    QualityScoreModel,
    ReviewReportModel,
    RewriteTaskModel,
    SceneCardModel,
    StyleGuideModel,
)
from bestseller.services import chapter_llm_quality_judge as commercial
from bestseller.services import commercial_judge_recovery as recovery
from bestseller.services import commercial_review_context as history
from bestseller.services import reviews
from bestseller.settings import load_settings

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.fixture
async def review_db(tmp_path, monkeypatch):
    path = tmp_path / "commercial_checkpoint_test.sqlite3"
    assert "test" in path.name
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{path}",
        connect_args={"timeout": 0.2},
    )
    async with engine.begin() as conn:
        for model in (
            ProjectModel,
            ChapterModel,
            ChapterDraftVersionModel,
            SceneCardModel,
            StyleGuideModel,
            ReviewReportModel,
            QualityScoreModel,
            RewriteTaskModel,
        ):
            ddl = str(CreateTable(model.__table__).compile(dialect=conn.dialect))
            await conn.exec_driver_sql(ddl.replace("::jsonb", ""))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        project = ProjectModel(
            slug=f"commercial-checkpoint-{uuid4().hex}",
            title="Fixture",
            genre="general-fiction",
            target_chapters=3,
            target_word_count=10000,
            metadata_json={},
            language="zh-CN",
        )
        session.add(project)
        await session.flush()
        chapter = ChapterModel(
            project_id=project.id,
            chapter_number=2,
            chapter_goal="核实名册后归还钥匙。",
            target_word_count=2600,
            metadata_json={"protected": "keep"},
        )
        session.add(chapter)
        await session.flush()
        scene = SceneCardModel(
            project_id=project.id,
            chapter_id=chapter.id,
            scene_number=1,
            scene_type="development",
            title="核实名册",
            participants=[],
            purpose={"story": "核实名册"},
            entry_state={},
            exit_state={},
            metadata_json={},
        )
        draft = ChapterDraftVersionModel(
            project_id=project.id,
            chapter_id=chapter.id,
            version_no=13,
            content_md="她核对名册后归还钥匙。\n\n" * 220,
            word_count=2200,
            is_current=True,
            promotion_state="candidate",
        )
        session.add_all([scene, draft])
        await session.commit()
        context = ChapterWriterContextPacket(
            project_id=project.id,
            project_slug=project.slug,
            chapter_id=chapter.id,
            chapter_number=2,
            query_text="review",
            chapter_goal=chapter.chapter_goal,
        )

        async def context_packet(*args, **kwargs):
            return context

        async def no_findings(*args, **kwargs):
            return [], {}

        async def no_list(*args, **kwargs):
            return []

        async def no_result(*args, **kwargs):
            return None

        async def no_duplication(*args, **kwargs):
            return 1.0, []

        async def preserve_review(_session, result, **kwargs):
            return result

        async def forbidden(*args, **kwargs):
            pytest.fail("checkpoint regression must not call a provider")

        # Keep the real entrypoint, loader, evaluator and ORM result persistence.
        # Unrelated audits are stubbed so this test isolates transaction ownership.
        monkeypatch.setattr(reviews, "build_chapter_writer_context", context_packet)
        monkeypatch.setattr(reviews, "_compute_chapter_duplication_signal", no_duplication)
        for name in (
            "_compute_chapter_antagonist_scope_signal",
            "_compute_premature_death_signal",
            "_compute_chapter_seam_signal",
            "_compute_reader_logic_signal",
            "_compute_stitched_draft_signal",
            "_compute_name_canon_signal",
        ):
            monkeypatch.setattr(reviews, name, no_findings)
        for name in (
            "_compute_chapter_methodology_reports",
            "_compute_countdown_regression_signal",
        ):
            monkeypatch.setattr(reviews, name, no_list)
        for name in (
            "compute_hook_ledger_audit_for_review",
            "compute_payoff_ledger_audit_for_review",
            "_evaluate_chapter_quality_bundle_for_review",
            "adjudicate_findings",
        ):
            monkeypatch.setattr(reviews, name, no_result)
        for name in (
            "merge_hook_ledger_audit_into_chapter_review",
            "merge_payoff_ledger_audit_into_chapter_review",
        ):
            monkeypatch.setattr(reviews, name, lambda result, *args, **kwargs: result)
        monkeypatch.setattr(
            reviews, "_teach_write_gate_blockers_the_bundle_cannot_see", preserve_review
        )
        monkeypatch.setattr(reviews, "complete_text", forbidden)
        monkeypatch.setattr(commercial, "complete_text", forbidden)
        monkeypatch.setattr(recovery, "load_commercial_retry_context", no_result)
        observed = []

        async def load_history(caller, **kwargs):
            # The real entrypoint has dirtied runtime adjudication metadata. This
            # ORM query autoflushes it, reproducing the parent's held write lock.
            assert chapter in session.dirty
            await caller.scalar(select(ChapterModel.id).where(ChapterModel.id == chapter.id))
            assert session.in_transaction()
            assert "common_sense_adjudication" in chapter.metadata_json
            observed.append("parent_audit_autoflushed")
            return {}

        monkeypatch.setattr(history, "load_commercial_review_history", load_history)
        settings = load_settings(env={})
        settings.quality.enable_llm_chapter_commentary = False
        settings.pipeline.enable_chapter_llm_commercial_judge = True
        settings.pipeline.enable_chapter_litstyle_judge = False
        settings.pipeline.enable_chapter_window_llm_judge = False
        settings.pipeline.enable_volume_llm_checkpoint_judge = False
        yield SimpleNamespace(
            session=session,
            factory=factory,
            project=project,
            chapter=chapter,
            draft=draft,
            settings=settings,
            observed=observed,
        )
        await session.rollback()
    await engine.dispose()


async def run_review(db, session=None):
    return await reviews.review_chapter_draft(
        session if session is not None else db.session,
        db.settings,
        db.project.slug,
        2,
    )


async def test_real_review_autoflushed_audit_is_committed_before_three_owned_sessions(
    review_db,
    monkeypatch,
):
    db = review_db
    body = db.draft.content_md
    incoming = {}

    async def owned_sample(slot):
        async with db.factory() as independent:
            row = await independent.scalar(
                select(ChapterModel).where(ChapterModel.id == db.chapter.id).with_for_update()
            )
            assert row.metadata_json["protected"] == "keep"
            assert "common_sense_adjudication" in row.metadata_json
            db.observed.append(f"independent_{slot}_observed_committed_audit")
            return row.metadata_json

    async def fanout(caller, settings, **kwargs):
        assert caller is db.session and not caller.in_transaction()
        assert "parent_audit_autoflushed" in db.observed
        incoming.update(kwargs)
        # SQLite ignores FOR UPDATE. A genuine independent UPDATE additionally
        # proves that the caller no longer holds SQLite's write lock.
        async with db.factory() as writer:
            await writer.execute(
                update(ChapterModel)
                .where(ChapterModel.id == db.chapter.id)
                .values(current_word_count=ChapterModel.current_word_count)
            )
            await writer.commit()
        snapshots = await asyncio.gather(*(owned_sample(slot) for slot in range(3)))
        assert snapshots[0] == snapshots[1] == snapshots[2]
        return LLMQualityJudgeResult(passed=True, overall_score=0.9)

    monkeypatch.setattr(commercial, "judge_chapter_commercial_quality_stable", fanout)
    result, report, quality, _task = await run_review(db)
    assert incoming["content_md"] == body
    assert incoming["draft_id"] == db.draft.id
    assert incoming["project_id"] == db.project.id
    assert incoming["chapter_id"] == db.chapter.id
    assert len(db.observed) == 4
    assert report.id is not None and quality.review_report_id == report.id
    # This lock-order fixture supplies no physical cohort. The action boundary
    # must retain it as unavailable, while the independent sessions above prove
    # the actual caller commit happened before fanout.
    assert result.evidence_summary["llm_commercial_judge"]["pass"] is False
    assert result.verdict == "unknown"
    assert db.draft.content_md == body


@pytest.mark.parametrize("failure", ["nested", "flush", "commit", "cancel"])
async def test_checkpoint_failure_propagates_without_dispatch_or_unknown_report(
    review_db,
    monkeypatch,
    failure,
):
    db = review_db
    fanouts = []

    async def forbidden_fanout(*args, **kwargs):
        fanouts.append(True)
        pytest.fail("checkpoint failure must stop before fanout")

    monkeypatch.setattr(commercial, "judge_chapter_commercial_quality_stable", forbidden_fanout)
    savepoint = None
    if failure == "nested":
        savepoint = await db.session.begin_nested()
        expected = RuntimeError
        match = "commercial_fanout_requires_outer_checkpoint"
    else:
        expected = asyncio.CancelledError if failure == "cancel" else ValueError
        match = "fixture checkpoint failure"

        async def fail(*args, **kwargs):
            raise expected(match)

        if failure == "flush":
            # Autoflush during history still succeeds; fail only the explicit
            # orchestrator flush immediately before dispatch.
            original_flush = db.session.flush

            async def fail_after_audit(*args, **kwargs):
                if "parent_audit_autoflushed" in db.observed:
                    await fail()
                return await original_flush(*args, **kwargs)

            monkeypatch.setattr(db.session, "flush", fail_after_audit)
        else:
            monkeypatch.setattr(db.session, "commit", fail)
    with pytest.raises(expected, match=match):
        await run_review(db)
    assert fanouts == []
    assert not any(isinstance(row, ReviewReportModel) for row in db.session.new)
    assert db.session.in_transaction()
    if savepoint is not None:
        assert savepoint.is_active
        await savepoint.rollback()


@pytest.mark.parametrize("unavailable", ["disabled", "history"])
async def test_no_fanout_adds_no_orchestrator_commit(review_db, monkeypatch, unavailable):
    db = review_db
    commits = []

    async def unexpected_commit():
        commits.append(True)
        pytest.fail("no dispatch must not introduce this checkpoint")

    async def bad_history(*args, **kwargs):
        raise ValueError("fixture adopted history missing")

    async def forbidden_fanout(*args, **kwargs):
        pytest.fail("unavailable or disabled commercial review must not dispatch")

    monkeypatch.setattr(db.session, "commit", unexpected_commit)
    monkeypatch.setattr(commercial, "judge_chapter_commercial_quality_stable", forbidden_fanout)
    if unavailable == "disabled":
        db.settings.pipeline.enable_chapter_llm_commercial_judge = False
    else:
        monkeypatch.setattr(history, "load_commercial_review_history", bad_history)
    await run_review(db)
    assert commits == []


async def test_noncommitting_fake_session_remains_compatible(review_db, monkeypatch):
    db = review_db
    calls = []

    class FakeWithoutCommit:
        def __getattr__(self, name):
            if name == "commit":
                raise AttributeError(name)
            return getattr(db.session, name)

    async def fake_fanout(*args, **kwargs):
        calls.append(deepcopy(kwargs["content_md"]))
        return LLMQualityJudgeResult(passed=True, overall_score=0.9)

    monkeypatch.setattr(commercial, "judge_chapter_commercial_quality_stable", fake_fanout)
    await run_review(db, FakeWithoutCommit())
    assert calls == [db.draft.content_md]
    assert db.session.in_transaction()
