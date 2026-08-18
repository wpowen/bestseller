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


# ── 盖戳端兜底（2026-08-19 第二环）────────────────────────────────────────
# packet 有合同但章行配方键恒 NULL：盖戳只读 scene_drafts（chapter-first 为空）。
# 生成端把分配写进 chapter.metadata_json["assigned_hype"]，盖戳按场景路径
# 同语义（盖计划值）兜底。


class _FakeChapter:
    def __init__(self, assigned=None):
        self.hype_type = None
        self.hype_recipe_key = None
        self.hype_intensity = None
        self.metadata_json = {"assigned_hype": assigned} if assigned else {}


class _FakeSession:
    def add(self, *a, **k):  # pragma: no cover
        raise AssertionError("stamp 不应向 session add 对象")


@pytest.mark.asyncio
async def test_stamp_reads_chapter_level_assignment_when_no_scene_drafts():
    from bestseller.services.drafts import stamp_chapter_hype

    chapter = _FakeChapter(
        assigned={"type": "face_slap", "recipe_key": "仙侠-宗门打脸", "intensity": 8.0}
    )
    await stamp_chapter_hype(
        _FakeSession(),
        chapter=chapter,
        chapter_number=2,
        content_md="他把水烧开，倒进壶里。" * 60,
        project=None,
        scene_drafts=(),
        refresh=False,
    )
    assert chapter.hype_type == "face_slap", "计划值必须落章行（场景路径同语义）"
    assert chapter.hype_recipe_key == "仙侠-宗门打脸"
    assert chapter.hype_intensity == 8.0


@pytest.mark.asyncio
async def test_refresh_ignores_assignment_and_recomputes():
    # refresh=换稿重算：以正文观测为准，不回填计划值
    from bestseller.services.drafts import stamp_chapter_hype

    chapter = _FakeChapter(
        assigned={"type": "face_slap", "recipe_key": "仙侠-宗门打脸", "intensity": 8.0}
    )
    chapter.hype_type = "face_slap"
    chapter.hype_recipe_key = "仙侠-宗门打脸"
    chapter.hype_intensity = 8.0
    await stamp_chapter_hype(
        _FakeSession(),
        chapter=chapter,
        chapter_number=2,
        content_md="他把水烧开，倒进壶里。" * 60,
        project=None,
        scene_drafts=(),
        refresh=True,
    )
    assert chapter.hype_type is None, "新稿读不出爽点必须清戳（不许拿计划值糊）"
    assert (chapter.metadata_json.get("hype_regressions") or []), "清戳必须留痕"


def test_generation_writes_chapter_level_assignment():
    import inspect

    from bestseller.services import drafts

    src = inspect.getsource(drafts)
    assert '"assigned_hype"' in src and "assigned_hype" in src
    # 生成端写入点必须以 packet 为源、整字典重赋值（JSONB 变更追踪）
    assert '_hype_meta["assigned_hype"]' in src


# ── 修复通道爽点保全（2026-08-19 第三环）──────────────────────────────────
# 写手带合同写出结算段，重写 prompt 不知道合同把它改没（真机一轮修订
# 吃掉 3 个爽点，盖戳 14→11 全部留痕）。修复通道必须同见合同。


def test_hype_preservation_block_renders_from_stamp_or_assignment():
    from bestseller.services.drafts import render_hype_preservation_block

    stamped = _FakeChapter()
    stamped.hype_type = "face_slap"
    stamped.hype_recipe_key = "仙侠-宗门打脸"
    text = render_hype_preservation_block(stamped)
    assert "爽点保全" in text and "face_slap" in text and "仙侠-宗门打脸" in text

    planned = _FakeChapter(
        assigned={"type": "reversal", "recipe_key": None, "intensity": 7.0}
    )
    text2 = render_hype_preservation_block(planned)
    assert "reversal" in text2

    empty = _FakeChapter()
    assert render_hype_preservation_block(empty) == "", "无合同章不注入（防空喊）"


def test_chapter_rewrite_prompt_consumes_preservation_block():
    import inspect

    from bestseller.services import reviews

    src = inspect.getsource(reviews)
    assert "render_hype_preservation_block(chapter)" in src, "重写通道必须同见爽点合同"
