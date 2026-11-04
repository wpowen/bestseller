"""Bounded live comparison. Raw prompts/keys stay under ignored books/ only."""
import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import random
import secrets

from deai.client import MiniMax
from ideation import judge, read, run, save, step
from ideation.filters import shape, similarity, validate
from ideation.market import snapshot, features

GENRES={'xuanhuan-power-fantasy':'玄幻爽文','suspense-mystery':'悬疑推理','enemies-to-lovers':'宿敌相恋'}


def cost(root):
    total=0.;unknown=0;calls=0;tokens=0
    for path in root.rglob('http/ledger.json'):
        for row in read(path,[]):
            usage=row.get('usage') or {}; incoming=usage.get('prompt_tokens');outgoing=usage.get('completion_tokens')
            unknown+=incoming is None or outgoing is None;calls+=1
            total+=((row['input_bytes'] if incoming is None else incoming)*.15+(row['max_tokens'] if outgoing is None else outgoing)*.60)/1e6
            tokens+=sum(v for v in (incoming,outgoing) if type(v) is int)
    return dict(usd=total,unknown=unknown,calls=calls,tokens=tokens)


def budget(root,cap):
    if cost(root)['usd']+cap>1: raise RuntimeError('总预算不足，停止')


def old_batch(root,genre,n,env):
    folder=root/'old'/genre/str(n);budget(root,.015)
    client=MiniMax(folder,env,budget=.015)
    prompt=f"为{GENRES[genre]}、100章短篇生成12个不同的中文小说选题。想法：自由发挥。每个含title和premise（80字以内，具体人物欲望、处境和核心冲突）。只返回JSON数组，恰好12项。"
    cards=step(folder,'topics-12',prompt,client,2600)
    if not isinstance(cards,list) or len(cards)!=12:raise ValueError('旧选题未返回十二项')
    save(folder/'candidates.json',cards);return cards


def baseline(root,genre,n,env):
    try:return old_batch(root,genre,n,env)
    except ValueError:
        # Preserve the failed legacy response and use a separate, bounded batch.
        return old_batch(root,genre,n+10,env)


def calibrate(root,genre,old,market,env):
    target=root/'calibration'/genre;data=dict(chapters=100)
    previous=read(root/'.market'/('judge-'+genre+'.json'),{})
    if previous.get('date')==market.get('date'):return previous
    anchors=features(market,genre,'女频' if genre=='enemies-to-lovers' else '男频').get('anchors',[])[:6]
    if len(anchors)<6:
        result=dict(valid=False,reason='不足六本同类真实榜单，未调用判官',date=market.get('date'))
    else:
        mix=[dict(title=c['title'],hook=c.get('hook','')[:120],real=True) for c in anchors]+[dict(title=c['title'],hook=c.get('premise','')[:120],real=False) for c in old[:6]]
        random.Random(20261104).shuffle(mix);save(target/'private-map.json',mix)
        budget(root,.008);scored=judge(target,mix,[],data,MiniMax(target,env,budget=.008),'calibration')
        order=sorted(range(12),key=lambda i:-sum(scored[i]['scores'].values()))
        hits=sum(mix[i]['real'] for i in order[:6])
        result=dict(valid=hits>=5,real_in_top6=hits,date=market.get('date'),order=order,anchor_fields='title + hook (120 chars); source hidden')
        save(target/'scores.json',scored)
    save(root/'.market'/('judge-'+genre+'.json'),result);return result


def metrics(batches):
    cards=[c for batch in batches for c in batch]
    pairs=[]
    for i,batch in enumerate(batches):
        for j,other in enumerate(batches[:i]):
            for a in batch:
                for b in other:
                    if similarity(a['title'],b['title'])>=.5:pairs.append([j,i,a['title'],b['title']])
    return dict(sizes=[len(b) for b in batches],cross_batch_near_duplicates=len(pairs),duplicate_pairs=pairs,
                shells=[len({c.get('axes',{}).get('shell') for c in b}) for b in batches],
                title_shapes=[dict(Counter(shape(c['title']) for c in b)) for b in batches],
                shape_max_ratio=[max(Counter(shape(c['title']) for c in b).values())/len(b) if b else None for b in batches],
                invalid=sum(bool(validate(c,100)) for c in cards))


def blind(root,destination,old,new):
    destination=Path(destination);destination.mkdir(parents=True,exist_ok=True)
    key={};lines=['# 选题盲选','','每组包含两批候选。请按书名、设定和追读意愿选择更想读的一批，也可圈出单个候选。','']
    plan=read(root/'private'/'blind-plan.json',{})
    if not plan:plan=dict(seed=secrets.randbits(63));save(root/'private'/'blind-plan.json',plan)
    rng=random.Random(plan['seed'])
    for group,genre in enumerate(GENRES,1):
        batches=[('old',old[genre][0]),('new',new[genre][-1])];rng.shuffle(batches)
        lines+=['## '+str(group)+' · '+GENRES[genre],'']
        for label,(source,cards) in zip(('甲','乙'),batches):
            code=f'{group}{label}';key[code]=dict(genre=genre,source=source,batch=0 if source=='old' else len(new[genre])-1)
            order=list(range(len(cards)));rng.shuffle(order);key[code]['order']=order
            lines+=['### '+code,'']
            # Equal fields prevent presentation richness revealing the generator.
            for n,index in enumerate(order,1):
                c=cards[index];lines += [f"**{n}. {c['title']}**",'',c['premise'],'']
    save(root/'private'/'blind-key.json',key)
    (destination/'BLIND.md').write_text('\n'.join(lines),encoding='utf-8')
    # Full cards remain available with random labels; no axes/scores/model metadata.
    package={code:[{k:all_cards[i][k] for k in ('title','premise')} for i in meta['order']] for code,meta in key.items() for all_cards in [old[meta['genre']][meta['batch']] if meta['source']=='old' else new[meta['genre']][meta['batch']]]}
    save(destination/'cards.json',package)


def main():
    p=argparse.ArgumentParser();p.add_argument('--env',type=Path,required=True);p.add_argument('--out',type=Path,required=True);p.add_argument('--blind',type=Path,required=True);args=p.parse_args()
    root=args.out.resolve();root.mkdir(parents=True,exist_ok=True)
    market=snapshot(root);old={};new={};calibration={}
    for genre in GENRES:
        old[genre]=[baseline(root,genre,n,args.env) for n in range(3 if genre=='xuanhuan-power-fantasy' else 1)]
        calibration[genre]=calibrate(root,genre,old[genre][0],market,args.env)
        new[genre]=[]
        for n in range(3 if genre=='xuanhuan-power-fantasy' else 1):
            folder=root/'new'/genre/str(n)
            if not (folder/'topic.json').exists():save(folder/'topic.json',dict(status='running',genre=genre,chapters=100,idea='',channel='女频' if genre=='enemies-to-lovers' else '男频',seed=None))
            data=read(folder/'topic.json')
            if data.get('seed') is None:data.pop('seed',None);save(folder/'topic.json',data)
            budget(root,.05)
            result=run(folder,MiniMax(folder,args.env,budget=.05),root)
            new[genre].append(result['candidates']);print(genre,n,len(result['candidates']),f"${result['cost_usd']:.6f}",flush=True)
        for extra in sorted((root/'new'/genre).glob('*/topic.json'),key=lambda p:p.parent.name):
            if extra.parent.name.isdigit() and int(extra.parent.name)>=len(new[genre]):
                completed=read(extra)
                if completed.get('status')=='complete':new[genre].append(completed['candidates'])
        summary=dict(old=metrics(old[genre]),new=metrics(new[genre]),calibration=calibration[genre],market_title_shapes=features(market,genre,'女频' if genre=='enemies-to-lovers' else '男频').get('title_shapes',{}))
        save(root/'metrics'/(genre+'.json'),summary)
    blind(root,args.blind,old,new)
    save(root/'summary.json',dict(cost=cost(root),calibration=calibration))
    print(json.dumps(cost(root)),flush=True)


if __name__=='__main__':main()
