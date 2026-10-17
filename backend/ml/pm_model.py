"""Learned PM2.5 model, in two parts, because the data say these are two different problems.

  level   city-wide PM2.5 at an hour = CAMS forecast x exp(f), with f from gradient-boosted trees on weather, inversion
          strength and a live-sensor nowcast (the city median of the sensors at forecast issue time and the lead in
          hours). Predicting a RATIO to CAMS matters: trees cannot extrapolate beyond the PM levels they were trained
          on (the history has no heavy inversion episode), but a ratio scales with the forecast when CAMS says 100.
  spatial how much dirtier/cleaner than the city a street is: log-deviation = (a + a_s * S/100) * z(log traffic)
          + (b + b_s * S/100) * z(terrain height), Huber-fitted on citizen sensors. Held-out sensors show only a weak
          street signal (66 % of between-sensor variance is a fixed per-sensor offset: calibration/siting), so this
          part is deliberately tiny and linear; anything richer memorised individual sensors in cross-validation.

PM(street, hour) = level(hour) * exp(spatial(street, stagnation)). The same feature builders serve training
(backend/ml/train.py) and serving (CaiModel). Live sensors then correct the value locally (see score.py).
"""
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from ..cai.graph import DATA

MODEL_PATH = DATA / "pm_model.joblib"

STATIC_COLS = ["T", "G", "canopy", "ndvi", "lst_anom", "elev", "rel_elev"]
HOUR_COLS = ["cams25", "cams10", "blh", "wind10", "wind925", "t2m", "inversion", "cloud", "rh", "radiation",
             "stagnation", "hour", "weekend"]
NOW_COLS = ["lead", "city_med", "cams_issue", "city_ratio"]
LEVEL_FEATURES = HOUR_COLS + NOW_COLS
FEATURES = STATIC_COLS + LEVEL_FEATURES  # layout of the training table
SPATIAL_COLS = ["T", "elev"]  # the only street features that survived held-out-sensor testing
MAX_LEAD_H = 47
FORMAT = 4  # bump when the saved layout changes; older model files are then ignored (3: targets on the calibrated scale, 4: quantile level)
LEAD_EDGES = (5, 23)  # lead buckets for the conformal interval widening: 0-5 h, 6-23 h, 24-47 h
LEVEL_SPLIT = 15.0  # ... times two level classes: p50 below / at-or-above this (ug/m3)
N_BUCKETS = 6
Z80 = 1.2815515655446004
DEV_CLIP = 0.7  # |log street deviation| cap (about x2 / x0.5)
S_IDX = HOUR_COLS.index("stagnation")


def static_matrix(feats, sf: dict | None = None) -> np.ndarray:
    """(n, len(STATIC_COLS)) from a Features object (all edges) or from `feats.at(...)` output `sf` (points)."""
    d = sf if sf is not None else {"T": feats.T, "G": feats.G, "canopy": feats.canopy, "ndvi": feats.ndvi, "lst": feats.lst,
                                  "elev": feats.elev, "rel_elev": feats.rel_elev}
    lst_anom = d["lst"] - (feats.median_lst if feats.median_lst is not None else np.nan)
    cols = {**d, "lst_anom": lst_anom}
    return np.column_stack([np.asarray(cols[c], dtype="float32") for c in STATIC_COLS])


def hour_vector(h: dict, *, hour: int, weekend: bool) -> list[float]:
    """HOUR_COLS for one forecast hour, from the dict build_forecast() produces (training builds the same dict shape)."""
    nan = float("nan")

    def g(k):
        v = h.get(k)
        return nan if v is None else float(v)

    t2m = g("t2m")
    vals = {"cams25": g("pm25"), "cams10": g("pm10"), "blh": g("blh"), "wind10": g("wind10m"), "wind925": g("wind925"), "t2m": t2m, "inversion": g("t925") - t2m,
            "cloud": g("cloud"), "rh": g("rh"), "radiation": g("radiation"), "stagnation": g("score"),
            "hour": float(hour), "weekend": float(weekend)}
    return [vals[c] for c in HOUR_COLS]


def now_vector(lead: float, city_med: float, cams_issue: float) -> list[float]:
    ratio = city_med / max(cams_issue, 2.0) if np.isfinite(cams_issue) and np.isfinite(city_med) else float("nan")
    return [float(lead), city_med, cams_issue, ratio]


def anchor(cams: float, city_med: float):
    """What the level ratio multiplies: the CAMS forecast for the hour; the live city median if CAMS is missing; else 10."""
    for v in (cams, city_med):
        if v is not None and np.isfinite(v):
            return v
    return 10.0


def spatial_design(static: np.ndarray, stagnation: float, mean: np.ndarray, std: np.ndarray) -> np.ndarray:
    """(n, 4): z(log1p T), z(elev), and both times S/100 (S = stagnation score; NaN/missing terrain counts as typical)."""
    cols = [STATIC_COLS.index(c) for c in SPATIAL_COLS]
    raw = static[:, cols].astype("float64").copy()
    raw[:, 0] = np.log1p(np.maximum(raw[:, 0], 0))
    z = np.nan_to_num((raw - mean) / std)
    sf = (stagnation / 100.0) if np.isfinite(stagnation) else 0.0
    return np.column_stack([z, z * sf])


def interval_bucket(lead, level50) -> np.ndarray:
    """Which conformal correction applies: lead bucket (0-5, 6-23, 24+ h) x level class (below / above LEVEL_SPLIT)."""
    lb = np.digitize(np.asarray(lead, dtype=float), LEAD_EDGES, right=True)
    return 2 * lb + (np.asarray(level50) >= LEVEL_SPLIT).astype(int)


@dataclass
class PmModel:
    """Level estimators + spatial coefficients + the metadata the API shows next to them.

    `level` is the median (p50) model of the log ratio to the anchor. `q_lo`/`q_hi` are the p10/p90 models; `conf_lo`/`conf_hi` widen
    them per bucket so that, on out-of-fold hours, 10 % of readings fall below the p10 and 10 % above the p90 (split-conformal)."""
    level: object
    spatial: dict  # mean, std (of SPATIAL_COLS over sensors), coef (4)
    meta: dict = field(default_factory=dict)
    q_lo: object = None
    q_hi: object = None
    conf_lo: np.ndarray = None  # (N_BUCKETS,) log-ratio units
    conf_hi: np.ndarray = None
    corr: float = 0.9  # correlation of the level error between hours of one window (back-test); used by the alert probability

    def level_quantiles(self, hour_vecs, now_vecs) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """(u_lo, u_50, u_hi): log1p of the city PM2.5 at p10 / p50 / p90 for each (hour, nowcast) row. Without the quantile models
        the interval collapses to the median (callers then treat the level as exact)."""
        hv, nv = np.atleast_2d(np.asarray(hour_vecs, dtype=float)), np.atleast_2d(np.asarray(now_vecs, dtype=float))
        X = np.hstack([hv, nv]).astype("float32")
        base = np.log1p([anchor(h[0], n[1]) for h, n in zip(hv, nv)])
        f50 = self.level.predict(X)
        u50 = base + f50
        if self.q_lo is None or self.q_hi is None or self.conf_lo is None:
            return u50, u50, u50
        b = interval_bucket(nv[:, 0], np.expm1(u50))
        lo, hi = base + self.q_lo.predict(X) - self.conf_lo[b], base + self.q_hi.predict(X) + self.conf_hi[b]
        return np.minimum(lo, u50), u50, np.maximum(hi, u50)

    def level_pm(self, hour_vec: list[float], now_vec: list[float]) -> float:
        x = np.asarray([hour_vec + now_vec], dtype="float32")
        return float(np.expm1(np.log1p(anchor(hour_vec[0], now_vec[1])) + self.level.predict(x)[0]).clip(0))

    def spatial_dev(self, static: np.ndarray, stagnation: float) -> np.ndarray:
        d = spatial_design(static, stagnation, np.asarray(self.spatial["mean"]), np.asarray(self.spatial["std"]))
        return np.clip(d @ np.asarray(self.spatial["coef"]), -DEV_CLIP, DEV_CLIP)

    def predict(self, static: np.ndarray, hour_vec: list[float], now_vec: list[float]) -> np.ndarray:
        """PM2.5 (ug/m3) for every row of `static` at one hour. The street deviation is a log1p difference (that is how it is fitted),
        so it is applied in log1p space: the kriged sensor residuals (backend/cai/krige.py) live in the same space."""
        lvl = self.level_pm(hour_vec, now_vec)
        return np.expm1(np.log1p(lvl) + self.spatial_dev(static, hour_vec[S_IDX])).clip(0).astype("float32")

    @classmethod
    def load(cls, path: Path = MODEL_PATH) -> "PmModel | None":
        if not path.exists():
            return None
        import joblib
        blob = joblib.load(path)
        if blob.get("format") != FORMAT or blob.get("features") != LEVEL_FEATURES:
            return None  # trained against another layout; fall back to the physics model
        meta = blob["meta"]
        if meta.get("calibrated"):
            # The model's inputs (live city median) and targets are on the calibrated scale. If the calibration is missing or
            # has changed since training, its numbers would be silently wrong by a factor of ~2, so do not use it.
            from . import calibration
            cal = calibration.get("pm25")
            if cal is None or cal.coef != meta.get("calibration_coef"):
                return None
        return cls(blob["level"], blob["spatial"], meta, blob.get("q_lo"), blob.get("q_hi"), blob.get("conf_lo"), blob.get("conf_hi"),
                   float(blob.get("corr", 0.9)))

    def save(self, path: Path = MODEL_PATH) -> None:
        import joblib
        joblib.dump({"format": FORMAT, "level": self.level, "spatial": self.spatial, "meta": self.meta, "features": LEVEL_FEATURES,
                     "q_lo": self.q_lo, "q_hi": self.q_hi, "conf_lo": self.conf_lo, "conf_hi": self.conf_hi, "corr": self.corr}, path, compress=3)
