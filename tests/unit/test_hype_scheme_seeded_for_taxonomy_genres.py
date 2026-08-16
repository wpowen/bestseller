"""爽点配方对 taxonomy 题材也必须落库——三本书 109 章 hype 全 NULL 的源头。

`seed_invariants` 的 hype_scheme 取自 `preset_overrides`，而调用点只用
`infer_genre_preset`（**只查 curated 预设表**）。真机对照（2026-08-16）：

    infer_genre_preset('东方玄幻') → GenrePreset，12 条配方   ← 表里有
    infer_genre_preset('搞笑沙雕') → None                     ← 表里没有

返回 None 时 `preset_overrides` 保持 `{}` → `hype_scheme` 落成空壳 →
`build_chapter_hype_blocks` 立即返回空块 → 爽点约束块 0 字 → 写手根本没收到
「每章要有结算」这条指令 → 盖戳全 NULL → 覆盖率 0.00。

`synthesize_genre_preset` 本就是为这种情况准备的兜底合成，但调用点从未调用它。
这是「目录↔taxonomy 两套词汇表」老病的又一处形态：能力存在，长在书不走的路上。

⚠️ 兜底**不得覆盖** curated：玄幻的 12 条手工配方比通用 5 条精确得多。
"""

from __future__ import annotations

import pytest

from bestseller.services.hype_engine import hype_scheme_from_preset_overrides
from bestseller.services.writing_presets import (
    infer_genre_preset,
    synthesize_genre_preset,
)


def _resolve_preset_overrides(genre: str, sub_genre: str, pack_key: str | None = None):
    """与 pipelines.py 调用点同源：curated 优先，查不到才兜底合成。"""

    preset = infer_genre_preset(genre or None, sub_genre or None)
    source = "curated"
    if preset is None:
        preset = synthesize_genre_preset(
            str(pack_key or sub_genre or genre or "").strip() or "light-novel",
            genre=genre or None,
            sub_genre=sub_genre or None,
        )
        source = "synthesized"
    return dict(preset.writing_profile_overrides or {}), source


def _deck(overrides) -> list:
    return list((overrides.get("hype") or {}).get("recipe_deck") or [])


def test_curated_genre_still_uses_its_own_deck():
    """玄幻在 curated 表里，兜底绝不能顶掉它手工调过的配方。"""

    overrides, source = _resolve_preset_overrides("东方玄幻", "东方玄幻")
    assert source == "curated"
    assert len(_deck(overrides)) >= 10, "curated 配方被兜底覆盖了"


@pytest.mark.parametrize(
    "genre, sub_genre, pack_key",
    [
        ("搞笑沙雕", "搞笑沙雕", "light-novel"),
        ("搞笑沙雕", "搞笑沙雕", None),
        ("", "", None),  # 题材缺失也不能退化成空壳
    ],
)
def test_taxonomy_genre_falls_back_to_a_real_deck(genre, sub_genre, pack_key):
    """curated 查不到时必须兜底出配方，而不是空手而归。"""

    overrides, source = _resolve_preset_overrides(genre, sub_genre, pack_key)
    assert source == "synthesized"
    assert _deck(overrides), f"{genre or '(空题材)'} 拿到空 recipe_deck"


@pytest.mark.parametrize(
    "genre, sub_genre",
    [("东方玄幻", "东方玄幻"), ("搞笑沙雕", "搞笑沙雕"), ("", "")],
)
def test_hype_scheme_is_never_empty(genre, sub_genre):
    """真正要守的不变量：任何题材建的书，hype_scheme 都不是空壳。

    空壳会让 build_chapter_hype_blocks 直接返回空块，整条爽点链从第一环就断。
    """

    overrides, _ = _resolve_preset_overrides(genre, sub_genre)
    scheme = hype_scheme_from_preset_overrides(overrides)
    assert not scheme.is_empty, f"{genre or '(空题材)'} 的 hype_scheme 仍是空壳"


def test_infer_returns_none_for_taxonomy_genre():
    """把病因本身钉住：这条一旦变绿，说明 curated 表补了词条，兜底仍应保留。"""

    assert infer_genre_preset("搞笑沙雕", "搞笑沙雕") is None
