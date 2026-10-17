"""Can local inputs improve on the kriging chain? ML on top of kriging, scored on sensors and weeks it never saw.

    python -m backend.ml.stack_eval [--folds 0 1] [--no-local] [--rows 120000]

The naked direct model (backend/ml/direct_model.py) loses to kriging because the trees memorise the sensors' positions. The standard cure is
to keep kriging as the backbone and let the trees learn only what is left over: for a hidden sensor i at hour t

    reading = base(level, terrain) + kriged correction from the other sensors        <- backend/cai/krige.py, refitted on the training weeks
              + ML correction from local inputs (grid weather, nearby temperature sensors, buildings, greenery, ...)

Same bi-fold protocol as direct_model.py (sensors and weeks both unseen). Hyper-parameters were set looking at folds 0 and 1 only;
the numbers to quote are those of the remaining folds.
"""
import argparse
import json
import sys

import numpy as np
import pandas as pd
from scipy.stats import spearmanr
from sklearn.ensemble import HistGradientBoostingRegressor

from . import direct_model as M
from . import spatial_fit as S

HEATING = (10, 11, 12, 1, 2, 3)
SEED = 7


def make_rows(w, p, off, post, hours, sensor_mask):
    rows = []
    for t in hours:
        r = S.loo_hour(w.d, t, p, off, post, with_idw=False)
        if r is None:
            continue
        sel = sensor_mask[r["pres"]]
        if not sel.any():
            continue
        rows.append(pd.DataFrame({"i": r["pres"][sel], "t": t, "kappa": r["krige_off"][sel], "kv": r["krige_off_kv"][sel], "base_u": r["base_u"][sel]}))
    out = pd.concat(rows, ignore_index=True)
    out["t0"], out["lead"] = out.t, 0
    return out


def hour_rho(df, col):
    r = [spearmanr(g[col], g.obs).statistic for _, g in df.groupby("t") if len(g) >= 15 and g[col].nunique() > 1]
    return float(np.nanmean(r)) if r else float("nan")


def gbm(strong: bool) -> HistGradientBoostingRegressor:
    return HistGradientBoostingRegressor(max_iter=150 if strong else 80, learning_rate=0.04, max_leaf_nodes=8, min_samples_leaf=400,
                                         l2_regularization=10.0, random_state=SEED)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--folds", type=int, nargs="+", default=[0, 1, 2, 3, 4])
    ap.add_argument("--nfolds", type=int, default=5)
    ap.add_argument("--train-hours", type=int, default=700)
    ap.add_argument("--no-local", action="store_true")
    ap.add_argument("--no-coords", action="store_true")
    ap.add_argument("--tag", default="")
    ap.add_argument("--sensor-frac", type=float, default=1.0, help="learning curve: share of the training sensors the trees may learn from")
    a = ap.parse_args()
    M.LOCAL = not a.no_local
    rng = np.random.default_rng(SEED)
    w = M.World()
    d = w.d
    sensor_fold = rng.permutation(len(d.ids)) % a.nfolds
    uw = np.unique(d.week)
    wf = dict(zip(uw, rng.permutation(len(uw)) % a.nfolds))
    wf_t = np.array([wf[x] for x in d.week])
    parts = []
    off_all = S.estimate_offsets(d.E0, np.arange(len(d.times)))["offset"]  # ceiling only: the hidden sensor's own persistent offset, which no model can know
    for k in a.folds:
        train_cols, test_cols = np.flatnonzero(wf_t != k), np.flatnonzero(wf_t == k)
        p, o, _ = S.fit_params(d, train_cols, rng)
        p.sd_scale = S.fit_sd_scale(d, p, o["offset"], o["post_var"], train_cols, rng)
        tr_hours = S.rng_pick(rng, S.pick_eval_hours(d, train_cols, rng), a.train_hours)
        te_hours = S.pick_eval_hours(d, test_cols, rng)
        keep = np.random.default_rng(SEED + k).random(len(d.ids)) < a.sensor_frac
        tr = make_rows(w, p, o["offset"], o["post_var"], tr_hours, (sensor_fold != k) & keep)
        te = make_rows(w, p, o["offset"], o["post_var"], te_hours, sensor_fold == k)
        out = {}
        for name, r in (("tr", tr), ("te", te)):
            F = M.features(w, r)
            drop = ["x", "y", "r_center"] if a.no_coords else []
            F = F.drop(columns=drop)
            F["kappa"], F["kv"], F["base_vs_med"] = r.kappa.to_numpy(), r.kv.to_numpy(), r.base_u.to_numpy() - F.med_others.to_numpy()
            out[name] = F
        # Train on the reading with the sensor's own persistent offset removed: otherwise the trees spend themselves learning each training
        # sensor's offset from features that fingerprint its place, which is exactly what does not carry over to a place without a sensor.
        y_tr = d.U[tr.i.to_numpy(), tr.t.to_numpy()] - (tr.base_u + tr.kappa).to_numpy() - o["offset"][tr.i.to_numpy()]
        y_te = d.U[te.i.to_numpy(), te.t.to_numpy()] - (te.base_u + te.kappa).to_numpy()
        m = gbm(True).fit(out["tr"], y_tr)
        corr = m.predict(out["te"])
        # shrink the correction by the factor that is best on the TRAINING rows' out-of-fold-like fit (least squares through the origin)
        ctr = m.predict(out["tr"])
        sc = float(np.clip((ctr * y_tr).sum() / max((ctr * ctr).sum(), 1e-9), 0.0, 1.0))
        df = pd.DataFrame({"t": te.t.to_numpy(), "sensor": te.i.to_numpy(), "obs": np.expm1(d.U[te.i.to_numpy(), te.t.to_numpy()]), "fold": k,
                           "krige": np.expm1(te.base_u + te.kappa), "ml": np.expm1(te.base_u + te.kappa + corr), "ml_half": np.expm1(te.base_u + te.kappa + 0.5 * corr),
                           "trend": np.expm1(te.base_u), "obs_deb": np.expm1(d.U[te.i.to_numpy(), te.t.to_numpy()] - off_all[te.i.to_numpy()]), "oracle": np.expm1(te.base_u + te.kappa + off_all[te.i.to_numpy()]), "month": d.month[te.t.to_numpy()], "city": d.city[te.t.to_numpy()]})
        df["rk"], df["rs"] = y_te, y_te - corr
        parts.append(df)
        print(f"fold {k}: train {len(tr):,} rows / test {len(te):,}; correction std {corr.std():.3f} vs residual std {y_te.std():.3f}; in-fold shrink {sc:.2f}; "
              f"residual SD after the ML step {np.std(y_te - corr):.3f}", file=sys.stderr)
        imp = pd.Series(np.abs(corr)).describe()
    ev = pd.concat(parts, ignore_index=True)
    cuts = {"all": np.ones(len(ev), bool), "heating": np.isin(ev.month, HEATING), "summer": ~np.isin(ev.month, HEATING),
            "polluted heating (city>15)": np.isin(ev.month, HEATING) & (ev.city > 15), "city>25": np.isin(ev.month, HEATING) & (ev.city > 25)}
    table = {}
    deb = {}
    for name, s_ in cuts.items():
        s = ev[s_]
        if s.t.nunique() < 5:
            continue
        sd = s.assign(obs=s.obs_deb)  # the truth with each sensor's persistent offset removed: the place's own hourly variation
        deb[name] = {"trend_mae": round(float((s.trend - sd.obs).abs().median()), 3), "krige_mae": round(float((s.krige - sd.obs).abs().median()), 3),
                     "ml_mae": round(float((s.ml - sd.obs).abs().median()), 3), "trend_rho": round(hour_rho(sd, "trend"), 3),
                     "krige_rho": round(hour_rho(sd, "krige"), 3), "ml_rho": round(hour_rho(sd, "ml"), 3)}
    print("\nagainst the offset-removed truth (what the place itself does, without the instrument's stable bias):", file=sys.stderr)
    print(pd.DataFrame(deb).T.to_string(), file=sys.stderr)
    for name, s_ in cuts.items():
        s = ev[s_]
        if s.t.nunique() < 5:
            continue
        table[name] = {"hours": int(s.t.nunique()), "rows": int(len(s)),
                       "trend_mae": round(float((s.trend - s.obs).abs().median()), 3), "krige_mae": round(float((s.krige - s.obs).abs().median()), 3),
                       "ml_mae": round(float((s.ml - s.obs).abs().median()), 3), "ml_half_mae": round(float((s.ml_half - s.obs).abs().median()), 3),
                       "oracle_mae": round(float((s.oracle - s.obs).abs().median()), 3), "krige_rho": round(hour_rho(s, "krige"), 3), "oracle_rho": round(hour_rho(s, "oracle"), 3), "ml_rho": round(hour_rho(s, "ml"), 3), "ml_half_rho": round(hour_rho(s, "ml_half"), 3)}
    print(f"\nfolds {a.folds}{' (no local inputs)' if a.no_local else ''}{' (no coordinates)' if a.no_coords else ''}: median abs error (ug/m3) and within-hour Spearman", file=sys.stderr)
    print(pd.DataFrame(table).T.to_string(), file=sys.stderr)
    (M.DATA / f"stack_eval{a.tag}.json").write_text(json.dumps(table, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
