"""Restore the existing U4 snapshot into our own separate test database only."""
import datetime
import hashlib
import json
from pathlib import Path
import subprocess

from isolation import DOCKER, OWNER, REPORT, preflight, verify

preflight()
plan=json.loads((REPORT/'ISOLATION.json').read_text());verify(plan)
source=Path('/Volumes/MACSSD/owen-home/Documents/workspace/bestseller-v3-private-20261027-g0a/s01-snapshot-v2/database.pgdump')
expected='57e9e958b5274fdf2135c3e6779570ded914ea3a1094b399d5f6e922edd8d956'
assert hashlib.file_digest(source.open('rb'),'sha256').hexdigest()==expected
name='full_flow_s01_20261028_test_db';private=Path(plan['private_root']);evidence=private/'evidence/s01-snapshot-restore';evidence.mkdir(mode=0o700)
row={'schema':'full-flow-s01-restore.v1','status':'进行中','owner':OWNER,'database':name,'source_sha256':expected,'production_access':False,'model_calls':0,'traceback_path':str(evidence/'restore.log')}
path=REPORT/'S01-RESTORE.json';path.write_text(json.dumps(row,indent=2)+'\n')
result=subprocess.run([DOCKER,'exec',plan['pg'],'psql','-U','postgres','-d',plan['database'],'-v','ON_ERROR_STOP=1','-c','CREATE DATABASE '+name],capture_output=True)
assert result.returncode==0,'OWNED_S01_DATABASE_CREATE_FAILED'
with source.open('rb') as data,(evidence/'restore.log').open('wb') as log:
    process=subprocess.Popen([DOCKER,'exec','-i',plan['pg'],'pg_restore','-U','postgres','-d',name,'--no-owner','--no-acl','--exit-on-error'],stdin=data,stdout=log,stderr=subprocess.STDOUT)
    try:code=process.wait(timeout=1800)
    except subprocess.TimeoutExpired:
        process.terminate();process.wait(timeout=30);code=124
row.update(status='通过' if code==0 else '失败',exit_code=code,
           original_snapshot_unchanged=hashlib.file_digest(source.open('rb'),'sha256').hexdigest()==expected,
           at_utc=datetime.datetime.now(datetime.timezone.utc).isoformat())
if code:
    row.update(root_exception_type='PGRestoreError',root_error_code='PG_RESTORE_EXIT_'+str(code),failed_workflow_type='S01 existing snapshot restore',failed_workflow_step='pg_restore',last_successful_llm_template='Unknown: zero calls',script_consumed_index=0,
               failure_differences=[{'field':'pg_restore.exit','expected':{'type':'int','summary':0},'actual':{'type':'int','summary':code}}])
path.write_text(json.dumps(row,indent=2)+'\n');print(json.dumps(row));raise SystemExit(code)
