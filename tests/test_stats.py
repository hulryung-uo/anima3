from anima3.stats import binom_two_sided, rounds_needed, two_proportions, wilson


def test_a_coin_is_not_significant_and_a_sweep_is():
    assert binom_two_sided(22, 42) > 0.7
    assert binom_two_sided(30, 30) < 1e-6
    lo, hi = wilson(22, 42)
    assert lo < 0.5 < hi


def test_two_arms_and_power():
    assert two_proportions(60, 100, 40, 100) < 0.01
    assert two_proportions(22, 42, 20, 42) > 0.5
    assert 180 < rounds_needed(0.1) < 220      # a 60% arm needs ~200 rounds to separate from a coin
