"""Data-led Q1 books: immutable design, bounded memories, observed progress."""
from copy import deepcopy
from dataclasses import replace
import datetime as dt
import fcntl
import json
import os
from pathlib import Path
import re
import signal
import time

import yaml

from .batch import usage_report
from .checks import high_confidence, roll_day
from .client import Client, SYSTEM
from tools.runtime_support import QuotaExhausted, ProjectError, write_runtime
from .config import Book
from .contracts import TYPES,choose_contract,valid_extract,payoff_ok,observations,reduce_growth,growth_warnings
from .export import export
from .endings import ending_candidates, apply_scene, scene_context, english_hits
from .identity import name_hits
from .precision import locate, _sentences, apply as sentence_edits
from .polish_checks import scan
from .serial_checks import adjacent, catch_hits
from .craft import reference_features, recall_events
from .longutil import ask as raw_ask, recover_fields, long_context
from .q1_plan import prepare, volume, outlines, beat_slice, FIX, DATA as PLAN_DATA, GROWTH
from .q1_quality import metrics, merge_state, progress, volume_issues, normalize_scenes
from .storage import load, now, save
from .text import chinese_count, prose,number_paragraphs,_paragraph_spans
DATA=Path(__file__).resolve().parents[1]/'data/q1_style.yaml'
REVISION='growth-payoff-3'
EXTRACT=yaml.safe_load((DATA.parent/'growth_extract.yaml').read_text())

def pack(value):
    return json.dumps(value,ensure_ascii=False,separators=(',',':'))


def ask(client,*args,**kwargs):
    try: return raw_ask(client,*args,**kwargs)
    except (QuotaExhausted, ProjectError):
        raise
    except (ValueError, TypeError) as exc:
        return {}


class QClient(Client):
    """Production writer retains the shared transport and cost ledger."""


def clean(text):
    return re.sub(r'\n(?:[ \t]*\n){2,}','\n\n',adjacent(prose(text))).strip()


def body_of(value,client):
    if isinstance(value,dict): value=value.get('paragraphs',value.get('text',value.get('body','')))
    if isinstance(value,list): value='\n\n'.join(p.get('text','') if isinstance(p,dict) else p for p in value if isinstance(p,(str,dict)))
    return clean(value) if isinstance(value,str) else ''


def context(bible,threads,vol,chapter,state,history,rows,previous,dropped=None):
    dropped=[] if dropped is None else dropped;names=pack(chapter.get('scenes',[]))
    people=sorted([c for c in bible.get('characters',[]) if c.get('role')=='protagonist' or c.get('name','?') in names],key=lambda c:c.get('role')!='protagonist')
    dash={k:(str(v) if len(str(v))<=18 else str(v)[:13]+'…'+str(v)[-4:]) for k,v in state.get('dashboard',{}).items()}
    dropped.extend('dashboard.'+k+':truncated' for k,v in state.get('dashboard',{}).items() if len(str(v))>18)
    cards=[]
    for c in people:
        card=dict(name=c['name'],voice=c.get('voice','')[:100],gender=c.get('gender') or '未知',address=c.get('address') or c['name'])
        card.update({k:c.get(k) for k in ('active_desire','personality_tags','never_do','desire','fear') if c.get(k)})
        card['current']=state.get('characters',{}).get(c['name'],{})
        if c.get('role')=='protagonist':card=dict(dashboard=dash,**card,continuity=state.get('continuity',{}),power_status=json.loads(pack({k:(v.get('值') if isinstance(v,dict) else v) for k,v in state.get('power_status',{}).items()}),object_hook=lambda d:{k:v for k,v in d.items() if k!='最后更新'}),power=bible.get('power',{}),realm_ability=next((str(v)[:160] for v in bible.get('world',{}).get('levels',[]) if str(state.get('dashboard',{}).get('strength','?')) in str(v)),''))
        cards.append(card)
    dossier=dict(characters=cards or [dict(name=n,current=v) for n,v in state.get('characters',{}).items()],fixed_cast=[(c['name'],c.get('role')) for c in bible.get('characters',[])],**({'dashboard':dash} if not cards else {}));hero=cards[0] if cards else {}
    candidates=[(c,'current') for c in cards[1:]]+[(hero,k) for k in ('desire','fear')]+[(c,k) for c in reversed(cards) for k in ('current','desire','fear','active_desire','personality_tags','never_do')]
    for card,key in candidates:
        if len(pack(dossier))-len(pack(dash))<=1500:break
        if key in card:dropped.append(card['name']+'.'+key);card.pop(key)
    outline={k:chapter[k] for k in ('number','payoff_due','payoff_type','payoff_criterion','solution_type','solution_avoid','group_allowed','growth_obligation','growth_setup','upgrade_due','next_upgrade','injury_limit','ending','hook_type','growth_target','growth_setup','upgrade_due','next_upgrade','title','scenes','hook','threads','must_respond','closing','callback_events','opening_conflict','structure') if k in chapter}
    return [('本章章纲',pack(outline)[:2500]),('出场人物档案、口吻卡与当前状态',pack(dossier)),
            ('上章结尾',previous[-900:]),('本章推进线',pack(beat_slice(threads,vol,chapter,state))[:1000])]


def normalize_extract(raw):
    if not isinstance(raw,dict) or not isinstance(raw.get('state'),dict):return raw
    fields=recover_fields(pack(raw))
    result=dict(raw);result.update({'type':EXTRACT.get('type_aliases',{}).get(raw['type'],raw['type'])} if isinstance(raw.get('type'),str) else {})
    for key in ('gains','costs','solutions','advances','events','r2_events','demotion_event','injuries','previous_delta','realm_displayed','realm_display_pid','realm_display_sid','opens_new_mainline','ending_reason','body','scene','rename','tail','patch','edits','paragraph_edits'):
        if result.get(key) is None and key in fields:result[key]=fields[key]
    return result


def ground(raw,text):
    raw=normalize_extract(raw);raw=raw if isinstance(raw,dict) else {};raw['state']=raw.get('state') if isinstance(raw.get('state'),dict) else {};spans=_sentences(text);paragraphs=_paragraph_spans(text)
    for k in ('gains','costs','solutions','injuries','events','advances'):raw[k]=raw[k] if isinstance(raw.get(k),list) else [raw[k]] if isinstance(raw.get(k),dict) else []
    values=[raw.get('payoff'),raw.get('demotion_event'),*[x for k in ('gains','costs','solutions','injuries') for x in (raw.get(k) or []) if isinstance(x,dict)]]
    for item,key in ((item,key) for item in [raw,*values] if isinstance(item,dict) for key in ('pid','sid','realm_display_pid','realm_display_sid')):
        if isinstance(item.get(key),str) and re.fullmatch(r'[PS]?\d+',item[key]):item[key]=int(item[key].lstrip('PS'))
    for item in values:
        if isinstance(item,dict) and type(item.get('pid')) is int and 1<=item['pid']<=len(paragraphs):a,b=paragraphs[item['pid']-1];item['quote']=text[a:b]
        elif isinstance(item,dict) and isinstance(item.get('pid'),str) and (m:=re.fullmatch(r'P?(\d+)\s*[-–—~至到]\s*P?(\d+)',item['pid'],re.I)) and 1<=int(m[1])<=int(m[2])<=len(paragraphs):item['quote']=text[paragraphs[int(m[1])-1][0]:paragraphs[int(m[2])-1][1]]
        elif isinstance(item,dict) and type(item.get('sid')) is int and 1<=item['sid']<=len(spans):
            a,b=spans[item['sid']-1];item['quote']=text[a:b]
    if type(raw.get('realm_display_sid')) is int and 1<=raw['realm_display_sid']<=len(spans):a,b=spans[raw['realm_display_sid']-1];raw['realm_display_quote']=text[a:b]
    if type(raw.get('realm_display_pid')) is int and 1<=raw['realm_display_pid']<=len(paragraphs):a,b=paragraphs[raw['realm_display_pid']-1];raw['realm_display_quote']=text[a:b]
    return raw



def prose_hits(text,bible,state):
    hits=name_hits(text,bible)
    hits += [dict(id=i,start=a,end=b,kind='english_or_injury',text=text[a:b]) for i,(a,b) in enumerate(_sentences(text),1) if english_hits(text[a:b]) or re.search(EXTRACT['injury_pattern'],text[a:b])]
    return hits+locate(text)+scan(text,bible.get('characters',[]),state)+catch_hits(text,[],bible.get('characters',[]))


def extract_state(client,name,text,state,bible,outline,rows,final=False):
    previous=rows[-1] if rows and rows[-1].get('state_failed') and not rows[-1].get('carry_attempted') else None
    prior=(client.out/f"chapter-{previous['number']:03d}.body.md").read_text() if previous else ''
    hits=prose_hits(text,bible,state) if not final else []
    contract={k:outline.get(k) for k in ('number','payoff_due','payoff_type','payoff_criterion','solution_type','solution_avoid','group_allowed','growth_obligation','growth_setup','upgrade_due','next_upgrade','injury_limit','ending','ending_review','force_scene','actual_chars','target_chars','word_range','dialogue_ratio')}
    contract['scene_start']=scene_context(text)['start'] if outline.get('ending') else None
    sections=[('合法值',pack(dict(types=TYPES,ladder=bible['canon']['ladder'],payoff_menu=bible['canon']['payoff_menu'],solution_menu=bible['canon']['solution_menu'],hook_types=bible['canon']['hook_types']))),('本章要求',pack(contract)),('编号正文',number_paragraphs(text)),('旧状态',pack({k:state.get(k) for k in ('dashboard','power_status','continuity','clock')})[:500]),('候选句S编号',pack(hits)[:1800]),('固定角色',pack([dict(name=c['name'],role=c.get('role')) for c in bible.get('characters',[])])),('末场可改范围',pack({k:v for k,v in scene_context(text).items() if k!='paragraphs'}) if outline.get('ending') else ''),('末章候选',pack(ending_candidates(text,bible,outline) if outline.get('ending') else [])),('上章失败顺带补提',pack(dict(number=previous['number'],text=prior)) if previous else '')]
    prompt=EXTRACT['final_prompt'] if final else EXTRACT['prompt']
    if not final and not outline.get('word_range',[1870,2700])[0]<=outline.get('actual_chars',2200)<=outline.get('word_range',[1870,2700])[1]:prompt=EXTRACT['length_first']+prompt
    schema=pack({k:v for k,v in EXTRACT['schema'].items() if not final or k not in ('edits','paragraph_edits','patch','body','scene','rename','tail','previous_delta')});saved=client.prefix;client.prefix=''
    complete=not previous or sections[-1][1] in long_context(sections,max(0,10000-len(SYSTEM)-len(prompt)-len(schema)-350))
    try:raw=ground(ask(client,name,prompt,sections,schema,cap=6000 if final else 7000,effort='low',input_limit=10000,max_attempts=1),text)
    finally:client.prefix=saved
    if outline.get('ending') and raw.get('hook_type') is None and raw.get('opens_new_mainline') is False:raw['hook_type']='完结收束'
    raw['_previous_complete']=complete
    save(client.out/'steps'/f'{name}.json',raw)
    return raw,not valid_extract(raw,text,bible['canon']['hook_types']+(['完结收束'] if outline.get('ending') else []),bible['canon']['payoff_menu'],ending=outline.get('ending',False)),0


def supplement(text,value):
    spans=_paragraph_spans(text);idx=value.get('after');patch=value.get('text')
    if type(idx) is int and 1<=idx<=len(spans) and isinstance(patch,str) and 0<len(patch.strip())<=400 and '\n' not in patch:
        a,b=spans[idx-1];return clean(text[:a]+patch.strip()+text[b:] if value.get('replace') is True else text[:b]+'\n\n'+patch.strip()+text[b:]),True
    return text,False


def amend(text,raw,bible,state,outline):
    hits=prose_hits(text,bible,state);detail=dict(edits=[],hits_before=len(hits),adoption_failed=False,remaining=[],patch=False)
    if outline.get('ending') and raw.get('scene'):
        changed,accepted=apply_scene(text,raw['scene']);return changed,dict(detail,patch=accepted)
    low,high=outline.get('word_range',(1870,2700));body=body_of(raw.get('body',''),None);count,amount=chinese_count(text),chinese_count(body)
    if body and (count>high and low<=amount<count or count<low and count<amount<=high):return body,dict(detail,patch=True)
    spans=_sentences(text);changed,edits=sentence_edits(text,raw.get('edits',[]),[dict(id=i,start=a,end=b) for i,(a,b) in enumerate(spans,1) if i in {h['id'] for h in hits}],allow_empty=True,allow_style=True)
    bad=not isinstance(raw.get('edits',[]),list) or len(edits)!=len(raw.get('edits',[])) or any(abs(changed.count(a)-changed.count(b))>abs(text.count(a)-text.count(b)) for a,b in ('“”','「」'))
    changed=text if bad else changed;changed,patched=supplement(changed,raw['patch']) if isinstance(raw.get('patch'),dict) else (changed,False)
    return changed,dict(detail,edits=[] if bad else edits,adoption_failed=bad,patch=patched)


def chapter_record(out, number):
    recovered = out / f'chapters/{number:03d}.recovery.json'
    return load(recovered if recovered.exists() else out / f'chapters/{number:03d}.json')


def chapter(client,book,bible,threads,profile,vol,outline,state,history,rows,out,style,data):
    idx=outline['number'];path=out/f'chapters/{idx:03d}.json'
    if path.exists() and load(path).get('fact_source')=='final_body':return chapter_record(out, idx)
    started=time.monotonic();prefix=f'r2-ch{idx}';target=book.words_per_chapter;canon=dict(bible['canon'],final_volume_start=max(1,((book.chapters-1)//25)*25+1));bible=dict(bible,canon=canon)
    low,high=(1870,2700) if target==2200 else (round(target*.85),round(target*1.15));previous=(out/f'chapter-{idx-1:03d}.body.md').read_text() if idx>1 else '';drops=[]
    contract=choose_contract(canon,rows,idx,state.get('pending_payoff') or outline.get('payoff_type'),state,canon['final_volume_start'])
    method=outline.get('solution_type');method=method if method in canon['solution_menu'] and method not in contract['solution_avoid'] else contract['solution_type']
    outline=dict(outline,**{k:v for k,v in contract.items() if k not in ('warnings','solution_type')},solution_type=method,payoff_criterion=GROWTH.get('payoff_criteria',{}).get(contract['payoff_type']),injury_limit=1,ending=idx==book.chapters,hook='末章只结清本书已有冲突，常规悬念钩子作废；不得留下新身份、物件来源或追查任务。以既有人物行动或对白收笔。' if idx==book.chapters else outline.get('hook'),growth_target=vol.get('expected_dashboard',{}))
    save(out/f'writing_outlines/{idx:03d}.json',outline);sections=context(bible,threads,vol,outline,state,history,rows,previous,drops)
    if book.style_reference:sections.append(('授权参考的统计风格特征',pack(reference_features(book.style_reference))))
    saved=client.prefix;client.prefix=''
    try:text=body_of(ask(client,prefix+'-draft',data[style]+f'\n目标{target}汉字，合格{low}—{high}，本章正文{2000 if target==2200 else low}—{2400 if target==2200 else high}汉字，只写两场戏，各约{round(target*.46)}—{round(target*.5)}字，用---分隔；最多新增1处伤病/代价。到本章情节完成立即收笔，直接输出正文。',sections,cap=6500,input_limit=10000,max_attempts=1),client)
    finally:client.prefix=saved
    save(out/f'chapter-{idx:03d}.r2.draft.md',text);outline.update(actual_chars=chinese_count(text),target_chars=target,word_range=[low,high],dialogue_ratio=metrics(text)['dialogue_ratio'])
    raw,failed,_=extract_state(client,prefix+'-finish',text,state,bible,outline,rows);initial_failed=failed;original=text;carry=raw.get('previous_delta');carry_full=raw.get('_previous_complete')
    text,notes=amend(text,raw,bible,state,outline);patched=notes['patch']
    for old,new in (raw.get('rename') if isinstance(raw.get('rename'),dict) else {}).items():
        if old in {h['name'] for h in name_hits(original,bible)} and isinstance(new,str) and new in {c['name'] for c in bible.get('characters',[])}:text=text.replace(old,new)
    changed=text!=original;failed=failed or bool((raw.get('scene') or raw.get('body')) and not changed)
    if changed:raw,failed,_=extract_state(client,prefix+'-final-state',text,state,bible,outline,[],final=True)
    if rows and rows[-1].get('state_failed') and not rows[-1].get('carry_attempted'):
        last=rows[-1];last['carry_attempted']=True;prior=(out/f"chapter-{last['number']:03d}.body.md").read_text();delta=ground(carry,prior)
        if carry_full and valid_extract(delta,prior,canon['hook_types'],canon['payoff_menu']):state,_=reduce_growth(state,delta,bible,prior,last['number'],base=merge_state(state,{k:v for k,v in delta['state'].items() if v is not None},bible,last['number']));last.update(state=state,state_recovered=True,payoff_fulfilled=payoff_ok(delta.get('payoff'),last.get('payoff_type'),prior,canon['payoff_menu']),**{k:delta[k] for k in ('type','hook_type','payoff')},**observations(delta,prior,last['number']))
        save(out/f"chapters/{last['number']:03d}.recovery.json",last)
    current=deepcopy(state);issues=[]
    if not failed:
        current,issues=reduce_growth(state,raw,bible,text,idx,base=merge_state(state,{k:v for k,v in raw['state'].items() if v is not None},bible,idx));current['clock']=roll_day(state.get('clock'),current.get('clock'),text[-1200:],text)
        current,thread_issues=progress(current,raw,outline,bible,threads,profile);issues+=thread_issues
    paid=not failed and payoff_ok(raw.get('payoff'),outline['payoff_type'],text,canon['payoff_menu']);current['pending_payoff']=outline['payoff_type'] if outline['payoff_due'] and not paid else None
    kinds=normalize_scenes(raw.get('scene_types'));events=[e for e in raw.get('events',[]) or [] if isinstance(e,dict)]
    row=dict(number=idx,title=outline.get('title',f'第{idx}章'),status='complete' if text else 'degraded',chars=chinese_count(text),within_band=low<=chinese_count(text)<=high,summary=str(raw.get('summary') or text[:100])[:200],state=current,style=metrics(text),type=raw.get('type'),hook_type=raw.get('hook_type'),payoff=raw.get('payoff'),payoff_due=outline['payoff_due'],payoff_type=outline['payoff_type'],payoff_fulfilled=paid,payoff_patch=patched,state_failed=failed,state_initial_failed=initial_failed,state_retries=0,dropped_fields=drops,
        scene_types=kinds,structure=raw.get('structure'),r2_events=raw.get('r2_events') or [],meeting_scenes=kinds.count('meeting'),scenes=len(kinds),hard_conflicts=len(high_confidence(state,events,text)),issues=issues+contract['warnings'],warnings=[],length_correction=chinese_count(original)!=chinese_count(text),polish=notes,dialogue_rewrite=False,writer='minimal-b',fact_source='final_body',revision=REVISION,seconds=round(time.monotonic()-started,3),outside_calls=1+int(changed),injuries_count=current.get('injuries_count',0) if not failed else None,ending_candidates=ending_candidates(text,bible,outline) if outline['ending'] else [],opens_new_mainline=raw.get('opens_new_mainline') if outline['ending'] else False)
    row.update(name_drift_before=len(name_hits(original,bible)),name_drift=name_hits(text,bible),english=english_hits(text));row.update(observations(raw,text,idx) if not failed else {});row['solutions']=[x for x in current.get('solutions',[]) if x['chapter']==idx];save(out/f'chapter-{idx:03d}.body.md',text);save(path,row);return row


def acceptance(rows,history,client,bible,threads,profile,base=0):
    new=[r for r in rows if r['number']>base]
    return dict(generated=sum(r['status']=='complete' for r in rows),chapter_chars=[r['chars'] for r in rows],
        word_band_pass=sum(r['within_band'] for r in rows),hard_conflicts=sum(r.get('hard_conflicts',0) for r in rows),
        format_failures=sum(c['status'] not in ('ok','pending') for c in client.calls),
        payoff_rate=sum(r.get('payoff_fulfilled',False) for r in new if r.get('payoff_due'))/max(1,sum(bool(r.get('payoff_due')) for r in new)),classification_empty=sum(any(r.get(k) is None for k in ('type','hook_type','payoff')) for r in new),
        dialogue_scope_base=base,new_chapters=len(new),new_dialogue20_pass=sum(r['style']['dialogue_ratio']>=.2 for r in new))


def run_q1(book_path,out,env,assets,timeout=180,style='b',extend_to=None):
    out=Path(out);out.mkdir(parents=True,exist_ok=True,mode=0o700)
    with (out/'runner.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        stopped=[False]
        def stop(signum,frame): stopped[0]=True
        handlers={s:signal.signal(s,stop) for s in (signal.SIGINT,signal.SIGTERM)}
        try:
            book=Book.read(book_path)
            if extend_to:
                meta=load(out/'run.json');old=Book(**meta['book'])
                if replace(old,chapters=book.chapters).record()!=book.record() or extend_to<old.chapters:raise ValueError('extension requires the original book and a larger chapter target')
                if extend_to>old.chapters:meta.update(book=replace(old,chapters=extend_to).record(),status='interrupted');save(out/'run.json',meta)
            return _run(replace(book,chapters=extend_to) if extend_to else book,out,env,Path(assets),timeout,style,lambda:stopped[0])
        except (ValueError, RuntimeError) as exc:
            kind = 'quota_exhausted' if isinstance(exc, QuotaExhausted) else 'error'
            runtime = load(out / 'runtime.json') if (out / 'runtime.json').exists() else {}
            write_runtime(out, kind, runtime.get('stage', 'run-q1'), error=str(exc))
            if (out / 'run.json').exists():
                meta = load(out / 'run.json')
                meta.update(status=kind, stop_reason=str(exc))
                save(out / 'run.json', meta)
            raise
        finally:
            for s,h in handlers.items(): signal.signal(s,h)


def _run(book,out,env,assets,timeout,style,stop):
    path=out/'run.json';meta=load(path) if path.exists() else dict(mode='Q1',book=book.record(),started=now(),style=style,status='running')
    if meta.get('mode')!='Q1' or Book(**meta['book']).record()!=book.record() or meta.get('style')!=style: raise ValueError('Q1 checkpoint settings changed')
    if meta['status']=='complete': return meta
    if meta.get('revision',{}).get('name')!=REVISION:
        meta.setdefault('previous_runs',[]).append(dict(status=meta['status'],stop_reason=meta.get('stop_reason'),wall_seconds=meta.get('wall_seconds',0)))
        meta['revision']=dict(name=REVISION,started=now(),base_chapters=meta.get('completed_chapters',0),base_seconds=meta.get('execution_seconds',meta.get('wall_seconds',0)))
    elif meta['status']=='interrupted':
        meta['revision'].update(started=now(),base_seconds=meta.get('execution_seconds',meta.get('wall_seconds',0)))
    revision=meta['revision'];revision.setdefault('initial_seconds',revision['base_seconds']);meta.pop('stop_reason',None)
    meta['status']='running'
    save(path,meta);data=yaml.safe_load(DATA.read_text());client=QClient(out,env,'',timeout)
    client.context_limit=10000;client.clip_context=True;client.relaxed=True;client.style_b=False;client.minimal_prose=True;client.stop_requested=stop
    try:bible,threads,profile=prepare(client,book,out,assets,design=True)
    except (QuotaExhausted, ProjectError):
        raise
    except (ValueError,RuntimeError) as exc:
        meta.update(status='failed',stop_reason=str(exc),usage=usage_report(client.calls));save(path,meta);raise
    canon=dict(bible['canon'],solution_menu=GROWTH['packs'][book.prompt_pack]['solution_menu'],solution_aliases=GROWTH.get('solution_aliases',{}),final_volume_start=max(1,((book.chapters-1)//25)*25+1));bible=dict(bible,canon=canon);profile=dict(profile,canon=canon)
    client.glossary=bible.get('glossary',{});client.plan_suffix='-growth2'
    client.prefix=pack(dict(reader_expectation=bible.get('reader_expectation'),anti_patterns=profile.get('anti_patterns',[])))[:650]
    rows=[];history=[];state=merge_state({}, {},bible);vol={};batch=[];before={};feedback=[];alerts=[]
    for idx in range(1,book.chapters+1):
        vn=(idx-1)//25+1
        saved=out/f'chapters/{idx:03d}.json'
        cached_record = saved.exists() and load(saved).get('fact_source') == 'final_body'
        cached = cached_record and idx <= meta.get('completed_chapters', 0)
        if cached:
            row=chapter_record(out, idx);rows.append(row);state=row['state']
            for p in (out/'volumes').glob('*.json'):
                m=load(p)
                if m.get('end')==idx:history=[h for h in history if h['number']!=m['number']]+[m]
            continue
        if not vol or (idx-1)%25==0:
            before=deepcopy(next((r['state'].get('dashboard',{}) for r in rows if r['number']==(vn-1)*25),bible.get('initial_dashboard',{}) if vn==1 else state.get('dashboard',{})))
            vol=volume(client,bible,threads,vn,book.chapters,state,history,feedback)
            if history and history[-1].get('volume_issues'):
                vol=volume(client,bible,threads,vn,book.chapters,state,history,feedback,redo=True)
        if not batch or not any(c.get('number')==idx for c in batch):
            end=min(idx+9,vol.get('end',vn*25),book.chapters)
            batch=outlines(client,bible,threads,profile,vol,idx,end,state,rows[-29:],history,alerts)
        if rows and not state.get('realm_progress'):
            level=state.get('dashboard',{}).get('strength');at=next((r['number'] for r in reversed(rows) if r['state'].get('dashboard',{}).get('strength')!=level),0)+1;state['realm_progress']=dict(level=level,upgrade_at=at,displayed_at=None)
        outline=next((c for c in batch if c.get('number')==idx),dict(number=idx,title=f'第{idx}章',scenes=[],threads=[]))
        row=chapter(client,book,bible,threads,profile,vol,outline,state,history,rows,out,style,data)
        rows.append(row);state=row['state'];alerts=row['issues'];meta.setdefault('dropped_fields',{})[str(idx)]=row.get('dropped_fields',[]);save(out/'state.json',state)
        if idx%25==0 or idx==book.chapters:
            memory_path=out/f'volumes/{vn:02d}-{idx:03d}.json'
            if memory_path.exists() and load(memory_path).get('fact_source')=='final_body': memory=load(memory_path)
            else:
                issues=volume_issues(before,state.get('dashboard',{}),vol,history)+growth_warnings(state,before,state.get('dashboard',{}),bible['canon'],vol['start'],idx)
                memory=dict(number=vn,start=vol.get('start',(vn-1)*25+1),end=idx,summary='；'.join(r['summary'][:60] for r in rows if vol['start']<=r['number']<=idx)[-1500:],before_dashboard=before,after_dashboard=state.get('dashboard',{}),volume_issues=issues,fact_source='final_body')
                save(memory_path,memory)
            history.append(memory);feedback=[*memory.get('feedback',[]),*memory.get('volume_issues',[]),*row.get('issues',[])]
        summary=acceptance(rows,history,client,bible,threads,profile,revision['base_chapters'])
        revision['seconds']=(dt.datetime.now(dt.timezone.utc)-dt.datetime.fromisoformat(revision['started'])).total_seconds()
        seconds=revision['base_seconds']+revision['seconds'];meta['execution_seconds']=seconds
        if not cached_record:
            row['run_elapsed'] = seconds
            save(out / f'chapters/{idx:03d}.json', row)
        recent=[r for r in rows if r['number']>revision['base_chapters']]
        if len(recent)>=50:
            average=sum(r['seconds'] for r in recent[-50:])/50
            calls=[c for c in client.calls if c['stage'].startswith('r2-ch') and int(re.findall(r'\d+',c['stage'])[0])>=idx-49]
            rate=sum(c['status'] not in ('ok','pending') for c in calls)/max(1,len(calls))
            if average>100 or rate>.1:
                meta.setdefault('warnings', []).append(dict(chapter=idx, average_seconds=average, failure_rate=rate))
        meta.update(completed_chapters=sum(r['status']=='complete' for r in rows),wall_seconds=round(seconds,3),usage=usage_report(client.calls),quality=summary,updated=now())
        save(path,meta)
        print(f"chapter={idx} chars={row['chars']} dialogue={row['style']['dialogue_ratio']:.3f} elapsed={seconds:.1f}s",flush=True)
        if idx%25==0 and idx>revision['base_chapters']:
            log=out/'progress.log';save(log,(log.read_text() if log.exists() else '')+f"chapter={idx} seconds={seconds:.1f} cost_usd={meta['usage']['estimated_usd']['conservative_total']} input_chars={max((c['input_chars'] for c in client.calls if c['stage']==f'g-ch{idx}-draft'),default=0)} active_lines={sum(bool(v.get('last')) for v in state.get('ledger',{}).values())} format_rate={summary['format_failures']/max(1,len(client.calls)):.4f}\n")
        if stop():
            meta['status']='interrupted';save(path,meta)
            return meta
    meta['quality']=acceptance(rows,history,client,bible,threads,profile,revision['base_chapters'])
    export(out,bible.get('title','Q1'),rows,[(out/f"chapter-{r['number']:03d}.body.md").read_text() for r in rows])
    meta.update(status='complete' if all(r['status']=='complete' for r in rows) else 'incomplete',finished=now(),acceptance=meta['quality']);save(path,meta)
    return meta
