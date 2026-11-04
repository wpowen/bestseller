"""Read actual chapter/checkpoint metrics; write the single report only on request."""
from pathlib import Path
import argparse
import datetime as dt
import json
from collections import Counter
import subprocess
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from bookengine.batch import usage_report
from bookengine.polish_checks import scan
from bookengine.precision import locate
from bookengine.storage import load,save
from bookengine.text import chinese_count

ROOT=Path(__file__).resolve().parents[2]
BOOKS=ROOT.parent/'engine-books'


def records(out):
    return [load(p) for p in sorted((out/'chapters').glob('*.json'))]


def words(rows):
    return dict(passed=sum(1870<=r['chars']<=2700 for r in rows),total=len(rows),
                failures=[f"{r['number']}:{r['chars']}" for r in rows if not 1870<=r['chars']<=2700])


def report_data():
    pilots={}
    for key in ('xuanhuan-30','suspense-30'):
        out=BOOKS/'polish-30'/key;rows=records(out);calls=load(out/'calls.json')
        pilots[key]=dict(words=words(rows),calls=len(calls),usage=usage_report(calls),
            processing_seconds=sum(c.get('seconds',0) or 0 for c in calls),
            edits=sum(len(r.get('edits',[]))+len(r.get('setting_edits',[]))+len(r.get('limit_edits',[])) for r in rows),
            family=sum(r.get('detail',{}).get('double_or_reawakening') is True for r in rows),
            remaining_candidates=sum(len(scan((out/f"chapter-{r['number']:03d}.body.md").read_text(),state=rows[i-1]['state'] if i else {})) for i,r in enumerate(rows)),
            ending=rows[-1].get('detail',{}).get('opens_new_mainline'))
    out=BOOKS/'benchmark'/'suspense-30';meta=load(out/'run.json');rows=records(out);new=[r for r in rows if r['number']>30]
    calls=load(out/'calls.json');start=min((c['seq'] for c in calls if 'extend-100' in c['stage']),default=len(calls)+1)
    added=[c for c in calls if c['seq']>=start]
    base=meta.get('previous_runs',[{}])[-1].get('wall_seconds',2865.492719)
    execution=max(0,meta.get('execution_seconds',0)-base)
    seconds=(dt.datetime.fromisoformat(meta.get('finished') if meta['status']=='complete' else meta.get('updated',meta['revision']['started']))-dt.datetime.fromisoformat(added[0]['started'])).total_seconds() if added else 0
    volumes=[load(p) for p in sorted((out/'volumes').glob('*-*.json'))]
    draft=lambda n:next((c['input_chars'] for c in calls if c['stage'] in (f'p7-ch{n}-draft',f'qb-ch{n}-draft')),None)
    result=dict(pilots=pilots,status=meta['status'],completed=meta.get('completed_chapters'),target=meta['book']['chapters'],
        stop_reason=meta.get('stop_reason'),new_words=words(new),all_words=words(rows),new_seconds=seconds,new_execution_seconds=execution,
        new_average=seconds/max(1,len(new)),execution_average=execution/max(1,len(new)),chapter_average=sum(r.get('seconds',0) for r in new)/max(1,len(new)),
        new_calls=len(added),new_usage=usage_report(added),length_calls=sum(r.get('length_correction',False) for r in new),
        dialogue20=sum(r['style']['dialogue_ratio']>=.2 for r in new),hard_alerts=sum(r.get('hard_conflicts',0) for r in new),
        output_failures=sum(c['status'] in ('degraded','invalid_output','invalid_stage_output') for c in added),
        input_chars={str(n):draft(n) for n in (20,31,50,75,100)},
        state_chars=len((out/'state.json').read_text()),scores={str(v['end']):v.get('scores') for v in volumes},
        new_family=sum(r.get('polish',{}).get('double_or_reawakening') is True for r in new),
        word_counts=[r['chars'] for r in new])
    result['round_cost']=sum(p['usage']['estimated_usd']['conservative_total'] for p in pilots.values())+result['new_usage']['estimated_usd']['conservative_total']
    windows=[(rows[i]['number'],max(Counter(r['hook_type'] for r in rows[i-9:i+1] if r.get('hook_type')).values(),default=0)) for i in range(9,len(rows))]
    result.update(hook_over_windows=sum(n>2 for _,n in windows),new_hook_over_windows=sum(i>30 and n>2 for i,n in windows),unknown_hooks=sum(not r.get('hook_type') for r in rows),max_state_chars=max(len(json.dumps(r['state'],ensure_ascii=False,indent=2)) for r in rows))
    return result


def write_report(d):
    scope=subprocess.run([sys.executable,str(ROOT/'engine/tools/check_scope.py')],capture_output=True,text=True,check=True).stdout.strip()
    label='100章实跑完成；字数/历史末钩等验收未通过，已停，未开千章。' if d['status']=='complete' else f"止损停在{d['completed']}章，未开千章：{d['stop_reason'] or d['status']}。"
    scores='；'.join(k+':'+ '/'.join(str(v.get(s,'?')) for s in ('追读欲','爽点兑现','人物魅力','新鲜感','多线推进','AI味')) for k,v in d['scores'].items())
    lines=['# 第7条：打磨与百章验证','',label,'',
        '| 范围 | 用时/均耗 | 调用(失败/重试) / tokens | 1870–2700字 | 估算费用 |',
        '|---|---:|---:|---:|---:|']
    for key,title in (('xuanhuan-30','玄幻30章定点处理'),('suspense-30','悬疑30章定点处理')):
        p=d['pilots'][key];w=p['words'];u=p['usage']
        lines.append(f"| {title} | 调用{p['processing_seconds']/60:.1f}分（非重写） | {p['calls']}({u['failures']}/{u['retries']}) / {u['total_tokens']:,} | {w['passed']}/{w['total']} | ${u['estimated_usd']['conservative_total']:.3f} |")
    w=d['new_words'];u=d['new_usage']
    lines.append(f"| 悬疑新增31–{d['completed']} | 墙钟{d['new_seconds']/60:.1f}分；执行/墙钟均耗{d['execution_average']:.1f}/{d['new_average']:.1f}秒 | {d['new_calls']}({u['failures']}/{u['retries']}) / {u['total_tokens']:,} | {w['passed']}/{w['total']} | ${u['estimated_usd']['conservative_total']:.3f} |")
    lines.extend(['',f"新增纠字{d['length_calls']}章；输出/校验失败{d['output_failures']}/{max(1,d['new_calls'])}；重试{u['retries']}；程序硬警报{d['hard_alerts']}（不等于全文语义零矛盾）；对白≥20% {d['dialogue20']}/{w['total']}。本轮合计约${d['round_cost']:.3f}。",
        f"输入字符20/31/50/75/100：{' / '.join(str(d['input_chars'][str(n)]) for n in (20,31,50,75,100))}（100/20={d['input_chars']['100']/d['input_chars']['20']:.2f}）；state.json 最大{d['max_state_chars']}字符。责编（追读/爽点/人物/新鲜/多线/自然，评阅时模型分）：{scores}。",'',
        '问题与边界：',
        '- 已修：英文夹词、跨句引号误补、邻句被复制、伤臂/封禁候选与类型解析、负余额幻觉、已确认超额扣费、两书末章新身体苏醒；余额缺证保留未知，不补造收入。',
        f"- 双身/复醒归类：历史{d['pilots']['suspense-30']['family']}＋新增{d['new_family']}，超过3；十章同类型超配{d['hook_over_windows']}窗（新增段{d['new_hook_over_windows']}窗），{d['unknown_hooks']}章类型未知，轮换未通过。旧昵称小满保留；新开书分配仅本地回归验证。",
        '- 字数未达标（章:字数）：玄幻旧章 '+', '.join(d['pilots']['xuanhuan-30']['words']['failures'])+'；悬疑旧章 '+', '.join(d['pilots']['suspense-30']['words']['failures'])+'；新增 '+(', '.join(w['failures']) or '无')+'。',
        '- 续写安全重载两次；未整本重跑。P段号/根字典曾被忽略，已修并零请求恢复44/75章旧候选；剩余长章候选净删量不足。两场章纲常推进4–6条线。程序零警报不代表全文语义零矛盾，榜单级仍待抽读。',
        '', '256项本地回归通过；核心4920→5000（+80）：','```text',scope,'```',
        '', '主角沈迟：1章余额未提取；50章命令改判封禁、右臂肘下不能弯、余额未知≤4天；100章仍封禁、右臂/右手受限、左指麻痹、余额2天。',
        '', '[抽读正文](../../../engine-books/benchmark/READ-ME-FIRST.md) · [百章正文](../../../engine-books/benchmark/suspense-30/book.md)；原圣经/节拍保留，续写设计在同目录continuation-100.yaml。第7条标记执行交付完成，验收未过；新增第8条待确认，本轮未执行，不自动续写或开千章。'])
    save(ROOT/'engine/reports/polish-30.md','\n'.join(lines)+'\n')


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--write',action='store_true');args=parser.parse_args()
    data=report_data()
    if args.write:write_report(data)
    print(json.dumps(data,ensure_ascii=False,separators=(',',':')))
