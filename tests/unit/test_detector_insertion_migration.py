from types import SimpleNamespace

import pytest
from sqlalchemy import select

from bestseller.infra.db.models import ChapterDraftVersionModel,ChapterModel
from bestseller.services.framework_state_migration import migrate_detector_insertions
from test_wp4_integration_closure import db,seed  # noqa: F401

pytestmark=pytest.mark.unit


@pytest.mark.asyncio
async def test_receipted_insert_becomes_review_candidate_with_original_preserved(db):
    chapter,_,draft,_=await seed(db)
    original="# 一章\n\n由旧程序追加的观察。\n\n原作者的场景和对白。"
    async with db() as session:
        row=await session.get(ChapterModel,chapter.id)
        row.metadata_json={"deterministic_hook_echo_bridge":{"bridge":"由旧程序追加的观察。","inserted_tokens":["观察"]}}
        current=await session.get(ChapterDraftVersionModel,draft.id)
        current.content_md=original
        await session.commit()
    project=SimpleNamespace(id=chapter.project_id,language="zh-CN")
    async with db() as session:
        report=await migrate_detector_insertions(session,project,apply=False)
        assert report[0]["status"]=="candidate_requires_review"
        assert len(list(await session.scalars(select(ChapterDraftVersionModel))))==1
    for _ in range(2):
        async with db() as session:
            await migrate_detector_insertions(session,project,apply=True)
            await session.commit()
    async with db() as session:
        rows=list(await session.scalars(select(ChapterDraftVersionModel)))
        assert len(rows)==2
        old=await session.get(ChapterDraftVersionModel,draft.id)
        assert old.content_md==original and old.is_current
        candidate=next(r for r in rows if r.id!=draft.id)
        assert candidate.content_md=="# 一章\n\n原作者的场景和对白。"
        assert not candidate.is_current and candidate.promotion_state=="candidate"
        receipt=(await session.get(ChapterModel,chapter.id)).metadata_json["framework_injection_repair"]
        assert receipt["parent_id"]==str(draft.id)


@pytest.mark.asyncio
@pytest.mark.parametrize("apply", [False, True])
async def test_unverified_insert_never_claims_a_created_candidate(db, apply):
    chapter, _, draft, _ = await seed(db)
    async with db() as session:
        row = await session.get(ChapterModel, chapter.id)
        row.metadata_json = {"deterministic_hook_echo_bridge": {"bridge": "无法核对的旧插句。"}}
        await session.commit()
    async with db() as session:
        report = await migrate_detector_insertions(
            session, SimpleNamespace(id=chapter.project_id, language="zh-CN"), apply=apply,
        )
        await session.commit()
        assert report[0]["status"] == "needs_source_review"
        assert report[0]["candidate_id"] is None
        assert len(list(await session.scalars(select(ChapterDraftVersionModel)))) == 1


@pytest.mark.asyncio
async def test_changed_legacy_body_creates_distinct_bound_candidate_and_archives_receipt(db):
    chapter, _, draft, _ = await seed(db)
    project = SimpleNamespace(id=chapter.project_id, language="zh-CN")
    async with db() as session:
        row = await session.get(ChapterModel, chapter.id)
        row.metadata_json = {"deterministic_hook_echo_bridge": {"bridge": "旧程序的插句。"}}
        current = await session.get(ChapterDraftVersionModel, draft.id)
        current.content_md = "# 一章\n\n旧程序的插句。\n\n她在院中修桌。"
        await session.commit()
    async with db() as session:
        first = await migrate_detector_insertions(session, project, apply=True)
        await session.commit()
    async with db() as session:
        current = await session.get(ChapterDraftVersionModel, draft.id)
        current.content_md = "# 一章\n\n旧程序的插句。\n\n他在剧场调琴。"
        await session.commit()
    async with db() as session:
        second = await migrate_detector_insertions(session, project, apply=True)
        await session.commit()
        assert first[0]["candidate_id"] != second[0]["candidate_id"]
    async with db() as session:
        third = await migrate_detector_insertions(session, project, apply=True)
        await session.commit()
        assert third == second
        rows = list(await session.scalars(select(ChapterDraftVersionModel)))
        assert len(rows) == 3
        assert {row.content_md for row in rows if not row.is_current} == {
            "# 一章\n\n她在院中修桌。", "# 一章\n\n他在剧场调琴。",
        }
        metadata = (await session.get(ChapterModel, chapter.id)).metadata_json
        assert len(metadata["framework_injection_repair_history"]) == 1
