"""Bounded completion of identified editorial fixes, using existing drafts."""
import json
import re
from pathlib import Path
from voice_scene_run import (PRIVATE,KEY,ENV,RULES,MODEL,VoiceClient,call,publish,
                             audit,chapter_checks,scene_hits,metrics,q1,validate_bible)
from bookengine.storage import load,save,now
from bookengine.text import _paragraph_spans
from dialogue_metrics import dialogues,spoken_dialogue,apply_dialogue_edits
from dialogue_diff import dialogue_diff
from dialogue_client import totals

def locator(spec):
    path=Path(spec['path']);return path.parent.name if path.name=='variant' else path.name

def change_paragraph(text,pid,find,replacement):
    a,b=_paragraph_spans(text)[pid-1];part=text[a:b]
    if find not in part:raise ValueError('Reviewed substring not present in paragraph')
    return text[:a]+part.replace(find,replacement,1)+text[b:]

def apply_review(key):
    review=PRIVATE/'review.json';client=VoiceClient(PRIVATE/'t15/editorial',ENV,PRIVATE,'t15')
    by_id={locator(s):s for s in key['groups'].values()}
    specs=[('5c91cbd7b29748f4adf8df5c4b3c4e92',36,'photo'),('5c91cbd7b29748f4adf8df5c4b3c4e92',68,'seat'),('c62268bd82f54f779f2be7de8ff7ed73',47,'threshold')]
    jobs=[]
    for ident,pid,kind in specs:
        s=by_id[ident];out=Path(s['path']);a=(out/'before-dialogue.md').read_text();b=(out/'final.md').read_text();aa,ab=_paragraph_spans(a)[pid-1];ba,bb=_paragraph_spans(b)[pid-1]
        jobs.append(dict(key=ident+':'+str(pid),pid=pid,kind=kind,before=a[aa:ab],after=b[ba:bb],requirement={'photo':'照片还在别人手机上，给目击者的消息应明确让他拍摄/保存新照片，不能当他已持有原照片。只重说这一条消息，给quote_text，不改其他句。','seat':'按固定座位将命令里的后排改为副驾，只给最小find/replace，两个版本均须能匹配。','threshold':'按固定不入正屋约束，跨进来的动作改为停在门槛外，其他字不动，给最小find/replace。'}[kind]))
    raw=call(client,'reviewed-micro-fixes',MODEL,'只处理已人工确认的3处事实衔接，不改文风、不增剧情。引号外只改点名位置词，照片一项只换那条对白。每个key返回find/replace或quote_text。',[('三处原文与约束',q1.pack(jobs))],'{"edits":[{"key":"","find":"","replace":"","quote_text":""}]}',cap=3000)
    fixes={r['key']:r for r in raw.get('edits',[]) if isinstance(r,dict) and r.get('key')}
    for job in jobs:
        ident,pid=job['key'].split(':');s=by_id[ident];out=Path(s['path']);flag=out/f"review-{pid}.json"
        if flag.exists():continue
        edit=fixes[job['key']]
        for filename in ('before-dialogue.md','final.md'):
            path=out/filename;text=path.read_text()
            if job['kind']=='photo':
                a,b=_paragraph_spans(text)[int(pid)-1];rows=[r for r in dialogues(text) if a<=r['start']<b and '照片' in r['text']]
                if len(rows)!=1:raise ValueError('Photo correction must match exactly one dialogue slot')
                r=rows[0];text,accepted=apply_dialogue_edits(text,[dict(id=r['id'],original=r['text'],text=edit['quote_text'])]);assert len(accepted)==1
            else:text=change_paragraph(text,int(pid),edit['find'],edit['replace'])
            save(path,text)
        save(flag,dict(kind=job['kind'],edit=edit,applied_to_both=True))
    # Fix an internally contradictory plan, preserving its original file.
    s=by_id['5c91cbd7b29748f4adf8df5c4b3c4e92'];out=Path(s['path']);path=out/'plan.repaired.json'
    if not path.exists():
        plan=load(Path(s['plan'])/'plan.json');text=(out/'final.md').read_text()
        raw=call(client,'repair-family-card','MiniMax-M3','原计划误把司机与失踪姐姐写成同名，且漏了姐姐卡。只修这一处设计矛盾，其他已有卡全保留。以正文明确且一贯使用的姐姐姓名补一张完整人物卡，不能把其他串姓/昵称追认为新角色。给纠正premise、sister_card(name/role/gender/voice/desire/aliases)、relations(from/type/to)，两姐妹不能同名或同一人。',[('原计划',q1.pack(plan)),('已完成正文',text)],'{"premise":"","sister_card":{"name":"","role":"support","gender":"女","voice":"","desire":"","aliases":[]},"relations":[{"from":"","type":"sister","to":""}]}',cap=2500)
        card=raw['sister_card'];hero=next(c for c in plan['characters'] if c['role']=='protagonist')
        assert card['name']!=hero['name'] and card['name'] in text
        plan['characters']=[c for c in plan['characters'] if c['name']!=card['name']]+[card];plan.update(premise=raw['premise'],relations=raw['relations']);validate_bible(plan)
        assert all(r['from']!=r['to'] for r in plan['relations']);save(path,plan)
    s['plan_file']=str(path);save(KEY,key);save(review,dict(applied=jobs,finished=now()))

def complete_dialogue(spec):
    out=Path(spec['path']);text=(out/'before-dialogue.md').read_text();final=(out/'final.md').read_text();diff=dialogue_diff(text,final)
    if not spec['strong'] or diff['substantive_ratio']>=.4:return
    client=VoiceClient(out/'missing-dialogue',ENV,PRIVATE,spec['track']);plan=load(spec.get('plan_file',str(Path(spec['plan'])/'plan.json')))
    old=dialogues(text);new=dialogues(final);lines={r['slot']:r for r in diff['lines']}
    pending=[(i,r) for i,r in enumerate(old,1) if spoken_dialogue(text,r) and not lines[i]['substantive']]
    pending.sort(key=lambda pair:-q1.chinese_count(pair[1]['text']));pending=pending[:80];pending.sort()
    targets=[[i,r['text']] for i,r in pending]
    cards=[{k:c.get(k) for k in ('name','voice','gender','habits','taboos')} for c in plan['characters']]
    raw=call(client,'complete-missing-slots',MODEL,'此前目标清单在输入尾部被裁剪，保留已实质改写的行，只补这份清单。每个槽整句换口语表达，至少75%本次目标有>=25%且至少2汉字内容变化。不要只删虚词/修标点；保留否定、条件、数量、姓名、身份、先后与事实，不加笑话设定。只改引号内，用原slot整数，无外层引号。',[('全部补写目标(slot,原句)',q1.pack(targets)),('口吻卡',q1.pack(cards)),('剧情只读摘要',q1.pack(plan['outline']))],'{"edits":[{"slot":1,"text":"整句换说法"}]}',cap=10000)
    edits=[dict(id=a['id'],original=a['text'],text=b['text']) for a,b in zip(old,new) if a['text']!=b['text'] and spoken_dialogue(text,a)]
    wanted={i for i,_ in pending}
    for e in raw.get('edits',[]):
        idx=e.get('slot');idx=int(idx) if str(idx).isdigit() else None
        if idx not in wanted:continue
        r=old[idx-1];edits=[x for x in edits if x['id']!=r['id']]+[dict(id=r['id'],original=r['text'],text=e['text'])]
    revised,adopted=apply_dialogue_edits(text,edits);candidate=dialogue_diff(text,revised)
    check=call(client,'missing-facts',MODEL,RULES['dialogue_facts'],[('仅新增槽位',q1.pack([r for r in candidate['lines'] if r['slot'] in wanted and r['before']!=r['after']]))],'{"issues":[{"slot":1,"problem":"","confirmed":true}]}',cap=3000)
    rejected={int(r['slot']) for r in check.get('issues',[]) if r.get('confirmed') is True and str(r.get('slot')).isdigit()}
    adopted=[e for e in adopted if next(i for i,r in enumerate(old,1) if r['id']==e['id']) not in rejected]
    revised,adopted=apply_dialogue_edits(text,adopted);diff=dialogue_diff(text,revised)
    assert diff['narration_unchanged'] and diff['slot_alignment_unchanged'];save(out/'final.md',revised);save(out/'missing-dialogue.json',dict(diff,model_facts=check,targets=len(targets),calls=len(client.calls)))

def main():
    key=load(KEY);apply_review(key)
    for s in key['groups'].values():complete_dialogue(s)
    for s in key['groups'].values():
        out=Path(s['path']);plan=load(s.get('plan_file',str(Path(s['plan'])/'plan.json')));text=(out/'final.md').read_text();r=load(out/'result.json');r['initial_final']=r.get('initial_final',r['final'])
        r.update(chars=q1.chinese_count(text),metrics=metrics(text),within_band=1870<=q1.chinese_count(text)<=2700)
        r['final']=dict(program=chapter_checks(text,plan['characters']),scene=scene_hits(text),model=r['final']['model'],english=q1.english_hits(text))
        if s['strong']:
            d=dialogue_diff((out/'before-dialogue.md').read_text(),text);r['dialogue']={k:d[k] for k in ('total_lines','changed_lines','substantive_ratio','mean_edit_distance','mean_literal_edit_distance','narration_unchanged')};save(out/'dialogue-diff.final.json',d)
        save(out/'result.json',r)
    publish(key);save(PRIVATE/'RESULT.json',dict(status='complete',samples=12,finished=now(),**totals(PRIVATE)))
    print('Bounded repairs and missing-slot completion finished',flush=True)

if __name__=='__main__':main()
