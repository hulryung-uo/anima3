"""Round-win statistics: is a win rate distinguishable from a coin, and from another arm?

The duels are decided in rounds, and rounds within a match are close to independent (both
fighters are restored between them), so a win rate over rounds is a binomial proportion.
"""
from __future__ import annotations

import math


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson interval for k successes in n trials."""
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def binom_two_sided(k: int, n: int, p: float = 0.5) -> float:
    """Exact two-sided binomial p-value (sum of outcomes no more likely than the observed one)."""
    if n == 0:
        return 1.0
    pmf = [math.comb(n, i) * p ** i * (1 - p) ** (n - i) for i in range(n + 1)]
    obs = pmf[k] * (1 + 1e-9)
    return min(1.0, sum(x for x in pmf if x <= obs))


def two_proportions(k1: int, n1: int, k2: int, n2: int) -> float:
    """Two-sided p-value of a pooled z-test that two win rates differ."""
    if not n1 or not n2:
        return 1.0
    pooled = (k1 + k2) / (n1 + n2)
    se = math.sqrt(pooled * (1 - pooled) * (1 / n1 + 1 / n2))
    if se == 0:
        return 1.0
    z = abs(k1 / n1 - k2 / n2) / se
    return math.erfc(z / math.sqrt(2))


def rounds_needed(delta: float, p: float = 0.5) -> int:
    """Rounds for 80% power to see a win rate of p+delta against p at the 5% level (one arm vs a coin)."""
    za, zb = 1.96, 0.84
    q = p + delta
    return math.ceil(((za * math.sqrt(p * (1 - p)) + zb * math.sqrt(q * (1 - q))) / delta) ** 2)


def describe(wins: int, losses: int, name: str) -> str:
    n = wins + losses
    if not n:
        return f"{name}: no decided rounds"
    lo, hi = wilson(wins, n)
    return (f"{name}: {wins}/{n} decided rounds = {wins / n:.1%} (95% CI {lo:.1%}–{hi:.1%}), "
            f"p = {binom_two_sided(wins, n):.3f} against a coin")
