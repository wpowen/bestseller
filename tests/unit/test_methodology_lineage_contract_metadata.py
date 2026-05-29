from __future__ import annotations

from types import SimpleNamespace

import pytest

from bestseller.services import narrative
from bestseller.services.methodology_lineage import METHODOLOGY_LINEAGE_METADATA_KEY


@pytest.mark.unit
def test_chapter_contract_metadata_keeps_lineage_out_when_flag_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("BESTSELLER_METHODOLOGY_V2", raising=False)
    chapter = SimpleNamespace(
        metadata_json={
            "methodology_contract": {"hooks_to_plant": ["new clue"]},
            METHODOLOGY_LINEAGE_METADATA_KEY: {"chapter_no": 1},
        }
    )

    metadata = narrative._chapter_contract_metadata_from_chapter(chapter)

    assert metadata == {"methodology_contract": {"hooks_to_plant": ["new clue"]}}


@pytest.mark.unit
def test_chapter_contract_metadata_copies_lineage_when_flag_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BESTSELLER_METHODOLOGY_V2", "1")
    lineage = {"chapter_no": 1, "selected": []}
    chapter = SimpleNamespace(
        metadata_json={
            "methodology_contract": {"hooks_to_plant": ["new clue"]},
            METHODOLOGY_LINEAGE_METADATA_KEY: lineage,
        }
    )

    metadata = narrative._chapter_contract_metadata_from_chapter(chapter)

    assert metadata["methodology_contract"] == {"hooks_to_plant": ["new clue"]}
    assert metadata[METHODOLOGY_LINEAGE_METADATA_KEY] == lineage


@pytest.mark.unit
def test_chapter_contract_metadata_copies_causal_contract_only_when_flag_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chapter = SimpleNamespace(
        metadata_json={
            "causal_contract": {"protagonist_choice": "沈砚选择进入暗门。"},
            "event_cycle_contract": {"event_role": "turn"},
        }
    )

    monkeypatch.delenv("BESTSELLER_METHODOLOGY_V2", raising=False)
    assert narrative._chapter_contract_metadata_from_chapter(chapter) == {}

    monkeypatch.setenv("BESTSELLER_METHODOLOGY_V2", "1")
    metadata = narrative._chapter_contract_metadata_from_chapter(chapter)

    assert metadata["causal_contract"] == {"protagonist_choice": "沈砚选择进入暗门。"}
    assert metadata["event_cycle_contract"] == {"event_role": "turn"}
