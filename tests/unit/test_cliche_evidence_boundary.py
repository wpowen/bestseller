import json
from pathlib import Path

import pytest

from bestseller.services.concept_tournament import ConceptCandidate, _cliche_hits

pytestmark = pytest.mark.unit


def test_real_sixth_candidates_do_not_turn_death_words_into_debt():
    data = json.loads(
        (Path(__file__).parents[1] / "fixtures/sixth-cliche-candidates.json").read_text()
    )
    assert len(data["candidates"]) == 4
    for row in data["candidates"]:
        assert "还债" not in row["mechanism"] + row["hook_question"] + row["concept"]
        assert _cliche_hits(ConceptCandidate(**row), ("替死人还债",)) == []


@pytest.mark.parametrize(
    "text,banned,expected",
    [
        ("替死人完成临死时被打断的最后一个动作", "替死人还债", []),
        ("收尸人替死人还债", "替死人还债", ["替死人还债"]),
        ("老祖飞升前给主角留下一份传承", "老祖飞升前留下传承", ["老祖飞升前留下传承"]),
        ("他被退婚后当众打脸前未婚妻全家", "退婚打脸", ["退婚打脸"]),
        ("退婚" + "路" * 30 + "退婚打脸", "退婚打脸", ["退婚打脸"]),
    ],
)
def test_bounded_phrase_evidence(text, banned, expected):
    assert _cliche_hits(ConceptCandidate(dimension="test", concept=text), (banned,)) == expected


def test_separate_fields_cannot_assemble_a_banned_claim():
    assert (
        _cliche_hits(
            ConceptCandidate(dimension="test", concept="退婚", mechanism="打脸"), ("退婚打脸",)
        )
        == []
    )
