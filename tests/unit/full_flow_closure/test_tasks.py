"""Native task producer/consumer seams, without adoption or model authority."""
from copy import deepcopy
from types import SimpleNamespace
from uuid import UUID

import pytest
from sqlalchemy import select
from test_rewrite_outline_binding import db as db, seed

from bestseller.infra.db.models import RewriteTaskModel
from bestseller.services import fanqie_short_source_export as export
from bestseller.services import reviews
from bestseller.services.fanqie_short_ranking_gate import FanqieRankingFinding, FanqieRankingGateReport
from bestseller.services.rewrite_patch import text_hash

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


def review(*, title=False, two=False):
    findings = tuple(FanqieRankingFinding(code=code, severity='critical', message='Repair only the cited issue.', evidence='typed component input', phase='title' if title else 'opening', target='title' if title else 'opening') for code in (('title_abstract_setting_only',) if title else ('first_screen_pressure_missing', 'first_screen_goal_missing') if two else ('first_screen_pressure_missing',)))
    return SimpleNamespace(ranking_report=None, short_v2_report=FanqieRankingGateReport(passed=False, phase='whole_story', findings=findings))


def assembly(c):
    return SimpleNamespace(manifest={'schema':'component-input-no-adoption-authority.v1','project_id':str(c.project.id),'sources':[{'chapter_number':1,'chapter_id':str(c.chapter.id),'draft_id':str(c.draft.id),'raw_body_sha256':text_hash(c.draft.content_md)}]},full_text=c.draft.content_md)


async def test_whole_tasks_bind_native_outline_and_preserve_old_task(db):
    async with db() as s:
        c = await seed(s)
        old = deepcopy((c.task.instructions,c.task.metadata_json,c.task.attempts,c.task.status))
        _, ids = await export._revision_tasks(s,c.project,assembly(c),review(),1)
        task = await s.get(RewriteTaskModel,UUID(ids[0]))
        assert task.metadata_json['source_plan_binding']==c.current
        await reviews._validate_rewrite_task_parent(s,task,c.draft)
        await s.commit()
        await s.refresh(c.task)
        assert (c.task.instructions,c.task.metadata_json,c.task.attempts,c.task.status)==old


async def test_same_chapter_findings_merge_into_one_fresh_task_and_reentry_is_stable(db):
    async with db() as s:
        c=await seed(s)
        before=assembly(c)
        counts,ids=await export._revision_tasks(s,c.project,before,review(two=True),1)
        assert len(ids)==1
        task=await s.get(RewriteTaskModel,UUID(ids[0]))
        assert len(task.metadata_json['whole_findings'])==2
        original=deepcopy(task.metadata_json)
        await s.commit()
        again,ids2=await export._revision_tasks(s,c.project,before,review(two=True),1)
        assert ids2==ids and again=={'ranking':0,'short_v2':0}
        assert task.metadata_json==original


async def test_title_only_never_creates_a_chapter_body_task(db):
    async with db() as s:
        c=await seed(s)
        counts,ids=await export._revision_tasks(s,c.project,assembly(c),review(title=True),1)
        assert not ids and counts=={'ranking':0,'short_v2':0}
        assert c.project.metadata_json['fanqie_whole_title_repair']['status']=='pending'
        assert list(await s.scalars(select(RewriteTaskModel)))==[c.task]


async def test_word_contract_has_explicit_work_instead_of_zero_tasks(db):
    async with db() as s:
        c=await seed(s)
        # This component has exactly one source. It is not an admitted plan or
        # a complete-book fixture; keep its declared census equally explicit.
        c.project.target_chapters = 1
        c.project.metadata_json={**c.project.metadata_json,'book_length_control':{'protocol':'book-length-control-v1','counting':'export-body-words-v1'}}
        whole=SimpleNamespace(ranking_report=None,short_v2_report=None)
        _,ids=await export._revision_tasks(s,c.project,assembly(c),whole,1)
        assert ids
        t=await s.get(RewriteTaskModel,UUID(ids[0]))
        assert t.metadata_json['whole_findings'][0]['finding']['code']=='whole_word_count_under'
        assert t.metadata_json['source_plan_binding']==c.current
