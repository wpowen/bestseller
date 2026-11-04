"""Run only the user-authorized INBOX11 experiment; ordinary CLI owns writing."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import yaml
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from bookengine import q1
from bookengine.longutil import loose_object
from bookengine.endings import ending_candidates,apply_tail
from bookengine.export import export
from bookengine.q1_quality import merge_state,metrics
from bookengine.storage import load,save,now
from bookengine.batch import usage_report

ENGINE=Path(__file__).resolve().parents[1]
WORK=ENGINE.parent
ROOT=WORK.parent/'engine-books/growth-payoff-2'
PHASE=ROOT/'phase.json'


def regression():
    phase=load(PHASE)
    if phase.get('ending_regression',{}).get('accepted'):return
    out=WORK.parent/'engine-books/growth-payoff/suspense-50';path=out/'chapter-050.body.md';original=path.read_text();spans=q1._paragraph_spans(original)
    bible=yaml.safe_load((out/'story_bible.yaml').read_text());policy=q1.GROWTH['packs']['suspense-mystery'];bible=dict(bible,canon=dict(bible['canon'],solution_menu=policy['solution_menu'],solution_aliases=q1.GROWTH['solution_aliases'],final_volume_start=26))
    previous=load(out/'chapters/049.json')['state'];outline=load(out/'writing_outlines/050.json')
    outline.update(number=50,ending=True,ending_review='审读指出末两段新身份、本人笔迹和旧年代关联没有解释，留下新核心主谜。请结合原文判定并去掉这条新主谜，用既有人物动作或对白收束，只修改尾2段。',target_chars=2200,word_range=[1870,2700],actual_chars=q1.chinese_count(original))
    client=q1.QClient(out,WORK/'../../.env','',180);client.context_limit=10000;client.clip_context=True;client.relaxed=True
    begin=time.monotonic();raw,failed,_=q1.extract_state(client,'r2-ch50-finish',original,previous,bible,outline,[])
    revised,accepted=apply_tail(original,raw.get('tail')) if raw.get('opens_new_mainline') is True or raw.get('tail') else (original,False)
    final={};final_failed=True
    if accepted:final,final_failed,_=q1.extract_state(client,'r2-ch50-final-state',revised,previous,bible,outline,[],final=True)
    first_verdict=final.get('opens_new_mainline')
    if accepted and first_verdict is True:
        repair=q1.ask(client,'r2-ch50-ending-repair','只输出JSON {"tail":["倒数第二自然段","最后自然段"]}。只替换末两段，前文逐字保留。必须把纸上姓名、本人笔迹、十二年前落款为什么存在说清，接回本章已揭示的第十三席替换机制，不留下来源/写字人/年份的新谜，不创设新人、新规则、新任务。停在明确行动或对白；不写“无关紧要”来绕过解释。', [('原章',original),('第一次候选',raw.get('tail')),('未收束原因',final.get('ending_reason'))],cap=1700,input_limit=10000,max_attempts=1)
        repair=loose_object(repair) if isinstance(repair,str) else repair
        revised,accepted=apply_tail(original,repair.get('tail') if isinstance(repair,dict) else None)
        if accepted:final,final_failed,_=q1.extract_state(client,'r2-ch50-ending-final-state',revised,previous,bible,outline,[],final=True)
    accepted=accepted and final.get('opens_new_mainline') is False
    result=dict(first_final_verdict=first_verdict,candidates_before=ending_candidates(original,bible,outline),model_open_before=raw.get('opens_new_mainline'),accepted=accepted,model_open_after=final.get('opens_new_mainline'),prefix_unchanged=revised[:spans[-2][0]]==original[:spans[-2][0]],seconds=time.monotonic()-begin,initial_state_failed=failed,final_state_failed=final_failed,before_tail=[original[a:b] for a,b in spans[-2:]],after_tail=[revised[a:b] for a,b in q1._paragraph_spans(revised)[-2:]])
    if accepted:
        assert result['prefix_unchanged']
        row=load(out/'chapters/050.json');row.update(chars=q1.chinese_count(revised),style=metrics(revised),ending_repair=result,opens_new_mainline=False)
        if not final_failed:
            state,issues=q1.reduce_growth(previous,final,bible,revised,50,base=merge_state(previous,{k:v for k,v in final['state'].items() if v is not None},bible,50))
            row.update(state=state,issues=issues,type=final['type'],hook_type=final['hook_type'],payoff=final['payoff'],payoff_fulfilled=q1.payoff_ok(final['payoff'],row.get('payoff_type'),revised,bible['canon']['payoff_menu']))
            row.update(q1.observations(final,revised,50));row['solutions']=[x for x in state.get('solutions',[]) if x['chapter']==50];save(out/'state.json',state)
        save(path,revised);save(out/'chapters/050.json',row)
        rows=[load(out/f'chapters/{n:03d}.json') for n in range(1,51)];export(out,bible['title'],rows,[(out/f'chapter-{n:03d}.body.md').read_text() for n in range(1,51)])
        meta=load(out/'run.json');meta['usage']=usage_report(client.calls);save(out/'run.json',meta)
    phase['ending_regression']=result;save(PHASE,phase);print('ending_regression',json.dumps({k:v for k,v in result.items() if k not in ('before_tail','after_tail','candidates_before')},ensure_ascii=False),flush=True)



def refresh_cached_facts():
    """Reparse saved responses after the P-number/nested-field bug; no API."""
    phase=load(PHASE);fixed=phase.setdefault('offline_parse_repairs',[])
    for name in ('xuanhuan-50','xuanhuan-20','suspense-20'):
        entry=phase['books'][name];out=Path(entry['path']);bp=out/'story_bible.yaml'
        if not bp.exists():continue
        bible=yaml.safe_load(bp.read_text());meta=load(out/'run.json');policy=q1.GROWTH['packs'][meta['book']['prompt_pack']]
        bible=dict(bible,canon=dict(bible['canon'],solution_menu=policy['solution_menu'],solution_aliases=q1.GROWTH['solution_aliases'],final_volume_start=(meta['book']['chapters']-1)//25*25+1))
        dirty=False;threads=yaml.safe_load((out/'threads.yaml').read_text());profile=yaml.safe_load((WORK/'config/prompt_packs'/f"{meta['book']['prompt_pack']}.yaml").read_text()).get('q1',{})
        files=sorted((out/'chapters').glob('*.json'))
        for file in files:
            row=load(file);n=row['number']
            if n<=entry['base_chapters']:continue
            paths=[out/'steps'/f'r2-ch{n}-{suffix}.json' for suffix in ('final-state','finish')];stage=next((p for p in paths if p.exists()),None)
            if not stage:continue
            text=(out/f'chapter-{n:03d}.body.md').read_text();raw=q1.ground(load(stage),text);canon=bible['canon']
            valid=q1.valid_extract(raw,text,canon['hook_types'],canon['payoff_menu'],ending=n==meta['book']['chapters'])
            if not valid and row.get('state_recovered'):
                nextfile=out/'steps'/f'r2-ch{n+1}-finish.json';saved=load(nextfile) if nextfile.exists() else {}
                delta=q1.ground(saved.get('previous_delta'),text)
                if saved.get('_previous_complete') and q1.valid_extract(delta,text,canon['hook_types'],canon['payoff_menu']):raw=delta;valid=True
            repaired=bool(row.get('state_failed') and valid)
            prior=load(out/f'chapters/{n-1:03d}.json')['state'] if n>1 else merge_state({}, {},bible)
            duplicate_fixed=bool(valid and q1.reduce_growth(prior,raw,bible,text,n)[0]['injuries_count']!=row.get('injuries_count'))
            if duplicate_fixed:phase.setdefault('cross_ledger_repairs',[]).append(dict(book=name,chapter=n))
            if not dirty and not repaired and not duplicate_fixed:continue
            dirty=True;prior=load(out/f'chapters/{n-1:03d}.json')['state'] if n>1 else merge_state({}, {},bible)
            if valid:
                state,issues=q1.reduce_growth(prior,raw,bible,text,n,base=merge_state(prior,{k:v for k,v in raw['state'].items() if v is not None},bible,n))
                state['clock']=q1.roll_day(prior.get('clock'),state.get('clock'),text[-1200:],text)
                outline=load(out/f'writing_outlines/{n:03d}.json');state,thread_issues=q1.progress(state,raw,outline,bible,threads,profile)
                row.update(state=state,state_failed=False,issues=issues+thread_issues,injuries_count=state['injuries_count'],payoff_fulfilled=q1.payoff_ok(raw['payoff'],row['payoff_type'],text,canon['payoff_menu']),**{k:raw[k] for k in ('type','hook_type','payoff')},**q1.observations(raw,text,n))
                row['solutions']=[x for x in state.get('solutions',[]) if x['chapter']==n]
                if repaired:row.update(state_recovered=True,state_recovery_source='offline_parser');fixed.append(dict(book=name,chapter=n,source='saved_response',model_calls=0))
                save(stage,raw)
            else:
                state=q1.deepcopy(prior);row.update(state=state,state_failed=True,injuries_count=None)
            state['pending_payoff']=row['payoff_type'] if row.get('payoff_due') and not row.get('payoff_fulfilled') else None
            save(file,row)
        if dirty:
            save(out/'state.json',load(files[-1])['state']);phase.setdefault('offline_replay_from',{}).setdefault(name,fixed[-1]['chapter'])
    save(PHASE,phase)


def main():
    os.environ.update(BOOKENGINE_PHASE=str(PHASE),BOOKENGINE_BUDGET_ROOT=str(ROOT),BOOKENGINE_BUDGET_USD='0.6',BOOKENGINE_RATE_FILE='/tmp/bookengine-rate-'+str(os.getuid())+'.json',PYTHONDONTWRITEBYTECODE='1',PYTHONPATH=str(ENGINE))
    regression()
    refresh_cached_facts()
    if not load(PHASE).get('ending_regression',{}).get('accepted'):print('Ending regression unresolved; retained original tail and recorded failure; continuing authorized validation',flush=True)
    specs=[('xuanhuan-50',ENGINE/'examples/growth-payoff/xuanhuan-50.yaml',ROOT.parent/'growth-payoff/xuanhuan-50'),*[(n,ENGINE/f'examples/growth-payoff-2/{n}.yaml',ROOT/n) for n in ('xuanhuan-20','suspense-20')]]
    processes=[]
    for name,book,out in specs:
        meta=load(out/'run.json') if (out/'run.json').exists() else {}
        if meta.get('status')=='complete' or meta.get('status')=='stopped' and meta.get('revision',{}).get('name')==q1.REVISION:continue
        log=(ROOT/(name+'.log')).open('a');cmd=[sys.executable,'-u','-m','bookengine',str(book),'--q1','--style','b','--env',str(WORK/'../../.env'),'--out',str(out)]
        processes.append((name,subprocess.Popen(cmd,cwd=WORK,stdout=log,stderr=subprocess.STDOUT,env=os.environ.copy()),log,out))
    try:
        while any(p.poll() is None for _,p,_,_ in processes):
            snapshot=[]
            for name,p,_,out in processes:
                m=load(out/'run.json') if (out/'run.json').exists() else {};snapshot.append(dict(book=name,status=m.get('status','starting'),chapters=m.get('completed_chapters',0),exit=p.poll()))
            print(json.dumps(snapshot,ensure_ascii=False),flush=True);time.sleep(25)
    except KeyboardInterrupt:
        for _,p,_,_ in processes:
            if p.poll() is None:p.terminate()
        for _,p,_,_ in processes:p.wait()
        raise
    finally:
        for _,_,log,_ in processes:log.close()
    phase=load(PHASE);phase['finished']=now();save(PHASE,phase)
    print('phase finished; model work stopped',flush=True)

if __name__=='__main__':main()
