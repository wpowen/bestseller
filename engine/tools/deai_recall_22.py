"""INBOX22 real-source calibration, bounded replay and anonymous read-through pack."""
from concurrent.futures import ThreadPoolExecutor,as_completed
from collections import Counter
import json
import os
from pathlib import Path
import random
import signal
import subprocess
import sys
import time

ENGINE=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ENGINE))
from deai.client import MiniMax,STOP
from deai.flow import Session
from deai.rules import detect
from deai.text import count,sentences
from deai.io import read_json,save
ROOT=ENGINE.parent;BOOKS=ROOT/'books';OUT=BOOKS/'deai-recall-20261104';PRIVATE=OUT/'private';ENV=ROOT/'.env'
SUSPECT=BOOKS/'webui/e9b3b44e-c77f-453d-a7bb-fe4f0f2d4af4';PUMPKIN=BOOKS/'webui/dc4a5e0e-4451-4eae-a269-3b507c75c324'
OLD=BOOKS/'blind-5/private/calibration-r2'

def cost(row):
    rates=(.15,.6) if 'Flash' in row.get('model','MiniMax-M3.1-Flash') else (.3,1.2);u=row.get('usage') or {}
    return ((u.get('prompt_tokens') if u.get('prompt_tokens') is not None else row['input_bytes'])*rates[0]+(u.get('completion_tokens') if u.get('completion_tokens') is not None else row['max_tokens'])*rates[1])/1e6

def ledgers(root):return {str(p):read_json(p,[]) for p in root.rglob('ledger.json') if p.parent.name=='http'}
def spending(root):return sum(cost(r) for rows in ledgers(root).values() for r in rows)

def prepare():
    PRIVATE.mkdir(parents=True,exist_ok=True);path=PRIVATE/'run.json'
    if path.exists():return read_json(path)
    roots={'calibration':PRIVATE/'calibration','suspect':SUSPECT/'final','pumpkin':PUMPKIN/'final'}
    d=dict(budget_usd=1,allocations=dict(calibration=.20,suspect=.50,pumpkin=.25),
           baseline={name:dict(cost=spending(root),rows={p:len(rows) for p,rows in ledgers(root).items()}) for name,root in roots.items()},roots={k:str(v) for k,v in roots.items()})
    for name,root in [('suspect',SUSPECT),('pumpkin',PUMPKIN)]:
        for p in (root/'final').glob('chapter-*'):
            if p.is_file():save(PRIVATE/'previous'/name,p.name,p.read_text())
    save(PRIVATE,'run.json',d);return d

def total_new(d):return sum(max(0,spending(Path(d['roots'][k]))-v['cost']) for k,v in d['baseline'].items())

def calibrate():
    d=prepare();manifest=read_json(OLD/'manifest.json');results={};budget=d['baseline']['calibration']['cost']+d['allocations']['calibration']
    def run(spec):
        path=Path(spec['source']);text=path.read_text();snapshot=(OLD/spec['id']/'source.md').read_text()
        if snapshot not in text:raise ValueError('Original calibration source changed: '+spec['id'])
        text=text[text.index(snapshot):text.index(snapshot)+len(snapshot)]  # Only headings/source labels are excluded.
        cards=read_json(OLD/spec['id']/'cards.json',{});ctx=cards if isinstance(cards,dict) else dict(characters=cards)
        client=MiniMax(PRIVATE/'calibration'/spec['id'],ENV,budget=budget,budget_root=PRIVATE/'calibration')
        tick=time.monotonic();hits,unknown=Session(client,ctx).classify(text,'recall22-calibration')
        return spec['id'],dict(role=spec['role'],chars=count(text),candidates=len({h['sid'] for h in hits}),density=1000*len({h['sid'] for h in hits})/max(1,count(text)),hits=hits,unknown=unknown,seconds=time.monotonic()-tick)
    with ThreadPoolExecutor(max_workers=2) as pool:
        for future in as_completed([pool.submit(run,s) for s in manifest['sources']]):
            key,result=future.result();results[key]=result;save(PRIVATE/'calibration',key+'.json',result)
            print(result['role'],result['candidates'],'unknown',len(result['unknown']),flush=True)
    gold=[]
    for item in manifest['heldout']:
        gold.append(dict(type=item['type'],seed=item['seed'],hit=any(any(h['sid']==m['sid'] and h['type']==item['type'] for h in results[m['id']]['hits']) for m in item['matches'])))
    named=['声音不高，却压住了满廊雨声','话音落下','走廊霎时静了','冷声打断','脸色一变','笑意淡了一瞬','指腹缓缓擦过']
    all_text='\n\n'.join(p.read_text() for p in sorted(SUSPECT.glob('chapter-*.body.md')))
    must={phrase:any(phrase in h['text'] for h in detect(all_text)) for phrase in named}
    anchor=next(r for r in results.values() if r['role']=='claude');model=[r for r in results.values() if r['role'].startswith('blind-')]
    # Zero anchor candidates means a nonzero model density is strictly greater; never divide by zero.
    ratios=[r['density']>=3*anchor['density'] and r['candidates']>0 for r in model]
    whole=[dict(chapter=int(p.stem.split('-')[1].split('.')[0]),chars=count(p.read_text()),candidates=len({h['sid'] for h in detect(p.read_text())})) for p in sorted(SUSPECT.glob('chapter-*.body.md'))]
    gpt=next(r for r in results.values() if r['role']=='gpt');gemini=next(r for r in results.values() if r['role']=='gemini')
    passed=all(must.values()) and sum(g['hit'] for g in gold)/len(gold)>=.8 and anchor['candidates']<=3 and all(ratios) and anchor['density']<=gpt['density'] and anchor['density']<gemini['density'] and not any(r['unknown'] for r in results.values())
    summary=dict(passed=passed,heldout=gold,recall=sum(g['hit'] for g in gold)/len(gold),named=must,anchor=anchor,whole_book=whole,sources=results,new_cost=total_new(d),ratio_note='anchor zero -> no finite quotient; each model must be nonzero',independent_budget=d['allocations'])
    save(OUT,'CALIBRATION.json',summary);print('CALIBRATION',passed,'heldout',summary['recall'],'task cost',summary['new_cost'],flush=True)
    return 0 if passed else 2

def polish(only=None):
    d=prepare()
    if not read_json(OUT/'CALIBRATION.json',{}).get('passed'):raise ValueError('Calibration not passed')
    protected={p:p.read_bytes() for b in (SUSPECT,PUMPKIN) for p in b.glob('chapter-*.body.md')}
    protected.update({p:p.read_bytes() for i in range(1,4) for p in [PUMPKIN/f'final/chapter-{i:03d}.md',PUMPKIN/f'final/chapter-{i:03d}.deai.json']})
    commands=[]
    for name,book,chapters in [('suspect',SUSPECT,'1-5'),('pumpkin',PUMPKIN,'4-5')]:
        if only and name!=only:continue
        cap=d['baseline'][name]['cost']+d['allocations'][name]
        commands.append([sys.executable,'-m','deai','run',str(book),'--env',str(ENV),'--chapters',chapters,'--jobs','2','--budget',str(cap),'--budget-root',str(book/'final')])
    env=dict(os.environ,PYTHONPATH=str(ENGINE));env.pop('BOOKENGINE_BUDGET_ROOT',None);env.pop('BOOKENGINE_BUDGET_USD',None)
    names=[only] if only else ['suspect','pumpkin']
    processes=[subprocess.Popen(cmd,cwd=ROOT,env=env,stdout=(PRIVATE/(name+'.log')).open('a'),stderr=subprocess.STDOUT) for name,cmd in zip(names,commands)]
    try:
        while any(p.poll() is None for p in processes):
            spent=total_new(d)
            if spent>1:
                for p in processes:
                    if p.poll() is None:p.send_signal(2)
                raise RuntimeError('Task USD limit exceeded; stopped')
            time.sleep(2)
    finally:
        for p in processes:
            if p.poll() is None:p.send_signal(2)
        for p in processes:p.wait()
    assert all(p.read_bytes()==raw for p,raw in protected.items()),'Original chapter or first three pumpkin finals changed'
    print('EXIT',[p.returncode for p in processes],'new cost',total_new(d),flush=True)
    return int(any(p.returncode for p in processes))

def pack():
    prepare();items=[]
    for n in (1,3,5):
        for version,p in [('draft',SUSPECT/f'chapter-{n:03d}.body.md'),('final',SUSPECT/f'final/chapter-{n:03d}.md')]:items.append(dict(chapter=n,version=version,path=str(p)))
    items.append(dict(chapter=0,version='anchor',path=str(BOOKS/'compare/claude.md')));random.SystemRandom().shuffle(items)
    lines=['# 盲读 · 七篇',''];key={}
    for label,item in zip('甲乙丙丁戊己庚',items):
        lines.extend(['## '+label,'',Path(item['path']).read_text().strip(),'']);key[label]=item
    save(OUT,'BLIND.md','\n'.join(lines));save(PRIVATE,'KEY.json',key);print('BLIND',OUT/'BLIND.md')

if __name__=='__main__':
    signal.signal(signal.SIGINT,lambda *_:STOP.set());signal.signal(signal.SIGTERM,lambda *_:STOP.set())
    action=sys.argv[1];raise SystemExit(calibrate() if action=='calibrate' else polish() if action=='polish' else polish('pumpkin') if action=='finish-pumpkin' else pack())
