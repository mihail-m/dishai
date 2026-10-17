"""Static per-segment features: traffic term T(e), greenery G(e), heat anomaly H(e).

T is a 2-D field: every drivable road is sampled every 5 m, each sample carries w_class*5/100, and the field
is convolved with exp(-d/40 m) out to 150 m (PLAN2 section 3, step 2). G blends OSM canopy fraction and
Sentinel-2 NDVI within 50 m; H is the Landsat LST anomaly vs the city median. Anything missing degrades
gracefully (no NDVI -> canopy only; no LST -> no heat term) and is reported in `Features.sources`.
"""
import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import rasterio
from pyproj import Transformer
from scipy import ndimage

from . import config as C
from .graph import DATA, Net, to_xy


def _clamp(a, lo=0.0, hi=1.0):
    return np.clip(a, lo, hi)


@dataclass
class Raster:
    arr: np.ndarray
    transform: rasterio.Affine
    crs: str

    def sample(self, lon, lat) -> np.ndarray:
        lon, lat = np.asarray(lon, dtype=np.float64), np.asarray(lat, dtype=np.float64)
        if self.crs != "EPSG:4326":
            lon, lat = Transformer.from_crs("EPSG:4326", self.crs, always_xy=True).transform(lon, lat)
        inv = ~self.transform
        col = inv.a * lon + inv.b * lat + inv.c
        row = inv.d * lon + inv.e * lat + inv.f
        r = np.clip(np.floor(row).astype(int), 0, self.arr.shape[0] - 1)
        c = np.clip(np.floor(col).astype(int), 0, self.arr.shape[1] - 1)
        out = self.arr[r, c].astype("float32")
        inside = (row >= 0) & (row < self.arr.shape[0]) & (col >= 0) & (col < self.arr.shape[1])
        out[~inside] = np.nan
        return out


def _load_raster(path: Path, scale: float = 1.0, buffer_m: float | None = None) -> Raster | None:
    if not path.exists():
        return None
    with rasterio.open(path) as src:
        a = src.read(1).astype("float32")
        if src.nodata is not None and not np.isnan(src.nodata):
            a[a == src.nodata] = np.nan
        a *= scale
        tr, crs = src.transform, src.crs.to_string()
        cell = abs(tr.a) * (111_320.0 * np.cos(np.radians(42.7)) if crs == "EPSG:4326" else 1.0)
    if buffer_m:
        size = max(1, int(round(2 * buffer_m / cell)) | 1)
        fill = np.nanmedian(a)
        a = ndimage.uniform_filter(np.where(np.isfinite(a), a, fill), size=size, mode="nearest")
    return Raster(a, tr, crs)


def traffic_field(net: Net) -> tuple[np.ndarray, tuple[float, float], float]:
    """Return (field, (x0, y0), cell_m) of the road-traffic term on a 25 m grid in local metres."""
    cell = C.TRAFFIC_GRID_M
    pad = C.TRAFFIC_RADIUS_M + 2 * cell
    x, y = to_xy(net.node_lon, net.node_lat)
    x0, y0 = x.min() - pad, y.min() - pad
    nx = int((x.max() + pad - x0) / cell) + 1
    ny = int((y.max() + pad - y0) / cell) + 1
    rx, ry = to_xy(net.road_lon.astype(np.float64), net.road_lat.astype(np.float64))
    dep = np.zeros((ny, nx), dtype=np.float32)
    ix = np.clip(((rx - x0) / cell).astype(int), 0, nx - 1)
    iy = np.clip(((ry - y0) / cell).astype(int), 0, ny - 1)
    np.add.at(dep, (iy, ix), net.road_w)
    k = int(C.TRAFFIC_RADIUS_M // cell)
    gx, gy = np.meshgrid(np.arange(-k, k + 1) * cell, np.arange(-k, k + 1) * cell)
    d = np.hypot(gx, gy)
    kern = np.where(d <= C.TRAFFIC_RADIUS_M, np.exp(-d / C.TRAFFIC_DECAY_M), 0.0).astype(np.float32)
    return ndimage.convolve(dep, kern, mode="constant"), (x0, y0), cell


@dataclass
class Features:
    T: np.ndarray  # traffic term per directed edge
    G: np.ndarray  # greenery 0..1
    H: np.ndarray  # heat anomaly 0..1
    canopy: np.ndarray
    ndvi: np.ndarray
    lst: np.ndarray
    construction: np.ndarray  # share of the ~150 m around the street that is a construction site (OSM); NaN when not baked
    elev: np.ndarray  # terrain height, m (NaN when data/dem.tif is not baked)
    rel_elev: np.ndarray  # height above the surrounding ~3 km mean: valley floor < 0 < ridge
    median_lst: float | None
    sources: dict
    _tfield: tuple = field(repr=False, default=None)
    _rasters: dict = field(repr=False, default_factory=dict)

    def at(self, lon, lat) -> dict[str, np.ndarray]:
        """Same features at arbitrary points (used for sensor validation)."""
        lon, lat = np.asarray(lon, dtype=np.float64), np.asarray(lat, dtype=np.float64)
        return _sample_all(self._tfield, self._rasters, self.median_lst, lon, lat)


def _sample_all(tfield, rasters, median_lst, lon, lat):
    fld, (x0, y0), cell = tfield
    x, y = to_xy(lon, lat)
    T = ndimage.map_coordinates(fld, [(y - y0) / cell, (x - x0) / cell], order=1, mode="nearest").astype("float32")
    canopy = np.clip(rasters["canopy"].sample(lon, lat), 0, 1) if rasters.get("canopy") else np.full(len(lon), np.nan, "float32")  # overlapping crowns can exceed 100 %
    ndvi = rasters["ndvi"].sample(lon, lat) if rasters.get("ndvi") else np.full(len(lon), np.nan, "float32")
    lst = rasters["lst"].sample(lon, lat) if rasters.get("lst") else np.full(len(lon), np.nan, "float32")
    ndvi_n = _clamp((ndvi - C.NDVI_LOW) / (C.NDVI_HIGH - C.NDVI_LOW))
    has_c, has_n = np.isfinite(canopy), np.isfinite(ndvi_n)
    wc, wn = C.GREEN_W_CANOPY * has_c, C.GREEN_W_NDVI * has_n
    tot = wc + wn
    G = np.where(tot > 0, (wc * np.nan_to_num(canopy) + wn * np.nan_to_num(ndvi_n)) / np.where(tot > 0, tot, 1), 0.0)
    G = _clamp(G).astype("float32")
    H = (_clamp((lst - median_lst) / C.HEAT_RANGE_K) if median_lst is not None else np.zeros(len(lon))).astype("float32")
    H = np.nan_to_num(H)
    elev = rasters["dem"].sample(lon, lat) if rasters.get("dem") else np.full(len(lon), np.nan, "float32")
    rel = rasters["dem_rel"].sample(lon, lat) if rasters.get("dem_rel") else np.full(len(lon), np.nan, "float32")
    construction = rasters["construction"].sample(lon, lat) if rasters.get("construction") else np.full(len(lon), np.nan, "float32")
    return {"T": T, "G": G, "H": H, "canopy": canopy, "ndvi": ndvi, "lst": lst, "construction": construction, "elev": elev, "rel_elev": elev - rel}


def compute_features(net: Net) -> Features:
    sources = {}
    rasters = {}
    # Tree canopy: the Sofia tree inventory (crown area per 10 m cell / 100 m2) when baked, else the OSM approximation.
    trees = _load_raster(DATA / "tree_crown_m2.tif", scale=0.01, buffer_m=C.GREEN_BUFFER_M)
    c = trees if trees is not None else _load_raster(DATA / "canopy_osm.tif", scale=0.01, buffer_m=C.GREEN_BUFFER_M)
    n = _load_raster(DATA / "ndvi.tif", buffer_m=C.GREEN_BUFFER_M)
    lst = _load_raster(DATA / "lst.tif", buffer_m=60.0)
    for name, r, label in (("canopy", c, "Sofia tree inventory (Sofiaplan)" if trees is not None else "OSM canopy"), ("ndvi", n, "Sentinel-2 NDVI"), ("lst", lst, "Landsat LST")):
        sources[name] = label if r is not None else "missing"
        rasters[name] = r
    cons = _load_raster(DATA / "construction.tif", buffer_m=150.0)
    rasters["construction"] = cons
    sources["construction"] = "OSM construction sites" if cons is not None else "missing"
    dem = _load_raster(DATA / "dem.tif")
    rasters["dem"] = dem
    # the mean over ~3 km (the raster is ~35 m/cell), so rel_elev = elevation - this
    rasters["dem_rel"] = _load_raster(DATA / "dem.tif", buffer_m=1500.0)
    sources["dem"] = "Copernicus GLO-30" if dem is not None else "missing"
    median_lst = float(np.nanmedian(lst.arr)) if lst is not None else None
    tfield = traffic_field(net)
    out = _sample_all(tfield, rasters, median_lst, net.e_mid_lon, net.e_mid_lat)
    print(f"features: {sources}; median LST {median_lst}", file=sys.stderr)
    return Features(T=out["T"], G=out["G"], H=out["H"], canopy=out["canopy"], ndvi=out["ndvi"], lst=out["lst"],
                    construction=out["construction"], elev=out["elev"], rel_elev=out["rel_elev"], median_lst=median_lst, sources=sources, _tfield=tfield, _rasters=rasters)
