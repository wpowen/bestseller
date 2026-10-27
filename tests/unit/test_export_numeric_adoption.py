"""Normal exports must select durable adoption evidence, never a working flag.

These tests use isolated SQL rows and real Markdown writes; prose gates are
outside this source-selection contract. No model calls are permitted.
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.schema import CreateTable

from bestseller.infra.db.models import (
    ChapterDraftVersionModel,
    ChapterModel,
    ExportArtifactModel,
    ProjectModel,
    QualityScoreModel,
    ReviewReportModel,
)
from bestseller.services import exports, llm
from bestseller.services.draft_promotion import numeric_promotion_debt_receipt
from bestseller.services.reviews import chapter_review_body_fingerprint
from bestseller.settings import load_settings

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@compiles(JSONB, "sqlite")
def _jsonb(_type, _compiler, **_kw):
    return "JSON"


@pytest.fixture
async def db(tmp_path, monkeypatch):
    async def forbidden(*_args, **_kwargs):
        pytest.fail("Model calls are forbidden in export source-selection tests")

    monkeypatch.setattr(llm, "complete_text", forbidden)
    path = tmp_path / "export_numeric_adoption_test.sqlite3"
    assert "test" in path.name
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    async with engine.begin() as conn:
        for model in (
            ProjectModel,
            ChapterModel,
            ChapterDraftVersionModel,
            QualityScoreModel,
            ReviewReportModel,
            ExportArtifactModel,
        ):
            ddl = str(CreateTable(model.__table__).compile(dialect=engine.dialect))
            await conn.exec_driver_sql(ddl.replace("::jsonb", ""))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        project = ProjectModel(
            slug=f"export-adoption-test-{uuid4().hex[:12]}",
            title="陌生港口",
            genre="test",
            language="zh-CN",
            target_word_count=10000,
            target_chapters=1,
        )
        session.add(project)
        await session.flush()
        chapter = ChapterModel(
            project_id=project.id,
            chapter_number=1,
            chapter_goal="交还船票",
            title="晚潮",
            status="revision",
            production_state="quality_debt",
        )
        session.add(chapter)
        await session.flush()
        old = ChapterDraftVersionModel(
            project_id=project.id,
            chapter_id=chapter.id,
            version_no=1,
            content_md="旧稿:他错过了渡轮。",
            promotion_state="promoted",
            is_current=False,
        )
        draft = ChapterDraftVersionModel(
            project_id=project.id,
            chapter_id=chapter.id,
            version_no=2,
            content_md="她把船票压在窗沿。等潮水退去。",
            promotion_state="under_review",
            is_current=True,
            assembled_from_scene_draft_ids=["chapter_first_scene:1"],
        )
        session.add_all([old, draft])
        await session.flush()
        report = ReviewReportModel(
            project_id=project.id,
            target_type="chapter",
            target_id=chapter.id,
            reviewer_type="chapter",
            verdict="pass",
            structured_output={
                "draft_id": str(draft.id),
                "findings": [],
                "reviewed_body_fingerprint": chapter_review_body_fingerprint(draft.content_md),
            },
        )
        session.add(report)
        await session.flush()
        score = QualityScoreModel(
            project_id=project.id,
            target_type="chapter",
            target_id=chapter.id,
            chapter_draft_version_id=draft.id,
            review_report_id=report.id,
            judge_key="test",
            score_overall=0.8,
            score_goal=0.8,
            score_conflict=0.8,
            score_emotion=0.8,
            score_style=0.8,
            is_current=True,
            evidence_summary={"hard_gates_passed": True},
        )
        session.add(score)
        await session.flush()
        receipt = numeric_promotion_debt_receipt(draft, score, report)
        assert receipt
        chapter.metadata_json = {
            "chapter_quality_debt": True,
            "numeric_promotion_debt": receipt,
            "chapter_quality_debt_reason": "chapter_promotion_ineligible",
        }
        await session.commit()
        yield session, project, chapter, draft, old, score, report, factory
    await engine.dispose()


async def selected(db, scope):
    session, project, *_ = db
    if scope == "chapter":
        return (await exports._load_chapter_export_payload(session, project.slug, 1))[2]
    _, payloads, skipped = await exports._load_project_export_payload(session, project.slug)
    assert skipped == []
    return payloads[0][1]


@pytest.mark.parametrize("scope", ["chapter", "book"])
@pytest.mark.parametrize("state", ["under_review", "candidate"])
async def test_exact_numeric_adoption_supersedes_old_promoted_source(db, scope, state):
    draft = db[3]
    draft.promotion_state = state
    await db[0].commit()
    before = (draft.content_md, draft.promotion_state, deepcopy(db[2].metadata_json))
    assert (await selected(db, scope)).id == draft.id
    assert (draft.content_md, draft.promotion_state, db[2].metadata_json) == before


@pytest.mark.parametrize("scope", ["chapter", "book"])
@pytest.mark.parametrize(
    "corruption",
    [
        "body",
        "score",
        "review",
        "hard_gate",
        "receipt_hash",
        "not_current",
        "missing_source",
        "foreign_owner",
        "malformed_receipt",
        "structural_block",
    ],
)
async def test_invalid_debt_source_never_falls_back_to_old_promoted(db, scope, corruption):
    session, _, chapter, draft, _, score, report, _ = db
    metadata = deepcopy(chapter.metadata_json)
    if corruption == "body":
        draft.content_md += "正文被改写。"
    elif corruption == "score":
        score.chapter_draft_version_id = None
    elif corruption == "review":
        report.structured_output = {**report.structured_output, "draft_id": str(uuid4())}
    elif corruption == "hard_gate":
        score.evidence_summary = {"hard_gates_passed": False}
    elif corruption == "receipt_hash":
        metadata["numeric_promotion_debt"]["reviewed_body_fingerprint"]["digest"] = "0" * 64
    elif corruption == "not_current":
        draft.is_current = False
    elif corruption == "missing_source":
        metadata["numeric_promotion_debt"]["draft_id"] = str(uuid4())
    elif corruption == "foreign_owner":
        draft.project_id = uuid4()
    elif corruption == "malformed_receipt":
        metadata["numeric_promotion_debt"] = {"draft_id": "invalid"}
    else:
        metadata["production_block_code"] = "canon_conflict"
    chapter.metadata_json = metadata
    await session.commit()
    with pytest.raises(ValueError):
        await selected(db, scope)
    assert not list(await session.scalars(select(ExportArtifactModel)))


@pytest.mark.parametrize("scope", ["chapter", "book"])
@pytest.mark.parametrize("state", ["candidate", "under_review", "rejected", "superseded"])
async def test_working_state_and_generic_debt_flag_are_not_adoption(db, scope, state):
    session, _, chapter, draft, old, *_ = db
    old.promotion_state = "superseded"
    draft.promotion_state = state
    chapter.metadata_json = {"chapter_quality_debt": True}
    await session.commit()
    with pytest.raises(ValueError):
        await selected(db, scope)


@pytest.mark.parametrize("scope", ["chapter", "book"])
@pytest.mark.parametrize("metadata", [{}, {"numeric_promotion_debt": None}])
async def test_promoted_source_remains_valid_with_a_separate_working_candidate(db, scope, metadata):
    session, _, chapter, draft, old, *_ = db
    chapter.metadata_json = metadata
    draft.promotion_state = "candidate"
    await session.commit()
    assert (await selected(db, scope)).id == old.id


async def test_mixed_book_and_each_chapter_use_the_same_adopted_sources(db):
    session, project, _, adopted, *_ = db
    second = ChapterModel(
        project_id=project.id,
        chapter_number=2,
        chapter_goal="归还钥匙",
        metadata_json={},
    )
    session.add(second)
    await session.flush()
    promoted = ChapterDraftVersionModel(
        project_id=project.id,
        chapter_id=second.id,
        version_no=1,
        content_md="锁开了。她把钥匙搁在桌上。",
        promotion_state="promoted",
        is_current=True,
    )
    session.add(promoted)
    await session.commit()
    _, payloads, skipped = await exports._load_project_export_payload(session, project.slug)
    assert skipped == []
    assert [draft.id for _, draft in payloads] == [adopted.id, promoted.id]
    for chapter, draft in payloads:
        result = await exports._load_chapter_export_payload(
            session, project.slug, chapter.chapter_number
        )
        assert result[2].id == draft.id
    comparisons = await exports.load_publication_comparison_payloads(session, project.id)
    assert [draft.id for _, draft in comparisons] == [adopted.id, promoted.id]
    prefix = await exports.load_publication_comparison_payloads(
        session,
        project.id,
        through_chapter_number=1,
    )
    assert [draft.id for _, draft in prefix] == [adopted.id]


@pytest.mark.parametrize("scope", ["chapter", "book"])
async def test_selection_refreshes_stale_identity_map_before_choosing_old_promoted(db, scope):
    session, _, chapter, adopted, _, _, _, factory = db
    valid = deepcopy(chapter.metadata_json)
    chapter.metadata_json = {}
    await session.commit()
    async with factory() as writer:
        fresh = await writer.get(ChapterModel, chapter.id)
        fresh.metadata_json = valid
        await writer.commit()
    assert chapter.metadata_json == {}  # Reader is holding stale cached metadata.
    assert (await selected(db, scope)).id == adopted.id


async def test_registration_revalidation_rejects_a_now_replaced_old_promoted_source(db):
    session, _, chapter, _, old, _, _, factory = db
    valid = deepcopy(chapter.metadata_json)
    chapter.metadata_json = {}
    await session.commit()
    assert (await selected(db, "chapter")).id == old.id
    async with factory() as writer:
        fresh = await writer.get(ChapterModel, chapter.id)
        fresh.metadata_json = valid
        await writer.commit()
    with pytest.raises(ValueError, match="source_replaced_by_adopted_debt"):
        await exports._validated_export_adoptions(session, [(chapter, old)])


async def test_promoted_manifest_revalidation_is_bound_to_the_original_body(db):
    session, _, chapter, _, old, _, _, factory = db
    chapter.metadata_json = {}
    await session.commit()
    assert (await selected(db, "chapter")).id == old.id
    async with factory() as writer:
        fresh = await writer.get(ChapterDraftVersionModel, old.id)
        fresh.content_md += "未经本次导出选择的变更。"
        await writer.commit()
    with pytest.raises(ValueError, match="source_body_mismatch"):
        await exports._validated_export_adoptions(session, [(chapter, old)])


@pytest.mark.parametrize("scope", ["chapter", "book"])
async def test_markdown_manifest_retains_exact_debt_and_frozen_source(
    db, monkeypatch, tmp_path, scope
):
    session, project, chapter, draft, old, _, _, factory = db
    before = (draft.content_md, draft.promotion_state, deepcopy(chapter.metadata_json))
    # Selection, source validation, artifact registration and file writes remain real.
    monkeypatch.setattr(exports, "_raise_if_export_blocked", lambda *_a, **_kw: None)
    monkeypatch.setattr(exports, "_run_terminal_export_gate", lambda *_a, **_kw: None)

    async def preflight(*_a, **_kw):
        return []

    monkeypatch.setattr(exports, "preflight_export_check", preflight)
    monkeypatch.setattr(exports, "_write_commercial_package_sidecars", lambda *_a, **_kw: None)

    settings = load_settings()
    settings.output.base_dir = str(tmp_path / "output")
    if scope == "chapter":
        artifact, mirror = await exports.export_chapter_markdown(session, settings, project.slug, 1)
    else:
        artifact, mirror = await exports.export_project_markdown(session, settings, project.slug)
    await session.commit()
    frozen_bytes = Path(artifact.storage_uri).read_bytes()
    assert draft.content_md.encode() in frozen_bytes
    assert old.content_md.encode() not in frozen_bytes
    assert hashlib.sha256(frozen_bytes).hexdigest() == artifact.checksum
    source = artifact.metadata_json["source_drafts"][0]
    assert source["draft_id"] == str(draft.id)
    assert source["adoption_state"] == "accepted_with_debt"
    assert source["promotion_state"] == "under_review"
    assert source["body_sha256"] == hashlib.sha256(draft.content_md.encode()).hexdigest()
    assert source["adoption_receipt"] == chapter.metadata_json["numeric_promotion_debt"]
    mirror.write_text("后续镜像内容", encoding="utf-8")
    async with factory() as reader:
        registered = await reader.get(ExportArtifactModel, artifact.id)
        persisted = await reader.get(ChapterDraftVersionModel, draft.id)
        persisted_chapter = await reader.get(ChapterModel, chapter.id)
        assert Path(registered.storage_uri).read_bytes() == frozen_bytes
        assert registered.metadata_json["source_drafts"] == [source]
        assert (
            persisted.content_md,
            persisted.promotion_state,
            persisted_chapter.metadata_json,
        ) == before


async def test_unvalidated_manifest_does_not_infer_adoption_from_debt_flags(db, tmp_path):
    _, project, chapter, draft, *_ = db
    path = tmp_path / "unvalidated.md"
    path.write_text(draft.content_md, encoding="utf-8")
    artifact = exports.create_export_artifact(
        project_id=project.id,
        export_type="markdown_draft",
        source_scope="chapter",
        source_id=chapter.id,
        storage_uri=str(path),
        checksum=hashlib.sha256(path.read_bytes()).hexdigest(),
        version_label="diagnostic",
        created_by_run_id=None,
        source_payloads=[(chapter, draft)],
    )
    source = artifact.metadata_json["source_drafts"][0]
    assert source["adoption_state"] == "not_adopted"
    assert "adoption_receipt" not in source


@pytest.mark.parametrize("scope", ["chapter", "book"])
async def test_export_revalidates_debt_before_writing_any_markdown(
    db, monkeypatch, tmp_path, scope
):
    session, project, chapter, *_ = db
    monkeypatch.setattr(exports, "_raise_if_export_blocked", lambda *_a, **_kw: None)

    def invalidate(*_a, **_kw):
        metadata = deepcopy(chapter.metadata_json)
        metadata["numeric_promotion_debt"]["reviewed_body_fingerprint"]["digest"] = "0" * 64
        chapter.metadata_json = metadata

    monkeypatch.setattr(exports, "_run_terminal_export_gate", invalidate)

    async def preflight(*_a, **_kw):
        return []

    monkeypatch.setattr(exports, "preflight_export_check", preflight)
    settings = load_settings()
    output = tmp_path / "output"
    settings.output.base_dir = str(output)
    with pytest.raises(ValueError, match="source_not_adopted"):
        if scope == "chapter":
            await exports.export_chapter_markdown(session, settings, project.slug, 1)
        else:
            await exports.export_project_markdown(session, settings, project.slug)
    assert not list(output.rglob("*.md"))
    assert not list(await session.scalars(select(ExportArtifactModel)))


@pytest.mark.parametrize(
    "field,value",
    [
        ("project_id", str(uuid4())),
        ("chapter_id", str(uuid4())),
        ("source_chapter_draft_version_id", str(uuid4())),
        ("source_version_no", 999),
        ("source_promotion_state", "promoted"),
        ("source_body_sha256", "0" * 64),
    ],
)
async def test_manifest_refuses_lineage_for_a_different_source(db, tmp_path, field, value):
    session, project, chapter, draft, *_ = db
    adoptions = await exports._validated_export_adoptions(session, [(chapter, draft)])
    adoptions[str(draft.id)][field] = value
    path = tmp_path / "bound.md"
    path.write_text(draft.content_md, encoding="utf-8")
    with pytest.raises(ValueError, match="does not match its exact source"):
        exports.create_export_artifact(
            project_id=project.id,
            export_type="markdown",
            source_scope="chapter",
            source_id=chapter.id,
            storage_uri=str(path),
            checksum=hashlib.sha256(path.read_bytes()).hexdigest(),
            version_label="test",
            created_by_run_id=None,
            source_payloads=[(chapter, draft)],
            source_adoptions=adoptions,
        )
    assert not (tmp_path / ".export-artifacts").exists()
