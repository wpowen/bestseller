"""Read back a complete real book, replay edits and verify source-preserving contracts."""
import argparse
from itertools import groupby
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from deai.io import chapters,read_json,read_text,save
from deai.text import invariant,replace,paragraphs,count

def verify(book):
    rows=[];errors=[]
    for n,source in chapters(book):
        old=read_text(source);work=book/f'final/.deai/{n}';out=book/f'final/chapter-{n}.md';path=book/f'final/chapter-{n}.deai.json'
        if not out.exists() or not path.exists():errors.append(f'{n}:missing final');continue
        text=read_text(out);r=read_json(path);checks=r.get('checks',{});bad=[]
        if not r.get('complete') or r.get('status') not in ('polished','unchanged'):bad.append('incomplete')
        if read_text(work/'input.md')!=old:bad.append('source differs from processed input')
        if read_text(work/'output.md')!=text:bad.append('output differs from checkpoint')
        bad+=invariant(old,text,{},checks.get('entities',[]),True)
        replay=old
        for _,edits in groupby(r.get('edits',[]),lambda e:(e.get('round'),e.get('source'))):
            group=list(edits)
            if any(replay[e['start']:e['end']]!=e['before'] for e in group):bad.append('edit before/offset mismatch');break
            replay=replace(replay,group)
        if replay!=text:bad.append('unrecorded text change')
        ending_exact=paragraphs(old)[-1]['text']==paragraphs(text)[-1]['text']
        rows.append(dict(chapter=int(n),status=r['status'],original_chars=count(old),final_chars=count(text),edits=len(r.get('edits',[])),hits_before=len(r.get('hits',[])),hits_after=len(checks.get('remaining_hits',[])),seconds=r.get('seconds'),ending_exact=ending_exact,errors=bad))
        errors.extend(f'{n}:{reason}' for reason in bad)
    result=dict(chapters=len(rows),expected=len(chapters(book)),errors=errors,rows=rows)
    save(book,'final/.deai/verification.json',result);return result

if __name__=='__main__':
    import json
    parser=argparse.ArgumentParser();parser.add_argument('book',type=Path);args=parser.parse_args();result=verify(args.book.resolve())
    print(json.dumps(result,ensure_ascii=False,indent=2));raise SystemExit(bool(result['errors']) or result['chapters']!=result['expected'])
