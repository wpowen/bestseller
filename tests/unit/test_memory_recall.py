"""Tests for ``services.memory_recall`` — the post-death memory cue
scheduler that keeps the cast from forgetting the dead.

The schedule fires cues at offsets of ``+3 / +10 / +30 / +80`` chapters
past each death (with a 1-chapter window per anchor). Only relationships
whose strength meets ``RECALL_STRENGTH_THRESHOLD`` produce cues — the
goal is naturalistic mourning, not OCD reminders for every passing
acquaintance.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID, uuid4

import pytest

from bestseller.services.memory_recall import (
    RECALL_STRENGTH_THRESHOLD,
    MemoryRecallCue,
    compute_memory_recall_cues,
    render_memory_recall_block,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


@dataclass
class _Char:
    name: str
    id: UUID = field(default_factory=uuid4)
    alive_status: str = "alive"
    death_chapter_number: int | None = None
    role: str | None = None


@dataclass
class _Rel:
    character_a_id: UUID
    character_b_id: UUID
    relationship_type: str
    strength: float


class _FakeSession:
    def __init__(
        self,
        *,
        deceased_rows: list[_Char],
        rels_by_deceased: dict[UUID, list[_Rel]],
        characters_by_id: dict[UUID, _Char],
    ) -> None:
        self.deceased_rows = list(deceased_rows)
        self.rels_by_deceased = dict(rels_by_deceased)
        self.characters_by_id = dict(characters_by_id)
        # The service queries deceased rows once, then per-deceased
        # relationship rows in the same order; we use a tiny FIFO.
        self._pending_rels: list[list[_Rel]] = []

    async def scalars(self, stmt: Any) -> list[Any]:
        text = str(stmt).lower()
        # The service issues queries in this order:
        #   1. characters (deceased) — table name "characters"
        #   2. relationships (per deceased) — table name "relationships"
        # We match on table name to route correctly.
        if "from relationships" in text:
            if self._pending_rels:
                return self._pending_rels.pop(0)
            return []
        if "from characters" in text:
            self._pending_rels = [
                self.rels_by_deceased.get(d.id, [])
                for d in self.deceased_rows
            ]
            return list(self.deceased_rows)
        return []

    async def get(self, model: Any, key: Any) -> Any:
        return self.characters_by_id.get(key)


# ---------------------------------------------------------------------------
# compute_memory_recall_cues
# ---------------------------------------------------------------------------


class TestComputeMemoryRecallCues:
    @pytest.mark.asyncio
    async def test_no_deceased_returns_empty(self) -> None:
        session = _FakeSession(
            deceased_rows=[], rels_by_deceased={}, characters_by_id={},
        )
        cues = await compute_memory_recall_cues(
            session, project_id=uuid4(), chapter_number=50,
        )
        assert cues == []

    @pytest.mark.asyncio
    async def test_anchor_plus_3_fires_acute_cue(self) -> None:
        deceased = _Char(
            name="师父", alive_status="deceased",
            death_chapter_number=100, role="mentor",
        )
        survivor = _Char(name="徒弟")
        rel = _Rel(deceased.id, survivor.id, "master-disciple", 0.9)
        session = _FakeSession(
            deceased_rows=[deceased],
            rels_by_deceased={deceased.id: [rel]},
            characters_by_id={deceased.id: deceased, survivor.id: survivor},
        )
        cues = await compute_memory_recall_cues(
            session, project_id=uuid4(), chapter_number=103,  # +3
        )
        assert len(cues) == 1
        c = cues[0]
        assert c.survivor_name == "徒弟"
        assert c.deceased_name == "师父"
        assert c.intensity == "acute"
        assert c.chapters_since_death == 3

    @pytest.mark.asyncio
    async def test_anchor_plus_10_fires_fresh_cue(self) -> None:
        deceased = _Char(
            name="爱人", alive_status="deceased",
            death_chapter_number=100, role="lover",
        )
        survivor = _Char(name="主角")
        rel = _Rel(deceased.id, survivor.id, "lover", 0.95)
        session = _FakeSession(
            deceased_rows=[deceased],
            rels_by_deceased={deceased.id: [rel]},
            characters_by_id={deceased.id: deceased, survivor.id: survivor},
        )
        cues = await compute_memory_recall_cues(
            session, project_id=uuid4(), chapter_number=110,
        )
        assert len(cues) == 1
        assert cues[0].intensity == "fresh"

    @pytest.mark.asyncio
    async def test_off_anchor_chapter_produces_nothing(self) -> None:
        deceased = _Char(
            name="师父", alive_status="deceased",
            death_chapter_number=100, role="mentor",
        )
        survivor = _Char(name="徒弟")
        rel = _Rel(deceased.id, survivor.id, "master-disciple", 0.9)
        session = _FakeSession(
            deceased_rows=[deceased],
            rels_by_deceased={deceased.id: [rel]},
            characters_by_id={deceased.id: deceased, survivor.id: survivor},
        )
        cues = await compute_memory_recall_cues(
            session, project_id=uuid4(), chapter_number=120,  # offset 20, not on anchor
        )
        assert cues == []

    @pytest.mark.asyncio
    async def test_weak_relationship_does_not_produce_cue(self) -> None:
        deceased = _Char(
            name="路边摊主", alive_status="deceased",
            death_chapter_number=100,
        )
        protag = _Char(name="主角")
        rel = _Rel(deceased.id, protag.id, "acquaintance", 0.1)
        session = _FakeSession(
            deceased_rows=[deceased],
            rels_by_deceased={deceased.id: [rel]},
            characters_by_id={deceased.id: deceased, protag.id: protag},
        )
        cues = await compute_memory_recall_cues(
            session, project_id=uuid4(), chapter_number=103,
        )
        assert cues == []

    @pytest.mark.asyncio
    async def test_anniversary_anchor_at_plus_80(self) -> None:
        deceased = _Char(
            name="父亲", alive_status="deceased",
            death_chapter_number=20, role="father",
        )
        protag = _Char(name="主角")
        rel = _Rel(deceased.id, protag.id, "parent", 0.9)
        session = _FakeSession(
            deceased_rows=[deceased],
            rels_by_deceased={deceased.id: [rel]},
            characters_by_id={deceased.id: deceased, protag.id: protag},
        )
        cues = await compute_memory_recall_cues(
            session, project_id=uuid4(), chapter_number=100,  # +80
        )
        assert len(cues) == 1
        assert cues[0].intensity == "anniversary"

    @pytest.mark.asyncio
    async def test_dead_survivor_is_skipped(self) -> None:
        deceased = _Char(
            name="A", alive_status="deceased", death_chapter_number=10,
        )
        also_dead = _Char(
            name="B", alive_status="deceased", death_chapter_number=12,
        )
        rel = _Rel(deceased.id, also_dead.id, "spouse", 0.9)
        session = _FakeSession(
            deceased_rows=[deceased],
            rels_by_deceased={deceased.id: [rel]},
            characters_by_id={deceased.id: deceased, also_dead.id: also_dead},
        )
        cues = await compute_memory_recall_cues(
            session, project_id=uuid4(), chapter_number=13,  # +3 from A
        )
        # B is also dead → no cue
        assert cues == []

    @pytest.mark.asyncio
    async def test_max_cues_respected(self) -> None:
        # Multiple deceased + multiple high-strength rels — the cap
        # keeps the prompt block small.
        d1 = _Char(name="X", alive_status="deceased", death_chapter_number=100)
        d2 = _Char(name="Y", alive_status="deceased", death_chapter_number=100)
        survivors = [
            _Char(name=f"S{i}") for i in range(6)
        ]
        rels1 = [_Rel(d1.id, s.id, "ally", 0.9) for s in survivors[:3]]
        rels2 = [_Rel(d2.id, s.id, "ally", 0.9) for s in survivors[3:]]

        chars = {d1.id: d1, d2.id: d2}
        chars.update({s.id: s for s in survivors})

        session = _FakeSession(
            deceased_rows=[d1, d2],
            rels_by_deceased={d1.id: rels1, d2.id: rels2},
            characters_by_id=chars,
        )
        cues = await compute_memory_recall_cues(
            session, project_id=uuid4(), chapter_number=103,
            max_cues=3,
        )
        assert len(cues) == 3


class TestRenderMemoryRecallBlock:
    def test_empty_cues_returns_empty_string(self) -> None:
        assert render_memory_recall_block([]) == ""

    def test_zh_block_includes_cue_details(self) -> None:
        cue = MemoryRecallCue(
            survivor_name="徒弟",
            deceased_name="师父",
            deceased_role="mentor",
            relationship_type="master-disciple",
            relationship_strength=0.9,
            chapters_since_death=3,
            intensity="acute",
        )
        block = render_memory_recall_block([cue], language="zh-CN")
        assert "死后记忆提示" in block
        assert "徒弟" in block
        assert "师父" in block
        assert "新丧之痛" in block

    def test_en_block(self) -> None:
        cue = MemoryRecallCue(
            survivor_name="Disciple",
            deceased_name="Master",
            deceased_role="mentor",
            relationship_type="master-disciple",
            relationship_strength=0.9,
            chapters_since_death=3,
            intensity="acute",
        )
        block = render_memory_recall_block([cue], language="en-US")
        assert "Memory recall cues" in block
        assert "Disciple" in block
        assert "Master" in block

    def test_threshold_constant_is_documented(self) -> None:
        # Guard: thresholds drift if someone tunes them — keep one
        # canonical place.
        assert 0.0 < RECALL_STRENGTH_THRESHOLD < 1.0
