"""File SQLite / real config services and API adapters; no vendor or production.

Literary generation is not performed. Consumer probes stop before model calls.
"""
from copy import deepcopy
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
import json
import re
import socket

import pytest
from sqlalchemy import select, text, event
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.schema import CreateTable
from support.adopted_bible_fixture import seed_empty_bible
from test_adopted_knowledge_lineage import _jsonb  # noqa: F401

from bestseller.infra.db.models import (ProjectModel, WorkflowRunModel, ChapterModel, ChapterDraftVersionModel, ChapterStateSnapshotModel,
    PlanningArtifactVersionModel, ExportArtifactModel, SceneCardModel, BookProductionControlModel,
    CharacterModel, RelationshipModel, WorldRuleModel, CharacterStateSnapshotModel)
from bestseller.services import creation_configuration as cfg
from bestseller.services.creation_intent_contract import CreationIntentContract
from bestseller.services.genre_intent_contract import GenreIntentContract
from bestseller.services.word_targets import effective_chapter_word_target, normalize_chapter_word_target
from bestseller.settings import load_settings
from bestseller.web import server

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


def submission(total=1000, target=2000, end=20, purpose='opening_window'):
    return {'configuration':{'platforms':{'primary':'qidian','compatible':['fanqie']},
        'readers':{'primary':'偏好精密推演与成长的读者','secondary':['轻松冒险读者']},
        'book':{'total_chapters':total,'chapter_target':target},'field_sources':{'platforms':'explicit','readers':'explicit','book':'explicit'}},
        'execution_window':{'purpose':purpose,'start':1,'end':end}}


@pytest.fixture
async def store(tmp_path, monkeypatch):
    def deny_network(*_args, **_kwargs):
        raise AssertionError('Configuration slice forbids network/provider calls')
    monkeypatch.setattr(socket.socket, 'connect', deny_network)
    path=tmp_path/'configuration_slice_test.sqlite3'
    assert path.name.endswith('_test.sqlite3')
    engine=create_async_engine(f'sqlite+aiosqlite:///{path}')
    @event.listens_for(engine.sync_engine,'connect')
    def sqlite_regex(dbapi,_record):
        dbapi.create_function('regexp_replace',4,lambda value,pattern,replacement,flags:re.sub(pattern,replacement,value or ''))
    async with engine.begin() as conn:
        identity=(await conn.execute(text('PRAGMA database_list'))).all()
        assert Path(next(row[2] for row in identity if row[1]=='main'))==path
        for model in (ProjectModel,WorkflowRunModel,ChapterModel,ChapterDraftVersionModel,ChapterStateSnapshotModel,PlanningArtifactVersionModel,ExportArtifactModel,SceneCardModel,BookProductionControlModel,
            CharacterModel,RelationshipModel,WorldRuleModel,CharacterStateSnapshotModel):
            ddl=str(CreateTable(model.__table__).compile(dialect=conn.dialect))
            await conn.exec_driver_sql(ddl.replace('::jsonb',''))
    settings=load_settings(env={}).model_copy(deep=True)
    settings.database.url=f'sqlite+aiosqlite:///{path}'
    settings.output.base_dir=tmp_path/'output'
    calls=[]
    from bestseller.services import llm
    async def deny(*args,**kwargs):
        calls.append('forbidden_vendor')
        raise AssertionError('supplier forbidden')
    monkeypatch.setattr(llm,'_call_litellm',deny)
    factory=async_sessionmaker(engine,expire_on_commit=False)
    @asynccontextmanager
    async def isolated_scope(_settings):
        async with factory() as session:
            yield session
    monkeypatch.setattr(server,'session_scope',isolated_scope)
    async with factory() as s:
        p=ProjectModel(slug='synthetic-observatory',title='测站合成项目',genre='悬疑',language='zh-CN',
            target_chapters=30,target_word_count=78000,metadata_json={'protected_authored':'保留'})
        s.add(p);await s.commit();pid=p.id
    yield SimpleNamespace(engine=engine,sessions=factory,settings=settings,pid=pid,slug=p.slug,calls=calls,path=path)
    assert calls==[]
    await engine.dispose()


async def test_save_reload_api_adapters_and_2000_consumer(store):
    s=store
    before=await server._project_creation_configuration(s.settings,s.slug)
    assert before['legacy'] and before['whole_book_chapters']==30
    saved=await server._project_creation_configuration(s.settings,s.slug,{'submission':submission(),
        'expected_hash':None,'expected_window_hash':None})
    assert saved['status']=='saved' and saved['diff']['automatic_rewrite'] is False
    readback=await server._project_creation_configuration(s.settings,s.slug)
    assert readback==saved['effective']
    async with s.sessions() as session:
        p=await session.get(ProjectModel,s.pid)
        assert p.target_chapters==1000 and p.target_word_count==2000000
        assert p.metadata_json['protected_authored']=='保留'
        assert effective_chapter_word_target(p,s.settings)==2000
        assert normalize_chapter_word_target(2600,p,s.settings)==2000
        assert cfg.project_settings(s.settings,p).generation.words_per_chapter.target==2000
        assert cfg.filter_window(p,range(1,31))==set(range(1,21))
        from bestseller.services import planner
        assert planner._project_target_platform(p,p.metadata_json)=='起点中文网'
        assert planner._project_target_audiences(p,{},p.metadata_json)==['偏好精密推演与成长的读者']
        assert s.settings.generation.words_per_chapter.target==2600
    assert not s.calls


@pytest.mark.parametrize('target',[1800,2000,2300,3000,3500])
async def test_custom_targets_roundtrip_without_2400_inflation(store,target):
    r=await server._project_creation_configuration(store.settings,store.slug,{'submission':submission(target=target),
        'expected_hash':None,'expected_window_hash':None})
    assert r['effective']['configuration']['book']['chapter_target']==target
    async with store.sessions() as s:
        p=await s.get(ProjectModel,store.pid)
        assert effective_chapter_word_target(p,store.settings)==target


@pytest.mark.parametrize('fault',['float','boolean','over_window','bad_primary','duplicate_reader','too_short','image_forgery'])
async def test_typed_configuration_rejects_invalid_values_without_writes(store,fault):
    v=submission()
    if fault=='float':v['configuration']['book']['total_chapters']=1000.5
    elif fault=='boolean':v['configuration']['book']['chapter_target']=True
    elif fault=='over_window':v['execution_window']['end']=1001
    elif fault=='bad_primary':v['configuration']['platforms']['primary']='unregistered'
    elif fault=='duplicate_reader':v['configuration']['readers']['secondary']=[v['configuration']['readers']['primary']]
    elif fault=='too_short':v['configuration']['book']['chapter_target']=1000
    else:v['configuration']['creative_asset_refs']=['unapproved-image']
    with pytest.raises(ValueError):cfg.preview_submission(v)
    before=await server._project_creation_configuration(store.settings,store.slug)
    assert before['legacy'] and before['whole_book_chapters']==30


async def test_protected_content_becomes_visible_proposal_never_automatic_rewrite(store):
    async with store.sessions() as s:
        ch=ChapterModel(project_id=store.pid,chapter_number=1,chapter_goal='保留采用稿',metadata_json={})
        s.add(ch);await s.flush()
        draft=ChapterDraftVersionModel(project_id=store.pid,chapter_id=ch.id,version_no=1,content_md='已采用的合成正文。',
            is_current=True,promotion_state='promoted')
        s.add(draft);await s.commit();did=draft.id
    result=await server._project_creation_configuration(store.settings,store.slug,{'submission':submission(),
        'expected_hash':None,'expected_window_hash':None})
    assert result['status']=='proposal_requires_replan'
    assert result['diff']['affected_chapters']==[1] and result['effective']['legacy']
    async with store.sessions() as s:
        p=await s.get(ProjectModel,store.pid);d=await s.get(ChapterDraftVersionModel,did)
        assert (p.target_chapters,p.target_word_count)==(30,78000)
        assert d.content_md=='已采用的合成正文。' and d.promotion_state=='promoted'


async def test_config_window_cas_and_legacy_nonmigration(store):
    legacy=await server._project_creation_configuration(store.settings,store.slug)
    assert legacy['configuration'] is None
    first=await server._project_creation_configuration(store.settings,store.slug,{'submission':submission(),
        'expected_hash':None,'expected_window_hash':None})
    v=submission();v['execution_window']['end']=10
    with pytest.raises(ValueError,match='concurrent'):
        await server._project_creation_configuration(store.settings,store.slug,{'submission':v,
            'expected_hash':first['effective']['configuration_hash'],'expected_window_hash':None})
    assert await server._project_creation_configuration(store.settings,store.slug)==first['effective']


async def test_v1_wire_and_hash_keep_original_shape():
    c=CreationIntentContract(genre_intent=GenreIntentContract(genre_key='mystery',genre_label='悬疑',prompt_pack_key='mystery'))
    raw=c.model_dump(mode='json')
    assert 'configuration' not in raw and 'configuration' not in raw['field_sources']
    assert CreationIntentContract.model_validate(raw).contract_hash()==c.contract_hash()
    from bestseller.services.prompt_source_policy import content_hash
    assert content_hash(raw)==c.contract_hash()


async def test_real_quickstart_custom_reader_beats_male_and_survives_save_consumption(store,monkeypatch,tmp_path):
    captured={}
    def no_dispatch(self,payload):
        captured.update(deepcopy(payload));return {'task_id':'isolated-no-dispatch'}
    monkeypatch.setattr(server.WebTaskManager,'create_autowrite_task',no_dispatch)
    manager=server.WebTaskManager(persist_path=tmp_path/'inert_tasks.json')
    manager.create_quickstart_task({'genre_key':'xianxia-upgrade','chapter_count':1000,
        'audience_orientation':'male','configuration_submission':submission()})
    primary=submission()['configuration']['readers']['primary']
    assert captured['audience']==primary and captured['audience_orientation']=='male'
    assert captured['target_words']==2000000 and captured['target_chapters']==1000
    contract=captured['creation_intent_contract']
    assert contract['schema_version']=='creation-intent.v2' and contract['audience_orientation']=='male'
    async with store.sessions() as s:
        p=await s.get(ProjectModel,store.pid)
        p.metadata_json={**p.metadata_json,**captured['metadata']}
        p.target_word_count=captured['target_words'];p.target_chapters=1000;p.audience=captured['audience']
        await s.commit()
    async with store.sessions() as s:
        p=await s.get(ProjectModel,store.pid)
        payload=server._payload_from_project_model(p)
        assert payload['target_chapters']==1000 and payload['metadata']['creation_configuration']['readers']['primary']==primary
        from bestseller.services import planner,conception
        assert planner._project_target_audiences(p,{},p.metadata_json)==[primary]
        ctx=conception._build_genre_context('xianxia-upgrade',1000,creation_intent_contract=contract)
        assert primary in conception._market_user_prompt(ctx)
        assert ctx['target_audience']==primary and ctx['words_per_chapter']==2000
    assert not store.calls


async def test_real_chapter_entry_rejects_21_before_any_paid_preprocessing(store):
    from bestseller.services import pipelines
    await server._project_creation_configuration(store.settings,store.slug,{'submission':submission(),
        'expected_hash':None,'expected_window_hash':None})
    async with store.sessions() as s:
        with pytest.raises(ValueError,match='chapter_outside_execution_window'):
            await pipelines.run_chapter_pipeline.__wrapped__(s,store.settings,store.slug,21)


async def seed_adopted(session,project,chapter,number,*,settings,monkeypatch):
    from bestseller.services.feedback import validate_adopted_knowledge_source
    d=ChapterDraftVersionModel(project_id=project.id,chapter_id=chapter.id,version_no=1,
        content_md=f'第{number}次合成测站记录。'+('值班员核对时间与封签。'*180),is_current=True,promotion_state='promoted')
    session.add(d);chapter.status='complete';chapter.production_state='ok';await session.flush()
    lineage,rejection=await validate_adopted_knowledge_source(session,project_id=project.id,chapter_id=chapter.id,
        source_chapter_draft_version_id=d.id,chapter_md=d.content_md,lock=False)
    assert rejection is None
    chapter.metadata_json={**(chapter.metadata_json or {}),'knowledge_feedback_receipts':[{'status':'empty','source_lineage':lineage}]}
    # Feedback/snapshot extraction is synthetic; adopted-body ownership and
    # persisted projections are real and independently source-bound. Bible
    # closure below uses the actual service with only its transport replaced.
    from bestseller.services import continuity
    from bestseller.services.continuity import _current_snapshot_input, _snapshot_dependencies
    previous=await _current_snapshot_input(session,project_id=project.id,chapter_number=number-1)
    snapshot=ChapterStateSnapshotModel(project_id=project.id,chapter_id=chapter.id,
        chapter_number=number,extraction_status='ok_promoted',facts={'facts':[],
        'snapshot_contract':{**lineage,'is_usable':True,
            'snapshot_dependencies':_snapshot_dependencies(number,previous)}},
        raw_extraction=json.dumps({'facts':[]}),extraction_model='synthetic-fixture')
    session.add(snapshot)
    await session.flush()
    # Admit the source-bound literal fixture through the real DB-only API.
    # Original raw, body, adoption and dependency evidence remain unchanged.
    original_facts=deepcopy(snapshot.facts)
    original_raw=snapshot.raw_extraction
    original_body=d.content_md
    witness=continuity.snapshot_epistemic_witness(snapshot)
    source={'project_id':project.id,'chapter_id':chapter.id,
        'source_chapter_draft_version_id':d.id,'chapter_md':d.content_md}
    proof=await continuity.validate_snapshot_epistemic_view(session,**source)
    assert proof['snapshot_sha256']==witness
    assert continuity.snapshot_epistemic_witness(snapshot)==witness
    applied=await continuity.apply_snapshot_epistemic_view(session,**source,
        expected_snapshot_sha256=proof['snapshot_sha256'],admission_receipt=proof['admission_receipt'],
        actor='configuration-slice-fixture',reason='Admit retained synthetic source without model calls')
    assert applied['status']=='applied' and applied['receipt']['provider_calls']==0
    assert {key:snapshot.facts[key] for key in original_facts}==original_facts
    assert snapshot.raw_extraction==original_raw and d.content_md==original_body
    assert d.is_current and d.promotion_state=='promoted'
    assert await continuity.snapshot_evidence_current(session,project_id=project.id,chapter_number=number)
    calls = await seed_empty_bible((session,settings,project,chapter,d),monkeypatch)
    assert len(calls) == 1
    assert calls[0].metadata['source_lineage']['source_body_sha256'] == lineage['source_body_sha256']
    from bestseller.services.chapter_knowledge_state import adopted_bible_status
    assert adopted_bible_status(chapter,lineage) == 'closed'
    return d


async def test_real_project_driver_1000_book_stops_20_and_resume_zero_new(store,monkeypatch):
    from bestseller.services import pipelines
    from bestseller.domain.pipeline import ChapterPipelineResult
    from bestseller.infra.db.models import WorkflowStepRunModel
    async with store.engine.begin() as c:
        await c.exec_driver_sql(str(CreateTable(WorkflowStepRunModel.__table__).compile(dialect=c.dialect)).replace('::jsonb',''))
    await server._project_creation_configuration(store.settings,store.slug,{'submission':submission(),
        'expected_hash':None,'expected_window_hash':None})
    async with store.sessions() as s:
        for number in range(1,22):s.add(ChapterModel(project_id=store.pid,chapter_number=number,chapter_goal='合成输入',metadata_json={}))
        await s.commit()
    settings=store.settings.model_copy(deep=True)
    settings.pipeline.require_foundation_identity_lock=False
    settings.pipeline.enable_outline_semantic_gate=False
    settings.pipeline.enable_commercial_planning_readiness_gate=False
    settings.pipeline.enable_rolling_outline=False
    settings.pipeline.consistency_check_interval=0
    settings.pipeline.rolling_summary_interval=0
    settings.pipeline.arc_summary_enabled=False
    async def inert(*args,**kwargs):return None
    for name in ('_ensure_project_invariants','_ensure_emotion_kernel_backfill_for_pipeline',
        '_ensure_public_emotion_kernel_backfill_for_pipeline','_ensure_entry_system_backfill_for_pipeline',
        '_enforce_truth_version_guard','sync_world_expansion_progress'):
        monkeypatch.setattr(pipelines,name,inert)
    monkeypatch.setattr(pipelines,'_project_uses_whole_book_quality_gate',lambda p:False)
    sent=[]
    async def synthetic_chapter(session,settings,slug,number,**kwargs):
        assert settings.generation.words_per_chapter.target==2000
        sent.append(number)
        p=await session.get(ProjectModel,store.pid)
        ch=await session.scalar(select(ChapterModel).where(ChapterModel.project_id==p.id,ChapterModel.chapter_number==number))
        d=await seed_adopted(session,p,ch,number,settings=settings,monkeypatch=monkeypatch)
        return ChapterPipelineResult(workflow_run_id=__import__('uuid').uuid4(),project_id=p.id,
            chapter_id=ch.id,chapter_number=number,chapter_draft_id=d.id,chapter_draft_version_no=1,final_verdict='synthetic')
    monkeypatch.setattr(pipelines,'run_chapter_pipeline',synthetic_chapter)
    async with store.sessions() as s:
        result=await pipelines.run_project_pipeline.__wrapped__(s,settings,store.slug,
            materialize_narrative_graph=False,materialize_narrative_tree=False,export_markdown=False)
        assert result.final_verdict=='execution_window_completed'
        assert sent==list(range(1,21))
    async with store.sessions() as s:
        p=await s.get(ProjectModel,store.pid)
        assert p.target_chapters==1000 and p.target_word_count==2000000 and p.status!='completed'
        ch21=await s.scalar(select(ChapterModel).where(ChapterModel.project_id==p.id,ChapterModel.chapter_number==21))
        assert ch21.status!='complete'
        resumed=await pipelines.run_project_pipeline.__wrapped__(s,settings,store.slug,export_markdown=False)
        assert resumed.final_verdict=='execution_window_completed' and resumed.chapter_results==[]
        assert len(sent)==20


@pytest.mark.parametrize('purpose,total,expected_kind',[('opening_window',1000,'execution_window_excerpt'),('complete_story',20,'complete_manuscript')])
async def test_window_export_labels_exact_adopted_range_and_not_whole_book_acceptance(store,monkeypatch,purpose,total,expected_kind):
    from bestseller.services import exports
    await server._project_creation_configuration(store.settings,store.slug,{'submission':submission(total=total,purpose=purpose),
        'expected_hash':None,'expected_window_hash':None})
    async with store.sessions() as s:
        p=await s.get(ProjectModel,store.pid)
        for number in range(1,21):
            ch=ChapterModel(project_id=p.id,chapter_number=number,chapter_goal='合成导出',metadata_json={})
            s.add(ch);await s.flush();await seed_adopted(s,p,ch,number,settings=store.settings,monkeypatch=monkeypatch)
        await s.commit()
    # Scope/adoption/knowledge/provenance are real; synthetic prose is not
    # literary acceptance. Only the content-only terminal judge is adapted.
    monkeypatch.setattr(exports,'_run_terminal_export_gate',lambda *a,**k:None)
    monkeypatch.setattr(exports,'_raise_if_export_blocked',lambda *a,**k:None)
    async with store.sessions() as s:
        artifact,path=await exports.export_execution_window_markdown(s,store.settings,store.slug)
        await s.commit()
        assert artifact.metadata_json['artifact_kind']==expected_kind
        assert artifact.metadata_json['whole_book_complete'] is False
        assert artifact.metadata_json['whole_book_chapters']==total
        assert len(artifact.metadata_json['source_drafts'])==20
        assert f'全书目标{total}章' in path.read_text()
        if total==1000:assert '非全书完结' in path.read_text()


async def test_window_without_knowledge_does_not_claim_complete_or_export(store):
    from bestseller.services.exports import export_execution_window_markdown
    await server._project_creation_configuration(store.settings,store.slug,{'submission':submission(total=20,purpose='complete_story'),
        'expected_hash':None,'expected_window_hash':None})
    async with store.sessions() as s:
        with pytest.raises(ValueError,match='knowledge_closed'):
            await export_execution_window_markdown(s,store.settings,store.slug)


async def _configured_one(store, *, adopted=False, monkeypatch=None):
    await server._project_creation_configuration(store.settings,store.slug,{'submission':submission(end=1),
        'expected_hash':None,'expected_window_hash':None})
    async with store.sessions() as s:
        p=await s.get(ProjectModel,store.pid)
        ch=ChapterModel(project_id=p.id,chapter_number=1,chapter_goal='独立隔离输入',target_word_count=2600,metadata_json={})
        s.add(ch);await s.flush()
        if adopted: await seed_adopted(s,p,ch,1,settings=store.settings,monkeypatch=monkeypatch)
        await s.commit()
        return ch.id


@pytest.mark.parametrize('debt',['chapter','project','paused'])
async def test_completed_cache_preserves_review_debt_and_pause(store,monkeypatch,debt):
    from bestseller.services import execution_windows as windows
    cid=await _configured_one(store,adopted=True,monkeypatch=monkeypatch)
    async def forbidden(*a,**k): raise AssertionError('debt must not export')
    from bestseller.services import exports
    monkeypatch.setattr(exports,'export_execution_window_markdown',forbidden)
    async with store.sessions() as s:
        p=await s.get(ProjectModel,store.pid);ch=await s.get(ChapterModel,cid)
        if debt=='chapter':ch.metadata_json={**ch.metadata_json,'requires_human_review':True}
        elif debt=='project':p.metadata_json={**p.metadata_json,'whole_book_quality_gate_blocked':True}
        else:p.status='paused'
        await s.commit()
        if debt=='paused':
            with pytest.raises(ValueError,match='halted'):await windows.completed_window_resume(s,store.settings,p,export_markdown=True)
        else:
            r=await windows.completed_window_resume(s,store.settings,p,export_markdown=True)
            assert r.requires_human_review and r.final_verdict=='execution_window_requires_review'
            assert r.export_artifact_id is None and p.status=='revising'


async def test_knowledge_only_resume_binds_adopted_body_without_rewrite(store,monkeypatch):
    from bestseller.services import execution_windows as windows,feedback
    cid=await _configured_one(store,adopted=True,monkeypatch=monkeypatch)
    async with store.sessions() as s:
        ch=await s.get(ChapterModel,cid);receipt=deepcopy(ch.metadata_json['knowledge_feedback_receipts'])
        ch.metadata_json={k:v for k,v in ch.metadata_json.items() if k!='knowledge_feedback_receipts'}
        await s.commit()
    seen=[]
    async def synthetic_feedback(session,settings,**kw):
        # Only extraction is synthetic; exact adopted source validation before
        # and after and durable workflow/receipt persistence are real.
        ch=kw['chapter'];d=await session.get(ChapterDraftVersionModel,kw['source_chapter_draft_version_id'])
        assert kw['chapter_md']==d.content_md
        seen.append(str(d.id));ch.metadata_json={**ch.metadata_json,'knowledge_feedback_receipts':receipt}
    monkeypatch.setattr(feedback,'extract_chapter_feedback',synthetic_feedback)
    async with store.sessions() as s:
        p=await s.get(ProjectModel,store.pid)
        r=await windows.completed_window_resume(s,store.settings,p)
        assert r.final_verdict=='execution_window_completed' and r.chapter_results==[]
        assert len(seen)==1
        assert len(list(await s.scalars(select(ChapterDraftVersionModel))))==1


async def test_scanner_and_queue_bound_window_not_whole_target(store,monkeypatch):
    from bestseller.worker import self_heal
    cid=await _configured_one(store)
    async def no_halts(*a,**k):return set()
    async def noop(*a,**k):return None
    async def inactive(*a,**k):return False
    monkeypatch.setattr(self_heal,'halted_project_ids',no_halts)
    monkeypatch.setattr(self_heal,'assert_execution_scope_open',noop)
    monkeypatch.setattr(self_heal,'_has_active_continuation_pipeline_run',inactive)
    @asynccontextmanager
    async def isolated():
        async with store.sessions() as s:yield s
    monkeypatch.setattr(self_heal,'get_server_session',isolated)
    async with store.sessions() as s:
        rows=await self_heal.find_stuck_projects(s)
        assert len(rows)==1 and rows[0].configured_window and rows[0].heal_kind=='autowrite'
        assert await self_heal._window_queue_allowed(rows[0])
        assert not await self_heal._window_queue_allowed(rows[0],repair=True)
        p=await s.get(ProjectModel,store.pid);ch=await s.get(ChapterModel,cid)
        await seed_adopted(s,p,ch,1,settings=store.settings,monkeypatch=monkeypatch);await s.commit()
        assert await self_heal.find_stuck_projects(s)==[]
        assert not await self_heal._window_queue_allowed(rows[0])
        assert p.target_chapters==1000


async def test_repair_service_refuses_window_before_global_mutation(store,monkeypatch):
    from bestseller.services import repair
    await _configured_one(store)
    async def forbidden(*a,**k):raise AssertionError('global repair forbidden')
    monkeypatch.setattr(repair,'refresh_rewrite_impacts',forbidden)
    async with store.sessions() as s:
        with pytest.raises(ValueError,match='execution_window_requires_explicit_bounded_repair'):
            await repair.run_project_repair(s,store.settings,store.slug,target_chapter_numbers=[1],include_pending_rewrite_tasks=False)


async def test_direct_writer_prompt_receives_2000_despite_old_chapter_2600(store,monkeypatch):
    from bestseller.services import drafts,planner
    from bestseller.infra.db.models import StyleGuideModel
    cid=await _configured_one(store)
    async with store.engine.begin() as c:
        await c.exec_driver_sql(str(CreateTable(StyleGuideModel.__table__).compile(dialect=c.dialect)).replace('::jsonb',''))
    async with store.sessions() as s:
        s.add(SceneCardModel(project_id=store.pid,chapter_id=cid,scene_number=1,scene_type='action',title='合成场景',purpose={}))
        await s.commit()
    monkeypatch.setattr(planner,'_assert_planning_capabilities_available',lambda *a:None)
    async def noop(*a,**k):return None
    for name in ('_enrich_chapter_first_context','_render_chapter_first_character_safety_block','_load_current_draft_text','_prepare_quality_uplift_prompt_blocks'):
        monkeypatch.setattr(drafts,name,noop)
    class PromptCaptured(Exception):pass
    def capture(*a,**kw):
        assert kw['target_word_count']==2000
        raise PromptCaptured
    monkeypatch.setattr(drafts,'build_chapter_first_draft_prompts',capture)
    async with store.sessions() as s:
        with pytest.raises(PromptCaptured):
            await drafts.generate_chapter_draft_once(s,store.slug,1,settings=store.settings,context_packet=SimpleNamespace())
        ch=await s.get(ChapterModel,cid)
        assert ch.target_word_count==2600  # No silent migration of stored cards.


@pytest.mark.parametrize('verdict',['execution_window_incomplete','execution_window_completed'])
async def test_actual_ordinary_autowrite_returns_before_whole_book_repair(store,monkeypatch,verdict):
    from bestseller.services import pipelines
    from bestseller.domain.project import ProjectCreate
    from bestseller.domain.pipeline import ProjectPipelineResult
    from bestseller.domain.planning import NovelPlanningResult
    from bestseller.services import repair
    from uuid import uuid4
    await _configured_one(store)
    async def noop(*a,**k):return None
    async def not_cleared(*a,**k):return False
    monkeypatch.setattr(pipelines,'_should_use_progressive_pipeline',lambda *a:False)  # Force supported ordinary entry.
    monkeypatch.setattr(pipelines,'_clear_auto_resumable_project_pause',not_cleared)
    monkeypatch.setattr(pipelines,'get_latest_approved_planning_artifact',noop)
    monkeypatch.setattr(pipelines,'get_latest_completed_workflow_run',noop)
    async def planning(session,settings,slug,premise,**kw):
        return NovelPlanningResult(workflow_run_id=uuid4(),project_id=store.pid,premise=premise,volume_count=1,chapter_count=20)
    monkeypatch.setattr(pipelines,'generate_novel_plan',planning)
    async def materialize(*a,**k):return SimpleNamespace(workflow_run_id=uuid4(),chapters_created=20,scenes_created=20,plot_arc_count=1,clue_count=1,node_count=1)
    for name in ('materialize_latest_story_bible','materialize_latest_chapter_outline_batch','materialize_latest_narrative_graph','materialize_latest_narrative_tree'):
        monkeypatch.setattr(pipelines,name,materialize)
    aid=uuid4() if verdict.endswith('completed') else None
    async def window_driver(*a,**k):
        return ProjectPipelineResult(workflow_run_id=uuid4(),project_id=store.pid,project_slug=store.slug,
            chapter_results=[],final_verdict=verdict,requires_human_review=aid is None,export_artifact_id=aid)
    monkeypatch.setattr(pipelines,'run_project_pipeline',window_driver)
    async def forbidden(*a,**k):raise AssertionError('unbounded repair/closure forbidden')
    monkeypatch.setattr(pipelines,'_drive_repair_to_closure',forbidden)
    monkeypatch.setattr(repair,'run_project_repair',forbidden)
    if hasattr(pipelines,'run_project_repair'):monkeypatch.setattr(pipelines,'run_project_repair',forbidden)
    async with store.sessions() as s:
        p=await s.get(ProjectModel,store.pid)
        payload=ProjectCreate(slug=p.slug,title=p.title,genre=p.genre,target_chapters=p.target_chapters,
            target_word_count=p.target_word_count,metadata=p.metadata_json)
        r=await pipelines.run_autowrite_pipeline.__wrapped__(s,store.settings,project_payload=payload,premise='合成',export_markdown=True)
        assert r.final_verdict==verdict and not r.repair_attempted and not r.whole_book_complete
        assert r.export_status==('window_exported' if aid else 'window_not_exported')


async def test_actual_repair_worker_stops_before_sweep(store,monkeypatch):
    from bestseller.worker import tasks
    from bestseller.services import chapter_block_recovery
    from uuid import uuid4
    await _configured_one(store)
    async def no_skip(*a,**k):return None
    for name in ('_skip_archived_project_if_needed','_skip_halted_project_if_needed','_skip_outline_replan_project_if_needed'):
        monkeypatch.setattr(tasks,name,no_skip)
    @asynccontextmanager
    async def isolated():
        async with store.sessions() as s:yield s
    monkeypatch.setattr(tasks,'get_server_session',isolated)
    class Reporter:
        def __init__(self,*a):pass
        async def emit(self,*a,**k):pass
    monkeypatch.setattr(tasks,'RedisProgressReporter',Reporter)
    monkeypatch.setattr(tasks,'make_sync_callback',lambda *a:lambda *a,**k:None)
    monkeypatch.setattr(tasks,'set_ambient',lambda *a:None)
    monkeypatch.setattr(tasks,'set_cancellation_checker',lambda *a:None)
    async def forbidden(*a,**k):raise AssertionError('sweep forbidden')
    monkeypatch.setattr(chapter_block_recovery,'sweep_recoverable_blocks',forbidden)
    r=await tasks.run_project_repair_task({'redis':object()},str(uuid4()),{'project_slug':store.slug})
    assert r['status']=='skipped_execution_window_requires_bounded_repair' and not r['production_data_changed']


async def test_corrupt_window_does_not_abort_other_scanner_projects_and_queue_denies(store,monkeypatch):
    from bestseller.worker import self_heal
    await _configured_one(store)
    async with store.sessions() as s:
        p=await s.get(ProjectModel,store.pid);p.metadata_json={**p.metadata_json,'execution_window_hash':'corrupt'}
        await s.commit()
    async def no_halts(*a,**k):return set()
    monkeypatch.setattr(self_heal,'halted_project_ids',no_halts)
    @asynccontextmanager
    async def isolated():
        async with store.sessions() as s:yield s
    monkeypatch.setattr(self_heal,'get_server_session',isolated)
    stuck=self_heal.StuckProject(project_id=store.pid,slug=store.slug,reason='execution_window_incomplete',
        stuck_at_chapter=1,chapters_total=0,chapters_with_draft=0,configured_window=True)
    async with store.sessions() as s:assert await self_heal.find_stuck_projects(s)==[]
    assert not await self_heal._window_queue_allowed(stuck)


async def test_window_only_change_keeps_default_book_provenance_and_creative_hash(store):
    value=submission();value['configuration']['field_sources']['book']='default'
    first=await server._project_creation_configuration(store.settings,store.slug,{'submission':value,'expected_hash':None,'expected_window_hash':None})
    async with store.sessions() as s:
        s.add(ChapterModel(project_id=store.pid,chapter_number=1,chapter_goal='保留已有稿件',metadata_json={}))
        await s.commit()
    value['execution_window']['end']=10
    second=await server._project_creation_configuration(store.settings,store.slug,{'submission':value,
        'expected_hash':first['effective']['configuration_hash'],'expected_window_hash':first['effective']['execution_window_hash']})
    assert second['status']=='saved'
    assert second['effective']['configuration_hash']==first['effective']['configuration_hash']
    assert second['effective']['configuration']['field_sources']['book']=='default'
    assert second['effective']['whole_book_chapters']==1000


async def test_direct_window_export_refuses_review_debt_even_with_adopted_knowledge(store,monkeypatch):
    from bestseller.services.exports import export_execution_window_markdown
    cid=await _configured_one(store,adopted=True,monkeypatch=monkeypatch)
    async with store.sessions() as s:
        ch=await s.get(ChapterModel,cid);ch.metadata_json={**ch.metadata_json,'requires_human_review':True}
        await s.commit()
        with pytest.raises(ValueError,match='review_debt_resolution'):
            await export_execution_window_markdown(s,store.settings,store.slug)
