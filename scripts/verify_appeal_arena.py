"""Story-quality HARD gate validation: new books vs REAL bestsellers (pairwise).

Answers "凭什么说达榜单级" with the trustworthy method (relative blind win-rate,
not absolute scores): each candidate blurb is judged head-to-head, double-blind &
position-swapped, against same-genre REAL bestseller blurbs, by a CROSS-FAMILY
judge (DeepSeek). win-rate ≥ bar ⇒ competitive with real bestsellers.

Candidates = the REAL model-generated new-book blurbs (玄幻/都市/纯爱) + a SLUSH
negative control + a real-bestseller sanity check (should land ~0.5 vs peers).

Run (inject stack env; needs DEEPSEEK_API_KEY + DB):
    <env...> .venv/bin/python scripts/verify_appeal_arena.py
"""

from __future__ import annotations

# ruff: noqa: ANN201, RUF001, RUF003, E501 — validation script.
import asyncio

from bestseller.infra.db.session import session_scope
from bestseller.services.premise_appeal_arena import make_deepseek_judge, run_appeal_arena
from bestseller.settings import load_settings

BAR = 0.45  # win-rate vs real bestsellers ≥ 0.45 ⇒ 与真实爆款同档（competitive）

# REAL model-generated new-book blurbs (from scripts/demo_story_appeal_real_books.py).
CANDIDATES = [
    ("玄幻·我能看见天道裂缝(真实生成)", "玄幻", "升级",
     "穿越到修仙世界的沐衍，本以为是废柴开局，却意外觉醒【天道熔炉系统】——十倍资源吸收效率，"
     "让他从杂役弟子一跃成为被质疑的异类。\n外门大比，他以淬体境碾压聚气境师兄，震惊全场。"
     "宗门长老以为他走了邪门歪道，派执法殿暗中调查。\n他发现自己不仅修炼快，还能看见别人看不见的"
     "东西——功法运行的破绽，天道规则的裂缝，每一次战斗的致命漏洞。\n但系统从不白给。每一次突破，"
     "他的寿元都在加速流逝；禁忌功法能让他走得更快，代价却是要么疯魔、要么速死。\n当他站在天道"
     "规则的裂缝前，终于看清这个世界的真相：所谓天道秩序，不过是一场精心设计的收割游戏。"),
    ("都市·维度契约(真实生成)", "都市", "都市异能",
     "一道维度裂缝撕开便利店的墙壁，绑定了他命运的【超维契约系统】。他获得第一个契约位——"
     "感知系·微观视觉：所有异能者的能量流动无所遁形，五行克制链在他眼中透明如窗。\n完成维度任务、"
     "获取契约碎片、解锁新契约位——火+风=烈焰风暴，金+感知=金属读心，能力叠加没有上限，碾压快感"
     "从不重复。\n但契约的代价在累积：每解锁一个维度，他与现实的共鸣就减弱一分——朋友出事时，他的"
     "第一反应竟是计算最优解，而非感受悲伤。\n维度裂缝正在扩大，异能者与现实的边界正在崩塌；他的"
     "契约系统，究竟是拯救一切的钥匙，还是毁灭的开端？"),
    ("纯爱·共情陷阱(真实生成)", "纯爱", "现代言情",
     "心理咨询师林知简是业内公认的「冷面判官」，擅长用共情穿透一切心理防御——直到她遇见策展人沈棠，"
     "这个人总能精准避开她的每一次专业围猎，用艺术留白制造出她无法解读的盲区。\n一场峰会上的策展招标，"
     "让「理性破防」撞上「感性守护」：她越想看透沈棠，越被对方不动声色的温柔拽入更深的漩涡。\n而沈棠"
     "也第一次在这个外冷内热的女人身上，尝到被「真正看见」的战栗——不是被分析，而是被理解。\n两个都"
     "带着情感创伤的人，在职场博弈与艺术交锋里彼此试探、彼此驯服。最治愈的共情，会不会是敢于为彼此留白？"),
    # Negative control — should lose badly vs real bestsellers.
    ("SLUSH·空泛AI稿(负控)", "玄幻", "升级",
     "这是一个关于成长的故事。主角本以为生活很平凡，却没想到命运的齿轮开始转动，他将何去何从？"
     "一段不平凡的旅程就此展开，让我们拭目以待，敬请期待。"),
    # Sanity — a real bestseller premise; vs OTHER real bestsellers it should land near 0.5.
    ("SANITY·凡人修仙传(真爆款)", "仙侠", "修真",
     "普通山村少年韩立，靠一个神秘小瓶和谨小慎微到极致的性子，踏进尔虞我诈的修真界。没有惊世天资，"
     "他偏要在这个一步踏错就尸骨无存的世界里活到最后，逆天求得那一线长生。"),
]


async def main():
    settings = load_settings()
    print(f"判官=跨家族 DeepSeek(deepseek-v4-flash) | 双盲位置交换 pairwise | 故事质量达标线 win-rate≥{BAR}\n")
    results = []
    async with session_scope() as session:
        judge = make_deepseek_judge(session, settings)
        for label, genre, sub, blurb in CANDIDATES:
            try:
                summary = await run_appeal_arena(
                    candidate_blurb=blurb, genre=genre, sub_genre=sub,
                    judge=judge, min_refs=4, max_refs=5,
                )
            except Exception as exc:
                print(f"  ! {label}: {exc!r}")
                continue
            met = summary.win_rate >= BAR
            results.append((label, summary.win_rate, met))
            outs = " ".join(f"{d['ref']}={d['outcome']}" for d in summary.details)
            print(f"  {label:<28} win-rate={summary.win_rate:.2f} "
                  f"(W{summary.wins}/L{summary.losses}/T{summary.ties}, n={summary.pairs}) "
                  f"{'✅达标' if met else '✗未达标'}\n      vs {outs}")

    print("\n" + "=" * 66)
    designed = [r for r in results if "真实生成" in r[0]]
    passed = sum(1 for _, _, m in designed if m)
    print(f"真实生成新书：{passed}/{len(designed)} 本 win-rate≥{BAR}（与真实爆款同档）")
    for label, wr, met in results:
        print(f"  {('✅' if met else '✗')} {label}: {wr:.2f}")
    print("=" * 66)


if __name__ == "__main__":
    asyncio.run(main())
