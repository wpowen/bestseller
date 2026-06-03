from __future__ import annotations

import pytest

from bestseller.services.anti_commonsense_hook import (
    build_hook_duplicate_risk_fn,
    build_hook_spec_from_mechanism,
    generate_hook_candidates,
)
from bestseller.services.anti_commonsense_mechanisms import list_mechanisms
from bestseller.services.genre_creativity import _FAMILY_TRAUMA_DISPLAY_RE


_LEGACY_EIGHT_KEYS = {
    "death_grows",
    "forced_loss",
    "emotion_value",
    "hide_anti_trope",
    "misunderstanding",
    "fourth_disaster",
    "rule_horror",
    "profession_reversal",
}

_REQUIRED_CN_KEYWORDS = (
    "签到",
    "炮灰",
    "神医",
    "读心",
    "追妻火葬场",
    "师尊",
    "替嫁",
    "冲喜",
    "锦鲤",
    "师徒反杀",
    "反派洗白",
    "真假千金",
    "群穿",
    "试炼",
    "剧本杀",
    "时间循环",
    "退婚",
    "休夫",
    "疯批",
    "病娇",
    "绿茶",
    "万人迷",
    "海王",
    "舔狗",
)


def test_mechanism_catalog_includes_legacy_eight_keys() -> None:
    mechanisms = list_mechanisms()
    keys = {item.key for item in mechanisms}

    assert len(mechanisms) >= 40
    assert _LEGACY_EIGHT_KEYS.issubset(keys)


@pytest.mark.parametrize("keyword", _REQUIRED_CN_KEYWORDS)
def test_mechanism_catalog_covers_required_cn_keyword(keyword: str) -> None:
    """Each required Chinese market trope term maps to at least one mechanism."""

    payload = " ".join(
        " ".join(
            [
                item.label,
                item.reversal_template,
                *item.base_desire_pool,
                *item.reward_pool,
                *item.cost_templates,
                *item.misunderstanding_patterns,
                *item.anti_cheat_rules,
                *item.arc_escalation_axes,
                *item.forbidden_overlaps,
            ]
        )
        for item in list_mechanisms()
    )
    assert keyword in payload, (
        f"CN keyword {keyword!r} is not covered by any mechanism label or field"
    )


def test_no_new_mechanism_field_matches_family_trauma_regex() -> None:
    """Guard against the family-trauma regex nuking any new mechanism's guardrail text."""

    for mechanism in list_mechanisms():
        searchable = " ".join(
            [
                mechanism.label,
                mechanism.reversal_template,
                *mechanism.base_desire_pool,
                *mechanism.reward_pool,
                *mechanism.cost_templates,
                *mechanism.misunderstanding_patterns,
                *mechanism.anti_cheat_rules,
                *mechanism.arc_escalation_axes,
                *mechanism.forbidden_overlaps,
            ]
        )
        assert not _FAMILY_TRAUMA_DISPLAY_RE.search(searchable), (
            f"Mechanism {mechanism.key!r} field matches family-trauma regex; "
            "rewrite to avoid 家庭/身世/失踪 etc."
        )


def test_every_mechanism_has_category_and_formula_affinity() -> None:
    """All mechanisms expose category and formula affinity for formula selection."""

    categories: dict[str, int] = {}
    for mechanism in list_mechanisms():
        assert mechanism.category, f"Mechanism {mechanism.key!r} missing category"
        assert mechanism.formula_affinity, f"Mechanism {mechanism.key!r} missing formula_affinity"
        categories[mechanism.category] = categories.get(mechanism.category, 0) + 1

    assert len(categories) >= 5, f"Expected ≥5 mechanism categories, got {sorted(categories)}"
    for category, count in categories.items():
        assert count >= 6, f"Category {category!r} only has {count} mechanisms (need ≥6)"


def test_generate_hook_candidates_is_deterministic_and_threshold_aware() -> None:
    first = generate_hook_candidates(genre="都市", count=3, seed=7)
    second = generate_hook_candidates(genre="都市", count=3, seed=7)

    assert [item.spec.one_liner for item in first] == [item.spec.one_liner for item in second]
    assert first
    assert first[0].score.h_norm >= 30
    assert first[0].spec.constraints
    assert first[0].spec.anti_cheat
    assert first[0].spec.costs


def test_all_mechanism_one_liners_avoid_broken_must_prefixes() -> None:
    forbidden = ("必须必须", "必须越", "必须最", "被迫越", "去被认可")

    for mechanism in list_mechanisms():
        spec = build_hook_spec_from_mechanism(mechanism, genre="都市")
        assert not any(token in spec.one_liner for token in forbidden), spec.one_liner
        assert spec.llm_design_brief
        assert spec.methodology_axes


def test_chinese_hook_spec_localizes_visible_methodology_axes() -> None:
    mechanism = list_mechanisms()[0]

    spec = build_hook_spec_from_mechanism(mechanism, genre="都市修真", variant_index=1)

    visible_axes = " ".join([*spec.arc_engine, *spec.methodology_axes])
    assert "deadline" not in visible_axes
    assert "countdown_threat" not in visible_axes
    assert "hook_lifecycle" not in visible_axes
    assert "倒计时" in visible_axes or "信息差" in visible_axes or "悬念" in visible_axes


def test_large_hook_batch_diversifies_mechanisms_and_sentence_shapes() -> None:
    candidates = generate_hook_candidates(genre="末日科幻", count=12, seed=7, min_h_norm=30)

    mechanism_keys = {item.spec.mechanism_key for item in candidates}
    styles = {item.spec.expression_style for item in candidates}
    one_liners = [item.spec.one_liner for item in candidates]

    assert len(candidates) == 12
    assert all(item.score.h_norm >= 30 for item in candidates)
    assert len(mechanism_keys) >= 6
    assert len(styles) >= 3
    assert len(set(one_liners)) == len(one_liners)
    assert not all(text.startswith("主角想") for text in one_liners)


def test_threshold_selection_prefers_passing_hook_when_available() -> None:
    candidates = generate_hook_candidates(genre="悬疑", count=6, seed=11, min_h_norm=30)
    game_candidates = generate_hook_candidates(genre="游戏", count=3, seed=11, min_h_norm=30)

    assert candidates
    assert all(item.score.h_norm >= 30 for item in candidates)
    assert game_candidates
    assert all(item.score.h_norm >= 30 for item in game_candidates)


def test_duplicate_risk_fn_marks_near_duplicate_and_affects_payload() -> None:
    baseline = generate_hook_candidates(genre="都市", count=6, seed=7, min_h_norm=30)
    duplicate_risk_fn = build_hook_duplicate_risk_fn([baseline[0].spec.one_liner])
    assert duplicate_risk_fn(baseline[0].spec) > 0

    reranked = generate_hook_candidates(
        genre="都市",
        count=6,
        seed=7,
        min_h_norm=30,
        duplicate_risk_fn=duplicate_risk_fn,
        rank_weights={"duplicate_risk": 0.8},
    )

    assert reranked
    assert reranked[0].spec.one_liner != baseline[0].spec.one_liner
    assert all(item.duplicate_risk < 1 for item in reranked)
