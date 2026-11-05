"""Q1 immutable design and lazy planning; defaults are plans, never story facts."""
from pathlib import Path
import json
import re

import yaml

from .client import SYSTEM
from tools.runtime_support import ProjectError, QuotaExhausted
from .contracts import choose_contract
from .identity import validate_bible
from .longmemory import long_context
from .longutil import loose_object
from .storage import save
from .text import pack

DATA = yaml.safe_load((Path(__file__).resolve().parents[1] / 'data/q1_schemas.yaml').read_text(encoding='utf-8'))
FIX = yaml.safe_load((Path(__file__).resolve().parents[1]/'data/fix100.yaml').read_text())
GROWTH=yaml.safe_load((Path(__file__).resolve().parents[1]/'data/growth_payoff.yaml').read_text())
BORROWED = yaml.safe_load((Path(__file__).resolve().parents[1]/'data/borrowed_style.yaml').read_text())
DATA['hook_types'] = list(dict.fromkeys([*DATA['hook_types'],*BORROWED['hook_types']]))


def parse(value):
    if isinstance(value,(dict,list)):return value
    cleaned=re.sub(r'^```(?:json|yaml)?\s*|\s*```$','',str(value).strip())
    if cleaned.startswith(('[','- ')):
        try:
            parsed=yaml.safe_load(cleaned)
            if isinstance(parsed,list):return parsed
        except yaml.YAMLError:pass
    try:return loose_object(value)
    except (ValueError,TypeError,AttributeError):return {}


def rows(value):
    return [x for x in value if isinstance(x, dict)] if isinstance(value, list) else []


def model_value(raw):
    value = parse(raw)
    cleaned = re.sub(r'^```(?:json|yaml)?\s*|\s*```$', '', str(raw).strip())
    if not value and cleaned not in ('{}','[]'):
        raise ValueError('Unparseable planning response')
    return value


def integer(value, default=0):
    try:
        return int(value) if not isinstance(value, bool) else default
    except (ValueError, TypeError, OverflowError):
        return default


def defaults(value, template):
    if template is None:return value
    if isinstance(template, dict):
        value = value if isinstance(value, dict) else {}
        return {**value, **{k: defaults(value.get(k), v) for k, v in template.items()}}
    if isinstance(template, list):
        return value if isinstance(value, list) else []
    if isinstance(template, int):
        return integer(value, template)
    return value if isinstance(value, type(template)) else template


def _ask(client, kind, key, sections, redo=False):
    def validate(value):
        if kind == 'prepare':
            raw = value if isinstance(value, dict) else {}
            bible = defaults(raw.get('bible', raw.get('story_bible', raw)), DATA['schemas']['prepare']['bible'])
            validate_bible(bible)
    schema = pack(DATA['schemas'][kind])
    instruction = DATA['rules'] + DATA['instructions'][kind] + FIX['planning']
    overhead = len(getattr(client, 'prefix', '')) + len(SYSTEM) + len(schema) + len(instruction) + 400
    budget = max(0, 10000 - overhead)
    packed = [(key, json.dumps(value, ensure_ascii=False, separators=(',', ':'))) for key, value in sections]
    prompt = instruction + '\n' + long_context(packed, budget)
    old = getattr(client, 'relaxed', False)
    client.relaxed = False
    try:
        r2 = kind != 'prepare' and integer(re.findall(r'\d+', key)[0]) >= (2 if kind == 'volume' else 26)
        stage = ('plan-q1-r2-' if r2 else 'plan-q1-') + key + getattr(client, 'plan_suffix', '')
        if redo:
            stage += '-redo' + (str(redo) if redo != 1 else '')
        return parse(client.stage(stage, prompt, schema, parser=model_value, validate=validate,
                                  max_attempts=6, cap=11000))
    except (QuotaExhausted, ProjectError):
        raise
    except (ValueError, TypeError):
        return {}
    finally:
        client.relaxed = old


def normalize_threads(value, count, total, profile):
    items = rows(value.get('threads')) if isinstance(value, dict) else rows(value)
    aliases = dict(主线='main',暗线='dark',隐藏线='hidden',感情线='romance',成长线='growth',配角线='support',power='growth',foreshadow='hidden',companion='support',love='romance',subplot='dark',consequence='main',aftermath='main')
    merged = {}
    for source in items:
        item = dict(source); kind = str(item.get('kind',item.get('type','main'))).strip()
        item['kind'] = aliases.get(kind,kind)
        if item['kind'] in merged:
            merged[item['kind']]['beats'] = rows(merged[item['kind']].get('beats'))+rows(item.get('beats'))
        else:
            merged[item['kind']] = item
    items = list(merged.values())
    items += [dict(id=k, kind=k) for k in DATA['thread_kinds'] if not any(x.get('kind') == k for x in items)]
    used, ids = set(), set()
    for idx, item in enumerate(items, 1):
        kind = item.get('kind', 'main')
        key = str(item.get('id') or f'{kind}-{idx}')
        while key in used:
            key += '-x'
        used.add(key)
        gap = max(1, integer(item.get('max_gap'), integer(profile.get(kind + '_max_gap'), 10)))
        if kind in ('dark', 'romance'):
            gap = max(1, min(gap, integer(profile.get(kind + '_max_gap'), 10), 10 if kind == 'dark' else gap))
        beats = []
        for n in range(1, count + 1):
            selected = [x for x in rows(item.get('beats')) if integer(x.get('volume')) == n]
            if any(x.get('description') for x in selected): selected = [x for x in selected if x.get('description')]
            minimum = max(2, (min(25, total - (n - 1) * 25) + gap - 1) // gap) if kind in ('dark', 'romance') else 1
            selected += [{} for _ in range(max(0, minimum - len(selected)))]
            for j, beat in enumerate(selected, 1):
                beat = defaults(beat, dict(id='', volume=n, description='', reveal_not_before=0))
                ident = beat['id'] or f'{key}-v{n}-{j}'
                while ident in ids:
                    ident += '-x'
                ids.add(ident)
                beat.update(id=ident, volume=n)
                if kind == 'hidden' and not beat['reveal_not_before']:
                    beat['reveal_not_before'] = (count - 1) * 25 + 1
                beats.append(beat)
        item.update(id=key, kind=kind, max_gap=gap, beats=beats)
    return dict(threads=items)


def prepare(client, book, out, assets, design=False):
    """Create missing YAML once; existing bible/threads are never overwritten."""
    profile_path = Path(assets) / 'prompt_packs' / (book.prompt_pack + '.yaml')
    source = parse(profile_path.read_text(encoding='utf-8')) if profile_path.exists() else {}
    profile = defaults(source.get('q1'), DATA['profile'])
    profile['canon']=dict(GROWTH['packs'][book.prompt_pack],solution_aliases=GROWTH.get('solution_aliases',{}))
    profile['anti_patterns'] = source.get('anti_patterns', [])
    profile.update(hook_types=DATA['hook_types'],dialogue_notes=BORROWED['dialogue_notes'])
    count = (book.chapters + 24) // 25
    paths = [Path(out) / name for name in ('story_bible.yaml', 'threads.yaml')]
    values = [parse(p.read_text(encoding='utf-8')) if p.exists() else None for p in paths]
    if values[0] is not None:validate_bible(values[0])
    if values[0] and values[0].get('canon'):profile['canon']=values[0]['canon']
    rules=yaml.safe_load((Path(__file__).resolve().parents[1]/'data/polish_rules.yaml').read_text())
    names=[dict(book=p.parent.name,names=[c.get('name') for c in parse(p.read_text()).get('characters',[])]) for p in Path(out).parent.glob('*/story_bible.yaml')]
    profile['name_pool']=rules['given_names'];profile['unavailable_names']=[x for x in names if x['book']!=Path(out).name]
    continuations=sorted(Path(out).glob('continuation-*.yaml'),key=lambda p:integer(p.stem.split('-')[-1]))
    if values[0] and book.chapters>max(v.get('end',0) for v in values[0].get('volumes',[])):
        continuations=list(dict.fromkeys([p for p in continuations if integer(p.stem.split('-')[-1])<=book.chapters]+[Path(out)/f'continuation-{book.chapters}.yaml']))
    for extra in continuations:
        if not extra.exists():
            state=json.loads((Path(out)/'state.json').read_text());b=values[0]
            lines=[dict(id=t['id'],kind=t.get('kind'),done=state.get('ledger',{}).get(t['id'],{}).get('done',[]),pending=[x for x in rows(t.get('beats')) if x.get('id') not in state.get('ledger',{}).get(t['id'],{}).get('done',[])]) for t in rows(values[1].get('threads'))]
            canon=dict(power=b.get('power'),summary=b.get('summary','')[:1200],characters=[{k:c.get(k) for k in ('name','role','desire','voice','bottom_line')} for c in b.get('characters',[])],world=b.get('world'))
            ending=Path(out)/f"chapter-{max(v.get('end',0) for v in b.get('volumes',[])):03d}.body.md"
            raw=_ask(client,'continuation',f'extend-{book.chapters}',[('新目标及旧单元结束',dict(total=book.chapters,previous_end=max(v.get('end',0) for v in b.get('volumes',[])))),('原设定',canon),('现状',{k:state.get(k) for k in ('continuity','power_status','dashboard')}),('已完成单元结尾',ending.read_text()[-1500:] if ending.exists() else ''),('节拍',lines)])
            raw=defaults(raw,{});raw['resume_after_chapter']=max(v.get('end',0) for v in b.get('volumes',[]));save(extra,yaml.safe_dump(raw,allow_unicode=True,sort_keys=False))
        patch=defaults(parse(extra.read_text()),{});extra_bible=defaults(patch.get('bible'),{})
        extra_bible['volumes']=list({v.get('number'):v for v in [*rows(values[0].get('volumes')),*rows(extra_bible.get('volumes'))]}.values())
        extra_bible['world']={**values[0].get('world',{}),**defaults(extra_bible.get('world'),{})}
        values[0]=dict(values[0],**{k:v for k,v in extra_bible.items() if k in ('volumes','world','summary')})
        additions=normalize_threads(patch.get('threads',{}),count,book.chapters,profile)['threads']
        for old in rows(values[1].get('threads')):
            new=next((t for t in additions if t.get('id')==old.get('id')),{});ids={b.get('id') for b in rows(old.get('beats'))}
            old['beats']+= [b for b in rows(new.get('beats')) if b.get('id') not in ids]
            old['resume_after_volume']=integer(patch.get('resume_after_chapter'))//25
    if any(x is None for x in values):
        from .benchmarking import concept_design
        selected=concept_design(client,book) if design else {}
        sections=[('用户要求',vars(book)),('固定成长阶梯',profile['canon']['ladder']),('题材',book.prompt_pack),('选定高概念',selected),('读者承诺',source.get('writing_profile_overrides',{})),('已有只读设计',values)]
        previous_error = ''
        for redo in range(6):
            extra = [('关键结构重试', previous_error + '。请给完整人物卡，保留 voice/desire，不要默认空卡。')] if redo else []
            raw = _ask(client, 'prepare', 'prepare', sections + extra, redo)
            raw = raw if isinstance(raw, dict) else {}
            bible = defaults(raw.get('bible', raw.get('story_bible', raw)), DATA['schemas']['prepare']['bible'])
            try:
                validate_bible(bible)
                break
            except ValueError as exc:
                previous_error = str(exc)
                if redo == 5:
                    raise
        bible['canon']=profile['canon'];bible['initial_dashboard'].update(strength=bible['initial_dashboard']['strength'] if bible['initial_dashboard']['strength'] in profile['canon']['ladder']['strength'] else profile['canon']['ladder']['strength'][0],wealth=bible['initial_dashboard']['wealth'] if str(bible['initial_dashboard']['wealth']).endswith(profile['canon']['ladder']['wealth_unit']) else '0'+profile['canon']['ladder']['wealth_unit'],status=bible['initial_dashboard']['status'] if bible['initial_dashboard']['status'] in profile['canon']['ladder']['status'] else profile['canon']['ladder']['status'][0])
        if selected:bible['title']=selected['titles'][0]
        bible['canon'].update(seed=bible['title'],scheduling=GROWTH.get('scheduling',{}))
        bible['summary'] = bible['summary'][:1800]
        bible['characters'] = [defaults(x, DATA['schemas']['prepare']['bible']['characters'][0]) for x in rows(bible['characters'])]
        skeletons = rows(bible['volumes'])
        bible['volumes'] = [dict(defaults(next((x for x in skeletons if integer(x.get('number')) == n), {}),
                            DATA['schemas']['prepare']['bible']['volumes'][0]), number=n, start=(n-1)*25+1,
                            end=min(n*25, book.chapters)) for n in range(1, count+1)]
        generated = [bible, normalize_threads(raw.get('threads', {}), count, book.chapters, profile)]
        if values[0] is None:
            peers=sorted({Path(out).name,*[p.name for p in Path(out).parent.iterdir() if p.is_dir()]});pool=[s+g for s,g in zip(rules['surname']*2,rules['given_names'])][peers.index(Path(out).name)::len(peers)]
            used={n for x in names if x['book']!=Path(out).name for n in x['names'] if n};mapping={}
            for c in bible['characters']:
                name=c['name']
                if name and (name[-2:] not in {n[-2:] for n in pool} or name in used or any(name[-2:]==n[-2:] for n in used)):mapping[name]=(name[:-2] or name[:1])+next(n[-2:] for n in pool if not any(n[-2:]==u[-2:] for u in used));used.add(mapping[name])
                else:used.add(name)
            mapping.update({a[-2:]:b[-2:] for a,b in list(mapping.items()) if len(a)>2})
            if mapping:generated=json.loads(re.sub('|'.join(re.escape(a) for a in sorted(mapping,key=len,reverse=True)),lambda m:mapping[m[0]],json.dumps(generated,ensure_ascii=False)))
        validate_bible(generated[0])
        for i, path in enumerate(paths):
            if values[i] is None:
                save(path, yaml.safe_dump(generated[i], allow_unicode=True, sort_keys=False))
                values[i] = generated[i]
    save(Path(out)/'names.json',dict(books=names,assigned=[c.get('name') for c in values[0].get('characters',[])],legacy_names_preserved=True))
    return defaults(values[0], DATA['schemas']['prepare']['bible']), normalize_threads(values[1],count,book.chapters,profile), profile


def _beats(threads, number, state=None):
    return [dict(b, thread_id=t.get('id'), kind=t.get('kind'), overdue=integer(b.get('volume'))<number) for t in rows(threads.get('threads'))
            for b in rows(t.get('beats')) if integer(b.get('volume'))>integer(t.get('resume_after_volume')) and (integer(b.get('volume')) == number or
            (state is not None and integer(b.get('volume'))<number and b.get('id') not in state.get('ledger',{}).get(t.get('id'),{}).get('done',[])))]


def callbacks(history):
    """Only observed first-volume events, never future plans."""
    if isinstance(history,dict):history=history.get('events',history.get('chapters',list(history.values())))
    result=[]
    for row in rows(history):
        if isinstance(row.get('events'),list):items=callbacks(row['events'])
        else:
            n=integer(row.get('chapter',row.get('number')));event=row.get('event') or row.get('summary')
            items=[dict(chapter=n,event=event)] if not {'start','end'} & row.keys() and 1<=n<=25 and isinstance(event,str) and event.strip() else []
        result.extend(x for x in items if x not in result)
    return result[:3]


def volume(client, bible, threads, number, total, state, history, feedback, redo=False):
    skeleton = next((x for x in rows(bible.get('volumes')) if integer(x.get('number')) == number), {})
    beats, events = _beats(threads, number, state), callbacks(history)
    ladder=bible.get('canon',{}).get('ladder',{}).get('strength',[]);old=state.get('dashboard',{}).get('strength')
    target=ladder[min(ladder.index(old)+1,len(ladder)-1)] if old in ladder else None
    raw = _ask(client, 'volume', f'volume-{number}', [('固定人物卡',bible.get('characters',[])),('成长阶梯/当前仪表盘/收获与解法账本', [bible.get('canon'),state.get('dashboard'),state.get('gains',[])[-25:],state.get('solutions',[])[-30:]]),('本卷固定走向与节拍', [skeleton, beats]),
               ('指定卷范围', dict(number=number, start=(number-1)*25+1, end=min(number*25, total))),
               ('反馈', feedback), ('已发生历史', events), ('实际状态', state), ('圣经', bible)], redo)
    value = defaults(dict(skeleton, **raw) if isinstance(raw, dict) else skeleton, DATA['schemas']['volume'])
    value.update(number=number, start=(number-1)*25+1, end=min(number*25, total), beats=beats, feedback=feedback)
    if target:value['expected_dashboard']=dict(value.get('expected_dashboard') or {},strength=target)
    value['callback_events'] = events
    return value


def schedule(start, end, profile, recent=(),state=None):
    """Return future rhythm requirements, never an observed payoff/progress."""
    previous = rows(list(recent)) if isinstance(recent,(list,tuple)) else []
    mix=defaults(profile.get('chapter_type_mix'),DATA['profile']['chapter_type_mix'])
    weights={k:max(1,integer(v,1)) for k,v in mix.items() if k in DATA['profile']['chapter_type_mix']}
    gap = max(1,min(5,integer(profile.get('payoff_max_gap'),4)))
    since = next((i for i,r in enumerate(reversed(previous)) if r.get('payoff')),len(previous))
    meeting = max([integer(r.get('number'),start-len(previous)+i) for i,r in enumerate(previous)
                   if integer(r.get('meeting_scenes')) or any(s.get('kind')=='meeting' for s in rows(r.get('scenes')))] or [start-5])
    result = [];reservation=dict(state or {},solutions=list((state or {}).get('solutions',[])))
    for n in range(start,end+1):
        choices=[k for k in weights if len(previous)<2 or not all(r.get('type')==k for r in previous[-2:])]
        kind=max(choices,key=lambda k:(len(previous)+1)*weights[k]/sum(weights.values())-sum(r.get('type')==k for r in previous))
        hooks = DATA['hook_types']; ordered=hooks[(n-1)%len(hooks):]+hooks[:(n-1)%len(hooks)]
        hook = next(h for h in ordered if sum(r.get('hook_type')==h for r in previous[-9:])<2 and (h!='clue' or all(r.get('hook_type')!='clue' for r in previous[-4:])))
        structures=DATA['structures'];structure=structures[(n-1)%len(structures)]
        if previous and previous[-1].get('structure')==structure:structure=structures[n%len(structures)]
        slot=dict(number=n,type=kind,hook_type=hook,structure=structure,payoff_due=n==3 or since+1>=gap,
                  meeting_allowed=n-meeting>=5,required_event='',action_required=n%5!=0)
        if profile.get('canon'):slot.update(choose_contract(profile['canon'],previous,n,state=reservation));slot['reserved_payoff_type']=slot['payoff_type'] if slot['payoff_due'] else None
        since=0 if slot['payoff_due'] else since+1
        if slot['meeting_allowed']:meeting=n
        result.append(slot);previous.append(slot)
        if slot.get('solution_type'):reservation['solutions'].append(dict(chapter=n,type=slot['solution_type'],content='计划未兑现'))
    return result


def beat_slice(threads,vol,chapter,state):
    lines=rows(threads.get('threads'));idx=chapter['number'];ledger=state.get('ledger',{})
    main=next((t for t in lines if t.get('kind')=='main'),None)
    others=sorted((t for t in lines if t is not main),key=lambda t:(t['id'] not in chapter.get('threads',[]),ledger.get(t['id'],{}).get('last',0)))
    chosen=[t for t in [main,*others[:1]] if t];result=[]
    for line in chosen:
        record=ledger.get(line['id'],{});done=record.get('done',[])
        beat=next((b for b in rows(line.get('beats')) if integer(line.get('resume_after_volume'))<b.get('volume',0)<=vol['number'] and b.get('id') not in done),{})
        if not beat and idx-record.get('last',0)<8:continue
        result.append(dict(thread=line['id'],beat=beat.get('id',''),event=beat.get('description','已有矛盾产生行动后果，不完成后卷节拍')[:100],
                           must_appear=idx-record.get('last',0)>=8,reveal_not_before=beat.get('reveal_not_before',0)))
    return result


def outlines(client, bible, threads, profile, volume, start, end, state, recent, history, alerts, redo=False):
    end = min(end, start+9, integer(volume.get('end'), end))
    if end < start:
        return []
    rhythm = schedule(start, end, profile, recent,state);planned_state=dict(state,ledger={k:dict(v) for k,v in state.get('ledger',{}).items()})
    for slot in rhythm:
        selected=beat_slice(threads,volume,slot,planned_state);slot['threads']=[x['thread'] for x in selected]
        for item in selected:planned_state['ledger'].setdefault(item['thread'],{})['last']=slot['number']
    from .craft import recall_events
    total=integer(bible.get('chapters',bible.get('total_chapters')),max(v.get('end',0) for v in bible.get('volumes',[]) or [{}]))
    saved=[json.loads(p.read_text()) for p in sorted((client.out/'chapters').glob('*.json'))][:25] if getattr(client,'out',None) else []
    events=recall_events(client.out,history or saved,end,total,volume['end']) if getattr(client,'out',None) else callbacks(history) if start<=100<=end else []
    raw = _ask(client, 'outlines', f'chapters-{start}-{end}', [('本批完整范围',dict(start=start,end=end,count=end-start+1)),('固定人物卡',[{k:c.get(k) for k in DATA['plan_card_fields']} for c in bible.get('characters',[])]),('固定成长阶梯与当前仪表盘',[profile.get('canon'),state.get('dashboard')]),('稳定节奏排期，每章只执行指定两条线',[{k:r[k] for k in DATA['plan_slot_fields'] if k in r} for r in rhythm]),('爽点落实标准',GROWTH.get('payoff_criteria',{})),
               ('尾章必须融入当下冲突的首卷事实与原文，不复述梗概', events), ('本卷与固定节拍', [volume, _beats(threads, integer(volume.get('number')),state)]),
               ('警报与已用解法',[alerts,state.get('solutions',[])[-29:]]), ('圣经摘要', bible.get('summary', '')[:1800]),
               ('人物与能力', [bible.get('characters', []), bible.get('power', {})]),
               ('实际状态及近章', [state,{r.get('number'):r.get('summary') for r in recent[-5:]}])], redo)
    source = rows(raw.get('chapters')) if isinstance(raw, dict) else rows(raw)
    mapped = {}
    for i, item in enumerate(source):
        mapped.setdefault(integer(item.get('number'), start+i), item)
    result = []
    for slot in rhythm:
        n = slot['number']
        chapter = defaults(mapped.get(n), DATA['schemas']['outlines']['chapters'][0])
        chapter.update(number=n, threads=slot['threads'], type=chapter['type'] if chapter['type'] in DATA['profile']['chapter_type_mix'] else slot['type'],
                       hook_type=chapter['hook_type'] if chapter['hook_type'] in DATA['hook_types'] else slot['hook_type'])
        chapter.update(structure=slot['structure'],required_event=slot['required_event'],payoff_due=slot['payoff_due'],payoff_type=slot.get('payoff_type'),hook=chapter.get('hook') if chapter['hook_type']==slot['hook_type'] else '末钩目标'+slot['hook_type']+'，用本场面既有冲突重构，不沿用旧结尾。',hook_type=slot['hook_type'],structure_rewrite='指定结构'+slot['structure']+'优先覆盖冲突旧场景，必须围绕它重新设计行动后果。')
        chapter['solution_type']=chapter.get('solution_type') or slot.get('solution_type');chapter.update(solution_avoid=slot.get('solution_avoid',[]),group_allowed=slot.get('group_allowed'),growth_obligation=slot.get('growth_obligation'),**{k:slot.get(k) for k in ('growth_setup','upgrade_due','next_upgrade')})
        chapter['scenes'] = [defaults(x, DATA['schemas']['outlines']['chapters'][0]['scenes'][0]) for x in rows(chapter['scenes'])]
        chapter['callback_events'] = events if n==total else events[:1] if n==volume['end'] else []
        result.append(chapter)
    return result
