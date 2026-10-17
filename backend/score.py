"""Stagnation score v0.

An indicative heuristic, NOT a validated model. All tunables live here.
"""

# Weights (must sum to 1.0)
W_BLH = 0.40
W_WIND = 0.30
W_INV = 0.30

# Term shapes
BLH_BAD_M = 800.0  # boundary layer height at/above this contributes 0
BLH_RANGE_M = 700.0  # ...and at/below (800-700)=100 m contributes the full 1.0
WIND_CALM_KMH = 12.0  # 10 m wind at/above this contributes 0
INV_OFFSET_C = 2.0  # t925 - t2m + offset ...
INV_RANGE_C = 8.0  # ... divided by this

# Labels
WATCH_FROM = 35.0
WARNING_FROM = 60.0


def clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def stagnation_score(blh: float, wind10m_kmh: float, t2m: float, t925: float) -> float:
    blh_term = clamp((BLH_BAD_M - blh) / BLH_RANGE_M)
    wind_term = clamp((WIND_CALM_KMH - wind10m_kmh) / WIND_CALM_KMH)
    inv_term = clamp((t925 - t2m + INV_OFFSET_C) / INV_RANGE_C)
    return 100.0 * (W_BLH * blh_term + W_WIND * wind_term + W_INV * inv_term)


def label_for(score: float) -> str:
    if score >= WARNING_FROM:
        return "warning"
    if score >= WATCH_FROM:
        return "watch"
    return "good"
