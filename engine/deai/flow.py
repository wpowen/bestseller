"""Bounded local repair; every publication decision is against the original text."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass,field,asdict
from pathlib import Path
import json
import random
import time
import yaml
from .text import paragraphs,sentences,count,parse,replace,invariant,distance,rollback_invalid
from .rules import detect,features,merge_hits,FACT_TYPES,carry_hits
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
            chosen=[r for r in rows if (kind!='empty_scene' or r['start']<450) and (kind!='written_dialogue' or any(q in r['text'] for q in ('“','「','"')))]
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
    raw=session.ask(stage,PROMPTS['runtime']['facts_preservation'],dict(items=items,characters=ctx.get('characters',[]),must_keep=ctx.get('facts',[]),ending=ctx.get('ending_image')),6000)
    return {x['id']:x for x in raw.get('checks',[]) if isinstance(x,dict) and isinstance(x.get('id'),str) and type(x.get('ok')) is bool}

def _rank_semantic(session,candidates,stage):
    for kind in KINDS:
        selected=[c for c in candidates if kind in c['hit']['types']]
        if not selected:continue
        payload=dict(paragraphs=[dict(pid=c['id'],sentences=[dict(sid=c['id'],text=c['after'])]) for c in selected],examples=[e for e in CASES['cases'] if e['type']==kind][:2],character_cards=session.ctx.get('characters',[]))
        ids={c['id'] for c in selected};votes=[]
        try:
            def read(i):return session.ask(stage+'-'+kind+'-'+str(i),PROMPTS['r2']['type_instructions'][kind]+'\n'+PROMPTS['r2']['style_format']+'\n只准type='+kind,payload,6000)
            with ThreadPoolExecutor(max_workers=3) as pool:reads=list(pool.map(read,range(3)))
            for raw in reads:
                if set(raw.get('checked_pids',[]))!=ids or not isinstance(raw.get('issues'),list):raise ValueError('incomplete candidate recheck')
                if any(h.get('sid') not in ids or h.get('type')!=kind for h in raw['issues']):raise ValueError('invalid candidate type')
                votes.append({h['sid'] for h in raw['issues']})
            for c in selected:c['semantic_bad']=c.get('semantic_bad',False) or sum(c['id'] in v for v in votes)>=2
        except Exception:
            for c in selected:c['semantic_bad']=True

def polish(text,ctx,llm):
    started=time.monotonic();ctx=dict(ctx);ctx.setdefault('word_range',(int(count(text)*.9+.999),int(count(text)*1.1)));session=Session(llm,ctx);result=Result(text);original=text;accepted=text;current_hits=[]
    baseline=len(getattr(llm,'calls',[]));all_edits=[];inventory={};original_ps=paragraphs(text)
    try:
        before,unknown=session.classify(text,'initial');current_hits=before;result.hits=before;result.before_score=score(before,text) if not unknown else None
        if unknown:result.warnings+=unknown;raise ValueError('Initial detector incomplete; original retained')
        inventory=ctx.get('inventory') or (dict(paragraphs=[],ending_image=original_ps[-1]['text'],scenes=[]) if not before and not ctx.get('force_dialogue') else None) or session.ask('inventory',PROMPTS['runtime']['inventory'],dict(paragraphs=original_ps,target_pids=sorted({h['pid'] for h in before}),characters=ctx.get('characters',[])),6500)
        if not isinstance(inventory.get('paragraphs'),list) or not inventory.get('ending_image'):raise ValueError('Missing fact inventory/end image')
        ctx['ending_image']=inventory['ending_image'];entities=_entities(ctx,inventory)
        for round_no in range(0 if ctx.get('skip_local') else 2):
            hits=before if round_no==0 else after
            if not hits:break
            rows={r['sid']:r for r in sentences(text)};ps=paragraphs(text);by_sid={h['sid']:dict(rows[h['sid']],types=[x['type'] for x in hits if x['sid']==h['sid']]) for h in hits}
            targets=[]
            for h in by_sid.values():
                i=next(i for i,p in enumerate(ps) if p['pid']==h['pid']);targets.append(dict(h,context=[p['text'] for p in ps[max(0,i-1):i+2]]))
            packets=[targets[i:i+5] for i in range(0,len(targets),5)]
            def propose(pair):
                index,packet=pair
                return session.ask(f'r{round_no}-candidates-{index}',PROMPTS['runtime']['candidates'],dict(targets=packet,characters=ctx.get('characters',[]),facts=inventory,word_range=ctx['word_range']),6500)
            raw=[]
            with ThreadPoolExecutor(max_workers=3) as pool:
                for value in pool.map(propose,enumerate(packets)):raw.extend(value.get('candidates',[]))
            candidates=[]
            for item in raw:
                hit=by_sid.get(item.get('sid'));choices=item.get('choices',[])
                if not hit or not isinstance(choices,list):continue
                choices=list(dict.fromkeys(c for c in choices if isinstance(c,str)))
                if len(choices)<3:result.warnings.append(dict(sid=hit['sid'],warning='fewer than three distinct candidates'));continue
                for index,replacement in enumerate(choices[:3]):
                    if not isinstance(replacement,str) or replacement==hit['text'] or '\n' in replacement or '\r' in replacement:continue
                    p=next(p for p in ps if p['pid']==hit['pid']);new=p['text'][:hit['start']-p['start']]+replacement+p['text'][hit['end']-p['start']:]
                    if invariant(p['text'],new,ctx,entities):continue
                    candidates.append(dict(id=hit['sid']+'-'+str(index),sid=hit['sid'],before=p['text'],after=new,replacement=replacement,hit=hit,rule_score=score(detect(new),new)))
            if not candidates:break
            checks={}
            for i in range(0,len(candidates),12):checks.update(_factcheck(session,f'r{round_no}-facts-{i}',[{k:c[k] for k in ('id','before','after')} for c in candidates[i:i+12]],ctx))
            _rank_semantic(session,candidates,f'r{round_no}-candidate-style')
            edits=[]
            for sid,hit in by_sid.items():
                oldp=next(p for p in ps if p['pid']==hit['pid'])['text'];options=[c for c in candidates if c['sid']==sid and checks.get(c['id'],{}).get('ok')]
                if not options:continue
                options=[c for c in options if not c.get('semantic_bad')]
                if not options:continue
                chosen=min(options,key=lambda c:(c['rule_score'],distance(hit['text'],c['replacement'])))
                if chosen['rule_score']>=score(detect(oldp),oldp) and not any(k in KINDS for k in hit['types']):continue
                options=[dict(text=c['replacement'],rule_score=c['rule_score'],semantic_bad=c.get('semantic_bad'),facts=checks.get(c['id'])) for c in candidates if c['sid']==sid]
                edits.append(dict(start=hit['start'],end=hit['end'],replacement=chosen['replacement'],before=hit['text'],sid=sid,pid=hit['pid'],round=round_no+1,candidates=options,facts=checks[chosen['id']]))
            if not edits:break
            revised=replace(text,edits)
            if invariant(original,revised,ctx,entities,True):result.warnings.append(dict(round=round_no+1,rollback='whole_chapter_invariant'));break
            after,unknown=session.classify(revised,f'r{round_no}-recheck')
            after=carry_hits(hits,after,revised,text)
            changed_pids={e['pid'] for e in edits};previous_kinds={(h['pid'],h['type'],h['text']) for h in hits};bad={h['pid'] for h in after if h['pid'] in changed_pids and (h['pid'],h['type'],h['text']) not in previous_kinds}
            if unknown:result.warnings+=unknown;result.warnings.append(dict(round=round_no+1,rollback='recheck_unknown'));break
            good_edits=[e for e in edits if e['pid'] not in bad];revised=replace(text,good_edits)
            if bad:result.warnings.append(dict(round=round_no+1,rollback='new_style_issue',paragraphs=sorted(bad)))
            combined=_factcheck(session,f'r{round_no}-combined', [dict(id='whole',before=original,after=revised)],ctx)
            if not combined.get('whole',{}).get('ok'):result.warnings.append(dict(round=round_no+1,rollback='combined_facts'));break
            if invariant(original,revised,ctx,entities,True):break
            if bad:after,unknown=session.classify(revised,f'r{round_no}-rollback-recheck');after=carry_hits(hits,after,revised,text)
            if unknown:break
            text=revised;accepted=text;current_hits=after;all_edits.extend(good_edits)
        ctx['dialogue_targets']=[h['pid'] for h in current_hits if h['type']=='written_dialogue']
        if ctx.get('dialogue_mode') in ('scene','actor') and (ctx.get('force_dialogue') or ctx['dialogue_targets']):
            from .dialogue import rewrite
            voiced,voice_edits=rewrite(session,text,inventory,ctx,ctx['dialogue_mode'],entities)
            if voice_edits and not invariant(original,voiced,ctx,entities,True):
                voice_hits,voice_unknown=session.classify(voiced,'dialogue-final')
                voice_hits=carry_hits(current_hits,voice_hits,voiced,text)
                prior={(h['pid'],h['type'],h['text']) for h in current_hits};changed={e['pid'] for e in voice_edits}
                bad={h['pid'] for h in voice_hits if h['pid'] in changed and (h['pid'],h['type'],h['text']) not in prior}
                voiced,voice_edits,rolled=rollback_invalid(text,voiced,voice_edits,ctx,bad)
                if rolled:voice_hits,voice_unknown=session.classify(voiced,'dialogue-rollback-recheck');voice_hits=carry_hits(current_hits,voice_hits,voiced,text);result.warnings.append(dict(rollback='dialogue_paragraphs',paragraphs=rolled))
                if not voice_unknown and score(voice_hits,voiced)<=score(current_hits,text):
                    text=voiced;accepted=text;all_edits.extend(voice_edits);current_hits=voice_hits
                else:result.warnings.append(dict(rollback='dialogue_recheck'))
        result.warnings.extend(session.warnings)
        result.text=text;result.edits=all_edits;result.after_score=score(current_hits,text);result.status='polished' if text!=original else 'unchanged'
    except Exception as exc:
        result.warnings.append(dict(error=type(exc).__name__+':'+str(exc)[:200]));result.text=accepted;result.edits=all_edits;result.after_score=score(current_hits,accepted) if result.before_score is not None else None;result.status='partial' if all_edits else 'retained'
    if invariant(original,result.text,ctx,_entities(ctx,inventory),True):
        result.text=original;result.edits=[];result.status='retained';result.after_score=result.before_score
    if result.edits and result.before_score is not None:
        result.text,result.edits,current_hits,rolled=final_adoption(original,result.text,result.edits,result.hits,current_hits,ctx)
        result.after_score=score(current_hits,result.text);result.status='polished' if result.text!=original else 'unchanged'
        if rolled:result.warnings.append(dict(rollback='final_style',paragraphs=rolled))
    diagnostics=session.diagnostics(original)
    result.checks=dict(diagnostics=diagnostics,entities=_entities(ctx,inventory),metrics_before=features(original),metrics_after=features(result.text),remaining_hits=current_hits,dialogue_mode=ctx.get('dialogue_mode'),original_chars=count(original),final_chars=count(result.text),programmatic=invariant(original,result.text,ctx,_entities(ctx,inventory),True),ending_image=ctx.get('ending_image'),facts='preservation_model_checked' if result.edits else 'original retained')
    entries=getattr(llm,'calls',[])[baseline:] if hasattr(llm,'calls') else None;usages=[r.get('usage') or {} for r in entries] if entries is not None else session.usages
    result.calls=len(entries) if entries is not None else session.calls;result.prompt_tokens=sum(u.get('prompt_tokens') or 0 for u in usages);result.completion_tokens=sum(u.get('completion_tokens') or 0 for u in usages);result.seconds=round(time.monotonic()-started,3)
    result.checks['unknown_usage']=sum(u.get('prompt_tokens') is None or u.get('completion_tokens') is None for u in usages)
    return result
