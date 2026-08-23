"""整章模式的书从不产场景稿，却被按场景分母打三条 high 级发现。

真机（书 9，2026-08-24）：**88 张场景卡、0 份场景稿、190 份章节稿、379 条 canon**。
而项目级审稿这样算覆盖率：

    canon_coverage    = 场景摘要数 / 场景数   → 0/88
    timeline_coverage = 时间线事件 / 场景数   → 0/88

于是两条 high 永远成立 → 项目审稿永远 attention → requires_human_review → 顶层
machine_blocked。真机对账：项目级 10 份判决**全是 attention**，每份带 6-7 条 high。

这**不是标准太严，是量错了对象**：书里明明有 379 条 canon，指标在数场景摘要。
与我当天撤回的「类别契约降级」不同——那次是标准有设计而无实现，属设计决定；
这次是同一份数据被换了个分母，属类别错误。

判据用**真实的场景稿数**，不用模式标志：场景卡是规划产物，整章模式一样会有。
"""

from __future__ import annotations

# ruff: noqa: RUF001, RUF002, RUF003 — 中文标点是刻意的。
import pytest

from bestseller.services.consistency import evaluate_project_consistency
from bestseller.settings import load_settings

pytestmark = pytest.mark.unit


def _evaluate(**over):
    base = dict(
        settings=load_settings(env={}),
        chapter_count=42,
        chapter_draft_count=42,
        complete_chapter_count=2,
        chapter_numbers=list(range(1, 43)),
        scene_count=88,
        approved_scene_count=0,
        scene_summary_count=0,
        timeline_event_count=0,
        pending_rewrite_count=0,
        project_export_count=0,
        chapter_export_count=0,
    )
    base.update(over)
    return evaluate_project_consistency(**base)


def _categories(result, severity=None):
    return {
        f.category
        for f in result.findings
        if severity is None or f.severity == severity
    }


def test_a_chapter_first_book_is_not_judged_on_scene_coverage() -> None:
    """真机形状：0 份场景稿时，两条场景覆盖率不得再报发现。"""

    result = _evaluate(scene_pipeline_active=False)
    assert "canon_coverage" not in _categories(result)
    assert "timeline_coverage" not in _categories(result)


def test_a_scene_pipeline_book_is_still_judged() -> None:
    """真的在跑场景管线却没有摘要 —— 原样定罪，标准不动。"""

    result = _evaluate(scene_pipeline_active=True)
    assert "canon_coverage" in _categories(result)
    assert "timeline_coverage" in _categories(result)


def test_the_default_preserves_the_old_behaviour() -> None:
    """不传新参数时行为逐字不变（老调用方安全）。"""

    assert _categories(_evaluate()) == _categories(_evaluate(scene_pipeline_active=True))


def test_other_findings_are_untouched_by_the_guard() -> None:
    """只放过场景覆盖率两条，其余发现一条不少。"""

    on = _categories(_evaluate(scene_pipeline_active=True))
    off = _categories(_evaluate(scene_pipeline_active=False))
    assert on - off == {"canon_coverage", "timeline_coverage"}


def test_the_guard_reads_real_scene_drafts_not_a_mode_flag() -> None:
    """接线断言：判据必须来自场景**稿**计数，不能靠模式标志或场景卡数。"""

    import inspect

    from bestseller.services import consistency

    source = inspect.getsource(consistency)
    idx = source.index("scene_pipeline_active=")
    call = source[idx : idx + 80]
    assert "_scene_draft_count > 0" in call, call
    assert "SceneDraftVersionModel" in source
