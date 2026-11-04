"""Two explicit scene-level dialogue experiments with fixed quote slots."""
from concurrent.futures import ThreadPoolExecutor
import re
from .text import paragraphs,replace,invariant

QUOTES=re.compile(r'“([^”]*)”|「([^」]*)」|"([^"\n]*)"')

def rewrite(session,text,inventory,ctx,mode,entities):
    ps=paragraphs(text);all_slots=[]
    for p in ps:
        for m in QUOTES.finditer(p['text']):
            group=next(i for i in (1,2,3) if m[i] is not None);a,b=m.span(group)
            if re.search(r'(?:写着|印着|标着|字样|信上|票面|屏幕|显示)[：:]?\s*[“「"]?$',p['text'][:m.start()]):continue
            all_slots.append(dict(id=f'D{len(all_slots)+1}',pid=p['pid'],start=p['start']+a,end=p['start']+b,text=m[group],context=p['text']))
    if not all_slots:return text,[]
    # Select one complete, declared scene for the A/B; other scene dialogue remains untouched.
    scene=next((s for s in inventory.get('scenes',[]) if isinstance(s,dict) and s.get('pids') and sum(x['pid'] in s['pids'] for x in all_slots)>=2),None)
    slots=[s for s in all_slots if not scene or s['pid'] in scene['pids']]
    cards=ctx.get('characters',[]);cast=session.ask('dialogue-cast','逐条识别真实说话人；不是对白或无法确认者speaker=null。只用已有姓名，不能猜年龄出身。返回{"slots":[{"id":"D1","speaker":null,"goal":""}]}。',dict(slots=slots,characters=cards),3500)
    owners={r['id']:r for r in cast.get('slots',[]) if isinstance(r,dict) and r.get('speaker')};slots=[s for s in slots if s['id'] in owners]
    if not slots:return text,[]
    variants=[]
    if mode=='scene':
        raw=session.ask('dialogue-scene','只重写这整场真实对白，不改旁白，不新增/删除说话轮次。给3套完整备选，保持否定、条件、说话人、数字、事件与信息来源；按卡分口吻，允许打断、答非所问、幽默和脾气；禁口号金句。逐槽给无引号无换行的text。返回{"variants":[{"lines":[{"id":"D1","text":""}]}]}。',dict(slots=slots,speakers=owners,characters=cards,facts=inventory),7000)
        variants=[v.get('lines',[]) for v in raw.get('variants',[])][:3]
    else:
        for variant in range(3):
            lines=[];history=[]
            for slot in slots:
                owner=owners[slot['id']];card=next((c for c in cards if isinstance(c,dict) and c.get('name')==owner['speaker']),dict(name=owner['speaker']))
                raw=session.ask(f'actor-{variant}-{slot["id"]}','你只演自己角色，不解释、不写旁白。对对方上一句回应，保留本轮原台词事实、否定条件和数量，不能新增情节或改变立场。用自己的口吻说，允许迟疑/打断/脾气，不讲原则金句。返回{"text":"无外层引号的本轮台词"}。',dict(character=card,goal=owner.get('goal'),previous_other=next((line for line in reversed(history) if line['speaker']!=owner['speaker']),None),original=slot['text']),1000)
                line=dict(id=slot['id'],text=raw.get('text'));lines.append(line);history.append(dict(speaker=owner['speaker'],text=line['text']))
            variants.append(lines)
    from .flow import _factcheck,score
    candidates=[];slots_by_id={s['id']:s for s in slots}
    for i,lines in enumerate(variants):
        mapped={x.get('id'):x.get('text') for x in lines if isinstance(x,dict)}
        for slot in slots:
            value=mapped.get(slot['id'],slot['text'])
            if not isinstance(value,str):continue
            value=value.strip()
            if len(value)>1 and (value[0],value[-1]) in (('“','”'),('「','」'),('"','"')):value=value[1:-1]
            value=value.replace('\r',' ').replace('\n',' ')
            if value==slot['text']:continue
            reasons=invariant(slot['text'],value,ctx,entities)
            if any(c in value for c in '“”「」"'):reasons.append('quote_structure')
            if reasons:session.warnings.append(dict(dialogue=mode,slot=slot['id'],variant=i,rollback=reasons));continue
            candidates.append(dict(id=f'V{i}-{slot["id"]}',variant=i,slot=slot,replacement=value))
    checks={};packets=[candidates[i:i+12] for i in range(0,len(candidates),12)]
    def check(pair):
        index,packet=pair
        items=[dict(id=c['id'],before=dict(speaker=owners[c['slot']['id']]['speaker'],line=c['slot']['text'],context=c['slot']['context']),after=dict(speaker=owners[c['slot']['id']]['speaker'],line=c['replacement'])) for c in packet]
        return _factcheck(session,'dialogue-linefacts-'+str(index),items,ctx)
    with ThreadPoolExecutor(max_workers=3) as pool:
        for value in pool.map(check,enumerate(packets)):checks.update(value)
    proposals=[]
    for i in range(len(variants)):
        edits=[]
        for c in [c for c in candidates if c['variant']==i and checks.get(c['id'],{}).get('ok')]:
            slot=c['slot'];edit=dict(start=slot['start'],end=slot['end'],replacement=c['replacement'],before=slot['text'],pid=slot['pid'],source=mode,variant=i)
            if invariant(text,replace(text,edits+[edit]),ctx,entities,True):session.warnings.append(dict(dialogue=mode,slot=slot['id'],rollback='chapter_word_band'));continue
            edits.append(edit)
        candidate=replace(text,edits)
        proposals.append(dict(id='V'+str(i),text=candidate,edits=edits))
    if not proposals:return text,[]
    combined=_factcheck(session,'dialogue-combined-facts',[dict(id=p['id'],before=text,after=p['text']) for p in proposals],ctx);valid=[]
    for p in proposals:
        if not combined.get(p['id'],{}).get('ok'):continue
        hits,unknown=session.classify(p['text'],'dialogue-'+p['id'],only=['written_dialogue'])
        if not unknown:valid.append(dict(p,score=score(hits,p['text']),hits=hits))
    original_hits,unknown=session.classify(text,'dialogue-original',only=['written_dialogue'])
    if unknown or not valid:return text,[]
    chosen=min(valid,key=lambda p:(p['score'],-len(p['edits'])))
    if chosen['score']>score(original_hits,text):return text,[]
    return chosen['text'],chosen['edits']
