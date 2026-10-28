"""Native length receipts; positives have no adopted/grade/promoted fixtures.

Only main runs these tests. SQLite is isolated under pytest's tmp_path. The
negative topology fixtures deliberately cannot produce an accepted receipt.
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.schema import CreateTable

from bestseller.infra.db.models import ChapterDraftVersionModel, ChapterModel, ProjectModel
from bestseller.services import full_flow_length as length
from bestseller.services import llm
from bestseller.services.drafts import count_words
from bestseller.services.fanqie_short_pipeline import assemble_fanqie_whole_story
from bestseller.settings import load_settings

pytestmark = pytest.mark.unit
PROTOCOL = "book-length-control-v1"
COUNTING = "export-body-words-v1"
FLAG = {"protocol": PROTOCOL, "counting": COUNTING}


@compiles(JSONB, "sqlite")
def _jsonb(_type, _compiler, **_kwargs):
    return "JSON"


@pytest.fixture
async def case(tmp_path, monkeypatch):
    def denied(*_args, **_kwargs):
        pytest.fail("Length contracts must never call a model")

    monkeypatch.setattr(llm, "complete_text", denied)
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'length_contract_test.sqlite3'}")
    try:
        async with engine.begin() as connection:
            for model in (ProjectModel, ChapterModel, ChapterDraftVersionModel):
                ddl = str(CreateTable(model.__table__).compile(dialect=engine.dialect))
                await connection.exec_driver_sql(ddl.replace("::jsonb", ""))
        factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
        async with factory() as session:
            project = ProjectModel(
                slug=f"length-test-{uuid4().hex}",
                title="Fixture",
                genre="test",
                language="zh-CN",
                target_word_count=8000,
                target_chapters=4,
                metadata_json={"book_length_control": deepcopy(FLAG)},
            )
            session.add(project)
            await session.flush()
            chapters = [
                ChapterModel(
                    project_id=project.id,
                    chapter_number=number,
                    chapter_goal="Close the door",
                    target_word_count=2000,
                    title=f"Chapter {number}",
                    metadata_json={},
                )
                for number in range(1, 5)
            ]
            session.add_all(chapters)
            await session.flush()
            settings = load_settings(
                env={
                    "BESTSELLER__GENERATION__WORDS_PER_CHAPTER__MIN": "1800",
                    "BESTSELLER__GENERATION__WORDS_PER_CHAPTER__TARGET": "2000",
                    "BESTSELLER__GENERATION__WORDS_PER_CHAPTER__MAX": "3500",
                }
            )
            yield SimpleNamespace(
                session=session,
                project=project,
                chapters=chapters,
                chapter=chapters[0],
                settings=settings,
            )
            await session.rollback()
    finally:
        await engine.dispose()


async def freeze(case, **kwargs):
    return await length.freeze_request(
        case.session, case.settings, project=case.project, chapter=case.chapter, **kwargs
    )


async def revalidate(case, receipt, **kwargs):
    return await length.revalidate_request(
        case.session,
        case.settings,
        project=case.project,
        chapter=case.chapter,
        receipt=receipt,
        **kwargs,
    )


def assert_no_authority(result):
    for key in ("grade", "adoption", "credit", "dispatch"):
        assert result[key] is False


async def test_freeze_uses_actual_plan_with_no_adoption_and_does_not_mutate(case):
    receipt = await freeze(case)
    assert receipt["protocol"] == PROTOCOL and receipt["counting"] == COUNTING
    assert receipt["schema"] == "book_length_request_v1"
    assert receipt["source"]["project"]["target_word_count"] == 8000
    assert [row["target_word_count"] for row in receipt["source"]["plan"]] == [2000] * 4
    assert receipt["source"]["adopted_prefix"] == []
    assert receipt["source"]["incumbent"] is None
    assert receipt["window"] == {"min_words": 1800, "target_words": 2000, "max_words": 2300}
    assert len(receipt["digest"]) == 64
    assert_no_authority(receipt)
    assert [chapter.target_word_count for chapter in case.chapters] == [2000] * 4
    assert not case.session.dirty and not case.session.new
    assert (await revalidate(case, receipt))["valid"] is True


@pytest.mark.parametrize(
    "language,metadata", [("zh-CN", {}), ("en-US", {"book_length_control": FLAG})]
)
async def test_legacy_and_english_return_none(case, language, metadata):
    await case.session.execute(
        update(ProjectModel)
        .where(ProjectModel.id == case.project.id)
        .values(language=language, metadata_json=metadata)
    )
    await case.session.flush()
    assert await freeze(case) is None


@pytest.mark.parametrize(
    "flag",
    [
        None,
        {},
        {"protocol": "v2", "counting": COUNTING},
        {"protocol": PROTOCOL, "counting": "cached"},
    ],
)
async def test_malformed_or_unknown_flag_cannot_downgrade(case, flag):
    await case.session.execute(
        update(ProjectModel)
        .where(ProjectModel.id == case.project.id)
        .values(metadata_json={"book_length_control": flag})
    )
    with pytest.raises(length.LengthControlError, match="PROTOCOL"):
        await freeze(case)


async def test_flag_removal_keeps_frozen_v1(case):
    receipt = await freeze(case)
    await case.session.execute(
        update(ProjectModel).where(ProjectModel.id == case.project.id).values(metadata_json={})
    )
    assert (await revalidate(case, receipt))["valid"] is True
    rebound = await freeze(case, receipt=receipt)
    assert rebound["digest"] == receipt["digest"]
    assert length.evaluate_candidate(receipt, "字" * 2301)["length_passed"] is False


@pytest.mark.parametrize(
    "field,value",
    [
        ("target_word_count", 9000),
        ("target_chapters", 5),
        ("language", "en-US"),
        ("audience", "起点"),
    ],
)
async def test_revalidate_reloads_project_old_fields(case, field, value):
    receipt = await freeze(case)
    # Core update bypasses ORM identity synchronization: service must re-read.
    await case.session.execute(
        update(ProjectModel)
        .where(ProjectModel.id == case.project.id)
        .values(**{field: value})
        .execution_options(synchronize_session=False)
    )
    result = await revalidate(case, receipt)
    assert result["valid"] is False
    assert result["issue_code"]
    assert_no_authority(result)


@pytest.mark.parametrize(
    "field,value", [("target_word_count", 2200), ("chapter_goal", "Changed"), ("title", "Changed")]
)
async def test_revalidate_reloads_original_chapter_plan(case, field, value):
    receipt = await freeze(case)
    await case.session.execute(
        update(ChapterModel)
        .where(ChapterModel.id == case.chapters[2].id)
        .values(**{field: value})
        .execution_options(synchronize_session=False)
    )
    assert (await revalidate(case, receipt))["valid"] is False


async def test_plan_sum_mismatch_is_not_silently_reallocated(case):
    await case.session.execute(
        update(ChapterModel)
        .where(ChapterModel.id == case.chapter.id)
        .values(target_word_count=2100)
    )
    with pytest.raises(length.LengthControlError, match="PLAN"):
        await freeze(case)


async def test_request_cannot_skip_an_unadopted_chapter(case):
    with pytest.raises(length.LengthControlError, match="NOT_NEXT"):
        await length.freeze_request(
            case.session, case.settings, project=case.project, chapter=case.chapters[1]
        )


async def test_negative_adopted_suffix_is_explicitly_rejected(case):
    # Negative fixture only: never used to obtain a length acceptance.
    suffix = ChapterDraftVersionModel(
        project_id=case.project.id,
        chapter_id=case.chapters[1].id,
        version_no=1,
        content_md="字" * 2000,
        promotion_state="promoted",
        is_current=False,
    )
    case.session.add(suffix)
    await case.session.flush()
    with pytest.raises(length.LengthControlError, match=r"PREFIX_GAP|ADOPTED_SUFFIX"):
        await freeze(case)


async def test_ambiguous_native_source_is_unknown_not_empty_prefix(case):
    # Undated non-current promotions cannot establish native adoption identity.
    for version in (1, 2):
        case.session.add(
            ChapterDraftVersionModel(
                project_id=case.project.id,
                chapter_id=case.chapter.id,
                version_no=version,
                content_md="字" * 2000,
                promotion_state="promoted",
                is_current=False,
            )
        )
    await case.session.flush()
    with pytest.raises(length.LengthControlError, match="SOURCE_UNKNOWN"):
        await freeze(case)


async def test_working_candidate_and_cached_count_do_not_become_budget_fact(case):
    candidate = ChapterDraftVersionModel(
        project_id=case.project.id,
        chapter_id=case.chapter.id,
        version_no=1,
        content_md="字" * 3499,
        word_count=8000,
        promotion_state="candidate",
        is_current=False,
    )
    case.session.add(candidate)
    await case.session.flush()
    receipt = await freeze(case)
    assert receipt["source"]["adopted_prefix"] == []
    assert receipt["budget"]["adopted_words"] == 0


@pytest.mark.parametrize(
    "count,passed", [(1799, False), (1800, True), (2000, True), (2300, True), (2301, False)]
)
async def test_candidate_exact_bounds_and_preview_do_not_authorize(case, count, passed):
    receipt = await freeze(case)
    before = deepcopy(receipt)
    result = length.evaluate_candidate(receipt, "字" * count)
    assert result["length_passed"] is passed
    assert result["candidate"]["word_count"] == count
    assert_no_authority(result)
    if passed:
        assert result["preview_budget"]["adopted_words"] == count
        assert result["preview_budget"]["remaining_chapters"] == 3
    else:
        assert result["preview_budget"] is None
    assert receipt == before


async def test_body_count_is_native_export_cleaning_not_cache_or_header(case):
    receipt = await freeze(case)
    text = "# 第一章 雨夜\n\n<!-- editorial 999 -->\n" + "字" * 1997 + " one 23 two"
    expected = assemble_fanqie_whole_story(
        case.project,
        [(case.chapter, ChapterDraftVersionModel(content_md=text))],
    )
    result = length.evaluate_candidate(receipt, text)
    assert result["candidate"]["word_count"] == count_words(expected) == 2000
    assert (
        result["candidate"]["export_body_sha256"] == hashlib.sha256(expected.encode()).hexdigest()
    )


@pytest.mark.parametrize("stage", ["rewrite", "deslop"])
async def test_revision_requires_actual_owned_parent(case, stage):
    with pytest.raises(length.LengthControlError, match="PARENT"):
        await freeze(case, stage=stage)


async def parent(case, content="字" * 1900, **kwargs):
    fields = {
        "project_id": case.project.id,
        "chapter_id": case.chapter.id,
        "version_no": 1,
        "content_md": content,
        "word_count": 9999,
        "promotion_state": "candidate",
        "is_current": False,
    }
    fields.update(kwargs)
    draft = ChapterDraftVersionModel(**fields)
    case.session.add(draft)
    await case.session.flush()
    return draft


async def test_deslop_uses_actual_parent_and_five_percent_without_grade(case):
    draft = await parent(case)
    receipt = await freeze(case, stage="deslop", parent_draft=draft)
    assert receipt["parent"]["word_count"] == 1900
    assert receipt["window"] == {
        "min_words": 1800,
        "target_words": 1995,
        "max_words": 1995,
    }
    assert length.evaluate_candidate(receipt, "字" * 1995)["length_passed"] is True
    result = length.evaluate_candidate(receipt, "字" * 1996)
    assert result["length_passed"] is False
    assert result["issue_code"] == "LENGTH_CONTROL_DESLOP_GROWTH"
    assert_no_authority(result)


async def test_deslop_parent_cap_with_no_legal_window_blocks_before_dispatch(case):
    draft = await parent(case, content="字" * 1700)
    receipt = await freeze(case, stage="deslop", parent_draft=draft)
    assert receipt["feasible"] is False and receipt["window"] is None
    assert receipt["issue_code"] == "LENGTH_CONTROL_DESLOP_BAND_INFEASIBLE"
    assert length.evaluate_candidate(receipt, "字" * 1800)["length_passed"] is False
    assert_no_authority(receipt)


async def test_parent_from_other_chapter_is_not_an_actual_parent(case):
    draft = await parent(case, chapter_id=case.chapters[1].id)
    with pytest.raises(length.LengthControlError, match="PARENT_OWNERSHIP"):
        await freeze(case, stage="deslop", parent_draft=draft)


async def test_unflushed_caller_edits_are_preserved_without_implicit_writes(case):
    case.project.target_word_count = 9000
    case.chapter.target_word_count = 2200
    receipt = await freeze(case)
    assert receipt["source"]["project"]["target_word_count"] == 8000
    assert receipt["source"]["plan"][0]["target_word_count"] == 2000
    assert case.project.target_word_count == 9000
    assert case.chapter.target_word_count == 2200
    assert case.project in case.session.dirty
    assert case.chapter in case.session.dirty


async def test_settings_change_invalidates_frozen_legacy_intersection(case):
    receipt = await freeze(case)
    case.settings.generation.words_per_chapter.max = 2200
    assert (await revalidate(case, receipt))["valid"] is False


async def test_v1_cannot_rebaseline_original_targets_even_if_sum_is_preserved(case):
    request = await freeze(case)
    await parent(case, promotion_metadata={"book_length_control": request})
    for chapter, target in zip(case.chapters[:2], (2100, 1900), strict=True):
        await case.session.execute(
            update(ChapterModel)
            .where(ChapterModel.id == chapter.id)
            .values(target_word_count=target)
        )
    with pytest.raises(length.LengthControlError, match="ORIGINAL_PLAN_CHANGED"):
        await freeze(case)


async def test_parent_source_is_reread_instead_of_trusting_caller_text(case):
    draft = await parent(case)
    draft.content_md = "字" * 2200  # unflushed caller mutation must not be used as actual parent
    receipt = await freeze(case, stage="deslop", parent_draft=draft)
    assert receipt["parent"]["word_count"] == 1900


async def test_parent_same_length_body_change_invalidates_frozen_request(case):
    draft = await parent(case)
    receipt = await freeze(case, stage="deslop", parent_draft=draft)
    await case.session.execute(
        update(ChapterDraftVersionModel)
        .where(
            ChapterDraftVersionModel.id == draft.id,
        )
        .values(content_md="文" * 1900)
        .execution_options(synchronize_session=False)
    )
    assert (await revalidate(case, receipt))["valid"] is False


async def test_v1_parent_cannot_downgrade_after_flag_removed(case):
    request = await freeze(case)
    draft = await parent(case, promotion_metadata={"book_length_control": request})
    await case.session.execute(
        update(ProjectModel).where(ProjectModel.id == case.project.id).values(metadata_json={})
    )
    receipt = await freeze(case, stage="rewrite", parent_draft=draft)
    assert receipt["protocol"] == PROTOCOL


async def test_v1_working_candidate_blocks_implicit_legacy_request(case):
    request = await freeze(case)
    await parent(case, promotion_metadata={"book_length_control": request})
    await case.session.execute(
        update(ProjectModel).where(ProjectModel.id == case.project.id).values(metadata_json={})
    )
    assert (await freeze(case))["protocol"] == PROTOCOL


async def test_digest_and_authority_tampering_are_rejected(case):
    receipt = await freeze(case)
    for mutate in (
        lambda r: r["window"].update(max_words=3500),
        lambda r: r.update(grade=True),
        lambda r: r.update(protocol="v2"),
    ):
        bad = deepcopy(receipt)
        mutate(bad)
        with pytest.raises(length.LengthControlError):
            length.evaluate_candidate(bad, "字" * 2000)
        assert (await revalidate(case, bad))["valid"] is False


async def test_resealed_inconsistent_window_is_still_rejected(case):
    receipt = await freeze(case)
    for key in ("window", "parent", "budget"):
        bad = deepcopy(receipt)
        bad.pop("digest")
        if key == "window":
            bad[key]["max_words"] = 3500
        else:
            bad.pop(key)
        serialized = json.dumps(
            bad, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        )
        bad["digest"] = hashlib.sha256(serialized.encode()).hexdigest()
        with pytest.raises(length.LengthControlError, match="RECEIPT_INVALID"):
            length.evaluate_candidate(bad, "字" * 2000)
        assert (await revalidate(case, bad))["valid"] is False


async def test_original_plan_source_drift_is_detected(case):
    receipt = await freeze(case)
    await case.session.execute(
        update(ChapterModel)
        .where(ChapterModel.id == case.chapter.id)
        .values(metadata_json={"outline_source_admission": {"source": "changed"}})
    )
    assert (await revalidate(case, receipt))["valid"] is False


async def test_unflagged_legacy_refresh_is_not_a_v1_acceptance(case):
    await case.session.execute(
        update(ProjectModel).where(ProjectModel.id == case.project.id).values(metadata_json={})
    )
    assert (
        await length.refresh_after_adoption(
            case.session, case.settings, project=case.project, chapter=case.chapter
        )
        is None
    )


async def test_refresh_requires_native_adoption_not_working_candidate(case):
    receipt = await freeze(case)
    await parent(case)
    with pytest.raises(length.LengthControlError, match="NOT_ADOPTED"):
        await length.refresh_after_adoption(
            case.session, case.settings, project=case.project, chapter=case.chapter, receipt=receipt
        )


async def test_platform_and_legacy_bounds_remain_stricter(case):
    await case.session.execute(
        update(ProjectModel)
        .where(ProjectModel.id == case.project.id)
        .values(metadata_json={"book_length_control": FLAG, "platform_target": "起点"})
    )
    receipt = await freeze(case)
    assert receipt["feasible"] is False
    assert receipt["window"] is None
    result = length.evaluate_candidate(receipt, "字" * 2000)
    assert result["length_passed"] is False
    assert_no_authority(result)
