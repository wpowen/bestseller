# ruff: noqa: E501, RUF001
"""番茄短故事 × 母题/故事内核融合（第一梯队）单元测试。

覆盖确定性部分：compact 提取、信念弧→段落映射、场景内核注入、向后兼容。
LLM 依赖部分（derive_ideology_kernel / litstyle 判官）在 pipeline 层以 try/except
降级，不在此处断言。
"""

from __future__ import annotations

import pytest

from bestseller.domain.fanqie_short import FanqieShortBeat, FanqieShortBeatSheet
from bestseller.services.fanqie_short_planner import (
    build_fanqie_segment_outline_batch,
    build_short_ideology_compact,
    derive_short_thesis_vectors,
    render_short_ideology_scene_block,
)

pytestmark = pytest.mark.unit


def _project(target_chapters: int = 6, target_words: int = 15_000):
    return type(
        "P",
        (),
        {
            "target_chapters": target_chapters,
            "target_word_count": target_words,
            "slug": "fanqie-ideology-test",
            "title": "内核融合测试",
            "language": "zh-CN",
            "genre": "悬疑",
            "sub_genre": "",
            "metadata_json": {"pov": "first_person"},
        },
    )()


_COMPACT = {
    "thesis_statement": "真相需要有人付出代价去守护",
    "core_question": "为了真相，你愿意失去什么？",
    "belief_arc": {
        "initial": "正义会自动到来",
        "shatter": "体制本身在掩盖真相",
        "reconstruction": "正义要靠人主动承担代价换取",
    },
    "primary_motif": {
        "key": "ethical_reversal_sacrifice",
        "display_name": "牺牲",
        "thesis": "守护真相必须自损",
        "symbols": ["褪色的警徽"],
    },
    "sub_themes": ["沉默也是一种共谋"],
    "cost_laws": [{"acquires": "关键证据", "costs": "暴露自身身份"}],
    "forbidden": ["天降贵人替主角承担后果"],
}


# ---------- 信念弧 → 段落映射 ----------


def test_derive_thesis_vectors_maps_belief_arc_endpoints() -> None:
    vectors = derive_short_thesis_vectors(6, 2, _COMPACT)
    assert "建立初始信念" in vectors[1]
    assert "正义会自动到来" in vectors[1]
    assert "重建新信念" in vectors[6]
    assert "正义要靠人主动承担代价换取" in vectors[6]
    # 中点必须出现碎裂
    shatter_segments = [s for s, v in vectors.items() if "信念碎裂" in v]
    assert shatter_segments, vectors


def test_derive_thesis_vectors_empty_without_arc() -> None:
    assert derive_short_thesis_vectors(6, 2, None) == {}
    assert derive_short_thesis_vectors(6, 2, {}) == {}
    assert derive_short_thesis_vectors(6, 2, {"belief_arc": {}}) == {}


def test_derive_thesis_vectors_covers_every_segment() -> None:
    for n in (4, 5, 6, 8):
        vectors = derive_short_thesis_vectors(n, 2, _COMPACT)
        assert set(vectors) == set(range(1, n + 1))


# ---------- compact 提取（容错） ----------


def test_build_short_ideology_compact_none_and_garbage() -> None:
    assert build_short_ideology_compact(None) == {}
    # 不合法的 dict 不应抛错，降级为空
    assert build_short_ideology_compact({"not": "a kernel"}) == {}


def test_build_short_ideology_compact_from_real_kernel() -> None:
    pytest.importorskip("bestseller.services.ideology_kernel")
    from bestseller.services.ideology_kernel import fallback_ideology_kernel

    # 确定性 fallback 返回 schema-valid 的 IdeologyKernel payload(dict)。
    kernel_payload = fallback_ideology_kernel(
        premise="侧写师为洗清污名追查一桩被掩盖的旧案。",
        title="短篇内核融合测试",
    )
    compact = build_short_ideology_compact(kernel_payload)
    assert compact["thesis_statement"]
    assert compact["core_question"]
    assert set(compact["belief_arc"]) == {"initial", "shatter", "reconstruction"}
    assert compact["primary_motif"]["key"]
    # 短篇精简：副母题/隐藏终局不应进入 compact
    assert "secondary_motifs" not in compact
    assert "hidden_endgame_motif" not in compact
    assert len(compact["cost_laws"]) <= 2
    assert len(compact["sub_themes"]) <= 2


# ---------- 场景内核渲染 ----------


def test_render_scene_block_empty_when_no_ideology() -> None:
    assert render_short_ideology_scene_block({}) == ""
    assert render_short_ideology_scene_block(None) == ""


def test_render_scene_block_contains_spine_and_guard() -> None:
    block = render_short_ideology_scene_block(_COMPACT, thesis_vector="信念碎裂：体制本身在掩盖真相")
    assert "主主题" in block
    assert "核心问题" in block
    assert "代价律" in block
    assert "禁止无代价开挂" in block
    assert "严禁说教" in block  # 反说教守卫
    assert "本段信念位置" in block


# ---------- 端到端：注入到 outline batch ----------


def test_outline_injects_ideology_into_scene_contracts() -> None:
    project = _project()
    vectors = derive_short_thesis_vectors(6, 2, _COMPACT)
    beats = [
        FanqieShortBeat(
            segment_number=i,
            beat_role="rising",
            purpose=f"段{i}目的",
            thesis_vector=vectors[i],
        )
        for i in range(1, 7)
    ]
    sheet = FanqieShortBeatSheet(beats=beats, ideology_compact=_COMPACT)

    batch = build_fanqie_segment_outline_batch(
        project,
        sheet,
        cast_spec={"protagonist": {"name": "我"}},
    )

    assert len(batch["chapters"]) == 6
    for ch in batch["chapters"]:
        scene_mc = ch["scenes"][0]["methodology_contract"]
        assert "主主题" in scene_mc["fanqie_short_ideology"]
        assert scene_mc["thesis_vector"]
        assert ch["causal_contract"]["thesis_vector"]
        # 内核块不得污染对读者可见的故事字段
        assert "主主题" not in ch["chapter_goal"]
        assert "主主题" not in ch["scenes"][0]["purpose"]["story"]


def test_outline_backward_compatible_without_ideology() -> None:
    """无母题内核时，新增字段应为空且不报错（向后兼容）。"""
    project = _project(target_chapters=4, target_words=8_000)
    beats = [
        FanqieShortBeat(segment_number=i, beat_role="rising", purpose=f"段{i}目的")
        for i in range(1, 5)
    ]
    sheet = FanqieShortBeatSheet(beats=beats)  # 无 ideology_compact

    batch = build_fanqie_segment_outline_batch(project, sheet)

    assert len(batch["chapters"]) == 4
    for ch in batch["chapters"]:
        scene_mc = ch["scenes"][0]["methodology_contract"]
        assert scene_mc["fanqie_short_ideology"] == ""
        assert scene_mc["thesis_vector"] == ""
