"""Training history for the PM2.5 model -> data/history/{sensors,weather,cams}.csv

Sensor.Community publishes one CSV per sensor per day (archive.sensor.community). We take the PM sensors that
are live in the Sofia bbox today, fetch two consecutive days out of every STEP days over the last DAYS days (so
the sample spans every season, inversion-prone winter included, and a forecast issued on day 1 can be scored out
to 48 h) and keep hourly means. Weather and CAMS come from Open-Meteo's
historical-forecast and air-quality endpoints, i.e. the same variables the live app feeds the model.

    python -m backend.data_prep.history [--days 365] [--step 7] [--episodes 14] [--series-days 800]     (resumable)

--episodes N also fetches the N worst days by CAMS daily-mean PM2.5 (and the day after) within the weather/CAMS
series: a random sample of days contains almost no inversion episode, and a model cannot learn what it never saw.
"""
import argparse
import asyncio
import csv
import gzip
import io
import sys
from collections import defaultdict
from datetime import date, timedelta

import httpx

from .stac_common import BBOX, DATA

OUT = DATA / "history"
LIVE = "https://data.sensor.community/airrohr/v1/filter/box={n},{w},{s},{e}"
ARCHIVE = "https://archive.sensor.community/{d}/{d}_{typ}_sensor_{sid}.csv"
ARCHIVE_OLD = "https://archive.sensor.community/{y}/{d}/{d}_{typ}_sensor_{sid}.csv.gz"  # before 2026 the archive is per year, gzipped
NEW_LAYOUT_FROM = date(2026, 1, 1)
LAT, LON = 42.6977, 23.3219
PM_TYPES = {"SDS011", "PMS5003", "PMS7003", "PMS3003", "SPS30", "HPM", "PMSX003", "NOVA SDS011"}
MAX_PM25 = 500.0
CONCURRENCY = 12  # the archive throttles harder than this (it stopped answering at 48)
MAX_FAILED_SHARE = 0.1  # a day with more failed requests than this is not recorded, so a re-run fetches it again

WEATHER = ("https://historical-forecast-api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}"
           "&start_date={a}&end_date={b}&timezone=Europe%2FSofia&hourly=boundary_layer_height,wind_speed_10m,"
           "temperature_2m,temperature_925hPa,wind_speed_925hPa,cloud_cover,apparent_temperature,shortwave_radiation,"
           "relative_humidity_2m,precipitation,surface_pressure")
CAMS = ("https://air-quality-api.open-meteo.com/v1/air-quality?latitude={lat}&longitude={lon}"
        "&start_date={a}&end_date={b}&timezone=Europe%2FSofia&hourly=pm2_5,pm10,nitrogen_dioxide,sulphur_dioxide,carbon_monoxide,ozone")


async def live_sensors(c: httpx.AsyncClient) -> dict[int, str]:
    w, s, e, n = BBOX
    r = await c.get(LIVE.format(n=n, w=w, s=s, e=e), timeout=60)
    r.raise_for_status()
    out = {}
    for rec in r.json():
        typ = (rec.get("sensor") or {}).get("sensor_type", {}).get("name", "").upper()
        if typ in PM_TYPES and str((rec.get("location") or {}).get("indoor", 0)) != "1":
            out[rec["sensor"]["id"]] = typ.lower().replace("nova ", "")
    return out


def save_types(types: dict[int, str]) -> int:
    """data/history/sensor_types.csv (sensor,type): the calibration depends on the sensor model (SDS011 vs the Plantower family)."""
    OUT.mkdir(parents=True, exist_ok=True)
    with open(OUT / "sensor_types.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["sensor", "type"])
        w.writerows(sorted(types.items()))
    return len(types)


async def day_rows(c, sem, d: date, sid: int, typ: str) -> list[tuple] | None:
    """Hourly rows for one sensor-day; [] when it has no file (404); None when the request failed after retries."""
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
    acc: dict[str, list[tuple[float, float]]] = defaultdict(list)
    lat = lon = None
    text = gzip.decompress(r.content).decode() if url.endswith(".gz") else r.text
    for row in csv.DictReader(io.StringIO(text), delimiter=";"):
        try:
            v = float(row.get("P2") or "nan")
            lat, lon = float(row["lat"]), float(row["lon"])
        except (ValueError, KeyError):
            continue
        try:
            v10 = float(row.get("P1") or "nan")
        except ValueError:
            v10 = float("nan")
        if 0 <= v <= MAX_PM25:
            acc[row["timestamp"][:13]].append((v, v10))  # UTC hour
    if lat is None:
        return []
    def mean10(vs):  # PM10 can be missing on some sensors; keep NaN rather than 0
        xs = [b for _, b in vs if 0 <= b <= 2 * MAX_PM25]
        return round(sum(xs) / len(xs), 2) if xs else ""

    return [(sid, round(lat, 5), round(lon, 5), h + ":00", round(sum(a for a, _ in v) / len(v), 2), mean10(v), len(v))
            for h, v in acc.items() if len(v) >= 6]


def episode_days(n: int) -> list[date]:
    """The n worst days by CAMS daily mean (at least 3 days apart), each with the following day."""
    import pandas as pd
    c = pd.read_csv(OUT / "cams.csv", parse_dates=["time_local"])
    daily = c.groupby(c.time_local.dt.date).pm2_5.mean().sort_values(ascending=False)
    chosen: list[date] = []
    for d in daily.index:
        if all(abs((d - o).days) >= 3 for o in chosen):
            chosen.append(d)
        if len(chosen) == n:
            break
    return sorted({x for d in chosen for x in (d, d + timedelta(days=1))})


async def fetch_sensors(days: int, step: int, episodes: int, name: str = "sensors", since: date | None = None) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    f = OUT / f"{name}.csv"
    done_f = OUT / (f"{name}_days_done.txt" if name != "sensors" else "days_done.txt")
    done = set(done_f.read_text().split()) if done_f.exists() else set()
    new = not f.exists()
    today = date.today()
    wanted = [today - timedelta(days=k + j) for k in range(3, days, step) for j in (0, -1)]
    if episodes:
        wanted += [d for d in episode_days(episodes) if d not in wanted]
    if since:
        wanted = [d for d in wanted if d >= since]
    sem = asyncio.Semaphore(CONCURRENCY)
    async with httpx.AsyncClient(follow_redirects=True) as c:
        sensors = await live_sensors(c)
        save_types(sensors)
        print(f"{len(sensors)} candidate PM sensors, {len(wanted)} days", file=sys.stderr)
        consecutive_bad = 0
        with open(f, "a", newline="") as fh:
            w = csv.writer(fh)
            if new:
                w.writerow(["sensor", "lat", "lon", "hour_utc", "pm25", "pm10", "n"])
            for d in wanted:
                if d.isoformat() in done:
                    continue
                parts = await asyncio.gather(*(day_rows(c, sem, d, sid, typ) for sid, typ in sensors.items()))
                failed = sum(p is None for p in parts)
                if failed > MAX_FAILED_SHARE * len(parts):
                    print(f"{d}: {failed}/{len(parts)} requests failed, not recorded (server throttling? re-run later)", file=sys.stderr)
                    consecutive_bad += 1
                    if consecutive_bad >= 2:
                        print("archive is not answering; stopping. Re-run later, finished days are kept.", file=sys.stderr)
                        return
                    await asyncio.sleep(60)
                    continue
                consecutive_bad = 0
                parts = [p or [] for p in parts]
                rows = [r for p in parts for r in p]
                w.writerows(rows)
                fh.flush()
                with open(done_f, "a") as df:
                    df.write(d.isoformat() + "\n")
                print(f"{d}: {len(rows)} sensor-hours from {sum(1 for p in parts if p)} sensors", file=sys.stderr)


async def fetch_series(url_t: str, out: str, days: int) -> None:
    a, b = date.today() - timedelta(days=days), date.today() - timedelta(days=1)
    async with httpx.AsyncClient(follow_redirects=True, timeout=120) as c:
        rows, keys = [], None
        cur = a
        while cur <= b:  # month-sized chunks keep the responses small
            end = min(cur + timedelta(days=60), b)
            r = await c.get(url_t.format(lat=LAT, lon=LON, a=cur, b=end))
            r.raise_for_status()
            h = r.json()["hourly"]
            keys = [k for k in h if k != "time"]
            rows += [[t] + [h[k][i] for k in keys] for i, t in enumerate(h["time"])]
            cur = end + timedelta(days=1)
    with open(OUT / out, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["time_local"] + keys)
        w.writerows(rows)
    print(f"{out}: {len(rows)} hours", file=sys.stderr)


async def main() -> None:
    global CONCURRENCY
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=365)
    ap.add_argument("--step", type=int, default=7)
    ap.add_argument("--episodes", type=int, default=14)
    ap.add_argument("--name", default="sensors", help="output file stem under data/history (e.g. sensors_pm10 for a separate PM10 pull)")
    ap.add_argument("--concurrency", type=int, default=CONCURRENCY)
    ap.add_argument("--since", type=date.fromisoformat, help="skip days before this date (e.g. 2026-01-01: the EEA reference starts then)")
    ap.add_argument("--types-only", action="store_true", help="only refresh data/history/sensor_types.csv from the live feed")
    ap.add_argument("--no-series", action="store_true", help="skip re-fetching weather/CAMS")
    ap.add_argument("--series-days", type=int, default=800, help="how far back weather/CAMS go (episodes are picked from this)")
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    CONCURRENCY = a.concurrency
    if a.types_only:
        async with httpx.AsyncClient(follow_redirects=True) as c:
            print(f"{save_types(await live_sensors(c))} sensor types -> {OUT / 'sensor_types.csv'}", file=sys.stderr)
        return
    if not a.no_series:
        await fetch_series(WEATHER, "weather.csv", a.series_days)
        await fetch_series(CAMS, "cams.csv", a.series_days)
    await fetch_sensors(a.days, a.step, a.episodes, a.name, a.since)


if __name__ == "__main__":
    asyncio.run(main())
