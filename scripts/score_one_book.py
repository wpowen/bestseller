"""按现行吸引力标准给单本存量书打分（门1 blurb + premise advisory + 门2 arena）。"""

from __future__ import annotations

# ruff: noqa: ANN201, RUF001, RUF002, RUF003, E501
import asyncio
import sys

from sqlalchemy import select

from bestseller.infra.db.models import ProjectModel
from bestseller.infra.db.session import session_scope
from bestseller.services.blurb_appeal_gate import evaluate_blurb_appeal
from bestseller.services.premise_appeal_arena import make_deepseek_judge, run_appeal_arena
from bestseller.services.premise_appeal_judge import evaluate_premise_appeal
from bestseller.services.story_appeal import load_story_appeal_config
from bestseller.settings import load_settings

SLUG = sys.argv[1] if len(sys.argv) > 1 else "custom-xuanhuan-1781970913"
GENRE = sys.argv[2] if len(sys.argv) > 2 else "玄幻"
SUB = sys.argv[3] if len(sys.argv) > 3 else "赛博修仙"


async def main():
    settings = load_settings()
    cfg = load_story_appeal_config()
    b_min = float(cfg["meets_bar"]["blurb_min"])
    w_min = float(cfg["arena"]["story_winrate_min"])

    async with session_scope() as session:
        p = (await session.execute(select(ProjectModel).where(ProjectModel.slug == SLUG))).scalar_one()
        m = p.metadata_json or {}
        title = p.title
        synopsis = (m.get("synopsis") or "").strip()
        premise = (m.get("premise") or m.get("logline") or "").strip()
        tags = m.get("tags") or []

        print(f"《{title}》  题材={GENRE}/{SUB}  简介长度={len(synopsis)}字\n")

        # 门1：确定性 blurb gate（逐维）
        blurb = evaluate_blurb_appeal(title=title, synopsis=synopsis, premise=premise,
                                      tags=tags, genre=GENRE, sub_genre=SUB, config=cfg)
        print(f"门1 · 简介点击力（确定性）= {blurb.total:.0f}/100 [{blurb.grade}]  "
              f"{'✅≥'+str(int(b_min)) if blurb.total>=b_min else '✗<'+str(int(b_min))}")
        for d in sorted(blurb.dimensions, key=lambda x: x.score):
            flag = "⚠️" if d.score < 3 else "  "
            print(f"   {flag} {d.label:<10} {d.score:.1f}/5  {d.rationale}")
        print("   弱项建议:", "；".join(blurb.suggestions[:4]) or "（无）")

        # premise 判官（advisory，跨家族 DeepSeek）
        pv = await evaluate_premise_appeal(
            session, settings, premise=premise, synopsis=synopsis, title=title, tags=tags,
            genre=GENRE, sub_genre=SUB, chapter_count=600, project_slug=None,
            judge_model_key="deepseek-v4-flash", config=cfg,
        )
        print(f"\n[advisory] 故事吸引力 LLM 判官(DeepSeek)= {pv.total:.0f}/100  触发钩子={list(pv.triggers_fired)}")
        for d in sorted(pv.dimensions, key=lambda x: x.score)[:4]:
            print(f"   - {d.label} {d.score:.1f}/5  {d.rationale}")
        print("   建议:", "；".join(pv.suggestions[:4]) or "（无）")

        # 门2：arena vs 同题材真爆款（DeepSeek 双盲位置交换）
        judge = make_deepseek_judge(session, settings)
        candidate = synopsis or premise
        arena = await run_appeal_arena(candidate_blurb=candidate, genre=GENRE, sub_genre=SUB,
                                       judge=judge, min_refs=2, max_refs=3)
        print(f"\n门2 · 故事质量 arena vs 真爆款 = win-rate {arena.win_rate:.2f}  "
              f"{'✅≥'+str(w_min) if arena.win_rate>=w_min else '✗<'+str(w_min)}")
        for d in arena.details:
            print(f"   vs {d['ref']}: {d['outcome']}")

    gate1 = blurb.total >= b_min
    gate2 = arena.win_rate >= w_min
    print("\n" + "=" * 60)
    print(f"综合判定（现行两道竞品锚定门）：门1 blurb {'过' if gate1 else '未过'}({blurb.total:.0f}) "
          f"+ 门2 arena {'过' if gate2 else '未过'}({arena.win_rate:.2f}) → "
          f"{'✅ 达标' if (gate1 and gate2) else '✗ 不达标'}")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
