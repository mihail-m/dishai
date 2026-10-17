"""Residential-heating proxy from OpenStreetMap buildings -> data/lowrise.tif, data/buildings.tif

Sofia's winter PM2.5 is mostly solid-fuel home heating (NIMH 2020: 42 % of PM emissions; Sofia Municipality 2024: about 80 % of winter
exceedances, from roughly 32,000 households), and those households live in low-rise houses outside the district-heating network.
No public map of which household burns what exists (see docs/PM_MODEL.md, Phase 4), so this builds the nearest thing OSM has:

    lowrise.tif    share of each ~35 m cell covered by house-like buildings: building=house/detached/semidetached_house/terrace/
                   bungalow/cabin, or levels <= 2, or (no levels tag, residential-type tag, footprint <= 200 m2)
    buildings.tif  share covered by any building (so lowrise / buildings is the low-rise fraction of the built-up area)

The district-heating area is NOT subtracted (no public polygon): in districts that Toplofikatsia serves, low-rise buildings are
mostly not burning wood or coal, so this is a ceiling on exposure, not an estimate of it.

    python -m backend.data_prep.heating_osm
"""
import sys
import time

import httpx
import numpy as np
from pyproj import Transformer
from rasterio.features import rasterize
from shapely.geometry import Polygon
from shapely.ops import transform as shp_transform

from .stac_common import BBOX, DATA, save_geotiff, target_grid

MIRRORS = ["https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter", "https://overpass.private.coffee/api/interpreter"]
HEADERS = {"User-Agent": "dishai-inversion-watch/0.1 (research prototype)"}
HOUSE_TAGS = {"house", "detached", "semidetached_house", "terrace", "bungalow", "cabin", "farm"}
GENERIC_TAGS = {"yes", "residential"}  # classified by levels / footprint
MAX_HOUSE_M2 = 200.0
MAX_HOUSE_LEVELS = 2
SUPERSAMPLE = 3
_to_utm = Transformer.from_crs(4326, 32635, always_xy=True).transform


def fetch(tries: int = 8) -> dict:
    w, s, e, n = BBOX
    q = f'[out:json][timeout:240];way["building"]({s},{w},{n},{e});out tags geom;'
    wait = 10
    for attempt in range(tries):
        url = MIRRORS[attempt % len(MIRRORS)]
        try:
            r = httpx.post(url, data={"data": q}, headers=HEADERS, timeout=300)
            if r.status_code == 200:
                return r.json()
            print(f"{url}: HTTP {r.status_code}, retrying in {wait}s", file=sys.stderr)
        except httpx.HTTPError as exc:
            print(f"{url}: {exc!r}, retrying in {wait}s", file=sys.stderr)
        time.sleep(wait)
        wait = min(wait * 2, 120)
    raise SystemExit("Overpass did not answer; try again later")


def levels(tags: dict) -> float:
    try:
        return float(str(tags.get("building:levels", "")).split(";")[0].replace(",", "."))
    except ValueError:
        return float("nan")


def classify(tags: dict, area_m2: float) -> str:
    """'house' (counts as home-heating housing), 'other' (any other building)."""
    b = tags.get("building", "")
    lv = levels(tags)
    if b in HOUSE_TAGS:
        return "house"
    if b in GENERIC_TAGS:
        if np.isfinite(lv):
            return "house" if lv <= MAX_HOUSE_LEVELS else "other"
        return "house" if area_m2 <= MAX_HOUSE_M2 else "other"
    return "other"


def main() -> int:
    data = fetch()
    houses, others, kinds = [], [], {}
    for el in data["elements"]:
        g = el.get("geometry")
        if not g or len(g) < 4:
            continue
        pts = [(p["lon"], p["lat"]) for p in g]
        poly = Polygon(pts)
        if not poly.is_valid or poly.is_empty:
            continue
        area = shp_transform(_to_utm, poly).area
        k = classify(el.get("tags", {}), area)
        kinds[k] = kinds.get(k, 0) + 1
        (houses if k == "house" else others).append(poly)
    print(f"{len(houses) + len(others)} buildings: {kinds}", file=sys.stderr)
    transform, w, h = target_grid()
    from rasterio.transform import Affine
    fine = Affine(transform.a / SUPERSAMPLE, 0, transform.c, 0, transform.e / SUPERSAMPLE, transform.f)

    def share(polys):
        mask = rasterize([(p, 1) for p in polys], out_shape=(h * SUPERSAMPLE, w * SUPERSAMPLE), transform=fine, dtype="uint8")
        return mask.reshape(h, SUPERSAMPLE, w, SUPERSAMPLE).mean(axis=(1, 3)).astype("float32")

    low, allb = share(houses), share(houses + others)
    save_geotiff(DATA / "lowrise.tif", low, transform, nodata=-1.0, descr="share of cell covered by house-like buildings (OSM)")
    save_geotiff(DATA / "buildings.tif", allb, transform, nodata=-1.0, descr="share of cell covered by any building (OSM)")
    print(f"low-rise cover {low.mean():.3%} of the box, all buildings {allb.mean():.3%}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
