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

    def __init__(self, items: list[Any], *, rowcount: int | None = None) -> None:
        self._items = list(items)
        self.rowcount = rowcount

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
        self.execute_calls: list[Any] = []

    async def scalar(self, _stmt: Any) -> Any:
        if not self.scalar_queue:
            return None
        return self.scalar_queue.pop(0)

    async def scalars(self, _stmt: Any) -> FakeResult:
        items = self.scalars_queue.pop(0) if self.scalars_queue else []
        return FakeResult(items)

    async def execute(self, stmt: Any) -> FakeResult:
        self.execute_calls.append(stmt)
        return FakeResult([], rowcount=1)

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
async def test_stored_duplicate_block_triggers_repair_even_with_latest_report() -> None:
    chapter = FakeChapter(
        metadata_json={
            "blocked_by_write_safety_gate": True,
            "write_safety_block_code": "CROSS_CHAPTER_REPETITION",
            "write_safety_hint": "第30章复用了第29章段落。",
            "post_assembly_duplicate_gate": {"status": "blocked"},
        },
    )
    scene = FakeScene(chapter_id=chapter.id, scene_number=1)
    report = FakeQualityReport(report_json={"blocking_codes": []})
    session = FakeSession(scalar_queue=[report], scalars_queue=[[scene]])

    triggered, codes = await maybe_prepare_chapter_auto_repair(
        session,
        project=FakeProject(),
        chapter=chapter,
        repairable_codes=("CROSS_CHAPTER_REPETITION",),
    )

    assert triggered is True
    assert codes == ("CROSS_CHAPTER_REPETITION",)
    assert chapter.production_state == "pending"
    assert scene.status == SceneStatus.NEEDS_REWRITE.value
    assert "第30章复用了第29章段落" in scene.metadata_json["auto_repair_hint"]
    assert "post_assembly_duplicate_gate" not in chapter.metadata_json
    assert len(session.execute_calls) == 1


@pytest.mark.asyncio
async def test_stored_pronoun_mismatch_block_triggers_rewrite_repair() -> None:
    chapter = FakeChapter(
        metadata_json={
            "blocked_by_write_safety_gate": True,
            "write_safety_block_code": "pronoun_mismatch",
            "write_safety_hint": "叶长青: expected 他, found 她的",
        },
    )
    scene = FakeScene(chapter_id=chapter.id, scene_number=4)
    session = FakeSession(scalar_queue=[None], scalars_queue=[[scene]])

    triggered, codes = await maybe_prepare_chapter_auto_repair(
        session,
        project=FakeProject(),
        chapter=chapter,
        repairable_codes=("pronoun_mismatch",),
    )

    assert triggered is True
    assert codes == ("pronoun_mismatch",)
    assert chapter.production_state == "pending"
    assert scene.status == SceneStatus.NEEDS_REWRITE.value
    assert "叶长青: expected 他, found 她的" in scene.metadata_json["auto_repair_hint"]
    assert scene.metadata_json["auto_repair_block_codes"] == ["pronoun_mismatch"]
    assert len(session.execute_calls) == 1


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
    assert "移除当下不可登场角色：Sam Blake" in scenes[0].metadata_json["auto_repair_hint"]
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
async def test_dead_alive_block_uses_offstage_character_repair() -> None:
    chapter = FakeChapter(
        chapter_number=7,
        metadata_json={
            "blocked_by_write_safety_gate": True,
            "write_safety_block_code": "dead_alive",
            "write_safety_hint": "母亲 appears to speak or act as if alive.",
        },
    )
    scenes = [
        FakeScene(
            chapter_id=chapter.id,
            scene_number=2,
            participants=["苏砚", "母亲"],
            entry_state={
                "苏砚": {"arc_state": "追查"},
                "母亲": {"arc_state": "当下开口"},
            },
        )
    ]
    session = FakeSession(
        scalar_queue=[None],
        scalars_queue=[scenes, ["母亲"]],
    )

    triggered, codes = await maybe_prepare_chapter_auto_repair(
        session,
        project=FakeProject(id=chapter.project_id),
        chapter=chapter,
        repairable_codes=("dead_alive",),
    )

    assert triggered is True
    assert codes == ("dead_alive",)
    assert scenes[0].participants == ["苏砚"]
    assert scenes[0].entry_state == {"苏砚": {"arc_state": "追查"}}
    assert scenes[0].metadata_json["auto_repair_removed_participants"] == ["母亲"]
    assert scenes[0].metadata_json["auto_repair_removed_state_refs"] == ["母亲"]
    assert "当下不可登场角色：母亲" in scenes[0].metadata_json["auto_repair_hint"]
    assert len(session.execute_calls) == 1


@pytest.mark.asyncio
async def test_character_missing_appearance_removes_offstage_participants_before_regen() -> None:
    chapter = FakeChapter(
        chapter_number=32,
        metadata_json={
            "blocked_by_write_safety_gate": True,
            "write_safety_block_code": "character_missing_appearance",
            "write_safety_hint": "孙九斤从第16章起失踪。",
        },
    )
    scenes = [
        FakeScene(
            chapter_id=chapter.id,
            scene_number=1,
            participants=["林渊", "苏婉宁", "孙九斤", "钱婆婆"],
            entry_state={
                "林渊": {"arc_state": "追查来信"},
                "孙九斤": {"arc_state": "当下协助"},
            },
        )
    ]
    session = FakeSession(
        scalar_queue=[None],
        scalars_queue=[scenes, ["孙九斤"]],
    )

    triggered, codes = await maybe_prepare_chapter_auto_repair(
        session,
        project=FakeProject(id=chapter.project_id),
        chapter=chapter,
        repairable_codes=("character_missing_appearance",),
    )

    assert triggered is True
    assert codes == ("character_missing_appearance",)
    assert scenes[0].participants == ["林渊", "苏婉宁", "钱婆婆"]
    assert scenes[0].entry_state == {"林渊": {"arc_state": "追查来信"}}
    assert scenes[0].metadata_json["auto_repair_removed_participants"] == ["孙九斤"]
    assert scenes[0].metadata_json["auto_repair_removed_state_refs"] == ["孙九斤"]
    assert "当下不可登场角色：孙九斤" in scenes[0].metadata_json["auto_repair_hint"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("block_code", "name", "hint"),
    [
        ("character_sealed_appearance", "陆沉", "陆沉被封印，不能当下行动。"),
        ("character_sleeping_appearance", "沈眠", "沈眠处于沉睡，不能当下对话。"),
        ("character_comatose_appearance", "秦妄", "秦妄处于昏迷，不能当下行动。"),
    ],
)
async def test_non_death_offstage_blocks_remove_participants_before_regen(
    block_code: str,
    name: str,
    hint: str,
) -> None:
    chapter = FakeChapter(
        chapter_number=32,
        metadata_json={
            "blocked_by_write_safety_gate": True,
            "write_safety_block_code": block_code,
            "write_safety_hint": hint,
        },
    )
    scenes = [
        FakeScene(
            chapter_id=chapter.id,
            scene_number=1,
            participants=["宁尘", name],
            entry_state={
                "宁尘": {"arc_state": "追查线索"},
                name: {"arc_state": "当下行动"},
            },
            exit_state={
                "宁尘": {"arc_state": "获得线索"},
                name: {"arc_state": "当下回应"},
            },
        )
    ]
    session = FakeSession(
        scalar_queue=[None],
        scalars_queue=[scenes, [name]],
    )

    triggered, codes = await maybe_prepare_chapter_auto_repair(
        session,
        project=FakeProject(id=chapter.project_id),
        chapter=chapter,
        repairable_codes=(block_code,),
    )

    assert triggered is True
    assert codes == (block_code,)
    assert scenes[0].participants == ["宁尘"]
    assert scenes[0].entry_state == {"宁尘": {"arc_state": "追查线索"}}
    assert scenes[0].exit_state == {"宁尘": {"arc_state": "获得线索"}}
    assert scenes[0].metadata_json["auto_repair_removed_participants"] == [name]
    assert scenes[0].metadata_json["auto_repair_removed_state_refs"] == [name]
    assert "当下不可登场角色" in scenes[0].metadata_json["auto_repair_hint"]
    assert "遗体" not in scenes[0].metadata_json["auto_repair_hint"]
    assert "悲悼" not in scenes[0].metadata_json["auto_repair_hint"]
    assert "昏迷肉身/沉睡身体/封印体/失踪线索" in scenes[0].metadata_json["auto_repair_hint"]


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
    # target_word_count is bumped to at least the per-scene chapter target.
    # target=6400, 2 scenes, attempt 1 floor=6400/2*1.05 → 3360.
    assert scenes[0].target_word_count == 3360
    assert scenes[1].target_word_count == 3360
    # regen_attempts tick incremented on the underlying report row.
    assert report.regen_attempts == 1
    assert session.flush_calls == 1


@pytest.mark.asyncio
async def test_repair_start_clears_stale_exhausted_marker() -> None:
    chapter = FakeChapter(
        metadata_json={
            "auto_repair_exhausted": True,
            "auto_repair_attempts": 3,
            "auto_repair_in_progress": False,
            "auto_accepted": True,
        }
    )
    scenes = [FakeScene(chapter_id=chapter.id, scene_number=1, target_word_count=1000)]
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
        attempt_number=2,
    )

    assert triggered is True
    assert codes == ("CHAPTER_LENGTH_BLOCK_LOW",)
    assert "auto_repair_exhausted" not in chapter.metadata_json
    assert chapter.metadata_json["auto_repair_attempts"] == 2
    assert chapter.metadata_json["auto_repair_in_progress"] is True
    assert chapter.metadata_json["auto_accepted"] is False


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
    assert scenes[0].target_word_count == 2520


@pytest.mark.asyncio
async def test_block_low_after_overlong_trim_restores_publishable_scene_budgets() -> None:
    chapter = FakeChapter(chapter_number=52)
    scenes = [
        FakeScene(chapter_id=chapter.id, scene_number=1, target_word_count=658),
        FakeScene(chapter_id=chapter.id, scene_number=2, target_word_count=658),
        FakeScene(chapter_id=chapter.id, scene_number=3, target_word_count=658),
    ]
    report = FakeQualityReport(
        report_json={
            "blocking_codes": ["CHAPTER_LENGTH_BLOCK_LOW"],
            "length_stability": {
                "word_count": 1295,
                "target_words": 2200,
                "min_words": 1980,
                "max_words": 2420,
            },
        },
    )
    session = FakeSession(scalar_queue=[report], scalars_queue=[scenes])

    triggered, codes = await maybe_prepare_chapter_auto_repair(
        session,
        project=FakeProject(),
        chapter=chapter,
        repairable_codes=("BLOCK_LOW", "BLOCK_HIGH"),
        attempt_number=3,
    )

    assert triggered is True
    assert codes == ("CHAPTER_LENGTH_BLOCK_LOW",)
    # attempt 3 must not keep the prior BLOCK_HIGH-trimmed 658字 budget; it
    # raises each scene back toward the chapter target so the next pass can
    # land inside the 1980-2420 publish range.
    for scene in scenes:
        assert scene.target_word_count == 987
        assert scene.metadata_json["auto_repair_min_scene_target_floor"] == 917
        assert scene.metadata_json["auto_repair_adjusted_target_word_count"] == 987


@pytest.mark.asyncio
async def test_block_low_clamps_legacy_huge_scene_budget() -> None:
    chapter = FakeChapter(chapter_number=347)
    scenes = [
        FakeScene(chapter_id=chapter.id, scene_number=i, target_word_count=1_528_400_259)
        for i in range(1, 5)
    ]
    report = FakeQualityReport(
        report_json={
            "blocking_codes": ["CHAPTER_LENGTH_BLOCK_LOW"],
            "length_stability": {
                "word_count": 2753,
                "target_words": 6400,
                "min_words": 5000,
                "max_words": 7500,
            },
        },
    )
    session = FakeSession(scalar_queue=[report], scalars_queue=[scenes])

    triggered, codes = await maybe_prepare_chapter_auto_repair(
        session,
        project=FakeProject(),
        chapter=chapter,
        repairable_codes=("BLOCK_LOW",),
        attempt_number=3,
    )

    assert triggered is True
    assert codes == ("CHAPTER_LENGTH_BLOCK_LOW",)
    for scene in scenes:
        assert scene.target_word_count == 3200
        assert scene.metadata_json["auto_repair_target_word_count_clamped"] is True
        assert scene.metadata_json["auto_repair_scene_target_cap"] == 3200


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
async def test_canon_forbidden_term_triggers_canon_rewrite_hint() -> None:
    chapter = FakeChapter()
    scenes = [FakeScene(chapter_id=chapter.id, scene_number=1)]
    report = FakeQualityReport(
        report_json={
            "blocking_codes": ["CANON_FORBIDDEN_TERM"],
            "violations": [
                {
                    "code": "CANON_FORBIDDEN_TERM",
                    "detail": "Forbidden canon term appears in chapter: 守夜人",
                }
            ],
        },
    )
    session = FakeSession(scalar_queue=[report], scalars_queue=[scenes])

    triggered, codes = await maybe_prepare_chapter_auto_repair(
        session,
        project=FakeProject(),
        chapter=chapter,
        repairable_codes=("CANON_FORBIDDEN_TERM",),
    )

    assert triggered is True
    assert codes == ("CANON_FORBIDDEN_TERM",)
    assert chapter.production_state == "pending"
    assert scenes[0].status == SceneStatus.NEEDS_REWRITE.value
    hint = scenes[0].metadata_json["auto_repair_hint"]
    assert "已禁止的旧设定" in hint
    assert "守夜人" in hint


@pytest.mark.asyncio
async def test_canon_state_regression_triggers_relationship_rewrite_hint() -> None:
    chapter = FakeChapter()
    scenes = [FakeScene(chapter_id=chapter.id, scene_number=1)]
    report = FakeQualityReport(
        report_json={
            "blocking_codes": ["CANON_STATE_REGRESSION"],
            "violations": [
                {
                    "code": "CANON_STATE_REGRESSION",
                    "detail": "Canon state regression for 林正淳: matched pattern '林正淳.{0,20}(爷爷|祖父)'",
                }
            ],
        },
    )
    session = FakeSession(scalar_queue=[report], scalars_queue=[scenes])

    triggered, codes = await maybe_prepare_chapter_auto_repair(
        session,
        project=FakeProject(),
        chapter=chapter,
        repairable_codes=("CANON_STATE_REGRESSION",),
    )

    assert triggered is True
    assert codes == ("CANON_STATE_REGRESSION",)
    assert chapter.production_state == "pending"
    assert scenes[0].status == SceneStatus.NEEDS_REWRITE.value
    hint = scenes[0].metadata_json["auto_repair_hint"]
    assert "正典人物状态" in hint
    assert "林正淳" in hint
    assert "不得把父亲写成爷爷" in hint


@pytest.mark.asyncio
async def test_naming_out_of_pool_is_not_auto_repaired_by_default() -> None:
    chapter = FakeChapter()
    scenes = [FakeScene(chapter_id=chapter.id, scene_number=1)]
    report = FakeQualityReport(
        report_json={
            "blocking_codes": ["NAMING_OUT_OF_POOL"],
            "violations": [
                {
                    "code": "NAMING_OUT_OF_POOL",
                    "detail": "1 name(s) not in pool: 严评委×3",
                }
            ],
        },
    )
    session = FakeSession(scalar_queue=[report], scalars_queue=[scenes])

    triggered, codes = await maybe_prepare_chapter_auto_repair(
        session,
        project=FakeProject(),
        chapter=chapter,
        repairable_codes=("BLOCK_LOW", "BLOCK_HIGH", "DIALOG_UNPAIRED"),
    )

    assert triggered is False
    assert codes == ("NAMING_OUT_OF_POOL",)
    assert chapter.production_state == "blocked"
    assert scenes[0].status == SceneStatus.APPROVED.value
    assert "auto_repair_hint" not in scenes[0].metadata_json


@pytest.mark.asyncio
async def test_block_high_resets_scenes_with_trim_hint_and_reduced_target() -> None:
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
    # BLOCK_HIGH must lower the concrete generation budget, not only append
    # a prose hint, or the next draft will keep using the stale target.
    assert scene.target_word_count == int(round(1600 * (6400 / 9500)))
    assert scene.metadata_json["auto_repair_original_target_word_count"] == 1600
    assert scene.metadata_json["auto_repair_adjusted_target_word_count"] == scene.target_word_count


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
async def test_auto_repair_clears_current_scene_drafts_for_regeneration() -> None:
    """Resetting scene status is not enough; the next scene pipeline only
    regenerates when there is no current draft."""
    chapter = FakeChapter()
    scenes = [
        FakeScene(chapter_id=chapter.id, scene_number=1),
        FakeScene(chapter_id=chapter.id, scene_number=2),
    ]
    report = FakeQualityReport(
        report_json={
            "blocking_codes": ["BLOCK_LOW"],
            "length_stability": {"word_count": 3000, "target_words": 4800},
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
    assert [scene.status for scene in scenes] == [
        SceneStatus.NEEDS_REWRITE.value,
        SceneStatus.NEEDS_REWRITE.value,
    ]
    assert len(session.execute_calls) == len(scenes)


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
