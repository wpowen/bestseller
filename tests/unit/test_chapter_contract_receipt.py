"""L1 tests for the chapter contract receipt (契约声明 vs 正文实际对账).

Warn-only detector: these tests pin the reconciliation semantics AND the
no-op degradations (empty declarations / empty text must yield a clean
receipt, never manufactured findings).
"""

from __future__ import annotations

# ruff: noqa: RUF001 — Chinese punctuation is intentional.
from dataclasses import dataclass, field
from typing import Any

from bestseller.services.chapter_contract_receipt import (
    ChapterContractReceipt,
    build_chapter_contract_receipt,
)


@dataclass
class _Scene:
    participants: list[str] = field(default_factory=list)
    metadata_json: dict[str, Any] = field(default_factory=dict)


def test_all_declared_participants_present_and_active() -> None:
    text = "林晚握紧了刀。「你到底是谁？」赵三退了半步，声音发颤。"
    receipt = build_chapter_contract_receipt(
        chapter_text=text,
        chapter_number=7,
        scenes=[_Scene(participants=["林晚", "赵三"])],
    )
    assert receipt.missing_participants == ()
    assert receipt.silent_participants == ()
    assert receipt.participant_coverage == 1.0
    assert receipt.clean


def test_missing_participant_detected() -> None:
    text = "林晚握紧了刀，独自走进巷子。"
    receipt = build_chapter_contract_receipt(
        chapter_text=text,
        chapter_number=7,
        scenes=[_Scene(participants=["林晚", "赵三"])],
    )
    assert receipt.missing_participants == ("赵三",)
    assert receipt.participant_coverage == 0.5
    assert not receipt.clean


def test_silent_participant_named_but_never_acts() -> None:
    # 周伯 appears only inside a pure-描述 sentence: no dialogue marker, no
    # activity verb from the evidence list.
    text = "巷口的老槐树下是周伯。林晚握紧刀走了过去。"
    receipt = build_chapter_contract_receipt(
        chapter_text=text,
        chapter_number=3,
        scenes=[_Scene(participants=["林晚", "周伯"])],
    )
    assert receipt.missing_participants == ()
    assert receipt.silent_participants == ("周伯",)


def test_given_name_fallback_matches_and_is_recorded() -> None:
    # Declared full name 王小明, prose drops the surname.
    text = "小明抓起包就跑，鞋都没穿好。"
    receipt = build_chapter_contract_receipt(
        chapter_text=text,
        chapter_number=1,
        scenes=[_Scene(participants=["王小明"])],
    )
    assert receipt.missing_participants == ()
    assert receipt.matched_via == {"王小明": "given_name"}


def test_alias_annotation_in_declaration_matches_either_surface() -> None:
    # Live-run finding (2026-08-17, custom-xuanhuan ch50): the contract
    # declares 沈絮(阿缨) while the prose uses only one surface form.
    text = "阿缨把灯芯挑亮，说：「他们到城下了。」"
    receipt = build_chapter_contract_receipt(
        chapter_text=text,
        chapter_number=50,
        scenes=[_Scene(participants=["沈絮(阿缨)"])],
    )
    assert receipt.missing_participants == ()
    assert receipt.matched_via == {"沈絮(阿缨)": "alias"}
    assert receipt.silent_participants == ()


def test_two_char_name_gets_no_fallback() -> None:
    # 2-char names have no droppable surname; absent means missing.
    text = "有人在门外等着。"
    receipt = build_chapter_contract_receipt(
        chapter_text=text,
        chapter_number=1,
        scenes=[_Scene(participants=["林晚"])],
    )
    assert receipt.missing_participants == ("林晚",)


def test_declared_location_reconciled() -> None:
    scenes = [
        _Scene(
            participants=["林晚"],
            metadata_json={"scene_contract": {"location": "乱葬岗"}},
        ),
        _Scene(
            participants=["林晚"],
            metadata_json={"methodology_contract": {"setting_detail": "城南当铺"}},
        ),
    ]
    text = "林晚在乱葬岗蹲了半夜，握着那半块玉。"
    receipt = build_chapter_contract_receipt(
        chapter_text=text, chapter_number=2, scenes=scenes
    )
    assert receipt.declared_locations == ("乱葬岗", "城南当铺")
    assert receipt.missing_locations == ("城南当铺",)


def test_noop_empty_scenes_yield_clean_receipt() -> None:
    receipt = build_chapter_contract_receipt(
        chapter_text="随便什么正文。", chapter_number=1, scenes=[]
    )
    assert receipt == ChapterContractReceipt(chapter_number=1)
    assert receipt.clean
    assert receipt.participant_coverage == 1.0


def test_noop_empty_text_never_manufactures_findings() -> None:
    receipt = build_chapter_contract_receipt(
        chapter_text="",
        chapter_number=1,
        scenes=[_Scene(participants=["林晚"])],
    )
    assert receipt.missing_participants == ()
    assert receipt.silent_participants == ()
    assert receipt.clean


def test_to_dict_round_trip_shape() -> None:
    payload = build_chapter_contract_receipt(
        chapter_text="林晚说：「走。」",
        chapter_number=9,
        scenes=[_Scene(participants=["林晚"])],
    ).to_dict()
    assert payload["chapter_number"] == 9
    assert payload["clean"] is True
    assert isinstance(payload["declared_participants"], list)
    assert isinstance(payload["matched_via"], dict)
