"""Unit tests for ideology kernel derivation, coherence gate, and advisory judge.

All pure (no LLM / no DB): we exercise the deterministic fallback, the prompt
builders, the parser merge logic, the structural gate, and the scoring path.
"""

from __future__ import annotations

import json

import pytest

from bestseller.domain.ideology import LAYER_KEYS, ideology_kernel_from_dict
from bestseller.services.ideology_coherence_gate import (
    audit_ideology_outline_grounding,
    evaluate_ideology_kernel_coherence,
)
from bestseller.services.ideology_judge import (
    build_ideology_judge_system_prompt,
    build_ideology_judge_user_prompt,
    build_ideology_repair_directives,
    load_ideology_judge_config,
    score_ideology_from_judge_json,
)
from bestseller.services.ideology_kernel import (
    build_ideology_system_prompt,
    build_ideology_user_prompt,
    fallback_ideology_kernel,
    ideology_kernel_health_summary,
    parse_ideology_kernel,
)

pytestmark = pytest.mark.unit


# --- fallback kernel --------------------------------------------------------


@pytest.mark.parametrize(
    "premise",
    [
        "边城百年被天罚清洗，少年拜入仙门追查真相。",
        "刑警接手坠楼案，越查越牵连自己失踪的女儿。",
        "外卖员被高压电击成为气运借贷的节点。",
        "寒门小吏在大灾边郡发现救灾的敌人是官制。",
        "",
    ],
)
def test_fallback_kernel_is_always_valid_and_four_layered(premise: str) -> None:
    payload = fallback_ideology_kernel(premise=premise, volumes=8)
    kernel = ideology_kernel_from_dict(payload)  # must not raise
    assert kernel.covered_layers() == set(LAYER_KEYS)
    assert kernel.secondary_roles() == {"action", "suspense"}
    assert kernel.hidden_endgame_motif is not None
    assert len(kernel.cost_system) >= 2
    assert kernel.forbidden_resolutions
    assert kernel.per_volume_thesis_pressure
    assert kernel.sub_themes  # 主主题 + 子题 model


def test_fallback_same_genre_different_premises_get_different_themes() -> None:
    """HARD REQUIREMENT proof at the kernel level: genre is NOT in the seed, so
    same-genre books with different premises get different theses/spines."""

    xianxia_premises = [
        "边城百年被天罚清洗，少年拜入仙门追查真相。",
        "废弟子捡到一缕剑魂，被迫替死人完成复仇。",
        "守山人世代镇着一口古井，直到井里开始说话。",
        "她继承了一座没有香火的山神庙，神位上空无一物。",
    ]
    theses = set()
    spines = set()
    for p in xianxia_premises:
        # Same genre label passed, but it must NOT collapse them to one theme.
        k = ideology_kernel_from_dict(fallback_ideology_kernel(premise=p, genre="仙侠", volumes=8))
        theses.add(k.thesis_statement)
        spines.add(tuple(b.motif_key for b in k.all_motifs()))
    assert len(theses) >= 3, f"same-genre theses too homogeneous: {theses}"
    assert len(spines) >= 2, f"same-genre spines too homogeneous: {spines}"


def test_fallback_multi_volume_sets_hidden_reveal_slot() -> None:
    kernel = ideology_kernel_from_dict(fallback_ideology_kernel(genre="仙侠", volumes=10))
    assert kernel.hidden_endgame_motif is not None
    assert kernel.hidden_endgame_motif.reveal_after_volume is not None
    assert 1 <= kernel.hidden_endgame_motif.reveal_after_volume <= 10


def test_health_summary_shape() -> None:
    kernel = ideology_kernel_from_dict(fallback_ideology_kernel(genre="玄幻", volumes=5))
    summary = ideology_kernel_health_summary(kernel)
    assert summary["layer_count"] == 4
    assert set(summary["secondary_roles"]) == {"action", "suspense"}


# --- prompt builders --------------------------------------------------------


def test_derivation_prompts_are_nonempty_and_mention_layers() -> None:
    sys_p = build_ideology_system_prompt(language="zh")
    usr_p = build_ideology_user_prompt(premise="一座边城百年被天罚清洗。", genre="仙侠", volumes=6)
    assert "IdeologyKernel" in sys_p
    assert "宇宙秩序" in usr_p
    assert "fallback" in usr_p.lower() or "最低结构" in usr_p


# --- parser merge -----------------------------------------------------------


def test_parse_kernel_merges_partial_llm_output_with_fallback() -> None:
    partial = json.dumps(
        {
            "thesis_statement": "边城的天罚只是收割，没有谁是无辜的。",
            "core_question": "若天罚只是收割，人还该不该护这座城？",
        }
    )
    kernel = parse_ideology_kernel(partial, genre="仙侠", volumes=6)
    # LLM fields kept...
    assert kernel.thesis_statement == "边城的天罚只是收割，没有谁是无辜的。"
    # ...missing required structure backfilled from the fallback (still valid).
    assert kernel.covered_layers() == set(LAYER_KEYS)


def test_parse_kernel_falls_back_on_garbage() -> None:
    kernel = parse_ideology_kernel("not json at all", genre="悬疑", volumes=3)
    assert kernel.covered_layers() == set(LAYER_KEYS)


# --- coherence gate ---------------------------------------------------------


def test_coherence_gate_passes_on_complete_fallback() -> None:
    kernel = ideology_kernel_from_dict(fallback_ideology_kernel(genre="仙侠", volumes=8))
    verdict = evaluate_ideology_kernel_coherence(kernel, volumes=8)
    assert verdict.verdict == "pass"
    assert verdict.coverage == 1.0
    assert verdict.critical_count == 0


def test_coherence_gate_is_advisory_not_blocking_on_missing_layer() -> None:
    # Strip to a single-layer kernel: only the primary remains.
    payload = fallback_ideology_kernel(genre="仙侠", volumes=4)
    payload["secondary_motifs"] = []
    payload["hidden_endgame_motif"] = None
    payload["layer_coverage"] = {}
    kernel = ideology_kernel_from_dict(payload)
    verdict = evaluate_ideology_kernel_coherence(kernel, volumes=4)
    # Advisory by default: warns, never blocks, even with a critical missing layer.
    assert verdict.verdict == "warn_only"
    assert verdict.required is False
    assert any(f.code == "ideology_missing_layer" for f in verdict.findings)


def test_coherence_gate_can_hard_block_when_required() -> None:
    payload = fallback_ideology_kernel(genre="仙侠", volumes=4)
    payload["secondary_motifs"] = []
    payload["hidden_endgame_motif"] = None
    payload["layer_coverage"] = {}
    kernel = ideology_kernel_from_dict(payload)
    verdict = evaluate_ideology_kernel_coherence(kernel, volumes=4, required=True)
    assert verdict.verdict == "blocked"


def test_coherence_gate_handles_invalid_payload() -> None:
    verdict = evaluate_ideology_kernel_coherence({"garbage": 1}, volumes=2)
    assert verdict.verdict == "warn_only"
    assert any(f.code == "ideology_kernel_invalid" for f in verdict.findings)


# --- grounding audit --------------------------------------------------------


def test_grounding_audit_detects_present_symbols_and_thesis() -> None:
    kernel = ideology_kernel_from_dict(fallback_ideology_kernel(genre="仙侠", volumes=4))
    # Ground several of the kernel's concrete symbols so coverage is real, not 1/12.
    symbols = list(kernel.primary_motif.concrete_symbols)
    for b in kernel.secondary_motifs:
        symbols.extend(b.concrete_symbols)
    grounded = "、".join(dict.fromkeys(symbols))  # all symbols appear in the outline
    thesis = kernel.thesis_statement
    outline = (
        f"第一卷：村庄祭天，{grounded}。主角相信庇佑，却目睹覆灭。"
        f"主题：{thesis} 主角每救一人都要付出代价、折寿偿还。"
    )
    g = audit_ideology_outline_grounding(kernel, outline)
    assert g.symbol_hits >= 3
    assert g.thesis_keyword_hits > 0
    assert g.cost_language_present is True
    assert "low_symbol_grounding" not in g.flagged


def test_grounding_audit_flags_empty_outline() -> None:
    kernel = ideology_kernel_from_dict(fallback_ideology_kernel(genre="仙侠", volumes=4))
    g = audit_ideology_outline_grounding(kernel, "主角升级打怪，一路碾压，无需多言。")
    assert "no_cost_language" in g.flagged


# --- judge scoring path -----------------------------------------------------


def test_judge_system_prompt_lists_all_dimensions() -> None:
    config = load_ideology_judge_config()
    assert len(config.dimensions) == 9
    assert config.base_score_max == 100
    sys_p = build_ideology_judge_system_prompt()
    for dim in config.dimensions:
        assert dim.key in sys_p


def test_judge_user_prompt_embeds_kernel_and_outline() -> None:
    kernel = fallback_ideology_kernel(genre="仙侠", volumes=4)
    usr = build_ideology_judge_user_prompt(kernel=kernel, outline_text="第一卷大纲……")
    assert "核心理念内核" in usr
    assert "待评大纲" in usr


def test_score_path_recomputes_final_and_clamps() -> None:
    kernel = fallback_ideology_kernel(genre="仙侠", volumes=4)
    # Model over-reports a dimension above its max and a low penalty.
    raw = json.dumps(
        {
            "thesis_clarity": 99,  # max is 12 → must clamp
            "core_question_dramatization": 12,
            "belief_arc_integrity": 12,
            "cost_binding": 10,
            "layer_coverage_depth": 8,
            "cosmic_premise_consistency": 10,
            "anti_sloganization": 10,
            "hidden_motif_setup": 5,
            "commercial_compatibility": 6,
            "sloganization_penalty": 2,
            "evidence": ["卷一祭天覆灭", "代价折寿", "终局善恶反转"],
            "top_issues": ["第三卷代价偏弱"],
            "revision_priority": ["给第三卷的破境加一笔记忆代价"],
        }
    )
    result = score_ideology_from_judge_json(
        raw, kernel=kernel, outline_text="祭坛余烬……主角折寿救人……"
    )
    assert result.dimension_scores["thesis_clarity"] == 12  # clamped
    # base = 12+12+12+10+8+10+10+5+6 = 85; final = 85 - penalty
    assert result.base_score == 85
    assert result.final_score == result.base_score - result.sloganization_penalty
    assert 0 <= result.final_score <= 100
    assert result.level


def test_score_path_self_labels_unavailable_on_empty_json() -> None:
    kernel = fallback_ideology_kernel(genre="仙侠", volumes=4)
    result = score_ideology_from_judge_json("{}", kernel=kernel, outline_text="x")
    assert "IDEOLOGY_JUDGE_UNAVAILABLE" in result.top_issues


def test_story_design_kernel_carries_and_renders_ideology() -> None:
    """The ideology kernel must attach to StoryDesignKernel and propagate via its
    prompt block, while staying backward-compatible when absent."""

    from bestseller.services.story_design_kernel import (
        render_story_design_kernel_prompt_block,
        story_design_kernel_from_dict,
    )

    base = {
        "shape": {
            "length_class": "long",
            "publication_mode": "web_serial",
            "outline_depth": "chapter",
            "primary_duties": ["forward_pull"],
            "ending_contract": "close loop",
        },
        "reader_promise": "冷宇宙下凡人互救",
        "premise_contract": {
            "unique_hook": "天罚是收割",
            "core_question": "人为何还善",
            "commercial_pull": "反神权",
        },
        "character_conflict_contracts": [
            {
                "character_key": "lu",
                "external_goal": "救城",
                "internal_need": "放下",
                "pressure_source": "天罚",
                "choice_axis": "城vs门",
                "change_vector": "受难→自立",
            }
        ],
        "structure_strategy": {
            "macro_strategy": "a",
            "chapter_engine": "b",
            "pacing_rule": "c",
            "freshness_rule": "d",
        },
        "plot_tree": [
            {
                "key": "m",
                "line_type": "main",
                "label": "主线",
                "role": "核心",
                "current_state": "a",
                "target_state": "b",
                "failure_if_removed": "塌",
            }
        ],
        "beat_schedule": [
            {
                "chapter_range": "1-10",
                "duty": "a",
                "state_change": "b",
                "payoff": "c",
                "hook_or_aftereffect": "d",
            }
        ],
        "change_vectors": ["受难→自立"],
    }

    # With ideology: attaches + renders into the propagated prompt block.
    with_id = {**base, "ideology_kernel": fallback_ideology_kernel(genre="仙侠", volumes=8)}
    k = story_design_kernel_from_dict(with_id)
    assert k.ideology_kernel is not None
    block = render_story_design_kernel_prompt_block(k)
    assert "核心理念内核" in block and "主主题" in block

    # Without ideology: still valid, no ideology block (backward compatible).
    k2 = story_design_kernel_from_dict(base)
    assert k2.ideology_kernel is None
    assert "核心理念内核" not in render_story_design_kernel_prompt_block(k2)


def test_parse_backfills_partial_llm_structure() -> None:
    """Pilot-found logic gap: the LLM returned 1 secondary + no hidden. The parser
    must backfill the action+suspense pair + hidden from the fallback, not let the
    partial output degrade the spine."""

    partial = json.dumps(
        {
            "thesis_statement": "真相是分期账单，每看清一层就欠下一笔命。",
            "core_question": "你真的想把账单看到底吗？",
            "primary_motif": {
                "motif_key": "daijia", "display_name": "代价", "layer": "subject_choice",
                "book_thesis": "看真相要折寿", "book_core_question": "愿用寿命换真相吗",
            },
            "secondary_motifs": [
                {"motif_key": "zhenxiang", "display_name": "真相", "layer": "cognitive_crisis",
                 "book_thesis": "天罚是收割", "book_core_question": "真相是什么", "role": "suspense"},
            ],
            # NO hidden_endgame_motif, only 1 secondary (missing action role)
        }
    )
    kernel = parse_ideology_kernel(partial, premise="边城天罚追真相", volumes=8)
    assert kernel.thesis_statement.startswith("真相是分期账单")  # LLM content kept
    assert kernel.secondary_roles() == {"action", "suspense"}, "must backfill the missing action role"
    assert kernel.hidden_endgame_motif is not None, "must backfill the missing hidden motif"
    assert len(kernel.secondary_motifs) >= 2


def test_compact_ideology_block_carries_spine() -> None:
    from bestseller.domain.ideology import render_ideology_compact_block

    kernel = ideology_kernel_from_dict(fallback_ideology_kernel(premise="边城天罚", volumes=8))
    block = render_ideology_compact_block(kernel)
    assert "核心理念(必须贯彻)" in block
    assert "主主题" in block and "信念弧" in block and "代价" in block
    assert kernel.thesis_statement in block  # the actual thesis text is present
    assert render_ideology_compact_block(None) == ""


def test_gate_credits_cosmic_premise_for_noncosmic_primary() -> None:
    """Pilot-found over-flagging: a valid 代价/真相-led spine (primary not cosmic)
    covers cosmic via its cosmic_premise text — the gate must not flag it missing."""

    payload = fallback_ideology_kernel(premise="边城天罚", volumes=8)
    # Force a subject-layer primary (代价) + cognitive suspense + ethical hidden so
    # the motif bindings span subject/cognitive/ethical = 3 (no cosmic binding).
    payload["primary_motif"] = {
        "motif_key": "daijia", "display_name": "代价", "layer": "subject_choice",
        "book_thesis": "看真相要折寿", "book_core_question": "愿用寿命换真相吗",
    }
    payload["secondary_motifs"] = [
        {"motif_key": "nitian", "display_name": "逆天", "layer": "subject_choice",
         "book_thesis": "不公就反", "book_core_question": "何时翻桌", "role": "action"},
        {"motif_key": "zhenxiang", "display_name": "真相", "layer": "cognitive_crisis",
         "book_thesis": "天罚是收割", "book_core_question": "真相为何", "role": "suspense"},
    ]
    payload["hidden_endgame_motif"] = {
        "motif_key": "changsheng_zhouzhou", "display_name": "长生是诅咒", "layer": "ethical_reversal",
        "book_thesis": "长生是刑罚", "book_core_question": "为何还活", "reveal_after_volume": 5,
    }
    payload["cosmic_premise"] = "灵脉可被收割，收割之地百年一劫，天罚只是周期工程。"
    payload["layer_coverage"] = {}
    kernel = ideology_kernel_from_dict(payload)
    assert "cosmic_order" not in kernel.covered_layers()  # no cosmic MOTIF binding
    verdict = evaluate_ideology_kernel_coherence(kernel, volumes=8)
    assert not any(f.code == "ideology_missing_layer" for f in verdict.findings), (
        "substantive cosmic_premise should satisfy the cosmic layer"
    )


def test_repair_directives_from_revision_priority() -> None:
    kernel = fallback_ideology_kernel(genre="仙侠", volumes=4)
    raw = json.dumps(
        {
            "thesis_clarity": 6,
            "revision_priority": ["把第二卷的主题口号改成事件", "给金手指绑代价"],
            "top_issues": ["主题靠旁白喊"],
            "sloganization_penalty": 6,
        }
    )
    result = score_ideology_from_judge_json(raw, kernel=kernel, outline_text="...")
    directives = build_ideology_repair_directives(result)
    assert directives
    assert any("第二卷" in d for d in directives)
