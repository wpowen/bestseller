"""Bounded local repair; every publication decision is against the original text."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass,field,asdict
from pathlib import Path
import json
import re
import random
import time
import yaml
from .text import paragraphs,sentences,count,parse,replace,invariant,distance,rollback_invalid
from .rules import detect,features,merge_hits,FACT_TYPES,carry_hits,RECALL,neutral_tags
from .client import LLMError
from .version import VERSION

DATA=Path(__file__).resolve().parent/'data'
RULES=yaml.safe_load((DATA/'deai_rules.yaml').read_text());PROMPTS=yaml.safe_load((DATA/'deai_prompts.yaml').read_text());CASES=yaml.safe_load((DATA/'ai_flavor_cases.yaml').read_text())
KINDS=('verb_mismatch','empty_scene','cool_micro_action','written_dialogue','character_label','emotion_overload')

def missing_paragraphs(value, parts):
    checked=value.get('checked_pids',[])
    return [p for p in parts if not isinstance(checked,list) or p['pid'] not in checked]

@dataclass
class Result:
    text:str;hits:list=field(default_factory=list);edits:list=field(default_factory=list);before_score:float|None=None;after_score:float|None=None
    calls:int=0;prompt_tokens:int=0;completion_tokens:int=0;seconds:float=0;status:str='unchanged';warnings:list=field(default_factory=list);checks:dict=field(default_factory=dict)
    version:str=VERSION
    def record(self):return asdict(self)

class Session:
    def __init__(self,llm,ctx):self.llm=llm;self.ctx=ctx;self.calls=0;self.usages=[];self.warnings=[]
    def ask(self,stage,prompt,data,cap=4500):
        for attempt in range(2):
            self.calls+=1
            try:raw=self.llm([dict(role='system',content=PROMPTS['r2']['shared']),dict(role='user',content=prompt+('\n上次未得到完整正文，请简短思考，直接输出完整JSON。' if attempt else '')+'\n'+json.dumps(data,ensure_ascii=False,separators=(',',':')))],stage=stage+('-format1' if attempt else ''),max_tokens=min(max(cap*2,8000),12000) if attempt else cap)
            except LLMError as exc:
                if not attempt and 'Missing/empty completion' in str(exc):continue
                raise
            self.usages.append(getattr(raw,'usage',getattr(self.llm,'last_usage',{})) or {})
            try:return parse(raw)
            except ValueError:
                if attempt:raise
    def classify(self,text,stage,only=None):
        rows=sentences(text);direct=detect(text,rows);ids={r['sid'] for r in rows};jobs=[]
        for kind in only or KINDS:
            chosen=[r for r in (rows[:RECALL['semantic_windows']['opening_sentences']] if kind=='empty_scene' else rows) if kind!='written_dialogue' or any(q in r['text'] for q in ('“','「','"'))]
            if not chosen:continue
            grouped={}
            for r in chosen:grouped.setdefault(r['pid'],[]).append({k:r[k] for k in ('sid','text')})
            ps=[dict(pid=p,sentences=v) for p,v in grouped.items()];examples=[e for e in CASES['cases'] if e['type']==kind][:2]
            for vote in range(3):jobs.append((kind,vote,ps,examples))
        def one(job):
            kind,vote,ps,examples=job;examples=list(examples);random.Random(314+vote).shuffle(examples)
            payload=dict(paragraphs=ps,character_cards=self.ctx.get('characters',[]),examples=examples,setting=self.ctx.get('setting',{}))
            prompt=PROMPTS['r2']['type_instructions'][kind]+'\n'+PROMPTS['r2']['style_format']+'\n本次只准type='+kind+'；审读角度='+str(vote)
            def read(parts,suffix):
                try:
                    value=self.ask(stage+'-'+kind+'-'+str(vote)+suffix,prompt,dict(payload,paragraphs=parts))
                    issues=value.get('issues')
                    if isinstance(issues,list):
                        for x in issues:
                            try:x['severity']=max(1,min(3,int(x.get('severity') or 2)))
                            except (ValueError,TypeError):x['severity']=2
                            x['type']=x.get('type') or kind
                    if not isinstance(issues,list) or any(x.get('sid') not in ids or x.get('type')!=kind or type(x.get('severity')) is not int or not 1<=x['severity']<=3 for x in issues):raise ValueError('invalid style output')
                    missing=missing_paragraphs(value,parts)
                    if missing:
                        if len(parts)==1 and '-missing' in suffix:raise ValueError('incomplete paragraph coverage after retry')
                        step=1 if '-missing' in suffix else 10
                        return issues+[issue for j in range(0,len(missing),step) for issue in read(missing[j:j+step],suffix+'-missing'+str(j))]
                    return issues
                except Exception as exc:
                    if ('422' not in str(exc) and '413' not in str(exc)) or len(parts)<=1:raise
                    step=10 if len(parts)>10 else 1
                    return [issue for j in range(0,len(parts),step) for issue in read(parts[j:j+step],suffix+'-p'+str(j))]
            try:return kind,read(ps,''),None
            except Exception as exc:return kind,[],type(exc).__name__+':'+str(exc)[:100]
        votes={};unknown=[]
        with ThreadPoolExecutor(max_workers=6) as pool:
            for kind,issues,error in pool.map(one,jobs):
                if error:unknown.append(dict(type=kind,error=error))
                else:votes.setdefault(kind,[]).append(issues)
        model=[]
        for kind,reads in votes.items():
            if len(reads)!=3:continue
            tally={}
            for read in reads:
                for sid in {x['sid'] for x in read}:tally.setdefault(sid,[]).append(next(x for x in read if x['sid']==sid))
            for sid,hits in tally.items():
                if len(hits)>=2:model.append(dict(next(r for r in rows if r['sid']==sid),type=kind,severity=sorted(x['severity'] for x in hits)[(len(hits)-1)//2],source='model',reason=[x.get('reason') for x in hits]))
        return merge_hits(direct,model),unknown
    def diagnostics(self,text):
        try:
            ps=paragraphs(text)
            data=self.ask('diagnostics',PROMPTS['runtime']['diagnostics'],dict(paragraphs=ps,setting=self.ctx.get('setting')),4500)
            data['status']='checked' if set(data.get('checked_pids',[]))=={p['pid'] for p in ps} and isinstance(data.get('facts'),list) and isinstance(data.get('chapter'),dict) else 'incomplete'
            return data
        except Exception as exc:return dict(status='unknown',error=type(exc).__name__)

def score(hits,text):
    severity={}
    for h in hits:severity[h['sid']]=max(severity.get(h['sid'],0),h.get('severity',2))
    return round(1000*sum(severity.values())/max(1,count(text)),4)

def final_adoption(original,text,edits,before,after,ctx):
    changed={e['pid'] for e in edits};known={(h['pid'],h['type'],h['text']) for h in before}
    bad={h['pid'] for h in after if h['pid'] in changed and (h['pid'],h['type'],h['text']) not in known}
    text,edits,rolled=rollback_invalid(original,text,edits,ctx,bad)
    return text,edits,carry_hits(before,[h for h in after if h['pid'] not in rolled],text,original),rolled

def _entities(ctx,inventory):
    cards=ctx.get('characters',[]);cards=list(cards.values()) if isinstance(cards,dict) else cards
    return [c.get('name') for c in cards if isinstance(c,dict) and c.get('name')]+[e for p in inventory.get('paragraphs',[]) for e in p.get('entities',[]) if isinstance(e,str)]+[e for e in inventory.get('entities',[]) if isinstance(e,str)]

def _factcheck(session,stage,items,ctx):
    items=[dict(item,before=neutral_tags(item['before']),after=neutral_tags(item['after'])) for item in items]
    raw=session.ask(stage,PROMPTS['runtime']['facts_preservation'],dict(items=items,characters=ctx.get('characters',[]),must_keep=ctx.get('facts',[]),ending=ctx.get('ending_image')),6000)
    return {x['id']:x for x in raw.get('checks',[]) if isinstance(x,dict) and isinstance(x.get('id'),str) and type(x.get('ok')) is bool}

def polish(text,ctx,llm):
    started=time.monotonic();ctx=dict(ctx);ctx.setdefault('word_range',(int(count(text)*.9+.999),int(count(text)*1.1)))
    session=Session(llm,ctx);result=Result(text);original=text;baseline=len(getattr(llm,'calls',[]));inventory={};current_hits=[]
    all_edits=[];accepted=original;ps=paragraphs(original);ctx['ending_image']=ps[-1]['text'] if ps else '';entities=_entities(ctx,inventory)
    try:
        before,unknown=session.classify(original,'recall22-initial');result.hits=before;current_hits=before
        if unknown:result.warnings+=unknown;raise ValueError('Initial detector incomplete; original retained')
        result.before_score=score(before,original);targets=[];by_sid={h['sid']:dict(h,types=[x['type'] for x in before if x['sid']==h['sid']]) for h in before}
        for hit in list(by_sid.values())[:RECALL['repair']['maximum_sentences']]:
            i=next(i for i,p in enumerate(ps) if p['pid']==hit['pid']);hit['context']=[p['text'] for p in ps[max(0,i-1):i+2]];targets.append(hit)
        raw=[];size=RECALL['repair']['packet_size']
        packets=[targets[i:i+size] for i in range(0,len(targets),size)]
        def propose(pair):
            i,packet=pair
            return session.ask('recall22-candidates-'+str(i),RECALL['prompts']['candidates'],dict(targets=packet,characters=ctx.get('characters',[])),7000)
        with ThreadPoolExecutor(max_workers=3) as pool:
            for data in pool.map(propose,enumerate(packets)):raw.extend(data.get('candidates',[]))
        edits=[]
        for hit in targets:
            choices=next((r.get('choices',[]) for r in raw if isinstance(r,dict) and r.get('sid')==hit['sid']),[])
            choices=[c for c in choices if isinstance(c,str) and c!=hit['text'] and '\n' not in c and '\r' not in c]
            view=re.sub(r'“[^”]*”|「[^」]*」|"[^"\n]*"',lambda m:' '*len(m[0]),hit['text'])
            for item in RECALL['patterns']:
                if item.get('replacement_regex'):
                    spans=list(re.finditer(item['replacement_regex'],view));cleaned=hit['text']
                    for m in reversed(spans):cleaned=cleaned[:m.start()]+cleaned[m.end():]
                    if spans:choices.insert(0,cleaned)
            if neutral_tags(hit['text'])!=hit['text']:choices=[neutral_tags(hit['text'])]
            candidates=[]
            for choice in dict.fromkeys(choices):
                if invariant(hit['text'],choice,ctx,entities):continue
                revised=replace(original,[dict(hit,replacement=choice)])
                if invariant(original,revised,ctx,entities,True):continue
                candidates.append(dict(text=choice,rule_score=score(detect(revised),revised)))
            if not candidates:continue
            chosen=min(candidates,key=lambda c:(c['rule_score'],distance(hit['text'],c['text'])))
            if chosen['rule_score']>score(detect(original),original):continue
            edits.append(dict(start=hit['start'],end=hit['end'],before=hit['text'],replacement=chosen['text'],pid=hit['pid'],sid=hit['sid'],types=hit['types'],round=1,candidates=candidates))
        revised=replace(original,edits)
        if invariant(original,revised,ctx,entities,True):raise ValueError('Whole chapter invariant failed')
        revised_ps={p['pid']:p['text'] for p in paragraphs(revised)};changed={e['pid'] for e in edits}
        fact_items=[dict(id=p['pid'],before=p['text'],after=revised_ps[p['pid']]) for p in ps if p['pid'] in changed]
        with ThreadPoolExecutor(max_workers=3) as pool:
            facts=pool.submit(_factcheck,session,'recall22-facts',fact_items,ctx) if edits else None
            review=pool.submit(session.classify,revised,'recall22-recheck') if edits else None
            diagnostic=pool.submit(session.diagnostics,original)
            checks=facts.result() if facts else {};after,unknown=review.result() if review else (before,[]);diagnostics=diagnostic.result()
        if unknown:result.warnings+=unknown;raise ValueError('Recheck incomplete; original retained')
        bad={p['id'] for p in fact_items if not checks.get(p['id'],{}).get('ok')}
        if bad:
            revised,edits,_=rollback_invalid(original,revised,edits,ctx,bad);result.warnings.append(dict(rollback='facts',paragraphs=sorted(bad)))
        after=carry_hits(before,after,revised,original)
        revised,edits,after,rolled=final_adoption(original,revised,edits,before,after,ctx)
        if rolled:result.warnings.append(dict(rollback='new_style',paragraphs=rolled))
        accepted=revised;all_edits=edits;current_hits=after
        ctx['dialogue_targets']=[h['pid'] for h in after if h['type']=='written_dialogue']
        if ctx.get('dialogue_mode') in ('scene','actor') and (ctx.get('force_dialogue') or ctx['dialogue_targets']):
            from .dialogue import rewrite
            inventory=session.ask('inventory',PROMPTS['runtime']['inventory'],dict(paragraphs=ps,target_pids=ctx['dialogue_targets'],characters=ctx.get('characters',[])),6500)
            voiced,voice_edits=rewrite(session,accepted,inventory,ctx,ctx['dialogue_mode'],entities)
            if voice_edits and not invariant(original,voiced,ctx,entities,True):
                voice_hits,voice_unknown=session.classify(voiced,'dialogue-final')
                if not voice_unknown and score(voice_hits,voiced)<=score(current_hits,accepted):accepted,all_edits,current_hits=voiced,all_edits+voice_edits,voice_hits
        result.status='polished' if accepted!=original else 'unchanged'
    except Exception as exc:
        result.warnings.append(dict(error=type(exc).__name__+':'+str(exc)[:200]));result.status='partial' if all_edits else 'retained';diagnostics=dict(status='unknown')
    if invariant(original,accepted,ctx,entities,True):accepted=original;all_edits=[];result.status='retained'
    result.text=accepted;result.edits=all_edits;result.after_score=score(current_hits,accepted) if result.before_score is not None else None
    result.checks=dict(diagnostics=diagnostics,entities=entities,metrics_before=features(original),metrics_after=features(accepted),remaining_hits=current_hits,dialogue_mode=ctx.get('dialogue_mode'),original_chars=count(original),final_chars=count(accepted),programmatic=invariant(original,accepted,ctx,entities,True),ending_image=ctx['ending_image'],facts='preservation_model_checked' if all_edits else 'original retained')
    entries=getattr(llm,'calls',[])[baseline:] if hasattr(llm,'calls') else None;usages=[r.get('usage') or {} for r in entries] if entries is not None else session.usages
    result.calls=len(entries) if entries is not None else session.calls;result.prompt_tokens=sum(u.get('prompt_tokens') or 0 for u in usages);result.completion_tokens=sum(u.get('completion_tokens') or 0 for u in usages);result.seconds=round(time.monotonic()-started,3)
    result.checks['unknown_usage']=sum(u.get('prompt_tokens') is None or u.get('completion_tokens') is None for u in usages)
    return result
