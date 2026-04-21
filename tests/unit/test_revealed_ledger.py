"""Unit tests for the revealed facts / beats ledger used by the planner."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID, uuid4

import pytest

from bestseller.services.revealed_ledger import (
    BeatMotif,
    HookUsage,
    RevealedFactEntry,
    RevealedLedger,
    build_revealed_ledger_from_rows,
)


# ---------------------------------------------------------------------------
# Fake rows
# ---------------------------------------------------------------------------

@dataclass
class FakeChapter:
    id: UUID
    chapter_number: int
    hook_type: str | None = None
    hook_description: str | None = None
    main_conflict: str | None = None
    chapter_goal: str | None = None
    information_revealed: list[Any] = field(default_factory=list)


@dataclass
class FakeScene:
    chapter_id: UUID
    scene_number: int
    purpose: dict[str, Any] = field(default_factory=dict)
    scene_type: str = "interactive"


@dataclass
class FakeSnapshot:
    chapter_number: int
    facts: dict[str, Any] = field(default_factory=dict)


def _ch(
    num: int,
    *,
    hook: str | None = None,
    conflict: str | None = None,
    goal: str | None = None,
    info: list[Any] | None = None,
) -> FakeChapter:
    return FakeChapter(
        id=uuid4(),
        chapter_number=num,
        hook_type=hook,
        main_conflict=conflict,
        chapter_goal=goal,
        information_revealed=info or [],
    )


# ---------------------------------------------------------------------------
# Empty state
# ---------------------------------------------------------------------------

def test_empty_ledger_renders_empty_block() -> None:
    ledger = RevealedLedger(project_id=uuid4())
    assert ledger.is_empty is True
    assert ledger.to_prompt_block() == ""


def test_build_from_rows_with_no_chapters_returns_empty() -> None:
    project_id = uuid4()
    ledger = build_revealed_ledger_from_rows(project_id, chapters=[])
    assert ledger.is_empty
    assert ledger.project_id == project_id
    assert ledger.chapters_covered == ()


# ---------------------------------------------------------------------------
# Facts — from chapter_state_snapshots
# ---------------------------------------------------------------------------

def test_extracts_facts_from_state_snapshots() -> None:
    ch1 = _ch(1)
    snap1 = FakeSnapshot(
        chapter_number=1,
        facts={"facts": [
            {"name": "浮标倒计时", "value": 3, "unit": "日", "kind": "countdown"},
            {"name": "主角·境界", "value": "金丹三层", "kind": "level", "subject": "林枫"},
        ]},
    )
    ledger = build_revealed_ledger_from_rows(
        uuid4(), chapters=[ch1], snapshots=[snap1]
    )
    assert len(ledger.facts) == 2
    by_name = {f.name: f for f in ledger.facts}
    assert by_name["浮标倒计时"].value == "3日"
    assert by_name["浮标倒计时"].kind == "countdown"
    assert by_name["主角·境界"].subject == "林枫"
    assert by_name["主角·境界"].kind == "level"


def test_dedupes_fact_entries_across_snapshots() -> None:
    """A fact that appears in several chapters should only list the earliest."""
    ch1 = _ch(1)
    ch2 = _ch(2)
    snap1 = FakeSnapshot(chapter_number=1, facts={"facts": [
        {"name": "浮标倒计时", "value": 3, "kind": "countdown"},
    ]})
    snap2 = FakeSnapshot(chapter_number=2, facts={"facts": [
        {"name": "浮标倒计时", "value": 2, "kind": "countdown"},
    ]})
    ledger = build_revealed_ledger_from_rows(
        uuid4(), chapters=[ch1, ch2], snapshots=[snap1, snap2]
    )
    # Same (kind, name) pair → first_chapter=1 wins.
    assert len(ledger.facts) == 1
    assert ledger.facts[0].first_chapter == 1


# ---------------------------------------------------------------------------
# Facts — from chapter.information_revealed
# ---------------------------------------------------------------------------

def test_extracts_facts_from_information_revealed_string_entries() -> None:
    ch1 = _ch(1, info=["浮标的实际触发条件是金丹期以上灵压", ""])
    ledger = build_revealed_ledger_from_rows(uuid4(), chapters=[ch1])
    assert len(ledger.facts) == 1
    assert ledger.facts[0].kind == "information"
    assert ledger.facts[0].first_chapter == 1


def test_extracts_facts_from_information_revealed_dict_entries() -> None:
    ch1 = _ch(1, info=[{"name": "浮标触发条件", "value": "金丹期灵压"}])
    ledger = build_revealed_ledger_from_rows(uuid4(), chapters=[ch1])
    assert len(ledger.facts) == 1
    assert ledger.facts[0].name == "浮标触发条件"


# ---------------------------------------------------------------------------
# Hook overuse
# ---------------------------------------------------------------------------

def test_detects_overused_hook_type_in_recent_window() -> None:
    chapters = [_ch(i, hook="危机悬念") for i in range(170, 200)]
    ledger = build_revealed_ledger_from_rows(uuid4(), chapters=chapters)
    overused = ledger.overused_hooks(recent_threshold=4)
    assert len(overused) == 1
    assert overused[0].hook_type == "危机悬念"
    assert overused[0].recent_count >= 4


def test_does_not_flag_varied_hooks() -> None:
    chapters = [
        _ch(1, hook="危机悬念"),
        _ch(2, hook="情感钩子"),
        _ch(3, hook="信息落差"),
        _ch(4, hook="反转揭示"),
        _ch(5, hook="身份冲突"),
    ]
    ledger = build_revealed_ledger_from_rows(uuid4(), chapters=chapters)
    overused = ledger.overused_hooks(recent_threshold=4)
    assert overused == ()


def test_hook_usage_recent_window_excludes_far_chapters() -> None:
    """A hook used heavily 100 chapters ago should not dominate the block."""
    chapters = (
        [_ch(i, hook="危机悬念") for i in range(1, 30)]  # ancient uses
        + [_ch(i, hook="信息落差") for i in range(181, 196)]  # recent uses
    )
    ledger = build_revealed_ledger_from_rows(uuid4(), chapters=chapters, recent_window=20)
    overused = ledger.overused_hooks(recent_threshold=4)
    assert len(overused) == 1
    assert overused[0].hook_type == "信息落差"


# ---------------------------------------------------------------------------
# Beat motifs (recurring conflict / scene-purpose phrases)
# ---------------------------------------------------------------------------

def test_detects_recurring_main_conflict_motif() -> None:
    chapters = [
        _ch(170, conflict="继续推进真相揭示"),
        _ch(171, conflict="继续推进真相揭示"),
        _ch(172, conflict="继续推进真相揭示"),
        _ch(173, conflict="突破层压阵法"),
    ]
    ledger = build_revealed_ledger_from_rows(uuid4(), chapters=chapters)
    phrases = {m.phrase for m in ledger.beat_motifs}
    assert "继续推进真相揭示" in phrases


def test_detects_recurring_scene_purpose_motif() -> None:
    chapter_rows = []
    scenes_map: dict[UUID, list[FakeScene]] = {}
    for i in range(10):
        ch = _ch(i + 1)
        chapter_rows.append(ch)
        scenes_map[ch.id] = [
            FakeScene(chapter_id=ch.id, scene_number=1,
                      purpose={"story": "推进对浮标的调查"}),
            FakeScene(chapter_id=ch.id, scene_number=2,
                      purpose={"story": "独特场景 — 跟沈沁对话"}),
        ]
    ledger = build_revealed_ledger_from_rows(
        uuid4(), chapters=chapter_rows, scenes_by_chapter=scenes_map
    )
    motifs_by_phrase = {m.phrase: m for m in ledger.beat_motifs}
    assert "推进对浮标的调查" in motifs_by_phrase
    assert motifs_by_phrase["推进对浮标的调查"].count == 10


def test_motif_ignores_short_phrases() -> None:
    chapters = [_ch(i, conflict="冲突") for i in range(1, 10)]
    ledger = build_revealed_ledger_from_rows(uuid4(), chapters=chapters)
    # Too short to be a meaningful motif
    assert all(len(m.phrase) >= 6 for m in ledger.beat_motifs)


# ---------------------------------------------------------------------------
# Recent conflicts list
# ---------------------------------------------------------------------------

def test_recent_conflicts_only_within_window() -> None:
    chapters = [_ch(i, conflict=f"chapter {i} conflict") for i in range(1, 201)]
    ledger = build_revealed_ledger_from_rows(uuid4(), chapters=chapters, recent_window=10)
    assert all(ch_num >= 191 for ch_num, _ in ledger.recent_conflicts)


def test_recent_conflicts_snippets_are_truncated() -> None:
    long_conflict = "a" * 500
    chapters = [_ch(1, conflict=long_conflict)]
    ledger = build_revealed_ledger_from_rows(uuid4(), chapters=chapters, recent_window=5)
    snippet = ledger.recent_conflicts[0][1]
    assert len(snippet) <= 60
    assert snippet.endswith("…")


# ---------------------------------------------------------------------------
# Prompt block rendering
# ---------------------------------------------------------------------------

def test_prompt_block_zh_contains_all_sections() -> None:
    chapters = [
        _ch(181, hook="危机悬念", conflict="继续推进真相揭示",
            info=[{"name": "浮标漏洞", "value": "金丹封锁"}]),
        _ch(182, hook="危机悬念", conflict="继续推进真相揭示"),
        _ch(183, hook="危机悬念", conflict="继续推进真相揭示"),
        _ch(184, hook="危机悬念", conflict="继续推进真相揭示"),
        _ch(185, hook="危机悬念"),
    ]
    snap = FakeSnapshot(chapter_number=181, facts={"facts": [
        {"name": "主角境界", "value": "金丹三层", "kind": "level", "subject": "林枫"},
    ]})
    ledger = build_revealed_ledger_from_rows(
        uuid4(), chapters=chapters, snapshots=[snap]
    )
    block = ledger.to_prompt_block(language="zh-CN")
    assert "【已揭示与已用节拍" in block
    assert "已确立的事实" in block
    assert "近期钩子类型使用频率过高" in block
    assert "危机悬念" in block
    assert "已反复出现的节拍" in block
    assert "继续推进真相揭示" in block
    assert "近期主要冲突" in block


def test_prompt_block_en_contains_all_sections() -> None:
    chapters = [
        _ch(181, hook="crisis_cliff", conflict="push the investigation",
            info=[{"name": "buoy weakness", "value": "golden-core seal"}]),
        _ch(182, hook="crisis_cliff", conflict="push the investigation"),
        _ch(183, hook="crisis_cliff", conflict="push the investigation"),
        _ch(184, hook="crisis_cliff", conflict="push the investigation"),
    ]
    ledger = build_revealed_ledger_from_rows(uuid4(), chapters=chapters)
    block = ledger.to_prompt_block(language="en")
    assert "Already-revealed facts" in block
    assert "Recently overused hook_types" in block
    assert "Recurring beat phrases" in block
    assert "Recent main conflicts" in block


def test_prompt_block_is_empty_for_empty_ledger() -> None:
    chapters = [_ch(1, conflict="unique conflict #1")]
    ledger = build_revealed_ledger_from_rows(uuid4(), chapters=chapters)
    # No overused hook, no repeated motif, only one conflict (still surfaces)
    block = ledger.to_prompt_block(language="zh-CN")
    assert "近期主要冲突" in block  # the conflict section fires even for a single entry
    assert "近期钩子类型使用频率过高" not in block
    assert "已反复出现的节拍" not in block


# ---------------------------------------------------------------------------
# Real-world scenario: ch181-style planner drift on xianxia project
# ---------------------------------------------------------------------------

def test_xianxia_ch181_style_planner_drift_detected() -> None:
    """Simulate the observed ch170-200 pattern from the xianxia upgrade:
    repeated hook_type='危机悬念' with 'scene_purpose_match' fingerprints.

    The ledger should surface BOTH the hook overuse AND the scene motif.
    """
    chapter_rows: list[FakeChapter] = []
    scenes_map: dict[UUID, list[FakeScene]] = {}
    for ch_num in range(170, 201):
        ch = _ch(
            ch_num,
            hook="危机悬念",
            conflict="推进调查并突破当前僵局",
        )
        chapter_rows.append(ch)
        scenes_map[ch.id] = [
            FakeScene(chapter_id=ch.id, scene_number=1,
                      purpose={"story": "继续推进浮标调查"}),
            FakeScene(chapter_id=ch.id, scene_number=2,
                      purpose={"story": "承压推进本章冲突"}),
        ]
    ledger = build_revealed_ledger_from_rows(
        uuid4(), chapters=chapter_rows, scenes_by_chapter=scenes_map
    )
    overused = ledger.overused_hooks()
    motifs = {m.phrase for m in ledger.beat_motifs}
    assert overused and overused[0].hook_type == "危机悬念"
    assert "继续推进浮标调查" in motifs
    assert "承压推进本章冲突" in motifs
    block = ledger.to_prompt_block(language="zh-CN")
    assert "危机悬念" in block
    assert "继续推进浮标调查" in block


# ---------------------------------------------------------------------------
# Determinism / idempotence
# ---------------------------------------------------------------------------

def test_ledger_is_deterministic_for_same_inputs() -> None:
    chapters = [_ch(i, hook="危机悬念", conflict="继续推进真相揭示") for i in range(1, 10)]
    project_id = uuid4()
    a = build_revealed_ledger_from_rows(project_id, chapters=chapters)
    b = build_revealed_ledger_from_rows(project_id, chapters=chapters)
    assert a.to_prompt_block() == b.to_prompt_block()
    assert a.facts == b.facts
    assert a.hook_usage == b.hook_usage
    assert a.beat_motifs == b.beat_motifs


# ---------------------------------------------------------------------------
# up_to_chapter filter via async path — verified by row-level filtering
# ---------------------------------------------------------------------------

def test_build_from_rows_respects_caller_filtering() -> None:
    """Caller filters the chapter list before passing — ledger just uses it."""
    all_chapters = [_ch(i) for i in range(1, 20)]
    early_only = [c for c in all_chapters if c.chapter_number <= 5]
    ledger = build_revealed_ledger_from_rows(uuid4(), chapters=early_only)
    assert ledger.chapters_covered == (1, 2, 3, 4, 5)


# ---------------------------------------------------------------------------
# Async path using an in-memory SQLite-like session mock
# ---------------------------------------------------------------------------

class _FakeScalarResult:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def __iter__(self):  # type: ignore[override]
        return iter(self._rows)


class _FakeSession:
    """Minimal async session mock that returns queued scalar result lists."""

    def __init__(self, result_queue: list[list[Any]]) -> None:
        self._queue = result_queue

    async def scalars(self, _query: Any) -> _FakeScalarResult:
        return _FakeScalarResult(self._queue.pop(0))


@pytest.mark.asyncio
async def test_build_revealed_ledger_async_happy_path() -> None:
    from bestseller.services.revealed_ledger import build_revealed_ledger

    project_id = uuid4()
    chapters = [
        _ch(1, hook="危机悬念", conflict="继续推进真相揭示"),
        _ch(2, hook="危机悬念", conflict="继续推进真相揭示"),
        _ch(3, hook="危机悬念", conflict="继续推进真相揭示"),
        _ch(4, hook="危机悬念", conflict="继续推进真相揭示"),
    ]
    scenes: list[FakeScene] = []
    for c in chapters:
        scenes.append(FakeScene(chapter_id=c.id, scene_number=1,
                                purpose={"story": "继续推进浮标调查"}))
    snap = FakeSnapshot(chapter_number=1, facts={"facts": [
        {"name": "浮标倒计时", "value": 3, "unit": "日", "kind": "countdown"},
    ]})
    session = _FakeSession(result_queue=[chapters, scenes, [snap]])

    ledger = await build_revealed_ledger(session, project_id)  # type: ignore[arg-type]
    assert ledger.project_id == project_id
    assert ledger.chapters_covered == (1, 2, 3, 4)
    # Facts pulled from snapshot
    assert any(f.name == "浮标倒计时" for f in ledger.facts)
    # Hook overuse
    assert any(h.hook_type == "危机悬念" for h in ledger.hook_usage)
    # Motif
    assert any("继续推进" in m.phrase for m in ledger.beat_motifs)


@pytest.mark.asyncio
async def test_build_revealed_ledger_async_no_chapters_returns_empty() -> None:
    from bestseller.services.revealed_ledger import build_revealed_ledger

    session = _FakeSession(result_queue=[[]])  # only the chapter query is executed
    ledger = await build_revealed_ledger(session, uuid4())  # type: ignore[arg-type]
    assert ledger.is_empty
