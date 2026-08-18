"""chapter-first 爽点第一环接线锁（2026-08-18《九姓井口只认我》定罪）。

病灶：build_chapter_hype_blocks 只活在 run_scene_pipeline（场景模式），
chapter-first 书的写手 prompt 爽点约束恒为空、配方零落库（真机 8 章
hype_recipe_key 全 NULL）、盖戳只剩分类器兜底——8·16 四环断链
「能力长在书不走的那条路上」的残留环。

修：build_chapter_writer_context（chapter-first 的 packet 构建处）按场景
管线同参调用 build_chapter_hype_blocks，五个字段进 packet；下游两端
（写手 prompt drafts.py + 装配落库 generation_params→chapter 行）已有
消费代码，接上即活。fail-open：任何异常不阻断 packet 构建。
"""

from __future__ import annotations

import inspect

import pytest

from bestseller.services import context as context_services

pytestmark = pytest.mark.unit


def test_chapter_first_packet_builder_wires_hype_blocks():
    src = inspect.getsource(context_services.build_chapter_writer_context)
    assert "build_chapter_hype_blocks" in src, "chapter-first 必须调用同一分配器"
    for field in (
        "reader_contract_block=_hype_reader_contract",
        "hype_constraints_block=_hype_constraints",
        "assigned_hype_type=_hype_assigned_type",
        "assigned_hype_recipe_key=_hype_assigned_recipe_key",
        "assigned_hype_intensity=_hype_assigned_intensity",
    ):
        assert field in src, f"packet 字段未接线：{field}"
    # 与场景管线同参：预设空 scheme 时必须 no-op（写作预设关闭爽文融合的书）
    assert "hype_scheme.is_empty" in src


def test_packet_class_carries_all_five_fields():
    from bestseller.domain.context import ChapterWriterContextPacket

    fields = ChapterWriterContextPacket.model_fields
    for name in (
        "reader_contract_block",
        "hype_constraints_block",
        "assigned_hype_type",
        "assigned_hype_recipe_key",
        "assigned_hype_intensity",
    ):
        assert name in fields
