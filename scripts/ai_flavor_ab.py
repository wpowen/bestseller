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
    args = parser.parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
