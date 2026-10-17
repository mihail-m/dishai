"""Terrain height (m) from the Copernicus 30 m DEM (Planetary Computer `cop-dem-glo-30`) -> data/dem.tif

Sofia sits in a basin under Vitosha: cold air pools on the valley floor, so height above the basin is a real
predictor of winter PM2.5. Used as a feature by the PM2.5 model.

    python -m backend.data_prep.dem
"""
import sys

import numpy as np

from .stac_common import BBOX, DATA, open_catalog, read_to_grid, save_geotiff, target_grid


def main() -> int:
    items = list(open_catalog().search(collections=["cop-dem-glo-30"], bbox=BBOX).items())
    print(f"{len(items)} DEM tiles", file=sys.stderr)
    transform, w, h = target_grid()
    out = np.full((h, w), np.nan, dtype="float32")
    for it in items:
        layer = read_to_grid(it.assets["data"].href, transform, w, h)
        out = np.where(np.isfinite(layer), layer, out)
    if not np.isfinite(out).any():
        return 1
    save_geotiff(DATA / "dem.tif", out, transform, descr="Copernicus GLO-30 elevation (m)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
