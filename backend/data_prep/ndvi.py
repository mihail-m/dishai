"""Median NDVI composite from Sentinel-2 L2A (Microsoft Planetary Computer) -> data/ndvi.tif

NDVI = (B08 - B04) / (B08 + B04) using reflectance after the asset's own scale/offset.
Sofia is covered by tiles T34TFN and T34TGN. June-September, cloud cover < 10 %.

    python -m backend.data_prep.ndvi [--max-items 8]
"""
import argparse
import sys
import warnings

import numpy as np

from .stac_common import BBOX, DATA, band_meta, nanmedian_stack, open_catalog, read_to_grid, save_geotiff, target_grid

warnings.filterwarnings("ignore", category=RuntimeWarning)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-items", type=int, default=5, help="scenes per tile")
    ap.add_argument("--datetime", default="2026-06-01/2026-09-30")
    args = ap.parse_args()

    cat = open_catalog()
    search = cat.search(
        collections=["sentinel-2-l2a"], bbox=BBOX, datetime=args.datetime, query={"eo:cloud_cover": {"lt": 10}}
    )
    items = list(search.items())
    print(f"{len(items)} Sentinel-2 scenes with cloud < 10 %", file=sys.stderr)
    by_tile: dict[str, list] = {}
    for it in items:
        by_tile.setdefault(it.properties.get("s2:mgrs_tile", it.id.split("_")[1]), []).append(it)
    transform, w, h = target_grid()
    layers = []
    for tile, its in by_tile.items():
        its = sorted(its, key=lambda i: i.properties["eo:cloud_cover"])[: args.max_items]
        print(f"tile {tile}: using {len(its)} scenes", file=sys.stderr)
        for it in its:
            try:
                sc4, of4, _ = band_meta(it.assets["B04"])
                sc8, of8, _ = band_meta(it.assets["B08"])
                if (sc4, of4) == (1.0, 0.0):
                    # Planetary Computer ships no scale/offset for S2 L2A. ESA processing baseline >= 04.00
                    # adds BOA_ADD_OFFSET = -1000 DN: reflectance = (DN - 1000) / 10000.
                    base = float(it.properties.get("s2:processing_baseline", "0") or 0)
                    sc4 = sc8 = 1e-4
                    of4 = of8 = -0.1 if base >= 4.0 else 0.0
                # overview_level=1 is the 40 m overview of the 10 m COG: matches our grid, far less data
                red = read_to_grid(it.assets["B04"].href, transform, w, h, nodata=0, overview_level=1)
                nir = read_to_grid(it.assets["B08"].href, transform, w, h, nodata=0, overview_level=1)
                red = red * sc4 + of4
                nir = nir * sc8 + of8
                with np.errstate(invalid="ignore", divide="ignore"):
                    ndvi = (nir - red) / (nir + red)
                ndvi[(nir + red) <= 0] = np.nan
                layers.append(ndvi)
                print(f"  ok {it.id} cover={np.isfinite(ndvi).mean():.0%} (scale {sc4}, offset {of4})", file=sys.stderr)
            except Exception as exc:  # noqa: BLE001
                print(f"  skip {it.id}: {exc!r}", file=sys.stderr)
    if not layers:
        print("no usable scenes; use greenery_osm.py fallback", file=sys.stderr)
        return 1
    comp = nanmedian_stack(layers)
    save_geotiff(DATA / "ndvi.tif", np.clip(comp, -1, 1), transform, descr="median NDVI Jun-Sep 2026")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
