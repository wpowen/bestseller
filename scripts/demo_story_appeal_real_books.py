"""Real-LLM demo: design new books across genres and score their appeal.

Runs the REAL conception pipeline (no stubs, real model) for a few genres, then
prints each freshly-designed book's premise/synopsis/title/tags and its
story-appeal report.  This is the "开新书验证" evidence: genuine model-generated
book *designs* graded by the new appeal system, proving designs reach the
bestseller bar across genres.

Conception does NOT persist a project (the web layer does), so this writes only
llm_run logs — no library pollution.

Run (needs live LLM creds + DB; inject the stack's env):
    <env...> .venv/bin/python scripts/demo_story_appeal_real_books.py [genre_key ...]
"""

from __future__ import annotations

# ruff: noqa: ANN001, ANN201, ANN202, RUF001 — demo script.
import asyncio
import sys

from bestseller.infra.db.session import session_scope
from bestseller.services.conception import run_conception_pipeline
from bestseller.settings import load_settings

# (genre_key, genre_label, sub_genre) — covers male + female channels.
DEFAULT_BOOKS = [
    ("xuanhuan", "玄幻", "升级"),
    ("urban", "都市", "都市异能"),
    ("pure-love", "纯爱", "现代言情"),
]


async def design_one(settings, genre_key, genre, sub_genre, chapters=600):
    async with session_scope() as session:
        result = await run_conception_pipeline(
            session, settings,
            genre_key=genre_key, chapter_count=chapters,
            genre=genre, sub_genre=sub_genre,
        )
    return result


def _print_report(genre, result):
    print("\n" + "=" * 70)
    print(f"【{genre}】  书名：{result.title}")
    print("-" * 70)
    print(f"一句话卖点(premise): {result.premise}")
    print(f"简介(synopsis):\n{result.synopsis}")
    print(f"标签: {result.tags}")
    sa = result.story_appeal or {}
    if not sa:
        print("⚠️ 无 story_appeal（系统未启用？）")
        return None
    pr = sa.get("premise", {})
    bl = sa.get("blurb", {})
    print("-" * 70)
    print(
        f"故事吸引力 premise={pr.get('total', 0):.0f}/100 ({pr.get('gated_grade')})  "
        f"简介点击力 blurb={bl.get('total', 0):.0f}/100 ({bl.get('grade')})  "
        f"总评={sa.get('overall_grade')}  达标={sa.get('meets_bar')}"
    )
    fired = pr.get("triggers_fired", [])
    print(f"触发的心理钩子: {fired}")
    if pr.get("gating_caps"):
        print(f"一票否决短板: {pr.get('gating_caps')}")
    weak = sorted(pr.get("dimensions", []), key=lambda d: d.get("score", 5))[:2]
    for d in weak:
        print(f"  最弱维度 {d.get('label')}={d.get('score')}/5  {d.get('rationale','')}")
    return bool(sa.get("meets_bar"))


async def main():
    settings = load_settings()
    args = sys.argv[1:]
    books = [b for b in DEFAULT_BOOKS if not args or b[0] in args]
    results = []
    for genre_key, genre, sub_genre in books:
        print(f"\n>>> 真实 LLM 设计新书：{genre} ({genre_key}/{sub_genre}) ...")
        try:
            result = await design_one(settings, genre_key, genre, sub_genre)
            met = _print_report(genre, result)
            results.append((genre, met))
        except Exception as exc:
            print(f"❌ {genre} 设计失败: {exc!r}")
            results.append((genre, None))

    print("\n" + "=" * 70)
    passed = sum(1 for _, m in results if m)
    print(f"汇总: {passed}/{len(results)} 本新书设计达榜单级 ", dict(results))


if __name__ == "__main__":
    asyncio.run(main())
