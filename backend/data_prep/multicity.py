"""Citizen sensors next to EEA reference stations in many European cities, 2026 -> data/history/multicity/

Why: the Sofia calibration rests on one reference station (Hipodruma, 13 sensors, 3 hours above 50 ug/m3). The same SDS011 sensors and the same EEA
stations exist across Europe, so the calibration can be fitted on many cities and scored on Sofia (and the other way round).

    python -m backend.data_prep.multicity discover      # candidate stations: EEA PM2.5 stations with >= 4 outdoor PM sensors within 1.5 km
    python -m backend.data_prep.multicity pull          # sensors (Sensor.Community archive, same 2026 sample days as the Sofia history), EEA PM2.5, local weather

Outputs (git-ignored, under data/history/multicity/): candidate_stations.csv, sc_sensors_24h.csv, sensors_hourly.csv (sensor, lat, lon, hour_utc, pm25, pm10, n),
eea_pm25.csv (station, hour_utc, value), weather.csv (station, time_local, temperature_2m, relative_humidity_2m).
EEA reference data: CC BY 4.0, European Environment Agency; Sensor.Community: open data (CC BY-SA 3.0-ish, see their site); Open-Meteo: CC BY 4.0.
"""
import asyncio
import csv
import io
import sys
from datetime import date

import httpx
import numpy as np
import pandas as pd

from . import history as H
from . import reference_eea as R

OUT = H.OUT / "multicity"
RADIUS_M = 1500.0
MIN_SENSORS = 4
PM_TYPES = {"SDS011", "PMS5003", "PMS7003", "SPS30", "HPM", "NOVA SDS011", "PMS3003"}


def discover() -> pd.DataFrame:
    OUT.mkdir(parents=True, exist_ok=True)
    m = pd.read_csv(io.BytesIO(httpx.get(R.META, timeout=300, follow_redirects=True).content), sep="\t", low_memory=False)
    m["code"] = m.AirPollutantCode.str.rsplit("/", n=1).str[-1]
    m = m[(m.code == "6001") & m.Latitude.notna() & (m.ObservationDateEnd.isna() | (m.ObservationDateEnd.astype(str) > "2026"))]
    st = m.groupby("AirQualityStationEoICode", as_index=False).agg(country=("Countrycode", "first"), lat=("Latitude", "first"), lon=("Longitude", "first"),
                                                                    type=("AirQualityStationType", "first"), area=("AirQualityStationArea", "first"))
    raw = httpx.get("https://data.sensor.community/static/v2/data.24h.json", timeout=300).json()
    rows = []
    for x in raw:
        loc, typ = x.get("location") or {}, (x.get("sensor") or {}).get("sensor_type", {}).get("name", "")
        if typ.upper() in PM_TYPES and str(loc.get("indoor", 0)) != "1":
            try:
                rows.append((x["sensor"]["id"], float(loc["latitude"]), float(loc["longitude"]), typ.lower().replace("nova ", "")))
            except (KeyError, TypeError, ValueError):
                pass
    sc = pd.DataFrame(rows, columns=["sensor", "lat", "lon", "type"]).drop_duplicates("sensor")
    sc.to_csv(OUT / "sc_sensors_24h.csv", index=False)
    n = []
    for _, s in st.iterrows():
        d = np.hypot((sc.lat.to_numpy() - s.lat) * 111_320, (sc.lon.to_numpy() - s.lon) * 111_320 * np.cos(np.radians(s.lat)))
        n.append(int((d <= RADIUS_M).sum()))
    st["n_sensors"] = n
    good = st[st.n_sensors >= MIN_SENSORS].sort_values("n_sensors", ascending=False)
    good.to_csv(OUT / "candidate_stations.csv", index=False)
    print(f"{len(good)} stations, {len(sc)} sensors worldwide", file=sys.stderr)
    return good


async def pull_sensors(stations: pd.DataFrame, days: list[date], conc: int = 8) -> None:
    sc = pd.read_csv(OUT / "sc_sensors_24h.csv")
    keep = {}
    for _, s in stations.iterrows():
        d = np.hypot((sc.lat.to_numpy() - s.lat) * 111_320, (sc.lon.to_numpy() - s.lon) * 111_320 * np.cos(np.radians(s.lat)))
        for _, r in sc[d <= RADIUS_M].iterrows():
            keep[int(r.sensor)] = str(r.type).lower().replace("nova ", "")
    f = OUT / "sensors_hourly.csv"
    done_f = OUT / "sensors_days_done.txt"
    done = set(done_f.read_text().split()) if done_f.exists() else set()
    new = not f.exists()
    sem = asyncio.Semaphore(conc)
    print(f"{len(keep)} sensors, {len(days)} days ({len(done)} done)", file=sys.stderr)
    async with httpx.AsyncClient(follow_redirects=True) as c:
        with open(f, "a", newline="") as fh:
            w = csv.writer(fh)
            if new:
                w.writerow(["sensor", "lat", "lon", "hour_utc", "pm25", "pm10", "n"])
            for d in sorted(days, reverse=True):
                if d.isoformat() in done:
                    continue
                parts = await asyncio.gather(*(H.day_rows(c, sem, d, sid, typ) for sid, typ in keep.items()))
                if sum(p is None for p in parts) > 0.1 * len(parts):
                    print(f"{d}: too many failed requests, skipped", file=sys.stderr)
                    continue
                rows = [r for p in parts for r in (p or [])]
                w.writerows(rows)
                fh.flush()
                with open(done_f, "a") as df:
                    df.write(d.isoformat() + "\n")
                print(f"{d}: {len(rows)} sensor-hours", file=sys.stderr)


def pull_eea(stations: pd.DataFrame) -> None:
    out = []
    want = set(stations.AirQualityStationEoICode)
    with httpx.Client(follow_redirects=True) as c:
        for country in sorted(stations.country.unique()):
            body = {"countries": [country], "cities": [], "pollutants": ["PM2.5"], "dataset": 1, "source": "Api", "dateTimeStart": "2026-01-01T00:00:00Z",
                    "dateTimeEnd": date.today().strftime("%Y-%m-%dT00:00:00Z"), "aggregationType": "hour"}
            r = c.post(R.API, json=body, timeout=180)
            for u in [x.strip().lstrip("﻿") for x in r.text.splitlines() if x.strip().startswith("http")]:
                stem = u.rsplit("/", 1)[-1]
                code = next((c_ for c_ in want if c_ in stem), None)  # file names differ by country (SPO-BG0050A_..., SPO.DE_DEBB032_..., SPO_PL0047A_...)
                if code is None:
                    continue
                d = R.hourly(c, u)
                if d is not None and len(d):
                    d.insert(0, "station", code)
                    out.append(d)
            have = {d.station.iloc[0] for d in out}
            print(f"EEA {country}: {len(have & set(stations[stations.country == country].AirQualityStationEoICode))} of {int((stations.country == country).sum())} stations", file=sys.stderr)
    pd.concat(out).drop_duplicates(["station", "hour_utc"]).to_csv(OUT / "eea_pm25.csv", index=False)


def pull_weather(stations: pd.DataFrame) -> None:
    url = ("https://historical-forecast-api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&start_date=2026-01-01&end_date={end}"
           "&timezone=UTC&hourly=temperature_2m,relative_humidity_2m")
    rows = []
    end = date.today().isoformat()
    with httpx.Client(follow_redirects=True) as c:
        for _, s in stations.iterrows():
            for attempt in range(6):
                try:
                    r = c.get(url.format(lat=round(s.lat, 3), lon=round(s.lon, 3), end=end), timeout=90)
                    h = r.json()["hourly"] if r.status_code == 200 else None
                except (httpx.HTTPError, ValueError, KeyError):
                    h = None
                if h:
                    rows.append(pd.DataFrame({"station": s.AirQualityStationEoICode, "hour_utc": h["time"], "temp": h["temperature_2m"], "rh": h["relative_humidity_2m"]}))
                    break
                import time
                time.sleep(8 * (attempt + 1))
    pd.concat(rows).to_csv(OUT / "weather.csv", index=False)


def main() -> int:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "discover"
    if cmd == "discover":
        discover()
        return 0
    st = pd.read_csv(OUT / "candidate_stations.csv")
    days = [date.fromisoformat(x) for x in (H.OUT / "days_done.txt").read_text().split() if x >= "2026-01-01"]
    if not (OUT / "eea_pm25.csv").exists():
        pull_eea(st)
    if not (OUT / "weather.csv").exists():
        pull_weather(st)
    asyncio.run(pull_sensors(st, days))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
