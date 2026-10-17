"""Fit and test the spatial stage: per-sensor offsets, variogram, residual persistence, and kriging against the old IDW (Phase 2).

    python -m backend.ml.spatial_fit [--no-save]

Residuals live in log1p space: e0[s,t] = log1p(sensor) - log1p(city median) - street trend (traffic, terrain, as fitted in PmModel).
  1. offsets   persistent per-sensor mean of e0, shrunk toward 0 by an empirical-Bayes factor that counts effectively independent hours
  2. variogram exponential fit to the pairwise semivariance of the offset-removed residuals, by season and for polluted hours
  3. evaluation, time-blocked (whole weeks held out; offsets and variogram refitted on the other weeks), leave-one-SENSOR-out at every
     held-out hour: the hidden sensor is predicted from the others at the same hour with a) level only b) level+trend c) the old IDW
     d) kriging without offset removal e) kriging with offset removal. The city level is taken as known so that this isolates the
     spatial stage; the level model is judged separately (train.py).
  4. persistence  how much of a kriged residual is still there h hours later (the multiplier the forecast uses)
"""
import argparse
import json
import sys
from datetime import datetime, timezone

import numpy as np
import pandas as pd
from scipy.optimize import curve_fit
from scipy.stats import spearmanr
from sklearn.model_selection import GroupKFold

from ..cai.features import compute_features
from ..cai.graph import DATA, get_net, to_xy
from ..cai.krige import K_NEIGHBOURS, PATH, SpatialParams, Z80, kriging_weights, sensor_noise
from ..cai.score import idw_residual
from ..score import stagnation_score
from . import calibration as calibration_mod
from .pm_model import DEV_CLIP, PmModel, SPATIAL_COLS, STATIC_COLS, static_matrix
from .train import MIN_SENSORS_PER_HOUR, calibrate_history, load_hours, load_sensors

BINS = np.array([0, 50, 100, 200, 300, 500, 750, 1000, 1500, 2000, 3000, 5000, 10000, 30000], dtype=float)
HEATING = (10, 11, 12, 1, 2, 3)
SEED = 7
LEADS = (0, 1, 2, 3, 6, 12, 24, 36, 47)
EVAL_HOURS = 700  # held-out hours scored per run (stratified so that polluted heating-season hours are well represented)


class Data:
    """Sensor x hour matrices of the residual problem."""

    def __init__(self):
        hrs, sens = load_hours(), load_sensors()
        cal = calibration_mod.get("pm25")
        if cal is None:
            raise SystemExit("no data/calibration.joblib: run `make calibrate` first")
        sens = calibrate_history(sens, hrs, cal)
        med = sens.groupby("time").pm25.transform("median")
        sens["pm"] = sens.pm25.clip(0, 4 * med + 10)
        g = sens.groupby("time").pm
        city = pd.DataFrame({"city": g.median(), "n": g.size()})
        city = city[city.n >= MIN_SENSORS_PER_HOUR]
        sens = sens[sens.time.isin(city.index)]
        w = hrs.loc[city.index.intersection(hrs.index)]
        keep = city.index.intersection(w.index)
        city, w = city.loc[keep], w.loc[keep]
        stag = np.array([stagnation_score(r.boundary_layer_height, r.wind_speed_10m, r.temperature_2m, r.temperature_925hPa) for r in w.itertuples()])
        ok = np.isfinite(stag)
        self.times = pd.DatetimeIndex(keep[ok])
        city, stag = city.loc[self.times], stag[ok]
        sens = sens[sens.time.isin(self.times)]
        loc = sens.groupby("sensor")[["lat", "lon"]].median()
        self.ids = loc.index.to_numpy()
        self.xy = np.column_stack(to_xy(loc.lon.to_numpy(), loc.lat.to_numpy()))
        net = get_net("walk")
        feats = compute_features(net)
        st = static_matrix(feats, feats.at(loc.lon.to_numpy(), loc.lat.to_numpy()))
        model = PmModel.load()
        if model is None:
            raise SystemExit("no usable data/pm_model.joblib (train it first: `make train`)")
        sp = model.spatial
        cols = [STATIC_COLS.index(c) for c in SPATIAL_COLS]
        raw = st[:, cols].astype("float64").copy()
        raw[:, 0] = np.log1p(np.maximum(raw[:, 0], 0))
        z = np.nan_to_num((raw - np.asarray(sp["mean"])) / np.asarray(sp["std"]))
        coef = np.asarray(sp["coef"])
        A, B = z @ coef[:2], z @ coef[2:]
        self.stag = stag
        self.dev = np.clip(A[:, None] + B[:, None] * stag[None, :] / 100.0, -DEV_CLIP, DEV_CLIP)  # (S, T)
        si = {s: i for i, s in enumerate(self.ids)}
        ti = {t: j for j, t in enumerate(self.times)}
        U = np.full((len(self.ids), len(self.times)), np.nan)
        U[sens.sensor.map(si).to_numpy(), sens.time.map(ti).to_numpy()] = np.log1p(sens.pm.to_numpy())
        self.U, self.PM = U, np.expm1(U)
        self.L = np.log1p(city.city.to_numpy())
        self.E0 = U - self.L[None, :] - self.dev
        self.month = self.times.month.to_numpy()
        self.day = self.times.normalize()
        self.week = ((self.day - self.day.min()).days // 7).to_numpy()
        self.city = city.city.to_numpy()
        iu, ju = np.triu_indices(len(self.ids), 1)
        self.iu, self.ju = iu, ju
        self.pair_d = np.linalg.norm(self.xy[iu] - self.xy[ju], axis=1)
        print(f"{len(self.ids)} sensors x {len(self.times)} hours, {np.isfinite(U).mean():.0%} filled", file=sys.stderr)


def estimate_offsets(E0: np.ndarray, cols: np.ndarray) -> dict:
    """Persistent per-sensor offset with empirical-Bayes shrinkage. n_eff discounts autocorrelated hours via the lag-1 correlation."""
    X = E0[:, cols]
    n = np.isfinite(X).sum(1)
    mean = np.where(n > 0, np.nanmean(np.where(np.isfinite(X), X, np.nan), axis=1), 0.0)
    D = X - mean[:, None]
    a, b = D[:, :-1], D[:, 1:]
    both = np.isfinite(a) & np.isfinite(b)
    rho1 = float(np.nansum(np.where(both, a * b, 0)) / max(np.nansum(np.where(both, a * a, 0)), 1e-9))
    rho1 = float(np.clip(rho1, 0.0, 0.98))
    sigma2 = float(np.nanmean(D**2))
    n_eff = np.maximum(n * (1 - rho1) / (1 + rho1), 1.0)
    se2 = sigma2 / n_eff
    has = n >= 24
    tau2 = float(max(np.var(mean[has]) - np.mean(se2[has]), 1e-4))
    shrink = np.where(has, tau2 / (tau2 + se2), 0.0)
    return {"offset": mean * shrink, "post_var": np.where(has, shrink * se2, tau2), "raw": mean, "tau2": tau2, "sigma2": sigma2, "rho1": rho1,
            "n_eff": n_eff, "n": n, "shrink": shrink}


def semivariogram(d: Data, E: np.ndarray, cols: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(mean distance, semivariance, pair count) per distance bin, pooled over the given hours."""
    b = np.digitize(d.pair_d, BINS) - 1
    nb = len(BINS) - 1
    ssum, cnt, dsum = np.zeros(nb), np.zeros(nb), np.zeros(nb)
    for t in cols:
        v = E[:, t]
        diff = (v[d.iu] - v[d.ju]) ** 2
        ok = np.isfinite(diff)
        ssum += np.bincount(b[ok], weights=diff[ok], minlength=nb)[:nb]
        cnt += np.bincount(b[ok], minlength=nb)[:nb]
        dsum += np.bincount(b[ok], weights=d.pair_d[ok], minlength=nb)[:nb]
    cnt_safe = np.maximum(cnt, 1)
    return dsum / cnt_safe, 0.5 * ssum / cnt_safe, cnt


def fit_exponential(h: np.ndarray, g: np.ndarray, cnt: np.ndarray) -> dict:
    """gamma(h) = nugget + psill * (1 - exp(-h / a)), weighted by pair count (Cressie)."""
    ok = cnt > 200
    f = lambda x, n, p, a: n + p * (1 - np.exp(-x / a))
    hi = float(np.max(g[ok]) * 1.5)
    try:
        (n, p, a), _ = curve_fit(f, h[ok], g[ok], p0=[g[ok][0], max(g[ok][-1] - g[ok][0], 1e-3), 500.0], sigma=g[ok] / np.sqrt(cnt[ok]),
                                 bounds=([0, 0, 20], [hi, hi, 20000]), maxfev=20000)
    except Exception:  # noqa: BLE001
        n, p, a = float(g[ok][0]), 0.0, 500.0
    return {"nugget": float(n), "psill": float(p), "range_m": float(a), "practical_range_m": float(3 * a)}


def season_cols(d: Data, cols: np.ndarray, kind: str) -> np.ndarray:
    m = np.isin(d.month[cols], HEATING)
    if kind == "heating":
        return cols[m]
    if kind == "summer":
        return cols[~m]
    if kind == "polluted_heating":
        return cols[m & (d.city[cols] > 15)]
    return cols


def rng_pick(rng, cols, k):
    return np.sort(rng.choice(cols, min(k, len(cols)), replace=False)) if len(cols) else cols


def fit_variograms(d: Data, E: np.ndarray, cols: np.ndarray, rng) -> dict:
    out = {}
    for kind in ("all", "heating", "summer", "polluted_heating"):
        c = rng_pick(rng, season_cols(d, cols, kind), 300)
        if len(c) < 20:
            continue
        h, g, cnt = semivariogram(d, E, c)
        out[kind] = {**fit_exponential(h, g, cnt), "hours": int(len(c)), "bins": {"h": h.round(0).tolist(), "gamma": g.round(4).tolist(), "pairs": cnt.astype(int).tolist()},
                     "total_variance": float(np.nanmean(np.nanvar(E[:, c], axis=0)))}
    return out


KN = K_NEIGHBOURS  # neighbours per prediction (--k)


def loo_hour(d: Data, t: int, params: SpatialParams, off: np.ndarray, post: np.ndarray, with_idw: bool = True) -> dict | None:
    """Predict every sensor present at hour t from the others. Returns arrays over the present sensors."""
    pres = np.flatnonzero(np.isfinite(d.U[:, t]))
    if len(pres) < MIN_SENSORS_PER_HOUR:
        return None
    xy = d.xy[pres]
    e0 = d.E0[pres, t]
    base_u = d.L[t] + d.dev[pres, t]
    noise_known = params.nugget + post[pres]
    self_idx = np.arange(len(pres))
    out = {"pres": pres, "e0": e0, "base_u": base_u}
    # kriging with offsets removed (deployed): the target is the sensor's raw reading, predicted at a location we know nothing else about
    for name, e, noise in (("krige_off", e0 - off[pres], noise_known), ("krige_raw", e0, np.full(len(pres), params.nugget + params.tau2))):
        W, kv = kriging_weights(xy, xy, noise, params, k=KN, exclude=self_idx)
        out[name] = W @ e
        out[name + "_kv"] = kv
    if with_idw:
        base_pm = np.expm1(base_u)
        resid = np.clip(d.PM[pres, t], 0, 4 * np.median(d.PM[pres, t]) + 10) - base_pm
        out["idw"] = np.array([idw_residual(xy[i : i + 1], np.delete(xy, i, 0), np.delete(resid, i))[0] for i in range(len(pres))])
    return out


def pick_eval_hours(d: Data, test_cols: np.ndarray, rng) -> np.ndarray:
    """Stratified: half polluted heating-season hours (where it matters), the rest spread over the remaining held-out hours."""
    poll = season_cols(d, test_cols, "polluted_heating")
    rest = np.setdiff1d(test_cols, poll)
    return np.sort(np.concatenate([rng_pick(rng, poll, EVAL_HOURS // 2), rng_pick(rng, rest, EVAL_HOURS // 2)]))


def score_hours(d: Data, params: SpatialParams, off, post, hours: np.ndarray) -> pd.DataFrame:
    rows = []
    for t in hours:
        r = loo_hour(d, t, params, off, post)
        if r is None:
            continue
        pres, base_u, e0 = r["pres"], r["base_u"], r["e0"]
        obs_pm = d.PM[pres, t]
        pm_hat = {
            "level_only": np.expm1(np.full(len(pres), d.L[t])),
            "level_trend": np.expm1(base_u),
            "idw_current": np.maximum(np.expm1(base_u) + r["idw"], 0),
            "krige_raw": np.expm1(base_u + r["krige_raw"]),
            "krige_offsets_removed": np.expm1(base_u + r["krige_off"]),
        }
        sd = np.sqrt(params.nugget + params.tau2 + r["krige_off_kv"])  # unscaled; the per-season factor is applied by the caller
        u = np.log1p(obs_pm)
        base = {"t": t, "sensor": pres, "obs": obs_pm, "z": (u - base_u - r["krige_off"]) / sd, "sd": sd}
        for k, v in pm_hat.items():
            base[k] = v
        rows.append(pd.DataFrame(base))
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()


METHODS = ["level_only", "level_trend", "idw_current", "krige_raw", "krige_offsets_removed"]


def fit_sd_scale(d: Data, params: SpatialParams, off, post, cols: np.ndarray, rng) -> dict:
    """Per-season factor on the SD so that 80 % intervals hold 80 % of the (training) readings: q80(|z|) / z80."""
    out = {}
    for kind in ("heating", "summer"):
        c = rng_pick(rng, season_cols(d, cols, kind), 150)
        if len(c) < 20:
            out[kind] = 1.0
            continue
        z = np.abs(score_hours(d, params, off, post, c).z.to_numpy())
        out[kind] = round(float(np.quantile(z[np.isfinite(z)], 0.8) / Z80), 3)
    return out


def summarise(d: Data, df: pd.DataFrame) -> dict:
    """Median error, mean within-hour Spearman and 80 % coverage, by season and level."""
    df = df.assign(month=d.month[df.t.to_numpy()], city=d.city[df.t.to_numpy()])
    df["heating"] = np.isin(df.month, HEATING)
    df["inside"] = (df.z.abs() <= Z80 * df.scale).astype(float)
    out = {}
    cuts = {"all": df.index == df.index, "heating": df.heating, "summer": ~df.heating, "polluted_heating": df.heating & (df.city > 15),
            "very_polluted_heating": df.heating & (df.city > 25)}
    for name, sel in cuts.items():
        s = df[sel]
        if s.t.nunique() < 5:
            continue
        res = {"hours": int(s.t.nunique()), "rows": int(len(s)), "coverage80": round(float(s.inside.mean()), 3)}
        for m in METHODS:
            err = (s[m] - s.obs).abs()
            rho = [spearmanr(g[m], g.obs).statistic if g[m].nunique() > 1 else np.nan for _, g in s.groupby("t") if len(g) >= 20]
            res[m] = {"median_abs_error": round(float(err.median()), 3), "mae": round(float(err.mean()), 3),
                      "within_hour_spearman": round(float(np.nanmean(rho)), 3) if rho else None}
        out[name] = res
    return out


def persistence(d: Data, params: SpatialParams, off, post, hours: np.ndarray) -> dict:
    """Slope of the offset-removed residual at lead h on the kriged residual now, and the residual spread around that prediction.
    Pairs only exist inside the sampled 2-day blocks, so long leads have fewer rows."""
    tidx = {t: i for i, t in enumerate(d.times)}
    res = {h: {"x": [], "y": []} for h in LEADS}
    for t in hours:
        r = loo_hour(d, t, params, off, post, with_idw=False)
        if r is None:
            continue
        for h in LEADS:
            j = tidx.get(d.times[t] + pd.Timedelta(hours=h))
            if j is None:
                continue
            y = d.E0[r["pres"], j] - off[r["pres"]]
            ok = np.isfinite(y)
            res[h]["x"].append(r["krige_off"][ok])
            res[h]["y"].append(y[ok])
    out = {}
    for h, v in res.items():
        if not v["x"]:
            continue
        x, y = np.concatenate(v["x"]), np.concatenate(v["y"])
        if len(x) < 2000:
            continue
        slope = float(np.cov(x, y)[0, 1] / np.var(x))
        out[h] = {"slope": round(slope, 3), "n": int(len(x)), "resid_sd": round(float(np.std(y - slope * x)), 4), "y_sd": round(float(np.std(y)), 4)}
    return out


def fit_params(d: Data, cols: np.ndarray, rng, with_variogram: bool = True) -> tuple[SpatialParams, dict, dict]:
    o = estimate_offsets(d.E0, cols)
    E = d.E0 - o["offset"][:, None]
    vg = fit_variograms(d, E, cols, rng) if with_variogram else {}
    v = vg.get("all") or {"nugget": 0.1, "psill": 0.0, "range_m": 500.0}
    p = SpatialParams(nugget=v["nugget"], psill=v["psill"], range_m=v["range_m"], tau2=o["tau2"])
    return p, o, vg


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-save", action="store_true")
    ap.add_argument("--folds", type=int, default=5)
    ap.add_argument("--k", type=int, default=K_NEIGHBOURS, help="kriging neighbours")
    a = ap.parse_args()
    global KN
    KN = a.k
    rng = np.random.default_rng(SEED)
    d = Data()
    allcols = np.arange(len(d.times))

    # ---- time-blocked evaluation: whole weeks held out, everything refitted on the rest
    parts, persist_all = [], []
    for k, (tr, te) in enumerate(GroupKFold(a.folds).split(allcols, groups=d.week)):
        p, o, _ = fit_params(d, tr, rng)
        p.sd_scale = fit_sd_scale(d, p, o["offset"], o["post_var"], tr, rng)
        hrs = pick_eval_hours(d, te, rng)
        df = score_hours(d, p, o["offset"], o["post_var"], hrs)
        df["scale"] = np.where(np.isin(d.month[df.t.to_numpy()], HEATING), p.sd_scale["heating"], p.sd_scale["summer"])
        parts.append(df)
        persist_all.append(persistence(d, p, o["offset"], o["post_var"], rng_pick(rng, hrs, 150)))
        print(f"fold {k}: nugget {p.nugget:.4f} psill {p.psill:.4f} range {p.range_m:.0f} m tau2 {p.tau2:.4f} sd scale {p.sd_scale}; "
              f"{len(hrs)} test hours, {len(df):,} rows", file=sys.stderr)
    ev = pd.concat(parts, ignore_index=True)
    summ = summarise(d, ev)
    for name, res in summ.items():
        print(f"\n[{name}] hours {res['hours']}, rows {res['rows']:,}, 80% interval coverage {res['coverage80']}", file=sys.stderr)
        print(pd.DataFrame({m: res[m] for m in METHODS}).T.to_string(), file=sys.stderr)

    # ---- final fit on everything
    p, o, vg = fit_params(d, allcols, rng)
    print("\nfinal variograms (exponential fit):", file=sys.stderr)
    for kind, v in vg.items():
        print(f"  {kind:17s} nugget {v['nugget']:.4f} psill {v['psill']:.4f} range a {v['range_m']:.0f} m (practical {v['practical_range_m']:.0f} m)  "
              f"nugget share {v['nugget'] / max(v['nugget'] + v['psill'], 1e-9):.0%}  hours {v['hours']}", file=sys.stderr)
    print("  semivariance by distance (all):", json.dumps(dict(zip(vg["all"]["bins"]["h"], vg["all"]["bins"]["gamma"]))), file=sys.stderr)
    print(f"offsets: tau2 {o['tau2']:.4f} (SD {np.sqrt(o['tau2']):.3f} log1p units, about x{np.exp(np.sqrt(o['tau2'])):.2f}), within-sensor variance {o['sigma2']:.4f}, "
          f"lag-1 autocorrelation {o['rho1']:.2f}, median shrink factor {np.median(o['shrink'][o['n'] >= 24]):.2f}", file=sys.stderr)
    # stability of the offsets: first half of the weeks vs second half
    half = d.week <= np.median(d.week)
    o1, o2 = estimate_offsets(d.E0, allcols[half]), estimate_offsets(d.E0, allcols[~half])
    both = (o1["n"] >= 100) & (o2["n"] >= 100)
    stab = float(np.corrcoef(o1["raw"][both], o2["raw"][both])[0, 1])
    print(f"offset stability, first vs second half of the weeks: r = {stab:.2f} on {int(both.sum())} sensors", file=sys.stderr)

    # persistence: pool the folds' slopes
    pers = {}
    for h in LEADS:
        sl = [x[h] for x in persist_all if h in x]
        if sl:
            w = np.array([s["n"] for s in sl], dtype=float)
            pers[h] = {"slope": round(float(np.average([s["slope"] for s in sl], weights=w)), 3), "n": int(w.sum()),
                       "resid_sd": round(float(np.average([s["resid_sd"] for s in sl], weights=w)), 4), "y_sd": round(float(np.average([s["y_sd"] for s in sl], weights=w)), 4)}
    print("\nresidual persistence (slope of the offset-removed residual at lead h on the kriged residual now):", json.dumps(pers), file=sys.stderr)

    p.offsets = {int(i): float(x) for i, x in zip(d.ids, o["offset"])}
    p.offset_var = {int(i): float(x) for i, x in zip(d.ids, o["post_var"])}
    # smooth decay c + (r0 - c) * exp(-h / tau) through the measured slopes (the raw values wobble because pairs only exist inside the
    # sampled 2-day blocks, and 24 h / 47 h recur at the same hour of day); stored at a few leads and interpolated at serving time
    hs = np.array(sorted(pers), dtype=float)
    sl = np.array([pers[int(h)]["slope"] for h in hs])
    wn = np.sqrt(np.array([pers[int(h)]["n"] for h in hs], dtype=float))
    f = lambda x, r0, c, tau: c + (r0 - c) * np.exp(-x / tau)
    (r0, c, tau), _ = curve_fit(f, hs, sl, p0=[0.8, 0.15, 4.0], sigma=1 / wn, bounds=([0.0, 0.0, 0.5], [1.0, 0.6, 48.0]))
    store = (0, 1, 2, 3, 4, 6, 9, 12, 18, 24, 36, 47)
    p.rho = {h: float(np.clip(f(h, r0, c, tau), 0.0, 1.0)) for h in store}
    print(f"persistence curve: rho(h) = {c:.2f} + ({r0:.2f} - {c:.2f}) * exp(-h / {tau:.1f} h)", file=sys.stderr)
    p.sd_scale = fit_sd_scale(d, p, o["offset"], o["post_var"], allcols, rng)
    p.meta = {"trained": datetime.now(timezone.utc).strftime("%Y-%m-%d"), "calibration_coef": calibration_mod.get("pm25").coef, "sensors": int(len(d.ids)), "hours": int(len(d.times)),
              "variograms": {k: {kk: vv for kk, vv in v.items()} for k, v in vg.items()}, "offset_stability_r": round(stab, 3),
              "offset_sd_log": round(float(np.sqrt(o["tau2"])), 3), "lag1_autocorr": round(o["rho1"], 3), "persistence": pers,
              "eval": summ, "folds": a.folds}
    if not a.no_save:
        p.save()
        print(f"saved {PATH}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
