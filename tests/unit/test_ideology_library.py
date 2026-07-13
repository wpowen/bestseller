"""Unit tests for the core-ideology motif library + combination engine."""

from __future__ import annotations

import pytest

from bestseller.domain.ideology import (
    IdeologyKernel,
    LAYER_KEYS,
    MotifBinding,
    ideology_kernel_from_dict,
    render_ideology_kernel_prompt_block,
)
from bestseller.services.ideology_library import (
    book_diversity_seed,
    load_motif_library,
    load_theme_corpus,
    render_motif_library_prompt_block,
    select_themes,
    suggest_motif_formula,
)

pytestmark = pytest.mark.unit


# --- library integrity ------------------------------------------------------


def test_library_has_thirteen_motifs_across_four_layers() -> None:
    lib = load_motif_library()
    assert len(lib.motifs) == 13
    layers = {m.layer for m in lib.motifs}
    assert layers == set(LAYER_KEYS)
    # Every layer declares its motif_keys and they resolve.
    for layer in lib.layers:
        for key in layer.motif_keys:
            assert lib.by_key(key) is not None, f"layer {layer.key} references unknown motif {key}"


def test_every_motif_has_belief_arc_and_thesis() -> None:
    lib = load_motif_library()
    for motif in lib.motifs:
        assert motif.thesis_template, f"{motif.key} missing thesis_template"
        assert motif.core_question_template, f"{motif.key} missing core_question"
        assert motif.belief_initial and motif.belief_shatter and motif.belief_reconstruction
        assert motif.concrete_symbol_hints, f"{motif.key} missing concrete symbols"


def test_pairs_well_with_references_are_valid() -> None:
    lib = load_motif_library()
    for motif in lib.motifs:
        for other in motif.pairs_well_with:
            assert lib.by_key(other) is not None, f"{motif.key} pairs with unknown {other}"


def test_combinations_resolve_and_cover_four_layers() -> None:
    lib = load_motif_library()
    assert lib.combinations
    for recipe in lib.combinations:
        motifs = [lib.by_key(recipe.primary), lib.by_key(recipe.hidden)]
        motifs += [lib.by_key(k) for k in recipe.secondary]
        assert all(m is not None for m in motifs), f"recipe {recipe.name} has unknown motif"
        layers = {m.layer for m in motifs if m}
        assert len(layers) >= 3, f"recipe {recipe.name} too layer-thin: {layers}"


# --- theme corpus (large, genre-agnostic) -----------------------------------


def test_theme_corpus_loads_and_is_large_genre_agnostic() -> None:
    lib = load_motif_library()
    corpus = load_theme_corpus()
    assert corpus is lib.themes
    assert len(corpus) >= 100, "seed corpus should be sizeable (scales to 1000-2000)"
    # Every theme references a known motif, has a derived layer, and a proposition.
    for t in corpus:
        assert lib.by_key(t.motif) is not None, f"theme {t.id} -> unknown motif {t.motif}"
        assert t.layer in LAYER_KEYS, f"theme {t.id} layer not derived"
        assert t.proposition
    # No duplicate ids.
    ids = [t.id for t in corpus]
    assert len(ids) == len(set(ids)), "duplicate theme ids"
    # Every motif has at least a few theme variants (diversity per motif).
    for motif in lib.motifs:
        assert len(lib.themes_for_motif(motif.key)) >= 3, f"motif {motif.key} too few themes"


# --- report supplements: motif scaffolding + exemplars + principles ---------


def test_every_motif_has_writing_scaffolding() -> None:
    """Report 母题模板表 fused: each motif carries an opening hook, 3-act spine,
    character paradigm, key scenes, and extensible subplots."""

    lib = load_motif_library()
    for m in lib.motifs:
        assert m.opening_hook, f"{m.key} missing opening_hook"
        assert m.three_act, f"{m.key} missing three_act"
        assert m.character_paradigm, f"{m.key} missing character_paradigm"
        assert m.key_scenes, f"{m.key} missing key_scenes"
        assert m.extensible_subplots, f"{m.key} missing extensible_subplots"


def test_exemplars_and_principles_load_and_resolve() -> None:
    from bestseller.services.ideology_library import load_ideology_exemplars

    exemplars, principles = load_ideology_exemplars()
    assert len(exemplars) >= 10, "report's 10 worked premises should be present"
    lib = load_motif_library()
    for ex in exemplars:
        assert ex.synopsis and ex.title
        for mk in ex.recipe:
            assert lib.by_key(mk) is not None, f"exemplar {ex.title} -> unknown motif {mk}"
    # The report's four creation principles must be present.
    for key in ("worldview_from_cost", "pacing_dual_track", "ip_preembed", "closing_rule"):
        assert key in principles, f"missing principle {key}"


def test_derivation_menu_includes_scaffolding_exemplars_principles() -> None:
    block = render_motif_library_prompt_block(seed="某前提")
    assert "母题配方范例" in block       # few-shot exemplars
    assert "写作脚手架" in block          # per-motif scaffolding
    assert "创作原则" in block            # report principles
    assert "代价表" in block              # worldview-from-cost principle


# --- mainstream (grounded) themes -------------------------------------------


def test_mainstream_subjects_load_and_map_to_valid_motifs() -> None:
    lib = load_motif_library()
    assert len(lib.subjects) >= 18, "should carry a broad mainstream subject set"
    for s in lib.subjects:
        assert lib.by_key(s.motif) is not None, f"subject {s.id} -> unknown motif {s.motif}"
        assert s.layer in LAYER_KEYS, f"subject {s.id} layer not derived"
        assert s.statements, f"subject {s.id} has no theme statements"
    # Recognized mainstream subjects must be present (not idiosyncratic inventions).
    names = {s.name for s in lib.subjects}
    for expected in ("成长", "守护", "复仇", "救赎与宽恕", "权力与腐化", "正义"):
        assert expected in names, f"missing mainstream subject {expected}"


def test_grounded_themes_present_and_lead_the_pool() -> None:
    lib = load_motif_library()
    grounded = lib.grounded_themes
    assert len(grounded) >= 60, "mainstream statements should dominate the pool"
    # Grounded themes are tagged with a subject id; aphorisms are not.
    assert all(t.subject for t in grounded)
    # Every motif that has a mainstream subject offers grounded themes.
    motifs_with_subject = {s.motif for s in lib.subjects}
    for mk in motifs_with_subject:
        assert lib.themes_for_motif(mk, grounded_only=True), f"motif {mk} has no grounded theme"


def test_primary_theme_is_grounded_mainstream() -> None:
    """The HARD requirement: the headline 主主题 must be a recognized, mainstream
    theme (not a contrived aphorism) — across many premises."""

    lib = load_motif_library()
    premises = [
        "边城百年被天罚清洗，少年拜入仙门追查真相。",
        "刑警接手坠楼案，越查越牵连自己失踪的女儿。",
        "外卖员被高压电击成为气运借贷的节点。",
        "寒门小吏在大灾边郡发现救灾的敌人是官制。",
        "废柴被退婚，三年后携满级天赋归来。",
        "她继承了一座没有香火的山神庙，神位上空无一物。",
    ]
    grounded_count = 0
    subjects_seen = set()
    for p in premises:
        seed = book_diversity_seed(premise=p)
        formula = suggest_motif_formula(seed=seed)
        sel = select_themes(lib, formula=formula, seed=seed)
        assert sel.primary_theme is not None
        if sel.primary_theme.grounded:
            grounded_count += 1
            subjects_seen.add(sel.primary_theme.subject)
    assert grounded_count == len(premises), "every primary 主主题 must be grounded/mainstream"
    assert len(subjects_seen) >= 3, "mainstream primaries must still vary by premise"


# --- seed-driven spine (genre-DECOUPLED) ------------------------------------


def test_suggest_formula_covers_four_layers_for_any_seed() -> None:
    for seed in ["a", "边城天罚", "刑警坠楼旧案", "外卖员高压电击", "x" * 50, ""]:
        formula = suggest_motif_formula(seed=seed)
        assert formula.covered_layers() == set(LAYER_KEYS), f"seed={seed!r} miss layers"
        assert len({m.key for m in formula.all_motifs()}) == 4


def test_suggest_formula_roles_match_layers() -> None:
    formula = suggest_motif_formula(seed="任意前提")
    assert formula.primary.layer == "cosmic_order"
    assert formula.secondary_action.layer == "subject_choice"
    assert formula.secondary_suspense.layer == "cognitive_crisis"
    assert formula.hidden.layer == "ethical_reversal"


def test_suggest_formula_is_reproducible_per_seed() -> None:
    a = suggest_motif_formula(seed="同一本书的前提")
    b = suggest_motif_formula(seed="同一本书的前提")
    assert [m.key for m in a.all_motifs()] == [m.key for m in b.all_motifs()]


def test_no_genre_binding_same_genre_different_premise_diverge() -> None:
    """HARD REQUIREMENT: same-genre books must NOT collapse to one theme.

    Two different 仙侠 premises (seed = premise identity, genre excluded) should
    produce different spines and/or different primary themes across a population —
    never a fixed genre→theme lock.
    """

    lib = load_motif_library()
    premises = [
        "边城百年被天罚清洗，少年拜入仙门追查真相。",
        "废弟子捡到一缕剑魂，被迫替死人完成未了的复仇。",
        "守山人世代镇着一口古井，直到井里开始说话。",
        "宗门拍卖会上，他用三年寿命换了一枚来历不明的印。",
        "小镇每逢月圆有人失踪，新来的游方道士住进了客栈。",
        "她继承了一座没有香火的山神庙，神位上空无一物。",
    ]
    spines = []
    primary_themes = set()
    for p in premises:
        seed = book_diversity_seed(premise=p)  # genre intentionally NOT in the seed
        formula = suggest_motif_formula(seed=seed)
        spines.append(tuple(m.key for m in formula.all_motifs()))
        sel = select_themes(lib, formula=formula, seed=seed)
        if sel.primary_theme:
            primary_themes.add(sel.primary_theme.proposition)
    # The population must show real variety, not one repeated theme.
    assert len(set(spines)) >= 3, f"spines too homogeneous: {set(spines)}"
    assert len(primary_themes) >= 4, f"primary themes too homogeneous: {primary_themes}"


def test_suggest_formula_takes_no_genre_param() -> None:
    # Proves genre cannot drive selection: the function does not accept a genre kwarg.
    import inspect

    sig = inspect.signature(suggest_motif_formula)
    assert "genre" not in sig.parameters
    assert "seed" in sig.parameters


def test_select_themes_returns_primary_plus_distinct_subs() -> None:
    lib = load_motif_library()
    formula = suggest_motif_formula(seed="一个具体前提")
    sel = select_themes(lib, formula=formula, seed="一个具体前提", n_sub=4)
    assert sel.primary_theme is not None
    props = [sel.primary_theme.proposition, *[t.proposition for t in sel.sub_themes]]
    assert len(props) == len(set(props)), "primary + sub themes must be distinct"
    assert 1 <= len(sel.sub_themes) <= 4


def test_library_prompt_block_lists_layers_and_warns_against_genre_cliche() -> None:
    block = render_motif_library_prompt_block(seed="某前提")
    for layer in ("宇宙秩序层", "主体抉择层", "认知危机层", "伦理反转层"):
        assert layer in block
    assert "多样性" in block  # the anti-cliché diversity instruction
    assert "主流主题库" in block  # the recognized-theme menu
    assert "主流主题样本" in block
    assert "标新立异" in block  # the "do not invent contrived themes" instruction


# --- domain kernel round-trip + robustness ----------------------------------


def _minimal_kernel_dict() -> dict:
    return {
        "cosmic_premise": "世界不会因为主角善良就奖励他。",
        "thesis_statement": "世界不保佑善者，但人仍可彼此托底。",
        "core_question": "天若不仁，人为何还要善？",
        "primary_motif": {
            "motif_key": "tiandi_buren",
            "display_name": "天地不仁",
            "layer": "cosmic_order",
            "book_thesis": "边城百年被天罚清洗，没有谁是无辜的。",
            "book_core_question": "若天罚只是收割，人还该不该护这座城？",
            "concrete_symbols": ["祭坛余烬", "雨夜浅坟"],
        },
        "secondary_motifs": [
            {
                "motif_key": "daijia",
                "display_name": "代价",
                "layer": "subject_choice",
                "book_thesis": "每次救人都要折一段寿。",
                "book_core_question": "你愿意用寿命换真相吗？",
                "role": "action",
            },
            {
                "motif_key": "zhenxiang",
                "display_name": "真相",
                "layer": "cognitive_crisis",
                "book_thesis": "天罚是上宗收割灵脉的周期工程。",
                "book_core_question": "你真的想知道天罚的真相吗？",
                "role": "suspense",
            },
        ],
        "hidden_endgame_motif": {
            "motif_key": "shane_diandao",
            "display_name": "善恶颠倒",
            "layer": "ethical_reversal",
            "book_thesis": "守护神才是收割者。",
            "book_core_question": "谁才是真正的恶？",
            "reveal_after_volume": 4,
        },
        "belief_arc": {
            "initial_belief": "相信祭天能换庇佑。",
            "midpoint_shatter": "祭天后全村照样覆灭。",
            "final_reconstruction": "不再求天，转向人间托底。",
        },
        "cost_system": [
            {"acquires": "真相", "costs": "寿命", "delayed": True, "irreversible": True},
        ],
        "motif_to_world_bindings": ["灵脉收割周期 = 天罚机制"],
        "per_volume_thesis_pressure": ["第1卷：相信天罚是天意"],
        "forbidden_resolutions": ["天道最终奖励好人"],
    }


def test_kernel_round_trip_preserves_structure() -> None:
    kernel = ideology_kernel_from_dict(_minimal_kernel_dict())
    assert isinstance(kernel, IdeologyKernel)
    assert kernel.primary_motif.display_name == "天地不仁"
    assert kernel.secondary_roles() == {"action", "suspense"}
    assert kernel.covered_layers() == set(LAYER_KEYS)
    assert kernel.hidden_endgame_motif is not None
    assert kernel.hidden_endgame_motif.reveal_after_volume == 4


def test_kernel_backfills_layer_coverage() -> None:
    kernel = ideology_kernel_from_dict(_minimal_kernel_dict())
    # All four layers should be represented in the coverage map.
    assert set(kernel.layer_coverage.keys()) == set(LAYER_KEYS)


def test_kernel_tolerates_llm_aliases() -> None:
    # LLM emits drifting field names; the kernel must normalize them, not crash.
    payload = {
        "premise": "天地无私。",
        "thesis": "活着本身就是代价。",
        "dramatic_question": "活得久就活得好吗？",
        "primary_motif": {
            "key": "changsheng_zhouzhou",
            "name": "长生是诅咒",
            "layer": "伦理反转",  # Chinese alias
            "thesis": "长生是刑罚。",
            "question": "时间延长后还剩什么？",
            "symbols": ["刻满忌日的木牌"],
        },
        "belief_arc": {
            "initial": "把长生当奖励。",
            "shatter": "故人换了三代。",
            "reconstruction": "在有限里找锚点。",
        },
        # No cost_system at all — must be synthesized, not crash.
    }
    kernel = ideology_kernel_from_dict(payload)
    assert kernel.primary_motif.layer == "ethical_reversal"
    assert kernel.cost_system, "empty cost_system must be backfilled"
    assert kernel.thesis_statement == "活着本身就是代价。"


def test_kernel_drops_unsalvageable_secondary_entries() -> None:
    payload = _minimal_kernel_dict()
    payload["secondary_motifs"].append({"garbage": "no usable fields"})  # malformed
    # Should not raise — the bad entry is dropped, good entries survive.
    kernel = ideology_kernel_from_dict(payload)
    # The malformed entry has no real layer/thesis; it is either dropped or
    # normalized to a custom binding — either way the kernel validates.
    assert len(kernel.secondary_motifs) >= 2


def test_render_kernel_prompt_block_contains_spine() -> None:
    kernel = ideology_kernel_from_dict(_minimal_kernel_dict())
    block = render_ideology_kernel_prompt_block(kernel)
    assert "主主题" in block
    assert "核心问题" in block
    assert "信念弧" in block
    assert "代价系统" in block
    assert "天地不仁" in block
    assert "禁用的廉价解法" in block


def test_render_kernel_prompt_block_handles_dict_and_none() -> None:
    assert render_ideology_kernel_prompt_block(None) == ""
    block = render_ideology_kernel_prompt_block(_minimal_kernel_dict())
    assert "主主题" in block
    # Malformed dict must not raise.
    assert render_ideology_kernel_prompt_block({"garbage": 1}) == ""


def test_kernel_carries_sub_themes() -> None:
    payload = _minimal_kernel_dict()
    payload["sub_themes"] = [
        {"proposition": "天赋是借来的，迟早连本带利还回去。", "motif_key": "daijia", "layer": "subject_choice"},
        "知道得越多，能信的人越少。",  # bare string also accepted
    ]
    kernel = ideology_kernel_from_dict(payload)
    assert len(kernel.sub_themes) == 2
    assert kernel.sub_themes[0].motif_key == "daijia"
    assert kernel.sub_themes[1].proposition  # string coerced
    block = render_ideology_kernel_prompt_block(kernel)
    assert "穿插子题" in block


def test_binding_requires_action_and_suspense_roles_only_on_secondary() -> None:
    binding = MotifBinding.model_validate(
        {
            "motif_key": "nitian",
            "display_name": "逆天",
            "layer": "subject_choice",
            "book_thesis": "规则不公时反抗就是意义。",
            "book_core_question": "什么时候必须翻桌？",
            "role": "行动",  # Chinese alias → action
        }
    )
    assert binding.role == "action"


# --- 纯正爽文·代价强度三档 (P1a) --------------------------------------------


class TestCostStyleTiers:
    """standard 逐字节兼容旧渲染 + external/minimal 变体 + schema/accessor。"""

    def _kernel(self, cost_style: str = "standard") -> IdeologyKernel:
        from bestseller.services.ideology_kernel import fallback_ideology_kernel

        fb = fallback_ideology_kernel(
            premise="少年得神秘传承闯仙界", volumes=3, seed="cs-seed"
        )
        k = ideology_kernel_from_dict(fb)
        return k if cost_style == "standard" else k.model_copy(
            update={"cost_style": cost_style}
        )

    def test_standard_preserves_legacy_cost_block(self) -> None:
        block = render_ideology_kernel_prompt_block(self._kernel("standard"))
        assert "### 代价系统（力量/真相/救赎都必须付费, 不可白给）" in block
        assert "→ 代价「" in block
        assert "外置代价" not in block and "极简代价" not in block

    def test_external_externalizes_and_forbids_self_harm(self) -> None:
        block = render_ideology_kernel_prompt_block(self._kernel("external"))
        assert "外置代价" in block
        assert "禁止：削减主角的记忆/身体/关系/寿命/地位" in block
        assert "力量/真相/救赎都必须付费" not in block

    def test_minimal_softens_cost(self) -> None:
        block = render_ideology_kernel_prompt_block(self._kernel("minimal"))
        assert "极简代价" in block
        assert "不写削弱主角的代价账" in block

    def test_cost_style_field_defaults_standard_and_round_trips(self) -> None:
        k = self._kernel("standard")
        assert k.cost_style == "standard"
        assert k.model_copy(update={"cost_style": "external"}).cost_style == "external"

    def test_derive_prompt_standard_is_byte_identical(self) -> None:
        from bestseller.services.ideology_kernel import build_ideology_user_prompt

        base = build_ideology_user_prompt(premise="x", volumes=1)
        assert build_ideology_user_prompt(
            premise="x", volumes=1, cost_style="standard"
        ) == base
        ext = build_ideology_user_prompt(premise="x", volumes=1, cost_style="external")
        assert "代价风格=外置" in ext and ext != base

    def test_selection_schema_and_accessor(self) -> None:
        from bestseller.services.story_enhancers import (
            STORY_ENHANCERS_METADATA_KEY,
            StoryEnhancerSelection,
            resolve_cost_style,
        )

        assert StoryEnhancerSelection(cost_style="external").cost_style == "external"
        assert StoryEnhancerSelection(cost_style="nope").cost_style == "standard"
        assert StoryEnhancerSelection(cost_style="EXTERNAL").cost_style == "external"
        # 不计入合同(is_empty)，但需持久化(非 default)。
        assert StoryEnhancerSelection(cost_style="external").is_empty() is True
        assert StoryEnhancerSelection(cost_style="external").is_default() is False
        assert StoryEnhancerSelection().is_default() is True
        assert resolve_cost_style(
            {STORY_ENHANCERS_METADATA_KEY: {"cost_style": "minimal"}}
        ) == "minimal"
        assert resolve_cost_style({}) == "standard"
