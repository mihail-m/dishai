"""Fit and evaluate the sensor calibration against EEA reference stations (Phase 1).

    python -m backend.ml.calibrate [--no-save] [--radius 1500]

Unit of calibration: the hourly MEDIAN of the sensors within R metres of a reference station (at least MIN_NEAR of them), against
that station's hourly value. A median is what the city level uses, and it avoids fitting each sensor's private offset into the
global curve (those offsets are the job of the spatial stage). Sensors are 253/254 SDS011, so "sensor type" cannot be tested.

Evaluation is time-blocked: every sampled week is held out once (GroupKFold over weeks), and a last-quarter-of-the-year split
checks the future. Candidates, simplest first:
    a ratio       ref = k * raw
    b rh          log(1+ref) = c0 + c1 log(1+raw) + c2 RH
    c rh+temp     b + c3 T
    d +season     c + heating-season flag
    e gbm         HistGradientBoosting on raw, RH, T, month (an upper bound on what the inputs contain)
The saved model is the simplest one whose block-wise error is within one standard error of the best.
"""
import argparse
import json
import sys
from datetime import datetime, timezone

import numpy as np
import pandas as pd
from scipy.stats import spearmanr
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.linear_model import HuberRegressor
from sklearn.model_selection import GroupKFold

from ..cai.graph import DATA, to_xy
from .calibration import FEATURES, PATH, Calibration, design, save

HIST = DATA / "history"
RADIUS_M = 1500.0
MIN_NEAR = 3  # sensors needed in the disc for an hour to count
REF_PM25 = ("BG0050A",)  # BG0070A is excluded: see EXCLUDED
EXCLUDED = {"BG0070A": "no daily cycle (12-13 ug/m3 all day), level steps from ~16 to ~8 in June, r=0.25 with Hipodruma while the "
                       "PM10 stations correlate 0.63-0.79 with each other; no sensor within 2 km to check it against"}
RH_BINS = [0, 50, 60, 70, 80, 90, 101]
LEVEL_BINS = [(0, 10), (10, 25), (25, 50), (50, 1e9)]
CANDIDATES = {"a_ratio": (), "b_rh": ("rh",), "c_rh_temp": ("rh", "temp"), "d_season": ("rh", "temp", "heating"), "e_gbm": None}
SEED = 7


def utc_weather() -> pd.DataFrame:
    w = pd.read_csv(HIST / "weather.csv", parse_dates=["time_local"])
    t = w.time_local.dt.tz_localize("Europe/Sofia", ambiguous="NaT", nonexistent="NaT").dt.tz_convert("UTC").dt.tz_localize(None)
    return w.assign(t=t).dropna(subset=["t"]).set_index("t")[["relative_humidity_2m", "temperature_2m"]].rename(
        columns={"relative_humidity_2m": "rh", "temperature_2m": "temp"})


def colocation(sensors: pd.DataFrame, ref: pd.DataFrame, radius: float, col: str = "pm25", stations=None, min_near: int = MIN_NEAR,
               block_days: int = 7) -> pd.DataFrame:
    """One row per (station, hour): median of `col` over the sensors within `radius` m, the reference value, RH and temperature."""
    wx = utc_weather()
    loc = sensors.groupby("sensor")[["lat", "lon"]].median()
    loc["x"], loc["y"] = to_xy(loc.lon.to_numpy(), loc.lat.to_numpy())
    rows = []
    for st, g in ref.groupby("station"):
        if stations is not None and st not in stations:
            continue
        sx, sy = to_xy(g.lon.iloc[0], g.lat.iloc[0])
        near = loc.index[np.hypot(loc.x - sx, loc.y - sy) <= radius]
        if len(near) < min_near:
            continue
        m = sensors[sensors.sensor.isin(near)].dropna(subset=[col]).groupby("t")[col].agg(raw="median", n="size")
        m = m[m.n >= min_near]
        j = m.join(g.set_index("t").value.rename("ref"), how="inner").join(wx, how="left")
        j["station"], j["n_sensors"] = st, len(near)
        rows.append(j.reset_index())
    out = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()
    if len(out):
        out["month"] = out.t.dt.month
        day = out.t.dt.normalize()
        out["block"] = ((day - day.min()).dt.days // block_days).to_numpy()
    return out


def load_inputs(sensor_file: str = "sensors") -> tuple[pd.DataFrame, pd.DataFrame]:
    s = pd.read_csv(HIST / f"{sensor_file}.csv")
    s["t"] = pd.to_datetime(s.pop("hour_utc"))
    r = pd.read_csv(HIST / "reference.csv")
    r["t"] = pd.to_datetime(r.pop("hour_utc"))
    return s, r


def fit_candidate(d: pd.DataFrame, terms) -> Calibration:
    """Fit one candidate on the rows of `d` (columns raw, ref, rh, temp, month)."""
    rh_fill, t_fill = float(d.rh.median()), float(d.temp.median())
    if terms == ():
        k = float(np.exp(np.median(np.log(np.maximum(d.ref, 0.5) / np.maximum(d.raw, 0.5)))))
        return Calibration("ratio", {"k": k}, rh_fill, t_fill)
    X = design(d.raw.to_numpy(), d.rh.to_numpy(), d.temp.to_numpy(), d.month.to_numpy(), rh_fill, t_fill)
    cols = [FEATURES.index(t) for t in ("log_raw",) + tuple(terms)]
    y = np.log1p(d.ref.to_numpy())
    h = HuberRegressor(epsilon=1.35, alpha=1e-6, max_iter=500).fit(X[:, cols], y)
    coef = {"const": float(h.intercept_), **{n: float(c) for n, c in zip(("log_raw",) + tuple(terms), h.coef_)}}
    return Calibration("linear", coef, rh_fill, t_fill)


class Gbm:
    """Evaluation-only upper bound: not saved, so it has no serving format."""
    def __init__(self, d: pd.DataFrame):
        self.rh_fill, self.t_fill = float(d.rh.median()), float(d.temp.median())
        self.est = HistGradientBoostingRegressor(max_iter=150, learning_rate=0.05, max_leaf_nodes=6, min_samples_leaf=40,
                                                 l2_regularization=5.0, random_state=SEED).fit(self._x(d), np.log1p(d.ref.to_numpy()))

    def _x(self, d):
        X = design(d.raw.to_numpy(), d.rh.to_numpy(), d.temp.to_numpy(), d.month.to_numpy(), self.rh_fill, self.t_fill)
        return np.column_stack([X[:, 0], X[:, 1], X[:, 2], d.month.to_numpy()])

    def predict(self, d):
        return np.expm1(self.est.predict(self._x(d))).clip(0)


def predict(model, d: pd.DataFrame) -> np.ndarray:
    if isinstance(model, Gbm):
        return model.predict(d)
    return model.apply(d.raw.to_numpy(), d.rh.to_numpy(), d.temp.to_numpy(), d.month.to_numpy())


def oof(d: pd.DataFrame, terms) -> np.ndarray:
    """Out-of-fold predictions with whole weeks held out."""
    out = np.zeros(len(d))
    for tr, te in GroupKFold(n_splits=min(8, d.block.nunique())).split(d, groups=d.block):
        m = Gbm(d.iloc[tr]) if terms is None else fit_candidate(d.iloc[tr], terms)
        out[te] = predict(m, d.iloc[te])
    return out


def block_mae(d: pd.DataFrame, pred: np.ndarray) -> pd.Series:
    return pd.Series(np.abs(pred - d.ref.to_numpy())).groupby(d.block.to_numpy()).mean()


def report(d: pd.DataFrame, pred: np.ndarray) -> dict:
    """Held-out accuracy: overall, and the median pred/ref ratio per RH bin and per reference level (with counts)."""
    ref = d.ref.to_numpy()
    ratio = pred / np.maximum(ref, 1.0)
    out = {"n": int(len(d)), "mae": round(float(np.mean(np.abs(pred - ref))), 2), "bias": round(float(np.mean(pred - ref)), 2),
           "r": round(float(np.corrcoef(pred, ref)[0, 1]), 3), "spearman": round(float(spearmanr(pred, ref).statistic), 3)}
    rb = pd.cut(d.rh, RH_BINS)
    out["ratio_by_rh"] = {str(k): {"n": int(len(v)), "median_ratio": round(float(np.median(ratio[v.index.map(d.index.get_loc)])), 2)}
                          for k, v in d.groupby(rb, observed=True)}
    lv = {}
    for lo, hi in LEVEL_BINS:
        sel = (ref > lo) & (ref <= hi) if lo else (ref <= hi)
        if sel.sum():
            lv[f">{lo}" if hi > 1e8 else f"{lo}-{hi}"] = {"n": int(sel.sum()), "median_ratio": round(float(np.median(ratio[sel])), 2),
                                                          "bias": round(float(np.mean(pred[sel] - ref[sel])), 1)}
    out["by_ref_level"] = lv
    # Binning on the reference selects on the outcome, so a regression to the mean looks like a bias there. The fair question is
    # the other way round: when the calibrated value is X, what does the reference say? (median ref / pred per predicted bin)
    pv = {}
    for lo, hi in LEVEL_BINS:
        sel = (pred > lo) & (pred <= hi) if lo else (pred <= hi)
        if sel.sum():
            pv[f">{lo}" if hi > 1e8 else f"{lo}-{hi}"] = {"n": int(sel.sum()), "median_ref_over_pred": round(float(np.median(ref[sel] / np.maximum(pred[sel], 1.0))), 2)}
    out["by_pred_level"] = pv
    return out


def city_check(sens: pd.DataFrame, d: pd.DataFrame, terms) -> dict:
    """The acceptance question: the median of ALL calibrated sensors in the city (>= 40 reporting) against the reference station, on
    weeks the calibration never saw. The calibration was fitted on the sensors near the station, so this also tests transfer
    to the rest of the city (where the sensors' own offsets differ)."""
    wx = utc_weather()
    day0 = d.t.dt.normalize().min()
    a = sens[sens.t >= "2026-01-01"].join(wx, on="t")
    a["block"] = ((a.t.dt.normalize() - day0).dt.days // 7)
    ref = d.groupby("t").ref.first()
    preds = []
    for tr, te in GroupKFold(n_splits=min(8, d.block.nunique())).split(d, groups=d.block):
        m = fit_candidate(d.iloc[tr], terms)
        sub = a[a.block.isin(d.block.iloc[te].unique())]
        sub = sub.assign(cal=m.apply(sub.pm25.to_numpy(), sub.rh.to_numpy(), sub.temp.to_numpy(), sub.t.dt.month.to_numpy()))
        g = sub.groupby("t").agg(raw=("pm25", "median"), cal=("cal", "median"), n=("pm25", "size"), rh=("rh", "first"))
        preds.append(g[g.n >= 40])
    c = pd.concat(preds).join(ref.rename("ref"), how="inner").dropna()
    ratio = c.cal / np.maximum(c.ref, 1.0)
    out = {"n": int(len(c)), "mae_raw": round(float(np.mean(np.abs(c.raw - c.ref))), 2), "mae_cal": round(float(np.mean(np.abs(c.cal - c.ref))), 2),
           "bias_cal": round(float(np.mean(c.cal - c.ref)), 2), "r": round(float(np.corrcoef(c.cal, c.ref)[0, 1]), 3)}
    out["mae_reduction_pct"] = round(100 * (1 - out["mae_cal"] / out["mae_raw"]), 1)
    out["ratio_by_rh"] = {str(k): {"n": int(len(v)), "median_ratio": round(float(np.median(ratio[v.index])), 2)}
                          for k, v in c.groupby(pd.cut(c.rh, RH_BINS), observed=True)}
    out["by_ref_level"] = {f">{lo}" if hi > 1e8 else f"{lo}-{hi}": {"n": int(((c.ref > lo) & (c.ref <= hi)).sum()),
                                                                      "median_ratio": round(float(np.median(ratio[(c.ref > lo) & (c.ref <= hi)])), 2)}
                           for lo, hi in LEVEL_BINS if ((c.ref > lo) & (c.ref <= hi)).sum()}
    return out


def pm10_check(ref: pd.DataFrame) -> tuple[Calibration | None, dict]:
    """Same procedure against the PM10 stations, using the short PM10 sensor pull (autumn 2026 only, ~9 days): does the humidity
    behaviour look like PM2.5's? Few days and a narrow RH range, so this is a cross-check and a stop-gap for the PM10 comparison."""
    f = HIST / "sensors_pm10.csv"  # the full PM10 re-pull (make history --name sensors_pm10 --same-days); else the 9-day stop-gap
    if not f.exists():
        f = HIST / "sensors_pm10_partial.csv"
    if not f.exists():
        return None, {"error": "no PM10 sensor history"}
    s10 = pd.read_csv(f)
    s10["t"] = pd.to_datetime(s10.pop("hour_utc"))
    d = colocation(s10, ref[ref.pollutant == "PM10"], RADIUS_M, col="pm10", block_days=7 if f.name == "sensors_pm10.csv" else 2).dropna(subset=["raw", "ref", "rh", "temp"]).reset_index(drop=True)
    if len(d) < 200:
        return None, {"error": f"only {len(d)} PM10 station-hours"}
    out = {"rows": int(len(d)), "stations": sorted(d.station.unique()), "days": int(d.t.dt.normalize().nunique()),
           "rh_range": [int(d.rh.quantile(.05)), int(d.rh.quantile(.95))], "raw_mae": round(float(np.mean(np.abs(d.raw - d.ref))), 2)}
    for name, terms in (("a_ratio", ()), ("b_rh", ("rh",))):
        out[name] = report(d, oof(d, terms))
    m = fit_candidate(d, ("rh",))
    m.meta = {"rows": out["rows"], "days": out["days"], "stations": out["stations"], "rh_range": out["rh_range"],
              "note": "fitted on ~9 autumn days: indicative, narrow humidity range"}
    out["coef_b_rh"] = m.coef
    pm25 = fit_candidate(colocation(*load_inputs(), RADIUS_M, stations=REF_PM25).dropna(subset=["raw", "ref", "rh", "temp"]), ("rh",))
    out["pm25_coef_b_rh_same_form"] = pm25.coef
    m.sigma_log = round(float(1.4826 * np.median(np.abs(np.log1p(oof(d, ("rh",))) - np.log1p(d.ref.to_numpy())
                                                         - np.median(np.log1p(oof(d, ("rh",))) - np.log1p(d.ref.to_numpy()))))), 3)
    return m, out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-save", action="store_true")
    ap.add_argument("--radius", type=float, default=RADIUS_M)
    a = ap.parse_args()
    sens, ref = load_inputs()
    ref25 = ref[ref.pollutant == "PM2.5"]
    d = colocation(sens, ref25, a.radius, stations=REF_PM25).dropna(subset=["raw", "ref", "rh", "temp"]).reset_index(drop=True)
    print(f"{len(d)} station-hours, {d.block.nunique()} week blocks, {d.n_sensors.iloc[0]} sensors within {a.radius:.0f} m of "
          f"{', '.join(REF_PM25)}; excluded: {json.dumps(EXCLUDED)}", file=sys.stderr)

    results, preds = {}, {"raw (no calibration)": d.raw.to_numpy()}
    for name, terms in CANDIDATES.items():
        preds[name] = oof(d, terms)
    for name, p in preds.items():
        results[name] = report(d, p)
    table = pd.DataFrame({k: {m: v[m] for m in ("mae", "bias", "r", "spearman")} for k, v in results.items()}).T
    print("\nweek-blocked out-of-fold accuracy on the co-location rows\n" + table.to_string(), file=sys.stderr)

    # simplest within one standard error of the best, judged on per-block MAE
    names = list(CANDIDATES)
    bm = {k: block_mae(d, preds[k]) for k in names}
    best = min(names, key=lambda k: bm[k].mean())
    chosen = best
    for k in names:
        diff = bm[k] - bm[best]
        if diff.mean() <= diff.std(ddof=1) / np.sqrt(len(diff)) + 1e-12:
            chosen = k
            break
    print(f"\nbest by MAE: {best}; simplest within one standard error: {chosen}", file=sys.stderr)
    if chosen == "e_gbm":
        chosen = min(n for n in names if n != "e_gbm" and n != "e_gbm" and bm[n].mean() <= bm["e_gbm"].mean() * 1.05) if any(
            bm[n].mean() <= bm["e_gbm"].mean() * 1.05 for n in names if n != "e_gbm") else "d_season"
        print(f"GBM has no serving format; using {chosen}", file=sys.stderr)
    raw_mae = float(np.mean(np.abs(d.raw - d.ref)))
    cr = results[chosen]
    print(f"\nchosen {chosen}: MAE {cr['mae']} vs raw {raw_mae:.2f} ({100 * (1 - cr['mae'] / raw_mae):.0f}% lower)", file=sys.stderr)
    print("ratio (calibrated/ref) by RH bin:", json.dumps(cr["ratio_by_rh"]), file=sys.stderr)
    print("by reference level:", json.dumps(cr["by_ref_level"]), file=sys.stderr)
    print("by calibrated level (median ref / calibrated):", json.dumps(cr["by_pred_level"]), file=sys.stderr)

    # future check: fit on the first 75 % of blocks, score the rest
    cut = np.quantile(d.block.unique(), 0.75)
    tr, te = d[d.block <= cut], d[d.block > cut]
    fut = report(te, predict(fit_candidate(tr, CANDIDATES[chosen]), te)) if len(te) > 50 else None
    print("future quarter:", json.dumps({k: fut[k] for k in ("n", "mae", "bias", "r")}) if fut else None, file=sys.stderr)

    # sensitivity to the disc radius (the chosen model, week-blocked)
    sens_r = {}
    for R in (1000, 1500, 2000):
        dd = colocation(sens, ref25, R, stations=REF_PM25).dropna(subset=["raw", "ref", "rh", "temp"]).reset_index(drop=True)
        p = oof(dd, CANDIDATES[chosen])
        sens_r[R] = {"n_sensors": int(dd.n_sensors.iloc[0]), **{k: report(dd, p)[k] for k in ("n", "mae", "bias", "r")}, "raw_mae": round(float(np.mean(np.abs(dd.raw - dd.ref))), 2)}
    print("radius sensitivity:", json.dumps(sens_r), file=sys.stderr)

    # lag check on the final model: is the EEA hour-start shift right?
    lag = {}
    for k in (-2, -1, 0, 1, 2):
        ref_s = ref25[ref25.station == REF_PM25[0]].assign(t=lambda x: x.t + pd.Timedelta(hours=k))
        dl = colocation(sens, ref_s, a.radius, stations=REF_PM25)
        lag[k] = round(float(np.corrcoef(dl.raw, dl.ref)[0, 1]), 3)
    print("raw-vs-reference correlation if the reference is shifted by k hours:", json.dumps(lag), file=sys.stderr)

    city = city_check(sens, d, CANDIDATES[chosen])
    print("\ncity median of all calibrated sensors vs the reference, held-out weeks:", json.dumps(city), file=sys.stderr)
    cal10, pm10 = pm10_check(ref)
    print("\nPM10 cross-check:", json.dumps({k: v for k, v in pm10.items() if k not in ("a_ratio",)}, default=float), file=sys.stderr)

    final = fit_candidate(d, CANDIDATES[chosen])
    resid = np.log1p(preds[chosen]) - np.log1p(d.ref.to_numpy())
    final.sigma_log = round(float(1.4826 * np.median(np.abs(resid - np.median(resid)))), 3)
    final.meta = {
        "trained": datetime.now(timezone.utc).strftime("%Y-%m-%d"), "candidate": chosen, "stations": list(REF_PM25), "excluded": EXCLUDED,
        "radius_m": a.radius, "rows": int(len(d)), "sensors": int(d.n_sensors.iloc[0]), "span": [str(d.t.min()), str(d.t.max())],
        "raw_mae": round(raw_mae, 2), "cv": {k: {m: v[m] for m in ("mae", "bias", "r")} for k, v in results.items()},
        "chosen_report": cr, "future": fut, "radius_sensitivity": sens_r, "lag_check": lag, "city_check": city, "pm10_check": pm10,
        "attribution": "Reference data: European Environment Agency (EEA) e-reporting, CC BY 4.0",
    }
    print("\ncoefficients:", json.dumps(final.coef), "sigma_log", final.sigma_log, file=sys.stderr)
    if not a.no_save:
        save({"pm25": final, **({"pm10": cal10} if cal10 else {})})
        print(f"saved {PATH}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
