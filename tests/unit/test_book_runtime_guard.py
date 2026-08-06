"""Config drift must be named, not discovered as an unfixable quality failure.

Editing a threshold mid-run used to make chapter 51 onwards die deterministically:
early chapters written and validated under one contract, later ones under
another, with nothing announcing the change.
"""

from __future__ import annotations

import pytest

from bestseller.services.book_runtime_guard import (
    GUARD_METADATA_KEY,
    BookRuntimeGuard,
    GuardMode,
    build_guard,
    guard_mode,
    load_guard,
    store_guard,
    verify_guard,
)


def _contract(*, min_words: int = 1800, pack: str = "xianxia@3") -> dict[str, object]:
    return {
        "length_contract": {"min": min_words, "aim": 2600, "max": 3500},
        "prompt_pack": pack,
        "gate_thresholds": {"ai_flavor_block": 38, "judge_min_overall": 0.87},
    }


@pytest.mark.unit
def test_identical_contract_reports_no_drift() -> None:
    metadata = store_guard({}, build_guard(_contract()))
    report = verify_guard(metadata, _contract(), mode=GuardMode.ENFORCE)

    assert not report.has_drift
    assert not report.blocks_production


@pytest.mark.unit
def test_changed_threshold_is_detected_and_named() -> None:
    """Naming the moved field is the point — "something changed" is not actionable."""

    metadata = store_guard({}, build_guard(_contract()))
    report = verify_guard(metadata, _contract(min_words=2200), mode=GuardMode.AUDIT_ONLY)

    assert report.has_drift
    assert report.changed == ("length_contract",)
    assert "length_contract" in report.describe()


@pytest.mark.unit
def test_prompt_pack_swap_is_detected() -> None:
    metadata = store_guard({}, build_guard(_contract()))
    report = verify_guard(metadata, _contract(pack="urban@1"), mode=GuardMode.AUDIT_ONLY)
    assert report.changed == ("prompt_pack",)


@pytest.mark.unit
def test_added_and_removed_contract_parts_are_reported_separately() -> None:
    metadata = store_guard({}, build_guard(_contract()))
    live = _contract()
    live.pop("prompt_pack")
    live["new_gate"] = {"enabled": True}

    report = verify_guard(metadata, live, mode=GuardMode.AUDIT_ONLY)

    assert report.removed == ("prompt_pack",)
    assert report.added == ("new_gate",)


@pytest.mark.unit
def test_audit_mode_records_drift_without_blocking() -> None:
    """Every hard-blocking gate added first has produced false-positive kills."""

    metadata = store_guard({}, build_guard(_contract()))
    report = verify_guard(metadata, _contract(min_words=9999), mode=GuardMode.AUDIT_ONLY)

    assert report.has_drift
    assert report.blocks_production is False


@pytest.mark.unit
def test_enforce_mode_blocks_on_drift() -> None:
    metadata = store_guard({}, build_guard(_contract()))
    report = verify_guard(metadata, _contract(min_words=9999), mode=GuardMode.ENFORCE)

    assert report.blocks_production is True


@pytest.mark.unit
def test_off_mode_never_reports_drift() -> None:
    metadata = store_guard({}, build_guard(_contract()))
    report = verify_guard(metadata, _contract(min_words=9999), mode=GuardMode.OFF)

    assert report.has_drift is False


@pytest.mark.unit
def test_books_without_a_guard_are_unverifiable_not_broken() -> None:
    """Refusing to write pre-existing books would be worse than the drift."""

    report = verify_guard({"planning_status": "ok"}, _contract(), mode=GuardMode.ENFORCE)

    assert report.guard_present is False
    assert report.has_drift is False
    assert report.blocks_production is False


@pytest.mark.unit
def test_storing_a_guard_preserves_existing_metadata_and_does_not_mutate() -> None:
    metadata = {"planning_status": "ok"}
    stored = store_guard(metadata, build_guard(_contract()))

    assert GUARD_METADATA_KEY not in metadata
    assert stored["planning_status"] == "ok"
    assert load_guard(stored) is not None


@pytest.mark.unit
def test_hash_is_order_insensitive_so_reserialisation_is_not_drift() -> None:
    """A dict that round-trips through JSON must not look like a config change."""

    first = build_guard({"gate": {"a": 1, "b": 2}})
    second = build_guard({"gate": {"b": 2, "a": 1}})

    assert first.fingerprints == second.fingerprints


@pytest.mark.unit
@pytest.mark.parametrize("raw", [None, {}, {GUARD_METADATA_KEY: "nonsense"}])
def test_malformed_guard_degrades_to_absent(raw: object) -> None:
    assert load_guard(raw) is None  # type: ignore[arg-type]


@pytest.mark.unit
def test_guard_round_trips_through_serialisation() -> None:
    guard = build_guard(_contract(), frozen_by="tester")
    restored = BookRuntimeGuard.from_dict(guard.to_dict())

    assert restored is not None
    assert restored.fingerprints == guard.fingerprints
    assert restored.frozen_by == "tester"


@pytest.mark.unit
def test_mode_defaults_to_audit_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BESTSELLER_BOOK_RUNTIME_GUARD_MODE", raising=False)
    assert guard_mode() is GuardMode.AUDIT_ONLY

    monkeypatch.setenv("BESTSELLER_BOOK_RUNTIME_GUARD_MODE", "enforce")
    assert guard_mode() is GuardMode.ENFORCE

    monkeypatch.setenv("BESTSELLER_BOOK_RUNTIME_GUARD_MODE", "typo")
    assert guard_mode() is GuardMode.AUDIT_ONLY
