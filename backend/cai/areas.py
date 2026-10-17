"""Aggregate street-segment values into square area cells so clean and dirty (or cool and hot) areas show at a glance.

Each cell holds the length-weighted mean of its streets' value, the best and worst street in it, and how much street it
contains. Cells with almost no street (fields, forest) are dropped: there is nobody to breathe there.
"""
import numpy as np

from . import config as C
from .graph import Net, to_lonlat, to_xy


def area_cells(net: Net, value: np.ndarray, extra: dict[str, np.ndarray] | None = None, cell_m: float = C.AREA_CELL_M,
               min_street_m: float = C.AREA_MIN_STREET_M) -> dict:
    rep = net.und_rep
    x, y = net.mid_xy[rep, 0], net.mid_xy[rep, 1]
    w = net.e_len[rep].astype(float)
    v = value[rep].astype(float)
    ok = np.isfinite(v)
    x, y, w, v = x[ok], y[ok], w[ok], v[ok]
    x0, y0 = np.floor(x.min() / cell_m) * cell_m, np.floor(y.min() / cell_m) * cell_m
    ix, iy = ((x - x0) // cell_m).astype(np.int64), ((y - y0) // cell_m).astype(np.int64)
    key = iy * (ix.max() + 1) + ix
    uniq, inv = np.unique(key, return_inverse=True)
    length = np.bincount(inv, w)
    mean = np.bincount(inv, w * v) / np.maximum(length, 1e-9)
    order = np.argsort(inv, kind="stable")
    starts = np.concatenate([[0], np.cumsum(np.bincount(inv))[:-1]])
    hi = np.maximum.reduceat(v[order], starts)
    lo = np.minimum.reduceat(v[order], starts)
    n = np.bincount(inv)
    cx = x0 + (uniq % (ix.max() + 1) + 0.5) * cell_m
    cy = y0 + (uniq // (ix.max() + 1) + 0.5) * cell_m
    lon, lat = to_lonlat(cx, cy)
    keep = length >= min_street_m
    out = {
        "lon": np.round(lon[keep], 5).tolist(), "lat": np.round(lat[keep], 5).tolist(),
        "mean": np.round(mean[keep], 1).tolist(), "max": np.round(hi[keep], 1).tolist(), "min": np.round(lo[keep], 1).tolist(),
        "street_m": np.round(length[keep]).astype(int).tolist(), "n": n[keep].astype(int).tolist(),
    }
    for name, arr in (extra or {}).items():
        a = arr[rep][ok].astype(float)
        out[name] = np.round((np.bincount(inv, w * a) / np.maximum(length, 1e-9))[keep], 1).tolist()
    return out


def summarize(values: list[float]) -> dict:
    a = np.array(values, dtype=float)
    return {"median": round(float(np.median(a)), 1), "p10": round(float(np.percentile(a, 10)), 1),
            "p90": round(float(np.percentile(a, 90)), 1), "min": round(float(a.min()), 1), "max": round(float(a.max()), 1)}
