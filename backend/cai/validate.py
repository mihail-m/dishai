"""Leave-one-out validation of the CAI's PM2.5 against outdoor citizen sensors (a single snapshot: the historical, time-blocked
evaluation of the spatial stage is in backend/ml/spatial_fit.py).

For each sensor: rebuild the model without it (bias, IDW residual) and predict PM2.5 at its location, then
compare with what it measured. Reports Spearman rank correlation and median absolute error for the full
model and for simpler baselines, so you can see whether each ingredient earns its place.
A measured weak model is better than an unmeasured strong one.
"""
import numpy as np
from scipy.stats import spearmanr

from . import config as C
from .features import Features
from .graph import to_xy
from .score import HourCtx, bias_factor, idw_residual, predict_pm, shield


def _rho(a, b) -> float | None:
    if np.ptp(a) == 0 or np.ptp(b) == 0:
        return None
    r = spearmanr(a, b).statistic
    return None if np.isnan(r) else round(float(r), 3)


def leave_one_out(feats: Features, sensors: list[dict], ctx: HourCtx, mode: str | None = None, gain: float | None = None) -> dict:
    mode = mode or C.RESIDUAL_AGAINST
    gain = C.TRAFFIC_GAIN if gain is None else gain
    n = len(sensors)
    if n < 10:
        return {"n": n, "error": "too few sensors"}
    lon = np.array([s["lon"] for s in sensors])
    lat = np.array([s["lat"] for s in sensors])
    obs = np.array([s["pm25"] for s in sensors], dtype=float)
    obs_c = np.clip(obs, 0, 4 * np.median(obs) + 10)
    x, y = to_xy(lon, lat)
    xy = np.column_stack([x, y])
    sf = feats.at(lon, lat)
    local = gain * sf["T"] * ctx.rush * ctx.amp * shield(sf["G"])  # traffic term at each sensor

    pred_full = np.zeros(n)
    pred_noR = np.zeros(n)
    pred_bg = np.zeros(n)
    for i in range(n):
        m = np.arange(n) != i
        b = bias_factor(obs_c[m], ctx.cams)
        B = ctx.cams * b if ctx.cams is not None else float(np.median(obs_c[m]))
        base = B + local[m] if mode == "model" else np.full(m.sum(), B)
        R = idw_residual(xy[i : i + 1], xy[m], obs_c[m] - base)[0]
        pred_bg[i], pred_noR[i], pred_full[i] = B, B + local[i], B + local[i] + R

    def mae(p):
        return round(float(np.median(np.abs(p - obs))), 2)

    rho_full, rho_noR = _rho(pred_full, obs), _rho(pred_noR, obs)
    verdict = "weak" if (rho_full or 0) < 0.3 else "moderate" if rho_full < 0.6 else "reasonable"
    return {
        "n": n, "residual_mode": mode, "traffic_gain": gain, "hour": ctx.time,
        "spearman": rho_full, "median_abs_error": mae(pred_full),
        "baselines": {
            "no_sensor_residual": {"spearman": rho_noR, "median_abs_error": mae(pred_noR)},
            "background_only": {"spearman": None, "median_abs_error": mae(pred_bg), "note": "one city-wide value: rank correlation undefined"},
            "constant_median": {"median_abs_error": round(float(np.median(np.abs(np.median(obs) - obs))), 2)},
        },
        "observed": {"median": round(float(np.median(obs)), 1), "p10": round(float(np.percentile(obs, 10)), 1), "p90": round(float(np.percentile(obs, 90)), 1)},
        "verdict": verdict,
        "caveat": "Single snapshot of low-cost sensors (they drift and read high in humid air); indicative only.",
    }


def leave_one_out_ml(model) -> dict | None:
    """Same leave-one-out on the live snapshot for the learned model: ML prediction at each sensor, then the IDW
    residual of the other sensors. Also returns the offline evaluation stored with the trained model."""
    if not getattr(model, "ml", None):
        return None
    sensors = model.sensors
    n = len(sensors)
    m = model.ml.meta
    meta = {"trained": m.get("trained"), "offline_eval": m.get("eval_unseen_sensors_future_days"),
            "episodes": (m.get("eval_unseen_sensors") or {}).get("episodes"), "span": m.get("span"), "sensors": m.get("sensors")}
    if n < 10:
        return {"n": n, "error": "too few sensors", **meta}
    lon = np.array([s["lon"] for s in sensors])
    lat = np.array([s["lat"] for s in sensors])
    obs = np.array([s["pm25"] for s in sensors], dtype=float)
    obs_c = np.clip(obs, 0, 4 * np.median(obs) + 10)
    x, y = to_xy(lon, lat)
    xy = np.column_stack([x, y])
    from ..ml.pm_model import static_matrix
    base = model._ml_pm(static_matrix(model.feats, model.feats.at(lon, lat)), model.now_idx).astype(float)
    idw = np.zeros(n)
    for i in range(n):
        m = np.arange(n) != i
        idw[i] = base[i] + idw_residual(xy[i : i + 1], xy[m], obs_c[m] - base[m])[0]

    def mae(p):
        return round(float(np.median(np.abs(p - obs))), 2)

    out = {"n": n, "ml_only": {"spearman": _rho(base, obs), "median_abs_error": mae(base)},
           "idw": {"spearman": _rho(idw, obs), "median_abs_error": mae(idw)}, "spatial": "idw", **meta}
    out.update(spearman=out["idw"]["spearman"], median_abs_error=out["idw"]["median_abs_error"])
    if getattr(model, "spatial", None):
        from . import krige
        p = model.spatial
        ids = [s.get("id") for s in sensors]
        e = np.log1p(obs_c) - np.log1p(base) - krige.sensor_offsets(ids, p)
        W, _ = krige.kriging_weights(xy, xy, krige.sensor_noise(ids, p), p, exclude=np.arange(n))
        kr = np.expm1(np.log1p(base) + p.rho_at(0) * (W @ e))
        out["krige"] = {"spearman": _rho(kr, obs), "median_abs_error": mae(kr)}
        out.update(spearman=out["krige"]["spearman"], median_abs_error=out["krige"]["median_abs_error"], spatial="krige")
    return out
