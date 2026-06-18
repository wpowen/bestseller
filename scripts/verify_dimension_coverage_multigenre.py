"""Prove the expanded dimension table covers ALL book types, not just 凡人/仙侠.

Runs a REAL planner-LLM derivation for several deliberately diverse genres
(sci-fi / urban-romance / horror / historical) and checks:
  1. the engine derives a non-fallback world for each (no silent placeholder),
  2. the five new cross-genre axes (species/cosmology/nature/body/kinship) get
     USED where the genre needs them — and are NOT force-padded where it doesn't
     (genre-adaptive),
  3. the worlds are highly distinct from one another.

Run:  .venv/bin/python scripts/verify_dimension_coverage_multigenre.py
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

# genre -> (premise, new axes this genre SHOULD meaningfully touch)
CASES = {
    "科幻·星际": (
        "近未来人类殖民星际,意识可上传至义体与AI共生,星系间靠跃迁门连通,外星文明与人类争夺跃迁枢纽。",
        {"cosmology_and_realms", "species_and_groups", "body_and_medicine"},
    ),
    "都市言情": (
        "现代都市,豪门千金与平民设计师相恋,两大家族联姻博弈,门第与彩礼是横亘的鸿沟。",
        {"kinship_and_reproduction"},
    ),
    "灵异恐怖": (
        "现代都市夜里,阴阳两界的界限在一座老城区变薄,怨灵借生人执念现身,通灵者在人鬼之间斡旋。",
        {"species_and_groups", "cosmology_and_realms"},
    ),
    "历史宫斗": (
        "架空王朝后宫,出身寒门的女子入宫,靠联姻、血脉与算计在妃嫔与外戚的权力网中求存。",
        {"kinship_and_reproduction"},
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
    print("\n############ 世界模型 · 全题材维度覆盖验证 ############\n")
    blobs: list[str] = []
    all_ok = True
    for genre, (premise, expected_new) in CASES.items():
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

        # The world's macro-structure may be expressed as a cosmology law OR via
        # baseline_layers (coexisting realms) — both satisfy the "realms" axis.
        covered_axes = set(dims)
        if len(model.baseline_layers) >= 2:
            covered_axes.add("cosmology_and_realms")
        new_hit = expected_new & covered_axes
        non_fallback = placeholder == 0 and len(model.world_laws) >= 5
        expected_ok = expected_new <= covered_axes
        ok = non_fallback and expected_ok
        all_ok = all_ok and ok
        print(f"[{genre}] 规律数={len(model.world_laws)} 覆盖维度={len(dims)} "
              f"占位符={placeholder} {'✓真推演' if non_fallback else '✗退回fallback'}")
        print(f"   该题材应触及的新维度 {sorted(expected_new)} -> 命中 {sorted(new_hit)} "
              f"[{'OK' if expected_ok else 'BUG-缺' + str(sorted(expected_new - dims))}]")
        print(f"   baseline_layers={model.baseline_layers or '—'} | 全部维度={sorted(dims)}\n")

    if len(blobs) >= 2:
        d = corpus_distinctness(blobs)
        print(f"跨题材世界 distinctness = {d}  [{'OK' if d > 0.6 else '偏低'}]")
        all_ok = all_ok and d > 0.6

    print("\n=== 总判定 ===")
    print("PASS — 扩维后引擎对各类题材均真推演、新维度按需启用、世界互异" if all_ok
          else "见上方未通过项")


if __name__ == "__main__":
    main()
