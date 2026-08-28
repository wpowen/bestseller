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


async def score_one(
    client, book: dict, *, sem: asyncio.Semaphore, repeats: int = 1
) -> dict | None:
    """每本判 ``repeats`` 次取均值。

    2026-08-28 的教训：同一判官、同样 n=14，两批书给出 AUC 0.82 和 0.41。
    噪声有两个来源——抽书 与 判官采样（温度 1.0、每本只判一次）。
    单次判定等于把整本书的分数押在一次采样上，必须先把这一路压掉，
    否则后面每个结论都建在噪声上。"""
    from bestseller.services.concept_tournament import (
        ConceptCandidate,
        _build_judge_messages,
    )

    from bestseller.services.concept_tournament import _FLOOR_AXIS_LABELS

    cand = ConceptCandidate(dimension="bench", concept=_blurb(book))
    channel = "男频" if str(book.get("平台")) == "男频" else "女频"
    system, user = _build_judge_messages(
        candidate=cand,
        genre=str(book.get("分类") or "玄幻"),
        sub_genre=str(book.get("分类") or ""),
        references=[],
        audience_orientation=channel,
    )

    async def _one() -> dict:
        # 空响应会间歇出现（限流/服务端抖动），必须重试而不是直接算废。
        # 2026-08-28：不重试 + 静默丢弃 + 强侧任务排在前面，三者叠加produce
        # 出「弱侧 4/4 = 100%」这种看着像结论的东西——限流打到的全是排在
        # 后面的弱侧任务，于是失败**系统性地只落在一侧**，直接污染 AUC。
        # 2026-08-28：3 次 × 1.5s 退避仍有 43% 丢弃率，直接吃掉统计功效
        # （n=60 只收到 34 本，AUC 的 95%CI 宽到 [0.45,0.85] 什么都判不了）。
        # 空响应看形态是服务端限流：同一请求隔一会儿重发就正常返回。
        # 退避拉长到指数式，次数加到 5——量具的丢弃率本身就是要压的噪声源。
        for attempt in range(5):
            async with sem:
                out = _json_of(await _call(client, system, user))
            if out:
                return out
            await asyncio.sleep(2.0 * (2**attempt))
        return {}

    raw = await asyncio.gather(*[_one() for _ in range(repeats)], return_exceptions=True)
    verdicts = [v for v in raw if isinstance(v, dict) and v]
    if not verdicts:
        errs = {type(v).__name__ for v in raw if isinstance(v, BaseException)}
        return {"_dropped": "判官零输出" + (f"({'/'.join(sorted(errs))})" if errs else "（解析为空）")}
    scores = {}
    for k, _l, _d in _FLOOR_AXIS_LABELS:
        vals = [float(v[k]) for v in verdicts if isinstance(v.get(k), (int, float))]
        if vals:
            scores[k] = st.mean(vals)
    if len(scores) < len(_FLOOR_AXIS_LABELS):
        missing = [k for k, _l, _d in _FLOOR_AXIS_LABELS if k not in scores]
        return {"_dropped": "缺轴:" + ",".join(missing)}
    preds = [
        float(v["predictable"]) for v in verdicts
        if isinstance(v.get("predictable"), (int, float))
    ]
    pred = st.mean(preds) if preds else None
    return {
        "title": str(book.get("书名")),
        "channel": channel,
        "genre": str(book.get("分类") or ""),
        "chapters": _chapters(book),
        "readers": float(book.get("在读人数_数值") or 0),
        "scores": scores,
        "predictable": pred,
        "n_verdicts": len(verdicts),
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


async def run(*, n: int, seed: int, concurrency: int, repeats: int) -> None:
    from bestseller.services.concept_tournament import _FLOOR_AXIS_LABELS

    floors = (yaml.safe_load(CFG.read_text(encoding="utf-8")) or {}).get(
        "judge_hard_floors"
    ) or {}
    strong, weak = load_sides(n=n, seed=seed)
    print(f"强侧 {len(strong)} 本（≥500 章 且 ≥30 万在读）  弱侧 {len(weak)} 本（<120 章 且 <2 万在读）")
    print(f"两侧同走生产判官 _build_judge_messages，按现行 judge_hard_floors 判")
    print(f"每本判 {repeats} 次取均值；AUC 报 95% 自助置信区间\n")

    sem = asyncio.Semaphore(concurrency)
    async with httpx.AsyncClient(timeout=300.0, headers={"Authorization": "Bearer " + _key()}) as c:
        done = {"n": 0}
        total = len(strong) + len(weak)

        async def wrapped(b: dict, side: str) -> dict | None:
            out = await score_one(c, b, sem=sem, repeats=repeats)
            done["n"] += 1
            print(f"  {done['n']}/{total}", end="\r", flush=True)
            if out:
                out["side"] = side
                out["title"] = out.get("title") or str(b.get("书名"))
            return out

        # 任务顺序必须打散：按「先全部强侧、再全部弱侧」创建时，一旦中途限流，
        # 失败会全部落在排在后面的那一侧，造成系统性偏差（实录：弱侧 26/30 全灭）。
        tasks = [wrapped(b, "强") for b in strong] + [wrapped(b, "弱") for b in weak]
        random.Random(seed).shuffle(tasks)
        res = await asyncio.gather(*tasks)
    print(" " * 40, end="\r")
    rows = [r for r in res if r and not r.get("_dropped")]
    dropped = [r for r in res if r and r.get("_dropped")]
    if dropped:
        import collections as _c

        tally = _c.Counter(f"{r['side']}/{r['_dropped']}" for r in dropped)
        by_side = _c.Counter(r["side"] for r in dropped)
        if len(by_side) == 1:
            print("  ⚠️⚠️ 丢弃**全部集中在一侧**——这是系统性偏差不是随机缺失，结论作废")
        print(f"  ⚠️ 被丢弃 {len(dropped)}/{total} 本——丢弃必须留痕，否则「样本少」会被读成「结果」：")
        for k, v in tally.most_common(8):
            print(f"     {k}  ×{v}")
    S = [r for r in rows if r["side"] == "强"]
    W = [r for r in rows if r["side"] == "弱"]
    if len(S) < 5 or len(W) < 5:
        raise SystemExit(f"样本不足（强 {len(S)} / 弱 {len(W)}）——不出结论")

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

    def _auc(sa: list[dict], wa: list[dict]) -> float:
        pr = [(a, b) for a in sa for b in wa]
        w = sum(1 for a, b in pr if a["mean"] > b["mean"])
        t = sum(1 for a, b in pr if a["mean"] == b["mean"])
        return (w + 0.5 * t) / max(1, len(pr))

    auc = _auc(S, W)
    # 自助法置信区间：**单点 AUC 不许当结论**——2026-08-28 同一判官
    # 两批 n=14 的书给出 0.82 与 0.41，点估计毫无意义。
    rng = random.Random(7)
    boots = sorted(
        _auc(
            [S[rng.randrange(len(S))] for _ in S],
            [W[rng.randrange(len(W))] for _ in W],
        )
        for _ in range(400)
    )
    lo, hi = boots[int(0.025 * len(boots))], boots[int(0.975 * len(boots)) - 1]
    print(f"\n  排序能力 AUC = {auc:.2f}  95%CI [{lo:.2f}, {hi:.2f}]（0.5 = 抛硬币）")
    if lo > 0.5:
        note = "区间整体高于 0.5 → 判官确有区分力" + (
            "，且下界 ≥0.70，够格谈恢复杀权" if lo >= 0.70 else "，但下界未达 0.70，仍不够格拿回杀权"
        )
    elif hi < 0.5:
        note = "区间整体低于 0.5 → 判官判反了"
    else:
        note = "区间跨过 0.5 → **无法证明它有区分力**，任何基于它的结论都不成立"
    print(f"  → {note}")

    OUT_DIR.mkdir(exist_ok=True)
    (OUT_DIR / "hook-gate-validation.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print("\n已存 .benchmark/hook-gate-validation.json")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=14)
    ap.add_argument("--seed", type=int, default=828)
    ap.add_argument("--concurrency", type=int, default=8)
    ap.add_argument("--repeats", type=int, default=3, help="每本判几次取均值，压判官采样噪声")
    a = ap.parse_args()
    asyncio.run(
        run(n=a.n, seed=a.seed, concurrency=a.concurrency, repeats=a.repeats)
    )


if __name__ == "__main__":
    main()
