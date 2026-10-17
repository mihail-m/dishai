"""Measured typical traffic by hour of day, set by backend/traffic.py from Google Routes (travel time of arterial corridors
at future departure times). Until a profile is loaded every function returns None and score.py keeps the hand-set curve."""
import numpy as np

from . import config as C

_P: dict | None = None  # {"wk": [24], "we": [24]}  traffic intensity 0..1 scaled like config.RUSH
_REF = 1.0


def set_profile(p: dict | None) -> None:
    global _P, _REF
    _P = p
    _REF = float(np.mean(p["wk"] + p["we"])) if p else 1.0


def loaded() -> bool:
    return _P is not None


def rush(hour: int, weekend: bool) -> float | None:
    if _P is None:
        return None
    return float(_P["we" if weekend else "wk"][hour % 24])


def rel(hour: int, weekend: bool) -> float:
    """Typical intensity of this hour relative to the all-week mean; 1.0 when no profile is loaded."""
    r = rush(hour, weekend)
    return 1.0 if r is None else float(np.clip(r / _REF, 0.4, 1.8))


def from_travel_times(wk: list[float], we: list[float]) -> dict:
    """wk/we: 24 values of the corridor-mean travel-time index (>= 1). The quietest hour of the week maps to
    C.RUSH_FLOOR and the busiest to 1.0, so the shape is measured and only the floor is an assumption."""
    lo, hi = min(min(wk), min(we)), max(max(wk), max(we))
    span = max(hi - lo, 1e-6)

    def scale(v):
        return [round(C.RUSH_FLOOR + (1.0 - C.RUSH_FLOOR) * (x - lo) / span, 3) for x in v]

    return {"wk": scale(wk), "we": scale(we)}
