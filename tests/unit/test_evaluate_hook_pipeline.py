from __future__ import annotations

import importlib.util
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[2] / "scripts" / "evaluate_hook_pipeline.py"
SPEC = importlib.util.spec_from_file_location("evaluate_hook_pipeline", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_no_champion_is_counted_as_loss_instead_of_skipped() -> None:
    winner, reason = MODULE._resolve_pairwise_votes(
        left_arm="minimal",
        right_arm="enhanced",
        left_full_pass=True,
        right_full_pass=False,
    )

    assert winner == "minimal"
    assert reason == "right_no_champion"


def test_two_no_champion_runs_are_recorded() -> None:
    winner, reason = MODULE._resolve_pairwise_votes(
        left_arm="minimal",
        right_arm="enhanced",
        left_full_pass=False,
        right_full_pass=False,
    )

    assert winner == "both_failed"
    assert reason == "both_no_champion"


def test_position_swapped_votes_must_agree_on_candidate() -> None:
    winner, reason = MODULE._resolve_pairwise_votes(
        left_arm="minimal",
        right_arm="enhanced",
        left_full_pass=True,
        right_full_pass=True,
        forward_winner="B",
        reverse_winner="A",
    )

    assert winner == "enhanced"
    assert reason == "consistent_position_swap"


def test_model_family_marks_minimax_variants_as_same_family() -> None:
    assert MODULE._model_family("minimax-m3") == "minimax"
    assert MODULE._model_family("minimax-m2.7-highspeed") == "minimax"
