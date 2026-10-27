"""Run real page helpers/handlers in Node; no browser or network mutation."""
import json
from pathlib import Path
import re
import subprocess

import pytest

HTML = Path("src/bestseller/web/novel_quickstart.html").read_text()
pytestmark = pytest.mark.unit


def function(name):
    found = re.search(r"^[ \t]*(?:async )?function " + name + r"\([^\n]*\) \{[\s\S]*?^(?:  |\t  )\}", HTML, re.M)
    assert found, name
    return found.group()


def assignment(name):
    found = re.search(r"^[ \t]*window\." + name + r" = async function[^\n]*\{[\s\S]*?^  \};", HTML, re.M)
    assert found, name
    return found.group()


def run(task, handlers=False, elapsed_only=False):
    script = """
const task=JSON.parse(process.argv[1]);task.task_id='isolated-offline-task';
const allTasks=[task],window={},requests=[],alerts=[];
const isShortCompletedTask=()=>false,isFanqieShortTask=()=>false,isUnverifiedCompletedTask=()=>false;
const repairPhaseOf=()=>'',hasStagePrefix=()=>false,isCompletedBookTask=()=>false,STAGE={};
const alert=value=>alerts.push(value);
const fetch=async()=>{requests.push('unexpected request');throw new Error('offline forbids sending');};
"""
    script += "\n".join(function(name) for name in (
        "isActiveTask", "taskExecutionTerminalCode", "taskExecutionStopPresentation", "isRetryableAttentionTask", "attentionReasonForTask", "classifyBookState", "fmtDur", "elapsedSinceCreated"))
    if handlers:
        script += assignment("resumeTaskById") + assignment("resumeTask")
    script += "\n(async()=>{"
    if handlers:
        script += "await window.resumeTaskById(task.task_id,'unrelated-museum');await window.resumeTask('unrelated-museum',task.task_id);"
    if elapsed_only:
        script += "console.log(JSON.stringify({elapsed:elapsedSinceCreated(task)}));})();"
    else:
        script += "console.log(JSON.stringify({code:taskExecutionTerminalCode(task),retryable:isRetryableAttentionTask(task),reason:attentionReasonForTask(task),state:classifyBookState(task),elapsed:elapsedSinceCreated(task),requests,alerts}));})();"
    result = subprocess.run(["/opt/homebrew/bin/node", "-e", script, json.dumps(task)],
        capture_output=True, text=True)
    assert result.returncode == 0, result.stderr[-2000:]
    return json.loads(result.stdout)


@pytest.mark.parametrize("task", [
    {"status": "failed", "error": "execution_result_unknown"},
    {"status": "failed", "result": {"execution_terminal": {"code": "execution_result_unknown"}}},
    {"status": "failed", "result": {"code": "execution_budget_exhausted"}},
    {"status": "cancelled", "result": {"execution_terminal": "execution_cancelled"}},
])
def test_real_page_blocks_bulk_and_both_direct_retry_handlers_for_terminal_execution(task):
    result = run(task, handlers=True)
    assert not result["retryable"] and result["requests"] == []
    assert len(result["alerts"]) == 2
    assert "重试" not in result["state"]["next"]
    assert result["state"]["phase"] == "blocked"


@pytest.mark.parametrize("code,label", [
    ("execution_deadline_exhausted", "执行时间已到期"),
    ("execution_budget_exhausted", "本次执行限额已到"),
    ("execution_cancelled", "执行已取消"),
    ("execution_ledger_unavailable", "执行记录暂不可用"),
    ("execution_result_unknown", "调用结果待核对"),
])
def test_page_explains_the_actual_stop_instead_of_misreporting_every_stop_as_unknown(code, label):
    result = run({"status": "failed", "result": {"execution_terminal": {"code": code}}}, handlers=True)
    assert result["state"]["label"] == label
    assert result["requests"] == [] and len(result["alerts"]) == 2
    if code != "execution_result_unknown":
        assert "回执待核对" not in result["reason"]
    else:
        assert "回执待核对" in result["reason"]


@pytest.mark.parametrize("task", [
    {"status": "failed", "error": "ordinary parser error"},
    {"status": "failed", "error": "chapter_review_unknown"},
    {"status": "failed", "error": None, "current_stage": "execution_reconciled_manual_resume_required",
     "result": {"last_execution_reconciliation": {"status": "active", "credit_released": False}}},
])
def test_ordinary_failures_and_explicitly_reconciled_tasks_remain_retryable(task):
    result = run(task)
    assert result["code"] is None and result["retryable"]


@pytest.mark.parametrize("status", ["failed", "cancelled", "completed", "incomplete"])
def test_stopped_task_elapsed_is_read_from_its_record_and_never_restarts_at_zero(status):
    result = run({"status": status, "created_at": "2026-10-20T01:00:00Z",
        "updated_at": "2026-10-20T02:00:00Z"}, elapsed_only=True)
    assert result["elapsed"] == "01:00:00"



def test_progress_poll_updates_global_header_and_never_calls_conception_stream_prose():
    script="const allTasks=[{task_id:'same',status:'failed',error:'execution_continuation_preflight_changed'}]; let active=-1;const updateDashboardMetrics=()=>{active=allTasks.filter(isActiveTask).length;};const STAGE={};"
    script+='\n'.join(function(name) for name in ('isActiveTask','mergeTaskProgressSnapshot','describeStageEvent'))
    script+="""
mergeTaskProgressSnapshot({task_id:'same',status:'running',error:null,result:null});
if(active!==1 || allTasks.length!==1 || allTasks[0].error!==null) throw Error('stale header');
const label=describeStageEvent('llm_stream_activity',{template:'logline_gate',chunks:10});
if(!label.includes('正文尚未开始') || label.includes('llm_stream_activity')) throw Error('wrong prose claim');
mergeTaskProgressSnapshot({task_id:'same',status:'failed',error:'real stop'});
if(active!==0 || allTasks.length!==1) throw Error('stopped task still active');
console.log('ok');
"""
    result=subprocess.run(['/opt/homebrew/bin/node','-e',script],capture_output=True,text=True)
    assert result.returncode==0,result.stderr
