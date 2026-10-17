"""Sofia Municipality's AIRTHINGS pilot network (Interreg Balkan-Mediterranean), read from its public map.

platform.airthings-project.com serves two anonymous endpoints to its own public map page: the station list and the latest
hourly "AirThingsCalibratedData" values (PM2.5, PM10) for every station. No login or key is involved. The site states no
licence, so, like the WAQI feed: results are held in memory only (never written to fixtures), the requests are few and
identify this app, and the source is credited on screen. The endpoints are undocumented and may change: every failure
here just means "no Airthings sensors this time".
"""
import logging
import os
import re
from datetime import datetime, timezone

import httpx

log = logging.getLogger("inversion_watch")

BASE = "https://platform.airthings-project.com/data"
PINS_URL = f"{BASE}/api/devices/pins"
LASTHOUR_URL = f"{BASE}/engine/telemetry/public/aqivalues/lasthour"
USER_AGENT = "dishai/0.1 (non-profit Sofia air-quality app; reads the public AIRTHINGS map data)"
SOFIA_BOX = (23.10, 42.55, 23.60, 42.85)  # west, south, east, north (the network also covers other countries)
MAX_AGE_MIN = 180  # values are hourly aggregates that arrive about 1-2 h late
MAX_PM25 = 500.0
ATTRIBUTION = "Airthings pilot network: Sofia Municipality / Interreg Balkan-Mediterranean (platform.airthings-project.com)"


def enabled() -> bool:
    return os.getenv("AIRTHINGS_DISABLED", "").lower() not in ("1", "true", "yes")


_DISCLAIMER = re.compile(r"\s*-?\s*При повишена влажност.*$", re.S)


def _clean_name(raw: str) -> str:
    """Some station names end with the same Bulgarian humidity disclaimer ("... - При повишена влажност е възможно ..."),
    with or without spaces around the dash."""
    return _DISCLAIMER.sub("", raw or "").strip(" \n\t-")


def _float(v) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None


def normalise(pins: list[dict], series: list[dict], now: datetime | None = None) -> list[dict]:
    """Join station positions with their latest hour. Same record shape as the Sensor.Community sensors, plus `source`/`name`.
    Drops stations outside Sofia, without a PM2.5 value, with an implausible one, or whose reading is older than MAX_AGE_MIN."""
    now = now or datetime.now(timezone.utc)
    by_id = {p.get("deviceId"): p for p in pins if isinstance(p, dict)}
    w, s, e, n = SOFIA_BOX
    out = []
    for item in series:
        try:
            did = item["tags"]["deviceId"]
            cols = item["columns"]
            row = item["values"][0]
            rec = dict(zip(cols, row))
            when = datetime.strptime(rec["time"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        except (KeyError, IndexError, TypeError, ValueError):
            continue
        pm25 = _float(rec.get("PM2"))
        pin = by_id.get(did)
        if pin is None or pm25 is None or pm25 < 0 or pm25 > MAX_PM25:
            continue
        lat, lon = _float(pin.get("latitude")), _float(pin.get("longitude"))
        if lat is None or lon is None or not (s <= lat <= n and w <= lon <= e):
            continue
        if (now - when).total_seconds() > MAX_AGE_MIN * 60:
            continue
        pm10 = _float(rec.get("PM10"))
        out.append({
            "id": did, "source": "airthings", "name": _clean_name(pin.get("name", "")),
            "lat": round(lat, 5), "lon": round(lon, 5), "pm25": round(pm25, 1),
            "pm10": round(pm10, 1) if pm10 is not None else None,
            "timestamp": when.strftime("%Y-%m-%d %H:%M:%S"),
        })
    return out


async def fetch_sensors() -> list[dict]:
    """Two requests: station list, then the latest hour for all stations at once."""
    async with httpx.AsyncClient(timeout=15, headers={"User-Agent": USER_AGENT}, follow_redirects=True) as client:
        pins = await client.get(PINS_URL)
        pins.raise_for_status()
        last = await client.get(LASTHOUR_URL, params={"selectFields[0]": "PM2", "selectFields[1]": "PM10"})
        last.raise_for_status()
        return normalise(pins.json(), last.json())
