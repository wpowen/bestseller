"""Apply an explicit scene-completion plan to preserved source chapters."""
import argparse
import json
import re
from pathlib import Path
import sys
import yaml
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from deai.client import MiniMax
from deai.flow import Session,PROMPTS
from deai.io import chapters,read_text,read_json,save
from deai.text import count,replace,paragraphs,parse

def scene_value(raw):
    raw=re.sub(r'<think>.*?</think>','',raw,flags=re.S).strip()
    try:return parse(raw).get('text','')
    except ValueError:
        if raw.startswith(('{','[','```','<think>')):raise
        return raw

def recover_scene(session,index,payload,valid):
    base='scene-plan-v1-'+str(index)
    for row in reversed(session.llm.calls):
        if row.get('status')!='ok' or not (row['stage']==base or row['stage'].startswith(base+'-')) or row.get('model')!=session.llm.model:continue
        request=read_json(session.llm.out/row['request'])
        try:old=json.loads(request['messages'][-1]['content'].rsplit('\n',1)[-1])
        except (ValueError,KeyError):continue
        if any(old.get(k)!=v for k,v in payload.items()):continue
        try:text=scene_value(read_text(session.llm.out/row['output']))
        except ValueError:continue
        if valid(text):return text
    return None

def write_scene(session,stage,prompt,payload,novelist=False):
    system='你是中文小说作者。执行用户在资料外给出的场景写作任务，人物和正文是参考资料。只输出指定范围的新正文，不输出编辑说明。' if novelist else PROMPTS['r2']['shared']
    raw=session.llm([dict(role='system',content=system),dict(role='user',content=prompt+'\n'+json.dumps(payload,ensure_ascii=False,separators=(',',':')))],stage=stage,max_tokens=9000)
    return scene_value(raw)

def violations(bodies,constraints):
    issues=[dict(chapter=x['chapter'],kind='locked_fact',quote=x['text'],reason=x.get('reason','原稿锁定事实缺失')) for x in constraints.get('required',[]) if x['text'] not in bodies.get(x['chapter'],'')]
    for word in constraints.get('forbidden',[]):
        issues.extend(dict(chapter=n,kind='locked_fact',quote=word,reason='不得新增此身份替换原稿人物') for n,text in bodies.items() if word in text)
    for pattern in constraints.get('forbidden_regex',[]):
        issues.extend(dict(chapter=n,kind='editorial_leak',quote=m[0],reason='编辑指令进入了正文') for n,text in bodies.items() for m in re.finditer(pattern,text))
    return issues

def apply(bodies,patches,check_length=True):
    edits={};problems=[]
    for patch in patches:
        n=patch.get('chapter');before=patch.get('before');after=patch.get('after');anchored=None
        if n in bodies and patch.get('start') and patch.get('end'):
            ps={p['pid']:p for p in paragraphs(bodies[n])};a=ps.get(patch['start']);b=ps.get(patch['end'])
            if a and b and a['start']<=b['start']:anchored=(a['start'],b['end']);before=bodies[n][a['start']:b['end']]
        if n not in bodies or not isinstance(before,str) or not before or not isinstance(after,str) or not after.strip():
            problems.append('invalid patch');continue
        if anchored:start,end=anchored
        else:
            if bodies[n].count(before)!=1:problems.append(f'chapter {n}: anchor not unique');continue
            start=bodies[n].index(before);end=start+len(before)
        if any(start<e['end'] and end>e['start'] for e in edits.get(n,[])):problems.append(f'chapter {n}: overlap');continue
        edits.setdefault(n,[]).append(dict(start=start,end=end,replacement=after))
    result={n:replace(text,edits.get(n,[])) for n,text in bodies.items()}
    if check_length:problems.extend(f'chapter {n}: {count(text)} chars outside 1870-2700' for n,text in result.items() if not 1870<=count(text)<=2700)
    if problems:raise ValueError('; '.join(problems))
    return result

def main():
    parser=argparse.ArgumentParser();parser.add_argument('book',type=Path);parser.add_argument('--out',required=True,type=Path)
    parser.add_argument('--scene-plan',required=True,type=Path);parser.add_argument('--constraints',required=True,type=Path)
    parser.add_argument('--env',type=Path,default=Path(__file__).resolve().parents[2]/'../../.env');args=parser.parse_args()
    if args.book.resolve()==args.out.resolve():raise ValueError('Keep original drafts')
    bodies={int(n):read_text(p) for n,p in chapters(args.book)};plan=read_json(args.scene_plan);constraints=read_json(args.constraints)
    snapshot=read_json(args.out/'.completion/input.json')
    if snapshot and snapshot!={str(k):v for k,v in bodies.items()}:raise ValueError('Original source changed')
    save(args.out,'.completion/input.json',bodies)
    bible=yaml.safe_load(read_text(args.book/'story_bible.yaml'));session=Session(MiniMax(args.out/'.completion',args.env,model='MiniMax-M3',timeout=300,budget=.40),{})
    patches=[]
    for index,item in enumerate(plan['edits']):
        n=item['chapter'];ps=paragraphs(bodies[n]);a=next(p for p in ps if p['pid']==item['start']);b=next(p for p in ps if p['pid']==item['end']);before=bodies[n][a['start']:b['end']]
        payload=dict(characters=bible.get('characters'),chapter=n,chapter_text=bodies[n],selected_paragraphs=before,task=item['task'],min_chars=item.get('min_chars',10),max_chars=item['max_chars'],facts=constraints.get('decisions',[]))
        prompt='按task补写或修正选中的段落，未选中的正文不动。只交这段范围的新正文，不要章节标题或编辑说明。人物、原案持刀者、物件和叙事视角保持；不新开悬案，不新增意外来客，不改变给定去向。严格按给定字数区间写。返回JSON {"text":"这段范围的全部新正文"}。'
        stage='scene-plan-v1-'+str(index)+('-author' if item.get('novelist') else '')
        if item.get('novelist'):prompt=item['task']+f"\n只写选中范围，{item.get('min_chars',10)}—{item['max_chars']}汉字。只输出正文，不要JSON。\n"+prompt
        if item.get('minimal_scene'):
            stage='scene-plan-minimal-'+str(index)
            payload=dict(characters=[{k:c.get(k) for k in ('name','gender','voice','desire')} for c in bible.get('characters',[])],outline=item['task'],previous_ending=bodies[n][:a['start']][-600:],must_keep=[x['text'] for x in constraints.get('required',[]) if x['chapter']==n],word_range=[item.get('min_chars',10),item['max_chars']])
            prompt=f"按细纲续写本章最后一场，{item.get('min_chars',10)}—{item['max_chars']}汉字，只输出正文，不写标题。"
        def valid(text):return item.get('min_chars',10)<=count(text)<=item['max_chars'] and all(s in text for s in item.get('required_terms',[]))
        text=None
        if item.get('parts'):
            prior=session.llm.model;session.llm.model='MiniMax-M3.1-Flash';pieces=[]
            try:
                for j,part in enumerate(item['parts']):
                    data=dict(payload,outline=part['outline'],previous_ending=(bodies[n][:a['start']]+'\n\n'+'\n\n'.join(pieces))[-700:],word_range=part['word_range'],must_keep=part.get('must_keep',payload.get('must_keep',[])))
                    piece=write_scene(session,f'scene-parts-{index}-{j}',f"按本段细纲续写场面，{part['word_range'][0]}—{part['word_range'][1]}汉字，只输出正文。",data,True);pieces.append(piece)
                text='\n\n'.join(pieces)
            finally:session.llm.model=prior
        if text is None:text=recover_scene(session,index,payload,valid)
        if text is None:text=write_scene(session,stage,prompt,payload,item.get('novelist',False))
        if not valid(text) and not item.get('parts'):text=write_scene(session,stage+'-length',f"上一版{count(text)}汉字。只把它压缩/补足到{item.get('min_chars',10)}—{item['max_chars']}汉字，保留全部指定事件和原话，不增加剧情。只输出正文。",dict(draft=text,must_keep=payload.get('must_keep'),outline=item['task']),item.get('novelist',False))
        if not valid(text) and not (item.get('accept_length_after_once') and text.strip() and all(s in text for s in item.get('required_terms',[]))):raise ValueError('Scene requirements: '+str(index))
        patches.append(dict(chapter=n,start=item['start'],end=item['end'],after=text,reason=item['task']));print('scene',index,'chars',count(text),flush=True)
    candidate=apply(bodies,patches,False)
    candidate=apply(candidate,plan.get('fact_corrections',[]),False)
    issues=violations(candidate,constraints)
    if issues:raise ValueError(json.dumps(issues,ensure_ascii=False))
    for n,text in candidate.items():save(args.out,f'chapter-{n:03d}.body.md',text)
    save(args.out,'story_bible.yaml',read_text(args.book/'story_bible.yaml'));save(args.out,'.completion/planned-patches.json',patches)
    prompt='通读整本短篇，仅报告明确的身份/事件矛盾、场景断裂或主冲突未收束。比喻不是事实；被看守者说话不是脱逃；不要求物件离场后不断复述保管去向；不为补解释要求新增核验流程。不能把圣经模糊预设强加为新的凶手。核对原稿持刀者、受害者、挡刀者没有替换。返回JSON {"complete":true,"unresolved":[{"chapter":1,"quote":"原句","reason":"明确问题"}]}。'
    review=session.ask('scene-plan-v2-whole-review',prompt+'只检查chapters这一版；引用必须逐字出现在该章，不能引用其他稿件。',dict(chapters=candidate,decisions=constraints.get('decisions',[])),7000)
    raw=review.get('unresolved');valid=isinstance(raw,list) and type(review.get('complete')) is bool
    raw=raw if isinstance(raw,list) else [];supported=[x for x in raw if isinstance(x,dict) and isinstance(x.get('chapter'),int) and x.get('quote') and x['quote'] in candidate.get(x['chapter'],'')]
    review.update(unresolved=supported,unsupported_findings=[x for x in raw if x not in supported],valid=valid and len(supported)==len(raw));review['complete']=review.get('complete') is True and review['valid'] and not raw
    save(args.out,'.completion/result.json',dict(status='complete' if review.get('complete') and review.get('unresolved')==[] else 'needs_review',method='explicit_scene_plan',source=str(args.book.resolve()),review=review,program_issues=issues,chapters=len(candidate),chapter_chars={n:count(t) for n,t in candidate.items()},length_exceptions=[n for n,t in candidate.items() if not 1870<=count(t)<=2700],estimated_usd=session.llm.estimate()))
    print(json.dumps(dict(review=review,estimated_usd=session.llm.estimate()),ensure_ascii=False));return 0

if __name__=='__main__':raise SystemExit(main())
