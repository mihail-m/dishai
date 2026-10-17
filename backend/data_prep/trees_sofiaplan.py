"""Sofia tree inventory (Sofiaplan GIS, public ArcGIS MapServer layer) -> data/tree_crown_m2.tif

~1.7 M trees in our bbox, each with a crown area. We page through the public Query API sequentially and politely
(one request at a time, a pause between pages, identifying User-Agent, retry with backoff), add each tree's crown
area to the 10 m cell of its trunk, and keep only that aggregate: individual trees are not stored.
Resumable: progress is saved every 25 pages to data/.trees_progress.npz (git-ignored).

    python -m backend.data_prep.trees_sofiaplan
Source: Sofia Municipality / Sofiaplan, https://gis.sofiaplan.bg (layer durveta/durveta/MapServer/0).
"""
import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import from_origin

from .stac_common import BBOX, DATA

URL = "https://gis.sofiaplan.bg/server/rest/services/durveta/durveta/MapServer/0/query"
UA = "inversion-watch-hackathon/0.1 (academic hackathon project; polite sequential crawl)"
CRS, RES, PAGE, PAUSE_S = "EPSG:32634", 10.0, 2000, 0.25
PROGRESS = DATA / ".trees_progress.npz"


def grid():
    from pyproj import Transformer

    w, s, e, n = BBOX
    tr = Transformer.from_crs("EPSG:4326", CRS, always_xy=True)
    xs, ys = zip(*[tr.transform(lon, lat) for lon, lat in [(w, s), (e, s), (w, n), (e, n)]])
    x0, y1 = min(xs), max(ys)
    return x0, y1, int((max(xs) - x0) / RES) + 1, int((y1 - min(ys)) / RES) + 1


def page(last_oid: int) -> dict:
    q = {"where": f"objectid>{last_oid}", "geometry": ",".join(map(str, BBOX)), "geometryType": "esriGeometryEnvelope",
         "inSR": "4326", "spatialRel": "esriSpatialRelIntersects", "outFields": "objectid,crown_area", "outSR": "32634",
         "geometryPrecision": "1", "orderByFields": "objectid", "resultRecordCount": str(PAGE), "f": "json"}
    for attempt in range(6):
        try:
            req = urllib.request.Request(URL + "?" + urllib.parse.urlencode(q), headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=90) as r:
                d = json.load(r)
            if "error" in d:
                raise RuntimeError(d["error"].get("message", "service error"))
            return d
        except Exception as exc:  # noqa: BLE001
            wait = min(60, 3 * 2**attempt)
            print(f"  page after oid {last_oid} failed ({type(exc).__name__}); retry in {wait}s", file=sys.stderr, flush=True)
            time.sleep(wait)
    raise RuntimeError("giving up after repeated failures")


def main() -> int:
    x0, y1, W, H = grid()
    acc = np.zeros((H, W), dtype="float32")
    last, n_trees, pages = 0, 0, 0
    if PROGRESS.exists():
        z = np.load(PROGRESS)
        acc, last, n_trees, pages = z["acc"], int(z["last"]), int(z["n"]), int(z["pages"])
        print(f"resuming after objectid {last} ({n_trees} trees)", file=sys.stderr, flush=True)
    t0 = time.time()
    while True:
        d = page(last)
        feats = d["features"]
        if not feats:
            break
        x = np.array([f["geometry"]["x"] for f in feats if f.get("geometry")])
        y = np.array([f["geometry"]["y"] for f in feats if f.get("geometry")])
        a = np.array([f["attributes"].get("crown_area") or 0.0 for f in feats if f.get("geometry")], dtype="float32")
        j, i = np.floor((x - x0) / RES).astype(int), np.floor((y1 - y) / RES).astype(int)
        ok = (i >= 0) & (i < H) & (j >= 0) & (j < W)
        np.add.at(acc, (i[ok], j[ok]), a[ok])
        last = max(f["attributes"]["objectid"] for f in feats)
        n_trees += len(feats)
        pages += 1
        if pages % 25 == 0:
            np.savez(PROGRESS, acc=acc, last=last, n=n_trees, pages=pages)
            rate = n_trees / max(1, time.time() - t0)
            print(f"page {pages}: {n_trees} trees, oid {last}, {rate:.0f} trees/s", file=sys.stderr, flush=True)
        if not d.get("exceededTransferLimit"):
            break
        time.sleep(PAUSE_S)
    with rasterio.open(DATA / "tree_crown_m2.tif", "w", driver="GTiff", height=H, width=W, count=1, dtype="float32", crs=CRS,
                       transform=from_origin(x0, y1, RES, RES), compress="deflate", predictor=3) as dst:
        dst.write(acc, 1)
        dst.set_band_description(1, "sum of tree crown area (m2) per 10 m cell, Sofiaplan tree inventory")
    print(f"done: {n_trees} trees, total crown area {acc.sum() / 1e6:.1f} km2 -> data/tree_crown_m2.tif", file=sys.stderr, flush=True)
    PROGRESS.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
