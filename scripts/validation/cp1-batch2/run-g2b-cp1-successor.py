"""User-approved CP1 batch2: local checks precede one SDK run + two reentries."""
import argparse,datetime as dt,hashlib,json,os,shutil,subprocess,sys
from pathlib import Path
ROOT=Path('/Volumes/MACSSD/owen-home/Documents/workspace/bestseller');C=ROOT/'scratch/v5-worktrees/g2b'
P=ROOT.parent/'bestseller-v3-private-20261027-g0a';R=ROOT/'reports/v3-repair-execution-2026-10-27/g2'
D='/Applications/Docker.app/Contents/Resources/bin/docker';IMAGE='sha256:9685577fa4d1a017afabd1d37de48903631c35c1910479702ec2856dc93bb797'
sys.path.insert(0,str(C/'tests'))
from support.cp1_rehearsal import RunState,run_owned_container,require,environment_exemption
parser=argparse.ArgumentParser();parser.add_argument('kind',choices=('local','timeout','sdk'));args=parser.parse_args()
free=shutil.disk_usage('/System/Volumes/Data').free;require(free>=5_000_000_000,'STOP_INTERNAL_DISK_BELOW_5GB','>=5GB',free)
v=subprocess.run([D,'version','--format','{{.Server.Version}}'],capture_output=True,text=True,timeout=15)
require(v.returncode==0 and bool(v.stdout.strip()),'STOP_DOCKER_UNRESPONSIVE','responsive',v.returncode)
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
protected={ROOT/'.git/index':'bee4bc898f975781d4158466fab0162bbb975ab8ede940b3f4b99e54470aa62c',ROOT/'src/bestseller/services/retained_review_input_proof.py':'b4c5b3b2022f9c7d189cc7f21ce9e1d5328a28e63b772d4a61c0ab726711cbd8',C/'src/bestseller/services/review_source_applicability.py':'ecf70d0a7191f26b2d3bab2d2ab78674c4cca9b7863f00613c40aa5990cf4561',C/'tests/unit/test_review_source_applicability.py':'3e478e6aad6c70a65c57ed4ae4e729c57dfec1c323752a87d87445ab775eb088'}
for path,digest in protected.items():require(sha(path)==digest,'PROTECTED_FILE_DRIFT:'+str(path),digest,sha(path))
previous=[json.loads(p.read_text()) for p in sorted(R.glob('cp1-batch2-sdk-*.json'))]
if args.kind=='sdk':
 business=[x for x in previous if not x.get('environment_exempted')]
 require(len(business)<3,'CP1_BATCH2_BUSINESS_LIMIT',3,len(business))
 for old in previous:
  if old['exit_code']:require(old['root_error_code'] not in ('','Unknown') and bool(old.get('failure_differences')),'PREVIOUS_FAILURE_FIELDS_REQUIRED',True,False)
 gate=json.loads((R/'CP1-BATCH2-LOCAL-GATE.json').read_text());require(gate['status']=='通过','LOCAL_BATCH_NOT_PASSED','通过',gate['status'])
 for relative,digest in gate['files'].items():require(sha(ROOT/relative)==digest,'LOCAL_GATE_SOURCE_CHANGED:'+relative,digest,sha(ROOT/relative))
at=dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ');stem='cp1-batch2-'+args.kind+'-'+at
out=P/'g2b-runtime-20261028/audits'/stem;out.mkdir(parents=True,mode=0o700)
state=RunState(R/(stem+'.json'));head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=C,env=os.environ|{'GIT_OPTIONAL_LOCKS':'0'},text=True).strip()
source_before={str(p.relative_to(C)):sha(p) for p in (C/'src/bestseller/services').rglob('*.py')}
tooling_paths=[ROOT/'scratch/run-g2b-cp1-successor.py',ROOT/'scratch/verify-g2b-cp1-successor.py',ROOT/'scratch/verify-g2b-cp1-local.py',C/'tests/support/cp1_rehearsal.py']
tooling_before={str(p.relative_to(ROOT)):sha(p) for p in tooling_paths}
state.data['tooling_hashes_before']=tooling_before
state.data.update(schema='cp1-batch2-host-run.v2',kind=args.kind,at_utc=at,candidate_commit=head,
 private_run_root=str(out),preflight={'internal_free_bytes':free,'docker_server':v.stdout.strip()},
 authorization='2026-10-28 explicit CP1 batch2 repair/local validation/SDK+2reentries;3 business attempts, same env exemption max2',
 production_access=False,external_model_calls=0,business_attempt=1+len([x for x in previous if not x.get('environment_exempted')]) if args.kind=='sdk' else None)
state.checkpoint('container_launch')
name='bestseller-'+stem.lower();inputs=P/'runs/s01-red';command=[D,'run','--rm','--pull=never','--no-healthcheck','--name',name,'--memory','3g','--cpus','2']
try:
 if args.kind=='timeout':
  command+=['--network','none','--entrypoint','python',IMAGE,'-c','import time;time.sleep(60)']
  outcome=run_owned_container(command,name=name,out=out,timeout=1)
  require(outcome.get('timed_out') and outcome.get('container_exit_verified'),'OWNED_TIMEOUT_CLEANUP',True,outcome)
  state.checkpoint('actual_container_timeout_verified',candidate_success=True,outcome=outcome)
 else:
  script=ROOT/('scratch/verify-g2b-cp1-local.py' if args.kind=='local' else 'scratch/verify-g2b-cp1-successor.py')
  pinned=out/'verify.py';pinned.write_bytes(script.read_bytes())
  if args.kind=='local':
   old=json.loads((R/'cp1-failure-readback-rescope-20261028T092419135994Z.json').read_text())['result']
   (out/'old-failure-readback.json').write_text(json.dumps(old,ensure_ascii=False,indent=2)+'\n')
  command+=['--network','bestseller-v3-g2b-internal','--env-file',str(inputs/'application.env'),
   '--env','LITELLM_LOCAL_MODEL_COST_MAP=true','--env','PYTHONDONTWRITEBYTECODE=1',
   '--env','PYTHONPATH=/app/src:/app/tests:/app/tests/support','--env','BESTSELLER_LLM_RUNTIME_PROFILE_PATH=/run/absent-profile.json',
   '--mount','type=bind,src='+str(C)+',dst=/app,readonly',
   '--mount','type=bind,src='+str(pinned)+',dst=/audit.py,readonly',
   '--mount','type=bind,src='+str(out)+',dst=/run',
   '--mount','type=bind,src='+str(inputs/'artifacts')+',dst=/input-artifacts,readonly',
   '--mount','type=bind,src='+str(inputs/'artifacts')+',dst=/app/artifacts,readonly',
   '--mount','type=bind,src='+str(inputs/'output')+',dst=/app/output,readonly',
   '--workdir','/app','--entrypoint','python',IMAGE,'-B','/audit.py']
  outcome=run_owned_container(command,name=name,out=out,timeout=900 if args.kind=='sdk' else 240)
  state.data['outcome']=outcome
  result=out/'RESULT.json'
  if result.exists():
   child=json.loads(result.read_text());state.data['result']=child
   for field in ('failed_workflow_type','failed_workflow_step','root_exception_type','root_error_code','last_successful_llm_template','script_consumed_index','traceback_path','failure_differences','root_location'):
    if field in child:state.data[field]=child[field]
   if str(state.data.get('traceback_path','')).startswith('/run/'):
    state.data['container_traceback_path']=state.data['traceback_path']
    state.data['traceback_path']=str(out/Path(state.data['traceback_path']).relative_to('/run'))
   if child.get('status')=='失败':state.failed=True
  if outcome.get('timed_out'):
   raise __import__('support.cp1_rehearsal',fromlist=['HarnessInvariantError']).HarnessInvariantError(outcome['root_error_code'],differences=outcome['failure_differences'])
  require(result.exists(),'CHILD_RESULT_NOT_WRITTEN',True,result.exists())
  require(outcome['exit_code']==0 and child['status']=='通过','CHILD_VALIDATION_FAILED','exit0+通过',{'exit':outcome['exit_code'],'status':child['status']})
  state.checkpoint('child_checks_passed',candidate_success=True)
except BaseException as error:
 # A child's earlier, precise native failure remains primary.
 if state.data.get('result',{}).get('status')=='失败':state.failed=True
 state.failure(error,step=state.data.get('failed_workflow_step','container_runtime'))
finally:
 tooling_after={str(p.relative_to(ROOT)):sha(p) for p in tooling_paths};state.data['tooling_hashes_after']=tooling_after
 after={name:sha(C/name) for name in source_before};state.data['source_hashes_before']=source_before;state.data['source_hashes_after']=after
 try:
  require(source_before==after,'SOURCE_CHANGED_DURING_RUN',source_before,after)
  require(tooling_before==tooling_after,'TOOLING_CHANGED_DURING_RUN',tooling_before,tooling_after)
  require(all(sha(path)==digest for path,digest in protected.items()),'PROTECTED_FILE_CHANGED',True,False)
 except BaseException as error:state.failure(error,step='host_final_source_check')
 state.data['protected_files_unchanged']=all(sha(path)==digest for path,digest in protected.items())
 state.data['source_unchanged_during_run']=source_before==after
 state.data['environment_exempted'],state.data['environment_same_cause_key']=environment_exemption(
  state.data.get('result'),previous,timed_out=state.data.get('outcome',{}).get('timed_out',False)) if args.kind=='sdk' else (False,None)
 state.data['artifacts']={str(p.relative_to(out)):sha(p) for p in out.rglob('*') if p.is_file() and '/artifacts/' not in str(p) and '/output/' not in str(p)}
 state.data['exit_code']=1 if state.failed else 0
 state.finish(final_checks_complete=bool(state.data.get('candidate_success') and not state.failed))
print(json.dumps({'report':str(state.path),'status':state.data['status'],'root_error_code':state.data['root_error_code'],'exit_code':state.data['exit_code']},ensure_ascii=False))
raise SystemExit(state.data['exit_code'])
