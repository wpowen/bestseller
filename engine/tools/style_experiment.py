#!/usr/bin/env python3
"""Paired historical chapter experiment; private assignments, real Flash calls."""
import argparse
import json
import os
from pathlib import Path
import random
import re
from types import SimpleNamespace
from unittest.mock import patch
import urllib.request

import yaml

from bookengine.batch import usage_report
from bookengine.client import Client, SYSTEM
from bookengine.config import Book, knowledge, style_rules
from bookengine.longmemory import long_context
from bookengine.longutil import paragraph_edits
from bookengine.storage import load, save
from bookengine.text import apply_paragraph_edits, chinese_count, number_paragraphs, pack, prose

STYLE_B = ('以场景—冲突—情绪变化组织小说，贴近主角的第三人称。只写正文，目标2200汉字。'
    '每段以3—6句为主，单句段不超过20%；不要靠合并空行伪装段落，要让感知、思绪和反应形成段落。'
    '每场至少3处写主角此刻想要什么、怕什么、怎么判断，用人物自己的念头而非作者讲道理。'
    '对白按口吻卡区分人物；允许幽默、斗嘴、脾气，禁止人人说口号式原则句。'
    '少写摆放、传递和核对物品的动作；常规手续一句带过，冲突靠选择、阻碍与情绪代价。'
    '不要把具体物件、可核验、证据或落地当作额外写作要求；保留原情节点与设定。'
    '章末以仍在发生的动作、意外或人物回应结束，禁止独立成段的感悟、格言与总结。')


def metrics(text):
    paragraphs = [p.strip() for p in re.split(r'\n\s*\n', text.strip()) if p.strip()]
    def sentences(p):
        p = re.sub(r'[。！？!?]([”」』\"])([^。！？!?]{0,8}(?:说|问|道|喊|答))[。！？!?]',r'\1\2。',p)
        return [s for s in re.split(r'[。！？!?]+[”」』\"]*', p) if chinese_count(s)]
    all_sentences = [s for p in paragraphs for s in sentences(p)]
    total = chinese_count(text)
    quoted = re.findall(r'“([^”]*)”|「([^」]*)」|"([^"\n]*)"', text)
    dialogue = sum(chinese_count(''.join(x)) for x in quoted)
    mental = re.compile(r'心想|想到|想起|想要|不想|不愿|害怕|怕|担心|盼|懊恼|后悔|觉得|意识到|明白|心里|心中|暗想|打算|犹豫|猜|判断')
    return dict(chars=total, paragraphs=len(paragraphs), single_sentence_ratio=round(sum(len(sentences(p))==1 for p in paragraphs)/max(1,len(paragraphs)),4),
        average_paragraph_chars=round(total/max(1,len(paragraphs)),2), dialogue_ratio=round(dialogue/max(1,total),4),
        mental_sentences=sum(bool(mental.search(s)) for s in all_sentences),
        placement_sentences=sum(bool(re.search(r'他把[^。！？!?]{0,50}(?:放|推|递)',s)) for s in all_sentences))


class Captured(Exception):
    pass


def snapshot(source, number, private, assets):
    """Rebuild facts from historical checkpoints without touching the book."""
    from bookengine import serialchapter, longchapter
    meta = load(source/'run.json'); book = Book(**meta['book'])
    plan = load(source/'steps/plan-overall.json')
    rows = [load(source/f'chapters/{i:03d}.json') for i in range(1,number)]
    prev = rows[-1]; summaries = [dict(chapter=r['number'],summary=r['summary']) for r in rows]
    begin = ((number-1)//10)*10+1
    chapter = next(c for c in load(source/f'steps/plan-chapters-{begin}-{begin+9}.json')['chapters'] if c['number']==number)
    if meta['mode']=='S5a':
        chapter = dict(chapter,open_threads=[re.sub(r'^F\d+\s*[:：]\s*','',s) if isinstance(s,str) else s for s in chapter.get('open_threads',[])])
    skeleton = next(v for v in plan['volumes'] if v['start']<=number<=v['end'])
    volume = dict(load(source/f"steps/plan-volume-{skeleton['number']}.json"),start=skeleton['start'],end=skeleton['end'])
    memory_number = ((number-1)//10)*10
    memory = source/f'steps/memory-{memory_number}'
    if memory.with_suffix('.json').exists():
        value = load(memory.with_suffix('.json'))
        rolling = str(value.get('summary') or '\n'.join(r['summary'] for r in rows[memory_number-3:memory_number]))[:900]
    else:
        rolling = memory.with_suffix('.md').read_text()[:900]
    prefix = knowledge(book,assets)
    if meta['mode']=='S4':
        prefix += '\n番茄玄幻只读口径：\n'+pack(yaml.safe_load((assets/'market_profiles/fanqie/xuanhuan-brain.yaml').read_text()))
    prefix = prefix[:5000]; calls = load(source/'calls.json')
    probe = SimpleNamespace(prefix=prefix,calls=calls)
    sandbox = private/'context-work'/source.name; sandbox.mkdir(parents=True,exist_ok=True)
    serial = prev.get('serial_meta',{})
    save(sandbox/'serial-meta.json',serial)
    captured = {}
    def capture(client, stage, instruction, sections, schema=None, **kwargs):
        captured.update(stage=stage,instruction=instruction,sections=sections,input_limit=kwargs.get('input_limit',30000))
        raise Captured()
    previous = [(source/f'chapter-{i:03d}.body.md').read_text() for i in range(1,number)]
    args = (probe,book,chapter,volume,plan['canon'],previous[-1][-1400:],prev['state'],summaries,rolling,style_rules(assets)+['arrives'],previous,sandbox)
    try:
        if meta['mode']=='S5a':
            threads = {k:v['description'] for k,v in serial.get('foreshadows',{}).items() if v['status']=='open'}
            with patch.object(serialchapter,'ask',capture): serialchapter.write_serial(*args,threads)
        else:
            with patch.object(longchapter,'ask',capture): longchapter.write_chapter(*args)
    except Captured:
        pass
    instruction = captured['instruction']
    overhead = len(prefix)+len(SYSTEM)+len(instruction)+350
    context = long_context(captured['sections'],max(0,min(30000,captured['input_limit'])-overhead))
    actual = next(c['input_chars'] for c in calls if c['stage']==f'ch{number}-draft')
    result = dict(source=str(source.resolve()),chapter=number,prefix=prefix,context=context,outline=chapter,
                  canon=plan['canon'],original_input_chars=actual,reconstructed_input_chars=len(SYSTEM)+len(prefix+'\n\n'+instruction+'\n'+context))
    save(private/f'context-{source.name}-{number}.json',result)
    return result


class ExperimentClient(Client):
    def __init__(self,out,env,prefix,root,temperature=None):
        super().__init__(out,env,prefix)
        self.relaxed=True;self.root=root;self.temperature=temperature

    def request(self,stage,prompt,cap,effort,attempt):
        spent = sum(usage_report(load(p))['estimated_usd']['conservative_total'] for p in self.root.rglob('calls.json'))
        upper = (len(prompt.encode())*.15+cap*.60)/1_000_000
        if spent+upper>.30:
            raise RuntimeError('Style experiment reached $0.30 limit')
        if self.temperature is None:
            return super().request(stage,prompt,cap,effort,attempt)
        original = urllib.request.urlopen
        def send(request,**kwargs):
            payload=json.loads(request.data);payload['temperature']=self.temperature
            request=urllib.request.Request(request.full_url,data=json.dumps(payload,ensure_ascii=False).encode(),headers=dict(request.header_items()))
            return original(request,**kwargs)
        try:
            with patch('bookengine.client.urllib.request.urlopen',send):
                return super().request(stage,prompt,cap,effort,attempt)
        finally:
            if self.calls:
                self.calls[-1]['requested_temperature']=self.temperature
                self.calls[-1]['effective_temperature']=1
                save(self.history_path,self.calls)


def run(root,env,assets,books):
    root.mkdir(parents=True,exist_ok=True,mode=0o700)
    private=root/'private';private.mkdir(exist_ok=True,mode=0o700);os.chmod(private,0o700)
    examples=yaml.safe_load((assets/'voice_few_shots.yaml').read_text())['examples']
    demos='\n'.join(examples[k] for k in ('xuanhuan-power-fantasy','xianxia-upgrade-core'))
    key_path=private/'KEY.json'
    if key_path.exists():
        key=load(key_path)
    else:
        variants=[dict(source=str(p.resolve()),chapter=n,version=v) for p,n in books for v in 'ABCD']
        random.SystemRandom().shuffle(variants)
        key={label:value for label,value in zip('甲乙丙丁戊己庚辛',variants)}
        save(key_path,key);os.chmod(key_path,0o600)
    contexts={(str(p.resolve()),n):snapshot(p,n,private,assets) for p,n in books}
    public=[]
    for label,item in key.items():
        source=Path(item['source']);number=item['chapter'];version=item['version']
        folder=private/'runs'/f'{source.name}-{number}-{version}'
        final=folder/('final.md' if version=='A' else 'final-contextfix.md')
        if final.exists():
            text=final.read_text()
        elif version=='A':
            text=(source/f'chapter-{number:03d}.body.md').read_text();save(final,text)
        else:
            data=contexts[(str(source),number)]
            # Facts remain intact; writing requirements are replaced by B.
            client=ExperimentClient(folder,env,'',private,1.2 if version=='D' else None)
            voices=[dict(name=c.get('name'),voice=c.get('voice'),wants=c.get('wants')) for c in data['canon'].get('characters',[])]
            head, raw = data['prefix'].split('只读题材与写作知识：\n',1)
            facts, _ = json.JSONDecoder().raw_decode(raw)
            facts = {k:v for k,v in facts.items() if k not in ('fragments','techniques')}
            prompt=STYLE_B+'\n原用户设定与题材承诺：\n'+head+pack(facts)+'\n人物口吻卡：'+pack(voices)+'\n原章纲与章前上下文：\n'+data['context']
            if version=='D':prompt+='\n两段仓库自写声口示范，只借声口，不照搬人物物品与剧情，B规则优先：\n'+demos
            text=prose(client.stage('draft-contextfix',prompt,cap=8000,effort='low',max_attempts=1))
            if version!='C':
                review=client.stage('review-contextfix',STYLE_B+'\n检查初稿是否符合要求；只给确需修改段落的完整替换，保留全部情节点与人物口吻，不添加流程戏。\n'+number_paragraphs(text),
                                    '{"edits":[{"paragraph":1,"text":""}]}',cap=7500,effort='low',max_attempts=1)
                edits=paragraph_edits(review if isinstance(review,dict) else {},text)['edits']
                text=apply_paragraph_edits(text,edits,{e['paragraph'] for e in edits})
            text=re.sub(r'\n(?:[ \t]*\n){2,}','\n\n',text).strip();save(final,text)
        row=metrics(text)
        public.append('## '+label+'\n\n'+f"字数 {row['chars']}；单句段 {row['single_sentence_ratio']:.1%}；平均段长 {row['average_paragraph_chars']} 汉字；对白 {row['dialogue_ratio']:.1%}；心理描写句 {row['mental_sentences']}；他把…放/推/递句 {row['placement_sentences']}。\n\n"+text)
        save(root/'METRICS.json',{**(load(root/'METRICS.json') if (root/'METRICS.json').exists() else {}),label:row})
        print('sample complete',label,flush=True)
    save(root/'BLIND.md','\n\n'.join(public)+'\n')
    usage=[usage_report(load(p)) for p in private.rglob('calls.json')]
    summary=dict(samples=len(public),calls=sum(x['calls'] for x in usage),tokens=sum(x['total_tokens'] for x in usage),
                 cost_usd=sum(x['estimated_usd']['conservative_total'] for x in usage),
                 context_reconstruction=[dict(chapter=d['chapter'],original=d['original_input_chars'],rebuilt=d['reconstructed_input_chars']) for d in contexts.values()],
                 temperature_note='MiniMax Flash ignores temperature and always uses 1; requested D=1.2 is ineffective.')
    save(private/'RESULT.json',summary);print(json.dumps(summary,ensure_ascii=False),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--out',default='../engine-books/style-ab');parser.add_argument('--env',default='../../.env')
    args=parser.parse_args()
    run(Path(args.out),args.env,Path('config'),[(Path('../engine-books/s5a/urban-300'),120),(Path('../engine-books/s4'),30)])
