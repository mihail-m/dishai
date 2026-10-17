"""Which OSM construction sites look active? Sentinel-2 10 m spectra inside each footprint -> data/construction_activity.csv

OSM says a site exists, not whether earthworks are going on. For every footprint we take the median over clear scenes of
  NDVI = (B08 - B04) / (B08 + B04)                       green vegetation
  BSI  = ((B11 + B04) - (B08 + B02)) / ((B11 + B04) + (B08 + B02))   bare soil / sealed surface
for a recent window (this warm season) and the same season a year earlier. A site that is bare now (low NDVI, high BSI)
and was greener a year ago is where soil was recently stripped; bare in both is long-running or abandoned.
Small footprints hold only a few 10 m pixels, so per-site values are noisy; sites with fewer than MIN_PIXELS are left unscored.

    python -m backend.data_prep.construction_activity [--scenes 4]
"""
import argparse
import csv
import json
import sys
import warnings

import numpy as np
import rasterio
from pyproj import Transformer
from rasterio.features import rasterize
from rasterio.windows import from_bounds
from shapely.geometry import shape
from shapely.ops import transform as shp_transform

from .stac_common import BBOX, DATA, open_catalog

warnings.filterwarnings("ignore", category=RuntimeWarning)
RECENT = "2026-05-01/2026-10-10"
PREVIOUS = "2025-05-01/2025-10-10"
MIN_PIXELS = 4
CLEAR_SCL = (4, 5, 6, 7)  # vegetation, bare soil, water, unclassified; excludes shadow, cloud, cirrus, snow


def refl(dn: np.ndarray, baseline: float) -> np.ndarray:
    """Sentinel-2 L2A DN -> reflectance. Planetary Computer ships no scale/offset; baseline >= 4.00 has a -1000 offset."""
    return (dn.astype("float32") - (1000.0 if baseline >= 4.0 else 0.0)) / 10000.0


def read(href: str, win_bounds, shape_hw, resampling=rasterio.enums.Resampling.nearest):
    with rasterio.open(href) as src:
        win = from_bounds(*win_bounds, transform=src.transform)
        return src.read(1, window=win, out_shape=shape_hw, resampling=resampling, boundless=True, fill_value=0)


def scene_stats(item, sites_utm: list, grid) -> dict[int, tuple[float, float, int]]:
    """{site index: (mean NDVI, mean BSI, pixels)} over the clear pixels of this scene."""
    (x0, y0, x1, y1), (h, w), transform = grid
    base = float(item.properties.get("s2:processing_baseline", "0") or 0)
    hw = (h, w)
    b02, b04, b08 = (refl(read(item.assets[b].href, (x0, y0, x1, y1), hw), base) for b in ("B02", "B04", "B08"))
    b11 = refl(read(item.assets["B11"].href, (x0, y0, x1, y1), hw, rasterio.enums.Resampling.bilinear), base)
    scl = read(item.assets["SCL"].href, (x0, y0, x1, y1), hw)
    labels = rasterize([(g, i + 1) for i, g in enumerate(sites_utm)], out_shape=hw, transform=transform, dtype="int32", fill=0)
    ok = (labels > 0) & np.isin(scl, CLEAR_SCL) & (b04 > 0) & (b08 > 0)
    with np.errstate(invalid="ignore", divide="ignore"):
        ndvi = (b08 - b04) / (b08 + b04)
        bsi = ((b11 + b04) - (b08 + b02)) / ((b11 + b04) + (b08 + b02))
    lab = labels[ok]
    n = np.bincount(lab, minlength=len(sites_utm) + 1)
    sn = np.bincount(lab, weights=ndvi[ok], minlength=len(sites_utm) + 1)
    sb = np.bincount(lab, weights=bsi[ok], minlength=len(sites_utm) + 1)
    return {i - 1: (sn[i] / n[i], sb[i] / n[i], int(n[i])) for i in np.nonzero(n)[0] if i > 0}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenes", type=int, default=4, help="clearest scenes per tile and period")
    args = ap.parse_args()
    feats = json.load(open(DATA / "construction.geojson"))["features"]
    to_utm = Transformer.from_crs(4326, 32634, always_xy=True).transform
    sites_utm = [shp_transform(to_utm, shape(f["geometry"])) for f in feats]
    # one common 10 m grid over the study area (UTM 34N, aligned to 10 m so every tile shares pixel edges)
    w, s, e, n = BBOX
    xs, ys = to_utm([w, e], [s, n])
    x0, x1 = np.floor(min(xs) / 10) * 10, np.ceil(max(xs) / 10) * 10
    y0, y1 = np.floor(min(ys) / 10) * 10, np.ceil(max(ys) / 10) * 10
    h, wd = int((y1 - y0) / 10), int((x1 - x0) / 10)
    grid = ((x0, y0, x1, y1), (h, wd), rasterio.Affine(10, 0, x0, 0, -10, y1))

    cat = open_catalog()
    result: dict[str, dict[int, list]] = {}
    for label, period in (("recent", RECENT), ("previous", PREVIOUS)):
        items = list(cat.search(collections=["sentinel-2-l2a"], bbox=BBOX, datetime=period, query={"eo:cloud_cover": {"lt": 15}}).items())
        by_tile: dict[str, list] = {}
        for it in items:
            by_tile.setdefault(it.properties.get("s2:mgrs_tile", it.id.split("_")[1]), []).append(it)
        acc: dict[int, list] = {}
        for tile, its in by_tile.items():
            for it in sorted(its, key=lambda i: i.properties["eo:cloud_cover"])[: args.scenes]:
                try:
                    st = scene_stats(it, sites_utm, grid)
                except Exception as exc:  # noqa: BLE001 - one bad scene should not lose the rest
                    print(f"  skip {it.id}: {exc!r}", file=sys.stderr)
                    continue
                for i, (nd, bs, px) in st.items():
                    acc.setdefault(i, []).append((nd, bs, px))
                print(f"  {label} {tile} {it.id[11:19]}: {len(st)} sites", file=sys.stderr)
        result[label] = acc

    out = DATA / "construction_activity.csv"
    with open(out, "w", newline="") as fh:
        wr = csv.writer(fh)
        wr.writerow(["site", "pixels", "ndvi_recent", "bsi_recent", "ndvi_previous", "bsi_previous", "scenes_recent", "scenes_previous"])
        for i in range(len(sites_utm)):
            row = [i]
            px = max((p for _, _, p in result["recent"].get(i, [(0, 0, 0)])), default=0)
            row.append(px)
            for lab in ("recent", "previous"):
                v = result[lab].get(i, [])
                v = [x for x in v if x[2] >= MIN_PIXELS]
                row += [round(float(np.median([x[0] for x in v])), 3), round(float(np.median([x[1] for x in v])), 3)] if v else ["", ""]
            row += [len([x for x in result["recent"].get(i, []) if x[2] >= MIN_PIXELS]), len([x for x in result["previous"].get(i, []) if x[2] >= MIN_PIXELS])]
            wr.writerow(row)
    print(f"wrote {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
