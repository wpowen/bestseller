"""Bounded local repair; every publication decision is against the original text."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass,field,asdict
from pathlib import Path
import json
import random
import time
import yaml
from .text import paragraphs,sentences,count,parse,replace,invariant,distance,rollback_invalid
from .rules import detect,features,merge_hits,FACT_TYPES

DATA=Path(__file__).resolve().parent/'data'
RULES=yaml.safe_load((DATA/'deai_rules.yaml').read_text());PROMPTS=yaml.safe_load((DATA/'deai_prompts.yaml').read_text());CASES=yaml.safe_load((DATA/'ai_flavor_cases.yaml').read_text())
KINDS=('verb_mismatch','empty_scene','cool_micro_action','written_dialogue','character_label','emotion_overload')

@dataclass
class Result:
    text:str;hits:list=field(default_factory=list);edits:list=field(default_factory=list);before_score:float|None=None;after_score:float|None=None
    calls:int=0;prompt_tokens:int=0;completion_tokens:int=0;seconds:float=0;status:str='unchanged';warnings:list=field(default_factory=list);checks:dict=field(default_factory=dict)
    def record(self):return asdict(self)

class Session:
    def __init__(self,llm,ctx):self.llm=llm;self.ctx=ctx;self.calls=0;self.usages=[];self.warnings=[]
    def ask(self,stage,prompt,data,cap=4500):
        for attempt in range(2):
            self.calls+=1
            raw=self.llm([dict(role='system',content=PROMPTS['r2']['shared']),dict(role='user',content=prompt+('\n只输出完整JSON。' if attempt else '')+'\n'+json.dumps(data,ensure_ascii=False,separators=(',',':')))],stage=stage+('-format1' if attempt else ''),max_tokens=min(max(cap*2,8000),12000) if attempt else cap)
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
                    if set(value.get('checked_pids',[]))!={p['pid'] for p in parts}:raise ValueError('incomplete paragraph coverage')
                    issues=value.get('issues')
                    if isinstance(issues,list):
                        for x in issues:
                            try:x['severity']=max(1,min(3,int(x.get('severity') or 2)))
                            except (ValueError,TypeError):x['severity']=2
                            x['type']=x.get('type') or kind
                    if not isinstance(issues,list) or any(x.get('sid') not in ids or x.get('type')!=kind or type(x.get('severity')) is not int or not 1<=x['severity']<=3 for x in issues):raise ValueError('invalid style output')
                    return issues
                except Exception as exc:
                    if ('422' not in str(exc) and '413' not in str(exc)) or len(parts)<=1:raise
                    step=10 if len(parts)>10 else 1
                    return [issue for j in range(0,len(parts),step) for issue in read(parts[j:j+step],suffix+'-p'+str(j))]
            try:return kind,read(ps,''),None
            except Exception as exc:return kind,[],type(exc).__name__+':'+str(exc)[:100]
        votes={};unknown=[]
        with ThreadPoolExecutor(max_workers=4) as pool:
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

def score(hits,text):
    severity={}
    for h in hits:severity[h['sid']]=max(severity.get(h['sid'],0),h.get('severity',2))
    return round(1000*sum(severity.values())/max(1,count(text)),4)

def _entities(ctx,inventory):
    cards=ctx.get('characters',[]);cards=list(cards.values()) if isinstance(cards,dict) else cards
    return [c.get('name') for c in cards if isinstance(c,dict) and c.get('name')]+[e for p in inventory.get('paragraphs',[]) for e in p.get('entities',[]) if isinstance(e,str)]+[e for e in inventory.get('entities',[]) if isinstance(e,str)]

def _factcheck(session,stage,items,ctx):
    raw=session.ask(stage,'只查事实是否逐项保留，不评文风。逐个比较原文和候选：实体身份/归属、事件动作与先后、数字、否定、条件、说话人、确定性与已知/未知不变；承诺不可变命令，回避/不管不可变成不知道，询问不可变成已知断言；不得把推测坐实。以原文为比较依据，人物卡仅辅助，不得用卡纠正原事实；不得凭空补事。章末画面不变，只可换叙述。只要无法确认就ok=false。返回{"checks":[{"id":"","ok":true,"reason":""}]}。',dict(items=items,characters=ctx.get('characters',[]),must_keep=ctx.get('facts',[]),ending=ctx.get('ending_image')),6000)
    return {x['id']:x for x in raw.get('checks',[]) if isinstance(x,dict) and isinstance(x.get('id'),str) and type(x.get('ok')) is bool}

def _rank_semantic(session,candidates,stage):
    for kind in KINDS:
        selected=[c for c in candidates if kind in c['hit']['types']]
        if not selected:continue
        payload=dict(paragraphs=[dict(pid=c['id'],sentences=[dict(sid=c['id'],text=c['after'])]) for c in selected],examples=[e for e in CASES['cases'] if e['type']==kind][:2],character_cards=session.ctx.get('characters',[]))
        ids={c['id'] for c in selected};votes=[]
        try:
            for i in range(3):
                raw=session.ask(stage+'-'+kind+'-'+str(i),PROMPTS['r2']['type_instructions'][kind]+'\n'+PROMPTS['r2']['style_format']+'\n只准type='+kind,payload,6000)
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
        inventory=ctx.get('inventory') or (dict(paragraphs=[],ending_image=original_ps[-1]['text'],scenes=[]) if not before and not ctx.get('force_dialogue') else None) or session.ask('inventory','只提取原文实际实体/事件，不把比喻当物体。entities用全章实际重要名词的逐字短名去重；paragraphs仅对target_pids给简短事件；非target不要逐段展开。ending_image不超过80字；scenes列场景连续pids。返回{"entities":[],"paragraphs":[{"pid":"P1","entities":[],"events":[]}],"ending_image":"","scenes":[]}。',dict(paragraphs=original_ps,target_pids=sorted({h['pid'] for h in before}),characters=ctx.get('characters',[])),6500)
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
                return session.ask(f'r{round_no}-candidates-{index}','只改命中原句，不动其余句。每句给3个不同候选，可把无事实总结删去，但不得删空整段。保留原句所有实际实体、数字、事件、条件和否定，不增情节，推测不能变定论，不知道不能变知道。去掉标签和诗化解释，改成符合人物的动作/对白/感觉。无换行。返回{"candidates":[{"sid":"S1","choices":["候选一","候选二","候选三"]}]}。',dict(targets=packet,characters=ctx.get('characters',[]),facts=inventory,word_range=ctx['word_range']),6500)
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
                    if not isinstance(replacement,str) or '\n' in replacement or '\r' in replacement:continue
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
                edits.append(dict(start=hit['start'],end=hit['end'],replacement=chosen['replacement'],before=hit['text'],sid=sid,pid=hit['pid'],round=round_no+1,candidates=len([c for c in candidates if c['sid']==sid]),facts=checks[chosen['id']]))
            if not edits:break
            revised=replace(text,edits)
            if invariant(original,revised,ctx,entities,True):result.warnings.append(dict(round=round_no+1,rollback='whole_chapter_invariant'));break
            after,unknown=session.classify(revised,f'r{round_no}-recheck')
            changed_pids={e['pid'] for e in edits};previous_kinds={(h['pid'],h['type'],h['text']) for h in hits};bad={h['pid'] for h in after if h['pid'] in changed_pids and (h['pid'],h['type'],h['text']) not in previous_kinds}
            if unknown:result.warnings+=unknown;result.warnings.append(dict(round=round_no+1,rollback='recheck_unknown'));break
            good_edits=[e for e in edits if e['pid'] not in bad];revised=replace(text,good_edits)
            combined=_factcheck(session,f'r{round_no}-combined', [dict(id='whole',before=original,after=revised)],ctx)
            if not combined.get('whole',{}).get('ok'):result.warnings.append(dict(round=round_no+1,rollback='combined_facts'));break
            if invariant(original,revised,ctx,entities,True):break
            if bad:after,unknown=session.classify(revised,f'r{round_no}-rollback-recheck')
            if unknown:break
            text=revised;accepted=text;current_hits=after;all_edits.extend(good_edits)
        if ctx.get('dialogue_mode') in ('scene','actor') and (ctx.get('force_dialogue') or any(h['type']=='written_dialogue' for h in result.hits)):
            from .dialogue import rewrite
            voiced,voice_edits=rewrite(session,text,inventory,ctx,ctx['dialogue_mode'],entities)
            if voice_edits and not invariant(original,voiced,ctx,entities,True):
                voice_hits,voice_unknown=session.classify(voiced,'dialogue-final')
                prior={(h['pid'],h['type'],h['text']) for h in current_hits};changed={e['pid'] for e in voice_edits}
                bad={h['pid'] for h in voice_hits if h['pid'] in changed and (h['pid'],h['type'],h['text']) not in prior}
                voiced,voice_edits,rolled=rollback_invalid(text,voiced,voice_edits,ctx,bad)
                if rolled:voice_hits,voice_unknown=session.classify(voiced,'dialogue-rollback-recheck');result.warnings.append(dict(rollback='dialogue_paragraphs',paragraphs=rolled))
                if not voice_unknown and score(voice_hits,voiced)<=score(current_hits,text):
                    text=voiced;accepted=text;all_edits.extend(voice_edits);current_hits=voice_hits
                else:result.warnings.append(dict(rollback='dialogue_recheck'))
        result.warnings.extend(session.warnings)
        result.text=text;result.edits=all_edits;result.after_score=score(current_hits,text);result.status='polished' if text!=original else 'unchanged'
    except Exception as exc:
        result.warnings.append(dict(error=type(exc).__name__+':'+str(exc)[:200]));result.text=accepted;result.edits=all_edits;result.after_score=score(current_hits,accepted) if result.before_score is not None else None;result.status='partial' if all_edits else 'retained'
    if invariant(original,result.text,ctx,_entities(ctx,inventory),True):
        result.text=original;result.edits=[];result.status='retained';result.after_score=result.before_score
    diagnostics={}
    try:
        diagnostics=session.ask('diagnostics','独立诊断，不改原稿，不计入AI味分。逐段看原稿事实：只有明确前后矛盾、日期算错、时代错位才报facts，类型仅continuity_conflict/date_arithmetic/era_conflict，先排除回忆/传闻/假界面/物主自知/超自然设定。另做章级表达检查：开头300字欲望阻碍、段落弃读风险、结尾具体画面、是否程序办事。只列需改的段，checked_pids覆盖全部。返回{"checked_pids":[],"facts":[{"pid":"P1","type":"date_arithmetic","reason":""}],"chapter":{"opening":"","risks":[],"ending":"","procedural":"","revision_route":"none/needs_upstream_revision"}}。本后处理不能改剧情，需整章重构的只提示上游。',dict(paragraphs=original_ps,setting=ctx.get('setting')),4500)
        if set(diagnostics.get('checked_pids',[]))!={p['pid'] for p in original_ps}:diagnostics['status']='incomplete'
    except Exception as exc:diagnostics=dict(status='unknown',error=type(exc).__name__)
    result.checks=dict(diagnostics=diagnostics,dialogue_mode=ctx.get('dialogue_mode'),original_chars=count(original),final_chars=count(result.text),programmatic=invariant(original,result.text,ctx,_entities(ctx,inventory),True),ending_image=ctx.get('ending_image'),facts='preservation_model_checked' if result.edits else 'original retained')
    entries=getattr(llm,'calls',[])[baseline:] if hasattr(llm,'calls') else None;usages=[r.get('usage') or {} for r in entries] if entries is not None else session.usages
    result.calls=len(entries) if entries is not None else session.calls;result.prompt_tokens=sum(u.get('prompt_tokens',0) for u in usages);result.completion_tokens=sum(u.get('completion_tokens',0) for u in usages);result.seconds=round(time.monotonic()-started,3)
    return result
