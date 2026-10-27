"""Exercise the actual reader revision handler with controlled task responses."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest

HTML = Path(__file__).resolve().parents[2] / 'src/bestseller/web/novel_reader.html'


def _run_handler(tasks):
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js is required for the reader handler test')
    script = r'''
const fs = require('fs');
const vm = require('vm');
const source = fs.readFileSync(process.argv[1], 'utf8');
const code = source.slice(source.indexOf('window.submitChapterRevision ='), source.indexOf('// ── Reader preferences'));
const tasks = JSON.parse(process.argv[2]);
let gets = 0, reloads = 0;
const notes = [];
const area = {querySelector: () => ({}), insertBefore: n => notes.push(n), prepend: n => notes.push(n)};
const button = {innerHTML:'问题', disabled:false, isConnected:true};
const sandbox = {window:{prompt:()=> '目标字数：2800-3500字。兑现核心要求。'}, currentChapter:20,
 slug:'fixture-book', document:{getElementById:()=>area,createElement:()=>({innerHTML:'',textContent:'',className:''})},
 escapeHtml: x=>x, post: async()=>({task:{task_id:'revision-1'}}),
 get:async()=>tasks[Math.min(gets++,tasks.length-1)],
 loadTOC:async()=>{}, loadChapter:async()=>{reloads++},
 setTimeout:fn=>{queueMicrotask(fn);return 0}, alert:x=>notes.push({textContent:x}), console};
vm.createContext(sandbox);
vm.runInContext(code,sandbox);
(async()=>{await sandbox.window.submitChapterRevision('issue',button);await Promise.resolve();
process.stdout.write(JSON.stringify({gets,reloads,disabled:button.disabled,notes}));})().catch(e=>{console.error(e);process.exit(1)});
'''
    result = subprocess.run([node, '-e', script, str(HTML), json.dumps(tasks)], capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


def test_rejected_revision_preserves_reader_and_shows_reason():
    result = _run_handler([{'status':'running'}, {'status':'failed', 'result':{'ok':False,'rejected_reason':'requirements_not_met'}}])
    assert result['gets'] == 2
    assert result['reloads'] == 0
    assert 'requirements_not_met' in json.dumps(result['notes'])
    assert result['disabled'] is False


def test_successful_revision_refreshes_only_after_completion():
    result = _run_handler([{'status':'queued'}, {'status':'running'}, {'status':'completed','result':{'ok':True,'result_version_no':5}}])
    assert result['gets'] == 3
    assert result['reloads'] == 1
    assert result['disabled'] is False


def test_no_change_revision_does_not_claim_new_version():
    result = _run_handler([{'status':'completed','result':{'ok':True,'no_change':True}}])
    assert result['gets'] == 1
    assert result['reloads'] == 0
    assert '新版本已保存' not in json.dumps(result['notes'],ensure_ascii=False)


def test_reader_separates_paused_quality_debt_from_active_manual_revision():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js is required for reader state behavior')
    source = HTML.read_text()
    code = source[source.index('const READER_STATE_LABEL ='):source.index('function formatReaderTimestamp')]
    cases = [
        {'production_intent':'run','production_state':'quality_debt','availability':'repair_in_progress'},
        {'production_intent':'pause','production_state':'repairing','availability':'repair_in_progress'},
        {'production_intent':'stop','production_state':'blocked','availability':'repair_in_progress'},
        {'reader_state':'active_repair','production_intent':'pause'},
        {'production_intent':'pause','production_state':'ok','availability':'available'},
    ]
    script = code + '\nconsole.log(JSON.stringify(' + json.dumps(cases) + '.map(c => { const state=readerStateFor(c); return [state,READER_STATE_LABEL[state]]; })));'
    result = subprocess.run([node,'-e',script],capture_output=True,text=True,check=True)
    assert json.loads(result.stdout) == [['quality_debt','待编辑'],['paused','待编辑'],['stopped','已停止'],['active_repair','正在修复'],['formal','已保存稿']]
