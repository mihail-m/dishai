"""Municipal reference stations via the World Air Quality Index (aqicn.org) API.

Data: Sofia Municipality, National Automated control air quality, republished by WAQI. Terms (aqicn.org/api):
valid token required, attribution mandatory, no redistribution of cached/archived data, no paid use.
So: token from WAQI_TOKEN only; results live in an in-memory cache and are NEVER written to fixtures; the token is
never logged or returned, and errors are reduced to a status code so no URL (which carries the token) leaks.
"""
import asyncio
import logging
import os
import re

import httpx

logging.getLogger("httpx").setLevel(logging.WARNING)  # httpx INFO lines contain the full URL, token included

BASE = "https://api.waqi.info"
BOUNDS = "42.55,23.10,42.85,23.60"  # south, west, north, east
ATTRIBUTION = (
    "Air quality data: Sofia Municipality, National Automated control air quality, via the World Air Quality Index "
    "Project (aqicn.org)"
)
# US EPA PM10 AQI breakpoints: (aqi_lo, aqi_hi, ug_lo, ug_hi)
_PM10 = [(0, 50, 0, 54), (51, 100, 55, 154), (101, 150, 155, 254), (151, 200, 255, 354), (201, 300, 355, 424),
         (301, 400, 425, 504), (401, 500, 505, 604)]


class WaqiError(RuntimeError):
    """Carries only a status code or a short reason, never a URL."""


def enabled() -> bool:
    return bool(os.getenv("WAQI_TOKEN"))


def pm10_aqi_to_ugm3(aqi: float | None) -> float | None:
    """Invert the EPA PM10 AQI. Approximate: WAQI reports integer AQI, so +-1 AQI is about +-1 ug/m3."""
    if aqi is None:
        return None
    for a0, a1, c0, c1 in _PM10:
        if aqi <= a1:
            return round(c0 + (aqi - a0) * (c1 - c0) / max(1, a1 - a0), 1)
    return None


def _names(raw: str) -> tuple[str, str | None]:
    en = raw.split(",")[0].strip()
    m = re.search(r"\(([^,)]+)", raw)
    return en, (m.group(1).strip() if m else None)


async def _get(client: httpx.AsyncClient, path: str, token: str, **params: str) -> dict:
    try:
        # all query parameters go through `params`: httpx would drop a query string embedded in the path
        r = await client.get(f"{BASE}{path}", params={**params, "token": token})
        r.raise_for_status()
        body = r.json()
    except httpx.HTTPStatusError as exc:
        raise WaqiError(f"HTTP {exc.response.status_code}") from None
    except Exception as exc:  # noqa: BLE001 - message could embed the URL/token; keep only the type
        raise WaqiError(type(exc).__name__) from None
    if body.get("status") != "ok":
        raise WaqiError("API refused the request (check WAQI_TOKEN)")
    return body["data"]


async def fetch_stations() -> list[dict]:
    token = os.getenv("WAQI_TOKEN")
    if not token:
        raise WaqiError("WAQI_TOKEN not set")
    s, w, n, e = BOUNDS.split(",")
    async with httpx.AsyncClient(timeout=15) as client:
        listing = await _get(client, "/map/bounds/", token, latlng=f"{s},{w},{n},{e}")
        feeds = await asyncio.gather(*[_get(client, f"/feed/@{st['uid']}/", token) for st in listing], return_exceptions=True)
    out = []
    for st, feed in zip(listing, feeds):
        if isinstance(feed, Exception):
            continue
        iaqi = {k: v.get("v") for k, v in feed.get("iaqi", {}).items()}
        en, bg = _names(st["station"]["name"])
        out.append({
            "uid": st["uid"], "name": en, "name_bg": bg, "lat": st["lat"], "lon": st["lon"],
            "time": feed.get("time", {}).get("iso"),
            "pm10": pm10_aqi_to_ugm3(iaqi.get("pm10")), "pm10_aqi": iaqi.get("pm10"), "pm25_aqi": iaqi.get("pm25"),
            "no2_aqi": iaqi.get("no2"), "o3_aqi": iaqi.get("o3"), "co_aqi": iaqi.get("co"),
            "temp": iaqi.get("t"), "humidity": iaqi.get("h"),
        })
    return out
