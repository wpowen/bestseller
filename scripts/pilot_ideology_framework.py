"""REAL framework pilot for the core-ideology layer (not a standalone re-impl).

Two parts, both using the ACTUAL framework code paths:

  PART A — Real LLM gateway: derive the IdeologyKernel through the framework's
    own ``derive_ideology_kernel`` → ``complete_text`` (circuit breaker, retry,
    audit logging to ``llm_runs``) with a real DB session. Proves the derivation
    runs inside the framework, not via a side-channel.

  PART B — Real downstream propagation: build a real StoryDesignKernel with the
    framework's own ``_fallback_story_design_kernel``, attach the ideology kernel
    exactly as ``_generate_story_design_kernel`` does, then call the REAL planner
    prompt builders for 卷纲 / 大纲 / 细纲 (volume_plan / outline / volume_outline)
    and the worldview. Verifies (1) the ideology is referenced by each downstream
    artifact, and (2) the framework's required novel-writing elements still stand.

Run:  .venv/bin/python scripts/pilot_ideology_framework.py
Needs .env (LLM keys) + the running Postgres (bestseller-db-1, localhost:5432).
"""

# ruff: noqa: E402, RUF001, E501, N812, ANN001, I001

from __future__ import annotations

import asyncio

from dotenv import load_dotenv

load_dotenv(".env")

import bestseller.services.planner as P
from bestseller.domain.ideology import ideology_kernel_to_dict
from bestseller.infra.db.models import ProjectModel
from bestseller.infra.db.session import session_scope
from bestseller.services.ideology_kernel import (
    derive_ideology_kernel,
    fallback_ideology_kernel,
    ideology_kernel_health_summary,
)
from bestseller.services.story_design_kernel import story_design_kernel_from_dict
from bestseller.settings import get_settings

PREMISE = (
    "边城百年一次被「天罚」清洗，幸存少年陆沉拜入仙门后逐渐发现，所谓天罚只是上宗收割灵脉的"
    "周期工程。为救城，他要不断交易寿命换取真相，最终在毁掉仙门与保全城民之间作选择。"
)
GENRE = "仙侠"
TITLE = "刍狗城"
VOLUMES = 8

BOOK_SPEC = {
    "title": TITLE,
    "logline": "天罚是收割，少年用寿命换真相，在毁门与护城之间抉择。",
    "genre": GENRE,
    "themes": ["真相与代价", "天道无情", "守护"],
    "protagonist": {
        "name": "陆沉",
        "external_goal": "查清天罚真相、保全边城",
        "internal_need": "从只想自保到愿为众生付代价",
    },
    "stakes": {"personal": "寿命与挚亲", "world": "边城存亡与仙门秩序"},
}
WORLD_SPEC = {
    "world_name": "九野",
    "world_premise": "灵脉滋养万物，上宗以天罚为名周期性收割灵脉。",
    "rules": [
        {"name": "灵脉律", "description": "灵脉可被收割，收割之地百年一劫。"},
        {"name": "寿契", "description": "以寿命为价可强行换取力量或真相。"},
    ],
}
CAST_SPEC = {
    "protagonist": {"name": "陆沉", "archetype": "受难者→自立者"},
    "antagonist": {"name": "上宗执律者", "force_type": "institution"},
    "supporting_cast": [{"name": "苏窈", "role": "同门"}],
}

# Framework's required novel-writing elements per downstream prompt.
FRAMEWORK_ELEMENTS = {
    "卷纲 volume_plan": ["volume", "JSON"],
    "大纲 outline": ["scene", "JSON"],
    "细纲 volume_outline (full)": ["chapter", "JSON"],
    "细纲 volume_outline (compact)": ["chapter", "JSON"],
}


def _check(label: str, sys_p: str, user_p: str, kernel) -> bool:
    prompt = f"{sys_p}\n{user_p}"
    print(f"\n### {label}  (prompt {len(prompt)} chars)")
    # (1) ideology referenced? — test the actual thesis text (mode-agnostic: works
    # for both the full kernel block and the compact ideology block).
    thesis_present = kernel.thesis_statement in prompt
    sub_present = any(t.proposition in prompt for t in kernel.sub_themes) if kernel.sub_themes else True
    belief_present = ("信念弧" in prompt) or (kernel.belief_arc.final_reconstruction in prompt)
    block = "核心理念内核" if "核心理念内核" in prompt else ("核心理念(紧凑)" if "核心理念(必须贯彻)" in prompt else "无")
    print(f"  理念引用: 主主题在场={thesis_present} | 子题在场={sub_present} | 信念弧在场={belief_present} | 块={block}")
    # (2) framework required elements still present?
    fw = FRAMEWORK_ELEMENTS.get(label, [])
    fw_miss = [e for e in fw if e.lower() not in prompt.lower()]
    print(f"  框架要素: 必需={fw}  缺失={fw_miss or '无'}")
    ok = thesis_present and sub_present and belief_present and not fw_miss
    print(f"  → {'PASS' if ok else 'CHECK'}")
    return ok


async def main() -> None:
    settings = get_settings()
    print(f"=== REAL framework pilot ===\nbook={TITLE!r} genre={GENRE} volumes={VOLUMES}\n")

    # ---- PART A: real LLM gateway through the framework -------------------
    kernel = None
    try:
        async with session_scope(settings) as session:
            print("[A] deriving IdeologyKernel via framework complete_text (real gateway)…")
            kernel = await derive_ideology_kernel(
                session, settings,
                premise=PREMISE, genre=GENRE, book_spec=BOOK_SPEC,
                volumes=VOLUMES, title=TITLE, language="zh",
            )
        summary = ideology_kernel_health_summary(kernel)
        print("[A] derived via real pipeline:")
        print(f"    主主题 : {summary['thesis_statement']}")
        print(f"    子题   : {summary['sub_themes']}")
        print(f"    脊柱   : {summary['primary_motif']} + {summary['secondary_motifs']} + 隐藏={summary['hidden_motif']}")
        print(f"    四层   : {summary['covered_layers']} ({summary['layer_count']}/4)")
    except Exception as e:
        print(f"[A] real gateway unavailable ({type(e).__name__}: {str(e)[:120]});"
              " falling back to deterministic kernel for the propagation test.")
        kernel = story_design_kernel_from_dict  # placeholder; replaced below

    if kernel is None or not hasattr(kernel, "thesis_statement"):
        from bestseller.domain.ideology import ideology_kernel_from_dict
        kernel = ideology_kernel_from_dict(
            fallback_ideology_kernel(premise=PREMISE, book_spec=BOOK_SPEC, volumes=VOLUMES, title=TITLE)
        )

    # ---- PART B: real downstream propagation ------------------------------
    project = ProjectModel(
        title=TITLE, slug="chu-gou-cheng", genre=GENRE, sub_genre="", language="zh",
        target_chapters=400, metadata_json={},
    )
    # Build a REAL StoryDesignKernel via the framework fallback, then attach the
    # ideology kernel exactly as _generate_story_design_kernel does.
    sdk = P._fallback_story_design_kernel(project, PREMISE, BOOK_SPEC, WORLD_SPEC, CAST_SPEC, category_key=None)
    sdk["ideology_kernel"] = ideology_kernel_to_dict(kernel)
    story_design_kernel_from_dict(sdk)  # validate the merged kernel
    project.metadata_json = {"story_design_kernel": sdk}

    print("\n[B] StoryDesignKernel built + ideology attached + validated."
          f" worldview_invariants={len(sdk.get('worldview_kernel', {}).get('invariants', []))}"
          f" plot_tree={len(sdk.get('plot_tree', []))} beats={len(sdk.get('beat_schedule', []))}")

    # Minimal real volume_plan for the outline builders. A 50-chapter volume keeps
    # 细纲 out of compact mode; a 10-chapter volume forces compact mode.
    volume_plan = [
        {"volume_number": i, "volume_title": f"第{i}卷", "title": f"第{i}卷",
         "goal": "推进主线", "obstacle": "上宗阻挠", "volume_climax": "一次代价兑现",
         "conflict_phase": "survival", "chapter_count_target": 50}
        for i in range(1, 4)
    ]
    vol_big = {**volume_plan[0], "chapter_count_target": 50}   # → full ideology block
    vol_small = {**volume_plan[0], "chapter_count_target": 10}  # → compact ideology block

    # Worldview check (it was generated INSIDE the ideology-injected kernel prompt).
    from bestseller.services.story_design_kernel import render_story_design_kernel_prompt_block
    sdk_block = render_story_design_kernel_prompt_block(sdk)
    wv_ok = "Worldview kernel" in sdk_block and "核心理念内核" in sdk_block
    print(f"\n### 世界观 worldview (in StoryDesignKernel block)\n  理念与世界观同块: {wv_ok}")

    # Real downstream prompt builders.
    results = {}
    try:
        results["卷纲"] = _check("卷纲 volume_plan", *P._volume_plan_prompts(project, BOOK_SPEC, WORLD_SPEC, CAST_SPEC), kernel)
    except Exception as e:
        print(f"  卷纲 builder error: {type(e).__name__}: {str(e)[:140]}")
    try:
        results["大纲"] = _check("大纲 outline", *P._outline_prompts(project, BOOK_SPEC, CAST_SPEC, volume_plan), kernel)
    except Exception as e:
        print(f"  大纲 builder error: {type(e).__name__}: {str(e)[:140]}")
    try:
        results["细纲(full)"] = _check("细纲 volume_outline (full)",
               *P._volume_outline_prompts(project, BOOK_SPEC, CAST_SPEC, volume_plan, vol_big), kernel)
    except Exception as e:
        print(f"  细纲(full) builder error: {type(e).__name__}: {str(e)[:140]}")
    try:
        results["细纲(compact)"] = _check("细纲 volume_outline (compact)",
               *P._volume_outline_prompts(project, BOOK_SPEC, CAST_SPEC, volume_plan, vol_small), kernel)
    except Exception as e:
        print(f"  细纲(compact) builder error: {type(e).__name__}: {str(e)[:140]}")

    passed = sum(1 for v in results.values() if v)
    print(f"\n=== pilot done === 下游引用通过 {passed}/{len(results)}"
          f" + 世界观同块={wv_ok} ===")


if __name__ == "__main__":
    asyncio.run(main())
