"""Exercise Q1.5 on existing final ten chapters; originals remain read-only."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import json
import shutil
import time
import yaml
from bookengine.q1 import QClient, voices, review_once, DATA
from bookengine.q1_plan import prepare
from bookengine.config import Book
from bookengine.craft import recall_events, surface_hits, reference_fragments
from bookengine.length import correct_length
from bookengine.longutil import ask, paragraph_edits
from bookengine.precision import locate
from bookengine.q1_quality import metrics
from bookengine.storage import save, load
from bookengine.text import chinese_count, number_paragraphs, apply_paragraph_edits
from bookengine.batch import usage_report

ROOT=Path('../engine-books')

def run(key):
    source=ROOT/'q1'/key;out=ROOT/'q1-5'/key;out.mkdir(parents=True,exist_ok=True)
    client=QClient(out,'../../.env','');client.context_limit=10000;client.clip_context=True;client.relaxed=True;client.minimal_prose=True
    meta=load(source/'run.json');book=Book(**meta['book']);bible,threads,profile=prepare(None,book,source,Path('config'))
    if not (out/'voice_cards.yaml').exists():shutil.copyfile(source/'voice_cards.yaml',out/'voice_cards.yaml')
    bible=voices(client,bible,out,yaml.safe_load(DATA.read_text()),source=source)
    history=[load(p) for p in sorted((source/'volumes').glob('*.json'))]
    for n in range(91,101):
        path=out/f'chapters/{n:03d}.json'
        if path.exists():continue
        start=time.monotonic();body=(source/f'chapter-{n:03d}.body.md').read_text();original=body
        recalled=recall_events(source,history,n,100,100);outline=dict(number=n,must_respond=recalled)
        save(out/f'writing_outlines/{n:03d}.json',outline)
        if n==100:
            value=ask(client,'q15-end-callback','只定点改最后一章的至多3个段落，自然回应3件首卷往事，以原文摘录为准，事件摘要只是索引，不能把摘要误写当事实。让旧事改变当场选择或对白，不复述梗概，不开新坑，不改其他段落。',
                [('正文',number_paragraphs(body)),('必须回应',json.dumps(recalled,ensure_ascii=False))],'{"edits":[{"paragraph":1,"text":"完整替换段落"}]}',cap=4000,input_limit=10000,max_attempts=1)
            body=apply_paragraph_edits(body,paragraph_edits(value,body)['edits'],set(range(1,len(body.split('\n\n'))+1)))
        corrected=not 1870<=chinese_count(body)<=2530
        if corrected:body=correct_length(client,f'q15-ch{n}-length',body,{'number':n},2200)
        before=surface_hits(body,bible['characters']);body=review_once(client,body,f'q15-ch{n}-polish',bible['characters'])[0]
        row=dict(number=n,chars=chinese_count(body),within_band=1870<=chinese_count(body)<=2530,changed=body!=original,
                 correction_requested=corrected,style=metrics(body),surface_before=before,surface_after=surface_hits(body,bible['characters']),
                 ai_after=len(locate(body)),recalled=recalled,seconds=round(time.monotonic()-start,3))
        if n==100:
            value=ask(client,'q15-end-check','逐条核对首卷事实在最终正文中的回引，列出最终正文原句和首卷原句。没回应不列，不把人名或一般设定当事件回引。每个源章最多一条。',
                [('最终正文',body),('首卷原文',json.dumps(recalled,ensure_ascii=False))],'{"references":[{"chapter":1,"body_quote":"","source_quote":""}]}',cap=2500,input_limit=10000,max_attempts=1)
            row['references']=reference_fragments(value.get('references',[]) if isinstance(value,dict) else [],body,source)
        save(out/f'chapter-{n:03d}.body.md',body);save(path,row)
        print(key,n,row['chars'],len(row['surface_after']),flush=True)
    rows=[load(p) for p in sorted((out/'chapters').glob('*.json'))]
    report=dict(book=key,chapters=len(rows),word_pass=sum(r['within_band'] for r in rows),english_remaining=sum('english' in h['categories'] for r in rows for h in r['surface_after']),
                pronoun_candidates=sum('pronoun_candidate' in h['categories'] for r in rows for h in r['surface_after']),references=len(rows[-1].get('references',[])),usage=usage_report(client.calls))
    save(out/'result.json',report);return report

if __name__=='__main__':
    with ThreadPoolExecutor(max_workers=3) as pool:result=list(pool.map(run,('xuanhuan-100','urban-100','suspense-100')))
    save(ROOT/'q1-5'/'result.json',result)
