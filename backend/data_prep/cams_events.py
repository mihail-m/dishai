"""What is normal for the time of year in the CAMS aerosol forecast over Sofia -> data/cams_climate.json (and data/history/cams_events.csv)

    python -m backend.data_prep.cams_events [--from 2017-07-01]

The alerts use the Copernicus CAMS forecast as it is published (backend/plumes.py). The only thing computed from the past is a yardstick: for each
calendar month, the median and the 75th percentile of the daily-mean PM2.5 and PM10, and the 90th percentile of hourly PM2.5 and carbon
monoxide, so "worse than a usual day" and "carbon monoxide unusually high for the season" mean something in July and in January alike.
Source: CAMS Europe analysis/forecast stitched by Open-Meteo (keyless), best-estimate hours.
"""
import argparse
import csv
import json
import sys
from datetime import date, timedelta

import httpx
import numpy as np

from .stac_common import DATA

OUT = DATA / "history" / "cams_events.csv"
CLIMATE = DATA / "cams_climate.json"
LAT, LON = 42.6977, 23.3219
VARS = ("pm10", "pm2_5", "dust", "aerosol_optical_depth", "carbon_monoxide")
URL = ("https://air-quality-api.open-meteo.com/v1/air-quality?latitude={lat}&longitude={lon}&start_date={a}&end_date={b}"
       "&timezone=Europe%2FSofia&hourly=" + ",".join(VARS))


def fetch(start: date, end: date) -> list[list]:
    rows = []
    with httpx.Client(timeout=90, follow_redirects=True) as c:
        d = start
        while d <= end:
            e = min(end, date(d.year + 1, 1, 1) - timedelta(days=1)) if d.year < end.year else end
            r = c.get(URL.format(lat=LAT, lon=LON, a=d, b=e))
            r.raise_for_status()
            h = r.json()["hourly"]
            rows += [[t] + [h[v][i] for v in VARS] for i, t in enumerate(h["time"])]
            print(f"{d}..{e}: {len(h['time'])} hours", file=sys.stderr)
            d = e + timedelta(days=1)
    return rows


def climate(rows: list[list]) -> dict:
    """{month: {...}} from hourly rows [time, pm10, pm2_5, dust, aod, co]."""
    by_day: dict[str, list[list]] = {}
    for r in rows:
        by_day.setdefault(r[0][:10], []).append(r)
    daily = {d: rs for d, rs in by_day.items() if len(rs) >= 18}
    out = {}
    for m in range(1, 13):
        days = [rs for d, rs in daily.items() if int(d[5:7]) == m]
        hours = [r for rs in by_day.values() for r in rs if int(r[0][5:7]) == m]
        dm = lambda i: [float(np.mean([r[i] for r in rs if r[i] is not None])) for rs in days]  # noqa: E731
        hq = lambda i, q: float(np.quantile([r[i] for r in hours if r[i] is not None], q))  # noqa: E731
        out[str(m)] = {"pm25_day_p50": round(float(np.median(dm(2))), 1), "pm25_day_p75": round(float(np.quantile(dm(2), 0.75)), 1),
                       "pm10_day_p50": round(float(np.median(dm(1))), 1), "pm10_day_p75": round(float(np.quantile(dm(1), 0.75)), 1),
                       "pm25_hour_p90": round(hq(2, 0.9), 1), "co_hour_p90": round(hq(5, 0.9)), "days": len(days)}
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="start", default="2017-07-01")
    ap.add_argument("--reuse", action="store_true", help="compute the yardstick from the history file already on disk, no download")
    a = ap.parse_args()
    if a.reuse:
        with open(OUT, newline="") as f:
            rd = csv.reader(f)
            next(rd)
            rows = [[r[0]] + [float(x) if x != "" else None for x in r[1:6]] for r in rd]
        a.start = rows[0][0][:10]
    else:
        rows = fetch(date.fromisoformat(a.start), date.today())
        OUT.parent.mkdir(parents=True, exist_ok=True)
        with open(OUT, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["time_local", *VARS])
            w.writerows(rows)
    CLIMATE.write_text(json.dumps({"source": "Copernicus CAMS Europe via Open-Meteo, hourly best-estimate", "from": a.start, "to": rows[-1][0][:10], "months": climate(rows)}, indent=1))
    print(f"wrote {len(rows)} hourly rows -> {OUT.name}, monthly yardstick -> {CLIMATE.name}", file=sys.stderr)


if __name__ == "__main__":
    main()
