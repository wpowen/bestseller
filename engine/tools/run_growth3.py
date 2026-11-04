"""Only INBOX13: two new books and three bounded legacy ending regressions."""
import concurrent.futures
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import yaml

ENGINE=Path(__file__).resolve().parents[1]
WORK=ENGINE.parent
ROOT=WORK.parent/'engine-books/growth-payoff-3'
PHASE=ROOT/'phase.json'
sys.path.insert(0,str(ENGINE))
from bookengine import q1
from bookengine.batch import usage_report
from bookengine.endings import apply_scene,scene_context,english_hits
from bookengine.export import export
from bookengine.q1_quality import merge_state,metrics
from bookengine.storage import load,save,now


def initialize():
    ROOT.mkdir(parents=True,exist_ok=True,mode=0o700)
    if PHASE.exists():return
    books={}
    for name in ('xuanhuan-20','suspense-20'):
        out=ROOT/name;out.mkdir(exist_ok=True,mode=0o700);save(out/'calls.json',[])
        books[name]=dict(path=str(out.resolve()),base_calls=0,base_chapters=0,base_seconds=0)
    regressions={}
    for name,relative,n in [('old-xuanhuan-50','growth-payoff/xuanhuan-50',50),('old-suspense-20','growth-payoff-2/suspense-20',20),('old-suspense-50','growth-payoff/suspense-50',50)]:
        out=ROOT.parent/relative
        regressions[name]=dict(path=str(out.resolve()),chapter=n,base_calls=len(load(out/'calls.json')))
    offsets={entry['path']:entry['base_calls'] for entry in [*books.values(),*regressions.values()]}
    save(PHASE,dict(task='2026-11-02-13',started=now(),budget_usd=.4,books=books,regressions=regressions,base_calls=offsets,regression_results={}))


def regressions():
    for label,spec in load(PHASE)['regressions'].items():
        phase=load(PHASE)
        if (prior:=phase.get('regression_results',{}).get(label,{})).get('finished') and (prior.get('accepted') or prior.get('han_count_checked')):continue
        out=Path(spec['path']);n=spec['chapter'];target=out/f'chapter-{n:03d}.body.md'
        proposal=out/'steps'/f'r3-ending-{n}-proposal.json'
        client=q1.QClient(out,WORK/'../../.env','',180);client.context_limit=10000;client.clip_context=True;client.relaxed=True
        bible=yaml.safe_load((out/'story_bible.yaml').read_text());policy=q1.GROWTH['packs'][load(out/'run.json')['book']['prompt_pack']]
        bible=dict(bible,canon=dict(bible['canon'],solution_menu=policy['solution_menu'],solution_aliases=q1.GROWTH['solution_aliases'],final_volume_start=(n-1)//25*25+1))
        previous=load(out/f'chapters/{n-1:03d}.json')['state'];outline=load(out/f'writing_outlines/{n:03d}.json')
        cached=load(proposal) if proposal.exists() else None
        if cached and (cached.get('accepted') or cached.get('han_count_checked')):result=cached
        else:
            original=target.read_text();ctx=scene_context(original)
            outline.update(number=n,ending=True,force_scene=True,ending_review='指定回归：原结尾新开主谜或用长解释代替场面。只重写给定末场，600字内、每段150字内，行动和直接对白完成已有人物的冲突。不要新幕后、新地点、新身份或转交警方；不要说明机关原理。',actual_chars=q1.chinese_count(original),target_chars=2200,word_range=[1870,2700])
            raw,failed,_=q1.extract_state(client,f'r3-ending-{n}-finish',original,previous,bible,outline,[])
            revised,changed=apply_scene(original,raw.get('scene'));final={};final_failed=True
            if changed:final,final_failed,_=q1.extract_state(client,f'r3-ending-{n}-final-state',revised,previous,bible,outline,[],final=True)
            start=raw.get('scene',{}).get('start') if isinstance(raw.get('scene'),dict) else None
            offset=max(q1._paragraph_spans(original)[start-1][0],ctx['offset'] if ctx['boundary'] else 0) if changed else len(original)
            accepted=bool(changed and not final_failed and final.get('opens_new_mainline') is False and not english_hits(revised))
            result=dict(han_count_checked=True,prior_program_rejection=bool(cached),label=label,changed=changed,accepted=accepted,initial_failed=failed,final_failed=final_failed,model_open_before=raw.get('opens_new_mainline'),model_open_after=final.get('opens_new_mainline'),reason=final.get('ending_reason'),prefix_chars=offset,prefix_unchanged=revised[:offset]==original[:offset],before_chars=q1.chinese_count(original),after_chars=q1.chinese_count(revised),replacement=revised[offset:] if changed else '',final=final)
            save(proposal,result)
        if result['accepted']:
            current=target.read_text();revised=current[:result['prefix_chars']]+result['replacement'];final=result['final']
            row=load(out/f'chapters/{n:03d}.json');state,issues=q1.reduce_growth(previous,final,bible,revised,n,base=merge_state(previous,{k:v for k,v in final['state'].items() if v is not None},bible,n))
            row.update(chars=q1.chinese_count(revised),within_band=1870<=q1.chinese_count(revised)<=2700,style=metrics(revised),state=state,state_failed=False,issues=issues,opens_new_mainline=False,ending_repair_r3={k:v for k,v in result.items() if k not in ('replacement','final')},type=final['type'],hook_type=final['hook_type'],payoff=final['payoff'],payoff_fulfilled=q1.payoff_ok(final['payoff'],row.get('payoff_type'),revised,bible['canon']['payoff_menu']))
            row.update(q1.observations(final,revised,n));row['solutions']=[x for x in state.get('solutions',[]) if x['chapter']==n]
            save(target,revised);save(out/f'chapters/{n:03d}.json',row);save(out/'state.json',state)
            rows=[load(out/f'chapters/{i:03d}.json') for i in range(1,n+1)];export(out,bible['title'],rows,[(out/f'chapter-{i:03d}.body.md').read_text() for i in range(1,n+1)])
        result={k:v for k,v in result.items() if k not in ('final','replacement')};result.update(finished=now(),calls=len(client.calls)-spec['base_calls'],call_seconds=round(sum(c.get('seconds',0) or 0 for c in client.calls[spec['base_calls']:]),3))
        meta=load(out/'run.json');meta['usage']=usage_report(client.calls);save(out/'run.json',meta)
        phase=load(PHASE);phase.setdefault('regression_results',{})[label]=result;save(PHASE,phase)
        print('ending',json.dumps(result,ensure_ascii=False),flush=True)


def main():
    initialize()
    os.environ.update(BOOKENGINE_PHASE=str(PHASE),BOOKENGINE_BUDGET_ROOT=str(ROOT),BOOKENGINE_BUDGET_USD='.4',BOOKENGINE_RATE_FILE='/tmp/bookengine-rate-'+str(os.getuid())+'.json',PYTHONDONTWRITEBYTECODE='1',PYTHONPATH=str(ENGINE))
    children=[]
    for name,spec in load(PHASE)['books'].items():
        out=Path(spec['path']);meta=load(out/'run.json') if (out/'run.json').exists() else {}
        if meta.get('status') in ('complete','failed','stopped'):continue
        log=(ROOT/(name+'.log')).open('a')
        command=[sys.executable,'-u','-m','bookengine',str(ENGINE/f'examples/growth-payoff-3/{name}.yaml'),'--q1','--style','b','--env',str(WORK/'../../.env'),'--out',str(out)]
        children.append((name,subprocess.Popen(command,cwd=WORK,stdout=log,stderr=subprocess.STDOUT),log,out))
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        endings=pool.submit(regressions)
        try:
            while any(p.poll() is None for _,p,_,_ in children) or not endings.done():
                states=[]
                for name,p,_,out in children:
                    m=load(out/'run.json') if (out/'run.json').exists() else {};states.append(dict(book=name,status=m.get('status','starting'),chapters=m.get('completed_chapters',0),exit=p.poll()))
                print(json.dumps(states,ensure_ascii=False),flush=True);time.sleep(25)
            try:endings.result()
            except Exception as exc:
                phase=load(PHASE);phase['regression_error']=str(exc);save(PHASE,phase);print('ending error',str(exc),flush=True)
        except KeyboardInterrupt:
            for _,p,_,_ in children:
                if p.poll() is None:p.terminate()
            for _,p,_,_ in children:p.wait()
            raise
        finally:
            for _,_,log,_ in children:log.close()
    phase=load(PHASE);phase['finished']=now();save(PHASE,phase);print('INBOX13 model work stopped',flush=True)


if __name__=='__main__':main()
