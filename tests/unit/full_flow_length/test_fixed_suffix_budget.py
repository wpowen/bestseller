"""RED numerical contracts for fixed-suffix replacement; main executes tests.

Counts below are pure mathematical fixtures, not SQL adoption/grade/current or
promoted evidence. They cannot authorize freeze/adoption. Main must separately
prove native reader, body SHA/CAS and readback behavior using its normal flow.
"""

from __future__ import annotations

import ast
from copy import deepcopy
import inspect

import pytest

from bestseller.services import full_flow_length as length

pytestmark = pytest.mark.unit


def _replacement(
    *,
    total=8000,
    plan=(2000, 2000, 2000, 2000),
    chapter=2,
    adopted=(2300, 2000, 2400, 2400),
    **kwargs,
):
    # Deliberately private arithmetic API: native freeze must derive these
    # inputs itself from actual source rows and never trust caller counts.
    return length._fixed_suffix_budget(
        total_words=total,
        planned_targets=plan,
        chapter_number=chapter,
        adopted_word_counts=adopted,
        **kwargs,
    )


@pytest.mark.parametrize(
    "chapter,adopted,fixed_numbers",
    [
        (1, (2000, 2300, 2400, 2400), [2, 3, 4]),
        (2, (2300, 2000, 2400, 2400), [1, 3, 4]),
    ],
)
def test_middle_replacement_counts_prefix_and_entire_fixed_suffix(chapter, adopted, fixed_numbers):
    budget = _replacement(chapter=chapter, adopted=adopted)
    assert budget["mode"] == "fixed_suffix_replacement"
    assert budget["fixed_words"] == 7100  # 2300 + 2400 + 2400; excludes current 2000
    assert budget["fixed_chapter_numbers"] == fixed_numbers
    assert budget["unadopted_chapter_numbers"] == []
    assert budget["feasible"] is True
    assert budget["next_band"] == {
        "min_words": 1800,
        "target_words": 1800,
        "max_words": 2100,
    }
    assert budget["targets"] == [1800]  # Only the requested replacement, no suffix targets.
    assert budget["exact_target_feasible"] is False


def test_middle_replacement_endpoints_preserve_whole_book_tolerance():
    budget = _replacement()
    window = budget["next_band"]
    assert budget["book_min_words"] == 6800
    assert budget["book_max_words"] == 9200
    for candidate_words in (window["min_words"], window["max_words"]):
        total = budget["fixed_words"] + candidate_words
        assert budget["book_min_words"] <= total <= budget["book_max_words"]
    assert budget["fixed_words"] + window["max_words"] + 1 > budget["book_max_words"]


@pytest.mark.parametrize("incumbent_words", [1800, 2000, 3000, 3501])
def test_incumbent_is_replaced_not_double_counted_or_grandfathered(incumbent_words):
    budget = _replacement(adopted=(2300, incumbent_words, 2400, 2400))
    assert budget["fixed_words"] == 7100
    assert budget["next_band"]["max_words"] == 2100
    # An over-wall incumbent may be repaired, but the replacement is bounded.
    assert budget["feasible"] is True


def test_fixed_suffix_never_expands_original_chapter_band_to_3500():
    budget = _replacement(adopted=(1800, 2000, 1800, 1800))
    assert budget["fixed_words"] == 5400
    assert budget["next_band"] == {
        "min_words": 1800,
        "target_words": 2300,
        "max_words": 2300,
    }
    assert budget["exact_target_feasible"] is False
    assert budget["fixed_words"] + 2300 == 7700


def test_book_lower_bound_can_tighten_original_chapter_floor():
    budget = _replacement(
        total=15000, plan=(2500,) * 6, chapter=3, adopted=(2000, 2000, 2500, 2000, 2000, 2000)
    )
    assert budget["fixed_words"] == 10000
    assert budget["next_band"] == {
        "min_words": 2750,
        "target_words": 2875,
        "max_words": 2875,
    }
    assert budget["fixed_words"] + budget["next_band"]["min_words"] == 12750


def test_fixed_suffix_infeasibility_has_no_executable_target():
    budget = _replacement(adopted=(2500, 2000, 2500, 2500))
    assert budget["fixed_words"] == 7500
    assert budget["feasible"] is False
    assert budget["issue_code"] == "BOOK_LENGTH_BUDGET_INFEASIBLE"
    assert budget["next_band"] is None and budget["targets"] == []


def test_last_legal_replacement_is_an_inclusive_single_point():
    budget = _replacement(adopted=(2400, 2000, 2500, 2500))
    assert budget["fixed_words"] == 7400
    assert budget["next_band"] == {
        "min_words": 1800,
        "target_words": 1800,
        "max_words": 1800,
    }
    assert budget["fixed_words"] + 1800 == budget["book_max_words"]


def test_partial_adopted_suffix_reserves_unadopted_tail_capacity():
    budget = _replacement(adopted=(3300, 2000, 2300, None))
    assert budget["fixed_words"] == 5600
    assert budget["fixed_chapter_numbers"] == [1, 3]
    assert budget["unadopted_chapter_numbers"] == [4]
    assert budget["next_band"] == {
        "min_words": 1800,
        "target_words": 1800,
        "max_words": 1800,
    }
    assert budget["targets"] == [1800]
    # Chapter 4 still needs its immutable minimum of 1800; it is not discarded.
    assert budget["fixed_words"] + 1800 + 1800 == budget["book_max_words"]


def test_nonuniform_original_targets_are_preserved_without_repartition():
    plan = (1800, 2000, 2100, 2100)
    before = deepcopy(plan)
    budget = _replacement(plan=plan, adopted=(1800, 2000, 2100, 2100))
    assert plan == before
    assert budget["fixed_words"] == 6000
    assert budget["next_band"] == {
        "min_words": 1800,
        "target_words": 2000,
        "max_words": 2300,
    }
    assert budget["targets"] == [2000]
    assert budget["exact_target_feasible"] is True


def test_stricter_existing_hard_band_is_kept_for_replacement():
    budget = _replacement(
        adopted=(1800, 2000, 1800, 1800), hard_min_words=1900, hard_max_words=2200
    )
    assert budget["next_band"] == {
        "min_words": 1900,
        "target_words": 2200,
        "max_words": 2200,
    }


def test_deslop_cap_intersects_fixed_suffix_budget_again():
    budget = _replacement()
    window, feasible, code = length._request_window(budget, "deslop", {"word_count": 1900})
    assert feasible is True and code is None
    assert window == {"min_words": 1800, "target_words": 1800, "max_words": 1995}


@pytest.mark.parametrize(
    "adopted",
    [
        (2300, 2000, None, 2400),  # Gap in the actual adopted source census.
        (None, 2000, 2400, 2400),  # Missing native adopted prefix.
        (2300, None, 2400, 2400),  # Not a replacement: requested chapter lacks adoption.
    ],
)
def test_replacement_does_not_silently_accept_missing_adopted_sources(adopted):
    with pytest.raises(length.LengthControlError):
        _replacement(adopted=adopted)


@pytest.mark.parametrize("adopted", [(2300, 2000, 3501, 1800), (1799, 2000, 2400, 2400)])
def test_another_fixed_adopted_body_outside_absolute_wall_blocks(adopted):
    budget = _replacement(adopted=adopted)
    assert budget["feasible"] is False
    assert budget["issue_code"] == "ADOPTED_CHAPTER_HARD_BOUNDARY"
    assert budget["next_band"] is None and budget["targets"] == []


def test_pure_replacement_budget_does_not_mint_grade_or_adoption_authority():
    budget = _replacement()
    assert all(
        budget.get(key, False) is False for key in ("grade", "adoption", "credit", "dispatch")
    )
    assert "approved" not in budget


@pytest.mark.parametrize(
    "function", [length.freeze_request, length._validate_receipt, length.refresh_after_adoption]
)
def test_suffix_is_bound_in_request_validation_and_native_refresh(function):
    # Static supplemental contract, not proof of native adoption/source equality.
    node = ast.parse(inspect.getsource(function))
    literals = {
        item.value
        for item in ast.walk(node)
        if isinstance(item, ast.Constant) and isinstance(item.value, str)
    }
    assert "adopted_suffix" in literals, f"{function.__name__} omits fixed suffix source identity"
