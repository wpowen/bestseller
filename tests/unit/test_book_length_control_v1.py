"""Opt-in length contract; pure tests, with no database/model access."""

from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
import subprocess
import sys
from types import ModuleType

import pytest

from bestseller.services import length_stability_gate as gate
from bestseller.services import word_targets as words
from bestseller.settings import load_settings

pytestmark = pytest.mark.unit
PROTOCOL = "book-length-control-v1"
ROOT = Path(__file__).resolve().parents[2]


def _baseline(name: str):
    path = f"src/bestseller/services/{name}.py"
    source = subprocess.check_output(["git", "show", f"refs/tags/e2e17:{path}"], cwd=ROOT)
    module = ModuleType(f"_p12_baseline_{name}")
    sys.modules[module.__name__] = module
    exec(compile(source, path, "exec"), module.__dict__)
    # New helpers may only be appended; legacy source bytes remain intact.
    assert (ROOT / path).read_bytes().startswith(source)
    return module


def _budget(total=8000, chapters=4, adopted=(), **kwargs):
    return words.plan_remaining_word_budget(
        total_words=total,
        total_chapters=chapters,
        adopted_word_counts=adopted,
        protocol=PROTOCOL,
        **kwargs,
    )


def _decision(count, *, stage="draft", parent=None, minimum=1800, maximum=2300):
    return gate.evaluate_length_control_candidate(
        word_count=count,
        min_words=minimum,
        target_words=2000,
        max_words=maximum,
        protocol=PROTOCOL,
        stage=stage,
        parent_word_count=parent,
    )


def test_default_off_is_not_an_acceptance_or_a_budget():
    assert words.chapter_length_control_band(2000) is None
    assert (
        words.plan_remaining_word_budget(
            total_words=8000,
            total_chapters=4,
            adopted_word_counts=(7740,),
        )
        is None
    )
    assert (
        gate.evaluate_length_control_candidate(
            word_count=9000,
            min_words=1800,
            target_words=2000,
            max_words=3500,
        )
        is None
    )


def test_legacy_gate_source_and_serialized_verdicts_are_byte_identical():
    baseline = _baseline("length_stability_gate")
    for enabled in (False, True):
        for count in (0, 1619, 1620, 1799, 1800, 2000, 2300, 3500, 3501, 3851):
            kwargs = {
                "word_count": count,
                "min_words": 1800,
                "target_words": 2000,
                "max_words": 3500,
                "hard_min_words": 1800,
                "hard_max_words": 3500,
                "enabled": enabled,
            }

            def serialize(r):
                return json.dumps(asdict(r), sort_keys=True).encode()

            assert serialize(gate.evaluate_chapter_length(**kwargs)) == serialize(
                baseline.evaluate_chapter_length(**kwargs)
            )


def test_legacy_word_targets_source_and_band_bytes_are_identical():
    baseline = _baseline("word_targets")
    settings = load_settings(env={"BESTSELLER__LLM__EDITOR__MAX_TOKENS": "16000"})
    for target in (1500, 2000, 2500, 3500, 6000):
        for language in ("zh-CN", "en"):
            for direction in ("normal", "under", "over"):
                kwargs = {"language": language, "direction": direction, "role": "editor"}

                def serialize(r):
                    return json.dumps(asdict(r), sort_keys=True).encode()

                assert serialize(words.chapter_rewrite_length_band(settings, target, **kwargs)) == (
                    serialize(baseline.chapter_rewrite_length_band(settings, target, **kwargs))
                )


@pytest.mark.parametrize(
    "target,expected",
    [
        (2000, (1800, 2000, 2300)),
        (2500, (2125, 2500, 2875)),
        (2001, (1800, 2001, 2301)),
        (1800, (1800, 1800, 2070)),
        (4000, (3400, 3500, 3500)),
    ],
)
def test_controlled_bands_use_inward_integer_rounding_and_hard_walls(target, expected):
    band = words.chapter_length_control_band(target, protocol=PROTOCOL)
    assert (band.min_words, band.target_words, band.max_words) == expected


def test_short_target_cannot_lower_absolute_floor():
    with pytest.raises(ValueError, match="no legal"):
        words.chapter_length_control_band(1500, protocol=PROTOCOL)
    assert not _budget(6000, 4).feasible


@pytest.mark.parametrize(
    "total,chapters,adopted,expected",
    [
        (8000, 4, (2300,), (1900, 1900, 1900)),
        (8000, 4, (2600,), (1800, 1800, 1800)),
        (15000, 6, (2875,), (2425, 2425, 2425, 2425, 2425)),
        (15000, 6, (3200,), (2360, 2360, 2360, 2360, 2360)),
    ],
)
def test_long_early_chapter_pulls_remaining_targets_back(total, chapters, adopted, expected):
    result = _budget(total, chapters, adopted)
    assert result.feasible and result.targets == expected
    assert sum(adopted) + sum(result.targets) == total
    assert result.next_band.min_words <= expected[0] <= result.next_band.max_words
    assert (
        result.next_band.max_words <= 2300 if total == 8000 else result.next_band.max_words <= 2875
    )


def test_original_book_remaining_1460_is_infeasible_not_a_short_chapter():
    result = _budget(adopted=(2495, 2433, 2812))
    assert not result.feasible
    assert result.issue_code == "BOOK_LENGTH_BUDGET_INFEASIBLE"
    assert result.book_max_words - result.adopted_words == 1460
    assert result.targets == () and result.next_band is None


@pytest.mark.parametrize(
    "count,accepted,code",
    [
        (1799, False, "LENGTH_CONTROL_UNDER"),
        (1800, True, None),
        (2300, True, None),
        (2301, False, "LENGTH_CONTROL_OVER"),
    ],
)
@pytest.mark.parametrize("stage", ["draft", "rewrite"])
def test_candidate_boundaries_are_inclusive_without_warning_buffer(count, accepted, code, stage):
    result = _decision(count, stage=stage)
    assert result.accepted is accepted and result.issue_code == code
    assert result.protocol == PROTOCOL


def test_deslop_respects_both_parent_growth_cap_and_contract_cap():
    assert _decision(2100, stage="deslop", parent=2000).accepted
    assert not _decision(2101, stage="deslop", parent=2000).accepted
    assert _decision(2301, stage="deslop", parent=2250).issue_code == "LENGTH_CONTROL_OVER"
    assert _decision(2300, stage="deslop", parent=2250).accepted
    assert _decision(2101, stage="deslop", parent=2001).accepted
    assert not _decision(2102, stage="deslop", parent=2001).accepted


@pytest.mark.parametrize("total,chapters", [(8000, 4), (15000, 6)])
@pytest.mark.parametrize("extreme", ["min_words", "max_words"])
def test_every_accepted_extreme_preserves_book_tolerance(total, chapters, extreme):
    adopted = ()
    for _ in range(chapters):
        result = _budget(total, chapters, adopted)
        assert result.feasible
        count = getattr(result.next_band, extreme)
        assert gate.evaluate_length_control_candidate(
            word_count=count,
            **asdict(result.next_band),
            protocol=PROTOCOL,
        ).accepted
        adopted += (count,)
    final = _budget(total, chapters, adopted)
    assert final.feasible and final.next_band is None and final.targets == ()
    assert final.book_min_words <= sum(adopted) <= final.book_max_words


def test_exact_target_can_be_unreachable_while_tolerance_remains_feasible():
    result = _budget(adopted=(2300, 2300, 2300))
    assert result.feasible and not result.exact_target_feasible
    assert result.targets == (1800,)
    assert result.next_band.max_words <= 2300


def test_integer_remainders_are_conserved():
    result = _budget(8001, 4, (2300,))
    assert result.targets == (1901, 1900, 1900)
    assert sum(result.targets) + 2300 == 8001


def test_unequal_original_targets_remain_anchored():
    result = _budget(8000, 4, (2300,), planned_targets=(2000, 1800, 2000, 2200))
    assert result.feasible and sum(result.targets) == 5700
    assert 1800 <= result.targets[0] <= 2070


def test_completed_but_overbudget_is_not_feasible():
    result = _budget(adopted=(2300, 2300, 2300, 2301))
    assert not result.feasible and result.issue_code == "BOOK_LENGTH_OUT_OF_RANGE"
    assert result.next_band is None


@pytest.mark.parametrize("bad", [-1, 1.5, True])
def test_invalid_counts_are_not_coerced(bad):
    with pytest.raises(ValueError):
        _budget(adopted=(bad,))
    with pytest.raises(ValueError):
        _decision(bad)


def test_unknown_protocol_and_stage_or_missing_parent_fail_closed():
    with pytest.raises(ValueError, match="protocol"):
        words.chapter_length_control_band(2000, protocol="future-v9")
    with pytest.raises(ValueError, match="protocol"):
        words.plan_remaining_word_budget(
            total_words=8000, total_chapters=4, adopted_word_counts=(), protocol="future-v9"
        )
    with pytest.raises(ValueError, match="protocol"):
        gate.evaluate_length_control_candidate(
            word_count=2000, min_words=1800, target_words=2000, max_words=2300, protocol="future-v9"
        )
    with pytest.raises(ValueError, match="stage"):
        _decision(2000, stage="untrusted")
    with pytest.raises(ValueError, match="parent"):
        _decision(2000, stage="deslop")


@pytest.mark.parametrize(
    "kwargs",
    [
        {"adopted_word_counts": (2000,) * 5},
        {"planned_targets": (2000, 2000)},
        {"planned_targets": (1900,) * 4},
    ],
)
def test_invalid_chapter_census_or_plan_is_not_silently_repaired(kwargs):
    with pytest.raises(ValueError):
        words.plan_remaining_word_budget(
            total_words=8000,
            total_chapters=4,
            protocol=PROTOCOL,
            **({"adopted_word_counts": ()} | kwargs),
        )


@pytest.mark.parametrize("adopted", [(3501,), (1799, 2000, 2000, 2201)])
def test_invalid_absolute_prefix_blocks_new_generation_without_regrading_old_receipts(adopted):
    result = _budget(adopted=adopted)
    assert not result.feasible
    assert result.issue_code == "ADOPTED_CHAPTER_HARD_BOUNDARY"
    assert result.targets == () and result.next_band is None


@pytest.mark.parametrize(
    "adopted,accepted",
    [
        ((2126, 2125, 2125, 2125, 2125, 2125), True),
        ((2125, 2125, 2125, 2125, 2125, 2125), False),
        ((2876, 2875, 2875, 2875, 2875, 2875), True),
        ((2877, 2875, 2875, 2875, 2875, 2875), False),
    ],
)
def test_non_round_book_total_uses_inward_integer_limits(adopted, accepted):
    result = _budget(15001, 6, adopted)
    assert (result.book_min_words, result.book_max_words) == (12751, 17251)
    assert result.feasible is accepted


def test_saturated_chapter_capacity_redistributes_remainders():
    result = _budget(10001, 4, (3000,), planned_targets=(3500, 1800, 2201, 2500))
    assert result.targets == (2070, 2339, 2592)


@pytest.mark.parametrize("extreme", ["min_words", "max_words"])
def test_unequal_plan_extremes_preserve_frozen_bands_and_book_feasibility(extreme):
    targets = (3500, 1800, 2201, 2500)
    adopted = ()
    for target in targets:
        plan = _budget(10001, 4, adopted, planned_targets=targets)
        original = words.chapter_length_control_band(target, protocol=PROTOCOL)
        assert plan.feasible
        assert original.min_words <= plan.next_band.min_words <= plan.next_band.max_words
        assert plan.next_band.max_words <= original.max_words
        adopted += (getattr(plan.next_band, extreme),)
    assert _budget(10001, 4, adopted, planned_targets=targets).feasible


def test_original_book_rescue_obeys_dynamic_window_not_only_book_ceiling():
    first_two = (2495, 2433)
    third_plan = _budget(adopted=first_two)
    assert (third_plan.next_band.min_words, third_plan.next_band.max_words) == (1800, 2070)
    assert not gate.evaluate_length_control_candidate(
        word_count=2272,
        **asdict(third_plan.next_band),
        protocol=PROTOCOL,
    ).accepted
    assert gate.evaluate_length_control_candidate(
        word_count=2070,
        **asdict(third_plan.next_band),
        protocol=PROTOCOL,
    ).accepted
    fourth_plan = _budget(adopted=(*first_two, 2070))
    assert gate.evaluate_length_control_candidate(
        word_count=2000,
        **asdict(fourth_plan.next_band),
        protocol=PROTOCOL,
    ).accepted
    final = _budget(adopted=(*first_two, 2070, 2000))
    assert final.feasible and final.adopted_words == 8998
