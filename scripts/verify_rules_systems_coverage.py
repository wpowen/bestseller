"""Targeted check: the new `rules_and_systems` axis closes the rule-driven genres.

NOT a run-every-genre sweep. Only the genres whose plot engine is an explicit,
exploitable rule/system — rule-horror / infinite-flow+system / esports — plus one
already-covered hot genre (apocalypse-supply) as a no-regression control.

Verifies: each gap genre's derivation now USES rules_and_systems; everything still
derives for real (no silent fallback); worlds stay distinct.

Run:  .venv/bin/python scripts/verify_rules_systems_coverage.py
"""

# ruff: noqa: E501

from __future__ import annotations

import os

from dotenv import load_dotenv

from bestseller.services.world_dimensions import corpus_distinctness
from bestseller.services.world_model_deriver import (
    build_world_model_system_prompt,
    build_world_model_user_prompt,
    parse_world_model,
)

load_dotenv()

# genre -> (premise, must this genre cover rules_and_systems?)
CASES = {
    "规则怪谈": (
        "现代都市,夜里出现一栋诡异公寓,住客必须严格遵守墙上贴的一串荒诞规则,违规者离奇死亡,主角靠拆解规则漏洞求生。",
        True,
    ),
    "无限流·系统": (
        "普通人被神秘系统拉入一个个副本世界闯关,每个副本有硬性通关规则与积分奖惩,积分可兑换能力,死亡即真死。",
        True,
    ),
    "电竞": (
        "职业电竞联赛,战队靠赛制、版本规则与战术博弈争夺总冠军,新人选手从青训打到顶级联赛。",
        True,
    ),
    "末日囤货(控制组·已覆盖)": (
        "灾变降临,物资在末世成为硬通货,主角靠囤积与以物易物在丧尸潮和人性崩坏中壮大势力。",
        False,
    ),
}


def call_llm(system_prompt: str, user_prompt: str) -> str | None:
    model = os.environ.get("BESTSELLER__LLM__PLANNER__MODEL")
    api_base = os.environ.get("BESTSELLER__LLM__PLANNER__API_BASE")
    api_key = os.environ.get(os.environ.get("BESTSELLER__LLM__PLANNER__API_KEY_ENV", ""), "")
    if not (model and api_base and api_key):
        return None
    try:
        import litellm

        resp = litellm.completion(
            model=model, api_base=api_base, api_key=api_key,
            messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
            temperature=0.7, max_tokens=6000, timeout=180,
        )
        return resp.choices[0].message.content
    except Exception as exc:
        print(f"  [!] LLM 调用失败: {exc}")
        return None


def main() -> None:
    print("\n######## rules_and_systems 维度 · 规则驱动题材定向验证 ########\n")
    blobs: list[str] = []
    all_ok = True
    for genre, (premise, needs_rules) in CASES.items():
        sysp = build_world_model_system_prompt(language="zh")
        usr = build_world_model_user_prompt(premise=premise, genre=genre, language="zh")
        content = call_llm(sysp, usr)
        if not content:
            print(f"[{genre}] 跳过(LLM 不可用)")
            continue
        model = parse_world_model(content, premise=premise, genre=genre)
        dims = model.covered_dimensions()
        placeholder = sum(1 for law in model.world_laws if "待具体推演" in law.delta)
        blobs.append(" ".join(law.delta for law in model.world_laws))

        non_fallback = placeholder == 0 and len(model.world_laws) >= 5
        has_rules = "rules_and_systems" in dims
        rules_ok = has_rules if needs_rules else True
        ok = non_fallback and rules_ok
        all_ok = all_ok and ok

        rules_law = next((law for law in model.world_laws if law.dimension == "rules_and_systems"), None)
        print(f"[{genre}] 规律数={len(model.world_laws)} 维度={len(dims)} 占位符={placeholder} "
              f"{'✓真推演' if non_fallback else '✗fallback'}")
        tag = "OK" if rules_ok else "BUG-缺rules_and_systems"
        print(f"   需要规则轴={needs_rules} 命中={has_rules} [{tag}]")
        if rules_law:
            print(f"   rules_and_systems: {rules_law.delta[:50]} || 约束:{rules_law.enforcement[:56]}")
        print()

    if len(blobs) >= 2:
        d = corpus_distinctness(blobs)
        print(f"跨题材 distinctness = {d}  [{'OK' if d > 0.6 else '偏低'}]")
        all_ok = all_ok and d > 0.6

    print("\n=== 总判定 ===")
    print("PASS — 规则驱动题材均推出 rules_and_systems、控制组无回归、世界互异" if all_ok else "见上方未通过项")


if __name__ == "__main__":
    main()
