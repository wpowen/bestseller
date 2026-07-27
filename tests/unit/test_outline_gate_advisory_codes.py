"""An advisory outline code must be advisory at EVERY enforcement site.

Field failure (2026-07-26, urban-power-reversal-1785026717): a book crashed
with a raw Python traceback —

    PlannerFallbackError: Whole-book outline semantic gate rejected promotion;
    replan is required. issues=OUTLINE_REUSED_PAYLOAD_ANCHOR

That code had already been diagnosed as a structural false positive the day
before and exempted at the BATCH-level gate (``planner.py`` ~3206, with the
reasoning inline: it claims to catch "a stale batch replaying an already-spent
payload" but each call site only ever sees ONE 3-chapter batch, so what it
really flags is "the first and last chapter quote the same phrase" — exactly
what a book with a named recurring mechanism does on purpose).

The whole-book gate was missed. Worse, it does not simply enforce the code —
it has two branches:

    hard_contract_findings(report)              if llm_adjudicated_all_volumes
    else <raw severity in {critical,high,block}>

``hard_contract_findings`` filters by ``OUTLINE_HARD_CONTRACT_CODES`` and
correctly drops the advisory code. The fallback re-derives the blocking set
from RAW SEVERITY and silently re-admits it. Same shape as the NAMING
raw-severity regression: a second path that bypasses the mapping.

Pinned here: one named set of advisory-only codes, honoured on both branches.
"""

from __future__ import annotations

import inspect

import pytest

from bestseller.services.outline_semantic_gate import (
    OUTLINE_ADVISORY_ONLY_CODES,
    OUTLINE_HARD_CONTRACT_CODES,
    blocking_findings_for_promotion,
)


pytestmark = pytest.mark.unit


class _Finding:
    def __init__(self, code: str, severity: str = "high") -> None:
        self.code = code
        self.severity = severity


class _Report:
    def __init__(self, findings) -> None:
        self.findings = tuple(findings)


def test_the_known_false_positive_is_declared_advisory() -> None:
    assert "OUTLINE_REUSED_PAYLOAD_ANCHOR" in OUTLINE_ADVISORY_ONLY_CODES


def test_advisory_codes_are_never_hard_contract_codes() -> None:
    """A code cannot be both 'never overridable' and 'never blocking'."""

    assert not (OUTLINE_ADVISORY_ONLY_CODES & OUTLINE_HARD_CONTRACT_CODES)


class TestBlockingSetHonoursAdvisoryOnBothBranches:
    def test_llm_adjudicated_branch_drops_advisory(self) -> None:
        report = _Report(
            [
                _Finding("OUTLINE_REUSED_PAYLOAD_ANCHOR"),
                _Finding("OUTLINE_STATE_REGRESSION"),
            ]
        )

        codes = {f.code for f in blocking_findings_for_promotion(report, llm_adjudicated=True)}

        assert codes == {"OUTLINE_STATE_REGRESSION"}

    def test_fallback_branch_also_drops_advisory(self) -> None:
        """THE regression: the non-adjudicated path used raw severity."""

        report = _Report(
            [
                _Finding("OUTLINE_REUSED_PAYLOAD_ANCHOR"),
                _Finding("OUTLINE_STATE_REGRESSION"),
            ]
        )

        codes = {f.code for f in blocking_findings_for_promotion(report, llm_adjudicated=False)}

        assert "OUTLINE_REUSED_PAYLOAD_ANCHOR" not in codes, (
            "the fallback branch must not re-admit an advisory-only code"
        )
        assert "OUTLINE_STATE_REGRESSION" in codes

    def test_advisory_alone_never_blocks_promotion(self) -> None:
        """A book whose ONLY complaint is the advisory code must proceed."""

        report = _Report([_Finding("OUTLINE_REUSED_PAYLOAD_ANCHOR")])

        assert blocking_findings_for_promotion(report, llm_adjudicated=False) == ()
        assert blocking_findings_for_promotion(report, llm_adjudicated=True) == ()

    def test_fallback_still_blocks_genuine_semantic_failures(self) -> None:
        """No loosening: the fallback keeps every non-advisory blocking code,
        including the semantic ones that are NOT hard-contract codes."""

        report = _Report(
            [
                _Finding("OPENING_PULL_PARAGRAPH_FAIL"),
                _Finding("OUTLINE_INFORMATION_CONTRACT_GAP"),
            ]
        )

        codes = {f.code for f in blocking_findings_for_promotion(report, llm_adjudicated=False)}

        assert codes == {"OPENING_PULL_PARAGRAPH_FAIL", "OUTLINE_INFORMATION_CONTRACT_GAP"}

    def test_low_severity_never_blocks(self) -> None:
        report = _Report([_Finding("OUTLINE_STATE_REGRESSION", severity="warn")])

        assert blocking_findings_for_promotion(report, llm_adjudicated=False) == ()


def test_planner_block_reaches_the_user_as_a_message_not_a_traceback() -> None:
    """A deliberate planning block must read as an explanation.

    ``PlannerFallbackError`` was the only deliberate-block exception without a
    handler in the web worker, so it fell through to the generic
    ``except Exception`` and the user saw a raw Python stack — indistinguishable
    from a framework crash, with no hint of what to change. Its siblings
    (ConceptContractError / AppealBarNotMetError / ProjectRepairPauseError) all
    render actionable text already.
    """

    import inspect

    from bestseller.web import server as web_server

    source = inspect.getsource(web_server.WebTaskManager._run_autowrite_worker)

    # The terminal fallback is the LAST `except Exception:` — the one that
    # formats a traceback. Inner try/excepts share the same text.
    generic_idx = source.rindex("except Exception:")
    handler_idx = source.find("except PlannerFallbackError")

    assert handler_idx != -1, "PlannerFallbackError needs its own handler"
    assert handler_idx < generic_idx, (
        "the specific handler must come before the generic traceback fallback"
    )
    assert "traceback.format_exc()" in source[generic_idx : generic_idx + 300], (
        "fixture assumption: the terminal handler is the traceback one"
    )


def test_whole_book_gate_uses_the_shared_helper() -> None:
    """Structural pin: the whole-book site must not re-derive the blocking set
    inline again — that inline copy is what drifted from the batch site."""

    from bestseller.services import planner

    source = inspect.getsource(planner.generate_novel_plan)
    idx = source.index("effective_blocking_findings")
    region = source[idx : idx + 600]

    assert "blocking_findings_for_promotion" in region, (
        "the whole-book gate must go through the shared helper so advisory "
        "codes cannot be re-admitted by a second inline severity filter"
    )
