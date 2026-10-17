"""A direct model: (coordinates, hour, nearest sensors' readings, weather, static features) -> what a sensor at that place would read.

    python -m backend.ml.direct_model [--folds 5] [--rows 450000]

One gradient-boosted model for any place and any lead. A training row is (target sensor i, target hour t, issue hour t0 = t - lead):
  * the K nearest OTHER sensors' calibrated readings at the issue hour, with their distance and direction (the target is never its own
    neighbour, so the model learns what a place with no sensor would read), plus neighbour means inside several radii;
  * the coordinates of the target and static features there (terrain, greenery, canopy, surface heat, construction, low-rise housing);
  * hour of day, month, weekend, the lead, weather and CAMS at the target hour and at the issue hour, the city median at the issue hour.
Target: log1p(sensor) minus log1p(CAMS at the target hour), so a heavy episode scales instead of capping (trees cannot extrapolate).

Evaluation is the strictest one available: a "bi-fold" design. Sensors and weeks are each split in 5; fold k tests on sensors of group k in
weeks of group k and trains on rows with BOTH sensor and week outside the group. So the model is scored on places it never trained on, in
weeks it never saw, with coordinates among its inputs (the usual way such a model memorises sensors). Compared on the same hidden sensors
and hours with the kriging chain (backend/ml/spatial_fit.py), refitted on the same training weeks.
"""
import argparse
import json
import sys

import numpy as np
import pandas as pd
from scipy.stats import spearmanr
from sklearn.ensemble import HistGradientBoostingRegressor

from ..cai.features import _load_raster, compute_features
from ..cai.graph import DATA, get_net
from . import spatial_fit as S
from .pm_model import static_matrix
from .train import load_hours

K = 8
RADII = (400, 800, 1600, 3200, 6400)
MAX_LEAD = 47
SEED = 7
Z80 = 1.2815515655446004
HEATING = (10, 11, 12, 1, 2, 3)
WEATHER = ["boundary_layer_height", "wind_speed_10m", "wind_speed_925hPa", "temperature_2m", "cloud_cover", "relative_humidity_2m", "shortwave_radiation"]


class World:
    """All matrices the rows are cut from."""

    def __init__(self):
        self.d = S.Data()
        d = self.d
        hrs = load_hours().reindex(d.times)
        self.hrs = hrs
        self.W = hrs[WEATHER].to_numpy(dtype=float)
        self.inv = (hrs.temperature_925hPa - hrs.temperature_2m).to_numpy(dtype=float)
        self.cams25, self.cams10 = hrs.cams25.to_numpy(dtype=float), hrs.cams10.to_numpy(dtype=float)
        self.tpos = pd.Series(np.arange(len(d.times)), index=d.times)
        self.D = np.linalg.norm(d.xy[:, None, :] - d.xy[None, :, :], axis=-1)
        lon, lat = self._lonlat()
        feats = compute_features(get_net("walk"))
        st = static_matrix(feats, feats.at(lon, lat))
        from .pm_model import STATIC_COLS
        cols = {c: st[:, STATIC_COLS.index(c)] for c in ("elev", "rel_elev", "G", "canopy", "ndvi", "lst_anom")}
        cons = feats.at(lon, lat)["construction"]
        low250, low1000 = (_load_raster(DATA / "lowrise.tif", buffer_m=r).sample(lon, lat) for r in (250, 1000))
        bld250 = _load_raster(DATA / "buildings.tif", buffer_m=250).sample(lon, lat)
        c0 = d.xy.mean(axis=0)
        self.static = pd.DataFrame({"x": d.xy[:, 0] / 1000, "y": d.xy[:, 1] / 1000, "r_center": np.linalg.norm(d.xy - np.array([[0.0, 0.0]]), axis=1) / 1000,
                                    **cols, "construction": cons, "lowrise250": low250, "lowrise1000": low1000, "bld250": bld250})
        self.static_cols = list(self.static.columns)

    def _lonlat(self):
        from ..cai.graph import to_lonlat
        lon, lat = to_lonlat(self.d.xy[:, 0], self.d.xy[:, 1])
        return np.asarray(lon), np.asarray(lat)


def sample_rows(w: World, n: int, rng, lead: int | None = None, hours: np.ndarray | None = None) -> pd.DataFrame:
    """Random (sensor, target hour, lead) rows; `lead` forces one lead, `hours` restricts the target hours."""
    d = w.d
    i, t = np.nonzero(np.isfinite(d.U))
    if hours is not None:
        keep = np.isin(t, hours)
        i, t = i[keep], t[keep]
    if n and len(i) > n:
        pick = rng.choice(len(i), n, replace=False)
        i, t = i[pick], t[pick]
    leads = np.full(len(i), lead) if lead is not None else rng.integers(0, MAX_LEAD + 1, len(i))
    t0 = w.tpos.reindex(d.times[t] - pd.to_timedelta(leads, unit="h")).to_numpy()
    ok = np.isfinite(t0)
    return pd.DataFrame({"i": i[ok], "t": t[ok], "t0": t0[ok].astype(int), "lead": leads[ok]})


def features(w: World, rows: pd.DataFrame) -> pd.DataFrame:
    d, U = w.d, w.d.U
    n = len(rows)
    out = {f"nn_u{k}": np.full(n, np.nan) for k in range(K)}
    out.update({f"nn_d{k}": np.full(n, np.nan) for k in range(K)})
    out.update({f"nn_dx{k}": np.full(n, np.nan) for k in range(K)})
    out.update({f"nn_dy{k}": np.full(n, np.nan) for k in range(K)})
    for r in RADII:
        out[f"mean_r{r}"], out[f"cnt_r{r}"] = np.full(n, np.nan), np.zeros(n)
    out["med_others"], out["sd_others"] = np.full(n, np.nan), np.full(n, np.nan)
    ri, rt0 = rows.i.to_numpy(), rows.t0.to_numpy()
    for t0, idx in rows.groupby("t0").indices.items():
        pres = np.flatnonzero(np.isfinite(U[:, t0]))
        vals = U[pres, t0]
        ti = ri[idx]
        Dm = w.D[np.ix_(ti, pres)].copy()
        Dm[pres[None, :] == ti[:, None]] = np.inf  # a target is never its own neighbour
        kk = min(K, len(pres) - 1)
        near = np.argpartition(Dm, kk - 1, axis=1)[:, :kk]
        order = np.take_along_axis(Dm, near, 1).argsort(axis=1)
        near = np.take_along_axis(near, order, 1)
        dist = np.take_along_axis(Dm, near, 1)
        # median and spread of the others (the target excluded exactly)
        self_col = (pres[None, :] == ti[:, None])
        masked = np.where(self_col, np.nan, vals[None, :])
        med = np.nanmedian(masked, axis=1)
        out["med_others"][idx], out["sd_others"][idx] = med, np.nanstd(masked, axis=1)
        for k in range(kk):
            col = pres[near[:, k]]
            out[f"nn_u{k}"][idx] = vals[near[:, k]] - med
            out[f"nn_d{k}"][idx] = np.log1p(dist[:, k])
            out[f"nn_dx{k}"][idx] = (d.xy[col, 0] - d.xy[ti, 0]) / 1000
            out[f"nn_dy{k}"][idx] = (d.xy[col, 1] - d.xy[ti, 1]) / 1000
        for r in RADII:
            inside = Dm <= r
            cnt = inside.sum(1)
            s = np.where(inside, vals[None, :] - med[:, None], 0.0).sum(1)
            out[f"mean_r{r}"][idx] = np.where(cnt > 0, s / np.maximum(cnt, 1), np.nan)
            out[f"cnt_r{r}"][idx] = cnt
    f = pd.DataFrame(out)
    st = w.static.iloc[ri].reset_index(drop=True)
    t, t0 = rows.t.to_numpy(), rows.t0.to_numpy()
    tt = d.times[t]
    time_f = pd.DataFrame({"hour": tt.hour, "month": tt.month, "weekend": (tt.weekday >= 5).astype(float), "lead": rows.lead.to_numpy(),
                           **{f"w_{c}": w.W[t, j] for j, c in enumerate(WEATHER)}, "w_inversion": w.inv[t], "w_stagnation": d.stag[t],
                           "cams25": w.cams25[t], "cams10": w.cams10[t], "cams25_issue": w.cams25[t0], "stag_issue": d.stag[t0]})
    f = pd.concat([st, time_f, f], axis=1)
    f["med_others_vs_cams"] = f.med_others - np.log1p(f.cams25_issue)
    return f


def target(w: World, rows: pd.DataFrame, f: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """(y, anchor): y = log1p(sensor) - anchor, anchor = log1p(CAMS at the target hour), else the city median at issue."""
    anchor = np.where(np.isfinite(f.cams25), np.log1p(f.cams25.to_numpy()), f.med_others.to_numpy())
    obs = w.d.U[rows.i.to_numpy(), rows.t.to_numpy()]
    return obs - anchor, anchor


def gbm(q: float | None, n_iter: int = 300) -> HistGradientBoostingRegressor:
    kw = dict(max_iter=n_iter, learning_rate=0.06, max_leaf_nodes=24, min_samples_leaf=100, l2_regularization=2.0, random_state=SEED)
    return HistGradientBoostingRegressor(loss="quantile", quantile=q, **kw) if q is not None else HistGradientBoostingRegressor(**kw)


def fit_models(X: pd.DataFrame, y: np.ndarray, weeks: np.ndarray, anchors: np.ndarray, lead: np.ndarray) -> dict:
    """p10/p50/p90 plus conformal widening from a held-out quarter of the training weeks (then no refit: the margin stays honest)."""
    uw = np.unique(weeks)
    rng = np.random.default_rng(SEED)
    cal_w = rng.choice(uw, max(2, len(uw) // 4), replace=False)
    cal = np.isin(weeks, cal_w)
    ms = {q: gbm(q).fit(X[~cal], y[~cal]) for q in (0.1, 0.5, 0.9)}
    Xc, yc = X[cal], y[cal]
    p = {q: ms[q].predict(Xc) for q in ms}
    b = np.digitize(lead[cal], (5, 23), right=True)
    lo, hi = np.zeros(3), np.zeros(3)
    for k in range(3):
        s = b == k
        if s.sum() > 300:
            lo[k], hi[k] = np.quantile(p[0.1][s] - yc[s], 0.9), np.quantile(yc[s] - p[0.9][s], 0.9)
    return {"m": ms, "lo": lo, "hi": hi}


def predict(model: dict, X: pd.DataFrame, lead: np.ndarray):
    p = {q: model["m"][q].predict(X) for q in model["m"]}
    b = np.digitize(lead, (5, 23), right=True)
    return p[0.5] - 0.0, np.minimum(p[0.1] - model["lo"][b], p[0.5]), np.maximum(p[0.9] + model["hi"][b], p[0.5])


def hour_rho(df: pd.DataFrame, col: str) -> float:
    r = [spearmanr(g[col], g.obs).statistic for _, g in df.groupby("t") if len(g) >= 15 and g[col].nunique() > 1]
    return float(np.nanmean(r)) if r else float("nan")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--folds", type=int, default=5)
    ap.add_argument("--rows", type=int, default=450_000)
    a = ap.parse_args()
    rng = np.random.default_rng(SEED)
    w = World()
    d = w.d
    nS = len(d.ids)
    sensor_fold = rng.permutation(nS) % a.folds
    uw = np.unique(d.week)
    week_fold = dict(zip(uw, rng.permutation(len(uw)) % a.folds))
    wf_t = np.array([week_fold[x] for x in d.week])  # fold of each hour

    rows = sample_rows(w, a.rows, rng)
    print(f"building features for {len(rows):,} rows ...", file=sys.stderr)
    F = features(w, rows)
    y, anchor = target(w, rows, F)
    rows_fold_s, rows_fold_w = sensor_fold[rows.i.to_numpy()], wf_t[rows.t.to_numpy()]
    wk = d.week[rows.t.to_numpy()]
    month = d.month[rows.t.to_numpy()]
    res_all, parts = {}, []
    for k in range(a.folds):
        test = (rows_fold_s == k) & (rows_fold_w == k)
        train = (rows_fold_s != k) & (rows_fold_w != k)
        mdl = fit_models(F[train].reset_index(drop=True), y[train], wk[train], anchor[train], rows.lead.to_numpy()[train])
        mid, lo, hi = predict(mdl, F[test], rows.lead.to_numpy()[test])
        out = pd.DataFrame({"t": rows.t.to_numpy()[test], "sensor": rows.i.to_numpy()[test], "lead": rows.lead.to_numpy()[test], "month": month[test],
                            "obs": np.expm1(d.U[rows.i.to_numpy()[test], rows.t.to_numpy()[test]]), "city": d.city[rows.t.to_numpy()[test]],
                            "direct": np.expm1(anchor[test] + mid), "inside": ((y[test] >= lo) & (y[test] <= hi)).astype(float),
                            "persistence": np.expm1(F.med_others.to_numpy()[test])})
        parts.append(out)
        print(f"fold {k}: trained on {int(train.sum()):,} rows, scored {int(test.sum()):,} (sensors x weeks never seen)", file=sys.stderr)
        # head to head at lead 0, hours chosen like the kriging evaluation, same hidden sensors, kriging refitted on the training weeks
        train_weeks = np.flatnonzero(wf_t != k)
        test_cols = np.flatnonzero(wf_t == k)
        p, o, _ = S.fit_params(d, train_weeks, rng)
        p.sd_scale = S.fit_sd_scale(d, p, o["offset"], o["post_var"], train_weeks, rng)
        hrs = S.pick_eval_hours(d, test_cols, rng)
        kr = S.score_hours(d, p, o["offset"], o["post_var"], hrs)
        kr = kr[sensor_fold[kr.sensor.to_numpy()] == k].copy()
        kr["scale"] = np.where(np.isin(d.month[kr.t.to_numpy()], S.HEATING), p.sd_scale["heating"], p.sd_scale["summer"])
        kr["inside_k"] = (kr.z.abs() <= Z80 * kr.scale).astype(float)
        r0 = pd.DataFrame({"i": kr.sensor.to_numpy(), "t": kr.t.to_numpy()})
        r0["t0"], r0["lead"] = r0.t, 0
        F0 = features(w, r0)
        y0, a0 = target(w, r0, F0)
        m0, l0, h0 = predict(mdl, F0, r0.lead.to_numpy())
        kr["direct"] = np.expm1(a0 + m0)
        kr["inside_d"] = ((y0 >= l0) & (y0 <= h0)).astype(float)
        kr["city"] = d.city[kr.t.to_numpy()]
        kr["month"] = d.month[kr.t.to_numpy()]
        kr["fold"] = k
        res_all.setdefault("head2head", []).append(kr)
    ev = pd.concat(parts, ignore_index=True)
    hh = pd.concat(res_all["head2head"], ignore_index=True)

    report = {}
    print("\nA. head to head at lead 0 (the hidden sensor is predicted from the other sensors at the same hour)", file=sys.stderr)
    cuts = {"all": np.ones(len(hh), bool), "heating": np.isin(hh.month, HEATING), "summer": ~np.isin(hh.month, HEATING),
            "polluted heating (city>15)": np.isin(hh.month, HEATING) & (hh.city > 15), "city>25": np.isin(hh.month, HEATING) & (hh.city > 25)}
    rows_a = {}
    for name, sel in cuts.items():
        s = hh[sel]
        if s.t.nunique() < 5:
            continue
        rows_a[name] = {"hours": int(s.t.nunique()), "rows": int(len(s)),
                        "level_trend_mae": round(float((s.level_trend - s.obs).abs().median()), 3), "krige_mae": round(float((s.krige_offsets_removed - s.obs).abs().median()), 3),
                        "direct_mae": round(float((s.direct - s.obs).abs().median()), 3),
                        "level_trend_rho": round(hour_rho(s, "level_trend"), 3), "krige_rho": round(hour_rho(s, "krige_offsets_removed"), 3), "direct_rho": round(hour_rho(s, "direct"), 3),
                        "krige_cov80": round(float(s.inside_k.mean()), 3), "direct_cov80": round(float(s.inside_d.mean()), 3)}
    print(pd.DataFrame(rows_a).T.to_string(), file=sys.stderr)
    report["head2head_lead0"] = rows_a

    print("\nB. direct model on random leads (0-47 h), unseen sensors and weeks: median abs error and 80% coverage vs persistence of the city median", file=sys.stderr)
    out_b = {}
    for name, (lo_, hi_) in {"0h": (0, 0), "1-5h": (1, 5), "6-23h": (6, 23), "24-47h": (24, 47)}.items():
        s = ev[(ev.lead >= lo_) & (ev.lead <= hi_)]
        out_b[name] = {"rows": int(len(s)), "direct_mae": round(float((s.direct - s.obs).abs().median()), 3), "persistence_mae": round(float((s.persistence - s.obs).abs().median()), 3),
                       "direct_rho": round(hour_rho(s.assign(t=s.t), "direct"), 3), "coverage80": round(float(s.inside.mean()), 3)}
    for name, sel in {"heating": np.isin(ev.month, HEATING), "polluted heating (city>15)": np.isin(ev.month, HEATING) & (ev.city > 15)}.items():
        s = ev[sel]
        out_b[name] = {"rows": int(len(s)), "direct_mae": round(float((s.direct - s.obs).abs().median()), 3), "persistence_mae": round(float((s.persistence - s.obs).abs().median()), 3),
                       "direct_rho": round(hour_rho(s, "direct"), 3), "coverage80": round(float(s.inside.mean()), 3)}
    print(pd.DataFrame(out_b).T.to_string(), file=sys.stderr)
    report["random_leads"] = out_b
    (DATA / "direct_model_eval.json").write_text(json.dumps(report, indent=1, default=float))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
