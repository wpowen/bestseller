"""Phase C gray-out config parsing + DB-backed accrual pipeline wiring."""

from types import SimpleNamespace
from uuid import UUID

import pytest

from bestseller.services.quality_gates_config import _build_phase_c


def test_only_enforce_from_chapter_parsed():
    cfg = _build_phase_c({"enabled": True, "only_enforce_from_chapter": 12})
    assert cfg.enabled is True
    assert cfg.only_enforce_from_chapter == 12


def test_only_enforce_from_chapter_defaults_none():
    cfg = _build_phase_c({"enabled": True})
    assert cfg.only_enforce_from_chapter is None


def test_only_enforce_from_chapter_rejects_below_one():
    cfg = _build_phase_c({"enabled": True, "only_enforce_from_chapter": 0})
    assert cfg.only_enforce_from_chapter is None


def test_only_enforce_from_chapter_handles_garbage():
    cfg = _build_phase_c({"enabled": True, "only_enforce_from_chapter": "oops"})
    assert cfg.only_enforce_from_chapter is None


# --- pipeline accrual wiring -------------------------------------------------


class _FakeScalarResult:
    def __init__(self, rows):
        self._rows = rows

    def __iter__(self):
        return iter(self._rows)


class _FakeSession:
    """Minimal async session: ``scalars`` returns canned rows; ``flush`` counts."""

    def __init__(self, rows):
        self._rows = rows
        self.flush_count = 0

    async def scalars(self, _stmt):
        return _FakeScalarResult(self._rows)

    async def flush(self):
        self.flush_count += 1


def _debt_row(**kw):
    base = dict(
        status="active",
        balance=1.0,
        interest_rate=0.10,
        accrued_through_chapter=1,
        due_chapter=10,
    )
    base.update(kw)
    return SimpleNamespace(**base)


@pytest.mark.asyncio
async def test_apply_post_chapter_phase_c_accrues_and_flushes(monkeypatch):
    from bestseller.services import pipelines

    monkeypatch.setattr(
        pipelines,
        "get_quality_gates_config",
        lambda: SimpleNamespace(
            phase_c=SimpleNamespace(enabled=True, only_enforce_from_chapter=None)
        ),
    )
    row = _debt_row(balance=1.0, accrued_through_chapter=1, due_chapter=10)
    session = _FakeSession([row])

    await pipelines._apply_post_chapter_phase_c(
        session=session,
        project_id=UUID("00000000-0000-0000-0000-000000000001"),
        chapter_number=4,
    )

    assert round(row.balance, 4) == 1.331  # 1.0 * 1.1^3
    assert row.accrued_through_chapter == 4
    assert session.flush_count == 1


@pytest.mark.asyncio
async def test_apply_post_chapter_phase_c_noop_when_disabled(monkeypatch):
    from bestseller.services import pipelines

    monkeypatch.setattr(
        pipelines,
        "get_quality_gates_config",
        lambda: SimpleNamespace(
            phase_c=SimpleNamespace(enabled=False, only_enforce_from_chapter=None)
        ),
    )
    row = _debt_row(balance=1.0, accrued_through_chapter=1, due_chapter=10)
    session = _FakeSession([row])

    await pipelines._apply_post_chapter_phase_c(
        session=session,
        project_id=UUID("00000000-0000-0000-0000-000000000001"),
        chapter_number=4,
    )
    assert row.balance == 1.0  # untouched
    assert session.flush_count == 0


@pytest.mark.asyncio
async def test_apply_post_chapter_phase_c_respects_grayout(monkeypatch):
    from bestseller.services import pipelines

    monkeypatch.setattr(
        pipelines,
        "get_quality_gates_config",
        lambda: SimpleNamespace(
            phase_c=SimpleNamespace(enabled=True, only_enforce_from_chapter=10)
        ),
    )
    row = _debt_row(balance=1.0, accrued_through_chapter=1, due_chapter=20)
    session = _FakeSession([row])

    # chapter 4 < gray-out 10 → skipped
    await pipelines._apply_post_chapter_phase_c(
        session=session,
        project_id=UUID("00000000-0000-0000-0000-000000000001"),
        chapter_number=4,
    )
    assert row.balance == 1.0
    assert session.flush_count == 0
