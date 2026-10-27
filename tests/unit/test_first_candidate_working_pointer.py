"""Candidate repair uses working pointers, never adoption or a different body."""

from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import JSON, MetaData, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.schema import CreateTable

from bestseller.infra.db.models import (
    ChapterDraftVersionModel,
    ChapterModel,
    ProjectModel,
    RewriteTaskModel,
    SceneCardModel,
    StyleGuideModel,
)
from bestseller.services import drafts, feedback, llm, pipelines, reviews
from bestseller.services.rewrite_patch import text_hash
from bestseller.settings import load_settings

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.fixture
async def db(tmp_path, monkeypatch):
    path = tmp_path / "first_candidate_working_pointer_test.sqlite3"
    assert "test" in path.name
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    factory = async_sessionmaker(engine, expire_on_commit=False)

    def ddl(connection):
        for model in (
            ProjectModel,
            ChapterModel,
            SceneCardModel,
            StyleGuideModel,
            ChapterDraftVersionModel,
            RewriteTaskModel,
        ):
            table = model.__table__.to_metadata(MetaData())
            for column in table.c:
                if isinstance(column.type, JSONB):
                    column.type = JSON()
                if column.server_default and "::jsonb" in str(column.server_default.arg):
                    column.server_default = None
            connection.execute(CreateTable(table, include_foreign_key_constraints=[]))
        connection.exec_driver_sql(
            "CREATE UNIQUE INDEX first_candidate_test_current "
            "ON chapter_draft_versions(chapter_id) WHERE is_current = 1"
        )

    async with engine.begin() as connection:
        await connection.run_sync(ddl)

    async def forbidden(*_args, **_kw):
        pytest.fail("No real model or knowledge gateway may run")

    for module in (llm, drafts, reviews, feedback):
        monkeypatch.setattr(module, "complete_text", forbidden)
    async with factory() as session:
        project = ProjectModel(
            slug="working-pointer-test",
            title="海边来信",
            genre="test",
            language="zh-CN",
            target_chapters=3,
            target_word_count=7800,
            metadata_json={},
        )
        session.add(project)
        await session.flush()
        chapter = ChapterModel(
            project_id=project.id,
            chapter_number=2,
            title="回信",
            chapter_goal="交还信件",
            target_word_count=2600,
            metadata_json={},
        )
        session.add(chapter)
        await session.flush()
        scene = SceneCardModel(
            project_id=project.id,
            chapter_id=chapter.id,
            scene_number=1,
            scene_type="dialogue",
            target_word_count=2600,
            metadata_json={},
        )
        session.add(scene)
        await session.commit()
        yield session, project, chapter, scene, factory
    await engine.dispose()


async def candidate(db, *, version=1, current=False, state="candidate"):
    session, project, chapter, *_ = db
    row = ChapterDraftVersionModel(
        project_id=project.id,
        chapter_id=chapter.id,
        version_no=version,
        content_md=f"她把第{version}封信交给了守塔人。",
        word_count=18,
        is_current=current,
        promotion_state=state,
        promotion_metadata={"effect_realization_gate": {"passed": False}},
    )
    session.add(row)
    await session.flush()
    return row


async def task_for(db, row):
    return await pipelines._create_chapter_first_local_auto_repair_task(
        db[0],
        project=db[1],
        chapter=db[2],
        block_codes=("STORY_EFFECT_REALIZATION_UNREALIZED",),
        attempt_number=1,
        source_draft_version_id=row.id,
    )


async def test_saved_first_paid_candidate_can_acquire_only_a_working_pointer(db):
    session, project, chapter, _, factory = db
    row = await candidate(db)
    original = row.content_md
    task = await task_for(db, row)
    assert row.is_current and row.promotion_state == "candidate"
    assert task.metadata_json["parent_draft_id"] == str(row.id)
    assert task.metadata_json["parent_draft_hash"] == text_hash(original)
    assert task.metadata_json["working_pointer_initialized"] is True
    loaded = await reviews._load_chapter_context(session, project.slug, chapter.chapter_number)
    assert loaded[-1].id == row.id
    await reviews._validate_rewrite_task_parent(session, task, row)
    source, rejection = await feedback.validate_adopted_knowledge_source(
        session,
        project_id=project.id,
        chapter_id=chapter.id,
        source_chapter_draft_version_id=row.id,
        chapter_md=original,
        lock=False,
    )
    assert rejection == "source_not_adopted"
    assert "adoption_state" not in source
    await session.commit()
    async with factory() as reader:
        saved = await reader.get(ChapterDraftVersionModel, row.id)
        assert saved.is_current and saved.content_md == original
        assert saved.promotion_state == "candidate"
        assert saved.promotion_metadata["effect_realization_gate"] == {"passed": False}
    again = await task_for(db, row)
    assert again.metadata_json["working_pointer_initialized"] is False
    assert again.metadata_json["parent_draft_id"] == str(row.id)


@pytest.mark.parametrize("state", ["candidate", "promoted"])
async def test_incumbent_and_all_candidates_are_preserved_without_wrong_parent_repair(db, state):
    session, *_ = db
    incumbent = await candidate(db, current=True, state=state)
    challenger = await candidate(db, version=2)
    assert await task_for(db, challenger) is None
    assert incumbent.is_current and incumbent.promotion_state == state
    assert not challenger.is_current and challenger.promotion_state == "candidate"
    assert len(list(await session.scalars(select(ChapterDraftVersionModel)))) == 2
    assert not list(await session.scalars(select(RewriteTaskModel)))


@pytest.mark.parametrize(
    "invalid", ["older", "rejected", "foreign_project", "foreign_chapter", "empty"]
)
async def test_first_pointer_recovery_requires_exact_latest_owned_candidate(db, invalid):
    row = await candidate(db)
    if invalid == "older":
        await candidate(db, version=2)
    elif invalid == "rejected":
        row.promotion_state = "rejected"
    elif invalid == "foreign_project":
        row.project_id = uuid4()
    elif invalid == "foreign_chapter":
        row.chapter_id = uuid4()
    else:
        row.content_md = ""
    await db[0].flush()
    with pytest.raises(ValueError, match="exact latest repairable candidate"):
        await task_for(db, row)
    assert not row.is_current
    assert not list(await db[0].scalars(select(RewriteTaskModel)))


async def generate_offline(db, monkeypatch, tmp_path):
    from bestseller.services import deterministic_post_write_audit

    async def nothing(*_args, **_kw):
        return None

    async def no_duplicates(*_args, **_kw):
        return []

    async def gate(*_args, **_kw):
        return "ok"

    async def writer(*_args, **_kw):
        # A deterministic transport fixture; no gateway or provider is called.
        return SimpleNamespace(
            content="她走到灯塔下。守塔人收起信封。",
            llm_run_id=None,
            model_name="offline-fixture",
            provider="fixture",
        )

    for name in (
        "_enrich_chapter_first_context",
        "_render_chapter_first_character_safety_block",
        "_prepare_quality_uplift_prompt_blocks",
        "_materialize_story_effect_evidence",
    ):
        monkeypatch.setattr(drafts, name, nothing)
    monkeypatch.setattr(
        drafts, "build_chapter_first_draft_prompts", lambda *_a, **_kw: ("fixture", "fixture")
    )
    monkeypatch.setattr(drafts, "_maybe_write_chapter_prompt_trace", lambda *_a, **_kw: None)
    monkeypatch.setattr(drafts, "_collect_post_assembly_duplicate_findings", no_duplicates)
    monkeypatch.setattr(drafts, "_evaluate_chapter_quality_gate", gate)
    monkeypatch.setattr(drafts, "complete_text", writer)
    monkeypatch.setattr(
        deterministic_post_write_audit,
        "audit_chapter_prose",
        lambda **_kw: SimpleNamespace(passed=True, to_dict=lambda: {"passed": True}),
    )
    settings = load_settings()
    settings.pipeline.chapter_continuity_critic_enabled = False
    settings.pipeline.commercial_strict_quality_mode = False
    settings.output.base_dir = str(tmp_path / "output")
    db[1].metadata_json = {"creation_intent_contract": {"field_sources": {"hook_spec": "explicit", "story_enhancers": "explicit"}, "story_enhancers": {"effect_skills": ["comedy_engine"]}, "hook_spec": {"effect_realization_contracts": {"comedy_engine": {"required_stages": ["setup", "reversal_or_misalignment", "character_reaction", "situation_change"]}}}}}
    db[2].metadata_json = {"selected_effect_skills": {"primary": "comedy_engine"}}
    await db[0].flush()
    return await drafts.generate_chapter_draft_once(
        db[0],
        db[1].slug,
        db[2].chapter_number,
        settings=settings,
        context_packet=SimpleNamespace(query_text="", assigned_hype_type=None),
    )


async def test_actual_generation_persists_first_effect_blocked_working_candidate(
    db, monkeypatch, tmp_path
):
    row = await generate_offline(db, monkeypatch, tmp_path)
    assert row.is_current and row.promotion_state == "candidate"
    assert db[2].production_state == "blocked"
    assert db[2].current_word_count == row.word_count > 0
    assert row.promotion_metadata["effect_realization_gate"]["passed"] is False
    task = await task_for(db, row)
    assert task.metadata_json["parent_draft_hash"] == text_hash(row.content_md)
    await reviews._validate_rewrite_task_parent(db[0], task, row)


async def test_actual_generation_never_evicts_a_promoted_incumbent_on_effect_failure(
    db, monkeypatch, tmp_path
):
    prior = await candidate(db, current=True, state="promoted")
    original = prior.content_md
    row = await generate_offline(db, monkeypatch, tmp_path)
    assert row.id != prior.id and not row.is_current
    assert prior.is_current and prior.promotion_state == "promoted"
    assert prior.content_md == original
    assert db[2].current_word_count == prior.word_count
    assert await task_for(db, row) is None


async def test_invalid_effect_obligation_reconciliation_persists_without_adoption(db):
    from copy import deepcopy
    from hashlib import sha256

    session, project, chapter, _scene, factory = db
    row = await candidate(db, current=True)
    original_body = row.content_md
    original_promotion = deepcopy(row.promotion_metadata)
    project.metadata_json = {"genre_intent_contract": {"explicit_enhancers": {"effect_skills": []}}}
    chapter.production_state = "blocked"
    chapter.metadata_json = {
        "selected_effect_skills": {"primary": "comedy_engine"},
        "effect_realization_gate": {"passed": False},
        "effect_realization_blocked": True,
        "story_effect_realization_block_codes": ["STORY_EFFECT_REALIZATION_UNREALIZED"],
        "quality_gate_block_codes": [
            "UNEXPLAINED_BODY_STATE",
            "STORY_EFFECT_REALIZATION_UNREALIZED",
        ],
    }
    result = drafts.reconcile_persisted_effect_requirement(project, chapter, row)
    assert result["changed"]
    await session.commit()
    async with factory() as reader:
        saved_chapter = await reader.get(ChapterModel, chapter.id)
        saved = await reader.get(ChapterDraftVersionModel, row.id)
        assert saved.is_current and saved.promotion_state == "candidate"
        assert saved.content_md == original_body and saved.promotion_metadata == original_promotion
        assert saved_chapter.production_state == "blocked"
        assert saved_chapter.metadata_json["quality_gate_block_codes"] == ["UNEXPLAINED_BODY_STATE"]
        receipt = saved_chapter.metadata_json["effect_requirement_supersessions"][0]
        assert receipt["source_draft_id"] == str(saved.id)
        assert receipt["source_sha256"] == sha256(original_body.encode()).hexdigest()
        saved_project = await reader.get(ProjectModel, project.id)
        assert not drafts.reconcile_persisted_effect_requirement(
            saved_project, saved_chapter, saved
        )["changed"]
        await reader.commit()
