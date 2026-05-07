"""Tests for ``services.death_ripple`` — propagation of grief / closure
events from a freshly-deceased character into the people who knew them.

These tests use a hand-rolled fake session that records writes and
serves a configured set of relationship / character rows, so we can
verify behaviour deterministically without spinning up Postgres.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID, uuid4

import pytest

from bestseller.services.death_ripple import (
    ENMITY_THRESHOLD,
    GRIEF_THRESHOLD,
    apply_death_ripple,
    apply_death_ripples_for_chapter,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Lightweight fakes — just enough surface for the service.
# ---------------------------------------------------------------------------


@dataclass
class _Char:
    name: str
    id: UUID = field(default_factory=uuid4)
    alive_status: str = "alive"
    death_chapter_number: int | None = None
    metadata_json: dict[str, Any] | None = None


@dataclass
class _Rel:
    character_a_id: UUID
    character_b_id: UUID
    relationship_type: str
    strength: float
    id: UUID = field(default_factory=uuid4)
    metadata_json: dict[str, Any] | None = None
    last_changed_chapter_no: int | None = None


class _FakeSession:
    """Minimal session double that captures writes and serves
    pre-configured queries for the death-ripple service. The matching
    is positional rather than SQL-aware: the service issues queries in
    a known order, so we just hand each query a pre-baked answer."""

    def __init__(
        self,
        *,
        rels_for_deceased: list[_Rel],
        characters_by_id: dict[UUID, _Char],
        existing_snapshots: list[Any] | None = None,
        existing_events: list[Any] | None = None,
    ) -> None:
        self.rels_for_deceased = list(rels_for_deceased)
        self.characters_by_id = dict(characters_by_id)
        self.existing_snapshots = list(existing_snapshots or [])
        self.existing_events = list(existing_events or [])
        self.added: list[Any] = []
        self.executed: list[tuple[str, dict[str, Any]]] = []
        # Populated dynamically per-test if needed.
        self._scalar_calls = 0

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        for obj in self.added:
            if getattr(obj, "id", None) is None:
                obj.id = uuid4()

    async def get(self, model: Any, key: Any) -> Any:
        return self.characters_by_id.get(key)

    async def scalar(self, stmt: Any) -> Any:
        # Order: existing-grief-snapshot lookups, then existing-event
        # lookups. For simplicity we hand them out in input order;
        # tests with multiple ripples must list every expected
        # response. Falling back to None means "no row exists" which
        # produces a fresh write.
        text = str(stmt).lower()
        if "character_state_snapshots" in text:
            if self.existing_snapshots:
                return self.existing_snapshots.pop(0)
            return None
        if "relationship_events" in text:
            if self.existing_events:
                return self.existing_events.pop(0)
            return None
        return None

    async def scalars(self, stmt: Any) -> list[Any]:
        # apply_death_ripple now performs two scalars() calls per
        # death: the relationship listing for ripples, then the
        # interpersonal-promises listing for the rollup. Route on
        # table name so each call returns the right rows.
        text = str(stmt).lower()
        if "from interpersonal_promises" in text:
            return []  # no promises in these tests
        if "from relationships" in text:
            return list(self.rels_for_deceased)
        return list(self.rels_for_deceased)

    async def execute(self, stmt: Any) -> Any:
        # Capture update statements for inspection. The service uses
        # ``update(...).where(...).values(...)`` — we record the str
        # representation as evidence.
        text = str(stmt)
        self.executed.append(("execute", {"sql": text[:200]}))
        # Return a non-None to satisfy await-style call sites.
        return None


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestApplyDeathRipple:
    @pytest.mark.asyncio
    async def test_high_strength_relationship_emits_grief(self) -> None:
        deceased = _Char(name="师父", alive_status="deceased",
                         death_chapter_number=100)
        survivor = _Char(name="徒弟")
        rel = _Rel(
            character_a_id=deceased.id,
            character_b_id=survivor.id,
            relationship_type="master-disciple",
            strength=0.8,
        )
        session = _FakeSession(
            rels_for_deceased=[rel],
            characters_by_id={deceased.id: deceased, survivor.id: survivor},
        )

        report = await apply_death_ripple(
            session,
            project_id=uuid4(),
            deceased=deceased,
            chapter_number=100,
        )

        assert len(report.entries) == 1
        e = report.entries[0]
        assert e.survivor_name == "徒弟"
        assert e.response_kind == "grief"
        assert e.snapshot_created is True
        assert e.relationship_event_created is True
        assert e.relationship_marked_ended is True
        # One snapshot + one event in session.added
        assert len(session.added) == 2
        # Plus one execute() — the RelationshipModel.metadata update.
        assert len(session.executed) == 1

    @pytest.mark.asyncio
    async def test_strong_negative_relationship_emits_vengeance_closure(self) -> None:
        deceased = _Char(name="宿敌", alive_status="deceased",
                         death_chapter_number=100)
        protagonist = _Char(name="主角")
        rel = _Rel(
            character_a_id=protagonist.id,
            character_b_id=deceased.id,
            relationship_type="rivalry",
            strength=-0.8,
        )
        session = _FakeSession(
            rels_for_deceased=[rel],
            characters_by_id={deceased.id: deceased, protagonist.id: protagonist},
        )

        report = await apply_death_ripple(
            session,
            project_id=uuid4(),
            deceased=deceased,
            chapter_number=100,
        )

        assert len(report.entries) == 1
        assert report.entries[0].response_kind == "vengeance_closure"
        assert report.vengeance_closure_count == 1
        assert report.grief_count == 0

    @pytest.mark.asyncio
    async def test_weak_relationship_does_not_ripple(self) -> None:
        deceased = _Char(name="过路人", alive_status="deceased",
                         death_chapter_number=100)
        acquaintance = _Char(name="路边摊主")
        rel = _Rel(
            character_a_id=deceased.id,
            character_b_id=acquaintance.id,
            relationship_type="acquaintance",
            strength=0.1,  # below GRIEF_THRESHOLD
        )
        session = _FakeSession(
            rels_for_deceased=[rel],
            characters_by_id={
                deceased.id: deceased,
                acquaintance.id: acquaintance,
            },
        )
        report = await apply_death_ripple(
            session,
            project_id=uuid4(),
            deceased=deceased,
            chapter_number=100,
        )
        assert report.entries == ()
        assert session.added == []
        assert session.executed == []

    @pytest.mark.asyncio
    async def test_already_deceased_survivor_is_skipped(self) -> None:
        # Two friends, one already dead before the new death — the
        # newly-dead character's ripple should not propagate to them.
        deceased = _Char(name="主角", alive_status="deceased",
                         death_chapter_number=100)
        also_dead = _Char(name="老友", alive_status="deceased",
                          death_chapter_number=80)
        rel = _Rel(
            character_a_id=deceased.id,
            character_b_id=also_dead.id,
            relationship_type="friend",
            strength=0.7,
        )
        session = _FakeSession(
            rels_for_deceased=[rel],
            characters_by_id={deceased.id: deceased, also_dead.id: also_dead},
        )
        report = await apply_death_ripple(
            session,
            project_id=uuid4(),
            deceased=deceased,
            chapter_number=100,
        )
        assert report.entries == ()

    @pytest.mark.asyncio
    async def test_idempotent_when_snapshot_already_exists(self) -> None:
        deceased = _Char(name="师父", alive_status="deceased",
                         death_chapter_number=100)
        survivor = _Char(name="徒弟")
        rel = _Rel(
            character_a_id=deceased.id,
            character_b_id=survivor.id,
            relationship_type="master-disciple",
            strength=0.9,
        )
        # Pretend a prior run wrote both the snapshot and the event.
        session = _FakeSession(
            rels_for_deceased=[rel],
            characters_by_id={deceased.id: deceased, survivor.id: survivor},
            existing_snapshots=[object()],   # fake existing snapshot
            existing_events=[object()],      # fake existing event
        )
        # Mark the relationship as already ended so the metadata
        # update is also skipped.
        rel.metadata_json = {"ended_by_death": True}

        report = await apply_death_ripple(
            session,
            project_id=uuid4(),
            deceased=deceased,
            chapter_number=100,
        )
        assert len(report.entries) == 1
        e = report.entries[0]
        assert e.snapshot_created is False
        assert e.relationship_event_created is False
        assert e.relationship_marked_ended is False
        # The ripple itself should not have written anything new.
        assert session.added == []
        # NOTE: the interpersonal_promises rollup may issue its own
        # update statements even when no promises exist (no-op execute
        # is acceptable). We assert no RIPPLE-related writes by
        # checking the captured SQL contents.
        ripple_writes = [
            ev for ev in session.executed
            if "interpersonal_promises" not in ev[1].get("sql", "")
        ]
        assert ripple_writes == []

    @pytest.mark.asyncio
    async def test_thresholds_visible_for_doc_consumers(self) -> None:
        # Sanity: the thresholds are exported and have sensible values.
        assert 0.0 < GRIEF_THRESHOLD < 1.0
        assert -1.0 < ENMITY_THRESHOLD < 0.0


class TestApplyDeathRipplesForChapter:
    @pytest.mark.asyncio
    async def test_propagates_multiple_deaths_in_one_chapter(self) -> None:
        # Two deaths in the same chapter (a battle), each with its own
        # high-strength survivor.
        d1 = _Char(name="死者甲", alive_status="deceased", death_chapter_number=200)
        d2 = _Char(name="死者乙", alive_status="deceased", death_chapter_number=200)
        s1 = _Char(name="妻子")
        s2 = _Char(name="挚友")
        # Each death has one strong relationship.
        r1 = _Rel(d1.id, s1.id, "marriage", 0.9)
        r2 = _Rel(d2.id, s2.id, "best_friend", 0.7)

        # The service queries relationships per death; we hand each
        # death its own session view by closing over a single fake
        # that returns different rels based on call sequence.

        class _MultiSession(_FakeSession):
            def __init__(self) -> None:
                super().__init__(
                    rels_for_deceased=[],  # populated per-death below
                    characters_by_id={
                        d1.id: d1, d2.id: d2, s1.id: s1, s2.id: s2,
                    },
                )
                self._call_idx = 0
                self._batches = [[r1], [r2]]

            async def scalars(self, stmt: Any) -> list[Any]:
                # Two scalars() per death: relationships then promises.
                # Promises always returns []; relationships uses the
                # batch queue.
                text = str(stmt).lower()
                if "from interpersonal_promises" in text:
                    return []
                if self._call_idx >= len(self._batches):
                    return []
                batch = self._batches[self._call_idx]
                self._call_idx += 1
                return list(batch)

        session = _MultiSession()
        reports = await apply_death_ripples_for_chapter(
            session,
            project_id=uuid4(),
            deceased_character_ids=[d1.id, d2.id],
            chapter_number=200,
        )
        assert len(reports) == 2
        all_survivors = sorted(
            entry.survivor_name
            for r in reports for entry in r.entries
        )
        assert all_survivors == ["妻子", "挚友"]
