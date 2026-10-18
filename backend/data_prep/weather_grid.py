"""Local weather and CAMS on a grid over Sofia -> data/history/weather_grid.csv

The model used one Open-Meteo point (the centre) for the whole city. On a polluted inversion night (2026-01-01 22:00) the same forecast
gives boundary-layer height 20 m in the centre and 220 m at the foot of Vitosha, wind 0.5 vs 13.5 km/h, humidity 51 vs 87 %, temperature
-1.8 vs -5.5 C. This pulls the same historical-forecast variables, plus CAMS PM2.5 / PM10 / NO2, at a grid of points so that every sensor
and every street can be given the weather of its own surroundings.

    python -m backend.data_prep.weather_grid [--days 800] [--nx 7 --ny 5]      (resumable: one part file per point and chunk)

Output columns: point, lat, lon, elev, time_local, <weather variables>, cams_pm2_5, cams_pm10, cams_no2
"""
import argparse
import asyncio
import sys
from datetime import date, timedelta

import httpx
import numpy as np
import pandas as pd

from .history import OUT
from .stac_common import BBOX

WX = ("boundary_layer_height,wind_speed_10m,temperature_2m,temperature_925hPa,wind_speed_925hPa,cloud_cover,apparent_temperature,"
      "shortwave_radiation,relative_humidity_2m,precipitation,surface_pressure")
WEATHER = ("https://historical-forecast-api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&start_date={a}&end_date={b}"
           "&timezone=Europe%2FSofia&hourly=" + WX)
CAMS = ("https://air-quality-api.open-meteo.com/v1/air-quality?latitude={lat}&longitude={lon}&start_date={a}&end_date={b}"
        "&timezone=Europe%2FSofia&hourly=pm2_5,pm10,nitrogen_dioxide")
PARTS = OUT / "weather_grid_parts"


async def get(c: httpx.AsyncClient, url: str) -> dict:
    wait = 5
    for _ in range(8):
        try:
            r = await c.get(url, timeout=90)
            if r.status_code == 200:
                return r.json()
            if r.status_code == 429:  # rate limited: back off
                await asyncio.sleep(wait * 4)
        except httpx.HTTPError:
            pass
        await asyncio.sleep(wait)
        wait = min(wait * 2, 60)
    raise RuntimeError(f"no answer from {url[:80]}")


async def one(c, sem, pid: int, lat: float, lon: float, a: date, b: date) -> None:
    f = PARTS / f"p{pid:02d}_{a}.csv"
    if f.exists():
        return
    async with sem:
        w = await get(c, WEATHER.format(lat=lat, lon=lon, a=a, b=b))
        q = await get(c, CAMS.format(lat=lat, lon=lon, a=a, b=b))
    hw, hq = w["hourly"], q["hourly"]
    df = pd.DataFrame({k: v for k, v in hw.items() if k != "time"}, index=hw["time"])
    cams = pd.DataFrame({f"cams_{k}": v for k, v in hq.items() if k != "time"}, index=hq["time"])
    df = df.join(cams, how="left")
    df.insert(0, "elev", w.get("elevation"))
    df.insert(0, "lon", lon)
    df.insert(0, "lat", lat)
    df.insert(0, "point", pid)
    df.index.name = "time_local"
    df.to_csv(f)


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=800)
    ap.add_argument("--nx", type=int, default=7)
    ap.add_argument("--ny", type=int, default=5)
    ap.add_argument("--concurrency", type=int, default=3)
    a = ap.parse_args()
    PARTS.mkdir(parents=True, exist_ok=True)
    w, s, e, n = BBOX
    pts = [(round(la, 4), round(lo, 4)) for la in np.linspace(s, n, a.ny) for lo in np.linspace(w, e, a.nx)]
    start, end = date.today() - timedelta(days=a.days), date.today() - timedelta(days=1)
    chunks, cur = [], start
    while cur <= end:
        nxt = min(cur + timedelta(days=60), end)
        chunks.append((cur, nxt))
        cur = nxt + timedelta(days=1)
    print(f"{len(pts)} points x {len(chunks)} chunks", file=sys.stderr)
    sem = asyncio.Semaphore(a.concurrency)
    async with httpx.AsyncClient(follow_redirects=True) as c:
        done = 0
        for chunk in chunks:
            await asyncio.gather(*(one(c, sem, i, la, lo, *chunk) for i, (la, lo) in enumerate(pts)))
            done += 1
            print(f"chunk {done}/{len(chunks)} {chunk[0]}..{chunk[1]}", file=sys.stderr)
    parts = sorted(PARTS.glob("p*.csv"))
    pd.concat([pd.read_csv(p) for p in parts], ignore_index=True).to_csv(OUT / "weather_grid.csv", index=False)
    print(f"{len(parts)} parts -> {OUT / 'weather_grid.csv'}", file=sys.stderr)


if __name__ == "__main__":
    asyncio.run(main())
