"""Back-test of the episode alert against the old stagnation-score rule (Phase 3).

    python -m backend.ml.backtest

A "window" is what the banner looks at: the 24 hours from an issue time (00, 06, 12, 18 local), with the live-sensor nowcast taken at
the issue time. An "episode" in the observations is the calibrated city median at or above 25 ug/m3 for at least 3 hours in a row.
The model's P(episode in the window) comes from the p10/p50/p90 level models refitted without the window's week (whole weeks held
out), so every probability is out of sample. The alert fires when P >= p*; p* maximises the Peirce skill score on the first heating
season only (2024/25), and everything is then reported on the later heating seasons (2025/26 and October 2026).

Baselines on the same windows: the current rule (any hour with stagnation score >= 60), persistence (the city is already above 25),
raw CAMS (>= 25 for 3 h in the window) and CAMS scaled by the live-sensor bias.

Caveat: the history is one week in seven plus the 14 worst-CAMS episodes, so episodes are over-represented against the climate
and false-alarm ratios are for this sample, not for a random winter day.
"""
import json
import sys
from datetime import datetime, timezone

import numpy as np
import pandas as pd
from sklearn.model_selection import GroupKFold

from ..alert import exceed_probability
from ..cai import config as C
from ..score import stagnation_score
from . import train as T
from .pm_model import MAX_LEAD_H, PmModel, hour_vector, now_vector

ISSUE_HOURS = (0, 6, 12, 18)
THR, MIN_H, H = C.PM_WARNING, C.ALERT_MIN_HOURS, 24
HEATING = (10, 11, 12, 1, 2, 3)
TRAIN_END = pd.Timestamp("2025-06-01")  # threshold chosen before this date (heating season 2024/25)
TEST_START = pd.Timestamp("2025-10-01")  # evaluated from here on (2025/26 and October 2026)
GRID = np.round(np.arange(0.05, 0.96, 0.05), 2)


def runs(flags: np.ndarray) -> list[tuple[int, int]]:
    out, i = [], 0
    while i < len(flags):
        if flags[i]:
            j = i
            while j + 1 < len(flags) and flags[j + 1]:
                j += 1
            out.append((i, j))
            i = j + 1
        else:
            i += 1
    return out


def city_series() -> tuple[pd.DataFrame, pd.DataFrame]:
    """(hourly table with weather + forecast inputs, calibrated city median per hour), both on local naive time."""
    hrs, sens = T.load_hours(), T.load_sensors()
    from . import calibration
    cal = calibration.get("pm25")
    if cal is not None:
        sens = T.calibrate_history(sens, hrs, cal)
    med = sens.groupby("time").pm25.transform("median")
    sens["pm"] = sens.pm25.clip(0, 4 * med + 10)
    g = sens.groupby("time").pm
    city = pd.DataFrame({"city": g.median(), "n": g.size()})
    return hrs, city[city.n >= T.MIN_SENSORS_PER_HOUR]


def build_windows(hrs: pd.DataFrame, city: pd.DataFrame) -> dict:
    hv, score = {}, {}
    for t, r in hrs.iterrows():
        h = {"pm25": r.cams25, "pm10": r.cams10, "blh": r.boundary_layer_height, "wind10m": r.wind_speed_10m, "wind925": r.wind_speed_925hPa,
             "t2m": r.temperature_2m, "t925": r.temperature_925hPa, "cloud": r.cloud_cover, "rh": r.relative_humidity_2m, "radiation": r.shortwave_radiation}
        if any(pd.isna(v) for v in h.values()):
            continue
        h["score"] = stagnation_score(h["blh"], h["wind10m"], h["t2m"], h["t925"])
        hv[t] = hour_vector(h, hour=t.hour, weekend=t.weekday() >= 5)
        score[t] = h["score"]
    cityv = city.city.to_dict()
    rows = []
    for t0 in city.index:
        if t0.hour not in ISSUE_HOURS:
            continue
        ts = [t0 + pd.Timedelta(hours=k) for k in range(H)]
        if not all(t in hv for t in ts):
            continue
        obs = np.array([cityv.get(t, np.nan) for t in ts])
        if np.isfinite(obs).sum() < 20:
            continue
        cams0 = hv[t0][0]
        rows.append({"t0": t0, "obs": obs, "score": np.array([score[t] for t in ts]),
                     "X": np.array([hv[t] + now_vector(min(k, MAX_LEAD_H), cityv[t0], cams0) for k, t in enumerate(ts)], dtype="float32"),
                     "cams": np.array([hv[t][0] for t in ts]), "city0": cityv[t0], "cams0": cams0})
    return rows


def observed_episode(obs: np.ndarray):
    flags = np.nan_to_num(obs, nan=0.0) >= THR
    r = [(a, b) for a, b in runs(flags) if b - a + 1 >= MIN_H]
    return r[0] if r else None


def skill(alert: np.ndarray, truth: np.ndarray) -> dict:
    a, o = alert.astype(bool), truth.astype(bool)
    hit, miss, fa, cn = (a & o).sum(), (~a & o).sum(), (a & ~o).sum(), (~a & ~o).sum()
    pod = hit / max(hit + miss, 1)
    pofd = fa / max(fa + cn, 1)
    return {"windows": int(len(a)), "observed": int(o.sum()), "alerts": int(a.sum()), "hits": int(hit), "misses": int(miss), "false_alarms": int(fa),
            "pod": round(float(pod), 3), "far": round(float(fa / max(hit + fa, 1)), 3), "pofd": round(float(pofd), 3),
            "peirce": round(float(pod - pofd), 3), "csi": round(float(hit / max(hit + miss + fa, 1)), 3)}


def lead_times(w: pd.DataFrame, alert: np.ndarray) -> dict:
    """Per distinct observed episode (a run of >= 3 h): was any window that contains its start alerting, and how long before the
    start was the earliest such window issued? One value per episode, hours."""
    eps = {}
    for i, r in w.iterrows():
        e = observed_episode(r.obs)
        if e is not None:
            eps.setdefault(r.t0 + pd.Timedelta(hours=e[0]), []).append(i)
    leads, missed = [], 0
    for start, _ in eps.items():
        cand = w[(w.t0 <= start) & (w.t0 + pd.Timedelta(hours=H) > start)]
        on = cand[alert[cand.index]]
        if len(on):
            leads.append((start - on.t0.min()) / pd.Timedelta(hours=1))
        else:
            missed += 1
    return {"episodes": len(eps), "missed_episodes": missed, "median_lead_h": float(np.median(leads)) if leads else None}


def main() -> int:
    hrs, city = city_series()
    rows = build_windows(hrs, city)
    w = pd.DataFrame({"t0": [r["t0"] for r in rows]})
    w["obs"], w["score"], w["cams"] = [r["obs"] for r in rows], [r["score"] for r in rows], [r["cams"] for r in rows]
    w["city0"], w["cams0"] = [r["city0"] for r in rows], [r["cams0"] for r in rows]
    X = np.stack([r["X"] for r in rows])  # (W, H, F)
    w["truth"] = [observed_episode(o) is not None for o in w.obs]
    w["in_progress"] = w.city0 >= THR
    w["heating"] = w.t0.dt.month.isin(HEATING)
    print(f"{len(w)} windows ({w.heating.sum()} in heating season), {int(w.truth.sum())} with an observed episode "
          f"({int((w.truth & w.heating).sum())} in heating season, {int((w.truth & ~w.in_progress).sum())} of all not already in progress)", file=sys.stderr)

    # out-of-fold probabilities: whole weeks held out
    df, _ = T.build_table(np.random.default_rng(T.SEED))
    df = df.reset_index(drop=True)
    dweek = ((df.day - df.day.min()).dt.days // 7).to_numpy()
    wk = ((w.t0.dt.normalize() - df.day.min()).dt.days // 7).to_numpy()
    n_f = X.shape[2]
    U = np.zeros((len(w), H, 3))
    folds = list(GroupKFold(5).split(np.arange(len(w)), groups=wk))
    for k, (_, te) in enumerate(folds):
        test_weeks = set(wk[te])
        ls = T.fit_level_set(df[~np.isin(dweek, list(test_weeks))])
        m = PmModel(ls["q50"], {}, {}, ls["q10"], ls["q90"], ls["conf_lo"], ls["conf_hi"])
        flat = X[te].reshape(-1, n_f)
        lo, mid, hi = m.level_quantiles(flat[:, : n_f - 4], flat[:, n_f - 4 :])
        U[te] = np.stack([lo, mid, hi], axis=-1).reshape(len(te), H, 3)
        print(f"fold {k}: {len(te)} windows", file=sys.stderr)
    p50 = np.expm1(U[:, :, 1])

    # error correlation between hours of one window (the common factor in exceed_probability)
    ok = np.isfinite(np.stack(w.obs.to_numpy()))
    y = np.log1p(np.nan_to_num(np.stack(w.obs.to_numpy())))
    sd = np.maximum(U[:, :, 2] - U[:, :, 0], 1e-3) / (2 * 1.2815515655446004)
    zres = np.where(ok, (y - U[:, :, 1]) / sd, np.nan)
    cors = {}
    for lag in (1, 2, 3, 6, 12):
        a, b = zres[:, :-lag].ravel(), zres[:, lag:].ravel()
        s = np.isfinite(a) & np.isfinite(b)
        cors[lag] = round(float(np.corrcoef(a[s], b[s])[0, 1]), 3)
    # AR(1): corr(h, h+k) = phi^k, so phi is the geometric mean of corr_k^(1/k) over the short lags
    phi = float(np.clip(np.exp(np.mean([np.log(max(cors[k], 1e-3)) / k for k in (1, 2, 3)])), 0.0, 0.98))
    print(f"standardised level error, correlation between hours k apart: {cors}; AR(1) phi {phi:.3f} implies "
          f"{ {k: round(phi ** k, 2) for k in cors} }", file=sys.stderr)
    corr = phi

    P = exceed_probability(U[:, :, 0], U[:, :, 1], U[:, :, 2], phi=phi)
    w["P"] = P
    sc = np.stack(w.score.to_numpy())
    cams = np.stack(w.cams.to_numpy())
    heur = (sc >= C.PM_WARNING * 0 + 60.0).any(axis=1)
    bias = np.clip(w.city0.to_numpy() / np.maximum(w.cams0.to_numpy(), C.CAMS_FLOOR), C.BIAS_MIN, C.BIAS_MAX)

    def run3(a):
        return np.array([any(b - s + 1 >= MIN_H for s, b in runs(r)) for r in a >= THR])

    det = run3(p50)  # Phase 0 rule: the median level itself at or above 25 for 3 h
    base_alerts = {"median level >= 25 for 3 h (Phase 0 rule)": det, "stagnation_score_ge_60 (old rule)": heur, "persistence (city already >= 25)": w.in_progress.to_numpy(),
                   "raw CAMS >= 25 for 3 h": run3(cams), "CAMS x sensor bias >= 25 for 3 h": run3(cams * bias[:, None])}

    train = (w.heating & (w.t0 < TRAIN_END)).to_numpy()
    test = (w.heating & (w.t0 >= TEST_START)).to_numpy()
    truth = w.truth.to_numpy()
    print(f"\nthreshold selection on heating season 2024/25: {int(train.sum())} windows, {int(truth[train].sum())} with an episode;"
          f" test (2025/26 + Oct 2026): {int(test.sum())} windows, {int(truth[test].sum())} with an episode", file=sys.stderr)
    sweep = {float(p): skill(P[train] >= p, truth[train])["peirce"] for p in GRID}
    best = max(sweep, key=lambda p: (sweep[p], -abs(p - 0.5)))
    print("Peirce skill by probability threshold (training period):", json.dumps(sweep), "-> p* =", best, file=sys.stderr)

    res = {"p_star": best, "p_watch": C.ALERT_P_WATCH, "phi": round(phi, 3), "level_error_corr_by_lag": cors, "train": {}, "test": {}, "test_not_in_progress": {}, "sweep_train": sweep,
           "sweep_test": {float(p): skill(P[test] >= p, truth[test]) for p in (0.2, 0.3, 0.4, 0.5, 0.6, 0.7)}}
    cases = {"model P >= p* (red)": P >= best, f"model P >= {C.ALERT_P_WATCH} (amber tier)": P >= C.ALERT_P_WATCH, **base_alerts}
    for label, sel, key in (("training period", train, "train"), ("test period", test, "test"), ("test, episodes not yet in progress at issue", test & ~w.in_progress.to_numpy(), "test_not_in_progress")):
        print(f"\n{label}: {int(sel.sum())} windows", file=sys.stderr)
        tab = {}
        for name, a in cases.items():
            s = skill(a[sel], truth[sel])
            ww = w[sel].reset_index(drop=True)
            lt = lead_times(ww, np.asarray(a)[sel])
            tab[name] = {**s, **lt}
        res[key] = tab
        print(pd.DataFrame(tab).T[["alerts", "hits", "misses", "false_alarms", "pod", "far", "pofd", "peirce", "episodes", "missed_episodes", "median_lead_h"]].to_string(), file=sys.stderr)

    # level accuracy on episode hours, in the units the app reports
    obs = np.stack(w.obs.to_numpy())
    ep_hours = np.isfinite(obs) & (obs >= THR)
    res["episode_hour_bias"] = {"hours": int(ep_hours.sum()), "mean_error_ugm3": round(float(np.nanmean((p50 - obs)[ep_hours])), 2),
                                "mean_error_ugm3_cams_ge_50": None}
    cams_ep = np.stack(w.cams.to_numpy()) >= 50
    s2 = ep_hours | (np.isfinite(obs) & cams_ep)
    if (np.isfinite(obs) & cams_ep).sum():
        res["episode_hour_bias"]["mean_error_ugm3_cams_ge_50"] = round(float(np.nanmean((p50 - obs)[np.isfinite(obs) & cams_ep])), 2)
    print("\nlevel error on observed episode hours (>= 25):", json.dumps(res["episode_hour_bias"]), file=sys.stderr)
    out = T.DATA / "history" / "backtest.json"
    out.write_text(json.dumps(res, indent=1, default=float))
    print(f"wrote {out}", file=sys.stderr)
    if "--no-save" not in sys.argv:  # the app reads these from the model file (config has the same values as fallbacks)
        model = PmModel.load()
        if model is not None:
            model.corr = phi
            model.meta["alert"] = {"p_red": best, "p_watch": C.ALERT_P_WATCH, "phi": round(phi, 3), "threshold": THR, "min_hours": MIN_H,
                                   "selected_on": "heating season 2024/25, Peirce skill", "backtest": {k: res[k] for k in ("train", "test", "test_not_in_progress")},
                                   "evaluated": datetime.now(timezone.utc).strftime("%Y-%m-%d")}
            model.save()
            print("saved alert settings into data/pm_model.joblib", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
