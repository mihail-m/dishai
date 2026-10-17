"""Inversion Watch API."""
import asyncio
import logging
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
from . import geo, sensors as sensors_mod, waqi
from .cache import Cache
from .scenario import scenario_pm25, winter_inversion_hourly
from .score import clamp, label_for, stagnation_score

log = logging.getLogger("inversion_watch")

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    """Minimal .env reader (no dependency). Existing environment variables win; values are never logged."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
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
    "&forecast_days=2&timezone=Europe%2FSofia"
)
AQ_URL = (
    "https://air-quality-api.open-meteo.com/v1/air-quality"
    f"?latitude={LAT}&longitude={LON}"
    "&hourly=pm10,pm2_5,european_aqi&forecast_days=2&timezone=Europe%2FSofia"
)
SENSORS_URL = "https://data.sensor.community/airrohr/v1/filter/box=42.80,23.15,42.60,23.55"

SCENARIOS = ("winter_inversion",)
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


def _hhmm(t: str) -> str:
    return t[11:16]


def _window(times: list[str], scores: list[float], now_idx: int, now: datetime) -> dict:
    """First contiguous warning run that starts within 24 h of now (or is already active)."""
    n = len(scores)
    start = None
    for i in range(now_idx, min(now_idx + 24, n)):
        if label_for(scores[i]) == "warning":
            start = i
            break
    horizon = scores[now_idx : now_idx + 24]
    peak_i = now_idx + max(range(len(horizon)), key=lambda k: horizon[k]) if horizon else now_idx
    base = {"peak_score": round(scores[peak_i], 1) if n else 0, "peak_time": times[peak_i] if n else None}
    if start is None:
        return {"active": False, **base, "message": "No inversion warning in the next 24 hours."}
    end = start
    while end + 1 < n and label_for(scores[end + 1]) == "warning":
        end += 1
    end_time = (datetime.strptime(times[end], "%Y-%m-%dT%H:%M") + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M")
    start_dt = datetime.strptime(times[start], "%Y-%m-%dT%H:%M")
    in_progress = start == now_idx
    if in_progress:
        msg = f"Inversion in progress until {_hhmm(end_time)}"
    else:
        same_day = start_dt.date() == now.date()
        night = start_dt.hour >= 17 or start_dt.hour < 5
        day = ("tonight" if night else "today") if same_day else ("tomorrow night" if night else "tomorrow")
        msg = f"Inversion expected {day} {_hhmm(times[start])} to {_hhmm(end_time)}"
    return {
        "active": True,
        "in_progress": in_progress,
        "start": times[start],
        "end": end_time,
        "start_hhmm": _hhmm(times[start]),
        "end_hhmm": _hhmm(end_time),
        "hours": end - start + 1,
        **base,
        "message": msg,
    }


def _best_window(times: list[str], scores: list[float], now_idx: int) -> dict | None:
    """Best daytime (10:00-18:00) two-hour slot in the next 24 h: lowest score."""
    cands = [i for i in range(now_idx, min(now_idx + 24, len(scores) - 1)) if 10 <= int(times[i][11:13]) <= 17]
    if not cands:
        return None
    i = min(cands, key=lambda k: scores[k] + scores[k + 1])
    end = (datetime.strptime(times[i], "%Y-%m-%dT%H:%M") + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M")
    return {"start": times[i], "end": end, "start_hhmm": _hhmm(times[i]), "end_hhmm": _hhmm(end)}


async def build_forecast(scenario: str | None) -> dict:
    now = sofia_now()
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    sources: dict[str, str]

    if scenario == "winter_inversion":
        h = winter_inversion_hourly(today)
        sources = {"weather": "scenario", "air_quality": "scenario"}
        pm25, pm10, aqi = h["pm2_5"], h["pm10"], h["european_aqi"]
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
        if aq:
            ah = aq["hourly"]
            if src_a == "fixture":
                ah = _rebase(ah, today)
            idx = {t: i for i, t in enumerate(ah["time"])}
            for i, t in enumerate(h["time"]):
                j = idx.get(t)
                if j is not None:
                    pm25[i], pm10[i], aqi[i] = ah["pm2_5"][j], ah["pm10"][j], ah["european_aqi"][j]
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
                "is_night": hr >= 18 or hr < 7,
            }
        )

    now_key = now.strftime("%Y-%m-%dT%H:00")
    now_idx = times.index(now_key) if now_key in times else 0
    worst = max(sources.values(), key=lambda s: SOURCE_RANK.get(s, 4))
    return {
        "scenario": scenario,
        "source": worst,
        "sources": sources,
        "generated_at": now.strftime("%Y-%m-%dT%H:%M"),
        "now_index": now_idx,
        "grid": {"lat": LAT, "lon": LON},
        "alert": _window(times, scores, now_idx, now),
        "best_window": _best_window(times, scores, now_idx),
        "hours": hours,
        "note": "Score is our own indicative proxy, not a validated model.",
    }


def Scenario(value: str | None):
    if value in (None, "", "live"):
        return None
    if value not in SCENARIOS:
        raise HTTPException(422, f"scenario must be one of {SCENARIOS}")
    return value


ScenarioQ = Query(None, description="winter_inversion replays a hand-built January inversion")


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



async def build_sensors(scenario: str | None) -> dict:
    try:
        recs, src = await cache.get("sensor_community", fetch_sensors)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(503, "Sensors unavailable and no fixture saved yet") from exc
    if src in ("live", "cache"):
        recs = sensors_mod.fresh_only(recs)
    out = []
    for r in recs:
        pm25 = scenario_pm25(r["pm25"]) if scenario else r["pm25"]
        out.append({**r, "pm25": pm25, "district": district_at(r["lon"], r["lat"])})
    vals = [r["pm25"] for r in out]
    return {
        "scenario": scenario,
        "simulated": bool(scenario),
        "source": src,
        "count": len(out),
        "median_pm25": round(statistics.median(vals), 1) if vals else None,
        "label": "Citizen sensors (Sensor.Community). Low-cost sensors drift and read high in humid air.",
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
    """Do the citizen sensors near each reference station read like the reference? PM10 is the only particulate
    the stations report (no PM2.5), so that is what can be compared. Indicative: stations are hourly, sensors are now."""
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
        rows.append({"station": st["name"], "n_sensors": len(near), "citizen_pm10": round(med, 1), "reference_pm10": st["pm10"],
                     "ratio": round(med / st["pm10"], 2) if st["pm10"] else None})
    ratios = [r["ratio"] for r in rows if r["ratio"] is not None]
    out = {"radius_m": COLLOCATION_RADIUS_M, "rows": rows, "median_ratio": round(statistics.median(ratios), 2) if ratios else None}
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
            "note": "These stations report PM10, NO2, O3 and CO but not PM2.5, so they cannot calibrate the PM2.5 model directly."}
    if Scenario(scenario):
        return {**base, "enabled": False, "reason": "Demo scenario: reference stations are real, so they are hidden.", "stations": []}
    if not waqi.enabled():
        return {**base, "enabled": False, "reason": "Set WAQI_TOKEN (free, aqicn.org/data-platform/token) to show municipal stations.", "stations": []}
    try:
        stations, src = await cache.get("waqi_stations", waqi.fetch_stations, persist=False)
    except Exception as exc:  # noqa: BLE001
        return {**base, "enabled": False, "reason": f"Reference stations unavailable ({type(exc).__name__}).", "stations": []}
    try:
        citizen = (await build_sensors(None))["sensors"]
    except HTTPException:
        citizen = []
    return {**base, "enabled": True, "source": src, "stations": stations, "sensor_check": sensor_check(stations, citizen)}


# ---------------------------------------------------------------------------------------------
# Districts
# ---------------------------------------------------------------------------------------------


def _district_scores(fc: dict, factor: float) -> list[float]:
    return [clamp(h["score"] * factor, 0, 100) for h in fc["hours"]]


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
            "note": "District shading is indicative: city-wide score times an elevation factor.",
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
):
    sc = Scenario(scenario)
    d = get_district(district)
    fc = await build_forecast(sc)
    times = [h["time"] for h in fc["hours"]]
    series = _district_scores(fc, d["factor"])
    ni = fc["now_index"]
    alert = _window(times, series, ni, datetime.strptime(fc["generated_at"], "%Y-%m-%dT%H:%M"))
    best = _best_window(times, series, ni)
    peak = max(series[ni : ni + 24]) if series else 0
    level = label_for(peak)
    zone = zone_for(d)
    return {
        "profile": profile,
        "district": d["name"],
        "scenario": sc,
        "status": {
            "level": level,
            "current_score": round(series[ni], 1),
            "peak_score": round(peak, 1),
            "alert": alert,
            "best_window": best,
        },
        "zone": zone,
        "cards": advice_mod.build_cards(profile, level, alert if alert["active"] else None, best, zone),
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
        seed, real = _lead_counts(d["name"])
        leads = seed + real
        rows.append(
            {
                "district": d["name"],
                "peak_24h": round(peak, 1),
                "level": label_for(peak),
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


from .cai_api import make_router  # noqa: E402

cai_router = make_router(build_forecast, build_sensors, Scenario, ScenarioQ, build_sensors_24h, district_at)
app.include_router(cai_router)
app.add_middleware(GZipMiddleware, minimum_size=1000)


@app.on_event("startup")
async def _warm():
    asyncio.create_task(cai_router.warmup())  # type: ignore[attr-defined]


@app.get("/api/health")
async def health():
    return {"ok": True, "districts": len(DISTRICTS)}


# Serve the built frontend and static data when present (single-process demo: `make start`).
app.mount("/data", StaticFiles(directory=DATA_DIR), name="data")
_dist = ROOT / "frontend" / "dist"
if _dist.exists():
    app.mount("/", StaticFiles(directory=_dist, html=True), name="frontend")
