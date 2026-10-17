"""Whole citizen-sensor networks of other cities, plus the city-agnostic place features -> data/history/multicity/cities/

For the cross-city test of the LOCAL model: a city's complete outdoor PM sensor network (not just the sensors beside a reference station), hourly raw PM2.5 on
the same 2026 sample days as the Sofia history, and for every city the rasters the place features are built from: GHSL building height / volume / population
(global, CC BY 4.0) and Copernicus GLO-30 terrain height (for the height above the surrounding 3 km).

    python -m backend.data_prep.city_networks [--only Berlin Cologne ...]

Output per city: net.csv (sensor, lat, lon, hour_utc, pm25, pm10, n), ghs_height.tif, ghs_volume.tif, ghs_pop.tif, dem.tif.
"""
import argparse
import asyncio
import csv
import sys
from datetime import date

import httpx
import numpy as np
import pandas as pd
import rasterio
from rasterio.windows import from_bounds

from . import history as H
from .ghsl import FILES, HOST
from .stac_common import DATA, open_catalog, read_to_grid, save_geotiff, target_grid

OUT = H.OUT / "multicity" / "cities"
CITIES = {  # name: (south, north, west, east)
    "Berlin": (52.40, 52.62, 13.20, 13.60), "Cologne": (50.85, 51.05, 6.80, 7.15), "Stuttgart": (48.70, 48.86, 9.05, 9.32),
    "Skopje": (41.93, 42.06, 21.35, 21.55), "Plovdiv": (42.10, 42.20, 24.68, 24.82), "Budapest": (47.40, 47.60, 18.95, 19.20),
    "Hamburg": (53.45, 53.70, 9.80, 10.20), "Munich": (48.05, 48.25, 11.40, 11.75),
}


def rasters(name: str, box) -> None:
    s, n, w, e = box
    m = 0.02
    d = OUT / name
    for key, rel in FILES.items():
        f = d / f"{key}.tif"
        if f.exists():
            continue
        with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif"):
            with rasterio.open(f"/vsicurl/{HOST}/{rel}") as src:
                win = from_bounds(w - m, s - m, e + m, n + m, transform=src.transform).round_offsets().round_lengths()
                a = src.read(1, window=win).astype("float32")
                prof = {"driver": "GTiff", "height": a.shape[0], "width": a.shape[1], "count": 1, "dtype": "float32", "crs": src.crs, "transform": src.window_transform(win), "nodata": -1.0}
        a[a < 0] = 0
        with rasterio.open(f, "w", **prof) as dst:
            dst.write(a, 1)
    f = d / "dem.tif"
    if not f.exists():
        bbox = (w - 0.06, s - 0.06, e + 0.06, n + 0.06)  # wider, so the 3 km mean around an edge sensor is defined
        items = list(open_catalog().search(collections=["cop-dem-glo-30"], bbox=bbox).items())
        transform, wd, ht = target_grid(bbox)
        out = np.full((ht, wd), np.nan, dtype="float32")
        for it in items:
            out = np.where(np.isfinite(layer := read_to_grid(it.assets["data"].href, transform, wd, ht)), layer, out)
        save_geotiff(f, out, transform, descr="Copernicus GLO-30 elevation (m)")


async def pull(name: str, box, days: list[date], conc: int = 8) -> None:
    sc = pd.read_csv(H.OUT / "multicity" / "sc_sensors_24h.csv")
    s, n, w, e = box
    sc = sc[sc.lat.between(s, n) & sc.lon.between(w, e)]
    keep = {int(r.sensor): str(r.type).lower().replace("nova ", "") for r in sc.itertuples()}
    f, done_f = OUT / name / "net.csv", OUT / name / "days_done.txt"
    done = set(done_f.read_text().split()) if done_f.exists() else set()
    new = not f.exists()
    sem = asyncio.Semaphore(conc)
    print(f"{name}: {len(keep)} sensors, {len(days)} days ({len(done)} done)", file=sys.stderr)
    async with httpx.AsyncClient(follow_redirects=True) as c:
        with open(f, "a", newline="") as fh:
            wr = csv.writer(fh)
            if new:
                wr.writerow(["sensor", "lat", "lon", "hour_utc", "pm25", "pm10", "n"])
            for d in sorted(days, reverse=True):
                if d.isoformat() in done:
                    continue
                parts = await asyncio.gather(*(H.day_rows(c, sem, d, sid, typ) for sid, typ in keep.items()))
                if sum(p is None for p in parts) > 0.1 * len(parts):
                    print(f"{name} {d}: too many failed requests, skipped", file=sys.stderr)
                    continue
                wr.writerows([r for p in parts for r in (p or [])])
                fh.flush()
                with open(done_f, "a") as df:
                    df.write(d.isoformat() + "\n")
            print(f"{name}: done", file=sys.stderr)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*", default=None)
    ap.add_argument("--no-pull", action="store_true")
    a = ap.parse_args()
    days = [date.fromisoformat(x) for x in (H.OUT / "days_done.txt").read_text().split() if x >= "2026-01-01"]
    for name, box in CITIES.items():
        if a.only and name not in a.only:
            continue
        (OUT / name).mkdir(parents=True, exist_ok=True)
        rasters(name, box)
        if not a.no_pull:
            asyncio.run(pull(name, box, days))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
