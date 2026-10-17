"""Regression kriging of the sensor residual field, with a per-sensor error term (stage 2 of the unified PM model).

The learned model gives every street a trend (level x traffic/terrain). The sensors say how far each reading is from that trend; this
module turns those residuals into a smooth correction and an honest uncertainty. All of it lives in log1p space, where the residuals
are close to Gaussian and a multiplicative correction scales with the level:

    PM(e, t) = expm1( log1p(trend(e, t)) + rho(lead) * kappa(e) )          kappa(e) = sum_j w_ej * resid_j
    resid_j  = log1p(sensor_j) - log1p(trend_j) - offset_j                  the sensor's persistent offset is REMOVED here

Simple kriging with an exponential covariance C(h) = psill * exp(-h / range) for the spatial part and a diagonal noise term
(nugget + the sensor's remaining offset uncertainty). The weights depend only on where the sensors are, so for one sensor snapshot
they are computed once (edges x sensors, k nearest, sparse) and each forecast hour is a sparse matrix-vector product.

What an offset means (decided and documented in docs/PM_MODEL.md): a sensor's persistent deviation from the city median, after the
trend, is a mix of calibration error and real local pollution and cannot be separated without co-location. When a sensor is used to
estimate OTHER places the offset is removed (otherwise one over-reading unit paints its neighbours red); at the sensor's own
location both numbers are reported. Sensors with no history get offset 0 and the full offset variance tau2 as extra noise.
"""
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from scipy.sparse import csr_matrix
from scipy.spatial import cKDTree

from .graph import DATA

PATH = DATA / "spatial.joblib"
FORMAT = 1
K_NEIGHBOURS = 24  # cross-validated 6/12/24/48: error and ranking improve up to ~24 and are flat after (the structure is city-scale)
CUTOFF_RANGES = 5.0  # neighbours farther than this many ranges carry no weight
Z80 = 1.2815515655446004  # standard normal quantile for the 80 % interval
HEATING_MONTHS = (10, 11, 12, 1, 2, 3)


@dataclass
class SpatialParams:
    nugget: float  # variance of the unstructured part of a reading (measurement + micro-scale), log1p units
    psill: float  # variance of the spatially correlated part
    range_m: float  # exponential scale a: C(h) = psill * exp(-h / a), so the practical range is about 3a
    tau2: float  # variance of the persistent per-sensor offsets across sensors
    offsets: dict = field(default_factory=dict)  # sensor id -> shrunk offset (log1p units)
    offset_var: dict = field(default_factory=dict)  # sensor id -> posterior variance of that offset
    rho: dict = field(default_factory=dict)  # lead hours (int) -> multiplier on the kriged residual; interpolated, flat beyond the ends
    sd_scale: dict = field(default_factory=lambda: {"heating": 1.0, "summer": 1.0})  # multiplies the SD so 80 % intervals hold 80 % per season
    meta: dict = field(default_factory=dict)

    def rho_at(self, lead: float) -> float:
        if not self.rho:
            return 1.0
        xs = sorted(self.rho)
        return float(np.interp(lead, xs, [self.rho[x] for x in xs]))

    def scale_for(self, month: int | None) -> float:
        if month is None:
            return max(self.sd_scale.values(), default=1.0)
        return float(self.sd_scale["heating" if month in HEATING_MONTHS else "summer"])

    def to_dict(self) -> dict:
        return {"nugget": self.nugget, "psill": self.psill, "range_m": self.range_m, "tau2": self.tau2, "offsets": self.offsets,
                "offset_var": self.offset_var, "rho": self.rho, "sd_scale": self.sd_scale, "meta": self.meta}

    def save(self, path: Path = PATH) -> None:
        import joblib
        joblib.dump({"format": FORMAT, **self.to_dict()}, path, compress=3)

    @classmethod
    def load(cls, path: Path = PATH) -> "SpatialParams | None":
        if not path.exists():
            return None
        import joblib
        blob = joblib.load(path)
        if blob.pop("format", None) != FORMAT:
            return None
        return cls(**blob)


_cache: dict = {"mtime": None, "p": None}


def get_params(path: Path = PATH) -> SpatialParams | None:
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None
    if _cache["mtime"] != mtime:
        _cache.update(mtime=mtime, p=SpatialParams.load(path))
    return _cache["p"]


def sensor_noise(ids, p: SpatialParams) -> np.ndarray:
    """Diagonal noise per sensor: unstructured variance + what is still unknown about its offset (all of tau2 for a new sensor)."""
    return np.array([p.nugget + p.offset_var.get(i, p.tau2) for i in ids], dtype=float)


def sensor_offsets(ids, p: SpatialParams) -> np.ndarray:
    return np.array([p.offsets.get(i, 0.0) for i in ids], dtype=float)


def kriging_weights(pts_xy: np.ndarray, sens_xy: np.ndarray, noise: np.ndarray, p: SpatialParams, k: int = K_NEIGHBOURS,
                    exclude: np.ndarray | None = None, chunk: int = 20_000) -> tuple[csr_matrix, np.ndarray]:
    """(W, kvar): sparse (n_pts x n_sens) simple-kriging weights from the k nearest sensors, and the kriging variance of the spatial
    field at each point (psill when no sensor is near). `exclude[i]` = sensor index to leave out for point i (leave-one-out)."""
    n, m = len(pts_xy), len(sens_xy)
    if m == 0 or p.psill <= 0:
        return csr_matrix((n, m)), np.full(n, max(p.psill, 0.0))
    kk = min(k + (1 if exclude is not None else 0), m)
    tree = cKDTree(sens_xy)
    rows, cols, vals = [], [], []
    kvar = np.empty(n)
    a, ps = p.range_m, p.psill
    for s in range(0, n, chunk):
        pts = pts_xy[s : s + chunk]
        d, idx = tree.query(pts, k=kk)
        d, idx = d.reshape(len(pts), -1), idx.reshape(len(pts), -1)
        if exclude is not None:  # drop the point's own sensor from its neighbours
            own = idx == exclude[s : s + chunk, None]
            order = np.argsort(own, axis=1, kind="stable")[:, : kk - 1]  # non-own columns first, in distance order
            d, idx = np.take_along_axis(d, order, 1), np.take_along_axis(idx, order, 1)
        near = d <= CUTOFF_RANGES * a
        c0 = np.where(near, ps * np.exp(-d / a), 0.0)
        xy = sens_xy[idx]  # (n, k, 2)
        dd = np.linalg.norm(xy[:, :, None, :] - xy[:, None, :, :], axis=-1)
        S = ps * np.exp(-dd / a) + np.einsum("nk,kl->nkl", noise[idx], np.eye(idx.shape[1]))
        w = np.linalg.solve(S, c0[:, :, None])[:, :, 0]
        w = np.where(near, w, 0.0)
        kvar[s : s + chunk] = np.maximum(ps - np.einsum("nk,nk->n", w, c0), 0.0)
        rows.append(np.repeat(np.arange(s, s + len(pts)), idx.shape[1]))
        cols.append(idx.ravel())
        vals.append(w.ravel())
    return csr_matrix((np.concatenate(vals), (np.concatenate(rows), np.concatenate(cols))), shape=(n, m)), kvar


def prediction_sd(kvar: np.ndarray, p: SpatialParams, rho: float = 1.0, month: int | None = None) -> np.ndarray:
    """SD (log1p units) of a reading on this street, spatial part only: the field's kriging variance, the unstructured part and the
    unknown per-sensor offset, times the per-season factor that makes the 80 % intervals hold 80 %. The correction decays with the
    lead (rho), so the variance it explains shrinks by rho^2."""
    var = p.nugget + p.tau2 + p.psill - rho**2 * (p.psill - kvar)
    return p.scale_for(month) * np.sqrt(np.maximum(var, 1e-12))
