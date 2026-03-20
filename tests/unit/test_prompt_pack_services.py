from __future__ import annotations

import pytest

from bestseller.services.prompt_packs import get_prompt_pack, list_prompt_packs, resolve_prompt_pack


pytestmark = pytest.mark.unit


def test_list_prompt_packs_returns_built_in_catalog() -> None:
    packs = list_prompt_packs()
    keys = {pack.key for pack in packs}

    assert "apocalypse-supply-chain" in keys
    assert "xianxia-upgrade-core" in keys
    assert "urban-power-reversal" in keys
    assert "romance-tension-growth" in keys


def test_resolve_prompt_pack_can_infer_from_genre() -> None:
    pack = resolve_prompt_pack(None, genre="末日科幻", sub_genre="重生囤货")

    assert pack is not None
    assert pack.key == "apocalypse-supply-chain"
    assert "囤货" in pack.description


def test_get_prompt_pack_returns_fragments() -> None:
    pack = get_prompt_pack("apocalypse-supply-chain")

    assert pack is not None
    assert "前三章" in (pack.fragments.planner_outline or "")
    assert "资源" in (pack.fragments.scene_writer or "")
