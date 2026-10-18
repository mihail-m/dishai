"""Hourly means from the Kaggle 'Sofia air quality dataset' (Sensor.Community Sofia, 2017-07 to 2019-07) -> data/history/sofia1719/

    python -m backend.data_prep.kaggle_sofia data/air-quality-dataset_Sofia.zip

The zip holds 50 monthly CSVs (SDS011 PM and BME280 weather readings every few minutes, about 15 GB uncompressed). They are streamed from the
zip in chunks and reduced to one row per sensor-hour, so nothing large is written to disk. Files are only ever read as data.
Outputs: sds_hourly.csv (sensor, location, lat, lon, hour_utc, pm10, pm25, n) and bme_hourly.csv (sensor, location, lat, lon, hour_utc, temp, rh, pressure_hpa, n).
There is no indoor flag in these files; the screening for indoor sensors is done afterwards from the BME280 temperatures (see ml/old_sofia.py).
"""
import sys
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

OUT = Path(__file__).resolve().parents[2] / "data" / "history" / "sofia1719"
CHUNK = 2_000_000


def hourly(chunks, kind: str) -> pd.DataFrame:
    parts = []
    for ch in chunks:
        ch = ch.rename(columns={"sensor_id": "sensor"})
        ch["hour_utc"] = ch["timestamp"].str.slice(0, 13)
        if kind == "sds":
            ch = ch[(ch.P2 >= 0) & (ch.P2 <= 500) & (ch.P1 >= 0) & (ch.P1 <= 1000)]
            ch = ch.assign(c=1)
            agg = ch.groupby(["sensor", "location", "hour_utc"], sort=False).agg(lat=("lat", "median"), lon=("lon", "median"), p1=("P1", "sum"), p2=("P2", "sum"), n=("c", "sum"))
        else:
            ch = ch[(ch.temperature > -45) & (ch.temperature < 55) & (ch.humidity >= 0) & (ch.humidity <= 100) & (ch.pressure > 50_000) & (ch.pressure < 110_000)]
            ch = ch.assign(c=1)
            agg = ch.groupby(["sensor", "location", "hour_utc"], sort=False).agg(lat=("lat", "median"), lon=("lon", "median"), t=("temperature", "sum"), h=("humidity", "sum"),
                                                                                p=("pressure", "sum"), n=("c", "sum"))
        parts.append(agg.reset_index())
    d = pd.concat(parts, ignore_index=True)
    sums = [c for c in d.columns if c in ("p1", "p2", "t", "h", "p", "n")]
    g = d.groupby(["sensor", "location", "hour_utc"], sort=False).agg({**{c: "sum" for c in sums}, "lat": "median", "lon": "median"}).reset_index()
    if kind == "sds":
        g["pm10"], g["pm25"] = g.p1 / g.n, g.p2 / g.n
        return g[["sensor", "location", "lat", "lon", "hour_utc", "pm10", "pm25", "n"]]
    g["temp"], g["rh"], g["pressure_hpa"] = g.t / g.n, g.h / g.n, g.p / g.n / 100.0
    return g[["sensor", "location", "lat", "lon", "hour_utc", "temp", "rh", "pressure_hpa", "n"]]


def main(path: str) -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    z = zipfile.ZipFile(path)
    sds, bme = [], []
    for info in sorted(z.infolist(), key=lambda i: i.filename):
        name = info.filename
        if not name.endswith(("sof.csv", ".csvsof")) or "/" in name or ".." in name:
            print(f"skipping {name}", file=sys.stderr)
            continue
        kind = "sds" if "sds011" in name else "bme" if "bme280" in name else None
        if kind is None:
            continue
        cols = ["sensor_id", "location", "lat", "lon", "timestamp", "P1", "P2"] if kind == "sds" else ["sensor_id", "location", "lat", "lon", "timestamp", "pressure", "temperature", "humidity"]
        with z.open(info) as fh:
            h = hourly(pd.read_csv(fh, usecols=cols, chunksize=CHUNK, dtype={"timestamp": str}), kind)
        (sds if kind == "sds" else bme).append(h)
        print(f"{name}: {len(h):,} sensor-hours, {h.sensor.nunique()} sensors", file=sys.stderr)
    for kind, parts, f in (("sds", sds, "sds_hourly.csv"), ("bme", bme, "bme_hourly.csv")):
        pd.concat(parts, ignore_index=True).to_csv(OUT / f, index=False, float_format="%.3f")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))
