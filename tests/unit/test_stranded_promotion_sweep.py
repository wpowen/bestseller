"""改判据会遗留「当时被拒、现在够格」的稿，而没有任何路径回头重评它们。

真机（书 9，2026-08-24）：提升资格在当天从掺回声公式的 ``score_overall``
改成诚实轴。改判之后评的第 13、15 章一路走到 ``promoted``；改判**之前**评的
第 7 章（诚实轴 0.860）与第 11 章（0.863）都过了 0.85 的线，却永远停在
``under_review`` —— 提升只在章评那一轮尝试一次。

在真库上带回滚跑这个清扫：救回 [7, 11]，且不多碰任何一章。

这个清扫只会**提升**：它复用 ``promote_best_draft``（自带资格校验），
不降级、不拦截、不改任何门的结论。
"""

from __future__ import annotations

# ruff: noqa: RUF001, RUF002, RUF003 — 中文标点是刻意的。
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest

from bestseller.services import draft_promotion

pytestmark = pytest.mark.unit


@dataclass
class _Score:
    judge_key: str | None
    evaluation_round: int = 1
    created_at: datetime = datetime(2026, 8, 24, tzinfo=UTC)
    id: UUID = uuid4()


class _Result(list):
    """够用的 scalars() 返回值：可迭代。"""


class _FakeSession:
    """只回答清扫真正问出的那几个问题。

    清扫对每一章的提问顺序是固定的：先 ``scalar``（这章有没有已提升的稿），
    只有答「没有」时才继续 ``scalars``（这章的分数行）。游标按这个顺序推进。
    """

    def __init__(
        self,
        *,
        chapter_ids: list[UUID],
        promoted: dict[UUID, bool],
        scores: dict[UUID, list[_Score]],
    ) -> None:
        self._chapter_ids = chapter_ids
        self._promoted = promoted
        self._scores = scores
        self._served_chapter_list = False
        self._i = 0
        self.attempted: list[tuple[UUID, str]] = []

    async def scalars(self, stmt: Any) -> _Result:
        if not self._served_chapter_list:
            self._served_chapter_list = True
            return _Result(self._chapter_ids)
        chapter_id = self._chapter_ids[self._i]
        self._i += 1
        return _Result(self._scores.get(chapter_id, []))

    async def scalar(self, stmt: Any) -> Any:
        chapter_id = self._chapter_ids[self._i]
        if self._promoted.get(chapter_id):
            self._i += 1  # 已提升 → 直接跳过，不会再问分数
            return uuid4()
        return None


async def _run(
    monkeypatch: pytest.MonkeyPatch,
    session: _FakeSession,
    *,
    outcomes: dict[UUID, Any],
) -> tuple[UUID, ...]:
    attempted: list[tuple[UUID, str]] = []

    async def fake_promote(_session, *, project_id, chapter_id, judge_key, **_kw):
        attempted.append((chapter_id, judge_key))
        result = outcomes[chapter_id]
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(draft_promotion, "promote_chapter_draft", fake_promote)
    rescued = await draft_promotion.repromote_stranded_chapters(
        session, project_id=uuid4()
    )
    session.attempted = attempted
    return rescued


@dataclass
class _Outcome:
    changed: bool
    reason: str = "promoted"


@pytest.mark.asyncio
async def test_sweep_skips_chapters_that_already_have_a_promoted_draft(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settled, stranded = uuid4(), uuid4()
    session = _FakeSession(
        chapter_ids=[settled, stranded],
        promoted={settled: True, stranded: False},
        scores={settled: [_Score("j1")], stranded: [_Score("j1")]},
    )

    rescued = await _run(monkeypatch, session, outcomes={stranded: _Outcome(True)})

    assert [c for c, _ in session.attempted] == [stranded]
    assert rescued == (stranded,)


@pytest.mark.asyncio
async def test_sweep_never_touches_a_chapter_with_no_judged_score(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unjudged = uuid4()
    session = _FakeSession(
        chapter_ids=[unjudged],
        promoted={unjudged: False},
        scores={unjudged: [_Score(None), _Score("   ")]},
    )
    rescued = await _run(monkeypatch, session, outcomes={})

    assert session.attempted == []
    assert rescued == ()


@pytest.mark.asyncio
async def test_sweep_uses_the_most_recent_judge_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chapter = uuid4()
    session = _FakeSession(
        chapter_ids=[chapter],
        promoted={chapter: False},
        scores={
            chapter: [
                _Score("old-judge", evaluation_round=1),
                _Score("new-judge", evaluation_round=3),
                _Score("mid-judge", evaluation_round=2),
            ]
        },
    )
    await _run(monkeypatch, session, outcomes={chapter: _Outcome(True)})

    assert session.attempted[0][1] == "new-judge"


@pytest.mark.asyncio
async def test_one_bad_chapter_cannot_abort_the_sweep(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bad, good = uuid4(), uuid4()
    session = _FakeSession(
        chapter_ids=[bad, good],
        promoted={bad: False, good: False},
        scores={bad: [_Score("j")], good: [_Score("j")]},
    )

    rescued = await _run(
        monkeypatch,
        session,
        outcomes={bad: RuntimeError("行锁冲突"), good: _Outcome(True)},
    )

    assert rescued == (good,)


@pytest.mark.asyncio
async def test_unchanged_outcome_is_not_reported_as_rescued(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chapter = uuid4()
    session = _FakeSession(
        chapter_ids=[chapter],
        promoted={chapter: False},
        scores={chapter: [_Score("j")]},
    )
    rescued = await _run(
        monkeypatch,
        session,
        outcomes={chapter: _Outcome(False, "no_eligible_candidate")},
    )

    assert session.attempted != []
    assert rescued == ()
