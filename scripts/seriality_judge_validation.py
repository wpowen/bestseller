"""验**连载性判官**——它是 sustain 那根轴上唯一带杀权的闸门。

2026-08-28 校准后的定位：框架 sustain 落后榜单书 19pt（p=0.065），且这一轴
是判官区分力最强的一根（80%），因此是本轮唯一经过效度校准的攻击目标。

代码事实：对 ≥200 章的书，`seriality_stage_mode` 返回 ``enforcing``——连载性
判官**带杀权**。所以 sustain 差不是「没人管」，而是「管了但放行了」。
它自己的 system prompt 就写着「字段写满不等于能写长」，而它的上游恰好是一个
反复补字段直到 ``has_proof`` 为真的修复循环（最多 2 轮）。

在给它加牙或改它之前先验它——这正是本轮最大教训（见 judge-validity-before-use）：
**拿未验证的判官读数改生产代码**。验法与那次相同：真书对真书，外部硬真值。

    强侧  ≥500 章 且 ≥30 万在读的真榜单长篇（确实写下去了）
    弱侧  <120 章 且 <2 万在读的真扑街书（确实没写下去）

两侧都走**生产的展开 prompt** ``_build_seriality_messages`` + **生产的判官**
``_build_seriality_judge_messages``，六轴取分。判官若有效，强侧应显著高于弱侧，
且弱侧应大量跌破 6 分地板（它自己的失败线）。

两种结论指向完全不同的修法，所以必须先分清：
  · 强弱分得开 → 判官有效，问题在「修复循环把不合格候选补到及格」→ 收紧修复
  · 强弱分不开 → 判官本身失明 → 给它加牙只会放大错误，得先重做判官
"""

from __future__ import annotations

import argparse
import asyncio
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
RANK_JSON = ROOT / ".benchmark" / "fanqie_rank.json"
OUT_DIR = ROOT / ".benchmark"
SIX_AXES = (
    "renewability",
    "escalation",
    "anti_reset",
    "coherence",
    "promise_survival",
    "unit_density",
)
FLOOR = 6.0  # 判官 prompt 自己写的失败线：任一项低于 6 都应判失败


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
    choices = r.json().get("choices") or []
    return (choices[0]["message"]["content"] or "") if choices else ""


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
    text = re.sub(r"^【[^】]*】", "", str(book.get("简介") or "").strip()).strip()
    return re.sub(r"\s+", " ", text)[:200]


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


async def score_one(client, book: dict, *, chapter_count: int, sem: asyncio.Semaphore) -> dict | None:
    """跑生产展开 + 生产判官，返回六轴分。"""
    from bestseller.services.concept_tournament import (
        ConceptCandidate,
        _apply_seriality_payload,
        _build_seriality_judge_messages,
        _build_seriality_messages,
    )

    seed = ConceptCandidate(dimension="bench", concept=_blurb(book))
    genre = str(book.get("分类") or "玄幻")
    async with sem:
        sys_p, usr_p = _build_seriality_messages(
            candidate=seed, genre=genre, chapter_count=chapter_count
        )
        expanded = _apply_seriality_payload(seed, await _call(client, sys_p, usr_p, max_tokens=3000))
        if expanded is None:
            return None
        sys_j, usr_j = _build_seriality_judge_messages(
            candidate=expanded, chapter_count=chapter_count
        )
        verdict = _json_of(await _call(client, sys_j, usr_j, max_tokens=900))
    # 存下展开件：定位信息是在「展开」丢的还是在「判官」丢的——
    # 两者的修法完全不同，不能混为一谈。
    fields = {
        k: getattr(expanded, k)
        for k in (
            "core_promise_invariant", "repeatable_story_unit", "unit_families",
            "role_ladder", "world_ladder", "renewal_sources", "accumulation_tracks",
            "phase_transitions", "opposing_ecology", "question_ladder",
            "unit_frequency", "endgame_direction", "ch50",
        )
        if hasattr(expanded, k)
    }
    scores = {a: float(verdict[a]) for a in SIX_AXES if isinstance(verdict.get(a), (int, float))}
    if len(scores) < len(SIX_AXES):
        return None
    return {
        "title": str(book.get("书名")),
        "chapters": _chapters(book),
        "readers": float(book.get("在读人数_数值") or 0),
        "scores": scores,
        "min_axis": min(scores.values()),
        "mean": st.mean(scores.values()),
        # 判官自己的失败线：任一轴 <6 即应判失败
        "would_fail": min(scores.values()) < FLOOR,
        "reason": str(verdict.get("reason") or "")[:60],
        "expansion": {k: (list(v) if isinstance(v, tuple) else v) for k, v in fields.items()},
    }


async def run(*, n: int, seed: int, chapter_count: int, concurrency: int) -> None:
    strong, weak = load_sides(n=n, seed=seed)
    print(f"强侧 {len(strong)} 本（≥500 章 且 ≥30 万在读）  弱侧 {len(weak)} 本（<120 章 且 <2 万在读）")
    print(f"两侧同走生产展开 + 生产连载性判官，目标章数统一 {chapter_count}\n")

    sem = asyncio.Semaphore(concurrency)
    async with httpx.AsyncClient(timeout=300.0, headers={"Authorization": "Bearer " + _key()}) as c:
        done = {"n": 0}
        total = len(strong) + len(weak)

        async def wrapped(book: dict, side: str) -> dict | None:
            out = await score_one(c, book, chapter_count=chapter_count, sem=sem)
            done["n"] += 1
            print(f"  {done['n']}/{total}", end="\r", flush=True)
            if out:
                out["side"] = side
            return out

        results = await asyncio.gather(
            *[wrapped(b, "强") for b in strong], *[wrapped(b, "弱") for b in weak]
        )
    print(" " * 40, end="\r")
    rows = [r for r in results if r]
    if not rows:
        raise SystemExit("全部解析失败")

    S = [r for r in rows if r["side"] == "强"]
    W = [r for r in rows if r["side"] == "弱"]
    print(f"═══ 生产连载性判官在真书上的表现（强 {len(S)} / 弱 {len(W)}）═══\n")
    print(f"  {'轴':<18}{'强侧均分':>10}{'弱侧均分':>10}{'差':>8}")
    for ax in SIX_AXES:
        a = st.mean([r["scores"][ax] for r in S]) if S else 0
        b = st.mean([r["scores"][ax] for r in W]) if W else 0
        print(f"  {ax:<18}{a:>10.2f}{b:>10.2f}{a - b:>+8.2f}")
    ms = st.mean([r["mean"] for r in S]) if S else 0
    mw = st.mean([r["mean"] for r in W]) if W else 0
    print(f"  {'六轴总均':<18}{ms:>10.2f}{mw:>10.2f}{ms - mw:>+8.2f}")

    fs = sum(1 for r in S if r["would_fail"])
    fw = sum(1 for r in W if r["would_fail"])
    print(f"\n  判失败率（任一轴 <{FLOOR:g}，判官自己的地板）")
    print(f"    强侧（真跑到 500+ 章的书） {fs}/{len(S)} = {fs / max(1, len(S)) * 100:.0f}%   ← 越低越好，这些是该过的")
    print(f"    弱侧（真没写下去的书）     {fw}/{len(W)} = {fw / max(1, len(W)) * 100:.0f}%   ← 越高越好，这些是该毙的")

    # 判读：判官能不能把强弱排开
    import itertools

    pairs = [(a, b) for a in S for b in W]
    win = sum(1 for a, b in pairs if a["mean"] > b["mean"])
    tie = sum(1 for a, b in pairs if a["mean"] == b["mean"])
    auc = (win + 0.5 * tie) / max(1, len(pairs))
    print(f"\n  排序能力 AUC = {auc:.2f}（0.5 = 抛硬币；随机取一强一弱，强分更高的概率）")
    verdict = (
        "判官有效 → 问题在修复循环把不合格候选补到及格，收紧修复"
        if auc >= 0.70
        else "判官失明 → 加牙只会放大错误，必须先重做判官"
        if auc <= 0.60
        else "信号弱 → 需要扩样本再定"
    )
    print(f"  → {verdict}")

    OUT_DIR.mkdir(exist_ok=True)
    (OUT_DIR / "seriality-judge-validation.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print("\n已存 .benchmark/seriality-judge-validation.json")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=14, help="每侧本数")
    ap.add_argument("--seed", type=int, default=828)
    ap.add_argument("--chapters", type=int, default=500)
    ap.add_argument("--concurrency", type=int, default=8)
    a = ap.parse_args()
    asyncio.run(
        run(n=a.n, seed=a.seed, chapter_count=a.chapters, concurrency=a.concurrency)
    )


if __name__ == "__main__":
    main()
