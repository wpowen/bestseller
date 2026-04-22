"""Unit tests for L4.5 Regeneration Loop.

The regen loop is pure orchestration — it takes a validator + regenerator
callable pair and drives them until the output passes quality gates or the
budget is exhausted. Tests below use stub callables to avoid touching the
real LLM stack.
"""

from __future__ import annotations

import pytest

from bestseller.services.output_validator import QualityReport, Violation
from bestseller.services.regen_loop import (
    DEFAULT_BUDGET_PER_CHAPTER,
    GlobalBudget,
    RegenAttempt,
    RegenerationExhausted,
    RegenerationResult,
    compose_feedback_from_violations,
    regenerate_until_valid,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


def _violation(code: str = "LANG_LEAK_CJK_IN_EN") -> Violation:
    return Violation(
        code=code,
        severity="block",
        location="chapter:head",
        detail=f"mock detail for {code}",
        prompt_feedback=f"integrate: fix {code}",
    )


def _passing_report() -> QualityReport:
    return QualityReport(violations=())


def _failing_report(*codes: str) -> QualityReport:
    if not codes:
        codes = ("LANG_LEAK_CJK_IN_EN",)
    return QualityReport(violations=tuple(_violation(c) for c in codes))


def _make_regenerator(outputs: list[str]):
    """Return an async callable that yields outputs[] in order."""

    call_log: list[str] = []

    async def regenerator(feedback: str) -> str:
        call_log.append(feedback)
        if not outputs:
            raise AssertionError("regenerator called more times than expected")
        return outputs.pop(0)

    regenerator.call_log = call_log  # type: ignore[attr-defined]
    return regenerator


def _make_validator(reports: list[QualityReport]):
    """Return an async callable that yields validation reports in order."""

    call_log: list[str] = []

    async def validator(text: str) -> QualityReport:
        call_log.append(text)
        if not reports:
            raise AssertionError("validator called more times than expected")
        return reports.pop(0)

    validator.call_log = call_log  # type: ignore[attr-defined]
    return validator


# ---------------------------------------------------------------------------
# Happy path.
# ---------------------------------------------------------------------------


class TestRegenerateUntilValid:
    @pytest.mark.asyncio
    async def test_initial_output_passing_short_circuits(self) -> None:
        regenerator = _make_regenerator([])  # must never be called
        validator = _make_validator([])

        result = await regenerate_until_valid(
            initial_output="valid",
            initial_report=_passing_report(),
            regenerator=regenerator,
            validator=validator,
        )

        assert isinstance(result, RegenerationResult)
        assert result.final_output == "valid"
        assert result.regen_count == 0
        assert result.final_report.blocks_write is False
        assert regenerator.call_log == []
        assert validator.call_log == []

    @pytest.mark.asyncio
    async def test_single_regen_recovers(self) -> None:
        regenerator = _make_regenerator(["good_output"])
        validator = _make_validator([_passing_report()])

        result = await regenerate_until_valid(
            initial_output="bad_output",
            initial_report=_failing_report(),
            regenerator=regenerator,
            validator=validator,
        )

        assert result.final_output == "good_output"
        assert result.regen_count == 1
        # Feedback should carry the violation integration instruction.
        assert "LANG_LEAK_CJK_IN_EN" in regenerator.call_log[0]

    @pytest.mark.asyncio
    async def test_multiple_regens_eventually_succeed(self) -> None:
        # Attempt 1 (initial): bad; attempt 2: still bad; attempt 3: good.
        regenerator = _make_regenerator(["still_bad", "finally_good"])
        validator = _make_validator([_failing_report("LENGTH_UNDER"), _passing_report()])

        result = await regenerate_until_valid(
            initial_output="initial_bad",
            initial_report=_failing_report("LANG_LEAK_CJK_IN_EN"),
            regenerator=regenerator,
            validator=validator,
        )

        assert result.final_output == "finally_good"
        assert result.regen_count == 2
        # Every attempt is logged in the trail.
        assert len(result.attempts) == 3
        assert [a.attempt_no for a in result.attempts] == [1, 2, 3]
        # Feedback for the second regen reflects the most recent violation.
        assert "LENGTH_UNDER" in regenerator.call_log[1]


# ---------------------------------------------------------------------------
# Budget exhaustion.
# ---------------------------------------------------------------------------


class TestBudgetExhaustion:
    @pytest.mark.asyncio
    async def test_budget_exhausted_raises(self) -> None:
        regenerator = _make_regenerator(["bad1", "bad2", "bad3"])
        validator = _make_validator([
            _failing_report(),
            _failing_report(),
            _failing_report(),
        ])

        with pytest.raises(RegenerationExhausted) as exc_info:
            await regenerate_until_valid(
                initial_output="initial_bad",
                initial_report=_failing_report(),
                regenerator=regenerator,
                validator=validator,
                budget=3,
            )

        # Attempt trail includes all 4 entries (initial + 3 retries).
        assert len(exc_info.value.attempts) == 4
        assert exc_info.value.budget == 3

    @pytest.mark.asyncio
    async def test_default_budget_is_three(self) -> None:
        assert DEFAULT_BUDGET_PER_CHAPTER == 3

    @pytest.mark.asyncio
    async def test_custom_budget_respected(self) -> None:
        # budget=1 should give exactly one retry (and then raise).
        regenerator = _make_regenerator(["still_bad"])
        validator = _make_validator([_failing_report()])

        with pytest.raises(RegenerationExhausted) as exc_info:
            await regenerate_until_valid(
                initial_output="initial_bad",
                initial_report=_failing_report(),
                regenerator=regenerator,
                validator=validator,
                budget=1,
            )

        # 1 initial + 1 retry = 2 attempts.
        assert len(exc_info.value.attempts) == 2


# ---------------------------------------------------------------------------
# Global budget governor.
# ---------------------------------------------------------------------------


class TestGlobalBudget:
    @pytest.mark.asyncio
    async def test_global_budget_stops_loop_without_raising(self) -> None:
        # Global budget allows 1 regen. Per-chapter budget allows 3.
        # After the first regen, global is exhausted → loop exits with
        # best-effort output instead of raising.
        regenerator = _make_regenerator(["still_bad"])
        validator = _make_validator([_failing_report()])
        gb = GlobalBudget(total=1, used=0)

        result = await regenerate_until_valid(
            initial_output="initial_bad",
            initial_report=_failing_report(),
            regenerator=regenerator,
            validator=validator,
            budget=3,
            global_budget=gb,
        )

        # One regen happened, global budget consumed, still blocking.
        assert result.final_output == "still_bad"
        assert gb.used == 1
        assert result.final_report.blocks_write is True

    @pytest.mark.asyncio
    async def test_global_budget_preemptive_exit(self) -> None:
        # Global budget already exhausted → loop doesn't even try first regen.
        regenerator = _make_regenerator([])
        validator = _make_validator([])
        gb = GlobalBudget(total=5, used=5)

        result = await regenerate_until_valid(
            initial_output="initial_bad",
            initial_report=_failing_report(),
            regenerator=regenerator,
            validator=validator,
            budget=3,
            global_budget=gb,
        )

        # No regen happened.
        assert result.final_output == "initial_bad"
        assert regenerator.call_log == []

    def test_global_budget_remaining(self) -> None:
        gb = GlobalBudget(total=12, used=4)
        assert gb.remaining == 8
        gb.consume(3)
        assert gb.remaining == 5
        # Over-consume clamps to 0.
        gb.consume(100)
        assert gb.remaining == 0


# ---------------------------------------------------------------------------
# Error propagation.
# ---------------------------------------------------------------------------


class TestErrorPropagation:
    @pytest.mark.asyncio
    async def test_regenerator_exception_raises_exhausted(self) -> None:
        async def bad_regenerator(feedback: str) -> str:
            raise RuntimeError("LLM is down")

        validator = _make_validator([])

        with pytest.raises(RegenerationExhausted) as exc_info:
            await regenerate_until_valid(
                initial_output="initial_bad",
                initial_report=_failing_report(),
                regenerator=bad_regenerator,
                validator=validator,
            )

        assert "regenerator_exception" in exc_info.value.reason
        # Only the initial attempt is in the trail — regen never completed.
        assert len(exc_info.value.attempts) == 1

    @pytest.mark.asyncio
    async def test_validator_exception_raises_exhausted(self) -> None:
        regenerator = _make_regenerator(["new_output"])

        async def bad_validator(text: str) -> QualityReport:
            raise RuntimeError("DB roster lookup failed")

        with pytest.raises(RegenerationExhausted) as exc_info:
            await regenerate_until_valid(
                initial_output="initial_bad",
                initial_report=_failing_report(),
                regenerator=regenerator,
                validator=bad_validator,
            )

        assert "validator_exception" in exc_info.value.reason


# ---------------------------------------------------------------------------
# compose_feedback_from_violations.
# ---------------------------------------------------------------------------


class TestComposeFeedback:
    def test_empty_violations_returns_empty_string(self) -> None:
        assert compose_feedback_from_violations(()) == ""

    def test_single_violation_includes_code_and_feedback(self) -> None:
        v = _violation("LENGTH_UNDER")
        out = compose_feedback_from_violations((v,))
        assert "LENGTH_UNDER" in out
        assert "integrate: fix LENGTH_UNDER" in out

    def test_multiple_violations_numbered(self) -> None:
        out = compose_feedback_from_violations((
            _violation("A_CODE"),
            _violation("B_CODE"),
        ))
        assert "1) [A_CODE]" in out
        assert "2) [B_CODE]" in out

    def test_custom_header(self) -> None:
        out = compose_feedback_from_violations(
            (_violation("X"),), header="CUSTOM:\n"
        )
        assert out.startswith("CUSTOM:")


# ---------------------------------------------------------------------------
# RegenerationResult accessors.
# ---------------------------------------------------------------------------


class TestRegenerationResult:
    def test_violation_codes_history(self) -> None:
        result = RegenerationResult(
            final_output="ok",
            attempts=(
                RegenAttempt(
                    attempt_no=1,
                    feedback="",
                    output="bad1",
                    report=_failing_report("A", "B"),
                ),
                RegenAttempt(
                    attempt_no=2,
                    feedback="fix A and B",
                    output="bad2",
                    report=_failing_report("A"),
                ),
                RegenAttempt(
                    attempt_no=3,
                    feedback="fix A",
                    output="ok",
                    report=_passing_report(),
                ),
            ),
        )

        assert result.violation_codes_history() == (
            ("A", "B"),
            ("A",),
            (),
        )
        assert result.regen_count == 2
        assert result.final_report.blocks_write is False
