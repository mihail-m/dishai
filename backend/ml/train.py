"""Train and honestly evaluate the PM2.5 model against the hand-weighted physics formula.

    python -m backend.ml.train [--no-save]

Data: data/history/{sensors,weather,cams}.csv from `python -m backend.data_prep.history`.

Every training row is one (sensor, hour) with a random forecast lead of 0-47 h: the "issue time" is when the
live sensor snapshot would have been taken, so city_med/cams_issue are known then and the hour's CAMS/weather
are the forecast for the target hour. Evaluation:
  A. unseen sensors   - 5-fold GroupKFold over sensors (does it generalise to a street with no sensor?)
  B. unseen sensors and unseen future days - fit on the first 75 % of sampled days, score the last 25 %
Both are compared with the formula the app used before (and simpler baselines) on exactly the same rows.

The model has two parts (see pm_model.py): a gradient-boosted city-level model, cross-validated over week blocks, and
a tiny linear street-deviation term, cross-validated over sensors.
"""
import argparse
import json
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from scipy.stats import spearmanr
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.linear_model import HuberRegressor
from sklearn.model_selection import GroupKFold

from ..cai import config as C
from ..cai.features import compute_features
from ..cai.graph import DATA, get_net
from ..cai.score import HourCtx, hour_contexts, predict_pm
from ..score import stagnation_score
from .pm_model import (DEV_CLIP, FEATURES, LEVEL_FEATURES, MAX_LEAD_H, S_IDX, SPATIAL_COLS, STATIC_COLS, PmModel, hour_vector,
                       now_vector, spatial_design, static_matrix)

HIST = DATA / "history"
TZ = ZoneInfo("Europe/Sofia")
SEED = 7
MIN_SENSORS_PER_HOUR = 40
EPISODE_CAMS = 50.0  # hours where the CAMS forecast is at least this = a forecast inversion episode (selected on what the app knows, not on the outcome)


def load_hours() -> pd.DataFrame:
    w = pd.read_csv(HIST / "weather.csv", index_col="time_local")
    a = pd.read_csv(HIST / "cams.csv", index_col="time_local")
    df = w.join(a[["pm2_5", "pm10"]].rename(columns={"pm2_5": "cams25", "pm10": "cams10"}), how="inner")
    df.index = pd.to_datetime(df.index)
    return df


def load_sensors() -> pd.DataFrame:
    s = pd.read_csv(HIST / "sensors.csv")
    if "pm10" not in s:  # history fetched before PM10 was kept
        s["pm10"] = np.nan
    t = pd.to_datetime(s.pop("hour_utc")).dt.tz_localize("UTC").dt.tz_convert(TZ).dt.tz_localize(None)
    s["time"] = t
    return s


def build_table(rng: np.random.Generator) -> tuple[pd.DataFrame, np.ndarray]:
    hrs, sens = load_hours(), load_sensors()
    # outlier-robust city nowcast, same clip the live CaiModel applies
    med = sens.groupby("time").pm25.transform("median")
    sens["pm"] = sens.pm25.clip(0, 4 * med + 10)
    g = sens.groupby("time").pm
    city = pd.DataFrame({"city_med": g.median(), "n": g.size()})
    city = city[city.n >= MIN_SENSORS_PER_HOUR]

    loc = sens.groupby("sensor")[["lat", "lon"]].median()
    feats = compute_features(get_net("walk"))
    sf = feats.at(loc.lon.to_numpy(), loc.lat.to_numpy())
    stat = pd.DataFrame(static_matrix(feats, sf), index=loc.index, columns=STATIC_COLS)

    # per-hour feature vectors (weather + CAMS + stagnation score)
    hv = {}
    for t, r in hrs.iterrows():
        h = {"pm25": r.cams25, "pm10": r.cams10, "blh": r.boundary_layer_height, "wind10m": r.wind_speed_10m,
             "wind925": r.wind_speed_925hPa, "t2m": r.temperature_2m, "t925": r.temperature_925hPa, "cloud": r.cloud_cover,
             "rh": r.relative_humidity_2m, "radiation": r.shortwave_radiation}
        if any(pd.isna(v) for v in h.values()):
            continue
        h["score"] = stagnation_score(h["blh"], h["wind10m"], h["t2m"], h["t925"])
        hv[t] = hour_vector(h, hour=t.hour, weekend=t.weekday() >= 5)
    sens = sens[sens.time.isin(hv.keys()) & sens.time.isin(city.index) & sens.sensor.isin(stat.index)].reset_index(drop=True)

    # random issue time in the same sampled block (<= 47 h earlier, with a live nowcast available)
    issue_pool = city.index.values
    leads = np.zeros(len(sens), dtype=int)
    cityv = city.city_med.to_dict()
    pool_set = set(issue_pool)
    t_arr = sens.time.to_numpy()
    uniq = {t: [t - pd.Timedelta(hours=k) for k in range(MAX_LEAD_H + 1) if (t - pd.Timedelta(hours=k)) in pool_set] for t in pd.unique(sens.time)}
    issue_med = np.empty(len(sens))
    cams_iss = np.empty(len(sens))
    for i, t in enumerate(t_arr):
        t = pd.Timestamp(t)
        opts = uniq[t]
        pick = opts[rng.integers(len(opts))] if opts else None
        if pick is None or pick not in hv:
            leads[i] = -1
            continue
        leads[i] = int((t - pick) / pd.Timedelta(hours=1))
        issue_med[i] = cityv[pick]
        cams_iss[i] = hv[pick][0]
    keep = leads >= 0
    sens, leads, issue_med, cams_iss = sens[keep].reset_index(drop=True), leads[keep], issue_med[keep], cams_iss[keep]

    X_static = stat.loc[sens.sensor].to_numpy()
    X_hour = np.array([hv[t] for t in sens.time])
    X_now = np.array([now_vector(l, m, c) for l, m, c in zip(leads, issue_med, cams_iss)])
    X = pd.DataFrame(np.hstack([X_static, X_hour, X_now]), columns=FEATURES)
    meta = sens[["sensor", "time", "pm", "lat", "lon"]].copy()
    meta["day"] = meta.time.dt.normalize()
    out = pd.concat([meta, X], axis=1)
    print(f"{len(out):,} rows, {out.sensor.nunique()} sensors, {out.day.nunique()} days "
          f"({out.day.min().date()}..{out.day.max().date()}), median PM2.5 {out.pm.median():.1f}", file=sys.stderr)
    return out, feats.median_lst


def physics_baseline(df: pd.DataFrame) -> np.ndarray:
    """The pre-ML formula at each row's sensor, without the live-sensor residual (that is a separate stage)."""
    out = np.empty(len(df))
    bias = np.clip(df.city_med / np.maximum(df.cams_issue, C.CAMS_FLOOR), C.BIAS_MIN, C.BIAS_MAX).to_numpy()
    for i, (T, G, c, st, ap, hr, wk, b) in enumerate(zip(df["T"], df.G, df.cams25, df.stagnation, df.t2m, df.hour, df.weekend, bias)):
        ctx = HourCtx(time="", cams=c, stagnation=st, apparent=ap, daylight=0.0, hour=int(hr), weekend=bool(wk))
        out[i] = predict_pm(T, G, ctx, c * b, 0.0)
    return out


def within_hour_rho(df: pd.DataFrame, pred: np.ndarray) -> float:
    """Mean Spearman across sensors within each hour: does it rank streets right at a given moment?"""
    d = pd.DataFrame({"t": df.time.to_numpy(), "p": pred, "o": df.pm.to_numpy()})
    rs = [spearmanr(g.p, g.o).statistic for _, g in d.groupby("t") if len(g) >= 20 and g.p.nunique() > 1]
    return float(np.nanmean(rs)) if rs else float("nan")


def metrics(df: pd.DataFrame, pred: np.ndarray, rank_score: np.ndarray | None = None) -> dict:
    """rank_score: the street-only part of the prediction. Rows carry different random leads, so the full prediction
    differs between sensors of the same hour for reasons unrelated to the street; ranking uses the street part."""
    o, p = df.pm.to_numpy(), np.asarray(pred, dtype=float)
    ss = ((o - p) ** 2).sum()
    return {
        "mae": round(float(np.mean(np.abs(o - p))), 2),
        "median_abs_error": round(float(np.median(np.abs(o - p))), 2),
        "rmse": round(float(np.sqrt(np.mean((o - p) ** 2))), 2),
        "r2": round(float(1 - ss / ((o - o.mean()) ** 2).sum()), 3),
        "spearman_overall": round(float(spearmanr(o, p).statistic), 3),
        "spearman_within_hour": round(within_hour_rho(df, p if rank_score is None else rank_score), 3),
    }


def compare(df: pd.DataFrame, ml: np.ndarray, ml_dev: np.ndarray) -> dict:
    bias = np.clip(df.city_med / np.maximum(df.cams_issue, C.CAMS_FLOOR), C.BIAS_MIN, C.BIAS_MAX).to_numpy()
    phys = physics_baseline(df)
    out = {
        "ml": metrics(df, ml, ml_dev),
        "physics_formula": metrics(df, phys, np.log(phys + 1e-6) - np.log(df.cams25.to_numpy() * bias + 1e-6)),
        "cams_x_bias": metrics(df, df.cams25.to_numpy() * bias),
        "cams_raw": metrics(df, df.cams25.to_numpy()),
        "persistence_city_median": metrics(df, df.city_med.to_numpy()),
    }
    by_lead = {}
    for name, m in {"0-5h": (0, 5), "6-23h": (6, 23), "24-47h": (24, 47)}.items():
        sel = (df.lead >= m[0]) & (df.lead <= m[1])
        if sel.sum() > 1000:
            sub = df[sel]
            by_lead[name] = {"ml_mae": metrics(sub, ml[sel.to_numpy()])["mae"], "physics_mae": metrics(sub, physics_baseline(sub))["mae"],
                             "persistence_mae": metrics(sub, sub.city_med.to_numpy())["mae"], "rows": int(sel.sum())}
    out["by_lead"] = by_lead
    # the hours the app exists for: CAMS forecasts an episode. bias < 0 = under-predicts.
    ep = (df.cams25 >= EPISODE_CAMS).to_numpy()
    if ep.sum() > 500:
        preds = {"ml": ml, "physics_formula": phys, "cams_x_bias": df.cams25.to_numpy() * bias, "persistence_city_median": df.city_med.to_numpy()}
        out["episodes"] = {"rows": int(ep.sum()), **{k: {"mae": round(float(np.mean(np.abs(v[ep] - df.pm.to_numpy()[ep]))), 2),
                                                         "bias": round(float(np.mean(v[ep] - df.pm.to_numpy()[ep])), 2)} for k, v in preds.items()}}
    return out


def hour_median(df: pd.DataFrame) -> pd.Series:
    return df.groupby("time").pm.transform("median")


def level_X(df: pd.DataFrame) -> np.ndarray:
    """Level-model inputs. A column that is entirely missing (a feature not downloaded, or ablated) becomes a constant,
    which the trees never split on; scikit-learn's binning crashes on an all-NaN column."""
    X = df[LEVEL_FEATURES].copy()
    for c in X.columns[X.isna().all()]:
        X[c] = 0.0
    return X.to_numpy()


def fit_level(df: pd.DataFrame) -> HistGradientBoostingRegressor:
    """City-level model: one row per (hour, a few random leads); target is log(1 + city median) - log(1 + anchor)."""
    d = df.groupby("time").head(4)
    est = HistGradientBoostingRegressor(max_iter=250, learning_rate=0.05, max_leaf_nodes=12, min_samples_leaf=40,
                                        l2_regularization=2.0, random_state=SEED)
    return est.fit(level_X(d), np.log1p(hour_median(df).loc[d.index]) - np.log1p(anchors(d)))


def anchors(df: pd.DataFrame) -> np.ndarray:
    return df.cams25.where(df.cams25.notna(), df.city_med).to_numpy()


def level_predict(est, df: pd.DataFrame) -> np.ndarray:
    return np.expm1(np.log1p(anchors(df)) + est.predict(level_X(df))).clip(0)


def spatial_stats(df: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    one = df.groupby("sensor")[STATIC_COLS].first()
    raw = one[SPATIAL_COLS].to_numpy(dtype="float64").copy()
    raw[:, 0] = np.log1p(np.maximum(raw[:, 0], 0))
    return np.nanmean(raw, 0), np.nanstd(raw, 0)


def design(st, stag, mean, std):
    cols = [STATIC_COLS.index(c) for c in SPATIAL_COLS]
    raw = st[:, cols].copy()
    raw[:, 0] = np.log1p(np.maximum(raw[:, 0], 0))
    z = np.nan_to_num((raw - mean) / std)
    sf = np.nan_to_num(stag / 100.0)[:, None]
    return np.hstack([z, z * sf])


def fit_spatial(df: pd.DataFrame, alpha: float, mean, std) -> np.ndarray:
    """Huber regression of the street's log-deviation from the same-hour city median on [z, z*S/100]; returns coef."""
    d = df.sample(min(len(df), 150_000), random_state=SEED)
    y = np.log1p(d.pm.to_numpy()) - np.log1p(hour_median(df).loc[d.index].to_numpy())
    X = design(d[STATIC_COLS].to_numpy(dtype="float64"), d.stagnation.to_numpy(), mean, std)
    return HuberRegressor(alpha=alpha, fit_intercept=False, epsilon=1.35, max_iter=200).fit(X, y).coef_


def spatial_predict(df: pd.DataFrame, coef, mean, std) -> np.ndarray:
    X = design(df[STATIC_COLS].to_numpy(dtype="float64"), df.stagnation.to_numpy(), mean, std)
    return np.clip(X @ coef, -DEV_CLIP, DEV_CLIP)


ALPHAS = (0.0, 3e3, 3e4, 3e5)  # Huber L2 on ~150k rows: stronger = more shrinkage toward "no street effect"


def pick_alpha(df: pd.DataFrame) -> tuple[float, dict]:
    """Sensor-grouped CV of the within-hour rank skill for each shrinkage strength."""
    scores = {}
    for a in ALPHAS:
        oof = np.zeros(len(df))
        for tr, te in GroupKFold(5, shuffle=True, random_state=SEED).split(df, groups=df.sensor):
            m, sd = spatial_stats(df.iloc[tr])
            oof[te] = spatial_predict(df.iloc[te], fit_spatial(df.iloc[tr], a, m, sd), m, sd)
        scores[a] = round(within_hour_rho(df, oof), 3)
    best = max(scores, key=scores.get)
    return best, scores


def fit_all(df: pd.DataFrame, alpha: float) -> PmModel:
    mean, std = spatial_stats(df)
    coef = fit_spatial(df, alpha, mean, std)
    return PmModel(fit_level(df), {"mean": mean.tolist(), "std": std.tolist(), "coef": coef.tolist(), "alpha": alpha})


def predict_pm_ml(model: PmModel, df: pd.DataFrame) -> np.ndarray:
    lvl = level_predict(model.level, df)
    dev = spatial_predict(df, np.asarray(model.spatial["coef"]), np.asarray(model.spatial["mean"]), np.asarray(model.spatial["std"]))
    return lvl * np.exp(dev)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-save", action="store_true")
    ap.add_argument("--drop", default="", help="comma-separated feature columns to blank out (ablation), e.g. rh,t2m")
    args = ap.parse_args()
    df, _ = build_table(np.random.default_rng(SEED))
    df = df.reset_index(drop=True)
    for c in filter(None, args.drop.split(",")):
        df[c] = np.nan
    if args.drop:
        print(f"ABLATION: {args.drop} blanked", file=sys.stderr)

    alpha, alpha_scores = pick_alpha(df)
    print(f"\nspatial shrinkage: within-hour rank skill by alpha (sensor-grouped CV) {alpha_scores} -> {alpha}", file=sys.stderr)

    # A. unseen sensors (spatial part) and unseen weeks (level part), combined out-of-fold
    week = ((df.day - df.day.min()).dt.days // 7).to_numpy()
    lvl = np.zeros(len(df))
    for tr, te in GroupKFold(5).split(df, groups=week):
        lvl[te] = level_predict(fit_level(df.iloc[tr]), df.iloc[te])
    dev = np.zeros(len(df))
    for tr, te in GroupKFold(5, shuffle=True, random_state=SEED).split(df, groups=df.sensor):
        m, sd = spatial_stats(df.iloc[tr])
        dev[te] = spatial_predict(df.iloc[te], fit_spatial(df.iloc[tr], alpha, m, sd), m, sd)
    A = compare(df, lvl * np.exp(dev), dev)
    A["level_only"] = metrics(df, lvl)

    # B. unseen sensors AND unseen future days
    days = np.sort(df.day.unique())
    cut = days[int(len(days) * 0.75)]
    sensors = df.sensor.unique()
    test_sensors = set(np.random.default_rng(SEED).choice(sensors, len(sensors) // 4, replace=False))
    tr = df[(df.day < cut) & ~df.sensor.isin(test_sensors)]
    te = df[(df.day >= cut) & df.sensor.isin(test_sensors)].reset_index(drop=True)
    model_b = fit_all(tr, alpha)
    dev_b = spatial_predict(te, np.asarray(model_b.spatial["coef"]), np.asarray(model_b.spatial["mean"]), np.asarray(model_b.spatial["std"]))
    B = compare(te, predict_pm_ml(model_b, te), dev_b)
    B["level_only"] = metrics(te, level_predict(model_b.level, te))

    for label, res in (("A. unseen sensors + unseen weeks (5-fold)", A), ("B. unseen sensors + future days", B)):
        print(f"\n{label}", file=sys.stderr)
        print(pd.DataFrame({k: v for k, v in res.items() if isinstance(v, dict) and "mae" in v}).T.to_string(), file=sys.stderr)
        print("by lead:", json.dumps(res["by_lead"]), file=sys.stderr)
        print("episode hours (CAMS >= 50):", json.dumps(res.get("episodes")), file=sys.stderr)

    from sklearn.inspection import permutation_importance
    t = te.groupby("time").head(2)
    imp = permutation_importance(model_b.level, level_X(t), np.log1p(hour_median(te).loc[t.index]) - np.log1p(anchors(t)), n_repeats=5, random_state=SEED)
    importance = sorted(zip(LEVEL_FEATURES, imp.importances_mean.round(4)), key=lambda kv: -kv[1])
    print("\nlevel model permutation importance (drop in R2, log scale):", importance[:10], file=sys.stderr)

    # Heavy-episode check: the history never exceeds ~25 ug/m3, so verify the level scales with the forecast instead of capping.
    tt = te.groupby("time").head(1).copy()
    base = level_predict(model_b.level, tt)
    tt[["cams25", "cams10", "city_med", "cams_issue"]] *= 5
    scaled = level_predict(model_b.level, tt)
    stress = float(np.median(scaled / np.maximum(base, 0.1)))
    print(f"\nepisode stress test: CAMS and sensors x5 -> predicted city level x{stress:.1f} (a model that caps would give ~x1)", file=sys.stderr)
    A["stress_x5_ratio"] = round(stress, 2)

    final = fit_all(df, alpha)
    names = [f"{c}{suffix}" for suffix in ("", " x S/100") for c in ("z(log T)", "z(elev)")]
    print("spatial coefficients:", dict(zip(names, np.round(final.spatial["coef"], 3))), file=sys.stderr)
    if not args.no_save:
        final.meta = {
            "trained": datetime.now(timezone.utc).strftime("%Y-%m-%d"), "rows": int(len(df)), "sensors": int(df.sensor.nunique()),
            "days": int(df.day.nunique()), "span": [str(df.day.min().date()), str(df.day.max().date())],
            "algorithm": "HistGradientBoosting (city level) x linear street deviation (traffic, terrain height)",
            "eval_unseen_sensors": A, "eval_unseen_sensors_future_days": B,
            "importance": [[k, float(v)] for k, v in importance[:10]], "spatial_coef": dict(zip(names, final.spatial["coef"])),
            "alpha_scores": {str(k): v for k, v in alpha_scores.items()},
        }
        final.meta = json.loads(json.dumps(final.meta, default=float).replace("NaN", "null"))
        final.save()
        print(f"saved {DATA / 'pm_model.joblib'}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
