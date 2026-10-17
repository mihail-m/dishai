"""Reference-grade PM2.5 / PM10 for Sofia from the EEA download service -> data/history/reference.csv (+ reference_stations.csv)

The WAQI feed the app shows live has no PM2.5, but Bulgaria reports it to the EEA, whose download API serves hourly values:
station list from the EEA metadata table (coordinates and station type are never guessed), then one parquet file per sampling point.

    python -m backend.data_prep.reference_eea [--datasets 1 3]

Facts learned the hard way (see docs/PM_MODEL.md):
  * dataset 1 (up-to-date, unverified stream) is the only one that overlaps the sensor history; it starts 2026-01-01. Dataset 2
    (verified) returned nothing for Bulgaria, dataset 3 (AirBase) ends years before the sensor history, so only 1 is used by default.
  * `Start` is UTC+1 all year (no DST): hour_utc = Start - 1 h. Re-checked by a lag scan in the calibration report.
  * Keep Validity > 0 only. Values arrive as Decimal.
Licence: EEA content is CC BY 4.0 (https://www.eea.europa.eu/en/legal-notice); credit the EEA and the reporting authority.
Nothing is executed from the downloads: parquet is read as data from memory.
"""
import argparse
import csv
import io
import sys
from datetime import date
from pathlib import Path

import httpx
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "history"
API = "https://eeadmz1-downloads-api-appservice.azurewebsites.net/ParquetFile/urls"
META = "https://discomap.eea.europa.eu/map/fme/metadata/PanEuropean_metadata.csv"
BOX = (23.12, 42.57, 23.58, 42.83)  # west, south, east, north: the sensor box plus a margin, so a station on its edge is kept
POLLUTANTS = {"PM2.5": "6001", "PM10": "5"}  # EEA pollutant vocabulary codes
START = "2023-01-01T00:00:00Z"
END = date.today().strftime("%Y-%m-%dT00:00:00Z")  # a future end date makes the service answer with an empty list
UTC_OFFSET_H = 1  # EEA hourly `Start` is in UTC+1 all year
ATTRIBUTION = "Air quality data: European Environment Agency (EEA) e-reporting, reported by Bulgaria; CC BY 4.0."


def stations(client: httpx.Client) -> pd.DataFrame:
    """Sofia-area stations that measure PM2.5 or PM10: code, pollutant, lon, lat, type, area (from the official metadata table)."""
    r = client.get(META, timeout=300)
    r.raise_for_status()
    m = pd.read_csv(io.BytesIO(r.content), sep="\t", low_memory=False)
    m = m[(m.Countrycode == "BG") & m.Longitude.between(BOX[0], BOX[2]) & m.Latitude.between(BOX[1], BOX[3])].copy()
    m["code"] = m.AirPollutantCode.str.rsplit("/", n=1).str[-1]
    m = m[m.code.isin(POLLUTANTS.values())]
    m["pollutant"] = m.code.map({v: k for k, v in POLLUTANTS.items()})
    out = m.groupby(["AirQualityStationEoICode", "pollutant"], as_index=False).agg(
        lon=("Longitude", "first"), lat=("Latitude", "first"), station_type=("AirQualityStationType", "first"),
        area=("AirQualityStationArea", "first"))
    return out.rename(columns={"AirQualityStationEoICode": "station"})


def urls(client: httpx.Client, pollutant: str, dataset: int) -> list[str]:
    body = {"countries": ["BG"], "cities": [], "pollutants": [pollutant], "dataset": dataset, "source": "Api",
            "dateTimeStart": START, "dateTimeEnd": END, "aggregationType": "hour"}
    r = client.post(API, json=body, timeout=120)
    r.raise_for_status()
    return [u.strip().lstrip("﻿") for u in r.text.splitlines() if u.strip().startswith("http")]


def hourly(client: httpx.Client, url: str) -> pd.DataFrame | None:
    r = client.get(url, timeout=180)
    if r.status_code != 200 or len(r.content) < 1000:  # the service answers a ~200-byte stub for files it does not have
        return None
    d = pd.read_parquet(io.BytesIO(r.content))
    d = d[d.Validity > 0]
    t = pd.to_datetime(d.Start) - pd.Timedelta(hours=UTC_OFFSET_H)
    return pd.DataFrame({"hour_utc": t.dt.strftime("%Y-%m-%dT%H:%M"), "value": d.Value.astype(float).round(2)})


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--datasets", type=int, nargs="+", default=[1])
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    rows, used = [], []
    with httpx.Client(follow_redirects=True) as c:
        st = stations(c)
        print(f"{len(st)} PM station-pollutant pairs in the Sofia box", file=sys.stderr)
        for pol, code in POLLUTANTS.items():
            want = st[st.pollutant == pol].set_index("station")
            for ds in a.datasets:
                for u in urls(c, pol, ds):
                    stem = u.rsplit("/", 1)[-1]  # SPO-BG0050A_06001_100.parquet
                    station = stem.split("_")[0].removeprefix("SPO-")
                    if station not in want.index or f"_{int(code):05d}_" not in stem:  # file names zero-pad the pollutant code
                        continue
                    d = hourly(c, u)
                    if d is None or d.empty:
                        continue
                    s = want.loc[station]
                    d.insert(0, "station", station)
                    d.insert(1, "pollutant", pol)
                    for k in ("lat", "lon", "station_type"):
                        d.insert(len(d.columns) - 2, k, round(s[k], 5) if k != "station_type" else s[k])
                    rows.append(d)
                    used.append((station, pol, ds, len(d), d.hour_utc.min(), d.hour_utc.max()))
                    print(f"{station} {pol} dataset {ds}: {len(d)} valid hours {d.hour_utc.min()}..{d.hour_utc.max()}", file=sys.stderr)
    if not rows:
        print("no reference data downloaded", file=sys.stderr)
        return 1
    ref = pd.concat(rows, ignore_index=True).drop_duplicates(["station", "pollutant", "hour_utc"])
    ref = ref[["station", "pollutant", "lat", "lon", "station_type", "hour_utc", "value"]]
    ref.to_csv(OUT / "reference.csv", index=False, quoting=csv.QUOTE_MINIMAL)
    st.merge(pd.DataFrame(used, columns=["station", "pollutant", "dataset", "hours", "first", "last"]), on=["station", "pollutant"]) \
        .to_csv(OUT / "reference_stations.csv", index=False)
    print(f"{len(ref):,} rows -> {OUT / 'reference.csv'}\n{ATTRIBUTION}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
