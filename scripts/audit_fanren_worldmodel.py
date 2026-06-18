"""Stress-test the world-model engine against 《凡人修仙传》 as a rigor ground-truth.

凡人 is the hardest fixture because its rigor rests on exactly the things a naive
engine gets wrong:
  * capability is TIER-GATED (only 筑基+ fly; mortals walk) — NOT "everyone flies"
  * costs/lifespans are a QUANTITATIVE ladder (境界→寿元/实力倍率)
  * two societies COEXIST (世俗界 under a hidden 修仙界)
  * an event ripples across MANY dimensions at once

This script (1) runs a REAL LLM derivation of 凡人's world via the planner model
(direct litellm, no DB), then (2) audits each architectural stage against the
ground truth and prints a per-stage BUG report.

Run:  .venv/bin/python scripts/audit_fanren_worldmodel.py
"""

# ruff: noqa: E501, ANN201, ANN202, ANN001

from __future__ import annotations

import json
import os

from dotenv import load_dotenv

from bestseller.domain.world_model import world_model_from_dict, world_model_health_summary
from bestseller.services.world_dimensions import select_baseline
from bestseller.services.world_law_consistency_gate import (
    _parse_judge_violations,
    build_world_law_judge_prompts,
    check_world_law_consistency_gate,
)
from bestseller.services.world_model_deriver import (
    build_world_model_system_prompt,
    build_world_model_user_prompt,
    extract_axioms,
    fallback_world_model,
    parse_world_model,
)
from bestseller.services.world_ripple import compute_state_ripples

load_dotenv()

FANREN_PREMISE = (
    "《凡人修仙传》设定:山村少年韩立资质平庸,机缘下踏入修仙界。这是一个弱肉强食的修真世界——"
    "凡人占绝大多数,只有拥有灵根者才能修炼;修士按境界(炼气、筑基、结丹、元婴、化神……)划分实力,"
    "境界越高寿元越长、实力呈碾压式差距(高一大境界可碾压低境界);修炼依赖天地灵气,灵石是硬通货;"
    "法宝、丹药、符箓可由修士炼制;修仙界隐于世俗王朝之上,凡人对其几乎一无所知。"
    "御器飞行、传送阵等只有到一定境界才能使用。韩立作为低灵根散修,靠谨慎、丹药与傀儡秘术在尔虞我诈中求存。"
)

# Ground-truth: dimension -> concepts a rigorous 凡人 derivation MUST capture.
GROUND_TRUTH = {
    "value_and_currency": ["灵石"],
    "class_and_stratification": ["灵根", "境界"],
    "violence_and_security": ["碾压", "灭杀", "暴力", "无保障", "最强", "镇压"],
    "life_death_and_time": ["寿元", "境界"],
    "power_and_institutions": ["宗门", "家族"],
    "knowledge_and_transmission": ["功法", "师承", "传承"],
    "mobility_and_transport": ["御器", "飞行", "传送"],
}


def call_llm(system_prompt: str, user_prompt: str) -> str | None:
    """Direct planner-model call via litellm (bypasses DB). Returns content or None."""

    model = os.environ.get("BESTSELLER__LLM__PLANNER__MODEL")
    api_base = os.environ.get("BESTSELLER__LLM__PLANNER__API_BASE")
    key_env = os.environ.get("BESTSELLER__LLM__PLANNER__API_KEY_ENV", "")
    api_key = os.environ.get(key_env, "")
    if not (model and api_base and api_key):
        print("  [!] planner LLM env incomplete — skipping real call")
        return None
    try:
        import litellm

        resp = litellm.completion(
            model=model,
            api_base=api_base,
            api_key=api_key,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.7,
            max_tokens=6000,
            timeout=180,
        )
        return resp.choices[0].message.content
    except Exception as exc:
        print(f"  [!] LLM call failed: {exc}")
        return None


def make_llm_judge(laws):
    """A sync LLM judge (direct litellm) for the audit's gate-recall test."""

    def _judge(text, active_laws):
        sysp, usr = build_world_law_judge_prompts(text, active_laws)
        content = call_llm(sysp, usr)
        return _parse_judge_violations(content or '{"violations":[]}', active_laws)

    return _judge


def banner(title: str) -> None:
    print("\n" + "=" * 72 + f"\n{title}\n" + "=" * 72)


def derive() -> tuple[object, bool]:
    """Real LLM derivation of 凡人's world. Returns (world_model, used_llm)."""

    banner("STAGE 0 · 真 LLM 推演《凡人》世界(planner 模型,直连)")
    sys_p = build_world_model_system_prompt(language="zh")
    usr_p = build_world_model_user_prompt(premise=FANREN_PREMISE, genre="仙侠", language="zh")
    content = call_llm(sys_p, usr_p)
    if content:
        model = parse_world_model(content, premise=FANREN_PREMISE, genre="仙侠")
        print("  [✓] 真推演成功")
        used_llm = True
    else:
        print("  [i] 回退到确定性 fallback(仅用于演示离线环节审计)")
        model = world_model_from_dict(fallback_world_model(premise=FANREN_PREMISE, genre="仙侠"))
        used_llm = False
    print(json.dumps(world_model_health_summary(model), ensure_ascii=False, indent=2))
    print("\n  推出的世界规律(dimension | delta | enforcement):")
    for law in model.world_laws:
        print(f"   - [{law.dimension}] {law.delta[:46]} || {law.enforcement[:60]}")
    return model, used_llm


def audit_axioms() -> None:
    banner("环节1 · 公理提取(离线启发式 vs 真公理)")
    ax = extract_axioms(FANREN_PREMISE)
    print("  extract_axioms 离线产出:")
    for a in ax:
        print(f"   - {a}")
    hollow = all(("灵根" not in a and "境界" not in a and "灵气" not in a) for a in ax)
    print(f"\n  [{'BUG' if hollow else 'OK'}] 离线公理是否抓到核心机制(灵根/境界/灵气): {'否——只是按标点切句,完全依赖 LLM' if hollow else '是'}")


def audit_baseline(model) -> None:
    banner("环节2 · 基线底座(单一 vs 双层社会)")
    key, why = select_baseline(genre="仙侠", premise=FANREN_PREMISE)
    print(f"  select_baseline -> {key} ({why})")
    base = (model.baseline or "")
    dual = ("世俗" in base or "凡人" in base) and ("修" in base or "仙" in base)
    print(f"  推演 baseline 字段: {base!r}")
    print(f"  [{'OK' if dual else 'BUG'}] 是否表达『世俗界 + 修仙界』双层共存: "
          f"{'是' if dual else '否——baseline 是单一字符串,无法表达两层社会并存(凡人的立身之本)'}")


def audit_coverage(model) -> None:
    banner("环节3 · 维度差分覆盖(对照《凡人》ground-truth)")
    covered = {law.dimension: f"{law.delta} {law.enforcement}" for law in model.world_laws}
    misses = []
    for dim, kws in GROUND_TRUTH.items():
        text = covered.get(dim, "")
        hit = any(k in text for k in kws)
        status = "OK " if hit else "BUG"
        if not hit:
            misses.append(dim)
        print(f"  [{status}] {dim}: 期望含{kws} -> {'命中' if hit else ('未覆盖该维度' if dim not in covered else '覆盖了但没抓到关键概念')}")
    print(f"\n  覆盖缺口: {misses or '无'}")


def audit_capability_gating(model) -> None:
    banner("环节4 · 能力门控(《凡人》核心:不是人人会飞)")
    # Inspect the mobility law: does it GATE flight by realm, or universalize it?
    mob = next((law for law in model.world_laws if law.dimension == "mobility_and_transport"), None)
    if mob is None:
        print("  [BUG] 未推出 mobility 规律,无法判断门控")
        return
    txt = f"{mob.delta} {mob.enforcement}"
    print(f"  mobility enforcement: {mob.enforcement}")
    gated = any(
        k in txt
        for k in ["境界", "筑基", "以上", "达到", "仅", "只有", "凡人无法", "门槛",
                  "高阶", "特权", "炼气期", "低阶", "低境界", "凡人交通无", "凡人无法"]
    )
    universal_default = ("默认" in txt and "飞行" in txt) and not gated
    print(f"  [{'OK' if gated else 'BUG'}] 飞行是否按境界门控: "
          f"{'是' if gated else '否——把飞行当作世界默认能力,会逼着凡人/低境界也飞(正是“人人会飞”陷阱)'}")
    # Demonstrate the gate's false-positive risk on faithful 凡人 prose.
    print("\n  ▶ 对抗用例:凡人/炼气期角色步行赶路(《凡人》里完全正确)")
    prose_walk = "韩立还只是炼气期,买不起灵兽坐骑,只能徒步翻山,走了整整三日才到坊市。"
    rep = check_world_law_consistency_gate(prose_walk, world_model=world_model_health_input(model)).to_checker_report()
    if universal_default and rep.issues:
        print(f"  [BUG] 一致性 gate 误判忠实正文为违规: {[i.id for i in rep.issues]}")
    else:
        print(f"  [info] gate issues={len(rep.issues)} (active_laws={rep.metrics.get('active_law_count')}) "
              f"— 误判风险取决于上面 enforcement 是否写成『默认飞行』")


def world_model_health_input(model):
    # gate accepts a dict world_model; round-trip the model to a dict.
    from bestseller.domain.world_model import world_model_to_dict

    return world_model_to_dict(model)


def audit_tier_ladder(model) -> None:
    banner("环节5 · 定量阶梯(境界→寿元/实力)能否被表达与校验")
    laws_with_tiers = [law for law in model.world_laws if law.tiers]
    has = bool(laws_with_tiers)
    print(f"  [{'OK' if has else 'BUG'}] WorldLaw 是否有结构化 tier 阶梯且被产出: "
          f"{'是' if has else '无——LLM 未产出 tiers'}")
    for law in laws_with_tiers[:3]:
        ladder = "、".join(f"{t.tier}={t.value}" for t in law.tiers)
        print(f"   - [{law.dimension}] {ladder}")
    print("  影响: gate 现可用 detect_tier_violations 核对“某章说筑基寿元四百年”与阶梯三百岁矛盾。")


def audit_gate_recall(model) -> None:
    banner("环节6 · 一致性 gate 召回率(真违规能否被抓)· LLM 语义判官")
    wm = world_model_health_input(model)
    judge = make_llm_judge(model.world_laws)
    cases = [
        ("炼气期御剑千里(违反境界门控)", "那炼气期的少年脚尖一点,御起飞剑直冲云霄,千里转瞬即至。", True),
        ("用纸币买丹药(违反灵石货币)", "他从怀里掏出一叠银票,数了数,买下那瓶筑基丹。", True),
        ("忠实正文:坊市灵石交易(应放行)", "韩立取出二十块灵石,从修士手中换来一张二阶符箓。", False),
    ]
    ok = True
    for label, prose, should_flag in cases:
        rep = check_world_law_consistency_gate(prose, world_model=wm, judge=judge).to_checker_report()
        flagged = len(rep.issues) > 0
        verdict = "✓" if flagged == should_flag else "✗"
        if flagged != should_flag:
            ok = False
        print(f"  [{verdict}] {label}: flagged={flagged}(期望{should_flag}) issues={[i.id for i in rep.issues]}")
    print(f"\n  [{'OK' if ok else 'BUG'}] LLM 语义判官召回+精度: {'两违规抓到、合法放行' if ok else '仍有漏报/误报(LLM 随机性,可多判官投票)'}")


def audit_ripple(model) -> None:
    banner("环节7 · 动态涟漪(里程碑事件应多维波及)")
    # 境界 var declares the causal chain via cascades_to → one event propagates.
    state_vars = [
        {"key": "韩立境界", "change_triggers": ["突破筑基", "筑基成功"], "current_value": "炼气期",
         "desired_direction": "提升", "cascades_to": ["韩立寿元", "御器飞行解锁", "散修地位"]},
        {"key": "韩立寿元", "change_triggers": ["寿元增长"], "current_value": "约百年", "desired_direction": "增长"},
        {"key": "御器飞行解锁", "change_triggers": [], "current_value": "未解锁", "desired_direction": "解锁"},
        {"key": "散修地位", "change_triggers": [], "current_value": "底层", "desired_direction": "上升"},
    ]
    event = "苦修多年后,韩立终于突破筑基,体内灵力暴涨。"
    updates = compute_state_ripples(state_vars, event, chapter_number=42)
    print(f"  事件: {event}")
    print(f"  涟漪更新了 {len(updates)}/{len(state_vars)} 个状态变量(含级联):")
    for u in updates:
        src = f"(级联自{u['cascaded_from']})" if u.get("cascaded_from") else "(主)"
        print(f"   - {u['key']}{src}: {u['current_value']}")
    cascaded = [u for u in updates if u.get("cascaded_from")]
    multi = len(updates) >= 3 and cascaded
    print(f"\n  [{'OK' if multi else 'BUG'}] 单次里程碑是否跨变量因果波及: "
          f"{'是——筑基⇒寿元/飞行解锁/地位 沿 cascades_to 因果传播,且带方向标记' if multi else '否'}")


def audit_dependency_graph(model) -> None:
    banner("环节8 · 规律间因果依赖(《凡人》严谨=规律环环相扣)")
    with_deps = [law for law in model.world_laws if law.depends_on]
    has = bool(with_deps)
    print(f"  [{'OK' if has else 'BUG'}] WorldLaw 是否有 law→law 依赖(depends_on)且被产出: "
          f"{'是' if has else '无——LLM 未产出 depends_on'}")
    for law in with_deps[:5]:
        print(f"   - [{law.dimension}] 依赖→ {law.depends_on}")
    if has:
        print("  → 可表达『资源垄断⇒阶级分层⇒暴力合法』这类链式推演,《凡人》严谨性的来源。")


def audit_baseline_layers(model) -> None:
    layers = list(model.baseline_layers)
    has = len(layers) >= 2
    print(f"\n  环节2补 · 双层 baseline: baseline_layers={layers} "
          f"[{'OK——双层社会已显式表达' if has else 'BUG——未产出共存层'}]")


def main() -> None:
    model, used_llm = derive()
    audit_axioms()
    audit_baseline(model)
    audit_baseline_layers(model)
    audit_coverage(model)
    audit_capability_gating(model)
    audit_tier_ladder(model)
    audit_gate_recall(model)
    audit_ripple(model)
    audit_dependency_graph(model)
    banner("结论")
    print(f"  推演来源: {'真 LLM(MiniMax planner)' if used_llm else '离线 fallback'}")
    print("  见上方各环节 [BUG]/[OK] 标记。架构级缺陷集中在: 能力门控 / 定量阶梯 / 双层基线 / "
          "gate 召回 / 涟漪因果 / 规律依赖图。")


if __name__ == "__main__":
    main()
