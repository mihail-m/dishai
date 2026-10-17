"""Clean Air Index engine (vectorised over directed edges).

    PM(e,t)     = B(t) + R(e,t) + T(e) * rush(t) * A(t) * shield(e)
    cost_burden = (1 - g) * clamp(PM/60) + g * H(e)       CAI = round(100 * (1 - cost_burden))

B = CAMS PM2.5 x sensor bias; R = IDW residual of sensors; A = 1 + S/100 with S the v1 stagnation score.
An indicative screening model with assumed weights, not a dispersion model.
"""
from dataclasses import dataclass
from datetime import datetime

import numpy as np
from scipy.spatial import cKDTree

from . import config as C
from .features import Features
from .graph import Net, to_xy


def rush_factor(hour: int, weekend: bool) -> float:
    return C.RUSH[hour % 24] * (C.WEEKEND_FACTOR if weekend else 1.0)


def bias_factor(sensor_pm25: np.ndarray, cams_now: float | None) -> float:
    """median(sensor PM2.5) / CAMS now, clamped. 1.0 when either side is missing."""
    if cams_now is None or len(sensor_pm25) == 0:
        return 1.0
    return float(np.clip(np.median(sensor_pm25) / max(cams_now, C.CAMS_FLOOR), C.BIAS_MIN, C.BIAS_MAX))


def idw_residual(pts_xy: np.ndarray, sens_xy: np.ndarray, resid: np.ndarray) -> np.ndarray:
    """Inverse-distance-squared mean of the k nearest sensor residuals within the radius, shrunk toward 0.

    R = sum(w r) / (sum(w) + w0) with w0 the weight of a sensor IDW_SHRINK_M away, so locations with no
    nearby sensor get ~0 instead of an extrapolated value.
    """
    n = len(resid)
    if n == 0:
        return np.zeros(len(pts_xy), dtype="float32")
    k = min(C.IDW_K, n)
    d, idx = cKDTree(sens_xy).query(pts_xy, k=k, distance_upper_bound=C.IDW_RADIUS_M)
    d = d.reshape(len(pts_xy), -1)
    idx = idx.reshape(len(pts_xy), -1)
    w = np.where(np.isfinite(d), 1.0 / np.maximum(d, 50.0) ** 2, 0.0)
    r = np.append(resid, 0.0)[np.where(idx >= n, n, idx)]
    return ((w * r).sum(1) / (w.sum(1) + 1.0 / C.IDW_SHRINK_M**2)).astype("float32")


@dataclass
class HourCtx:
    time: str
    cams: float | None
    stagnation: float
    apparent: float
    daylight: float
    hour: int
    weekend: bool
    rush_override: float | None = None  # e.g. daily-mean rush for 24 h validation

    @property
    def rush(self) -> float:
        return self.rush_override if self.rush_override is not None else rush_factor(self.hour, self.weekend)

    @property
    def amp(self) -> float:
        return 1.0 + C.VENT_GAIN * self.stagnation / 100.0

    @property
    def gate(self) -> float:
        return float(np.clip((self.apparent - C.HEAT_GATE_FROM_C) / C.HEAT_GATE_RANGE_C, 0, 1)) * self.daylight

    @property
    def g(self) -> float:
        return C.HEAT_MAX_SHARE * self.gate


def hour_contexts(hours: list[dict]) -> list[HourCtx]:
    out = []
    for h in hours:
        t = datetime.strptime(h["time"], "%Y-%m-%dT%H:%M")
        rad = h.get("radiation")
        daylight = float(np.clip(rad / 300.0, 0, 1)) if rad is not None else float(max(0.0, np.sin(np.pi * (t.hour - 6) / 13)) if 6 <= t.hour <= 19 else 0.0)
        out.append(HourCtx(
            time=h["time"], cams=h.get("pm25"), stagnation=float(h["score"]),
            apparent=float(h.get("apparent") if h.get("apparent") is not None else h["t2m"]),
            daylight=daylight, hour=t.hour, weekend=t.weekday() >= 5,
        ))
    return out


def shield(G: np.ndarray) -> np.ndarray:
    return 1.0 - C.GREEN_CAP * G


def predict_pm(T, G, ctx: HourCtx, B: float, R) -> np.ndarray:
    return B + R + C.TRAFFIC_GAIN * T * ctx.rush * ctx.amp * shield(G)


class CaiModel:
    """Everything needed to score any edge for any forecast hour, from one sensor snapshot."""

    def __init__(self, net: Net, feats: Features, hours: list[dict], now_idx: int, sensors: list[dict]):
        self.net, self.feats = net, feats
        self.ctxs = hour_contexts(hours)
        self.now_idx = now_idx
        self.sensors = sensors
        self._cache: dict[int, dict] = {}
        pm = np.array([s["pm25"] for s in sensors], dtype=float)
        if len(pm):
            pm = np.clip(pm, 0, 4 * np.median(pm) + 10)  # outliers should not drive the IDW field
        now = self.ctxs[now_idx]
        self.bias = bias_factor(pm, now.cams)
        self.sensor_median = float(np.median(pm)) if len(pm) else None
        self.B_now = self._background(now)
        if len(sensors):
            sx, sy = to_xy([s["lon"] for s in sensors], [s["lat"] for s in sensors])
            self.sens_xy = np.column_stack([sx, sy])
            sf = feats.at([s["lon"] for s in sensors], [s["lat"] for s in sensors])
            base = np.full(len(sensors), self.B_now, dtype="float32")
            if C.RESIDUAL_AGAINST == "model":
                base = predict_pm(sf["T"], sf["G"], now, self.B_now, 0.0)
            self.resid = (pm - base).astype("float32")
            self.R_now = idw_residual(net.mid_xy, self.sens_xy, self.resid)
        else:
            self.R_now = np.zeros(net.n_edges, dtype="float32")

    def _background(self, ctx: HourCtx) -> float:
        if ctx.cams is not None:
            return ctx.cams * self.bias
        return self.sensor_median if self.sensor_median is not None else 10.0

    def R(self, i: int) -> np.ndarray:
        """Sensor residual persists, scaled with the background level (sensor error scales with level)."""
        ratio = float(np.clip(self._background(self.ctxs[i]) / max(self.B_now, C.CAMS_FLOOR), 0.5, 2.0))
        return self.R_now * ratio

    def hour(self, i: int) -> dict:
        i = int(np.clip(i, 0, len(self.ctxs) - 1))
        if i in self._cache:
            return self._cache[i]
        ctx, f = self.ctxs[i], self.feats
        B = self._background(ctx)
        pm = np.maximum(predict_pm(f.T, f.G, ctx, B, self.R(i)), 0.0).astype("float32")
        air = np.clip(pm / C.PM_NORM, 0, 1)
        cb = ((1 - ctx.g) * air + ctx.g * f.H).astype("float32")
        cai = np.round(100 * (1 - cb)).astype("uint8")
        # Routing cost burden is NOT clamped: in a heavy episode the city-wide PM exceeds PM_NORM everywhere, which
        # would flatten every street to cost_burden 1 and make "cleanest" identical to "fastest".
        cr = ((1 - ctx.g) * (pm / C.PM_NORM) + ctx.g * f.H).astype("float32")
        out = {"pm": pm, "cai": cai, "cb": cb, "cr": cr, "B": B, "ctx": ctx}
        if len(self._cache) > 12:
            self._cache.pop(next(iter(self._cache)))
        self._cache[i] = out
        return out

    def explain(self, e: int, i: int) -> dict:
        """Decomposition for the 'Why is this red?' popover. PM terms sum exactly to PM."""
        h, f = self.hour(i), self.feats
        ctx = h["ctx"]
        raw = float(C.TRAFFIC_GAIN * f.T[e] * ctx.rush)
        vent = raw * (ctx.amp - 1.0)
        green = -raw * ctx.amp * C.GREEN_CAP * float(f.G[e])
        air = float(np.clip(h["pm"][e] / C.PM_NORM, 0, 1))
        cai_air = round(100 * (1 - air))
        return {
            "edge": int(e), "time": ctx.time, "pm25": round(float(h["pm"][e]), 1), "cai": int(h["cai"][e]),
            "components_ugm3": {
                "background": round(h["B"], 1),
                "local_correction": round(float(self.R(i)[e]), 1),
                "traffic": round(raw, 1),
                "ventilation": round(vent, 1),
                "greenery": round(green, 1),
            },
            "drivers": {
                "traffic_term": round(float(f.T[e]), 2), "rush": round(ctx.rush, 2), "amplifier": round(ctx.amp, 2),
                "greenery": round(float(f.G[e]), 2), "heat_anomaly": round(float(f.H[e]), 2),
                "heat_weight": round(ctx.g, 2), "stagnation_score": round(ctx.stagnation, 1),
            },
            "heat_points": int(h["cai"][e]) - cai_air,
            "cai_air_only": cai_air,
            "note": "Indicative screening model with assumed weights.",
        }
