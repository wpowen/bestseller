# ruff: noqa: E501, RUF001
"""番茄短故事 × 母题/故事内核融合（第一梯队）单元测试。

覆盖确定性部分：compact 提取、信念弧→段落映射、场景内核注入、向后兼容。
LLM 依赖部分（derive_ideology_kernel / litstyle 判官）在 pipeline 层以 try/except
降级，不在此处断言。
"""

from __future__ import annotations

from copy import deepcopy

import pytest
from test_ideology_cost_source_projection import author_metadata, cached

from bestseller.domain.fanqie_short import FanqieShortBeat, FanqieShortBeatSheet
from bestseller.services.creation_intent_contract import contract_from_payload
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


def authored_ideology():
    kernel = cached()
    fields = {
        key: deepcopy(kernel[key]) for key in
        ("thesis_statement", "core_question", "belief_arc", "primary_motif", "sub_themes")
    }
    metadata = author_metadata(ideology_fields=fields)
    metadata["story_design_kernel"] = {"ideology_kernel": deepcopy(kernel)}
    return kernel, metadata, build_short_ideology_compact(kernel, metadata=metadata)


# ---------- 信念弧 → 段落映射 ----------


def test_derive_thesis_vectors_maps_belief_arc_endpoints() -> None:
    kernel, metadata, compact = authored_ideology()
    vectors = derive_short_thesis_vectors(6, 2, compact)
    assert "建立初始信念" in vectors[1]
    assert kernel["belief_arc"]["initial_belief"] in vectors[1]
    assert "重建新信念" in vectors[6]
    assert kernel["belief_arc"]["final_reconstruction"] in vectors[6]
    # 中点必须出现碎裂
    shatter_segments = [s for s, v in vectors.items() if "信念碎裂" in v]
    assert shatter_segments, vectors


def test_derive_thesis_vectors_empty_without_arc() -> None:
    assert derive_short_thesis_vectors(6, 2, None) == {}
    assert derive_short_thesis_vectors(6, 2, {}) == {}
    assert derive_short_thesis_vectors(6, 2, {"belief_arc": {}}) == {}


def test_derive_thesis_vectors_covers_every_segment() -> None:
    _kernel, _metadata, compact = authored_ideology()
    for n in (4, 5, 6, 8):
        vectors = derive_short_thesis_vectors(n, 2, compact)
        assert set(vectors) == set(range(1, n + 1))


# ---------- compact 提取（容错） ----------


def test_build_short_ideology_compact_none_and_garbage() -> None:
    assert build_short_ideology_compact(None) == {}
    # 不合法的 dict 不应抛错，降级为空
    assert build_short_ideology_compact({"not": "a kernel"}) == {}


def test_build_short_ideology_compact_from_explicit_author_kernel() -> None:
    kernel_payload, metadata, compact = authored_ideology()
    assert compact["thesis_statement"] == kernel_payload["thesis_statement"]
    assert compact["core_question"] == kernel_payload["core_question"]
    assert set(compact["belief_arc"]) == {"initial", "shatter", "reconstruction"}
    assert compact["author_fields"]["primary_motif"] == kernel_payload["primary_motif"]
    assert "secondary_motifs" not in compact
    assert "hidden_endgame_motif" not in compact
    assert not compact.get("cost_laws")
    assert not compact.get("forbidden")
    assert compact["author_fields"]["sub_themes"] == kernel_payload["sub_themes"]


def test_legacy_full_cache_does_not_become_short_author_intent() -> None:
    kernel = cached()
    before = deepcopy(kernel)
    compact = build_short_ideology_compact(kernel)
    assert not compact.get("thesis_statement")
    assert not compact.get("primary_motif")
    assert not any((compact.get("belief_arc") or {}).values())
    assert derive_short_thesis_vectors(6, 2, compact) == {}
    assert render_short_ideology_scene_block(compact) == ""
    assert kernel == before


# ---------- 场景内核渲染 ----------


def test_render_scene_block_empty_when_no_ideology() -> None:
    assert render_short_ideology_scene_block({}) == ""
    assert render_short_ideology_scene_block(None) == ""


def test_render_scene_block_contains_only_current_authored_spine() -> None:
    kernel, metadata, compact = authored_ideology()
    vector = "信念碎裂：" + kernel["belief_arc"]["midpoint_shatter"]
    block = render_short_ideology_scene_block(compact, thesis_vector=vector, metadata=metadata)
    assert kernel["thesis_statement"] in block
    assert kernel["core_question"] in block
    assert kernel["primary_motif"]["display_name"] in block
    assert kernel["belief_arc"]["midpoint_shatter"] in block
    assert kernel["cost_system"][0]["costs"] not in block
    assert kernel["forbidden_resolutions"][0] not in block
    # The selected belief text survives, without imposing an extra fixed transition.
    assert "本段信念位置" not in block


# ---------- 端到端：注入到 outline batch ----------


def test_outline_preserves_selected_ideology_fields_without_rendering() -> None:
    project = _project()
    kernel, metadata, compact = authored_ideology()
    # This fixture owns its exact typed selection; it is not legacy paid proof.
    contract = contract_from_payload(metadata)
    metadata["creation_intent_contract_hash"] = contract.contract_hash()
    metadata["creation_input_receipt"] = {
        "schema_version": contract.schema_version,
        "contract_hash": contract.contract_hash(),
        "field_sources": deepcopy(metadata["creation_intent_contract"]["field_sources"]),
    }
    project.metadata_json.update(metadata)
    vectors = derive_short_thesis_vectors(6, 2, compact)
    beats = [
        FanqieShortBeat(
            segment_number=i,
            beat_role="rising",
            purpose=f"段{i}目的",
            thesis_vector=vectors[i],
        )
        for i in range(1, 7)
    ]
    sheet = FanqieShortBeatSheet(beats=beats, ideology_compact=compact)
    before = deepcopy((project.metadata_json, sheet.model_dump(mode="json")))

    batch = build_fanqie_segment_outline_batch(
        project,
        sheet,
        cast_spec={"protagonist": {"name": "我"}},
    )

    assert len(batch["chapters"]) == 6
    for ch, beat in zip(batch["chapters"], beats, strict=True):
        scene_mc = ch["scenes"][0]["methodology_contract"]
        selected = scene_mc["selected_creation_contracts"]["creation_intent_contract"]["hook_spec"]["ideology_contract"]
        assert selected == metadata["creation_intent_contract"]["hook_spec"]["ideology_contract"]
        assert selected["fields"]["thesis_statement"] == kernel["thesis_statement"]
        assert "fanqie_short_ideology" not in scene_mc
        assert scene_mc["thesis_vector"] == beat.thesis_vector
        assert ch["causal_contract"]["thesis_vector"] == beat.thesis_vector
        assert ch["chapter_goal"] == beat.purpose
        # 内核块不得污染对读者可见的故事字段
        assert "主主题" not in ch["chapter_goal"]
        assert "主主题" not in ch["scenes"][0]["purpose"]["story"]
    assert (project.metadata_json, sheet.model_dump(mode="json")) == before


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
        assert "fanqie_short_ideology" not in scene_mc
        assert scene_mc["selected_creation_contracts"] == {}
        assert scene_mc["thesis_vector"] == ""


def test_stale_short_compact_does_not_survive_removed_author_selection():
    kernel, metadata, compact = authored_ideology()
    before = deepcopy((kernel, metadata, compact))
    assert render_short_ideology_scene_block(compact, metadata={}) == ""
    assert (kernel, metadata, compact) == before


def test_cost_only_short_direction_does_not_authorize_cached_theme_or_belief_arc():
    kernel = cached()
    metadata = author_metadata("minimal")
    metadata["story_design_kernel"] = {"ideology_kernel": kernel}
    compact = build_short_ideology_compact(kernel, metadata=metadata)
    assert not compact.get("thesis_statement")
    assert not any((compact.get("belief_arc") or {}).values())
    assert derive_short_thesis_vectors(6, 2, compact) == {}
    block = render_short_ideology_scene_block(compact, metadata=metadata)
    assert "极简代价" in block
    assert kernel["thesis_statement"] not in block
    assert kernel["cost_system"][0]["costs"] not in block
