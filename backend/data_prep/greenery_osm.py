"""OSM greenery fallback: rasterise parks, forest, trees and tree rows into a 10 m canopy-fraction grid.

Writes data/canopy_osm.tif (EPSG:32634, uint8, percent canopy 0-100). Used on its own if the satellite NDVI
step fails, and blended with NDVI otherwise (PLAN2 section 3: G = 0.5 * canopy + 0.5 * NDVI).

    python -m backend.data_prep.greenery_osm
"""
import sys
import time

import numpy as np
import osmnx as ox
import rasterio
from rasterio import features
from rasterio.transform import from_origin
from shapely.geometry import box

from .stac_common import BBOX, DATA

CRS = "EPSG:32634"
RES = 10.0
AREA_TAGS = {
    "leisure": ["park", "garden", "playground", "nature_reserve", "recreation_ground"],
    "landuse": ["forest", "grass", "meadow", "village_green", "orchard", "cemetery"],
    "natural": ["wood", "scrub", "grassland", "tree_row", "tree"],
}
# Fraction of a 10 m cell counted as canopy per feature type (heuristics)
AREA_CANOPY = {"wood": 1.0, "forest": 1.0, "scrub": 0.6, "park": 0.6, "garden": 0.5, "nature_reserve": 0.8,
               "orchard": 0.6, "cemetery": 0.5, "grass": 0.15, "meadow": 0.1, "grassland": 0.1,
               "village_green": 0.2, "playground": 0.2, "recreation_ground": 0.3}
TREE_CELL_FRACTION = 0.25  # a single tree canopy (~25 m2) in a 100 m2 cell
TREE_ROW_BUFFER_M = 3.0


def fetch(attempts: int = 4):
    ox.settings.requests_timeout = 300
    ox.settings.cache_folder = str(DATA / ".osmnx_cache")  # git-ignored
    last = None
    for i in range(attempts):
        try:
            return ox.features_from_bbox(BBOX, AREA_TAGS)
        except Exception as exc:  # noqa: BLE001
            last = exc
            wait = 5 * 2**i
            print(f"  features failed: {exc!r}; retry in {wait}s", file=sys.stderr)
            time.sleep(wait)
    raise RuntimeError(f"OSM greenery download failed: {last!r}")


def main() -> int:
    gdf = fetch().to_crs(CRS)
    print(f"{len(gdf)} OSM features", file=sys.stderr)
    w, s, e, n = BBOX
    bounds = ox.projection.project_geometry(box(w, s, e, n), crs="EPSG:4326", to_crs=CRS)[0].bounds
    x0, y0, x1, y1 = bounds
    width, height = int((x1 - x0) / RES) + 1, int((y1 - y0) / RES) + 1
    transform = from_origin(x0, y1, RES, RES)
    canopy = np.zeros((height, width), dtype="float32")

    def burn(geoms_vals):
        shapes = [(g, v) for g, v in geoms_vals if g is not None and not g.is_empty]
        if not shapes:
            return
        layer = features.rasterize(shapes, out_shape=(height, width), transform=transform, fill=0,
                                   dtype="float32", merge_alg=rasterio.enums.MergeAlg.replace, all_touched=False)
        np.maximum(canopy, layer, out=canopy)

    def value(row) -> float:
        for key in ("natural", "landuse", "leisure"):
            v = row.get(key)
            if isinstance(v, str) and v in AREA_CANOPY:
                return AREA_CANOPY[v]
        return 0.0

    polys = gdf[gdf.geometry.geom_type.isin(["Polygon", "MultiPolygon"])]
    # low-value classes first so denser canopy (max) wins anyway; max-merge makes order irrelevant
    burn((row.geometry, value(row)) for _, row in polys.iterrows())
    rows = gdf[(gdf.get("natural") == "tree_row") & gdf.geometry.geom_type.isin(["LineString", "MultiLineString"])]
    burn((g.buffer(TREE_ROW_BUFFER_M), 0.8) for g in rows.geometry)
    trees = gdf[(gdf.get("natural") == "tree") & (gdf.geometry.geom_type == "Point")]
    ti = np.floor((y1 - trees.geometry.y.values) / RES).astype(int)
    tj = np.floor((trees.geometry.x.values - x0) / RES).astype(int)
    ok = (ti >= 0) & (ti < height) & (tj >= 0) & (tj < width)
    np.add.at(canopy, (ti[ok], tj[ok]), TREE_CELL_FRACTION)
    canopy = np.clip(canopy, 0, 1)
    print(f"canopy mean={canopy.mean():.3f}, {len(trees)} trees, {len(polys)} polygons, {len(rows)} tree rows", file=sys.stderr)
    DATA.mkdir(exist_ok=True)
    with rasterio.open(DATA / "canopy_osm.tif", "w", driver="GTiff", height=height, width=width, count=1,
                       dtype="uint8", crs=CRS, transform=transform, nodata=255, compress="deflate") as dst:
        dst.write((canopy * 100).round().astype("uint8"), 1)
    print(f"wrote {DATA / 'canopy_osm.tif'} ({height}x{width})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
