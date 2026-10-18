"""NO2 for Sofia with measured traffic: the pollutant road traffic actually moves here.

Why NO2 and not PM2.5: against measured vehicle counts (BGTOLL ring-road control points, backend/data_prep/bgtoll.py) the hourly PM2.5 and
PM10 at the EEA stations show no traffic signal, while NO2 does: weekday NO2 is ~1.3x weekend and so is traffic, and after weather and
hour of day a doubling of traffic goes with +10 to +29 % NO2 (docs/TRAFFIC.md).

    python -m backend.no2_model            # trains and writes data/no2_model.json (needs data/history/*, see data_prep)

Model: log(city NO2) = linear in log boundary-layer height, 10 m wind, temperature, humidity, log CAMS NO2 and log TYPICAL traffic
(measured hour-of-week volume share). Typical, not live, traffic: for NO2 the day-specific counts added nothing over the typical
profile, and the counts are only published in daily batches. Fitted on the 5-station urban mean (March to October 2026); week-blocked
cross-validation gives the skill, the residual quantiles give the band. It has not seen a heating season, so winter values are an
extrapolation and the card says so. Serving needs only numpy, json and httpx.
"""
import asyncio
import datetime as dt
import json
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
HIST = DATA / "history"
MODEL_PATH = DATA / "no2_model.json"
PROFILE_PATH = DATA / "bgtoll_profile.json"
URBAN = ("BG0040A", "BG0050A", "BG0052A", "BG0073A", "BG0079A")  # 3 background + 2 traffic stations; BG0070A is rural-near-city
FEATS = ("const", "lblh", "wind", "t2m", "rh", "lcams", "ltraffic")
FEATS_NO_CAMS = tuple(f for f in FEATS if f != "lcams")
WHO_24H = 25.0  # ug/m3, WHO 2021 guideline for the 24 h mean
MIN_TRAFFIC_SHARE = 0.03
CAMS_URL = ("https://air-quality-api.open-meteo.com/v1/air-quality?latitude=42.6977&longitude=23.3219"
            "&hourly=nitrogen_dioxide&forecast_days=4&timezone=Europe%2FSofia")


# --- features (shared by training and serving) ---------------------------------------------------------------------------


def log_traffic(profile: dict, hour: int, weekend: bool) -> float:
    return float(np.log(max(profile["weekend" if weekend else "weekday"][hour % 24], MIN_TRAFFIC_SHARE)))


def design(blh, wind, t2m, rh, cams, ltraffic, with_cams: bool = True) -> np.ndarray:
    cols = [np.ones(len(blh)), np.log(np.clip(blh, 30, None)), wind, t2m, np.asarray(rh) / 100.0]
    if with_cams:
        cols.append(np.log(np.clip(cams, 0.5, None)))
    cols.append(ltraffic)
    return np.column_stack(cols).astype(float)


# --- serving -------------------------------------------------------------------------------------------------------------


def load_model(path: Path = MODEL_PATH) -> dict | None:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def predict(model: dict, hours: list[dict], cams: dict[str, float] | None) -> list[dict]:
    """City-mean NO2 (ug/m3) with a p10-p90 band for forecast hours ({"time": local ISO, "blh", "wind10m", "t2m", "rh"})."""
    out = []
    q = model["resid_q"]
    for h in hours:
        if any(h.get(k) is None for k in ("blh", "wind10m", "t2m", "rh")):
            continue
        t = dt.datetime.strptime(h["time"], "%Y-%m-%dT%H:%M")
        c = (cams or {}).get(h["time"])
        with_cams = c is not None
        x = design(np.array([h["blh"]]), np.array([h["wind10m"]]), np.array([h["t2m"]]), np.array([h["rh"]]), np.array([c if with_cams else 1.0]),
                   np.array([log_traffic(model["traffic_profile"], t.hour, t.weekday() >= 5)]), with_cams)
        beta = np.array(model["coef"] if with_cams else model["coef_no_cams"])
        mu = float((x @ beta)[0])
        qq = q if with_cams else model["resid_q_no_cams"]
        out.append({"time": h["time"], "no2": round(float(np.exp(mu + qq["p50"])), 1), "p10": round(float(np.exp(mu + qq["p10"])), 1),
                    "p90": round(float(np.exp(mu + qq["p90"])), 1), "with_cams": with_cams})
    return out


def traffic_scenarios(model: dict) -> list[dict]:
    """What a citywide cut in traffic does to NO2, from the within-hour elasticity (doubling traffic = x 2^e)."""
    e = model["elasticity"]
    rows = []
    for cut in (0.2, 0.5):
        f = 1 - cut
        rows.append({"traffic_cut_pct": int(cut * 100), "no2_change_pct": round(100 * (f ** e["mean"] - 1), 1),
                     "range_pct": [round(100 * (f ** e["hi"] - 1), 1), round(100 * (f ** e["lo"] - 1), 1)]})
    return rows


_cams_cache: tuple[float, dict] | None = None


async def cams_no2() -> dict[str, float]:
    """CAMS NO2 forecast from Open-Meteo, local time -> ug/m3 (empty on failure; the model then runs without it)."""
    global _cams_cache
    if _cams_cache and time.time() - _cams_cache[0] < 3600:
        return _cams_cache[1]
    import httpx

    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(CAMS_URL)
            r.raise_for_status()
            h = r.json()["hourly"]
        res = {t: v for t, v in zip(h["time"], h["nitrogen_dioxide"]) if v is not None}
    except Exception:  # noqa: BLE001 - optional input
        return _cams_cache[1] if _cams_cache else {}
    _cams_cache = (time.time(), res)
    return res


def make_router(build_forecast):
    from fastapi import APIRouter

    r = APIRouter()

    @r.get("/api/no2")
    async def api_no2():
        """NO2 forecast (city mean, 48 h) from weather, CAMS NO2 and the measured typical traffic, plus the traffic-NO2 study."""
        model = load_model()
        if model is None:
            return {"enabled": False}
        fc = await build_forecast(None)
        i = fc["now_index"]
        hours = fc["hours"][i : i + 48]
        cams = await cams_no2()
        pred = predict(model, [{"time": h["time"], "blh": h.get("blh"), "wind10m": h.get("wind10m"), "t2m": h.get("t2m"), "rh": h.get("rh")} for h in hours], cams)
        day = [p["no2"] for p in pred[:24]]
        return {
            "enabled": True, "forecast": pred, "mean_24h": round(float(np.mean(day)), 1) if day else None, "who_24h": WHO_24H,
            "scenarios": traffic_scenarios(model), "study": model["study"], "traffic_profile": model["traffic_profile"], "eval": model["eval"], "elasticity": model["elasticity"],
            "trained": model["trained"], "range": model["range"], "n_hours": model["n_hours"], "stations": model["stations"],
            "caveats": model["caveats"],
        }

    return r


# --- training and the study ----------------------------------------------------------------------------------------------


def _cv_pred(X: np.ndarray, y: np.ndarray, week: np.ndarray) -> np.ndarray:
    pred = np.zeros_like(y)
    for k in np.unique(week):
        te = week == k
        beta = np.linalg.lstsq(X[~te], y[~te], rcond=None)[0]
        pred[te] = X[te] @ beta
    return pred


def _boot(X: np.ndarray, y: np.ndarray, week: np.ndarray, col: int, n: int = 300, seed: int = 0) -> tuple[float, float, float]:
    rng = np.random.default_rng(seed)
    weeks = np.unique(week)
    idx_by = {k: np.where(week == k)[0] for k in weeks}
    co = []
    for _ in range(n):
        idx = np.concatenate([idx_by[k] for k in rng.choice(weeks, len(weeks))])
        co.append(np.linalg.lstsq(X[idx], y[idx], rcond=None)[0][col])
    return float(np.mean(co)), float(np.percentile(co, 5)), float(np.percentile(co, 95))


def train() -> dict:
    import pandas as pd

    from .data_prep.bgtoll import PROFILE_POINTS

    profile = json.loads(PROFILE_PATH.read_text())
    t = pd.read_csv(HIST / "bgtoll_sofia_hourly.csv", parse_dates=["hour_utc"])
    t = t[t.scp.isin(PROFILE_POINTS)].copy()
    t["norm"] = t.vehicles_per_hour / t.groupby("scp").vehicles_per_hour.transform("median")
    tr = t.groupby("hour_utc").agg(idx=("norm", "mean"), n=("scp", "nunique")).reset_index()
    tr = tr[tr.n >= 8]
    no2 = pd.read_csv(HIST / "no2_reference.csv", parse_dates=["hour_utc"])
    piv = no2.pivot_table(index="hour_utc", columns="station", values="value", aggfunc="mean")
    urban = piv[list(URBAN)]
    city = urban.mean(axis=1).where(urban.notna().sum(axis=1) >= 3).rename("no2")
    w = pd.read_csv(HIST / "weather.csv", parse_dates=["time_local"])
    c = pd.read_csv(HIST / "cams.csv", parse_dates=["time_local"])[["time_local", "nitrogen_dioxide"]].rename(columns={"nitrogen_dioxide": "cams"})
    X = tr.merge(city.reset_index(), on="hour_utc").dropna()
    X["local"] = X.hour_utc.dt.tz_localize("UTC").dt.tz_convert("Europe/Sofia").dt.tz_localize(None)
    X = X.merge(w, left_on="local", right_on="time_local").merge(c, on="time_local").dropna(subset=["boundary_layer_height", "cams", "relative_humidity_2m"])
    X = X[X.no2 > 0].reset_index(drop=True)
    X["hr"], X["dow"] = X.local.dt.hour, X.local.dt.dayofweek
    X["we"] = (X.dow >= 5)
    week = X.local.dt.isocalendar().week.astype(int).to_numpy()
    y = np.log(X.no2.to_numpy())
    typ = np.array([log_traffic(profile, h, bool(we)) for h, we in zip(X.hr, X.we)])
    act = np.log(X.idx.clip(lower=MIN_TRAFFIC_SHARE).to_numpy())
    A = lambda cams, lt, wc=True: design(X.boundary_layer_height.to_numpy(), X.wind_speed_10m.to_numpy(), X.temperature_2m.to_numpy(), X.relative_humidity_2m.to_numpy(), cams, lt, wc)  # noqa: E731
    cams = X.cams.to_numpy()
    hourfe = np.column_stack([(X.hr == h).astype(float) for h in range(1, 24)] + [X.we.astype(float)])

    def rmse(p):
        return float(np.sqrt(np.mean((y - p) ** 2)))

    base = rmse(_cv_pred(np.ones((len(X), 1)), y, week))
    rows = {
        "constant": base,
        "CAMS NO2 only": rmse(_cv_pred(np.column_stack([np.ones(len(X)), np.log(np.clip(cams, 0.5, None))]), y, week)),
        "weather only": rmse(_cv_pred(A(cams, np.zeros(len(X)), False)[:, :-1], y, week)),
        "weather + CAMS NO2": rmse(_cv_pred(A(cams, np.zeros(len(X)))[:, :-1], y, week)),
        "weather + typical traffic": rmse(_cv_pred(A(cams, typ, False), y, week)),
        "weather + CAMS NO2 + typical traffic (served)": rmse(_cv_pred(A(cams, typ), y, week)),
        "weather + CAMS NO2 + live counts": rmse(_cv_pred(A(cams, act), y, week)),
        "weather + CAMS NO2 + hour-of-week dummies": rmse(_cv_pred(np.column_stack([A(cams, np.zeros(len(X)))[:, :-1], hourfe]), y, week)),
    }
    ev = {k: {"rmse_log": round(v, 3), "skill_pct": round(100 * (1 - v / base), 1)} for k, v in rows.items()}
    Xs, Xs0 = A(cams, typ), A(cams, typ, False)
    cvp = _cv_pred(Xs, y, week)
    cvp0 = _cv_pred(Xs0, y, week)
    q = lambda p: {"p10": round(float(np.percentile(y - p, 10)), 4), "p50": round(float(np.percentile(y - p, 50)), 4), "p90": round(float(np.percentile(y - p, 90)), 4)}  # noqa: E731
    ev["median_abs_error_ugm3"] = round(float(np.median(np.abs(np.exp(cvp + q(cvp)["p50"]) - X.no2))), 2)
    ev["median_abs_error_constant_ugm3"] = round(float(np.median(np.abs(X.no2.median() - X.no2))), 2)
    ev["band_coverage_p10_p90"] = round(float(np.mean((y >= cvp + q(cvp)["p10"]) & (y <= cvp + q(cvp)["p90"]))), 3)
    coef = np.linalg.lstsq(Xs, y, rcond=None)[0]
    coef0 = np.linalg.lstsq(Xs0, y, rcond=None)[0]

    # traffic elasticity: within hour of day (hour dummies + weekend), so it comes from day-to-day and weekday/weekend variation
    XF = np.column_stack([A(cams, np.zeros(len(X)))[:, :-1], hourfe, act])
    el = _boot(XF, y, week, XF.shape[1] - 1)

    # per-station elasticity and the PM comparison, same recipe
    def station_fit(series):
        d = X[["hour_utc"]].merge(series.rename("v").reset_index(), on="hour_utc", how="left")
        ok = d.v.notna().to_numpy() & (d.v.to_numpy() > 0)
        yy = np.log(d.v.to_numpy()[ok])
        F = np.column_stack([A(cams, np.zeros(len(X)), False)[:, :-1], hourfe, act])[ok]
        F0 = F[:, :-1]
        wk = week[ok]
        c3 = _boot(F, yy, wk, F.shape[1] - 1, n=200)
        F1 = np.column_stack([A(cams, np.zeros(len(X)), False)[:, :-1], act])[ok]
        F10 = F1[:, :-1]
        gain_nofe = 100 * (1 - np.sqrt(np.mean((yy - _cv_pred(F1, yy, wk)) ** 2)) / np.sqrt(np.mean((yy - _cv_pred(F10, yy, wk)) ** 2)))
        gain_fe = 100 * (1 - np.sqrt(np.mean((yy - _cv_pred(F, yy, wk)) ** 2)) / np.sqrt(np.mean((yy - _cv_pred(F0, yy, wk)) ** 2)))
        return {"elasticity": round(c3[0], 3), "lo": round(c3[1], 3), "hi": round(c3[2], 3), "cv_gain_pct_no_hour_fe": round(float(gain_nofe), 1),
                "cv_gain_pct_with_hour_fe": round(float(gain_fe), 1), "hours": int(ok.sum())}

    stations = []
    kinds = pd.read_csv(HIST / "no2_stations.csv").set_index("station")
    for st in URBAN:
        stations.append({"station": st, "type": kinds.loc[st].station_type, **station_fit(piv[st])})
    ref = pd.read_csv(HIST / "reference.csv", parse_dates=["hour_utc"])
    pm = {}
    for name, pol, sts in (("pm25", "PM2.5", ("BG0050A", "BG0070A")), ("pm10", "PM10", ("BG0040A", "BG0050A", "BG0052A", "BG0073A", "BG0079A"))):
        s = ref[(ref.pollutant == pol) & ref.station.isin(sts)].pivot_table(index="hour_utc", columns="station", values="value", aggfunc="mean").mean(axis=1)
        pm[name] = station_fit(s)

    wkday = (X.dow < 5).to_numpy()
    prof_no2 = X.groupby(["we", "hr"]).no2.mean().unstack(0)
    study = {
        "weekday_weekend_ratio": {"no2": round(float(X.no2[wkday].mean() / X.no2[~wkday].mean()), 2), "traffic": round(float(X.idx[wkday].mean() / X.idx[~wkday].mean()), 2)},
        "diurnal_no2": {"weekday": [round(float(v), 1) for v in prof_no2[False]], "weekend": [round(float(v), 1) for v in prof_no2[True]]},
        "stations": stations, "pm": pm,
        "reading": "NO2 follows measured traffic (weekday/weekend ratio and elasticity); PM2.5 and PM10 show no usable traffic signal in these months.",
    }
    model = {
        "trained": dt.datetime.now().strftime("%Y-%m-%d"), "range": [str(X.local.min()), str(X.local.max())], "n_hours": int(len(X)),
        "target": "mean NO2 of 5 urban EEA stations (3 background, 2 traffic), ug/m3", "stations": list(URBAN),
        "feats": list(FEATS), "coef": [float(v) for v in coef], "coef_no_cams": [float(v) for v in coef0],
        "resid_q": q(cvp), "resid_q_no_cams": q(cvp0), "traffic_profile": {"weekday": profile["weekday"], "weekend": profile["weekend"]},
        "elasticity": {"mean": round(el[0], 3), "lo": round(el[1], 3), "hi": round(el[2], 3), "note": "log NO2 per log traffic, within hour of day; 90 % block-bootstrap interval"},
        "eval": ev, "study": study,
        "caveats": ["Fitted on March to October 2026 only: no heating season, so winter NO2 is an extrapolation.",
                    "Traffic is counted on the ring road and its approaches (BGTOLL), not on city streets: this is a city-wide level, not a street map.",
                    "Typical traffic by hour of week; public holidays are not treated as Sundays.",
                    "The weekday/weekend and elasticity results are associations with weather and hour of day controlled, not proof of cause."],
    }
    MODEL_PATH.write_text(json.dumps(model, indent=1))
    return model


if __name__ == "__main__":
    m = train()
    print(json.dumps({"n_hours": m["n_hours"], "eval": m["eval"], "elasticity": m["elasticity"], "weekday_weekend": m["study"]["weekday_weekend_ratio"],
                      "pm": m["study"]["pm"], "stations": m["study"]["stations"]}, indent=1), file=sys.stderr)
