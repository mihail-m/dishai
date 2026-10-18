"""BGTOLL open data (CC BY 4.0): measured vehicle counts, speeds and classes at the national road network's control points.

    python -I backend/data_prep/bgtoll.py PATH_TO_Traffic_data.zip   # the "Traffic data.zip" from https://bgtoll.bg/en/open-data

The archive is a zip of daily zips of 15-minute JSON snapshots (one row per control point: count15min, average_speed_15min, per-class
counts). This keeps the control points within RADIUS_KM of central Sofia and writes one row per control point and UTC hour to
data/history/bgtoll_sofia_hourly.csv (derived, with attribution). The input is untrusted: it is only read with zipfile/json.

Attribution: contains data of the Bulgarian road toll authority (BGTOLL), CC BY 4.0, https://bgtoll.bg/en/open-data
"""
import io
import json
import math
import sys
import zipfile
from collections import defaultdict
from pathlib import Path

CENTER = (42.6977, 23.3219)  # lat, lon
RADIUS_KM = 25.0
OUT = Path(__file__).resolve().parents[2] / "data" / "history" / "bgtoll_sofia_hourly.csv"
HEAVY = ("class3_heavy_12t", "class4_heavy_2a", "class5_heavy_3a", "class6_heavy_4a", "class7_heavy_5a", "class8_bus_12t", "class9_bus_heavy")


def km_from_center(lat: float, lon: float) -> float:
    return math.hypot((lon - CENTER[1]) * 111.2 * math.cos(math.radians(CENTER[0])), (lat - CENTER[0]) * 111.2)


def main(path: str) -> None:
    outer = zipfile.ZipFile(path)
    days = sorted(n for n in outer.namelist() if n.endswith(".zip"))
    # (scp, hour) -> [sum of 15-min counts, snapshots, sum of heavy, sum of speed x count]
    acc: dict[tuple, list] = defaultdict(lambda: [0.0, 0, 0.0, 0.0])
    meta: dict[int, tuple] = {}
    for n, name in enumerate(days):
        inner = zipfile.ZipFile(io.BytesIO(outer.read(name)))
        for f in inner.namelist():
            if not f.endswith(".json"):
                continue
            try:
                rows = json.loads(inner.read(f))
            except ValueError:
                continue
            for r in rows:
                try:
                    lat, lon = float(r["lat"]), float(r["lon"])
                    c15 = r.get("count15min")
                    t = r["time"]
                except (KeyError, TypeError, ValueError):
                    continue
                if c15 is None or km_from_center(lat, lon) > RADIUS_KM:
                    continue
                scp = int(r["scp"])
                meta[scp] = (r.get("nameENG") or r.get("name"), lat, lon, round(km_from_center(lat, lon), 1))
                a = acc[(scp, t[:13])]
                a[0] += c15
                a[1] += 1
                a[2] += sum((r.get("classes15min") or {}).get(k, 0) for k in HEAVY)
                a[3] += (r.get("average_speed_15min") or 0) * c15
        if n % 20 == 0:
            print(f"{n + 1}/{len(days)} {name}", file=sys.stderr)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w") as fh:
        fh.write("scp,name,lat,lon,km_from_center,hour_utc,snapshots,vehicles_per_hour,heavy_share,mean_speed_kmh\n")
        for (scp, hour), (cnt, k, heavy, spd) in sorted(acc.items()):
            nm, lat, lon, km = meta[scp]
            per_hour = cnt / k * 4.0  # mean 15-minute count in the hour x 4 (snapshots are sparse: ~89 of 96 per day)
            fh.write(f'{scp},"{nm}",{lat},{lon},{km},{hour.replace(" ", "T")}:00,{k},{per_hour:.1f},{(heavy / cnt if cnt else 0):.4f},{(spd / cnt if cnt else 0):.1f}\n')
    print(f"{len(meta)} control points, {len(acc)} point-hours -> {OUT}", file=sys.stderr)


# Ring-road and motorway approaches with a complete record (both directions); the other points in the radius are small or partial.
PROFILE_POINTS = (10191, 10192, 10181, 10182, 20401, 20402, 10201, 10202, 20011, 20012, 20421, 20422)
PROFILE_OUT = Path(__file__).resolve().parents[2] / "data" / "bgtoll_profile.json"


def build_profile(csv_path: Path = OUT, out: Path = PROFILE_OUT) -> dict:
    """Typical vehicle volume by local hour, weekday and weekend, as a share of the weekday peak (mean of the points' counts, each divided
    by its own median so a busy point does not dominate)."""
    import pandas as pd

    t = pd.read_csv(csv_path, parse_dates=["hour_utc"])
    t = t[t.scp.isin(PROFILE_POINTS)].copy()
    t["norm"] = t.vehicles_per_hour / t.groupby("scp").vehicles_per_hour.transform("median")
    g = t.groupby("hour_utc").agg(idx=("norm", "mean"), n=("scp", "nunique"), heavy=("heavy_share", "mean")).reset_index()
    g = g[g.n >= 8]  # hours where at least 8 of the 12 points reported
    loc = g.hour_utc.dt.tz_localize("UTC").dt.tz_convert("Europe/Sofia").dt.tz_localize(None)
    g["hr"], g["we"] = loc.dt.hour, loc.dt.dayofweek >= 5
    prof = g.groupby(["we", "hr"]).idx.mean().unstack(0)
    peak = float(prof[False].max())
    heavy = g.groupby("we").heavy.mean()
    names = t.drop_duplicates("scp").set_index("scp").name.to_dict()
    res = {
        "source": "BGTOLL open data, traffic intensity (15-minute vehicle counts at control points), CC BY 4.0, https://bgtoll.bg/en/open-data",
        "points": [{"scp": int(k), "name": names[k]} for k in PROFILE_POINTS if k in names],
        "from": str(g.hour_utc.min()), "to": str(g.hour_utc.max()), "hours_used": int(len(g)),
        "weekday": [round(float(v) / peak, 3) for v in prof[False]], "weekend": [round(float(v) / peak, 3) for v in prof[True]],
        "heavy_share": {"weekday": round(float(heavy[False]), 3), "weekend": round(float(heavy[True]), 3)},
        "note": "Share of the weekday peak hour. Control points are on the national network around Sofia (ring road and motorway approaches), not city streets.",
    }
    out.write_text(json.dumps(res, indent=1))
    return res


if __name__ == "__main__":
    if sys.argv[1] == "--profile":
        print(build_profile(), file=sys.stderr)
    else:
        main(sys.argv[1])
