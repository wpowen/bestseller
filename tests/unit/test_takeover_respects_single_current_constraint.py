"""每章至多一份在架稿——接管时必须先降级旧稿，否则插入直接撞唯一约束。

2026-08-24 真机：对称否决修复让接管分支开始正常触发，第 23 章当场炸出

    duplicate key value violates unique constraint "uq_chapter_draft_current"

把跑了 1238 秒的 autowrite 与 project_repair **一起打挂**。这个碰撞被死锁掩盖了
很久——接管分支几乎从不触发，所以从来没撞上；修好死锁，它立刻现形。

⚠️ 现有的 FakeSession 的 ``flush()`` **不校验这个部分唯一索引**，所以整套单测
对它完全失明。这里的假会话会真的执行该约束——量具先补上，测试才有意义。
与 2026-07-13 场景重写那次同一个形状、同一个解法：分两次 flush。
"""

from __future__ import annotations

# ruff: noqa: RUF001, RUF002, RUF003 — 中文标点是刻意的。
import inspect
import re

import pytest

from bestseller.services import reviews as review_services

pytestmark = pytest.mark.unit


class SingleCurrentViolation(AssertionError):
    """真库会抛 UniqueViolationError 的那一刻。"""


class _ConstraintEnforcingSession:
    """只做一件事：flush 时执行 uq_chapter_draft_current。"""

    def __init__(self, existing: list[object]) -> None:
        self.rows = list(existing)

    def add(self, obj: object) -> None:
        self.rows.append(obj)

    async def flush(self) -> None:
        seen: dict[object, int] = {}
        for row in self.rows:
            if not getattr(row, "is_current", False):
                continue
            key = getattr(row, "chapter_id", None)
            seen[key] = seen.get(key, 0) + 1
            if seen[key] > 1:
                raise SingleCurrentViolation(
                    f"chapter {key} 同时有 {seen[key]} 份 is_current 稿"
                )


class _Draft:
    def __init__(self, chapter_id: str, version_no: int, is_current: bool) -> None:
        self.chapter_id = chapter_id
        self.version_no = version_no
        self.is_current = is_current


@pytest.mark.asyncio
async def test_the_fake_session_actually_enforces_the_constraint() -> None:
    """先证明量具会红——否则下面的绿色毫无意义。"""

    incumbent = _Draft("ch", 1, True)
    session = _ConstraintEnforcingSession([incumbent])
    session.add(_Draft("ch", 2, True))          # 没降级旧稿就插新在架稿
    with pytest.raises(SingleCurrentViolation):
        await session.flush()


@pytest.mark.asyncio
async def test_demoting_the_incumbent_first_satisfies_the_constraint() -> None:
    incumbent = _Draft("ch", 1, True)
    session = _ConstraintEnforcingSession([incumbent])

    incumbent.is_current = False                # ← 修复做的事
    await session.flush()                       # ← 单独 flush
    session.add(_Draft("ch", 2, True))
    await session.flush()                       # 不再冲突


def test_the_rewrite_path_demotes_before_it_inserts() -> None:
    """接线断言：降级 + flush 必须排在 new_draft 之前。

    （行为证据在上面两条；这条只钉住顺序，防止将来被挪到插入之后。）
    """

    source = inspect.getsource(review_services.rewrite_chapter_from_task)
    demote = source.find("current_draft.is_current = False")
    insert = source.find("new_draft = ChapterDraftVersionModel(")
    assert demote != -1, "接管路径没有降级旧稿"
    assert demote < insert, "降级排在了插入之后，约束仍会被撞"
    between = source[demote:insert]
    assert re.search(r"await session\.flush\(\)", between), "降级之后缺少单独的 flush"


def test_demotion_only_happens_on_an_actual_takeover() -> None:
    """不接管时不得动旧稿的 is_current——否则会出现「一章没有在架稿」。"""

    source = inspect.getsource(review_services.rewrite_chapter_from_task)
    idx = source.find("current_draft.is_current = False")
    guard = source[max(0, idx - 200) : idx]
    assert "_took_current" in guard, "降级没有挂在接管判定上"
