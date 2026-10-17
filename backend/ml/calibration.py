"""Sensor calibration: raw Sensor.Community reading -> reference-station scale (stage 0 of the unified PM model).

The citizen sensors read about half of the EEA reference station in Sofia, and the ratio depends on humidity (see
docs/PM_MODEL.md, "Phase 1"). The fitted mapping is a small log-linear model,

    log(1 + ref) = c0 + c1 * log(1 + raw) + c2 * RH + c3 * T            (terms are present only if they earned their place)

fitted by `python -m backend.ml.calibrate` against the hourly median of the sensors around the reference station. This module
only applies a saved calibration, so serving needs numpy and joblib, nothing else. If the file is missing or has another format,
`load()` returns None and callers keep the raw values and say so (the app never invents a calibration).

`sigma_log` is the residual spread of the calibrated median against the reference, in log units: the calibration's own error
bar, which the spatial stage adds to each sensor's nugget.
"""
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from ..cai.graph import DATA

PATH = DATA / "calibration.joblib"
FORMAT = 1  # bump when the saved layout changes; older files are then ignored
FEATURES = ("log_raw", "rh", "temp", "heating")  # columns a "linear" calibration can use, in this order
HEATING_MONTHS = (10, 11, 12, 1, 2, 3)


def design(raw, rh, temp, month, rh_fill: float, t_fill: float) -> np.ndarray:
    """(n, 4) feature matrix. Missing RH / temperature count as the training median (a neutral value, flagged by the caller)."""
    raw = np.maximum(np.asarray(raw, dtype=float), 0.0)
    n = raw.shape[0]

    def col(v, fill):
        a = np.full(n, fill, dtype=float) if v is None else np.broadcast_to(np.asarray(v, dtype=float), (n,)).copy()
        a[~np.isfinite(a)] = fill
        return a

    m = col(month, np.nan) if month is not None else np.full(n, np.nan)
    heating = np.isin(m, HEATING_MONTHS).astype(float)
    return np.column_stack([np.log1p(raw), col(rh, rh_fill) / 100.0, col(temp, t_fill) / 10.0, heating])


@dataclass
class Calibration:
    kind: str  # "ratio" (ref = k * raw) or "linear" (log-linear on the columns named in `terms`)
    coef: dict  # ratio: {"k"}; linear: {"const", <term>...}
    rh_fill: float = 65.0
    t_fill: float = 10.0
    sigma_log: float = 0.3
    clip: tuple = (0.7, 8.0)  # calibrated / raw is kept inside this; a sensor reading 0.1 must not be multiplied by 30
    meta: dict = field(default_factory=dict)

    @property
    def terms(self) -> list[str]:
        return [k for k in self.coef if k != "const" and k != "k"]

    def apply(self, raw, rh=None, temp=None, month=None) -> np.ndarray:
        """Calibrated PM (reference scale) for raw sensor values. `rh` (%), `temp` (deg C) and `month` may be scalars or arrays."""
        raw = np.atleast_1d(np.asarray(raw, dtype=float))
        if self.kind == "ratio":
            out = self.coef["k"] * raw
        else:
            X = design(raw, rh, temp, month, self.rh_fill, self.t_fill)
            z = self.coef["const"] + sum(self.coef[t] * X[:, FEATURES.index(t)] for t in self.terms)
            out = np.expm1(z)
        out = np.clip(out, self.clip[0] * np.maximum(raw, 0), self.clip[1] * np.maximum(raw, 0))
        return np.maximum(out, 0.0)

    def factor(self, raw, rh=None, temp=None, month=None) -> np.ndarray:
        raw = np.atleast_1d(np.asarray(raw, dtype=float))
        return self.apply(raw, rh, temp, month) / np.maximum(raw, 1e-6)

    @classmethod
    def load(cls, path: Path = PATH, pollutant: str = "pm25") -> "Calibration | None":
        if not path.exists():
            return None
        import joblib
        blob = joblib.load(path)
        if blob.get("format") != FORMAT or pollutant not in blob:
            return None
        return cls(**blob[pollutant])

    def to_dict(self) -> dict:
        return {"kind": self.kind, "coef": self.coef, "rh_fill": self.rh_fill, "t_fill": self.t_fill, "sigma_log": self.sigma_log,
                "clip": self.clip, "meta": self.meta}


def save(cals: dict[str, Calibration], path: Path = PATH) -> None:
    import joblib
    joblib.dump({"format": FORMAT, **{k: v.to_dict() for k, v in cals.items()}}, path, compress=3)


_cache: dict = {"mtime": None, "blob": {}}


def get(pollutant: str = "pm25", path: Path = PATH) -> Calibration | None:
    """Cached loader for the serving path; follows the file's modification time like alert.get_model."""
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None
    if _cache["mtime"] != mtime:
        _cache.update(mtime=mtime, blob={})
    if pollutant not in _cache["blob"]:
        _cache["blob"][pollutant] = Calibration.load(path, pollutant)
    return _cache["blob"][pollutant]
