from __future__ import annotations

import pytest

from bestseller.services.novel_categories import (
    NovelCategoryResearch,
    get_novel_category,
    list_novel_categories,
    load_novel_category_registry,
    render_category_anti_patterns,
    render_category_challenge_evolution_summary,
    render_category_reader_promise,
    resolve_novel_category,
)


pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Registry loading
# ---------------------------------------------------------------------------


def test_load_novel_category_registry_returns_all_nine() -> None:
    registry = load_novel_category_registry()
    expected_keys = {
        "action-progression",
        "relationship-driven",
        "suspense-mystery",
        "strategy-worldbuilding",
        "esports-competition",
        "female-growth-ncp",
        "base-building",
        "eastern-aesthetic",
        "default",
    }
    assert expected_keys == set(registry.keys())


def test_list_novel_categories_matches_registry_count() -> None:
    categories = list_novel_categories()
    registry = load_novel_category_registry()
    assert len(categories) == len(registry)


def test_get_novel_category_existing_key() -> None:
    cat = get_novel_category("action-progression")
    assert cat is not None
    assert cat.key == "action-progression"
    assert cat.name == "动作升级类"


def test_get_novel_category_none_key() -> None:
    assert get_novel_category(None) is None


def test_get_novel_category_unknown_key() -> None:
    assert get_novel_category("nonexistent-category-xyz") is None


# ---------------------------------------------------------------------------
# Challenge evolution pathway validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "key",
    [
        "action-progression",
        "relationship-driven",
        "suspense-mystery",
        "strategy-worldbuilding",
        "esports-competition",
        "female-growth-ncp",
        "base-building",
        "eastern-aesthetic",
        "default",
    ],
)
def test_every_category_has_at_least_three_phases(key: str) -> None:
    cat = get_novel_category(key)
    assert cat is not None
    assert len(cat.challenge_evolution_pathway) >= 3, (
        f"{key} has only {len(cat.challenge_evolution_pathway)} phases"
    )


def test_action_progression_phases_order() -> None:
    cat = get_novel_category("action-progression")
    assert cat is not None
    phase_keys = [p.phase_key for p in cat.challenge_evolution_pathway]
    assert phase_keys == [
        "individual_survival",
        "faction_friction",
        "power_system_test",
        "world_threat",
        "transcendence",
    ]


def test_default_category_phases_order() -> None:
    cat = get_novel_category("default")
    assert cat is not None
    phase_keys = [p.phase_key for p in cat.challenge_evolution_pathway]
    assert phase_keys == ["introduction", "escalation", "crisis", "resolution"]


# ---------------------------------------------------------------------------
# Protagonist archetypes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "key",
    [
        "action-progression",
        "relationship-driven",
        "suspense-mystery",
        "strategy-worldbuilding",
        "esports-competition",
        "female-growth-ncp",
        "base-building",
        "eastern-aesthetic",
        "default",
    ],
)
def test_every_category_has_at_least_one_archetype(key: str) -> None:
    cat = get_novel_category(key)
    assert cat is not None
    assert len(cat.protagonist_archetypes) >= 1, (
        f"{key} has no protagonist archetypes"
    )


def test_action_progression_has_power_seeker_archetype() -> None:
    cat = get_novel_category("action-progression")
    assert cat is not None
    archetype_keys = {a.archetype_key for a in cat.protagonist_archetypes}
    assert "power_seeker" in archetype_keys


# ---------------------------------------------------------------------------
# World rule templates
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "key",
    [
        "action-progression",
        "relationship-driven",
        "suspense-mystery",
        "strategy-worldbuilding",
        "esports-competition",
        "female-growth-ncp",
        "base-building",
        "eastern-aesthetic",
        "default",
    ],
)
def test_every_category_has_world_rule_templates(key: str) -> None:
    cat = get_novel_category(key)
    assert cat is not None
    assert len(cat.world_rule_templates) >= 1, (
        f"{key} has no world rule templates"
    )


# ---------------------------------------------------------------------------
# Quality traps
# ---------------------------------------------------------------------------


def test_action_progression_has_critical_trap() -> None:
    cat = get_novel_category("action-progression")
    assert cat is not None
    critical_traps = [t for t in cat.quality_traps if t.severity == "critical"]
    assert len(critical_traps) >= 1


def test_default_has_missing_engine_trap() -> None:
    cat = get_novel_category("default")
    assert cat is not None
    trap_keys = {t.trap_key for t in cat.quality_traps}
    assert "missing_engine" in trap_keys


# ---------------------------------------------------------------------------
# Disqualifiers
# ---------------------------------------------------------------------------


def test_action_progression_has_fatal_disqualifier() -> None:
    cat = get_novel_category("action-progression")
    assert cat is not None
    fatal = [d for d in cat.disqualifiers if d.severity == "fatal"]
    assert len(fatal) >= 1


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


def test_resolve_with_keyword_xianxia() -> None:
    cat = resolve_novel_category("仙侠", "修仙升级")
    assert cat.key == "action-progression"


def test_resolve_with_keyword_romance() -> None:
    # "romance" bypasses infer_genre_preset (English keyword), hits keyword map
    cat = resolve_novel_category("romance", "contemporary")
    assert cat.key == "relationship-driven"


def test_resolve_with_keyword_mystery() -> None:
    cat = resolve_novel_category("悬疑推理", None)
    assert cat.key == "suspense-mystery"


def test_resolve_with_keyword_strategy() -> None:
    cat = resolve_novel_category("历史权谋", None)
    assert cat.key == "strategy-worldbuilding"


def test_resolve_with_keyword_esports() -> None:
    cat = resolve_novel_category("电竞", "游戏竞技")
    assert cat.key == "esports-competition"


def test_resolve_with_keyword_base_building() -> None:
    # "基建" bypasses infer_genre_preset (no preset match), hits keyword map
    cat = resolve_novel_category("基建", "领地经营")
    assert cat.key == "base-building"


def test_resolve_with_keyword_eastern_aesthetic() -> None:
    cat = resolve_novel_category("东方美学", "国风奇谭")
    assert cat.key == "eastern-aesthetic"


def test_resolve_unknown_genre_returns_default() -> None:
    cat = resolve_novel_category("完全未知的题材", "不存在的子类")
    assert cat.key == "default"


# ---------------------------------------------------------------------------
# Rendering helpers
# ---------------------------------------------------------------------------


def test_render_anti_patterns_zh() -> None:
    cat = get_novel_category("action-progression")
    assert cat is not None
    text = render_category_anti_patterns(cat, is_en=False)
    assert "必须避免的品类陷阱" in text
    assert "一票否决项" in text


def test_render_anti_patterns_en() -> None:
    cat = get_novel_category("action-progression")
    assert cat is not None
    text = render_category_anti_patterns(cat, is_en=True)
    assert "MUST AVOID" in text
    assert "DISQUALIFIERS" in text


def test_render_anti_patterns_empty_for_no_traps() -> None:
    empty = NovelCategoryResearch(key="test", name="test")
    assert render_category_anti_patterns(empty) == ""


def test_render_reader_promise_zh() -> None:
    cat = get_novel_category("action-progression")
    assert cat is not None
    text = render_category_reader_promise(cat, is_en=False)
    assert "读者承诺" in text
    assert len(text) > 20


def test_render_reader_promise_en() -> None:
    cat = get_novel_category("action-progression")
    assert cat is not None
    text = render_category_reader_promise(cat, is_en=True)
    assert "Reader Promise" in text


def test_render_challenge_evolution_summary_zh() -> None:
    cat = get_novel_category("suspense-mystery")
    assert cat is not None
    text = render_category_challenge_evolution_summary(cat, is_en=False)
    assert "挑战进化路径" in text
    # Should contain numbered phases
    assert "1." in text
    assert "2." in text


def test_render_challenge_evolution_summary_en() -> None:
    cat = get_novel_category("suspense-mystery")
    assert cat is not None
    text = render_category_challenge_evolution_summary(cat, is_en=True)
    assert "Challenge Evolution Pathway" in text


def test_render_challenge_evolution_summary_empty() -> None:
    empty = NovelCategoryResearch(key="test", name="test")
    assert render_category_challenge_evolution_summary(empty) == ""
