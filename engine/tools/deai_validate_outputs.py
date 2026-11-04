"""Apply new hard-check regressions to existing final files, never draft files."""
from pathlib import Path
import sys,json
ENGINE=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ENGINE))
from deai.text import rollback_invalid,invariant,count
from deai.io import read_json,read_text,save
from deai.client import MiniMax
from deai.flow import Session,score
ROOT=ENGINE.parent.parent/'engine-books/blind-5/private/experiment';ENV=ENGINE.parent/'../../.env'

def validate(original,output,report,context,work):
    old=read_text(original);final=read_text(output);meta=read_json(report);updated,kept,rolled=rollback_invalid(old,final,meta.get('edits',[]),context)
    if not rolled:
        assert not invariant(old,final,context,whole=True)
        if not meta.get('checks',{}).get('diagnostics'):
            meta.setdefault('checks',{})['diagnostics']=Session(MiniMax(work,ENV),context).diagnostics(old);save(report.parent,report.name,meta)
        return
    client=MiniMax(work,ENV);session=Session(client,context)
    hits,unknown=session.classify(updated,'preservation-rollback-recheck')
    if unknown:raise RuntimeError('Rollback recheck incomplete')
    changed={e['pid'] for e in kept};prior={(h.get('pid'),h.get('type'),h.get('text')) for h in meta.get('hits',[])}
    bad={h['pid'] for h in hits if h['pid'] in changed and (h['pid'],h['type'],h['text']) not in prior}
    if bad:
        updated,kept,extra=rollback_invalid(old,updated,kept,context,bad);rolled+=extra;hits,unknown=session.classify(updated,'new-style-rollback-recheck')
        if unknown:raise RuntimeError('Second rollback recheck incomplete')
    assert not invariant(old,updated,context,whole=True)
    meta.update(text=updated,edits=kept,after_score=score(hits,updated),status='polished' if kept else 'unchanged');meta.setdefault('warnings',[]).append(dict(rollback='new_hard_contract',paragraphs=sorted(set(rolled))))
    meta.setdefault('checks',{}).update(programmatic=[],final_chars=count(updated),diagnostics=session.diagnostics(old));save(output.parent,output.name,updated);save(report.parent,report.name,meta)
    if (work/'output.md').exists():save(work,'output.md',updated)
    print(output,'rolled',rolled,'remaining',len(kept),flush=True)

if __name__=='__main__':
    for folder in sorted((ROOT/'books').iterdir()):
        if (folder/'final/chapter-001.md').exists():validate(folder/'chapter-001.body.md',folder/'final/chapter-001.md',folder/'final/chapter-001.deai.json',read_json(folder/'deai-context.json'),folder/'final/.deai/001')
    for mode in ('scene','actor'):
        folder=ROOT/'ab'/mode
        if (folder/'final.md').exists():validate(ROOT/'books/city-flash/chapter-001.body.md',folder/'final.md',folder/'result.json',read_json(ROOT/'books/city-flash/deai-context.json'),folder)
