"""Offline rehearsal evidence controls. No application startup or credentials."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import traceback


class HarnessInvariantError(ValueError):
    def __init__(self, code, *, differences):
        self.differences = differences
        super().__init__(code)


def canonical_row(value):
    # Only native typed datetimes; strings including old paid wire stay exact.
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise HarnessInvariantError('NAIVE_COMPARISON_TIMESTAMP', differences=[
                {'field':'timestamp.tzinfo','expected':summary('aware'),'actual':summary(None)}])
        return value.astimezone(timezone.utc).isoformat(timespec='microseconds')
    if isinstance(value, dict):
        return {key:canonical_row(item) for key,item in value.items()}
    if isinstance(value, (list, tuple)):
        return [canonical_row(item) for item in value]
    return value


def summary(value):
    return {'type':type(value).__name__,
        'length':len(value) if isinstance(value,(dict,list,str,bytes)) else None,
        'sha256':hashlib.sha256(json.dumps(value,sort_keys=True,default=str).encode()).hexdigest()}


def require(ok, field, expected, actual):
    if not ok:
        raise HarnessInvariantError(field, differences=[
            {'field':field,'expected':summary(expected),'actual':summary(actual)}])


def verify_budgets(before, after, *, target, calls_delta, tokens_delta):
    expected = deepcopy(before)
    require(target in expected, 'budget.target_exists', True, target in expected)
    expected[target]['calls_used'] += calls_delta
    expected[target]['tokens_charged'] += tokens_delta
    exact=lambda value:json.dumps(value,sort_keys=True,default=str,separators=(",",":"))
    if exact(expected) != exact(after):
        differences=[]
        for scope in sorted(set(expected) | set(after)):
            left,right=expected.get(scope),after.get(scope)
            if isinstance(left,dict) and isinstance(right,dict):
                for field in sorted(set(left) | set(right)):
                    if exact(left.get(field))!=exact(right.get(field)):
                        differences.append({'field':f'budgets.{scope}.{field}',
                            'expected':summary(left.get(field)),'actual':summary(right.get(field))})
            elif exact(left)!=exact(right):
                differences.append({'field':f'budgets.{scope}',
                    'expected':summary(left),'actual':summary(right)})
        raise HarnessInvariantError('BUDGET_PROTECTED_FIELDS_CHANGED', differences=differences)


def id_batches(identifiers, *, size=1000):
    require(type(size) is int and 0<size<=1000, 'id_batch_size', '1..1000', size)
    batch=[]
    for identity in identifiers:
        batch.append(identity)
        if len(batch)==size:
            yield batch
            batch=[]
    if batch:
        yield batch


def error_code(error):
    if isinstance(error, HarnessInvariantError):
        return str(error)
    value=str(getattr(error,'code',None) or str(error).split('\n')[0])
    if re.fullmatch(r"[A-Za-z0-9_.:' -]{1,180}", value):
        return value
    return type(error).__name__+':'+summary(value)['sha256'][:20]


def failure_differences(error, step):
    explicit=getattr(error,'differences',None)
    if explicit:return explicit
    frame=None;trace=error.__traceback__
    while trace is not None:
        frame=trace.tb_frame
        trace=trace.tb_next
    if isinstance(error,KeyError):
        key=str(error.args[0]) if error.args else 'Unknown'
        if len(key)>160:key='sha256:'+summary(key)['sha256']
        return [{'field':step+'.missing_key.'+key,
            'expected':{'type':'present_mapping_value','length':None,'sha256':None},
            'actual':{'type':'missing','length':0,'sha256':summary(None)['sha256']},
            'operands':{name:summary(value) for name,value in (frame.f_locals.items() if frame else ())
                if isinstance(value,(dict,list,str,bool,int,float,type(None)))}}]
    return [{'field':step,'expected':summary('successful native stage'),
        'actual':summary(type(error).__name__+':'+str(error)),
        'location':None if frame is None else frame.f_code.co_filename+':'+str(frame.f_lineno),
        'operands':{name:summary(value) for name,value in (frame.f_locals.items() if frame else ())
            if isinstance(value,(dict,list,str,bool,int,float,type(None)))}}]


class RunState:
    def __init__(self, path):
        self.path=Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.evidence_root=self.path.parent/(self.path.stem+"-evidence")
        self.evidence_root.mkdir(exist_ok=True)
        self.sequence=0
        self.failed=False
        self.data={'status':'进行中','secondary_failures':[],
            'failed_workflow_type':'CP1_native_successor','failed_workflow_step':'initializing',
            'root_exception_type':'RunNotFinished','root_error_code':'RUN_NOT_FINISHED',
            'last_successful_llm_template':'Unknown: no completed native call observed',
            'script_consumed_index':0,'traceback_path':str(self.evidence_root/'traceback.txt')}
        self.persist()

    def persist(self):
        raw=json.dumps(self.data,ensure_ascii=False,indent=2,default=str)+'\n'
        temporary=self.path.with_name(self.path.name+'.next')
        temporary.write_text(raw)
        temporary.replace(self.path)
        self.sequence+=1
        stages=self.evidence_root/'stages';stages.mkdir(exist_ok=True)
        (stages/f'{self.sequence:04d}.json').write_text(raw)

    def checkpoint(self, step, **values):
        self.data.update(current_stage=step,**values)
        if not self.failed:
            self.data['failed_workflow_step']=step
        self.data['status']='失败' if self.failed else '进行中'
        self.persist()

    def failure(self, error, *, step, secondary=False):
        path=self.evidence_root/f'traceback-{self.sequence+1:04d}.txt'
        path.write_text(''.join(traceback.format_exception(error)))
        item={'root_exception_type':type(error).__name__,'root_error_code':error_code(error),
            'failed_workflow_step':step,'traceback_path':str(path),
            'failure_differences':failure_differences(error,step),
            'root_location':None if error.__traceback__ is None else traceback.extract_tb(error.__traceback__)[-1].filename+':'+str(traceback.extract_tb(error.__traceback__)[-1].lineno)}
        if self.failed:
            self.data['secondary_failures'].append(item)
        else:
            self.data.update(item)
        self.failed=True;self.data['status']='失败';self.persist()

    def finish(self, *, final_checks_complete=False):
        if not self.failed and not final_checks_complete:
            self.failure(HarnessInvariantError('FINAL_CHECKS_NOT_COMPLETE',differences=[
                {'field':'final_checks_complete','expected':summary(True),'actual':summary(False)}]),step='finalize')
        if not self.failed:
            self.data.update(status='通过',root_exception_type='none',root_error_code='none',
                failed_workflow_step=None,final_checks_complete=True)
        self.persist()


def _raw(value):
    if value is None:return b''
    return value if isinstance(value,bytes) else value.encode()


def run_owned_container(command, *, name, out, timeout, run=subprocess.run):
    require('--name' in command and command[command.index('--name')+1]==name,
        'owned_container_name',name,command)
    out=Path(out);out.mkdir(parents=True,exist_ok=True)
    docker=command[0]
    with (out/'stdout.log').open('wb') as stdout, (out/'stderr.log').open('wb') as stderr:
        try:
            process=run(command,stdout=stdout,stderr=stderr,timeout=timeout)
            return {'exit_code':process.returncode,'timed_out':False}
        except subprocess.TimeoutExpired:
            errors=[];stop_code=None
            try:
                stop=run([docker,'stop','--time','5',name],capture_output=True,timeout=15)
                stop_code=stop.returncode
            except (OSError,subprocess.TimeoutExpired) as error:
                errors.append(error_code(error))
            def exit_verified():
                inspected=run([docker,'inspect','--format','{{.State.Running}}',name],
                    capture_output=True,text=True,timeout=15)
                absent=inspected.returncode!=0 and bool(re.search(r'No such (?:object|container)', str(inspected.stderr),re.I))
                return (inspected.returncode==0 and str(inspected.stdout).strip()=='false') or absent
            try:
                exited=exit_verified()
            except (OSError,subprocess.TimeoutExpired) as error:
                errors.append(error_code(error));exited=False
            if not exited:
                try:
                    run([docker,'kill',name],capture_output=True,timeout=15)
                    exited=exit_verified()
                except (OSError,subprocess.TimeoutExpired) as error:
                    errors.append(error_code(error));exited=False
            record={'exit_code':124,'timed_out':True,'container_exit_verified':exited,
                'stop_exit_code':stop_code,'cleanup_errors':errors,'root_exception_type':'TimeoutExpired',
                'root_error_code':'CONTAINER_DEADLINE_EXCEEDED' if exited else 'CONTAINER_STOP_NOT_CONFIRMED',
                'failure_differences':[{'field':'container.running','expected':summary(False),
                    'actual':summary(False if exited else 'Unknown')}],
                'traceback_path':str(out/'timeout.json')}
            (out/'timeout.json').write_text(json.dumps(record,indent=2)+'\n')
            return record


COMPARISON_TABLES = (
    'projects','chapters','chapter_draft_versions','review_reports','quality_scores',
    'rewrite_tasks','workflow_runs','llm_runs','execution_budgets',
    'execution_operations','execution_attempts','book_production_control',
)


async def comparison_session(connection):
    from sqlalchemy import text
    await connection.execute(text('SET TRANSACTION READ ONLY'))
    database=await connection.scalar(text('SELECT current_database()'))
    require(bool(re.search(r'(?:^|_)test(?:_|$)',database)), 'comparison.test_database', 'explicit test DB', database)
    await connection.execute(text("SET LOCAL TIME ZONE 'UTC'"))
    actual=await connection.scalar(text("SELECT current_setting('TimeZone')"))
    require(actual=='UTC','comparison.timezone','UTC',actual)
    return database


async def table_snapshot(connection, table):
    from sqlalchemy import text
    require(table in COMPARISON_TABLES,'comparison.table_allowlist',COMPARISON_TABLES,table)
    key={'execution_budgets':'scope_id','execution_operations':'key',
         'book_production_control':'project_id'}.get(table,'id')
    # No ID-list parameters: scan server rows and compare maps locally. Every
    # column is included in to_jsonb; arrays and original paid strings untouched.
    rows=dict((str(row[0]),row[1]) for row in await connection.execute(text(
        f"SELECT {key},encode(sha256(convert_to(to_jsonb(t)::text,'UTF8')),'hex') FROM public.{table} t ORDER BY {key}")))
    dates=list(await connection.scalars(text("SELECT column_name FROM information_schema.columns "
        "WHERE table_schema='public' AND table_name=:name AND data_type='timestamp with time zone' ORDER BY ordinal_position"),{'name':table}))
    epochs={}
    if dates:
        fields=','.join("'"+name+"',extract(epoch FROM t.\""+name+"\")::text" for name in dates)
        epochs=dict((str(row[0]),row[1]) for row in await connection.execute(text(
            f"SELECT {key},encode(sha256(convert_to(jsonb_build_object({fields})::text,'UTF8')),'hex') FROM public.{table} t ORDER BY {key}")))
    return {'rows':rows,'timestamp_epochs':epochs,'timestamp_columns':dates}


async def all_snapshots(connection):
    return {table:await table_snapshot(connection,table) for table in COMPARISON_TABLES}


def verify_old_rows(before,after,*,field):
    differences=[]
    for key,digest in before.items():
        if after.get(key)!=digest:
            differences.append({'field':field+'.'+str(key),
                'expected':summary(digest),'actual':summary(after.get(key))})
            if len(differences)>=20:break
    if differences:raise HarnessInvariantError('OLD_ROWS_CHANGED:'+field,differences=differences)


async def budget_rows(connection):
    from sqlalchemy import text
    rows=await connection.execute(text('SELECT scope_id,to_jsonb(t) FROM public.execution_budgets t ORDER BY scope_id'))
    return {row[0]:(json.loads(row[1]) if isinstance(row[1],str) else row[1]) for row in rows}


def environment_exemption(child, previous, *, timed_out):
    # Never excuse timeout/missing RESULT: neither proves an environment root.
    if timed_out or not isinstance(child,dict):return False,None
    location=child.get('root_location') or ''
    kind=child.get('root_exception_type')
    step=child.get('failed_workflow_step') or ''
    index=child.get('script_consumed_index')
    if (child.get('status')!='失败' or type(index) is not int or index!=0
        or kind not in {'NameError','ModuleNotFoundError','ImportError','FileNotFoundError','SyntaxError'}
        or not location.startswith('/audit.py:') or step.startswith('native_caught:')
        or any((row.get('failed_workflow_step') or '').startswith('native_caught:')
               for row in child.get('secondary_failures',[]))):
        return False,None
    key=kind+':/audit.py:before-send'
    used=sum(row.get('environment_exempted') is True and row.get('environment_same_cause_key')==key
             for row in previous)
    return used<2,key
