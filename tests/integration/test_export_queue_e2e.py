"""Actual ARQ -> native export -> fresh closure, on synthetic isolated PG rows.

No publisher/admission/control/finance/lease mock. Only settings/session factory
are injected. Gateway/SDK/HTTP replacements are zero-send tripwires, not evidence.
"""
from copy import deepcopy
import hashlib
import inspect
import json
import pickle
from pathlib import Path
import subprocess
import sys
import warnings
from uuid import uuid4

import pytest
from arq.connections import create_pool,RedisSettings
from arq.jobs import Job
from arq.worker import Worker
from sqlalchemy import select

from .test_export_only_recovery_pg import pg_case,immutable  # noqa: F401
from bestseller.infra.db import session as db_session
from bestseller.infra.db import models as m
from bestseller.services import export_only_recovery as native
from bestseller.services.book_delivery_readiness import read_book_delivery_closure
from bestseller.worker import export_recovery as queue

pytestmark=[pytest.mark.integration,pytest.mark.asyncio]
EVIDENCE=Path(__file__).parents[2]/'reports/project-e2e-validation-2026-10-25/export-queue'


@pytest.fixture(autouse=True)
def zero_send_tripwire(monkeypatch):
    calls=[]
    def deny(*a,**kw):
        calls.append('forbidden gateway/SDK/HTTP invocation')
        raise AssertionError('Export queue E2E forbids every gateway/SDK/HTTP send')
    from bestseller.services import llm
    import httpx,litellm
    for module in list(sys.modules.values()):
        if getattr(module,'__name__','').startswith('bestseller.services.') and hasattr(module,'complete_text'):
            monkeypatch.setattr(module,'complete_text',deny)
    monkeypatch.setattr(llm,'complete_text',deny)
    monkeypatch.setattr(litellm,'completion',deny);monkeypatch.setattr(litellm,'acompletion',deny)
    monkeypatch.setattr(httpx.Client,'send',deny);monkeypatch.setattr(httpx.AsyncClient,'send',deny)
    yield calls
    assert calls==[],calls


@pytest.fixture
def isolated_redis_endpoint():
    """Independent, nonpersistent container; no reused application Redis URL."""
    name='export-queue-test-'+uuid4().hex[:16]
    result=subprocess.run(['docker','run','--pull=never','--rm','-d','--name',name,
        '-p','127.0.0.1::6379','redis:7-alpine','redis-server','--save','','--appendonly','no'],
        capture_output=True,text=True)
    assert result.returncode==0,'Cannot create independent test Redis: '+result.stderr[:200]
    try:
        port=subprocess.run(['docker','port',name,'6379/tcp'],check=True,capture_output=True,text=True).stdout.strip()
        host,number=port.rsplit(':',1);assert host=='127.0.0.1' and 1024<int(number)<65536
        yield host,int(number),name
    finally:
        # Discard the isolated instance only AFTER assertions. Never delete a
        # result key to make a deterministic requeue artificially succeed.
        stopped=subprocess.run(['docker','stop',name],capture_output=True,text=True)
        assert stopped.returncode==0,'Test Redis cleanup failed'


@pytest.fixture
async def queued_case(pg_case,isolated_redis_endpoint,monkeypatch):
    case=pg_case;host,port,container=isolated_redis_endpoint
    case.settings.output.base_dir=Path(case.settings.output.base_dir).resolve()
    case.settings.artifact_store.local_dir=str(case.settings.output.base_dir.parent/'artifact-store')
    case.settings.llm.mock=False
    assert 'synthetic' in case.project.slug and case.schema.startswith('r08_test_')
    # Shared direct-helper fixture used an arbitrary r08-test-* scope. A real
    # worker canonicalizes only native project/conception scope namespaces.
    # Give this newly synthetic project its real native scope, never bypass it.
    async with case.factory() as session:
        stored=await session.get(m.ProjectModel,case.project.id)
        stored.metadata_json={**stored.metadata_json,'execution_scope_id':f'project:{stored.id}'}
        await session.commit()
        case.project.metadata_json=deepcopy(stored.metadata_json)
    monkeypatch.setattr(db_session,'_global_session_factory',case.factory)
    monkeypatch.setattr(queue,'get_settings',lambda:case.settings)
    qname='arq:queue:export-test:'+uuid4().hex
    redis=await create_pool(RedisSettings(host=host,port=port,database=0),default_queue_name=qname)
    assert await redis.ping()
    from bestseller.worker.main import WorkerSettings
    registration=[f for f in WorkerSettings.functions
        if getattr(f,'name',getattr(f,'__name__',None))=='run_export_only_recovery_task']
    assert len(registration)==1
    workers=[]
    case.redis=redis;case.queue_name=qname;case.container=container
    case.job_id='export-only:heal:'+case.project.slug
    case.result_key='arq:result:'+case.job_id
    case.worker_keep_result=getattr(WorkerSettings,'keep_result',inspect.signature(Worker).parameters['keep_result'].default)
    case.registration_keep_result=getattr(registration[0],'keep_result_s',None)

    async def consume(*, legacy_result=False):
        # Actual production function registration/retention; no startup hooks
        # that could scan unrelated projects or install another DB/Redis pool.
        case.last_legacy_result=legacy_result
        # Actual old retention, real Worker/handler/result serialization. This
        # intentionally reproduces the captured pre-fix registration only.
        functions=[queue.run_export_only_recovery_task] if legacy_result else registration
        worker=Worker(functions=functions,redis_pool=redis,queue_name=qname,
            burst=True,handle_signals=False,max_jobs=1,poll_delay=.01,
            job_timeout=WorkerSettings.job_timeout,max_tries=WorkerSettings.max_tries,
            keep_result=case.worker_keep_result,log_results=False)
        workers.append(worker)
        count=await worker.run_check()
        assert worker.jobs_failed==0
        return count
    case.consume=consume
    try:yield case
    finally:
        try:
            with warnings.catch_warnings():
                # ARQ 0.26 cleanup calls Redis.close, deprecated in Redis5;
                # keep real Worker cleanup, silence only that vendor warning.
                warnings.filterwarnings('ignore',message='Call to deprecated close.*',category=DeprecationWarning)
                for worker in workers:await worker.close()
        finally:
            await redis.aclose(close_connection_pool=True)


async def original_finance(case):
    async with case.factory() as s:
        rows=[]
        for model in (m.ExecutionBudgetModel,m.ExecutionOperationModel,m.ExecutionAttemptModel):
            rows.extend(native._row(row) for row in await s.scalars(select(model)))
        return native._sha(rows)


async def fresh_delivery(case):
    """Native closure plus direct checksums/source-body bytes, not a saved bool."""
    async with case.factory() as s:
        project=await s.get(m.ProjectModel,case.project.id)
        closure=await read_book_delivery_closure(s,project,lock=False)
        metadata=deepcopy(project.metadata_json)
    paths=metadata.get('fanqie_whole_export_receipt',{}).get('export_paths',{})
    observed={}
    for label,value in paths.items():
        if not value:continue
        p=Path(value).resolve()
        assert p.is_relative_to(case.settings.output.base_dir)
        if p.is_file():
            data=p.read_bytes();observed[label]={'path':str(p),'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()}
    if closure.verdict.is_complete:
        assert project.status=='completed'
        required={'markdown_path','readiness_path','source_receipt_path'}
        assert required<=set(observed)
        assert case.draft.content_md in Path(paths['markdown_path']).read_text()
        for label in ('readiness_path','source_receipt_path'):json.loads(Path(paths[label]).read_text())
        for label,record in observed.items():
            saved=metadata['fanqie_whole_export_receipt']['delivery_verification']['files'].get(label)
            if saved:assert record['sha256']==saved['sha256']
        for key in ('retained_counters','debt','old_failed_title'):
            assert metadata[key]==case.project.metadata_json[key]
    return closure.verdict.is_complete,metadata,observed


async def result_evidence(case):
    job=Job(case.job_id,case.redis,_queue_name=case.queue_name)
    info=await job.result_info()
    return {'result_exists':info is not None,'result_ttl_ms':await case.redis.pttl(case.result_key),
        'result_key_sha256':hashlib.sha256(await case.redis.get(case.result_key) or b'').hexdigest(),
        'result':info.result if info is not None else None,
        'function':info.function if info is not None else None,
        'queued_count':await case.redis.zcard(case.queue_name),
        'in_progress':bool(await case.redis.exists('arq:in-progress:'+case.job_id)),
        'worker_keep_result':case.worker_keep_result,'registration_keep_result':case.registration_keep_result,
        'last_worker_used_legacy_registration':getattr(case,'last_legacy_result',False)}


def evidence(name,case,data):
    EVIDENCE.mkdir(parents=True,exist_ok=True)
    value={'scenario':name,'synthetic_retained_rows':True,'not_paid_or_literary_acceptance':True,
        'project_id':str(case.project.id),'project_slug':case.project.slug,'database':'bestseller_repair_test_20261015',
        'schema':case.schema,'job_id':case.job_id,'test_redis_container':case.container,
        'provider_calls':0,**data}
    (EVIDENCE/(name+'.json')).write_text(json.dumps(value,ensure_ascii=False,indent=2,default=str)+'\n')


async def first_complete(case, *, legacy_result=False):
    source=await immutable(case);finance=await original_finance(case)
    queued=await queue.enqueue_export_only_recoveries(case.settings,case.redis)
    assert queued==[{'slug':case.project.slug,'task_id':case.job_id,'heal_kind':'export_only'}],queued
    assert await case.consume(legacy_result=legacy_result)==1
    result=await result_evidence(case)
    evidence('first-result-probe-'+case.project.slug,case,{'actual_arq':result})
    if result['result'] is not None:assert result['result']['status']=='completed',result['result']
    complete,metadata,files=await fresh_delivery(case)
    assert complete and await immutable(case)==source and await original_finance(case)==finance
    return source,finance,metadata,files,result


async def test_first_complete_actual_queue_worker_native_publisher_and_fresh_readback(queued_case):
    case=queued_case
    source,finance,metadata,files,result=await first_complete(case)
    evidence('first-complete',case,{'source_sha256':source,'financial_sha256':finance,'native_closure_complete':True,
        'files':files,'actual_arq':result})


async def test_same_project_queue_and_completed_delivery_are_idempotent(queued_case):
    case=queued_case;source=await immutable(case)
    first=await queue.enqueue_export_only_recoveries(case.settings,case.redis)
    duplicate=await queue.enqueue_export_only_recoveries(case.settings,case.redis)
    assert len(first)==1 and duplicate==[] and await case.redis.zcard(case.queue_name)==1
    assert await case.consume()==1
    complete,_,files=await fresh_delivery(case);assert complete
    after=await queue.enqueue_export_only_recoveries(case.settings,case.redis)
    assert after==[] and await immutable(case)==source
    evidence('same-project-idempotent',case,{'first_enqueue':first,'duplicate_enqueue':duplicate,'after_complete_enqueue':after,
        'native_closure_complete':complete,'files':files,'actual_arq':await result_evidence(case)})


async def test_deleted_tmp_export_requeues_same_deterministic_id_without_deleting_arq_result(queued_case):
    case=queued_case
    source,finance,metadata,files,first_result=await first_complete(case)
    path=Path(metadata['fanqie_whole_export_receipt']['export_paths']['markdown_path']).resolve()
    assert path.is_relative_to(case.settings.output.base_dir) and 'synthetic' in case.project.slug
    original=path.read_bytes();path.unlink()  # Only this fixture's tmp export file.
    complete,_,_=await fresh_delivery(case);assert not complete
    async with case.factory() as s:
        preflight=await native.preflight_export_only_recovery(s,case.settings,project_id=case.project.id,requested_by=queue.REQUESTED_BY)
    assert preflight.status is native.ExportOnlyStatus.ELIGIBLE,preflight.to_dict()
    second=await queue.enqueue_export_only_recoveries(case.settings,case.redis)
    executed=await case.consume()
    complete,_,readback=await fresh_delivery(case)
    actual=await result_evidence(case)
    evidence('deleted-export-requeue',case,{'source_unchanged':await immutable(case)==source,
        'finance_unchanged':await original_finance(case)==finance,'deleted_tmp_path':str(path),
        'first_arq':first_result,'native_preflight':preflight.to_dict(),'second_enqueue':second,'jobs_executed_after_deletion':executed,
        'fresh_native_closure_complete':complete,'file_restored':path.is_file(),'readback':readback,'actual_arq':actual,
        'no_manual_arq_key_deletion':True})
    assert await immutable(case)==source and await original_finance(case)==finance
    assert second==[{'slug':case.project.slug,'task_id':case.job_id,'heal_kind':'export_only'}],(
        'Native export eligible but deterministic recovery was not requeued; actual ARQ result TTL ms='+str(actual['result_ttl_ms']))
    assert executed==1 and complete and path.read_bytes()==original


async def missing_after_real_legacy_result(case):
    source,finance,metadata,files,result=await first_complete(case,legacy_result=True)
    assert result['result_exists'] and result['result']['status']=='completed'
    assert result['result_ttl_ms']>3500000
    raw=await case.redis.get(case.result_key)
    path=Path(metadata['fanqie_whole_export_receipt']['export_paths']['markdown_path']).resolve()
    assert path.is_relative_to(case.settings.output.base_dir)
    body=path.read_bytes();path.unlink()
    return source,finance,path,body,raw,result


async def test_real_old_completed_arq_result_is_retired_then_same_id_runs_current_policy(queued_case):
    case=queued_case
    source,finance,path,body,raw,old=await missing_after_real_legacy_result(case)
    assert case.registration_keep_result==0
    queued=await queue.enqueue_export_only_recoveries(case.settings,case.redis)
    assert queued==[{'slug':case.project.slug,'task_id':case.job_id,'heal_kind':'export_only'}]
    assert not await case.redis.exists(case.result_key)  # Deleted by native CAS, never the test.
    assert await case.consume()==1
    complete,_,files=await fresh_delivery(case)
    assert complete and path.read_bytes()==body
    assert not await case.redis.exists(case.result_key)  # New policy doesn't recreate it.
    assert await immutable(case)==source and await original_finance(case)==finance
    evidence('legacy-result-compatible',case,{'actual_old_arq_result':old,
        'old_raw_sha256':hashlib.sha256(raw).hexdigest(),'same_id_enqueue':queued,
        'new_job_executed':1,'new_registration_keep_result':case.registration_keep_result,
        'native_closure_complete':complete,'files':files,'source_unchanged':True,'finance_unchanged':True,
        'test_manually_deleted_result_key':False})


@pytest.mark.parametrize('active',['job','in-progress','retry','queue','lease'])
async def test_active_ownership_never_loses_its_old_result_or_keys(queued_case,active):
    from arq.jobs import deserialize_result,serialize_job
    from arq.utils import timestamp_ms
    case=queued_case
    source,finance,path,_,raw,old=await missing_after_real_legacy_result(case)
    saved=deserialize_result(raw)
    if active=='queue':
        await case.redis.zadd(case.queue_name,{case.job_id:timestamp_ms()+60000});key=None
    elif active=='lease':
        key='bestseller:pipeline-live:'+case.project.slug
        await case.redis.set(key,uuid4().hex,ex=30)
    elif active=='job':
        key='arq:job:'+case.job_id
        await case.redis.set(key,serialize_job(saved.function,saved.args,saved.kwargs,1,timestamp_ms()),ex=30)
    else:
        key='arq:'+active+':'+case.job_id;await case.redis.set(key,b'1',ex=30)
    owned=await case.redis.get(key) if key else await case.redis.zscore(case.queue_name,case.job_id)
    assert await queue.enqueue_export_only_recoveries(case.settings,case.redis)==[]
    assert await case.redis.get(case.result_key)==raw
    assert (await case.redis.get(key) if key else await case.redis.zscore(case.queue_name,case.job_id))==owned
    assert not path.exists() and await immutable(case)==source and await original_finance(case)==finance
    evidence('legacy-active-refused-'+active,case,{'active_kind':active,'old_result_unchanged':True,
        'ownership_unchanged':True,'source_unchanged':True,'finance_unchanged':True,'actual_old_result':old})


@pytest.mark.parametrize('foreign',['function','project','slug','job_id','unknown'])
async def test_foreign_or_unknown_retained_result_is_not_deleted(queued_case,foreign):
    case=queued_case
    source,finance,path,_,raw,old=await missing_after_real_legacy_result(case)
    # Explicit adversarial Redis fixture bytes, derived from a REAL ARQ result.
    # These are not represented as a provider return or native paid verdict.
    altered=pickle.loads(raw)
    if foreign=='function':altered['f']='run_autowrite_task'
    elif foreign=='project':altered['k']['payload']['project_id']=str(uuid4())
    elif foreign=='slug':altered['k']['payload']['project_slug']='foreign-synthetic-project'
    elif foreign=='job_id':altered['id']='export-only:heal:foreign-synthetic-project'
    else:altered['r']['status']='unknown'
    stored=pickle.dumps(altered)
    await case.redis.set(case.result_key,stored,px=await case.redis.pttl(case.result_key))
    assert await queue.enqueue_export_only_recoveries(case.settings,case.redis)==[]
    assert await case.redis.get(case.result_key)==stored
    assert not path.exists() and await immutable(case)==source and await original_finance(case)==finance
    evidence('legacy-foreign-refused-'+foreign,case,{'adversarial_fixture_field':foreign,'stored_result_unchanged':True,
        'source_unchanged':True,'finance_unchanged':True,'original_actual_arq_result':old})
