"""Local experiment helpers; no production writes or credential persistence."""
from concurrent.futures import ThreadPoolExecutor, as_completed
import fcntl
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import tempfile
import threading
import time

import yaml

from bookengine.client import Client, MODEL, SYSTEM
from bookengine.longutil import loose_object, ask
from bookengine.q1 import body_of, clean, pack, context, voices
from bookengine.q1_plan import prepare, volume, outlines
from bookengine.config import Book
from bookengine.storage import load, save

PRICES={MODEL:(.15,.60),'MiniMax-M3':(.30,1.20)}
ROOT=Path('../engine-books/style-ab2/private').resolve()
LOCK=threading.RLock()
INFLIGHT={}


def cost(calls,skip_pending=False):
    total=0
    for c in calls:
        if c['status']=='pending' and skip_pending:continue
        p=PRICES.get(c.get('requested_model'),(.30,1.20))
        u=c.get('usage') or {}
        if 'prompt_tokens' in u and 'completion_tokens' in u:
            total+=(u['prompt_tokens']*p[0]+u['completion_tokens']*p[1])/1e6
        else:total+=(c.get('input_bytes',0)*p[0]+c.get('output_token_cap',0)*p[1])/1e6
    return total


def total_cost():
    extra=load(ROOT/'UNCERTAIN-COST.json')['upper_usd'] if (ROOT/'UNCERTAIN-COST.json').exists() else 0
    return extra+sum(cost(load(p),str(p.parent) in INFLIGHT) for p in ROOT.rglob('calls.json'))


class PaidClient(Client):
    def __init__(self,folder,env,model=MODEL):
        super().__init__(folder,env,'',180,model=model)
        self.relaxed=True;self.context_limit=10000;self.clip_context=True;self.style_b=True
        if model=='MiniMax-M3':self.thinking={'type':'disabled'}

    def request(self,stage,prompt,cap,effort,attempt):
        p=PRICES[self.model];upper=(len(prompt.encode())*p[0]+cap*p[1])/1e6
        key=str(self.out)
        with LOCK:
            if total_cost()+sum(INFLIGHT.values())+upper>.50:raise RuntimeError('AB2 shared $0.50 budget reached')
            INFLIGHT[key]=upper
        try:return super().request(stage,prompt,cap,effort,attempt)
        finally:
            with LOCK:
                if self.calls:
                    self.calls[-1]['requested_model']=self.model;save(self.history_path,self.calls)
                INFLIGHT.pop(key,None)


def parallel(tasks,fn,jobs=4):
    result=[]
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        futures={pool.submit(fn,t):t for t in tasks}
        for future in as_completed(futures):
            result.append(future.result())
    return result


def polish(text,folder,env):
    from precision_rules import locate,apply,stats
    final=folder/'final.md'
    targets=locate(text)
    existing=load(folder/'edits.json') if (folder/'edits.json').exists() else []
    wanted={t['id'] for t in targets}
    good=[e for e in existing if e['id'] in wanted and not locate(e['text'])]
    pending=[t for t in targets if t['id'] not in {e['id'] for e in good}]
    if final.exists() and not pending:
        updated,accepted=apply(text,good);save(final,updated);save(folder/'edits.json',accepted);return updated,accepted
    if not targets:save(final,text);save(folder/'edits.json',[]);return text,[]
    client=PaidClient(folder,env)
    instruction=('只改这些命中的句子，每项id/text。把总结金句、顿悟解说、诗化比喻改成动作、对白或当下具体感受。'
        '保留人物、事实、信息与当下情绪，不加新情节/线索，不做比喻，不说原则，不用“不是…而是…”或“意识到/明白/知道”收束。'
        '条件、假设、未完成和否定不得写成已发生，不改变说话人。'
        'ending=true的末句必须停在正在发生的动作或对白，不升华总结。每项不换段、不含换行；只交付替换原句，不补写上下文或整段。')
    try:
        value=ask(client,'precision-polish-r2' if final.exists() else 'precision-polish',instruction,
            [('命中原句',pack([dict(id=t['id'],text=t['text'],categories=t['categories'],ending=t['ending']) for t in pending]))],
            '{"edits":[{"id":1,"text":"替换原句"}]}',cap=4500,input_limit=10000,max_attempts=1)
    except RuntimeError as exc:
        if 'budget' in str(exc):raise
        save(folder/'error.json',dict(reason=str(exc)));value={}
    edits=good+(value.get('edits',[]) if isinstance(value,dict) else [])
    updated,accepted=apply(text,edits);save(final,updated);save(folder/'edits.json',accepted)
    return updated,accepted


def design(source,number,env,assets):
    dest=ROOT/'contexts'/f'{source.name}-{number}';saved=dest/'context.json'
    if saved.exists():return load(saved)
    meta=load(source/'run.json');book=Book(**meta['book']);client=PaidClient(dest,env)
    bible,threads,profile=prepare(client,book,source,assets)
    # Existing sidecar cards are read without creating anything in the book.
    if not (source/'voice_cards.yaml').exists():raise RuntimeError('Missing existing voice cards')
    bible=voices(client,bible,source,yaml.safe_load(Path('engine/data/q1_style.yaml').read_text()))
    rows=[load(source/f'chapters/{n:03d}.json') for n in range(1,number)]
    history=[load(p) for p in sorted((source/'volumes').glob('*.json')) if load(p).get('end',0)<number]
    state=rows[-1]['state'];vn=(number-1)//25+1
    client.prefix=pack(dict(reader_expectation=bible.get('reader_expectation'),anti_patterns=profile.get('anti_patterns',[])))[:650]
    feedback=[*history[-1].get('feedback',[]),*history[-1].get('volume_issues',[]),*rows[-1].get('issues',[])] if history else []
    vol=volume(client,bible,threads,vn,book.chapters,state,history,feedback)
    chapter=outlines(client,bible,threads,profile,vol,number,number,state,rows[-5:],history,[])[0]
    previous=(source/f'chapter-{number-1:03d}.body.md').read_text()
    sections=context(bible,threads,vol,chapter,state,history,rows,previous)
    data=yaml.safe_load(Path('engine/data/q1_style.yaml').read_text())
    instruction=data['b']+f'\n目标{book.words_per_chapter}汉字，合格1870—2530，理想2100—2250，标点不算。'
    instruction+='\n每场戏连续推进，不写章名、编号、创作说明。返回paragraphs数组，一项一段，段内多句自然连贯。'
    instruction+='\n本题材禁区：'+pack(profile.get('forbidden_zones',[]))[:450]
    prefix=pack(dict(reader_expectation=bible.get('reader_expectation'),anti_patterns=profile.get('anti_patterns',[])))[:650]
    value=dict(source=str(source.resolve()),chapter=number,outline=chapter,sections=sections,instruction=instruction,prefix=prefix,
        minimal_people=[{k:v for k,v in c.items() if k not in ('taboos','habits')} for c in json.loads(dict(sections)['出场人物档案、口吻卡与当前状态'])['characters']],previous=previous[-900:])
    save(saved,value);return value


def draft(data,version,folder,env,model=MODEL):
    final=folder/'final.md'
    if final.exists() and final.read_text().strip():return final.read_text()
    client=PaidClient(folder,env,model)
    if model=='MiniMax-M3':client.timeout=300
    if version=='B':
        instruction='写本章正文，目标约2200汉字（2000—2500），完整写完。直接输出正文，不带书名、章名、提纲或字数说明。'
        sections=[('本章大纲',pack(data['outline'])),('人物',pack(data['minimal_people'])),('上章结尾',data['previous'])]
        raw=ask(client,'writer',instruction,sections,cap=7500,max_attempts=1,input_limit=10000)
        text=clean(raw) if isinstance(raw,str) else body_of(raw,client)
    else:
        client.prefix=data['prefix']
        value=ask(client,'writer-direct' if model=='MiniMax-M3' else 'writer',data['instruction'],data['sections'],'{"paragraphs":["正文一段"]}',cap=7500,input_limit=10000,max_attempts=1)
        text=body_of(value,client)
    save(final,text);return text


def opencode_draft(data,folder):
    final=folder/'final.md'
    if final.exists():return final.read_text(),load(folder/'result.json')
    if (folder/'result.json').exists() and load(folder/'result.json').get('status')=='skipped':return '',load(folder/'result.json')
    from bookengine.longmemory import long_context
    instruction=data['instruction'];schema='{"paragraphs":["正文一段"]}'
    overhead=len(data['prefix'])+len(SYSTEM)+len(instruction)+len(schema)+350
    prompt=data['prefix']+'\n\n'+instruction+'\n'+long_context(data['sections'],10000-overhead)+'\n只返回完整JSON，结构：\n'+schema
    model='opencode/deepseek-v4.1-flash-free';work=Path(tempfile.mkdtemp(prefix='bookengine-ab2-'))
    start=time.monotonic()
    try:
        process=subprocess.Popen(['/usr/bin/env','OPENCODE_PERMISSION={"*":"deny"}','/opt/homebrew/bin/opencode','run','--pure','--format','json','--model',model,'--dir',str(work),'--title','BookEngine blind sample',prompt],
            cwd=work,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,start_new_session=True)
        output,error=process.communicate(timeout=300)
        run=subprocess.CompletedProcess(process.args,process.returncode,output,error)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid,signal.SIGTERM)
        try:process.communicate(timeout=5)
        except subprocess.TimeoutExpired:os.killpg(process.pid,signal.SIGKILL);process.communicate()
        result=dict(status='skipped',reason='OpenCode non-interactive request timed out after 300 seconds',model=model);save(folder/'result.json',result);return '',result
    events=[]
    for line in run.stdout.splitlines():
        try:events.append(json.loads(line))
        except ValueError:pass
    chunks=[e.get('part',{}).get('text','') for e in events if e.get('type')=='text']
    text='\n'.join(chunks).strip();raw=text
    try:text=body_of(loose_object(text),type('Probe',(),{'calls':[]})())
    except (ValueError,TypeError,AttributeError):text=clean(text)
    finish=[e.get('part',{}) for e in events if e.get('type')=='step_finish']
    reported_cost=sum(p.get('cost',0) or 0 for p in finish) if finish else None
    result=dict(status='ok' if text else 'skipped',model=model,exit_code=run.returncode,seconds=round(time.monotonic()-start,3),reported_cost=reported_cost,
        reason='' if text else 'OpenCode returned no text; '+('CLI error' if run.returncode else 'no text event'),tokens=[p.get('tokens',{}) for p in finish])
    if not text:
        errors=[str(e.get('error',{}).get('name','')) for e in events if isinstance(e.get('error'),dict)]
        result['error_names']=[e for e in errors if e]
    save(folder/'result.json',result)
    if text:save(final,text);save(folder/'raw-text.md',raw)
    return text,result
