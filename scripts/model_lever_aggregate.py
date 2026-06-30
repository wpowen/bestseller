"""Aggregate AI-tic rate per writer MODEL (baseline prompt) across the model-lever run.

Lower tic-rate/1k = innately cleaner (less AI-腔) base writer. The DELTA between
models is the lever signal. Tic-count is a crude proxy (see falsified-jargon memo),
so eyeball the top model's prose too.
"""
from __future__ import annotations
import re, statistics, collections
from pathlib import Path

ROOT = Path("output/prose-prompt-arena/model-lever")
NEG = re.compile(r"没(抬头|回头|吭声|应声|说话|接话|出声|动弹|搭理|言语)")
SIM = re.compile(r"似的|像[^，。！？\n]{1,15}一样|仿佛[^，。！？\n]{1,15}一(般|样)")
SUMM = re.compile(r"算了一笔账|脑子里过的|心里(盘算|默念|快速)|盘算着|在心里算|权衡了")

def tics(t: str) -> tuple[int, int]:
    n = len(NEG.findall(t)) + len(SIM.findall(t)) + len(SUMM.findall(t))
    chars = sum(1 for c in t if "一" <= c <= "鿿") or 1
    return n, chars

per_model = collections.defaultdict(list)        # model -> [rate/1k]
per_model_book = collections.defaultdict(lambda: collections.defaultdict(list))
for md in ROOT.glob("*/drafts/*__s*.md"):
    book = md.parent.parent.name.split("-ch")[0]
    m = re.match(r"production_control__(.+?)__s\d+\.md", md.name)
    if not m:
        continue
    model = m.group(1)
    n, chars = tics(md.read_text(encoding="utf-8"))
    rate = n / chars * 1000
    per_model[model].append(rate)
    per_model_book[model][book].append(rate)

print(f"{'writer model':22} {'tic/1k':>8} {'zero%':>7} {'n':>4}   guaitan / modao")
print("-"*70)
for model, rates in sorted(per_model.items(), key=lambda kv: statistics.mean(kv[1])):
    mean = statistics.mean(rates)
    zero = sum(1 for r in rates if r == 0)/len(rates)*100
    gb = per_model_book[model].get("guaitan", [])
    mb = per_model_book[model].get("modao", [])
    gm = statistics.mean(gb) if gb else float("nan")
    mm = statistics.mean(mb) if mb else float("nan")
    print(f"{model:22} {mean:>8.2f} {zero:>6.0f}% {len(rates):>4}   {gm:>5.2f} / {mm:.2f}")
