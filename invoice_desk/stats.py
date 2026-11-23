"""Intervals for small samples (round 3, DECISIONS.md D72).

The Wilson score interval is the one recommended for small samples (Brown, Cai and DasGupta,
2001); the usual plus-or-minus formula gives nonsense near 0 and 1, which is exactly where
"0 wrongly approved" sits.
"""
from __future__ import annotations

from math import sqrt

Z95 = 1.959964


def wilson(k: int, n: int, z: float = Z95) -> tuple[float, float]:
    """The Wilson interval for k successes in n trials, as fractions between 0 and 1."""
    if n <= 0 or not 0 <= k <= n:
        raise ValueError(f"need 0 <= k <= n and n > 0, got k={k}, n={n}")
    p = k / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return max(0.0, centre - half) if k else 0.0, min(1.0, centre + half) if k < n else 1.0


def documents_needed(upper: float, z: float = Z95) -> int:
    """How many documents with no miss it takes for the Wilson upper bound to fall below `upper`."""
    n = 1
    while wilson(0, n, z)[1] >= upper:
        n += 1
    return n
