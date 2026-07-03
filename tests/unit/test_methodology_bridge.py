from __future__ import annotations

import pytest

from bestseller.services.methodology_bridge import (
    _MASTER_FALLBACK_BUILDERS,
    get_fragment,
    get_fragments_for_phase,
    render_phase_block,
)
from bestseller.services.prompt_packs import get_prompt_pack

pytestmark = pytest.mark.unit


def test_bridge_returns_pack_fragment_when_present() -> None:
    pack = get_prompt_pack("suspense-mystery")
    text = get_fragment(pack, phase="scene", fragment_key="dialogue_rules")
    assert text and "悬疑对话规则" in text


def test_bridge_falls_back_to_master_when_pack_missing() -> None:
    # 弹簧法兜底只给爽文向 pack；悬疑等非爽文 pack 不再被硬套压抑-打脸循环。
    pack = get_prompt_pack("xianxia-upgrade-core")
    text = get_fragment(pack, phase="prewrite", fragment_key="spring_model")
    assert text
    assert "compression" in text or "压缩" in text or "release" in text


def test_bridge_gates_shuangwen_emotion_fallback_for_non_shuangwen_packs() -> None:
    suspense = get_prompt_pack("suspense-mystery")
    assert get_fragment(suspense, phase="prewrite", fragment_key="spring_model") == ""
    assert get_fragment(None, phase="prewrite", fragment_key="spring_model") == ""
    comedy = get_prompt_pack("shezhu-bailan-comedy")
    # 沙雕喜剧没有自带 emotion_engineering，也绝不能兜底继承爽文压抑-羞辱循环。
    assert get_fragment(comedy, phase="scene", fragment_key="emotion_engineering") == ""


def test_master_fallback_works_for_all_registered_paths() -> None:
    """Every registered fallback must resolve to real master methodology text.

    爽文情绪键(emotion_engineering/spring_model)按 pack 门控，用爽文 pack 验证；
    其余键用 pack=None 验证兜底本身没有断链。
    """
    shuangwen_pack = get_prompt_pack("xianxia-upgrade-core")
    failures = []
    for (phase, key), yaml_path in _MASTER_FALLBACK_BUILDERS.items():
        gated = key in {"emotion_engineering", "spring_model"}
        text = get_fragment(shuangwen_pack if gated else None, phase=phase, fragment_key=key)
        if not text:
            failures.append(f"{phase}::{key} -> {yaml_path}")
    assert not failures, f"Broken master fallbacks: {failures}"


def test_planner_block_works_for_minimal_pack() -> None:
    """Sparse prompt packs still need a complete planner methodology block."""
    pack = get_prompt_pack("cozy-mystery")
    block = render_phase_block(pack, phase="planner")

    assert block
    for key in (
        "opening_rules",
        "character_design",
        "reversal_design",
        "climax_design",
        "core_loop",
    ):
        assert key in block


def test_bridge_returns_empty_when_neither_source_has_fragment() -> None:
    pack = get_prompt_pack("suspense-mystery")
    text = get_fragment(pack, phase="planner", fragment_key="nonexistent_fragment_xyz")
    assert text == ""


def test_get_fragments_for_phase_returns_all_available() -> None:
    pack = get_prompt_pack("suspense-mystery")
    fragments = get_fragments_for_phase(pack, phase="planner")
    assert "opening_rules" in fragments
    assert "character_design" in fragments
    assert "reversal_design" in fragments
    assert "climax_design" in fragments
    assert "core_loop" in fragments


def test_render_phase_block_produces_nonempty_for_planner() -> None:
    pack = get_prompt_pack("suspense-mystery")
    block = render_phase_block(pack, phase="planner")
    assert block
    assert "写法方法论指导" in block
    assert "opening_rules" in block or "开篇" in block
