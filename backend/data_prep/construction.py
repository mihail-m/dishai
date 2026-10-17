"""Construction sites (buildings and land under construction) from OpenStreetMap -> data/construction.geojson + data/construction.tif

Construction raises local dust (PM10 mostly, some PM2.5) and brings heavy-vehicle traffic. We take closed areas tagged
landuse=construction or building=construction (linear roadworks are excluded: they have no footprint) and rasterise the
share of each ~35 m cell that is a site. features.py then averages it over 150 m around every street.

OSM coverage of sites is incomplete and not dated, so this is a coarse, static proxy: no start/end dates, no activity level.

    python -m backend.data_prep.construction
"""
import json
import sys
import time

import httpx
import numpy as np
from rasterio.features import rasterize
from shapely.geometry import Polygon, mapping, shape
from shapely.ops import unary_union

from .stac_common import BBOX, DATA, save_geotiff, target_grid

MIRRORS = ["https://overpass.private.coffee/api/interpreter", "https://overpass-api.de/api/interpreter",
           "https://overpass.kumi.systems/api/interpreter"]
HEADERS = {"User-Agent": "dishai-inversion-watch/0.1 (research prototype)"}  # public Overpass servers reject anonymous clients
SUPERSAMPLE = 4


def query() -> str:
    w, s, e, n = BBOX
    box = f"({s},{w},{n},{e})"
    return ("[out:json][timeout:90];(" + "".join(f'{k}["{t}"="construction"]{box};' for k in ("way", "relation") for t in ("landuse", "building"))
            + ");out geom;")


def fetch(tries: int = 8) -> dict:
    wait = 10
    for attempt in range(tries):
        url = MIRRORS[attempt % len(MIRRORS)]
        try:
            r = httpx.post(url, data={"data": query()}, headers=HEADERS, timeout=120)
            if r.status_code == 200:
                return r.json()
            print(f"{url}: HTTP {r.status_code}, retrying in {wait}s", file=sys.stderr)
        except httpx.HTTPError as exc:
            print(f"{url}: {exc!r}, retrying in {wait}s", file=sys.stderr)
        time.sleep(wait)
        wait = min(wait * 2, 120)
    raise SystemExit("Overpass did not answer; try again later")


def polygons(data: dict) -> list[Polygon]:
    out = []
    for el in data.get("elements", []):
        rings = []
        if el["type"] == "way" and "geometry" in el:
            rings = [el["geometry"]]
        elif el["type"] == "relation":  # simple multipolygons: closed outer member ways
            rings = [m["geometry"] for m in el.get("members", []) if m.get("role") == "outer" and m.get("geometry")]
        for g in rings:
            pts = [(p["lon"], p["lat"]) for p in g]
            if len(pts) >= 4 and pts[0] == pts[-1]:
                poly = Polygon(pts)
                if poly.is_valid and not poly.is_empty:
                    out.append(poly)
    return out


def main() -> int:
    polys = polygons(fetch())
    print(f"{len(polys)} construction areas", file=sys.stderr)
    if not polys:
        return 1
    (DATA / "construction.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {}, "geometry": mapping(p)} for p in polys]}))
    transform, w, h = target_grid()
    from rasterio.transform import Affine
    fine = Affine(transform.a / SUPERSAMPLE, 0, transform.c, 0, transform.e / SUPERSAMPLE, transform.f)
    mask = rasterize([(shape(mapping(unary_union(polys))), 1)], out_shape=(h * SUPERSAMPLE, w * SUPERSAMPLE), transform=fine, dtype="uint8")
    frac = mask.reshape(h, SUPERSAMPLE, w, SUPERSAMPLE).mean(axis=(1, 3)).astype("float32")
    save_geotiff(DATA / "construction.tif", frac, transform, nodata=-1.0, descr="share of cell under construction (OSM)")
    print(f"{(frac > 0).sum()} cells touched, total area {frac.sum() * 35 * 33 / 1e6:.2f} km2", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
