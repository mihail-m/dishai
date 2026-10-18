"""The pre-specified construction check, now that the PM10 history exists (docs/PM_MODEL.md, "Construction sites").

    PYTHONPATH=. .venv/bin/python -m backend.ml.construction_pm10_check

Pre-specified in the earlier write-up: on PM10, for sites flagged 'cleared since last summer' (Sentinel-2) within 300 m, compare workday with
weekend at 9-17 per sensor, against sensors with no mapped site within 300 m. If the interval excludes zero, add a site-activity term to a
PM10 model. One test, one cut, no other looking. Each sensor is its own control (workday minus weekend), measured as the log deviation
from the same hour's city median of PM10, so calibration and siting cancel. Also reported for the other site groups, flagged as secondary.
"""
import json

import numpy as np
import pandas as pd
from pyproj import Transformer
from shapely.geometry import Point, shape
from shapely.ops import transform, unary_union

from ..cai.graph import DATA

tr = Transformer.from_crs(4326, 32635, always_xy=True).transform
HIST = DATA / "history"


def main() -> int:
    feats = json.load(open(DATA / "construction.geojson"))["features"]
    act = pd.read_csv(DATA / "construction_activity.csv")
    geoms = [transform(tr, shape(f["geometry"])) for f in feats]
    nd, ndp = act.ndvi_recent.to_numpy(), act.ndvi_previous.to_numpy()
    groups = {
        "cleared since last summer (PRE-SPECIFIED)": np.nan_to_num((((ndp - nd) > 0.15) & (nd < 0.3)).astype(float)).astype(bool),
        "bare now (NDVI < 0.2)": np.nan_to_num((nd < 0.2).astype(float)).astype(bool),
        "any mapped site": np.ones(len(geoms), bool),
        "green now (control)": np.nan_to_num((nd >= 0.3).astype(float)).astype(bool),
    }
    s = pd.read_csv(HIST / "sensors_pm10.csv").dropna(subset=["pm10", "pm25"])
    t = pd.to_datetime(s.hour_utc).dt.tz_localize("UTC").dt.tz_convert("Europe/Sofia").dt.tz_localize(None)
    s["t"], s["hr"], s["wd"] = t, t.dt.hour, t.dt.weekday < 5
    s["lr"] = np.log1p(s.pm10) - np.log1p(s.groupby("t").pm10.transform("median"))
    loc = s.groupby("sensor")[["lat", "lon"]].median()
    pts = [transform(tr, Point(lo, la)) for la, lo in zip(loc.lat, loc.lon)]
    day = s[s.hr.between(9, 17)]
    a = day[day.wd].groupby("sensor").lr.agg(["mean", "size"])
    b = day[~day.wd].groupby("sensor").lr.agg(["mean", "size"])
    j = a.join(b, lsuffix="_wd", rsuffix="_we").dropna()
    j = j[(j.size_wd >= 150) & (j.size_we >= 60)]
    j["diff"] = j.mean_wd - j.mean_we
    print(f"{s.t.dt.date.nunique()} days of PM10, {len(j)} sensors with enough workday and weekend daytime hours; mean workday-minus-weekend {j['diff'].mean():+.3f} log units")
    rng = np.random.default_rng(0)
    res = {}
    allsites = unary_union(geoms)
    for name, m in groups.items():
        sel = unary_union([g for g, k in zip(geoms, m) if k]) if m.any() else None
        near = pd.Series([p.distance(sel) <= 300 if sel is not None else False for p in pts], index=loc.index).loc[j.index]
        far = pd.Series([p.distance(allsites) > 300 for p in pts], index=loc.index).loc[j.index]  # no mapped site of any kind within 300 m
        if near.sum() < 5 or far.sum() < 5:
            print(f"{name:42s} near {int(near.sum())} / far {int(far.sum())}: too few sensors")
            continue
        dn, df_ = j["diff"][near].to_numpy(), j["diff"][far].to_numpy()
        gap = dn.mean() - df_.mean()
        bs = [rng.choice(dn, len(dn)).mean() - rng.choice(df_, len(df_)).mean() for _ in range(4000)]
        lo, hi = np.percentile(bs, [2.5, 97.5])
        res[name] = {"near": int(near.sum()), "far": int(far.sum()), "gap": round(float(gap), 3), "ci95": [round(float(lo), 3), round(float(hi), 3)]}
        print(f"{name:42s} near {int(near.sum()):3d} / far {int(far.sum()):3d}: workday-minus-weekend gap {gap:+.3f}  95% CI [{lo:+.3f}, {hi:+.3f}]  -> {'EXCLUDES 0' if lo > 0 or hi < 0 else 'includes 0'}")
    # PM10 / PM2.5 ratio in workday daytime, sensors near sites vs far
    day = day.assign(ratio=np.log(day.pm10.clip(lower=1) / day.pm25.clip(lower=1)))
    r = day[day.wd].groupby("sensor").ratio.median().reindex(j.index)
    m = groups["any mapped site"]
    sel = unary_union(geoms)
    near = pd.Series([p.distance(sel) <= 150 for p in pts], index=loc.index).loc[j.index]
    print(f"workday-daytime PM10/PM2.5 (median of log ratio): sensors within 150 m of any mapped site {r[near].median():+.3f} (n={int(near.sum())}) vs the rest {r[~near].median():+.3f} (n={int((~near).sum())})")
    (DATA / "construction_pm10_check.json").write_text(json.dumps(res, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
