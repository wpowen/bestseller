from __future__ import annotations

import pytest

from bestseller.services.methodology_bridge import render_phase_block
from bestseller.services.prompt_packs import get_prompt_pack

pytestmark = pytest.mark.unit


def test_planner_block_does_not_turn_suspense_taxonomy_into_opening_obligations() -> None:
    pack = get_prompt_pack("suspense-mystery")
    block = render_phase_block(pack, phase="planner")
    assert "SOURCE AUTHORITY" in block
    assert "opening_rules" not in block
    assert "黄金三章" not in block and "前 500 字" not in block


def test_planner_block_keeps_character_design_optional_and_source_bound() -> None:
    pack = get_prompt_pack("suspense-mystery")
    block = render_phase_block(pack, phase="planner")
    assert "可选写作指导" in block
    assert "character_design" not in block and "三重锚点" not in block


def test_planner_block_nonempty_for_supported_packs() -> None:
    for pack_name in ("suspense-mystery", "xianxia-upgrade-core", "epic-fantasy"):
        pack = get_prompt_pack(pack_name)
        block = render_phase_block(pack, phase="planner")
        assert block, f"planner block must be nonempty for {pack_name}"
