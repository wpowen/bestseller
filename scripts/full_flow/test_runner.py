"""Versioned local integration tests; never loads a production env file."""
import argparse,datetime,hashlib,json,os,signal,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];PRODUCTION=ROOT.parents[1]
VENV=PRODUCTION/'.venv';REPORT=ROOT/'reports/full-flow-2026-10-28'
sys.path.insert(0,str(ROOT/'tests'))
from support.cp1_rehearsal import RunState,require
import shutil
parser=argparse.ArgumentParser();parser.add_argument('stage');parser.add_argument('modules',nargs='*');parser.add_argument('--selection-file');parser.add_argument('--external-temp-fixtures',action='store_true');parser.add_argument('--timeout-seconds',type=int,default=1800);args=parser.parse_intermixed_args()
at=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ');name=args.stage+'-'+at
run=REPORT/'runs'/name;run.mkdir(parents=True);(run/'tmp').mkdir()
state=RunState(REPORT/(name+'.json'));state.data.update(stage=args.stage,candidate_sha=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),run_root=str(run),production_access=False,model_calls=0,modules=[])
_original_excepthook=sys.excepthook
def persist_startup_error(kind,error,tb):
 state.failure(error.with_traceback(tb),step=state.data.get('current_stage','harness_preflight'))
 state.finish(final_checks_complete=False)
 _original_excepthook(kind,error,tb)
sys.excepthook=persist_startup_error
def source_fingerprints():
 paths=list((ROOT/'src').rglob('*.py'))
 paths += [path for path in (ROOT/'config').rglob('*') if path.is_file()]
 return {str(path.relative_to(ROOT)):hashlib.sha256(path.read_bytes()).hexdigest()
         for path in sorted(set(paths)) if not path.is_symlink()}
state.data['failed_workflow_type']='isolated pytest contract validation'
require(1<=args.timeout_seconds<=7200,'TEST_TIMEOUT_RANGE',{'min':1,'max':7200},args.timeout_seconds)
state.data['timeout_seconds']=args.timeout_seconds
state.data['source_sha256_before']=source_fingerprints()
require(shutil.disk_usage('/System/Volumes/Data').free>=5_000_000_000,'STOP_DISK_BELOW_5GB',True,False)
p=subprocess.run(['/Applications/Docker.app/Contents/Resources/bin/docker','version','--format','{{.Server.Version}}'],capture_output=True,text=True,timeout=15)
require(p.returncode==0 and bool(p.stdout.strip()),'STOP_DOCKER_UNRESPONSIVE','responsive',p.returncode)
modules=args.modules if not args.selection_file else Path(args.selection_file).read_text().splitlines()
for module in modules:require((ROOT/module.split('::')[0]).exists(),'TEST_MODULE_MISSING:'+module,'exists',False)
state.data['modules']=modules

plugin=run/'full_flow_test_evidence.py'
plugin.write_text('''import hashlib,json,os,pytest,socket,re,time
from pathlib import Path
_diffs=[]
def pytest_runtest_logstart(nodeid,location):
 Path(os.environ['FULL_FLOW_PROGRESS']).write_text(json.dumps({'test':nodeid,'phase':'running','at_epoch':time.time()})+'\\n')
def pytest_runtest_logreport(report):
 if report.when=='teardown':
  Path(os.environ['FULL_FLOW_PROGRESS']).write_text(json.dumps({'test':report.nodeid,'phase':'finished','at_epoch':time.time()})+'\\n')
def summ(x):return {'type':type(x).__name__,'length':len(x) if isinstance(x,(str,list,dict,bytes)) else None,'sha256':hashlib.sha256(json.dumps(x,sort_keys=True,default=str).encode()).hexdigest()}
def safe_chain(error):
 rows=[];seen=set()
 while error is not None and id(error) not in seen and len(rows)<12:
  seen.add(id(error));raw=str(getattr(error,'code','') or str(error).split('\\n')[0])
  code=raw if len(raw)<=160 and re.fullmatch(r'[A-Za-z][A-Za-z0-9_:.\\-]*',raw) else type(error).__name__
  rows.append({'exception':type(error).__name__,'code':code,'message':summ(str(error)),
   'differences':getattr(error,'differences',None)})
  error=error.__cause__ or error.__context__
 return rows
def pytest_runtest_setup(item):_diffs.clear()
def pytest_assertrepr_compare(config,op,left,right):
 _diffs.append({'field':'assertion.'+op,'expected':summ(right),'actual':summ(left)})
 return None
@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item,call):
 outcome=yield;report=outcome.get_result()
 if report.failed:
  error=call.excinfo.value
  if callable(getattr(error,'errors',None)):
   for detail in error.errors():
    _diffs.append({'field':'.'.join(map(str,detail.get('loc',()))) or item.nodeid,
     'expected':summ({'constraint':detail.get('type'),'context':detail.get('ctx',{})}),
     'actual':summ(detail.get('input'))})
  chain=safe_chain(error);root=next((x for x in reversed(chain) if x['code']!=x['exception']),chain[-1])
  props=[]
  for key,value in item.user_properties:
   if not any(name in key for name in ('operand','difference')):continue
   try:parsed=json.loads(value)
   except (TypeError,ValueError):continue
   for entry in parsed if isinstance(parsed,list) else [parsed]:
    if isinstance(entry,dict) and isinstance(entry.get('field'),str) and all(isinstance(entry.get(side),dict) and set(entry[side]) <= {'type','length','len','sha256'} and 'type' in entry[side] for side in ('expected','actual')):props.append(entry)
  row={'test':item.nodeid,'step':report.when,'exception':root['exception'],'code':root['code'],'exception_chain':chain,'differences':getattr(error,'differences',None) or next((x['differences'] for x in chain if x['differences']),None) or props or list(_diffs) or [{'field':item.nodeid,'expected':summ('PASS'),'actual':summ(type(error).__name__+':'+str(error))}]}
  with Path(os.environ['FULL_FLOW_FAILURES']).open('a') as f:f.write(json.dumps(row,ensure_ascii=False)+'\\n')
@pytest.fixture(autouse=True)
def prohibit_external_network(monkeypatch):
 def deny(*a,**k):raise RuntimeError('FULL_FLOW_LEVEL0_EXTERNAL_NETWORK_DENIED')
 monkeypatch.setattr(socket.socket,'connect',deny);monkeypatch.setattr(socket.socket,'connect_ex',deny)
''')
url='sqlite+aiosqlite:///'+str(run/'full_flow_test.sqlite3');require(Path(url.split('///')[1]).name.endswith('_test.sqlite3'),'TEST_DB_NAME','_test.sqlite3',url)
env={k:v for k,v in os.environ.items() if not k.startswith('BESTSELLER__')}
env.update(P3_SYNTHETIC_BODY_PATH='/Volumes/MACSSD/owen-home/Documents/workspace/bestseller-par-private/int-full-flow-20261028/fixtures/p3-synthetic-chapter-3f0b6c78.md',MINIMAX_API_KEY='full-flow-offline-fixture-not-a-real-key',BESTSELLER__DATABASE__URL=url,PYTHONDONTWRITEBYTECODE='1',PYTHONPATH=os.pathsep.join(map(str,[ROOT/'src',ROOT/'tests',ROOT/'tests/support',ROOT/'tests/unit',ROOT,run])),PYTEST_DISABLE_PLUGIN_AUTOLOAD='1',LITELLM_LOCAL_MODEL_COST_MAP='true',TMPDIR='/private/tmp' if args.external_temp_fixtures else str(run/'tmp'),FULL_FLOW_FAILURES=str(run/'failures.jsonl'),BESTSELLER_LLM_RUNTIME_PROFILE_PATH=str(run/'absent-profile.json'))
env['FULL_FLOW_PROGRESS']=str(run/'progress.json')
retained_rewrite=PRODUCTION.parent/'bestseller-par-private/MAIN-SNAPSHOT-20261029/files/reports/continuous-recovery-2026-10-16/freezes/before-rewrite-candidate-evidence/reviews.py'
if retained_rewrite.is_file():
 require(hashlib.sha256(retained_rewrite.read_bytes()).hexdigest()=='5004d2b7fead1344748ea4241657c54686ae5b09a70797fb95b13bfc2238c776','RETAINED_REWRITE_SNAPSHOT_CHANGED','expected_source_sha','mismatch')
 env['BESTSELLER_TEST_RETAINED_REWRITE_SOURCE']=str(retained_rewrite)
 env['BESTSELLER_TEST_PROTECTED_SNAPSHOT']=str(PRODUCTION.parent/'bestseller-par-private/MAIN-SNAPSHOT-20261029/files')
command=[str(VENV/'bin/pytest'),'-p','pytest_cov.plugin','-p','pytest_asyncio.plugin','-p','full_flow_test_evidence','--no-cov','-q',*modules,'--tb=short','--basetemp='+str(run/'pytest'),'--junitxml='+str(run/'junit.xml')]
state.checkpoint('pytest_running',selected_database=Path(url.split('///')[1]).name)
with (run/'pytest.log').open('w') as log:
 process=subprocess.Popen(command,cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
 try:code=process.wait(timeout=args.timeout_seconds)
 except subprocess.TimeoutExpired:
  os.killpg(process.pid,signal.SIGTERM)
  try:process.wait(timeout=5)
  except subprocess.TimeoutExpired:os.killpg(process.pid,signal.SIGKILL);process.wait()
  code=124
state.data['source_sha256_after']=source_fingerprints()
state.data['source_changed_during_tests']=state.data['source_sha256_before']!=state.data['source_sha256_after']
if (run/'progress.json').exists():state.data['last_test_progress']=json.loads((run/'progress.json').read_text())
failure_file=run/'failures.jsonl';failures=[json.loads(x) for x in failure_file.read_text().splitlines()] if failure_file.exists() else []
state.data['failures']=failures;state.data['pytest_exit_code']=code;state.data['exit_code']=code;state.data['traceback_path']=str(run/'pytest.log')
if code == 0 and state.data['source_changed_during_tests']:
 try:
  require(False,'TEST_CANDIDATE_CHANGED_DURING_VALIDATION',state.data['source_sha256_before'],state.data['source_sha256_after'])
 except Exception as error:
  state.failure(error,step='final_candidate_fingerprint')
 code=1;state.data['exit_code']=code
if code:
 if failures:
  first=failures[0];state.data.update(root_exception_type=first['exception'],root_error_code=first['code'] or 'PYTEST_ASSERTION_FAILED',failure_differences=first['differences'],failed_workflow_step=first['test'],status='失败',final_checks_complete=False);state.failed=True
 elif code==124:
  try:require(False,'PYTEST_RUNNER_TIMEOUT_SECONDS',{'complete_within_seconds':args.timeout_seconds},{'timeout_seconds':args.timeout_seconds,'last_test':state.data.get('last_test_progress','Unknown')})
  except Exception as error:state.failure(error,step='pytest_running')
 elif not state.failed:
  state.failure(RuntimeError('PYTEST_COLLECTION_OR_TIMEOUT:'+str(code)),step='pytest_collection_or_timeout')
else:state.checkpoint('pytest_passed',candidate_success=True)
state.finish(final_checks_complete=code==0)
print(json.dumps({'receipt':str(state.path),'exit_code':code},ensure_ascii=False));print((run/'pytest.log').read_text()[-4500:]);raise SystemExit(code)
