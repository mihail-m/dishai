"""Does training on the 2017-2019 Sofia sensors (Kaggle) help predict today's hidden sensors? (pooled eras)

    PYTHONPATH=. .venv/bin/python -m backend.ml.pool_eval [--folds 2 3 4]

A deliberately weather-free local model, so that both eras can be used (2017-19 has no CAMS or numerical weather in this repository):
for a hidden sensor i at hour t, the backbone is the kriged correction of the other sensors (offsets removed, estimated per era), and trees
learn what is left from static features of the place (terrain, canopy, NDVI, surface heat, construction, low-rise housing, GHSL buildings and
population), the nearest neighbours' residuals and distances, the kriging variance, hour and month. Coordinates are NOT features. The residual
target has the sensor's own offset removed, as in stack_eval. Raw sensor scale in both eras (log1p of the uncalibrated reading, minus the city median).

Test: sensors and weeks of 2024-26 never trained on (the same bi-fold design). Training sets compared:
  A. 2024-26 training rows only;   B. 2017-19 rows only (another era, another set of sensors);   C. both.
"""
import argparse
import sys
from types import SimpleNamespace

import numpy as np
import pandas as pd
from scipy.stats import spearmanr
from sklearn.ensemble import HistGradientBoostingRegressor

from ..cai.features import _load_raster, compute_features
from ..cai.graph import DATA, get_net, to_lonlat
from . import old_sofia as O
from . import spatial_fit as S
from .pm_model import STATIC_COLS, static_matrix

SEED = 7
HEATING = S.HEATING


def as_data(era: O.Era) -> SimpleNamespace:
    """The attributes spatial_fit's functions read, for a raw-scale era without a terrain trend."""
    t = era.times
    d = SimpleNamespace(ids=era.ids, xy=era.xy, U=era.U, PM=np.expm1(era.U), L=era.L, E0=era.E0, dev=np.zeros_like(era.U), times=t, month=t.month.to_numpy(),
                        city=np.expm1(era.L), iu=era.iu, ju=era.ju, pair_d=era.pair_d)
    day = t.normalize()
    d.week = ((day - day.min()).days // 7).to_numpy()
    return d


def static_features(xy: np.ndarray) -> pd.DataFrame:
    lon, lat = (np.asarray(a) for a in to_lonlat(xy[:, 0], xy[:, 1]))
    feats = compute_features(get_net("walk"))
    sf = feats.at(lon, lat)
    st = static_matrix(feats, sf)
    out = {c: st[:, STATIC_COLS.index(c)] for c in ("elev", "rel_elev", "G", "canopy", "ndvi", "lst_anom")}
    out["construction"] = sf["construction"]
    for nm, f, rr in (("lowrise", "lowrise", (250, 1000)), ("ghs_height", "ghs_height", (150, 400, 1000)), ("ghs_volume", "ghs_volume", (150, 400, 1000)), ("ghs_pop", "ghs_pop", (150, 400, 1000))):
        for r in rr:
            out[f"{nm}_{r}"] = _load_raster(DATA / f"{f}.tif", buffer_m=r).sample(lon, lat)
    return pd.DataFrame(out)


def rows_for(d, static, p, off, post, hours, sensor_mask):
    """One row per (sensor in sensor_mask present at an hour in `hours`): kriging backbone + neighbour features."""
    parts = []
    D = np.linalg.norm(d.xy[:, None] - d.xy[None], axis=-1)
    for t in hours:
        r = S.loo_hour(d, t, p, off, post, with_idw=False)
        if r is None:
            continue
        sel = sensor_mask[r["pres"]]
        if not sel.any():
            continue
        pres = r["pres"]
        e = d.E0[pres, t] - off[pres]
        Dm = D[np.ix_(pres, pres)].copy()
        np.fill_diagonal(Dm, np.inf)
        k = 4
        near = np.argsort(Dm, axis=1)[:, :k]
        nd = np.take_along_axis(Dm, near, 1)
        ne = e[near]
        df = pd.DataFrame({"i": pres, "t": t, "kappa": r["krige_off"], "kv": r["krige_off_kv"], "L": d.L[t], "n_present": len(pres), "hour": d.times[t].hour,
                           "month": d.times[t].month, "weekend": float(d.times[t].weekday() >= 5)})
        for j in range(k):
            df[f"nn_e{j}"], df[f"nn_d{j}"] = ne[:, j], np.log1p(nd[:, j])
        df["mean_e1km"] = [np.nanmean(e[Dm[a] <= 1000]) if (Dm[a] <= 1000).any() else np.nan for a in range(len(pres))]
        parts.append(df[sel])
    out = pd.concat(parts, ignore_index=True)
    out = pd.concat([out, static.iloc[out.i.to_numpy()].reset_index(drop=True)], axis=1)
    out["y"] = d.U[out.i.to_numpy(), out.t.to_numpy()] - (d.L[out.t.to_numpy()] + out.kappa.to_numpy()) - off[out.i.to_numpy()]  # offset removed
    out["obs"] = np.expm1(d.U[out.i.to_numpy(), out.t.to_numpy()])
    out["base"] = d.L[out.t.to_numpy()] + out.kappa.to_numpy()
    return out


def gbm():
    return HistGradientBoostingRegressor(max_iter=150, learning_rate=0.04, max_leaf_nodes=8, min_samples_leaf=400, l2_regularization=10.0, random_state=SEED)


def hour_rho(df, col):
    r = [spearmanr(g[col], g.obs).statistic for _, g in df.groupby("t") if len(g) >= 15 and g[col].nunique() > 1]
    return float(np.nanmean(r)) if r else float("nan")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--folds", type=int, nargs="+", default=[2, 3, 4])
    ap.add_argument("--train-hours", type=int, default=700)
    a = ap.parse_args()
    rng = np.random.default_rng(SEED)
    H = O.HIST
    s_old = pd.read_csv(H / "sofia1719" / "sds_hourly.csv", usecols=["sensor", "lat", "lon", "hour_utc", "pm25"])
    s_old["t"] = pd.to_datetime(s_old.hour_utc, format="%Y-%m-%dT%H").dt.tz_localize("UTC").dt.tz_convert("Europe/Sofia").dt.tz_localize(None)
    s_new = pd.read_csv(H / "sensors.csv", usecols=["sensor", "lat", "lon", "hour_utc", "pm25"])
    s_new["t"] = pd.to_datetime(s_new.hour_utc).dt.tz_localize("UTC").dt.tz_convert("Europe/Sofia").dt.tz_localize(None)
    dn, do = as_data(O.Era("new", s_new)), as_data(O.Era("old", s_old))
    st_n, st_o = static_features(dn.xy), static_features(do.xy)
    print(f"new era {len(dn.ids)} sensors / {len(dn.times)} h; old era {len(do.ids)} sensors / {len(do.times)} h", file=sys.stderr)

    # the old era is prepared once (its own offsets and variogram from all of its own data: it is training data only)
    allo = np.arange(len(do.times))
    po, oo, _ = S.fit_params(do, allo, rng)
    po.sd_scale = S.fit_sd_scale(do, po, oo["offset"], oo["post_var"], allo, rng)
    old_hours = S.rng_pick(rng, S.pick_eval_hours(do, allo, rng), a.train_hours)
    old_rows = rows_for(do, st_o, po, oo["offset"], oo["post_var"], old_hours, np.ones(len(do.ids), bool))
    print(f"old-era training rows: {len(old_rows):,}", file=sys.stderr)

    sensor_fold = rng.permutation(len(dn.ids)) % 5
    uw = np.unique(dn.week)
    wf = dict(zip(uw, rng.permutation(len(uw)) % 5))
    wf_t = np.array([wf[x] for x in dn.week])
    parts = []
    for k in a.folds:
        tr_cols, te_cols = np.flatnonzero(wf_t != k), np.flatnonzero(wf_t == k)
        p, o, _ = S.fit_params(dn, tr_cols, rng)
        p.sd_scale = S.fit_sd_scale(dn, p, o["offset"], o["post_var"], tr_cols, rng)
        tr_h = S.rng_pick(rng, S.pick_eval_hours(dn, tr_cols, rng), a.train_hours)
        te_h = S.pick_eval_hours(dn, te_cols, rng)
        tr = rows_for(dn, st_n, p, o["offset"], o["post_var"], tr_h, sensor_fold != k)
        te = rows_for(dn, st_n, p, o["offset"], o["post_var"], te_h, sensor_fold == k)
        feats = [c for c in te.columns if c not in ("i", "t", "y", "obs", "base")]
        out = te[["t", "obs", "base"]].copy()
        out["fold"], out["month"], out["city"] = k, dn.month[te.t.to_numpy()], dn.city[te.t.to_numpy()]
        out["krige"] = np.expm1(te.base)
        for name, X, y in (("A_new_only", tr[feats], tr.y), ("B_old_only", old_rows[feats], old_rows.y), ("C_both", pd.concat([tr[feats], old_rows[feats]]), pd.concat([tr.y, old_rows.y]))):
            m = gbm().fit(X, y)
            out[name] = np.expm1(te.base + m.predict(te[feats]))
        parts.append(out)
        print(f"fold {k}: train {len(tr):,} new rows, test {len(te):,}", file=sys.stderr)
    ev = pd.concat(parts, ignore_index=True)
    cuts = {"all": np.ones(len(ev), bool), "heating": np.isin(ev.month, HEATING), "summer": ~np.isin(ev.month, HEATING),
            "polluted heating (city>15)": np.isin(ev.month, HEATING) & (ev.city > 15)}
    rows = {}
    for name, m in cuts.items():
        g = ev[m]
        r = {"hours": int(g.t.nunique()), "rows": int(len(g))}
        for v in ("krige", "A_new_only", "B_old_only", "C_both"):
            r[f"{v}_mae"], r[f"{v}_rho"] = round(float((g[v] - g.obs).abs().median()), 3), round(hour_rho(g, v), 3)
        rows[name] = r
    print(f"\nfolds {a.folds}, raw sensor scale, median abs error (raw ug/m3) and within-hour Spearman on 2024-26 hidden sensors:", file=sys.stderr)
    print(pd.DataFrame(rows).T.to_string(), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
