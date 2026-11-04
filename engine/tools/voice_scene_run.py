"""INBOX15/16 only. Independent one-chapter experiments; no default changes."""
from concurrent.futures import ThreadPoolExecutor,as_completed
from copy import deepcopy
import json
import os
from pathlib import Path
import random
import re
import sys
import uuid
import yaml

ENGINE=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ENGINE))
from bookengine import q1
from bookengine.client import MODEL
from bookengine.identity import validate_bible
from bookengine.longutil import ask
from bookengine.storage import load,save,now
from bookengine.text import _paragraph_spans,number_paragraphs,chinese_count
from dialogue_metrics import dialogues,spoken_dialogue,apply_dialogue_edits,metrics
from dialogue_diff import dialogue_diff
from voice_scene_checks import scene_hits,chapter_checks,outline_checks
from voice_scene_client import VoiceClient
from dialogue_client import totals

WORK=ENGINE.parent;ROOT=WORK.parent/'engine-books/blind-3';PRIVATE=ROOT/'private';KEY=PRIVATE/'KEY.json';ENV=WORK/'../../.env'
RULES=yaml.safe_load((ENGINE/'data/voice_scene_experiment.yaml').read_text());STRONG=yaml.safe_load((ENGINE/'data/dialogue_strong.yaml').read_text())
SCHEMA=json.dumps(dict(title='',premise='',characters=[dict(name='',role='',gender='',voice='',desire='',aliases=[],habits=[],taboos=[])],entity_owners=[],seat_map={},narrator_card=dict(attitude='',humor='',local_words=[],objects=[],banned_imagery=[]),outline=dict(number=1,title='',threads=[],hook='',scenes=[dict(id=1,goal='',obstacle='',choice='',opponent='',contest_id='',result='temporary'),dict(id=2,goal='',obstacle='',choice='',opponent='',contest_id='',result='final_win')])),ensure_ascii=False)

def call(client,stage,model,prompt,sections,schema=None,cap=7000):
    client.model=model
    return ask(client,stage,prompt,sections,schema,cap=cap,max_attempts=1,input_limit=10000,effort='low')

def initialize():
    PRIVATE.mkdir(parents=True,exist_ok=True,mode=0o700);os.chmod(PRIVATE,0o700)
    if KEY.exists():return load(KEY)
    old=load(WORK.parent/'engine-books/blind-2/private/KEY.json');groups=[];plans=[]
    for genre in ('xuanhuan-power-fantasy','suspense-mystery'):
        for version in ('B','C'):
            prior=next(s for s in old['groups'].values() if s['genre']==genre and s['version']==version)
            bible=yaml.safe_load((Path(prior['source'])/'story_bible.yaml').read_text())
            folder=PRIVATE/'t15'/uuid.uuid4().hex;folder.mkdir(parents=True,exist_ok=True,mode=0o700)
            plan=dict(path=str(folder),genre=genre,track='t15',model='MiniMax-M3' if version=='C' else MODEL,premise=prior['premise'],locked_names=[c['name'] for c in bible['characters']],golden=version=='C')
            plans.append(plan);out=folder/'variant';groups.append(dict(path=str(out),plan=str(folder),genre=genre,track='t15',version=version,body_model=MODEL,voice=False,strong=True,golden=version=='C'))
        folder=PRIVATE/'t16'/uuid.uuid4().hex;folder.mkdir(parents=True,exist_ok=True,mode=0o700)
        plan=dict(path=str(folder),genre=genre,track='t16',model=MODEL,premise=None,locked_names=[],golden=False);plans.append(plan)
        for version in ('F1','F2','M1','M2'):
            out=folder/uuid.uuid4().hex;groups.append(dict(path=str(out),plan=str(folder),genre=genre,track='t16',version=version,body_model=MODEL if version.startswith('F') else 'MiniMax-M3',voice=version!='F1',strong=version!='F1',golden=False))
    random.SystemRandom().shuffle(groups);key=dict(task='2026-11-03-15+16',created=now(),budget=.8,plans=plans,groups=dict(zip('甲乙丙丁戊己庚辛壬癸子丑',groups)))
    save(KEY,key);os.chmod(KEY,0o600);return key

def select_concept(client,genre):
    value=call(client,'select-living',MODEL,RULES['living_selection'],[('题材',genre)],'{"concepts":[{"pitch":"","occupation":"","place":"","era":"","custom":"","personal_need":"","scores":{"occupation":0,"place":0,"era":0,"custom":0,"personal_need":0,"abstract_dependency":0}}]}',cap=5000)
    rows=value.get('concepts',[]) if isinstance(value,dict) else []
    if len(rows)!=3:raise RuntimeError('Need three scored grounded concepts; checkpoint retained')
    for row in rows:
        scores=row.get('scores',{})
        if not all(isinstance(scores.get(k),(int,float)) and 0<=scores[k]<=10 for k in ('occupation','place','era','custom','personal_need','abstract_dependency')):raise RuntimeError('Missing grounded concept scores')
        row['weighted_score']=sum(scores[k]*w for k,w in [('occupation',.2),('place',.2),('era',.15),('custom',.15),('personal_need',.3)])-.3*scores['abstract_dependency']
    rows.sort(key=lambda r:-r['weighted_score']);save(client.out/'concepts.json',rows);return rows[0]

def plan_book(spec):
    out=Path(spec['path']);client=VoiceClient(out,ENV,PRIVATE,spec['track']);dest=out/'plan.json'
    if dest.exists():return load(dest)
    concept=select_concept(client,spec['genre']) if spec['track']=='t16' else dict(pitch=spec['premise'])
    pack=yaml.safe_load((WORK/'config/prompt_packs'/f"{spec['genre']}.yaml").read_text())
    materials=dict(reader_promise=pack.get('writing_profile_overrides',{}).get('market',{}),anti_patterns=pack.get('anti_patterns',[]))
    for attempt in (1,2):
        value=call(client,f'plan-{attempt}',spec['model'],RULES['plan']+(RULES['golden'] if spec['golden'] else ''),[('选定选题',q1.pack(concept)),('固定全名，禁止另取昵称',q1.pack(spec['locked_names'])),('题材承诺',q1.pack(materials)),('关键重试',attempt)],SCHEMA,cap=7500)
        try:
            for card in value.get('characters',[]):
                role=card.get('role','')
                if isinstance(role,str) and re.match(r'^(?:protagonist|主角|主人公)(?:[，,、：:；;]|$)',role):card.update(role_description=role,role='protagonist')
            validate_bible(value)
            assert all(c.get('gender') in ('男','女','male','female') and isinstance(c.get('aliases'),list) for c in value['characters'])
            assert not spec['locked_names'] or {c['name'] for c in value['characters']}<=set(spec['locked_names'])
            assert isinstance(value.get('narrator_card'),dict) and value['narrator_card'].get('attitude')
            assert len(value['outline']['scenes'])==2 and not outline_checks(value['outline'])
            break
        except (ValueError,AssertionError,KeyError,TypeError):
            if attempt==2:raise RuntimeError('Critical fixed cast/outline invalid twice; stop before prose')
    save(dest,value);return value

def paragraph_patch(text,edits,allowed):
    spans=_paragraph_spans(text);pairs=[];seen=set()
    for edit in edits if isinstance(edits,list) else []:
        pid=edit.get('pid',edit.get('paragraph'));value=edit.get('text')
        if isinstance(pid,str) and pid.isdigit():pid=int(pid)
        if type(pid) is int and pid in allowed and pid not in seen and 1<=pid<=len(spans) and isinstance(value,str) and value.strip():
            pairs.append((*spans[pid-1],value));seen.add(pid)
    for a,b,value in sorted(pairs,reverse=True):text=text[:a]+value+text[b:]
    return text,len(pairs)

def audit(client,text,plan,stage):
    raw=call(client,stage,MODEL,RULES['facts'],[('固定事实',q1.pack({k:plan.get(k) for k in ('characters','entity_owners','seat_map','outline')})),('编号正文',number_paragraphs(text))],'{"issues":[{"pid":1,"kind":"","problem":"","confirmed":true}]}',cap=2500)
    return [r for r in raw.get('issues',[]) if isinstance(r,dict) and r.get('confirmed') is True] if isinstance(raw,dict) else []

def prepare_prose(client,text,plan,spec):
    before=dict(program=chapter_checks(text,plan['characters']),scene=scene_hits(text),model=audit(client,text,plan,'facts-before'))
    flags=[*before['program']['confirmed'],*before['program']['candidates'],*before['model']]
    spans=_paragraph_spans(text)
    flags += [dict(paragraph=i,kind='english',text=text[a:b]) for i,(a,b) in enumerate(spans,1) if q1.english_hits(text[a:b])]
    if spec['voice']:flags+=before['scene']
    pids={x.get('pid',x.get('paragraph')) for x in flags};pids={int(x) for x in pids if str(x).isdigit()}
    if pids:
        raw=call(client,'paragraph-repair',MODEL,RULES['paragraph_repair'],[('指定问题',q1.pack(flags)),('固定人物/归属/座位',q1.pack({k:plan.get(k) for k in ('characters','entity_owners','seat_map')})),('原文P段',number_paragraphs(text))],'{"edits":[{"pid":1,"text":"仅该段替换文字"}]}',cap=7000)
        text,count=paragraph_patch(text,raw.get('edits',[]) if isinstance(raw,dict) else [],pids)
        before['adopted_paragraphs']=count
    save(client.out/'precheck.json',before);return text

def strong_dialogue(client,text,plan):
    all_rows=dialogues(text);targets=[d for d in all_rows if spoken_dialogue(text,d)];by_id={d['id']:d for d in targets};by_slot={i:d for i,d in enumerate(all_rows,1) if d['id'] in by_id};accepted=[];updated=text
    for attempt in (1,2):
        previous=dialogue_diff(text,updated)
        if attempt==2 and previous['substantive_ratio']>=.4:break
        prompt=STRONG['polish_prompt']+'\n本次目标80%对白整句换说法，至少40%实质距离>=25%且至少2个汉字改变；不是修标点、删虚词或仅删冗字。短句保意思但换表达；人物姓名、数字与否定条件不换。'
        done={r['slot'] for r in previous['lines'] if r['substantive']}
        pending=sorted(((i,d) for i,d in by_slot.items() if i not in done),key=lambda x:-chinese_count(x[1]['text']))[:80];pending.sort()
        cards=[{k:c.get(k) for k in ('name','voice','habits','taboos')} for c in plan['characters']]
        raw=call(client,f'strong-dialogue-{attempt}',MODEL,prompt,[('本批全部目标，槽位整数/原句',q1.pack([[i,d['text']] for i,d in pending])),('固定口吻卡',q1.pack(cards)),('只读情节摘要',q1.pack(plan['outline'])[:1200]),('未达标反馈',q1.pack({k:previous[k] for k in ('substantive_ratio','changed_lines','total_lines')}))],'{"edits":[{"slot":1,"text":"重新说的对白"}]}',cap=10000)
        edits=[]
        for e in raw.get('edits',[]) if isinstance(raw,dict) else []:
            if not isinstance(e,dict):continue
            slot=e.get('slot');slot=int(slot) if str(slot).isdigit() else None
            ident=e.get('id') if e.get('id') in by_id else by_slot[slot]['id'] if slot in by_slot else None
            if ident not in by_id:continue
            value=e.get('text')
            if isinstance(value,str) and len(value)>1 and (value[0],value[-1]) in (('“','”'),('"','"'),('「','」')):value=value[1:-1]
            edits.append(dict(id=ident,original=by_id[ident]['text'],text=value))
        merged={e['id']:e for e in [*accepted,*edits]};updated,accepted=apply_dialogue_edits(text,list(merged.values()))
    diff=dialogue_diff(text,updated)
    changed=[dict(slot=r['slot'],before=r['before'],after=r['after']) for r in diff['lines'] if r['before']!=r['after']]
    check=call(client,'dialogue-facts',MODEL,RULES['dialogue_facts'],[('固定人物事实',q1.pack(plan['characters'])),('改动对白',q1.pack(changed))],'{"issues":[{"slot":1,"problem":"","confirmed":true}]}',cap=3000)
    rejected={int(r['slot']) for r in check.get('issues',[]) if isinstance(r,dict) and r.get('confirmed') is True and str(r.get('slot')).isdigit()} if isinstance(check,dict) else set()
    old_rows=dialogues(text);good=[e for e in accepted if next(i for i,d in enumerate(old_rows,1) if d['id']==e['id']) not in rejected]
    updated,accepted=apply_dialogue_edits(text,good);diff=dialogue_diff(text,updated)
    save(client.out/'dialogue-diff.json',dict(diff,model_fact_flags=check,adopted=len(accepted)))
    if not diff['narration_unchanged'] or not diff['slot_alignment_unchanged']:raise RuntimeError('Dialogue edit changed narration/slots')
    return updated,diff

def run_variant(spec):
    out=Path(spec['path']);out.mkdir(parents=True,exist_ok=True,mode=0o700);done=out/'final.md'
    if done.exists() and (out/'result.json').exists():return load(out/'result.json')
    plan=load(Path(spec['plan'])/'plan.json');client=VoiceClient(out,ENV,PRIVATE,spec['track']);draft=out/'draft.md'
    sections=[('本章章纲',q1.pack(plan['outline'])),('出场人物卡',q1.pack(plan['characters'])),('上章结尾',''),('本章推进线',q1.pack(plan['outline'].get('threads',[])))]
    if spec['voice']:sections[1]=('人物口吻与叙述口吻卡',q1.pack(dict(characters=plan['characters'],narrator=plan['narrator_card'])))
    if spec['version']=='M2':
        openings=call(client,'openings-three','MiniMax-M3','同一章纲写3个彼此不同的开头，每个150–250汉字。落在人身上，不空镜、不讲规则，不抄外部文本。',[('正文约束',q1.pack(sections)),('口吻要求',RULES['voice'])],'{"openings":[{"id":1,"text":""}]}',cap=4000)
        choices=openings.get('openings',[]) if isinstance(openings,dict) else []
        if len(choices)==2:
            extra=call(client,'opening-missing','MiniMax-M3','已有两个成功开头，只补第3个不同的150–250字开头，不重写前两个；只第1章。',[('同一章纲',q1.pack(sections)),('已有两开头',q1.pack(choices)),('叙述口吻',RULES['voice'])],'{"text":"第三个开头"}',cap=1600)
            if isinstance(extra,dict) and extra.get('text'):choices=[*choices,dict(id=max(int(r['id']) for r in choices)+1,text=extra['text'])]
        if len(choices)!=3:raise RuntimeError('M2 needs three saved opening candidates')
        save(out/'opening-candidates.json',choices)
        choice=call(client,'opening-choice','MiniMax-M3','选择最想让读者往下读且最贴人物欲望/危机的一版，不按辞藻选，只回id及理由。',[('三开头',q1.pack(choices))],'{"id":1,"reason":""}',cap=900)
        selected=next((r['text'] for r in choices if r.get('id')==choice.get('id')),None)
        if not selected:raise RuntimeError('Opening selector did not choose a candidate')
        sections.append(('选定开头，从此原文接着写，不另起一遍',selected))
    prompt='写本章正文。目标2200汉字，本章1900—2300汉字（悬疑可2000—2400），只写第1章、两场戏，直接给正文，不输出标题，不写第2章。固定人物全名、性别、别名、归属与位置不变，同一场冲突只最终获胜一次。'
    if spec['voice']:prompt+='\n'+RULES['voice']
    if spec['golden']:prompt+='\n'+RULES['golden']
    if draft.exists():text=draft.read_text()
    else:
        text=q1.body_of(call(client,'body-draft',spec['body_model'],prompt,sections,cap=8500),client);save(draft,text)
    if not text:raise RuntimeError('Empty draft; retain checkpoint and stop')
    if not 1870<=chinese_count(text)<=2700:
        text=q1.body_of(call(client,'length-once',spec['body_model'],'只修本章字数至2000–2300汉字，保留所有人物、事实、动作场面与对白，不加剧情、不总结。',[('原稿',text)],cap=7500),client)
    text=prepare_prose(client,text,plan,spec);save(out/'before-dialogue.md',text)
    diff=None
    if spec['strong']:text,diff=strong_dialogue(client,text,plan)
    final=dict(program=chapter_checks(text,plan['characters']),scene=scene_hits(text),model=audit(client,text,plan,'facts-final'),english=q1.english_hits(text))
    if final['english']:raise RuntimeError('Final English detected; do not publish')
    result=dict(chars=chinese_count(text),metrics=metrics(text),dialogue={k:diff[k] for k in ('total_lines','changed_lines','substantive_ratio','mean_edit_distance','mean_literal_edit_distance','narration_unchanged')} if diff else None,final=final,within_band=1870<=chinese_count(text)<=2700)
    save(done,text);save(out/'result.json',result);print('one sample complete',flush=True);return result

def publish(key):
    blocks=['# 第三轮盲读','12个独立第1章。代号随机；方案、模型及干预对应仅存私有文件。先按读感判断，不据词频选优。']
    for label,spec in key['groups'].items():
        text=(Path(spec['path'])/'final.md').read_text();m=metrics(text);count=len(scene_hits(text))
        blocks.extend(['## '+label+' · '+('玄幻' if spec['genre'].startswith('xuanhuan') else '悬疑'),'### 第1章',text,f"汉字 {m['chars']}；引号内占比 {m['dialogue_ratio']:.1%}；写景/微动作候选段 {count}。"])
    save(ROOT/'BLIND.md','\n\n'.join(blocks)+'\n')

def main():
    key=initialize();os.environ['BOOKENGINE_RATE_FILE']='/tmp/bookengine-rate-'+str(os.getuid())+'.json'
    with ThreadPoolExecutor(max_workers=4) as pool:
        for f in as_completed([pool.submit(plan_book,s) for s in key['plans']]):f.result()
    with ThreadPoolExecutor(max_workers=4) as pool:
        for f in as_completed([pool.submit(run_variant,s) for s in key['groups'].values()]):f.result()
    publish(key);result=dict(status='complete',finished=now(),samples=12,**totals(PRIVATE));save(PRIVATE/'RESULT.json',result);print(json.dumps(result),flush=True)

if __name__=='__main__':main()
