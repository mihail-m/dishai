"""Clean Air Index engine (vectorised over directed edges).

    PM(e,t)     = B(t) + R(e,t)                      (learned model: its prediction + R; no assumed traffic term anywhere)
    cost_burden = (1 - g) * (1 - air_score(PM)/100) + g * H(e)       CAI = round(100 * (1 - cost_burden))

B = CAMS PM2.5 x sensor bias; R = residual of the live sensors. Traffic is deliberately not modelled: the road-class weights and the
rush-hour curve it needed were assumptions, and measured traffic (backend/traffic.py) has no history aligned with the sensor record to
calibrate an effect against. It is shown as observed data on the map and in the street popover instead.

When data/pm_model.joblib exists (python -m backend.ml.train) B is replaced by a gradient-boosted model trained on a year of
citizen-sensor history (backend/ml); the live-sensor residual R and everything downstream (CAI, heat, routing) are unchanged.
"""
from dataclasses import dataclass
from datetime import datetime

import numpy as np
from scipy.spatial import cKDTree

from . import config as C
from .features import Features
from .graph import Net, to_xy
from ..ml import calibration as calibration_mod
from ..ml.pm_model import MAX_LEAD_H, STATIC_COLS, PmModel, hour_vector, now_vector, static_matrix
from . import krige


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


def air_score(pm) -> np.ndarray:
    """PM2.5 (ug/m3) -> 100 (clean) .. 0 (worst), piecewise linear through the health anchors in config (WHO 5/15, EU 25)."""
    return np.interp(pm, C.AIR_PM, C.AIR_SCORE)


def route_burden(pm: np.ndarray) -> np.ndarray:
    """Routing cost term: PM relative to the city's own spread (p5..p95 over all streets), floored and capped (see config)."""
    lo, hi = np.percentile(pm, [5, 95])
    return np.clip((pm - lo) / max(hi - lo, C.ROUTE_MIN_SPREAD), 0.0, C.ROUTE_REL_MAX)


def shield(G: np.ndarray) -> np.ndarray:
    return 1.0 - C.GREEN_CAP * G


def predict_pm(B: float, R) -> np.ndarray:
    """Fallback without the learned model: the sensor-corrected background only. There is no traffic term: road-class weights and a
    rush-hour curve were assumptions that did not improve the estimate on held-out sensors, so they were removed."""
    return B + R


def ctx_month(time: str) -> int | None:
    try:
        return int(time[5:7])
    except (ValueError, TypeError):
        return None


class CaiModel:
    """Everything needed to score any edge for any forecast hour, from one sensor snapshot."""

    def __init__(self, net: Net, feats: Features, hours: list[dict], now_idx: int, sensors: list[dict], ml: "PmModel | None | bool" = True):
        self.net, self.feats = net, feats
        self.hours = hours
        self.ml: PmModel | None = (PmModel.load() if C.USE_ML else None) if ml is True else (ml or None)
        self.static = static_matrix(feats) if self.ml else None
        self.spatial = self._spatial_params() if self.ml and C.SPATIAL_METHOD == "krige" else None
        self.kappa = self.kvar = None  # kriged log1p residual and its field variance per directed edge (krige path)
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
            if self.ml:
                base = self._ml_pm(static_matrix(feats, sf), now_idx)
            else:
                base = np.full(len(sensors), self.B_now, dtype="float32")
            if self.spatial:
                self._krige(sensors, pm, base)
                self.R_now = np.zeros(net.n_edges, dtype="float32")
            else:
                self.resid = (pm - base).astype("float32")
                self.R_now = idw_residual(net.mid_xy, self.sens_xy, self.resid)
        else:
            self.R_now = np.zeros(net.n_edges, dtype="float32")
            if self.spatial:
                self.kappa, self.kvar = np.zeros(net.n_edges, "float32"), np.full(net.n_edges, self.spatial.psill, "float32")
        if self.ml:
            self.B_now = self._ml_sub(now_idx)

    @staticmethod
    def _spatial_params() -> "krige.SpatialParams | None":
        """The fitted spatial file, if it was made against the calibration now in use (offsets are calibration-dependent)."""
        p = krige.get_params()
        cal = calibration_mod.get("pm25")
        if p is None or (cal is not None and p.meta.get("calibration_coef") not in (None, cal.coef)):
            return None
        return p

    def _krige(self, sensors: list[dict], pm: np.ndarray, base: np.ndarray) -> None:
        """Kriging weights for every undirected street segment (once per sensor snapshot), then the residual field at the snapshot hour.
        Sensors' persistent offsets are removed; those without history count as offset 0 with the full offset variance as noise."""
        p, net = self.spatial, self.net
        ids = [s.get("id") for s in sensors]
        e = np.log1p(pm) - np.log1p(base.astype(float)) - krige.sensor_offsets(ids, p)
        rep = net.und_rep
        W, kv = krige.kriging_weights(net.mid_xy[rep], self.sens_xy, krige.sensor_noise(ids, p), p)
        self.kappa = (W @ e)[net.e_und].astype("float32")
        self.kvar = kv[net.e_und].astype("float32")

    def _ml_sub(self, i: int) -> float:
        return float(np.median(self._ml_pm(self.static[:: max(1, len(self.static) // 5000)], i)))

    def _ml_all(self, i: int) -> np.ndarray:
        return self._ml_pm(self.static, i)

    def _ml_pm(self, static: np.ndarray, i: int) -> np.ndarray:
        """ML prediction for the given static-feature rows at forecast hour i; lead = hours ahead of the live snapshot."""
        ctx = self.ctxs[i]
        now = self.ctxs[self.now_idx]
        hv = hour_vector(self.hours[i], hour=ctx.hour, weekend=ctx.weekend)
        nv = now_vector(min(max(0, i - self.now_idx), MAX_LEAD_H), self.sensor_median if self.sensor_median is not None else float("nan"),
                        now.cams if now.cams is not None else float("nan"))
        return self.ml.predict(static, hv, nv)

    def _background(self, ctx: HourCtx) -> float:
        if ctx.cams is not None:
            return ctx.cams * self.bias
        return self.sensor_median if self.sensor_median is not None else 10.0

    def R(self, i: int) -> np.ndarray:
        """Local correction in ug/m3 (street value minus the trend). IDW path: the sensor residual persists, scaled with the background
        level. Kriging path: derived from the hour's field (kriged log residual x its persistence at this lead)."""
        if self.spatial:
            h = self.hour(i)
            return (h["pm"] - h["base"]).astype("float32")
        if self.ml:
            bg = self._ml_sub(i)
        else:
            bg = self._background(self.ctxs[i])
        ratio = float(np.clip(bg / max(self.B_now, C.CAMS_FLOOR), 0.5, 2.0))
        return self.R_now * ratio

    def hour(self, i: int) -> dict:
        i = int(np.clip(i, 0, len(self.ctxs) - 1))
        if i in self._cache:
            return self._cache[i]
        ctx, f = self.ctxs[i], self.feats
        extra = {}
        if self.spatial:
            B = self._ml_sub(i)
            base = self._ml_all(i)
            rho = self.spatial.rho_at(max(0, i - self.now_idx))
            pm = np.maximum(np.expm1(np.log1p(base) + rho * self.kappa), 0.0).astype("float32")
            # spatial part of the uncertainty (log1p units); the city-level part is added where the level is known (alert, API)
            extra = {"base": base, "rho": rho, "sd_u": krige.prediction_sd(self.kvar, self.spatial, rho, ctx_month(ctx.time)).astype("float32")}
        elif self.ml:
            B = self._ml_sub(i)
            pm = np.maximum(self._ml_all(i) + self.R(i), 0.0).astype("float32")
        else:
            B = self._background(ctx)
            pm = np.maximum(predict_pm(B, self.R(i)), 0.0).astype("float32")
        air = 1.0 - air_score(pm) / 100.0
        cb = ((1 - ctx.g) * air + ctx.g * f.H).astype("float32")
        cai = np.round(100 * (1 - cb)).astype("uint8")
        # Routing burden is relative to the spatial spread of PM (see route_burden), so a clean, flat day still has a
        # cleanest route and a heavy episode does not flatten every street to the same cost.
        cr = ((1 - ctx.g) * route_burden(pm) + ctx.g * f.H).astype("float32")
        out = {"pm": pm, "cai": cai, "cb": cb, "cr": cr, "B": B, "ctx": ctx, **extra}
        if len(self._cache) > 12:
            self._cache.pop(next(iter(self._cache)))
        self._cache[i] = out
        return out

    def _ml_split(self, e: int, i: int) -> tuple[float, float, float]:
        """(background, terrain, greenery) contributions in ug/m3 by swapping this street's features in one group at a time into a
        typical street (the city median). Steps are sequential, so with the background they sum to the model's prediction for the street."""
        med = np.nanmedian(self.static, axis=0)
        row = self.static[e]
        idx = {c: k for k, c in enumerate(STATIC_COLS)}
        steps = [("elev", "rel_elev", "lst_anom"), ("G", "canopy", "ndvi")]
        cur = med.copy()
        prev = bg = float(self._ml_pm(cur[None, :], i)[0])
        out = [bg]
        for grp in steps:
            for c in grp:
                cur[idx[c]] = row[idx[c]]
            now = float(self._ml_pm(cur[None, :], i)[0])
            out.append(now - prev)
            prev = now
        return out[0], out[1], out[2]

    def explain(self, e: int, i: int) -> dict:
        """Decomposition for the 'Why is this red?' popover. PM terms sum exactly to PM."""
        h, f = self.hour(i), self.feats
        ctx = h["ctx"]
        if self.ml:
            bg, vent, green = self._ml_split(e, i)
        else:
            bg, vent, green = h["B"], 0.0, 0.0
        air = float(1.0 - air_score(h["pm"][e]) / 100.0)
        cai_air = round(100 * (1 - air))
        return {
            "edge": int(e), "time": ctx.time, "pm25": round(float(h["pm"][e]), 1), "cai": int(h["cai"][e]),
            "components_ugm3": {
                "background": round(bg, 1),
                "local_correction": round(float(self.R(i)[e]), 1),
                "ventilation": round(vent, 1),
                "greenery": round(green, 1),
            },
            "drivers": {
                "greenery": round(float(f.G[e]), 2), "heat_anomaly": round(float(f.H[e]), 2),
                "heat_weight": round(ctx.g, 2), "stagnation_score": round(ctx.stagnation, 1),
                # context only: OSM construction sites within ~150 m. Not in the PM2.5 model (no effect on held-out sensors).
                "construction_share": round(float(np.nan_to_num(f.construction[e])), 3),
            },
            "heat_points": int(h["cai"][e]) - cai_air,
            "cai_air_only": cai_air,
            "model": "ml" if self.ml else "physics",
            "spatial": "krige" if self.spatial else "idw",
            "note": "Learned from citizen-sensor history; indicative." if self.ml else "Background plus the local sensor correction; indicative.",
        }
