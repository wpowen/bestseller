"""Aggregate AI-tic counts across the process-first scale-up drafts.

Scans output/prose-prompt-arena/scaleup/<book-chN>/drafts/<strategy>__<model>__sK.md
and reports tic-rate per 1000 chars: baseline (production_control) vs human_process_first,
broken down by book and writer model. The DELTA is the signal.
"""
from __future__ import annotations
import re, statistics, collections
from pathlib import Path

ROOT = Path("output/prose-prompt-arena/scaleup")

# Heuristic AI-tic detectors (consistent across arms → delta is meaningful)
NEG = re.compile(r"没(抬头|回头|吭声|应声|说话|接话|出声|动弹|搭理|言语)")
SIM = re.compile(r"似的|像[^，。！？\n]{1,15}一样|仿佛[^，。！？\n]{1,15}一(般|样)")
SUMM = re.compile(r"算了一笔账|脑子里过的|心里(盘算|默念|快速)|盘算着|在心里算|权衡了")

def tics(t: str) -> tuple[int, int]:
    n = len(NEG.findall(t)) + len(SIM.findall(t)) + len(SUMM.findall(t))
    chars = sum(1 for c in t if "一" <= c <= "鿿") or 1
    return n, chars

rows = collections.defaultdict(lambda: collections.defaultdict(list))  # (book,model) -> strat -> [rate/1k]
detail = collections.defaultdict(lambda: collections.defaultdict(list))
for md in ROOT.glob("*/drafts/*__s*.md"):
    book = md.parent.parent.name
    m = re.match(r"(.+?)__(.+?)__s\d+\.md", md.name)
    if not m:
        continue
    strat, model = m.group(1), m.group(2)
    n, chars = tics(md.read_text(encoding="utf-8"))
    rate = n / chars * 1000
    rows[(book.split("-ch")[0], model)][strat].append(rate)
    detail[strat][model].append(n)

print(f"{'book':9} {'model':18} {'baseline/1k':>12} {'process1st/1k':>14} {'Δ%':>7} {'n':>3}")
print("-"*68)
order=["production_control","human_process_first"]
for (book, model), by in sorted(rows.items()):
    b = statistics.mean(by.get("production_control",[0]) or [0])
    p = statistics.mean(by.get("human_process_first",[0]) or [0])
    delta = (p-b)/b*100 if b else 0.0
    n = len(by.get("production_control",[]))
    print(f"{book:9} {model:18} {b:>12.2f} {p:>14.2f} {delta:>6.0f}% {n:>3}")

print("\n=== overall (all books+models pooled) ===")
allb=[r for (bk,mo),by in rows.items() for r in by.get("production_control",[])]
allp=[r for (bk,mo),by in rows.items() for r in by.get("human_process_first",[])]
if allb and allp:
    mb,mp=statistics.mean(allb),statistics.mean(allp)
    print(f"baseline tic-rate/1k = {mb:.2f}  (n={len(allb)})")
    print(f"process-first  /1k   = {mp:.2f}  (n={len(allp)})")
    print(f"reduction = {(mb-mp)/mb*100:.0f}%")
    # zero-tic draft share
    zb=sum(1 for r in allb if r==0)/len(allb)*100
    zp=sum(1 for r in allp if r==0)/len(allp)*100
    print(f"zero-tic drafts: baseline {zb:.0f}%  ->  process-first {zp:.0f}%")
