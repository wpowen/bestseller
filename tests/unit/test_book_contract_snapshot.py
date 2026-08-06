"""The contract snapshot must read real settings, not silently yield None.

A field read from the wrong settings section returns ``None``, which hashes
perfectly consistently — so the guard looks healthy while watching nothing.
Two fields were wired to the wrong section on the first attempt, which is
exactly why this file asserts on live settings rather than on a stub.
"""

from __future__ import annotations

import pytest

from bestseller.services.book_contract_snapshot import collect_book_contract
from bestseller.services.book_runtime_guard import build_guard, store_guard, verify_guard
from bestseller.services.book_runtime_guard import GuardMode
from bestseller.settings import load_settings


@pytest.mark.unit
def test_every_contract_field_resolves_against_real_settings() -> None:
    """A None here means the field is being read from the wrong section."""

    contract = collect_book_contract(load_settings())

    unresolved = [
        f"{section}.{key}"
        for section, values in contract.items()
        if isinstance(values, dict)
        for key, value in values.items()
        if value is None
    ]
    assert unresolved == [], (
        "these contract fields resolved to None and would make the guard "
        f"watch nothing: {unresolved}"
    )


@pytest.mark.unit
def test_contract_covers_the_settings_that_decide_correctness() -> None:
    contract = collect_book_contract(load_settings())

    assert {"length_contract", "prose_prompt_profile", "repair_budgets"} <= set(contract)


@pytest.mark.unit
def test_a_changed_setting_is_detected_as_drift() -> None:
    """End-to-end: freeze a real contract, change one knob, see it named."""

    settings = load_settings()
    frozen = store_guard({}, build_guard(collect_book_contract(settings)))

    drifted = collect_book_contract(settings)
    drifted["length_contract"] = {**drifted["length_contract"], "language": "en"}

    report = verify_guard(frozen, drifted, mode=GuardMode.AUDIT_ONLY)

    assert report.changed == ("length_contract",)


@pytest.mark.unit
def test_snapshot_never_raises_on_an_unfamiliar_settings_shape() -> None:
    """Older books and test doubles must yield a partial contract, not a crash."""

    class Empty:
        pass

    assert collect_book_contract(Empty()) is not None


@pytest.mark.unit
def test_per_book_overrides_join_the_contract() -> None:
    """Changing a book-level override mid-run splits the book like a global does."""

    class FakeProject:
        metadata_json = {"prompt_pack_key": "xianxia@3", "unrelated": "ignored"}

    contract = collect_book_contract(load_settings(), FakeProject())

    assert contract["book_overrides"] == {"prompt_pack_key": "xianxia@3"}
