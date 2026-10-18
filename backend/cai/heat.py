"""Hourly local temperature for every street: the city forecast plus the learned place offset (backend/ml/heat_field.py).

Replaces the static Landsat snapshot. Without data/heat_field.joblib the offset is zero everywhere and the response says so (`model: false`).
Heat stress bands are the ones the advice cards already use (backend/advice.py HEAT_BANDS), applied to the feels-like temperature.
"""
import joblib
import numpy as np

from ..advice import HEAT_BANDS, HEAT_LEVELS
from ..ml import heat_field as HF
from .graph import Net

_cache: dict[int, tuple[Net, "HeatField"]] = {}


class HeatField:
    def __init__(self, net: Net):
        try:
            self.model = joblib.load(HF.MODEL_PATH)
        except (OSError, ValueError):
            self.model = None
        self.static = HF.static_for(net.e_mid_lon, net.e_mid_lat) if self.model is not None else None

    @property
    def available(self) -> bool:
        return self.model is not None

    def offsets(self, hour: dict) -> np.ndarray | None:
        """Degrees warmer (+) or cooler (-) than the city at each directed edge, for one forecast hour (dict from build_forecast)."""
        if self.model is None:
            return None
        return HF.predict_offset(self.model, self.static, hour.get("radiation"), hour.get("cloud"), hour.get("wind10m"))

    def meta(self) -> dict:
        m = self.model or {}
        return {"sensors": m.get("sensors"), "sensor_hours": m.get("sensor_hours"), "eval": m.get("eval")}


def get(net: Net) -> HeatField:
    hit = _cache.get(id(net))
    if hit is None or hit[0] is not net:
        hit = _cache[id(net)] = (net, HeatField(net))
    return hit[1]


def level_for(feels: float) -> str:
    return HEAT_LEVELS[sum(feels >= b for b in HEAT_BANDS)]


# absolute colour scale: white at 18 C, full blue at -2 C and full red at 38 C (the scale is symmetric around the centre so the
# diverging heat palette can be reused; the centre and half-width are returned to the client)
ABS_CENTRE, ABS_SPAN = 18.0, 20.0
