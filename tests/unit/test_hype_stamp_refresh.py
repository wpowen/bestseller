"""盖戳 refresh 模式——戳必须跟着当前稿走，爽点丢失必须留痕。

2026-08-17 真机定罪（玄幻书《丑石赖我求切开》）：整章重生成（长度/质量债触发
chapter_first 再跑）产出丢失爽点的新版并**无条件上位**（旧 current 直接翻
false，零比较），而旧守卫「hype_type 非空即跳过盖戳」让戳永不刷新——
20 个戳里 **11 个是幽灵**：ch18 六个版本 v1-v5 全有 status_jump，
v6 重写丢失后照样上位；ch14 v4 有 face_slap，v5 重写丢失后上位。

旧守卫的本意是防止 DiversityBudget 重复登记——那个目的必须保住，
但不该连带把戳冻结。refresh 模式把两件事拆开：
    * 重算出类型 → 更新三字段，**不**登记预算
    * 重算为 None 而旧戳在 → 清戳（数据诚实）+ metadata 留痕 + warning
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from bestseller.services.drafts import stamp_chapter_hype


# 词表分类器确定性命中 face_slap 的章尾（打脸/僵住/脸色铁青 ≥2 词）
_TEXT_WITH_HYPE = (
    "他一步不退。" * 40
    + "满堂宾客看着长老僵住，脸色铁青，这一记当众打脸来得又快又狠，"
    + "谁都没想到废柴会赢。他把令牌拍在案上，无人再敢出声。"
)

# 任何爽点词都不含的章尾
_TEXT_NO_HYPE = "他把水烧开，倒进壶里，看着窗外的雨。" * 60


class _FakeChapter:
    def __init__(self, hype_type=None, recipe=None, intensity=None):
        self.hype_type = hype_type
        self.hype_recipe_key = recipe
        self.hype_intensity = intensity
        self.metadata_json = {}


class _FakeSession:
    """stamp 在无 project 时不触预算/DB——session 只需存在。"""

    def add(self, *a, **k):  # pragma: no cover - not expected
        raise AssertionError("stamp 不应向 session add 对象")


@pytest.mark.asyncio
async def test_refresh_clears_ghost_stamp_and_records_regression():
    """幽灵戳场景：旧戳在、新稿读不出 → 清戳 + 留痕。"""

    chapter = _FakeChapter(hype_type="status_jump", recipe="仙侠-天骄榜登顶", intensity=0.8)
    await stamp_chapter_hype(
        _FakeSession(),
        chapter=chapter,
        chapter_number=18,
        content_md=_TEXT_NO_HYPE,
        project=None,
        scene_drafts=(),
        refresh=True,
    )
    assert chapter.hype_type is None, "幽灵戳未被清除"
    assert chapter.hype_recipe_key is None
    assert chapter.hype_intensity is None
    regs = chapter.metadata_json.get("hype_regressions")
    assert regs and regs[0]["lost_type"] == "status_jump", "爽点丢失没有留痕"


@pytest.mark.asyncio
async def test_refresh_updates_stamp_when_new_draft_still_delivers():
    """新稿仍有爽点 → 戳更新为新读数，不报回归。"""

    chapter = _FakeChapter(hype_type="status_jump", recipe=None, intensity=0.5)
    await stamp_chapter_hype(
        _FakeSession(),
        chapter=chapter,
        chapter_number=14,
        content_md=_TEXT_WITH_HYPE,
        project=None,
        scene_drafts=(),
        refresh=True,
    )
    assert chapter.hype_type is not None, "有爽点的新稿不该被清戳"
    assert "hype_regressions" not in chapter.metadata_json


@pytest.mark.asyncio
async def test_non_refresh_never_clears_existing_stamp():
    """非 refresh 调用（首次盖戳语义）读不出爽点时保持 NULL 语义不动旧值——
    不出现「首次盖戳把别人的戳清掉」的越权。"""

    chapter = _FakeChapter(hype_type="face_slap")
    await stamp_chapter_hype(
        _FakeSession(),
        chapter=chapter,
        chapter_number=3,
        content_md=_TEXT_NO_HYPE,
        project=None,
        scene_drafts=(),
        refresh=False,
    )
    assert chapter.hype_type == "face_slap", "非 refresh 模式越权清戳"
    assert "hype_regressions" not in chapter.metadata_json


@pytest.mark.asyncio
async def test_first_stamp_stays_null_on_no_hype():
    """首次盖戳、读不出爽点 → 保持 NULL（诚实信号），无留痕。"""

    chapter = _FakeChapter()
    await stamp_chapter_hype(
        _FakeSession(),
        chapter=chapter,
        chapter_number=1,
        content_md=_TEXT_NO_HYPE,
        project=None,
        scene_drafts=(),
    )
    assert chapter.hype_type is None
    assert "hype_regressions" not in chapter.metadata_json


@pytest.mark.asyncio
async def test_fallback_intensity_uses_0_to_10_scale():
    """量纲统一（2026-08-17）：兜底分类器的 intensity 必须与指派路径同为 0-10 制。

    曾经这里 ÷10 存成 0-1 制，而指派路径写 intensity_target（7.5）、消费方
    commercial_planning_readiness 按 `>= 7.0` 判黄金章强度——整本书 0.1-0.4，
    检查恒 False 空转。
    """

    from bestseller.services.hype_engine import classify_hype

    expected = classify_hype(_TEXT_WITH_HYPE, language="zh-CN", segment="tail")
    assert expected is not None, "测试文本必须能被分类器读出"
    _, expected_confidence = expected

    chapter = _FakeChapter()
    await stamp_chapter_hype(
        _FakeSession(),
        chapter=chapter,
        chapter_number=7,
        content_md=_TEXT_WITH_HYPE,
        project=None,
        scene_drafts=(),
    )
    assert chapter.hype_intensity == pytest.approx(float(expected_confidence)), (
        "兜底 intensity 应直接存 confidence（0-10 制），不得再 ÷10"
    )
    assert chapter.hype_intensity > 1.0, "0-1 制残留（÷10 又回来了）"
