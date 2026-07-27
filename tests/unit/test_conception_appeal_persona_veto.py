"""Persona veto must not starve the bounded regeneration loop.

Field failure (2026-07-24): two validation books were hard-killed during
conception with ``attempts=0``.

* ``apocalypse-supply-1784833397`` — blurb 73.5, title 89.6
* ``xianxia-upgrade-1784855398``  — blurb 81.4, title 94.8

Both cleared the numeric bar (``meets_bar.blurb_min=68`` / ``title_min=80``),
so the regeneration ``while`` — whose condition read only ``report.meets_bar``
— never entered even once. The actual killer was ``persona_judge`` (0/3
simulated clicks) which holds ``block_below: true``. Its feedback string was
built and then discarded, because the only consumer sat inside the loop body
that never ran.

``config/story_appeal.yaml`` states the intended contract explicitly:
"persona 反馈一个真实的收敛机会,仍不达标才硬拦" — give the persona feedback a
real convergence chance, hard-block only if it still fails. These tests pin
that contract so a gate holding veto power can never again skip the repair
rounds it is supposed to drive.
"""

from __future__ import annotations

import inspect

import pytest

from bestseller.services import conception as conception_services
from bestseller.services.story_appeal import (
    AppealBarNotMetError,
    appeal_regen_should_continue,
    persona_hard_veto,
)


pytestmark = pytest.mark.unit


_VETO_CONFIG = {"persona_judge": {"block_below": True, "click_rate_min": 0.34}}
_ADVISORY_CONFIG = {"persona_judge": {"block_below": False, "click_rate_min": 0.34}}


def test_persona_hard_veto_when_judge_blocks_and_idea_failed() -> None:
    assert persona_hard_veto({"advisory_pass": False}, _VETO_CONFIG) is True


def test_persona_hard_veto_false_when_idea_passed() -> None:
    assert persona_hard_veto({"advisory_pass": True}, _VETO_CONFIG) is False


def test_persona_hard_veto_false_when_block_below_disabled() -> None:
    """advisory-only posture must never block, however bad the click rate."""

    assert persona_hard_veto({"advisory_pass": False}, _ADVISORY_CONFIG) is False


@pytest.mark.parametrize("report", [None, {}, "not-a-dict", []])
def test_persona_hard_veto_fails_open_on_unusable_report(report) -> None:
    """LLM unavailable / malformed report must never kill a book."""

    assert persona_hard_veto(report, _VETO_CONFIG) is False


def test_regen_continues_when_persona_blocks_even_though_scores_pass() -> None:
    """THE regression: numeric bar met, persona vetoing → must still regenerate.

    This is the exact shape of both field failures.
    """

    assert (
        appeal_regen_should_continue(
            enabled=True,
            attempts=0,
            max_attempts=2,
            needs_score_regen=False,
            persona_blocks=True,
        )
        is True
    )


def test_regen_continues_when_scores_fail_and_persona_passes() -> None:
    assert (
        appeal_regen_should_continue(
            enabled=True,
            attempts=0,
            max_attempts=2,
            needs_score_regen=True,
            persona_blocks=False,
        )
        is True
    )


def test_regen_stops_when_nothing_is_blocking() -> None:
    assert (
        appeal_regen_should_continue(
            enabled=True,
            attempts=0,
            max_attempts=2,
            needs_score_regen=False,
            persona_blocks=False,
        )
        is False
    )


def test_regen_respects_attempt_budget_even_while_persona_blocks() -> None:
    """Bounded: a permanently-vetoing judge must not spin forever."""

    assert (
        appeal_regen_should_continue(
            enabled=True,
            attempts=2,
            max_attempts=2,
            needs_score_regen=True,
            persona_blocks=True,
        )
        is False
    )


def test_regen_disabled_short_circuits() -> None:
    assert (
        appeal_regen_should_continue(
            enabled=False,
            attempts=0,
            max_attempts=3,
            needs_score_regen=True,
            persona_blocks=True,
        )
        is False
    )


def test_appeal_error_names_the_gate_that_actually_blocked() -> None:
    """The field message read "appeal bar not met (blurb=81.4 title=94.8)" —
    both numbers had PASSED. Operators must see which gate really blocked."""

    err = AppealBarNotMetError(
        {"blurb": {"total": 81.4}, "title": {"total": 94.8}},
        "0/3 会点",
        blocked_by=("persona_judge",),
    )
    text = str(err)
    assert "persona_judge" in text
    assert err.blocked_by == ("persona_judge",)
    # Scores stay in the message as context, but must not be the stated cause.
    assert "81.4" in text


def test_appeal_error_defaults_stay_backward_compatible() -> None:
    err = AppealBarNotMetError({"blurb": {"total": 51.0}}, "too weak")
    assert err.blocked_by == ()
    assert "51.0" in str(err)


def test_finalize_loop_condition_consults_persona_verdict() -> None:
    """Structural pin (the loop is inlined in a ~4000-line pipeline function).

    Mirrors the house convention used by test_persona_click_judge_wiring.py:
    assert on the control-flow anchor so the fix cannot be silently undone.
    """

    source = inspect.getsource(conception_services.run_conception_pipeline)

    assert "appeal_regen_should_continue(" in source, (
        "regen loop must route through the shared predicate so the persona "
        "verdict is part of the continuation decision"
    )
    assert "persona_blocks=" in source


def test_finalize_refreshes_persona_verdict_inside_regen_loop() -> None:
    """Without an in-loop refresh the loop cannot observe a fixed blurb and
    would burn its whole budget (or exit on a stale verdict)."""

    source = inspect.getsource(conception_services.run_conception_pipeline)
    loop_start = source.index("appeal_regen_should_continue(")
    # The loop body ends where the winning candidate is unpacked back out.
    loop_end = source.index("report, premise, synopsis, tags, title = best", loop_start)
    loop_region = source[loop_start:loop_end]

    assert "_persona_click_advisory(" in loop_region, (
        "persona verdict must be re-evaluated against the regenerated blurb"
    )
    assert "_persona_blocks = persona_hard_veto(" in loop_region, (
        "the refreshed verdict must update the loop's continuation flag"
    )
