"""Whatever the ontology verdict judges, the repair must be able to reach.

2026-08-06, ``custom-xuanhuan-1785968727``: conception passed every gate, then
died at the final ontology check over a single word (手机). A repair stage
exists for exactly this, it ran, and it changed nothing — because the verdict
surface reads 24 ``high_concept`` fields that were never passed to the repair.
A drift landing there was structurally unfixable, so whether a book survived
depended on which field the model happened to put the word in.
"""

from __future__ import annotations

import inspect

import pytest

from bestseller.services import conception

pytestmark = pytest.mark.unit


def test_repair_receives_every_region_the_verdict_reads() -> None:
    """The structural invariant. If this fails, some drift is unfixable."""

    surface = inspect.signature(conception._conception_ontology_story_surface)
    repair = inspect.signature(conception._repair_final_ontology_drift)

    judged = {
        name
        for name in surface.parameters
        if name not in ("self",)
    }
    repairable = set(repair.parameters)

    unreachable = judged - repairable
    assert not unreachable, (
        "these regions are judged but cannot be repaired, so a violation there "
        f"can only kill the book: {sorted(unreachable)}"
    )


def test_high_concept_is_explicitly_repairable() -> None:
    """Named separately because it is the one that actually killed a book."""

    assert "high_concept" in inspect.signature(
        conception._repair_final_ontology_drift
    ).parameters


def test_repair_input_carries_the_champion_fields() -> None:
    source = inspect.getsource(conception._repair_final_ontology_drift)
    assert "high_concept_story" in source, "champion fields must reach the model"
    assert "_ONTOLOGY_HIGH_CONCEPT_STORY_FIELDS" in source


def test_repaired_champion_is_written_back_before_rescan() -> None:
    """A fix that is computed and then discarded is a no-op.

    The re-scan reads ``ctx['high_concept']``; if the repaired champion is not
    written back, the gate re-reads the unrepaired text and still fails.
    """

    source = inspect.getsource(conception.run_conception_pipeline)
    index = source.index("_repair_final_ontology_drift(")
    region = source[index : index + 3000]
    assert 'ctx["high_concept"] = ' in region


def test_repair_returns_the_champion_to_the_caller() -> None:
    source = inspect.getsource(conception._repair_final_ontology_drift)
    tail = source[source.rindex("return ("):]
    assert "new_champion" in tail
