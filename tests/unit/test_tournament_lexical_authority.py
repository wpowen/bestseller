"""A vocabulary observation cannot overrule candidate eligibility or rank."""

import json
from pathlib import Path

import pytest

from bestseller.services import concept_tournament as ct
from bestseller.services.anti_default_motif import default_debt_family_hits

CASES = json.loads(
    (Path(__file__).parents[1] / "fixtures/framework_motif/independent-cases.json").read_text(
        encoding="utf-8"
    )
)["cases"]


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["case_id"])
def test_independent_story_material_keeps_eligibility(case):
    approved = [("candidate", case["case_id"]), ("comparison", "无相关词")]
    result = ct.ConceptTournamentResult()
    kept = ct._demote_default_family_cards(
        approved,
        prebuilt_kernels={approved[0]: case["kernel"], approved[1]: {"core_abnormality": "听风"}},
        seed_concept="",
        result=result,
    )
    assert kept == approved
    assert result.engine_rejections == []
    assert (
        bool(default_debt_family_hits(case["story_text"]))
        == case["diagnostic_expectation"]["lexical_signal_expected"]
    )


def test_actual_sweeper_card_keeps_its_approved_slot():
    card = json.loads(
        (Path(__file__).parents[1] / "fixtures/framework_motif/b1-sweeper-card.json").read_text(
            encoding="utf-8"
        )
    )
    approved = [("sweeper", "扫地改变剑感"), ("cook", "厨娘做汤")]
    result = ct.ConceptTournamentResult()
    kept = ct._demote_default_family_cards(
        approved,
        prebuilt_kernels={approved[0]: card, approved[1]: {"one_liner": "厨娘做汤"}},
        seed_concept="",
        result=result,
    )
    assert kept == approved
    assert result.engine_rejections == []
    assert any(f["policy"] == "advisory_only" for f in result.quality_findings)


def test_theme_signal_cannot_take_a_raw_expansion_slot():
    def row(index, score, **extra):
        return dict(
            index=index,
            domain=str(index),
            freshness=score,
            click_seed=score,
            character_logic=score,
            action_seed=score,
            promise_survival=score,
            genre_fidelity=score,
            ai_assembly=0,
            dumb_cost=False,
            after_opening_promise="主角与对手继续改变合作条件",
            action_families=["合作", "竞赛", "谈判"],
            growth_surface="队伍经验积累",
            **extra,
        )

    ranking = [row(0, 9, default_family=True), row(1, 8)]
    selected = ct._select_raw_ideas_for_expansion(
        ranking,
        raw_floor=7,
        progression_floor=5,
        limit=1,
    )
    assert [r["index"] for r in selected] == [0]


def test_real_market_overlap_still_affects_expansion_order():
    base = {
        "freshness": 8,
        "click_seed": 8,
        "character_logic": 8,
        "action_seed": 8,
        "promise_survival": 8,
        "genre_fidelity": 8,
        "ai_assembly": 0,
        "after_opening_promise": "继续改变合作条件",
        "growth_surface": "经验积累",
        "action_families": ["合作", "竞赛", "谈判"],
    }
    ranking = [
        dict(base, index=0, domain="a", market_collision=[{"title": "旧书"}]),
        dict(base, index=1, domain="b"),
    ]
    selected = ct._select_raw_ideas_for_expansion(
        ranking,
        raw_floor=7,
        progression_floor=5,
        limit=1,
    )
    assert [r["index"] for r in selected] == [1]
