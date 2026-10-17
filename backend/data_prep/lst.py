"""Median land surface temperature (deg C) from Landsat 8/9 Collection 2 L2 `lwir11` -> data/lst.tif

Scale and offset come from the asset's raster metadata. Cloud, shadow and dilated cloud are masked
with `qa_pixel`. Summer scenes with cloud cover < 15 %.

    python -m backend.data_prep.lst [--max-items 10]
"""
import argparse
import sys
import warnings

import numpy as np
from rasterio.warp import Resampling

from .stac_common import BBOX, DATA, band_meta, nanmedian_stack, open_catalog, read_to_grid, save_geotiff, target_grid

warnings.filterwarnings("ignore", category=RuntimeWarning)
# qa_pixel bits: 1 dilated cloud, 2 cirrus, 3 cloud, 4 cloud shadow
BAD_BITS = (1 << 1) | (1 << 2) | (1 << 3) | (1 << 4)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-items", type=int, default=10)
    ap.add_argument("--datetime", default="2026-06-01/2026-09-30")
    args = ap.parse_args()

    cat = open_catalog()
    search = cat.search(
        collections=["landsat-c2-l2"], bbox=BBOX, datetime=args.datetime, query={"eo:cloud_cover": {"lt": 15}}
    )
    items = [i for i in search.items() if "lwir11" in i.assets]
    items = sorted(items, key=lambda i: i.properties["eo:cloud_cover"])[: args.max_items * 2]
    print(f"{len(items)} Landsat scenes (cloud < 15 %)", file=sys.stderr)
    transform, w, h = target_grid()
    layers = []
    for it in items:
        try:
            scale, offset, nodata = band_meta(it.assets["lwir11"])
            dn = read_to_grid(it.assets["lwir11"].href, transform, w, h, nodata=nodata if nodata is not None else 0)
            kelvin = dn * scale + offset
            lst = kelvin - 273.15
            # QA is a bit-flag band: it must be resampled with nearest, never averaged
            qa = read_to_grid(it.assets["qa_pixel"].href, transform, w, h, nodata=None, resampling=Resampling.nearest)
            lst[(np.nan_to_num(qa, nan=0).astype("uint32") & BAD_BITS) > 0] = np.nan
            lst[(lst < -20) | (lst > 70)] = np.nan
            if np.isfinite(lst).mean() < 0.05:
                continue
            layers.append(lst)
            print(f"  ok {it.id} cover={np.isfinite(lst).mean():.0%} median={np.nanmedian(lst):.1f}C (scale {scale}, offset {offset})", file=sys.stderr)
        except Exception as exc:  # noqa: BLE001
            print(f"  skip {it.id}: {exc!r}", file=sys.stderr)
        if len(layers) >= args.max_items:
            break
    if not layers:
        print("no usable scenes; heat term will be skipped", file=sys.stderr)
        return 1
    save_geotiff(DATA / "lst.tif", nanmedian_stack(layers), transform, descr="median LST degC summer 2026")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
