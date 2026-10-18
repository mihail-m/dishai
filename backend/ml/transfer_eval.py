"""Cross-city transfer of the LOCAL model: train on other cities' sensor networks, score on Sofia sensors and weeks never seen.

    PYTHONPATH=. .venv/bin/python -m backend.ml.transfer_eval [--folds 2 3 4]      (needs `python -m backend.data_prep.city_networks`)

The local model is the weather-free one of pool_eval.py: kriged backbone (offsets removed, estimated per city on its own data) and trees on city-agnostic place
features (height above the surrounding 3 km, GHSL building height / volume / population at several radii, hour, month), the nearest neighbours' residuals and
distances, kriging variance. No coordinates, no city identifier. Raw sensor scale (no calibration, no terrain trend), one year of 2026 sample days everywhere.

Training sets compared on the same Sofia hidden sensors / weeks:  A. Sofia training rows only;  O. other cities only;  C. both.
Also reported: what a model trained on Sofia does in each other city (the reverse direction).
"""
import argparse
import sys
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from scipy.ndimage import uniform_filter
from scipy.stats import spearmanr

from ..cai.graph import DATA, to_xy
from . import old_sofia as O
from . import pool_eval as PE
from . import spatial_fit as S

SEED = 7
HEATING = S.HEATING
CITY_TZ = {"Berlin": "Europe/Berlin", "Cologne": "Europe/Berlin", "Stuttgart": "Europe/Berlin", "Hamburg": "Europe/Berlin", "Munich": "Europe/Berlin",
           "Skopje": "Europe/Skopje", "Plovdiv": "Europe/Sofia", "Budapest": "Europe/Budapest", "Sofia": "Europe/Sofia"}
BOXES = {"Berlin": (52.40, 52.62, 13.20, 13.60), "Cologne": (50.85, 51.05, 6.80, 7.15), "Stuttgart": (48.70, 48.86, 9.05, 9.32), "Skopje": (41.93, 42.06, 21.35, 21.55),
         "Plovdiv": (42.10, 42.20, 24.68, 24.82), "Budapest": (47.40, 47.60, 18.95, 19.20), "Hamburg": (53.45, 53.70, 9.80, 10.20), "Munich": (48.05, 48.25, 11.40, 11.75),
         "Sofia": (42.60, 42.80, 23.15, 23.55)}
CITY_DIR = DATA / "history" / "multicity" / "cities"
RADII = (150, 400, 1000)


def sample_buffered(path, lon, lat, radius_m: float, lat0: float) -> np.ndarray:
    """Mean of a raster over a square of side 2*radius around each point, sampled at the point (own scale for the latitude; NaN outside)."""
    import rasterio
    with rasterio.open(path) as src:
        a = src.read(1).astype("float32")
        tr = src.transform
        if src.nodata is not None and not np.isnan(src.nodata):
            a[a == src.nodata] = np.nan
    cell_m = 0.5 * (abs(tr.a) * 111_320 * np.cos(np.radians(lat0)) + abs(tr.e) * 110_574)
    size = max(1, int(round(2 * radius_m / cell_m)) | 1)
    fill = np.nanmedian(a)
    f = uniform_filter(np.where(np.isfinite(a), a, fill), size=size, mode="nearest")
    col = np.clip(((np.asarray(lon) - tr.c) / tr.a).astype(int), 0, a.shape[1] - 1)
    row = np.clip(((np.asarray(lat) - tr.f) / tr.e).astype(int), 0, a.shape[0] - 1)
    return f[row, col]


def place_features(name: str, ids_lonlat: tuple[np.ndarray, np.ndarray], lat0: float) -> pd.DataFrame:
    lon, lat = ids_lonlat
    d = DATA if name == "Sofia" else CITY_DIR / name
    out = {}
    elev = d / ("dem.tif")
    out["rel_elev"] = sample_buffered(elev, lon, lat, 30, lat0) - sample_buffered(elev, lon, lat, 1500, lat0)
    for k, f in (("ghs_height", "ghs_height.tif"), ("ghs_volume", "ghs_volume.tif"), ("ghs_pop", "ghs_pop.tif")):
        for r in RADII:
            out[f"{k}_{r}"] = sample_buffered(d / f, lon, lat, r, lat0)
    return pd.DataFrame(out)


def load_city(name: str):
    if name == "Sofia":
        s = pd.read_csv(O.HIST / "sensors.csv", usecols=["sensor", "lat", "lon", "hour_utc", "pm25"])
    else:
        s = pd.read_csv(CITY_DIR / name / "net.csv", usecols=["sensor", "lat", "lon", "hour_utc", "pm25"])
    tz = ZoneInfo(CITY_TZ[name])
    s["t"] = pd.to_datetime(s.hour_utc).dt.tz_localize("UTC").dt.tz_convert(tz).dt.tz_localize(None)
    box = BOXES[name]
    lat0, lon0 = 0.5 * (box[0] + box[1]), 0.5 * (box[2] + box[3])
    proj = lambda lon, lat: ((np.asarray(lon) - lon0) * 111_320 * np.cos(np.radians(lat0)), (np.asarray(lat) - lat0) * 110_574)
    n_in_box = s[s.lat.between(box[0], box[1]) & s.lon.between(box[2], box[3])].sensor.nunique()
    era = O.Era(name, s, box=box, xy_fn=proj, min_present=int(min(40, max(8, 0.4 * n_in_box))))
    d = PE.as_data(era)
    lon = lon0 + era.xy[:, 0] / (111_320 * np.cos(np.radians(lat0)))
    lat = lat0 + era.xy[:, 1] / 110_574
    return d, place_features(name, (lon, lat), lat0), era


def hour_rho(df, col):
    r = [spearmanr(g[col], g.obs).statistic for _, g in df.groupby("t") if len(g) >= 6 and g[col].nunique() > 1]
    return float(np.nanmean(r)) if r else float("nan")


def prepare_training(d, st, rng, hours=500):
    S.MIN_SENSORS_PER_HOUR = int(min(40, max(8, 0.4 * len(d.ids))))
    allc = np.arange(len(d.times))
    p, o, _ = S.fit_params(d, allc, rng)
    p.sd_scale = S.fit_sd_scale(d, p, o["offset"], o["post_var"], allc, rng)
    h = S.rng_pick(rng, S.pick_eval_hours(d, allc, rng), hours)
    return PE.rows_for(d, st, p, o["offset"], o["post_var"], h, np.ones(len(d.ids), bool))


def diagnostics(d, era) -> dict:
    """How the city's sensors behave: offset spread, whether close neighbours share offsets, variogram shape, kriging skill vs the plain city median."""
    allc = np.arange(len(d.times))
    o = S.estimate_offsets(d.E0, allc)
    has = o["n"] >= 100
    b, xy = o["offset"][has], d.xy[has]
    D = np.linalg.norm(xy[:, None] - xy[None], axis=-1)
    np.fill_diagonal(D, np.inf)
    nn = np.argmin(D, axis=1)
    near = D[np.arange(len(b)), nn] < 400
    hrs = np.random.default_rng(1).choice(allc, min(250, len(allc)), replace=False)
    h, g, cnt = S.semivariogram(d, d.E0 - o["offset"][:, None], hrs)
    vg = S.fit_exponential(h, g, cnt)
    return {"sensors": int(len(d.ids)), "hours": int(len(d.times)), "offset_sd": round(float(np.sqrt(o["tau2"])), 3),
            "nn_offset_corr": round(float(np.corrcoef(b[near], b[nn][near])[0, 1]), 3) if near.sum() > 8 else None, "n_close_pairs": int(near.sum()),
            "variogram_nugget_share": round(vg["nugget"] / (vg["nugget"] + vg["psill"]), 2), "range_km": round(vg["range_m"] / 1000, 1)}


def evaluate_city(name, d, st, rows_other, feats, rng, folds):
    """Hidden-sensor evaluation inside `name` (bi-fold over sensors and weeks); trees trained on A: this city's training rows, O: other cities, C: both."""
    S.MIN_SENSORS_PER_HOUR = int(min(40, max(8, 0.4 * len(d.ids))))  # small networks: fewer sensors are enough for an hour to count
    nf = int(min(5, max(2, len(d.ids) // 15)))
    sensor_fold = rng.permutation(len(d.ids)) % nf
    uw = np.unique(d.week)
    wf = dict(zip(uw, rng.permutation(len(uw)) % nf))
    wf_t = np.array([wf[x] for x in d.week])
    parts = []
    for k in range(nf):
        tr_cols, te_cols = np.flatnonzero(wf_t != k), np.flatnonzero(wf_t == k)
        if len(te_cols) < 40:
            continue
        p, o, _ = S.fit_params(d, tr_cols, rng)
        p.sd_scale = S.fit_sd_scale(d, p, o["offset"], o["post_var"], tr_cols, rng)
        tr = PE.rows_for(d, st, p, o["offset"], o["post_var"], S.rng_pick(rng, S.pick_eval_hours(d, tr_cols, rng), 500), sensor_fold != k)
        te = PE.rows_for(d, st, p, o["offset"], o["post_var"], S.pick_eval_hours(d, te_cols, rng), sensor_fold == k)
        f = [c for c in feats if c in te.columns]
        out = te[["t", "obs", "base"]].copy()
        out["fold"], out["month"], out["city"] = k, d.month[te.t.to_numpy()], d.city[te.t.to_numpy()]
        out["krige"] = np.expm1(te.base)
        for nm, X, y in (("A", tr[f], tr.y), ("O", rows_other[f], rows_other.y), ("C", pd.concat([tr[f], rows_other[f]]), pd.concat([tr.y, rows_other.y]))):
            out[nm] = np.expm1(te.base + PE.gbm().fit(X, y).predict(te[f]))
        parts.append(out)
    ev = pd.concat(parts, ignore_index=True)
    heat = np.isin(ev.month, HEATING)
    res = {}
    for cut, m in (("all", np.ones(len(ev), bool)), ("heating", heat), ("polluted", heat & (ev.city > 15))):
        g = ev[m]
        if g.t.nunique() < 5:
            continue
        r = {"rows": int(len(g))}
        for v in ("krige", "A", "O", "C"):
            r[f"{v}_mae"], r[f"{v}_rho"] = round(float((g[v] - g.obs).abs().median()), 3), round(hour_rho(g, v), 3)
        res[cut] = r
    return res


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cities", nargs="*", default=["Sofia", "Berlin", "Cologne", "Stuttgart", "Skopje", "Plovdiv", "Budapest"])
    a = ap.parse_args()
    rng = np.random.default_rng(SEED)
    cities, diag = {}, {}
    for c in a.cities:
        try:
            d, st, era = load_city(c)
        except (FileNotFoundError, ValueError) as e:
            print(f"{c}: skipped ({e!r})", file=sys.stderr)
            continue
        if len(d.ids) < 15 or len(d.times) < 200:
            print(f"{c}: too little data ({len(d.ids)} sensors, {len(d.times)} hours)", file=sys.stderr)
            continue
        cities[c] = (d, st)
        diag[c] = diagnostics(d, era)
        print(f"{c}: {diag[c]}", file=sys.stderr)
    print("\nPER-CITY SENSOR BEHAVIOUR (raw scale)", file=sys.stderr)
    print(pd.DataFrame(diag).T.to_string(), file=sys.stderr)

    all_rows = {c: prepare_training(d, st, rng) for c, (d, st) in cities.items()}
    feats = [c for c in next(iter(all_rows.values())).columns if c not in ("i", "t", "y", "obs", "base")]
    summary = {}
    for c, (d, st) in cities.items():
        other = pd.concat([r for k, r in all_rows.items() if k != c], ignore_index=True)
        summary[c] = evaluate_city(c, d, st, other, feats, rng, None)
        r = summary[c].get("polluted") or summary[c].get("heating") or summary[c]["all"]
        print(f"{c}: done (kriging {r['krige_mae']}/{r['krige_rho']}, own trees {r['A_mae']}/{r['A_rho']}, other cities {r['O_mae']}/{r['O_rho']}, both {r['C_mae']}/{r['C_rho']})", file=sys.stderr)
    for cut in ("all", "heating", "polluted"):
        rows = {c: v[cut] for c, v in summary.items() if cut in v}
        print(f"\nHIDDEN SENSORS, each city tested on its own unseen sensors and weeks, trees trained on other cities (O), on the city (A) or both (C): median error / within-hour Spearman [{cut}]", file=sys.stderr)
        print(pd.DataFrame(rows).T.to_string(), file=sys.stderr)
    import json
    (DATA / "transfer_eval.json").write_text(json.dumps({"diagnostics": diag, "results": summary}, indent=1, default=float))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
