"""New-batch readonly comparison regression: original S01 and old failed clone."""
import asyncio,json,os,socket,sys
from pathlib import Path
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from support.cp1_rehearsal import RunState,require,comparison_session,all_snapshots,budget_rows,verify_budgets,verify_old_rows
from support.database_safety import selected_database_url,install_database_safety
STATE=RunState('/run/RESULT.json');HOST='bestseller-v3-g0-pg'
_original_excepthook=sys.excepthook
def persist_uncaught(kind,error,tb):
    STATE.failure(error.with_traceback(tb),step='uncaught_entrypoint')
    STATE.finish()
    _original_excepthook(kind,error,tb)
sys.excepthook=persist_uncaught

ips={row[4][0] for row in socket.getaddrinfo(HOST,None,socket.AF_INET)}
def network_guard(event,args):
 if event=='socket.getaddrinfo' and args[0] not in {HOST,*ips}:raise RuntimeError('local_check_external_dns_denied')
 if event=='socket.connect' and (not isinstance(args[1],tuple) or args[1][0] not in ips or args[1][1]!=5432):raise RuntimeError('local_check_external_connection_denied')
sys.addaudithook(network_guard)
async def main():
 source_url=selected_database_url();require(source_url.database=='bestseller_v3_s01_red_test','source_test_db','bestseller_v3_s01_red_test',source_url.database)
 cleanup=install_database_safety();engines=[];source_connection=None;clone_connection=None
 STATE.data.update(failed_workflow_type='CP1_batch2_local_comparison',production_access=False,model_calls=0,database_writes=0)
 try:
  prior=json.loads(Path('/run/old-failure-readback.json').read_text())
  clone_name=prior['failed_clone']
  require(clone_name=='bestseller_v3_cp1_38208396fe3e457182bcf2947f4ba511_test','recorded_failed_clone',True,clone_name=='bestseller_v3_cp1_38208396fe3e457182bcf2947f4ba511_test')
  source=create_async_engine(source_url);engines.append(source);source_connection=await source.connect()
  source_timezone=await source_connection.scalar(text("SELECT current_setting('TimeZone')"));await source_connection.rollback()
  await comparison_session(source_connection);before=await all_snapshots(source_connection);source_budgets=await budget_rows(source_connection)
  scope=await source_connection.scalar(text("SELECT metadata->>'execution_scope_id' FROM projects WHERE id=:id"),{'id':'d407ca85-9a9b-4564-a033-6bc529e0dd76'})
  STATE.checkpoint('readonly_source_baseline',source_timezone_before=source_timezone,comparison_timezone='UTC')
  os.environ['BESTSELLER__DATABASE__URL']=source_url.set(database=clone_name).render_as_string(hide_password=False)
  clone=create_async_engine(os.environ['BESTSELLER__DATABASE__URL']);engines.append(clone);clone_connection=await clone.connect()
  clone_timezone=await clone_connection.scalar(text("SELECT current_setting('TimeZone')"));await clone_connection.rollback()
  await comparison_session(clone_connection);actual=await all_snapshots(clone_connection);clone_budgets=await budget_rows(clone_connection)
  for table in ('llm_runs','execution_operations','execution_attempts','review_reports','rewrite_tasks','workflow_runs','book_production_control'):
   verify_old_rows(before[table]['rows'],actual[table]['rows'],field=table)
   verify_old_rows(before[table]['timestamp_epochs'],actual[table]['timestamp_epochs'],field=table+'.epochs')
  verify_budgets(source_budgets,clone_budgets,target=scope,calls_delta=2,tokens_delta=400)
  STATE.checkpoint('old_failed_clone_history_verified',candidate_success=True,old_full_rows_and_timestamp_epochs_unchanged=True,
   complete_budgets_preserved=True,source_timezone_before=source_timezone,clone_timezone_before=clone_timezone,
   total_old_paid_runs_checked=len(before['llm_runs']['rows']),comparison_timezone='UTC',fields_removed=0,paid_raw_modified=False)
 except BaseException as error:STATE.failure(error,step=STATE.data['failed_workflow_step'])
 finally:
  if clone_connection is not None:
   try:await clone_connection.close()
   except BaseException as error:STATE.failure(error,step='close_clone')
  os.environ['BESTSELLER__DATABASE__URL']=source_url.render_as_string(hide_password=False)
  if source_connection is not None:
   try:
    after=await all_snapshots(source_connection)
    require(before==after,'source_unchanged',before,after)
    STATE.checkpoint('original_source_unchanged',source_unchanged=True)
   except BaseException as error:STATE.failure(error,step='final_source_check')
   finally:await source_connection.close()
  for engine in engines:await engine.dispose()
  cleanup();STATE.finish(final_checks_complete=bool(STATE.data.get('candidate_success') and STATE.data.get('source_unchanged')))
 raise SystemExit(0 if STATE.data['status']=='通过' else 1)
asyncio.run(main())
