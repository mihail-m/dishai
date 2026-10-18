"""Building height, building volume and population around Sofia from the JRC Global Human Settlement Layer -> data/ghs_*.tif

GHS-BUILT-H R2023A (average net building height, m, 2018), GHS-BUILT-V R2023A (building volume per cell, m3, 2020) and GHS-POP R2023A
(people per cell, 2020), all at 3 arc seconds (about 90 m x 68 m here). Cloud-optimised GeoTIFFs on the open JRC bucket, so only the Sofia
window is read. Licence CC BY 4.0, credit: European Commission, Joint Research Centre (GHSL), Pesaresi & Politis 2023 (DOI
10.2905/85005901-3A49-48DD-9D19-6261354F56FE) and Schiavina et al. 2023 (GHS-POP). Height comes from satellite radar and optical data, not from a
building register: it is coarse but covers every building, which OSM does not (OSM has a level count for about 16 % of Sofia's buildings).

    python -m backend.data_prep.ghsl
"""
import sys

import rasterio
from rasterio.windows import from_bounds

from .stac_common import BBOX, DATA

HOST = "https://jrc-ghsl.s3.eu-central-1.amazonaws.com"
FILES = {
    "ghs_height": "ghs-built-h-anbh/r2023a/4326/3asec/2018/GHS_BUILT_H_ANBH_E2018_GLOBE_R2023A_4326_3ss_V1_0.tif",
    "ghs_volume": "ghs-built-v/r2023a/4326/3asec/2020/GHS_BUILT_V_E2020_GLOBE_R2023A_4326_3ss_V1_0.tif",
    "ghs_pop": "ghs-pop/r2023a/4326/3asec/2020/GHS_POP_E2020_GLOBE_R2023A_4326_3ss_V1_0.tif",
}
MARGIN = 0.02  # degrees around the model box, so a buffer at the edge still has data


def main() -> int:
    w, s, e, n = BBOX
    bounds = (w - MARGIN, s - MARGIN, e + MARGIN, n + MARGIN)
    for name, key in FILES.items():
        with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif"):
            with rasterio.open(f"/vsicurl/{HOST}/{key}") as src:
                win = from_bounds(*bounds, transform=src.transform).round_offsets().round_lengths()
                a = src.read(1, window=win).astype("float32")
                profile = {"driver": "GTiff", "height": a.shape[0], "width": a.shape[1], "count": 1, "dtype": "float32", "crs": src.crs,
                           "transform": src.window_transform(win), "nodata": -1.0, "compress": "deflate"}
        a[a < 0] = 0.0
        with rasterio.open(DATA / f"{name}.tif", "w", **profile) as dst:
            dst.write(a, 1)
        print(f"{name}: {a.shape}, {(a > 0).mean():.0%} of cells non-zero, max {a.max():.1f}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
