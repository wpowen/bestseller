"""Apply previously ignored candidates from saved responses; never call a model."""
import argparse
import fcntl
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from bookengine.client import content_of
from bookengine.length import normalize_candidates,choose_edits
from bookengine.longutil import loose_object
from bookengine.q1 import clean
from bookengine.q1_quality import metrics
from bookengine.polish_checks import scan
from bookengine.storage import load,save
from bookengine.text import _paragraph_spans,chinese_count


def recover(out):
    out=Path(out);results=[]
    with (out/'runner.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        for c in load(out/'calls.json'):
            stage=c['stage']
            if not stage.startswith('p7-ch') or not stage.endswith('-length'):continue
            idx=int(stage.split('-')[1][2:]);path=out/f'chapters/{idx:03d}.json'
            if not path.exists():continue
            row=load(path);body_path=out/f'chapter-{idx:03d}.body.md';body=body_path.read_text()
            if row.get('length_recovered') or 1870<=chinese_count(body)<=2700:continue
            source=(out/f'chapter-{idx:03d}.draft.md').read_text();dialogue=out/'steps'/f'p7-ch{idx}-dialogue.md'
            if dialogue.exists():source=clean(dialogue.read_text())
            response=out/f"responses/{c['seq']:03d}.json"
            if not response.exists():continue
            try:value=loose_object(content_of(load(response)))
            except (ValueError,TypeError):continue
            parsed=normalize_candidates(value,source);old=load(out/'steps'/f'{stage}.json')
            if len(parsed['edits'])<=len(old.get('edits',[])):continue
            a,b=_paragraph_spans(source),_paragraph_spans(body);mapped=[]
            for e in parsed['edits']:
                original=source[slice(*a[e['paragraph']-1])];found=[i for i,(left,right) in enumerate(b,1) if body[left:right]==original]
                if len(found)!=1:continue
                if any(original.count(x)-original.count(y)!=e['text'].count(x)-e['text'].count(y) for x,y in ('“”','「」')):continue
                mapped.append(dict(paragraph=found[0],text=e['text']))
            text,accepted=choose_edits(body,mapped,2200)
            if not accepted:continue
            before=sum('quote_candidate' in h['categories'] for h in scan(body));after=sum('quote_candidate' in h['categories'] for h in scan(text))
            if after>before:continue
            detail=dict(seq=c['seq'],old_candidates=len(old.get('edits',[])),recovered_candidates=len(parsed['edits']),applied=len(accepted),before=chinese_count(body),after=chinese_count(text))
            row.update(chars=detail['after'],within_band=1870<=detail['after']<=2700,style=metrics(text),length_recovered=detail)
            save(body_path,text);save(path,row);save(out/'steps'/f'{stage}.json',parsed);results.append(dict(chapter=idx,**detail))
        save(out/'length-recovery.json',results)
    print(results)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('out',type=Path);args=parser.parse_args();recover(args.out)
