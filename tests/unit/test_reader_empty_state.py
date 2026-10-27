"""Run the real reader JS offline; no server, database or provider is started."""

import json
import os
from pathlib import Path
import shutil
import socket
import subprocess

import pytest
from sqlalchemy.engine import Engine

HTML = Path(__file__).resolve().parents[2] / "src/bestseller/web/novel_reader.html"
pytestmark = pytest.mark.unit

SCRIPT = r"""
const fs = require('fs'), vm = require('vm');
const source = fs.readFileSync(process.argv[1], 'utf8');
const script = source.match(/<script>([\s\S]*?)<\/script>/)[1];
// Run the unchanged functions once, without the final automatic init call.
const code = script.slice(0, script.lastIndexOf('\n  init();'));
const spec = JSON.parse(process.argv[2]), elements = new Map(), requests = [];
const element = id => {
  if (!elements.has(id)) elements.set(id, {
    innerHTML:'', textContent:'', dataset:{}, classList:{toggle(){}},
    addEventListener(){}, querySelector(){return null}, scrollTo(){},
  });
  return elements.get(id);
};
const context = {
  location:{pathname:'/read/offline-fixture', hash:spec.hash || ''},
  window:{scrollTo(){}}, localStorage:{getItem(){return null}},
  document:{getElementById:element, body:{dataset:{}}, title:'小说阅读器',
    addEventListener(){}, querySelectorAll(){return []}},
  fetch:async (url,options) => {
    if (options?.method && options.method !== 'GET') throw new Error('non-GET tripwire');
    requests.push(url);
    const toc = url.endsWith('/toc');
    if (!toc && !/\/chapter\/\d+$/.test(url)) throw new Error('fallback route tripwire');
    const status = (toc ? spec.toc_status : spec.body_status) || 200;
    const value = toc ? spec.toc_data :
      ('body_data' in spec ? spec.body_data : {error:'Not found.'});
    return {ok:status>=200 && status<300, status,
      json:async()=>value, text:async()=>JSON.stringify(value)};
  },
};
vm.createContext(context);
vm.runInContext(code,context,{timeout:1000});
(async()=>{
  await vm.runInContext('init()',context,{timeout:1000});
  if (spec.direct_chapter) await vm.runInContext(`loadChapter(${spec.direct_chapter})`,context);
  process.stdout.write(JSON.stringify({requests, title:element('book-title').textContent,
    project_exists:vm.runInContext('readerProjectExists',context),
    page_title:context.document.title, info:element('chapter-info').textContent,
    area:element('reading-area').innerHTML, toc:element('toc-list').innerHTML}));
})().catch(e=>{console.error(e);process.exit(1)});
"""


class ExternalAccessForbidden(BaseException):
    pass


@pytest.fixture(autouse=True)
def no_external_access(monkeypatch):
    def forbidden(*args, **kwargs):
        raise ExternalAccessForbidden("Reader UI tests forbid DB/network/provider access")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(Engine, "connect", forbidden)


def _run(spec):
    node = shutil.which("node")
    assert node, "Node is required to execute actual reader JavaScript"
    result = subprocess.run(
        [node, "-e", SCRIPT, str(HTML), json.dumps(spec)],
        check=True,
        capture_output=True,
        text=True,
        timeout=15,
    )
    return json.loads(result.stdout)


def _toc(chapters=None, **extra):
    return {
        "project_exists": True,
        "title": "离线阅读样例",
        "chapters": chapters or [],
        "volumes": [],
        **extra,
    }


def _chapter(**extra):
    return {
        "number": 1,
        "title": "离线章题",
        "reader_state": "adopted",
        "availability": "available",
        **extra,
    }


@pytest.mark.parametrize("hash_value", ["", "#4"])
def test_known_empty_catalog_never_requests_a_chapter_even_with_stale_hash(hash_value):
    result = _run({"toc_data": _toc(), "hash": hash_value})
    assert result["requests"] == ["/api/reader/offline-fixture/toc"]
    assert result["title"] == "离线阅读样例"
    assert "暂无章节" in result["toc"]
    assert "暂无可阅读的正式正文" in result["area"]
    assert "Not found" not in result["area"]
    assert "copy-body-btn" not in result["area"]
    assert "chapter-body" not in result["area"]


@pytest.mark.parametrize("indicator", [False, None, "true", 1, {}, []])
def test_only_exact_native_true_can_enter_empty_success(indicator):
    result = _run({"toc_data": _toc(project_exists=indicator)})
    assert len(result["requests"]) == 1
    assert "无法加载" in result["area"]
    assert "暂无可阅读的正式正文" not in result["area"]


def test_missing_project_indicator_is_an_error_even_with_a_title():
    data = _toc()
    del data["project_exists"]
    result = _run({"toc_data": data})
    assert len(result["requests"]) == 1
    assert "无法加载" in result["area"]


@pytest.mark.parametrize(
    "data",
    [
        None,
        [],
        {"project_exists": True},
        _toc(chapters="bad"),
        _toc(chapters=[None]),
        _toc(chapters=[{"number": "1"}]),
    ],
)
def test_malformed_catalog_cannot_be_reported_as_empty_success(data):
    result = _run({"toc_data": data})
    assert len(result["requests"]) == 1
    assert "无法加载" in result["area"]
    assert "暂无可阅读的正式正文" not in result["area"]


@pytest.mark.parametrize("status", [404, 500])
def test_catalog_http_errors_do_not_request_body_or_claim_planning(status):
    result = _run({"toc_status": status, "toc_data": {"error": "catalog unavailable"}})
    assert len(result["requests"]) == 1
    assert "无法加载" in result["area"]
    assert "暂无可阅读的正式正文" not in result["area"]


def test_known_listed_chapter_404_is_unavailable_and_preserves_catalog():
    result = _run({"toc_data": _toc([_chapter()]), "body_status": 404})
    assert len(result["requests"]) == 2
    assert "本章正文暂不可用" in result["area"]
    assert "离线章题" in result["toc"]
    assert "Not found" not in result["area"]
    assert "chapter-body" not in result["area"]


@pytest.mark.parametrize("indicator", [False, None, "missing"])
def test_legacy_nonempty_catalog_keeps_body_without_claiming_project_or_adoption(indicator):
    data = _toc([{"number": 1, "title": "旧导出章", "availability": "available"}])
    if indicator == "missing":
        del data["project_exists"]
    else:
        data["project_exists"] = indicator
    result = _run({"toc_data": data, "body_data": {"html": "<p>LEGACY_BODY</p>"}})
    assert len(result["requests"]) == 2
    assert '<div class="chapter-body"><p>LEGACY_BODY</p></div>' in result["area"]
    assert "旧导出章" in result["toc"]
    assert result["project_exists"] is False
    assert "reader-status-pill formal" in result["area"]
    assert "reader-status-pill adopted" not in result["area"]
    assert "本章已采用" not in result["area"]


@pytest.mark.parametrize("indicator", [False, None, "missing"])
def test_legacy_nonempty_catalog_404_remains_an_error_without_another_source(indicator):
    data = _toc([{"number": 1, "title": "旧导出章", "availability": "available"}])
    if indicator == "missing":
        del data["project_exists"]
    else:
        data["project_exists"] = indicator
    result = _run({"toc_data": data, "body_status": 404})
    assert len(result["requests"]) == 2
    assert "无法加载" in result["area"]
    assert "HTTP 404" in result["area"]
    assert "本章正文暂不可用" not in result["area"]
    assert "旧导出章" in result["toc"]
    assert "chapter-body" not in result["area"]


def test_body_500_is_an_escaped_error_not_normal_unavailability():
    message = '<img src=x onerror="globalThis.readerInjected=1">'
    result = _run(
        {"toc_data": _toc([_chapter()]), "body_status": 500, "body_data": {"error": message}}
    )
    assert len(result["requests"]) == 2
    assert "HTTP 500" in result["area"]
    assert "无法加载" in result["area"]
    assert "本章正文暂不可用" not in result["area"]
    assert "<img" not in result["area"]
    assert "&lt;img" in result["area"]
    assert "离线章题" in result["toc"]


@pytest.mark.parametrize("payload", [None, {}, {"html": None}, {"html": ""}])
def test_missing_body_in_success_response_is_an_error_not_fake_prose(payload):
    result = _run({"toc_data": _toc([_chapter()]), "body_data": payload})
    assert "无法加载" in result["area"]
    assert "chapter-body" not in result["area"]


@pytest.mark.parametrize(
    "state,notice",
    [
        ("adopted", ""),
        ("accepted_with_debt", "下面显示已采用的正文，质量债仍保留"),  # noqa: RUF001
        ("blocked", "下面显示未采用的候选草稿"),
    ],
)
def test_existing_approved_and_candidate_body_and_notices_are_preserved(state, notice):
    result = _run(
        {
            "toc_data": _toc([_chapter(reader_state=state)]),
            "body_data": {"html": "<p>OFFLINE_BODY_SENTINEL</p>"},
        }
    )
    assert len(result["requests"]) == 2
    assert '<div class="chapter-body"><p>OFFLINE_BODY_SENTINEL</p></div>' in result["area"]
    assert notice in result["area"]
    assert f"reader-status-pill {state}" in result["area"]
    if state == "adopted":
        assert "未采用的候选草稿" not in result["area"]
    assert "copy-body-btn" in result["area"]
    assert "暂无可阅读" not in result["area"]


def test_fanqie_single_piece_body_remains_single_piece():
    result = _run(
        {
            "toc_data": _toc([_chapter(single_piece=True)]),
            "body_data": {"html": "<p>OFFLINE_SINGLE_PIECE</p>", "single_piece": True},
        }
    )
    assert "OFFLINE_SINGLE_PIECE" in result["area"]
    assert "单篇全文" in result["area"]
    assert 'id="prev-tb-btn"' not in result["area"]
    assert 'id="next-tb-btn"' not in result["area"]


def test_direct_unknown_chapter_cannot_trigger_an_extra_fetch():
    result = _run({"toc_data": _toc(), "direct_chapter": 8})
    assert len(result["requests"]) == 1
    assert "无法加载" in result["area"]


def test_unknown_hash_does_not_silently_select_another_catalog_body():
    result = _run({"toc_data": _toc([_chapter(reader_state="blocked")]), "hash": "#8"})
    assert result["requests"] == ["/api/reader/offline-fixture/toc"]
    assert "无法加载" in result["area"]
    assert "chapter-body" not in result["area"]


def test_empty_and_error_states_in_actual_isolated_chromium_dom():
    """Optional test runtime, no new project dependency; no live browser is used."""
    node = shutil.which("node")
    assert node
    check = subprocess.run([node, "-e", "require('playwright')"], capture_output=True)
    if check.returncode:
        pytest.skip("Set NODE_PATH to an existing Playwright runtime for real DOM verification")
    script = r"""
const fs=require('fs'), {chromium}=require('playwright');
const source=fs.readFileSync(process.argv[1],'utf8');
const script=source.match(/<script>([\s\S]*?)<\/script>/)[1];
const code=script.slice(0,script.lastIndexOf('\n  init();'));
(async()=>{
 const browser=await chromium.launch({headless:true});
 try {
  const results=[];
  for (const stage of ['empty','missing','toc','body']) {
   const context=await browser.newContext();
   let actualNetwork=0;
   await context.route('**/*',route=>{actualNetwork++;return route.abort()});
   const page=await context.newPage();
   await page.setContent(source.replace(/<script>[\s\S]*?<\/script>/,''));
   await page.evaluate(stage=>{
    if(stage==='empty')location.hash='#4';
    window.fixtureGets=[];
    window.fetch=async url=>{
     window.fixtureGets.push(url);
     const isToc=url.endsWith('/toc');
     const fail=stage==='toc'||!isToc;
     const attack='<img id="reader-injected" src=x onerror="globalThis.readerInjected=1">';
     const payload=fail?{error:attack}:{project_exists:true,title:'离线 DOM 样例',
      chapters:stage==='empty'?[]:[{number:1,title:'离线 DOM 章'}],volumes:[]};
     return new Response(JSON.stringify(payload),{status:fail?(stage==='missing'?404:500):200});
    };
   },stage);
   await page.addScriptTag({content:code});
   await page.evaluate(()=>init());
   results.push(await page.evaluate(()=>({
    area:document.getElementById('reading-area').textContent,
    sidebar:document.getElementById('toc-list').textContent,
    images:document.querySelectorAll('#reading-area img,#toc-list img').length,
    injected:globalThis.readerInjected===1,requests:window.fixtureGets.length,
    buttons:document.querySelectorAll('#reading-area button').length,
    title:document.getElementById('book-title').textContent,
   })));
   if(stage==='empty' && process.env.BESTSELLER_READER_SCREENSHOT)
    await page.screenshot({path:process.env.BESTSELLER_READER_SCREENSHOT,fullPage:true});
   if(actualNetwork)throw Error('network tripwire fired');
   await context.close();
  }
  process.stdout.write(JSON.stringify(results));
 } finally {await browser.close()}
})().catch(e=>{console.error(e);process.exit(1)});
"""
    result = subprocess.run(
        [node, "-e", script, str(HTML)],
        capture_output=True,
        text=True,
        timeout=45,
        env=os.environ.copy(),
    )
    assert result.returncode == 0, result.stderr
    empty, missing, toc, body = json.loads(result.stdout)
    assert empty["requests"] == 1
    assert "暂无可阅读的正式正文" in empty["area"]
    assert "暂无章节" in empty["sidebar"]
    assert empty["buttons"] == 0
    assert empty["title"] == "离线 DOM 样例"
    assert missing["requests"] == 2
    assert "本章正文暂不可用" in missing["area"]
    assert "Not found" not in missing["area"]
    assert "离线 DOM 章" in missing["sidebar"]
    assert missing["buttons"] == 0
    for actual in (toc, body):
        assert actual["images"] == 0
        assert actual["injected"] is False
        assert "HTTP 500" in actual["area"]
        assert '<img id="reader-injected"' in actual["area"]
    assert toc["requests"] == 1
    assert body["requests"] == 2
    assert "离线 DOM 章" in body["sidebar"]
