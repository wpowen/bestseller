"""Tests for ``services.interpersonal_promises`` — the inter-character
oath / promise / debt ledger.

The service has three job-shapes worth pinning:

* **Snapshot rendering** — convert ORM rows to ``PromiseSnapshot``
  with derived fields (``chapters_until_due``, ``is_overdue``).
* **Active query** — ``active_promises_for_chapter`` returns the
  prompt-ready list, sorted by urgency, capped, and pruning rows
  that are too old to keep nagging the writer.
* **Death rollup** — ``mark_promises_on_death`` transitions the
  deceased's outstanding promises to ``inherited`` (if a successor
  is named) or ``lapsed``.

The fakes mirror the death-ripple test pattern: a session that
captures writes and serves pre-baked rows.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID, uuid4

import pytest

from bestseller.services.interpersonal_promises import (
    PROMISE_STATUS_ACTIVE,
    PROMISE_STATUS_INHERITED,
    PROMISE_STATUS_LAPSED,
    PromiseSnapshot,
    active_promises_for_chapter,
    mark_promises_on_death,
    render_promises_block,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


@dataclass
class _Char:
    name: str
    id: UUID = field(default_factory=uuid4)


@dataclass
class _Promise:
    project_id: UUID
    promisor_label: str
    promisee_label: str
    content: str
    promisor_id: UUID | None = None
    promisee_id: UUID | None = None
    kind: str | None = None
    made_chapter_number: int | None = None
    due_chapter_number: int | None = None
    status: str = PROMISE_STATUS_ACTIVE
    resolved_chapter_number: int | None = None
    resolution_summary: str | None = None
    inherited_by_id: UUID | None = None
    inherited_by_label: str | None = None
    metadata_json: dict[str, Any] = field(default_factory=dict)
    id: UUID = field(default_factory=uuid4)


class _FakeSession:
    def __init__(
        self,
        *,
        promises: list[_Promise] | None = None,
        characters_by_name: dict[str, _Char] | None = None,
    ) -> None:
        self.promises = list(promises or [])
        self.characters_by_name = dict(characters_by_name or {})
        self.added: list[Any] = []
        self.updates: list[dict[str, Any]] = []

    async def scalars(self, stmt: Any) -> list[Any]:
        # Single shape used: select(InterpersonalPromiseModel).where(...)
        # We return all matching active/inherited rows; tests configure
        # the row set to match what the test asks for.
        return list(self.promises)

    async def scalar(self, stmt: Any) -> Any:
        # Used by mark_promises_on_death to look up a passes_to_label
        # character. Naive lookup over the configured map.
        text = str(stmt)
        for name, char in self.characters_by_name.items():
            if name in str(text.replace("\n", " ")):
                return char
        return None

    async def execute(self, stmt: Any) -> Any:
        # We capture the values dict from update(...).values(...) by
        # extracting the bound parameters. SQLAlchemy compiles the
        # update with a dict of params; rather than parse the SQL we
        # store the raw stmt for inspection.
        compiled = stmt.compile()
        self.updates.append({
            "params": dict(compiled.params),
            "sql": str(compiled)[:200],
        })
        return None

    async def flush(self) -> None:
        return None

    def add(self, obj: Any) -> None:
        self.added.append(obj)


# ---------------------------------------------------------------------------
# active_promises_for_chapter
# ---------------------------------------------------------------------------


class TestActivePromisesForChapter:
    @pytest.mark.asyncio
    async def test_empty_returns_empty(self) -> None:
        session = _FakeSession(promises=[])
        out = await active_promises_for_chapter(
            session, project_id=uuid4(), chapter_number=10,
        )
        assert out == []

    @pytest.mark.asyncio
    async def test_returns_snapshot_with_derived_fields(self) -> None:
        proj = uuid4()
        promise = _Promise(
            project_id=proj,
            promisor_label="主角",
            promisee_label="妹妹",
            content="保护妹妹",
            due_chapter_number=20,
        )
        session = _FakeSession(promises=[promise])
        out = await active_promises_for_chapter(
            session, project_id=proj, chapter_number=10,
        )
        assert len(out) == 1
        snap = out[0]
        assert snap.chapters_until_due == 10  # 20 - 10
        assert snap.is_overdue is False

    @pytest.mark.asyncio
    async def test_overdue_within_lookback_kept(self) -> None:
        proj = uuid4()
        # Overdue by 5 chapters — within the 30-chapter lookback.
        promise = _Promise(
            project_id=proj,
            promisor_label="主角",
            promisee_label="妹妹",
            content="保护妹妹",
            due_chapter_number=10,
        )
        session = _FakeSession(promises=[promise])
        out = await active_promises_for_chapter(
            session, project_id=proj, chapter_number=15,
        )
        assert len(out) == 1
        assert out[0].is_overdue is True
        assert out[0].chapters_until_due == -5

    @pytest.mark.asyncio
    async def test_long_overdue_dropped(self) -> None:
        proj = uuid4()
        promise = _Promise(
            project_id=proj,
            promisor_label="主角",
            promisee_label="妹妹",
            content="保护妹妹",
            due_chapter_number=10,
        )
        session = _FakeSession(promises=[promise])
        out = await active_promises_for_chapter(
            session, project_id=proj, chapter_number=200,  # 190 chapters overdue
        )
        # Beyond the lookback window — pruned out.
        assert out == []

    @pytest.mark.asyncio
    async def test_sort_priority_overdue_first_then_due_soon(self) -> None:
        proj = uuid4()
        promises = [
            _Promise(project_id=proj, promisor_label="A", promisee_label="B",
                     content="x", due_chapter_number=None),
            _Promise(project_id=proj, promisor_label="C", promisee_label="D",
                     content="y", due_chapter_number=15),
            _Promise(project_id=proj, promisor_label="E", promisee_label="F",
                     content="z", due_chapter_number=8),  # overdue
        ]
        session = _FakeSession(promises=promises)
        out = await active_promises_for_chapter(
            session, project_id=proj, chapter_number=10,
        )
        # Overdue first, then due-soon, then open-ended.
        assert [s.promisor_label for s in out] == ["E", "C", "A"]

    @pytest.mark.asyncio
    async def test_limit_caps_output(self) -> None:
        proj = uuid4()
        promises = [
            _Promise(project_id=proj, promisor_label=f"P{i}",
                     promisee_label="X", content="...", due_chapter_number=20)
            for i in range(20)
        ]
        session = _FakeSession(promises=promises)
        out = await active_promises_for_chapter(
            session, project_id=proj, chapter_number=10, limit=5,
        )
        assert len(out) == 5


# ---------------------------------------------------------------------------
# mark_promises_on_death
# ---------------------------------------------------------------------------


class TestMarkPromisesOnDeath:
    @pytest.mark.asyncio
    async def test_lapses_when_no_inheritor(self) -> None:
        proj = uuid4()
        deceased = _Char(name="主角")
        promise = _Promise(
            project_id=proj,
            promisor_label="主角",
            promisor_id=deceased.id,
            promisee_label="妹妹",
            content="保护妹妹",
        )
        session = _FakeSession(promises=[promise])
        # Hack: the mock execute captures params. We expect
        # ``status`` → ``lapsed`` in the bound params.
        report = await mark_promises_on_death(
            session,
            project_id=proj,
            deceased=deceased,  # type: ignore[arg-type]
            chapter_number=100,
        )
        assert report == {"inherited": 0, "lapsed": 1}
        assert len(session.updates) == 1
        params = session.updates[0]["params"]
        assert params["status"] == PROMISE_STATUS_LAPSED

    @pytest.mark.asyncio
    async def test_inherits_when_explicit_inheritor_passed(self) -> None:
        proj = uuid4()
        deceased = _Char(name="师父")
        inheritor = _Char(name="徒弟")
        promise = _Promise(
            project_id=proj,
            promisor_label="师父",
            promisor_id=deceased.id,
            promisee_label="师姐",
            content="找回宗门遗物",
        )
        session = _FakeSession(promises=[promise])
        report = await mark_promises_on_death(
            session,
            project_id=proj,
            deceased=deceased,  # type: ignore[arg-type]
            chapter_number=100,
            inheritor=inheritor,  # type: ignore[arg-type]
        )
        assert report == {"inherited": 1, "lapsed": 0}
        params = session.updates[0]["params"]
        assert params["status"] == PROMISE_STATUS_INHERITED
        assert params["inherited_by_id"] == inheritor.id
        assert params["inherited_by_label"] == "徒弟"

    @pytest.mark.asyncio
    async def test_no_outstanding_promises_returns_zero(self) -> None:
        proj = uuid4()
        deceased = _Char(name="主角")
        session = _FakeSession(promises=[])
        report = await mark_promises_on_death(
            session,
            project_id=proj,
            deceased=deceased,  # type: ignore[arg-type]
            chapter_number=100,
        )
        assert report == {"inherited": 0, "lapsed": 0}
        assert session.updates == []


# ---------------------------------------------------------------------------
# render_promises_block
# ---------------------------------------------------------------------------


class TestRenderPromisesBlock:
    def test_empty_returns_empty_string(self) -> None:
        assert render_promises_block([]) == ""

    def test_zh_block_marks_overdue(self) -> None:
        snap = PromiseSnapshot(
            id=uuid4(), promisor_label="主角", promisee_label="妹妹",
            content="保护妹妹", kind="protection",
            made_chapter_number=5, due_chapter_number=10,
            status="active", inherited_by_label=None,
            chapters_until_due=-15, is_overdue=True,
        )
        block = render_promises_block([snap], language="zh-CN")
        assert "未了的人际承诺" in block
        assert "主角" in block
        assert "妹妹" in block
        assert "逾期" in block
        assert "15" in block

    def test_inherited_promise_shows_carrier(self) -> None:
        snap = PromiseSnapshot(
            id=uuid4(), promisor_label="师父", promisee_label="师姐",
            content="找回宗门遗物", kind=None,
            made_chapter_number=5, due_chapter_number=200,
            status=PROMISE_STATUS_INHERITED,
            inherited_by_label="徒弟",
            chapters_until_due=180, is_overdue=False,
        )
        block = render_promises_block([snap], language="zh-CN")
        assert "徒弟" in block
        assert "承担" in block
