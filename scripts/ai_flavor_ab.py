#!/usr/bin/env python3
"""A/B harness for the AI-flavor optimisation loop (real model, faithful lever).

The DB-backed single-scene regen path is brittle on a *finished* benchmark
book, so this harness reproduces the production writer call directly:

* **system prompt** = the real ``compile_methodology(PROSE_SCENE, …)`` block
  (the exact place the 去AI味 / 节奏 / 风格 guidance lives) compiled from the
  current configs at the production token budget (3200). Editing the configs
  and re-running is the A/B knob.
* **user prompt** = a faithful scene brief built from the book's real
  ``SceneCardModel`` rows (entry/exit/purpose/beats/sensory/forbidden/hook).
* **model** = the real ``writer`` role (MiniMax-M3).

Output: one ``.md`` per scene under ``tmp/ai_flavor_ab/<label>/`` plus a
manifest, ready for ``scripts/ai_flavor_diagnose.py``.

Usage:
    python scripts/ai_flavor_ab.py --slug shilouyan-bench-v1 \
        --label A_baseline --scenes 1:1 2:1 3:1
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sqlalchemy import select

from bestseller.infra.db.models import (
    ChapterModel,
    ProjectModel,
    SceneCardModel,
)
from bestseller.infra.db.session import create_engine, create_session_factory
from bestseller.services.llm import LLMCompletionRequest, complete_text
from bestseller.services.methodology_compiler import (
    MethodologyStage,
    compile_methodology,
)
from bestseller.settings import load_settings


# The highest-priority去AI味 directive: "AI 写信息，人类写体验". Drafted from
# the editor checklist + the line-level audit of the narration-style baseline.
# Kept concrete (forbidden → rewrite) so the model actually changes behaviour.
_EXPERIENCE_FIRST = """\
【最高写作铁律·体验优先，违反即重写】
这是凌驾一切的第一规则：**不要告诉读者发生了什么，要让读者亲眼看见、亲身体验。**
AI 写"信息"，人写"体验"。下面四条必须逐句遵守：

1. 单一视角沉浸。全程只写本场视角人物**能亲身感知到**的——他看见、听见、摸到、闻到、想到的。
   别人的心理、来历、动机，只能从他观察到的外在(动作/神态/语气/物件)去**推断**，不能由叙述者直接说出。

2. 体验先于信息，禁止旁白交代。每个拍点先落**视角人物当下的感知或动作**，再带出必要信息。
   严禁把来历/前情/机制/背景当独立旁白丢出来。下列写法一出现就算违规，必须改写：
   - 来历交代："X 是…分下来的 / 三年没人用" → 改成人物此刻摸到/掂到/认出它的那一下。
     ✗「方砚是翠屏峰分下来的，三年没人用，边角磕掉了米粒大一块。」
     ✓「他指腹先碰到那道缺口——米粒大，硌手。是这方砚没错，比记忆里更糙了。」
   - 解释机制/前情："这是他惯用的手段：先…再…" / "那是她替人挡符箭的后遗症" → 删掉解释，
     只写人物看见的现象 + 自己的判断闪念，让读者从动作里自己拼出机制。
   - 全知旁白："没人看见她每夜…" / "他不知道的是…" / "一个更大的阴谋即将展开" → 一律删，
     换成视角人物此刻具体的一个动作或一件能看见的物证。
   - **回忆/前情概述同样禁止**。不许用"如今三年过去，他修为停滞、灵根驳杂、连着两次垫底……"
     这种一口气罗列处境的概述。回忆只能是**一个具体的画面或一次具体的动作**，一两笔带过即收回当下。
     ✗「如今三年过去，他修为停滞，灵根驳杂，月评连着两次垫底，殷泱步步紧逼。」
     ✓「他想起上次月评，自己的名字也是这样被念到最后一个。指节在砚背上收紧了些。」

3. 画面感。只用**具体可见**的物、动作、身体反应，让读者能在脑中拼出完整画面。
   不下抽象评价(矮得可怜/强大/神秘/可怕)，不直接命名情绪(震惊/紧张/愤怒)——
   情绪用身体外显写(手抖/咽口水/捏紧/说话变短/盯着不动)，让读者自己得出结论。

4. 禁结论先行、禁否定排比。不要先抛判断再补过程；过程在前，结论留给读者。
   禁止"不是…而是…""不是…不是…是…""不是…，是…""与其…不如…""既…又…"这类句式——
   包括描写物件时的"不是黑墨，是暗金的浓墨"，直接写"墨里浮着一丝暗金"。

5. 镜头跟着视角实时推进，先动作、后结果。结果是读者跟着人物一起发现的 payoff，
   不是预先 announce 的标签。凡"先报结果/反应/含义，再回头补动作或解释"的句子，一律倒过来：
   - ✗「砚心有什么动了一下。是一缕暗金的线。」(先报异动再说明)
     ✓「墨面平得像镜子，映着满堂的脸。镜面忽然裂开一道缝——一根线，比发丝还细，金的，从墨底钻上来。」
   - 禁群体反应贴标签:"倒抽一口冷气""死一般静""鸦雀无声" → 改成某一个具体的人一个具体动作。
   - 禁情绪贴标签:"笑僵在脸上""脸一寸寸白" → 改成她做了什么/停了什么(要落的笔停在半空，再没落下)。
   - 禁旁白解读:"她知道那声音意味着什么""等着看他走上那条线""按常理早该…" → 删，只留可见现象。

6. 用电影镜头语言写场景，让读者脑中能放出画面：
   - 反应镜头：主角的威慑/强大/震慑，**不直接写主角**，切到配角或在场 NPC、物件的反应来侧写。
     ✗「殷泱认出了养墨，脸色煞白。」
     ✓「点名的执事笔尖一滞，一滴朱砂坠在册页上，洇开。他没去擦。」
   - 建立镜头→推近：进新场景先给一个空间广角锚点(高台、人群、案上九方砚)，再推到主角当下的手/眼。
   - 特写/插入：关键物件或动作给放大细节(砚底那道缺口、封泥上没干的朱、墨面裂开的缝)，用细节顶张力。
   - 揭示靠调度：信息靠人物走位、视线转移、物件位置推到读者眼前，不靠叙述者开口宣布。

每写一句先自检两遍：①这句是"报信息"还是"让读者看见/体验"？②这是"先报结果再解释"还是"镜头跟着动作走、结果自己落地"？不对就重写。
"""


def _fmt(value: object) -> str:
    if isinstance(value, dict):
        return value.get("summary") or "; ".join(f"{k}:{v}" for k, v in value.items())
    if isinstance(value, (list, tuple)):
        return "；".join(str(v) for v in value)
    return str(value or "")


# Stress briefs: scene situations that most strongly *tempt* the reflective
# AI-flavor patterns (顿悟宣告 / 不是X而是Y / 抽象结论 / 裸对白标签 / 微动作模板).
# Generic xianxia situations authored here — not copied from any book.
_STRESS_BRIEFS: tuple[tuple[str, str], ...] = (
    (
        "reflect_betrayal",
        "【场景】少年得知一直护着他的师兄，其实是把他卖给仇家的人。此刻师兄已走，"
        "只剩他一个人站在空院里，手里还攥着师兄方才塞给他的半块玉佩。"
        "写他从震惊到想通这件事的全过程。约900字。只输出正文，紧贴他单一视角，"
        "不要由叙述者替他下结论，不要用『他突然明白』『这不是…而是…』这类宣告句。",
    ),
    (
        "terse_confront",
        "【场景】债主带人堵在门口逼债，少年挡在病重的妹妹床前。两人一句顶一句地交锋，"
        "气氛紧绷，话都很短。约900字。只输出正文，对白要有动作与停顿穿插，"
        "不要连续用『X说』『X道』这类裸标签收尾。",
    ),
    (
        "emotion_climax",
        "【场景】少年拼尽全力也没能救回妹妹，她在他怀里咽了气。写这一刻他的反应。"
        "约900字。只输出正文，落到具体可见的动作与物件，"
        "不要堆『瞳孔一缩』『心头一紧』这类模板化身体反应，也不要直接命名情绪。",
    ),
)


def _build_brief(scene: SceneCardModel, *, target: int) -> str:
    parts: list[str] = ["【场景任务】只输出小说正文，不要任何解释、标题或方括号标注。"]
    if scene.title:
        parts.append(f"标题线索：{scene.title}")
    if scene.participants:
        parts.append(f"在场人物：{_fmt(scene.participants)}")
    if scene.time_label:
        parts.append(f"时间/地点：{scene.time_label}")
    if scene.purpose:
        parts.append(f"本场目的：{_fmt(scene.purpose)}")
    if scene.entry_state:
        parts.append(f"开场状态：{_fmt(scene.entry_state)}")
    if scene.exit_state:
        parts.append(f"收场必须到达：{_fmt(scene.exit_state)}")
    if scene.key_dialogue_beats:
        parts.append(f"关键对白节拍：{_fmt(scene.key_dialogue_beats)}")
    if scene.sensory_anchors:
        parts.append(f"可用感官锚点：{_fmt(scene.sensory_anchors)}")
    if scene.forbidden_actions:
        parts.append(f"禁止：{_fmt(scene.forbidden_actions)}")
    if scene.hook_requirement:
        parts.append(f"结尾钩子：{_fmt(scene.hook_requirement)}")
    parts.append(f"目标字数：约 {target} 字。紧贴单一视角，长短句交错，写完整场景。")
    return "\n".join(parts)


async def _run(args: argparse.Namespace) -> int:
    settings = load_settings()
    engine = create_engine(settings)
    session_factory = create_session_factory(engine=engine)
    out_dir = Path("tmp/ai_flavor_ab") / args.label
    out_dir.mkdir(parents=True, exist_ok=True)

    # Phase 1: read all scene briefs into memory (one session, then closed).
    # Each entry: (name, brief, target). For stress mode, name is the brief id;
    # for DB mode, name is "chNNN-sNN".
    briefs: list[tuple[str, str, int]] = []
    project_id = None
    pack = None
    async with session_factory() as session:
        project = await session.scalar(
            select(ProjectModel).where(ProjectModel.slug == args.slug)
        )
        if project is None:
            raise SystemExit(f"project not found: {args.slug}")
        project_id = project.id
        pack = (project.metadata_json or {}).get("prompt_pack_key")
        if args.stress:
            for name, brief in _STRESS_BRIEFS:
                briefs.append((name, brief, 900))
        for pair in (args.scenes or []):
            cs, _, ss = pair.partition(":")
            chapter_no = int(cs)
            scene_no = int(ss or "1")
            chapter = await session.scalar(
                select(ChapterModel).where(
                    ChapterModel.project_id == project.id,
                    ChapterModel.chapter_number == chapter_no,
                )
            )
            scene = await session.scalar(
                select(SceneCardModel).where(
                    SceneCardModel.chapter_id == chapter.id,
                    SceneCardModel.scene_number == scene_no,
                )
            ) if chapter is not None else None
            if scene is None:
                print(f"ch{chapter_no}-s{scene_no}: scene card not found, skip")
                continue
            target = scene.target_word_count or 1600
            briefs.append(
                (f"ch{chapter_no:03d}-s{scene_no:02d}", _build_brief(scene, target=target), target)
            )

    # Phase 2: generate each scene with a fresh session (avoids cross-call
    # greenlet/pre-ping issues after the LLM-run logging writes).
    for name, user_prompt, target in briefs:
        compiled = compile_methodology(
            stage=MethodologyStage.PROSE_SCENE,
            prompt_pack_key=pack,
            language="zh-CN",
            chapter_no=1,
            token_budget=3200,
            include_writing_methodology_bridge=False,
        )
        if args.naked:
            system_prompt = "你是顶尖中文网络小说写手。只输出正文，不要解释。"
        else:
            system_prompt = (
                "你是顶尖中文网络小说写手。严格遵守下列写作方法论与禁忌，只输出正文。\n\n"
                + compiled.text
            )
        if args.experience_first:
            # The dominant去AI味 lever: write the *experience*, not the
            # *information*. Placed FIRST and marked as overriding so it wins
            # against any narration-style guidance below it.
            system_prompt = _EXPERIENCE_FIRST + "\n\n" + system_prompt
        t0 = time.monotonic()
        req = LLMCompletionRequest(
            logical_role="writer",
            model_tier="strong",
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            fallback_response="（生成失败）",
            prompt_template="scene_writer",
            prompt_version="ab-harness",
            project_id=project_id,
            max_tokens_override=max(2048, int(target * 2.2)),
        )
        async with session_factory() as session:
            result = await complete_text(session, settings, req)
            await session.rollback()
        elapsed = time.monotonic() - t0
        content = (result.content or "").strip()
        path = out_dir / f"{name}.md"
        path.write_text(content, encoding="utf-8")
        cjk = sum(1 for c in content if "一" <= c <= "鿿")
        print(
            f"{name}: {cjk} 字, sys≈{compiled.estimated_tokens}tok, "
            f"{elapsed:.0f}s, sources={len(compiled.used_sources)} -> {path}"
        )

    await engine.dispose()
    print(f"\nwrote scenes under {out_dir}")
    print(f"diagnose: python scripts/ai_flavor_diagnose.py '{out_dir}/*.md'")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--slug", required=True)
    parser.add_argument("--label", required=True)
    parser.add_argument("--scenes", nargs="*", default=[], help="chapter:scene pairs")
    parser.add_argument("--stress", action="store_true", help="use built-in pattern-eliciting briefs")
    parser.add_argument("--naked", action="store_true", help="omit methodology block (bare model baseline)")
    parser.add_argument("--experience-first", action="store_true", help="prepend the 体验优先 POV-immersion directive")
    args = parser.parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
