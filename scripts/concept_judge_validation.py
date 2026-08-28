"""验判官——在用它评价任何东西之前，先证明它有区分力。

2026-08-28：对标验证台跑出「框架赢了已跑满 500+ 章的真榜单书 63%」，
与人工判断正面冲突；同一批书同一份代码重跑两次，总胜负一致率 5/12=42%
（抛硬币是 50%）。结论只能有一个：先验尺子，再用尺子。

三项检验，全部用**真榜单书对真榜单书**，不掺框架产出：

  区分力  拿在读人数差 5 倍以上的两本真书，判官能不能挑出人多的那本。
          挑不出 → 它对「哪个更能拉读者」没有信号，下游全部作废。
  位置偏置 同一对交换左右再判一次。翻面 = 它在看位置不在看内容。
  重测信度 同一对同一顺序判 N 次。不一致 = 纯采样噪声。

在读人数是外部硬真值：这些书都已经跑完 500+ 章，谁被更多人读是既成事实，
不是我们的主观标注。这是本仓库能拿到的最接近 ground truth 的东西。
"""

from __future__ import annotations

import argparse
import asyncio
import itertools
import json
import os
import pathlib
import random
import re
import sys

import httpx

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[0]))

API = "https://api.minimaxi.com/v1/text/chatcompletion_v2"
RANK_JSON = pathlib.Path(__file__).resolve().parents[1] / ".benchmark" / "fanqie_rank.json"
OUT_DIR = pathlib.Path(__file__).resolve().parents[1] / ".benchmark"
AXES = ("sustain", "escalate", "hook", "concrete")

_JUDGE_SYSTEM = (
    "你是网文平台的选题主编，每天要从大量投稿里挑出能连载几百上千章的那几个。"
    "只输出JSON。"
)


def _key() -> str:
    k = os.environ.get("MINIMAX_API_KEY") or os.environ.get("BESTSELLER__LLM__API_KEY")
    if not k:
        raise SystemExit("缺 MINIMAX_API_KEY")
    return k


async def _call(client, system: str, user: str, *, max_tokens: int = 900, temperature: float) -> str:
    r = await client.post(
        API,
        json={
            "model": "MiniMax-M3",
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "thinking": {"type": "disabled"},
        },
    )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"] or ""


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
    """取简介前若干字作为「这本书是什么」——两边同样处理，不做压缩。

    刻意不走 LLM 压缩：压缩本身带随机性，会把生成噪声混进判官噪声里，
    使这次检验测不出判官自己的问题。
    """
    text = str(book.get("简介") or "").strip()
    text = re.sub(r"^【[^】]*】", "", text).strip()
    text = re.sub(r"\s+", " ", text)
    return text[:160]


def load_extreme_pairs(*, n_pairs: int, seed: int) -> list[tuple[dict, dict]]:
    """决定性对照：跑成了的长篇 vs 早早断更的扑街书。

    必须先跑这一组。若判官连「1700 章 / 49 万在读」和「88 章 / 1.6 万在读」
    都分不出，那它对好坏没有任何信号；若它分得出，则说明它有效度，只是
    500+ 章长篇彼此之间的人气差异并不由简介决定——那是真值选错，不是判官坏。
    这两种结论指向完全不同的修法，所以不能跳过。
    """
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
    by_ch: dict[str, list[dict]] = {}
    for b in weak:
        by_ch.setdefault(str(b.get("平台") or "?"), []).append(b)
    pairs: list[tuple[dict, dict]] = []
    for hi in strong:
        pool = by_ch.get(str(hi.get("平台") or "?")) or []
        if not pool:
            continue
        pairs.append((hi, pool.pop()))
        if len(pairs) >= n_pairs:
            break
    return pairs


def load_pairs(*, n_pairs: int, min_ratio: float, seed: int) -> list[tuple[dict, dict]]:
    """构造已知优劣的真书对：同频道、同为 500+ 章长篇、在读人数差 min_ratio 倍以上。

    同频道 + 同为长篇是为了把 sustain/题材偏好这些混杂变量按住，让「在读人数」
    尽量成为两本书之间唯一的系统性差异。
    """
    rows = json.loads(RANK_JSON.read_text(encoding="utf-8"))
    pool = [
        b
        for b in rows
        if b.get("榜单类型") == "阅读榜"
        and _chapters(b) >= 500
        and len(_blurb(b)) >= 60
        and float(b.get("在读人数_数值") or 0) > 0
    ]
    rng = random.Random(seed)
    by_channel: dict[str, list[dict]] = {}
    for b in pool:
        by_channel.setdefault(str(b.get("平台") or "?"), []).append(b)

    cands: list[tuple[dict, dict]] = []
    for books in by_channel.values():
        for hi, lo in itertools.combinations(books, 2):
            a, b = float(hi["在读人数_数值"]), float(lo["在读人数_数值"])
            if a < b:
                hi, lo, a, b = lo, hi, b, a
            if b > 0 and a / b >= min_ratio:
                cands.append((hi, lo))
    rng.shuffle(cands)

    picked: list[tuple[dict, dict]] = []
    used: set[str] = set()
    for hi, lo in cands:
        if hi["书名"] in used or lo["书名"] in used:
            continue
        picked.append((hi, lo))
        used.update({hi["书名"], lo["书名"]})
        if len(picked) >= n_pairs:
            break
    return picked


async def judge(client, left: str, right: str, *, temperature: float) -> dict:
    user = (
        "两本书都要连载到 500 章以上。\n\n"
        "A：" + left + "\n\nB：" + right + "\n\n"
        "四项各自独立判断，不要让某一项影响另一项：\n"
        "1. sustain：哪个能持续产出同类故事到目标章数，而不是开局高光之后没东西写；\n"
        "2. escalate：哪个的升级是换玩法、换局面，而不是换更大的名词或数字；\n"
        "3. hook：哪个更让人想点开第一章；\n"
        "4. concrete：哪个的设定更具体可想象，不是概念口号。\n"
        '输出JSON：{"sustain":"A/B","escalate":"A/B","hook":"A/B","concrete":"A/B"}'
    )
    return _json_of(await _call(client, _JUDGE_SYSTEM, user, temperature=temperature))


async def run_placement(*, n_pairs: int, seed: int, repeats: int, concurrency: int = 5) -> None:
    """把**真正的生产淘汰赛**产出，放到判官有分辨率的那把尺子上。

    2026-08-28 两处修正，对应此前翻车的两个原因：

    ① 走生产路径。此前用 `_build_raw_idea_pool_messages` 取 ideas[0]，
       `run_concept_tournament` 一次都没跑过，于是任何淘汰赛层的改动都验证不了
       （我据此改过一次选拔逻辑，已 revert）。现改为经 TournamentDriver 跑完整
       淘汰赛，并用 driver.assert_ran() 强制自证真的调到了被测代码。

    ② 对手换成扑街书。效度检验结论：判官在「成功长篇 vs 成功长篇」区间四轴
       52-54%（等于抛硬币），在「成功长篇 vs 扑街断更书」区间 73-80%。
       此前对标台全程在无分辨率区间比较，读数无意义。

    同一批扑街书上跑两条臂，刻度线与被测同批同判官，消除批次差异：

        刻度线臂  真成功长篇（≥500 章 且 ≥30 万在读）vs 扑街书
        被测臂    框架淘汰赛冠军（用扑街书自己的题材/频道生成）vs 扑街书

    框架落在刻度线附近 = 达到榜单水平；落在 50% = 与扑街书无法区分。
    """
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    from concept_tournament_driver import TournamentDriver, run_production_tournament

    rows = json.loads(RANK_JSON.read_text(encoding="utf-8"))
    weak = [
        b for b in rows
        if 0 < _chapters(b) < 120
        and 0 < float(b.get("在读人数_数值") or 0) < 20_000
        and len(_blurb(b)) >= 60
    ]
    strong = [
        b for b in rows
        if _chapters(b) >= 500
        and float(b.get("在读人数_数值") or 0) >= 300_000
        and len(_blurb(b)) >= 60
    ]
    rng = random.Random(seed)
    rng.shuffle(weak)
    rng.shuffle(strong)
    sample = weak[:n_pairs]
    by_ch: dict[str, list[dict]] = {}
    for b in strong:
        by_ch.setdefault(str(b.get("平台") or "?"), []).append(b)

    print(f"扑街对照 {len(sample)} 本（<120 章 且 在读<2 万）")
    print("  刻度线臂：真成功长篇 vs 扑街书    被测臂：框架完整淘汰赛冠军 vs 扑街书")
    print(f"  每本：左右各判一次 × {repeats} 轮；框架侧走 run_concept_tournament\n")

    OUT_DIR.mkdir(exist_ok=True)
    records: list[dict] = []
    dry: list[dict] = []
    lock = asyncio.Lock()
    done = 0

    async with httpx.AsyncClient(timeout=300.0, headers={"Authorization": "Bearer " + _key()}) as c:
        # 每轮淘汰赛实测 ~35 次模型调用、单调用 ~30s；不放宽并发跑不完
        driver = TournamentDriver(c, concurrency=14)
        sem = asyncio.Semaphore(concurrency)

        async def one(idx: int, book: dict) -> None:
            nonlocal done
            async with sem:
                theirs = _blurb(book)
                channel = str(book.get("平台") or "男频")
                genre = str(book.get("分类") or "玄幻")
                arms: dict[str, str] = {}
                # 被测臂：真生产淘汰赛
                try:
                    out = await run_production_tournament(
                        driver,
                        genre=genre,
                        sub_genre=genre,
                        chapter_count=500,
                        audience_orientation=channel,
                        seed=seed + idx,
                    )
                    arms["框架"] = out.get("concept") or out.get("high_concept") or ""
                    if not arms["框架"]:
                        # 干涸必须留痕。静默跳过会让「跑了但没产出」和「压根没跑」
                        # 长得一样——2026-08-28 基线跑批里框架臂 0 条记录，
                        # 差点被读成「脚本坏了」，其实是 12/12 全部干涸。
                        dry.append({"idx": idx, "book": str(book["书名"]), "genre": genre})
                except Exception as exc:  # noqa: BLE001
                    print(f"  [{idx}] 淘汰赛失败 {type(exc).__name__}: {str(exc)[:90]}", flush=True)
                    dry.append({"idx": idx, "book": str(book["书名"]), "error": type(exc).__name__})
                # 刻度线臂：同频道真成功长篇
                pool = by_ch.get(channel) or []
                if pool:
                    arms["榜单"] = _blurb(pool[idx % len(pool)])

                for arm, ours in arms.items():
                    if not ours:
                        continue
                    for rep in range(repeats):
                        try:
                            fwd, rev = await asyncio.gather(
                                judge(c, ours, theirs, temperature=1.0),
                                judge(c, theirs, ours, temperature=1.0),
                            )
                        except Exception as exc:  # noqa: BLE001
                            print(f"  [{idx}] {arm} 判定失败 {type(exc).__name__}", flush=True)
                            continue
                        if not fwd or not rev:
                            continue
                        async with lock:
                            records.append({
                                "temp": 1.0, "arm": arm, "pair": idx, "rep": rep,
                                "hi": arm, "lo": str(book["书名"]), "ratio": 1.0,
                                "ours": ours[:300],
                                "fwd": {a: (fwd.get(a) == "A") for a in AXES if fwd.get(a) in ("A", "B")},
                                "rev": {a: (rev.get(a) == "B") for a in AXES if rev.get(a) in ("A", "B")},
                            })
                done += 1
                print(f"  {done}/{len(sample)} 完成（淘汰赛累计 {driver.calls} 次调用）", flush=True)

        await asyncio.gather(*(one(i, b) for i, b in enumerate(sample, 1)))

        # 铁律自证：这次必须能证明真的跑到了被测代码
        driver.assert_ran(at_least=len(sample))
        print(f"\n✅ 生产淘汰赛确认执行：{driver.calls} 次模型调用，阶段 {driver.stages}")
        if driver.failures:
            print(f"   （其中 {driver.failures} 次取模型失败走了兜底，与生产同构）")

    print(f"\n  框架淘汰赛干涸 {len(dry)}/{len(sample)} 本"
          + ("（干涸=一条候选都没通过闸门，生产上会回落保底概念）" if dry else ""))
    for d in dry[:6]:
        print(f"    · {d.get('book')}（{d.get('genre') or d.get('error')}）")

    _report_arms(records)
    (OUT_DIR / "judge-placement.json").write_text(
        json.dumps(records, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print("已存 .benchmark/judge-placement.json")


def _report_arms(records: list[dict]) -> None:
    """两条臂并排——刻度线是「达到榜单水平」的那条线，不是 100%。"""
    from math import comb

    def stat(arm: str, ax: str) -> tuple[float, int] | None:
        v = [
            (r["fwd"][ax], r["rev"][ax])
            for r in records
            if r.get("arm") == arm and ax in r["fwd"] and ax in r["rev"]
        ]
        if not v:
            return None
        return sum(f + b for f, b in v) / (2 * len(v)), 2 * len(v)

    def fisher(a: int, b: int, c: int, d: int) -> float:
        n = a + b + c + d
        f = lambda x: comb(a + b, x) * comb(c + d, a + c - x) / comb(n, a + c)  # noqa: E731
        o = f(a)
        lo, hi = max(0, a + c - (c + d)), min(a + b, a + c)
        return sum(f(x) for x in range(lo, hi + 1) if f(x) <= o + 1e-12)

    print("\n═══ 打同一批扑街书的战绩 ═══")
    print(f"  {'轴':<10}{'刻度线(榜单)':>13}{'框架':>8}{'差':>8}{'p':>9}")
    for ax in AXES:
        a, b = stat("榜单", ax), stat("框架", ax)
        if not a or not b:
            continue
        ka, na = round(a[0] * a[1]), a[1]
        kb, nb = round(b[0] * b[1]), b[1]
        p = fisher(ka, na - ka, kb, nb - kb)
        note = "" if ax != "hook" else "  ←该轴效度不足"
        print(f"  {ax:<10}{a[0] * 100:>12.0f}%{b[0] * 100:>7.0f}%{(b[0] - a[0]) * 100:>+7.0f}pt{p:>9.3f}{note}")
    n_a = stat("榜单", "sustain")
    n_b = stat("框架", "sustain")
    print(f"\n  样本：刻度线 {n_a[1] if n_a else 0} 次判定 / 框架 {n_b[1] if n_b else 0} 次判定")
    print("  50% = 与扑街书无法区分；刻度线 = 真榜单成功长篇的战绩\n")


async def run(
    *, n_pairs: int, min_ratio: float, seed: int, repeats: int, temps: list[float], mode: str
) -> None:
    if mode == "place":
        await run_placement(n_pairs=n_pairs, seed=seed, repeats=repeats)
        return
    pairs = (
        load_extreme_pairs(n_pairs=n_pairs, seed=seed)
        if mode == "extreme"
        else load_pairs(n_pairs=n_pairs, min_ratio=min_ratio, seed=seed)
    )
    if not pairs:
        raise SystemExit("没有构造出满足条件的书对")
    gaps = [float(h["在读人数_数值"]) / float(l["在读人数_数值"]) for h, l in pairs]
    print(
        f"真书对 {len(pairs)} 组（同频道·均 500+ 章·在读人数差 ≥{min_ratio:g}x，"
        f"实际中位 {sorted(gaps)[len(gaps) // 2]:.1f}x）"
    )
    print(f"每组：左右各判一次 × 重复 {repeats} 轮 × 温度 {temps}\n")

    OUT_DIR.mkdir(exist_ok=True)
    records: list[dict] = []
    async with httpx.AsyncClient(timeout=300.0, headers={"Authorization": "Bearer " + _key()}) as c:
        for temp in temps:
            for idx, (hi, lo) in enumerate(pairs, 1):
                bh, bl = _blurb(hi), _blurb(lo)
                for rep in range(repeats):
                    try:
                        fwd, rev = await asyncio.gather(
                            judge(c, bh, bl, temperature=temp),  # 高人气在 A
                            judge(c, bl, bh, temperature=temp),  # 高人气在 B
                        )
                    except Exception as exc:  # noqa: BLE001
                        print(f"  [T{temp}][{idx}] 调用失败 {type(exc).__name__}")
                        continue
                    if not fwd or not rev:
                        continue
                    records.append(
                        {
                            "temp": temp,
                            "pair": idx,
                            "rep": rep,
                            "hi": hi["书名"],
                            "lo": lo["书名"],
                            "ratio": float(hi["在读人数_数值"]) / float(lo["在读人数_数值"]),
                            # 归一化成「判官是否选中了高人气那本」
                            "fwd": {a: (fwd.get(a) == "A") for a in AXES if fwd.get(a) in ("A", "B")},
                            "rev": {a: (rev.get(a) == "B") for a in AXES if rev.get(a) in ("A", "B")},
                        }
                    )
                print(f"  [T{temp}] {idx}/{len(pairs)} {hi['书名'][:12]} vs {lo['书名'][:12]}", end="\r")
            print(" " * 78, end="\r")
            _report(records, temp)

    path = OUT_DIR / "judge-validation.json"
    path.write_text(json.dumps(records, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n已存 {path}")


def _report(records: list[dict], temp: float) -> None:
    rs = [r for r in records if r["temp"] == temp]
    if not rs:
        return
    print(f"═══ 温度 {temp}（{len(rs)} 次成对判定）═══")
    print(f"  {'轴':<10}{'区分力':>9}{'位置一致':>10}{'重测一致':>10}")
    for ax in AXES:
        votes = [(r["fwd"].get(ax), r["rev"].get(ax)) for r in rs if ax in r["fwd"] and ax in r["rev"]]
        if not votes:
            continue
        # 区分力：两个方向合起来，选中高人气那本的比例（50% = 无信号）
        acc = sum(f + v for f, v in votes) / (2 * len(votes))
        # 位置一致：换边后是否还判同一本书赢（低 = 判官在看位置不在看内容）
        pos = sum(1 for f, v in votes if f == v) / len(votes)
        # 重测一致：同一对同一方向、不同轮次之间的一致率
        by_pair: dict[int, list[bool]] = {}
        for r in rs:
            if ax in r["fwd"]:
                by_pair.setdefault(r["pair"], []).append(r["fwd"][ax])
        agree = [
            sum(1 for a, b in itertools.combinations(v, 2) if a == b) / max(1, len(list(itertools.combinations(v, 2))))
            for v in by_pair.values()
            if len(v) > 1
        ]
        ret = sum(agree) / len(agree) if agree else float("nan")
        print(f"  {ax:<10}{acc * 100:>8.0f}%{pos * 100:>9.0f}%{ret * 100:>9.0f}%")
    print("  基准：区分力 50% = 无信号；位置一致 100% = 无位置偏置；重测一致 100% = 无采样噪声\n")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pairs", type=int, default=14)
    ap.add_argument("--ratio", type=float, default=5.0)
    ap.add_argument("--repeats", type=int, default=2)
    ap.add_argument("--seed", type=int, default=828)
    ap.add_argument("--temps", type=str, default="1.0,0.0")
    ap.add_argument("--mode", choices=("readers", "extreme", "place"), default="readers")
    a = ap.parse_args()
    asyncio.run(
        run(
            n_pairs=a.pairs,
            min_ratio=a.ratio,
            seed=a.seed,
            repeats=a.repeats,
            temps=[float(t) for t in a.temps.split(",")],
            mode=a.mode,
        )
    )


if __name__ == "__main__":
    main()
