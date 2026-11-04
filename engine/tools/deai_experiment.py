"""Explicit independent five-draft evaluation and blind dialogue comparison."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from dataclasses import asdict
import argparse
import json
import os
import random
import sys
import time
import yaml

ENGINE=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ENGINE))
from deai import polish
from deai.flow import Session
from deai.client import MiniMax
from deai.io import save,read_json,read_text,process
from deai.rules import features,detect
from deai.text import count,parse,paragraphs,invariant
WORK=ENGINE.parent;BOOKS=WORK.parent/'engine-books';ROOT=BOOKS/'blind-5';PRIVATE=ROOT/'private';EXP=PRIVATE/'experiment';ENV=WORK/'../../.env';FLASH='MiniMax-M3.1-Flash'

def initialize():
    EXP.mkdir(parents=True,exist_ok=True);path=EXP/'manifest.json'
    if path.exists():return read_json(path)
    old=read_json(BOOKS/'blind-3/private/KEY.json');plans={}
    for genre in ('suspense-mystery','xuanhuan-power-fantasy'):
        spec=next(s for s in old['groups'].values() if s.get('track')=='t16' and s['version']=='F1' and s['genre']==genre)
        plans[genre]=read_json(Path(spec['plan'])/'plan.json')
    prior=PRIVATE/'calibration-r2';source_manifest=read_json(prior/'manifest.json');specs=[]
    for key,model,genre in [('city-flash',FLASH,'suspense-mystery'),('city-m3','MiniMax-M3','suspense-mystery'),('fantasy-flash',FLASH,'xuanhuan-power-fantasy')]:
        plan=plans[genre];folder=EXP/'books'/key;folder.mkdir(parents=True,exist_ok=True)
        ctx=dict(characters=plan['characters'],setting=dict(premise=plan['premise'],era=1992 if genre=='suspense-mystery' else 2004),genre=genre,facts=[],dialogue_mode='scene')
        save(folder,'deai-context.json',ctx);save(folder,'plan.json',plan);specs.append(dict(key=key,path=str(folder),model=model,kind='new',genre=genre))
    for role in ('gpt','gemini'):
        source=next(s for s in source_manifest['sources'] if s['role']==role);folder=EXP/'books'/('city-'+role);folder.mkdir(parents=True,exist_ok=True)
        text=read_text(prior/source['id']/'source.md');save(folder,'chapter-001.body.md',text)
        cards=read_json(PRIVATE/'calibration'/source['id']/'cards.json');save(folder,'deai-context.json',dict(characters=cards.get('characters',[]),setting=cards.get('setting',{}),genre='suspense-mystery',facts=[],dialogue_mode='scene'))
        specs.append(dict(key='city-'+role,path=str(folder),model=role,kind='stored',genre='suspense-mystery'))
    anchor=next(s for s in source_manifest['sources'] if s['role']=='claude');save(EXP,'anchor.md',read_text(prior/anchor['id']/'source.md'))
    save(EXP,'manifest.json',dict(specs=specs,created=time.time()));return read_json(path)

def draft(spec):
    folder=Path(spec['path']);target=folder/'chapter-001.body.md'
    if target.exists():return
    plan=read_json(folder/'plan.json');client=MiniMax(folder/'.writing',ENV,spec['model'])
    text=client([dict(role='system',content='你写中文网络小说，只交付正文。'),dict(role='user',content='写第1章正文，目标2200汉字，范围2000—2400，不写标题。只按给定人物与章纲展开。\n'+json.dumps(dict(premise=plan['premise'],characters=plan['characters'],outline=plan['outline'],previous_ending='',threads=plan['outline'].get('threads',[])),ensure_ascii=False))],stage='chapter1-draft',max_tokens=6500)
    save(folder,'chapter-001.body.md',text.strip());print('draft '+spec['key']+' chars='+str(count(text)),flush=True)

def local(spec):
    folder=Path(spec['path']);started=time.monotonic();status=process(folder,'001',folder/'chapter-001.body.md',ENV,'scene',polish,MiniMax)
    save(folder/'final','SUMMARY.md','# deai\n\nchapter-001: '+status+'\n');print('polish '+spec['key']+' '+status+' '+str(round(time.monotonic()-started,2))+'s',flush=True)
    return status

def ab():
    source=EXP/'books/city-flash';text=read_text(source/'chapter-001.body.md');ctx=read_json(source/'deai-context.json');client=MiniMax(EXP/'ab/common',ENV)
    inventory=Session(client,ctx).ask('ab-inventory','只按正文确定场景边界和必须保留事实，不改写。不要逐段写事件，paragraphs只需首场一个摘要。entities全章实际重要实体短名去重，ending_image不超过80字。scenes中每场pids连续，第一场要完整并包含该场全部说话轮次。返回{"entities":[],"paragraphs":[{"pid":"P1","entities":[],"events":[]}],"ending_image":"末场具体画面","scenes":[{"pids":["P1","P2"]}]}。',dict(paragraphs=paragraphs(text),characters=ctx['characters']),6500)
    def one(mode):
        folder=EXP/'ab'/mode;output=folder/'final.md'
        if output.exists() and read_json(folder/'result.json').get('selection_policy')=='paragraph_rollback':return
        context=dict(ctx,inventory=inventory,skip_local=True,force_dialogue=True,dialogue_mode=mode)
        result=polish(text,context,MiniMax(folder,ENV));record=result.record();record['selection_policy']='paragraph_rollback';save(folder,'final.md',result.text);save(folder,'result.json',record);print('AB '+mode+' '+result.status+' edits='+str(len(result.edits)),flush=True)
    with ThreadPoolExecutor(max_workers=2) as pool:list(pool.map(one,('scene','actor')))

def finish():
    m=initialize();samples=[];results=[]
    for spec in m['specs']:
        folder=Path(spec['path']);draft=folder/'chapter-001.body.md';final=folder/'final/chapter-001.md';report=folder/'final/chapter-001.deai.json'
        if not final.exists():raise RuntimeError('Missing final: '+spec['key'])
        before=read_text(draft);after=read_text(final);r=read_json(report);context=read_json(folder/'deai-context.json');violations=invariant(before,after,context,whole=True)
        if violations:raise RuntimeError('Invariant violation '+spec['key']+':'+str(violations))
        results.append(dict(key=spec['key'],model=spec['model'],status=r['status'],before_chars=count(before),after_chars=count(after),before_score=r.get('before_score'),after_score=r.get('after_score'),edits=len(r.get('edits',[])),seconds=r.get('seconds'),checks=r.get('checks'),warnings=r.get('warnings')))
        for version,path in [('draft',draft),('final',final)]:samples.append(dict(key=spec['key'],version=version,path=str(path)))
    samples.append(dict(key='anchor',version='anchor',path=str(EXP/'anchor.md')))
    for mode in ('scene','actor'):
        path=EXP/'ab'/mode/'final.md'
        if path.exists():samples.append(dict(key='dialogue-ab',version=mode,path=str(path)))
    keyfile=PRIVATE/'KEY.json'
    if keyfile.exists():key=read_json(keyfile)
    else:
        random.SystemRandom().shuffle(samples);key={label:spec for label,spec in zip('甲乙丙丁戊己庚辛壬癸子丑寅卯辰巳',samples)};save(PRIVATE,'KEY.json',key);os.chmod(keyfile,0o600)
    sections=['# 盲读样章\n\n代号不代表排名，请按人物、场面和读下去的意愿判断。']
    for label,spec in key.items():
        text=read_text(spec['path']);sections.extend(['## '+label,'字数：'+str(count(text)),text])
    save(ROOT,'BLIND.md','\n\n'.join(sections)+'\n');save(EXP,'results.json',results);print(json.dumps(results,ensure_ascii=False),flush=True)

if __name__=='__main__':
    a=argparse.ArgumentParser();a.add_argument('stage',choices=['prepare','draft','polish','ab','finish']);args=a.parse_args();m=initialize()
    if args.stage=='draft':
        with ThreadPoolExecutor(max_workers=3) as pool:list(pool.map(draft,[s for s in m['specs'] if s['kind']=='new']))
    if args.stage=='polish':
        with ThreadPoolExecutor(max_workers=3) as pool:list(pool.map(local,m['specs']))
    if args.stage=='ab':ab()
    if args.stage=='finish':finish()
