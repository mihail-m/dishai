"""Local conditions for any place and hour: weather from a grid, temperature and humidity from nearby sensors, buildings and people.

The single city-wide weather point and the single city median hid the fact that Sofia is several climates at once (see docs/PM_MODEL.md,
"Step back: data"). Here every target place gets

  * grid weather and CAMS (data/history/weather_grid.csv, 35 Open-Meteo points): inverse-distance mean of the 3 nearest points, valid at
    any hour including forecast hours;
  * observed local temperature / humidity / pressure (data/history/env.csv: the BME280 / DHT22 next to the PM sensors) from the nearest
    sensors EXCLUDING any within 60 m of the target (a place without a sensor has no sensor of its own), at the issue hour, plus how far
    that is from the city median (cold-air pooling) and the city-wide temperature-height slope (a measured inversion strength);
  * GHSL building height, volume and population around the place (data/ghs_*.tif) at several radii.
"""
import numpy as np
import pandas as pd

from ..cai.features import _load_raster
from ..cai.graph import DATA, to_lonlat, to_xy

HIST = DATA / "history"
GRID_VARS = ["boundary_layer_height", "wind_speed_10m", "wind_speed_925hPa", "temperature_2m", "temperature_925hPa", "relative_humidity_2m",
             "cloud_cover", "shortwave_radiation", "surface_pressure", "cams_pm2_5", "cams_pm10", "cams_nitrogen_dioxide"]
SELF_M = 60.0  # an environment sensor closer than this to the target is "its own" and is not used
K_GRID, K_ENV = 3, 4


def idw(d: np.ndarray, v: np.ndarray, power: float = 2.0) -> np.ndarray:
    """Inverse-distance mean over the last axis; NaN values are ignored. d, v: (n, k)."""
    w = np.where(np.isfinite(v), 1.0 / np.maximum(d, 100.0) ** power, 0.0)
    return (w * np.nan_to_num(v)).sum(1) / np.where(w.sum(1) > 0, w.sum(1), np.nan)


class Local:
    def __init__(self, times: pd.DatetimeIndex, sensor_xy: np.ndarray):
        self.times = times
        self.sensor_xy = sensor_xy
        self._grid()
        self._env()
        lon, lat = to_lonlat(sensor_xy[:, 0], sensor_xy[:, 1])
        self.lonlat = (np.asarray(lon), np.asarray(lat))
        self._static()

    # ---- grid weather --------------------------------------------------------------------------------------------
    def _grid(self):
        g = pd.read_csv(HIST / "weather_grid.csv")
        g["t"] = pd.to_datetime(g.time_local)
        pts = g.groupby("point")[["lat", "lon", "elev"]].first()
        x, y = to_xy(pts.lon.to_numpy(), pts.lat.to_numpy())
        self.grid_xy, self.grid_elev = np.column_stack([x, y]), pts.elev.to_numpy()
        self.grid = {}
        for v in GRID_VARS:
            p = g.pivot_table(index="point", columns="t", values=v).reindex(columns=self.times)
            self.grid[v] = p.to_numpy()
        self.grid["inversion"] = self.grid["temperature_925hPa"] - self.grid["temperature_2m"]

    def grid_features(self, xy: np.ndarray, t: np.ndarray) -> pd.DataFrame:
        """Inverse-distance mean of the K_GRID nearest grid points for each (place, hour index)."""
        d = np.linalg.norm(xy[:, None, :] - self.grid_xy[None, :, :], axis=-1)
        near = np.argsort(d, axis=1)[:, :K_GRID]
        dist = np.take_along_axis(d, near, 1)
        out = {}
        for v, arr in self.grid.items():
            out[f"g_{v}"] = idw(dist, arr[near, t[:, None]])
        out["g_elev"] = idw(dist, self.grid_elev[near])
        return pd.DataFrame(out)

    # ---- observed local temperature -------------------------------------------------------------------------------
    def _env(self):
        e = pd.read_csv(HIST / "env.csv")
        t = pd.to_datetime(e.pop("hour_utc")).dt.tz_localize("UTC").dt.tz_convert("Europe/Sofia").dt.tz_localize(None)
        e["t"] = t
        loc = e.groupby("sensor")[["lat", "lon"]].median()
        x, y = to_xy(loc.lon.to_numpy(), loc.lat.to_numpy())
        self.env_xy = np.column_stack([x, y])
        dem = _load_raster(DATA / "dem.tif")
        self.env_elev = dem.sample(loc.lon.to_numpy(), loc.lat.to_numpy())
        self.env = {}
        for col, name in (("temp", "T"), ("rh", "RH"), ("pressure_hpa", "P")):
            p = e.pivot_table(index="sensor", columns="t", values=col).reindex(index=loc.index, columns=self.times)
            self.env[name] = p.to_numpy()
        # city-wide statistics per hour: median, and the temperature-height slope (degrees per 100 m) = a measured inversion strength
        T = self.env["T"]
        self.env_med = {k: np.nanmedian(v, axis=0) for k, v in self.env.items()}
        slope = np.full(T.shape[1], np.nan)
        ok = np.isfinite(self.env_elev)
        for j in range(T.shape[1]):
            m = ok & np.isfinite(T[:, j])
            if m.sum() >= 30 and np.ptp(self.env_elev[m]) > 40:
                slope[j] = np.polyfit(self.env_elev[m], T[m, j], 1)[0] * 100.0
        self.env_lapse = slope
        self.env_count = np.isfinite(T).sum(0)

    def env_features(self, xy: np.ndarray, t0: np.ndarray) -> pd.DataFrame:
        d = np.linalg.norm(xy[:, None, :] - self.env_xy[None, :, :], axis=-1)
        d = np.where(d < SELF_M, np.inf, d)  # never the target's own sensor
        near = np.argsort(d, axis=1)[:, :K_ENV]
        dist = np.take_along_axis(d, near, 1)
        out = {}
        for k in ("T", "RH", "P"):
            v = self.env[k][near, t0[:, None]]
            val = idw(np.where(np.isfinite(dist), dist, 1e9), v)
            out[f"e_{k}"] = val
            out[f"e_{k}_vs_city"] = val - self.env_med[k][t0]
        out["e_dist"] = np.log1p(dist[:, 0])
        out["e_lapse"] = self.env_lapse[t0]
        out["e_T_city"] = self.env_med["T"][t0]
        return pd.DataFrame(out)

    # ---- buildings and people -------------------------------------------------------------------------------------
    def _static(self):
        lon, lat = self.lonlat
        out = {}
        for name, radii in (("ghs_height", (150, 400, 1000)), ("ghs_volume", (150, 400, 1000)), ("ghs_pop", (150, 400, 1000))):
            for r in radii:
                out[f"{name}_{r}"] = _load_raster(DATA / f"{name}.tif", buffer_m=r).sample(lon, lat)
        self.static = pd.DataFrame(out)
