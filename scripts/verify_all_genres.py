"""每题材达标全量验证：对所有 canonical 题材真实生成新书，过两道竞品锚定门。

门1 简介点击力：确定性 blurb gate ≥ blurb_min（构思期，零 LLM、零家族偏见）。
门2 故事质量：候选简介 vs 同题材真实爆款简介的双盲位置交换 pairwise 胜率
            ≥ story_winrate_min（跨家族 DeepSeek 判官）。

对每个题材：run_conception_pipeline(真实模型) → 取 (title, synopsis|premise) →
评 blurb gate + run_appeal_arena。结果增量写 JSON（断点可续/可审）。

Run（注入栈 env；需 MINIMAX_API_KEY + DEEPSEEK_API_KEY + DB）：
    <env...> .venv/bin/python scripts/verify_all_genres.py [genre_key ...]
"""

from __future__ import annotations

# ruff: noqa: ANN001, ANN201, ANN202, RUF001, RUF002, RUF003, E501, S108 — validation script.
import asyncio
import json
from pathlib import Path
import sys

from bestseller.infra.db.session import session_scope
from bestseller.services.blurb_appeal_gate import evaluate_blurb_appeal
from bestseller.services.conception import run_conception_pipeline
from bestseller.services.premise_appeal_arena import make_deepseek_judge, run_appeal_arena
from bestseller.services.story_appeal import load_story_appeal_config
from bestseller.settings import load_settings

# (genre_key, genre_label, sub_genre) — 全 20 canonical 题材，男频/女频/general 全覆盖。
GENRES = [
    ("xuanhuan", "玄幻", "东方玄幻"),
    ("xianxia", "仙侠", "修真文明"),
    ("wuxia", "武侠", "传统武侠"),
    ("urban", "都市", "都市生活"),
    ("history", "历史", "架空历史"),
    ("military", "军事", "战争幻想"),
    ("scifi", "科幻", "星际文明"),
    ("apocalypse", "末世", "末日求生"),
    ("game", "游戏竞技", "电子竞技"),
    ("suspense", "悬疑推理", "侦探推理"),
    ("occult", "灵异", "灵异神怪"),
    ("infinite-flow", "无限流", "无限闯关"),
    ("light-novel", "轻小说", "同人衍生"),
    ("gu-yan", "古代言情", "宫斗宅斗"),
    ("xian-yan", "现代言情", "都市甜宠"),
    ("fantasy-romance", "幻想言情", "玄幻言情"),
    ("female-growth", "女性成长", "无CP大女主"),
    ("pure-love", "纯爱", "现代纯爱"),
    ("female-derivative", "穿书快穿", "穿书改命"),
    ("realistic", "现实", "现实百态"),
]

OUT = Path("/tmp/all_genres_results.json")


def _load_prev() -> dict:
    if OUT.exists():
        try:
            return json.loads(OUT.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


async def main():
    settings = load_settings()
    cfg = load_story_appeal_config()
    bar = cfg.get("meets_bar", {})
    b_min = float(bar.get("blurb_min", 65))
    w_min = float(cfg.get("arena", {}).get("story_winrate_min", 0.45))

    only = set(sys.argv[1:])
    conc = int(__import__("os").environ.get("GENRE_CONCURRENCY", "4"))
    results = _load_prev()
    todo = [g for g in GENRES if (not only or g[0] in only)
            and not (g[0] in results and results[g[0]].get("ok") is not None)]

    print(f"全题材达标验证 | 门1 blurb≥{b_min} | 门2 arena win-rate≥{w_min}（跨家族 DeepSeek）| 并发={conc}\n")
    sem = asyncio.Semaphore(conc)
    lock = asyncio.Lock()

    async def one(gkey, glabel, sub):
        async with sem:
            try:
                async with session_scope() as session:
                    rc = await run_conception_pipeline(
                        session, settings, genre_key=gkey, chapter_count=600,
                        genre=glabel, sub_genre=sub,
                    )
                    candidate = (rc.synopsis or "").strip() or rc.premise
                    blurb = evaluate_blurb_appeal(
                        title=rc.title, synopsis=rc.synopsis or rc.premise, premise=rc.premise,
                        tags=rc.tags, genre=glabel, sub_genre=sub, config=cfg,
                    )
                    judge = make_deepseek_judge(session, settings)
                    # min_refs=2: 所有题材都有≥2条自有参照 → 只跟同题材真爆款比，
                    # 不掺跨题材（避免现实/军事被玄幻钩子不公平碾压）。
                    arena = await run_appeal_arena(
                        candidate_blurb=candidate, genre=glabel, sub_genre=sub,
                        judge=judge, min_refs=2, max_refs=3,
                    )
                gate1 = blurb.total >= b_min
                gate2 = arena.win_rate >= w_min
                results[gkey] = {
                    "genre": glabel, "title": rc.title, "blurb": round(blurb.total, 1),
                    "arena_winrate": arena.win_rate, "arena_detail": list(arena.details),
                    "gate1_blurb": gate1, "gate2_arena": gate2, "ok": gate1 and gate2,
                    "premise": rc.premise[:160],
                }
                r = results[gkey]
                mark = "✅达标" if r["ok"] else ("△门1过/门2欠" if gate1 else "✗门1未过")
                line = f"  {glabel:<8} 《{r['title']}》 blurb={r['blurb']:.0f} arena={r['arena_winrate']:.2f} {mark}"
            except Exception as exc:
                results[gkey] = {"genre": glabel, "error": repr(exc), "ok": False}
                line = f"  ❌ {glabel}: {exc!r}"
            async with lock:
                OUT.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
                print(line, flush=True)

    await asyncio.gather(*(one(g[0], g[1], g[2]) for g in todo))

    done = [v for v in results.values() if "error" not in v and v.get("ok") is not None]
    passed = sum(1 for v in done if v["ok"])
    print("\n" + "=" * 62)
    print(f"全题材达标：{passed}/{len(done)} 通过两道竞品锚定门")
    for gkey, _, _ in GENRES:
        v = results.get(gkey)
        if not v:
            continue
        if "error" in v:
            print(f"  ❌ {v['genre']}: {v['error'][:60]}")
        else:
            mark = "✅" if v["ok"] else "✗"
            print(f"  {mark} {v['genre']:<8} blurb={v.get('blurb',0):.0f} arena={v.get('arena_winrate',0):.2f}")
    print("=" * 62)


if __name__ == "__main__":
    asyncio.run(main())
