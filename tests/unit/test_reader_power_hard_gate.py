from bestseller.services.reader_power_gate import evaluate_reader_power_gate, holm_adjusted_alpha


def test_power_gate_is_hard_at_n60_and_080():
    assert evaluate_reader_power_gate(sample_size=59, preregistered_power=0.9).passed is False
    assert evaluate_reader_power_gate(sample_size=60, preregistered_power=0.8).passed is True
    assert evaluate_reader_power_gate(sample_size=60, preregistered_power=0.79).passed is False


def test_power_gate_enforces_effect_ci_and_holm():
    result = evaluate_reader_power_gate(
        sample_size=60,
        observed_successes=36,
        preregistered_power=0.9,
        p_values={"genre": 0.01, "scope": 0.02},
    )
    assert result.passed is False
    assert result.adjusted_alpha == 0.025
    assert holm_adjusted_alpha([]) == 0.05
