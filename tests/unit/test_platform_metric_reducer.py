from bestseller.services.platform_metric_reducer import (
    all_primary_metrics_pass,
    holm_adjust,
    wilson_interval,
)


def test_wilson_interval_is_bounded() -> None:
    lower, upper = wilson_interval(80, 100) or (None, None)
    assert 0 <= lower <= 0.8 <= upper <= 1


def test_holm_adjustment_preserves_original_order() -> None:
    assert holm_adjust((0.001, 0.04, 0.9)) == (True, False, False)


def test_all_primary_metrics_are_conjunctive() -> None:
    assert all_primary_metrics_pass(((0.7, 0.6, 0.5, 0.6), (0.8, 0.7, 0.6, 0.7)))
    assert not all_primary_metrics_pass(((0.7, 0.6, 0.5, 0.6), (0.8, 0.4, 0.6, 0.7)))
