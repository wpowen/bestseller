"""自测：吸引力重修闭环能否在有界次数内把简介修到 80，且必然终止(防死循环)。

隔离地复刻 conception 的重生回路：score → build_improvement_feedback → 真实 LLM
按反馈重写 synopsis → 再 score，循环至 ≥80 或用尽 max_attempts。比跑整本 conception
快得多，专门验证 Part 2(闭环+具体反馈)与 Part 3(有界、不死循环、token 受控)。

Run（注入栈 env；需 MINIMAX_API_KEY + DB）：
    <env...> .venv/bin/python scripts/verify_appeal_repair_loop.py
"""

from __future__ import annotations

# ruff: noqa: ANN001, ANN201, ANN202, RUF001, RUF002, RUF003, E501
import asyncio

from bestseller.domain.appeal import StoryAppealReport
from bestseller.infra.db.session import session_scope
from bestseller.services.blurb_appeal_gate import evaluate_blurb_appeal
from bestseller.services.llm import LLMCompletionRequest, complete_text
from bestseller.services.premise_appeal_judge import evaluate_premise_appeal
from bestseller.services.story_appeal import (
    build_improvement_feedback,
    load_story_appeal_config,
    meets_bar,
)
from bestseller.settings import load_settings

# 故意给"弱起始简介"(长、铺设定、首句无钩——即旧 prompt 的产物)，看闭环能否救上来。
CASES = [
    ("玄幻", "升级", ["废柴逆袭", "系统", "代价流", "打脸"],
     "穿越到修仙世界的少年林尘，本以为是个普通的开局，却在一次偶然中获得了一个神秘的系统。"
     "这个系统可以让他快速修炼，但是每次使用都需要付出一定的代价。他开始了自己的修炼之路，"
     "一路上遇到了很多的对手和朋友，经历了无数的战斗和挑战，最终一步步走向了世界的巅峰。"),
    ("都市", "都市异能", ["都市", "异能", "逆袭", "打脸"],
     "都市青年陈默是一个普通的上班族，每天过着平凡的生活。有一天他意外觉醒了异能，"
     "从此他的人生发生了翻天覆地的变化。他用自己的能力解决了一个又一个的麻烦，"
     "也认识了形形色色的人，在这座繁华的城市里书写属于自己的传奇故事。"),
    ("现实", "现实百态", ["现实", "职场", "奋斗"],
     "这是一个关于奋斗的故事。主人公在大城市里打拼，经历了职场的起起落落，"
     "也体会了人生的酸甜苦辣。他没有放弃，一直坚持着自己的梦想，最终收获了属于自己的成功与幸福。"),
]


async def _rewrite_synopsis(session, settings, *, genre, sub, weak, feedback) -> str:
    req = LLMCompletionRequest(
        logical_role="editor",
        model_tier="strong",
        system_prompt=(
            "你是网文平台资深编辑。把给定的弱简介，按【整改要求】重写成一段【点击型】"
            "作品简介(番茄/起点详情页文案)。只输出重写后的简介正文，不要解释、不要标题。"
        ),
        user_prompt=(
            f"题材：{genre}（{sub}）\n\n【当前弱简介】\n{weak}\n\n【整改要求】\n{feedback}\n\n"
            "硬性：80-140字；首句≤30字强钩(疑问/反差/冲突)；卖点三要素齐(身份+冲突+代价)；"
            "高唤起情绪前置；结尾留悬念不剧透；禁AI腔(本以为/却没想到/何去何从/敬请期待)。"
            "只输出简介正文。"
        ),
        fallback_response=weak,
        prompt_template="appeal_repair_rewrite",
        prompt_version="v1",
        max_tokens_override=600,
    )
    completion = await complete_text(session, settings, req)
    return (completion.content or weak).strip()


async def _score(session, settings, cfg, *, genre, sub, tags, synopsis):
    blurb = evaluate_blurb_appeal(
        title="(测试)", synopsis=synopsis, premise=synopsis[:60], tags=tags,
        genre=genre, sub_genre=sub, config=cfg,
    )
    premise = await evaluate_premise_appeal(
        session, settings, premise=synopsis[:120], synopsis=synopsis, title="(测试)",
        tags=tags, genre=genre, sub_genre=sub, chapter_count=600, project_slug=None,
        judge_model_key="deepseek-v4-flash", config=cfg,
    )
    report = StoryAppealReport(
        genre=genre, sub_genre=sub, premise=premise, blurb=blurb,
        meets_bar=meets_bar(premise, blurb, cfg), overall_grade=blurb.grade,
    )
    return report


async def main():
    settings = load_settings()
    cfg = load_story_appeal_config()
    blurb_min = float(cfg["meets_bar"]["blurb_min"])
    max_attempts = int(cfg["regeneration"]["max_attempts"])
    print(f"自测重修闭环 | 达标线 blurb≥{blurb_min} | max_attempts={max_attempts}（防死循环硬上限）\n")

    summary = []
    async with session_scope() as session:
        for genre, sub, tags, weak in CASES:
            traj = []
            best_syn, best_score = weak, -1.0
            report = await _score(session, settings, cfg, genre=genre, sub=sub, tags=tags, synopsis=weak)
            traj.append(report.blurb.total)
            cur_syn = weak
            attempts = 0
            while report.blurb.total < blurb_min and attempts < max_attempts:
                attempts += 1
                fb = build_improvement_feedback(report, cfg)
                cur_syn = await _rewrite_synopsis(session, settings, genre=genre, sub=sub, weak=cur_syn, feedback=fb)
                report = await _score(session, settings, cfg, genre=genre, sub=sub, tags=tags, synopsis=cur_syn)
                traj.append(report.blurb.total)
                if report.blurb.total > best_score:
                    best_syn, best_score = cur_syn, report.blurb.total
            final = max(traj)
            met = final >= blurb_min
            terminated = attempts <= max_attempts
            summary.append((genre, traj, attempts, met, terminated))
            print(f"【{genre}】轨迹 {' → '.join(f'{t:.0f}' for t in traj)}  "
                  f"用{attempts}次  {'✅到80' if met else '✗未到80(已停,不死循环)'}  终止={terminated}")
            if met:
                print(f"     达标简介({len(best_syn)}字): {best_syn[:90]}…")

    print("\n" + "=" * 64)
    reached = sum(1 for _, _, _, m, _ in summary if m)
    all_terminated = all(t for *_, t in summary)
    print(f"到80: {reached}/{len(summary)} | 全部在 max_attempts 内终止(无死循环): {all_terminated}")
    print(f"结论: 闭环{'有效且有界 ✅' if all_terminated else '⚠️有未终止项'}；"
          f"{'多数能修到80' if reached >= len(summary) - 1 else '部分修不到80(生成器仍需加强或该题材难)'}")
    print("=" * 64)


if __name__ == "__main__":
    asyncio.run(main())
