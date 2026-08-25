"""提示词真机验证台（L2/L3）——把 2026-08-24 池层 prompt A/B 的手工流程固化。

用法（都从仓库根目录跑，读 .env 的 MINIMAX_API_KEY，走生产同款 M3+thinking 关闭）：

  # L2 A/B：当前树上的 builder vs 一份存档 prompt（或两份存档互比），各 R 轮
  .venv/bin/python scripts/prompt_bench.py ab \
      --variant current --variant path/to/old_prompt.json --replicates 2

  # L3 判官稳定性：同一批创意重复判 K 次，输出每轴标准差
  .venv/bin/python scripts/prompt_bench.py judge-stability --repeats 5

判定规约（与 docs/一句话创意提示词工程分析-20260824.md 附录一同源）：
  * 主指标 = 生产排序判官六轴（同一把尺判所有 variant）+ freshness&click 双≥7 数
  * 副指标 = 产量、种词回声哨兵命中、场域种数
  * 结论标准 = 每一轮 replicate 都不输对照的最好一轮才算赢（防单轮噪声）
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import statistics
import sys
from pathlib import Path

sys.path.insert(0, "src")

API = "https://api.minimaxi.com/v1/chat/completions"
AXES = (
    "freshness", "click_seed", "character_logic",
    "action_seed", "promise_survival", "genre_fidelity",
)
# 已定罪的骨架/种词哨兵——命中≥半批说明示例/指令词又被复印了
ECHO_SENTINELS = ("今天", "少年", "亲手", "他要的不是", "宁可", "主动")

GEN_KW = dict(
    genre="玄幻", sub_genre="东方玄幻", count=12, seed_concept="",
    prompt_arm="author_pitch", focus_hint="", audience_orientation="男频",
    tone_preference="hot", effect_skills=("hype_satisfaction_engine",),
    creation_intent_block="",
)


def _load_key() -> str:
    for line in Path(".env").read_text().splitlines():
        if line.startswith("MINIMAX_API_KEY="):
            return line.split("=", 1)[1].strip()
    raise SystemExit("MINIMAX_API_KEY not found in .env")


async def _call(client, system: str, user: str, *, max_tokens: int, temperature: float) -> str:
    r = await client.post(API, json={
        "model": "MiniMax-M3",
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": temperature, "max_tokens": max_tokens,
        # 生产同款：不关思考 M3 会把全部预算烧进 <think> 返回空正文
        "thinking": {"type": "disabled"},
    })
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"] or ""


def _extract_json(raw: str) -> dict | None:
    txt = re.sub(r"<think>.*?</think>", "", raw, flags=re.S).strip()
    m = re.search(r"\{.*\}", txt, re.S)
    if not m:
        return None
    blob = m.group(0)
    try:
        return json.loads(blob)
    except Exception:
        for end in range(len(blob), 0, -1):
            if blob[end - 1] == "}":
                for closer in ("]}", "}]}"):
                    try:
                        return json.loads(blob[:end] + closer)
                    except Exception:
                        pass
        return None


def _variant_messages(spec: str) -> tuple[str, str, str]:
    """'current' → 树上 builder；路径 → 存档 {system,user} JSON。"""

    if spec == "current":
        from bestseller.services.concept_tournament import _build_raw_idea_pool_messages

        system, user = _build_raw_idea_pool_messages(**GEN_KW)
        return "current", system, user
    payload = json.loads(Path(spec).read_text())
    return Path(spec).stem, payload["system"], payload["user"]


async def _judge_pool(client, ideas: list[dict]) -> list[dict]:
    from bestseller.services.concept_tournament import _build_raw_idea_rank_messages

    pitch = {str(i.get("seed", "")): {"graft": str(i.get("graft", ""))} for i in ideas}
    tuples = [(str(i.get("lane", "")), str(i.get("seed", ""))) for i in ideas if i.get("seed")]
    ranked: list[dict] = []
    for b in range(0, len(tuples), 4):
        batch = tuples[b:b + 4]
        rs, ru = _build_raw_idea_rank_messages(
            genre=GEN_KW["genre"], sub_genre=GEN_KW["sub_genre"], ideas=batch,
            audience_orientation=GEN_KW["audience_orientation"], pitch_by_seed=pitch,
        )
        payload = _extract_json(await _call(client, rs, ru, max_tokens=8000, temperature=0.2))
        for item in (payload or {}).get("ranked") or []:
            try:
                idx = int(item.get("index", -1))
            except (TypeError, ValueError):
                continue
            if 0 <= idx < len(batch):
                item["seed"] = batch[idx][1]
                ranked.append(item)
    return ranked


def _pool_metrics(ideas: list[dict], ranked: list[dict]) -> dict:
    seeds = [str(i.get("seed", "")) for i in ideas]
    comp = [sum(float(r.get(a, 0)) for a in AXES) / len(AXES) for r in ranked] or [0.0]
    return {
        "yield": len(ideas),
        "composite": round(statistics.mean(comp), 2),
        **{a: round(statistics.mean(float(r.get(a, 0)) for r in ranked), 2) if ranked else 0.0 for a in AXES},
        "double7": sum(
            1 for r in ranked
            if float(r.get("freshness", 0)) >= 7 and float(r.get("click_seed", 0)) >= 7
        ),
        "domains": len({r.get("domain", "") for r in ranked if r.get("domain")}),
        "echo": {t: sum(t in s for s in seeds) for t in ECHO_SENTINELS if any(t in s for s in seeds)},
    }


async def cmd_ab(args) -> None:
    import httpx

    variants = [(spec, *_variant_messages(spec)[1:]) for spec in args.variant]
    names = [_variant_messages(spec)[0] for spec in args.variant]
    headers = {"Authorization": f"Bearer {_load_key()}", "Content-Type": "application/json"}
    results: dict[str, list[dict]] = {n: [] for n in names}
    async with httpx.AsyncClient(timeout=600, headers=headers) as client:
        for rep in range(args.replicates):
            for name, (_, system, user) in zip(names, variants):
                raw = await _call(client, system, user, max_tokens=9000, temperature=0.82)
                ideas = (_extract_json(raw) or {}).get("ideas") or []
                ranked = await _judge_pool(client, ideas)
                m = _pool_metrics(ideas, ranked)
                results[name].append(m)
                print(f"[rep{rep + 1}] {name}: {json.dumps(m, ensure_ascii=False)}", flush=True)
    print("\n== 汇总（每轮都不输对照最好一轮才算赢）==")
    for name, rows in results.items():
        comps = [r["composite"] for r in rows]
        print(f"{name}: composite={comps} double7={[r['double7'] for r in rows]}")
    out = Path(args.out or "prompt_bench_ab.json")
    out.write_text(json.dumps(results, ensure_ascii=False, indent=1))
    print(f"written {out}")


async def cmd_judge_stability(args) -> None:
    import httpx

    headers = {"Authorization": f"Bearer {_load_key()}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=600, headers=headers) as client:
        if args.pool:
            ideas = json.loads(Path(args.pool).read_text())
        else:
            name, system, user = _variant_messages("current")
            ideas = (_extract_json(
                await _call(client, system, user, max_tokens=9000, temperature=0.82)
            ) or {}).get("ideas") or []
            print(f"generated fixed pool: {len(ideas)} ideas", flush=True)
        runs: list[list[dict]] = []
        for k in range(args.repeats):
            ranked = await _judge_pool(client, ideas)
            runs.append(ranked)
            print(f"[judge pass {k + 1}] judged {len(ranked)}", flush=True)
    # 按 seed 对齐，算每轴跨次判分标准差
    by_seed: dict[str, dict[str, list[float]]] = {}
    for ranked in runs:
        for r in ranked:
            slot = by_seed.setdefault(r["seed"], {a: [] for a in AXES})
            for a in AXES:
                slot[a].append(float(r.get(a, 0)))
    print("\n== 判官稳定性（每轴：跨次判分标准差的中位数；≥1.0=该轴复判即换分）==")
    for a in AXES:
        stds = [
            statistics.pstdev(vals[a]) for vals in by_seed.values() if len(vals[a]) >= 2
        ]
        flips = sum(
            1 for vals in by_seed.values()
            if len(vals[a]) >= 2 and (min(vals[a]) < 7.0 <= max(vals[a]))
        )
        print(
            f"  {a}: median_std={round(statistics.median(stds), 2) if stds else 'n/a'}"
            f"  7分线翻面={flips}/{len(by_seed)}"
        )
    out = Path(args.out or "prompt_bench_stability.json")
    out.write_text(json.dumps(
        {seed: vals for seed, vals in by_seed.items()}, ensure_ascii=False, indent=1
    ))
    print(f"written {out}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    ab = sub.add_parser("ab", help="变体 A/B（生产判官统一打分）")
    ab.add_argument("--variant", action="append", required=True,
                    help="'current' 或存档 prompt JSON 路径；可重复")
    ab.add_argument("--replicates", type=int, default=2)
    ab.add_argument("--out", default=None)
    st = sub.add_parser("judge-stability", help="同池重复判分的方差")
    st.add_argument("--repeats", type=int, default=5)
    st.add_argument("--pool", default=None, help="固定创意池 JSON（缺省现生成一池）")
    st.add_argument("--out", default=None)
    args = parser.parse_args()
    asyncio.run(cmd_ab(args) if args.cmd == "ab" else cmd_judge_stability(args))


if __name__ == "__main__":
    main()
