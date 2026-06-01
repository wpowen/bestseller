from __future__ import annotations

import pytest

from bestseller.services.prompt_packs import get_prompt_pack, render_methodology_block

pytestmark = pytest.mark.unit


def test_render_methodology_block_uses_yaml_fallback_for_sparse_pack() -> None:
    pack = get_prompt_pack("cozy-mystery")
    assert pack is not None
    block = render_methodology_block(pack, phase="scene")
    assert block
    assert "写法方法论" in block or "emotion" in block.lower() or "情绪" in block
