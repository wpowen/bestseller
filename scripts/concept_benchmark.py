"""创意对标验证台：以榜单长篇为目标，量框架创意离它有多远。

用户提出的设计（2026-08-28）：榜单书本身就是标准答案，不需要人工标注。
同样的输入参数（分类/标签/频道），框架生成 vs 真实爆款，盲评胜率即分数。

## 三条设计约束（缺一条，测出来的分就是假的）

1. **同格式比同格式**。我们产出的是一句话创意，榜单给的是完整简介（带标签行、
   分行、对白）——直接对比，判官光看格式就能认出谁是谁。所以先把榜单简介压成
   同格式的一句话，压缩时**只许删不许编**。
2. **盲**。左右随机互换；判官提示里不出现「AI/生成/框架/投稿」。
3. **只用已证明的长篇**。阅读榜 + ≥500 章：这些书**真的跑到了**那个长度。

## v2 的两处修复（2026-08-28 基线定罪）

**① 四轴独立判。** v1 把 winner 放在四轴之前，判官选定一边后四条轴全打给它
——真机 12/12 完全一致，分轴信息量为零。v2 删掉 winner 项，由四轴多数票导出。

**② 加发动机推演。** v1 只比一句话，而**一句话里装不下发动机**：基线里框架赢了
6/12，输给它的对照是**真的跑了 1603 章**的书；框架那句「守墓少年掘开师父的坟，
刀出鞘时方圆十里亡魂下跪」确实更有画面——但好句子和发动机是两回事，只看一句话
的判官只会奖励漂亮。v2 逼两边都回答「写到第 N 章时在解决什么、对手是谁、
手上多了什么」，再连同一句话一起判。答不出来，就是没有发动机。

用法：
    .venv/bin/python scripts/concept_benchmark.py run --n 12 --tag v2-baseline
    .venv/bin/python scripts/concept_benchmark.py compare v2-baseline after-fix
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import re
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

API = "https://api.minimaxi.com/v1/chat/completions"
OUT_DIR = ROOT / ".benchmark"
RANK_JSON = OUT_DIR / "fanqie_rank.json"
AXES = ("sustain", "escalate", "hook", "concrete")


def _key() -> str:
    for line in (ROOT / ".env").read_text().splitlines():
        if line.startswith("MINIMAX_API_KEY="):
            return line.split("=", 1)[1].strip()
    raise SystemExit("MINIMAX_API_KEY not found in .env")


async def _call(client, system: str, user: str, *, max_tokens: int = 2000) -> str:
    r = await client.post(
        API,
        json={
            "model": "MiniMax-M3",
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
    return r.json()["choices"][0]["message"]["content"] or ""


def _json_of(raw: str) -> dict:
    text = re.sub(r"<think>.*?</think>", "", raw, flags=re.S)
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return {}
    try:
        return json.loads(m.group(0))
    except Exception:
        return {}


def _chapters(book: dict) -> int:
    m = re.search(r"第\s*([0-9]+)\s*章", str(book.get("最新章节") or ""))
    return int(m.group(1)) if m else 0


def load_targets(min_chapters: int = 500) -> list[dict]:
    rows = json.loads(RANK_JSON.read_text(encoding="utf-8"))
    return [
        b
        for b in rows
        if b.get("榜单类型") == "阅读榜"
        and _chapters(b) >= min_chapters
        and len(str(b.get("简介") or "")) > 120
    ]


async def compress_to_logline(client, book: dict) -> str:
    """把榜单简介压成与框架同格式的一句话。只许删不许编。"""
    system = (
        "你把长简介压缩成一句话故事梗概。铁律：只许从原文里取，"
        "不许添加原文没有的人物、设定、情节或评价。只输出这一句话。"
    )
    user = (
        "【原简介】\n"
        + str(book.get("简介"))
        + "\n\n压成一句话（60-120字）：谁 + 处在什么反常处境 + 由此开启什么。"
        "保留原文的具体名词，不要写成抽象概括。只输出这句话。"
    )
    return (await _call(client, system, user, max_tokens=600)).strip().split("\n")[0]


async def generate_concept(client, book: dict) -> str:
    """用生产 prompt、以该书的参数生成一条创意。"""
    from bestseller.services.concept_tournament import (
        _build_raw_idea_pool_messages,
        _parse_raw_idea_pool,
    )

    tags = [t for t in (book.get("标签") or []) if isinstance(t, str)][:5]
    channel = "男频" if book.get("平台") == "男频" else "女频"
    system, user = _build_raw_idea_pool_messages(
        genre=str(book.get("分类") or "玄幻"),
        sub_genre=str(book.get("分类") or "玄幻"),
        count=3,
        seed_concept="",
        prompt_arm="author_pitch",
        focus_hint="、".join(tags),
        audience_orientation=channel,
        tone_preference="",
        effect_skills=(),
        creation_intent_block="",
    )
    raw = await _call(client, system, user, max_tokens=4000)
    ideas = _parse_raw_idea_pool(raw, limit=3)
    return ideas[0][1] if ideas else ""


_PROJECT_SYSTEM = (
    "你是长篇连载的责任编辑。给你一个故事的开局，回答它写到指定章数时的样子。"
    "不要复述开局，要说那时候的新局面。只输出JSON。"
)


async def project_chapter(client, logline: str, at: int) -> str:
    """逼一个开局把发动机展开——答不出来就是没有发动机。"""
    user = (
        "【开局】"
        + logline
        + "\n\n这本书写到第 "
        + str(at)
        + " 章时：主角在解决什么具体问题？对手是谁（不能还是开局那个）？"
        "他手上多了什么开局没有的东西？这一章的冲突是什么？\n"
        '输出JSON：{"problem":"...","opponent":"...","gained":"...","conflict":"..."}'
    )
    d = _json_of(await _call(client, _PROJECT_SYSTEM, user, max_tokens=900))
    if not d:
        return ""
    return "；".join(
        k + "=" + str(d.get(k) or "") for k in ("problem", "opponent", "gained", "conflict")
    )


_JUDGE_SYSTEM = (
    "你是网文平台的选题主编，每天要从大量投稿里挑出能连载几百上千章的那几个。"
    "只输出JSON。"
)


async def judge_pair(
    client,
    left: str,
    right: str,
    *,
    left_far: str = "",
    right_far: str = "",
    target: int = 500,
) -> dict:
    """四轴独立判，不给总胜项——总胜由四轴多数票导出。"""
    far = ""
    if left_far or right_far:
        far = (
            "\n\n【A 写到第 " + str(target) + " 章时】" + (left_far or "（答不出）")
            + "\n【B 写到第 " + str(target) + " 章时】" + (right_far or "（答不出）")
        )
    user = (
        "这本书的目标是连载到 **" + str(target) + " 章**。\n\n"
        "A：" + left + "\n\nB：" + right + far + "\n\n"
        "四项各自独立判断，不要让某一项影响另一项：\n"
        "1. sustain：哪个能持续产出同类故事到目标章数，而不是开局高光之后没东西写；\n"
        "2. escalate：哪个的升级是换玩法、换局面，而不是换更大的名词或数字；\n"
        "3. hook：哪个更让人想点开第一章；\n"
        "4. concrete：哪个的设定更具体可想象，不是概念口号。\n"
        '输出JSON：{"sustain":"A/B","escalate":"A/B","hook":"A/B","concrete":"A/B",'
        '"why":"30字内说明落后的那个差在哪"}'
    )
    return _json_of(await _call(client, _JUDGE_SYSTEM, user, max_tokens=900))


async def run(n: int, tag: str, seed: int) -> None:
    OUT_DIR.mkdir(exist_ok=True)
    targets = load_targets()
    random.seed(seed)
    sample = random.sample(targets, min(n, len(targets)))
    print("标准答案池 " + str(len(targets)) + " 本（阅读榜 ≥500 章），抽样 " + str(len(sample)) + " 本\n")

    results: list[dict] = []
    async with httpx.AsyncClient(
        timeout=300.0, headers={"Authorization": "Bearer " + _key()}
    ) as c:
        for i, book in enumerate(sample, 1):
            title = str(book.get("书名"))
            target = _chapters(book)
            try:
                real, ours = await asyncio.gather(
                    compress_to_logline(c, book), generate_concept(c, book)
                )
            except Exception as exc:  # noqa: BLE001
                print("  [" + str(i) + "] " + title[:14] + " 生成失败 " + type(exc).__name__)
                continue
            if not ours or not real:
                print("  [" + str(i) + "] " + title[:14] + " 空产出，跳过")
                continue
            # 发动机推演：两边都往前推到目标章数
            far_probe = min(target, 500)
            try:
                real_far, ours_far = await asyncio.gather(
                    project_chapter(c, real, far_probe),
                    project_chapter(c, ours, far_probe),
                )
            except Exception:  # noqa: BLE001
                real_far = ours_far = ""
            ours_is_left = random.random() < 0.5
            left, right = (ours, real) if ours_is_left else (real, ours)
            lf, rf = (ours_far, real_far) if ours_is_left else (real_far, ours_far)
            v = await judge_pair(c, left, right, left_far=lf, right_far=rf, target=far_probe)
            if not v:
                print("  [" + str(i) + "] " + title[:14] + " 判官无输出，跳过")
                continue

            def side(key: str) -> str | None:
                pick = str(v.get(key) or "").strip().upper()
                if pick not in ("A", "B"):
                    return None
                return "ours" if (pick == "A") == ours_is_left else "real"

            axes = {a: side(a) for a in AXES}
            won = sum(1 for a in AXES if axes[a] == "ours")
            row = {
                "title": title,
                "category": book.get("分类"),
                "channel": book.get("平台"),
                "chapters": target,
                "real": real,
                "ours": ours,
                "real_far": real_far,
                "ours_far": ours_far,
                "axes": axes,
                "axes_won": won,
                "winner": "ours" if won > len(AXES) / 2 else "real",
                "why": v.get("why", ""),
            }
            results.append(row)
            mark = "✅框架胜" if row["winner"] == "ours" else "  榜单胜"
            print(
                "  [" + str(i) + "] " + title[:14].ljust(16) + mark
                + " 轴 " + str(won) + "/4  " + str(row["why"])[:30]
            )

    path = OUT_DIR / ("run-" + tag + ".json")
    path.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    _report(results, tag)
    print("\n已存 " + str(path))


def _report(rows: list[dict], tag: str) -> None:
    if not rows:
        print("\n无有效样本")
        return
    n = len(rows)
    win = sum(1 for r in rows if r["winner"] == "ours")
    print("\n=== " + tag + "（n=" + str(n) + "）===")
    print("  总胜率（四轴多数）  框架 " + str(win) + "/" + str(n) + " = " + format(win / n, ".0%"))
    for ax in AXES:
        w = sum(1 for r in rows if r["axes"].get(ax) == "ours")
        print("  " + ax.ljust(10) + " 框架 " + str(w) + "/" + str(n) + " = " + format(w / n, ">5.0%"))
    # 量具自检：四轴若与总胜完全一致，说明判官仍在锚定
    locked = sum(1 for r in rows if len({r["axes"].get(a) for a in AXES}) == 1)
    print("  四轴全同的样本 " + str(locked) + "/" + str(n) + "（越低越说明分轴真的独立）")


def compare(a: str, b: str) -> None:
    for tag in (a, b):
        p = OUT_DIR / ("run-" + tag + ".json")
        if not p.exists():
            raise SystemExit("缺少 " + str(p))
        _report(json.loads(p.read_text(encoding="utf-8")), tag)


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="跑一轮对标")
    r.add_argument("--n", type=int, default=12)
    r.add_argument("--tag", default="baseline")
    r.add_argument("--seed", type=int, default=828)
    c = sub.add_parser("compare", help="比较两轮")
    c.add_argument("a")
    c.add_argument("b")
    args = ap.parse_args()
    if args.cmd == "run":
        asyncio.run(run(args.n, args.tag, args.seed))
    else:
        compare(args.a, args.b)


if __name__ == "__main__":
    main()
