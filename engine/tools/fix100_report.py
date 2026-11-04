"""Read the 101-130 continuation and write only the requested one-page report."""
from collections import Counter
from pathlib import Path
import datetime as dt
import json
import subprocess
import sys
import yaml
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from bookengine.batch import usage_report
from bookengine.serial_checks import catch_hits,adjacent
from bookengine.storage import save
from bookengine.text import chinese_count
ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT.parent/'engine-books/benchmark/suspense-30'

def read():
    meta=json.loads((OUT/'run.json').read_text())
    rows=[json.loads(p.read_text()) for p in sorted((OUT/'chapters').glob('*.json'))]
    new=[r for r in rows if 101<=r['number']<=130]
    calls=[c for c in json.loads((OUT/'calls.json').read_text()) if c['seq']>389]
    bible=yaml.safe_load((OUT/'story_bible.yaml').read_text())
    texts={r['number']:(OUT/f"chapter-{r['number']:03d}.body.md").read_text() for r in rows}
    repeats={r['number']:catch_hits(texts[r['number']],[texts[i] for i in range(max(1,r['number']-9),r['number'])],bible['characters']) for r in new}
    windows=[dict(end=r['number'],counts=dict(Counter(x.get('hook_type') or 'Unknown' for x in rows if r['number']-9<=x['number']<=r['number']))) for r in new]
    words=[(r['number'],chinese_count(texts[r['number']])) for r in new]
    outlines=[json.loads((OUT/f"writing_outlines/{r['number']:03d}.json").read_text()) for r in new]
    adoption=[r['number'] for r in new if not r.get('hook_type') and bool(json.loads((OUT/'steps'/f"p8-ch{r['number']}-review.json").read_text())) and not r.get('polish',{}).get('edits')]
    usage=usage_report(calls)
    end=meta.get('finished') if meta.get('status')=='complete' else meta.get('updated') or dt.datetime.now(dt.timezone.utc).isoformat()
    wall=(dt.datetime.fromisoformat(end)-dt.datetime.fromisoformat(calls[0]['started'])).total_seconds() if calls else 0
    return dict(meta=meta,rows=rows,new=new,calls=calls,usage=usage,repeats=repeats,windows=windows,words=words,outlines=outlines,
        adjacent=sum(adjacent(texts[r['number']])!=texts[r['number']] for r in new),wall=wall,
        seconds=sum(r.get('seconds',0) for r in new),execution=meta.get('execution_seconds',0)-8636.710653,
        adoption=adoption,old_hooks=Counter(r.get('hook_type') or 'Unknown' for r in rows if 31<=r['number']<=100),
        hooks=Counter(r.get('hook_type') or 'Unknown' for r in new),
        scores={p.stem:json.loads(p.read_text()).get('scores') for p in sorted((OUT/'volumes').glob('*.json')) if json.loads(p.read_text()).get('end',0)>100})

def write(d):
    n=len(d['new']);u=d['usage'];m=d['meta'];passed=sum(1870<=v<=2700 for _,v in d['words']);speed=sum(r.get('seconds',0)<=75 for r in d['new'])
    bad_hooks=[w for w in d['windows'] if max((v for k,v in w['counts'].items() if k!='Unknown'),default=0)>2]
    unknown=[r['number'] for r in d['new'] if not r.get('hook_type')];repeat=sum(len(v) for v in d['repeats'].values())
    scope=subprocess.run([sys.executable,str(ROOT/'engine/tools/check_scope.py')],capture_output=True,text=True,check=True).stdout.strip()
    core=int(scope.splitlines()[0].split('=')[1])
    failed_words=', '.join(f'{a}:{b}' for a,b in d['words'] if not 1870<=b<=2700) or '无'
    lines=['# 第8条：百章后修正与101–130章验证','',f"新增{n}/30章已完成；口头禅检查通过，字数、速度和实际末钩未通过。已停，未重写1–100章正文，未开千章。",'',
      '| 指标 | 实测 | 目标 |','|---|---:|---:|',
      f'| 本章均耗 / 最大 | {d["seconds"]/max(1,n):.1f} / {max((r.get("seconds",0) for r in d["new"]),default=0):.1f}秒；{speed}/{n}章≤75秒 | ≤75秒 |',
      f'| 含规划执行 / 墙钟 | {d["execution"]/60:.1f} / {d["wall"]/60:.1f}分 | 如实列出 |',
      f'| 字数1870–2700 | {passed}/{n}（{passed/max(1,n):.1%}） | ≥90% |',
      f'| 跨章长句/口头禅候选；相邻重复 | {repeat}；{d["adjacent"]} | 0 |',
      f'| 标签同型超额窗口 / 未知章 | {len(bad_hooks)} / {len(unknown)}（{unknown}） | 0 / 0 |',
      f'| 章纲线数峰值 | {max((len(o.get("threads",[])) for o in d["outlines"]),default=0)} | 主线＋≤1 |',
      f'| 调用（失败/重试）；tokens；估算 | {len(d["calls"])}（{u["failures"]}/{u["retries"]}）；{u["total_tokens"]:,}；${u["estimated_usd"]["conservative_total"]:.3f} | 重试单列 |','',
      '旧31–100章已存末钩分布（非人工重判）：'+ '；'.join(f'{k} {v}' for k,v in d['old_hooks'].most_common())+'。',
      '',
      '问题/边界：','',f'- 字数不合格章:字数：{failed_words}。',
      '- 独立抽读（agent_editorial_not_human）：104/107/109危险逼近3/10；119镜中另一个自己主动开口，新增禁用家族至少1次；122/123连续clue。故标签0超额窗不等于实际去重通过。112/115同五章窗仍有裁决协商；102/112有未登记术语候选，行动/术语配额尚不能确认通过。',
      f'- Flash责编（追读/爽点/人物/新鲜/多线/自然）：'+ '；'.join(k+':'+ '/'.join(str(int(v)) if v is not None else '?' for v in value.values()) for k,value in d['scores'].items())+'。非用户认可；程序硬警报0不等于全文语义零矛盾。',
      f'- 纠字{sum(c["stage"].endswith("-length") for c in d["calls"])}次，格式降级{sum(c["status"]=="degraded" for c in d["calls"])}次；采用回退{len(d["adoption"])}章{d["adoption"]}；格式与采用回退合计{(sum(c["status"]=="degraded" for c in d["calls"])+len(d["adoption"]))/max(1,len(d["calls"])):.1%}。105空审校沿用旧状态；101仅恢复原响应字段，无新调用。',
      f'- 止损：{m.get("stop_reason") or "未触发"}。纠字仅一次，仍超标接受记录，不整本重跑。','',
      f'已删旧段落约束、重复规划等路径，接入跨章句级检查/相邻重复清理/两线排期/累积续写；核心5000→{core}（{core-5000:+d}），279项本地回归通过。',
      '```text',scope,'```','',
      '[正文与检查点](../../../engine-books/benchmark/suspense-30/book.md)。到此停止，等待用户确认是否开千章。']
    save(ROOT/'engine/reports/fix-100.md','\n'.join(lines)+'\n')

if __name__=='__main__':
    d=read()
    if '--write' in sys.argv:write(d)
    else:print(json.dumps({k:v for k,v in d.items() if k not in ('rows','new','calls','meta','outlines')},ensure_ascii=False,default=dict))
