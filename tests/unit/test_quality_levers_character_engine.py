"""Unit tests for ``quality_levers.character_engine``."""

from __future__ import annotations

import pytest

from bestseller.services.quality_levers.character_engine import (
    collect_forbidden_words,
    collect_forbidden_words_from_profiles,
    collect_signature_words,
    collect_signature_words_from_profiles,
    get_character_profile,
    load_character_engine,
    render_character_engine_profile_block,
    render_character_profile_block,
    synthesize_character_engine_profile,
)

pytestmark = pytest.mark.unit


def test_load_character_engine_returns_three_sample_profiles() -> None:
    config = load_character_engine()
    assert {"shen_qingya", "zhou_shensuan", "the_fourth_man"} <= set(config.sample_profiles.keys())


def test_shen_qingya_profile_has_voice_dna_and_signature() -> None:
    profile = get_character_profile("shen_qingya")
    assert profile is not None
    assert "按程序" in profile.voice_dna.signature_words
    assert "我感到" in profile.voice_dna.forbidden_words
    assert profile.signature_assets.phrase
    # confrontation chain populated
    chain = profile.unique_response_chain.get("confrontation_with_villain")
    assert chain is not None
    assert chain.step_1 and chain.step_2 and chain.step_3


def test_get_character_profile_returns_none_for_unknown() -> None:
    assert get_character_profile("ghost") is None
    assert get_character_profile("") is None


def test_render_character_profile_block_includes_voice_and_signature() -> None:
    block = render_character_profile_block(
        character_ids=("shen_qingya", "zhou_shensuan"),
        scene_stimulus="confrontation_with_villain",
    )
    assert "shen_qingya" in block
    assert "zhou_shensuan" in block
    assert "voice_dna" in block
    assert "signature" in block
    # Stimulus chain rendered for shen_qingya
    assert "三步反应链" in block


def test_render_character_profile_block_skips_unknown_chain() -> None:
    # zhou_shensuan has confrontation_with_villain but not "summer_picnic"
    block = render_character_profile_block(
        character_ids=("zhou_shensuan",),
        scene_stimulus="summer_picnic",
    )
    assert "zhou_shensuan" in block
    assert "三步反应链" not in block


def test_render_character_profile_block_empty_when_no_matches() -> None:
    assert (
        render_character_profile_block(
            character_ids=("ghost1", "ghost2"),
            scene_stimulus=None,
        )
        == ""
    )


def test_collect_signature_words_union_dedups() -> None:
    sigs = collect_signature_words(("shen_qingya", "zhou_shensuan"))
    assert "按程序" in sigs
    assert "上头" in sigs
    # de-dupe — no repeats
    assert len(set(sigs)) == len(sigs)


def test_collect_forbidden_words_union() -> None:
    forbidden = collect_forbidden_words(("shen_qingya",))
    assert "我感到" in forbidden
    assert "肯定" in forbidden


def test_synthesize_character_engine_profile_from_cast_spec_fields() -> None:
    profile = synthesize_character_engine_profile(
        {
            "name": "林澈",
            "role": "protagonist",
            "goal": "夺回被宗门冻结的灵田经营权",
            "fear": "再次把盟友拖进无法偿还的债务",
            "flaw": "习惯把所有风险独自扛下",
            "secret": "私下替旧同门承担灵契坏账",
            "voice_profile": {
                "speech_register": "克制的账房口吻",
                "verbal_tics": ["先把账算清"],
                "sentence_style": "短句利落型",
                "mannerisms": ["谈判前把账珠按颜色排好"],
            },
            "moral_framework": {
                "core_values": ["守约"],
                "lines_never_crossed": ["不伪造盟友意愿"],
            },
            "ip_anchor": {
                "quirks": ["清账前会把账珠按颜色排成三列"],
                "signature_objects": ["裂纹账珠"],
                "core_wound": "曾因替人隐瞒坏账失去公开信任",
            },
            "relationships": [
                {"character": "苏绾", "type": "ally", "tension": "授权与债务互相拉扯"}
            ],
        }
    )

    assert profile["source"] == "cast_spec_fusion"
    assert profile["want_vs_need"]["want"] == "夺回被宗门冻结的灵田经营权"
    assert "习惯把所有风险独自扛下" in profile["want_vs_need"]["need"]
    assert profile["signature_assets"]["object"] == "裂纹账珠"
    assert "先把账算清" in profile["voice_dna"]["signature_words"]
    assert profile["voice_dna"]["sentence_length_preference"] == "short"
    assert profile["relationship_memory"][0]["target_id"] == "苏绾"


def test_synthesize_character_engine_profile_merges_character_strategy() -> None:
    profile = synthesize_character_engine_profile(
        {
            "name": "林澈",
            "role": "protagonist",
            "goal": "用旧账法破解宗门灵契",
            "fear": "再次拖累盟友",
            "secret": "继承了原身的契约债",
            "relationships": [
                {"character": "苏绾", "type": "ally", "tension": "信任与债务互相拉扯"}
            ],
            "voice_profile": {"response_pattern_to_question": "先反问规则漏洞再给判断"},
        },
        character_strategy={
            "source": "distillation_character_intelligence",
            "required_axes": ["agency", "identity_pressure", "relationship_debt"],
            "state_variables": ["knowledge_asymmetry", "identity_debt"],
            "reader_reward_contracts": ["Strategic satisfaction from information advantage"],
            "agency_policy": {
                "must_act_within_chapters": 3,
                "default_problem_solving_modes": ["cross_system_rule_arbitrage"],
                "choice_with_cost_required": True,
                "forbidden_passive_modes": ["passive reception"],
            },
            "identity_pressure": {
                "required_external_pressure": True,
                "choice_axis": "predecessor_loyalty vs self_determination",
                "debt_sources": ["宿主身份债"],
            },
            "relationship_policy": {
                "reciprocal_commitment_required": True,
                "cost_or_promise_required": True,
                "track_axes": ["group_commitment"],
            },
            "dialogue_policy": {
                "exposition_through_conflict": True,
                "max_revelations_before_break": 3,
            },
            "risk_controls": ["Identity crisis cannot resolve only by reflection."],
        },
    )

    assert profile["strategy_source"] == "distillation_character_intelligence"
    assert profile["agency_policy"]["must_act_within_chapters"] == 3
    assert profile["agency_policy"]["default_problem_solving_modes"] == [
        "cross_system_rule_arbitrage"
    ]
    assert profile["identity_pressure"]["choice_axis"] == (
        "predecessor_loyalty vs self_determination"
    )
    assert profile["relationship_debt"]["active_relationships"][0]["target_id"] == "苏绾"
    assert profile["dialogue_function"]["max_revelations_before_break"] == 3
    assert profile["character_reward_contract"]["reader_rewards"] == [
        "Strategic satisfaction from information advantage"
    ]


def test_render_character_engine_profile_block_for_project_profiles() -> None:
    profile = synthesize_character_engine_profile(
        {
            "name": "林澈",
            "role": "protagonist",
            "goal": "夺回灵田",
            "voice_profile": {"verbal_tics": ["先把账算清"]},
            "ip_anchor": {"signature_objects": ["裂纹账珠"]},
        },
        character_strategy={
            "required_axes": ["agency"],
            "agency_policy": {
                "default_problem_solving_modes": ["knowledge_application"],
                "choice_with_cost_required": True,
            },
            "reader_reward_contracts": ["Strategic payoff"],
        },
    )

    block = render_character_engine_profile_block([profile])

    assert "character_engine 融合档案" in block
    assert "林澈" in block
    assert "want/need" in block
    assert "agency" in block
    assert "reward_contract" in block
    assert "反应链" in block


def test_collect_words_from_project_character_engine_profiles() -> None:
    profile = synthesize_character_engine_profile(
        {
            "name": "林澈",
            "goal": "夺回灵田",
            "voice_profile": {
                "verbal_tics": ["先把账算清"],
                "forbidden_words": ["我觉得"],
            },
            "ip_anchor": {"signature_objects": ["裂纹账珠"]},
        }
    )

    signature_words = collect_signature_words_from_profiles([profile, profile])
    forbidden_words = collect_forbidden_words_from_profiles([profile])

    assert signature_words.count("先把账算清") == 1
    assert "裂纹账珠" in signature_words
    assert forbidden_words == ("我觉得",)
