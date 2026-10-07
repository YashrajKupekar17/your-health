"""Small-sample statistics for eval reports. Honest error bars, no dependencies.

Why these two: with 3 trials per scenario, normal-approximation error bars are badly wrong, and a
suite-wide "it got better" claim needs a paired test over scenarios, not a difference of averages.
"""

from __future__ import annotations

from math import comb, sqrt


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson score interval for k successes in n trials (stays sensible at n=3)."""
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return (max(0.0, centre - half), min(1.0, centre + half))


def sign_test(improved: int, worsened: int) -> float:
    """Exact two-sided sign test (McNemar on scenarios that changed). Unchanged scenarios carry no signal."""
    n = improved + worsened
    if n == 0:
        return 1.0
    tail = sum(comb(n, i) for i in range(min(improved, worsened) + 1)) / 2**n
    return min(1.0, 2 * tail)


def fmt_interval(k: int, n: int) -> str:
    lo, hi = wilson(k, n)
    return f"{k}/{n} [{lo:.2f}–{hi:.2f}]"
