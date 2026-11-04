"""One bounded final-English sentence repair using an unused outside-body slot."""
import json
import os
from pathlib import Path
import sys
import yaml

ENGINE=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ENGINE))
from bookengine import q1
from bookengine.batch import usage_report
from bookengine.identity import name_hits
from bookengine.precision import _sentences,apply
from bookengine.storage import load,save

ROOT=ENGINE.parent.parent/'engine-books/growth-payoff-3';PHASE=ROOT/'phase.json'
os.environ.update(BOOKENGINE_PHASE=str(PHASE),BOOKENGINE_BUDGET_ROOT=str(ROOT),BOOKENGINE_BUDGET_USD='.4')

def replace_quotes(value,pairs):
    if isinstance(value,str):
        for old,new in pairs:value=value.replace(old,new)
    elif isinstance(value,list):value=[replace_quotes(x,pairs) for x in value]
    elif isinstance(value,dict):value={k:replace_quotes(v,pairs) for k,v in value.items()}
    return value

for spec in load(PHASE)['books'].values():
    out=Path(spec['path']);meta=load(out/'run.json')
    if meta['status']!='complete':raise RuntimeError('Wait for existing book process to finish')
    bible=yaml.safe_load((out/'story_bible.yaml').read_text());client=q1.QClient(out,ENGINE.parent/'../../.env','',180);client.relaxed=True
    extra=0
    for p in sorted((out/'chapters').glob('*.json')):
        row=load(p);n=row['number'];target=out/f'chapter-{n:03d}.body.md';text=target.read_text()
        hits=[dict(id=i,start=a,end=b,text=text[a:b]) for i,(a,b) in enumerate(_sentences(text),1) if q1.english_hits(text[a:b])]
        if not hits or row['outside_calls']>=2:continue
        stage=f'r2-ch{n}-english';offset=len(client.calls)
        raw=client.stage(stage,'只将点名句中的英文译成自然中文，消除中英重复。只改这些句，不加删情节、人物或动作，保留人名、数值、对白引号。返回edits，每项id沿用。\n'+q1.pack(hits),schema='{"edits":[{"id":1,"text":"改好的一原句"}]}',cap=1000,effort='low',max_attempts=1)
        revised,edits=apply(text,raw.get('edits',[]) if isinstance(raw,dict) else [],hits,allow_style=True)
        accepted=len(edits)==len(hits) and not q1.english_hits(revised) and not name_hits(revised,bible)
        seconds=sum(c.get('seconds',0) or 0 for c in client.calls[offset:]);extra+=seconds
        row.update(seconds=row['seconds']+seconds,outside_calls=row['outside_calls']+1,final_english_repair=dict(accepted=accepted,seconds=seconds,edits=edits))
        if accepted:
            pairs=[(next(h['text'] for h in hits if h['id']==e['id']),e['text']) for e in edits]
            row=replace_quotes(row,pairs);save(target,revised);row.update(chars=q1.chinese_count(revised),within_band=1870<=q1.chinese_count(revised)<=2700,style=q1.metrics(revised),english=[])
        save(p,row);print(json.dumps(dict(book=out.name,chapter=n,accepted=accepted,edits=edits,seconds=seconds),ensure_ascii=False),flush=True)
    rows=[load(p) for p in sorted((out/'chapters').glob('*.json'))]
    q1.export(out,bible['title'],rows,[(out/f"chapter-{r['number']:03d}.body.md").read_text() for r in rows])
    meta.update(usage=usage_report(client.calls),execution_seconds=meta['execution_seconds']+extra,wall_seconds=meta['wall_seconds']+extra)
    meta['acceptance']=meta['quality']=q1.acceptance(rows,[],client,bible,{},{});save(out/'run.json',meta)
