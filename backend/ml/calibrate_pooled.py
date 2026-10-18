"""Pooled calibration across many European cities, scored on Sofia (and leave-one-city-out for everyone).

    PYTHONPATH=. .venv/bin/python -m backend.ml.calibrate_pooled         (needs `python -m backend.data_prep.multicity pull`)

Same unit as ml/calibrate.py: the hourly median of the Sensor.Community PM sensors within 1.5 km of an EEA PM2.5 station (at least 3 sensors) against the
station's hourly value, with RH and temperature from Open-Meteo at the station, 2026 only. Model: the chosen Sofia form
log(1+ref) = c0 + c1 log(1+raw) + c2 RH + c3 T + c4 heating-season. Scored by whole weeks held out.

Fits compared on Sofia's Hipodruma and on every other city:
  sofia      fitted on Hipodruma only (what the app uses)
  others     fitted on all other cities (never saw Sofia)
  pooled     fitted on all cities, weeks held out
  own        fitted on the city itself (weeks held out): the best one could do locally
  raw        no calibration
"""
import sys

import numpy as np
import pandas as pd
from sklearn.model_selection import GroupKFold

from ..cai.graph import DATA
from .calibrate import fit_candidate, report
from .calibration import Calibration

MC = DATA / "history" / "multicity"
TERMS = ("rh", "temp", "heating")


def load() -> pd.DataFrame:
    st = pd.read_csv(MC / "candidate_stations.csv")
    sc = pd.read_csv(MC / "sc_sensors_24h.csv")
    s = pd.read_csv(MC / "sensors_hourly.csv")
    s["t"] = pd.to_datetime(s.hour_utc)
    ref = pd.read_csv(MC / "eea_pm25.csv")
    ref["t"] = pd.to_datetime(ref.hour_utc)
    wx = pd.read_csv(MC / "weather.csv")
    wx["t"] = pd.to_datetime(wx.hour_utc)
    wx = wx.set_index(["station", "t"])[["temp", "rh"]]
    pos = s.groupby("sensor")[["lat", "lon"]].median()
    rows = []
    for _, r in st.iterrows():
        code = r.AirQualityStationEoICode
        rr = ref[ref.station == code].set_index("t").value
        if rr.empty:
            continue
        d = np.hypot((pos.lat - r.lat) * 111_320, (pos.lon - r.lon) * 111_320 * np.cos(np.radians(r.lat)))
        near = pos.index[d <= 1500]
        m = s[s.sensor.isin(near) & s.pm25.between(0, 500)].groupby("t").pm25.agg(raw="median", n="size")
        m = m[m.n >= 3]
        j = m.join(rr.rename("ref"), how="inner").join(wx.loc[code], how="left").dropna()
        j["station"], j["country"], j["n_sensors"], j["lat"], j["lon"] = code, r.country, len(near), r.lat, r.lon
        rows.append(j.reset_index())
    d = pd.concat(rows, ignore_index=True)
    d["ref"] = d.ref.clip(lower=0.0)  # a few stations report small negative values near zero
    d["month"] = d.t.dt.month
    d["block"] = ((d.t.dt.normalize() - pd.Timestamp("2026-01-01")).dt.days // 7).to_numpy()
    # a city = stations within 30 km of each other
    xy = np.column_stack([d.groupby("station").lat.first(), d.groupby("station").lon.first()])
    names = d.groupby("station").lat.first().index.to_numpy()
    city, lab = {}, 0
    for i, n in enumerate(names):
        if n in city:
            continue
        for j, m_ in enumerate(names):
            if np.hypot((xy[i, 0] - xy[j, 0]) * 111, (xy[i, 1] - xy[j, 1]) * 111 * np.cos(np.radians(xy[i, 0]))) < 30 and m_ not in city:
                city[m_] = lab
        lab += 1
    d["city"] = d.station.map(city)
    return d


def fit(d: pd.DataFrame) -> Calibration:
    return fit_candidate(d, TERMS)


def cv_predict(d: pd.DataFrame, train_mask: np.ndarray, test_mask: np.ndarray) -> np.ndarray:
    """Fit on d[train_mask]; predict d[test_mask]. If the two masks overlap, whole weeks of the test rows are removed from training."""
    tr, te = d[train_mask], d[test_mask]
    if (train_mask & test_mask).any():
        out = np.zeros(len(te))
        for a, b in GroupKFold(min(6, te.block.nunique())).split(te, groups=te.block):
            blocks = te.block.iloc[b].unique()
            cal = fit(tr[~tr.block.isin(blocks)])
            out[b] = cal.apply(te.raw.iloc[b].to_numpy(), te.rh.iloc[b].to_numpy(), te.temp.iloc[b].to_numpy(), te.month.iloc[b].to_numpy())
        return out
    cal = fit(tr)
    return cal.apply(te.raw.to_numpy(), te.rh.to_numpy(), te.temp.to_numpy(), te.month.to_numpy())


def mae(p, y):
    return float(np.mean(np.abs(np.asarray(p) - np.asarray(y))))


def main() -> int:
    d = load()
    sofia = d.station == "BG0050A"
    print(f"{d.station.nunique()} stations, {d.city.nunique()} cities, {len(d):,} station-hours; Sofia (BG0050A): {int(sofia.sum())} hours, {int(d[sofia].n_sensors.iloc[0])} sensors; "
          f"reference > 25: {int((d.ref > 25).sum()):,} hours, > 50: {int((d.ref > 50).sum()):,} (Sofia: {int((d[sofia].ref > 25).sum())}, {int((d[sofia].ref > 50).sum())})", file=sys.stderr)
    per = d.groupby("country").agg(stations=("station", "nunique"), hours=("t", "size"), sensors=("n_sensors", "sum"), ref_med=("ref", "median"), raw_med=("raw", "median"), ref_p95=("ref", lambda x: x.quantile(.95)))
    per["ratio_med"] = (per.raw_med / per.ref_med).round(2)
    print(per.round(1).to_string(), file=sys.stderr)

    res = {}
    # ---- scored on Sofia
    te = sofia.to_numpy()
    y = d.ref[te].to_numpy()
    sets = {"raw": d.raw[te].to_numpy(),
            "sofia (own station, weeks held out)": cv_predict(d, te, te),
            "other cities only (never saw Sofia)": cv_predict(d, ~te, te),
            "all cities pooled, Sofia weeks held out": cv_predict(d, np.ones(len(d), bool), te)}
    print("\nSCORED ON SOFIA (Hipodruma), whole weeks held out", file=sys.stderr)
    tab = {}
    for name, p in sets.items():
        rep = report(d[te].reset_index(drop=True), p)
        hi = rep["by_ref_level"]
        tab[name] = {"mae": rep["mae"], "bias": rep["bias"], "r": rep["r"], "ratio_RH_min": min(v["median_ratio"] for v in rep["ratio_by_rh"].values()),
                     "ratio_RH_max": max(v["median_ratio"] for v in rep["ratio_by_rh"].values()), "mae>25": round(float(np.mean(np.abs(p - y)[y > 25])), 2),
                     "bias>25": round(float(np.mean((p - y)[y > 25])), 2)}
    print(pd.DataFrame(tab).T.to_string(), file=sys.stderr)
    res["sofia"] = tab

    # ---- leave-one-city-out on everyone
    print("\nLEAVE ONE CITY OUT: MAE (ug/m3) per city: raw | fitted on the other cities | fitted on Sofia only | fitted on the city itself (weeks held out)", file=sys.stderr)
    rows = []
    sofia_cal = fit(d[sofia])
    for c, g in d.groupby("city"):
        m = (d.city == c).to_numpy()
        if m.sum() < 400:
            continue
        p_other = cv_predict(d, ~m, m)
        p_sofia = sofia_cal.apply(g.raw.to_numpy(), g.rh.to_numpy(), g.temp.to_numpy(), g.month.to_numpy()) if c != d[sofia].city.iloc[0] else np.full(len(g), np.nan)
        p_own = cv_predict(d, m, m)
        rows.append({"city": c, "country": g.country.iloc[0], "stations": g.station.nunique(), "hours": len(g), "ref_med": round(g.ref.median(), 1), "raw": round(mae(g.raw, g.ref), 2),
                     "others": round(mae(p_other, g.ref), 2), "sofia_only": round(mae(p_sofia, g.ref), 2) if np.isfinite(p_sofia).all() else None, "own": round(mae(p_own, g.ref), 2)})
    t = pd.DataFrame(rows).sort_values("hours", ascending=False)
    print(t.to_string(index=False), file=sys.stderr)
    res["leave_one_city_out"] = t.to_dict("records")
    ok = t.dropna(subset=["sofia_only"])
    print(f"\nweighted by hours, over cities other than Sofia: raw {np.average(ok.raw, weights=ok.hours):.2f} | other cities {np.average(ok.others, weights=ok.hours):.2f} | "
          f"Sofia-only fit {np.average(ok.sofia_only, weights=ok.hours):.2f} | own {np.average(ok.own, weights=ok.hours):.2f}", file=sys.stderr)
    cal = fit(d)
    print("\npooled coefficients:", {k: round(v, 3) for k, v in cal.coef.items()}, " | Sofia-only coefficients:", {k: round(v, 3) for k, v in sofia_cal.coef.items()}, file=sys.stderr)
    import json
    (DATA / "calibrate_pooled.json").write_text(json.dumps({**res, "pooled_coef": cal.coef, "sofia_coef": sofia_cal.coef}, indent=1, default=float))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
