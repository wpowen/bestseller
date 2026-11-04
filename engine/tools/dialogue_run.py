"""INBOX14 only: paired dialogue intervention and an opening/design intervention."""
from concurrent.futures import ThreadPoolExecutor,as_completed
from copy import deepcopy
import json
import os
from pathlib import Path
import random
import sys
import time
import uuid
import yaml

ENGINE=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ENGINE))
from bookengine import q1
from bookengine.client import MODEL
from bookengine.config import Book
from bookengine.longutil import ask
from bookengine.storage import load,save,now
from dialogue_client import ExperimentClient,totals
from dialogue_metrics import dialogues,metrics,apply_dialogue_edits,spoken_dialogue

WORK=ENGINE.parent;ROOT=WORK.parent/'engine-books/blind-2';PRIVATE=ROOT/'private';KEY=PRIVATE/'KEY.json';ENV=WORK/'../../.env'
OPENING='只强化开篇：第1章前300汉字同时给主角此刻的欲望和危机，前1000汉字完成首次可感的爽点或悬念兑现，随后必须有围观者/对手当场反应镜头。第2—3章延续行动后果。每章末钩是具体未决行动、危险或矛盾，不是感悟。前三章不是全书结尾，不提前揭尽长谜。'
SEEDS={
 'xuanhuan-power-fantasy':'新选题：被逐出剑坊的少年能听见兵器私下骂主人；替兵器办成一件小心愿，才可借它一招，每柄只能借一次。对手神剑不愿出鞘的理由很丢脸。主角要夺回宗门试剑入谷资格，救被扣在谷中的姐姐；不靠被动挨打，主动利用兵器脾气让傲慢对手吃亏，真正得到名额和资源。借招会磨掉兵器对某位旧主的一段记忆，不能无限借。少量固定人物，要有欲望和脾气。长线追问姐姐与旧剑主的关系。不要账册、契约、工单、登记、核验或程序争论主导冲突。',
 'suspense-mystery':'新选题：雨夜，女网约车司机接到失踪三个月姐姐的预约订单，目的地是明早拆除的旧游泳馆。来上车的是穿姐姐衣服、嘴硬不肯说真名的陌生少女；接单软件却显示姐姐本人已在后排。主角先要把活人从追车中带走，再主动查清是谁借姐姐身份叫车。现实犯罪悬疑，无超自然、双胞胎或系统故障敷衍解释；少女有自己的目的和脾气。全书长线是姐姐为何安排失踪。少量固定人物，冲突靠路线选择、藏人与试探推进，禁止核对文件、印章和开会主导。'}

def initialize():
    PRIVATE.mkdir(parents=True,exist_ok=True,mode=0o700);os.chmod(PRIVATE,0o700)
    if KEY.exists():
        key=load(KEY)
        if key.get('task')!='2026-11-03-14':raise RuntimeError('Existing experiment belongs to another task')
        return key
    entries=[]
    for genre,premise in SEEDS.items():
        family=uuid.uuid4().hex
        source=PRIVATE/'runs'/family/uuid.uuid4().hex;intervention=PRIVATE/'runs'/family/uuid.uuid4().hex
        derived=PRIVATE/'dialogue'/uuid.uuid4().hex
        for path in (source,intervention,derived):path.mkdir(parents=True,exist_ok=True,mode=0o700)
        for version,path,base in [('A',source,source),('B',derived,source),('C',intervention,intervention)]:entries.append(dict(genre=genre,premise=premise,version=version,path=str(path),source=str(base),final_path=str(PRIVATE/'dialogue'/path.name) if version=='C' else str(path)))
    random.SystemRandom().shuffle(entries)
    key=dict(task='2026-11-03-14',created=now(),budget_usd=.5,book_horizon=100,chapters_written=3,groups=dict(zip('甲乙丙丁戊己',entries)),protocol='A/B share identical final prose before dialogue intervention; C has the same seed premise, independently M3-planned. No main-flow changes.')
    save(KEY,key);os.chmod(KEY,0o600);return key

def english_cleanup(client,text,n):
    if not q1.english_hits(text):return text,[]
    hits=[dict(id=i,start=a,end=b,text=text[a:b]) for i,(a,b) in enumerate(q1._sentences(text),1) if q1.english_hits(text[a:b])]
    raw=ask(client,f'english-{n}','只把点名句的英文译成自然中文，消除中英重复，人物、情节、信息和引号边界不变。不改其他句。',[('原句',q1.pack(hits))],'{"edits":[{"id":1,"text":"中文原句"}]}',cap=1600,max_attempts=1,input_limit=10000)
    revised,edits=q1.sentence_edits(text,raw.get('edits',[]) if isinstance(raw,dict) else [],hits,allow_style=True)
    if q1.english_hits(revised):raise RuntimeError('Final English remains after one bounded repair')
    return revised,edits

def source_book(spec):
    out=Path(spec['path']);opening=spec['version']=='C';client=ExperimentClient(out,ENV,PRIVATE,'MiniMax-M3' if opening else MODEL,spec['genre']=='xuanhuan-power-fantasy')
    book=Book(spec['genre'],100,2200,spec['premise']);client.prefix=OPENING if opening else ''
    bible,threads,profile=q1.prepare(client,book,out,WORK/'config',design=False)
    client.prefix=(OPENING+'\n' if opening else '')+q1.pack(dict(reader_expectation=bible.get('reader_expectation'),anti_patterns=profile.get('anti_patterns',[])))[:650]
    state=q1.merge_state({}, {},bible);vol=q1.volume(client,bible,threads,1,100,state,[],[]);batch=q1.outlines(client,bible,threads,profile,vol,1,3,state,[],[],[])
    if len(batch)!=3 or any(not x.get('scenes') for x in batch):raise RuntimeError('Incomplete golden-three outline; checkpoint retained')
    data=yaml.safe_load(q1.DATA.read_text());data=dict(data,b=data['b']+('\n'+OPENING if opening else ''));rows=[]
    for n in range(1,4):
        row=q1.chapter(client,book,bible,threads,profile,vol,batch[n-1],state,[],rows,out,'b',data)
        path=out/f'chapter-{n:03d}.body.md';before=path.read_text();text,edits=english_cleanup(client,before,n)
        if q1.chinese_count(text)<300:raise RuntimeError('Empty or unusably short source chapter')
        if edits:
            spans=q1._sentences(before)
            for e in edits:
                a,b=spans[e['id']-1];row=json.loads(json.dumps(row,ensure_ascii=False).replace(json.dumps(before[a:b],ensure_ascii=False)[1:-1],json.dumps(e['text'],ensure_ascii=False)[1:-1]))
            row.update(chars=q1.chinese_count(text),style=q1.metrics(text),english=[],english_cleanup=edits);save(path,text);save(out/f'chapters/{n:03d}.json',row)
        rows.append(row);state=row['state'];save(out/'state.json',state);print('source chapter completed',n,flush=True)
    save(out/'result.json',dict(status='complete',chapters=3,book=book.record(),metrics=[metrics((out/f'chapter-{n:03d}.body.md').read_text()) for n in range(1,4)]))
    return spec

def narration(text):
    parts=[];last=0
    for d in dialogues(text):
        if spoken_dialogue(text,d):parts.append(text[last:d['start']]);last=d['end']
    parts.append(text[last:]);return parts

def polished_book(spec,replay=False):
    out=Path(spec['path']);source=Path(spec['source']);client=ExperimentClient(out,ENV,PRIVATE);bible=yaml.safe_load((source/'story_bible.yaml').read_text());rules=yaml.safe_load((ENGINE/'data/dialogue_experiment.yaml').read_text())
    details=[]
    for n in range(1,4):
        source_path=source/f'chapter-{n:03d}.body.md';text=source_path.read_text();target=out/f'chapter-{n:03d}.body.md'
        if target.exists() and not replay:continue
        checkpoint=out/f'dialogue-input-{n}.md'
        if checkpoint.exists() and checkpoint.read_text()!=text:raise RuntimeError('Dialogue input changed; do not reuse its edits')
        if not checkpoint.exists():save(checkpoint,text)
        cards=[{k:c.get(k) for k in ('name','role','voice','habits','taboos','desire','fear')} for c in bible['characters']]
        prompt=rules['polish_prompt']+'\n依据原卡为每人给口头语倾向、句长、粗口/文雅、方言偏好（无依据不硬加方言）；放voice_cards。本次所有卡要互相有区别。不要写机械口头禅。引用归属不确定时保留Unknown；可以从完整场景理解对话，但不要改发言人的身份。正文至少一处斗嘴/抬杠/玩笑，在playful_ids列原引文id。'
        targets=[dict(id=d['id'],text=d['text'],speaker='Unknown') for d in dialogues(text) if spoken_dialogue(text,d)]
        raw=ask(client,f'dialogue-{n}',prompt,[('原人物口吻卡',q1.pack(cards)),('只读完整正文',text),('可替换引文',q1.pack(targets))],'{"voice_cards":[],"playful_ids":[],"edits":[{"id":"原id","text":"引号内中文"}]}',cap=5000,max_attempts=1,input_limit=10000)
        by_id={d['id']:d for d in dialogues(text) if spoken_dialogue(text,d)}
        edits=[dict(e,original=by_id[e['id']]['text']) for e in raw.get('edits',[]) if isinstance(e,dict) and e.get('id') in by_id] if isinstance(raw,dict) else []
        for e in edits:
            value=e.get('text')
            if isinstance(value,str) and len(value)>=2 and (value[0],value[-1]) in (('“','”'),('「','」'),('『','』'),('"','"')):e['text']=value[1:-1]
        final,accepted=apply_dialogue_edits(text,edits)
        if narration(final)!=narration(text):raise RuntimeError('Narration changed during dialogue intervention')
        if q1.english_hits(final):raise RuntimeError('English remains after dialogue intervention')
        if not accepted:raise RuntimeError('Dialogue intervention returned no adoptable edits')
        save(target,final);detail=dict(chapter=n,accepted=len(accepted),before=metrics(text),after=metrics(final),narration_unchanged=True,playful_ids=raw.get('playful_ids',[]),voice_cards=raw.get('voice_cards',[]));save(out/f'{n:03d}.json',detail);details.append(detail)
        print('dialogue chapter completed',n,flush=True)
    save(out/'result.json',dict(status='complete',chapters=3))

def publish(key):
    blocks=['# 第二轮盲读：六组三章','每组连续三章。代号随机，正文后附程序诊断；指标不等于好读。']
    for label,spec in key['groups'].items():
        out=Path(spec.get('final_path',spec['path']));rows=[];blocks.append('## '+label+' · '+('玄幻' if spec['genre'].startswith('xuanhuan') else '悬疑'))
        for n in range(1,4):
            text=(out/f'chapter-{n:03d}.body.md').read_text()
            if q1.english_hits(text):raise RuntimeError('English detected in final blind output')
            blocks.extend([f'### 第{n}章',text]);m=metrics(text);c=m['pattern_counts'];rows.append(f"|{n}|{m['chars']}|{m['dialogue_ratio']:.1%}|{c['contrast']}/{c['parallelism']}/{c['aphorism']}|{c['long_sentence']}|{m['abstract_noun_density']:.1%}|{m['sentence_length_variance']:.2f}|")
        blocks.extend(['### 程序指标','\n'.join(['|章|汉字|对白比|转折/排比/格言命中|长对白句>35字|抽象词密度|对白句长方差|','|---|---:|---:|---|---:|---:|---:|',*rows])])
    blocks.append('指标只扫描引号内部；转折/排比/格言为词式候选。对白句长方差为整体代理，不虚构说话人归属，不判断作者身份或文学质量。')
    save(ROOT/'BLIND.md','\n\n'.join(blocks)+'\n')

def main():
    started=time.monotonic();key=initialize();os.environ['BOOKENGINE_RATE_FILE']='/tmp/bookengine-rate-'+str(os.getuid())+'.json'
    sources=[s for s in key['groups'].values() if s['version'] in ('A','C')]
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures=[pool.submit(source_book,s) for s in sources]
        for f in as_completed(futures):f.result()
    polished=[]
    for s in key['groups'].values():
        if s['version']=='B':polished.append(s)
        elif s['version']=='C':
            dest=PRIVATE/'dialogue'/Path(s['path']).name;dest.mkdir(parents=True,exist_ok=True,mode=0o700)
            # Public C must use the dialogue-treated result; source remains independently readable.
            polished.append(dict(s,source=s['path'],path=str(dest)))
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures=[pool.submit(polished_book,s) for s in polished]
        for f in as_completed(futures):f.result()
    finished=now()
    from datetime import datetime
    publish(key);result=dict(status='complete',groups=6,chapters=18,seconds=(datetime.fromisoformat(finished)-datetime.fromisoformat(key['created'])).total_seconds(),last_resume_seconds=round(time.monotonic()-started,3),finished=finished,**totals(PRIVATE));save(PRIVATE/'RESULT.json',result);print(json.dumps(result),flush=True)

if __name__=='__main__':main()
