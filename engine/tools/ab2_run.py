#!/usr/bin/env python3
"""Q1 sentence polish, paired writer blind samples, and two isolated repairs."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import os
from pathlib import Path
import random
import re
import signal
import sys

import yaml

from ab2_support import ROOT, PaidClient, total_cost, parallel, polish, design, draft, opencode_draft
from precision_rules import locate, apply, stats
from bookengine.client import MODEL
from bookengine.length import correct_length
from bookengine.q1_quality import metrics as quality_metrics
from bookengine.storage import load,save
from bookengine.text import chinese_count,pack

BOOKS=Path('../engine-books/q1').resolve()
POLISH=BOOKS.parent/'q1-polish'
BLIND=ROOT.parent
ENV=Path('../../.env').resolve()
ASSETS=Path('config').resolve()


def polish_books(inputs):
    def one(item):
        book,n,text=item;repaired=load(BOOKS/book/f'chapters/{n:03d}.json').get('local_repair')=='q1-polish-ab2'
        folder=ROOT/'runs'/'polish'/book/(f'{n:03d}'+('-repaired-final' if repaired else ''))
        if (folder/'input.md').exists():text=(folder/'input.md').read_text()
        elif repaired:save(folder/'input.md',text)
        updated,edits=polish(text,folder,ENV)
        before,after=stats(text),stats(updated)
        save(POLISH/book/f'chapter-{n:03d}.body.md',updated)
        row=dict(book=book,chapter=n,before=before,after=after,edits=len(edits))
        save(POLISH/book/f'chapter-{n:03d}.json',row)
        return row
    result=parallel(inputs,one)
    result.sort(key=lambda x:(x['book'],x['chapter']));save(POLISH/'METRICS.json',result)
    return result


def blind_samples():
    samples=[(BOOKS/'urban-100',51),(BOOKS/'xuanhuan-100',49)]
    contexts=parallel(samples,lambda x:design(*x,ENV,ASSETS),jobs=2)
    tasks=[(data,v) for data in contexts for v in ('A','B','D','E')]
    def one(item):
        data,v=item;source=Path(data['source']);folder=ROOT/'runs'/'blind'/f'{source.name}-{data["chapter"]}-{v}'
        if v=='E':text,detail=opencode_draft(data,folder)
        else:
            try:text=draft(data,v,folder,ENV,'MiniMax-M3' if v=='D' else MODEL);detail=dict(status='ok' if text else 'skipped')
            except RuntimeError as exc:
                if 'budget' in str(exc):raise
                text='';detail=dict(status='skipped',reason=str(exc))
        return dict(book=source.name,chapter=data['chapter'],version=v,model='MiniMax-M3' if v=='D' else 'opencode/deepseek-v4.1-flash-free' if v=='E' else MODEL,
            path=str(folder/'final.md'),text=text,detail=detail)
    result=parallel(tasks,one,jobs=4)
    for data in contexts:
        a=next(r for r in result if r['book']==Path(data['source']).name and r['version']=='A')
        if not a['text']:continue
        folder=ROOT/'runs'/'blind'/f'{a["book"]}-{a["chapter"]}-C'
        text,edits=polish(a['text'],folder,ENV)
        result.append(dict(a,version='C',path=str(folder/'final.md'),text=text,detail=dict(status='ok',edits=len(edits))))
    save(ROOT/'SKIPS.json',[{k:v for k,v in r.items() if k!='text'} for r in result if not r['text']])
    present=[r for r in result if r['text']]
    path=ROOT/'KEY.json'
    if path.exists() and len(load(path))==len(present):
        key=load(path)
    else:
        random.SystemRandom().shuffle(present)
        key={label:{k:v for k,v in row.items() if k not in ('text','detail')} for label,row in zip('甲乙丙丁戊己庚辛壬癸',present)}
        save(path,key);os.chmod(path,0o600)
    sections=[];public={}
    for label,item in key.items():
        text=Path(item['path']).read_text();m=stats(text);public[label]=m
        kinds=m['categories']
        line=f"字数 {m['chars']}；AI句命中 {m['hits']}（总结 {kinds.get('summary',0)}／顿悟 {kinds.get('epiphany',0)}／诗化升华 {kinds.get('poetic',0)}）；单句段 {m['single_sentence_ratio']:.1%}；对白 {m['dialogue_ratio']:.1%}。"
        sections.extend(['## '+label,line,text])
    save(BLIND/'BLIND.md','\n\n'.join(sections)+'\n');save(BLIND/'METRICS.json',public)
    return dict(samples=len(present),skipped=len(result)-len(present),cli=[r['detail'] for r in result if r['version']=='E'])


def original_outline(folder,n):
    for path in sorted((folder/'steps').glob('plan-q1-r2-chapters-*.json'),key=lambda p:p.stat().st_mtime,reverse=True):
        value=load(path)
        for item in value.get('chapters',[]) if isinstance(value,dict) else value:
            if str(item.get('number'))==str(n):return deepcopy(item)
    raise RuntimeError('Missing original outline')


def repairs():
    result=[]
    for book,n in [('urban-100',33),('xuanhuan-100',32)]:
        source=BOOKS/book;original=(ROOT/'originals'/book/f'{n:03d}.md').read_text()
        folder=ROOT/'runs'/'repairs'/f'{book}-{n}';final=folder/'final.md'
        if final.exists():text=final.read_text()
        else:
            chapter=original_outline(source,n)
            chapter['continuity_anchors']=dict(previous=(source/f'chapter-{n-1:03d}.body.md').read_text()[-750:],following=(source/f'chapter-{n+1:03d}.body.md').read_text()[:750],rule='衔接前后已定正文，不提前兑现后章；只扩写本章原有场景，不改变章末所在地、物品归属、时间和已知信息。原文已有事实优先。')
            client=PaidClient(folder,ENV)
            text=correct_length(client,'authorized-repair-length',original,chapter,2200)
            save(final,text)
        row=load(source/f'chapters/{n:03d}.json')
        updated=chinese_count(text)
        if 1870<=updated<=2530:
            save(source/f'chapter-{n:03d}.body.md',text)
            row.update(chars=updated,within_band=True,style=quality_metrics(text),length_correction=True,local_repair='q1-polish-ab2')
            save(source/f'chapters/{n:03d}.json',row)
            meta=load(source/'run.json');rows=[load(p) for p in sorted((source/'chapters').glob('*.json'))]
            meta['quality'].update(chapter_chars=[r['chars'] for r in rows],word_band_pass=sum(r['within_band'] for r in rows))
            save(source/'run.json',meta)
        result.append(dict(book=book,chapter=n,before=chinese_count(original),after=updated,applied=1870<=updated<=2530,call_limit=1))
    save(ROOT/'REPAIRS.json',result)
    return result


def main():
    ROOT.mkdir(parents=True,exist_ok=True,mode=0o700);os.chmod(ROOT,0o700)
    inputs=[]
    for source in sorted(BOOKS.glob('*-100')):
        for n in range(26,51):
            path=source/f'chapter-{n:03d}.body.md'
            if path.exists():inputs.append((source.name,n,path.read_text()))
        n=33 if source.name=='urban-100' else 32 if source.name=='xuanhuan-100' else None
        if n and not (ROOT/'originals'/source.name/f'{n:03d}.md').exists():save(ROOT/'originals'/source.name/f'{n:03d}.md',(source/f'chapter-{n:03d}.body.md').read_text())
    # Freeze all existing input texts before either experiment or authorized repair.
    with ThreadPoolExecutor(max_workers=2) as pool:
        p=pool.submit(polish_books,inputs);b=pool.submit(blind_samples)
        polished=p.result();blind=b.result()
    fixed=repairs()
    totals={}
    for book in sorted({r['book'] for r in polished}):
        rows=[r for r in polished if r['book']==book]
        totals[book]=dict(chapters=len(rows),before=sum(r['before']['hits'] for r in rows),after=sum(r['after']['hits'] for r in rows),edits=sum(r['edits'] for r in rows),
            categories_before={k:sum(r['before']['categories'].get(k,0) for r in rows) for k in ('summary','epiphany','poetic')},
            categories_after={k:sum(r['after']['categories'].get(k,0) for r in rows) for k in ('summary','epiphany','poetic')})
    result=dict(polish=totals,blind=blind,repairs=fixed,estimated_usd=total_cost())
    save(ROOT/'RESULT.json',result)
    print('COMPLETE',json.dumps(result,ensure_ascii=False),flush=True)


if __name__=='__main__':main()
