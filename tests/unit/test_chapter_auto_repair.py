"""Unit tests for :func:`drafts.maybe_prepare_chapter_auto_repair` (C6).

The helper is the "auto-repair core" — given a ``ChapterQualityReportModel``
it decides whether the latest blocking codes are inside the repair
allowlist and, if so, resets every scene to ``NEEDS_REWRITE`` with a
hint while returning ``chapter.production_state`` to ``pending``.  It does
*not* kick off the next regen cycle — that is the pipeline's responsibility.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import pytest

from bestseller.domain.enums import SceneStatus
from bestseller.services.drafts import maybe_prepare_chapter_auto_repair


pytestmark = pytest.mark.unit


# ── Light-weight test doubles ──────────────────────────────────────────
#
# We deliberately avoid the real SQLAlchemy models here so the test
# surface stays small.  The helper only reads a handful of attributes
# and the ``async session.scalar / scalars / flush`` trio, so thin
# in-memory stand-ins are enough.


@dataclass
class FakeChapter:
    id: Any = field(default_factory=uuid4)
    project_id: Any = field(default_factory=uuid4)
    chapter_number: int = 1
    production_state: str = "blocked"
    metadata_json: dict[str, Any] = field(default_factory=dict)


@dataclass
class FakeScene:
    id: Any = field(default_factory=uuid4)
    chapter_id: Any = None
    scene_number: int = 1
    status: str = SceneStatus.APPROVED.value
    metadata_json: dict[str, Any] = field(default_factory=dict)
    target_word_count: int = 1600
    participants: list[str] = field(default_factory=list)
    entry_state: dict[str, Any] = field(default_factory=dict)
    exit_state: dict[str, Any] = field(default_factory=dict)


@dataclass
class FakeQualityReport:
    chapter_id: Any = None
    report_json: dict[str, Any] = field(default_factory=dict)
    regen_attempts: int = 0
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    blocks_write: bool = True


@dataclass
class FakeProject:
    id: Any = field(default_factory=uuid4)


class FakeResult:
    """Mimic a SQLAlchemy scalars() ``ScalarResult`` with ``list()``."""

    def __init__(self, items: list[Any]) -> None:
        self._items = list(items)

    def __iter__(self):
        return iter(self._items)


class FakeSession:
    """Minimal AsyncSession stand-in.

    * ``scalar`` pops from ``scalar_queue`` (first-in-first-out)
    * ``scalars`` pops from ``scalars_queue`` (returns a ``FakeResult``)
    * ``flush`` is a no-op awaitable — tracks call count
    """

    def __init__(
        self,
        *,
        scalar_queue: list[Any] | None = None,
        scalars_queue: list[list[Any]] | None = None,
    ) -> None:
        self.scalar_queue = list(scalar_queue or [])
        self.scalars_queue = list(scalars_queue or [])
        self.flush_calls = 0

    async def scalar(self, _stmt: Any) -> Any:
        if not self.scalar_queue:
            return None
        return self.scalar_queue.pop(0)

    async def scalars(self, _stmt: Any) -> FakeResult:
        items = self.scalars_queue.pop(0) if self.scalars_queue else []
        return FakeResult(items)

    async def flush(self) -> None:
        self.flush_calls += 1


# ── Tests ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_empty_repairable_codes_short_circuits() -> None:
    """Passing an empty allowlist must not hit the DB."""
    session = FakeSession()
    triggered, codes = await maybe_prepare_chapter_auto_repair(
        session,
        project=FakeProject(),
        chapter=FakeChapter(),
        repairable_codes=(),
    )
    assert triggered is False
    assert codes == ()
    assert session.flush_calls == 0


@pytest.mark.asyncio
async def test_no_latest_report_returns_no_trigger() -> None:
    session = FakeSession(scalar_queue=[None])  # no ChapterQualityReportModel
    triggered, codes = await maybe_prepare_chapter_auto_repair(
        session,
        project=FakeProject(),
        chapter=FakeChapter(),
        repairable_codes=("BLOCK_LOW", "BLOCK_HIGH"),
    )
    assert triggered is False
    assert codes == ()


@pytest.mark.asyncio
async def test_report_without_blocking_codes_returns_no_trigger() -> None:
    """If the latest report was a passing one, no auto-repair."""
    report = FakeQualityReport(report_json={"blocking_codes": []})
    session = FakeSession(scalar_queue=[report])
    triggered, codes = await maybe_prepare_chapter_auto_repair(
        session,
        project=FakeProject(),
        chapter=FakeChapter(),
        repairable_codes=("BLOCK_LOW",),
    )
    assert triggered is False
    assert codes == ()


@pytest.mark.asyncio
async def test_non_repairable_code_returns_the_codes_but_not_triggered() -> None:
    """A deterministic block (e.g. naming) must surface the codes but
    *not* trigger auto-repair — user intervention is required."""
    report = FakeQualityReport(
        report_json={"blocking_codes": ["NAMING", "DIALOG_INTEGRITY"]},
    )
    session = FakeSession(scalar_queue=[report])
    triggered, codes = await maybe_prepare_chapter_auto_repair(
        session,
        project=FakeProject(),
        chapter=FakeChapter(),
        repairable_codes=("BLOCK_LOW", "BLOCK_HIGH"),
    )
    assert triggered is False
    # Codes are still returned so callers can log what blocked.
    assert set(codes) == {"NAMING", "DIALOG_INTEGRITY"}


@pytest.mark.asyncio
async def test_character_resurrection_removes_dead_participants_before_regen() -> None:
    chapter = FakeChapter(
        chapter_number=371,
        metadata_json={
            "blocked_by_write_safety_gate": True,
            "write_safety_block_code": "character_resurrection",
            "write_safety_hint": "Sam Blake died in chapter 323.",
        },
    )
    scenes = [
        FakeScene(
            chapter_id=chapter.id,
            scene_number=1,
            participants=["Rowan Ashford", "Sam Blake"],
            entry_state={
                "Rowan Ashford": {"arc_state": "cornered"},
                "Sam Blake": {"arc_state": "active"},
            },
            exit_state={
                "Rowan Ashford": {"arc_state": "chooses"},
                "Sam Blake": {"arc_state": "presses"},
            },
        ),
        FakeScene(
            chapter_id=chapter.id,
            scene_number=2,
            participants=["Rowan Ashford"],
        ),
    ]
    session = FakeSession(
        scalar_queue=[None],
        scalars_queue=[scenes, ["Sam Blake"]],
    )

    triggered, codes = await maybe_prepare_chapter_auto_repair(
        session,
        project=FakeProject(id=chapter.project_id),
        chapter=chapter,
        repairable_codes=("character_resurrection",),
    )

    assert triggered is True
    assert codes == ("character_resurrection",)
    assert chapter.production_state == "pending"
    assert "write_safety_block_code" not in chapter.metadata_json
    assert scenes[0].participants == ["Rowan Ashford"]
    assert "Sam Blake" not in scenes[0].entry_state
    assert "Sam Blake" not in scenes[0].exit_state
    assert scenes[0].status == SceneStatus.NEEDS_REWRITE.value
    assert scenes[0].metadata_json["auto_repair_removed_participants"] == [
        "Sam Blake"
    ]
    assert scenes[0].metadata_json["auto_repair_removed_state_refs"] == [
        "Sam Blake"
    ]
    assert "移除已故角色：Sam Blake" in scenes[0].metadata_json["auto_repair_hint"]
    assert scenes[1].participants == ["Rowan Ashford"]
    assert "auto_repair_removed_participants" not in scenes[1].metadata_json
    assert session.flush_calls == 1


@pytest.mark.asyncio
async def test_character_resurrection_removes_dead_state_refs_even_after_participant_was_cleaned() -> None:
    chapter = FakeChapter(
        chapter_number=502,
        metadata_json={
            "blocked_by_write_safety_gate": True,
            "write_safety_block_code": "character_resurrection",
            "write_safety_hint": "陆沉死于第458章。",
        },
    )
    scenes = [
        FakeScene(
            chapter_id=chapter.id,
            scene_number=1,
            participants=["宁尘"],
            entry_state={
                "宁尘": {"arc_state": "重振旗鼓"},
                "陆沉": {"arc_state": "独立行动"},
            },
            exit_state={
                "宁尘": {"arc_state": "做出抉择"},
                "陆沉": {"arc_state": "暗自打算"},
            },
        )
    ]
    session = FakeSession(
        scalar_queue=[None],
        scalars_queue=[scenes, ["陆沉"]],
    )

    triggered, codes = await maybe_prepare_chapter_auto_repair(
        session,
        project=FakeProject(id=chapter.project_id),
        chapter=chapter,
        repairable_codes=("character_resurrection",),
    )

    assert triggered is True
    assert codes == ("character_resurrection",)
    assert scenes[0].participants == ["宁尘"]
    assert scenes[0].entry_state == {"宁尘": {"arc_state": "重振旗鼓"}}
    assert scenes[0].exit_state == {"宁尘": {"arc_state": "做出抉择"}}
    assert scenes[0].metadata_json["auto_repair_removed_state_refs"] == ["陆沉"]
    assert "auto_repair_removed_participants" not in scenes[0].metadata_json


@pytest.mark.asyncio
async def test_block_low_resets_scenes_and_injects_hint() -> None:
    chapter = FakeChapter()
    scenes = [
        FakeScene(chapter_id=chapter.id, scene_number=1, target_word_count=1500),
        FakeScene(chapter_id=chapter.id, scene_number=2, target_word_count=1600),
    ]
    report = FakeQualityReport(
        report_json={
            "blocking_codes": ["BLOCK_LOW"],
            "length_stability": {
                "word_count": 3500,
                "target_words": 6400,
                "band": "BLOCK_LOW",
            },
        },
    )
    session = FakeSession(scalar_queue=[report], scalars_queue=[scenes])

    triggered, codes = await maybe_prepare_chapter_auto_repair(
        session,
        project=FakeProject(),
        chapter=chapter,
        repairable_codes=("BLOCK_LOW", "BLOCK_HIGH"),
    )

    assert triggered is True
    assert set(codes) == {"BLOCK_LOW"}
    assert chapter.production_state == "pending"
    # Every scene is reset to NEEDS_REWRITE with the hint attached.
    for scene in scenes:
        assert scene.status == SceneStatus.NEEDS_REWRITE.value
        hint = scene.metadata_json.get("auto_repair_hint")
        assert isinstance(hint, str)
        assert "大幅扩写" in hint  # Chinese hint for BLOCK_LOW
        assert scene.metadata_json.get("auto_repair_block_codes") == ["BLOCK_LOW"]
    # target_word_count is bumped proportionally to the shortfall.
    # target=6400, wc=3500 → ratio=1.82, capped to 1.5.
    assert scenes[0].target_word_count == int(round(1500 * 1.5))
    assert scenes[1].target_word_count == int(round(1600 * 1.5))
    # regen_attempts tick incremented on the underlying report row.
    assert report.regen_attempts == 1
    assert session.flush_calls == 1


@pytest.mark.asyncio
async def test_legacy_length_block_low_alias_triggers_block_low_repair() -> None:
    chapter = FakeChapter()
    scenes = [
        FakeScene(chapter_id=chapter.id, scene_number=1, target_word_count=1000),
    ]
    report = FakeQualityReport(
        report_json={
            "blocking_codes": ["CHAPTER_LENGTH_BLOCK_LOW"],
            "length_stability": {"word_count": 1200, "target_words": 2400},
        },
    )
    session = FakeSession(scalar_queue=[report], scalars_queue=[scenes])

    triggered, codes = await maybe_prepare_chapter_auto_repair(
        session,
        project=FakeProject(),
        chapter=chapter,
        repairable_codes=("BLOCK_LOW",),
    )

    assert triggered is True
    assert codes == ("CHAPTER_LENGTH_BLOCK_LOW",)
    assert chapter.production_state == "pending"
    assert scenes[0].status == SceneStatus.NEEDS_REWRITE.value
    assert "大幅扩写" in scenes[0].metadata_json["auto_repair_hint"]
    assert scenes[0].target_word_count == 1500


@pytest.mark.asyncio
async def test_dialog_unpaired_triggers_dialog_rewrite_hint() -> None:
    chapter = FakeChapter()
    scenes = [FakeScene(chapter_id=chapter.id, scene_number=1)]
    report = FakeQualityReport(report_json={"blocking_codes": ["DIALOG_UNPAIRED"]})
    session = FakeSession(scalar_queue=[report], scalars_queue=[scenes])

    triggered, codes = await maybe_prepare_chapter_auto_repair(
        session,
        project=FakeProject(),
        chapter=chapter,
        repairable_codes=("DIALOG_UNPAIRED",),
    )

    assert triggered is True
    assert codes == ("DIALOG_UNPAIRED",)
    assert chapter.production_state == "pending"
    assert scenes[0].status == SceneStatus.NEEDS_REWRITE.value
    assert "未闭合或孤立的对话标记" in scenes[0].metadata_json["auto_repair_hint"]


@pytest.mark.asyncio
async def test_weak_ending_triggers_hook_rewrite_hint() -> None:
    chapter = FakeChapter()
    scenes = [FakeScene(chapter_id=chapter.id, scene_number=1)]
    report = FakeQualityReport(report_json={"blocking_codes": ["ENDING_SENTENCE_WEAK"]})
    session = FakeSession(scalar_queue=[report], scalars_queue=[scenes])

    triggered, codes = await maybe_prepare_chapter_auto_repair(
        session,
        project=FakeProject(),
        chapter=chapter,
        repairable_codes=("ENDING_SENTENCE_WEAK",),
    )

    assert triggered is True
    assert codes == ("ENDING_SENTENCE_WEAK",)
    assert chapter.production_state == "pending"
    assert scenes[0].status == SceneStatus.NEEDS_REWRITE.value
    assert "章节结尾缺少明确钩子" in scenes[0].metadata_json["auto_repair_hint"]


@pytest.mark.asyncio
async def test_block_high_resets_scenes_with_trim_hint_no_target_bump() -> None:
    chapter = FakeChapter()
    scenes = [
        FakeScene(chapter_id=chapter.id, scene_number=1, target_word_count=1600),
    ]
    report = FakeQualityReport(
        report_json={
            "blocking_codes": ["BLOCK_HIGH"],
            "length_stability": {
                "word_count": 9500,
                "target_words": 6400,
                "band": "BLOCK_HIGH",
            },
        },
    )
    session = FakeSession(scalar_queue=[report], scalars_queue=[scenes])

    triggered, codes = await maybe_prepare_chapter_auto_repair(
        session,
        project=FakeProject(),
        chapter=chapter,
        repairable_codes=("BLOCK_LOW", "BLOCK_HIGH"),
    )

    assert triggered is True
    assert set(codes) == {"BLOCK_HIGH"}
    scene = scenes[0]
    assert scene.status == SceneStatus.NEEDS_REWRITE.value
    hint = scene.metadata_json.get("auto_repair_hint")
    assert isinstance(hint, str)
    assert "压缩冗余" in hint
    # BLOCK_HIGH must NOT bump target_word_count — that would make
    # the next draft even longer.
    assert scene.target_word_count == 1600


@pytest.mark.asyncio
async def test_existing_auto_repair_hint_is_preserved_across_cycles() -> None:
    """Successive repair cycles must stack hints, not wipe them."""
    chapter = FakeChapter()
    scenes = [
        FakeScene(
            chapter_id=chapter.id,
            scene_number=1,
            metadata_json={"auto_repair_hint": "first-cycle hint"},
            target_word_count=1500,
        ),
    ]
    report = FakeQualityReport(
        report_json={
            "blocking_codes": ["BLOCK_LOW"],
            "length_stability": {"word_count": 4000, "target_words": 6400},
        },
    )
    session = FakeSession(scalar_queue=[report], scalars_queue=[scenes])

    triggered, _ = await maybe_prepare_chapter_auto_repair(
        session,
        project=FakeProject(),
        chapter=chapter,
        repairable_codes=("BLOCK_LOW",),
    )
    assert triggered is True
    merged = scenes[0].metadata_json["auto_repair_hint"]
    assert merged.startswith("first-cycle hint\n")
    assert "大幅扩写" in merged


@pytest.mark.asyncio
async def test_scene_query_failure_is_non_fatal() -> None:
    """If the scene query explodes mid-flight, no changes happen and the
    helper returns (False, ())."""

    class ExplodingSession(FakeSession):
        async def scalar(self, _stmt: Any) -> Any:
            raise RuntimeError("db down")

    session = ExplodingSession()
    triggered, codes = await maybe_prepare_chapter_auto_repair(
        session,
        project=FakeProject(),
        chapter=FakeChapter(),
        repairable_codes=("BLOCK_LOW",),
    )
    assert triggered is False
    assert codes == ()
    assert session.flush_calls == 0


@pytest.mark.asyncio
async def test_mixed_blocking_codes_picks_only_repairable_subset() -> None:
    """When the chapter fails on both a repairable and a non-repairable
    code, we only auto-repair the former but return *all* codes in the
    report — upstream logging needs the full picture."""
    chapter = FakeChapter()
    scenes = [FakeScene(chapter_id=chapter.id, scene_number=1)]
    report = FakeQualityReport(
        report_json={
            "blocking_codes": ["BLOCK_LOW", "NAMING"],
            "length_stability": {"word_count": 3800, "target_words": 6400},
        },
    )
    session = FakeSession(scalar_queue=[report], scalars_queue=[scenes])
    triggered, codes = await maybe_prepare_chapter_auto_repair(
        session,
        project=FakeProject(),
        chapter=chapter,
        repairable_codes=("BLOCK_LOW", "BLOCK_HIGH"),
    )
    assert triggered is True
    # All upstream blocking codes surface for logging.
    assert set(codes) == {"BLOCK_LOW", "NAMING"}
    # But only the BLOCK_LOW hint was applied.
    hint = scenes[0].metadata_json["auto_repair_hint"]
    assert "大幅扩写" in hint
    assert scenes[0].metadata_json["auto_repair_block_codes"] == ["BLOCK_LOW"]
