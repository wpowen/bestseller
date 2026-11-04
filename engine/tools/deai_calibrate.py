"""INBOX19 r2: isolated paragraph/type calibration; not a production default."""
from concurrent.futures import ThreadPoolExecutor,as_completed
from collections import Counter
from copy import deepcopy
import json
import os
from pathlib import Path
import random
import re
import sys
import time
import yaml

ENGINE=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ENGINE))
from bookengine.client import Client,NetworkRetry,SYSTEM
from bookengine.storage import load,save,now
from bookengine.text import chinese_count,_paragraph_spans
from deai_rules import features,detect,merge_hits,sentence_rows,FACT_TYPES

WORK=ENGINE.parent;BOOKS=WORK.parent/'engine-books';ROOT=BOOKS/'blind-5';OLD=ROOT/'private/calibration';PRIVATE=ROOT/'private/calibration-r2';ENV=WORK/'../../.env';DATA=ENGINE/'deai/data'
CASES=yaml.safe_load((DATA/'ai_flavor_cases.yaml').read_text());PROMPTS=yaml.safe_load((DATA/'deai_prompts.yaml').read_text())
MODEL_TYPES=('verb_mismatch','empty_scene','cool_micro_action','written_dialogue','character_label','emotion_overload')
RATE=Path('/tmp')/f'bookengine-deai-rate-{os.getuid()}.json'
os.environ.setdefault('BOOKENGINE_RATE_FILE',str(RATE))

class CalibrationClient(Client):
    """Native 15-minute network policy plus a bounded retry for other HTTP errors."""
    def request(self,stage,prompt,cap,effort,attempt):
        try:return super().request(stage,prompt,cap,effort,attempt)
        except RuntimeError:
            entry=self.calls[-1] if self.calls else {}
            if entry.get('status')=='http_error' and sum(c.get('http_status')==entry.get('http_status') for c in self.calls if c['stage']==stage)<3:
                raise NetworkRetry('http_recheck_'+str(entry.get('http_status'))) from None
            raise

def prepare():
    PRIVATE.mkdir(parents=True,exist_ok=True,mode=0o700);os.chmod(PRIVATE,0o700)
    path=PRIVATE/'manifest.json'
    if path.exists():return load(path)
    prior=load(OLD/'sources-final.json');gold=load(OLD/'summary.json')['named_labels']
    # Stratify before new requests: both semantic and surface cases remain held out.
    example_indexes={0,3,6,7};examples=[];heldout=[]
    for i,row in enumerate(gold):
        item=deepcopy(row);item.pop('hit',None)
        for match in item['matches']:match.pop('hit',None)
        (examples if i in example_indexes else heldout).append(item)
    sources=prior['sources']
    for spec in sources:
        folder=PRIVATE/spec['id'];folder.mkdir(exist_ok=True,mode=0o700)
        text=(OLD/spec['id']/'source.md').read_text();save(folder/'source.md',text);save(folder/'sentences.json',sentence_rows(text))
    # Only these training labels may appear as few-shots; heldout originals never do.
    alternatives=['她摸了摸墙，指尖湿了一片。','墙皮脱得一块一块的。','她把报价单翻回来，又问了一遍能少多少。','灯管发蓝，桌沿照出一道淡淡的光。']
    training=[dict(type=g['type'],original=g['seed'],human_alternative=a,why='用户已点名的表达问题；替换仅是自写示意，不作为待修改书稿事实。') for g,a in zip(examples,alternatives)]
    manifest=dict(created=now(),sources=sources,examples=examples,heldout=heldout,training=training,gate=dict(min_recall=.8,max_anchor_candidates=3,ranking='anchor no higher than GPT; strictly lower than Gemini and each MiniMax stored sample',score='1000 * sum(max severity per unique confirmed sentence) / Han; deterministic rules OR 2/3 semantic votes; facts never counted'),split_note='本轮提示留出，样本在r1已出现，非全新外部盲测；4例/5留出，冻结后不调规则或阈值')
    held_phrases=[g['seed'] for g in heldout]
    assert not any(p in json.dumps(training,ensure_ascii=False) for p in held_phrases)
    save(path,manifest);return manifest

def rule_candidates(rows):
    return detect('',rows=rows)

def validate(raw,ids,kind,pids=None):
    if not isinstance(raw,dict) or not isinstance(raw.get('issues'),list):raise ValueError('missing issues')
    if pids is not None and (not isinstance(raw.get('checked_pids'),list) or set(raw['checked_pids'])!=set(pids)):raise ValueError('paragraph coverage incomplete')
    for x in raw['issues']:
        if not isinstance(x,dict) or x.get('sid') not in ids or type(x.get('severity')) is not int or not 1<=x['severity']<=3 or not x.get('reason'):raise ValueError('invalid issue location/severity/reason')
        allowed=FACT_TYPES if kind=='facts' else {kind}
        if x.get('type') not in allowed:raise ValueError('out-of-lane type')

def invoke(folder,name,instruction,payload,validator=None,cap=6500):
    client=CalibrationClient(folder/name,ENV,PROMPTS['r2']['shared'],timeout=180)
    prompt=instruction+'\n待审数据：\n'+json.dumps(payload,ensure_ascii=False,separators=(',',':'))
    save(folder/(name+'.input.json'),dict(prompt=prompt,temperature='provider_fixed_1'))
    return client.stage('judge',prompt,schema='完整JSON对象',validate=validator,max_attempts=2,cap=cap,effort='low')

def tasks(manifest):
    jobs=[]
    for spec in manifest['sources']:
        folder=PRIVATE/spec['id'];rows=load(folder/'sentences.json');text=(folder/'source.md').read_text();ids={x['sid'] for x in rows}
        cards=load(OLD/spec['id']/'cards.json')
        for kind in MODEL_TYPES:
            selected=rows
            if kind=='empty_scene':selected=[r for r in rows if r['start']<450]
            if kind=='written_dialogue':selected=[r for r in rows if any(q in r['text'] for q in ('“','「','"'))]
            groups={}
            for row in selected:groups.setdefault(row['pid'],[]).append({k:row[k] for k in ('sid','text')})
            if not groups:continue
            paragraphs=[dict(pid=k,sentences=v) for k,v in groups.items()];pids=list(groups)
            positives=[{k:e[k] for k in ('type','original','human_alternative','why')} for e in CASES['cases'] if e['type']==kind][:2]
            positives.extend(e for e in manifest['training'] if e['type']==kind)
            for vote in range(3):
                examples=deepcopy(positives);random.Random(19040+vote).shuffle(examples)
                payload=dict(paragraphs=paragraphs,character_cards=cards.get('characters',[]),setting=cards.get('setting',{}),dialogues=cards.get('dialogues',[]) if kind=='written_dialogue' else [],examples=examples)
                prompt=PROMPTS['r2']['type_instructions'][kind]+'\n'+PROMPTS['r2']['style_format']+'\n独立审读角度：'+PROMPTS['independent_sentence_reads']['perspectives'][vote]['id']+'\n本次只允许type='+kind
                jobs.append(dict(spec=spec,kind=kind,vote=vote,name=f'{kind}-{vote}',instruction=prompt,payload=payload,validator=lambda raw,ids=ids,kind=kind,pids=pids:validate(raw,ids,kind,pids)))
        payload=dict(paragraphs=[dict(pid=f'P{i}',text=text[a:b],sids=[r['sid'] for r in rows if r['pid']==f'P{i}']) for i,(a,b) in enumerate(_paragraph_spans(text),1)],characters=cards.get('characters',[]),setting=cards.get('setting',{}))
        jobs.append(dict(spec=spec,kind='facts',vote=0,name='facts',instruction=PROMPTS['r2']['facts'],payload=payload,validator=lambda raw,ids=ids:validate(raw,ids,'facts')))
        jobs.append(dict(spec=spec,kind='chapter',vote=0,name='chapter',instruction=PROMPTS['r2']['chapter'],payload=payload,validator=lambda raw:None if isinstance(raw,dict) and all(k in raw for k in ('opening_desire','ending_hook','procedural_plot','paragraphs')) else (_ for _ in ()).throw(ValueError('missing chapter fields'))))
    return jobs

def execute(job):
    folder=PRIVATE/job['spec']['id'];status=folder/(job['name']+'.status.json')
    try:
        value=invoke(folder,job['name'],job['instruction'],job['payload'],job['validator'])
        result=dict(valid=True,value=value,kind=job['kind'],vote=job['vote'])
    except Exception as exc:
        result=dict(valid=False,kind=job['kind'],vote=job['vote'],error=type(exc).__name__+':'+str(exc)[:150])
    save(status,result);print(job['spec']['id']+' '+job['name']+' '+('OK' if result['valid'] else 'UNKNOWN'),flush=True)
    return result

def summarize(manifest):
    results=[]
    for spec in manifest['sources']:
        folder=PRIVATE/spec['id'];text=(folder/'source.md').read_text();rows=load(folder/'sentences.json');rules=detect(text,rows);model=[];unknown=[];diagnostics={}
        for kind in MODEL_TYPES:
            states=[load(p) for p in (folder/f'{kind}-{v}.status.json' for v in range(3)) if p.exists()]
            if kind=='written_dialogue' and not states:continue
            if len(states)!=3 or not all(x['valid'] for x in states):unknown.append(kind);continue
            tally={}
            for state in states:
                for sid in {x['sid'] for x in state['value']['issues']}:
                    issue=next(x for x in state['value']['issues'] if x['sid']==sid);tally.setdefault(sid,[]).append(issue)
            for sid,votes in tally.items():
                if len(votes)>=2:
                    row=next(r for r in rows if r['sid']==sid);model.append(dict(row,type=kind,severity=sorted(x['severity'] for x in votes)[(len(votes)-1)//2],reason=[x['reason'] for x in votes],source='model',votes=len(votes)))
        for kind in ('facts','chapter'):
            path=folder/(kind+'.status.json');diagnostics[kind]=load(path) if path.exists() else dict(valid=False,error='missing')
        merged=merge_hits(rules,model);per_sid={}
        for hit in merged:per_sid[hit['sid']]=max(per_sid.get(hit['sid'],0),hit['severity'])
        result=dict(id=spec['id'],role=spec['role'],features=features(text,rows),rule_candidates=rules,model_confirmed=model,confirmed=merged,style_score=round(1000*sum(per_sid.values())/max(1,chinese_count(text)),4) if not unknown else None,unique_style_hits=len(per_sid),unknown_style=unknown,diagnostics=diagnostics)
        save(folder/'result.json',result);results.append(result)
    by={r['id']:r for r in results};anchor=next(r for r in results if r['role']=='claude')
    labels=[]
    for group in ('examples','heldout'):
        for label in manifest[group]:
            label=deepcopy(label);label['split']=group
            label['hit']=any(any(h['sid']==m['sid'] for h in by[m['id']]['confirmed']) for m in label['matches']);labels.append(label)
    held=[x for x in labels if x['split']=='heldout'];recall=sum(x['hit'] for x in held)/len(held)
    valid=all(not r['unknown_style'] and r['diagnostics']['chapter']['valid'] for r in results)
    ranking=valid and all(anchor['style_score']<r['style_score'] for r in results if r['role']=='gemini' or r['role'].startswith('blind-')) and anchor['style_score']<=next(r['style_score'] for r in results if r['role']=='gpt')
    gate=dict(valid=valid,ranking=ranking,heldout_recall=recall>=.8,anchor_upper_bound=anchor['unique_style_hits']<=3)
    calls=[c for path in PRIVATE.rglob('calls.json') for c in load(path)];known=[c for c in calls if c.get('usage')];pt=sum(c['usage'].get('prompt_tokens',0) for c in known);ct=sum(c['usage'].get('completion_tokens',0) for c in known)
    summary=dict(finished=now(),gate=gate,passed=all(gate.values()),heldout_hits=sum(x['hit'] for x in held),heldout_total=len(held),heldout_recall=recall,labels=labels,sources=results,calls=len(calls),status_counts=dict(Counter(c['status'] for c in calls)),prompt_tokens=pt,completion_tokens=ct,known_cost_usd=round((pt*.15+ct*.6)/1e6,9),unknown_usage=len(calls)-len(known),missing_usage_upper_usd=round(sum((c.get('input_bytes',0)*.15+c.get('output_token_cap',0)*.6)/1e6 for c in calls if not c.get('usage')),9),decision='continue implementation' if all(gate.values()) else 'STOP; report per-type misses')
    save(PRIVATE/'summary.json',summary);print(json.dumps({k:summary[k] for k in ('gate','passed','heldout_hits','heldout_total','heldout_recall','calls','known_cost_usd','unknown_usage')},ensure_ascii=False),flush=True)
    return summary

def retry_parts(manifest,jobs):
    pending=[];parents=[]
    for job in jobs:
        if job['kind'] in ('facts','chapter'):continue
        path=PRIVATE/job['spec']['id']/(job['name']+'.status.json')
        if not path.exists() or load(path)['valid']:continue
        parents.append(job);parts=job['payload']['paragraphs']
        for index in range(0,len(parts),10):
            selected=parts[index:index+10];sub=dict(job,name=job['name']+f'-parts-{index//10:02d}',payload=dict(job['payload'],paragraphs=selected))
            ids={r['sid'] for p in selected for r in p['sentences']};pids={p['pid'] for p in selected};kind=job['kind']
            sub['validator']=lambda raw,ids=ids,kind=kind,pids=pids:validate(raw,ids,kind,pids);pending.append(sub)
    print('Retry split into '+str(len(pending))+' small paragraph batches; completed calls reused',flush=True)
    with ThreadPoolExecutor(max_workers=16) as pool:
        for result in pool.map(execute,pending):pass
    isolated=[]
    for sub in pending:
        folder=PRIVATE/sub['spec']['id'];status=load(folder/(sub['name']+'.status.json'))
        if status['valid']:continue
        for para in sub['payload']['paragraphs']:
            leaf=dict(sub,name=sub['name']+'-'+para['pid'],payload=dict(sub['payload'],paragraphs=[para]));ids={r['sid'] for r in para['sentences']};kind=leaf['kind'];pids={para['pid']}
            leaf['validator']=lambda raw,ids=ids,kind=kind,pids=pids:validate(raw,ids,kind,pids);isolated.append(leaf)
    if isolated:
        print('Isolating '+str(len(isolated))+' previously rejected paragraphs; no text removed',flush=True)
        with ThreadPoolExecutor(max_workers=16) as pool:
            for result in pool.map(execute,isolated):pass
    for job in parents:
        folder=PRIVATE/job['spec']['id'];issues=[];covered=set();errors=[]
        for sub in [x for x in pending if x['spec']['id']==job['spec']['id'] and x['name'].startswith(job['name']+'-parts-')]:
            status=load(folder/(sub['name']+'.status.json'))
            if status['valid']:
                issues.extend(status['value']['issues']);covered.update(status['value']['checked_pids']);continue
            for leaf in [x for x in isolated if x['spec']['id']==job['spec']['id'] and x['name'].startswith(sub['name']+'-P')]:
                state=load(folder/(leaf['name']+'.status.json'))
                if state['valid']:issues.extend(state['value']['issues']);covered.update(state['value']['checked_pids'])
                else:errors.append(dict(paragraph=leaf['payload']['paragraphs'][0]['pid'],error=state['error']))
        path=folder/(job['name']+'.status.json');previous=load(path);save(folder/(job['name']+'.original-status.json'),previous)
        complete=covered=={p['pid'] for p in job['payload']['paragraphs']}
        save(path,dict(valid=complete,value=dict(issues=issues,checked_pids=sorted(covered)),kind=job['kind'],vote=job['vote'],retried_by_paragraphs=True,errors=errors,error=None if complete else 'paragraphs remain unknown'))
        print(job['name']+' coverage '+str(len(covered))+'/'+str(len(job['payload']['paragraphs']))+' '+str(complete),flush=True)

if __name__=='__main__':
    started=time.monotonic();manifest=prepare();jobs=tasks(manifest);save(PRIVATE/'job-count.json',dict(jobs=len(jobs),created=now()));print('r2 prepared '+str(len(jobs))+' jobs',flush=True)
    if '--retry-parts' in sys.argv:
        retry_parts(manifest,jobs);summarize(manifest)
    elif '--prepare-only' not in sys.argv:
        with ThreadPoolExecutor(max_workers=16) as pool:
            futures=[pool.submit(execute,job) for job in jobs]
            for f in as_completed(futures):f.result()
        summarize(manifest)
    print('elapsed_seconds='+str(round(time.monotonic()-started,3)),flush=True)
