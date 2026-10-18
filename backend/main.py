"""Inversion Watch API."""
import asyncio
import json
import logging
import math
import os
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

import httpx
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict

from . import advice as advice_mod
from . import alert as alert_mod
from . import airthings, geo, google_air, no2_model, pollen as pollen_mod, sensors as sensors_mod, symptoms as symptoms_mod, waqi
from .cache import Cache
from .cai import config as C
from .cai import krige as krige_mod
from .ml import calibration as calibration_mod
from .scenario import HOURLY as SCENARIO_HOURLY, SCENARIO_DAY, scenario_pm25
from .score import clamp, label_for, stagnation_score

log = logging.getLogger("inversion_watch")

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    """Minimal .env reader (no dependency). Existing environment variables win; values are never logged."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            if v.strip():
                os.environ.setdefault(k.strip(), v.strip().strip("\"'"))


_load_dotenv(ROOT / ".env")
DATA_DIR = ROOT / "public" / "data"
TZ = ZoneInfo("Europe/Sofia")

LAT, LON = 42.6977, 23.3219
WEATHER_URL = (
    "https://api.open-meteo.com/v1/forecast"
    f"?latitude={LAT}&longitude={LON}"
    "&hourly=boundary_layer_height,wind_speed_10m,temperature_2m,temperature_925hPa,wind_speed_925hPa,cloud_cover,"
    "apparent_temperature,shortwave_radiation,relative_humidity_2m"
    "&forecast_days=4&timezone=Europe%2FSofia"
)
AQ_URL = (
    "https://air-quality-api.open-meteo.com/v1/air-quality"
    f"?latitude={LAT}&longitude={LON}"
    "&hourly=pm10,pm2_5,european_aqi,alder_pollen,birch_pollen,grass_pollen,mugwort_pollen,olive_pollen,ragweed_pollen,"
    "uv_index,dust,ozone,aerosol_optical_depth"
    "&forecast_days=4&timezone=Europe%2FSofia"
)
SENSORS_URL = "https://data.sensor.community/airrohr/v1/filter/box=42.80,23.15,42.60,23.55"

SCENARIOS = tuple(SCENARIO_HOURLY)
SOURCE_RANK = {"scenario": 0, "live": 0, "cache": 1, "stale": 2, "fixture": 3}

cache = Cache()

app = FastAPI(title="Inversion Watch", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


# ---------------------------------------------------------------------------------------------
# Districts
# ---------------------------------------------------------------------------------------------


def _load_districts() -> list[dict]:
    out = []
    for f in geo.load_features(DATA_DIR / "districts.geojson"):
        g = f["geometry"]
        out.append(
            {
                "name": f["properties"]["name"],
                "props": f["properties"],
                "geometry": g,
                "bbox": geo.bbox(g),
                "centroid": geo.centroid(g),
                "factor": float(f["properties"].get("elevation_factor", 1.0)),
            }
        )
    return out


DISTRICTS = _load_districts()
try:  # share of each district under OSM construction (python -m backend.data_prep.construction_districts); optional
    CONSTRUCTION = json.loads((DATA_DIR / "construction_districts.json").read_text(encoding="utf-8"))
except (OSError, ValueError):
    CONSTRUCTION = None
DISTRICT_BY_NAME = {d["name"].lower(): d for d in DISTRICTS}
LEZ = geo.load_features(DATA_DIR / "lez_illustrative.geojson")
_point_district: dict[tuple[float, float], str | None] = {}


def district_at(lon: float, lat: float) -> str | None:
    key = (lon, lat)
    if key not in _point_district:
        found = None
        for d in DISTRICTS:
            x0, y0, x1, y1 = d["bbox"]
            if x0 <= lon <= x1 and y0 <= lat <= y1 and geo.point_in_geometry(lon, lat, d["geometry"]):
                found = d["name"]
                break
        _point_district[key] = found
    return _point_district[key]


def get_district(name: str) -> dict:
    d = DISTRICT_BY_NAME.get(name.strip().lower())
    if not d:
        raise HTTPException(404, f"Unknown district '{name}'")
    return d


def zone_for(district: dict) -> dict:
    lon, lat = district["centroid"]
    hit = None
    for f in LEZ:
        if geo.point_in_geometry(lon, lat, f["geometry"]):
            if f["properties"]["zone"] == "inner":
                hit = "inner"
                break
            hit = "outer"
    return {
        "zone": hit,
        "illustrative": True,
        "label": "Low-emission zone (illustrative, not official)",
        "basis": "Judged from the district centre point only.",
    }


# ---------------------------------------------------------------------------------------------
# Upstream fetchers
# ---------------------------------------------------------------------------------------------


async def _get_json(url: str):
    async with httpx.AsyncClient(timeout=12, follow_redirects=True) as c:
        r = await c.get(url)
        r.raise_for_status()
        return r.json()


async def fetch_weather():
    return await _get_json(WEATHER_URL)


async def fetch_air_quality():
    return await _get_json(AQ_URL)


async def fetch_sensors():
    return sensors_mod.normalise(await _get_json(SENSORS_URL))


# ---------------------------------------------------------------------------------------------
# Forecast
# ---------------------------------------------------------------------------------------------


def sofia_now() -> datetime:
    return datetime.now(TZ).replace(tzinfo=None)


def _rebase(hourly: dict, today: datetime) -> dict:
    """Shift fixture timestamps so the first day is today (offline demo keeps a sensible time axis)."""
    first = datetime.strptime(hourly["time"][0], "%Y-%m-%dT%H:%M")
    delta = today.date() - first.date()
    out = dict(hourly)
    out["time"] = [(datetime.strptime(t, "%Y-%m-%dT%H:%M") + delta).strftime("%Y-%m-%dT%H:%M") for t in hourly["time"]]
    return out


def _ffill(values: list, default: float) -> list[float]:
    out, last = [], None
    for v in values:
        if v is not None:
            last = v
        out.append(last if last is not None else default)
    return out


def _pollen_at(hourly: dict, i: int) -> dict | None:
    """Pollen per type at index i of an Open-Meteo-shaped hourly dict; None when the feed has no pollen keys (old fixtures, winter)."""
    got = {k: hourly[f"{k}_pollen"][i] for k in advice_mod.POLLEN_TYPES if f"{k}_pollen" in hourly}
    return got or None


ENV_FIELDS = {"uv": "uv_index", "dust": "dust", "ozone": "ozone", "aod": "aerosol_optical_depth"}  # hour key -> Open-Meteo variable


def _hhmm(t: str) -> str:
    return t[11:16]


async def build_forecast(scenario: str | None) -> dict:
    now = sofia_now()
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    sources: dict[str, str]

    if scenario:
        h = SCENARIO_HOURLY[scenario](today)
        sources = {"weather": "scenario", "air_quality": "scenario"}
        pm25, pm10, aqi = h["pm2_5"], h["pm10"], h["european_aqi"]
        pollen = [_pollen_at(h, i) for i in range(len(h["time"]))]
        env = {k: h.get(f) or [None] * len(h["time"]) for k, f in ENV_FIELDS.items()}
    else:
        try:
            weather, src_w = await cache.get("open_meteo_forecast", fetch_weather)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(503, "Forecast unavailable and no fixture saved yet") from exc
        try:
            aq, src_a = await cache.get("open_meteo_air_quality", fetch_air_quality)
        except Exception:  # noqa: BLE001
            aq, src_a = None, "unavailable"
        h = weather["hourly"]
        if src_w == "fixture":
            h = _rebase(h, today)
        n = len(h["time"])
        pm25, pm10, aqi = [None] * n, [None] * n, [None] * n
        pollen = [None] * n
        env = {k: [None] * n for k in ENV_FIELDS}
        if aq:
            ah = aq["hourly"]
            if src_a == "fixture":
                ah = _rebase(ah, today)
            idx = {t: i for i, t in enumerate(ah["time"])}
            for i, t in enumerate(h["time"]):
                j = idx.get(t)
                if j is not None:
                    pm25[i], pm10[i], aqi[i] = ah["pm2_5"][j], ah["pm10"][j], ah["european_aqi"][j]
                    pollen[i] = _pollen_at(ah, j)
                    for k, f in ENV_FIELDS.items():
                        env[k][i] = (ah.get(f) or [None] * len(ah["time"]))[j]
        sources = {"weather": src_w, "air_quality": src_a}

    times = h["time"]
    blh = _ffill(h["boundary_layer_height"], 800)
    wind = _ffill(h["wind_speed_10m"], 12)
    t2m = _ffill(h["temperature_2m"], 10)
    t925 = _ffill(h["temperature_925hPa"], 8)
    w925 = _ffill(h["wind_speed_925hPa"], 10)
    cloud = _ffill(h["cloud_cover"], 50)
    n_h = len(times)
    apparent = h.get("apparent_temperature") or [None] * n_h  # absent in old fixtures
    radiation = h.get("shortwave_radiation") or [None] * n_h
    rh = h.get("relative_humidity_2m") or [None] * n_h

    hours, scores = [], []
    for i, t in enumerate(times):
        s = stagnation_score(blh[i], wind[i], t2m[i], t925[i])
        scores.append(s)
        hr = int(t[11:13])
        hours.append(
            {
                "time": t,
                "score": round(s, 1),
                "label": label_for(s),
                "blh": blh[i],
                "wind10m": wind[i],
                "t2m": t2m[i],
                "t925": t925[i],
                "inversion_delta": round(t925[i] - t2m[i], 1),
                "wind925": w925[i],
                "cloud": cloud[i],
                "apparent": apparent[i] if apparent[i] is not None else t2m[i],
                "radiation": radiation[i],
                "rh": rh[i],
                "pm25": pm25[i],
                "pm10": pm10[i],
                "aqi": aqi[i],
                "pollen": pollen[i],  # grains/m3 per type; None where there is no data (absent in old fixtures)
                **{k: env[k][i] for k in ENV_FIELDS},  # uv index, mineral dust and ozone (ug/m3), aerosol optical depth; None when absent
                "is_night": hr >= 18 or hr < 7,
            }
        )

    now_key = now.strftime("%Y-%m-%dT%H:00")
    now_idx = times.index(now_key) if now_key in times else 0
    worst = max(sources.values(), key=lambda s: SOURCE_RANK.get(s, 4))

    # One PM2.5 level per hour from the learned model, shared by the banner, the chart, the advice and the street map.
    try:
        sensor_pm = [s["pm25"] for s in (await build_sensors(scenario))["sensors"]]
    except HTTPException:
        sensor_pm = []
    pm_model = alert_mod.get_model()
    levels, basis = alert_mod.city_levels(hours, now_idx, sensor_pm, pm_model)
    band = alert_mod.level_band(hours, now_idx, sensor_pm, pm_model) if basis == "model" else None
    for i, (h, v) in enumerate(zip(hours, levels)):
        h["pm25_model"] = v
        h["pm25_p10"], h["pm25_p90"] = (round(float(band[0][i]), 1), round(float(band[2][i]), 1)) if band is not None else (None, None)
        h["level"] = alert_mod.level_for_pm(v) if v is not None else h["label"]
    calibrated = bool(pm_model and pm_model.meta.get("calibrated"))
    return {
        "scenario": scenario,
        "source": worst,
        "sources": sources,
        "generated_at": now.strftime("%Y-%m-%dT%H:%M"),
        "now_index": now_idx,
        "grid": {"lat": LAT, "lon": LON},
        "alert": alert_mod.build_alert(times, levels, scores, now_idx, now, basis, calibrated, band, alert_mod.alert_settings(pm_model)),
        "best_window": alert_mod.best_window(times, levels if basis != "stagnation" else scores, now_idx),
        "pm_basis": basis,
        "hours": hours,
        "note": "PM2.5 is our learned city-level estimate; the stagnation score is an indicative proxy, not a validated model.",
    }


def Scenario(value: str | None):
    if value in (None, "", "live"):
        return None
    if value not in SCENARIOS:
        raise HTTPException(422, f"scenario must be one of {SCENARIOS}")
    return value


ScenarioQ = Query(None, description="demo scenario: winter_inversion, summer_heatwave or birch_season (hand-built 48 h replays)")


@app.get("/api/forecast")
async def api_forecast(scenario: str | None = ScenarioQ):
    return await build_forecast(Scenario(scenario))


# ---------------------------------------------------------------------------------------------
# Sensors
# ---------------------------------------------------------------------------------------------

SENSORS_24H_URL = "https://data.sensor.community/static/v2/data.24h.json"  # 24 h means, worldwide (~9 MB)
SOFIA_BOX = (23.15, 42.60, 23.55, 42.80)


async def fetch_sensors_24h():
    raw = await _get_json(SENSORS_24H_URL)
    w, s, e, n = SOFIA_BOX
    near = []
    for r in raw:
        loc = r.get("location") or {}
        try:
            lat, lon = float(loc["latitude"]), float(loc["longitude"])
        except (KeyError, TypeError, ValueError):
            continue
        if s <= lat <= n and w <= lon <= e:
            near.append(r)
    return sensors_mod.normalise(near)


async def build_sensors_24h() -> list[dict]:
    recs, _ = await cache.get("sensor_community_24h", fetch_sensors_24h)
    return recs



async def current_conditions(scenario: str | None) -> dict:
    """RH (%), temperature (deg C) and month at the current Sofia hour, for the sensor calibration. Missing values stay None
    (the calibration then uses its training median and the sensors response says so)."""
    now = sofia_now()
    out = {"rh": None, "temp": None, "month": now.month}
    try:
        if scenario:
            h = SCENARIO_HOURLY[scenario](now.replace(hour=0, minute=0, second=0, microsecond=0))
        else:
            weather, src = await cache.get("open_meteo_forecast", fetch_weather)
            h = _rebase(weather["hourly"], now) if src == "fixture" else weather["hourly"]
        i = h["time"].index(now.strftime("%Y-%m-%dT%H:00"))
        out["rh"], out["temp"] = h.get("relative_humidity_2m", [None] * (i + 1))[i], h["temperature_2m"][i]
    except Exception:  # noqa: BLE001
        pass
    return out


def calibrate_records(recs: list[dict], cond: dict) -> tuple[list[dict], dict]:
    """Raw Sensor.Community readings -> reference scale. Keeps the raw values as pm25_raw / pm10_raw. When no calibration file
    exists the readings pass through unchanged and `calibrated` is False, so every screen can say the values are uncalibrated."""
    cal25, cal10 = calibration_mod.get("pm25"), calibration_mod.get("pm10")
    info = {"calibrated": cal25 is not None, "pm10_calibrated": cal10 is not None, "rh_used": cond.get("rh") is not None}
    if not recs or (cal25 is None and cal10 is None):
        return [{**r, "pm25_raw": r["pm25"], "pm10_raw": r.get("pm10")} for r in recs], info
    args = (cond.get("rh"), cond.get("temp"), cond.get("month"))
    pm25 = cal25.apply([r["pm25"] for r in recs], *args) if cal25 else [r["pm25"] for r in recs]
    p10 = [r.get("pm10") for r in recs]
    ok = [i for i, v in enumerate(p10) if v is not None]
    if cal10 and ok:
        cal = cal10.apply([p10[i] for i in ok], *args)
        for i, v in zip(ok, cal):
            p10[i] = round(float(v), 1)
    return [{**r, "pm25_raw": r["pm25"], "pm10_raw": r.get("pm10"), "pm25": round(float(a), 1), "pm10": b} for r, a, b in zip(recs, pm25, p10)], info


def sensor_offset(r: dict) -> dict:
    """At a sensor's own location both numbers are shown: its reading (`pm25`) and the reading with its persistent offset removed
    (`pm25_adjusted`, what the street map extrapolates to other places). The offset mixes calibration error and real local pollution."""
    p = krige_mod.get_params()
    off = p.offsets.get(r["id"]) if p else None
    if off is None:
        return {"offset_known": False, "pm25_adjusted": r["pm25"]}
    return {"offset_known": True, "offset_pct": round(100 * math.expm1(off)),
            "pm25_adjusted": round(math.expm1(max(math.log1p(r["pm25"]) - off, 0.0)), 1)}


async def build_airthings(scenario: str | None) -> tuple[list[dict], dict]:
    """Sofia's AIRTHINGS pilot stations as extra sensors. Never raises: a missing or changed upstream just means none this time.
    Their values come already calibrated by the project, so they skip calibrate_records (Sensor.Community's correction)."""
    info = {"enabled": airthings.enabled(), "count": 0, "source": None, "attribution": airthings.ATTRIBUTION}
    if not info["enabled"]:
        return [], info
    try:
        recs, src = await cache.get("airthings", airthings.fetch_sensors, persist=False)  # memory only: the site states no licence
    except Exception:  # noqa: BLE001
        return [], {**info, "source": "unavailable"}
    out = [{**r, "pm25": scenario_pm25(r["pm25"], scenario) if scenario else r["pm25"], "pm25_raw": r["pm25"], "pm10_raw": r.get("pm10")} for r in recs]
    out = [{**r, "district": district_at(r["lon"], r["lat"]), **sensor_offset(r)} for r in out]
    return out, {**info, "count": len(out), "source": src}


async def build_sensors(scenario: str | None) -> dict:
    try:
        recs, src = await cache.get("sensor_community", fetch_sensors)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(503, "Sensors unavailable and no fixture saved yet") from exc
    dropped: list[dict] = []
    if src in ("live", "cache"):
        recs = sensors_mod.fresh_only(recs)
        recs, dropped = sensors_mod.drop_isolated_outliers(recs)
    raw = [{**r, "pm25": scenario_pm25(r["pm25"], scenario) if scenario else r["pm25"]} for r in recs]
    recs, cal = calibrate_records(raw, await current_conditions(scenario))
    out = [{**r, "source": "sensor_community", "district": district_at(r["lon"], r["lat"]), **sensor_offset(r)} for r in recs]
    extra, at_info = await build_airthings(scenario)
    out += extra
    vals = [r["pm25"] for r in out]
    return {
        "scenario": scenario,
        "simulated": bool(scenario),
        "source": src,
        "count": len(out),
        "median_pm25": round(statistics.median(vals), 1) if vals else None,
        "outliers_dropped": [{"id": r["id"], "pm25_raw": r["pm25"], "lat": r["lat"], "lon": r["lon"]} for r in dropped],
        **cal,
        "airthings": at_info,
        "label": ("Citizen sensors (Sensor.Community), calibrated to the EEA reference-station scale (they read about half of it raw)."
                  if cal["calibrated"] else "Citizen sensors (Sensor.Community), NOT calibrated: they read roughly half of reference stations.")
                 + (f" Plus {at_info['count']} stations of Sofia's Airthings pilot network (hourly, already calibrated by the project)." if at_info["count"] else ""),
        "sensors": out,
    }


@app.get("/api/sensors")
async def api_sensors(scenario: str | None = ScenarioQ):
    return await build_sensors(Scenario(scenario))


# ---------------------------------------------------------------------------------------------
# Municipal reference stations (WAQI). Optional: needs WAQI_TOKEN. Held in memory only (terms forbid archiving).
# ---------------------------------------------------------------------------------------------

COLLOCATION_RADIUS_M = 1500.0
COLLOCATION_MIN_SENSORS = 3


def sensor_check(stations: list[dict], citizen: list[dict]) -> dict:
    """Do the citizen sensors near each reference station read like the reference? PM10 is the only particulate the WAQI feed
    carries (the EEA stream has PM2.5 too, see ml/calibrate.py), so that is what can be compared live. `ratio` uses calibrated
    sensor values when a PM10 calibration exists, `ratio_raw` the raw ones. Indicative: stations are hourly, sensors are now."""
    from .cai.graph import to_xy

    rows = []
    cx, cy = to_xy([c["lon"] for c in citizen], [c["lat"] for c in citizen]) if citizen else ([], [])
    for st in stations:
        if st.get("pm10") is None:
            continue
        sx, sy = to_xy(st["lon"], st["lat"])
        near = [c["pm10"] for i, c in enumerate(citizen)
                if c.get("pm10") is not None and ((cx[i] - sx) ** 2 + (cy[i] - sy) ** 2) ** 0.5 <= COLLOCATION_RADIUS_M]
        if len(near) < COLLOCATION_MIN_SENSORS:
            continue
        med = statistics.median(near)
        raw = [c.get("pm10_raw") if c.get("pm10_raw") is not None else c["pm10"] for i, c in enumerate(citizen)
               if c.get("pm10") is not None and ((cx[i] - sx) ** 2 + (cy[i] - sy) ** 2) ** 0.5 <= COLLOCATION_RADIUS_M]
        rows.append({"station": st["name"], "n_sensors": len(near), "citizen_pm10": round(med, 1), "citizen_pm10_raw": round(statistics.median(raw), 1),
                     "reference_pm10": st["pm10"], "ratio": round(med / st["pm10"], 2) if st["pm10"] else None,
                     "ratio_raw": round(statistics.median(raw) / st["pm10"], 2) if st["pm10"] else None})
    ratios = [r["ratio"] for r in rows if r["ratio"] is not None]
    raw_ratios = [r["ratio_raw"] for r in rows if r["ratio_raw"] is not None]
    out = {"radius_m": COLLOCATION_RADIUS_M, "rows": rows, "median_ratio": round(statistics.median(ratios), 2) if ratios else None,
           "median_ratio_raw": round(statistics.median(raw_ratios), 2) if raw_ratios else None,
           "calibrated": bool(calibration_mod.get("pm10"))}
    if ratios:
        m = out["median_ratio"]
        out["verdict"] = ("Citizen sensors read about the same as the reference stations" if 0.8 <= m <= 1.25
                          else f"Citizen sensors read about {m:.1f}x the reference stations" if m > 1.25
                          else f"Citizen sensors read about {m:.1f}x the reference stations (lower)")
    return out


@app.get("/api/reference")
async def api_reference(scenario: str | None = ScenarioQ):
    """Municipal reference stations (PM10, NO2, O3) and how the citizen sensors compare. Live data only."""
    base = {"attribution": waqi.ATTRIBUTION, "pm25_available": False,
            "note": "This live feed (WAQI) carries PM10, NO2, O3 and CO but not PM2.5. The PM2.5 calibration uses the EEA stream instead (Hipodruma, hourly)."}
    if Scenario(scenario):
        return {**base, "enabled": False, "reason": "Demo scenario: reference stations are real, so they are hidden.", "stations": []}
    if not waqi.enabled():
        return {**base, "enabled": False, "reason": "Set WAQI_TOKEN (free, aqicn.org/data-platform/token) to show municipal stations.", "stations": []}
    try:
        stations, src = await cache.get("waqi_stations", waqi.fetch_stations, persist=False)
    except Exception as exc:  # noqa: BLE001
        return {**base, "enabled": False, "reason": f"Reference stations unavailable ({type(exc).__name__}).", "stations": []}
    try:
        # the check is about Sensor.Community specifically (the Airthings stations are not "citizen sensors")
        citizen = [r for r in (await build_sensors(None))["sensors"] if r.get("source") != "airthings"]
    except HTTPException:
        citizen = []
    return {**base, "enabled": True, "source": src, "stations": stations, "sensor_check": sensor_check(stations, citizen)}


@app.get("/api/google-air")
async def api_google_air():
    """Google Air Quality (current + 48 h) and Pollen (5 days) for central Sofia: an independent cross-check of our own estimate."""
    return await google_air.get()


# ---------------------------------------------------------------------------------------------
# Pollen and allergy
# ---------------------------------------------------------------------------------------------

Species = Literal["alder", "birch", "grass", "mugwort", "olive", "ragweed"]
CALENDAR = pollen_mod.load_calendar()  # typical season from model history (python -m backend.data_prep.pollen_calendar); optional
POLLEN_SOURCE = "CAMS European air quality forecast via Open-Meteo (modelled, ~11 km grid, not trap counts)"


def season_day(scenario: str | None):
    """The date the season outlook is measured from: today, or the month a demo scenario pretends to be in."""
    today = sofia_now().date()
    md = SCENARIO_DAY.get(scenario) if scenario else None
    return today.replace(month=md[0], day=md[1]) if md else today


@app.get("/api/pollen")
async def api_pollen(scenario: str | None = ScenarioQ):
    """Pollen per species (4 days, daily peaks and hourly series), UV / ozone / mineral dust and where each species is in its typical season."""
    sc = Scenario(scenario)
    fc = await build_forecast(sc)
    hrs = fc["hours"]
    return {
        "scenario": sc, "generated_at": fc["generated_at"], "source": fc["sources"].get("air_quality"), "attribution": POLLEN_SOURCE,
        "pollen": pollen_mod.forecast(hrs, fc["now_index"]),
        "environment": pollen_mod.environment(hrs),
        "season": pollen_mod.seasons(CALENDAR, season_day(sc)),
        "today": season_day(sc).isoformat(),
        "note": "Modelled concentrations for all of Sofia at about 11 km, not counts from a sampler. Bands are approximate; the feed only covers the pollen season.",
    }


@app.get("/api/pollen/calendar")
async def api_pollen_calendar():
    """Typical season per species: 36 ten-day bins of the highest daily model value (median over years) and the typical start and end."""
    if not CALENDAR:
        return {"available": False}
    species = {}
    for k, v in CALENDAR["species"].items():
        lv = [advice_mod._pollen_level(k, g) for g in v["bins"]]
        species[k] = {"label": advice_mod.POLLEN_LABEL[k], "bins": v["bins"], "levels": lv, "years": v["years"], "season": v.get("season"),
                      "threshold": v["threshold"], "bands": {"moderate": advice_mod.POLLEN_BANDS[k][0], "high": advice_mod.POLLEN_BANDS[k][1]}}
    return {"available": True, "years": CALENDAR["years"], "source": CALENDAR["source"], "note": CALENDAR["note"], "species": species,
            "today_bin": (lambda d: (d.month - 1) * 3 + (0 if d.day <= 10 else 1 if d.day <= 20 else 2))(season_day(None)),
            "season": pollen_mod.seasons(CALENDAR, season_day(None))}


class SymptomIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    district: str
    severity: Literal["none", "mild", "bad"]
    species: list[Species] = []
    scenario: str | None = None


@app.post("/api/symptoms")
async def api_symptoms(rep: SymptomIn):
    """One anonymous check-in. Stores date, district, severity and the model's pollen level for the user's species. Nothing personal."""
    d = get_district(rep.district)
    if rep.scenario not in (None, "", "live"):
        return {"ok": True, "stored": False, "reason": "Demo data: nothing is stored."}
    fc = await build_forecast(None)
    day = sofia_now().date().isoformat()
    todays = [h for h in fc["hours"] if h["time"][:10] == day]
    pol = advice_mod.pollen_summary(todays, frozenset(rep.species) or None)
    ok = symptoms_mod.record(sofia_now().date(), d["name"], rep.severity, pol["level"], pol["types"][0]["type"] if pol["types"] else None)
    if not ok:
        raise HTTPException(429, "Too many check-ins today. Try again tomorrow.")
    return {"ok": True, "stored": True, "level": pol["level"], "message": "Thanks. Stored as an anonymous count with the date, district and pollen level only."}


@app.get("/api/symptoms/summary")
async def api_symptoms_summary(days: int = Query(30, ge=1, le=365)):
    return symptoms_mod.summary(sofia_now().date(), days)


# ---------------------------------------------------------------------------------------------
# Districts
# ---------------------------------------------------------------------------------------------


def _district_scores(fc: dict, factor: float) -> list[float]:
    return [clamp(h["score"] * factor, 0, 100) for h in fc["hours"]]


def district_alert(fc: dict, sens: dict, name: str) -> dict:
    """The banner's alert recomputed for one district: the city forecast moved by the district's sensors (see alert.district_forecast)."""
    from .cai import krige
    p = krige.get_params()
    lr, n = alert_mod.district_log_ratios(sens["sensors"]).get(name, (0.0, 0))
    series, band = alert_mod.district_forecast(fc, lr, p.rho_at if p else None)
    base = fc["alert"]
    if base["basis"] != "model":
        return {**base, "district_sensors": n}
    times, scores = [h["time"] for h in fc["hours"]], [h["score"] for h in fc["hours"]]
    pm_model = alert_mod.get_model()
    a = alert_mod.build_alert(times, series, scores, fc["now_index"], datetime.strptime(fc["generated_at"], "%Y-%m-%dT%H:%M"), "model",
                              base["scale"] == "reference", band, alert_mod.alert_settings(pm_model))
    return {**a, "district_sensors": n}


@app.get("/api/districts")
async def api_districts(scenario: str | None = ScenarioQ, hour: int | None = None):
    sc = Scenario(scenario)
    fc, sens = await asyncio.gather(build_forecast(sc), build_sensors(sc))
    idx = fc["now_index"] if hour is None else max(0, min(hour, len(fc["hours"]) - 1))
    per: dict[str, list[float]] = defaultdict(list)
    for s in sens["sensors"]:
        if s["district"]:
            per[s["district"]].append(s["pm25"])
    feats = []
    for d in DISTRICTS:
        series = _district_scores(fc, d["factor"])
        nxt = series[fc["now_index"] : fc["now_index"] + 24]
        peak = max(nxt) if nxt else series[idx]
        vals = per.get(d["name"], [])
        da = district_alert(fc, sens, d["name"])
        feats.append(
            {
                "type": "Feature",
                "properties": {
                    **{k: v for k, v in d["props"].items() if k != "osm_id"},
                    "score": round(series[idx], 1),
                    "label": label_for(series[idx]),
                    "peak_24h": round(peak, 1),
                    "peak_24h_label": label_for(peak),
                    "sensor_count": len(vals),
                    "median_pm25": round(statistics.median(vals), 1) if vals else None,
                    # the PM2.5 view: city forecast moved by this district's sensors; `label` above stays the stagnation index shading
                    "level_pm": da["level"], "p_episode": da["probability"], "peak_pm25": da.get("peak_pm25"),
                    "centroid": list(d["centroid"]),
                },
                "geometry": d["geometry"],
            }
        )
    return {
        "type": "FeatureCollection",
        "meta": {
            "time": fc["hours"][idx]["time"],
            "hour_index": idx,
            "scenario": sc,
            "source": fc["source"],
            "note": "District shading is indicative: city-wide score times an elevation factor. level_pm / p_episode are the PM2.5 alert for the district.",
        },
        "features": feats,
    }


# ---------------------------------------------------------------------------------------------
# Advice
# ---------------------------------------------------------------------------------------------


@app.get("/api/advice")
async def api_advice(
    profile: Literal["driver", "wood_heater", "vulnerable"],
    district: str,
    scenario: str | None = ScenarioQ,
    toggles: list[Literal["pollen_allergy", "asthma", "heat_sensitive", "outdoor_exercise", "kids_or_elderly"]] = Query(
        [], description="personalisation toggles; repeat the parameter. Not stored."
    ),
    species: list[Species] = Query([], description="pollen species the user reacts to (with the pollen_allergy toggle); repeat the parameter. Not stored."),
):
    sc = Scenario(scenario)
    d = get_district(district)
    fc, sens = await asyncio.gather(build_forecast(sc), build_sensors(sc))
    series = _district_scores(fc, d["factor"])  # stagnation score scaled by the district's elevation factor: supporting context
    ni = fc["now_index"]
    alert = district_alert(fc, sens, d["name"])  # the banner's alert, moved to this district by its sensors
    best = fc["best_window"]
    peak = max(series[ni : ni + 24]) if series else 0
    level = alert["level"]
    zone = zone_for(d)
    # the exercise-slot picker mixes PM2.5 with heat and pollen penalties on the score scale (see C.PM_TO_SCORE)
    penalty = [h["pm25_model"] * C.PM_TO_SCORE if h.get("pm25_model") is not None else x for h, x in zip(fc["hours"], series)]
    cond = advice_mod.conditions(fc["hours"][ni : ni + 24], penalty[ni : ni + 24], "pollen_allergy" in toggles, species)
    season = pollen_mod.seasons(CALENDAR, season_day(sc))
    return {
        "profile": profile,
        "toggles": sorted(set(toggles)),
        "species": sorted(set(species)),
        "season": season,
        "district": d["name"],
        "scenario": sc,
        "status": {
            "level": level,
            "current_score": round(series[ni], 1),
            "peak_score": round(peak, 1),
            "peak_pm25": alert.get("peak_pm25"),
            "alert": alert,
            "best_window": best,
        },
        "zone": zone,
        "conditions": cond,
        "cards": advice_mod.build_cards(profile, level, alert if alert["active"] else None, best, zone,
                                           {**cond, "construction": advice_mod.construction_context(CONSTRUCTION, d["name"]), "season": season}, toggles),
        "grant_note": advice_mod.GRANT_NOTE,
    }


# ---------------------------------------------------------------------------------------------
# Leads (C.3.2 self-reported interest). Aggregates only: district + heating type, nothing personal.
# ---------------------------------------------------------------------------------------------

HeatingType = Literal["wood", "coal", "electric", "gas", "district_heating", "other"]


class LeadIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    district: str
    heating_type: HeatingType
    likely_eligible: bool = False


# Sample leads so the dashboard is not empty on a cold start. Shown as "sample" in the UI.
SEED_LEADS: dict[str, dict[str, int]] = {
    "Nadezhda": {"wood": 5, "coal": 4},
    "Kremikovtsi": {"coal": 6, "wood": 2},
    "Lyulin": {"wood": 4, "coal": 3},
    "Novi Iskar": {"wood": 4, "coal": 2},
    "Vrabnitsa": {"wood": 3, "coal": 2},
    "Poduyane": {"coal": 3, "wood": 2},
    "Slatina": {"wood": 3, "coal": 1},
    "Mladost": {"wood": 2, "coal": 1},
    "Iskar": {"wood": 2, "coal": 1},
    "Bankya": {"wood": 2},
}
submitted: dict[str, Counter] = defaultdict(Counter)
submitted_eligible: Counter = Counter()


def _lead_counts(name: str) -> tuple[int, int]:
    seed = sum(SEED_LEADS.get(name, {}).values())
    real = sum(submitted[name].values())
    return seed, real


@app.post("/api/leads")
async def api_leads(lead: LeadIn):
    d = get_district(lead.district)
    submitted[d["name"]][lead.heating_type] += 1
    if lead.likely_eligible:
        submitted_eligible[d["name"]] += 1
    seed, real = _lead_counts(d["name"])
    return {
        "ok": True,
        "district": d["name"],
        "district_total": seed + real,
        "message": "Interest recorded as an anonymous count. " + advice_mod.GRANT_NOTE,
        "stored": "district and heating type only",
    }


@app.delete("/api/leads")
async def api_leads_reset(request: Request):
    """Demo helper: forget submitted leads (sample leads stay). Localhost only, since the app may be shared publicly."""
    if (request.client.host if request.client else "") not in ("127.0.0.1", "::1", "testclient"):
        raise HTTPException(403, "reset is only allowed from the machine running the app")
    submitted.clear()
    submitted_eligible.clear()
    return {"ok": True}


# ---------------------------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------------------------

# Sample data: plausible arterial roads, NOT derived from traffic or dust measurements.
SAMPLE_STREETS = {
    "Nadezhda": ["Bul. Slivnitsa", "Bul. Vladimir Vazov", "Bul. Hristo Botev"],
    "Kremikovtsi": ["Bul. Botevgradsko shose", "Str. Tsar Simeon"],
    "Lyulin": ["Bul. Vladimir Vazov", "Bul. Tsar Boris III", "Bul. Lyulin"],
    "Novi Iskar": ["Bul. Tsarigradsko shose", "Bul. Sofia-Varna"],
    "Vrabnitsa": ["Bul. Slivnitsa", "Bul. Ring Road (north)"],
    "Poduyane": ["Bul. Maria Luiza", "Bul. Evlogi i Hristo Georgievi"],
    "Slatina": ["Bul. Tsarigradsko shose", "Bul. Hristo Botev"],
    "Mladost": ["Bul. Aleksandar Malinov", "Bul. Tsarigradsko shose"],
    "Iskar": ["Bul. Tsarigradsko shose", "Bul. Bulgaria"],
    "Serdika": ["Bul. Maria Luiza", "Bul. Slivnitsa"],
    "Triaditsa": ["Bul. Bulgaria", "Bul. Cherni vrah"],
}


def _sample_streets(name: str) -> list[str]:
    return SAMPLE_STREETS.get(name) or [f"Main arterial A in {name}", f"Main arterial B in {name}"]


@app.get("/api/dashboard")
async def api_dashboard(scenario: str | None = ScenarioQ):
    sc = Scenario(scenario)
    fc, sens = await asyncio.gather(build_forecast(sc), build_sensors(sc))
    ni = fc["now_index"]
    per_sensor = Counter(s["district"] for s in sens["sensors"] if s["district"])
    rows = []
    for d in DISTRICTS:
        series = _district_scores(fc, d["factor"])
        peak = max(series[ni : ni + 24]) if series else 0
        da = district_alert(fc, sens, d["name"])
        seed, real = _lead_counts(d["name"])
        leads = seed + real
        rows.append(
            {
                "district": d["name"],
                "peak_24h": round(peak, 1),
                "level": da["level"], "p_episode": da["probability"],  # the district's PM2.5 alert; districts are ranked by score x leads
                "leads": leads,
                "sample_leads": seed,
                "submitted_leads": real,
                "eligible_submitted": submitted_eligible[d["name"]],
                "hotspot_index": round(peak * leads, 1),
                "sensors": per_sensor.get(d["name"], 0),
            }
        )
    hotspots = sorted(rows, key=lambda r: (r["hotspot_index"], r["peak_24h"]), reverse=True)[:5]
    washing = []
    for rank, h in enumerate(hotspots[:3], start=1):
        for street in _sample_streets(h["district"]):
            washing.append(
                {
                    "rank": len(washing) + 1,
                    "district": h["district"],
                    "street": street,
                    "district_rank": rank,
                    "peak_24h": h["peak_24h"],
                    "sample": True,
                }
            )
    submitted_total = sum(r["submitted_leads"] for r in rows)
    return {
        "scenario": sc,
        "source": fc["source"],
        "alert": fc["alert"],
        "hotspots": hotspots,
        "leads_by_district": sorted([r for r in rows if r["leads"]], key=lambda r: r["leads"], reverse=True),
        "leads_total": sum(r["leads"] for r in rows),
        "leads_submitted": submitted_total,
        "street_washing": washing[:8],
        "street_washing_note": "Street names are sample data; priority follows the hotspot ranking (score x leads).",
        "sensors_active": sens["count"],
        "sensors_simulated": sens["simulated"],
    }


from . import alerts_api  # noqa: E402
from .cai_api import make_router  # noqa: E402

def refresh_forecast_sources():
    cache.expire(("open_meteo_forecast", "open_meteo_air_quality", "sensor_community", "sensor_community_24h", "waqi_stations", "airthings"))


cai_router = make_router(build_forecast, build_sensors, Scenario, ScenarioQ, build_sensors_24h, district_at,
                         refresh_sources=refresh_forecast_sources, now=lambda: sofia_now())
app.include_router(cai_router)
app.include_router(alerts_api.make_router(build_forecast, Scenario, ScenarioQ, cache))  # /api/alerts and web push
app.include_router(no2_model.make_router(build_forecast))  # /api/no2: NO2 forecast and the traffic-NO2 study
app.add_middleware(GZipMiddleware, minimum_size=1000)


@app.on_event("startup")
async def _warm():
    asyncio.create_task(cai_router.warmup())  # type: ignore[attr-defined]


@app.get("/api/health")
async def health():
    return {"ok": True, "app": "dishai", "districts": len(DISTRICTS)}


# Serve the built frontend and static data when present (single-process demo: `make start`).
app.mount("/data", StaticFiles(directory=DATA_DIR), name="data")
_dist = ROOT / "frontend" / "dist"
if _dist.exists():
    app.mount("/", StaticFiles(directory=_dist, html=True), name="frontend")
