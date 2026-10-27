from bestseller.services.platform_metric_reducer import (
    empirical_quantile_type7,
    percentile_bootstrap,
)


def test_type7_percentiles() -> None:
    assert empirical_quantile_type7([0, 1, 2, 3], 0.25) == 0.75
    assert empirical_quantile_type7([0, 1, 2, 3], 0.5) == 1.5


def test_book_cluster_bootstrap_is_reproducible() -> None:
    first = percentile_bootstrap([0.2, 0.4, 0.6, 0.8], seed=42, repetitions=200)
    second = percentile_bootstrap([0.2, 0.4, 0.6, 0.8], seed=42, repetitions=200)
    assert first == second
    assert first is not None and first[0] <= 0.5 <= first[1]
