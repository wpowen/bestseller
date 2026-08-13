"""R20 regression: configurable chapter-level total scene-rounds budget.

The default repair topology (per scene ~3 evals × 2 rewrites; per chapter 3
auto-repair passes) allows ~30 scene rounds per chapter.  R20 adds
``settings.pipeline.max_total_scene_rounds_per_chapter``:

* default ``0`` = unlimited (exact status-quo behavior);
* a positive value makes ``maybe_prepare_chapter_auto_repair`` refuse to
  trigger another repair pass once the SUM of every scene's cumulative
  round counter reaches the budget — the known block codes are written to
  ``chapter.metadata_json["rounds_budget_exhausted"]`` and the chapter is
  stamped ``requires_machine_repair`` so it follows the existing
  machine-repair route (fail-fast mode for ops, no business-logic change).
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from bestseller.services import drafts
from bestseller.services.drafts import (
    CHAPTER_ROUNDS_BUDGET_EXHAUSTED_KEY,
    bump_scene_auto_repair_counter,
    is_chapter_scene_rounds_budget_exhausted,
    mark_chapter_rounds_budget_exhausted,
    maybe_prepare_chapter_auto_repair,
    total_chapter_scene_repair_rounds,
)
from bestseller.settings import get_settings

pytestmark = pytest.mark.unit


def _scene_with_rounds(rounds: int) -> SimpleNamespace:
    scene = SimpleNamespace(metadata_json={})
    for _ in range(rounds):
        bump_scene_auto_repair_counter(scene)
    return scene


def _chapter(metadata: dict | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid4(),
        chapter_number=3,
        metadata_json=dict(metadata or {}),
        production_state="blocked",
    )


class _FakeSession:
    """Minimal async-session stub for the early budget guard path."""

    def __init__(self, scenes: list[SimpleNamespace]) -> None:
        self._scenes = scenes
        self.flushed = False

    async def scalar(self, *_args, **_kwargs):
        return None  # no quality report row

    async def scalars(self, *_args, **_kwargs):
        return list(self._scenes)

    async def flush(self) -> None:
        self.flushed = True


# ---------------------------------------------------------------------------
# Config default + pure helpers
# ---------------------------------------------------------------------------


def test_settings_default_bounds_runaway_scene_rounds() -> None:
    # 2026-06-25: default raised 0 → 20 so an unsatisfiable finding can't churn a
    # chapter's scene auto-repair loop forever (the "minimum iterations" contract).
    assert get_settings().pipeline.max_total_scene_rounds_per_chapter == 20
    assert drafts._resolve_chapter_scene_rounds_budget() == 20


def test_total_rounds_sums_scene_counters() -> None:
    scenes = [_scene_with_rounds(2), _scene_with_rounds(0), _scene_with_rounds(3)]
    assert total_chapter_scene_repair_rounds(scenes) == 5


def test_budget_zero_explicit_never_exhausts() -> None:
    # An explicit budget=0 is still the opt-out (unbounded) sentinel.
    scenes = [_scene_with_rounds(50)]
    assert is_chapter_scene_rounds_budget_exhausted(scenes, budget=0) is False


def test_default_budget_bounds_runaway() -> None:
    # 2026-06-25: default is now 20, so a runaway chapter (50 rounds) DOES exhaust
    # and gets routed to machine-repair instead of churning forever.
    scenes = [_scene_with_rounds(50)]
    assert is_chapter_scene_rounds_budget_exhausted(scenes) is True
    # a normal chapter well under the budget is unaffected
    assert is_chapter_scene_rounds_budget_exhausted([_scene_with_rounds(6)]) is False


def test_budget_threshold_behavior() -> None:
    scenes = [_scene_with_rounds(2), _scene_with_rounds(2)]
    assert is_chapter_scene_rounds_budget_exhausted(scenes, budget=5) is False
    assert is_chapter_scene_rounds_budget_exhausted(scenes, budget=4) is True
    assert is_chapter_scene_rounds_budget_exhausted(scenes, budget=3) is True


def test_mark_chapter_rounds_budget_exhausted_stamps_machine_repair_route() -> None:
    chapter = _chapter({"existing": "kept"})
    mark_chapter_rounds_budget_exhausted(
        chapter,
        block_codes=("LENGTH_OVER", "HOOK_ECHO_LOW", ""),
        total_scene_rounds=12,
        budget=10,
    )
    meta = chapter.metadata_json
    assert meta["existing"] == "kept"
    payload = meta[CHAPTER_ROUNDS_BUDGET_EXHAUSTED_KEY]
    assert payload["block_codes"] == ["LENGTH_OVER", "HOOK_ECHO_LOW"]
    assert payload["total_scene_rounds"] == 12
    assert payload["budget"] == 10
    assert meta["requires_machine_repair"] is True
    assert meta["auto_repair_in_progress"] is False
    assert meta["auto_accepted"] is False
    assert chapter.production_state == "blocked"


# ---------------------------------------------------------------------------
# 零违规的章不许被「停止修复」判成坏章
# ---------------------------------------------------------------------------
#
# 真机 2026-08-06 custom-xuanhuan-1786023406：C3 plateau 用「每轮消掉几个
# blocker」当进度曲线，已经 0 blocker 的章永远「没长进」（没有 blocker 可消），
# 于是被 plateau → 标 blocked → machine_blocked。9 章里 3 章从未产生过任何
# blocking 质量报告却被标 blocked，editor+critic 吃掉全书 68% token。
# 「不再花修复轮次」和「这章有问题」是两件事。


def test_clean_chapter_is_not_marked_blocked_when_repair_stops() -> None:
    chapter = _chapter()
    chapter.production_state = "pending"
    mark_chapter_rounds_budget_exhausted(
        chapter, block_codes=(), total_scene_rounds=6, budget=6,
    )
    meta = chapter.metadata_json
    # 状态不动：质量报告已经说它通过了，停修复不构成毙它的理由。
    assert chapter.production_state == "pending"
    assert meta["requires_machine_repair"] is False
    assert meta["rounds_budget_stopped_while_clean"] is True
    # 轮次记录仍要写——停这件事本身值得审计。
    assert meta[CHAPTER_ROUNDS_BUDGET_EXHAUSTED_KEY]["block_codes"] == []
    assert meta[CHAPTER_ROUNDS_BUDGET_EXHAUSTED_KEY]["total_scene_rounds"] == 6
    assert meta["auto_repair_in_progress"] is False


def test_blank_only_block_codes_count_as_clean() -> None:
    # 真机 block codes 里混过空串；过滤后为空就是干净，不能因为"传了个列表"就毙章。
    chapter = _chapter()
    chapter.production_state = "ok"
    mark_chapter_rounds_budget_exhausted(
        chapter, block_codes=("", ""), total_scene_rounds=3, budget=3,
    )
    assert chapter.production_state == "ok"
    assert chapter.metadata_json["requires_machine_repair"] is False


def test_dirty_chapter_still_routes_to_machine_repair() -> None:
    # no-op 契约：有真违规时行为与修复前逐字节一致。
    chapter = _chapter()
    chapter.production_state = "pending"
    mark_chapter_rounds_budget_exhausted(
        chapter, block_codes=("LENGTH_UNDER",), total_scene_rounds=9, budget=8,
    )
    assert chapter.production_state == "blocked"
    assert chapter.metadata_json["requires_machine_repair"] is True
    assert "rounds_budget_stopped_while_clean" not in chapter.metadata_json


# ---------------------------------------------------------------------------
# Guard inside maybe_prepare_chapter_auto_repair
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_repair_stops_when_rounds_budget_exhausted(monkeypatch) -> None:
    monkeypatch.setattr(drafts, "_resolve_chapter_scene_rounds_budget", lambda: 4)
    scenes = [_scene_with_rounds(2), _scene_with_rounds(2)]  # total 4 >= budget 4
    session = _FakeSession(scenes)
    chapter = _chapter(
        {"auto_repair_last_block_codes": ["LENGTH_OVER", "HOOK_ECHO_LOW"]}
    )
    project = SimpleNamespace(id=uuid4())

    repair_triggered, block_codes = await maybe_prepare_chapter_auto_repair(
        session,
        project=project,
        chapter=chapter,
        repairable_codes=("LENGTH_OVER",),
        attempt_number=2,
    )

    assert repair_triggered is False
    assert block_codes == ("LENGTH_OVER", "HOOK_ECHO_LOW")
    payload = chapter.metadata_json[CHAPTER_ROUNDS_BUDGET_EXHAUSTED_KEY]
    assert payload["block_codes"] == ["LENGTH_OVER", "HOOK_ECHO_LOW"]
    assert payload["total_scene_rounds"] == 4
    assert payload["budget"] == 4
    assert chapter.metadata_json["requires_machine_repair"] is True
    assert session.flushed is True
    # scenes were NOT reset — fail-fast must not start another rewrite round
    for scene in scenes:
        assert "auto_repair_hint" not in scene.metadata_json


@pytest.mark.asyncio
async def test_repair_proceeds_when_under_rounds_budget(monkeypatch) -> None:
    monkeypatch.setattr(drafts, "_resolve_chapter_scene_rounds_budget", lambda: 4)
    scenes = [_scene_with_rounds(2), _scene_with_rounds(1)]  # total 3 < budget 4
    session = _FakeSession(scenes)
    chapter = _chapter({"auto_repair_last_block_codes": ["LENGTH_OVER"]})
    project = SimpleNamespace(id=uuid4())

    repair_triggered, _block_codes = await maybe_prepare_chapter_auto_repair(
        session,
        project=project,
        chapter=chapter,
        repairable_codes=("NOT_A_REAL_CODE",),
        attempt_number=2,
    )

    # not triggered here either (codes not repairable), but crucially the
    # budget guard did NOT fire: no exhaustion stamp, no machine-repair flag.
    assert repair_triggered is False
    assert CHAPTER_ROUNDS_BUDGET_EXHAUSTED_KEY not in chapter.metadata_json
    assert "requires_machine_repair" not in chapter.metadata_json


@pytest.mark.asyncio
async def test_budget_zero_skips_guard_entirely(monkeypatch) -> None:
    """Default 0 must keep status quo — the guard never queries scenes."""
    monkeypatch.setattr(drafts, "_resolve_chapter_scene_rounds_budget", lambda: 0)

    class _ExplodingSession(_FakeSession):
        async def scalars(self, *_args, **_kwargs):  # pragma: no cover - guard
            raise AssertionError("budget guard must not query scenes when 0")

    session = _ExplodingSession([_scene_with_rounds(50)])
    chapter = _chapter()
    project = SimpleNamespace(id=uuid4())

    repair_triggered, block_codes = await maybe_prepare_chapter_auto_repair(
        session,
        project=project,
        chapter=chapter,
        repairable_codes=("LENGTH_OVER",),
        attempt_number=1,
    )
    assert repair_triggered is False
    assert block_codes == ()
    assert CHAPTER_ROUNDS_BUDGET_EXHAUSTED_KEY not in chapter.metadata_json
