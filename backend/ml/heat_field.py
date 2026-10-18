"""Local air temperature: how much warmer or cooler than the city a place is, by hour of the day and weather. -> data/heat_field.joblib

The old heat layer was a summer satellite snapshot (Landsat ground temperature) shown all year, with no hour, no weather and no forecast.
Here the temperature of a place is the city forecast plus a learned local offset:

    T(place, hour) = T_city(hour) + offset(place, weather of the hour)

The offset is learned from the ~220 citizen sensors that report temperature (BME280 / DHT22 / SHT3x next to the PM sensors, data/history/env.csv,
Nov 2024 - Oct 2026). Target: a sensor's reading minus the median of all sensors in that hour, so the sensor bias common to all units and the
city-wide forecast error cancel and only the spatial part is learned. Sensor type enters as a nuisance term in training (an unsheltered BMP280
reads 1-2 degrees warm) and is dropped when predicting.

Features, kept to the three that carry signal in held-out tests (docs/HEAT.md): terrain height, height above the ~3 km surroundings (cold air
pools in the valley at night) and GHSL building height within 1 km (urban heat island, the strongest by far). Tested and dropped: tree canopy and
NDVI (no gain), and Landsat surface temperature (rank correlation with measured temperature differences 0.05-0.12: it does not describe air).

Each feature acts through a handful of weather terms (daytime sunshine, night, calm clear night, sunshine x calm), so "built-up places stay warm on
calm clear nights" and "the mountain is cooler" come out as numbers, not assumptions. Ridge regression, so 220 places cannot be memorised.

    PYTHONPATH=. .venv/bin/python -m backend.ml.heat_field            # evaluate with sensors held out, then fit on all and save

Evaluation: 5-fold over SENSORS (a held-out sensor's own history is never seen), judged by how well the places of one hour are ordered
(Spearman, averaged over hours) and by the spatial error left after removing each hour's mean.
"""
import json
import sys

import joblib
import numpy as np
import pandas as pd
from scipy.stats import spearmanr
from sklearn.linear_model import Ridge
from sklearn.model_selection import GroupKFold

from ..cai.features import _load_raster
from ..cai.graph import DATA

HIST = DATA / "history"
MODEL_PATH = DATA / "heat_field.joblib"
REPORT_PATH = DATA / "heat_field_eval.json"

STATIC = ["elev", "rel_elev", "ghs_height_1000"]  # place features in the model
BASIS = ["const", "sun", "night", "calm_clear_night", "sun_calm"]  # weather terms each place feature acts through
ALPHA = 30.0
MIN_SENSORS = 25  # hours with fewer reporting sensors have no reliable city median
BBOX = (23.15, 42.60, 23.55, 42.80)


def load_rasters() -> dict:
    return {
        "dem": _load_raster(DATA / "dem.tif"),
        "dem_rel": _load_raster(DATA / "dem.tif", buffer_m=1500.0),
        "ghs_height_1000": _load_raster(DATA / "ghs_height.tif", buffer_m=1000.0),
        "lst": _load_raster(DATA / "lst.tif", buffer_m=60.0),
    }


def static_for(lon, lat, rasters: dict | None = None) -> np.ndarray:
    """(n, len(STATIC)) place features at points. Pass `rasters` (load_rasters()) to avoid reloading between calls."""
    r = rasters if rasters is not None else load_rasters()
    lon, lat = np.asarray(lon, dtype="float64"), np.asarray(lat, dtype="float64")
    elev = r["dem"].sample(lon, lat)
    return np.column_stack([elev, elev - r["dem_rel"].sample(lon, lat), r["ghs_height_1000"].sample(lon, lat)]).astype("float32")


def basis(radiation, cloud, wind10) -> np.ndarray:
    """(n, len(BASIS)): the weather terms. Radiation in W/m2, cloud in %, wind in km/h (Open-Meteo units, also in the forecast hours)."""
    rad = np.nan_to_num(np.asarray(radiation, dtype=float), nan=0.0)
    cl = np.nan_to_num(np.asarray(cloud, dtype=float), nan=50.0)
    wd = np.nan_to_num(np.asarray(wind10, dtype=float), nan=8.0)
    sun = np.clip(rad / 800.0, 0, 1.2)
    night = (rad < 5).astype(float)
    calm = 1 - np.clip(wd, 0, 12) / 12
    return np.column_stack([np.ones(len(rad)), sun, night, night * (1 - cl / 100) * calm, sun * calm])


def design(static: np.ndarray, B: np.ndarray, mu: np.ndarray, sd: np.ndarray) -> np.ndarray:
    """Baseline weather terms, then each standardised place feature times each weather term."""
    Z = (static - mu) / sd
    return np.column_stack([B] + [Z[:, [j]] * B for j in range(Z.shape[1])])


def predict_offset(model: dict, static: np.ndarray, radiation, cloud, wind10) -> np.ndarray:
    """Offset (degrees) of places with `static` features, for ONE hour's weather scalars (or arrays of the same length as the places)."""
    n = len(static)
    B = basis(np.broadcast_to(radiation, n), np.broadcast_to(cloud, n), np.broadcast_to(wind10, n))
    filled = np.where(np.isfinite(static), static, model["mu"])  # a place without terrain or building data gets the average place
    return (design(filled, B, model["mu"], model["sd"]) @ model["coef"]).astype("float32")


def load_observations():
    e = pd.read_csv(HIST / "env.csv")
    e["t"] = pd.to_datetime(e.pop("hour_utc")).dt.tz_localize("UTC").dt.tz_convert("Europe/Sofia").dt.tz_localize(None)
    e = e[(e.temp > -30) & (e.temp < 50)]
    loc = e.groupby("sensor").agg(lat=("lat", "median"), lon=("lon", "median"), type=("type", "first"))
    loc = loc[(loc.lon.between(BBOX[0], BBOX[2])) & (loc.lat.between(BBOX[1], BBOX[3]))]
    e = e[e.sensor.isin(loc.index)]
    T = e.pivot_table(index="t", columns="sensor", values="temp")
    T = T[T.notna().sum(axis=1) >= MIN_SENSORS]
    return loc, T.sub(T.median(axis=1), axis=0)


def build_table():
    loc, D = load_observations()
    w = pd.read_csv(HIST / "weather.csv", parse_dates=["time_local"]).set_index("time_local")
    D = D.loc[D.index.intersection(w.index)]
    w = w.reindex(D.index)
    rasters = load_rasters()
    S = static_for(loc.lon.to_numpy(), loc.lat.to_numpy(), rasters)
    keep = np.isfinite(S).all(axis=1)
    loc, S, D = loc[keep], S[keep], D.loc[:, keep]
    lst = rasters["lst"].sample(loc.lon.to_numpy(), loc.lat.to_numpy())
    a, b = np.polyfit(S[:, 0], lst, 1)
    vals = D.to_numpy()
    ti, si = np.nonzero(np.isfinite(vals))
    tab = pd.DataFrame({
        "t": D.index[ti], "sensor": loc.index.to_numpy()[si], "type": loc["type"].to_numpy()[si], "y": vals[ti, si].astype("float32"),
        "lst_detr": (lst - (a * S[:, 0] + b))[si].astype("float32"),
    })
    wx = w.iloc[ti]
    tab["radiation"], tab["cloud"], tab["wind10"] = wx.shortwave_radiation.to_numpy(), wx.cloud_cover.to_numpy(), wx.wind_speed_10m.to_numpy()
    return tab, S[si]


def fit(tab: pd.DataFrame, S: np.ndarray, tr: np.ndarray | None = None) -> dict:
    """Ridge on the interaction design plus sensor-type dummies. `tr` selects training rows."""
    idx = np.arange(len(tab)) if tr is None else tr
    mu, sd = S[idx].mean(0), S[idx].std(0)
    B = basis(tab.radiation.to_numpy()[idx], tab.cloud.to_numpy()[idx], tab.wind10.to_numpy()[idx])
    types = sorted(tab["type"].unique())
    T = np.column_stack([(tab["type"].to_numpy()[idx] == k).astype(float) for k in types])
    D = np.column_stack([design(S[idx], B, mu, sd), T])
    m = Ridge(alpha=ALPHA, fit_intercept=False).fit(D, tab.y.to_numpy()[idx])
    n = D.shape[1] - len(types)
    return {"mu": mu, "sd": sd, "coef": m.coef_[:n], "type_effect": dict(zip(types, m.coef_[n:].round(2))), "static": STATIC, "basis": BASIS}


def evaluate(tab: pd.DataFrame, S: np.ndarray) -> dict:
    pred = np.zeros(len(tab))
    for tr, te in GroupKFold(5).split(tab, groups=tab.sensor):
        mdl = fit(tab, S, tr)
        pred[te] = predict_offset(mdl, S[te], tab.radiation.to_numpy()[te], tab.cloud.to_numpy()[te], tab.wind10.to_numpy()[te])
    y = tab.y.to_numpy()
    t = pd.DatetimeIndex(tab.t)
    mo, hr, rad = t.month.to_numpy(), t.hour.to_numpy(), tab.radiation.to_numpy()
    lst = tab.lst_detr.to_numpy()
    old = lst - lst.mean()  # the old layer: Landsat anomaly (rank only, so the scale does not matter)
    night = (hr >= 22) | (hr <= 5)
    regimes = {
        "all hours": np.ones(len(y), bool),
        "summer afternoons (Jun-Aug, 12-17 h, sun)": np.isin(mo, [6, 7, 8]) & (hr >= 12) & (hr <= 17) & (rad > 400),
        "summer nights (Jun-Aug, 22-05 h)": np.isin(mo, [6, 7, 8]) & night,
        "clear calm winter nights (Dec-Feb)": np.isin(mo, [12, 1, 2]) & night & (tab.cloud.to_numpy() < 30) & (tab.wind10.to_numpy() < 8),
        "winter days (Dec-Feb, 11-15 h)": np.isin(mo, [12, 1, 2]) & (hr >= 11) & (hr <= 15),
    }
    out = {}
    for name, m in regimes.items():
        df = pd.DataFrame({"t": t[m], "y": y[m], "p": pred[m], "o": old[m]})
        gb = df.groupby("t")
        rho = lambda c: float(np.nanmean([spearmanr(g.y, g[c])[0] for _, g in gb if len(g) >= 30 and g[c].std() > 0]))
        yc, pc = df.y - gb.y.transform("mean"), df.p - gb.p.transform("mean")
        out[name] = {"rows": int(m.sum()), "rank_model": round(rho("p"), 2), "rank_old_landsat": round(rho("o"), 2),
                     "spatial_sd": round(float(np.sqrt(np.mean(yc**2))), 2), "spatial_rmse_model": round(float(np.sqrt(np.mean((yc - pc) ** 2))), 2)}
    return out


def main() -> int:
    tab, S = build_table()
    n_s = tab.sensor.nunique()
    print(f"{len(tab):,} sensor-hours, {n_s} sensors, {tab.t.nunique()} hours", file=sys.stderr)
    ev = evaluate(tab, S)
    for k, v in ev.items():
        print(f"{k:45s} {v}", file=sys.stderr)
    mdl = fit(tab, S)
    mdl.update(eval=ev, sensors=int(n_s), sensor_hours=int(len(tab)))
    # what the model says: offset of a dense centre, the valley floor and the mountain foot, in contrasting weather
    probe = np.array([[550, 0, 12], [550, 0, 0], [560, 10, 0], [900, 150, 0]], dtype="float32")
    for lab, (r_, c_, w_) in {"sunny afternoon": (700, 10, 6), "clear calm night": (0, 0, 2), "cloudy windy night": (0, 90, 20)}.items():
        mdl.setdefault("probe", {})[lab] = [round(float(v), 2) for v in predict_offset(mdl, probe, r_, c_, w_)]
        print(f"probe {lab}: centre/valley/outskirts/hill = {mdl['probe'][lab]}", file=sys.stderr)
    joblib.dump(mdl, MODEL_PATH, compress=3)
    REPORT_PATH.write_text(json.dumps({"eval": ev, "sensors": int(n_s), "sensor_hours": int(len(tab)), "type_effect": {k: float(v) for k, v in mdl["type_effect"].items()},
                                       "probe": mdl["probe"]}, indent=1))
    print(f"wrote {MODEL_PATH}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
