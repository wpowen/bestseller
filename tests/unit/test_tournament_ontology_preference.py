"""The tournament must not crown a champion the final gate will kill.

2026-08-05, ``custom-xuanhuan-1785943635``: the user asked for 东方玄幻 with
废柴逆袭/升级流/血脉觉醒. The tournament crowned an underworld-civil-servant KPI
comedy, conception ran to completion on it, and the fail-closed ontology
tripwire then killed the whole book with a one-line message. Generator and
acceptor disagreed, and a full conception burned in between.

The fix is a preference inside the tournament, not a relaxation of the gate.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from bestseller.services.concept_tournament import _prefer_ontology_clean
from bestseller.services.genre_intent_contract import GenreIntentContract

pytestmark = pytest.mark.unit


@dataclass
class _Candidate:
    concept: str
    composite: float = 0.5
    mechanism: str = ""
    hook_question: str = ""
    progress_bar: str = ""


def _xuanhuan_contract() -> GenreIntentContract:
    return GenreIntentContract.model_validate(
        {
            "genre_key": "xuanhuan",
            "genre_label": "东方玄幻",
            "sub_genre_label": "东方玄幻",
            "channel_key": "male",
            "allowed_modernity": "genre_native",
            "prompt_pack_key": "xuanhuan-power-fantasy",
            "category_key": "action-progression",
        }
    )


# Verbatim from the failed run.
_DRIFTED = _Candidate(
    concept=(
        "一个给人看风水的算命先生，因为太准被阴间挂号处当成走丢的临时工抓去干活，"
        "从此在阴阳两界的职场夹缝里拼命保住自己的阳间编制。"
    ),
    composite=0.95,
)
_CLEAN = _Candidate(
    concept="少年血脉觉醒，宗门大比夺魁，凭一柄断剑问鼎剑道。",
    composite=0.60,
)


def test_drifting_candidate_is_demoted_even_when_it_scores_highest() -> None:
    """Score alone crowned the drifter; that is what burned the conception."""

    kept = _prefer_ontology_clean([_DRIFTED, _CLEAN], _xuanhuan_contract())

    assert kept == [_CLEAN]


def test_pool_is_never_emptied_when_everything_drifts() -> None:
    """Starving the pool converts a nudge-able book into an uncreatable one."""

    kept = _prefer_ontology_clean([_DRIFTED], _xuanhuan_contract())
    assert kept == [_DRIFTED]

    two_drifters = [_DRIFTED, _Candidate(concept="他在阴间职场当临时工。")]
    assert _prefer_ontology_clean(two_drifters, _xuanhuan_contract()) == two_drifters


def test_no_contract_means_no_opinion() -> None:
    finalists = [_DRIFTED, _CLEAN]
    assert _prefer_ontology_clean(finalists, None) == finalists


def test_single_finalist_is_passed_through_untouched() -> None:
    assert _prefer_ontology_clean([_DRIFTED], _xuanhuan_contract()) == [_DRIFTED]


def test_all_clean_pool_is_unchanged() -> None:
    others = [_CLEAN, _Candidate(concept="宗门试炼，古剑认主。")]
    assert _prefer_ontology_clean(others, _xuanhuan_contract()) == others


def test_scan_covers_mechanism_not_only_the_one_liner() -> None:
    """Drift hid in the mechanism field in the real run's sibling candidates."""

    sneaky = _Candidate(
        concept="少年得一枚古印。",
        mechanism="古印本质是阴司的职场考勤系统，每月结算业绩。",
        composite=0.99,
    )
    kept = _prefer_ontology_clean([sneaky, _CLEAN], _xuanhuan_contract())
    assert kept == [_CLEAN]


def test_detector_failure_never_breaks_the_tournament() -> None:
    """A guard that can crash conception is worse than the drift it catches."""

    class _Exploding:
        @property
        def allowed_modernity(self) -> str:
            raise RuntimeError("boom")

    finalists = [_DRIFTED, _CLEAN]
    assert _prefer_ontology_clean(finalists, _Exploding()) == finalists


def test_conception_forwards_the_contract_to_the_tournament() -> None:
    """A parameter that is never passed is a fix that does not exist."""

    import inspect

    from bestseller.services import conception

    source = inspect.getsource(conception.run_conception_pipeline)
    index = source.index("run_concept_tournament(")
    region = source[index : index + 2500]
    assert "genre_intent_contract=genre_intent_contract" in region
