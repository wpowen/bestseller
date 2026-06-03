"""WS-C3 regression: per-scene auto-repair hard cap, no outer reset.

Historical observation (青囊不语问阴阳 ch1 — 43 draft versions on 3 scenes
without convergence): per-scene rewrites were not bounded, and the outer
``project_repair`` loop could re-enter the chapter pipeline and implicitly
reset the repair counter. WS-C3 fixes this by:

1. Tracking ``scene.metadata_json["scene_auto_repair_total_attempts"]`` —
   a cumulative, never-reset counter scoped to the scene.
2. Comparing it against ``settings.pipeline.chapter_auto_repair_max_scene_rewrites``
   before resetting the scene to ``NEEDS_REWRITE``.  When the cap is
   reached, the scene is stamped ``auto_accepted_with_debt=True`` and left
   in place (its previous draft is kept as ``is_current``).
3. Routing the chapter to ``ok`` so the assembler can use the prior draft
   — the cap MUST NOT cause ``machine_repair_required``.

These tests pin the helper-level contract; integration coverage lives in the
pipeline tests.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from bestseller.services import drafts
from bestseller.services.drafts import (
    bump_scene_auto_repair_counter,
    claim_scene_auto_repair_attempt,
    is_scene_at_auto_repair_cap,
    mark_scene_auto_accepted_with_debt,
    read_scene_auto_repair_counter,
    reset_scene_auto_repair_counter,
)

pytestmark = pytest.mark.unit


def _scene() -> SimpleNamespace:
    return SimpleNamespace(metadata_json={})


def test_counter_starts_at_zero() -> None:
    assert read_scene_auto_repair_counter(_scene()) == 0


def test_bump_increments_counter_persists_in_metadata() -> None:
    scene = _scene()
    assert bump_scene_auto_repair_counter(scene) == 1
    assert scene.metadata_json["scene_auto_repair_total_attempts"] == 1
    assert bump_scene_auto_repair_counter(scene) == 2
    assert read_scene_auto_repair_counter(scene) == 2


def test_reset_scene_auto_repair_residue_preserves_counter() -> None:
    """The residue cleanup must NOT clear the per-scene cap counter.

    Outer ``project_repair`` invokes this helper to wipe stale hint residue
    before the next attempt.  The cap counter is the protective contract —
    it must survive every attempt boundary, including residue cleanup.
    """
    scene = _scene()
    bump_scene_auto_repair_counter(scene)
    bump_scene_auto_repair_counter(scene)
    # The helper under test wipes the auto_repair_residue keys but not the cap
    # counter.  We add a known residue key to confirm residue gets wiped.
    scene.metadata_json["auto_repair_hint"] = "old hint"
    scene.metadata_json["auto_repair_block_codes"] = ["X"]
    drafts._reset_scene_auto_repair_residue_for_attempt(scene)
    assert read_scene_auto_repair_counter(scene) == 2
    assert "auto_repair_hint" not in scene.metadata_json
    assert "auto_repair_block_codes" not in scene.metadata_json


def test_is_scene_at_auto_repair_cap_uses_settings_default() -> None:
    """The cap helper must honor the configured per-scene cap (default 3)."""
    scene = _scene()
    assert is_scene_at_auto_repair_cap(scene) is False
    bump_scene_auto_repair_counter(scene)
    bump_scene_auto_repair_counter(scene)
    assert is_scene_at_auto_repair_cap(scene) is False
    bump_scene_auto_repair_counter(scene)  # 3rd bump -> at cap
    assert is_scene_at_auto_repair_cap(scene) is True
    bump_scene_auto_repair_counter(scene)  # 4th bump -> past cap
    assert is_scene_at_auto_repair_cap(scene) is True


def test_is_scene_at_auto_repair_cap_respects_explicit_cap() -> None:
    """A caller-supplied cap overrides the global default (test isolation)."""
    scene = _scene()
    bump_scene_auto_repair_counter(scene)
    assert is_scene_at_auto_repair_cap(scene, cap=1) is True
    assert is_scene_at_auto_repair_cap(scene, cap=5) is False


def test_mark_scene_auto_accepted_with_debt_stamps_metadata() -> None:
    scene = _scene()
    bump_scene_auto_repair_counter(scene)
    bump_scene_auto_repair_counter(scene)
    bump_scene_auto_repair_counter(scene)
    mark_scene_auto_accepted_with_debt(
        scene, cap=3, reason="repeated length under target after 3 attempts"
    )
    assert scene.metadata_json["auto_accepted_with_debt"] is True
    assert scene.metadata_json["auto_accepted_with_debt_cap"] == 3
    assert scene.metadata_json["auto_accepted_with_debt_at_attempt"] == 3
    assert "length under target" in scene.metadata_json[
        "auto_accepted_with_debt_reason"
    ]


def test_reset_scene_auto_repair_counter_only_clears_caller_flag() -> None:
    """A separate opt-in helper wipes the counter for explicit re-runs.

    The cap is a *cumulative* safety.  Tests, deterministic replays, and the
    platform-level ``accept_with_debt`` operator action may legitimately
    reset the counter; the production repair flow must never do so.
    """
    scene = _scene()
    bump_scene_auto_repair_counter(scene)
    bump_scene_auto_repair_counter(scene)
    reset_scene_auto_repair_counter(scene)
    assert read_scene_auto_repair_counter(scene) == 0


def test_default_cap_setting_is_three() -> None:
    """Pin the configured default so accidental config drift shows up in CI."""
    from bestseller.settings import get_settings

    settings = get_settings()
    assert settings.pipeline.chapter_auto_repair_max_scene_rewrites == 3


# ---------------------------------------------------------------------------
# F10: pass_id semantics — counter records *chapter-level* auto-repair
# attempts, not raw reset invocations.  A single chapter pass can hit one
# scene from multiple reset paths (write-safety / metadata-code /
# length-stability); without dedup, the counter would over-count and
# hit the cap before the chapter sees the configured number of real
# rewrite cycles.  ``pass_id`` is the chapter-level attempt number from
# ``maybe_prepare_chapter_auto_repair(attempt_number=...)``.
# ---------------------------------------------------------------------------


def test_claim_with_pass_id_bumps_on_first_seen_pass() -> None:
    scene = _scene()
    assert claim_scene_auto_repair_attempt(scene, pass_id=1) == 1
    assert scene.metadata_json["scene_auto_repair_total_attempts"] == 1
    assert scene.metadata_json["scene_auto_repair_last_pass_id"] == 1


def test_claim_is_idempotent_within_a_pass() -> None:
    """Repeated calls for the same pass_id must NOT over-count."""
    scene = _scene()
    # Three calls in the same chapter pass — only the first bumps.
    assert claim_scene_auto_repair_attempt(scene, pass_id=2) == 1
    assert claim_scene_auto_repair_attempt(scene, pass_id=2) == 1
    assert claim_scene_auto_repair_attempt(scene, pass_id=2) == 1
    assert read_scene_auto_repair_counter(scene) == 1


def test_claim_bumps_on_new_pass_id() -> None:
    scene = _scene()
    assert claim_scene_auto_repair_attempt(scene, pass_id=1) == 1
    assert claim_scene_auto_repair_attempt(scene, pass_id=2) == 2
    assert claim_scene_auto_repair_attempt(scene, pass_id=3) == 3
    assert read_scene_auto_repair_counter(scene) == 3
    assert scene.metadata_json["scene_auto_repair_last_pass_id"] == 3


def test_claim_with_pass_id_zero_does_not_bump() -> None:
    """Defensive: a 0 pass_id must not silently count or silently dedup."""
    scene = _scene()
    assert claim_scene_auto_repair_attempt(scene, pass_id=0) == 0
    assert read_scene_auto_repair_counter(scene) == 0
    # And a real pass_id still works afterwards.
    assert claim_scene_auto_repair_attempt(scene, pass_id=1) == 1
