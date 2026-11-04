"""Compile requested reading excerpts; calibrate completed pilots with M3 once."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import yaml
from bookengine.q1 import QClient,DATA
from bookengine.longutil import loose_object
from bookengine.text import pack
from bookengine.storage import save,load
from bookengine.batch import usage_report

ROOT=Path('../engine-books/benchmark')

def calibrate(d):
    bible=yaml.safe_load((d/'story_bible.yaml').read_text());rows=[load(p) for p in sorted((d/'chapters').glob('*.json'))]
    for model,name in (('MiniMax-M3.1-Flash','flash-editor.json'),('MiniMax-M3','m3-editor.json')):
        if (d/name).exists():continue
        c=QClient(d,'../../.env','',model=model);c.relaxed=True
        if model=='MiniMax-M3':c.thinking={'type':'disabled'}
        manuscript='\n\n'.join(f"第{r['number']}章\n"+(d/f"chapter-{r['number']:03d}.body.md").read_text() for r in rows)
        criteria='\n'.join(line for line in yaml.safe_load(DATA.read_text())['editor'].splitlines() if '候选' not in line)
        selected=load(d/'concept/SELECTED.json')
        prompt=criteria+'\n以下是完整30章，依正文评分，规划不是已兑现成果。再列最严重的3处句级/跨章逻辑缺口；找不到则空。ai_examples给章号及正文原句，不编原句。\n选题与能力约定：'+pack(dict(concept={k:selected.get(k) for k in ('pitch','gift','first_payoff','sustainability')},power=bible.get('power'),characters=bible.get('characters')))[:5000]+'\n完整正文：\n'+manuscript
        result=c.stage('whole-30-calibration-'+model,prompt,'{"scores":{"追读欲":0,"爽点兑现":0,"人物魅力":0,"新鲜感":0,"多线推进":0,"AI味":0},"reasons":{},"ai_examples":[{"chapter":1,"quote":"","reason":""}],"logic_issues":[]}',parser=loose_object,cap=10000,max_attempts=1)
        result=result if isinstance(result,dict) else {}
        result['evaluation']='model_editorial_not_human';result['model']=model;save(d/name,result)
        meta=load(d/'run.json');meta['usage']=usage_report(c.calls);save(d/'run.json',meta)

if __name__=='__main__':
    books=[ROOT/k for k in ('xuanhuan-30','suspense-30')];sections=[];concepts=[]
    for d in books:
        bible=yaml.safe_load((d/'story_bible.yaml').read_text());sections.append('# '+bible['title'])
        for n in (1,2,3,10,30):
            p=d/f'chapter-{n:03d}.body.md'
            if p.exists():sections.append(f'## 第{n}章\n\n'+p.read_text().strip())
        p=d/'concept/CANDIDATES.md'
        if p.exists():concepts.append('# '+d.name+'\n\n'+p.read_text())
    save(ROOT/'READ-ME-FIRST.md','\n\n'.join(sections)+'\n');save(ROOT/'concept/CANDIDATES.md','\n\n'.join(concepts)+'\n')
    if all(load(d/'run.json')['status']=='complete' for d in books):
        with ThreadPoolExecutor(max_workers=2) as pool:list(pool.map(calibrate,books))
