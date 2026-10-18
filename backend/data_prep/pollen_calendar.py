"""Typical pollen season for Sofia from the CAMS Europe model history (Open-Meteo archive) -> data/pollen_calendar.json

    python -m backend.data_prep.pollen_calendar [--from-year 2022] [--to-year 2025]

For each species and year: the daily peak (highest hourly value) of the model at central Sofia. Output per species:
  bins    36 ten-day bins (3 per month): median over years of the highest daily peak in the bin (grains/m3)
  season  typical start / end as day of year: the days on which the daily peak reaches the CAMS season threshold, taking the 5th and
          95th percentile of those days within each year (so a stray early day does not start the season), median over years.
          Absent when fewer than half of the years have at least 5 such days (olive in Sofia).
This is MODEL output, not trap counts: it shows when the model expects pollen, not what a sampler caught. A year counts for a species only when the feed has values for at
least 90 % of the days in the months where that species flowers (WINDOW).
"""
import argparse
import json
import statistics
import sys
import time
from datetime import date, datetime
from pathlib import Path

import httpx

from ..advice import POLLEN_TYPES

OUT = Path(__file__).resolve().parent.parent.parent / "data" / "pollen_calendar.json"
LAT, LON = 42.6977, 23.3219
URL = "https://air-quality-api.open-meteo.com/v1/air-quality"
# CAMS ground-level pollen viewer: season starts where the concentration reaches 10 grains/m3 (trees, mugwort) or 3 (grass, ragweed)
THRESHOLD = {"alder": 10, "birch": 10, "olive": 10, "mugwort": 10, "grass": 3, "ragweed": 3}
MIN_DAYS = 5
MIN_COVERAGE = 0.9
# Months in which each species can matter in Sofia. Older model versions only published March to August, so a year counts for a species
# when the feed covers THESE months, not the whole year.
WINDOW = {"alder": (2, 3), "birch": (3, 5), "grass": (5, 8), "mugwort": (7, 9), "ragweed": (8, 9), "olive": (5, 6)}


def fetch_year(year: int) -> dict:
    params = {"latitude": LAT, "longitude": LON, "start_date": f"{year}-01-01", "end_date": f"{year}-12-31", "timezone": "Europe/Sofia",
              "hourly": ",".join(f"{k}_pollen" for k in POLLEN_TYPES)}
    last = None
    for attempt in range(4):
        try:
            r = httpx.get(URL, params=params, timeout=90)
            r.raise_for_status()
            return r.json()["hourly"]
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            last = exc
            time.sleep(2 + 3 * attempt)
    raise RuntimeError(f"Open-Meteo history for {year} failed: {last!r}")


def daily_peaks(hourly: dict, key: str) -> dict[date, float]:
    out: dict[date, float] = {}
    for t, v in zip(hourly["time"], hourly[f"{key}_pollen"]):
        if v is None:
            continue
        d = datetime.strptime(t[:10], "%Y-%m-%d").date()
        out[d] = max(out.get(d, 0.0), float(v))
    return out


def bin_of(d: date) -> int:
    return (d.month - 1) * 3 + (0 if d.day <= 10 else 1 if d.day <= 20 else 2)


def pctl(sorted_vals: list[int], q: float) -> int:
    return sorted_vals[min(len(sorted_vals) - 1, int(q * len(sorted_vals)))]


def build(years: list[int]) -> dict:
    per_year: dict[int, dict] = {}
    for y in years:
        hourly = fetch_year(y)
        days = {k: daily_peaks(hourly, k) for k in POLLEN_TYPES}
        per_year[y] = days
    species = {}
    for k in POLLEN_TYPES:
        m0, m1 = WINDOW[k]
        ok = []
        for y, days in per_year.items():
            want = (date(y, m1 + 1, 1) - date(y, m0, 1)).days if m1 < 12 else (date(y + 1, 1, 1) - date(y, m0, 1)).days
            have = sum(1 for d in days[k] if m0 <= d.month <= m1)
            print(f"{y} {k}: {have}/{want} days in months {m0}-{m1}", file=sys.stderr)
            if have / want >= MIN_COVERAGE:
                ok.append(y)
        if not ok:
            raise SystemExit(f"no year with enough data for {k}")
        bins = []
        for b in range(36):
            vals = [max((v for d, v in per_year[y][k].items() if bin_of(d) == b), default=0.0) for y in ok]
            bins.append(round(statistics.median(vals), 1))
        starts, ends, per = [], [], {}
        for y in ok:
            days = per_year[y]
            hot = sorted(d.timetuple().tm_yday for d, v in days[k].items() if v >= THRESHOLD[k])
            per[str(y)] = {"days_at_threshold": len(hot)}
            if len(hot) >= MIN_DAYS:
                starts.append(pctl(hot, 0.05))
                ends.append(pctl(hot, 0.95))
                per[str(y)].update(start_doy=starts[-1], end_doy=ends[-1])
        entry = {"bins": bins, "threshold": THRESHOLD[k], "years": ok, "per_year": per}
        if len(starts) * 2 >= len(ok):
            entry["season"] = {"start_doy": int(statistics.median(starts)), "end_doy": int(statistics.median(ends))}
        species[k] = entry
    return {
        "source": "CAMS European air quality forecast / analysis via Open-Meteo (modelled pollen at 42.6977 N, 23.3219 E, ~11 km grid)",
        "licence": "CAMS data (Copernicus); Open-Meteo API, attribution required",
        "note": "Modelled, not trap counts. Season = days at the CAMS threshold (5th to 95th percentile of those days), median over years.",
        "years": sorted(per_year), "bin_days": "10-day bins, three per month", "species": species,
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-year", type=int, default=2022)
    ap.add_argument("--to-year", type=int, default=date.today().year - 1)
    a = ap.parse_args()
    cal = build(list(range(a.from_year, a.to_year + 1)))
    OUT.write_text(json.dumps(cal, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {OUT} ({len(cal['years'])} years: {cal['years']})", file=sys.stderr)
    for k, v in cal["species"].items():
        print(k, v.get("season", "no stable season"), file=sys.stderr)
