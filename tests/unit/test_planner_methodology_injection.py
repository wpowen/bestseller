from __future__ import annotations

import pytest

from bestseller.services.methodology_bridge import render_phase_block
from bestseller.services.prompt_packs import get_prompt_pack

pytestmark = pytest.mark.unit


def test_planner_block_contains_opening_rules_for_suspense() -> None:
    pack = get_prompt_pack("suspense-mystery")
    block = render_phase_block(pack, phase="planner")
    assert "opening_rules" in block or "开篇" in block
    assert "黄金三章" in block or "前 500 字" in block


def test_planner_block_contains_character_design() -> None:
    pack = get_prompt_pack("suspense-mystery")
    block = render_phase_block(pack, phase="planner")
    assert "character_design" in block or "角色" in block
    assert "三重锚点" in block or "动机" in block


def test_planner_block_nonempty_for_supported_packs() -> None:
    for pack_name in ("suspense-mystery", "xianxia-upgrade-core", "epic-fantasy"):
        pack = get_prompt_pack(pack_name)
        block = render_phase_block(pack, phase="planner")
        assert block, f"planner block must be nonempty for {pack_name}"
