# ruff: noqa: F811
"""Actual SQLite: report multiplicity cannot spend repair passes."""
from uuid import UUID,uuid4
from sqlalchemy.schema import CreateTable
from sqlalchemy import select
import pytest
from test_draft_continuity import sql  # noqa:F401
from bestseller.infra.db import models as m
from bestseller.services.chapter_repair_attempt_history import load_repair_attempt_history
from bestseller.services.quality_report_claim import graded_text_fingerprint
from bestseller.services.quality_plateau import evaluate_rework_plateau,blocking_code_scores
pytestmark=[pytest.mark.unit,pytest.mark.asyncio]

async def seed(s,chapter,version,body,template='chapter_first_writer',task_id=None):
 metadata={'chapter_number':chapter.chapter_number}
 if template.startswith('chapter_rewrite'):
  if task_id is None:
   task=m.RewriteTaskModel(project_id=chapter.project_id,trigger_type='chapter_auto_repair',trigger_source_id=chapter.id,rewrite_strategy='fixture',priority=1,status='failed',instructions='fixture exact source',context_required=[],metadata_json={},attempts=1)
   s.add(task);await s.flush();task_id=task.id
  metadata['rewrite_task_id']=str(task_id)
 run=m.LlmRunModel(project_id=chapter.project_id,logical_role='writer',provider='minimax',model_name='synthetic',prompt_template=template,prompt_version='fixture',prompt_hash=str(uuid4()),finish_reason='stop',metadata_json=metadata)
 s.add(run);await s.flush()
 draft=m.ChapterDraftVersionModel(project_id=chapter.project_id,chapter_id=chapter.id,version_no=version,content_md=body,is_current=version==1,promotion_state='candidate',llm_run_id=run.id)
 s.add(draft);await s.flush();return draft

async def report(s,chapter,body,extra=None):
 row=m.ChapterQualityReportModel(chapter_id=chapter.id,report_json={'graded':graded_text_fingerprint(body),'blocking_codes':['LENGTH_UNDER','DUPLICATE_CONTENT'],**(extra or {})},blocks_write=True,regen_attempts=0)
 s.add(row);await s.flush();return row

async def test_many_intermediate_reports_are_three_persisted_attempt_samples(sql):
 async with sql() as s:
  connection=await s.connection();await connection.exec_driver_sql(str(CreateTable(m.RewriteTaskModel.__table__).compile(dialect=connection.dialect)).replace('::jsonb',''))
  p=m.ProjectModel(slug='unrelated-orbital-probe',title='Probe',genre='science-fiction',target_chapters=5,target_word_count=10000,metadata_json={});s.add(p);await s.flush()
  c=m.ChapterModel(project_id=p.id,chapter_number=2,chapter_goal='Launch a new balloon.',metadata_json={'chapter_first_local_repair_count':1,'chapter_first_full_regeneration_count':1});s.add(c);await s.flush()
  bodies=['The tide gauge was inspected.','The sounding bag was sealed.','The sounding bag was released.']
  for i,body in enumerate(bodies,1):
   await seed(s,c,i,body,'chapter_rewrite_repair' if i==2 else 'chapter_first_writer')
   for _ in range(4):await report(s,c,body)
  await report(s,c,'An intermediate edit not persisted as a draft.')
  await seed(s,c,4,'A cosmetic variation, not a repair.','scene_writer');await report(s,c,'A cosmetic variation, not a repair.')
  second_draft=await s.scalar(select(m.ChapterDraftVersionModel).where(m.ChapterDraftVersionModel.chapter_id==c.id,m.ChapterDraftVersionModel.version_no==2))
  second=await s.get(m.LlmRunModel,second_draft.llm_run_id)
  await seed(s,c,5,bodies[1],'chapter_rewrite_repair',second.metadata_json['rewrite_task_id'])
  completed_task=await s.get(m.RewriteTaskModel,UUID(second.metadata_json['rewrite_task_id']));completed_task.status='completed'
  await s.commit();before=dict(c.metadata_json)
  history=await load_repair_attempt_history(s,chapter=c)
  assert len(history)==3 and [x['repair_attempt_source']['version_no'] for x in history]==[1,5,3]
  assert all(x['graded']==graded_text_fingerprint(body) for x,body in zip(history,bodies,strict=True))
  decision=evaluate_rework_plateau(blocking_code_scores(history),patience=3,min_delta=1.0)
  assert not decision.should_stop and c.metadata_json==before
  # A real additional completed candidate is a fourth attempt even if it
  # repeats earlier prose. Neither its rejection nor its counter is erased.
  await seed(s,c,6,bodies[0],'chapter_rewrite');await s.commit()
  history=await load_repair_attempt_history(s,chapter=c)
  assert len(history)==4
  assert evaluate_rework_plateau(blocking_code_scores(history),patience=3,min_delta=1.0).should_stop
  assert c.metadata_json==before

async def test_unbound_report_missing_origin_and_foreign_rows_cannot_establish_plateau(sql):
 async with sql() as s:
  connection=await s.connection();await connection.exec_driver_sql(str(CreateTable(m.RewriteTaskModel.__table__).compile(dialect=connection.dialect)).replace('::jsonb',''))
  p=m.ProjectModel(slug='another-coast',title='Coast',genre='realistic',target_chapters=3,target_word_count=6000,metadata_json={});s.add(p);await s.flush()
  c=m.ChapterModel(project_id=p.id,chapter_number=1,chapter_goal='Secure the quay.',metadata_json={});s.add(c);await s.flush()
  await seed(s,c,1,'Current rope is secured.')
  await report(s,c,'A different body, not the secured rope.')
  await report(s,c,'Current rope is secured.',{'graded':{}})
  s.add(m.ChapterDraftVersionModel(project_id=p.id,chapter_id=c.id,version_no=2,content_md='Unknown origin.',is_current=False,promotion_state='candidate'))
  await report(s,c,'Unknown origin.')
  await s.commit()
  assert await load_repair_attempt_history(s,chapter=c)==[]
  assert await load_repair_attempt_history(s,chapter=c,limit=0)==[]
