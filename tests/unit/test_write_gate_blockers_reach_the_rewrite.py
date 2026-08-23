"""能毙掉一章的码，必须也能教得动重写。

2026-08-24 真机（书 9）定罪：``POV_DRIFT`` 在 write_gate 的报告里以
``severity="block"`` 在**全部 26 章**开火（原话：POV declared as 'close_third'
but 8/40 sampled narrative sentences use the wrong person），而重写指令是用
**统一质量快照**的阻断项渲染的 —— 快照只带 9 个码，里面没有 POV_DRIFT。

后果：第 13/14/15/16/25 章整章第一人称出货（剥掉对白后叙述层「我」字 24-45 个，
其余 21 章 ≤5）。这 5 章一共 7 个重写任务，**没有一个**的指令提到人称；
``quality_repair_playbooks`` 里那条 POV_DRIFT 整改方案从来没被用上。

「能毙但不教」——与同日修掉的商业判官是同一个形状，也是本项目的元病
「同一事实住两地」：判定在一处，教学读的是另一处。

这个补丁只加教学文本，不改 verdict、不改 severity、不新增杀权。
"""

from __future__ import annotations

# ruff: noqa: RUF001, RUF002, RUF003 — 中文标点是刻意的。
from typing import Any
from uuid import uuid4

import pytest

from bestseller.domain.review import ChapterReviewResult, ChapterReviewScores
from bestseller.infra.db.models import ChapterModel
from bestseller.services import reviews as review_services

pytestmark = pytest.mark.unit


def _review(instructions: str = "") -> ChapterReviewResult:
    return ChapterReviewResult(
        verdict="rewrite",
        severity_max="high",
        # 按真实 schema 全字段填满——手写子集会被 pydantic 拒绝，
        # 而那正是「测试夹具与真机不同源」的入口。
        scores=ChapterReviewScores(
            **{name: 0.8 for name in ChapterReviewScores.model_fields}
        ),
        rewrite_instructions=instructions,
    )


class _Row:
    def __init__(self, payload: Any, created_at: Any) -> None:
        self.report_json = payload
        self.created_at = created_at


class _Session:
    """按 created_at 倒序返回多份报告——真机同一秒会写入多份。"""

    def __init__(self, payload: Any, *, extra: list[tuple[Any, int]] | None = None) -> None:
        if payload is None:
            self._rows: list[_Row] = []
        else:
            self._rows = [_Row(payload, 100)]
        for extra_payload, stamp in extra or []:
            self._rows.append(_Row(extra_payload, stamp))
        self._rows.sort(key=lambda r: r.created_at, reverse=True)

    async def scalars(self, _stmt: Any) -> list[_Row]:
        return list(self._rows)


def _chapter() -> ChapterModel:
    ch = ChapterModel(
        project_id=uuid4(), chapter_number=15, title="袖墨", chapter_goal="推进",
        information_revealed=[], information_withheld=[], foreshadowing_actions={},
        metadata_json={}, target_word_count=2600,
    )
    ch.id = uuid4()
    return ch


_POV_REPORT = {
    "violations": [
        {"code": "POV_DRIFT", "severity": "block",
         "detail": "POV declared as 'close_third' but 8/40 sampled narrative sentences use the wrong person"},
        {"code": "SOME_WARN_ONLY", "severity": "warn", "detail": "noise"},
    ]
}


@pytest.mark.asyncio
async def test_a_blocking_code_the_bundle_never_saw_now_teaches_the_rewrite() -> None:
    out = await review_services._teach_write_gate_blockers_the_bundle_cannot_see(
        _Session(_POV_REPORT), _review("原有要求"), chapter=_chapter(), language="zh",
    )

    text = out.rewrite_instructions or ""
    assert "原有要求" in text, "不得覆盖既有整改要求"
    assert "POV_DRIFT" in text
    assert "第一人称" in text, text          # playbook 的正文真的被渲染进来了
    assert out.verdict == "rewrite"          # 判定不动
    assert out.severity_max == "high"        # 等级不动


@pytest.mark.asyncio
async def test_warn_level_violations_are_not_promoted_into_the_rewrite() -> None:
    out = await review_services._teach_write_gate_blockers_the_bundle_cannot_see(
        _Session({"violations": [{"code": "SOME_WARN_ONLY", "severity": "warn"}]}),
        _review("原有要求"), chapter=_chapter(), language="zh",
    )
    assert out.rewrite_instructions == "原有要求"


@pytest.mark.asyncio
async def test_a_code_already_in_the_instructions_is_not_repeated() -> None:
    out = await review_services._teach_write_gate_blockers_the_bundle_cannot_see(
        _Session(_POV_REPORT), _review("已有：POV_DRIFT 请改人称"),
        chapter=_chapter(), language="zh",
    )
    assert out.rewrite_instructions == "已有：POV_DRIFT 请改人称"


@pytest.mark.asyncio
async def test_no_report_leaves_the_review_untouched() -> None:
    out = await review_services._teach_write_gate_blockers_the_bundle_cannot_see(
        _Session(None), _review("原有要求"), chapter=_chapter(), language="zh",
    )
    assert out.rewrite_instructions == "原有要求"


@pytest.mark.asyncio
async def test_same_second_reports_do_not_turn_teaching_into_a_lottery() -> None:
    """同一秒多份报告、对同一个码分级不同 —— 取并集，不抽签。

    真机（书 9 第 36 章，2026-08-24 06:23:24）同秒三份：一份 POV_DRIFT:warn、
    另两份 POV_DRIFT:block。原实现 ``ORDER BY created_at DESC LIMIT 1`` 抽到哪一份
    由数据库决定，抽到 warn 那份时教学就不开火 —— 这正是 2026-08-22 记录过的
    「同一秒三份报告，判定端取『最新报告』」，我又踩了一次。
    """

    warn_only = {"violations": [{"code": "POV_DRIFT", "severity": "warn"}]}
    blocking = {"violations": [{"code": "POV_DRIFT", "severity": "block"}]}

    # warn 那份排在前面（模拟数据库先返回它）
    session = _Session(warn_only, extra=[(blocking, 100)])
    out = await review_services._teach_write_gate_blockers_the_bundle_cannot_see(
        session, _review("原有要求"), chapter=_chapter(), language="zh",
    )

    assert "POV_DRIFT" in (out.rewrite_instructions or ""), out.rewrite_instructions
    assert "第一人称" in (out.rewrite_instructions or "")


@pytest.mark.asyncio
async def test_older_reports_are_not_mixed_in() -> None:
    """只取最新时刻那一批——更早的报告不得混进来。"""

    stale = {"violations": [{"code": "OBSOLETE_CODE", "severity": "block"}]}
    fresh = {"violations": [{"code": "POV_DRIFT", "severity": "block"}]}
    session = _Session(fresh, extra=[(stale, 1)])
    out = await review_services._teach_write_gate_blockers_the_bundle_cannot_see(
        session, _review(""), chapter=_chapter(), language="zh",
    )

    assert "OBSOLETE_CODE" not in (out.rewrite_instructions or "")
