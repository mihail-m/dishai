"""Local temperature, humidity and pressure at the citizen-sensor locations -> data/history/env.csv

Most PM sensors in Sofia sit next to a BME280 / DHT22 / SHT3x that reports the weather AT that spot. 203 of the 258 outdoor PM locations
have one. That is local temperature and humidity (and, from pressure and height, a way to see the valley-floor versus hillside contrast
that drives inversions), which the single Open-Meteo point for the whole city cannot give.

Same days as the PM history (days_done.txt), one hourly mean per sensor-hour, resumable:

    python -m backend.data_prep.history_env [--concurrency 8]

Output columns: sensor, type, lat, lon, hour_utc, temp, rh, pressure_hpa, n
"""
import argparse
import asyncio
import csv
import gzip
import io
import sys
from collections import defaultdict
from datetime import date

import httpx

from .history import ARCHIVE, ARCHIVE_OLD, LIVE, NEW_LAYOUT_FROM, OUT
from .stac_common import BBOX

ENV_TYPES = {"BME280", "DHT22", "SHT31", "SHT30", "SHT35", "BMP280", "HTU21D", "BME680"}
DONE = OUT / "env_days_done.txt"
MAX_FAILED_SHARE = 0.1


async def live_env_sensors(c: httpx.AsyncClient) -> dict[int, str]:
    w, s, e, n = BBOX
    r = await c.get(LIVE.format(n=n, w=w, s=s, e=e), timeout=60)
    r.raise_for_status()
    out = {}
    for rec in r.json():
        typ = (rec.get("sensor") or {}).get("sensor_type", {}).get("name", "").upper()
        if typ in ENV_TYPES and str((rec.get("location") or {}).get("indoor", 0)) != "1":
            out[rec["sensor"]["id"]] = typ.lower()
    return out


def num(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return float("nan")


async def day_rows(c, sem, d: date, sid: int, typ: str):
    url = (ARCHIVE if d >= NEW_LAYOUT_FROM else ARCHIVE_OLD).format(d=d.isoformat(), y=d.year, typ=typ, sid=sid)
    async with sem:
        for attempt in range(3):
            try:
                r = await c.get(url, timeout=40)
            except httpx.HTTPError:
                await asyncio.sleep(1 + attempt)
                continue
            if r.status_code == 404:
                return []
            if r.status_code == 200:
                break
            await asyncio.sleep(1 + attempt)
        else:
            return None
    text = gzip.decompress(r.content).decode() if url.endswith(".gz") else r.text
    acc: dict[str, list[tuple[float, float, float]]] = defaultdict(list)
    lat = lon = None
    for row in csv.DictReader(io.StringIO(text), delimiter=";"):
        try:
            lat, lon = float(row["lat"]), float(row["lon"])
        except (ValueError, KeyError):
            continue
        t, h, p = num(row.get("temperature")), num(row.get("humidity")), num(row.get("pressure"))
        if -45 < t < 55:  # a sensor in the sun or a failed read gives absurd values; keep a sane physical range
            acc[row["timestamp"][:13]].append((t, h if 0 <= h <= 100 else float("nan"), p / 100.0 if p > 50_000 else float("nan")))
    if lat is None:
        return []

    def mean(xs):
        xs = [x for x in xs if x == x]
        return round(sum(xs) / len(xs), 2) if xs else ""

    return [(sid, typ, round(lat, 5), round(lon, 5), h + ":00", mean([a for a, _, _ in v]), mean([b for _, b, _ in v]), mean([c_ for _, _, c_ in v]), len(v))
            for h, v in acc.items() if len(v) >= 3]


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--concurrency", type=int, default=8)
    a = ap.parse_args()
    days = [date.fromisoformat(x) for x in (OUT / "days_done.txt").read_text().split()]
    done = set(DONE.read_text().split()) if DONE.exists() else set()
    f = OUT / "env.csv"
    new = not f.exists()
    sem = asyncio.Semaphore(a.concurrency)
    async with httpx.AsyncClient(follow_redirects=True) as c:
        sensors = await live_env_sensors(c)
        print(f"{len(sensors)} candidate environment sensors, {len(days)} days ({len(done)} done)", file=sys.stderr)
        bad = 0
        with open(f, "a", newline="") as fh:
            w = csv.writer(fh)
            if new:
                w.writerow(["sensor", "type", "lat", "lon", "hour_utc", "temp", "rh", "pressure_hpa", "n"])
            for d in sorted(days, reverse=True):
                if d.isoformat() in done:
                    continue
                parts = await asyncio.gather(*(day_rows(c, sem, d, sid, typ) for sid, typ in sensors.items()))
                failed = sum(p is None for p in parts)
                if failed > MAX_FAILED_SHARE * len(parts):
                    bad += 1
                    print(f"{d}: {failed}/{len(parts)} requests failed, not recorded", file=sys.stderr)
                    if bad >= 2:
                        print("archive is not answering; stopping (finished days are kept)", file=sys.stderr)
                        return
                    await asyncio.sleep(60)
                    continue
                bad = 0
                rows = [r for p in parts for r in (p or [])]
                w.writerows(rows)
                fh.flush()
                with open(DONE, "a") as df:
                    df.write(d.isoformat() + "\n")
                print(f"{d}: {len(rows)} sensor-hours from {sum(1 for p in parts if p)} sensors", file=sys.stderr)


if __name__ == "__main__":
    asyncio.run(main())
