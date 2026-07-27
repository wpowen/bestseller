"""The logline hard gate must be able to tell a bestseller from garbage.

Measured 2026-07-25 with the production judge (MiniMax-M3), production
thresholds, and the same call shape conception uses (logline + premise +
genre) — feeding it the repo's OWN reference bestsellers from
``config/appeal_reference_blurbs.yaml``:

    《斗破苍穹》 REJECT  overall 1.59/5   8 of 9 core axes under floor
    《完美世界》 REJECT  overall 1.98/5   9 of 9 core axes under floor
    《诡秘之主》 REJECT  overall 2.93/5   7 of 9 core axes under floor

Three of the best-selling Chinese web novels ever written, rejected 3/3. A gate
that scores 斗破苍穹's 反差张力 at 1.0 is not a strict gate — it is a broken
instrument, and its verdicts carry no information.

There is a matching STRUCTURAL contradiction that needs no LLM to see. The
judge's own anchors (``logline_gate.py``) read:

    3：合格但平庸，会划走      4：被钩住、想点

``pass_floor`` is 3.5 — above the judge's own definition of 合格, i.e. passing
requires nearly a 4 on ALL NINE hard-veto axes simultaneously. Conjunctive
vetoes across nine independently-judged dimensions compound: even a generous
85%-per-axis pass rate yields 0.85**9 ≈ 23% for the book.

Precedent for the fix: the sibling blurb gate had the identical defect. Its bar
sat at 80 until someone measured real bestsellers (斗破 71 / 诡秘 68 / 大奉 68)
and recalibrated to 68, recorded in ``config/story_appeal.yaml`` as
"旧值80脱离现实——真爆款简介都到不了80". The logline gate never got that
measurement — so it ships advisory until it earns the veto back via
``scripts/logline_gate_calibration.py``.
"""

from __future__ import annotations

import pytest

from bestseller.services.logline_gate import (
    CORE_AXES,
    load_logline_gate_config,
)


pytestmark = pytest.mark.unit


def test_gate_does_not_hard_block_until_it_is_calibrated() -> None:
    """It still runs, still scores, still drives regeneration — it just may not
    be the thing that kills a book while it rejects 3/3 proven bestsellers."""

    cfg = load_logline_gate_config(None)

    assert cfg["enabled"] is True, "keep measuring — the signal is still logged"
    assert cfg["block_expansion"] is False, (
        "a gate that rejects 斗破苍穹/完美世界/诡秘之主 must not hold veto "
        "power; re-enable only after scripts/logline_gate_calibration.py "
        "shows real bestsellers passing"
    )


def test_pass_floor_is_not_above_the_judges_own_definition_of_acceptable() -> None:
    """Structural check, no LLM needed.

    The rubric anchors 3 as 合格 (acceptable). A pass_floor above 3 means the
    gate rejects everything its own judge calls acceptable — on every one of
    nine conjunctive axes.
    """

    import inspect

    from bestseller.services import logline_gate

    source = inspect.getsource(logline_gate)
    assert "3：合格但平庸" in source, "fixture assumption: the anchor wording"

    cfg = load_logline_gate_config(None)
    assert cfg["pass_floor"] <= 3.0, (
        f"pass_floor={cfg['pass_floor']} sits above the judge's own 合格 "
        "anchor of 3 — passing then demands ~4/5 ('被钩住、想点') on all "
        f"{len(CORE_AXES)} hard-veto axes at once"
    )


def test_conjunctive_veto_count_is_documented_as_a_risk() -> None:
    """Nine independent LLM-judged hard vetoes is the compounding-failure shape.

    Not asserting a maximum — the axes are individually reasonable. Asserting
    that anyone adding a tenth has to see this test and think about the product
    of the per-axis pass rates.
    """

    assert len(CORE_AXES) == 9, (
        f"core hard-veto axes changed to {len(CORE_AXES)}; each one multiplies "
        "into the book's total pass probability — re-run "
        "scripts/logline_gate_calibration.py before shipping the change"
    )


def test_configured_regeneration_budget_is_actually_read() -> None:
    """Dead config is worse than no config: it looks tuned and is not.

    ``load_logline_gate_config`` emits ``max_regen``; conception read
    ``regenerate_attempts``, a key that function never produces — so the yaml's
    ``max_regen: 3`` did nothing and every book silently got a hardcoded 2
    rescue rounds.
    """

    import inspect

    from bestseller.services import conception

    cfg = load_logline_gate_config(None)
    assert "max_regen" in cfg
    assert "regenerate_attempts" not in cfg, (
        "if the loader starts emitting this key, update the reader too"
    )

    source = inspect.getsource(conception.run_conception_pipeline)
    idx = source.index("_logline_regen_rescue")
    region = source[idx : idx + 1200]
    assert 'get("max_regen")' in region, (
        "the rescue budget must read the key the config loader actually emits"
    )


def test_calibration_script_exists_and_uses_real_references() -> None:
    from pathlib import Path

    script = Path("scripts/logline_gate_calibration.py").read_text(encoding="utf-8")

    assert "appeal_reference_blurbs" in script
    assert "evaluate_logline_gate" in script
    for mutation in ("UPDATE ", "DELETE ", "session.add(", "commit()"):
        assert mutation not in script, "calibration must stay read-only"
