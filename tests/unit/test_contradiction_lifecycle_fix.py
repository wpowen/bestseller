"""Regression tests for the lifecycle-scanner fixes in
``services/contradiction.py``.

These cover the bugs that produced 880 false-positive findings on
《道种破虚》 (xianxia-upgrade-1776137730) before the rescue plan:

* ``_check_resurrection`` previously fired whenever
  ``character.alive_status == 'deceased'`` regardless of the chapter
  being scanned, smearing post-death state across the entire timeline.
* ``_check_power_tier_regression`` previously hashed unknown tiers when
  ``invariants.power_system.tiers`` was missing, producing random
  rankings (and therefore random "regression" warnings) when old and
  new tier taxonomies coexisted in the snapshot table.

The tests below pin the desired behaviour — death decisions are now
made from ``death_chapter_number`` only, and power-tier comparisons
require a configured ladder, alias-aware canonicalization, and a
≥2-tier gap before warning.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock

import pytest

from bestseller.services.contradiction import (
    _check_power_tier_regression,
    _check_resurrection,
)

pytestmark = pytest.mark.unit


# ── Stub rows ─────────────────────────────────────────────────────────────


@dataclass
class _Project:
    invariants_json: dict[str, Any] | None = None


@dataclass
class _Character:
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    name: str = "陆沉"
    alive_status: str = "alive"
    death_chapter_number: int | None = None
    power_tier: str | None = None
    metadata_json: dict[str, Any] | None = None
    stance: str | None = None
    stance_locked_until_chapter: int | None = None


@dataclass
class _Snapshot:
    chapter_number: int = 0
    scene_number: int | None = None
    power_tier: str | None = None


# ── Session builder ───────────────────────────────────────────────────────


def _make_session(
    *,
    project: _Project,
    characters: dict[str, _Character],
    snapshots_for: dict[uuid.UUID, list[_Snapshot]] | None = None,
) -> AsyncMock:
    """Build an AsyncMock session that mimics the queries the contradiction
    module issues. Sequencing matters: ``_check_resurrection`` and
    ``_check_power_tier_regression`` open a session, fetch the project
    via ``session.get(ProjectModel, project_id)``, then for each scene
    participant look up the character via ``session.scalar(... where
    name == ...)`` and (for power tier) snapshots via
    ``session.scalar(... at chapter ...)`` and ``session.scalars(... <
    chapter ...)``.
    """

    snapshots_for = snapshots_for or {}
    session = AsyncMock()
    session.get = AsyncMock(return_value=project)

    char_by_name = dict(characters)

    async def _scalar(stmt: Any) -> Any:
        text = str(stmt)
        # Character lookup — match the hard-coded WHERE clause shape.
        if "characters" in text and "name" in text:
            params = stmt.compile().params
            for name, character in char_by_name.items():
                if name in params.values():
                    return character
            # Fallback: return first character if name routing fails.
            return next(iter(char_by_name.values()), None)
        # At-chapter snapshot query (single result).
        if "character_state_snapshots" in text and "limit" in text.lower():
            params = stmt.compile().params
            chapter_no = next(
                (v for k, v in params.items() if "chapter_number" in k and isinstance(v, int)),
                None,
            )
            char_id = next(
                (v for k, v in params.items() if "character_id" in k),
                None,
            )
            snaps = snapshots_for.get(char_id, [])
            for snap in snaps:
                if snap.chapter_number == chapter_no and snap.power_tier:
                    return snap
            return None
        return None

    async def _scalars(stmt: Any) -> list[Any]:
        text = str(stmt)
        if "character_state_snapshots" in text:
            params = stmt.compile().params
            char_id = next(
                (v for k, v in params.items() if "character_id" in k),
                None,
            )
            chapter_no = next(
                (
                    v
                    for k, v in params.items()
                    if "chapter_number" in k and isinstance(v, int)
                ),
                None,
            )
            snaps = snapshots_for.get(char_id, [])
            if chapter_no is None:
                return snaps
            return [s for s in snaps if s.chapter_number < chapter_no and s.power_tier]
        return []

    session.scalar.side_effect = _scalar
    session.scalars.side_effect = _scalars
    return session


# ── _check_resurrection ───────────────────────────────────────────────────


class TestResurrection:
    """Locks down the death-check OR-bug fix at contradiction.py:669-673."""

    @pytest.mark.asyncio
    async def test_alive_status_deceased_alone_does_not_fire_in_pre_death_chapter(
        self,
    ) -> None:
        """《道种破虚》 had 311 false positives because the OR branch
        ``alive_status == 'deceased'`` fired in chapter 1 for characters
        who would only die at chapter 458. Pin: alive_status alone must
        NOT trigger when death_chapter_number is unset (or > scan
        chapter)."""

        char = _Character(
            name="陆沉",
            alive_status="deceased",
            death_chapter_number=458,
        )
        session = _make_session(
            project=_Project(),
            characters={"陆沉": char},
        )
        violations, warnings = await _check_resurrection(
            session,
            project_id=uuid.uuid4(),
            chapter_number=100,  # well before chapter 458
            scene_participants=["陆沉"],
        )
        assert violations == []
        assert warnings == []

    @pytest.mark.asyncio
    async def test_real_post_death_appearance_still_fires(self) -> None:
        """The fix must not over-correct: a character who appears in
        chapter 459 after dying at 458 is a real bug and must be flagged."""

        char = _Character(
            name="陆沉",
            alive_status="deceased",
            death_chapter_number=458,
        )
        session = _make_session(
            project=_Project(),
            characters={"陆沉": char},
        )
        violations, warnings = await _check_resurrection(
            session,
            project_id=uuid.uuid4(),
            chapter_number=459,
            scene_participants=["陆沉"],
        )
        assert len(violations) == 1
        assert violations[0].check_type == "character_resurrection"

    @pytest.mark.asyncio
    async def test_no_death_chapter_set_never_fires(self) -> None:
        """Even with alive_status='deceased', if death_chapter_number is
        absent we have no timeline anchor and must stay silent."""

        char = _Character(
            name="某NPC",
            alive_status="deceased",
            death_chapter_number=None,
        )
        session = _make_session(
            project=_Project(),
            characters={"某NPC": char},
        )
        violations, warnings = await _check_resurrection(
            session,
            project_id=uuid.uuid4(),
            chapter_number=200,
            scene_participants=["某NPC"],
        )
        assert violations == []
        assert warnings == []

    @pytest.mark.asyncio
    async def test_flashback_scene_exempts_deceased_appearance(self) -> None:
        """A flashback scene may legitimately stage a deceased character
        as memory, vision, or quoted reference — the resurrection check
        must not flag those passes. Pin the new ``scene`` keyword path."""

        from types import SimpleNamespace

        char = _Character(
            name="陆沉",
            alive_status="deceased",
            death_chapter_number=458,
        )
        session = _make_session(
            project=_Project(),
            characters={"陆沉": char},
        )
        # Real post-death chapter — without scene context this would
        # fire (test_real_post_death_appearance_still_fires pins that).
        flashback_scene = SimpleNamespace(
            scene_type="flashback", metadata_json={}
        )
        violations, warnings = await _check_resurrection(
            session,
            project_id=uuid.uuid4(),
            chapter_number=600,
            scene_participants=["陆沉"],
            scene=flashback_scene,
        )
        assert violations == []
        assert warnings == []

    @pytest.mark.asyncio
    async def test_memorial_scene_metadata_also_exempts(self) -> None:
        """A scene marked via ``metadata_json.scene_mode = "memorial"``
        is also exempt — the planner uses scene_mode for the long tail
        of post-death framings (memorial, vision, dream, quoted).
        """
        from types import SimpleNamespace

        char = _Character(
            name="陆沉",
            alive_status="deceased",
            death_chapter_number=458,
        )
        session = _make_session(
            project=_Project(),
            characters={"陆沉": char},
        )
        memorial_scene = SimpleNamespace(
            scene_type="setup",
            metadata_json={"scene_mode": "memorial"},
        )
        violations, _ = await _check_resurrection(
            session,
            project_id=uuid.uuid4(),
            chapter_number=600,
            scene_participants=["陆沉"],
            scene=memorial_scene,
        )
        assert violations == []

    @pytest.mark.asyncio
    async def test_fake_death_revealed_no_longer_blocks_appearance(self) -> None:
        """A character whose 'death' was a ruse — and the reveal chapter
        has already passed — must be allowed to act normally. We model
        the fake death via ``metadata_json.fake_death.revealed_chapter``.
        """
        char = _Character(
            name="林霄",
            alive_status="deceased",
            death_chapter_number=20,
            metadata_json={"fake_death": {"revealed_chapter": 35}},
        )
        session = _make_session(
            project=_Project(),
            characters={"林霄": char},
        )
        # Chapter 50 — well past the reveal — so the resurrection
        # check must NOT fire.
        violations, _ = await _check_resurrection(
            session,
            project_id=uuid.uuid4(),
            chapter_number=50,
            scene_participants=["林霄"],
        )
        assert violations == []

    @pytest.mark.asyncio
    async def test_fake_death_before_reveal_still_blocks(self) -> None:
        """A fake death that has NOT yet been revealed in-story still
        keeps the character off-stage — otherwise the writer could
        sneak a reveal in via the back door."""
        char = _Character(
            name="林霄",
            alive_status="deceased",
            death_chapter_number=20,
            metadata_json={"fake_death": {"revealed_chapter": 35}},
        )
        session = _make_session(
            project=_Project(),
            characters={"林霄": char},
        )
        violations, _ = await _check_resurrection(
            session,
            project_id=uuid.uuid4(),
            chapter_number=25,  # before reveal
            scene_participants=["林霄"],
        )
        assert len(violations) == 1

    @pytest.mark.asyncio
    async def test_evidence_string_does_not_reference_alive_status(self) -> None:
        """After the fix we no longer carry the alive_status binding into
        the evidence formatter; this guards against the NameError that
        crashed the scanner the first time we re-ran it."""

        char = _Character(
            name="陆沉",
            alive_status="deceased",
            death_chapter_number=458,
        )
        session = _make_session(
            project=_Project(),
            characters={"陆沉": char},
        )
        violations, _ = await _check_resurrection(
            session,
            project_id=uuid.uuid4(),
            chapter_number=460,
            scene_participants=["陆沉"],
        )
        assert violations
        assert "alive_status" not in (violations[0].evidence or "")
        assert "death_chapter=458" in (violations[0].evidence or "")


# ── _check_power_tier_regression ──────────────────────────────────────────


class TestPowerTierRegression:
    """Locks down the hash-fallback removal + alias map at
    contradiction.py:876-994."""

    @pytest.fixture
    def xianxia_invariants(self) -> dict[str, Any]:
        return {
            "power_system": {
                "tiers": [
                    "炼气",
                    "筑基",
                    "金丹",
                    "元婴",
                    "化神",
                ],
                "tier_aliases": {
                    "中阶": "筑基",
                    "金丹期": "金丹",
                    "金丹中期": "金丹",
                    "元婴期": "元婴",
                },
            }
        }

    @pytest.mark.asyncio
    async def test_no_tier_order_means_no_warnings(self) -> None:
        """When power_system is unset (the《道种破虚》 starting state),
        the previous code hash-ranked unknown tiers and emitted random
        regressions. The fix must produce zero warnings instead."""

        char_id = uuid.uuid4()
        char = _Character(
            id=char_id, name="陆沉", power_tier="低阶"
        )
        snaps = [_Snapshot(chapter_number=5, scene_number=1, power_tier="高阶")]
        session = _make_session(
            project=_Project(invariants_json=None),
            characters={"陆沉": char},
            snapshots_for={char_id: snaps},
        )
        _, warnings = await _check_power_tier_regression(
            session,
            project_id=uuid.uuid4(),
            chapter_number=10,
            scene_participants=["陆沉"],
        )
        assert warnings == []

    @pytest.mark.asyncio
    async def test_aliases_collapse_old_and_new_taxonomies(
        self, xianxia_invariants: dict[str, Any]
    ) -> None:
        """``中阶`` (old) and ``筑基`` (new) must rank identically once
        the alias map is loaded — so a chapter snapshot of ``中阶`` after
        a peak of ``筑基`` produces no warning."""

        char_id = uuid.uuid4()
        char = _Character(
            id=char_id, name="陆沉", power_tier="筑基"
        )
        snaps = [
            _Snapshot(chapter_number=5, scene_number=1, power_tier="筑基"),
            _Snapshot(
                chapter_number=10,
                scene_number=None,
                power_tier="中阶",  # post-write extracted, same canonical
            ),
        ]
        session = _make_session(
            project=_Project(invariants_json=xianxia_invariants),
            characters={"陆沉": char},
            snapshots_for={char_id: snaps},
        )
        _, warnings = await _check_power_tier_regression(
            session,
            project_id=uuid.uuid4(),
            chapter_number=10,
            scene_participants=["陆沉"],
        )
        assert warnings == []

    @pytest.mark.asyncio
    async def test_one_tier_drop_below_noise_floor(
        self, xianxia_invariants: dict[str, Any]
    ) -> None:
        """A single-tier drop is plausible cross-taxonomy noise (e.g.
        the planner snapshot says 金丹 but the LLM extraction says 筑基).
        We require a 2-tier gap before warning."""

        char_id = uuid.uuid4()
        char = _Character(
            id=char_id, name="主角", power_tier="筑基"
        )
        snaps = [
            _Snapshot(chapter_number=5, scene_number=1, power_tier="金丹"),
            _Snapshot(chapter_number=10, scene_number=None, power_tier="筑基"),
        ]
        session = _make_session(
            project=_Project(invariants_json=xianxia_invariants),
            characters={"主角": char},
            snapshots_for={char_id: snaps},
        )
        _, warnings = await _check_power_tier_regression(
            session,
            project_id=uuid.uuid4(),
            chapter_number=10,
            scene_participants=["主角"],
        )
        assert warnings == []

    @pytest.mark.asyncio
    async def test_two_tier_drop_fires_warning(
        self, xianxia_invariants: dict[str, Any]
    ) -> None:
        """A real injury / sealing event drops 2+ tiers and must surface."""

        char_id = uuid.uuid4()
        char = _Character(
            id=char_id, name="主角", power_tier="炼气"
        )
        snaps = [
            _Snapshot(chapter_number=5, scene_number=1, power_tier="金丹"),
            _Snapshot(chapter_number=10, scene_number=None, power_tier="炼气"),
        ]
        session = _make_session(
            project=_Project(invariants_json=xianxia_invariants),
            characters={"主角": char},
            snapshots_for={char_id: snaps},
        )
        _, warnings = await _check_power_tier_regression(
            session,
            project_id=uuid.uuid4(),
            chapter_number=10,
            scene_participants=["主角"],
        )
        assert len(warnings) == 1
        assert warnings[0].check_type == "character_power_tier_regression"

    @pytest.mark.asyncio
    async def test_pre_death_chapter_without_snapshot_is_skipped(
        self, xianxia_invariants: dict[str, Any]
    ) -> None:
        """When the character is currently dead (post-death state stored
        in characters.power_tier) and we scan a pre-death chapter that
        has no per-chapter snapshot, the live value is misleading and we
        must skip the comparison."""

        char_id = uuid.uuid4()
        char = _Character(
            id=char_id,
            name="陆沉",
            power_tier="炼气",  # post-death residual, low tier
            death_chapter_number=458,
        )
        snaps = [
            _Snapshot(chapter_number=9, scene_number=None, power_tier="金丹")
        ]
        session = _make_session(
            project=_Project(invariants_json=xianxia_invariants),
            characters={"陆沉": char},
            snapshots_for={char_id: snaps},
        )
        _, warnings = await _check_power_tier_regression(
            session,
            project_id=uuid.uuid4(),
            chapter_number=11,  # pre-death, no at-chapter snapshot
            scene_participants=["陆沉"],
        )
        assert warnings == []
