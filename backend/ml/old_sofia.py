"""The Kaggle Sofia 2017-2019 sensors against today's: do the findings about sensor offsets and spatial structure replicate in another era?

    PYTHONPATH=. .venv/bin/python -m backend.ml.old_sofia            (needs data/history/sofia1719/*.csv from kaggle_sofia.py)

Both eras are treated identically and on the RAW sensor scale (no calibration, no terrain trend), so that only the sensors' behaviour is compared:
residual e[s,t] = log1p(PM2.5 raw) - log1p(median of the sensors that hour); offset_s = mean of e over the sensor's hours (shrunk as in spatial_fit).
Reports (1) the spread of offsets, (2) how neighbouring sensors' offsets correlate, (3) the variogram of the hour-to-hour residual after the offsets,
(4) for the sensors present in both eras (the same physical instruments, up to seven years apart) how stable the offset is.
"""
import json
import sys

import numpy as np
import pandas as pd

from ..cai.graph import DATA, to_xy
from . import spatial_fit as S

HIST = DATA / "history"
MIN_PRESENT = 40
BOX = (42.60, 42.80, 23.15, 23.55)


class Era:
    def __init__(self, name: str, df: pd.DataFrame):
        """df: sensor, lat, lon, t (hour, naive), pm25 (raw)."""
        self.name = name
        df = df[df.lat.between(BOX[0], BOX[1]) & df.lon.between(BOX[2], BOX[3]) & df.pm25.between(0, 500)]
        loc = df.groupby("sensor")[["lat", "lon"]].median()
        self.ids = loc.index.to_numpy()
        self.xy = np.column_stack(to_xy(loc.lon.to_numpy(), loc.lat.to_numpy()))
        times = pd.DatetimeIndex(sorted(df.t.unique()))
        si, ti = pd.Series(np.arange(len(self.ids)), index=self.ids), pd.Series(np.arange(len(times)), index=times)
        U = np.full((len(self.ids), len(times)), np.nan)
        U[si.reindex(df.sensor).to_numpy(), ti.reindex(df.t).to_numpy()] = np.log1p(df.pm25.to_numpy())
        keep = np.isfinite(U).sum(0) >= MIN_PRESENT
        self.U, self.times = U[:, keep], times[keep]
        self.L = np.nanmedian(self.U, axis=0)
        self.E0 = self.U - self.L[None, :]
        self.month = self.times.month.to_numpy()
        iu, ju = np.triu_indices(len(self.ids), 1)
        self.iu, self.ju = iu, ju
        self.pair_d = np.linalg.norm(self.xy[iu] - self.xy[ju], axis=1)
        self.n_hours = len(self.times)


def report(era: Era, rng) -> dict:
    cols = np.arange(era.n_hours)
    o = S.estimate_offsets(era.E0, cols)
    off, has = o["offset"], o["n"] >= 100
    E = era.E0 - off[:, None]
    out = {"sensors": int(len(era.ids)), "sensors_with_100h": int(has.sum()), "hours": int(era.n_hours), "offset_sd": round(float(np.sqrt(o["tau2"])), 3),
           "within_sensor_var": round(o["sigma2"], 3), "lag1": round(o["rho1"], 2)}
    # do neighbouring sensors share offsets?
    b = off[has]
    xy = era.xy[has]
    D = np.linalg.norm(xy[:, None] - xy[None], axis=-1)
    iu, ju = np.triu_indices(len(b), 1)
    d = D[iu, ju]
    corr = {}
    for lo, hi in [(0, 150), (150, 300), (300, 600), (600, 1200), (1200, 2500), (2500, 6000)]:
        m = (d >= lo) & (d < hi)
        corr[f"{lo}-{hi} m"] = {"pairs": int(m.sum()), "corr": round(float(np.corrcoef(b[iu][m], b[ju][m])[0, 1]), 3) if m.sum() > 8 else None}
    out["offset_corr_by_distance"] = corr
    np.fill_diagonal(D, np.inf)
    nn = np.argmin(D, axis=1)
    out["offset_corr_nearest_neighbour"] = round(float(np.corrcoef(b, b[nn])[0, 1]), 3)
    # variogram of the residual after the offsets
    hrs = rng.choice(cols, min(300, len(cols)), replace=False)
    h, g, cnt = S.semivariogram(era, E, hrs)
    out["variogram"] = S.fit_exponential(h, g, cnt)
    out["semivariance_by_distance"] = {str(int(x)): round(float(y), 4) for x, y in zip(h, g)}
    return out, dict(zip(era.ids[has], off[has]))


def main() -> int:
    rng = np.random.default_rng(7)
    s = pd.read_csv(HIST / "sofia1719" / "sds_hourly.csv", usecols=["sensor", "lat", "lon", "hour_utc", "pm25"])
    s["t"] = pd.to_datetime(s.hour_utc, format="%Y-%m-%dT%H").dt.tz_localize("UTC").dt.tz_convert("Europe/Sofia").dt.tz_localize(None)
    old = Era("2017-2019", s)
    n = pd.read_csv(HIST / "sensors.csv", usecols=["sensor", "lat", "lon", "hour_utc", "pm25"])
    n["t"] = pd.to_datetime(n.hour_utc).dt.tz_localize("UTC").dt.tz_convert("Europe/Sofia").dt.tz_localize(None)
    new = Era("2024-2026", n)
    res, offs = {}, {}
    for era in (old, new):
        res[era.name], offs[era.name] = report(era, rng)
        r = res[era.name]
        print(f"\n== {era.name}: {r['sensors']} sensors ({r['sensors_with_100h']} with >=100 h), {r['hours']} hours with >= {MIN_PRESENT} sensors", file=sys.stderr)
        print(f"   offset SD {r['offset_sd']} (log1p units, raw scale); within-sensor variance {r['within_sensor_var']}; lag-1 {r['lag1']}", file=sys.stderr)
        print("   correlation of offsets between sensors by distance:", json.dumps(r["offset_corr_by_distance"]), "| nearest neighbour:", r["offset_corr_nearest_neighbour"], file=sys.stderr)
        print("   variogram of the residual after offsets:", json.dumps(r["variogram"]), file=sys.stderr)
        print("   semivariance by distance:", json.dumps(r["semivariance_by_distance"]), file=sys.stderr)
    common = sorted(set(offs["2017-2019"]) & set(offs["2024-2026"]))
    a, b = np.array([offs["2017-2019"][i] for i in common]), np.array([offs["2024-2026"][i] for i in common])
    stab = {"sensors_in_both": len(common), "corr": round(float(np.corrcoef(a, b)[0, 1]), 3) if len(common) > 5 else None,
            "rank_corr": round(float(pd.Series(a).corr(pd.Series(b), method="spearman")), 3) if len(common) > 5 else None,
            "slope": round(float(np.polyfit(a, b, 1)[0]), 3) if len(common) > 5 else None}
    res["same_instruments_two_eras"] = stab
    print(f"\n== the {len(common)} sensor ids present in both eras (same instruments, 5-7 years apart): offset correlation {stab['corr']}, rank {stab['rank_corr']}, slope {stab['slope']}", file=sys.stderr)
    (DATA / "old_sofia_report.json").write_text(json.dumps(res, indent=1, default=float))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
