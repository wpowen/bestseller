"""Prove the core-ideology layer is genre-DECOUPLED and diverse.

The hard requirement: themes must NOT be bound to genre. Same-genre books with
different premises must get different 主主题/子题 — no "all 仙侠 = 天地不仁".

This script is deterministic (no LLM / no DB needed): it derives the fallback
IdeologyKernel for many premises within ONE genre and reports the variety of
primary themes, sub-themes and motif spines. It also confirms that passing a
different genre label does NOT change the result (selection ignores genre).

Run:  .venv/bin/python scripts/verify_ideology_diversity.py
"""

# ruff: noqa: RUF001, E501, ANN202

from __future__ import annotations

from bestseller.domain.ideology import ideology_kernel_from_dict
from bestseller.services.ideology_kernel import fallback_ideology_kernel
from bestseller.services.ideology_library import load_motif_library

# 10 different 仙侠 premises — same genre, deliberately varied situations.
XIANXIA_PREMISES = [
    "边城百年被天罚清洗，少年拜入仙门追查真相。",
    "废弟子捡到一缕剑魂，被迫替死人完成未了的复仇。",
    "守山人世代镇着一口古井，直到井里开始说话。",
    "宗门拍卖会上，他用三年寿命换了一枚来历不明的印。",
    "小镇每逢月圆有人失踪，新来的游方道士住进了客栈。",
    "她继承了一座没有香火的山神庙，神位上空无一物。",
    "卖丹的少年发现自己炼出的每一炉丹，都会让一个人忘掉一段记忆。",
    "渡劫失败的老修士借尸还魂，醒来时身在自己的仇家府上。",
    "采药人误入一座只在雾天出现的城，城里的人都在等一个早死的人。",
    "少女背着会越长越重的剑下山，剑里封着她不肯承认的师父。",
]


def _kernel_for(premise: str, genre: str | None = None):
    return ideology_kernel_from_dict(
        fallback_ideology_kernel(premise=premise, genre=genre, volumes=8)
    )


def main() -> None:
    lib = load_motif_library()
    print(f"\n=== Ideology diversity (genre-decoupled) === motifs={len(lib.motifs)} themes={len(lib.themes)}\n")
    print("同一题材(仙侠)、不同前提 → 不同主主题/脊柱：\n")

    theses: set[str] = set()
    spines: set[tuple[str, ...]] = set()
    primary_motifs: set[str] = set()
    for i, premise in enumerate(XIANXIA_PREMISES, 1):
        k = _kernel_for(premise, genre="仙侠")
        spine = tuple(b.motif_key for b in k.all_motifs())
        theses.add(k.thesis_statement)
        spines.add(spine)
        primary_motifs.add(k.primary_motif.display_name)
        subs = " / ".join(t.proposition for t in k.sub_themes[:2])
        print(f"[{i:>2}] {premise[:22]}…")
        print(f"      主主题: {k.thesis_statement}")
        print(f"      脊柱  : {k.primary_motif.display_name}·{k.secondary_motifs[0].display_name}·"
              f"{k.secondary_motifs[1].display_name}·"
              f"{k.hidden_endgame_motif.display_name if k.hidden_endgame_motif else '—'}")
        print(f"      子题  : {subs}")
        print()

    n = len(XIANXIA_PREMISES)
    print("=== DIVERSITY ===")
    print(f"unique 主主题   : {len(theses)}/{n}")
    print(f"unique 脊柱组合 : {len(spines)}/{n}")
    print(f"unique 宇宙母题 : {len(primary_motifs)}/4  ({', '.join(sorted(primary_motifs))})")

    # Prove genre does NOT drive selection: same premise + different genre label
    # → identical kernel (selection ignores genre).
    p = XIANXIA_PREMISES[0]
    same = _kernel_for(p, genre="仙侠").thesis_statement == _kernel_for(p, genre="都市").thesis_statement \
        == _kernel_for(p, genre=None).thesis_statement
    print(f"\ngenre 不影响选择(同前提换题材→同主题): {same}")

    ok = len(theses) >= max(4, n // 2) and len(spines) >= 3 and same
    print(f"\n判定: {'PASS — 主题与题材解耦且高度多样' if ok else 'FAIL — 多样性不足/疑似绑定题材'}\n")


if __name__ == "__main__":
    main()
