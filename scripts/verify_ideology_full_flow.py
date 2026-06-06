"""End-to-end verification of the FULL ideology flow through the real framework.

Unlike pilot_ideology_framework.py (which only checks prompt propagation), this
actually GENERATES content through the real gateway and verifies the ideology +
grounded mainstream theme land in the OUTPUT, then runs the judge + gate:

  1. derive_ideology_kernel        — real complete_text gateway
  2. _fallback_story_design_kernel — real framework kernel + attach ideology
  3. _volume_plan_prompts → LLM    — generate a real 卷纲 (volume plan)
  4. _outline_prompts → LLM        — generate a real 大纲 (chapter outline)
  5. judge_outline_ideology        — advisory ideology judge on the GENERATED outline
  6. evaluate_ideology_kernel_coherence + audit_ideology_outline_grounding
     — structural gate + does the generated outline ground the kernel's symbols/thesis

Run:  .venv/bin/python scripts/verify_ideology_full_flow.py
Needs .env (LLM keys) + running Postgres (bestseller-db-1, localhost:5432).
"""

# ruff: noqa: E402, RUF001, E501, S112, N812, ANN001, ANN202, I001

from __future__ import annotations

import asyncio
import json
import re

from dotenv import load_dotenv

load_dotenv(".env")

import bestseller.services.planner as P
from bestseller.domain.ideology import ideology_kernel_to_dict
from bestseller.infra.db.models import ProjectModel
from bestseller.infra.db.session import session_scope
from bestseller.services.ideology_coherence_gate import (
    audit_ideology_outline_grounding,
    evaluate_ideology_kernel_coherence,
)
from bestseller.services.ideology_judge import judge_outline_ideology
from bestseller.services.ideology_kernel import (
    derive_ideology_kernel,
    ideology_kernel_health_summary,
)
from bestseller.services.llm import LLMCompletionRequest, complete_text
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
    "themes": ["真相与代价", "守护"],
    "protagonist": {"name": "陆沉", "external_goal": "查清天罚真相、保全边城",
                    "internal_need": "从只想自保到愿为众生付代价"},
    "stakes": {"personal": "寿命与挚亲", "world": "边城存亡与仙门秩序"},
}
WORLD_SPEC = {
    "world_name": "九野", "world_premise": "灵脉滋养万物，上宗以天罚为名周期性收割灵脉。",
    "rules": [{"name": "灵脉律", "description": "灵脉可被收割，收割之地百年一劫。"},
              {"name": "寿契", "description": "以寿命为价可强行换取力量或真相。"}],
}
CAST_SPEC = {"protagonist": {"name": "陆沉", "archetype": "受难者→自立者"},
             "antagonist": {"name": "上宗执律者", "force_type": "institution"},
             "supporting_cast": [{"name": "苏窈", "role": "同门"}]}


def _parse_json(text: str):
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", (text or "").strip(), flags=re.I | re.S).strip()
    for cand in (t, (re.search(r"[\[{].*[\]}]", t, re.S) or [None])[0] if re.search(r"[\[{].*[\]}]", t, re.S) else None):
        if not cand:
            continue
        try:
            return json.loads(cand)
        except Exception:
            continue
    try:
        from json_repair import repair_json
        return repair_json(t, return_objects=True)
    except Exception:
        return None


async def _gen(session, settings, sys_p, user_p, *, label, max_tokens=4000):
    comp = await complete_text(
        session, settings,
        LLMCompletionRequest(
            logical_role="planner", system_prompt=sys_p, user_prompt=user_p,
            fallback_response="[]", prompt_template=f"full_flow_{label}", prompt_version="v1",
            metadata={"verify": "ideology_full_flow", "stage": label},
            max_tokens_override=max_tokens,
        ),
    )
    return comp.content


def _grade(name: str, ok: bool, detail: str = "") -> bool:
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    return ok


async def main() -> None:
    settings = get_settings()
    print(f"=== Ideology FULL-FLOW verification === book={TITLE!r} genre={GENRE}\n")
    results: list[bool] = []

    async with session_scope(settings) as session:
        # 1) derive kernel (real gateway)
        print("[1] derive IdeologyKernel via real complete_text gateway…")
        kernel = await derive_ideology_kernel(
            session, settings, premise=PREMISE, genre=GENRE, book_spec=BOOK_SPEC,
            volumes=VOLUMES, title=TITLE, language="zh",
        )
        s = ideology_kernel_health_summary(kernel)
        print(f"    主主题: {s['thesis_statement']}")
        print(f"    脊柱: {s['primary_motif']} + {s['secondary_motifs']} + 隐藏={s['hidden_motif']} | 四层 {s['layer_count']}/4 | 子题 {s['sub_theme_count']}")
        results.append(_grade("内核结构完整(2副母题+隐藏+≥1子题)",
                              set(s["secondary_roles"]) == {"action", "suspense"} and s["hidden_motif"] and s["sub_theme_count"] >= 1))

        # 2) structural gate
        gate = evaluate_ideology_kernel_coherence(ideology_kernel_to_dict(kernel), volumes=VOLUMES)
        results.append(_grade("结构门禁(advisory)无 critical", gate.critical_count == 0,
                              f"verdict={gate.verdict} coverage={gate.coverage:.2f}"))

        # 3) build StoryDesignKernel + attach ideology
        project = ProjectModel(title=TITLE, slug="chu-gou-cheng", genre=GENRE, sub_genre="",
                               language="zh", target_chapters=400, metadata_json={})
        sdk = P._fallback_story_design_kernel(project, PREMISE, BOOK_SPEC, WORLD_SPEC, CAST_SPEC, category_key=None)
        sdk["ideology_kernel"] = ideology_kernel_to_dict(kernel)
        story_design_kernel_from_dict(sdk)
        project.metadata_json = {"story_design_kernel": sdk}
        results.append(_grade("StoryDesignKernel 校验通过 + 理念已挂载", bool(sdk.get("ideology_kernel"))))

        # 4) GENERATE real 卷纲 (volume plan) through the framework prompt + gateway
        print("[4] generate 卷纲 via real planner prompt + gateway…")
        vp_sys, vp_user = P._volume_plan_prompts(project, BOOK_SPEC, WORLD_SPEC, CAST_SPEC)
        vp_text = await _gen(session, settings, vp_sys, vp_user, label="volume_plan", max_tokens=4000)
        vp = _parse_json(vp_text)
        volume_plan = vp if isinstance(vp, list) else (vp.get("volumes") if isinstance(vp, dict) else None)
        results.append(_grade("卷纲生成为合法 JSON 数组", isinstance(volume_plan, list) and len(volume_plan) >= 1,
                              f"{len(volume_plan) if isinstance(volume_plan, list) else 0} 卷"))
        if not isinstance(volume_plan, list) or not volume_plan:
            volume_plan = [{"volume_number": i, "title": f"第{i}卷", "goal": "推进主线",
                            "chapter_count_target": 50} for i in range(1, 4)]

        # 5) GENERATE real 大纲 (chapter outline) through the framework prompt + gateway
        print("[5] generate 大纲 via real planner prompt + gateway…")
        ol_sys, ol_user = P._outline_prompts(project, BOOK_SPEC, CAST_SPEC, volume_plan)
        ol_text = await _gen(session, settings, ol_sys, ol_user, label="outline", max_tokens=6000)
        ol = _parse_json(ol_text)
        results.append(_grade("大纲生成为合法 JSON", ol is not None, f"{len(ol_text)} chars"))

        # 6) does the GENERATED outline actually express the kernel? (deterministic)
        grounding = audit_ideology_outline_grounding(kernel, ol_text)
        landed = (
            grounding.thesis_keyword_hits > 0
            or grounding.symbol_hits > 0
            or grounding.cost_language_present
        )
        results.append(_grade("生成的大纲落地了理念(符号/主题词/代价语言)", landed,
                              f"符号命中={grounding.symbol_hits}/{grounding.symbol_total} 主题词={grounding.thesis_keyword_hits} 代价语言={grounding.cost_language_present}"))

        # 7) advisory ideology judge on the GENERATED outline (real gateway)
        print("[7] judge the generated 大纲 (advisory ideology judge)…")
        judged = await judge_outline_ideology(
            session, settings, outline_text=ol_text, kernel=kernel, genre=GENRE,
        )
        results.append(_grade("理念判官评出有效分(非 unavailable)",
                              "IDEOLOGY_JUDGE_UNAVAILABLE" not in judged.top_issues and judged.final_score > 0,
                              f"final={judged.final_score}/100 level={judged.level}"))

    passed = sum(results)
    print(f"\n=== FULL FLOW: {passed}/{len(results)} 通过 ===")
    print("链路: 推导内核 → 结构门禁 → 挂载StoryDesignKernel → 生成卷纲 → 生成大纲 → 落地校验 → 理念判官")
    print("（全程经真实 complete_text 网关 + 真实 planner prompt builders + 真实 Postgres）")


if __name__ == "__main__":
    asyncio.run(main())
