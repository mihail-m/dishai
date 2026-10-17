"""Shared helpers for Planetary Computer STAC raster composites."""
import sys
from pathlib import Path

import numpy as np
import planetary_computer as pc
import rasterio
from pystac_client import Client
from rasterio.transform import from_origin
from rasterio.warp import Resampling, reproject

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
STAC_URL = "https://planetarycomputer.microsoft.com/api/stac/v1"
BBOX = (23.15, 42.60, 23.55, 42.80)  # west, south, east, north

# Output grid: EPSG:4326, ~40 m x 33 m cells
RES_X, RES_Y = 0.0004, 0.0003


def target_grid(bbox=BBOX):
    w, s, e, n = bbox
    width = int(round((e - w) / RES_X))
    height = int(round((n - s) / RES_Y))
    return from_origin(w, n, RES_X, RES_Y), width, height


def open_catalog() -> Client:
    return Client.open(STAC_URL, modifier=pc.sign_inplace)


def band_meta(asset) -> tuple[float, float, float | None]:
    """(scale, offset, nodata) from the asset's raster metadata (never hardcoded)."""
    rb = (asset.extra_fields.get("raster:bands") or [{}])[0]
    return float(rb.get("scale", 1.0)), float(rb.get("offset", 0.0)), rb.get("nodata")


def read_to_grid(href: str, transform, width: int, height: int, nodata=None, resampling=Resampling.average, overview_level=None) -> np.ndarray:
    """Reproject a (remote COG) band onto the target grid, averaging when downsampling. NaN where empty."""
    dst = np.full((height, width), np.nan, dtype="float32")
    kwargs = {"overview_level": overview_level} if overview_level is not None else {}
    with rasterio.open(href, **kwargs) as src:
        src_nodata = nodata if nodata is not None else src.nodata
        reproject(
            source=rasterio.band(src, 1),
            destination=dst,
            dst_transform=transform,
            dst_crs="EPSG:4326",
            resampling=resampling,
            src_nodata=src_nodata,
            dst_nodata=np.nan,
        )
    return dst


def save_geotiff(path: Path, arr: np.ndarray, transform, nodata=np.nan, descr: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(
        path, "w", driver="GTiff", height=arr.shape[0], width=arr.shape[1], count=1, dtype="float32",
        crs="EPSG:4326", transform=transform, nodata=nodata, compress="deflate",
    ) as dst:
        dst.write(arr.astype("float32"), 1)
        if descr:
            dst.set_band_description(1, descr)
    print(f"wrote {path} {arr.shape} valid={np.isfinite(arr).mean():.0%}", file=sys.stderr)


def nanmedian_stack(layers: list[np.ndarray]) -> np.ndarray:
    return np.nanmedian(np.stack(layers), axis=0)
