from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from bestseller.services.methodology_cards import load_methodology_cards
from bestseller.services.methodology_profile import (
    enabled_cards,
    gate_mode_for_card,
    load_methodology_profile,
    load_profile_deck,
    render_methodology_profile_block,
    validate_methodology_profile,
)
from bestseller.services.quality_gates_config import load_quality_gates_config

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
PLOVA_PROFILE = REPO_ROOT / "config" / "methodology_profiles" / "plova_structured_writing_v1.yaml"


def _write_yaml(path: Path, content: str) -> Path:
    path.write_text(dedent(content).strip() + "\n", encoding="utf-8")
    return path


def test_plova_profile_loads_and_validates_against_card_deck() -> None:
    profile = load_methodology_profile(PLOVA_PROFILE)
    deck = load_profile_deck(profile)

    assert validate_methodology_profile(profile, deck) == ()
    assert len(profile.pending_sources) == 3
    assert len(profile.cards) == 35
    assert gate_mode_for_card(profile, "plova.opening.three_chapter_function") == "audit_only"


def test_enabled_cards_filters_by_stage_scope_and_priority() -> None:
    profile = load_methodology_profile(PLOVA_PROFILE)
    deck = load_methodology_cards(REPO_ROOT / profile.card_deck)

    opening_cards = enabled_cards(profile, deck, stage="review", scope="chapter")
    opening_ids = [card.id for card in opening_cards[:4]]

    assert opening_ids[0] == "plova.opening.three_chapter_function"
    assert "plova.opening.anti_pitfall" in opening_ids
    assert all("chapter" in card.scope for card in opening_cards)
    assert all("review" in card.stage for card in opening_cards)


def test_render_methodology_profile_block_is_short_and_stage_scoped() -> None:
    profile = load_methodology_profile(PLOVA_PROFILE)
    deck = load_profile_deck(profile)

    block = render_methodology_profile_block(
        profile,
        deck,
        stage="drafting",
        scope="scene",
        language="zh-CN",
        max_cards=2,
    )

    assert "方法论 profile" in block
    assert "plova.action_scene.structure" in block
    assert "必填合约" in block
    assert block.count("\n- ") == 2


def test_validate_profile_reports_unknown_card(tmp_path: Path) -> None:
    path = _write_yaml(
        tmp_path / "profile.yaml",
        """
        profile_id: test_profile
        title: "Test profile"
        source_set_id: test_source
        card_deck: data/methodology_sources/plova/cards.yaml
        default_mode: warn
        cards:
          plova.missing.card:
            enabled: true
        """,
    )
    profile = load_methodology_profile(path)
    deck = load_methodology_cards(REPO_ROOT / "data" / "methodology_sources" / "plova" / "cards.yaml")

    findings = validate_methodology_profile(profile, deck)

    assert findings[0].code == "METHODOLOGY_PROFILE_CARD_MISSING"
    assert findings[0].card_id == "plova.missing.card"


def test_quality_gate_config_loads_methodology_framework_knobs(tmp_path: Path) -> None:
    path = _write_yaml(
        tmp_path / "quality_gates.yaml",
        """
        methodology_framework:
          enabled: true
          profile_id: plova_structured_writing_v1
          cards:
            enabled: true
            data_dir: data/methodology_sources/plova
          opening_three_function:
            enabled: true
            default: audit_only
            block_until_chapter: 3
          action_scene_structure:
            enabled: false
            default: audit_only
          chekhov_emphasis:
            enabled: true
            default: warn
            overdue_window_default: 6
          longform_chaos:
            enabled: true
            start_after_chapter: 40
        """,
    )

    cfg = load_quality_gates_config(path).methodology_framework

    assert cfg.enabled is True
    assert cfg.profile_id == "plova_structured_writing_v1"
    assert cfg.action_scene_structure_enabled is False
    assert cfg.chekhov_emphasis_default == "warn"
    assert cfg.chekhov_overdue_window_default == 6
    assert cfg.longform_chaos_start_after_chapter == 40
