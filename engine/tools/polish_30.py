"""Review existing pilots into candidates; --adopt is a separate offline action.

Run from the worktree root with --env PATH [--root ../engine-books].
Review candidates manually before invoking --adopt. Drafts, outlines, old calls,
standalone editor results and concept data are never changed by this tool.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from copy import deepcopy
import fcntl
from pathlib import Path
import re
import signal
import sys
from threading import Event, current_thread, main_thread
import time

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bookengine.batch import usage_report
from bookengine.q1_quality import merge_state, metrics
from bookengine.storage import load, save
from bookengine.text import chinese_count

BOOKS = ('xuanhuan-30', 'suspense-30')
LOW, HIGH = 1870, 2700


class Stopped(Exception):
    pass


def overlay(base, patch):
    """Retain fields unknown to the normal state reducer, including nested ones."""
    result = deepcopy(base) if isinstance(base, dict) else {}
    for key, value in patch.items() if isinstance(patch, dict) else ():
        result[key] = overlay(result.get(key), value) if isinstance(value, dict) else deepcopy(value)
    return result


def read_body(path):
    # Preserve newlines exactly for the later literal original comparison.
    return path.read_bytes().decode('utf-8')


def body_path(folder, idx):
    return folder / f'chapter-{idx:03d}.body.md'


def row_path(folder, idx):
    return folder / 'chapters' / f'{idx:03d}.json'


def preflight(root):
    for key in BOOKS:
        source, out = root / 'benchmark' / key, root / 'polish-30' / key
        for name in ('story_bible.yaml', 'threads.yaml'):
            if not (source / name).is_file():
                raise ValueError(f'{key}: missing {name}')
        for idx in range(1, 31):
            text = read_body(body_path(source, idx))
            row = load(row_path(source, idx))
            if not text.strip() or row.get('number') != idx:
                raise ValueError(f'{key}: incomplete chapter {idx}')
            original = body_path(out / 'original', idx)
            if original.exists() and original.read_bytes() != body_path(source, idx).read_bytes():
                raise ValueError(f'{key}: original changed at chapter {idx}')


def initialize(source, out):
    for name in ('story_bible.yaml', 'threads.yaml'):
        target = out / name
        if not target.exists():
            save(target, read_body(source / name))
    for idx in range(1, 31):
        original = body_path(out / 'original', idx)
        if not original.exists():
            save(original, read_body(body_path(source, idx)))
        original_row = row_path(out / 'original', idx)
        if not original_row.exists():
            save(original_row, load(row_path(source, idx)))


def character_cards(source, bible):
    cards = deepcopy(bible.get('characters', []))
    for name in ('voice_cards.yaml', 'identity_cards.json'):
        path = source / name
        if not path.exists():
            continue
        values = yaml.safe_load(path.read_text(encoding='utf-8'))
        values = values.get('cards', []) if isinstance(values, dict) else values
        mapped = {c.get('name'): c for c in values if isinstance(c, dict)} if isinstance(values, list) else {}
        for card in cards:
            # Bible's character constraints remain authoritative.
            for field, value in mapped.get(card.get('name'), {}).items():
                if field != 'name' and not card.get(field):
                    card[field] = value
    return cards


def new_client(out, env, stop):
    from bookengine.q1 import QClient

    class PolishClient(QClient):
        def request(self, *args, **kwargs):
            if stop.is_set():
                raise Stopped()
            try:
                return super().request(*args, **kwargs)
            except RuntimeError as exc:
                if 'budget' in str(exc).lower():
                    stop.set()
                raise

    client = PolishClient(out, env, '', model='MiniMax-M3.1-Flash')
    client.prefix = ''
    client.relaxed = client.minimal_prose = client.clip_context = True
    client.context_limit = 10000
    client.stop_requested = stop.is_set
    return client


def candidate_count(text, cards, state, glossary):
    from bookengine.polish_checks import scan
    from bookengine.precision import locate, _sentences
    from bookengine.q1 import POLISH
    terms = sum(any(m.group('term') not in glossary
                    and m.group('term') not in POLISH['term_exclusions']['exact']
                    for pattern in POLISH['term_patterns']
                    for m in re.finditer(pattern, text[a:b])) for a, b in _sentences(text))
    return len(locate(text)) + len(scan(text, cards, state)) + terms


def summarize(out, key, status):
    rows = [load(row_path(out, n)) for n in range(1, 31) if row_path(out, n).exists()]
    calls = load(out / 'calls.json') if (out / 'calls.json').exists() else []
    result = dict(book=key, status=status, chapters=len(rows), rows=rows,
                  seconds=round(sum(r['seconds'] for r in rows), 3), usage=usage_report(calls),
                  opens_new_mainline=next((r.get('opens_new_mainline') for r in rows if r['number'] == 30), None))
    save(out / 'summary.json', result)
    return result


def polish_book(root, key, env, stop):
    from bookengine.length import correct_length
    from bookengine.q1 import DATA, review_once

    source, out = root / 'benchmark' / key, root / 'polish-30' / key
    out.mkdir(parents=True, exist_ok=True)
    with (out / 'runner.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        status = 'stopped'
        try:
            initialize(source, out)
            bible = yaml.safe_load((out / 'story_bible.yaml').read_text(encoding='utf-8'))
            cards = character_cards(source, bible)
            data = yaml.safe_load(DATA.read_text(encoding='utf-8'))
            state = merge_state({}, {}, bible)
            state['power_status'] = deepcopy(bible.get('power', {}))
            previous, history, client, family_count = '', [], None, 0
            for idx in range(1, 31):
                if stop.is_set():
                    raise Stopped()
                saved = row_path(out, idx)
                if saved.exists():
                    row = load(saved)
                    text = read_body(body_path(out, idx))
                    if row.get('status') != 'complete' or row.get('chars') != chinese_count(text):
                        raise ValueError('Incomplete candidate checkpoint')
                else:
                    if client is None:
                        client = new_client(out, env, stop)
                        client.canon_power=bible.get('power',{})
                        client.glossary = deepcopy(bible.get('glossary', {}))
                    client.chapter_number = idx
                    original = read_body(body_path(out / 'original', idx))
                    old = load(row_path(out / 'original', idx))
                    # This chapter's old row describes its END, never its input.
                    before = deepcopy(state)
                    original_candidates = candidate_count(original, cards, before, client.glossary)
                    stage = f'polish30-ch{idx:03d}'
                    timer = out / 'steps' / f'{stage}-progress.json'
                    progress = load(timer) if timer.exists() else dict(call_start=len(client.calls), elapsed=0)
                    save(timer, progress)
                    started = time.monotonic()
                    try:
                        length_used = not LOW <= chinese_count(original) <= HIGH
                        length_path = out / 'steps' / f'{stage}-length.md'
                        if length_path.exists():
                            text = read_body(length_path)
                        else:
                            text = correct_length(client, stage + '-length', original,
                                                  dict(number=idx, title=old.get('title', '')), 2200) if length_used else original
                            save(length_path, text)
                        length_chars = chinese_count(text)
                        text, review, detail = review_once(
                            client, text, stage + '-review', cards=cards, state=before,
                            previous=previous[-600:], planned=[], data=data,
                            history=[dict(family_count=family_count), *history[-9:]], ending=idx == 30)
                        if not (out / 'steps' / f'{stage}-review.json').exists():
                            raise RuntimeError('Review checkpoint missing')
                        patch = review.get('state', {})
                        reduced = merge_state(before, patch, bible)
                        after_base = overlay(before, old.get('state', {}))
                        current = overlay(after_base, merge_state(after_base, patch, bible))
                        # Typed state must be EXACTLY the core reducer's result:
                        # recursive overlay could reinsert explicitly released blocks.
                        current.update({k: deepcopy(reduced[k]) for k in ('continuity', 'power_status')})
                        chapter_calls = client.calls[progress['call_start']:]
                        row = dict(number=idx, status='complete', original_chars=chinese_count(original),
                                   length_chars=length_chars, chars=chinese_count(text), style=metrics(text),
                                   within_band=LOW <= chinese_count(text) <= HIGH, changed=text != original,
                                   length_correction=length_used, state=current,
                                   original_candidates=original_candidates,
                                   program_candidates=detail['hits_before'], edits=detail['edits'],
                                   remaining=candidate_count(text, cards, before, client.glossary), detail=detail,
                                   hook_type=review.get('hook_type', review.get('actual_hook_type', old.get('hook_type'))),
                                   summary=review.get('summary') or old.get('summary', ''),
                                   opens_new_mainline=detail.get('opens_new_mainline'),
                                   seconds=round(max(progress['elapsed'] + time.monotonic() - started,
                                                     sum(c.get('seconds', 0) for c in chapter_calls)), 3),
                                   calls=len(chapter_calls), usage=usage_report(chapter_calls))
                        save(body_path(out, idx), text)
                        save(saved, row)  # Row is the chapter completion checkpoint.
                    finally:
                        progress['elapsed'] += time.monotonic() - started
                        save(timer, progress)
                state, previous = row['state'], text
                family_count += row['detail'].get('double_or_reawakening') is True
                history.append(dict(number=idx, hook_type=row.get('hook_type'), ending=text[-120:]))
                history = history[-9:]
                save(out / 'state.json', state)
                summarize(out, key, 'running')
                print(f'{key} {idx:02d}: {row["original_chars"]}->{row["chars"]} chars; '
                      f'{row["program_candidates"]} candidates, {len(row["edits"])} edits, '
                      f'{row["remaining"]} remaining; {row["seconds"]}s, {row["calls"]} calls; '
                      f'estimate={row["usage"]["estimated_usd"]["no_cache_discount"]}; '
                      f'opens_new_mainline={row["opens_new_mainline"] if idx == 30 else "n/a"}', flush=True)
            status = 'complete'
        except Stopped:
            status = 'stopped'
        except Exception as exc:
            stop.set()
            status = 'stopped_budget' if 'budget' in str(exc).lower() else 'stopped_error'
            # Never print provider exceptions, credentials or env contents.
            print(f'{key}: {status} ({type(exc).__name__}); checkpoints retained', flush=True)
        return summarize(out, key, status)


def run(root, env):
    root = Path(root)
    preflight(root)  # Validate both inputs before constructing any paid client.
    stop = Event()
    handlers = {}
    if current_thread() is main_thread():
        for sig in (signal.SIGINT, signal.SIGTERM):
            handlers[sig] = signal.signal(sig, lambda *_: stop.set())
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = {key: pool.submit(polish_book, root, key, env, stop) for key in BOOKS}
            books = {key: future.result() for key, future in futures.items()}
        result = dict(books=books)
        save(root / 'polish-30' / 'summary.json', result)
        return result
    finally:
        for sig, handler in handlers.items():
            signal.signal(sig, handler)


def adopt(root):
    """No client construction, env access or network; caller reviews first."""
    root = Path(root)
    changes, book_updates = [], []
    with ExitStack() as locks:
        for key in BOOKS:
            source, out = root / 'benchmark' / key, root / 'polish-30' / key
            for folder in (source, out):
                lock = locks.enter_context((folder / 'runner.lock').open('a'))
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            for idx in range(1, 31):
                original = body_path(out / 'original', idx).read_bytes()
                if body_path(source, idx).read_bytes() != original:
                    raise ValueError(f'{key}: chapter {idx} differs from original; nothing adopted')
                candidate = load(row_path(out, idx))
                text = read_body(body_path(out, idx))
                if candidate.get('status') != 'complete' or not text.strip() or candidate.get('chars') != chinese_count(text):
                    raise ValueError(f'{key}: incomplete candidate {idx}; nothing adopted')
                row = load(row_path(source, idx))
                row.update(chars=chinese_count(text), style=metrics(text), within_band=LOW <= chinese_count(text) <= HIGH)
                row['state'] = deepcopy(row.get('state', {}))
                row['state'].update({k: deepcopy(candidate['state'][k]) for k in ('continuity', 'power_status')})
                row.update(polish=deepcopy(candidate['detail']), hook_type=candidate['hook_type'], summary=candidate['summary'])
                row['polish_30'] = {k: candidate[k] for k in ('original_chars', 'length_chars', 'chars',
                                     'length_correction', 'program_candidates', 'edits', 'remaining',
                                     'detail', 'opens_new_mainline', 'seconds', 'calls', 'usage')}
                changes.append((source, idx, original, text, row))
            bible = yaml.safe_load((source / 'story_bible.yaml').read_text(encoding='utf-8'))
            candidate_bible = yaml.safe_load((out / 'story_bible.yaml').read_text(encoding='utf-8'))
            glossary = overlay(bible.get('glossary'), candidate_bible.get('glossary'))
            bible_changed = glossary != bible.get('glossary', {})
            if bible_changed:
                bible['glossary'] = glossary
            state = load(source / 'state.json') if (source / 'state.json').exists() else {}
            state.update({k: deepcopy(candidate['state'][k]) for k in ('continuity', 'power_status')})
            book_updates.append((source, bible if bible_changed else None, state))
        # All 60 originals must pass before any benchmark body/metadata write.
        for source, idx, original, text, row in changes:
            if body_path(source, idx).read_bytes() != original:
                raise ValueError('Original changed during adoption; stopped')
            save(body_path(source, idx), text)
            save(row_path(source, idx), row)
        for source, bible, state in book_updates:
            if bible is not None:
                save(source / 'story_bible.yaml', yaml.safe_dump(bible, allow_unicode=True, sort_keys=False))
            save(source / 'state.json', state)
    print('Adopted 60 bodies, chapter metadata, glossary and final typed state; drafts/calls/editor files retained.')


def replay(root):
    """Reapply cached proposals after parser fixes; no keys or network involved."""
    from bookengine.q1 import DATA, review_once
    data=yaml.safe_load(DATA.read_text())
    class Cached:
        prefix=''
        def stage(self,name,*args,**kwargs):return load(self.out/'steps'/f'{name}.json')
    for key in BOOKS:
        out=Path(root)/'polish-30'/key;source=Path(root)/'benchmark'/key
        if not out.exists():continue
        bible=yaml.safe_load((source/'story_bible.yaml').read_text());cards=character_cards(source,bible)
        client=Cached();client.out=out;client.glossary={};state=merge_state({}, {},bible);state['power_status']=bible.get('power',{});previous='';history=[];family=0
        for p in sorted((out/'chapters').glob('*.json')):
            row=load(p);idx=row['number'];client.chapter_number=idx;stage=f'polish30-ch{idx:03d}'
            text=read_body(out/'steps'/f'{stage}-length.md');before=deepcopy(state)
            text,review,detail=review_once(client,text,stage+'-review',cards,before,previous,[],data,[dict(family_count=family),*history[-9:]],idx==30)
            old=load(row_path(out/'original',idx));reduced=merge_state(before,review.get('state',{}),bible)
            state=overlay(overlay(before,old.get('state',{})),reduced)
            state.update({k:deepcopy(reduced[k]) for k in ('continuity','power_status')})
            row.update(chars=chinese_count(text),within_band=LOW<=chinese_count(text)<=HIGH,style=metrics(text),state=state,edits=detail['edits'],detail=detail,
                remaining=candidate_count(text,cards,before,client.glossary),summary=review.get('summary') or old.get('summary',''),hook_type=review.get('actual_hook_type',old.get('hook_type')),
                opens_new_mainline=detail.get('opens_new_mainline'))
            save(body_path(out,idx),text);save(p,row);previous=text;family+=detail.get('double_or_reawakening') is True
            history.append(dict(number=idx,hook_type=row['hook_type'],ending=text[-120:]))
        repair=out/'settings-applied.json'
        if repair.exists():
            from bookengine.precision import apply, _sentences
            record=load(repair);ceiling=None
            for idx in range(1,31):
                if not row_path(out,idx).exists():break
                text=read_body(body_path(out,idx));points=_sentences(text)
                text,accepted=apply(text,[e for e in record.get('edits',[]) if e['chapter']==idx],
                    [dict(id=i,start=a,end=b) for i,(a,b) in enumerate(points,1)],allow_empty=True,allow_style=True)
                for e in record.get('manual_precision',[]):
                    if e['chapter']==idx:text=text.replace(e['old'],e['new'])
                row=load(row_path(out,idx));row.update(chars=chinese_count(text),style=metrics(text),within_band=LOW<=chinese_count(text)<=HIGH,setting_edits=accepted)
                balances=record.get('balances',{});balance=balances.get(str(idx),balances.get(idx))
                if balance:
                    amount=balance.get('amount');ceiling=amount if amount is not None else ceiling
                    row['state']['continuity'].setdefault('balances',{})['寿命']=dict(amount=amount,unit=balance.get('unit','天'),ceiling=ceiling)
                if idx in record.get('family_confirmed_chapters',[]):row['detail']['double_or_reawakening']=True
                if idx==30:
                    ending=record.get('ending',{});row['opens_new_mainline']=ending.get('opens_new_mainline')
                    row['detail'].update(opens_new_mainline=ending.get('opens_new_mainline'),ending_reason=ending.get('reason'),double_or_reawakening=False)
                save(body_path(out,idx),text);save(row_path(out,idx),row);state=row['state']
        save(out/'state.json',state);summarize(out,key,'complete' if repair.exists() else 'interrupted')


def concepts(root,env):
    from bookengine.craft import surface_hits
    from bookengine.q1 import review_once
    for key in BOOKS:
        source=Path(root)/'benchmark'/key/'concept';out=Path(root)/'polish-30'/key
        value=load(source/'ALL.json');changes={};client=None
        for c in value.get('concepts',[]):
            for field in ('pitch','gift','first_payoff','sustainability'):
                text=c.get(field,'')
                if isinstance(text,str) and any('english' in h['categories'] for h in surface_hits(text)):
                    client=client or new_client(out,env,Event())
                    revised,_,_=review_once(client,text,f'concept-english-{c["id"]}-{field}')
                    if not surface_hits(revised):changes[text]=revised
        if changes:
            import json
            for path in (source/'ALL.json',source/'SELECTED.json'):
                def replace_value(v):
                    if isinstance(v,str):return changes.get(v,v)
                    if isinstance(v,list):return [replace_value(x) for x in v]
                    if isinstance(v,dict):return {k:replace_value(x) for k,x in v.items()}
                    return v
                save(path,replace_value(load(path)))
            p=source/'CANDIDATES.md';text=p.read_text()
            for old,new in changes.items():text=text.replace(old,new)
            save(p,text)
        print(key,'concept English values repaired:',len(changes))


def settings(root,env):
    """Bounded repair of confirmed setting problems from existing candidates."""
    from bookengine.q1 import ask, pack
    from bookengine.precision import _sentences
    for key in BOOKS:
        out=Path(root)/'polish-30'/key;done=out/'settings-applied.json'
        if done.exists():continue
        bible=yaml.safe_load((out/'story_bible.yaml').read_text());bodies={};spans={};selected=[]
        for idx in range(1,31):
            text=read_body(body_path(out,idx));bodies[idx]=text;spans[idx]=_sentences(text)
            pattern=r'寿命|余命|还剩[一二三四五六七八九十百零两\d]+天|代价：[一二三四五六七八九十百零两\d]+日' if key=='suspense-30' else r'永久封|截脉剑意|右臂|左臂|右手|左手'
            if key!='suspense-30' and idx not in (1,2,3,10,11,30):continue
            for i,(a,b) in enumerate(spans[idx],1):
                if re.search(pattern,text[a:b]) or idx==30 and i>=len(spans[idx])-5:
                    selected.append(dict(chapter=idx,id=i,text=text[a:b],context=text[max(0,a-35):min(len(text),b+35)]))
        client=new_client(out,env,Event())
        instruction=('复核已确认的设定错误，只改列出的编号句，不整章重写，不重写其他句，不改剧情节点，不凭空充值、解封或加规则。'
            '寿命是转给规则的资源，抽象感受不等于全部扣光。不能把不明确的消耗推算成0或负数。'
            '正文写剩4天却扣7天是确证矛盾，按既有每次1至10天代价区间定点修扣费句及后文余额句；不能通过新规则或补充值解围。'
            '无法从既有正文确定准确余数，就把冲突的硬数字改成具体感受或非量化说法，不虚构一个新的确数。'
            'balances逐章仅记录有明确依据的结尾余额，amount未知用null，禁止用未注明的臆测扣减；每项reason说明字面依据或未知。'
            '封禁是否可以轮转以圣经为准，左/右手不能乱换，失去痛觉不等于必然不能行动，伤残动作有上下文支撑才修。'
            '末章已发现尸体坐起/新身体醒来仍承诺后续冲突。不能只把最后一句换成“已经闭合”来掩盖。'
            '把引出独立意志/新任务的整串命中末句修成尸体静止、旧事回收，停在既有人物动作或对白上，不能添新事件。'
            '返回edits每项chapter/id/text/reason，保留原句引号边界。ending给修后是否仍新开主线和理由；没有可证错误不要改。')
        value=ask(client,'polish30-confirmed-settings',instruction,[('原圣经机制',pack(bible.get('power',{}))),('按章候选句',pack(selected))],
            '{"edits":[{"chapter":1,"id":1,"text":"","reason":""}],"balances":[{"chapter":1,"amount":null,"unit":"天","reason":""}],"ending":{"opens_new_mainline":false,"reason":""}}',cap=8000,input_limit=10000,max_attempts=1)
        allowed={(x['chapter'],x['id']) for x in selected};edits={}
        for e in value.get('edits',[]) if isinstance(value,dict) and isinstance(value.get('edits'),list) else []:
            if isinstance(e,dict) and (e.get('chapter'),e.get('id')) in allowed and isinstance(e.get('text'),str):edits[(e['chapter'],e['id'])]=e
        amounts={b['chapter']:b for b in value.get('balances',[]) if isinstance(b,dict) and isinstance(b.get('chapter'),int)} if isinstance(value,dict) else {}
        for idx,text in bodies.items():
            changes=[e for (n,_),e in edits.items() if n==idx]
            for e in sorted(changes,key=lambda e:-e['id']):
                a,b=spans[idx][e['id']-1];text=text[:a]+e['text']+text[b:]
            row=load(row_path(out,idx));row['state'].setdefault('continuity',{})
            if key=='suspense-30':
                balance=amounts.get(idx,{})
                row['state']['continuity'].setdefault('balances',{})['寿命']=dict(amount=balance.get('amount'),unit='天')
            row.update(chars=chinese_count(text),style=metrics(text),within_band=LOW<=chinese_count(text)<=HIGH)
            row['setting_edits']=changes;row['setting_balance']=amounts.get(idx)
            save(body_path(out,idx),text);save(row_path(out,idx),row)
        save(out/'state.json',row['state']);save(done,dict(edits=list(edits.values()),balances=amounts,ending=value.get('ending')))
        summarize(out,key,'complete');print(key,'setting sentence edits:',len(edits),'ending:',value.get('ending'),flush=True)


def limits(root,env):
    from bookengine.precision import _sentences,apply
    from bookengine.q1 import ask,pack
    out=Path(root)/'polish-30'/'xuanhuan-30';marker=out/'limits-applied.json'
    if marker.exists():return
    selected=[];bodies={};points={}
    for n in (1,30):
        text=read_body(body_path(out,n));bodies[n]=text;points[n]=_sentences(text)
        for i,(a,b) in enumerate(points[n],1):
            if n==1 and re.search('化身|五指|手掌|黑雾|掌心|一招',text[a:b]) or n==30 and i>len(points[n])-10:
                selected.append(dict(chapter=n,id=i,text=text[a:b],context=text[max(0,a-40):min(len(text),b+40)]))
    bible=yaml.safe_load((out/'story_bible.yaml').read_text());client=new_client(out,env,Event())
    value=ask(client,'polish30-capability-ending','只修确证问题原句，不动别的句和段落，不添新剧情。第1章化身首击摧毁赤铜钉后，又主动扣喉、转身凝雾开路，违反每次仅一击/一招且不能代日常行动。后续攻击/转身/凝雾如需保留效果，只写首击残余气劲和沈孤本人的行动，不能补第二次借天或凭空免代价。第30章最后一串远方新身体睁眼、它醒了、下一具身体已经醒了明确开新主线，不能声称是闭合余波。将这整串已定位末句改成传承所得与同伴承诺的当下动作/对白，删掉远方苏醒的新事实，不再造悬念，不加金句总结。S编号只是位置，一句一edit；不把邻句复制进替换。返回edits chapter/id/text/reason与ending.opens_new_mainline/reason。',
        [('圣经机制',pack(bible.get('power',{}))),('编号命中及相邻语境',pack(selected))],'{"edits":[],"ending":{"opens_new_mainline":false,"reason":""}}',cap=6000,input_limit=10000,max_attempts=1)
    allowed={(x['chapter'],x['id']) for x in selected};accepted_all=[]
    for n,text in bodies.items():
        edits=[e for e in value.get('edits',[]) if isinstance(e,dict) and (e.get('chapter'),e.get('id')) in allowed and e['chapter']==n]
        text,accepted=apply(text,edits,[dict(id=i,start=a,end=b) for i,(a,b) in enumerate(points[n],1)],allow_empty=True,allow_style=True)
        row=load(row_path(out,n));row.update(chars=chinese_count(text),style=metrics(text),within_band=LOW<=chinese_count(text)<=HIGH)
        row['limit_edits']=accepted
        if n==30:row['detail'].update(opens_new_mainline=value.get('ending',{}).get('opens_new_mainline'),ending_reason=value.get('ending',{}).get('reason'),double_or_reawakening=False)
        save(body_path(out,n),text);save(row_path(out,n),row);accepted_all.extend(dict(chapter=n,**e) for e in accepted)
    save(marker,dict(edits=accepted_all,ending=value.get('ending')));summarize(out,'xuanhuan-30','complete')


def quotes(root,env):
    from bookengine.polish_checks import scan
    from bookengine.precision import _sentences,apply
    from bookengine.q1 import ask,pack
    for key in BOOKS:
        out=Path(root)/'polish-30'/key
        for path in sorted((out/'chapters').glob('*.json')):
            row=load(path);n=row['number'];marker=out/'steps'/f'quote-{n}-adopted.json'
            if marker.exists():continue
            body=read_body(body_path(out,n));hits=[h for h in scan(body) if 'quote_candidate' in h['categories']]
            if not hits:continue
            points=_sentences(body);client=new_client(out,env,Event())
            numbered=body
            for i,(a,b) in reversed(list(enumerate(points,1))):numbered=numbered[:a]+f'[S{i}]'+numbered[a:]
            value=ask(client,f'quote-{n}','只修引号，不改汉字、标点、空白或段落。全章按原段落连续阅读，S只是位置标号；跨S对白不是每句独立加引号。程序发现此前有缺开引号的对白，导致后面引号连锁错配。找真正缺口并补齐，仅返回需要改的原句id/text/reason，text是该句完整内容，不能吞并邻句或加说话人。',
                [('完整编号正文',numbered),('候选id',pack([h['id'] for h in hits]))],'{"edits":[]}',cap=7000,input_limit=10000,max_attempts=1)
            def bare(s):return re.sub('[“”「」\"‘’]','',s)
            edits=[]
            for e in value.get('edits',[]) if isinstance(value,dict) else []:
                if not isinstance(e,dict):continue
                try:i=int(e.get('id',0))
                except (ValueError,TypeError):continue
                if 1<=i<=len(points) and isinstance(e.get('text'),str) and bare(e['text'])==bare(body[slice(*points[i-1])]):edits.append(dict(e,id=i))
            text,accepted=apply(body,edits,[dict(id=i,start=a,end=b) for i,(a,b) in enumerate(points,1)],allow_style=True)
            row.update(style=metrics(text),chars=chinese_count(text));row['quote_edits']=accepted
            save(body_path(out,n),text);save(path,row);save(marker,dict(edits=accepted,remaining=[h for h in scan(text) if 'quote_candidate' in h['categories']]))
            source=Path(root)/'benchmark'/key
            if read_body(body_path(source,n))==body:
                source_row=load(row_path(source,n));source_row.update(style=metrics(text),quote_edits=accepted)
                save(body_path(source,n),text);save(row_path(source,n),source_row)
            print(key,n,'quote edits',len(accepted),'remaining',len([h for h in scan(text) if 'quote_candidate' in h['categories']]),flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env', type=Path, help='Credentials read only by QClient; unused with --adopt')
    parser.add_argument('--root', type=Path, default=Path('../engine-books'))
    parser.add_argument('--adopt', action='store_true', help='Offline adoption after manual candidate review')
    parser.add_argument('--replay',action='store_true',help='Offline reapply cached proposals after parser changes')
    parser.add_argument('--concepts',action='store_true',help='Check conceptual story text for mixed English')
    parser.add_argument('--settings',action='store_true',help='Repair confirmed setting candidates without regenerating chapters')
    parser.add_argument('--limits',action='store_true',help='Fix the confirmed one-strike limit and closing candidates')
    parser.add_argument('--quotes',action='store_true',help='One focused typography repair, without altering prose words')
    args = parser.parse_args()
    if not args.adopt and not args.replay and args.env is None:
        parser.error('--env is required for model review')
    try:
        if args.quotes:
            quotes(args.root,args.env)
        elif args.limits:
            limits(args.root,args.env)
        elif args.settings:
            settings(args.root,args.env)
        elif args.concepts:
            concepts(args.root,args.env)
        elif args.replay:
            replay(args.root)
        elif args.adopt:
            adopt(args.root)
        else:
            result = run(args.root, args.env)
            return 0 if all(b['status'] == 'complete' for b in result['books'].values()) else 1
    except (OSError, ValueError, RuntimeError) as exc:
        print(f'Stopped ({type(exc).__name__}); no automatic retry or adoption.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
