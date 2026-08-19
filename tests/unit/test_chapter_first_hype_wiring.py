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
    # 三段律豁免：爽点三拍不是车轱辘，去水规则不得作用于它们
    # （2026-08-19：8 章修订丢 5 个爽点的根因是这条规则冲突）
    assert "不是车轱辘" in text
    assert "优先级高于任何去水/去重规则" in text

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


def test_all_three_rewrite_channels_see_the_contract():
    """三条修订通道必须全部同见合同（2026-08-19 复发定罪）。

    首修只给了 chapter_rewrite（真机 23 次），而 deslop_revise（16 次）
    没有——盖戳照掉。chapter_rewrite_repair（19 次）复用同一 system prompt
    因此自动继承。「修在书不走的那条路上」的同形复发。
    """
    import inspect

    from bestseller.services import deslop_revise, pipelines, reviews

    assert "render_hype_preservation_block(chapter)" in inspect.getsource(reviews)
    ds = inspect.getsource(deslop_revise)
    assert "hype_preservation_block" in ds, "deslop system prompt 必须带保全块"
    pl = inspect.getsource(pipelines)
    assert "hype_preservation_block=render_hype_preservation_block(" in pl, (
        "deslop 调用点必须传本章合同"
    )


@pytest.mark.asyncio
async def test_refresh_keeps_recipe_when_type_still_matches():
    """观测类型==计划类型时保留配方身份（2026-08-19：配方8→2而盖戳稳7）。

    重生成后爽点还在、配方键却被观测路径抹成 NULL，「计划了什么 vs
    实际有什么」就再也对不上账——审计能力自损。
    """
    from bestseller.services.drafts import stamp_chapter_hype

    text = (
        "他一步不退。" * 30
        + "满堂宾客看着长老僵住，脸色铁青，这一记当众打脸来得又快又狠，"
        "谁都没想到废柴会赢。他把令牌拍在案上，无人再敢出声。"
    )
    chapter = _FakeChapter(
        assigned={"type": "face_slap", "recipe_key": "仙侠-宗门打脸", "intensity": 8.0}
    )
    chapter.hype_type = "face_slap"
    chapter.hype_recipe_key = "仙侠-宗门打脸"
    await stamp_chapter_hype(
        _FakeSession(),
        chapter=chapter,
        chapter_number=3,
        content_md=text,
        project=None,
        scene_drafts=(),
        refresh=True,
    )
    assert chapter.hype_type == "face_slap"
    assert chapter.hype_recipe_key == "仙侠-宗门打脸", "同类型重算必须保住配方身份"


def test_chapter_first_regen_also_carries_preservation():
    """整章重生成路径同样注入保全块（2026-08-19 真机 ch3/ch4 掉戳）。

    合同(hype_constraints_block)在 prompt 里，模型重写时仍没兑现——
    注意力被「修上一稿的问题」占满，结算段被挤掉。已盖戳=上一稿写出
    过爽点，新稿必须同样写出。首次生成(未盖戳)不注入，避免空喊。
    """
    import inspect

    from bestseller.services import drafts

    src = inspect.getsource(drafts.build_chapter_first_draft_prompts)
    assert "render_hype_preservation_block(chapter)" in src
