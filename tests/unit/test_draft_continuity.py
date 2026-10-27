"""Pending-reference policy/SQL isolation. No provider or production database."""
import json
from copy import deepcopy
from types import SimpleNamespace
from uuid import UUID,uuid4
from unittest.mock import AsyncMock
import pytest
from sqlalchemy import select,func
from sqlalchemy.ext.asyncio import create_async_engine,async_sessionmaker
from sqlalchemy.schema import CreateTable
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from bestseller.infra.db import models as m
from bestseller.services import draft_continuity as d
from bestseller.services.critic_evidence_gate import build_critic_body_anchors
from bestseller.services.quality_report_claim import graded_text_fingerprint
from bestseller.services.prompt_source_policy import content_hash
from bestseller.settings import load_settings

pytestmark=pytest.mark.unit
BODY='档案员把玻璃匣留在观测台。\n\n信封尚未打开，旧日谁曾读过，仍待核实。'
SOURCES={'current':{'rule':'不得擅自打开密封匣。','history':'此前谁看过信封，未知。','a/b~c':'保留原字。'},'prior':[]}

def response(body=BODY,sources=None):
    sources=SOURCES if sources is None else sources
    ids=[a['id'] for a in build_critic_body_anchors(body)];sid=d.source_catalog(sources)[0]['id']
    return {'schema':d.POLICY,'body_sha256':d.body_hash(body),'source_sha256':content_hash(sources),'checked_body_ids':ids,
        'checks':[{'axis':a,'status':'pass','coverage':'complete','body_ids':[ids[0]],'source_ids':[sid],'explanation':'按所引规则及当前未开匣的行动逐项核对；未证历史仍明确未知。'} for a in d.AXES],
        'blocking_issues':[],'unknown_issues':[],'submission_complete':'all_applicable_checks_examined'}

@pytest.mark.parametrize('fault',['missing_axis','unknown','partial','body','source','bad_body_id','bad_source_id','coverage_gap','duplicate_coverage','blocking','unknown_issue'])
def test_missing_unknown_mismatched_or_blocked_cannot_qualify(fault):
    value=response()
    if fault=='missing_axis':value['checks'].pop()
    if fault=='unknown':value['checks'][0]['status']='unknown'
    if fault=='partial':value['checks'][0]['coverage']='partial'
    if fault=='body':value['body_sha256']='f'*64
    if fault=='source':value['source_sha256']='f'*64
    if fault=='bad_body_id':value['checks'][0]['body_ids']=['Dother:P0001']
    if fault=='bad_source_id':value['checks'][0]['source_ids']=['unregistered']
    if fault=='coverage_gap':value['checked_body_ids'].pop()
    if fault=='duplicate_coverage':value['checked_body_ids']=[value['checked_body_ids'][0]]*2
    if fault=='blocking':value['blocking_issues']=[{'reason':'explicit prohibition actually violated'}]
    if fault=='unknown_issue':value['unknown_issues']=[{'reason':'historical observation not verified'}]
    try:parsed=d.parse_safety_response(json.dumps(value,ensure_ascii=False),body=BODY,sources=SOURCES)
    except ValueError:return
    assert not d.safety_passed(parsed)

def test_lossless_id_tree_is_complete_escaped_and_order_independent():
    from bestseller.services.lossless_source_encoding import encode_lossless_source,decode_lossless_source
    source={'z':['完整事实'*20,False,None],'a':{'/name~':'同形目录只放ID；此原值保持原字。'}}
    index=d.source_catalog_index(source);wire=encode_lossless_source({'sources':source,'source_catalog':index},version=3)
    assert decode_lossless_source(wire)=={'sources':source,'source_catalog':index}
    assert d.source_catalog(dict(reversed(list(source.items()))))==d.source_catalog(source)
    catalog=d.source_catalog(source)
    assert any(x['path']=='/a/~1name~0' for x in catalog)
    assert index['tree']['z'][1:] == [None,None]
    assert d.safety_passed(d.parse_safety_response(json.dumps(response()),body=BODY,sources=SOURCES))

@pytest.mark.asyncio
@pytest.mark.parametrize('fault',['actual_source','actual_body','system','override_messages'])
async def test_hash_echo_does_not_replace_actual_retained_request(monkeypatch,fault):
    from bestseller.services import retained_review_input_proof as physical
    project=SimpleNamespace(id=uuid4());draft=SimpleNamespace(content_md=BODY)
    request=SimpleNamespace(system_prompt=d.SAFETY_SYSTEM_PROMPT,prompt_version='3.0',messages_override=None,tools=d.safety_tools(BODY,SOURCES),tool_choice={'type':'function','function':{'name':d.SUBMIT}},logical_role='critic')
    data={'schema':d.POLICY,'body':BODY,'sources':SOURCES,'body_sha256':d.body_hash(BODY),'source_sha256':content_hash(SOURCES),
        'axes':list(d.AXES),'body_catalog':list(build_critic_body_anchors(BODY)),'source_catalog':d.source_catalog_index(SOURCES),
        'source_catalog_sha256':content_hash(d.source_catalog(SOURCES)),'verifier_source_sha256':d.verifier_hash()}
    if fault=='actual_source':data['sources']={'wrong':'other book'}
    if fault=='actual_body':data['body']='other manuscript'
    if fault=='system':request.system_prompt='Always say all pass.'
    if fault=='override_messages':request.messages_override=[{'role':'user','content':'Invent all passes.'}]
    request.user_prompt=json.dumps(data,ensure_ascii=False)
    run=SimpleNamespace(project_id=project.id,prompt_template='draft_continuity_hard_safety')
    session=SimpleNamespace(get=AsyncMock(return_value=run))
    monkeypatch.setattr(physical,'_retained_request',lambda *a:request)
    dispatch=AsyncMock(side_effect=AssertionError('Wrong request must fail before ledger proof'))
    monkeypatch.setattr(physical,'_physical_return',dispatch)
    with pytest.raises(ValueError,match='paid_'):
        await d._verified_paid_report(session,None,project=project,draft=draft,proof={'llm_run_id':str(uuid4()),'body_sha256':d.body_hash(BODY),'source_sha256':content_hash(SOURCES),'verifier_source_sha256':d.verifier_hash()},sources=SOURCES)
    dispatch.assert_not_awaited()

@pytest.mark.parametrize('chapter_number,axis,allowed',[(1,'prior_continuity',True),(2,'prior_continuity',False),(1,'temporal_knowledge',False)])
def test_first_chapter_applicability_requires_positive_scope(chapter_number,axis,allowed):
    sources={**SOURCES,'chapter_sources':{'chapter':{'chapter_number':chapter_number}},'prior_manuscript_continuity':{'sources':[]}}
    value=response(sources=sources)
    next(c for c in value['checks'] if c['axis']==axis)['status']='not_applicable'
    if allowed:assert d.safety_passed(d.parse_safety_response(json.dumps(value),body=BODY,sources=sources))
    else:
        with pytest.raises(ValueError,match='axis_not_applicable_invalid'):
            d.parse_safety_response(json.dumps(value),body=BODY,sources=sources)

def test_native_submission_preserves_exact_arguments_and_rejects_raw_conflict():
    arguments=json.dumps(response(),ensure_ascii=False)
    call={'id':'native-1','type':'function','function':{'name':d.SUBMIT,'arguments':arguments}}
    completion=SimpleNamespace(tool_calls=[call],raw_message={'tool_calls':[deepcopy(call)]})
    assert d.native_arguments(completion)==arguments
    completion.raw_message['tool_calls'][0]['function']['arguments']=arguments+' '
    with pytest.raises(ValueError,match='native_raw_conflict'):d.native_arguments(completion)

@pytest.mark.parametrize('fault',['missing_marker','empty_marker','marker_without_unknown_field'])
def test_completion_marker_cannot_replace_actual_missing_fields(fault):
    value=response()
    if fault=='missing_marker':value.pop('submission_complete')
    if fault=='empty_marker':value['submission_complete']=''
    if fault=='marker_without_unknown_field':value.pop('unknown_issues')
    with pytest.raises(ValueError):d.parse_safety_response(json.dumps(value),body=BODY,sources=SOURCES)

@pytest.mark.asyncio
@pytest.mark.parametrize('arguments',['{"unknown_issues": [], "submission_complete": "all_applicable_checks_examined"}','{"unknown_issues": '])
async def test_wire_observer_preserves_complete_and_incomplete_bytes_without_repair(tmp_path,arguments):
    import importlib.util
    import hashlib
    from pathlib import Path
    import httpx
    path=Path(__file__).resolve().parents[2]/'reports/p5-live-first-chapter-2026-10-19/provider_wire_observer.py'
    spec=importlib.util.spec_from_file_location('isolated_wire_observer',path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    entity=json.dumps({'choices':[{'message':{'reasoning_content':'fixture reasoning retained','tool_calls':[{'function':{'name':d.SUBMIT,'arguments':arguments}}]},'finish_reason':'tool_calls'}]},ensure_ascii=False).encode()
    response=httpx.Response(200,content=entity,headers={'x-secret-fixture':'must-not-be-retained'})
    receipt=await module.retain_response_body(response,root=tmp_path,attempt_id='synthetic-attempt',scope='fixture-scope',workflow_id='fixture-workflow')
    saved=Path(receipt['body_file'])
    assert saved.read_bytes()==response.content==entity and receipt['body_sha256']==hashlib.sha256(entity).hexdigest()
    assert json.loads(saved.read_text())['choices'][0]['message']['tool_calls'][0]['function']['arguments']==arguments
    assert 'must-not-be-retained' not in saved.read_text() and receipt['request_headers_retained'] is False
    assert saved.stat().st_mode&0o777==0o600

@compiles(JSONB,'sqlite')
def _jsonb(type_,compiler,**kw):return 'JSON'

@pytest.fixture
async def sql(tmp_path):
    path=tmp_path/'pending_continuity_test.sqlite3';assert 'test' in path.name
    engine=create_async_engine(f'sqlite+aiosqlite:///{path}')
    async with engine.begin() as con:
        for model in (m.ProjectModel,m.ChapterModel,m.ChapterDraftVersionModel,m.WorkflowRunModel,m.ChapterQualityReportModel,m.ReviewReportModel,m.QualityScoreModel,m.CanonFactModel,m.TimelineEventModel,m.ExportArtifactModel,m.LlmRunModel):
            await con.exec_driver_sql(str(CreateTable(model.__table__).compile(dialect=engine.dialect)).replace('::jsonb',''))
    yield async_sessionmaker(engine,expire_on_commit=False)
    await engine.dispose()

@pytest.mark.asyncio
@pytest.mark.parametrize('tamper',['body','source','receipt','formal_priority'])
async def test_noncurrent_pending_reference_reopens_but_never_becomes_canon_or_final_export(sql,monkeypatch,tmp_path,tamper):
    # The semantic/physical verifier boundary is stubbed ONLY for this SQL
    # transaction test. Pure negative tests above and live QA verify that boundary.
    active_sources=deepcopy(SOURCES)
    async def current(*args,**kwargs):return deepcopy(active_sources)
    monkeypatch.setattr(d,'current_sources',current);monkeypatch.setattr(d,'hard_policy_hash',lambda:'a'*64)
    parsed=response();verified=AsyncMock(return_value=parsed);monkeypatch.setattr(d,'_verified_paid_report',verified)
    async with sql() as s:
        project=m.ProjectModel(slug='unrelated-observatory',title='Observatory',genre='science-fiction',target_chapters=3,target_word_count=6000,metadata_json={})
        s.add(project);await s.flush()
        chapter=m.ChapterModel(project_id=project.id,chapter_number=1,chapter_goal='Check the unopened instrument case.',status='revision',production_state='blocked',metadata_json={'chapter_first_local_repair_count':2,'chapter_first_full_regeneration_count':1})
        s.add(chapter);await s.flush()
        baseline=m.ChapterDraftVersionModel(project_id=project.id,chapter_id=chapter.id,version_no=9,content_md=BODY,is_current=False,promotion_state='candidate')
        working=m.ChapterDraftVersionModel(project_id=project.id,chapter_id=chapter.id,version_no=10,content_md='未核实新历史，不得偷偷采用。',is_current=True,promotion_state='candidate')
        s.add_all([baseline,working]);await s.flush()
        binding={'evaluation_id':str(uuid4()),'draft_id':str(baseline.id),'body_sha256':d.body_hash(BODY),'source_sha256':content_hash(SOURCES)}
        qr=m.ChapterQualityReportModel(chapter_id=chapter.id,report_json={'graded':graded_text_fingerprint(BODY),'blocking_codes':[],'draft_continuity_hard_safety':binding},blocks_write=False,regen_attempts=0)
        s.add(qr);await s.flush()
        proof={'schema':d.POLICY,'project_id':str(project.id),'chapter_id':str(chapter.id),'draft_id':str(baseline.id),'body_sha256':d.body_hash(BODY),
            'source_sha256':content_hash(SOURCES),'hard_safety_status':'verified','report':parsed,'deterministic_outcome':'ok','duplicate_findings':[],
            'quality_report_id':str(qr.id),'hard_policy_sha256':'a'*64,'deterministic_binding':binding}
        await d.enable_draft_mode(s,project=project,actor='explicit-fixture-operator',authorization_ref='fixture-authorization',explicit_user_approval=True)
        await d.qualify_reference(s,load_settings(env={}),project=project,chapter=chapter,draft=baseline,proof=proof,actor='explicit-fixture-operator',authorization_ref='fixture-authorization',explicit_user_approval=True)
        await s.commit();ids=project.id,chapter.id,baseline.id,working.id
    async with sql() as s:
        project=await s.get(m.ProjectModel,ids[0]);chapter=await s.get(m.ChapterModel,ids[1]);baseline,reference=await d.select_reference(s,load_settings(env={}),project=project,chapter=chapter)
        assert baseline.id==ids[2] and not baseline.is_current and reference['formal_adoption'] is False
        assert (await s.get(m.ChapterDraftVersionModel,ids[3])).is_current
        assert chapter.production_state=='blocked' and chapter.metadata_json['chapter_first_local_repair_count']==2
        from bestseller.services.exports import _load_adopted_export_draft
        from bestseller.services.feedback import validate_adopted_knowledge_source
        assert await _load_adopted_export_draft(s,chapter) is None
        _,rejection=await validate_adopted_knowledge_source(s,project_id=project.id,chapter_id=chapter.id,source_chapter_draft_version_id=baseline.id,chapter_md=BODY,lock=False)
        assert rejection is not None
        artifact=await d.export_pending_window(s,load_settings(env={}),project=project,start=1,end=1,directory=tmp_path)
        assert artifact['reopened'] and not artifact['formal_export'] and artifact['progress']['qualified_pending']==[1]
        assert artifact['progress']['whole_book_complete'] is False
        for model in (m.CanonFactModel,m.TimelineEventModel,m.ExportArtifactModel):assert await s.scalar(select(func.count()).select_from(model))==0
        from bestseller.services.planning_continuity_context import build_planning_continuity_context
        with pytest.raises(ValueError,match='prior_chapter_not_adopted'):
            await build_planning_continuity_context(s,project_id=project.id,before_chapter=2)
        context=await build_planning_continuity_context(s,project_id=project.id,before_chapter=2,allow_draft_references=True)
        assert context['adopted_sources']==[] and context['recent_adopted_prose']==[]
        assert context['qualified_pending_sources'][0]['draft_id']==str(baseline.id)
        assert context['recent_qualified_pending_prose'][0]['body']==BODY
        from bestseller.services.commercial_review_context import load_commercial_review_history,load_authorized_commercial_history
        with pytest.raises(ValueError,match='prior_chapter_not_adopted'):
            await load_commercial_review_history(s,project_id=project.id,chapter_number=2)
        commercial=await load_authorized_commercial_history(s,project_id=project.id,chapter_number=2)
        assert commercial['recent_reference_prose'][0]['body']==BODY and commercial['adopted_sources']==[]
        from bestseller.services.context import _load_previous_chapter_draft_summary
        summary=await _load_previous_chapter_draft_summary(s,project_id=project.id,current_chapter_number=2,settings=load_settings(env={}))
        assert summary.summary==BODY and summary.source_reference['formal_adoption'] is False
        assert 'v9' in summary.scene_title and 'v10' not in summary.scene_title
        if tamper=='formal_priority':
            formal=m.ChapterDraftVersionModel(project_id=project.id,chapter_id=chapter.id,version_no=11,content_md=BODY+'\n\n正式采用的新结尾。',is_current=False,promotion_state='promoted')
            s.add(formal);await s.commit()
            forbidden=AsyncMock(side_effect=AssertionError('Formal source must take precedence over old pending pointer'))
            monkeypatch.setattr(d,'select_reference',forbidden)
            summary=await _load_previous_chapter_draft_summary(s,project_id=project.id,current_chapter_number=2,settings=load_settings(env={}))
            assert summary.source_reference['formal_adoption'] is True and 'v11' in summary.scene_title
            forbidden.assert_not_awaited()
            return
        if tamper=='body':baseline.content_md+='已篡改。'
        if tamper=='source':active_sources['current']['rule']='Changed current source constraint.'
        if tamper=='receipt':
            receipt=await s.get(m.WorkflowRunModel,UUID(reference['receipt_id']))
            metadata=deepcopy(receipt.metadata_json);metadata['proof']['hard_safety_status']='unknown';receipt.metadata_json=metadata
        await s.commit()
        expected={'body':'referenced_body_changed','source':'referenced_sources_changed','receipt':'receipt_proof_changed'}[tamper]
        with pytest.raises(ValueError,match=expected):
            await d.select_reference(s,load_settings(env={}),project=project,chapter=chapter)

def test_literary_rewrite_does_not_claim_hard_failure_or_hard_pass():
    from bestseller.services.reviews import _promotion_evidence_from_review
    result=_promotion_evidence_from_review(SimpleNamespace(verdict='rewrite',findings=[],evidence_summary={}))
    assert result['hard_gates_passed'] is None and result['independent_hard_safety_status']=='unknown'
    assert result['literary_review_verdict']=='rewrite' and result['review_gate_passed'] is False


@pytest.mark.asyncio
async def test_hard_stage_retains_real_run_on_parse_failure_and_binds_project_audit(sql,monkeypatch):
    from bestseller.services import llm,drafts,retained_review_input_proof as physical
    async def source(*a,**k):return deepcopy(SOURCES)
    monkeypatch.setattr(d,'current_sources',source)
    async def gate(*,session,project,chapter_number,content,extra_report_payload,**kw):
        chapter=await session.scalar(select(m.ChapterModel).where(m.ChapterModel.project_id==project.id,m.ChapterModel.chapter_number==chapter_number))
        session.add(m.ChapterQualityReportModel(chapter_id=chapter.id,blocks_write=False,regen_attempts=0,report_json={**extra_report_payload,'graded':graded_text_fingerprint(content),'blocking_codes':[]}))
        await session.flush();return 'ok'
    monkeypatch.setattr(drafts,'_evaluate_chapter_quality_gate',gate)
    monkeypatch.setattr(drafts,'_collect_post_assembly_duplicate_findings',AsyncMock(return_value=()))
    native=[{'id':'synthetic-native','type':'function','function':{'name':d.SUBMIT,'arguments':'{"unknown_issues": '}}]
    record={}
    async def gateway(session,settings,request):
        assert llm._project_prompt_audit.get()[0]==request.project_id
        run=m.LlmRunModel(project_id=request.project_id,workflow_run_id=request.workflow_run_id,logical_role='critic',provider='openai',model_name='fixture-model',
            prompt_template=request.prompt_template,prompt_version=request.prompt_version,prompt_hash='a'*64,input_tokens=5,output_tokens=3,finish_reason='tool_calls',metadata_json={'fixture':True,'request_was_audited':True})
        session.add(run);await session.flush();record['id']=run.id
        return llm.LLMCompletionResult(content='',provider='openai',model_name='fixture-model',llm_run_id=run.id,input_tokens=5,output_tokens=3,finish_reason='tool_calls',tool_calls=native,raw_message={'tool_calls':native})
    monkeypatch.setattr(llm,'complete_text',gateway)
    async def physical_boundary(session,run,**kw):
        return llm.LLMCompletionResult(content='',provider='openai',model_name='fixture-model',input_tokens=5,output_tokens=3,finish_reason='tool_calls',tool_calls=native,raw_message={'tool_calls':native}),None
    monkeypatch.setattr(physical,'_physical_return',physical_boundary)
    async with sql() as s:
        p=m.ProjectModel(slug='checkpoint-fixture',title='Checkpoint fixture',genre='science-fiction',target_chapters=2,target_word_count=4000,metadata_json={})
        s.add(p);await s.flush();c=m.ChapterModel(project_id=p.id,chapter_number=1,chapter_goal='Observe the unopened instrument',production_state='blocked',metadata_json={});s.add(c);await s.flush()
        draft=m.ChapterDraftVersionModel(project_id=p.id,chapter_id=c.id,version_no=1,content_md=BODY,is_current=True,promotion_state='candidate');s.add(draft)
        wf=m.WorkflowRunModel(project_id=p.id,workflow_type='draft_continuity_hard_safety',scope_type='chapter',scope_id=c.id,status='running',current_step='fixture');s.add(wf);await s.commit()
        with pytest.raises(json.JSONDecodeError):await d.evaluate_hard_safety(s,load_settings(env={}),project=p,chapter=c,draft=draft,workflow_id=wf.id,scope='test-fixture')
        await s.rollback()
    async with sql() as s:
        stored=await s.get(m.LlmRunModel,record['id'])
        assert stored is not None and stored.metadata_json['request_was_audited'] is True
        assert llm._project_prompt_audit.get() is None
        assert await s.scalar(select(func.count()).select_from(m.CanonFactModel))==0
