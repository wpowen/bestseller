"""Recover missing metadata from already-paid reviews; never edit chapter prose."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from bookengine.client import content_of
from bookengine.longutil import loose_object
from bookengine.q1_plan import DATA
from bookengine.q1_quality import normalize_scenes
from bookengine.storage import save

p=argparse.ArgumentParser();p.add_argument('out',type=Path);p.add_argument('--after',type=int,required=True);a=p.parse_args()
meta=json.loads((a.out/'run.json').read_text())
if meta['status']=='running':raise SystemExit('Wait for the safe checkpoint before metadata recovery')
calls=json.loads((a.out/'calls.json').read_text())
for path in sorted((a.out/'chapters').glob('*.json')):
 r=json.loads(path.read_text());n=r['number']
 if n<=a.after or r.get('hook_type') and r.get('scene_classification')=='model':continue
 stage=f'p8-ch{n}-review';c=next((x for x in reversed(calls) if x['stage']==stage and x['status']=='ok'),None)
 if not c or c.get('finish_reason')!='length':continue
 detail=json.loads((a.out/'steps'/f'{stage}-adopted.json').read_text())
 raw=loose_object(content_of(json.loads((a.out/'responses'/f"{c['seq']:03d}.json").read_text())))
 if len(raw.get('edits',[]))!=len(detail.get('edits',[])):continue
 hook=raw.get('actual_hook_type') or raw.get('hook_type');kinds=normalize_scenes(raw.get('scene_types'))
 changed=False
 if not r.get('hook_type') and hook in DATA['hook_types']:r['hook_type']=hook;changed=True
 if r.get('scene_classification')!='model' and kinds and 'unknown' not in kinds:
  r.update(scene_types=kinds,scenes=len(kinds),meeting_scenes=kinds.count('meeting'),scene_classification='model');changed=True
 if changed:
  if not r.get('type') and raw.get('actual_type') in DATA['profile']['chapter_type_mix']:r['type']=raw['actual_type']
  if not r.get('payoff_observed') and 'actual_payoff' in raw:r.update(payoff=raw['actual_payoff'] or None,payoff_observed=True)
  r['narrative_classification']='model' if r.get('type') and r.get('hook_type') else 'unknown'
  r['metadata_recovered']=True;save(path,r);print('recovered_missing_review_fields',n,hook)
