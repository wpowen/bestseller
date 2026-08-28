"""验**钩子硬门**——修完连载性那刀之后，它成了淘汰赛干涸的大头。

2026-08-28 实测（scripts/concept_judge_validation.py --mode place）：收回连载性
判官杀权后，12 本里干涸 7 本，剩余干涸原因以钩子门为主：
    no hook-qualified contenders / no independently qualified hooks  共 5 次
    no seriality-qualified finalists                                    2 次

问的问题和验连载性判官时完全一样，且必须先问：**真榜单书能不能过这道门。**
若真跑到 500+ 章、几十万在读的书也被地板毙掉，那门就是校准错了，
和 seriality_hard_floors 的 7.0（真榜单书仅 8% 通过）是同一个病。

两侧同走**生产判官** ``_build_judge_messages``，同一套 ``judge_hard_floors``：
    强侧  ≥500 章 且 ≥30 万在读的真榜单长篇
    弱侧  <120 章 且 <2 万在读的真扑街书

报三件事：
  逐轴区分力  强侧均分 − 弱侧均分（判官分不分得开）
  地板通过率  按现行 config 判，两侧各有多少能过（门杀不杀错人）
  致死轴归因  强侧被毙时，是哪几根轴把它毙的（该改哪一根）

注意：判官不是写手，锚点/证据进判官 prompt 是本仓库允许的
（「判官读证据≠写手读证据」），所以这里只量校准，不动 prompt 措辞。
"""

from __future__ import annotations

import argparse
import asyncio
import collections
import json
import os
import pathlib
import random
import re
import statistics as st
import sys

import httpx
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

API = "https://api.minimaxi.com/v1/text/chatcompletion_v2"
MODEL = "MiniMax-M3"
RANK_JSON = ROOT / ".benchmark" / "fanqie_rank.json"
OUT_DIR = ROOT / ".benchmark"
CFG = ROOT / "config" / "concept_tournament.yaml"


def _key() -> str:
    k = os.environ.get("MINIMAX_API_KEY") or os.environ.get("BESTSELLER__LLM__API_KEY")
    if not k:
        raise SystemExit("缺 MINIMAX_API_KEY")
    return k


async def _call(client, system: str, user: str, *, max_tokens: int = 2000) -> str:
    r = await client.post(
        API,
        json={
            "model": MODEL,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 1.0,
            "max_tokens": max_tokens,
            "thinking": {"type": "disabled"},
        },
    )
    r.raise_for_status()
    ch = r.json().get("choices") or []
    return (ch[0]["message"]["content"] or "") if ch else ""


def _json_of(raw: str) -> dict:
    text = re.sub(r"<think>.*?</think>", "", raw, flags=re.S)
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return {}
    try:
        return json.loads(m.group(0))
    except Exception:  # noqa: BLE001
        return {}


def _chapters(book: dict) -> int:
    m = re.search(r"(\d+)", str(book.get("最新章节") or ""))
    return int(m.group(1)) if m else 0


def _blurb(book: dict) -> str:
    t = re.sub(r"^【[^】]*】", "", str(book.get("简介") or "").strip()).strip()
    return re.sub(r"\s+", " ", t)[:200]


def load_sides(*, n: int, seed: int) -> tuple[list[dict], list[dict]]:
    rows = json.loads(RANK_JSON.read_text(encoding="utf-8"))
    strong = [
        b for b in rows
        if _chapters(b) >= 500
        and float(b.get("在读人数_数值") or 0) >= 300_000
        and len(_blurb(b)) >= 60
    ]
    weak = [
        b for b in rows
        if 0 < _chapters(b) < 120
        and 0 < float(b.get("在读人数_数值") or 0) < 20_000
        and len(_blurb(b)) >= 60
    ]
    rng = random.Random(seed)
    rng.shuffle(strong)
    rng.shuffle(weak)
    return strong[:n], weak[:n]


async def score_one(client, book: dict, *, sem: asyncio.Semaphore) -> dict | None:
    from bestseller.services.concept_tournament import (
        ConceptCandidate,
        _build_judge_messages,
    )

    cand = ConceptCandidate(dimension="bench", concept=_blurb(book))
    channel = "男频" if str(book.get("平台")) == "男频" else "女频"
    async with sem:
        system, user = _build_judge_messages(
            candidate=cand,
            genre=str(book.get("分类") or "玄幻"),
            sub_genre=str(book.get("分类") or ""),
            references=[],
            audience_orientation=channel,
        )
        verdict = _json_of(await _call(client, system, user))
    if not verdict:
        return None
    from bestseller.services.concept_tournament import _FLOOR_AXIS_LABELS

    scores = {
        k: float(verdict[k])
        for k, _l, _d in _FLOOR_AXIS_LABELS
        if isinstance(verdict.get(k), (int, float))
    }
    if len(scores) < len(_FLOOR_AXIS_LABELS):
        return None
    pred = verdict.get("predictable")
    return {
        "title": str(book.get("书名")),
        "channel": channel,
        "genre": str(book.get("分类") or ""),
        "chapters": _chapters(book),
        "readers": float(book.get("在读人数_数值") or 0),
        "scores": scores,
        "predictable": float(pred) if isinstance(pred, (int, float)) else None,
        "mean": st.mean(scores.values()),
    }


def verdict_under_floors(row: dict, floors: dict) -> tuple[bool, list[str]]:
    """复用生产判据的形状：灾难轴一票否决，软缺陷超额才出局。"""
    from bestseller.services.concept_tournament import _FLOOR_AXIS_LABELS

    catastrophe = float(floors.get("catastrophe_floor", 4.0))
    allowance = int(floors.get("soft_miss_allowance", 3))
    failed: list[str] = []
    fatal = False
    for key, _label, default in _FLOOR_AXIS_LABELS:
        v = row["scores"].get(key)
        if v is None:
            continue
        limit = float(floors.get(key, default))
        if v < catastrophe:
            fatal = True
            failed.append(f"{key}!")
        elif v < limit:
            failed.append(key)
    pmax = float(floors.get("predictable_max", 5.5))
    if row.get("predictable") is not None and row["predictable"] > pmax:
        failed.append("predictable")
    soft = [f for f in failed if not f.endswith("!")]
    passed = not fatal and len(soft) <= allowance
    return passed, failed


async def run(*, n: int, seed: int, concurrency: int) -> None:
    from bestseller.services.concept_tournament import _FLOOR_AXIS_LABELS

    floors = (yaml.safe_load(CFG.read_text(encoding="utf-8")) or {}).get(
        "judge_hard_floors"
    ) or {}
    strong, weak = load_sides(n=n, seed=seed)
    print(f"强侧 {len(strong)} 本（≥500 章 且 ≥30 万在读）  弱侧 {len(weak)} 本（<120 章 且 <2 万在读）")
    print("两侧同走生产判官 _build_judge_messages，按现行 judge_hard_floors 判\n")

    sem = asyncio.Semaphore(concurrency)
    async with httpx.AsyncClient(timeout=300.0, headers={"Authorization": "Bearer " + _key()}) as c:
        done = {"n": 0}
        total = len(strong) + len(weak)

        async def wrapped(b: dict, side: str) -> dict | None:
            out = await score_one(c, b, sem=sem)
            done["n"] += 1
            print(f"  {done['n']}/{total}", end="\r", flush=True)
            if out:
                out["side"] = side
            return out

        res = await asyncio.gather(
            *[wrapped(b, "强") for b in strong], *[wrapped(b, "弱") for b in weak]
        )
    print(" " * 40, end="\r")
    rows = [r for r in res if r]
    S = [r for r in rows if r["side"] == "强"]
    W = [r for r in rows if r["side"] == "弱"]
    if not S or not W:
        raise SystemExit("样本不足")

    print(f"═══ 生产钩子判官在真书上的表现（强 {len(S)} / 弱 {len(W)}）═══\n")
    print(f"  {'轴':<20}{'地板':>6}{'强侧均':>8}{'弱侧均':>8}{'区分':>8}")
    for key, label, default in _FLOOR_AXIS_LABELS:
        a = st.mean([r["scores"][key] for r in S])
        b = st.mean([r["scores"][key] for r in W])
        print(f"  {key+'('+label+')':<20}{float(floors.get(key, default)):>6.1f}{a:>8.2f}{b:>8.2f}{a-b:>+8.2f}")

    ps = [verdict_under_floors(r, floors) for r in S]
    pw = [verdict_under_floors(r, floors) for r in W]
    okS = sum(1 for p, _ in ps if p)
    okW = sum(1 for p, _ in pw if p)
    print(f"\n  按现行地板判——通过率")
    print(f"    强侧（真跑到 500+ 章的书） {okS}/{len(S)} = {okS/len(S)*100:>3.0f}%   ← 越高越好，这些是该过的")
    print(f"    弱侧（真没写下去的书）     {okW}/{len(W)} = {okW/len(W)*100:>3.0f}%   ← 越低越好，这些是该毙的")
    print(f"    分离度 {(okS/len(S)-okW/len(W))*100:+.0f}pt")

    cnt = collections.Counter(f for _p, fs in ps for f in fs)
    print("\n  强侧被扣分的轴（真榜单书栽在哪根）：")
    for k, v in cnt.most_common(8):
        print(f"    {k:<24}{v:>3}/{len(S)}")

    pairs = [(a, b) for a in S for b in W]
    win = sum(1 for a, b in pairs if a["mean"] > b["mean"])
    tie = sum(1 for a, b in pairs if a["mean"] == b["mean"])
    auc = (win + 0.5 * tie) / max(1, len(pairs))
    print(f"\n  排序能力 AUC = {auc:.2f}（0.5 = 抛硬币）")
    print(
        "  → "
        + (
            "判官有效，若强侧通过率仍低则是**地板校准**问题"
            if auc >= 0.70
            else "判官排序能力不足，先修判官再谈地板"
        )
    )

    OUT_DIR.mkdir(exist_ok=True)
    (OUT_DIR / "hook-gate-validation.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print("\n已存 .benchmark/hook-gate-validation.json")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=14)
    ap.add_argument("--seed", type=int, default=828)
    ap.add_argument("--concurrency", type=int, default=6)
    a = ap.parse_args()
    asyncio.run(run(n=a.n, seed=a.seed, concurrency=a.concurrency))


if __name__ == "__main__":
    main()
