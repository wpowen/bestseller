"""End-to-end verification: does the PROSE prompt carry each chapter's
story/content/rules/constraints/style — and (after the fix) the selected story
enhancers?

This drives the REAL runtime path on REAL DB-persisted data:
  get_project_by_slug → load chapter/scene → build_scene_writer_context_from_models
  → build_scene_draft_prompts  (the actual prose-prompt assembly)

How it stays cheap and side-effect-free:
  * ``complete_text`` is stubbed everywhere → 0 LLM tokens, deterministic, offline.
    (Only the prewrite/beat-planner helper text degrades to its built-in fallback;
    the dimensions under test come from DB + metadata + deterministic renderers.)
  * ``build_scene_draft_prompts`` is wrapped to capture its real (system, user)
    output and then abort via a ``BaseException`` sentinel — so the writer LLM call
    is never reached and no draft row is written.
  * ``session_scope`` only commits on a clean exit; the sentinel is not an
    ``Exception`` so the commit is skipped → the real book's rows are untouched.

Two variants are compared on the SAME real chapter:
  A. as-is in DB (no story_enhancers selected)  → baseline
  B. story_enhancers turned on + this chapter's brainhole/effect contract synced
     through the REAL persistence fix             → proves the new wiring

Run:  .venv/bin/python scripts/verify_story_enhancer_prose_e2e.py
"""

from __future__ import annotations

import asyncio
import sys
from types import SimpleNamespace

from sqlalchemy import select

import bestseller.services.drafts as drafts_mod
import bestseller.services.llm as llm_mod
from bestseller.infra.db.models import ChapterModel
from bestseller.infra.db.session import session_scope
from bestseller.services.drafts import generate_scene_draft
from bestseller.services.llm import LLMCompletionResult
from bestseller.services.projects import get_project_by_slug
from bestseller.services.workflows import _sync_chapter_causality_metadata
from bestseller.settings import load_settings

# Requires a live DB (docker compose db) + an already-generated book. Override
# the target via argv: verify_story_enhancer_prose_e2e.py <slug> <chapter> <scene>
SLUG = sys.argv[1] if len(sys.argv) > 1 else "zhaoshen-hr-v3-1781180702"
CHAPTER_NO = int(sys.argv[2]) if len(sys.argv) > 2 else 1
SCENE_NO = int(sys.argv[3]) if len(sys.argv) > 3 else 1

_REAL_BUILD = drafts_mod.build_scene_draft_prompts
_HOLDER: dict[str, object] = {}


class _PromptCaptured(BaseException):
    """Sentinel — NOT an Exception, so internal ``except Exception`` and
    ``session_scope``'s commit-on-exit both let it pass through untouched."""


def _wrap_build(*args, **kwargs):
    system_prompt, user_prompt = _REAL_BUILD(*args, **kwargs)
    _HOLDER["system"] = system_prompt
    _HOLDER["user"] = user_prompt
    _HOLDER["project"] = args[0]
    _HOLDER["chapter"] = args[1]
    _HOLDER["scene"] = args[2]
    raise _PromptCaptured


async def _fake_complete_text(session, settings, request):  # noqa: ANN001
    return LLMCompletionResult(
        content=getattr(request, "fallback_response", None) or "（mock）",
        provider="fake",
        model_name="fake",
    )


def _install_patches() -> None:
    real = llm_mod.complete_text
    for module in list(sys.modules.values()):
        try:
            if getattr(module, "complete_text", None) is real:
                module.complete_text = _fake_complete_text
        except Exception:
            pass
    llm_mod.complete_text = _fake_complete_text
    drafts_mod.build_scene_draft_prompts = _wrap_build


async def _capture(mutate=None) -> dict[str, object]:
    _HOLDER.clear()
    settings = load_settings()
    try:
        async with session_scope(settings) as session:
            if mutate is not None:
                await mutate(session)
            await generate_scene_draft(
                session, SLUG, CHAPTER_NO, SCENE_NO, settings=settings
            )
    except _PromptCaptured:
        pass
    return dict(_HOLDER)


async def _enable_enhancers(session) -> None:  # noqa: ANN001
    """Turn on book-level enhancers and sync this chapter's cashed contract
    through the REAL persistence path — exactly what a real run does after the
    planner emits the chapter outline."""
    project = await get_project_by_slug(session, SLUG)
    meta = dict(project.metadata_json or {})
    meta["story_enhancers"] = {
        "brainhole": True,
        "concept_lab": False,
        "creativity_direction": None,
        "effect_skills": ["comedy_engine", "hype_satisfaction_engine"],
    }
    project.metadata_json = meta

    chapter = await session.scalar(
        select(ChapterModel).where(
            ChapterModel.project_id == project.id,
            ChapterModel.chapter_number == CHAPTER_NO,
        )
    )
    outline = SimpleNamespace(
        brainhole_contract={
            "one_sentence_sell": "哮天犬递交第301次辞职信，逼主角用现代HR流程把神兽留下",
            "modern_system": "现代企业离职/挽留流程（OA审批、离职面谈、竞业协议）",
            "contrast_mechanism": "神兽的神话威严 撞上 打工人的离职拉扯",
            "visible_comedy": "哮天犬把辞职信写成驱邪符，HR系统识别成连续病假条",
            "plot_consequence": "主角顺藤摸瓜发现神仙离职潮背后有人在挖天庭墙角",
        },
        selected_effect_skills={
            "primary": "comedy_engine",
            "secondary": "hype_satisfaction_engine",
            "expected_contracts": {
                "comic_effect_contract": "神仙用现代规则报错引发连锁笑点（辞职信＝驱邪符）",
                "hype_satisfaction_contract": "主角三句KPI话术把哮天犬当场怼到撤回辞呈",
            },
        },
    )
    _sync_chapter_causality_metadata(chapter, outline, None)


# ── dimension detection ───────────────────────────────────────────────────────


def _dimensions(captured: dict[str, object]) -> dict[str, tuple[bool, str]]:
    system_prompt = str(captured.get("system", ""))
    user_prompt = str(captured.get("user", ""))
    full = system_prompt + "\n" + user_prompt
    chapter = captured.get("chapter")
    scene = captured.get("scene")
    title = str(getattr(chapter, "title", "") or "")
    goal = str(getattr(chapter, "chapter_goal", "") or "")
    purpose = getattr(scene, "purpose", None) or {}
    purpose_story = str(purpose.get("story", "")) if isinstance(purpose, dict) else ""

    def has(*needles: str) -> tuple[bool, str]:
        for n in needles:
            if n and n in full:
                return True, n
        return False, needles[0] if needles else ""

    return {
        "故事/内容 (本章在讲什么)": has(
            title, goal[:12] if goal else "", purpose_story[:12] if purpose_story else "",
            f"Chapter {CHAPTER_NO}",
        ),
        "规则 (世界观/设定/圣经)": has("故事圣经", "世界观", "设定", "圣经", "canon", "世界规则"),
        "限定条件 (硬指标/字数/守则)": has(
            "字数", "硬指标", "上榜", "自检", "禁止", "硬约束", "守则", "ranking"
        ),
        "风格 (题材/流派/语气)": has("主攻", "流派", "题材", "tone", "pov", "视角", "文风"),
        "故事增强 (书级合同)": has("故事增强", "基调锚点", "故事效果"),
        "本章增强兑现点 (脑洞落点)": has(
            "本章已规划的脑洞", "本章主推的故事效果", "驱邪符", "第301次辞职", "comic_effect_contract"
        ),
    }


def _print_report(label: str, captured: dict[str, object]) -> dict[str, bool]:
    dims = _dimensions(captured)
    user_len = len(str(captured.get("user", "")))
    sys_len = len(str(captured.get("system", "")))
    print(f"\n{'=' * 72}\n变体 {label}  (system {sys_len} 字 / user {user_len} 字)\n{'=' * 72}")
    result = {}
    for name, (ok, hit) in dims.items():
        mark = "✅" if ok else "—"
        detail = f"  ← 命中: 「{hit}」" if ok else ""
        print(f"  {mark}  {name}{detail}")
        result[name] = ok
    return result


def _extract_enhancer_section(captured: dict[str, object]) -> str:
    full = str(captured.get("user", ""))
    idx = full.find("故事增强")
    if idx < 0:
        return "(无故事增强块)"
    end = full.find("\n\n\n", idx)
    snippet = full[idx : (end if end > 0 else idx + 900)]
    return snippet.strip()


async def main() -> int:
    _install_patches()

    print("驱动真实运行时：generate_scene_draft → build_scene_writer_context_from_models")
    print(f"底料：真实已生成书 {SLUG} 第{CHAPTER_NO}章 第{SCENE_NO}场景（喜剧/脑洞题材）")
    print("（complete_text 全打桩=0 token；写手调用前用哨兵截获真实 prompt；不写库）")

    variant_a = await _capture()
    variant_b = await _capture(mutate=_enable_enhancers)

    if not variant_a.get("user") or not variant_b.get("user"):
        print("\n❌ 未能捕获 prompt（generate_scene_draft 未走到 build_scene_draft_prompts）")
        return 2

    res_a = _print_report("A：基线（未勾选增强）", variant_a)
    res_b = _print_report("B：勾选增强 + 本章兑现点已同步", variant_b)

    print(f"\n{'=' * 72}\n本章增强兑现点（变体 B 实际注入写手 prompt 的片段）\n{'=' * 72}")
    print(_extract_enhancer_section(variant_b)[:1100])

    # 保存全量供人工核查
    for label, cap in (("A_baseline", variant_a), ("B_enhanced", variant_b)):
        path = f"/tmp/prose_prompt_{label}.txt"
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(f"=== SYSTEM ===\n{cap.get('system','')}\n\n=== USER ===\n{cap.get('user','')}")
        print(f"\n[full prompt saved] {path}")

    # ── 验收判定 ──
    print(f"\n{'=' * 72}\n判定\n{'=' * 72}")
    base_dims = ["故事/内容 (本章在讲什么)", "规则 (世界观/设定/圣经)",
                 "限定条件 (硬指标/字数/守则)", "风格 (题材/流派/语气)"]
    base_ok = all(res_a.get(d) for d in base_dims)
    enh_off_in_a = not res_a.get("故事增强 (书级合同)")
    enh_on_in_b = res_b.get("故事增强 (书级合同)") and res_b.get("本章增强兑现点 (脑洞落点)")

    print(f"  [基础四维 故事/规则/限定/风格 在两变体都到位] {'✅' if base_ok else '❌'}")
    print(f"  [基线A 不含故事增强（修复前的真实状态）]        {'✅' if enh_off_in_a else '❌'}")
    print(f"  [变体B 含书级合同 + 本章兑现点（修复后）]        {'✅' if enh_on_in_b else '❌'}")

    ok = base_ok and enh_off_in_a and enh_on_in_b
    print(f"\n{'🟢 端到端验证 PASS' if ok else '🔴 端到端验证 FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
