"""taxonomy 建的书必须拿得到爽点配方（2026-08-16 真机定罪）。

真机现象：三本书 109 章的 `hype_type` / `hype_intensity` / `hype_recipe_key`
**100% NULL**，正文零爽点结算——《破澡堂真话局》不对称碾压覆盖率 0.00
（50 章零次，人类语料中位 0.37），而我在 user_hints 里白纸黑字写了
「每章一个当场兑现的爽点结算」。指令写了，产出零效果。

根因链（逐环实证）：
    taxonomy 建书 → synthesize_genre_preset 不产 hype 命名空间
    → writing_profile_overrides 无 hype
    → hype_scheme_from_preset_overrides 返回 is_empty=True
    → build_chapter_hype_blocks 立即返回 EMPTY_HYPE_BLOCKS
    → 爽点约束整块不进 prompt
    → hype 三字段从未赋值 → 正文没有爽点 → 三个门禁全 audit_only 不报警

爽点配方此前只挂在旧的 curated preset 上，taxonomy 路径整套引擎拿不到——
「目录↔taxonomy 两套词汇表」老病的新形态。

⚠️ 关键契约：**空 deck 比"配方不够贴题材"危险得多**。配方选得泛一点只是
指令不够精准；deck 为空会让整套引擎短路，等于这本书完全没有爽点约束。
所以退到通用牌是正确行为，退到空牌不是。
"""

from __future__ import annotations

from bestseller.services.hype_engine import hype_scheme_from_preset_overrides
from bestseller.services.writing_presets import synthesize_genre_preset


def _scheme_for(genre_key: str, genre: str, sub_genre: str | None):
    preset = synthesize_genre_preset(genre_key, genre=genre, sub_genre=sub_genre)
    return preset, hype_scheme_from_preset_overrides(
        getattr(preset, "writing_profile_overrides", {}) or {}
    )


def test_taxonomy_preset_carries_nonempty_hype_deck() -> None:
    """三条真机路径都必须拿到非空配方。"""

    cases = [
        ("custom-light-novel", "轻小说·二次元", "搞笑沙雕"),
        ("custom-xuanhuan", "东方玄幻", "符箓秘术"),
        ("custom-urban", "都市", "都市逆袭"),
    ]
    for key, genre, sub in cases:
        preset, scheme = _scheme_for(key, genre, sub)
        overrides = getattr(preset, "writing_profile_overrides", {}) or {}
        assert "hype" in overrides, f"{genre}/{sub} 缺 hype 命名空间"
        assert scheme.recipe_deck, f"{genre}/{sub} 配方为空 → 整套引擎会短路"
        assert not scheme.is_empty, f"{genre}/{sub} is_empty=True → EMPTY_HYPE_BLOCKS"


def test_unknown_genre_falls_back_to_generic_not_empty() -> None:
    """匹配不上时退到通用牌——退到空牌是失败模式，不是安全默认。"""

    _preset, scheme = _scheme_for("custom-unknown", "某个没见过的题材", None)
    assert scheme.recipe_deck
    assert not scheme.is_empty


def test_hype_constraints_actually_reach_the_prompt() -> None:
    """端到端：约束块必须非空且带类型/配方/强度（修前长度为 0）。"""

    import inspect
    from uuid import uuid4

    from bestseller.services.diversity_budget import DiversityBudget
    from bestseller.services.invariants import LengthEnvelope, ProjectInvariants
    from bestseller.services.prompt_constructor import build_chapter_hype_blocks

    required = [
        name
        for name, param in inspect.signature(LengthEnvelope.__init__).parameters.items()
        if name != "self" and param.default is inspect.Parameter.empty
    ]
    envelope = LengthEnvelope(
        **{
            name: (1800 if "min" in name else 3600 if "max" in name else 2600)
            for name in required
        }
    )
    project_id = uuid4()
    _preset, scheme = _scheme_for("custom-light-novel", "轻小说·二次元", "搞笑沙雕")
    invariants = ProjectInvariants(
        project_id=project_id,
        language="zh-CN",
        length_envelope=envelope,
        hype_scheme=scheme,
    )
    blocks = build_chapter_hype_blocks(
        invariants,
        DiversityBudget(project_id=project_id),
        chapter_no=5,
        total_chapters=50,
    )
    assert blocks.hype_constraints_block, "爽点约束块为空 → 写手看不到任何爽点要求"
    assert blocks.assigned_hype_type is not None
    assert blocks.assigned_hype_recipe is not None
    assert blocks.assigned_hype_intensity is not None
    assert "爽点" in blocks.hype_constraints_block
