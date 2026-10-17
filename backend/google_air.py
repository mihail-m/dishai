"""Google Air Quality and Pollen APIs (server side, same key as the Routes client). Optional: nothing breaks without a key.

- Air Quality: current conditions (PM2.5, PM10, NO2, O3, CO, SO2, Universal and Bulgarian AQI, health advice) and an hourly forecast, ~500 m model.
- Pollen: 5-day index for grass, tree and weed, plus individual plants in season.
Responses are held in memory for one hour (the Air Quality terms cap cached values at one hour) and never written to disk.
"""
import datetime as dt
import logging
import os
import time

import httpx

log = logging.getLogger("inversion_watch")
CENTER = (42.6977, 23.3219)  # lat, lng: central Sofia (the models are ~500 m grids, so one point speaks for the city centre)
TTL = 3600
_cache: dict[str, tuple[float, dict]] = {}


def enabled() -> bool:
    return not os.getenv("TRAFFIC_DISABLED") and bool(os.getenv("GOOGLE_ROUTES_SERVER_KEY"))


def _key() -> str:
    return os.environ["GOOGLE_ROUTES_SERVER_KEY"]


def _conc(p: dict) -> float | None:
    v = (p.get("concentration") or {}).get("value")
    return round(float(v), 1) if v is not None else None


async def _current(c: httpx.AsyncClient, lat: float, lng: float) -> dict:
    r = await c.post("https://airquality.googleapis.com/v1/currentConditions:lookup", params={"key": _key()}, json={
        "location": {"latitude": lat, "longitude": lng}, "languageCode": "en",
        "extraComputations": ["POLLUTANT_CONCENTRATION", "LOCAL_AQI", "HEALTH_RECOMMENDATIONS", "DOMINANT_POLLUTANT_CONCENTRATION"]})
    r.raise_for_status()
    d = r.json()
    units = {p["code"]: (p.get("concentration") or {}).get("units", "") for p in d.get("pollutants", [])}
    return {
        "time": d.get("dateTime"),
        "indexes": [{"code": i["code"], "name": i.get("displayName"), "aqi": i.get("aqi"), "category": i.get("category"),
                     "dominant": i.get("dominantPollutant")} for i in d.get("indexes", [])],
        "pollutants": {p["code"]: {"name": p.get("displayName"), "value": _conc(p), "unit": "µg/m³" if "MICRO" in units[p["code"]] else "ppb"}
                       for p in d.get("pollutants", [])},
        "health": d.get("healthRecommendations", {}),
    }


async def _forecast(c: httpx.AsyncClient, lat: float, lng: float, hours: int = 48) -> list[dict]:
    # the API only forecasts from the next full hour on
    start = dt.datetime.now(dt.timezone.utc).replace(minute=0, second=0, microsecond=0) + dt.timedelta(hours=1)
    r = await c.post("https://airquality.googleapis.com/v1/forecast:lookup", params={"key": _key()}, json={
        "location": {"latitude": lat, "longitude": lng}, "pageSize": hours + 4,
        "period": {"startTime": start.strftime("%Y-%m-%dT%H:%M:%SZ"), "endTime": (start + dt.timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")},
        "extraComputations": ["POLLUTANT_CONCENTRATION"]})
    r.raise_for_status()
    out = []
    for h in r.json().get("hourlyForecasts", []):
        pm = {p["code"]: _conc(p) for p in h.get("pollutants", [])}
        uaqi = next((i.get("aqi") for i in h.get("indexes", []) if i.get("code") == "uaqi"), None)
        out.append({"time": h.get("dateTime"), "pm25": pm.get("pm25"), "pm10": pm.get("pm10"), "no2": pm.get("no2"), "uaqi": uaqi})
    return out


async def _pollen(c: httpx.AsyncClient, lat: float, lng: float, days: int = 5) -> list[dict]:
    r = await c.get("https://pollen.googleapis.com/v1/forecast:lookup",
                    params={"key": _key(), "location.latitude": lat, "location.longitude": lng, "days": days, "languageCode": "en"})
    r.raise_for_status()
    out = []
    for d in r.json().get("dailyInfo", []):
        dd = d.get("date", {})
        types = {}
        for t in d.get("pollenTypeInfo", []):
            ix = t.get("indexInfo") or {}
            types[t["code"]] = {"name": t.get("displayName"), "in_season": bool(t.get("inSeason")), "value": ix.get("value"), "category": ix.get("category"),
                                "advice": (t.get("healthRecommendations") or [None])[0]}
        plants = [{"name": p.get("displayName"), "type": (p.get("plantDescription") or {}).get("type"), "value": (p.get("indexInfo") or {}).get("value"),
                   "category": (p.get("indexInfo") or {}).get("category")} for p in d.get("plantInfo", []) if p.get("inSeason") and p.get("indexInfo")]
        out.append({"date": f"{dd.get('year')}-{dd.get('month'):02d}-{dd.get('day'):02d}", "types": types, "plants": plants})
    return out


async def get() -> dict:
    """Current air quality, 48 h forecast and pollen for central Sofia. Each part fails on its own."""
    if not enabled():
        return {"enabled": False}
    hit = _cache.get("all")
    if hit and time.time() - hit[0] < TTL:
        return hit[1]
    lat, lng = CENTER
    out: dict = {"enabled": True, "location": {"lat": lat, "lon": lng}, "source": "Google Air Quality API and Pollen API",
                 "note": "A ~500 m model for central Sofia. Independent of our citizen-sensor model, so a disagreement is information, not an error."}
    async with httpx.AsyncClient(timeout=15) as c:
        for name, fn in (("current", _current), ("forecast", _forecast), ("pollen", _pollen)):
            try:
                out[name] = await fn(c, lat, lng)
            except Exception as exc:  # noqa: BLE001 - never log the URL (key)
                log.warning("Google %s failed: %s", name, type(exc).__name__)
                out[name] = None
    if any(out.get(k) for k in ("current", "forecast", "pollen")):
        out["updated"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
        _cache["all"] = (time.time(), out)
    return out
