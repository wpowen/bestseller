"""定向探针：创意池对哪些题材会生成跑题内容。

2026-08-28 线索（scripts/concept_judge_validation.py --mode place，n=12）：
    男频 战神赘婿×3 + 传统玄幻×1  →  0/4 全部干涸
    女频 古风世情/种田/民国言情/游戏体育 →  5/8 产出
且基线跑批里被钩子门毙掉的候选，genre_fidelity 有 20/28 落在 ≤3.0
（真机一例：给「种田」类书生成了乡镇邮递员送信）。

但 n=4 不足以定案，且必须先分清两种完全不同的解释：
    A. 创意池对某些题材**生成跑题内容**  → 修生成端的题材路由
    B. 判官对**所有**题材都给低分       → 是判官刻度问题，与频道无关

所以这里不跑完整淘汰赛（一轮 ~8 分钟、~35 次调用），只跑最短的因果链：
    _build_raw_idea_pool_messages  →  _build_judge_messages 取 genre_fidelity
每个题材各生成 N 组，看 genre_fidelity 的分布怎么随题材/频道变化。

量具纪律沿用 hook_gate_validation 的三条（那三处是踩出来的）：
  · 每条判 repeats 次取均值，压判官采样噪声
  · 任务顺序打散，避免限流把失败系统性压到某一侧
  · 空响应指数退避重试；丢弃留痕并按组统计，单侧塌陷直接判结论作废
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

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

API = "https://api.minimaxi.com/v1/text/chatcompletion_v2"
MODEL = "MiniMax-M3"
OUT_DIR = ROOT / ".benchmark"

# 取自建书页真实可选项，两侧条数对齐，避免频道间样本量不等造成的假差异。
MALE = ("传统玄幻", "仙侠", "都市", "末世", "历史", "武侠")
FEMALE = ("古风世情", "种田", "民国言情", "现代言情", "女性成长", "幻想言情")


def _key() -> str:
    k = os.environ.get("MINIMAX_API_KEY") or os.environ.get("BESTSELLER__LLM__API_KEY")
    if not k:
        raise SystemExit("缺 MINIMAX_API_KEY")
    return k


async def _call(client, system: str, user: str, *, max_tokens: int = 4000) -> str:
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


async def _retrying(fn, *, tries: int = 5):
    """空响应是服务端限流，不是内容问题——退避重试，别当废数据。"""
    for attempt in range(tries):
        out = await fn()
        if out:
            return out
        await asyncio.sleep(2.0 * (2**attempt))
    return None


async def probe(
    client, genre: str, channel: str, *, sem: asyncio.Semaphore, repeats: int
) -> dict:
    from bestseller.services.concept_tournament import (
        ConceptCandidate,
        _build_judge_messages,
        _build_raw_idea_pool_messages,
        _parse_raw_idea_pool,
    )

    sys_p, usr_p = _build_raw_idea_pool_messages(
        genre=genre,
        sub_genre=genre,
        count=1,
        seed_concept="",
        prompt_arm="author_pitch",
        focus_hint="",
        audience_orientation=channel,
        tone_preference="",
        effect_skills=(),
        creation_intent_block="",
    )

    async def _gen():
        async with sem:
            raw = await _call(client, sys_p, usr_p)
        ideas = _parse_raw_idea_pool(raw, limit=1)
        return ideas[0][1] if ideas else ""

    concept = await _retrying(_gen)
    if not concept:
        return {"_dropped": "创意池零输出", "genre": genre, "channel": channel}

    sys_j, usr_j = _build_judge_messages(
        candidate=ConceptCandidate(dimension="probe", concept=concept),
        genre=genre,
        sub_genre=genre,
        references=[],
        audience_orientation=channel,
    )

    async def _judge():
        async with sem:
            return _json_of(await _call(client, sys_j, usr_j, max_tokens=2000))

    verdicts = [v for v in await asyncio.gather(*[_retrying(_judge) for _ in range(repeats)]) if v]
    vals = [float(v["genre_fidelity"]) for v in verdicts if isinstance(v.get("genre_fidelity"), (int, float))]
    if not vals:
        return {"_dropped": "判官零输出", "genre": genre, "channel": channel}
    return {
        "genre": genre,
        "channel": channel,
        "concept": concept[:120],
        "genre_fidelity": st.mean(vals),
        "n": len(vals),
    }


async def run(*, per_genre: int, repeats: int, concurrency: int, seed: int, tag: str = "") -> None:
    jobs = [(g, "男频") for g in MALE for _ in range(per_genre)]
    jobs += [(g, "女频") for g in FEMALE for _ in range(per_genre)]
    random.Random(seed).shuffle(jobs)  # 打散：别让限流系统性压到某一侧
    print(f"男频 {len(MALE)} 个题材 / 女频 {len(FEMALE)} 个，每题材 {per_genre} 组，"
          f"每组判 {repeats} 次取均值 —— 共 {len(jobs)} 组\n")

    sem = asyncio.Semaphore(concurrency)
    done = {"n": 0}
    async with httpx.AsyncClient(timeout=300.0, headers={"Authorization": "Bearer " + _key()}) as c:

        async def one(g: str, ch: str) -> dict:
            out = await probe(c, g, ch, sem=sem, repeats=repeats)
            done["n"] += 1
            print(f"  {done['n']}/{len(jobs)}", end="\r", flush=True)
            return out

        res = await asyncio.gather(*(one(g, ch) for g, ch in jobs))
    print(" " * 40, end="\r")

    rows = [r for r in res if not r.get("_dropped")]
    dropped = [r for r in res if r.get("_dropped")]
    if dropped:
        tally = collections.Counter(f"{r['channel']}/{r['_dropped']}" for r in dropped)
        print(f"  ⚠️ 丢弃 {len(dropped)}/{len(res)}：{dict(tally)}")
        if len({r["channel"] for r in dropped}) == 1:
            print("  ⚠️⚠️ 丢弃全部集中在一个频道——系统性偏差，结论作废")
    if len(rows) < 10:
        raise SystemExit(f"样本不足（{len(rows)}）——不出结论")

    print("═══ genre_fidelity 按频道 ═══\n")
    for ch in ("男频", "女频"):
        v = [r["genre_fidelity"] for r in rows if r["channel"] == ch]
        if v:
            print(f"  {ch}  n={len(v):<3} 均值 {st.mean(v):.2f}  中位 {st.median(v):.1f}  "
                  f"≤3.0 占比 {sum(1 for x in v if x <= 3.0)/len(v)*100:.0f}%")
    print("\n═══ 按题材（低分 = 创意池对该题材写跑题）═══\n")
    by = collections.defaultdict(list)
    for r in rows:
        by[(r["channel"], r["genre"])].append(r["genre_fidelity"])
    for (ch, g), v in sorted(by.items(), key=lambda kv: st.mean(kv[1])):
        flag = "  ← 跑题" if st.mean(v) <= 3.0 else ""
        print(f"  {ch} {g:<8} n={len(v)}  均值 {st.mean(v):.2f}{flag}")

    worst = sorted(rows, key=lambda r: r["genre_fidelity"])[:3]
    print("\n  最跑题的三条产出：")
    for r in worst:
        print(f"    [{r['channel']}·{r['genre']}·{r['genre_fidelity']:.1f}] {r['concept'][:70]}")

    OUT_DIR.mkdir(exist_ok=True)
    name = f"genre-fidelity-probe{('-' + tag) if tag else ''}.json"
    (OUT_DIR / name).write_text(
        json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(f"\n已存 .benchmark/{name}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-genre", type=int, default=3)
    ap.add_argument("--repeats", type=int, default=2)
    ap.add_argument("--concurrency", type=int, default=3)
    ap.add_argument("--seed", type=int, default=828)
    ap.add_argument("--tag", type=str, default="", help="输出文件后缀，防止 A/B 两臂互相覆盖")
    a = ap.parse_args()
    asyncio.run(
        run(per_genre=a.per_genre, repeats=a.repeats, concurrency=a.concurrency, seed=a.seed, tag=a.tag)
    )


if __name__ == "__main__":
    main()
