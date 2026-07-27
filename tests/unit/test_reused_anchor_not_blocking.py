"""A book's signature mechanism is not a "spent payload".

Field incident (2026-07-25, 《仇人膝上养帝王》): outline batch 4-6 failed
``OUTLINE_REUSED_PAYLOAD_ANCHOR@ch4`` three times in a row, which failed the
volume-outline gate, which the auto-resume loop then retried forever —
118 LLM calls / ~880k tokens with zero chapters produced.

Why the detector cannot be right at this call site:

* Its own comment states the signal it is looking for — "a stale batch
  replaying an already-spent event payload" — i.e. a REGENERATED batch echoing
  content from a PREVIOUS batch.
* But ``planner`` calls it with only the current batch's rows
  (``{"chapters": normalized_payload["chapters"]}``), 3 chapters at a time.
  Cross-batch replay is invisible from there.
* Its rule is "a quoted phrase ≥6 chars appearing in two chapters ≥2 apart".
  Inside a 3-chapter batch only the FIRST and LAST chapter can satisfy that.
  So in practice it fires on: "chapters 4 and 6 both mention the same quoted
  phrase" — which is exactly what a book with a named recurring mechanism does
  by design (here “婴啼-注视-回应三连暗号”, the baby protagonist's whole
  method of communicating).

The repair directive it emits ("该情报或事件已被前章消耗，换成新的行动对象…")
asks the model to delete the book's core mechanism, which is why three repair
attempts could not satisfy it.

Kept as a REPORTED finding — cross-batch replay is a real defect worth seeing —
but it may not block promotion on evidence this thin. Same shape as the
``ai_flavor`` detector's ``_ADVISORY_STRUCTURAL`` cap, which stops content-blind
families from blocking a chapter on their own.
"""

from __future__ import annotations

import pytest

from bestseller.services.outline_semantic_gate import (
    evaluate_outline_semantic_gate,
    hard_contract_findings,
)


pytestmark = pytest.mark.unit


def _chapter(number: int, text: str) -> dict:
    return {
        "chapter_number": number,
        "summary": text,
        "chapter_goal": text,
    }


def test_signature_mechanism_recurring_in_a_batch_is_still_detected() -> None:
    """Detection is preserved — the signal is real information."""

    report = evaluate_outline_semantic_gate(
        {
            "chapters": [
                _chapter(4, '他用“婴啼-注视-回应三连暗号”向老宦官确认时机。'),
                _chapter(5, '萧崇抱他去点兵，暗号没有机会送出。'),
                _chapter(6, '他再次动用“婴啼-注视-回应三连暗号”，这次换来一个名字。'),
            ]
        }
    )

    codes = [f.code for f in report.findings]
    assert "OUTLINE_REUSED_PAYLOAD_ANCHOR" in codes, (
        "the finding should still be reported for inspection"
    )


def test_reused_anchor_alone_does_not_block_promotion() -> None:
    """THE loop. A recurring named mechanism must not fail the outline gate."""
    report = evaluate_outline_semantic_gate(
        {
            "chapters": [
                _chapter(4, '他用“婴啼-注视-回应三连暗号”向老宦官确认时机。'),
                _chapter(5, '萧崇抱他去点兵，暗号没有机会送出。'),
                _chapter(6, '他再次动用“婴啼-注视-回应三连暗号”，这次换来一个名字。'),
            ]
        }
    )

    assert "OUTLINE_REUSED_PAYLOAD_ANCHOR" not in {
        finding.code for finding in hard_contract_findings(report)
    }


def test_genuinely_structural_findings_still_block() -> None:
    """No blanket loosening — the codes that CAN be judged from the rows alone
    keep their veto."""

    report = evaluate_outline_semantic_gate(
        {
            "chapters": [
                {
                    "chapter_number": 4,
                    "chapter_goal": "推进选择",
                    "information_revealed": ["秘密"],
                    "metadata": {"causal_contract": {"pressure": "逼近"}},
                },
                {"chapter_number": 5, "chapter_goal": "承受后果"},
            ]
        }
    )

    hard_codes = {finding.code for finding in hard_contract_findings(report)}
    assert "OUTLINE_INFORMATION_CONTRACT_GAP" in hard_codes
